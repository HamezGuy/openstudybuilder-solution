"""Registered specialist context route with real guards and synthetic native I/O."""

import asyncio
import copy
import time
from threading import BoundedSemaphore, Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from clinical_mdr_api.routers.integrations import (
    native_identity,
)
from clinical_mdr_api.routers.integrations import (
    native_item_observation as bounded_reader,
)
from clinical_mdr_api.routers.integrations import native_package_review_context as route
from clinical_mdr_api.tests.unit.services import test_native_package_v2 as fixture
from common.auth.dependencies import validate_token

PATH = "/native-packages-v2/review-context"


def client(monkeypatch):
    principal = SimpleNamespace(
        purpose="workflow-orchestration",
        capabilities={"package:release"},
        tenant_id=fixture.TENANT,
        study_ids={fixture.STUDY},
    )
    monkeypatch.setattr(native_identity, "user", lambda: principal)
    monkeypatch.setattr(
        route,
        "auth",
        lambda: SimpleNamespace(
            authentication_verified=True,
            access_token_claims=SimpleNamespace(exp=int(time.time()) + 300),
        ),
    )
    monkeypatch.setattr(
        route,
        "settings",
        SimpleNamespace(
            platform_commands_prototype_enabled=True,
            deployment_environment="prototype",
        ),
    )
    app = FastAPI()
    app.include_router(route.router)

    async def authenticated_provider():
        return None

    app.dependency_overrides[validate_token] = authenticated_provider
    return TestClient(app), principal


def request_body(store):
    return {
        "transformationCheckpointArtifact": store.checkpoint_artifact,
        "transformationCheckpoint": store.checkpoint,
    }


