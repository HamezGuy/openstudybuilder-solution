from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
)
from clinical_mdr_api.models.integrations.mapping_context import (
    MappingContextCandidate,
    MappingContextCandidateGroup,
    MappingContextV2Response,
)
from clinical_mdr_api.services.integrations import candidate_set as candidate_set_module
from clinical_mdr_api.services.integrations import study_metadata_mapping as metadata_module
from clinical_mdr_api.services.integrations.candidate_set import (
    CANDIDATE_REQUEST_MEDIA_TYPE,
    OsbCandidateSetError,
    assert_candidate_set_current,
    candidate_assignment_identity,
    generate_candidate_set,
)
from clinical_mdr_api.tests.unit.services.test_osb_candidate_request_projection import (
    _intent,
    _payload,
    _routed,
)
from clinical_mdr_api.tests.unit.services.test_candidate_set_v1 import _refresh, _set_contract_version
from clinical_mdr_api.tests.unit.services.test_study_metadata_mapping import (
    COUNT,
    NativePort as MetadataPort,
    intent as metadata_intent,
)

TENANT = "11111111-1111-4111-8111-111111111111"
STUDY = "22222222-2222-4222-8222-222222222222"
OPENAPI_HASH = "sha256:" + "ab" * 32


class FakeQuery:
    def __init__(self, binding=None):
        self.binding = binding or (
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "Study_990001", "0.1",
        )
        self.sets: dict[tuple[str, str, str], list] = {}
        self.binding_receipts: list[list[str]] = []
        self.native_checkpoint = ("0.1", "DRAFT")

    def cypher_query(self, query, params=None):
        params = params or {}
        if "PlatformNativeStudyBinding" in query:
            return ([list(self.binding)], None)
        if "PlatformNativeIdentityAudit" in query:
            return (self.binding_receipts, None)
        if "RETURN study.uid,type(head)" in query:
            return ([[self.binding[1], "LATEST_DRAFT", *self.native_checkpoint, None, None, True]], None)
        if "OsbCandidateRequestLock" in query:
            return ([[1]], None)
        if "MATCH (candidate:OsbCandidateSetV1" in query:
            stored = [
                row for key, row in self.sets.items()
                if key[0] == params["tenant_id"] and (
                    key[2] == params["candidate_set_version_id"]
                    if "candidate_set_version_id" in params else key[1] == params["request_hash"]
                )
            ]
            return (stored, None)
        if "MERGE (request:OsbCandidateRequestV1" in query:
            row = [
                params["payload_json"], params["payload_hash"], params["artifact_ref_json"],
                params["set_id"], params["set_version_id"], params["native_study_id"],
                params["native_version"], params["signed_envelope_json"],
            ]
            key = (params["tenant_id"], params["request_hash"], params["set_version_id"])
            self.sets.setdefault(key, row)
            return ([[params["set_version_id"], params["payload_hash"], params["assignment_id"]]], None)
        return ([], None)


class FakeMapping:
    def __init__(self, *, native=True, omit=False, reorder=False, incomplete=False):
        self.native = native
        self.omit = omit
        self.reorder = reorder
        self.incomplete = incomplete
        self.requested_fact_ids: list[list[str]] = []

    def get_context_v2(self, request, osb_openapi_hash):
        groups = []
        requested = list(request.candidate_groups)
        self.requested_fact_ids.append([str(group.fact_id) for group in requested])
        if self.omit:
            requested = requested[:-1]
        if self.reorder:
            requested = list(reversed(requested))
        for group in requested:
            candidates = []
            if self.native:
                candidates = [MappingContextCandidate(
                    resource_family=group.resource_family,
                    resource_type="" if self.incomplete else "CriteriaTemplate",
                    uid="" if self.incomplete else "CriteriaTemplate_1",
                    version="" if self.incomplete else "1.0",
                    status="Final",
                    label="Age >= 18",
                )]
            groups.append(MappingContextCandidateGroup(
                fact_id=group.fact_id, concept_id=group.concept_id, target_key=group.target_key,
                semantic_role=group.semantic_role, resource_family=group.resource_family,
                complete=True, truncated=False, candidates=candidates, release_blockers=[],
            ))
        return MappingContextV2Response(
            study_uid=request.study_uid, study_value_version=request.study_value_version,
            generated_at=datetime.now(UTC), context_hash="context-hash-1",
            osb_openapi_hash=osb_openapi_hash, governed=True, candidate_groups=groups,
        )


