"""Real carrier route guards, physical worker budget and exact owner headers."""

import asyncio
import copy
import time
from threading import BoundedSemaphore, Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from verify_signed_command_publication import make_command

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json_hash_ref,
    descriptor_hash,
)
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    command_intent,
)
from clinical_mdr_api.routers.integrations import native_identity
from clinical_mdr_api.routers.integrations import native_item_observation as bounded
from clinical_mdr_api.routers.integrations import native_package_carrier as route
from clinical_mdr_api.tests.unit.services import signed_command_test_fixtures
from clinical_mdr_api.tests.unit.services.test_atomic_signed_command import (
    NativeMemoryStore,
)
from clinical_mdr_api.tests.unit.services.test_native_package_carrier_flow import (
    canonical,
    flow,
    identity,
    network_custody,
    prepared,
    record_review,
)
from common.auth.dependencies import validate_token

PATH = "/native-packages-v2/carrier-review-context"


def client(monkeypatch, fixture=None):
    source = fixture or prepared()
    principal = SimpleNamespace(
        purpose="workflow-orchestration",
        capabilities={"package:release"},
        tenant_id=source["scope"]["platformTenantId"],
        study_ids={source["scope"]["platformStudyId"]},
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
            platform_commands_prototype_enabled=True, deployment_environment="prototype"
        ),
    )
    app = FastAPI()
    app.include_router(route.router)
    app.include_router(native_identity.router)

    async def authenticated():
        return None

    app.dependency_overrides[validate_token] = authenticated
    return TestClient(app), principal


def body(source=None):
    source = source or prepared()
    reference = next(
        item["artifact"]
        for item in source["artifacts"]
        if item["artifact"]["kind"] == "transformation-checkpoint"
    )
    return {
        "transformationCheckpointArtifact": reference,
        "osbStudyIdentity": identity(source),
    }


def test_real_registered_context_page_and_entry_have_exact_wire_bytes(
    monkeypatch, flow
):
    api, principal = client(monkeypatch, flow.f)
    request = {
        "transformationCheckpointArtifact": flow.built["artifact"],
        "osbStudyIdentity": identity(flow.f),
    }
    headers = {"x-platform-study-id": principal.study_ids.copy().pop()}
    response = api.post(PATH, json=request, headers=headers)
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    context = response.json()["data"]
    assert context["nativeStateHash"] == flow.captured["nativeStateHash"]
    page = api.post(
        PATH.replace("context", "page"),
        json={**request, "section": "terminalAtoms", "offset": 0, "limit": 2},
        headers=headers,
    )
    assert page.status_code == 200 and len(page.content) <= 262144
    entry = api.post(
        PATH.replace("context", "entry"),
        json={**request, "section": "terminalAtoms", "position": 0},
        headers=headers,
    )
    assert entry.status_code == 200
    assert entry.content == canonical(page.json()["data"]["items"][0]["value"]).encode(
        "utf-8"
    )
    assert "x-osb-inspection-proof" in entry.headers
    assert not flow.store.reviews and not flow.store.packages


@pytest.mark.parametrize("field", ["purpose", "capabilities", "study_ids", "tenant_id"])
def test_platform_scope_refused_before_invalid_body(monkeypatch, field):
    api, principal = client(monkeypatch)
    study = next(iter(principal.study_ids))
    setattr(principal, field, set() if field in {"capabilities", "study_ids"} else "")
    response = api.post(
        PATH, content=b"invalid", headers={"x-platform-study-id": study}
    )
    assert (
        response.status_code == 403 and response.headers["cache-control"] == "no-store"
    )


