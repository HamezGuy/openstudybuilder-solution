"""OSBI-3 (D-45, 2026-10-26), OSB_ISOLATION_DESIGN.md sections 3.4, 3.5, 4.1, 4.2
and 8: the route-level child check of the delegated study boundary.

``enforce_visible_study`` keeps its order (the path study, then the source
studies) and then, under the delegated profile, refuses a child path uid that the
path study does not own, before the handler runs. In strict mode a blank or padded
study path uid answers 404, the 9dd1f75d rule of the consumer API. Outside the
profile nothing changes and no owner query is made. Unit-level, monkeypatched
strict mode, the fixture shape of test_domain_isolation.py, synthetic studies only.

OSBI-4 (D-45): the route table. Every path parameter of every route of the
application that depends on enforce_visible_study is a study root, a source study,
a child or a classified non-child, and every route with a child names its study.
"""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from neomodel import db
from starlette.requests import Request

from clinical_mdr_api import main
from clinical_mdr_api.routers.studies import study_access, study_disease_milestones
from clinical_mdr_api.routers.studies.study import router as study_router
from clinical_mdr_api.services.studies import study as study_service
from clinical_mdr_api.services.studies import study_visibility as vis
from common.auth import rbac
from common.auth.dependencies import security
from common.exception_handlers import register_exception_handlers
from common.exceptions import (
    BusinessLogicException,
    ForbiddenException,
    NotFoundException,
)

_OWN = "Study_000999"
_FOREIGN = "Study_000998"
_MILESTONE = "StudyDiseaseMilestone_000777"
_HANDLER_REACHED = "The handler ran."
_NON_CHILD = [
    "adam_report",
    "consecutive_visit_group_uid",
    "from_version",
    "new_order",
    "to_version",
    "ts_parameter_term_uid",
    "uid",
]


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {_OWN}
    purpose = "interactive-domain-access"
    capabilities = {"study:read", "study:write"}
    roles = {"Study.Read", "Study.Write"}


def _scope(uid):
    return (
        (True, "tenant-synthetic", "active")
        if uid == _OWN
        else (True, "another-tenant", "active")
    )


def _never(*_args, **_kwargs):
    raise AssertionError("no lookup may run here")


@pytest.fixture(autouse=True)
def strict_domain_scope(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    monkeypatch.setattr(vis, "_request_user", SyntheticUser)
    monkeypatch.setattr(vis, "_study_scope", _scope)
    monkeypatch.setattr(
        vis, "_study_scopes", lambda uids: {uid: _scope(uid) for uid in uids}
    )


def _owners(owner_by_uid):
    """The owner seam, answering from a map of child uid to owning studies."""

    def study_child_owners(uids):
        return {uid: frozenset(owner_by_uid.get(uid, ())) for uid in uids}

    return study_child_owners


def _request(path_params, method="GET", path="/studies/x/items/y"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "path_params": path_params,
        }
    )


@pytest.fixture(name="owner_queries")
def fixture_owner_queries(monkeypatch):
    """The real owner seam runs against this stand-in for ``db.cypher_query``.

    Only the owner query may reach it: the scope seams are patched above, so
    every call recorded here is one owner query.
    """
    calls = []

    def cypher_query(query, params=None, **_kwargs):
        assert "StudySelection" in query and "apoc.coll.toSet" in query
        calls.append(params)
        return [[uid, [_OWN]] for uid in params["uids"]], None

    monkeypatch.setattr(db, "cypher_query", cypher_query)
    return calls


# Over HTTP, through the real study router


class _RecordingMilestoneService:
    calls: list = []

    def find_by_uid(self, *args, **kwargs):
        self.calls.append(("find_by_uid", args, kwargs))
        raise BusinessLogicException(msg=_HANDLER_REACHED)

    def reorder(self, *args, **kwargs):
        self.calls.append(("reorder", args, kwargs))
        raise BusinessLogicException(msg=_HANDLER_REACHED)

    def delete(self, *args, **kwargs):
        self.calls.append(("delete", args, kwargs))
        raise BusinessLogicException(msg=_HANDLER_REACHED)