def _request_bundle(intents=None, family: str = "criteria_templates", routed=None):
    now = datetime.now(UTC).replace(microsecond=0)
    payload = _payload(
        intents=intents or [_intent("fact-eligibility-age-18", family=family)],
        routed=routed,
    )
    payload["contractVersion"] = "OsbCandidateRequestV1@1.0.0"
    payload["createdAt"] = now.isoformat().replace("+00:00", "Z")
    payload["expiresAt"] = (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    payload["createdBy"] = "service:csl"
    payload_hash = canonical_json_hash_ref(
        payload, schema_version="OsbCandidateRequestV1@1.0.0", media_type=CANDIDATE_REQUEST_MEDIA_TYPE,
    )
    fields = {
        "artifactId": payload["requestId"], "artifactVersionId": payload["requestVersionId"],
        "kind": "osb-candidate-request",
        "stableLocator": f'artifact://csl/osb-candidate-request/{payload["requestVersionId"]}',
        "payloadHash": payload_hash, "byteSize": len(canonical_json(payload).encode()),
        "classification": "regulated-non-phi", "tenantId": TENANT,
        "region": "us-central1", "producerService": "csl.attestation",
        "producerEnvironment": "prototype", "producerVersion": "test",
        "payloadContract": "accuratrials.osb.OsbCandidateRequestV1",
        "payloadContractVersion": "1.0.0", "purpose": "osb-candidate-generation",
        "createdAt": payload["createdAt"],
    }
    descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}
    artifact = {"contractVersion": "ArtifactRefV1@1.0.0", **fields, "descriptorHash": descriptor_hash(descriptor)}
    envelope = {
        "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
        "artifactDescriptor": descriptor, "payloadHash": payload_hash,
        "signingStatement": {
            "signingPurpose": "osb-candidate-request", "producerService": "csl.attestation",
            "payloadContract": "accuratrials.osb.OsbCandidateRequestV1",
            "payloadContractVersion": "1.0.0",
        },
    }
    verification = {
        "verified": True, "payloadHash": payload_hash,
        "envelopeHash": canonical_json_hash_ref(envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"),
        "signerKeyId": "prototype/csl/attestation/test", "trustedTime": payload["createdAt"],
    }
    return payload, artifact, envelope, verification


def _generate(monkeypatch, mapping=None, openapi_hash=OPENAPI_HASH, intents=None, routed=None):
    store = FakeQuery()
    monkeypatch.setattr(candidate_set_module, "db", store)
    payload, artifact, envelope, verification = _request_bundle(intents=intents, routed=routed)
    generated = generate_candidate_set(
        request_payload=payload, artifact=artifact, tenant_id=TENANT, platform_study_id=STUDY,
        osb_openapi_hash=openapi_hash, actor="service:osb",
        signed_envelope=envelope, signature_verification=verification,
        mapping_context_service=mapping or FakeMapping(),
    )
    return generated, store, payload, artifact, envelope, verification


def test_generate_projects_conservation_census_and_assignment(monkeypatch):
    generated, *_ = _generate(monkeypatch)
    payload = generated["payload"]
    census = payload["conservation"]
    assert census["contractVersion"] == "ConservationCensusV1@1.0.0"
    assert census["counts"]["rows"] == 1
    assert census["counts"]["native"] == 1
    assert census["rows"][0]["source"]["path"] == "#/typedSourceIntents/0"
    assert census["rows"][0]["target"]["path"] == "#/candidateRecords/0"
    assert payload["candidateRecords"][0]["nativeCandidates"][0]["uid"] == "CriteriaTemplate_1"
    assert generated["assignment"]["assignmentId"] == candidate_assignment_identity(
        tenant_id=TENANT, platform_study_id=STUDY,
        candidate_set_version_id=generated["candidateSetVersionId"],
    )["assignmentId"]
    assert generated["signedEnvelope"]["signingStatement"]["signingPurpose"] == "osb-candidate-set"


@pytest.mark.parametrize("version", ["1.2.0", "1.3.0"])
def test_current_candidate_producer_retains_full_source_and_evidence_through_storage(monkeypatch, version):
    intent = _intent("source-with-context", family="criteria_templates")
    intent["source"]["classification"] = {"standardsBindings": [
        {"domain": "VS", "variable": "VSTESTCD"}, {"domain": "LB", "variable": "LBTESTCD"}]}
    intent["source"]["values"] = [
        {"name": "value", "sourcePath": "/fields/value", "valueType": "number", "value": 0}]
    intent["evidence"] = {"citations": [{"quote": "Source", "tableId": "table-1"}, None]}
    if version == "1.3.0":
        intent["source"]["context"] = {
            "encounters": [{"formScope": ["F1", "F2"], "required": False}, None],
            "relationships": [{"targetFactId": "target"}, {"targetFactId": "target"}],
            "semanticAssociations": None,
            "unresolvedRelationships": [],
        }
    values = list(_request_bundle(intents=[intent]))
    _set_contract_version(values, version)
    store = FakeQuery()
    monkeypatch.setattr(candidate_set_module, "db", store)
    before = deepcopy(values)
    result = generate_candidate_set(
        request_payload=values[0], artifact=values[1], tenant_id=TENANT, platform_study_id=STUDY,
        osb_openapi_hash=OPENAPI_HASH, actor="service:osb", signed_envelope=values[2],
        signature_verification=values[3], mapping_context_service=FakeMapping(),
    )
    record = result["payload"]["candidateRecords"][0]
    assert record["source"] == intent["source"]
    assert record["evidence"] == intent["evidence"]
    stored = json.loads(next(iter(store.sets.values()))[0])
    assert stored["candidateRecords"] == result["payload"]["candidateRecords"]
    assert values == before
    replay = generate_candidate_set(
        request_payload=values[0], artifact=values[1], tenant_id=TENANT, platform_study_id=STUDY,
        osb_openapi_hash=OPENAPI_HASH, actor="service:osb", signed_envelope=values[2],
        signature_verification=values[3], mapping_context_service=FakeMapping(),
    )
    assert replay["replay"] is True and replay["payload"] == result["payload"]


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])
def test_candidate_metadata_version_guard_uses_actual_source_plan_and_native_dto(monkeypatch, version):
    source = _intent("enrollment", family="study_metadata")
    source.update(metadata_intent("enrollment", COUNT, 42))
    values = list(_request_bundle(intents=[source]))
    _set_contract_version(values, version)
    store, mapping = FakeQuery(), FakeMapping(native=False)
    port = MetadataPort(uid=store.binding[1])
    monkeypatch.setattr(candidate_set_module, "db", store)
    monkeypatch.setattr(metadata_module, "NativeStudyMetadataPort", lambda: port)

    def generate():
        return generate_candidate_set(
            request_payload=values[0], artifact=values[1], tenant_id=TENANT, platform_study_id=STUDY,
            osb_openapi_hash=OPENAPI_HASH, actor="service:osb", signed_envelope=values[2],
            signature_verification=values[3], mapping_context_service=mapping,
        )

    if version in {"1.0.0", "1.1.0"}:
        with pytest.raises(OsbCandidateSetError) as error:
            generate()
        assert error.value.code == "OSB_STUDY_METADATA_REQUEST_VERSION_REQUIRED"
        assert store.sets == {} and mapping.requested_fact_ids == []
    else:
        record = generate()["payload"]["candidateRecords"][0]
        offer = record["createOption"]["nativeStudyOperation"]
        assert offer["metadataValue"] == 42
        assert offer["metadataPath"] == COUNT
        assert record["source"] == source["source"] and record["evidence"] == source["evidence"]
    assert port.patches == port.locked == []