@pytest.mark.parametrize(
    "raw", [b"[]", b"{}", b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":"\\ud800"}', b"\xff"]
)
def test_closed_strict_request_refused_before_native_read(monkeypatch, raw):
    api, principal = client(monkeypatch)
    calls = []
    monkeypatch.setattr(
        route, "carrier_review_context", lambda **kwargs: calls.append(kwargs)
    )
    response = api.post(
        PATH,
        content=raw,
        headers={"x-platform-study-id": next(iter(principal.study_ids))},
    )
    assert (
        response.status_code == 422 and response.headers["cache-control"] == "no-store"
    )
    assert not calls


def test_whole_serialized_response_is_bounded(monkeypatch):
    api, principal = client(monkeypatch)
    monkeypatch.setattr(
        route, "carrier_review_context", lambda **kwargs: {"oversize": "é" * 140000}
    )
    response = api.post(
        PATH,
        json=body(),
        headers={"x-platform-study-id": next(iter(principal.study_ids))},
    )
    assert (
        response.status_code == 413 and response.headers["cache-control"] == "no-store"
    )


def test_body_byte_limit_ignores_forged_length(monkeypatch):
    api, principal = client(monkeypatch)
    response = api.post(
        PATH,
        content=b"x" * (128 * 1024 + 1),
        headers={
            "x-platform-study-id": next(iter(principal.study_ids)),
            "content-length": "1",
        },
    )
    assert response.status_code == 413


def test_shared_physical_worker_capacity_applies(monkeypatch):
    api, principal = client(monkeypatch)
    monkeypatch.setattr(bounded, "_slots", BoundedSemaphore(0))
    response = api.post(
        PATH,
        json=body(),
        headers={"x-platform-study-id": next(iter(principal.study_ids))},
    )
    assert (
        response.status_code == 503 and response.headers["cache-control"] == "no-store"
    )


@pytest.mark.parametrize(
    "mode", ["disabled", "production", "unauthenticated", "expired"]
)
def test_prototype_and_original_credentials_are_required(monkeypatch, mode):
    api, principal = client(monkeypatch)
    if mode == "disabled":
        route.settings.platform_commands_prototype_enabled = False
    elif mode == "production":
        route.settings.deployment_environment = "production"
    else:
        monkeypatch.setattr(
            route,
            "auth",
            lambda: SimpleNamespace(
                authentication_verified=mode != "unauthenticated",
                access_token_claims=SimpleNamespace(exp=int(time.time()) - 1),
            ),
        )
    response = api.post(
        PATH,
        json=body(),
        headers={"x-platform-study-id": next(iter(principal.study_ids))},
    )
    assert response.status_code in {403, 408, 503}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("boundary", ["cancellation", "deadline", "credential"])
def test_command_transaction_refuses_commit_after_cancellation_or_expiry(
    monkeypatch, boundary
):
    client(monkeypatch)
    reader = route._CarrierOperation(lambda value: value)
    reader.cancellation = Event()
    committed = []

    class Store:
        def serializable(self, command, callback):
            result = callback(object())
            committed.append(command)
            return result

    def handler(_transaction):
        if boundary == "cancellation":
            reader.cancellation.set()
        elif boundary == "deadline":
            reader.deadline = 0
        else:
            reader.expiry = 0
        return "write would commit"

    with pytest.raises(HTTPException):
        route._DeadlineCommandStore(Store(), reader).serializable({}, handler)
    assert not committed


def test_carrier_get_echoes_retained_descriptor_and_refuses_coherent_metadata_change(
    monkeypatch, flow
):
    api, principal = client(monkeypatch, flow.f)
    reference = flow.captured["nativeStateArtifact"]
    path = (
        f'/artifacts/carrier/{reference["artifactId"]}/{reference["artifactVersionId"]}'
    )
    headers = {
        "x-platform-study-id": next(iter(principal.study_ids)),
        "x-artifact-reference": flow.custody.encode_artifact_reference_header(
            reference
        ),
    }
    response = api.get(path, headers=headers)
    assert response.status_code == 200
    assert (
        flow.custody.decode_artifact_reference_header(
            response.headers["x-artifact-reference"]
        )
        == reference
    )
    changed = copy.deepcopy(reference)
    changed["producerVersion"] = "changed metadata"
    changed["descriptorHash"] = descriptor_hash(
        {
            **{key: value for key, value in changed.items() if key != "descriptorHash"},
            "contractVersion": "ArtifactDescriptorV1@1.0.0",
        }
    )
    response = api.get(
        path,
        headers={
            **headers,
            "x-artifact-reference": flow.custody.encode_artifact_reference_header(
                changed
            ),
        },
    )
    assert (
        response.status_code == 409 and response.headers["cache-control"] == "no-store"
    )


def test_published_specialist_route_uses_actual_retained_owner_descriptor(
    monkeypatch, flow
):
    _, review = record_review(flow)
    api, principal = client(monkeypatch, flow.f)
    monkeypatch.setattr(
        native_identity,
        "db",
        SimpleNamespace(
            cypher_query=lambda *_args: (
                [
                    [
                        canonical(review["payload"]),
                        review["payloadHash"]["value"],
                        canonical(review["artifactRef"]),
                    ]
                ],
                [],
            )
        ),
    )
    response = api.get(
        "/artifacts/specialist-reviews/" + review["artifactRef"]["artifactVersionId"],
        headers={
            "x-platform-study-id": next(iter(principal.study_ids)),
            "x-artifact-reference": flow.custody.encode_artifact_reference_header(
                review["artifactRef"]
            ),
        },
    )
    assert response.status_code == 200
    assert (
        flow.custody.decode_artifact_reference_header(
            response.headers["x-artifact-reference"]
        )
        == review["artifactRef"]
    )


def test_stream_chunk_count_is_bounded(monkeypatch):
    client(monkeypatch)

    class Request:
        async def stream(self):
            for _ in range(8193):
                yield b""

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            route._bytes(Request(), route._CarrierOperation(lambda value: value), 100)
        )
    assert error.value.status_code == 413


