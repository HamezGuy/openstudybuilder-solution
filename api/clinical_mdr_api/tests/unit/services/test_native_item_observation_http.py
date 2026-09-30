"""Registered HTTP route; authenticated provider context remains an authored seam."""
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.auth.dependencies import validate_token
from clinical_mdr_api.routers.integrations.native_item_observation import router
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationService
from clinical_mdr_api.tests.unit.services.test_native_item_observation import fixture


def client(f):
    app = FastAPI()
    app.include_router(router)
    async def provider():
        return None
    app.dependency_overrides[validate_token] = provider
    return TestClient(app)


def test_registered_route_safe_projection_and_no_store():
    f = fixture()
    with patch("clinical_mdr_api.routers.integrations.native_item_observation.NativeItemObservationService", return_value=f.service):
        response = client(f).post("/native-items/current-observation", json=f.request.model_dump())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["item"]["fields"]["datatype"] == "integer"
    assert response.json()["semanticApprovalVerified"] is False
    assert "rationale" not in response.text


@pytest.mark.parametrize("case", ["unverified", "expiry", "native-scope", "library-role", "study-selection"])
def test_registered_route_never_uses_compatibility_authority(case):
    f = fixture()
    if case == "unverified": f.auth.authentication_verified = False
    if case == "expiry": f.now[0] += 400
    if case == "native-scope": f.auth.user.study_ids.remove(f.request.nativeStudyId)
    if case == "library-role": f.auth.user.roles.remove("Library.Read")
    if case == "study-selection": f.request = f.request.model_copy(update={"scope": "study-selection"})
    with patch("clinical_mdr_api.routers.integrations.native_item_observation.NativeItemObservationService", return_value=f.service):
        response = client(f).post("/native-items/current-observation", json=f.request.model_dump())
    assert response.status_code in {403, 409, 422}
    assert f.repository.calls == []
    assert "item" not in response.json()


def test_http_body_caps_duplicate_keys_and_closed_fields():
    f = fixture()
    with patch("clinical_mdr_api.routers.integrations.native_item_observation.NativeItemObservationService") as service:
        api = client(f)
        assert api.post("/native-items/current-observation", content=b"x" * 8193).status_code == 413
        assert api.post("/native-items/current-observation", content='{"scope":"library-item","scope":"study-selection"}').status_code == 422
        assert api.post("/native-items/current-observation", json={**f.request.model_dump(), "privateRationale": "ignored?"}).status_code == 422
        service.assert_not_called()


def test_real_machine_dependency_not_dummy_auth_even_without_token():
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post("/native-items/current-observation", json={})
    assert response.status_code in {401, 403}


def test_registered_openapi_retains_closed_companion_contract():
    from clinical_mdr_api.routers.integrations.native_identity import router as actual_parent_router
    app = FastAPI()
    app.include_router(actual_parent_router)
    spec = app.openapi()
    operation = spec["paths"]["/native-items/current-observation"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["additionalProperties"] is False
    assert "mappingContextHash" in request_schema["required"]
    assert operation["security"]
    response_schema = spec["components"]["schemas"]["NativeItemObservationResponse"]
    assert response_schema["additionalProperties"] is False
    assert response_schema["properties"]["semanticApprovalVerified"]["const"] is False
