"""OSB_NATIVE_REGIMEN_WRITES_ENABLED (FX-OSB-04, N-OSBROLE) on the registered
native regimen and arm lifecycle POST routes, without a database."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from clinical_mdr_api.routers.studies import study
from clinical_mdr_api.routers.studies.study_access import enforce_visible_study
from clinical_mdr_api.services.studies import study as study_service_module
from clinical_mdr_api.tests.fixtures.null_adjudication_http import (
    install_native_exception_handlers,
)
from common.auth import rbac
from common.auth.dependencies import security
from common.config import settings

STUDY_UID = "Study_000001"
LIFECYCLE_EVENT = {
    "expected_study_version": "draft-1",
    "lifecycle_state": "Paused",
    "effective_date": "2026-10-26T00:00:00Z",
    "decision_reference": "SYNTHETIC-DECISION-1",
    "change_reason": "Synthetic switch test",
}
# (path, request path, body, router attribute of the service the route builds,
# the service method it calls)
ROUTES = [
    (
        "/studies/{study_uid}/regimens",
        f"/studies/{STUDY_UID}/regimens",
        {
            "expected_study_version": "draft-1",
            "arms": [{"local_key": "arm-a", "name": "Synthetic arm A"}],
        },
        "StudyRegimenService",
        "create",
    ),
    (
        "/studies/{study_uid}/study-arms/{study_arm_uid}/lifecycle-events",
        f"/studies/{STUDY_UID}/study-arms/StudyArm_000001/lifecycle-events",
        LIFECYCLE_EVENT,
        "StudyArmSelectionService",
        "create_lifecycle_event",
    ),
    (
        "/studies/{study_uid}/study-branch-arms/{study_branch_arm_uid}/lifecycle-events",
        f"/studies/{STUDY_UID}/study-branch-arms/StudyBranchArm_000001/lifecycle-events",
        LIFECYCLE_EVENT,
        # Branch arm lifecycle events are recorded by the study arm service.
        "StudyArmSelectionService",
        "create_lifecycle_event",
    ),
]
PATHS = [route[0] for route in ROUTES]
REQUESTS = [route[1:] for route in ROUTES]


class ReachedService(Exception):
    """The request passed every dependency and the lock check."""


def client():
    app = FastAPI()
    # The registered APIRoutes with their own dependencies; only authentication,
    # study visibility and the role check are authored seams.
    selected = APIRouter()
    selected.routes.extend(
        route
        for route in study.router.routes
        if route.path in PATHS and route.methods == {"POST"}
    )
    assert len(selected.routes) == 3
    app.include_router(selected)
    install_native_exception_handlers(app)

    async def allowed():
        return None

    app.dependency_overrides[security.dependency] = allowed
    app.dependency_overrides[enforce_visible_study] = allowed
    app.dependency_overrides[rbac.STUDY_WRITE.dependency] = allowed
    return TestClient(app)


def unlocked_study_service():
    service = MagicMock()
    service.check_if_study_is_locked.return_value = False
    return service


def test_the_switch_defaults_on():
    assert settings.native_regimen_writes_enabled is True
    assert study.require_native_regimen_writes() is None


def test_the_dependency_refuses_when_the_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "native_regimen_writes_enabled", False)
    with pytest.raises(HTTPException) as error:
        study.require_native_regimen_writes()
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "OSB_NATIVE_REGIMEN_WRITES_GOVERNED"
    assert "osb.regimen.onboard" in error.value.detail["message"]


@pytest.mark.parametrize("path", PATHS)
def test_each_route_carries_the_switch_after_the_write_role(path):
    (route,) = [
        route
        for route in study.router.routes
        if route.path == path and route.methods == {"POST"}
    ]
    calls = [dependant.call for dependant in route.dependant.dependencies]
    assert study.require_native_regimen_writes in calls
    assert calls.index(study.require_native_regimen_writes) > calls.index(
        rbac.STUDY_WRITE.dependency
    )


@pytest.mark.parametrize("url, body, service_name, method", REQUESTS)
def test_switch_off_refuses_403_before_the_lock_check_and_the_service(
    monkeypatch, url, body, service_name, method
):
    monkeypatch.setattr(settings, "native_regimen_writes_enabled", False)
    with (
        patch.object(study_service_module, "StudyService") as study_service,
        patch.object(study, service_name) as service,
    ):
        response = client().post(url, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "OSB_NATIVE_REGIMEN_WRITES_GOVERNED"
    assert "osb.regimen.onboard" in response.json()["message"]
    assert not study_service.mock_calls
    assert not service.mock_calls
    assert not getattr(service.return_value, method).called


@pytest.mark.parametrize("url, body, service_name, method", REQUESTS)
def test_switch_on_reaches_the_native_service(url, body, service_name, method):
    assert settings.native_regimen_writes_enabled is True
    with (
        patch.object(
            study_service_module, "StudyService", return_value=unlocked_study_service()
        ),
        patch.object(study, service_name) as service,
    ):
        getattr(service.return_value, method).side_effect = ReachedService
        with pytest.raises(ReachedService):
            client().post(url, json=body)
    getattr(service.return_value, method).assert_called_once()
