from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.controlled_terminologies import (
    ct_term_generic_repository,
)
from clinical_mdr_api.models.study_selections.study import (
    StudyPatchRequestJsonModel,
)
from clinical_mdr_api.services.integrations.proposal_review import (
    ProposalReviewPrincipal,
)
from clinical_mdr_api.services.studies import study as study_service
from clinical_mdr_api.services.studies import study_visibility as vis
from clinical_mdr_api.services.user_info import UserInfoService
from clinical_mdr_api.tests.unit.domain.clinical_programme_aggregate.test_clinical_programme import (
    create_random_clinical_programme,
)
from clinical_mdr_api.tests.unit.domain.controlled_terminology_aggregates.test_ct_term_names import (
    create_random_ct_term_name_ar,
    create_random_ct_term_name_ars,
)
from clinical_mdr_api.tests.unit.domain.project_aggregate.test_project import (
    create_random_project,
)
from clinical_mdr_api.tests.unit.domain.study_definition_aggregate.test_root import (
    create_random_study,
)
from clinical_mdr_api.tests.unit.services.test_study import (
    UnitDefinitionRepositoryForTestImpl,
)
from common.exceptions import ForbiddenException, NotFoundException


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {"Study_000999"}
    purpose = "interactive-domain-access"
    capabilities = {"study:read", "study:write"}
    roles = {"Study.Read", "Study.Write"}


def _synthetic_scope(uid):
    return (
        (True, "tenant-synthetic", "active")
        if uid == "Study_000999"
        else (True, "another-tenant", "active")
    )


def _patch_scopes(monkeypatch, scope):
    """Patch the per-uid seam and the batched seam from the same per-uid map."""
    monkeypatch.setattr(vis, "_study_scope", scope)
    monkeypatch.setattr(
        vis, "_study_scopes", lambda uids: {uid: scope(uid) for uid in uids}
    )


@pytest.fixture(autouse=True)
def strict_domain_scope(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    monkeypatch.setattr(vis, "_request_user", SyntheticUser)
    _patch_scopes(monkeypatch, _synthetic_scope)


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
def test_write_scope_rejects_wrong_purpose_capability_or_role(
    monkeypatch, field, value
):
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
    vis.assert_collection_scope(
        require_write=False, route_path="/api/epochs/allowed-configs"
    )
    with pytest.raises(ForbiddenException):
        vis.assert_collection_scope(
            require_write=True, route_path="/api/epochs/allowed-configs"
        )
    principal = SyntheticUser()
    principal.capabilities = set()
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    with pytest.raises(ForbiddenException):
        vis.assert_collection_scope(
            require_write=False, route_path="/api/epochs/allowed-configs"
        )


@pytest.mark.parametrize(
    ("local_path", "request_path"),
    [("/list", "/studies/list"), ("/list", "/api/studies/list"), ("", "/studies")],
)
def test_included_router_uses_concrete_prefixed_collection_path(
    local_path, request_path
):
    from starlette.requests import Request

    from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": request_path,
            "headers": [],
            "query_string": b"",
            "path_params": {},
            "route": SimpleNamespace(path=local_path),
        }
    )
    enforce_visible_study(request, _auth=None)


def test_local_route_template_cannot_authorize_a_different_collection():
    from starlette.requests import Request

    from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/studies/headers",
            "headers": [],
            "query_string": b"",
            "path_params": {},
            "route": SimpleNamespace(path="/studies/list"),
        }
    )
    with pytest.raises(ForbiddenException):
        enforce_visible_study(request, _auth=None)


@pytest.mark.parametrize(
    "child_parameter",
    [
        "study_standard_version_uid",
        "study_visit_uid",
        "study_epoch_uid",
        "study_arm_uid",
        "study_cohort_uid",
        "study_element_uid",
        "study_objective_uid",
        "study_endpoint_uid",
        "study_criteria_uid",
        "study_activity_uid",
        "study_activity_instance_uid",
        "study_design_cell_uid",
    ],
)
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_nested_item_is_checked_against_its_parent_study(
    monkeypatch, child_parameter, method
):
    from starlette.requests import Request

    from clinical_mdr_api.routers.studies import study_access

    checked = []

    def assert_parent(uid, *, require_write):
        checked.append((uid, require_write))
        vis.assert_study_uid_visible(uid, require_write=require_write)

    monkeypatch.setattr(study_access, "assert_study_uid_visible", assert_parent)
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": "/studies/Study_000999/items/Child_1",
            "headers": [],
            "query_string": b"",
            "path_params": {"study_uid": "Study_000999", child_parameter: "Child_1"},
        }
    )
    study_access.enforce_visible_study(request, _auth=None)
    assert checked == [("Study_000999", method == "PATCH")]
    request.scope["path_params"]["study_uid"] = "Study_000998"
    with pytest.raises(NotFoundException):
        study_access.enforce_visible_study(request, _auth=None)


