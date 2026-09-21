from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.studies import study as study_service
from clinical_mdr_api.services.studies import study_visibility as vis
from clinical_mdr_api.services.integrations.proposal_review import (
    ProposalReviewPrincipal,
)
from common.exceptions import ForbiddenException, NotFoundException


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {"Study_000999"}
    purpose = "interactive-domain-access"
    capabilities = {"study:read", "study:write"}
    roles = {"Study.Read", "Study.Write"}


@pytest.fixture(autouse=True)
def strict_domain_scope(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    monkeypatch.setattr(vis, "_request_user", lambda: SyntheticUser())
    monkeypatch.setattr(
        vis,
        "_study_scope",
        lambda uid: (True, "tenant-synthetic", "active")
        if uid == "Study_000999"
        else (True, "another-tenant", "active"),
    )


def test_exact_assignment_and_native_tenant_binding_are_required():
    vis.assert_study_uid_visible("Study_000999", require_write=True)

    with pytest.raises(NotFoundException):
        vis.assert_study_uid_visible("Study_000998")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("purpose", "wrong-purpose"),
        ("capabilities", {"study:read"}),
        ("roles", {"Study.Read"}),
    ],
)
def test_write_scope_rejects_wrong_purpose_capability_or_role(monkeypatch, field, value):
    principal = SyntheticUser()
    monkeypatch.setattr(principal, field, value)
    monkeypatch.setattr(vis, "_request_user", lambda: principal)

    with pytest.raises(ForbiddenException):
        vis.assert_study_uid_visible("Study_000999", require_write=True)


def test_collection_scope_never_turns_missing_authority_into_wildcard(monkeypatch):
    principal = SyntheticUser()
    principal.study_ids = set()
    monkeypatch.setattr(vis, "_request_user", lambda: principal)

    vis.assert_collection_scope(require_write=False, route_path="/api/studies")
    assert vis.study_visible_to_user(principal, None, study_uid=None) is False


@pytest.mark.parametrize(
    ("route_path", "require_write"),
    [
        ("/api/studies/list", True),
        ("/api/studies/headers", False),
        ("/api/studies/template", False),
        ("/api/studies", True),
    ],
)
def test_legacy_unscoped_collection_routes_fail_closed(route_path, require_write):
    with pytest.raises(ForbiddenException):
        vis.assert_collection_scope(
            require_write=require_write,
            route_path=route_path,
        )


def test_scoped_list_route_is_read_only():
    vis.assert_collection_scope(require_write=False, route_path="/api/studies/list")


def test_epoch_configuration_read_keeps_delegated_operation_scope(monkeypatch):
    vis.assert_collection_scope(require_write=False, route_path="/api/epochs/allowed-configs")
    with pytest.raises(ForbiddenException):
        vis.assert_collection_scope(require_write=True, route_path="/api/epochs/allowed-configs")
    principal = SyntheticUser()
    principal.capabilities = set()
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    with pytest.raises(ForbiddenException):
        vis.assert_collection_scope(require_write=False, route_path="/api/epochs/allowed-configs")


@pytest.mark.parametrize(
    ("local_path", "request_path"),
    [("/list", "/studies/list"), ("/list", "/api/studies/list"), ("", "/studies")],
)
def test_included_router_uses_concrete_prefixed_collection_path(local_path, request_path):
    from starlette.requests import Request
    from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

    request = Request({
        "type": "http", "method": "GET", "path": request_path,
        "headers": [], "query_string": b"", "path_params": {},
        "route": SimpleNamespace(path=local_path),
    })
    enforce_visible_study(request, _auth=None)


def test_local_route_template_cannot_authorize_a_different_collection():
    from starlette.requests import Request
    from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

    request = Request({
        "type": "http", "method": "GET", "path": "/studies/headers",
        "headers": [], "query_string": b"", "path_params": {},
        "route": SimpleNamespace(path="/studies/list"),
    })
    with pytest.raises(ForbiddenException):
        enforce_visible_study(request, _auth=None)