def _external_binding_bundle():
    values = list(_request_bundle())
    store = FakeQuery()
    identity = values[0]["osbStudyIdentity"]
    identity["contractVersion"] = "1.1.0"
    identity["bindingId"] = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    receipt = {
        "contractVersion": "1.0.0",
        "receiptId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "tenantId": TENANT, "platformStudyId": STUDY,
        "targetSystem": "osb", "namespace": "accuratrials-osb",
        "objectType": "study-draft-root",
        "nativeIdentity": store.binding[1], "nativeVersion": store.binding[2],
        "targetStateHash": canonical_json_hash_ref({
            "nativeIdentity": store.binding[1], "nativeVersion": store.binding[2],
            "status": "draft", "domainBindingId": store.binding[0],
        }, schema_version="OSBNativeStudyRootStateV1@1.0.0"),
    }
    retained_envelope = {"testFixture": "retained native publisher envelope"}
    identity["evidence"] = {
        "receiptId": receipt["receiptId"],
        "receiptPayloadHash": canonical_json_hash_ref(
            receipt, schema_version="NativeIdentityBindingReceiptV1@1.0.0",
        ),
        "signedEnvelopeHash": canonical_json_hash_ref(
            retained_envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0",
        ),
    }
    store.binding_receipts = [[canonical_json(receipt), canonical_json(retained_envelope)]]
    _refresh(values)
    return store, values


