"""Actual registered routes and strict DTOs; authentication is an authored seam."""

from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from clinical_mdr_api.routers.studies import studies
from clinical_mdr_api.routers.studies.study_access import enforce_visible_study
from clinical_mdr_api.tests.fixtures.null_adjudication import (
    STUDY_UID,
    native_harness,
    request_payload,
)
from clinical_mdr_api.tests.fixtures.null_adjudication_http import (
    install_native_exception_handlers,
)
from common.auth import rbac
from common.auth.dependencies import security


def client(h, allowed=True, legacy=False):
    app = FastAPI()
    paths = (
        {"/{study_uid}"}
        if legacy
        else {"/{study_uid}", "/{study_uid}/null-adjudications"}
    )
    # Preserve the original APIRoute dependencies and validation, selecting only
    # these registered routes to avoid booting the full application.
    selected = APIRouter()
    selected.routes.extend(
        route
        for route in studies.router.routes
        if route.path in paths and route.methods <= {"GET", "PATCH"}
    )
    app.include_router(selected)
    install_native_exception_handlers(app)
    checks = []

    async def authenticated():
        checks.append("authenticated")

    async def visible():
        checks.append("visible")
        if not allowed:
            raise HTTPException(403, "Authored denied native scope")

    async def writable():
        checks.append("Study.Write")

    app.dependency_overrides[security.dependency] = authenticated
    app.dependency_overrides[enforce_visible_study] = visible
    app.dependency_overrides[rbac.STUDY_WRITE.dependency] = writable
    app.dependency_overrides[rbac.STUDY_READ.dependency] = authenticated

    return TestClient(app), checks


def test_guarded_route_executes_real_service_and_returns_request_bound_receipt():
    with native_harness() as h, patch.object(
        studies, "StudyService", return_value=h.service
    ):
        api, checks = client(h)
        capability = api.get(f"/{STUDY_UID}/null-adjudications")
        assert capability.status_code == 200
        assert len(capability.json()["fields"]) == 44
        response = api.patch(f"/{STUDY_UID}/null-adjudications", json=request_payload())
        assert response.status_code == 200, response.text
        assert response.json()["preconditions_verified"] is True
        assert response.json()["study_uid"] == STUDY_UID
        assert len(response.json()["checked_paths"]) == 2
        assert {"authenticated", "visible", "Study.Write"} <= set(checks)
        assert len([call for call in h.repository.calls if call[0] == "save"]) == 1


def test_route_preserves_scope_refusal_before_native_mutation():
    with native_harness() as h, patch.object(
        studies, "StudyService", return_value=h.service
    ):
        api, _ = client(h, allowed=False)
        assert (
            api.patch(
                f"/{STUDY_UID}/null-adjudications", json=request_payload()
            ).status_code
            == 403
        )
        assert h.repository.calls == []


@pytest.mark.parametrize("missing", ["expected_value", "expected_null_companion"])
def test_missing_guard_http_request_never_calls_service(missing):
    with native_harness() as h, patch.object(
        studies, "StudyService", return_value=h.service
    ):
        api, _ = client(h)
        payload = request_payload()
        del payload["adjudications"][0][missing]
        response = api.patch(f"/{STUDY_UID}/null-adjudications", json=payload)
        assert response.status_code == 400
        assert response.json()["type"] == "RequestValidationError"
        assert any(missing in item["field"] for item in response.json()["details"])
        assert h.repository.calls == []


def test_old_router_returns_404_and_does_not_route_guard_to_ordinary_patch():
    with native_harness() as h, patch.object(
        studies, "StudyService", return_value=h.service
    ):
        api, _ = client(h, legacy=True)
        assert api.get(f"/{STUDY_UID}/null-adjudications").status_code == 404
        assert (
            api.patch(
                f"/{STUDY_UID}/null-adjudications", json=request_payload()
            ).status_code
            == 404
        )
        assert h.repository.calls == []


def test_unsupported_pair_uses_documented_native_validation_response_without_saving():
    with native_harness() as h, patch.object(
        studies, "StudyService", return_value=h.service
    ):
        api, _ = client(h)
        payload = request_payload(
            companion_path="high_level_study_design.is_adaptive_design_null_value_code"
        )
        response = api.patch(f"/{STUDY_UID}/null-adjudications", json=payload)
        assert response.status_code == 400, response.text
        assert (
            "NULL_ADJUDICATION_UNKNOWN_VALUE_COMPANION_PAIR"
            in response.json()["message"]
        )
        assert not any(call[0] == "save" for call in h.repository.calls)


def test_registered_guarded_openapi_requires_closed_observations_and_has_no_dry_query():
    with native_harness() as h:
        api, _ = client(h)
        schema = api.app.openapi()
        operation = schema["paths"]["/{study_uid}/null-adjudications"]["patch"]
        assert all(parameter["name"] != "dry" for parameter in operation["parameters"])
        models = schema["components"]["schemas"]
        request = models["StudyNullAdjudicationRequest"]
        assert request["additionalProperties"] is False
        assert set(request["required"]) == {"contract_version", "adjudications"}
        row = models["NullAdjudication"]
        assert {"expected_value", "expected_null_companion"} <= set(row["required"])
        assert row["additionalProperties"] is False
        assert "412" in operation["responses"]
        assert "400" in operation["responses"]