def test_registered_context_returns_exact_state_without_any_custody_write(monkeypatch):
    api, _ = client(monkeypatch)
    store = fixture.setup()
    store.inbound.clear()
    monkeypatch.setattr(route, "review_context", fixture.PACKAGE["review_context"])
    response = api.post(
        PATH,
        json=request_body(store),
        headers={"x-platform-study-id": fixture.STUDY},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["ok"] is True
    assert result["data"]["nativeStateHash"] == fixture.h(
        result["data"]["nativeState"], fixture.STATE["STATE_SCHEMA"]
    )
    assert result["data"]["productionEligible"] is False
    assert result["data"]["clinicalApproval"] is False
    assert not store.writes and not store.inbound


@pytest.mark.parametrize("scope", ["purpose", "capabilities", "tenant_id", "study_ids"])
def test_actual_platform_principal_refuses_wrong_scope_before_body_or_native_read(
    monkeypatch, scope
):
    api, principal = client(monkeypatch)
    setattr(principal, scope, set() if scope in {"capabilities", "study_ids"} else "")
    calls = []
    monkeypatch.setattr(route, "review_context", lambda **kwargs: calls.append(kwargs))
    response = api.post(
        PATH, content=b"invalid JSON", headers={"x-platform-study-id": fixture.STUDY}
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert not calls


@pytest.mark.parametrize(
    "environment,enabled", [("prototype", False), ("prod", True), ("production", True)]
)
def test_context_remains_default_off_and_prototype_only(
    monkeypatch, environment, enabled
):
    api, _ = client(monkeypatch)
    route.settings.deployment_environment = environment
    route.settings.platform_commands_prototype_enabled = enabled
    response = api.post(PATH, json={})
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"{}",
        b'{"transformationCheckpointArtifact":{},"transformationCheckpoint":{},"extra":true}',
        b'{"transformationCheckpointArtifact":{},"transformationCheckpoint":null}',
        b'{"transformationCheckpointArtifact":{},"transformationCheckpoint":{},"transformationCheckpoint":{}}',
        b'{"transformationCheckpointArtifact":{"key":1,"key":2},"transformationCheckpoint":{}}',
        b'{"transformationCheckpointArtifact":{},"transformationCheckpoint":{"value":NaN}}',
        b'{"transformationCheckpointArtifact":{},"transformationCheckpoint":{"value":1e999}}',
        b'{"transformationCheckpointArtifact":{},"transformationCheckpoint":{"value":'
        + b"9" * 400
        + b"}}",
        b'{"transformationCheckpointArtifact":{},"transformationCheckpoint":{"value":"\\ud800"}}',
        b"\xff",
    ],
)
def test_strict_body_refuses_invalid_values_before_native_read(monkeypatch, raw):
    api, _ = client(monkeypatch)
    calls = []
    monkeypatch.setattr(route, "review_context", lambda **kwargs: calls.append(kwargs))
    response = api.post(
        PATH, content=raw, headers={"x-platform-study-id": fixture.STUDY}
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert not calls


def test_http_body_limit_is_enforced_without_trusting_content_length(monkeypatch):
    api, _ = client(monkeypatch)
    monkeypatch.setattr(route, "MAX_REVIEW_CONTEXT_BYTES", 8)
    response = api.post(
        PATH,
        content=b"x" * 9,
        headers={"x-platform-study-id": fixture.STUDY, "content-length": "1"},
    )
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("authenticated,expiry", [(False, 300), (True, -1)])
def test_original_authentication_and_expiry_are_required(
    monkeypatch, authenticated, expiry
):
    api, _ = client(monkeypatch)
    monkeypatch.setattr(
        route,
        "auth",
        lambda: SimpleNamespace(
            authentication_verified=authenticated,
            access_token_claims=SimpleNamespace(exp=int(time.time()) + expiry),
        ),
    )
    calls = []
    monkeypatch.setattr(route, "review_context", lambda **kwargs: calls.append(kwargs))
    response = api.post(
        PATH,
        json={"transformationCheckpointArtifact": {}, "transformationCheckpoint": {}},
        headers={"x-platform-study-id": fixture.STUDY},
    )
    assert response.status_code == 403
    assert not calls


def test_native_reader_capacity_is_bounded_before_native_query(monkeypatch):
    api, _ = client(monkeypatch)
    monkeypatch.setattr(bounded_reader, "_slots", BoundedSemaphore(0))
    calls = []
    monkeypatch.setattr(route, "review_context", lambda **kwargs: calls.append(kwargs))
    response = api.post(
        PATH,
        json={"transformationCheckpointArtifact": {}, "transformationCheckpoint": {}},
        headers={"x-platform-study-id": fixture.STUDY},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "OSB_REVIEW_CONTEXT_READ_CAPACITY_UNAVAILABLE"
    assert not calls


def test_serialized_response_limit_is_enforced_in_the_bounded_reader(monkeypatch):
    api, _ = client(monkeypatch)
    monkeypatch.setattr(route, "MAX_REVIEW_CONTEXT_BYTES", 128)
    monkeypatch.setattr(route, "review_context", lambda **_kwargs: {"value": "é" * 100})
    response = api.post(
        PATH,
        json={"transformationCheckpointArtifact": {}, "transformationCheckpoint": {}},
        headers={"x-platform-study-id": fixture.STUDY},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "OSB_REVIEW_CONTEXT_RESPONSE_LIMIT"
    assert response.headers["cache-control"] == "no-store"


def test_stalled_native_reader_times_out_and_releases_owned_worker_after_completion(
    monkeypatch,
):
    api, _ = client(monkeypatch)
    monkeypatch.setattr(route, "REVIEW_CONTEXT_READ_SECONDS", 0.03)
    release, finished = Event(), Event()

    def native_read(**_kwargs):
        try:
            release.wait(2)
            return {}
        finally:
            finished.set()

    monkeypatch.setattr(route, "review_context", native_read)
    try:
        response = api.post(
            PATH,
            json={
                "transformationCheckpointArtifact": {},
                "transformationCheckpoint": {},
            },
            headers={"x-platform-study-id": fixture.STUDY},
        )
        assert response.status_code == 408
        assert response.json()["detail"] == "OSB_REVIEW_CONTEXT_READ_TIMEOUT"
        assert response.headers["cache-control"] == "no-store"
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(1)


@pytest.mark.parametrize("case,status", [("chunks", 413), ("timeout", 408)])
def test_stream_chunk_and_time_budgets_are_bounded(monkeypatch, case, status):
    client(monkeypatch)
    monkeypatch.setattr(route, "MAX_REVIEW_CONTEXT_CHUNKS", 2)
    monkeypatch.setattr(route, "REVIEW_CONTEXT_READ_SECONDS", 0.01)

    class StreamingRequest:
        headers = {"x-platform-study-id": fixture.STUDY}

        async def stream(self):
            if case == "timeout":
                await asyncio.sleep(0.03)
            for _ in range(3):
                yield b" "

    with pytest.raises(HTTPException) as error:
        asyncio.run(route.read_native_package_review_context(StreamingRequest()))
    assert error.value.status_code == status
    assert error.value.headers["cache-control"] == "no-store"


def test_native_refusal_retains_error_and_never_returns_success(monkeypatch):
    api, _ = client(monkeypatch)
    store = fixture.setup()
    body = copy.deepcopy(request_body(store))
    body["transformationCheckpointArtifact"]["descriptorHash"]["value"] = (
        "sha256:" + "0" * 64
    )
    monkeypatch.setattr(route, "review_context", fixture.PACKAGE["review_context"])
    monkeypatch.setattr(route, "OsbCandidateSetError", fixture.Error)
    response = api.post(PATH, json=body, headers={"x-platform-study-id": fixture.STUDY})
    assert response.status_code == 422
    assert (
        response.json()["detail"]["code"] == "OSB_RELEASE_ARTIFACT_DESCRIPTOR_MISMATCH"
    )
    assert not store.writes


def signed_review_route(monkeypatch):
    client(monkeypatch)
    monkeypatch.setattr(native_identity, "settings", route.settings)
    principal = SimpleNamespace(
        tenant_id=fixture.TENANT,
        study_ids={fixture.STUDY},
        sub="synthetic-reviewer",
        actor_chain=(),
        roles=(),
        purpose="workflow-orchestration",
        capabilities={"package:release"},
    )
    monkeypatch.setattr(native_identity, "user", lambda: principal)
    monkeypatch.setattr(
        native_identity,
        "execute_osb_atomic_signed_command",
        lambda _body, _principal, _system, _store, handler: handler(None),
    )


@pytest.mark.parametrize("guarded", [True, False])
def test_signed_review_route_passes_exact_expected_state_without_making_it_input_payload(
    monkeypatch, guarded
):
    signed_review_route(monkeypatch)
    calls = []

    def record(**kwargs):
        calls.append(kwargs)
        return {
            "payload": {
                "osbStudyIdentity": {
                    "nativeIdentity": "Study_990001",
                    "nativeVersion": "0.1",
                }
            },
            "payloadHash": {"value": "synthetic-review-hash"},
            "artifactRef": {"artifactId": "synthetic-review"},
        }

    monkeypatch.setattr(native_identity, "record_specialist_review", record)
    body = {
        "action": "osb.specialist-review.record",
        "targetCapability": "package:release",
        "platformStudyId": fixture.STUDY,
        "requestingActor": {"issuerQualifiedSubject": "synthetic-reviewer"},
        "inputPayload": {
            "transformationCheckpointArtifact": {"artifactId": "checkpoint"}
        },
    }
    if guarded:
        body["expectedTargetState"] = fixture.h({}, fixture.STATE["STATE_SCHEMA"])
    result = native_identity.execute_specialist_review_command(body)
    assert result["ok"] is True
    assert calls[0]["expected_target_state"] == body.get("expectedTargetState")
    assert calls[0]["input_payload"] == body["inputPayload"]


@pytest.mark.parametrize("guard", ["absent", "null", "valid"])
def test_declared_review_profile_requires_guard_through_actual_signed_route_handler(
    monkeypatch, guard
):
    signed_review_route(monkeypatch)
    store = fixture.setup()
    monkeypatch.setattr(
        native_identity,
        "record_specialist_review",
        fixture.PACKAGE["record_specialist_review"],
    )
    monkeypatch.setattr(native_identity, "OsbCandidateSetError", fixture.Error)
    body = {
        "action": "osb.specialist-review.record",
        "targetCapability": "package:release",
        "platformStudyId": fixture.STUDY,
        "requestingActor": {"issuerQualifiedSubject": "synthetic-reviewer"},
        "inputPayload": {
            "contractVersion": "OsbSpecialistReviewInputV1@1.0.0",
            "transformationCheckpointArtifact": store.checkpoint_artifact,
        },
    }
    if guard == "null":
        body["expectedTargetState"] = None
    elif guard == "valid":
        body["expectedTargetState"] = fixture.context(store)["nativeStateHash"]
    if guard != "valid":
        queries = []
        store.hook = lambda query, _params: queries.append(query)
        with pytest.raises(HTTPException) as error:
            native_identity.execute_specialist_review_command(body)
        assert error.value.status_code == 422
        assert (
            error.value.detail["code"]
            == "OSB_SPECIALIST_REVIEW_EXPECTED_STATE_REQUIRED"
        )
        assert not queries and not store.writes
    else:
        result = native_identity.execute_specialist_review_command(body)
        assert result["ok"] is True
        retained = fixture.review(store)
        assert retained["replay"] is True
        assert (
            retained["payloadHash"]
            == result["data"]["effectPayload"]["specialistReviewHash"]
        )
        assert store.writes == ["review"]


@pytest.mark.parametrize("version", [None, "", "OsbSpecialistReviewInputV1@9.0.0", []])
def test_unknown_supplied_review_profile_is_refused_before_native_read(
    monkeypatch, version
):
    signed_review_route(monkeypatch)
    store = fixture.setup()
    monkeypatch.setattr(
        native_identity,
        "record_specialist_review",
        fixture.PACKAGE["record_specialist_review"],
    )
    monkeypatch.setattr(native_identity, "OsbCandidateSetError", fixture.Error)
    body = {
        "action": "osb.specialist-review.record",
        "targetCapability": "package:release",
        "platformStudyId": fixture.STUDY,
        "requestingActor": {"issuerQualifiedSubject": "synthetic-reviewer"},
        "expectedTargetState": fixture.context(store)["nativeStateHash"],
        "inputPayload": {
            "contractVersion": version,
            "transformationCheckpointArtifact": store.checkpoint_artifact,
        },
    }
    queries = []
    store.hook = lambda query, _params: queries.append(query)
    with pytest.raises(HTTPException) as error:
        native_identity.execute_specialist_review_command(body)
    assert error.value.status_code == 422
    assert (
        error.value.detail["code"] == "OSB_SPECIALIST_REVIEW_INPUT_VERSION_UNSUPPORTED"
    )
    assert not queries and not store.writes


def test_missing_machine_token_is_not_interactive_dummy_authority():
    app = FastAPI()
    app.include_router(route.router)
    response = TestClient(app).post(PATH, json={})
    assert response.status_code in {401, 403}


def test_parent_router_registers_closed_context_and_machine_security():
    app = FastAPI()
    app.include_router(native_identity.router)
    operation = app.openapi()["paths"][PATH]["post"]
    assert operation["security"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "transformationCheckpointArtifact",
        "transformationCheckpoint",
    }
