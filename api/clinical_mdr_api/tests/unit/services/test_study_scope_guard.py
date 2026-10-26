"""OSBI-1 (D-45, 2026-10-26), OSB_ISOLATION_DESIGN.md sections 3.1, 3.6, 8 and 9:
the scope readers of the delegated study boundary. Assignments are evaluated once,
in one batched query, behind two policies: ``assigned_study_uids`` refuses an
invalid assignment with 403, ``visible_study_scope`` leaves it out.
``study_readable`` answers one study without raising, and ``assert_study_authority``
is the public form of the operation-scope rule. Results are memoized per request
and per caller in the request context. Unit-level, monkeypatched strict mode, the
same fixture shape as test_domain_isolation.py, with synthetic studies only."""

from types import SimpleNamespace

import pytest
from neomodel import db
from starlette_context import request_cycle_context

from clinical_mdr_api.services.studies import study_visibility as vis
from common.exceptions import ForbiddenException, NotFoundException

_OWN = "Study_000999"
_FOREIGN = "Study_000998"
_MISSING = "Study_000997"
_QUARANTINED = "Study_000996"

_SCOPES = {
    _OWN: (True, "tenant-synthetic", "active"),
    _FOREIGN: (True, "another-tenant", "active"),
    _QUARANTINED: (True, "tenant-synthetic", "quarantined"),
}


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    purpose = "interactive-domain-access"
    capabilities = {"study:read", "study:write"}
    roles = {"Study.Read", "Study.Write"}

    def __init__(self, study_ids=None):
        self.study_ids = set(study_ids or {_OWN})


def _scope(uid):
    return _SCOPES.get(uid, (False, None, None))


def _never(*_args, **_kwargs):
    raise AssertionError("no study scope lookup may run here")