def test_nested_item_without_parent_and_cross_study_target_still_fail_closed():
    from starlette.requests import Request

    from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

    request = Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/study-standard-versions/Child_1",
            "headers": [],
            "query_string": b"",
            "path_params": {"study_standard_version_uid": "Child_1"},
        }
    )
    with pytest.raises(ForbiddenException):
        enforce_visible_study(request, _auth=None)
    request.scope["path_params"] = {
        "study_uid": "Study_000999",
        "target_study_uid": "Study_000998",
    }
    with pytest.raises(NotFoundException):
        enforce_visible_study(request, _auth=None)


COPY_FROM_STUDY_PATHS = [
    "/studies/{study_uid}/study-arms/copy-from-study/{study_source_uid}",
    "/studies/{study_uid}/study-elements/copy-from-study/{study_source_uid}",
    "/studies/{study_uid}/study-branch-arms/copy-from-study/{study_source_uid}",
]


def _copy_request(path, query_string=b"", path_params=None, method="POST"):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": query_string,
            "path_params": path_params or {},
        }
    )


@pytest.mark.parametrize("route_path", COPY_FROM_STUDY_PATHS)
def test_copy_from_study_refuses_a_source_study_the_caller_cannot_read(route_path):
    from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

    params = {"study_uid": "Study_000999", "study_source_uid": "Study_000998"}
    request = _copy_request(route_path.format(**params), path_params=params)
    with pytest.raises(NotFoundException):
        enforce_visible_study(request, _auth=None)


@pytest.mark.parametrize("route_path", COPY_FROM_STUDY_PATHS)
def test_copy_from_study_reads_the_source_and_writes_the_target(
    monkeypatch, route_path
):
    from clinical_mdr_api.routers.studies import study_access

    principal = SyntheticUser()
    principal.study_ids = {"Study_000999", "Study_000998"}
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    _patch_scopes(monkeypatch, lambda uid: (True, "tenant-synthetic", "active"))
    checked = []

    def record(uid, *, require_write):
        checked.append((uid, require_write))
        vis.assert_study_uid_visible(uid, require_write=require_write)

    monkeypatch.setattr(study_access, "assert_study_uid_visible", record)
    params = {"study_uid": "Study_000999", "study_source_uid": "Study_000998"}
    study_access.enforce_visible_study(
        _copy_request(route_path.format(**params), path_params=params), _auth=None
    )
    assert checked == [("Study_000999", True), ("Study_000998", False)]


@pytest.mark.parametrize(
    "query_string",
    [
        b"reference_study_uid=Study_000998&component_to_copy=high_level_study_design",
        b"reference_study_uid=Study_000999&reference_study_uid=Study_000998",
    ],
)
def test_copy_component_refuses_an_unassigned_reference_study(query_string):
    from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

    request = _copy_request(
        "/studies/Study_000999/copy-component",
        query_string=query_string,
        path_params={"study_uid": "Study_000999"},
        method="GET",
    )
    with pytest.raises(NotFoundException):
        enforce_visible_study(request, _auth=None)
    request = _copy_request(
        "/studies/Study_000999/copy-component",
        query_string=b"reference_study_uid=Study_000999",
        path_params={"study_uid": "Study_000999"},
        method="GET",
    )
    enforce_visible_study(request, _auth=None)


def test_copy_routes_name_the_source_study_parameters_the_dependency_checks():
    from clinical_mdr_api.routers.studies import studies, study, study_access

    study_paths = {route.path for route in study.router.routes}
    assert set(COPY_FROM_STUDY_PATHS) <= study_paths
    assert "study_source_uid" in study_access.SOURCE_STUDY_PATH_PARAMETERS
    copy_component = next(
        route
        for route in studies.router.routes
        if route.path == "/{study_uid}/copy-component"
    )
    query_names = {param.name for param in copy_component.dependant.query_params}
    assert "reference_study_uid" in query_names
    assert "reference_study_uid" in study_access.SOURCE_STUDY_QUERY_PARAMETERS


CLONE_INPUT = SimpleNamespace(
    project_number="SYN", study_number="901", study_acronym="SYNCOPY"
)


