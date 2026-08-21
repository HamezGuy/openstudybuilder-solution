"""Exact tenant/study visibility for OSB native study objects.

Legacy author metadata remains a development compatibility aid only. When the
delegated profile is enabled, a signed token assignment plus an active native
tenant binding is required; Admin.Read is not a cross-study wildcard.
"""

from neomodel import db

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


def bind_study_to_current_tenant(study_uid: str) -> None:
    """Bind a newly-created native root; ambiguous legacy roots stay quarantined."""
    caller = _request_user()
    tenant_id = str(getattr(caller, "tenant_id", "") or "").strip()
    status = "active" if tenant_id else "quarantined"
    if _strict() and not tenant_id:
        raise ForbiddenException(msg="An exact tenant is required to create an OSB study.")
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
    if not rows or rows[0][0] != (tenant_id or None) or rows[0][1] != status:
        raise ForbiddenException(msg="OSB study tenant binding conflicts with the authenticated tenant.")


def study_visible_to_user(
    caller, version_author: str | None, study_uid: str | None = None
) -> bool:
    if _strict():
        if caller is None:
            return False
        uid = str(study_uid or "").strip()
        tenant_id = str(getattr(caller, "tenant_id", "") or "").strip()
        assigned = {
            str(value).strip()
            for value in (getattr(caller, "study_ids", None) or [])
            if str(value).strip()
        }
        if not uid or not tenant_id or uid not in assigned:
            return False
        exists, bound_tenant, status = _study_scope(uid)
        return exists and status == "active" and bound_tenant == tenant_id

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


def _assert_operation_scope(caller, require_write: bool) -> None:
    if not _strict():
        return
    if caller is None:
        raise ForbiddenException(msg="Authenticated delegated authority is required.")
    if getattr(caller, "purpose", "") not in {
        "interactive-domain-access",
        "workflow-orchestration",
    }:
        raise ForbiddenException(msg="Token purpose does not authorize OSB study access.")
    needed = "study:write" if require_write else "study:read"
    if needed not in set(getattr(caller, "capabilities", None) or []):
        raise ForbiddenException(msg=f"{needed} capability is required.")
    role_needed = "Study.Write" if require_write else "Study.Read"
    admin_role = "Admin.Write" if require_write else "Admin.Read"
    roles = set(getattr(caller, "roles", None) or [])
    if role_needed not in roles and admin_role not in roles:
        raise ForbiddenException(msg=f"{role_needed} or {admin_role} role is required.")


def assert_collection_scope(
    *, require_write: bool = False, route_path: str | None = None
) -> None:
    """Authorize only collection routes with a proven non-wildcard implementation.

    Legacy cross-study reports, headers, templates, and the generic study-create
    endpoint do not have a tenant/study-native query contract. They therefore
    remain unavailable when delegated claims are required instead of querying
    all studies and filtering after the fact.
    """
    _assert_operation_scope(_request_user(), require_write)
    if not _strict():
        return

    normalized = f"/{str(route_path or '').strip('/')}"
    read_only_root = not require_write and normalized.endswith("/studies")
    read_only_static_config = not require_write and normalized.endswith(
        "/study-elements/allowed-element-configs"
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
    tenant_id = str(getattr(caller, "tenant_id", "") or "").strip()
    if not tenant_id:
        raise ForbiddenException(msg="An exact tenant is required for OSB study access.")
    assigned = tuple(
        sorted(
            {
                str(value).strip()
                for value in (getattr(caller, "study_ids", None) or [])
                if str(value).strip()
            }
        )
    )
    for study_uid in assigned:
        exists, bound_tenant, status = _study_scope(study_uid)
        if not exists or status != "active" or bound_tenant != tenant_id:
            raise ForbiddenException(
                msg="A delegated OSB study assignment has no matching active tenant binding."
            )
    return assigned


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