@pytest.fixture(name="strict")
def fixture_strict(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    principal = SyntheticUser()
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    return principal


@pytest.fixture(name="db_spy")
def fixture_db_spy(monkeypatch):
    """The real seams run against this stand-in for ``db.cypher_query``."""
    bound = {}
    calls = []

    def lookup(uid):
        return bound.get(uid) or _scope(uid)

    def cypher_query(query, params=None, **_kwargs):
        params = params or {}
        calls.append(params)
        if "MERGE (scope:DomainStudyScope" in query:
            bound[params["study_uid"]] = (True, params["tenant_id"], params["status"])
            return [[params["tenant_id"], params["status"]]], None
        if "uids" in params:
            return [[uid, *lookup(uid)] for uid in params["uids"]], None
        return [list(lookup(params["study_uid"]))], None

    monkeypatch.setattr(db, "cypher_query", cypher_query)
    return calls


def _own_tenant_studies(count):
    return {f"Study_{index:06d}" for index in range(100, 100 + count)}


# Outside the delegated profile


def test_outside_the_profile_nothing_is_looked_up(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(vis, "_request_user", lambda: SyntheticUser({_FOREIGN}))
    monkeypatch.setattr(vis, "_study_scope", _never)
    monkeypatch.setattr(vis, "_study_scopes", _never)
    monkeypatch.setattr(db, "cypher_query", _never)

    assert vis.visible_study_scope() is None
    assert vis.study_readable(_FOREIGN) is True
    assert vis.study_readable(_MISSING) is True
    assert vis.study_readable(None) is True
    vis.assert_study_authority(require_write=True)
    assert not vis.assigned_study_uids()


def test_outside_the_profile_a_caller_without_authority_is_not_refused(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    principal = SyntheticUser()
    principal.capabilities = set()
    principal.roles = set()
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    monkeypatch.setattr(db, "cypher_query", _never)

    vis.assert_study_authority(require_write=True)
    assert vis.study_readable(_OWN) is True
    assert vis.visible_study_scope() is None


# visible_study_scope


def test_visible_study_scope_drops_every_invalid_binding(monkeypatch, strict):
    strict.study_ids = {_OWN, _FOREIGN, _MISSING, _QUARANTINED, " ", ""}
    monkeypatch.setattr(vis, "_study_scope", _never)
    monkeypatch.setattr(
        vis, "_study_scopes", lambda uids: {uid: _scope(uid) for uid in uids}
    )

    assert vis.visible_study_scope() == (_OWN,)


def test_visible_study_scope_is_sorted_and_empty_without_assignments(
    monkeypatch, strict
):
    monkeypatch.setattr(
        vis,
        "_study_scopes",
        lambda uids: {uid: (True, "tenant-synthetic", "active") for uid in uids},
    )
    strict.study_ids = {"Study_000003", "Study_000001", " Study_000002 "}
    assert vis.visible_study_scope() == (
        "Study_000001",
        "Study_000002",
        "Study_000003",
    )

    strict.study_ids = set()
    monkeypatch.setattr(vis, "_study_scopes", _never)
    assert vis.visible_study_scope() == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("purpose", "wrong-purpose"),
        ("capabilities", {"study:write"}),
        ("capabilities", set()),
        ("roles", {"Study.Write"}),
        ("roles", set()),
        ("tenant_id", ""),
        ("tenant_id", "   "),
        ("tenant_id", None),
    ],
)
def test_visible_study_scope_is_empty_without_authority_or_tenant(
    monkeypatch, strict, field, value
):
    setattr(strict, field, value)
    monkeypatch.setattr(vis, "_study_scope", _never)
    monkeypatch.setattr(vis, "_study_scopes", _never)
    monkeypatch.setattr(db, "cypher_query", _never)

    assert vis.visible_study_scope() == ()


@pytest.mark.usefixtures("strict")
def test_visible_study_scope_is_empty_without_a_caller(monkeypatch):
    monkeypatch.setattr(vis, "_request_user", lambda: None)
    monkeypatch.setattr(vis, "_study_scopes", _never)

    assert vis.visible_study_scope() == ()


def test_visible_study_scope_admits_the_admin_read_role(monkeypatch, strict):
    strict.roles = {"Admin.Read"}
    monkeypatch.setattr(
        vis, "_study_scopes", lambda uids: {uid: _scope(uid) for uid in uids}
    )

    assert vis.visible_study_scope() == (_OWN,)


def test_visible_study_scope_makes_one_query_for_fifty_studies(strict, db_spy):
    strict.study_ids = _own_tenant_studies(50)

    with request_cycle_context({}):
        # None of the fifty is bound yet: all are dropped, in one query.
        assert vis.visible_study_scope() == ()
        assert len(db_spy) == 1
        assert sorted(db_spy[0]["uids"]) == sorted(strict.study_ids)

    db_spy.clear()
    for uid in strict.study_ids:
        _SCOPES[uid] = (True, "tenant-synthetic", "active")
    try:
        with request_cycle_context({}):
            first = vis.visible_study_scope()
            second = vis.visible_study_scope()
            assert first == second == tuple(sorted(strict.study_ids))
            assert len(db_spy) == 1
            # study_readable shares the memo: no further query.
            assert vis.study_readable(first[0]) is True
            assert len(db_spy) == 1
    finally:
        for uid in strict.study_ids:
            _SCOPES.pop(uid, None)


@pytest.mark.usefixtures("strict")
def test_without_a_request_context_there_is_no_memo(monkeypatch):
    calls = []

    def scopes(uids):
        calls.append(tuple(uids))
        return {uid: _scope(uid) for uid in uids}

    monkeypatch.setattr(vis, "_study_scopes", scopes)
    # The unit suite may run inside a session-wide context; leave it for none.
    monkeypatch.setattr(vis.context, "exists", lambda: False)

    assert vis.visible_study_scope() == (_OWN,)
    assert vis.visible_study_scope() == (_OWN,)
    assert len(calls) == 2


@pytest.mark.usefixtures("strict")
def test_the_memo_never_serves_another_caller(monkeypatch):
    calls = []

    def scopes(uids):
        calls.append(tuple(uids))
        return {uid: _scope(uid) for uid in uids}

    monkeypatch.setattr(vis, "_study_scopes", scopes)
    other = SyntheticUser()
    with request_cycle_context({}):
        assert vis.visible_study_scope() == (_OWN,)
        monkeypatch.setattr(vis, "_request_user", lambda: other)
        assert vis.visible_study_scope() == (_OWN,)
    assert len(calls) == 2


# assigned_study_uids


def test_assigned_study_uids_still_refuses_an_invalid_binding(monkeypatch, strict):
    monkeypatch.setattr(
        vis, "_study_scopes", lambda uids: {uid: _scope(uid) for uid in uids}
    )
    monkeypatch.setattr(vis, "_study_scope", _scope)
    assert vis.assigned_study_uids() == (_OWN,)

    for invalid in (_FOREIGN, _MISSING, _QUARANTINED):
        strict.study_ids = {_OWN, invalid}
        with pytest.raises(ForbiddenException, match="no matching active tenant"):
            vis.assigned_study_uids()


def test_assigned_study_uids_keeps_its_authority_and_tenant_refusals(
    monkeypatch, strict
):
    monkeypatch.setattr(vis, "_study_scopes", _never)
    monkeypatch.setattr(vis, "_study_scope", _never)
    strict.capabilities = {"study:read"}
    with pytest.raises(ForbiddenException, match="study:write capability"):
        vis.assigned_study_uids(require_write=True)

    strict.capabilities = {"study:read", "study:write"}
    strict.tenant_id = " "
    with pytest.raises(ForbiddenException, match="exact tenant is required"):
        vis.assigned_study_uids()


def test_assigned_study_uids_makes_one_query_for_fifty_studies(strict, db_spy):
    strict.study_ids = _own_tenant_studies(50)
    for uid in strict.study_ids:
        _SCOPES[uid] = (True, "tenant-synthetic", "active")
    try:
        assert vis.assigned_study_uids() == tuple(sorted(strict.study_ids))
        assert len(db_spy) == 1
        with request_cycle_context({}):
            vis.assigned_study_uids()
            vis.assigned_study_uids()
            vis.visible_study_scope()
        assert len(db_spy) == 2
    finally:
        for uid in strict.study_ids:
            _SCOPES.pop(uid, None)


# study_readable


def test_study_readable_reads_only_an_assigned_study_of_the_tenant(monkeypatch, strict):
    strict.study_ids = {_OWN, _FOREIGN, _MISSING, _QUARANTINED}
    monkeypatch.setattr(vis, "_study_scope", _scope)

    assert vis.study_readable(_OWN) is True
    assert vis.study_readable(_FOREIGN) is False
    assert vis.study_readable(_MISSING) is False
    assert vis.study_readable(_QUARANTINED) is False


@pytest.mark.usefixtures("strict")
def test_study_readable_is_false_for_an_unassigned_or_blank_study(monkeypatch):
    monkeypatch.setattr(vis, "_study_scope", _never)

    assert vis.study_readable(_FOREIGN) is False
    assert vis.study_readable("Study_000123") is False
    assert vis.study_readable(None) is False
    assert vis.study_readable("  ") is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("purpose", "wrong-purpose"),
        ("capabilities", {"study:write"}),
        ("roles", {"Study.Write", "Admin.Write"}),
        ("tenant_id", ""),
    ],
)
def test_study_readable_is_false_without_read_authority(
    monkeypatch, strict, field, value
):
    setattr(strict, field, value)
    monkeypatch.setattr(vis, "_study_scope", _never)

    assert vis.study_readable(_OWN) is False


