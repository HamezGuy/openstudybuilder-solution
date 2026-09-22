from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
    sha256_bytes,
)
from clinical_mdr_api.services.integrations import candidate_set as candidate_set_module
from clinical_mdr_api.services.integrations.candidate_set import (
    CANDIDATE_REQUEST_MEDIA_TYPE,
    OsbCandidateSetError,
    verify_candidate_request_artifact,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
STUDY_ID = "22222222-2222-4222-8222-222222222222"


def _fixture() -> tuple[dict, dict, dict, dict]:
    now = datetime.now(UTC).replace(microsecond=0)
    source_hash = canonical_json_hash_ref(
        {"source": True}, schema_version="SourceFactPackageV1@1.0.0"
    )
    members = []
    intents = []
    for index in range(2):
        fact_id = f"fact-{index + 1:04d}"
        fact = {"factId": fact_id, "revision": 1, "fields": {"value": index + 1}}
        member = {
            "sourceFactId": fact_id,
            "revision": 1,
            "lifecycle": "accepted",
            "valueHash": canonical_json_hash_ref(fact, schema_version="FactDto@1.0.0"),
        }
        intent = {
            "factId": fact_id,
            "revision": 1,
            "conceptId": f"33333333-3333-4333-8333-33333333333{index}",
            "targetKey": "primary",
            "semanticRole": "protocol concept",
            "resourceFamily": "controlled_terminology",
            "source": {"assertionType": None, "clinicalDomain": None, "candidateType": None,
                       "exactQuote": None, "label": None,
                       "values": [{"name": "value", "sourcePath": "/fields/value",
                                   "valueType": "number", "value": index + 1}]},
            "evidence": {"primaryProvenanceId": f"citation-{index + 1}", "citations": []},
            "searchStrings": [],
            "searchCodes": [],
            "createOption": {"allowed": True, "requestedNativeType": "governed-extension"},
        }
        members.append(member)
        intents.append(intent)
    rows = [{
        "unitId": f'{intent["factId"]}@1:primary',
        "source": {
            "artifactId": "99999999-9999-4999-8999-999999999999",
            "contract": "accuratrials.csl.SemanticSnapshotV1@1.0.0",
            "type": "active-claim-revision",
            "path": f"#/activeClaimRevisions/{index}",
            "valueHash": members[index]["valueHash"],
        },
        "target": {
            "artifactId": "66666666-6666-4666-8666-666666666666",
            "contract": "accuratrials.osb.OsbCandidateRequestV1@1.0.0",
            "type": "typed-source-intent",
            "path": f"#/typedSourceIntents/{index}",
            "valueHash": canonical_json_hash_ref(
                intent, schema_version="OsbTypedSourceIntentV1@1.0.0"
            ),
        },
        "multiplicity": {"source": 1, "target": 1},
        "splitMergeGroup": None,
        "splitMergeRule": None,
        "ordering": {"significant": True, "sourceIndex": index, "targetIndex": index},
        "disposition": "native",
        "exclusionPolicy": None,
        "evidenceRefs": [f'source-fact:{intent["factId"]}@1'],
        "receiptRefs": [],
    } for index, intent in enumerate(intents)]
    snapshot_hash = canonical_json_hash_ref(
        {"snapshot": True}, schema_version="SemanticSnapshotV1@1.0.0"
    )
    source_artifact = {
        "contractVersion": "ArtifactRefV1@1.0.0",
        "artifactId": "44444444-4444-4444-8444-444444444444",
        "artifactVersionId": "55555555-5555-4555-8555-555555555555",
        "tenantId": TENANT_ID,
        "payloadHash": source_hash,
    }
    payload = {
        "contractVersion": "OsbCandidateRequestV1@1.0.0",
        "requestId": "66666666-6666-4666-8666-666666666666",
        "requestVersionId": "77777777-7777-4777-8777-777777777777",
        "tenantId": TENANT_ID,
        "platformStudyId": STUDY_ID,
        "osbStudyIdentity": {
            "contractVersion": "1.0.0", "system": "osb", "tenantId": TENANT_ID,
            "platformStudyId": STUDY_ID, "namespace": "accuratrials-osb",
            "objectType": "study-draft-root",
            "bindingId": "88888888-8888-4888-8888-888888888888",
            "nativeIdentity": "Study_990041", "nativeVersion": "0.1",
            "verificationStatus": "verified",
        },
        "sourceFactPackage": {
            "packageVersionId": source_artifact["artifactVersionId"],
            "payloadHash": source_hash,
            "factSetHash": canonical_json_hash_ref([], schema_version="SourceFactRevisionMembershipV1@1.0.0"),
        },
        "semanticSnapshot": {
            "snapshotVersionId": "99999999-9999-4999-8999-999999999999",
            "payloadHash": snapshot_hash,
            "memberSetHash": canonical_json_hash_ref(
                members, schema_version="SemanticSnapshotMemberSetV1@1.0.0"
            ),
        },
        "activeClaimRevisions": members,
        "requestedObjectFamilies": ["controlled_terminology"],
        "typedSourceIntents": intents,
        "evidenceArtifactRefs": [source_artifact],
        "projectionRuleset": {
            "id": "csl-to-osb-candidate-request", "version": "1.0.0",
            "hash": canonical_json_hash_ref(
                {"mapping": "fail-closed-family-router", "version": "1.0.0"},
                schema_version="ProjectionRulesetV1@1.0.0",
            ),
        },
        "inputConservation": {
            "contractVersion": "ConservationCensusV1@1.0.0", "rows": rows,
            "rowSetHash": canonical_json_hash_ref(
                rows, schema_version="ConservationCensusRowsV1@1.0.0"
            ),
            "counts": {
                "rows": 2, "native": 2, "governedExtension": 0, "excludedSigned": 0,
                "deferredBlocking": 0, "quarantined": 0, "rejected": 0,
            },
        },
        "checkpointPreconditions": {
            "osbNativeVersion": "0.1", "semanticSnapshotHash": snapshot_hash,
        },
        "expiresAt": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        "createdAt": now.isoformat().replace("+00:00", "Z"),
        "createdBy": "service:csl",
    }
    payload_hash = canonical_json_hash_ref(
        payload,
        schema_version="OsbCandidateRequestV1@1.0.0",
        media_type=CANDIDATE_REQUEST_MEDIA_TYPE,
    )
    descriptor_fields = {
        "artifactId": payload["requestId"], "artifactVersionId": payload["requestVersionId"],
        "kind": "osb-candidate-request",
        "stableLocator": f'artifact://csl/osb-candidate-request/{payload["requestVersionId"]}',
        "payloadHash": payload_hash, "byteSize": len(canonical_json(payload).encode()),
        "classification": "regulated-non-phi", "tenantId": TENANT_ID,
        "region": "us-central-1", "producerService": "csl.attestation",
        "producerEnvironment": "prototype", "producerVersion": "test",
        "payloadContract": "accuratrials.osb.OsbCandidateRequestV1",
        "payloadContractVersion": "1.0.0", "purpose": "osb-candidate-generation",
        "createdAt": payload["createdAt"],
    }
    descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0", **descriptor_fields}
    artifact = {
        "contractVersion": "ArtifactRefV1@1.0.0", **descriptor_fields,
        "descriptorHash": descriptor_hash(descriptor),
    }
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
        "envelopeHash": canonical_json_hash_ref(
            envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"
        ),
        "signerKeyId": "prototype/csl/attestation/test", "trustedTime": payload["createdAt"],
    }
    return payload, artifact, envelope, verification


