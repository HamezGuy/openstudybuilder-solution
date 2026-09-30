import asyncio
import threading
import time
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from common.auth.dependencies import validate_token
from clinical_mdr_api.routers.integrations import selected_activity_item_observation as route
from clinical_mdr_api.tests.unit.services.test_selected_activity_item_observation import selected_fixture
from clinical_mdr_api.tests.unit.services.test_native_item_observation_bounds import Request


def app_client():
    from clinical_mdr_api.routers.integrations.native_identity import router
    app = FastAPI()
    app.include_router(router, prefix="/integrations/platform-control")
    async def verified_provider(): return None
    app.dependency_overrides[validate_token] = verified_provider
    return TestClient(app)


def test_actual_registered_sibling_route_and_closed_safe_response():
    f = selected_fixture()
    with patch.object(route, "SelectedActivityItemService", return_value=f.service):
        response = app_client().post("/integrations/platform-control/selected-activity-items/current-observation", json=f.request.model_dump())
    assert response.status_code == 200
    value = response.json()
    assert value["selectedActivityReachabilityVerified"] is True
    assert value["semanticApprovalVerified"] is False and value["formApplicabilityVerified"] is False
    assert response.headers["cache-control"] == "no-store"
    assert "rationale" not in response.text and "elementId" not in response.text


def test_route_refuses_borrowed_native_scope_extra_fields_and_unbounded_body():
    f = selected_fixture()
    f.auth.user.study_ids.remove(f.request.nativeStudyId)
    with patch.object(route, "SelectedActivityItemService", return_value=f.service):
        client = app_client()
        path = "/integrations/platform-control/selected-activity-items/current-observation"
        assert client.post(path, json=f.request.model_dump()).status_code == 403
        assert client.post(path, json={**f.request.model_dump(), "formRef": "inferred"}).status_code == 422
        assert client.post(path, content=b"x" * 8193).status_code == 413
    assert "path_projection" not in f.repository.calls


def test_actual_machine_dependency_has_no_dummy_fallback():
    app = FastAPI()
    app.include_router(route.router)
    assert TestClient(app).post("/selected-activity-items/current-observation", json={}).status_code in {401, 403}


def test_selected_io_wait_is_bounded_by_the_original_credential():
    async def run():
        f = selected_fixture()
        f.service.clock, f.service.monotonic = time.time, time.monotonic
        f.auth.access_token_claims.exp = int(time.time()) + 2
        f.auth.access_token_claims.iat = int(time.time()) - 1
        entered, release = threading.Event(), threading.Event()
        original = f.repository.path_projection
        def pause(p, timeout):
            entered.set()
            assert release.wait(5)
            return original(p, timeout)
        f.repository.path_projection = pause
        with patch.object(route, "SelectedActivityItemService", return_value=f.service):
            task = asyncio.create_task(route.current_selected_activity_item(Request(f.request)))
            assert await asyncio.to_thread(entered.wait, 1)
            try:
                await asyncio.sleep(max(0, f.auth.access_token_claims.exp-time.time()) + 0.2)
                assert task.done(), "Native selected projection must not prolong original authority"
                try: await task
                except HTTPException as error: assert error.detail == "OSB_ITEM_READ_TIMEOUT"
                else: assert False
            finally:
                release.set()
                await asyncio.sleep(0.1)
    asyncio.run(run())