@pytest.mark.usefixtures("strict")
def test_study_readable_is_false_without_a_caller(monkeypatch):
    monkeypatch.setattr(vis, "_request_user", lambda: None)
    monkeypatch.setattr(vis, "_study_scope", _never)

    assert vis.study_readable(_OWN) is False


# assert_study_authority


def test_assert_study_authority_is_the_operation_scope_rule(monkeypatch, strict):
    monkeypatch.setattr(db, "cypher_query", _never)
    vis.assert_study_authority(require_write=True)
    vis.assert_study_authority(require_write=False)

    strict.capabilities = {"study:read"}
    with pytest.raises(ForbiddenException, match="study:write capability"):
        vis.assert_study_authority(require_write=True)
    vis.assert_study_authority(require_write=False)

    strict.roles = {"Library.Read"}
    with pytest.raises(ForbiddenException, match="Study.Read or Admin.Read"):
        vis.assert_study_authority(require_write=False)

    strict.purpose = "wrong-purpose"
    with pytest.raises(ForbiddenException, match="Token purpose"):
        vis.assert_study_authority(require_write=False)

    monkeypatch.setattr(vis, "_request_user", lambda: None)
    with pytest.raises(ForbiddenException, match="Authenticated delegated"):
        vis.assert_study_authority(require_write=False)