def _code(values: tuple[dict, dict, dict, dict]) -> str:
    with pytest.raises(OsbCandidateSetError) as caught:
        verify_candidate_request_artifact(*values[:2], TENANT_ID, STUDY_ID, *values[2:])
    return caught.value.code


def _refresh(values: list[dict]) -> None:
    values[1]["payloadHash"] = canonical_json_hash_ref(
        values[0], schema_version=values[0]["contractVersion"],
        media_type=CANDIDATE_REQUEST_MEDIA_TYPE,
    )
    values[1]["byteSize"] = len(canonical_json(values[0]).encode())
    descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0",
                  **{key: value for key, value in values[1].items()
                     if key not in {"contractVersion", "descriptorHash"}}}
    values[1]["descriptorHash"] = descriptor_hash(descriptor)
    values[2]["artifactDescriptor"] = descriptor
    values[2]["payloadHash"] = values[1]["payloadHash"]
    values[3]["payloadHash"] = values[1]["payloadHash"]
    values[3]["envelopeHash"] = canonical_json_hash_ref(
        values[2], schema_version="SignedArtifactEnvelopeV1@1.0.0"
    )


def test_verified_exact_request_is_accepted() -> None:
    values = _fixture()
    verify_candidate_request_artifact(*values[:2], TENANT_ID, STUDY_ID, *values[2:])


@pytest.mark.parametrize("identity_version", ["1.0.0", "1.1.0"])
def test_supported_external_identity_versions_are_accepted(identity_version: str) -> None:
    values = list(_fixture())
    values[0]["osbStudyIdentity"]["contractVersion"] = identity_version
    _refresh(values)
    verify_candidate_request_artifact(*values[:2], TENANT_ID, STUDY_ID, *values[2:])