def _generate_external(monkeypatch, store, values):
    monkeypatch.setattr(candidate_set_module, "db", store)
    return generate_candidate_set(
        request_payload=values[0], artifact=values[1], tenant_id=TENANT, platform_study_id=STUDY,
        osb_openapi_hash=OPENAPI_HASH, actor="service:osb",
        signed_envelope=values[2], signature_verification=values[3],
        mapping_context_service=FakeMapping(),
    )


def test_external_and_native_binding_ids_join_through_exact_published_receipt(monkeypatch):
    store, values = _external_binding_bundle()
    generated = _generate_external(monkeypatch, store, values)
    assert generated["payload"]["osbStudyIdentity"] == values[0]["osbStudyIdentity"]
    assert generated["payload"]["osbStudyIdentity"]["bindingId"] != store.binding[0]
    assert _generate_external(monkeypatch, store, values)["replay"] is True


@pytest.mark.parametrize("mutation", [
    "missing_receipt", "duplicate_receipt", "changed_receipt", "changed_envelope",
    "changed_binding", "changed_root_version", "wrong_receipt_scope",
])
def test_external_binding_join_rejects_missing_mutated_or_stale_evidence(monkeypatch, mutation):
    store, values = _external_binding_bundle()
    if mutation == "missing_receipt":
        store.binding_receipts = []
    elif mutation == "duplicate_receipt":
        store.binding_receipts *= 2
    elif mutation == "changed_receipt":
        receipt = json.loads(store.binding_receipts[0][0])
        receipt["receiptId"] = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        store.binding_receipts[0][0] = canonical_json(receipt)
    elif mutation == "changed_envelope":
        store.binding_receipts[0][1] = canonical_json({"testFixture": "different envelope"})
    elif mutation == "changed_binding":
        store.binding = ("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", *store.binding[1:])
    elif mutation == "changed_root_version":
        store.native_checkpoint = ("1.0", "RELEASED")
    else:
        receipt = json.loads(store.binding_receipts[0][0])
        receipt["platformStudyId"] = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        store.binding_receipts[0][0] = canonical_json(receipt)
        values[0]["osbStudyIdentity"]["evidence"]["receiptPayloadHash"] = canonical_json_hash_ref(
            receipt, schema_version="NativeIdentityBindingReceiptV1@1.0.0",
        )
        _refresh(values)
    with pytest.raises(OsbCandidateSetError) as error:
        _generate_external(monkeypatch, store, values)
    assert error.value.code == "OSB_CANDIDATE_REQUEST_NATIVE_PRECONDITION_FAILED"
    assert store.sets == {}