@pytest.mark.parametrize("child_parameter", [
    "study_standard_version_uid", "study_visit_uid", "study_epoch_uid",
    "study_arm_uid", "study_cohort_uid", "study_element_uid",
    "study_objective_uid", "study_endpoint_uid", "study_criteria_uid",
    "study_activity_uid", "study_activity_instance_uid", "study_design_cell_uid",
])
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_nested_item_is_checked_against_its_parent_study(monkeypatch, child_parameter, method):
    from starlette.requests import Request
    from clinical_mdr_api.routers.studies import study_access

    checked = []
    def assert_parent(uid, *, require_write):
        checked.append((uid, require_write))
        vis.assert_study_uid_visible(uid, require_write=require_write)
    monkeypatch.setattr(study_access, "assert_study_uid_visible", assert_parent)
    request = Request({
        "type": "http", "method": method, "path": "/studies/Study_000999/items/Child_1",
        "headers": [], "query_string": b"",
        "path_params": {"study_uid": "Study_000999", child_parameter: "Child_1"},
    })
    study_access.enforce_visible_study(request, _auth=None)
    assert checked == [("Study_000999", method == "PATCH")]
    request.scope["path_params"]["study_uid"] = "Study_000998"
    with pytest.raises(NotFoundException):
        study_access.enforce_visible_study(request, _auth=None)


def test_nested_item_without_parent_and_cross_study_target_still_fail_closed():
    from starlette.requests import Request
    from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

    request = Request({
        "type": "http", "method": "PATCH", "path": "/study-standard-versions/Child_1",
        "headers": [], "query_string": b"",
        "path_params": {"study_standard_version_uid": "Child_1"},
    })
    with pytest.raises(ForbiddenException):
        enforce_visible_study(request, _auth=None)
    request.scope["path_params"] = {
        "study_uid": "Study_000999", "target_study_uid": "Study_000998",
    }
    with pytest.raises(NotFoundException):
        enforce_visible_study(request, _auth=None)


@pytest.mark.parametrize("minimal_response", [True, False])
@pytest.mark.parametrize("deleted", [True, False])
def test_list_repository_queries_only_exact_assigned_roots(
    monkeypatch, minimal_response, deleted
):
    from clinical_mdr_api.domain_repositories.study_definitions import (
        study_definition_repository as repository_module,
    )

    calls = []
    monkeypatch.setattr(
        repository_module.db,
        "cypher_query",
        lambda query, params: (calls.append((query, params)) or ([], [])),
    )
    repository = SimpleNamespace(_check_not_closed=lambda: None)
    result = repository_module.StudyDefinitionRepository.get_studies_list(
        repository,
        minimal_response=minimal_response,
        deleted=deleted,
        has_study_endpoint=False,
        study_uids=("Study_000999",),
    )
    assert result == []
    query, params = calls[0]
    assert "UNWIND $study_uids AS scoped_uid MATCH (sr:StudyRoot {uid: scoped_uid})" in query
    assert params == {"study_uids": ["Study_000999"]}
    assert "NOT EXISTS((sv)-[:HAS_STUDY_ENDPOINT]->(:StudyEndpoint))" in query
    deletion_predicate = "EXISTS((sv)<-[:BEFORE]-(:Delete))"
    assert (f"NOT {deletion_predicate}" in query) is not deleted


def test_list_repository_empty_scope_never_queries_all_studies(monkeypatch):
    from clinical_mdr_api.domain_repositories.study_definitions import (
        study_definition_repository as repository_module,
    )

    def unexpected_query(*_args, **_kwargs):
        raise AssertionError("An empty assignment must not query the catalogue")

    monkeypatch.setattr(repository_module.db, "cypher_query", unexpected_query)
    repository = SimpleNamespace(_check_not_closed=lambda: None)
    assert repository_module.StudyDefinitionRepository.get_studies_list(
        repository, study_uids=()
    ) == []