@pytest.mark.parametrize("identity_version", ["1.0.0", "1.1.0"])
@pytest.mark.parametrize("field,value", [
    ("contractVersion", "1.2.0"),
    ("tenantId", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ("platformStudyId", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ("verificationStatus", "pending"),
    ("namespace", "another-osb"),
    ("objectType", "another-root"),
])
def test_external_identity_version_does_not_relax_scope_or_verification(
    identity_version: str, field: str, value: str,
) -> None:
    values = list(_fixture())
    values[0]["osbStudyIdentity"]["contractVersion"] = identity_version
    values[0]["osbStudyIdentity"][field] = value
    _refresh(values)
    assert _code(tuple(values)) == "OSB_CANDIDATE_REQUEST_IDENTITY_INVALID"


@pytest.mark.parametrize("mutation,expected", [
    (lambda payload: payload["typedSourceIntents"].pop(), "OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH"),
    (lambda payload: payload["typedSourceIntents"].append(deepcopy(payload["typedSourceIntents"][0])),
     "OSB_CANDIDATE_REQUEST_DUPLICATE_MEMBER"),
    (lambda payload: payload["typedSourceIntents"].reverse(), "OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH"),
    (lambda payload: payload["requestedObjectFamilies"].append("units"),
     "OSB_CANDIDATE_REQUEST_FAMILY_MISMATCH"),
    (lambda payload: payload["typedSourceIntents"][0].__setitem__("resourceFamily", "unsupported"),
     "OSB_CANDIDATE_REQUEST_FAMILY_UNSUPPORTED"),
    (lambda payload: payload["checkpointPreconditions"].__setitem__("osbNativeVersion", "0.2"),
     "OSB_CANDIDATE_REQUEST_CHECKPOINT_MISMATCH"),
])
def test_projection_mutations_fail_before_generation(mutation, expected: str) -> None:
    values = list(_fixture())
    mutation(values[0])
    _refresh(values)
    assert _code(tuple(values)) == expected


def test_expired_mutated_and_unsigned_requests_fail() -> None:
    expired = list(_fixture())
    expired[0]["expiresAt"] = "2020-01-01T00:00:00Z"
    _refresh(expired)
    assert _code(tuple(expired)) == "OSB_CANDIDATE_REQUEST_EXPIRED"

    mutated = list(_fixture())
    mutated[0]["createdBy"] = "attacker"
    assert _code(tuple(mutated)) == "OSB_CANDIDATE_REQUEST_HASH_MISMATCH"

    unsigned = list(_fixture())
    unsigned[3]["verified"] = False
    assert _code(tuple(unsigned)) == "OSB_CANDIDATE_REQUEST_SIGNATURE_UNVERIFIED"


def _set_contract_version(values: list[dict], minor: str) -> None:
    values[0]["contractVersion"] = f"OsbCandidateRequestV1@{minor}"
    values[1]["payloadContractVersion"] = minor
    values[2]["signingStatement"]["payloadContractVersion"] = minor
    _refresh(values)


@pytest.mark.parametrize("version", ["1.1.0", "1.2.0", "1.3.0", "1.4.0"])
def test_incremented_minor_contract_version_is_accepted(version) -> None:
    """CSL's change-window bump inside the V1 major must not 422."""
    values = list(_fixture())
    _set_contract_version(values, version)
    verify_candidate_request_artifact(*values[:2], TENANT_ID, STUDY_ID, *values[2:])


def test_unknown_contract_version_is_rejected() -> None:
    values = list(_fixture())
    # 1.4.0 became an accepted version on 2026-09-21 (plan item A5); 1.5.0 is the next unknown one.
    _set_contract_version(values, "1.5.0")
    assert _code(tuple(values)) == "OSB_CANDIDATE_REQUEST_ARTIFACT_INVALID"


def test_contract_version_mismatch_between_payload_and_artifact_is_rejected() -> None:
    values = list(_fixture())
    values[0]["contractVersion"] = "OsbCandidateRequestV1@1.1.0"
    _refresh(values)
    assert _code(tuple(values)) == "OSB_CANDIDATE_REQUEST_SCOPE_INVALID"


def _refresh_intent_census(values):
    payload = values[0]
    for index, intent in enumerate(payload["typedSourceIntents"]):
        payload["inputConservation"]["rows"][index]["target"]["valueHash"] = canonical_json_hash_ref(
            intent, schema_version="OsbTypedSourceIntentV1@1.0.0"
        )
    payload["inputConservation"]["rowSetHash"] = canonical_json_hash_ref(
        payload["inputConservation"]["rows"], schema_version="ConservationCensusRowsV1@1.0.0"
    )
    _refresh(values)


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0"])
@pytest.mark.parametrize("context", [{}, {"encounters": None}, {"relationships": []}])
def test_historical_request_versions_cannot_claim_new_source_context(version, context):
    values = list(_fixture())
    _set_contract_version(values, version)
    values[0]["typedSourceIntents"][0]["source"]["context"] = context
    _refresh_intent_census(values)
    assert _code(tuple(values)) == "OSB_SOURCE_CONTEXT_REQUEST_VERSION_REQUIRED"


@pytest.mark.parametrize("context", [
    None, [], False, "", {"other": []}, {"encounters": {}},
    {"relationships": "lost"}, {"semanticAssociations": 0}, {"unresolvedRelationships": True},
])
def test_current_request_rejects_malformed_context_sections(context):
    values = list(_fixture())
    _set_contract_version(values, "1.3.0")
    values[0]["typedSourceIntents"][0]["source"]["context"] = context
    _refresh_intent_census(values)
    assert _code(tuple(values)) == "OSB_TYPED_SOURCE_CONTEXT_INVALID"


@pytest.mark.parametrize("context", [
    {},
    {"encounters": None, "relationships": [], "semanticAssociations": [None, False, 0],
     "unresolvedRelationships": [{"sourceFactId": "fact-0001", "reason": "ambiguous"}]},
])
def test_current_request_preserves_absent_null_empty_and_ordered_context(context):
    values = list(_fixture())
    _set_contract_version(values, "1.3.0")
    values[0]["typedSourceIntents"][0]["source"]["context"] = context
    _refresh_intent_census(values)
    before = deepcopy(values)
    verify_candidate_request_artifact(*values[:2], TENANT_ID, STUDY_ID, *values[2:])
    assert values == before
    assert "context" not in values[0]["typedSourceIntents"][1]["source"]


@pytest.mark.parametrize("missing", ["source", "evidence"])
def test_current_request_requires_source_and_evidence_members(missing):
    values = list(_fixture())
    _set_contract_version(values, "1.3.0")
    del values[0]["typedSourceIntents"][0][missing]
    _refresh_intent_census(values)
    assert _code(tuple(values)) == "OSB_TYPED_SOURCE_INTENT_INVALID"


@pytest.mark.parametrize("version,context,code", [
    ("1.2.0", {}, "OSB_SOURCE_CONTEXT_REQUEST_VERSION_REQUIRED"),
    ("1.3.0", {"relationships": {}}, "OSB_TYPED_SOURCE_CONTEXT_INVALID"),
])
def test_transfer_context_guard_rejects_before_retaining_bytes(monkeypatch, version, context, code):
    values = list(_fixture())
    _set_contract_version(values, version)
    values[0]["typedSourceIntents"][0]["source"]["context"] = context
    _refresh_intent_census(values)
    writes = []

    def persist(query, params):
        writes.append(params)
        return [[params["platform_study_id"], params["artifact_version_id"], params["payload_json"],
                 params["byte_size"], params["envelope_json"]]], None

    monkeypatch.setattr(candidate_set_module, "db", SimpleNamespace(cypher_query=persist))
    wire = canonical_json(values[0]).encode("utf-8")
    values[2]["signatureProfile"] = "jws-detached-rfc7797/1.0"
    values[2]["signingStatement"]["payloadHash"] = values[1]["payloadHash"]
    with pytest.raises(OsbCandidateSetError) as error:
        candidate_set_module.store_candidate_request_bytes(
            tenant_id=TENANT_ID, platform_study_id=STUDY_ID,
            bytes_value=wire, expected_hash=sha256_bytes(wire), signed_envelope=values[2],
        )
    assert error.value.code == code
    assert writes == []


def test_current_transfer_retains_exact_context_bytes(monkeypatch):
    values = list(_fixture())
    _set_contract_version(values, "1.3.0")
    values[0]["typedSourceIntents"][0]["source"]["context"] = {
        "encounters": None, "relationships": [], "semanticAssociations": [None, False, 0, 0],
    }
    _refresh_intent_census(values)
    retained = []

    def persist(query, params):
        retained.append(params)
        return [[params["platform_study_id"], params["artifact_version_id"], params["payload_json"],
                 params["byte_size"], params["envelope_json"]]], None

    monkeypatch.setattr(candidate_set_module, "db", SimpleNamespace(cypher_query=persist))
    wire = canonical_json(values[0]).encode("utf-8")
    values[2]["signatureProfile"] = "jws-detached-rfc7797/1.0"
    values[2]["signingStatement"]["payloadHash"] = values[1]["payloadHash"]
    result = candidate_set_module.store_candidate_request_bytes(
        tenant_id=TENANT_ID, platform_study_id=STUDY_ID,
        bytes_value=wire, expected_hash=sha256_bytes(wire), signed_envelope=values[2],
    )
    assert retained[0]["payload_json"].encode("utf-8") == wire
    assert result["contentHash"] == sha256_bytes(wire)