def test_external_binding_rollover_invalidates_previously_generated_set(monkeypatch):
    store, values = _external_binding_bundle()
    generated = _generate_external(monkeypatch, store, values)
    store.binding = ("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", *store.binding[1:])
    with pytest.raises(OsbCandidateSetError) as error:
        assert_candidate_set_current(
            deepcopy(generated["payload"]),
            binding=dict(zip(("bindingId", "nativeIdentity", "nativeVersion"), store.binding)),
            osb_openapi_hash=OPENAPI_HASH,
        )
    assert error.value.code == "OSB_CANDIDATE_SET_STALE"


def test_crash_retry_returns_one_candidate_set_and_assignment(monkeypatch):
    generated, store, payload, artifact, envelope, verification = _generate(monkeypatch)
    replay = generate_candidate_set(
        request_payload=payload, artifact=artifact, tenant_id=TENANT, platform_study_id=STUDY,
        osb_openapi_hash=OPENAPI_HASH, actor="service:osb",
        signed_envelope=envelope, signature_verification=verification,
        mapping_context_service=FakeMapping(),
    )
    assert replay["replay"] is True
    assert replay["candidateSetVersionId"] == generated["candidateSetVersionId"]
    assert replay["assignment"]["assignmentId"] == generated["assignment"]["assignmentId"]
    assert len(store.sets) == 1


def test_capability_mutation_invalidates_stored_set(monkeypatch):
    generated, store, payload, artifact, envelope, verification = _generate(monkeypatch)
    with pytest.raises(OsbCandidateSetError) as error:
        generate_candidate_set(
            request_payload=payload, artifact=artifact, tenant_id=TENANT, platform_study_id=STUDY,
            osb_openapi_hash="sha256:" + "cd" * 32, actor="service:osb",
            signed_envelope=envelope, signature_verification=verification,
            mapping_context_service=FakeMapping(),
        )
    assert error.value.code == "OSB_CANDIDATE_SET_STALE"
    assert generated["payload"]["capabilityCheckpoint"]["osbOpenApiHash"] == OPENAPI_HASH
    assert len(store.sets) == 1


def test_explicit_capability_refresh_preserves_prior_version_and_replays_current(monkeypatch):
    generated, store, payload, artifact, envelope, verification = _generate(monkeypatch)
    original = deepcopy(generated)
    original_rows = deepcopy(store.sets)
    changed_hash = "sha256:" + "cd" * 32
    args = {
        "request_payload": payload, "artifact": artifact, "tenant_id": TENANT,
        "platform_study_id": STUDY, "osb_openapi_hash": changed_hash,
        "actor": "service:osb", "signed_envelope": envelope,
        "signature_verification": verification, "mapping_context_service": FakeMapping(),
    }
    refreshed = generate_candidate_set(
        **args, supersedes_candidate_set_version_id=generated["candidateSetVersionId"],
    )
    assert refreshed["candidateSetId"] == generated["candidateSetId"]
    assert refreshed["candidateSetVersionId"] != generated["candidateSetVersionId"]
    assert refreshed["payload"]["capabilityCheckpoint"]["osbOpenApiHash"] == changed_hash
    assert refreshed["payload"]["request"] == original["payload"]["request"]
    assert refreshed["payload"]["candidateRecords"] == original["payload"]["candidateRecords"]
    assert refreshed["payload"]["deferredMembers"] == original["payload"]["deferredMembers"]
    assert generated == original
    assert all(store.sets[key] == value for key, value in original_rows.items())
    assert len(store.sets) == 2
    for extra in [{}, {"supersedes_candidate_set_version_id": generated["candidateSetVersionId"]}]:
        replay = generate_candidate_set(**args, **extra)
        assert replay["replay"] is True
        assert replay["candidateSetVersionId"] == refreshed["candidateSetVersionId"]
        assert replay["payloadHash"] == refreshed["payloadHash"]
    with pytest.raises(OsbCandidateSetError, match="Capability or native checkpoint changed"):
        assert_candidate_set_current(
            original["payload"],
            binding=dict(zip(("bindingId", "nativeIdentity", "nativeVersion"), store.binding)),
            osb_openapi_hash=changed_hash,
        )
    assert len(store.sets) == 2


