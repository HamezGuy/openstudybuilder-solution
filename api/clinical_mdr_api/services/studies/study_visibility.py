"""Exact tenant/study visibility for OSB native study objects.

Legacy author metadata remains a development compatibility aid only. When the
delegated profile is enabled, a signed token assignment plus an active native
tenant binding is required; Admin.Read is not a cross-study wildcard.

Under that profile the caller's assignments are evaluated in one query and the
answers are memoized for the rest of the request (D-45, OSB_ISOLATION_DESIGN.md
sections 3.1 and 9). ``assigned_study_uids`` refuses an invalid assignment,
``visible_study_scope`` leaves it out, and ``study_readable`` answers one study
without raising. Outside the profile none of them looks anything up.

Two checks complete the D-45 study boundary (sections 3.1 to 3.4).
``assert_tied_to_study`` proves that body study uids and child uids belong to
the acted-on study; it never looks at the caller, so a worker principal that
its own entry point admitted passes for its own study. ``assert_related_studies``
checks the caller's authority and reach to every other study an operation reads
or writes on purpose. Outside the profile both return before doing anything.
"""

from collections.abc import Iterable, Mapping

from neomodel import db
from starlette_context import context

from common.config import settings
from common.exceptions import ForbiddenException, NotFoundException


def caller_identities(caller) -> set[str]:
    if caller is None:
        return set()
    values = [
        getattr(caller, "oid", None),
        getattr(caller, "username", None),
        getattr(caller, "email", None),
        getattr(caller, "name", None),
        getattr(caller, "sub", None),
        getattr(caller, "preferred_username", None),
    ]
    ident = getattr(caller, "id", None)
    if callable(ident):
        values.append(ident())
    elif ident:
        values.append(ident)
    return {str(value).strip() for value in values if value and str(value).strip()}


def _strict() -> bool:
    return bool(settings.delegated_claims_required)


def _study_scope(study_uid: str) -> tuple[bool, str | None, str | None]:
    rows, _ = db.cypher_query(
        """
        OPTIONAL MATCH (study:StudyRoot {uid: $study_uid})
        OPTIONAL MATCH (scope:DomainStudyScope {study_uid: $study_uid})
        RETURN study IS NOT NULL, scope.tenant_id, scope.status
        """,
        {"study_uid": study_uid},
    )
    if not rows:
        return False, None, None
    return bool(rows[0][0]), rows[0][1], rows[0][2]


def _study_scopes(
    study_uids: list[str],
) -> dict[str, tuple[bool, str | None, str | None]]:
    """``_study_scope`` for many studies in one query, by uid."""
    rows, _ = db.cypher_query(
        """
        UNWIND $uids AS uid
        OPTIONAL MATCH (study:StudyRoot {uid: uid})
        OPTIONAL MATCH (scope:DomainStudyScope {study_uid: uid})
        RETURN uid, study IS NOT NULL, scope.tenant_id, scope.status
        """,
        {"uids": list(study_uids)},
    )
    scopes: dict[str, tuple[bool, str | None, str | None]] = {}
    for row in rows:
        # The first row of a uid wins, as rows[0] does in _study_scope.
        scopes.setdefault(row[0], (bool(row[1]), row[2], row[3]))
    return scopes


def _study_child_owners(uids: list[str]) -> dict[str, frozenset[str]]:
    """The owning study roots of each child uid, in one query, by uid.

    Both anchors count: the root whose audit trail created the child, and every
    root whose study value holds it. A normal child has one owner; a missing uid
    has none, and legacy data can have two (section 3.2 rule 4). Only owner uids
    are read, never a property of the child.
    """
    rows, _ = db.cypher_query(
        """
        UNWIND $uids AS uid
        OPTIONAL MATCH (child:StudySelection {uid: uid})
        OPTIONAL MATCH (child)<-[:AFTER]-(:StudyAction)<-[:AUDIT_TRAIL]-(author:StudyRoot)
        OPTIONAL MATCH (child)<--(:StudyValue)<-[:LATEST|HAS_VERSION]-(holder:StudyRoot)
        RETURN uid, apoc.coll.toSet(collect(author.uid) + collect(holder.uid)) AS owners
        """,
        {"uids": list(uids)},
    )
    owners: dict[str, frozenset[str]] = {}
    for row in rows:
        owners[row[0]] = owners.get(row[0], frozenset()) | frozenset(row[1] or [])
    return owners