class _CloneServiceRecorder:
    """Stands in for StudyService: records every call the clone routes make."""

    calls: list = []

    def __init__(self):
        self.calls.append("init")

    def get_studies_list(self):
        self.calls.append("get_studies_list")
        return [SimpleNamespace(uid="Study_000999", id="SYN-900", acronym=None)]

    def clone_study(self, **kwargs):
        self.calls.append(("clone_study", kwargs["study_src_uid"]))
        return {"uid": "Study_001000"}

    def clone_study_async(self, **kwargs):
        self.calls.append(("clone_study_async", kwargs["study_src_uid"]))
        return {"uid": "Job_000001"}


@pytest.fixture(name="clone_service")
def fixture_clone_service(monkeypatch):
    from clinical_mdr_api.routers.studies import studies

    _CloneServiceRecorder.calls = []
    monkeypatch.setattr(studies, "StudyService", _CloneServiceRecorder)
    return _CloneServiceRecorder


@pytest.mark.parametrize(
    "route_path", ["/{study_uid}/clone", "/{study_uid}/clone/async"]
)
def test_clone_routes_refuse_a_copy_in_delegated_mode_and_create_nothing(
    clone_service, route_path
):
    from fastapi import BackgroundTasks

    from clinical_mdr_api.routers.studies import studies, study_access

    # The caller holds write on the source, so the router dependency admits it.
    params = {"study_uid": "Study_000999"}
    study_access.enforce_visible_study(
        _copy_request("/studies" + route_path.format(**params), path_params=params),
        _auth=None,
    )
    background_tasks = BackgroundTasks()
    with pytest.raises(ForbiddenException) as refused:
        if route_path.endswith("/async"):
            studies.clone_study_async(
                "Study_000999", CLONE_INPUT, background_tasks=background_tasks
            )
        else:
            studies.clone_study("Study_000999", CLONE_INPUT)
    assert "no exact delegated study boundary" in str(refused.value.msg)
    # No service was built, so no study root, tenant binding or job was written.
    assert not clone_service.calls
    assert not background_tasks.tasks
    route = next(r for r in studies.router.routes if r.path == route_path)
    assert route.methods == {"POST"}


def test_clone_refuses_an_unassigned_source_with_not_found(clone_service):
    from clinical_mdr_api.routers.studies import study_access

    with pytest.raises(NotFoundException):
        study_access.enforce_visible_study(
            _copy_request(
                "/studies/Study_000998/clone",
                path_params={"study_uid": "Study_000998"},
            ),
            _auth=None,
        )
    assert not clone_service.calls


def test_clone_routes_still_copy_outside_delegated_mode(monkeypatch, clone_service):
    from fastapi import BackgroundTasks

    from clinical_mdr_api.routers.studies import studies

    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)

    assert studies.clone_study("Study_000999", CLONE_INPUT) == {"uid": "Study_001000"}
    assert studies.clone_study_async(
        "Study_000999", CLONE_INPUT, background_tasks=BackgroundTasks()
    ) == {"uid": "Job_000001"}
    assert clone_service.calls == [
        "init",
        ("clone_study", "Study_000999"),
        "init",
        "get_studies_list",
        ("clone_study_async", "Study_000999"),
    ]


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
    assert (
        "UNWIND $study_uids AS scoped_uid MATCH (sr:StudyRoot {uid: scoped_uid})"
        in query
    )
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
    assert (
        repository_module.StudyDefinitionRepository.get_studies_list(
            repository, study_uids=()
        )
        == []
    )


def test_list_service_passes_validated_scope_and_closes_repositories(monkeypatch):
    calls, closed = [], []
    item = {
        "uid": "Study_000999",
        "id": "SCOPED-001",
        "acronym": None,
        "subpart_acronym": None,
    }
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
    principal.assert_proposal_access("tenant-synthetic", "Study_000999", "Study.Write")

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


# AR-OSB-01: PATCH /studies/{uid} names a parent in its body, which the path
# dependency never sees. The service checks it, and the subparts a parent
# change or cascade rewrites, before reading them.
_OWN, _FOREIGN, _OWN_TENANT_PARENT, _MISSING = (
    "Study_000999",
    "Study_000998",
    "Study_000997",
    "Study_000996",
)


