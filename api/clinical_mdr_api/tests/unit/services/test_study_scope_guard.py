"""OSBI-1 (D-45, 2026-10-26), OSB_ISOLATION_DESIGN.md sections 3.1, 3.6, 8 and 9:
the scope readers of the delegated study boundary. Assignments are evaluated once,
in one batched query, behind two policies: ``assigned_study_uids`` refuses an
invalid assignment with 403, ``visible_study_scope`` leaves it out.
``study_readable`` answers one study without raising, and ``assert_study_authority``
is the public form of the operation-scope rule. Results are memoized per request
and per caller in the request context. Unit-level, monkeypatched strict mode, the
same fixture shape as test_domain_isolation.py, with synthetic studies only."""

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