# The memo


@pytest.mark.usefixtures("strict")
def test_the_path_study_is_looked_up_once_per_request(db_spy):
    with request_cycle_context({}):
        vis.assert_study_uid_visible(_OWN, require_write=True)
        vis.assert_study_uid_visible(_OWN)
        assert vis.study_readable(_OWN) is True
        assert vis.visible_study_scope() == (_OWN,)
    assert len(db_spy) == 1

    with request_cycle_context({}):
        vis.assert_study_uid_visible(_OWN)
    assert len(db_spy) == 2


def test_a_refused_study_is_refused_again_from_the_memo(strict, db_spy):
    strict.study_ids = {_OWN, _FOREIGN}
    with request_cycle_context({}):
        for _ in range(2):
            with pytest.raises(NotFoundException):
                vis.assert_study_uid_visible(_FOREIGN)
        assert vis.study_readable(_FOREIGN) is False
        assert vis.visible_study_scope() == (_OWN,)
    assert len(db_spy) == 2


def test_binding_a_new_study_drops_its_memo_entry(strict, db_spy):
    new_uid = "Study_000555"
    strict.study_ids = {_OWN, new_uid}
    with request_cycle_context({}):
        assert vis.study_readable(new_uid) is False
        vis.bind_study_to_current_tenant(new_uid)
        assert vis.study_readable(new_uid) is True
        assert vis.visible_study_scope() == (new_uid, _OWN)
    # The lookup, the bind, the fresh lookup, then one batch for the one
    # assignment not yet in the memo.
    assert [sorted(params) for params in db_spy] == [
        ["study_uid"],
        ["status", "study_uid", "tenant_id"],
        ["study_uid"],
        ["uids"],
    ]
    assert db_spy[3]["uids"] == [_OWN]


# OSBI-2: the two checks and the collection-scope tightening (sections 3.1 to
# 3.4, 4.2 "beyond: hardening" and 8).

_WORKER_PLATFORM_STUDY = "4d6a0c3e-0000-4000-8000-000000000999"

# Child uid: (owners through the audit anchor, owners through the holder anchor).
# The holder anchor repeats a root once per version that holds the child.
_CHILDREN = {
    "StudyDataSupplier_000001": ([_OWN], [_OWN, _OWN, _OWN]),
    "StudyArm_000001": ([_OWN], [_OWN]),
    "StudyArm_000002": ([_OWN], [_OWN]),
    "StudyDataSupplier_000002": ([_FOREIGN], [_FOREIGN]),
    "StudyDataSupplier_000003": ([_OWN], [_OWN, _FOREIGN]),
    "StudyDataSupplier_000004": ([_FOREIGN], [_OWN]),
}
_OWN_CHILD = "StudyDataSupplier_000001"
_FOREIGN_CHILD = "StudyDataSupplier_000002"
_DOUBLY_OWNED_CHILD = "StudyDataSupplier_000003"
_SPLIT_CHILD = "StudyDataSupplier_000004"
_MISSING_CHILD = "StudyDataSupplier_000099"


@pytest.fixture(name="owner_spy")
def fixture_owner_spy(monkeypatch):
    """The real owner seam runs against this stand-in for ``db.cypher_query``.

    It answers the owner query as Cypher would, with apoc.coll.toSet over both
    anchors, and the scope queries from the same per-uid map as ``db_spy``.
    """
    calls = []

    def cypher_query(query, params=None, **_kwargs):
        params = params or {}
        calls.append((query, params))
        if "StudySelection" in query:
            assert "apoc.coll.toSet" in query
            rows = []
            for uid in params["uids"]:
                authors, holders = _CHILDREN.get(uid, ([], []))
                rows.append([uid, list(dict.fromkeys(authors + holders))])
            return rows, None
        if "uids" in params:
            return [[uid, *_scope(uid)] for uid in params["uids"]], None
        return [list(_scope(params["study_uid"]))], None

    monkeypatch.setattr(db, "cypher_query", cypher_query)
    return calls