@pytest.fixture(name="parent_scope")
def fixture_parent_scope(monkeypatch):
    """Study_000997 is a second study of the caller's tenant, assigned to them."""
    principal = SyntheticUser()
    principal.study_ids = {_OWN, _OWN_TENANT_PARENT}
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    scopes = {
        _OWN: (True, "tenant-synthetic", "active"),
        _OWN_TENANT_PARENT: (True, "tenant-synthetic", "active"),
        _FOREIGN: (True, "another-tenant", "active"),
    }
    _patch_scopes(monkeypatch, lambda uid: scopes.get(uid, (False, None, None)))
    return principal


def _synthetic_study(uid, study_number, acronym):
    return create_random_study(
        lambda: uid,
        new_id_metadata_fixed_values={
            "project_number": "SYN",
            "study_number": study_number,
            "study_acronym": acronym,
        },
    )


@pytest.fixture(name="patch_service")
def fixture_patch_service(monkeypatch, parent_scope):
    monkeypatch.setattr(
        UserInfoService, "get_author_username_from_id", classmethod(lambda cls, i: i)
    )
    # ensure_transaction joins this stand-in instead of opening a session.
    monkeypatch.setattr(db, "_active_transaction", object(), raising=False)

    studies = {
        _OWN: _synthetic_study(_OWN, "1111", "OWNACR"),
        _FOREIGN: _synthetic_study(_FOREIGN, "2222", "SECRETACR"),
        _OWN_TENANT_PARENT: _synthetic_study(_OWN_TENANT_PARENT, "3333", "PARENTACR"),
    }
    repos = MagicMock()
    repos.study_definition_repository.find_by_uid.side_effect = (
        lambda uid, **_kwargs: studies.get(uid)
    )
    repos.ct_term_name_repository.find_by_uid.return_value = (
        create_random_ct_term_name_ar()
    )
    repos.ct_term_name_repository.find_by_uids.side_effect = (
        lambda term_uids, *_args, **_kwargs: create_random_ct_term_name_ars(
            term_uids=list(term_uids)
        )
    )
    repos.ct_term_name_repository.find_by_uids.__module__ = (
        ct_term_generic_repository.__name__
    )
    repos.project_repository.find_by_project_number.return_value = (
        create_random_project(
            clinical_programme_uid="CP", generate_uid_callback=lambda: "Project_1"
        )
    )
    repos.clinical_programme_repository.find_by_uid.return_value = (
        create_random_clinical_programme(generate_uid_callback=lambda: "CP")
    )
    units = UnitDefinitionRepositoryForTestImpl()
    repos.unit_definition_repository.find_all.side_effect = (
        lambda *_args, **_kwargs: units.find_all()
    )
    repos.unit_definition_repository.find_by_uid_2.side_effect = units.find_by_uid_2

    service = study_service.StudyService.__new__(study_service.StudyService)
    service.author_id = "synthetic-writer"
    service._repos = repos
    monkeypatch.setattr(
        service, "_get_next_available_study_subpart_id", lambda *_a, **_k: "a"
    )
    return SimpleNamespace(
        service=service, repos=repos, studies=studies, principal=parent_scope
    )


def _subpart_patch(parent_uid):
    return StudyPatchRequestJsonModel(
        study_parent_part_uid=parent_uid,
        current_metadata={
            "identification_metadata": {"study_subpart_acronym": "SUBA"},
            "study_description": {},
        },
    )


def _read_uids(repos):
    return [
        call.args[0] if call.args else call.kwargs.get("uid")
        for call in repos.study_definition_repository.find_by_uid.call_args_list
    ]


@pytest.mark.parametrize("dry", [True, False])
@pytest.mark.parametrize("parent_uid", [_FOREIGN, _MISSING])
def test_patch_refuses_an_unwritable_parent_before_reading_it(
    patch_service, dry, parent_uid
):
    request = _subpart_patch(parent_uid)

    with pytest.raises(NotFoundException) as refused:
        patch_service.service.patch(_OWN, dry, request)

    # The parent is never read, so no foreign identity is copied or returned,
    # and a foreign study answers exactly like one that does not exist.
    assert _read_uids(patch_service.repos) == [_OWN]
    ident = request.current_metadata.identification_metadata
    assert ident.study_number is None
    assert ident.study_acronym is None
    assert request.current_metadata.study_description.study_title is None
    assert "SECRETACR" not in str(refused.value.msg)
    assert str(refused.value.msg).replace(parent_uid, "<uid>") == str(
        NotFoundException("Study Definition", "<uid>").msg
    )
    repository = patch_service.repos.study_definition_repository
    repository.save.assert_not_called()
    repository.update_subpart_relationship.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [("capabilities", {"study:read"}), ("roles", {"Study.Read"})],
)
def test_patch_refuses_an_assigned_parent_the_caller_may_only_read(
    patch_service, field, value
):
    # OSB write authority is token-wide, so a read-only parent is a token
    # that reads, not writes, its assigned studies.
    setattr(patch_service.principal, field, value)

    with pytest.raises(ForbiddenException):
        patch_service.service.patch(_OWN, True, _subpart_patch(_OWN_TENANT_PARENT))

    assert _OWN_TENANT_PARENT not in _read_uids(patch_service.repos)