def test_capability_refresh_requires_exact_prior_version(monkeypatch):
    generated, store, payload, artifact, envelope, verification = _generate(monkeypatch)
    with pytest.raises(OsbCandidateSetError) as error:
        generate_candidate_set(
            request_payload=payload, artifact=artifact, tenant_id=TENANT, platform_study_id=STUDY,
            osb_openapi_hash="sha256:" + "cd" * 32, actor="service:osb",
            signed_envelope=envelope, signature_verification=verification,
            mapping_context_service=FakeMapping(),
            supersedes_candidate_set_version_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        )
    assert error.value.code == "OSB_CANDIDATE_REFRESH_PRECONDITION_FAILED"
    assert len(store.sets) == 1
    assert generated["payload"]["capabilityCheckpoint"]["osbOpenApiHash"] == OPENAPI_HASH


@pytest.mark.parametrize("prior_version", ["", "not-a-uuid", 3, []])
def test_capability_refresh_rejects_invalid_prior_identity(monkeypatch, prior_version):
    _, store, payload, artifact, envelope, verification = _generate(monkeypatch)
    with pytest.raises(OsbCandidateSetError) as error:
        generate_candidate_set(
            request_payload=payload, artifact=artifact, tenant_id=TENANT, platform_study_id=STUDY,
            osb_openapi_hash="sha256:" + "cd" * 32, actor="service:osb",
            signed_envelope=envelope, signature_verification=verification,
            mapping_context_service=FakeMapping(), supersedes_candidate_set_version_id=prior_version,
        )
    assert error.value.code == "OSB_CANDIDATE_REFRESH_PRECONDITION_INVALID"
    assert len(store.sets) == 1


def test_capability_refresh_does_not_bypass_native_binding_change(monkeypatch):
    generated, store, payload, artifact, envelope, verification = _generate(monkeypatch)
    store.binding = (store.binding[0], store.binding[1], "0.2")
    with pytest.raises(OsbCandidateSetError) as error:
        generate_candidate_set(
            request_payload=payload, artifact=artifact, tenant_id=TENANT, platform_study_id=STUDY,
            osb_openapi_hash="sha256:" + "cd" * 32, actor="service:osb",
            signed_envelope=envelope, signature_verification=verification,
            mapping_context_service=FakeMapping(),
            supersedes_candidate_set_version_id=generated["candidateSetVersionId"],
        )
    assert error.value.code == "OSB_CANDIDATE_REQUEST_NATIVE_PRECONDITION_FAILED"
    assert len(store.sets) == 1


def test_capability_refresh_does_not_swallow_identity_failure_during_prior_validation(monkeypatch):
    store, values = _external_binding_bundle()
    generated = _generate_external(monkeypatch, store, values)
    validate_identity = candidate_set_module._assert_identity_binding
    checked = 0

    def concurrent_identity_change(*args, **kwargs):
        nonlocal checked
        checked += 1
        if checked == 2:
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_STALE", "Native binding changed during prior-set validation.")
        return validate_identity(*args, **kwargs)

    monkeypatch.setattr(candidate_set_module, "_assert_identity_binding", concurrent_identity_change)
    with pytest.raises(OsbCandidateSetError, match="Native binding changed during prior-set validation"):
        generate_candidate_set(
            request_payload=values[0], artifact=values[1], tenant_id=TENANT, platform_study_id=STUDY,
            osb_openapi_hash="sha256:" + "cd" * 32, actor="service:osb",
            signed_envelope=values[2], signature_verification=values[3],
            mapping_context_service=FakeMapping(),
            supersedes_candidate_set_version_id=generated["candidateSetVersionId"],
        )
    assert checked == 2
    assert len(store.sets) == 1