def test_registered_capture_uses_actual_atomic_executor_and_preserves_signed_replay(
    monkeypatch, flow
):
    api, principal = client(monkeypatch, flow.f)
    actor = "synthetic:authorized-capture-owner"
    principal.sub = actor
    principal.actor_chain = [{"subject": actor, "type": "human"}]
    principal.roles = {"osb-specialist"}
    # Bind the existing synthetic signer to this independently produced fixture's
    # tenant before it creates its envelope and hashes; validation stays real.
    monkeypatch.setattr(signed_command_test_fixtures, "TENANT", flow.scope["tenant_id"])
    store, publisher = NativeMemoryStore(), signed_command_test_fixtures.StubPublisher()
    original_execute = route.execute_osb_atomic_signed_command
    monkeypatch.setattr(route, "_command_store", lambda: store)
    monkeypatch.setattr(
        route,
        "execute_osb_atomic_signed_command",
        lambda *args, **kwargs: original_execute(*args, **kwargs, publisher=publisher),
    )
    command = make_command(
        flow.scope["tenant_id"], flow.scope["platform_study_id"], actor
    )
    command.update(
        action="osb.carrier-native-state.capture",
        inputPayload=flow.capture_input,
        expectedTargetState=flow.captured["nativeStateHash"],
    )
    command["inputHash"] = canonical_json_hash_ref(
        command["inputPayload"], schema_version="CommandInputV1@1.0.0"
    )
    command["commandIntentHash"] = canonical_json_hash_ref(
        command_intent(command), schema_version="CommandIntentV1@1.0.0"
    )
    headers = {"x-platform-study-id": flow.scope["platform_study_id"]}
    first = api.post("/commands/carrier-native-state", json=command, headers=headers)
    assert first.status_code == 200, first.text
    repeated = api.post("/commands/carrier-native-state", json=command, headers=headers)
    assert repeated.status_code == 200, repeated.text
    result, replay = first.json()["data"], repeated.json()["data"]
    assert (
        result["publicationMode"] == "signed"
        and not result["replay"]
        and replay["replay"]
    )
    assert (
        result["effectPayload"]["nativeStateArtifact"]
        == flow.captured["nativeStateArtifact"]
    )
    assert result["signedReceiptEnvelope"] == replay["signedReceiptEnvelope"]
    assert publisher.calls == 1 and first.headers["cache-control"] == "no-store"