@pytest.mark.parametrize("dry", [True, False])
def test_patch_accepts_an_assigned_writable_parent_of_the_same_tenant(
    patch_service, dry
):
    result = patch_service.service.patch(_OWN, dry, _subpart_patch(_OWN_TENANT_PARENT))

    assert _OWN_TENANT_PARENT in _read_uids(patch_service.repos)
    assert result.study_parent_part.uid == _OWN_TENANT_PARENT
    assert result.current_metadata.identification_metadata.study_id == "SYN-3333-SUBA"
    relationship = (
        patch_service.repos.study_definition_repository.update_subpart_relationship
    )
    if dry:
        relationship.assert_not_called()
    else:
        assert relationship.call_args.args[1] == _OWN_TENANT_PARENT


def test_patch_refuses_to_detach_from_a_parent_the_caller_cannot_write(
    patch_service,
):
    # A link to a foreign root, as the hole could leave behind. Detaching
    # writes that root and renumbers its subparts.
    patch_service.studies[_OWN].study_parent_part_uid = _FOREIGN

    with pytest.raises(NotFoundException):
        patch_service.service.patch(
            _OWN,
            False,
            StudyPatchRequestJsonModel(
                study_parent_part_uid=None,
                current_metadata={"identification_metadata": {"study_number": None}},
            ),
        )

    assert _read_uids(patch_service.repos) == [_OWN]
    patch_service.repos.study_definition_repository.save.assert_not_called()


def test_patch_outside_delegated_mode_reads_the_parent_as_before(
    monkeypatch, patch_service
):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    checked = []
    # raising=False keeps this test meaningful on code without the check.
    monkeypatch.setattr(
        study_service,
        "assert_study_uid_visible",
        lambda uid, **kwargs: checked.append(uid),
        raising=False,
    )

    result = patch_service.service.patch(_OWN, True, _subpart_patch(_FOREIGN))

    assert not checked
    assert _FOREIGN in _read_uids(patch_service.repos)
    assert result.study_parent_part.uid == _FOREIGN


def test_parent_cascade_refuses_a_subpart_the_caller_cannot_write(patch_service):
    parent = SimpleNamespace(study_subpart_uids=[_OWN_TENANT_PARENT, _FOREIGN])

    with pytest.raises(NotFoundException):
        patch_service.service._cascade_update_subparts(parent)

    # Every subpart is checked first, so not even the writable one is rewritten.
    assert not _read_uids(patch_service.repos)
    patch_service.repos.study_definition_repository.save.assert_not_called()


def test_parent_cascade_outside_delegated_mode_rewrites_every_subpart(
    monkeypatch, patch_service
):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(
        study_service.StudyService,
        "_patch_prepare_new_id_metadata",
        lambda self, **_kwargs: None,
    )
    foreign = patch_service.studies[_FOREIGN]
    monkeypatch.setattr(foreign, "edit_metadata", lambda **_kwargs: None)
    parent = SimpleNamespace(
        study_subpart_uids=[_FOREIGN],
        current_metadata=patch_service.studies[_OWN].current_metadata,
    )

    patch_service.service._cascade_update_subparts(parent)

    assert _read_uids(patch_service.repos) == [_FOREIGN]
    patch_service.repos.study_definition_repository.save.assert_called_once_with(
        foreign
    )


def test_subpart_reorder_refuses_an_unwritable_parent_or_subpart(patch_service):
    service = patch_service.service

    # A subpart's detach or delete reorders its parent's subparts.
    with pytest.raises(NotFoundException):
        service.reorder_study_subparts(_FOREIGN)
    assert not _read_uids(patch_service.repos)

    patch_service.studies[_OWN].study_subpart_uids = [_FOREIGN]
    with pytest.raises(NotFoundException):
        service.reorder_study_subparts(_OWN)
    assert _read_uids(patch_service.repos) == [_OWN]
    patch_service.repos.study_definition_repository.save.assert_not_called()