def test_expired_candidate_set_cannot_be_decided(monkeypatch):
    generated, *_ = _generate(monkeypatch)
    with pytest.raises(OsbCandidateSetError) as error:
        assert_candidate_set_current(
            generated["payload"],
            binding={
                "bindingId": generated["payload"]["osbStudyIdentity"]["bindingId"],
                "nativeIdentity": generated["nativeIdentity"],
                "nativeVersion": generated["nativeVersion"],
            },
            osb_openapi_hash=OPENAPI_HASH,
            now=datetime.now(UTC) + timedelta(hours=2),
        )
    assert error.value.code == "OSB_CANDIDATE_SET_EXPIRED"


def test_omitted_context_group_is_rejected(monkeypatch):
    with pytest.raises(OsbCandidateSetError) as error:
        _generate(monkeypatch, mapping=FakeMapping(omit=True))
    assert error.value.code == "OSB_CANDIDATE_SET_MEMBER_MISMATCH"


def test_reordered_context_group_is_rejected(monkeypatch):
    with pytest.raises(OsbCandidateSetError) as error:
        _generate(
            monkeypatch,
            mapping=FakeMapping(reorder=True),
            intents=[
                _intent("fact-1", family="criteria_templates"),
                _intent("fact-2", family="criteria_templates"),
            ],
        )
    assert error.value.code == "OSB_CANDIDATE_SET_MEMBER_MISMATCH"


def test_unreadable_target_without_create_is_rejected(monkeypatch):
    intent = _intent("fact-eligibility-age-18", family="criteria_templates")
    intent["createOption"] = None
    with pytest.raises(OsbCandidateSetError) as error:
        _generate(monkeypatch, mapping=FakeMapping(native=False), intents=[intent])
    assert error.value.code == "OSB_CANDIDATE_SET_TARGET_UNREADABLE"


def test_unsupported_native_create_cannot_be_reported_as_governed_extension(monkeypatch):
    generated, *_ = _generate(monkeypatch, mapping=FakeMapping(native=False))
    assert generated["payload"]["conservation"]["counts"]["governedExtension"] == 0
    assert generated["payload"]["conservation"]["counts"]["deferredBlocking"] == 1
    record = generated["payload"]["candidateRecords"][0]
    assert record["createOption"] is None
    assert record["blockers"] == ["OSB_NATIVE_SOURCE_CREATE_UNSUPPORTED"]
    assert record["source"] == _intent("fact-eligibility-age-18", family="criteria_templates")["source"]


def test_supported_managed_create_without_native_target_remains_governed_extension(monkeypatch):
    intent = _intent("fact-managed", family="compound_product_relationships")
    generated, *_ = _generate(monkeypatch, mapping=FakeMapping(native=False), intents=[intent])
    assert generated["payload"]["conservation"]["counts"]["governedExtension"] == 1
    assert generated["payload"]["candidateRecords"][0]["createOption"]["allowed"] is True


def test_capture_create_offer_uses_the_native_source_planner(monkeypatch):
    from clinical_mdr_api.tests.unit.services.test_native_capture_mapping import fixture

    intent = _intent("fact-capture", family="odm_forms", extra={"source": fixture()[0]["intent"]["source"]})
    generated, *_ = _generate(monkeypatch, mapping=FakeMapping(native=False), intents=[intent])
    record = generated["payload"]["candidateRecords"][0]
    assert record["createOption"] == {"allowed": True, "requestedNativeType": "OdmForm"}
    assert record["blockers"] == []
    intent["source"]["values"] = [value for value in intent["source"]["values"] if value["name"] != "repeating"]
    blocked, *_ = _generate(monkeypatch, mapping=FakeMapping(native=False), intents=[intent])
    blocked_record = blocked["payload"]["candidateRecords"][0]
    assert blocked_record["createOption"] is None
    assert blocked_record["blockers"] == ["OSB_CAPTURE_SOURCE_FIELD_REQUIRED"]


