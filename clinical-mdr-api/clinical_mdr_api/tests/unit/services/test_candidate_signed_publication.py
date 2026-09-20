"""Publication tests use a stub signer; live receipts are verified separately."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json_hash_ref
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import PlatformCommandError, command_intent
from clinical_mdr_api.routers.integrations import native_identity as routes
from clinical_mdr_api.services.integrations import candidate_set as candidate_module
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError, read_candidate_set_for_publication
from clinical_mdr_api.tests.unit.services.test_osb_candidate_set_generation import _generate, FakeMapping, OPENAPI_HASH, STUDY, TENANT
from clinical_mdr_api.tests.unit.services.signed_command_test_fixtures import MemoryCommandStore, StubPublisher






def setup_publication(monkeypatch, *, generate=False):
    generated, native, payload, artifact, envelope, verification = _generate(monkeypatch)
    control, publisher = MemoryCommandStore(), StubPublisher()
    reads = []
    query = native.cypher_query

    def record_query(text, params=None):
        reads.append(text)
        return query(text, params)

    native.cypher_query = record_query
    monkeypatch.setattr(routes.settings, "platform_commands_prototype_enabled", True)
    monkeypatch.setattr(routes.settings, "deployment_environment", "test")
    monkeypatch.setenv("OSB_PLATFORM_COMMAND_SIGNING_URL", "http://localhost:8765/v1/sign-command-receipt")
    monkeypatch.setattr(routes, "platform_command_store", control)
    monkeypatch.setattr(routes, "RemotePlatformCommandReceiptPublisherV1", lambda *_, **__: publisher)
    monkeypatch.setattr(routes, "_platform_principal", lambda *_: SimpleNamespace(
        tenant_id=TENANT, study_ids={STUDY}, sub="service:osb", actor_chain=[],
        roles={"service"}, purpose="workflow-orchestration", capabilities={"candidate:generate"},
    ))
    monkeypatch.setattr(routes, "load_candidate_request", lambda *_: payload)
    monkeypatch.setattr(routes, "_verify_candidate_request_signature", lambda *_: verification)
    monkeypatch.setattr(routes, "canonical_hash", lambda _: OPENAPI_HASH)
    monkeypatch.setattr(routes, "generate_candidate_set", lambda **_: pytest.fail("Publication must not generate/search/write candidates."))
    if generate:
        native.sets.clear()
        monkeypatch.setattr(routes, "generate_candidate_set", candidate_module.generate_candidate_set)
        monkeypatch.setattr(candidate_module, "MappingContextService", FakeMapping)
    expiry = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    command = {
        "contractVersion": "CommandEnvelopeV1@1.0.0", "commandId": str(uuid4()),
        "workflowId": str(uuid4()), "workflowStepId": str(uuid4()), "correlationId": str(uuid4()),
        "tenantId": TENANT, "platformStudyId": STUDY, "targetSystem": "osb",
        "targetCapability": "candidate:generate", "action": "osb.candidate-set.publish",
        "requestingActor": {"issuerQualifiedSubject": "service:osb", "subjectType": "service", "actorChain": []},
        "purpose": "workflow-orchestration", "deadlineAt": expiry, "idempotencyKey": str(uuid4()),
        "authorizationDecisionRef": {"decisionId": str(uuid4()), "expiresAt": expiry},
        "inputPayload": {"candidateRequestArtifact": artifact, "candidateRequestSignedEnvelope": envelope,
                         "candidateSetArtifact": generated["artifactRef"]},
    }
    if generate:
        command["action"] = "osb.candidate-set.generate"
        del command["inputPayload"]["candidateSetArtifact"]
    command["inputHash"] = canonical_json_hash_ref(command["inputPayload"], schema_version="CommandInputV1@1.0.0")
    command["commandIntentHash"] = canonical_json_hash_ref(command_intent(command), schema_version="CommandIntentV1@1.0.0")
    request = SimpleNamespace(app=SimpleNamespace(openapi=lambda: {}))
    control.command = command
    invoke = lambda: routes.execute_candidate_set_command(command, request)["data"]
    return generated, native, control, publisher, reads, invoke


def test_standard_conductor_generation_stages_once_and_completes_with_signed_receipt(monkeypatch):
    _, native, control, publisher, reads, invoke = setup_publication(monkeypatch, generate=True)
    assert not native.sets
    result = invoke()
    assert result["publicationMode"] == "signed"
    assert result["receipt"]["status"] == "succeeded"
    assert result["receipt"]["conservationCounts"]["sourceIntents"] == 1
    assert len(native.sets) == 1
    assert control.published is not None
    original = deepcopy(native.sets)
    count = len(reads)
    replay = invoke()
    assert replay["replay"] is True
    assert replay["receipt"] == result["receipt"]
    assert replay["signedReceiptEnvelope"] == result["signedReceiptEnvelope"]
    assert native.sets == original and len(reads) == count
    assert publisher.calls == 1


@pytest.mark.parametrize("custody", ["unsigned", "conflicting_published", "conflicting_prepared"])
def test_generation_checks_existing_command_custody_before_any_staging(monkeypatch, custody):
    _, native, control, _, reads, invoke = setup_publication(monkeypatch, generate=True)
    value = {"commandIntentHashValue": control.command["commandIntentHash"]["value"],
             "publicationMode": "prototype_unsigned"}
    if custody == "unsigned":
        control.published = value
    elif custody == "conflicting_published":
        control.published = {**value, "commandIntentHashValue": "different"}
    else:
        control.preparation = {"commandIntentHashValue": "different"}
    with pytest.raises(HTTPException) as error:
        invoke()
    assert error.value.detail["code"] == "COMMAND_IDEMPOTENCY_CONFLICT"
    assert not native.sets and not reads


def test_generation_validates_command_scope_before_staging(monkeypatch):
    _, native, control, _, reads, invoke = setup_publication(monkeypatch, generate=True)
    control.command["tenantId"] = str(uuid4())
    with pytest.raises(HTTPException) as error:
        invoke()
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "COMMAND_SCOPE_DENIED"
    assert not native.sets and not reads


def test_signed_candidate_publication_is_read_only_and_replay_preserves_receipt(monkeypatch):
    generated, native, control, publisher, reads, invoke = setup_publication(monkeypatch)
    original = deepcopy(native.sets)
    result = invoke()
    assert result["publicationMode"] == "signed"
    assert result["receipt"]["producedArtifacts"] == [generated["artifactRef"]]
    assert control.published is not None
    assert native.sets == original
    assert reads and all("MERGE" not in query and " SET " not in query for query in reads)
    count = len(reads)
    replay = invoke()
    assert replay["replay"] is True
    assert replay["receipt"] == result["receipt"]
    assert replay["signedReceiptEnvelope"] == result["signedReceiptEnvelope"]
    assert publisher.calls == 1
    assert len(reads) == count


def test_failed_signer_publishes_no_effect_and_retry_resumes_exact_preparation(monkeypatch):
    _, native, control, publisher, _, invoke = setup_publication(monkeypatch)
    original = deepcopy(native.sets)
    publisher.fail = True
    with pytest.raises(HTTPException) as error:
        invoke()
    assert error.value.status_code == 503
    assert control.published is None
    retained = deepcopy(control.preparation)
    assert retained["state"] == "prepared"
    assert native.sets == original
    publisher.fail = False
    result = invoke()
    assert result["receipt"] == retained["receipt"]
    assert control.preparation["preparationId"] == retained["preparationId"]


@pytest.mark.parametrize("change", ["binding", "bytes", "expiry"])
def test_staged_artifact_and_native_checkpoint_are_rechecked_after_signing(monkeypatch, change):
    _, native, control, publisher, _, invoke = setup_publication(monkeypatch)
    def mutate():
        if change == "binding":
            native.binding = (*native.binding[:2], "0.2")
        elif change == "bytes":
            row = next(iter(native.sets.values()))
            row[0] = row[0].replace('"createdBy":"service:osb"', '"createdBy":"changed"')
        else:
            class FutureClock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.now(tz) + timedelta(hours=1)
            monkeypatch.setattr(candidate_module, "datetime", FutureClock)
    publisher.after_sign = mutate
    with pytest.raises(HTTPException) as error:
        invoke()
    assert error.value.detail["code"] == {
        "binding": "OSB_CANDIDATE_SET_STALE",
        "bytes": "OSB_CANDIDATE_PUBLICATION_ARTIFACT_MISMATCH",
        "expiry": "OSB_CANDIDATE_REQUEST_EXPIRED",
    }[change]
    assert publisher.calls == 1
    assert control.published is None


@pytest.mark.parametrize("change", ["version", "descriptor", "hash", "scope"])
def test_publication_rejects_substituted_artifact_or_scope(monkeypatch, change):
    generated, _, payload, artifact, envelope, verification = _generate(monkeypatch)
    candidate = deepcopy(generated["artifactRef"])
    tenant, study = TENANT, STUDY
    if change == "version":
        candidate["artifactVersionId"] = str(uuid4())
    elif change == "descriptor":
        candidate["byteSize"] += 1
    elif change == "hash":
        candidate["payloadHash"]["value"] = "sha256:" + "00" * 32
    else:
        study = str(uuid4())
    with pytest.raises(OsbCandidateSetError) as error:
        read_candidate_set_for_publication(
            request_payload=payload, request_artifact=artifact, candidate_artifact=candidate,
            tenant_id=tenant, platform_study_id=study, osb_openapi_hash=OPENAPI_HASH,
            signed_envelope=envelope, signature_verification=verification,
        )
    assert error.value.code == {
        "version": "OSB_CANDIDATE_PUBLICATION_TARGET_REQUIRED",
        "descriptor": "OSB_CANDIDATE_PUBLICATION_ARTIFACT_MISMATCH",
        "hash": "OSB_CANDIDATE_PUBLICATION_ARTIFACT_MISMATCH",
        "scope": "OSB_CANDIDATE_REQUEST_SCOPE_INVALID",
    }[change]
