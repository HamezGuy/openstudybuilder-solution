from copy import deepcopy
from types import SimpleNamespace

import pytest

from clinical_mdr_api.routers.integrations import native_identity as routes


@pytest.mark.parametrize("count", [0, 1, 34, 300])
def test_candidate_command_preserves_all_blockers_in_object_receipt_records(monkeypatch, count):
    codes = [f"MAPPING_CONTEXT_GROUP_TRUNCATED:fact-{index}:concept-{index}:primary" for index in range(count)]
    artifact = {"payloadHash": {"value": "sha256:" + "ab" * 32}}
    generated = {
        "payload": {"candidateRecords": [], "deferredMembers": [], "blockers": codes},
        "nativeIdentity": "Study_990001", "nativeVersion": "draft",
        "payloadHash": {"value": "sha256:" + "cd" * 32},
        "candidateSetId": "candidate-set", "candidateSetVersionId": "candidate-version",
        "artifactRef": artifact, "assignment": {}, "signedEnvelope": {},
    }
    original = deepcopy(generated)
    monkeypatch.setattr(routes.settings, "platform_commands_prototype_enabled", True)
    monkeypatch.setattr(routes.settings, "deployment_environment", "test")
    monkeypatch.setenv("OSB_PLATFORM_COMMAND_SIGNING_URL", "http://localhost:8765/test-signer")
    monkeypatch.setattr(routes, "_platform_principal", lambda *_: SimpleNamespace(
        tenant_id="tenant", study_ids={"study"}, sub="service:test", actor_chain=[],
        roles={"service"}, purpose="workflow-orchestration", capabilities={"candidate:generate"},
    ))
    monkeypatch.setattr(routes, "load_candidate_request", lambda *_: {})
    monkeypatch.setattr(routes, "_verify_candidate_request_signature", lambda *_: {"verified": True})
    generation_arguments = {}
    def generate(**kwargs):
        generation_arguments.update(kwargs)
        return generated
    monkeypatch.setattr(routes, "generate_candidate_set", generate)
    monkeypatch.setattr(routes, "read_candidate_set_for_publication", lambda **_: generated)
    monkeypatch.setattr(routes, "validate_platform_command", lambda *_: None)
    transaction = SimpleNamespace(
        find_by_command_id=lambda *_: None, find_by_idempotency_key=lambda *_: None,
        find_preparation_by_command_id=lambda *_: None, find_preparation_by_idempotency_key=lambda *_: None,
    )
    monkeypatch.setattr(routes, "platform_command_store", SimpleNamespace(serializable=lambda body, call: call(transaction)))
    monkeypatch.setattr(routes, "execute_signed_platform_command", lambda *args: args[5].prepare())
    response = routes.execute_candidate_set_command({
        "commandId": "command", "idempotencyKey": "key",
        "action": "osb.candidate-set.generate", "targetCapability": "candidate:generate",
        "platformStudyId": "study",
        "inputPayload": {"candidateRequestArtifact": artifact, "candidateRequestSignedEnvelope": {},
                         "supersedesCandidateSetVersionId": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"},
        "requestingActor": {"issuerQualifiedSubject": "service:test"},
    }, SimpleNamespace(app=SimpleNamespace(openapi=lambda: {})))
    assert generation_arguments["supersedes_candidate_set_version_id"] == "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    blockers = response["data"]["blockers"]
    assert all(isinstance(blocker, dict) for blocker in blockers)
    assert len(blockers) <= 256
    assert [code for blocker in blockers for code in blocker["blockerCodes"]] == codes
    assert sum(blocker["count"] for blocker in blockers) == count
    assert generated == original