def test_incomplete_native_identity_is_rejected(monkeypatch):
    with pytest.raises(OsbCandidateSetError) as error:
        _generate(monkeypatch, mapping=FakeMapping(incomplete=True))
    assert error.value.code == "OSB_CANDIDATE_SET_TARGET_UNREADABLE"


def test_deferred_and_governed_members_flow_to_set_without_search_work(monkeypatch):
    """2 native + 1 deferred + 1 governed_extension: the set carries 2
    candidate records, 2 deferred members, and a summing census; the routed
    members produce zero library-search work."""
    mapping = FakeMapping()
    generated, *_ = _generate(
        monkeypatch,
        mapping=mapping,
        intents=[
            _intent("fact-native-a", family="criteria_templates"),
            _intent("fact-native-b", family="criteria_templates"),
        ],
        routed=[
            _routed("fact-x-deferred", "deferred_blocking"),
            _routed("fact-y-governed", "governed_extension"),
        ],
    )
    payload = generated["payload"]
    assert [record["factId"] for record in payload["candidateRecords"]] == [
        "fact-native-a", "fact-native-b",
    ]
    assert payload["deferredMembers"] == [
        {"factId": "fact-x-deferred", "revision": 1, "disposition": "deferred_blocking",
         "reasonCodes": ["source-fact:fact-x-deferred@1",
                         "routing:OSB_RESOURCE_TYPE_WITHOUT_CANDIDATE_FAMILY"]},
        {"factId": "fact-y-governed", "revision": 1, "disposition": "governed_extension",
         "reasonCodes": ["source-fact:fact-y-governed@1",
                         "routing:OSB_RESOURCE_TYPE_WITHOUT_CANDIDATE_FAMILY"]},
    ]
    census = payload["conservation"]
    assert census["counts"] == {"rows": 4, "native": 2, "governedExtension": 1,
                                "excludedSigned": 0, "deferredBlocking": 1,
                                "quarantined": 0, "rejected": 0}
    tally = {"native": 0, "governed_extension": 0, "deferred_blocking": 0}
    for row in census["rows"]:
        tally[row["disposition"]] += 1
        if row["disposition"] != "native":
            assert row["target"] is None
            assert row["multiplicity"] == {"source": 1, "target": 0}
            assert any(ref.startswith("routing:") for ref in row["evidenceRefs"])
    assert tally == {"native": 2, "governed_extension": 1, "deferred_blocking": 1}
    # The mapping context was asked about the native subset only.
    assert mapping.requested_fact_ids == [["fact-native-a", "fact-native-b"]]


def test_all_native_request_produces_empty_deferred_section(monkeypatch):
    generated, *_ = _generate(monkeypatch)
    assert generated["payload"]["deferredMembers"] == []
    assert generated["payload"]["conservation"]["counts"]["deferredBlocking"] == 0


def test_candidate_record_carries_claim_content_and_both_family_spellings(monkeypatch):
    """The adjudicating human sees the claim's source and evidence on the
    record, plus the raw requested family alongside the canonical one."""
    intent = _intent("fact-check-1", family="edit_checks")
    intent["source"]["exactQuote"] = "Check AE term against MedDRA."
    generated, *_ = _generate(monkeypatch, intents=[intent])
    record = generated["payload"]["candidateRecords"][0]
    assert record["requestedResourceFamily"] == "edit_checks"
    assert record["resourceFamily"] == "odm_methods"
    assert record["source"]["label"] == "Age at least 18 years"
    assert record["source"]["exactQuote"] == "Check AE term against MedDRA."
    assert record["evidence"] == {"locator": "synthetic"}