@pytest.fixture(name="client")
def fixture_client(monkeypatch):
    _RecordingMilestoneService.calls = []
    monkeypatch.setattr(
        study_disease_milestones,
        "StudyDiseaseMilestoneService",
        _RecordingMilestoneService,
    )
    # The lock decorator of the writing routes runs inside the handler.
    monkeypatch.setattr(
        study_service.StudyService, "__init__", lambda self, *a, **k: None
    )
    monkeypatch.setattr(
        study_service.StudyService,
        "check_if_study_is_locked",
        lambda self, study_uid: False,
    )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(study_router)
    app.dependency_overrides[security.dependency] = lambda: None
    app.dependency_overrides[rbac.STUDY_READ.dependency] = lambda: None
    app.dependency_overrides[rbac.STUDY_WRITE.dependency] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("PATCH", "/order", {"new_order": 2}),
        ("DELETE", "", None),
    ],
)
def test_foreign_child_path_uid_answers_404_before_the_handler(
    monkeypatch, client, method, suffix, body
):
    monkeypatch.setattr(vis, "_study_child_owners", _owners({_MILESTONE: {_FOREIGN}}))

    response = client.request(
        method,
        f"/studies/{_OWN}/study-disease-milestones/{_MILESTONE}{suffix}",
        json=body,
    )

    assert response.status_code == 404, response.text
    assert _MILESTONE in response.text
    assert "Study Disease Milestone" in response.text
    assert _FOREIGN not in response.text
    assert not _RecordingMilestoneService.calls


def test_missing_and_doubly_owned_child_answer_the_same_404(monkeypatch, client):
    url = f"/studies/{_OWN}/study-disease-milestones/{_MILESTONE}"
    answers = []
    for owners in ({}, {_MILESTONE: {_OWN, _FOREIGN}}):
        monkeypatch.setattr(vis, "_study_child_owners", _owners(owners))
        response = client.get(url)
        answers.append((response.status_code, response.json().get("message")))

    assert answers[0] == answers[1]
    assert answers[0][0] == 404
    assert not _RecordingMilestoneService.calls


def test_own_child_path_uid_reaches_the_handler(monkeypatch, client):
    monkeypatch.setattr(vis, "_study_child_owners", _owners({_MILESTONE: {_OWN}}))

    response = client.get(f"/studies/{_OWN}/study-disease-milestones/{_MILESTONE}")

    assert response.status_code == 400, response.text
    assert _HANDLER_REACHED in response.text
    assert len(_RecordingMilestoneService.calls) == 1


# The dependency itself


def test_own_child_passes_with_exactly_one_owner_query(owner_queries):
    request = _request(
        {"study_uid": _OWN, "study_disease_milestone_uid": _MILESTONE}, "PATCH"
    )

    study_access.enforce_visible_study(request, _auth=None)

    assert owner_queries == [{"uids": [_MILESTONE]}]


def test_every_child_of_a_route_is_checked_in_one_owner_query(owner_queries):
    request = _request(
        {
            "study_uid": _OWN,
            "study_arm_uid": "StudyArm_000001",
            "study_branch_arm_uid": "StudyBranchArm_000001",
        }
    )

    study_access.enforce_visible_study(request, _auth=None)

    assert len(owner_queries) == 1
    assert sorted(owner_queries[0]["uids"]) == [
        "StudyArm_000001",
        "StudyBranchArm_000001",
    ]


def test_children_are_checked_against_the_path_study_by_resource_name(monkeypatch):
    seen = []
    monkeypatch.setattr(
        study_access,
        "assert_tied_to_study",
        lambda study_uid, **kwargs: seen.append((study_uid, kwargs)),
    )
    request = _request(
        {
            "study_uid": _OWN,
            "arm_uid": "StudyArm_000001",
            "study_arm_uid": "StudyArm_000002",
            "study_activity_request_uid": "StudyActivity_000003",
            "new_order": "2",
        }
    )

    study_access.enforce_visible_study(request, _auth=None)

    assert seen == [
        (
            _OWN,
            {
                "children": {
                    "Study Arm": ["StudyArm_000001", "StudyArm_000002"],
                    "Study Activity": ["StudyActivity_000003"],
                }
            },
        )
    ]


@pytest.mark.parametrize("name", _NON_CHILD)
def test_non_child_path_parameters_cost_no_owner_query(monkeypatch, name):
    monkeypatch.setattr(vis, "_study_child_owners", _never)
    monkeypatch.setattr(db, "cypher_query", _never)
    request = _request({"study_uid": _OWN, name: "Anything_000001"})

    study_access.enforce_visible_study(request, _auth=None)


