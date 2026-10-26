"""Plan W2.3 (2026-09-21): the consumer API and the study-metadata listing honour the
delegated study scope like every study router. Unit-level, monkeypatched strict mode,
the same fixture shape as test_domain_isolation.py."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from clinical_mdr_api.services.studies import study_visibility as vis
from common.auth import dependencies as auth_dependencies
from common.exception_handlers import register_exception_handlers
from common.exceptions import ForbiddenException, NotFoundException
from consumer_api.shared import visibility as consumer_visibility
from consumer_api.v1 import main as consumer_v1_main
from consumer_api.v1.main import router as consumer_v1_router


class SyntheticUser:
    tenant_id = "tenant-synthetic"
    study_ids = {"Study_000999"}
    purpose = "interactive-domain-access"
    capabilities = {"study:read"}
    roles = {"Study.Read"}


@pytest.fixture(autouse=True)
def strict_domain_scope(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", True)
    monkeypatch.setattr(vis, "_request_user", SyntheticUser)
    monkeypatch.setattr(
        vis,
        "_study_scope",
        lambda uid: (
            (True, "tenant-synthetic", "active")
            if uid == "Study_000999"
            else (True, "another-tenant", "active")
        ),
    )


class SyntheticWriter(SyntheticUser):
    capabilities = {"study:read", "study:write"}
    roles = {"Study.Read", "Study.Write"}


class SyntheticNoCapability(SyntheticUser):
    capabilities: set[str] = set()


def _request(path: str, query_params=None, **path_params):
    return SimpleNamespace(
        path_params=path_params,
        query_params=query_params or {},
        url=SimpleNamespace(path=path),
    )


def test_a_study_route_outside_the_assignment_answers_not_found():
    consumer_visibility.enforce_visible_consumer_study(
        _request("/v1/studies/Study_000999/study-visits", uid="Study_000999"),
        _auth=None,
    )
    with pytest.raises(NotFoundException):
        consumer_visibility.enforce_visible_consumer_study(
            _request("/v1/studies/Study_000998/study-visits", uid="Study_000998"),
            _auth=None,
        )


def test_the_study_list_keeps_only_assigned_studies():
    rows = [
        {"uid": "Study_000999", "id": "A"},
        {"uid": "Study_000998", "id": "B"},
        {"uid": "", "id": "C"},
    ]
    assert [
        row["id"] for row in consumer_visibility.visible_consumer_studies(rows)
    ] == ["A"]


def test_cross_study_collection_surfaces_fail_closed_in_delegated_mode():
    consumer_visibility.enforce_consumer_collection_scope(
        _request("/v1/studies"), _auth=None
    )
    for path in ("/v1/studies/audit-trail", "/v1/papillons/soa"):
        with pytest.raises(ForbiddenException):
            consumer_visibility.enforce_consumer_collection_scope(
                _request(path), _auth=None
            )


def test_study_metadata_listing_asserts_visibility_after_resolving_the_uid(monkeypatch):
    from clinical_mdr_api.domain_repositories.study_definitions import (
        study_definition_repository as repository_module,
    )
    from clinical_mdr_api.services.listings import listings_study as listings

    monkeypatch.setattr(
        repository_module.StudyDefinitionRepository,
        "find_uid_by_study_number",
        staticmethod(lambda **_kwargs: "Study_000998"),
    )
    service = listings.StudyMetadataListingService()
    with pytest.raises(NotFoundException):
        service.get_study_metadata("P1", "0998", version="1")


# OP-OSB1 (2026-10-25): the three consumer study routes that had no scope.


def test_soa_replacement_needs_write_on_an_assigned_study(monkeypatch):
    monkeypatch.setattr(vis, "_request_user", SyntheticWriter)
    consumer_visibility.enforce_writable_consumer_study(
        _request("/v1/studies/Study_000999/soa", uid="Study_000999"), _auth=None
    )
    with pytest.raises(NotFoundException):
        consumer_visibility.enforce_writable_consumer_study(
            _request("/v1/studies/Study_000998/soa", uid="Study_000998"), _auth=None
        )


def test_soa_replacement_refuses_a_reader_on_its_own_study():
    with pytest.raises(ForbiddenException, match="study:write"):
        consumer_visibility.enforce_writable_consumer_study(
            _request("/v1/studies/Study_000999/soa", uid="Study_000999"), _auth=None
        )


def test_consumer_study_create_fails_closed_in_delegated_mode(monkeypatch):
    monkeypatch.setattr(vis, "_request_user", SyntheticWriter)
    with pytest.raises(ForbiddenException, match="no exact delegated study boundary"):
        consumer_visibility.enforce_consumer_collection_write_scope(
            _request("/v1/studies"), _auth=None
        )


def test_consumer_study_create_still_needs_write_authority():
    with pytest.raises(ForbiddenException, match="study:write"):
        consumer_visibility.enforce_consumer_collection_write_scope(
            _request("/v1/studies"), _auth=None
        )


def _metadata_request(**query_params):
    return _request("/v1/papillons/study-metadata", query_params=query_params)


def test_study_metadata_by_number_reads_an_assigned_study(monkeypatch):
    seen = []

    def roots(project_id, study_number, subpart_acronym):
        seen.append((project_id, study_number, subpart_acronym))
        return ["Study_000999"]

    monkeypatch.setattr(consumer_visibility, "_study_roots_by_number", roots)
    consumer_visibility.enforce_visible_consumer_study_by_number(
        _metadata_request(project_id="P1", study_number="0999"), _auth=None
    )
    consumer_visibility.enforce_visible_consumer_study_by_number(
        _metadata_request(project_id="P1", study_number="0999", subpart_acronym="a"),
        _auth=None,
    )
    assert seen == [("P1", "0999", None), ("P1", "0999", "a")]


@pytest.mark.parametrize(
    "roots",
    [[], ["Study_000998"], ["Study_000998", "Study_000999"]],
    ids=["no-study", "unassigned", "one-of-two-unassigned"],
)
def test_study_metadata_by_number_outside_the_assignment_answers_not_found(
    monkeypatch, roots
):
    monkeypatch.setattr(
        consumer_visibility, "_study_roots_by_number", lambda *_args: roots
    )
    with pytest.raises(NotFoundException) as raised:
        consumer_visibility.enforce_visible_consumer_study_by_number(
            _metadata_request(project_id="P1", study_number="0998"), _auth=None
        )
    # The same answer as a study that does not exist: no uid is disclosed.
    assert raised.value.msg == "Study id P1-0998 not found."


def test_study_metadata_by_number_checks_the_caller_before_any_lookup(monkeypatch):
    monkeypatch.setattr(vis, "_request_user", SyntheticNoCapability)

    def roots(*_args):
        raise AssertionError("no study lookup without read authority")

    monkeypatch.setattr(consumer_visibility, "_study_roots_by_number", roots)
    with pytest.raises(ForbiddenException, match="study:read"):
        consumer_visibility.enforce_visible_consumer_study_by_number(
            _metadata_request(project_id="P1", study_number="0998"), _auth=None
        )


def test_study_metadata_without_selectors_is_left_to_route_validation(monkeypatch):
    def roots(*_args):
        raise AssertionError("no study lookup without both selectors")

    monkeypatch.setattr(consumer_visibility, "_study_roots_by_number", roots)
    consumer_visibility.enforce_visible_consumer_study_by_number(
        _metadata_request(project_id="P1"), _auth=None
    )


# AR-EO-08 (2026-10-26): the {uid} the scope check reads is the {uid} the handler
# receives. A blank or padded uid used to skip the check (blank) or pass it as the
# trimmed uid while the handler received the padded one.


class SyntheticWriterWithReaderToken(SyntheticWriter):
    """Holds the Study.Write role but only the study:read capability."""

    capabilities = {"study:read"}


@pytest.fixture(name="soa_client")
def fixture_soa_client(monkeypatch):
    calls = []

    class RecordingSoAService:
        def create_soa(self, study_uid, soa_input):  # pylint: disable=unused-argument
            calls.append(study_uid)
            return {"epochs": {}, "visits": {}, "activities": {}}

    class RoleHolder:
        def authorize(self, *_roles):
            return None

    monkeypatch.setattr(consumer_v1_main, "SoAService", RecordingSoAService)
    monkeypatch.setattr(auth_dependencies, "user", RoleHolder)
    app = FastAPI()
    register_exception_handlers(app, value_error=False)
    app.include_router(consumer_v1_router, prefix="/v1")
    app.dependency_overrides[auth_dependencies.validate_token] = lambda: None
    app.dependency_overrides[auth_dependencies.dummy_user_auth] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)

    def post(uid_segment):
        return client.post(
            f"/v1/studies/{uid_segment}/soa",
            json={"epochs": [], "visits": [], "activities": []},
        )

    return post, calls


def test_soa_replacement_reaches_the_handler_for_an_assigned_study(
    monkeypatch, soa_client
):
    post, calls = soa_client
    monkeypatch.setattr(vis, "_request_user", SyntheticWriter)
    response = post("Study_000999")
    assert response.status_code == 201, response.text
    assert calls == ["Study_000999"]


def test_soa_replacement_on_a_blank_uid_still_needs_write_authority(
    monkeypatch, soa_client
):
    post, calls = soa_client
    monkeypatch.setattr(vis, "_request_user", SyntheticWriterWithReaderToken)
    response = post("%20")
    assert response.status_code == 403, response.text
    assert "study:write" in response.text
    assert not calls


def test_soa_replacement_on_a_blank_uid_answers_not_found(monkeypatch, soa_client):
    post, calls = soa_client
    monkeypatch.setattr(vis, "_request_user", SyntheticWriter)
    response = post("%20")
    assert response.status_code == 404, response.text
    assert not calls


def test_soa_replacement_on_a_padded_assigned_uid_answers_not_found(
    monkeypatch, soa_client
):
    post, calls = soa_client
    monkeypatch.setattr(vis, "_request_user", SyntheticWriter)
    for segment in ("Study_000999%20", "%20Study_000999", "Study_000999%09"):
        response = post(segment)
        assert response.status_code == 404, (segment, response.text)
    assert not calls


@pytest.mark.parametrize("uid", [" ", "Study_000999 ", " Study_000999"])
def test_a_blank_or_padded_read_uid_answers_not_found(uid):
    with pytest.raises(NotFoundException):
        consumer_visibility.enforce_visible_consumer_study(
            _request("/v1/studies/x/study-visits", uid=uid), _auth=None
        )


def test_a_blank_read_uid_still_needs_read_authority(monkeypatch):
    monkeypatch.setattr(vis, "_request_user", SyntheticNoCapability)
    with pytest.raises(ForbiddenException, match="study:read"):
        consumer_visibility.enforce_visible_consumer_study(
            _request("/v1/studies/x/study-visits", uid=" "), _auth=None
        )


def test_a_blank_uid_is_refused_outside_delegated_mode(monkeypatch):
    monkeypatch.setattr(vis.settings, "delegated_claims_required", False)
    monkeypatch.setattr(vis, "_request_user", lambda: None)
    for enforce in (
        consumer_visibility.enforce_visible_consumer_study,
        consumer_visibility.enforce_writable_consumer_study,
    ):
        for uid in (" ", "Study_000999 "):
            with pytest.raises(NotFoundException):
                enforce(_request("/v1/studies/x/soa", uid=uid), _auth=None)


_STUDY_SELECTOR_QUERY_PARAMS = {
    "study_uid",
    "study_id",
    "study_number",
    "project",
    "project_id",
    "exclude_study_ids",
}
_WRITE_SCOPE_DEPENDENCIES = {
    consumer_visibility.enforce_writable_consumer_study,
    consumer_visibility.enforce_consumer_collection_write_scope,
}
_STUDY_SCOPE_DEPENDENCIES = {
    consumer_visibility.enforce_visible_consumer_study,
    consumer_visibility.enforce_writable_consumer_study,
}
_SCOPE_DEPENDENCIES = _STUDY_SCOPE_DEPENDENCIES | {
    consumer_visibility.enforce_visible_consumer_study_by_number,
    consumer_visibility.enforce_consumer_collection_scope,
    consumer_visibility.enforce_consumer_collection_write_scope,
}


def _consumer_routes():
    return [route for route in consumer_v1_router.routes if isinstance(route, APIRoute)]


def test_every_consumer_study_route_carries_a_scope_dependency():
    """A route with ``{uid}``, a non-GET method, a study selector query or a path
    under ``/studies`` must carry a scope dependency: a write one when it writes,
    a per-study one when it names a study in its path."""
    routes = _consumer_routes()
    missing = []
    checked = 0
    for route in routes:
        calls = {dependency.call for dependency in route.dependant.dependencies}
        query_names = {param.name for param in route.dependant.query_params}
        writes = bool(set(route.methods) - {"GET", "HEAD", "OPTIONS"})
        names_study = "{uid}" in route.path
        selects_study = bool(query_names & _STUDY_SELECTOR_QUERY_PARAMS)
        if not (
            names_study or writes or selects_study or route.path.startswith("/studies")
        ):
            continue
        checked += 1
        label = f"{sorted(route.methods)} {route.path}"
        if not calls & _SCOPE_DEPENDENCIES:
            missing.append(f"{label}: no scope dependency")
        if writes and not calls & _WRITE_SCOPE_DEPENDENCIES:
            missing.append(f"{label}: no write scope dependency")
        if names_study and not calls & _STUDY_SCOPE_DEPENDENCIES:
            missing.append(f"{label}: no per-study scope dependency")
    assert not missing, missing
    # The scan cannot pass vacuously: the 11 study routes of the consumer v1 API.
    assert checked >= 11, checked