_SCOPE_MEMO_KEY = "osb_d45_study_scope_memo"
_CHILD_OWNER_MEMO_KEY = "osb_d45_study_child_owner_memo"


def _request_memo(caller, key: str) -> dict | None:
    """The request's memo under ``key``, or None outside a request.

    The memo belongs to one caller: a context that outlives a caller, such as a
    reused test context, starts a new memo for the next caller and never serves
    the previous caller's answers. Nothing is cached across requests, because
    assignments, bindings and children can change between them.
    """
    if caller is None or not context.exists():
        return None
    entry = context.get(key)
    if entry is None or entry[0] is not caller:
        entry = (caller, {})
        context[key] = entry
    return entry[1]


def _scope_memo(caller) -> dict[str, tuple[bool, str | None, str | None]] | None:
    """The request's memo of study scope answers, or None outside a request."""
    return _request_memo(caller, _SCOPE_MEMO_KEY)


def _memoized_child_owners(uids: tuple[str, ...]) -> dict[str, frozenset[str]]:
    # The caller only keys the memo; the answer does not depend on it.
    memo = _request_memo(_request_user(), _CHILD_OWNER_MEMO_KEY)
    known = {uid: memo[uid] for uid in uids if memo is not None and uid in memo}
    missing = [uid for uid in uids if uid not in known]
    if missing:
        fetched = _study_child_owners(missing)
        for uid in missing:
            known[uid] = fetched.get(uid, frozenset())
            if memo is not None:
                memo[uid] = known[uid]
    return known


def _memoized_scope(caller, study_uid: str) -> tuple[bool, str | None, str | None]:
    memo = _scope_memo(caller)
    if memo is not None and study_uid in memo:
        return memo[study_uid]
    scope = _study_scope(study_uid)
    if memo is not None:
        memo[study_uid] = scope
    return scope


def _memoized_scopes(
    caller, study_uids: tuple[str, ...]
) -> dict[str, tuple[bool, str | None, str | None]]:
    memo = _scope_memo(caller)
    known = {uid: memo[uid] for uid in study_uids if memo is not None and uid in memo}
    missing = [uid for uid in study_uids if uid not in known]
    if missing:
        fetched = _study_scopes(missing)
        for uid in missing:
            known[uid] = fetched.get(uid, (False, None, None))
            if memo is not None:
                memo[uid] = known[uid]
    return known


def _binding_matches(
    scope: tuple[bool, str | None, str | None], tenant_id: str
) -> bool:
    exists, bound_tenant, status = scope
    return exists and status == "active" and bound_tenant == tenant_id


def _caller_tenant(caller) -> str:
    return str(getattr(caller, "tenant_id", "") or "").strip()


def _caller_assignments(caller) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value).strip()
                for value in (getattr(caller, "study_ids", None) or [])
                if str(value).strip()
            }
        )
    )