def test_the_child_and_non_child_tables_have_the_design_names():
    assert len(study_access.CHILD_PATH_PARAMETERS) == 28
    assert sorted(study_access.NON_CHILD_PATH_PARAMETERS) == _NON_CHILD
    classified = [
        study_access.STUDY_ROOT_PATH_PARAMETERS,
        study_access.SOURCE_STUDY_PATH_PARAMETERS,
        set(study_access.CHILD_PATH_PARAMETERS),
        study_access.NON_CHILD_PATH_PARAMETERS,
    ]
    for index, names in enumerate(classified):
        for other in classified[index + 1 :]:
            assert not set(names) & set(other)


def test_the_path_study_is_checked_before_its_children(monkeypatch):
    monkeypatch.setattr(vis, "_study_child_owners", _never)
    request = _request(
        {"study_uid": _FOREIGN, "study_disease_milestone_uid": _MILESTONE}
    )

    with pytest.raises(NotFoundException) as refused:
        study_access.enforce_visible_study(request, _auth=None)
    assert _FOREIGN in refused.value.msg


# Blank and padded path study uids


@pytest.mark.parametrize("parameter", ["study_uid", "target_study_uid"])
@pytest.mark.parametrize("value", [" ", f" {_OWN}", f"{_OWN} ", f"{_OWN}\t"])
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_blank_or_padded_path_study_uid_answers_404_in_strict_mode(
    monkeypatch, parameter, value, method
):
    for seam in ("_study_scope", "_study_scopes", "_study_child_owners"):
        monkeypatch.setattr(vis, seam, _never)
    monkeypatch.setattr(db, "cypher_query", _never)
    path_params = {"study_uid": _OWN, parameter: value}
    request = _request(
        {**path_params, "study_disease_milestone_uid": _MILESTONE}, method
    )

    with pytest.raises(NotFoundException) as refused:
        study_access.enforce_visible_study(request, _auth=None)
    assert "Study Definition" in refused.value.msg


def test_padded_path_study_uid_keeps_the_403_of_a_caller_without_authority(
    monkeypatch,
):
    principal = SyntheticUser()
    principal.capabilities = {"study:read"}
    monkeypatch.setattr(vis, "_request_user", lambda: principal)
    monkeypatch.setattr(vis, "_study_child_owners", _never)

    with pytest.raises(ForbiddenException):
        study_access.enforce_visible_study(
            _request({"study_uid": f"{_OWN} "}, "PATCH"), _auth=None
        )


@pytest.mark.parametrize("value", [" ", f" {_OWN}", f"{_OWN} "])
def test_blank_or_padded_path_study_uid_is_unchanged_outside_the_profile(
    monkeypatch, value
):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(vis, "_study_child_owners", _never)
    monkeypatch.setattr(db, "cypher_query", _never)
    visible = []
    collections = []
    monkeypatch.setattr(
        study_access,
        "assert_study_uid_visible",
        lambda uid, *, require_write: visible.append((uid, require_write)),
    )
    monkeypatch.setattr(
        vis,
        "assert_collection_scope",
        lambda **kwargs: collections.append(kwargs["require_write"]),
    )

    study_access.enforce_visible_study(
        _request({"study_uid": value, "study_disease_milestone_uid": _MILESTONE}),
        _auth=None,
    )

    if value.strip():
        assert visible == [(value.strip(), False)]
        assert not collections
    else:
        assert not visible
        assert collections == [False]


# Outside the delegated profile


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_outside_the_profile_no_owner_query_is_made(monkeypatch, method):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(vis, "_request_user", lambda: None)
    monkeypatch.setattr(vis, "_study_child_owners", _never)
    monkeypatch.setattr(db, "cypher_query", _never)
    request = _request(
        {
            "study_uid": _FOREIGN,
            "study_disease_milestone_uid": _MILESTONE,
            "study_arm_uid": "StudyArm_000001",
        },
        method,
    )

    study_access.enforce_visible_study(request, _auth=None)


def test_outside_the_profile_a_collection_route_with_path_parameters_is_unchanged(
    monkeypatch,
):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(vis, "_request_user", lambda: None)
    monkeypatch.setattr(db, "cypher_query", _never)

    study_access.enforce_visible_study(
        _request({"study_standard_version_uid": "Child_1"}, path="/x/Child_1"),
        _auth=None,
    )


def test_strict_collection_route_with_path_parameters_is_refused():
    request = _request({"uid": "Objective_000001"}, path="/objectives/x/studies")

    with pytest.raises(ForbiddenException):
        study_access.enforce_visible_study(request, _auth=None)