def _owner_queries(calls):
    return [params for query, params in calls if "StudySelection" in query]


def _no_lookup_may_run(monkeypatch):
    for seam in ("_study_scope", "_study_scopes", "_study_child_owners"):
        monkeypatch.setattr(vis, seam, _never)
    monkeypatch.setattr(db, "cypher_query", _never)


def test_outside_the_profile_both_checks_do_nothing(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    principal = SyntheticUser({_FOREIGN})
    principal.capabilities = set()
    principal.roles = set()
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    _no_lookup_may_run(monkeypatch)

    vis.assert_tied_to_study(
        _OWN,
        children={"Study Data Supplier": [_FOREIGN_CHILD, _MISSING_CHILD]},
        body_study_uids=(_FOREIGN, "", " "),
    )
    vis.assert_related_studies(
        require_write=True,
        read_study_uids=(_FOREIGN,),
        write_study_uids=(_MISSING,),
        subpart_uids=(_FOREIGN,),
    )


# assert_tied_to_study: body study uids


@pytest.mark.usefixtures("strict")
def test_a_body_study_equal_to_the_acted_on_study_passes(monkeypatch):
    _no_lookup_may_run(monkeypatch)

    vis.assert_tied_to_study(_OWN, body_study_uids=(_OWN, None, _OWN))
    vis.assert_tied_to_study(_OWN, body_study_uids=[])
    vis.assert_tied_to_study(_OWN)


@pytest.mark.parametrize(
    "value",
    [
        _FOREIGN,  # another tenant's study
        _MISSING,  # no such study
        "",
        "   ",
        f" {_OWN}",  # no strip
        _OWN.lower(),  # no case folding
        "Study_000123",  # assigned, but not the acted-on study
    ],
)
def test_a_foreign_missing_blank_or_other_body_study_answers_404_with_no_lookup(
    monkeypatch, strict, value
):
    strict.study_ids = {_OWN, "Study_000123"}
    _no_lookup_may_run(monkeypatch)

    with pytest.raises(NotFoundException) as refused:
        vis.assert_tied_to_study(
            _OWN,
            children={"Study Data Supplier": _OWN_CHILD},
            body_study_uids=(_OWN, value),
        )
    assert refused.value.msg == f"Study Definition with UID '{value}' doesn't exist."


# assert_tied_to_study: children


@pytest.mark.usefixtures("strict")
def test_an_own_child_with_both_anchors_passes(owner_spy):
    vis.assert_tied_to_study(
        _OWN,
        children={"Study Data Supplier": _OWN_CHILD},
        body_study_uids=(_OWN,),
    )
    assert _owner_queries(owner_spy) == [{"uids": [_OWN_CHILD]}]


@pytest.mark.parametrize(
    "child", [_FOREIGN_CHILD, _MISSING_CHILD, _DOUBLY_OWNED_CHILD, _SPLIT_CHILD]
)
@pytest.mark.usefixtures("strict")
def test_a_foreign_missing_doubly_owned_or_split_child_answers_the_same_404(
    owner_spy, child
):
    with pytest.raises(NotFoundException) as refused:
        vis.assert_tied_to_study(
            _OWN,
            children={
                "Study Arm": ["StudyArm_000001"],
                "Study Data Supplier": child,
            },
        )
    assert refused.value.msg == f"Study Data Supplier with UID '{child}' doesn't exist."
    assert len(_owner_queries(owner_spy)) == 1


@pytest.mark.usefixtures("strict")
def test_a_padded_child_uid_is_not_stripped(owner_spy):
    with pytest.raises(NotFoundException) as refused:
        vis.assert_tied_to_study(_OWN, children={"Study Arm": " StudyArm_000001"})
    assert refused.value.msg == "Study Arm with UID ' StudyArm_000001' doesn't exist."
    assert _owner_queries(owner_spy) == [{"uids": [" StudyArm_000001"]}]


@pytest.mark.usefixtures("strict")
def test_none_values_none_lists_and_blanks_are_skipped(monkeypatch):
    _no_lookup_may_run(monkeypatch)

    vis.assert_tied_to_study(
        _OWN,
        children={
            "Study Data Supplier": None,
            "Study Arm": [None, "", "  "],
            "Study Branch Arm": (),
            "Study Cohort": "",
        },
        body_study_uids=(None,),
    )
    vis.assert_tied_to_study(_OWN, children=None)
    vis.assert_tied_to_study(_OWN, children={})


@pytest.mark.usefixtures("strict")
def test_many_child_uids_cost_one_query_and_a_second_call_none(owner_spy):
    children = {
        "Study Data Supplier": _OWN_CHILD,
        "Study Arm": ["StudyArm_000001", None, "StudyArm_000002"],
        "Study Branch Arm": iter(["StudyArm_000001"]),
    }
    with request_cycle_context({}):
        vis.assert_tied_to_study(_OWN, children=children)
        vis.assert_tied_to_study(
            _OWN, children={"Study Arm": ["StudyArm_000002", "StudyArm_000001"]}
        )
        vis.assert_tied_to_study(_OWN, children={"Study Data Supplier": _OWN_CHILD})
    queries = _owner_queries(owner_spy)
    assert len(queries) == 1
    assert sorted(queries[0]["uids"]) == [
        "StudyArm_000001",
        "StudyArm_000002",
        _OWN_CHILD,
    ]

    # A refused child is refused again from the memo, with no further query.
    owner_spy.clear()
    with request_cycle_context({}):
        for _ in range(2):
            with pytest.raises(NotFoundException):
                vis.assert_tied_to_study(_OWN, children={"Study Arm": _FOREIGN_CHILD})
    assert len(_owner_queries(owner_spy)) == 1


def test_a_worker_principal_passes_for_an_own_child(monkeypatch, owner_spy):
    """The governed regimen command's token holds no study:write and no Study
    role: the ownership check must not depend on the caller (section 3.5)."""
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    worker = SyntheticUser({_WORKER_PLATFORM_STUDY, _OWN})
    worker.purpose = "workflow-orchestration"
    worker.capabilities = {"regimen:onboard"}
    worker.roles = {"service"}
    monkeypatch.setattr(vis, "_request_user", lambda: worker)

    with request_cycle_context({}):
        vis.assert_tied_to_study(
            _OWN,
            children={"Study Arm": ["StudyArm_000001"], "Study Branch Arm": None},
            body_study_uids=(_OWN,),
        )
    assert len(_owner_queries(owner_spy)) == 1
    # The same caller holds no study authority of its own.
    with pytest.raises(ForbiddenException):
        vis.assert_study_authority(require_write=True)


# assert_related_studies


@pytest.mark.parametrize("require_write", [True, False])
def test_related_studies_answer_403_before_any_lookup(
    monkeypatch, strict, require_write
):
    strict.capabilities = set()
    _no_lookup_may_run(monkeypatch)

    with pytest.raises(ForbiddenException, match="capability is required"):
        vis.assert_related_studies(
            require_write=require_write,
            read_study_uids=(_FOREIGN,),
            write_study_uids=(_FOREIGN,),
            subpart_uids=(_FOREIGN,),
        )


@pytest.mark.usefixtures("strict")
def test_related_studies_pass_for_visible_studies(owner_spy):
    vis.assert_related_studies(
        require_write=True,
        read_study_uids=(_OWN,),
        write_study_uids=(_OWN,),
        subpart_uids=(_OWN,),
    )
    vis.assert_related_studies(require_write=False)
    assert owner_spy


@pytest.mark.parametrize("field", ["read_study_uids", "write_study_uids"])
@pytest.mark.parametrize("uid", [_FOREIGN, _MISSING, _QUARANTINED, "Study_000123"])
def test_a_foreign_read_or_write_study_answers_404_naming_it(
    strict, owner_spy, field, uid
):
    strict.study_ids = {_OWN, _FOREIGN, _QUARANTINED}
    with pytest.raises(NotFoundException) as refused:
        vis.assert_related_studies(require_write=False, **{field: (_OWN, uid)})
    assert refused.value.msg == f"Study Definition with UID '{uid}' doesn't exist."
    assert not _owner_queries(owner_spy)


def test_a_read_only_caller_asked_for_write_answers_403(monkeypatch, strict):
    strict.capabilities = {"study:read"}
    strict.roles = {"Study.Read"}
    _no_lookup_may_run(monkeypatch)

    with pytest.raises(ForbiddenException, match="study:write capability"):
        vis.assert_related_studies(require_write=True, read_study_uids=(_OWN,))
    with pytest.raises(ForbiddenException, match="study:write capability"):
        vis.assert_related_studies(require_write=False, write_study_uids=(_OWN,))
    with pytest.raises(ForbiddenException, match="study:write capability"):
        vis.assert_related_studies(require_write=True, subpart_uids=(_OWN,))


def test_a_write_only_caller_asked_to_read_a_study_answers_403(monkeypatch, strict):
    strict.capabilities = {"study:write"}
    strict.roles = {"Study.Write"}
    _no_lookup_may_run(monkeypatch)

    with pytest.raises(ForbiddenException, match="study:read capability"):
        vis.assert_related_studies(require_write=True, read_study_uids=(_OWN,))
    with pytest.raises(ForbiddenException, match="study:read capability"):
        vis.assert_related_studies(require_write=False, subpart_uids=(_OWN,))


@pytest.mark.parametrize("require_write", [True, False])
@pytest.mark.parametrize("uid", [_FOREIGN, _MISSING, "Study_000123"])
def test_a_hidden_subpart_answers_the_subpart_message_with_no_uid(
    strict, owner_spy, require_write, uid
):
    strict.study_ids = {_OWN, _FOREIGN}
    with pytest.raises(NotFoundException) as refused:
        vis.assert_related_studies(
            require_write=require_write, subpart_uids=[_OWN, uid]
        )
    assert refused.value.msg == "A subpart of this study is outside your study scope."
    assert "Study_" not in refused.value.msg
    assert owner_spy


@pytest.mark.usefixtures("strict")
def test_related_studies_skip_none_and_blank_and_read_only_what_is_passed(
    monkeypatch,
):
    looked_up = []

    def scope(uid):
        looked_up.append(uid)
        return _scope(uid)

    monkeypatch.setattr(vis, "_study_scope", scope)
    monkeypatch.setattr(vis, "_study_scopes", _never)
    monkeypatch.setattr(vis, "_study_child_owners", _never)
    # An AR-OSB1 style double that carries only study_subpart_uids.
    parent = SimpleNamespace(study_subpart_uids=[_OWN, None, ""])
    vis.assert_related_studies(
        require_write=True,
        read_study_uids=[None, " "],
        write_study_uids=(None,),
        subpart_uids=parent.study_subpart_uids,
    )
    assert looked_up == [_OWN]


# assert_collection_scope: path parameters (4.2, "beyond: hardening")


@pytest.mark.usefixtures("strict")
def test_strict_collection_scope_refuses_a_route_with_path_parameters(monkeypatch):
    monkeypatch.setattr(db, "cypher_query", _never)

    with pytest.raises(ForbiddenException, match="no exact delegated study boundary"):
        vis.assert_collection_scope(
            require_write=False,
            route_path="/api/objectives/O1/studies",
            path_parameters={"objective_uid": "O1"},
        )
    with pytest.raises(ForbiddenException, match="no exact delegated study boundary"):
        vis.assert_collection_scope(
            require_write=False,
            route_path="/api/epochs/allowed-configs",
            path_parameters={"uid": "x"},
        )
    vis.assert_collection_scope(
        require_write=False, route_path="/api/studies", path_parameters={}
    )
    vis.assert_collection_scope(
        require_write=False, route_path="/api/studies/list", path_parameters=None
    )
    # Callers that pass no path parameters (OSBI-3 adds them) are unchanged.
    vis.assert_collection_scope(
        require_write=False, route_path="/api/objectives/O1/studies"
    )


def test_collection_scope_ignores_path_parameters_outside_the_profile(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(vis, "_request_user", SyntheticUser)
    monkeypatch.setattr(db, "cypher_query", _never)

    for require_write in (False, True):
        vis.assert_collection_scope(
            require_write=require_write,
            route_path="/api/objectives/O1/studies",
            path_parameters={"objective_uid": "O1"},
        )