def _evaluate_assignments(
    caller, tenant_id: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Every assigned uid, and those with an active binding in ``tenant_id``.

    One evaluation, in at most one query, behind two policies:
    ``assigned_study_uids`` refuses when the two differ, and
    ``visible_study_scope`` returns the second.
    """
    assigned = _caller_assignments(caller)
    if not assigned:
        return (), ()
    scopes = _memoized_scopes(caller, assigned)
    valid = tuple(uid for uid in assigned if _binding_matches(scopes[uid], tenant_id))
    return assigned, valid


def bind_study_to_current_tenant(study_uid: str) -> None:
    """Bind a newly-created native root; ambiguous legacy roots stay quarantined."""
    caller = _request_user()
    tenant_id = str(getattr(caller, "tenant_id", "") or "").strip()
    status = "active" if tenant_id else "quarantined"
    if _strict() and not tenant_id:
        raise ForbiddenException(
            msg="An exact tenant is required to create an OSB study."
        )
    rows, _ = db.cypher_query(
        """
        MATCH (study:StudyRoot {uid: $study_uid})
        MERGE (scope:DomainStudyScope {study_uid: $study_uid})
        ON CREATE SET scope.tenant_id = $tenant_id,
                      scope.status = $status,
                      scope.created_at = datetime(),
                      scope.reason = CASE WHEN $status = 'active'
                        THEN 'native-create' ELSE 'tenant-attribution-missing' END
        RETURN scope.tenant_id, scope.status
        """,
        {"study_uid": study_uid, "tenant_id": tenant_id or None, "status": status},
    )
    memo = _scope_memo(caller)
    if memo is not None:
        memo.pop(study_uid, None)
    if not rows or rows[0][0] != (tenant_id or None) or rows[0][1] != status:
        raise ForbiddenException(
            msg="OSB study tenant binding conflicts with the authenticated tenant."
        )


def study_visible_to_user(
    caller, version_author: str | None, study_uid: str | None = None
) -> bool:
    if _strict():
        if caller is None:
            return False
        uid = str(study_uid or "").strip()
        tenant_id = _caller_tenant(caller)
        if not uid or not tenant_id or uid not in _caller_assignments(caller):
            return False
        return _binding_matches(_memoized_scope(caller, uid), tenant_id)

    # Explicitly time-bounded compatibility behaviour for local/non-delegated
    # installs. Production startup requires delegated claims.
    if caller is None:
        return True
    has_role = getattr(caller, "has_role", None)
    if callable(has_role) and has_role("Admin.Read"):
        return True
    author = (version_author or "").strip()
    if author and author in caller_identities(caller):
        return True
    assigned = {
        str(value).strip()
        for value in (getattr(caller, "study_ids", None) or [])
        if str(value).strip()
    }
    uid = (study_uid or "").strip()
    return bool(uid) and uid in assigned


def catalog_item_author(item) -> str | None:
    if item is None:
        return None
    if isinstance(item, dict):
        return item.get("version_author") or item.get("version_author_id")
    meta = getattr(item, "current_metadata", None)
    if meta is None:
        return None
    ver = getattr(meta, "version_metadata", None) or getattr(meta, "ver_metadata", None)
    if ver is None:
        return None
    return getattr(ver, "version_author", None)


def _request_user():
    try:
        from common.auth.user import user as current_user

        return current_user()
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def caller_is_catalog_admin() -> bool:
    caller = _request_user()
    if _strict():
        # Cross-study listings are never granted by an admin role alone.
        return False
    if caller is None:
        return True
    has_role = getattr(caller, "has_role", None)
    return callable(has_role) and has_role("Admin.Read")


def delegated_study_scope_required() -> bool:
    """Expose the production boundary mode without leaking settings to callers."""
    return _strict()


def _operation_scope_refusal(caller, require_write: bool) -> str | None:
    """The delegated authority rule: None when the caller holds it, else why not.

    Purpose, then the study:read or study:write capability, then a Study or Admin
    role. Outside the delegated profile every caller holds it.
    """
    if not _strict():
        return None
    if caller is None:
        return "Authenticated delegated authority is required."
    if getattr(caller, "purpose", "") not in {
        "interactive-domain-access",
        "workflow-orchestration",
    }:
        return "Token purpose does not authorize OSB study access."
    needed = "study:write" if require_write else "study:read"
    if needed not in set(getattr(caller, "capabilities", None) or []):
        return f"{needed} capability is required."
    role_needed = "Study.Write" if require_write else "Study.Read"
    admin_role = "Admin.Write" if require_write else "Admin.Read"
    roles = set(getattr(caller, "roles", None) or [])
    if role_needed not in roles and admin_role not in roles:
        return f"{role_needed} or {admin_role} role is required."
    return None


def _has_operation_scope(caller, require_write: bool) -> bool:
    return _operation_scope_refusal(caller, require_write) is None


def _assert_operation_scope(caller, require_write: bool) -> None:
    refusal = _operation_scope_refusal(caller, require_write)
    if refusal is not None:
        raise ForbiddenException(msg=refusal)


def assert_study_authority(*, require_write: bool) -> None:
    """403 unless the caller holds delegated study authority, before any lookup."""
    _assert_operation_scope(_request_user(), require_write)


def assert_collection_scope(
    *,
    require_write: bool = False,
    route_path: str | None = None,
    path_parameters: Mapping[str, object] | None = None,
) -> None:
    """Authorize only collection routes with a proven non-wildcard implementation.

    The root and list queries restrict reads to validated assigned study UIDs.
    Legacy cross-study reports, headers, templates, and the generic study-create
    endpoint do not have a tenant/study-native query contract. They therefore
    remain unavailable when delegated claims are required instead of querying
    all studies and filtering after the fact.

    A collection route has no path parameters, so in delegated mode a route
    that has any, such as "/objectives/{uid}/studies", never counts as the root
    list whatever its path ends with.
    """
    _assert_operation_scope(_request_user(), require_write)
    if not _strict():
        return

    if path_parameters:
        raise ForbiddenException(
            msg="This OSB collection route has no exact delegated study boundary."
        )
    normalized = f"/{str(route_path or '').strip('/')}"
    read_only_root = not require_write and (
        normalized.endswith("/studies") or normalized.endswith("/studies/list")
    )
    # These endpoints return terminology configuration, without study records.
    # They still require the delegated read purpose, capability and role above.
    read_only_static_config = not require_write and (
        normalized.endswith("/study-elements/allowed-element-configs")
        or normalized.endswith("/epochs/allowed-configs")
    )
    if read_only_root or read_only_static_config:
        return
    raise ForbiddenException(
        msg="This OSB collection route has no exact delegated study boundary."
    )


def assert_mapping_context_scope(
    study_uid: str | None, *, require_write: bool = False
) -> None:
    """Authorize mapping context only against an exact native study in strict mode."""
    normalized_uid = str(study_uid or "").strip()
    if normalized_uid:
        assert_study_uid_visible(normalized_uid, require_write=require_write)
        return
    _assert_operation_scope(_request_user(), require_write)
    if _strict():
        raise ForbiddenException(
            msg="A delegated mapping-context request requires an exact OSB study."
        )


def assigned_study_uids(*, require_write: bool = False) -> tuple[str, ...]:
    """Return exact assigned native studies after validating every binding.

    A malformed assignment invalidates the whole request; it is never silently
    dropped while other studies are returned.
    """
    caller = _request_user()
    _assert_operation_scope(caller, require_write)
    if not _strict():
        return ()
    tenant_id = _caller_tenant(caller)
    if not tenant_id:
        raise ForbiddenException(
            msg="An exact tenant is required for OSB study access."
        )
    assigned, valid = _evaluate_assignments(caller, tenant_id)
    if valid != assigned:
        raise ForbiddenException(
            msg="A delegated OSB study assignment has no matching active tenant binding."
        )
    return assigned


def visible_study_scope() -> tuple[str, ...] | None:
    """The studies a listing may show the caller, for a filter inside its query.

    Outside the delegated profile: None, meaning no filter. Inside it: the sorted
    assigned uids with an active binding in the caller's tenant, possibly ().
    A caller without study read authority or without a tenant gets (), and an
    invalid assignment is left out, since a filter can only remove rows. It
    never refuses.
    """
    if not _strict():
        return None
    caller = _request_user()
    if not _has_operation_scope(caller, require_write=False):
        return ()
    tenant_id = _caller_tenant(caller)
    if not tenant_id:
        return ()
    return _evaluate_assignments(caller, tenant_id)[1]


def study_readable(study_uid: str | None) -> bool:
    """Whether the caller may read one study, decided without raising.

    Outside the delegated profile: True, so every read stays as it is. Inside it:
    study read authority, and an assigned study with an active binding in the
    caller's tenant.
    """
    if not _strict():
        return True
    caller = _request_user()
    if not _has_operation_scope(caller, require_write=False):
        return False
    return study_visible_to_user(caller, None, study_uid=study_uid)


def assert_study_uid_visible(study_uid: str, *, require_write: bool = False) -> None:
    caller = _request_user()
    _assert_operation_scope(caller, require_write)
    if _strict():
        if not study_visible_to_user(caller, None, study_uid=study_uid):
            raise NotFoundException("Study Definition", study_uid)
        return
    if caller is None:
        return
    has_role = getattr(caller, "has_role", None)
    if callable(has_role) and has_role("Admin.Read"):
        return

    from clinical_mdr_api.services._meta_repository import MetaRepository

    repos = MetaRepository()
    try:
        study = repos.study_definition_repository.find_by_uid(uid=study_uid)
        if study is None or not study_visible_to_user(
            caller, catalog_item_author(study), study_uid=study_uid
        ):
            raise NotFoundException("Study Definition", study_uid)
    finally:
        repos.close()


ChildValue = str | Iterable[str | None] | None

_SUBPART_OUT_OF_SCOPE = "A subpart of this study is outside your study scope."


def _given_values(values: str | Iterable[str | None] | None) -> list[str]:
    """The values that are not None and not blank, as sent, in order."""
    if values is None:
        return []
    if isinstance(values, str):
        values = (values,)
    return [str(value) for value in values if value is not None and str(value).strip()]


def assert_tied_to_study(
    study_uid: str,
    *,
    children: Mapping[str, ChildValue] | None = None,
    body_study_uids: Iterable[str | None] = (),
) -> None:
    """Under the delegated profile, refuse a body study or child of another study.

    ``body_study_uids`` are body values that only echo the acted-on study: each
    one that is not None must equal ``study_uid`` exactly, with no strip and no
    case folding, and a blank one is refused. That is a comparison, so a refusal
    looks nothing up. ``children`` maps a resource name, used in the refusal, to
    one raw uid or several; None and blank uids are skipped, and every other uid
    must have exactly one owning study, ``study_uid``, in one owner query for
    the whole call. A missing, a foreign and a doubly owned uid answer the same
    404. The caller is never consulted: its entry point already checked the
    acted-on study (section 3.5). Outside the profile nothing is looked up.
    """
    if not _strict():
        return
    if isinstance(body_study_uids, str):
        body_study_uids = (body_study_uids,)
    for value in body_study_uids:
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or value != study_uid:
            raise NotFoundException("Study Definition", value)

    named: list[tuple[str, str]] = []
    for resource_name, raw in (children or {}).items():
        named.extend((resource_name, uid) for uid in _given_values(raw))
    if not named:
        return
    owners = _memoized_child_owners(tuple(dict.fromkeys(uid for _, uid in named)))
    expected = frozenset({study_uid})
    for resource_name, uid in named:
        if owners.get(uid, frozenset()) != expected:
            raise NotFoundException(resource_name, uid)


def assert_related_studies(
    *,
    require_write: bool,
    read_study_uids: Iterable[str | None] = (),
    write_study_uids: Iterable[str | None] = (),
    subpart_uids: Iterable[str | None] = (),
) -> None:
    """Under the delegated profile, the caller's reach to the other studies an
    operation reads or writes on purpose (section 3.2 rules 1 and 5).

    Every authority check runs before any lookup, so a caller without it gets
    403 first. A read study needs read authority and visibility, a write study
    write authority and visibility; a refusal names the uid, which the caller
    sent or the acted-on study stores as its parent. A subpart needs visibility
    with the authority of the operation, and its refusal names no study, since
    responses do not list a hidden subpart. None and blank values are skipped,
    and only the values passed are read. Outside the profile nothing happens.
    """
    if not _strict():
        return
    assert_study_authority(require_write=require_write)
    caller = _request_user()
    reads = _given_values(read_study_uids)
    writes = _given_values(write_study_uids)
    subparts = _given_values(subpart_uids)
    if reads or (subparts and not require_write):
        _assert_operation_scope(caller, require_write=False)
    if writes or (subparts and require_write):
        _assert_operation_scope(caller, require_write=True)

    for uid in [*reads, *writes]:
        if not study_visible_to_user(caller, None, study_uid=uid):
            raise NotFoundException("Study Definition", uid)
    for uid in subparts:
        if not study_visible_to_user(caller, None, study_uid=uid):
            raise NotFoundException(msg=_SUBPART_OUT_OF_SCOPE)