# The route table (OSBI-4): every path parameter of every route of the application
# that depends on enforce_visible_study is classified, and every route with a child
# parameter names its study.

# The seven routers that mount the dependency, the twelve modules that add their
# routes to the study router (OSB_ISOLATION_DESIGN.md section 4.1) and the three
# that add theirs to the studies router.
_GUARDED_ROUTE_MODULES = {
    "clinical_mdr_api.routers.ddf.study_definitions",
    "clinical_mdr_api.routers.integrations.edc_export",
    "clinical_mdr_api.routers.integrations.study_authority",
    "clinical_mdr_api.routers.listings.listings_adam",
    "clinical_mdr_api.routers.listings.listings_sdtm",
    "clinical_mdr_api.routers.studies.studies",
    "clinical_mdr_api.routers.studies.study",
    "clinical_mdr_api.routers.ctr_xml.ctr_xml",
    "clinical_mdr_api.routers.studies.study_activity_instructions",
    "clinical_mdr_api.routers.studies.study_activity_schedule",
    "clinical_mdr_api.routers.studies.study_compound_dosing",
    "clinical_mdr_api.routers.studies.study_design_cell",
    "clinical_mdr_api.routers.studies.study_design_classes",
    "clinical_mdr_api.routers.studies.study_disease_milestones",
    "clinical_mdr_api.routers.studies.study_epochs",
    "clinical_mdr_api.routers.studies.study_soa_footnotes",
    "clinical_mdr_api.routers.studies.study_source_variables",
    "clinical_mdr_api.routers.studies.study_standard_version",
    "clinical_mdr_api.routers.studies.study_visits",
    "clinical_mdr_api.routers.studies.study_design_figure",
    "clinical_mdr_api.routers.studies.study_flowchart",
    "clinical_mdr_api.routers.studies.study_interventions",
}


def _depends_on(dependant, call):
    """Whether the dependency tree of a route reaches ``call``, whether it was
    mounted on the router or declared on the route."""
    return any(
        sub.call is call or _depends_on(sub, call) for sub in dependant.dependencies
    )


@pytest.fixture(name="guarded_routes", scope="module")
def fixture_guarded_routes():
    """(methods and path, path parameter names, endpoint module) of every route of
    the application that depends on enforce_visible_study."""
    # The application holds included routers, not their routes: the route
    # contexts carry each route's full path and its router-level dependencies.
    return [
        (
            f"{','.join(sorted(route.methods))} {route.path}",
            set(route.param_convertors),
            route.endpoint.__module__,
        )
        for route in iter_route_contexts(main.app.routes)
        if isinstance(route.original_route, APIRoute)
        and _depends_on(route.dependant, study_access.enforce_visible_study)
    ]


def test_the_guarded_routes_are_those_of_the_design_modules(guarded_routes):
    assert {module for _route, _names, module in guarded_routes} == (
        _GUARDED_ROUTE_MODULES
    )


def test_every_path_parameter_of_a_guarded_route_is_classified(guarded_routes):
    classified = (
        study_access.STUDY_ROOT_PATH_PARAMETERS
        | study_access.SOURCE_STUDY_PATH_PARAMETERS
        | set(study_access.CHILD_PATH_PARAMETERS)
        | study_access.NON_CHILD_PATH_PARAMETERS
    )
    unclassified = sorted(
        (name, route)
        for route, names, _module in guarded_routes
        for name in names - classified
    )
    for name, route in unclassified:
        print(f"unclassified path parameter {name} on {route}")

    assert not unclassified, unclassified


def test_the_guarded_path_parameters_are_the_design_names(guarded_routes):
    """Section 4.1: study_uid, target_study_uid, study_source_uid, the 28 children
    and the 7 non-children, and no other name."""
    found = set().union(*(names for _route, names, _module in guarded_routes))

    assert found == (
        study_access.STUDY_ROOT_PATH_PARAMETERS
        | study_access.SOURCE_STUDY_PATH_PARAMETERS
        | set(study_access.CHILD_PATH_PARAMETERS)
        | study_access.NON_CHILD_PATH_PARAMETERS
    )


def test_every_guarded_route_with_a_child_parameter_has_study_uid(guarded_routes):
    without_study = sorted(
        route
        for route, names, _module in guarded_routes
        if names & set(study_access.CHILD_PATH_PARAMETERS) and "study_uid" not in names
    )
    for route in without_study:
        print(f"child path parameter without study_uid on {route}")

    assert not without_study, without_study