def test_list_service_passes_validated_scope_and_closes_repositories(monkeypatch):
    calls, closed = [], []
    item = {"uid": "Study_000999", "id": "SCOPED-001", "acronym": None, "subpart_acronym": None}
    service = object.__new__(study_service.StudyService)
    service._repos = SimpleNamespace(
        study_definition_repository=SimpleNamespace(
            get_studies_list=lambda *args, **kwargs: (
                calls.append((args, kwargs)) or [item]
            )
        ),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(study_service, "_caller_may_see", lambda _item: True)
    result = service.get_studies_list(has_study_endpoint=True, deleted=True)
    assert result[0].uid == "Study_000999"
    assert calls[0][1] == {"study_uids": ("Study_000999",)}
    assert calls[0][0][3] is True
    assert calls[0][0][-1] is True
    assert closed == [True]


def test_list_service_invalid_assignment_fails_before_repository_read(monkeypatch):
    principal = SyntheticUser()
    principal.study_ids = {"Study_000999", "Study_000998"}
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    service = object.__new__(study_service.StudyService)
    service._repos = SimpleNamespace(
        study_definition_repository=SimpleNamespace(),
        close=lambda: None,
    )
    with pytest.raises(ForbiddenException):
        service.get_studies_list()


def test_assigned_collection_scope_rejects_any_invalid_binding(monkeypatch):
    principal = SyntheticUser()
    principal.study_ids = {"Study_000999", "Study_000998"}
    monkeypatch.setattr(vis, "_request_user", lambda: principal)

    with pytest.raises(ForbiddenException):
        vis.assigned_study_uids()


def test_mapping_context_requires_exact_native_study_in_delegated_mode():
    with pytest.raises(ForbiddenException):
        vis.assert_mapping_context_scope(None)

    vis.assert_mapping_context_scope("Study_000999", require_write=True)


def test_proposal_review_requires_purpose_and_capability():
    principal = ProposalReviewPrincipal(
        actor_id="synthetic-reviewer",
        human_user_id="synthetic-reviewer",
        token_id="synthetic-session",
        tenant_id="tenant-synthetic",
        scoped_study_ids=frozenset({"Study_000999"}),
        organization_ids=frozenset(),
        roles=frozenset({"Study.Write"}),
        authentication_verified=True,
        purpose="workflow-orchestration",
        capabilities=frozenset({"study:write"}),
        enforce_delegated_scope=True,
    )
    principal.assert_proposal_access(
        "tenant-synthetic", "Study_000999", "Study.Write"
    )

    invalid = ProposalReviewPrincipal(
        **{
            **principal.__dict__,
            "capabilities": frozenset({"study:read"}),
        }
    )
    with pytest.raises(ValueError, match="CAPABILITY_REQUIRED"):
        invalid.assert_proposal_access(
            "tenant-synthetic", "Study_000999", "Study.Write"
        )


def test_delegated_root_listing_never_calls_unscoped_repository(monkeypatch):
    requested = []

    class ExactRepository:
        @staticmethod
        def find_all(**_kwargs):
            raise AssertionError("delegated collection called unscoped find_all")

        @staticmethod
        def find_by_uid(uid):
            requested.append(uid)
            return {"uid": uid}

    repository = ExactRepository()
    repos = SimpleNamespace(
        study_definition_repository=repository,
        project_repository=SimpleNamespace(find_by_project_number=lambda _value: None),
        clinical_programme_repository=SimpleNamespace(find_by_uid=lambda _value: None),
        close=lambda: None,
    )
    service = object.__new__(study_service.StudyService)
    service._repos = repos

    monkeypatch.setattr(study_service, "delegated_study_scope_required", lambda: True)
    monkeypatch.setattr(
        study_service,
        "assigned_study_uids",
        lambda **_kwargs: ("Study_000999",),
    )
    monkeypatch.setattr(study_service, "_caller_may_see", lambda _item: True)
    monkeypatch.setattr(
        study_service.StudyService,
        "_models_compact_study_from_study_definition_ar",
        staticmethod(lambda study_definition_ar, **_kwargs: study_definition_ar),
    )
    monkeypatch.setattr(
        study_service,
        "service_level_generic_filtering",
        lambda items, **_kwargs: SimpleNamespace(items=items, total=len(items)),
    )

    result = service.get_all()

    assert requested == ["Study_000999"]
    assert result.items == [{"uid": "Study_000999"}]
