from copy import deepcopy

import pytest

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json_hash_ref
from clinical_mdr_api.services.integrations.candidate_set import (
    OsbCandidateSetError,
    _assert_request_projection,
    assert_candidate_request_transfer_envelope,
    decode_signed_artifact_envelope_header,
    require_exactly_one_active_osb_binding,
)


def _hash(value, schema: str):
    return canonical_json_hash_ref(value, schema_version=schema)


def _intent(fact_id: str = "fact-1", family: str = "controlled_terminology", extra: dict | None = None):
    source = {
        "assertionType": None,
        "clinicalDomain": None,
        "candidateType": None,
        "exactQuote": None,
        "label": "Age at least 18 years",
        "values": [],
    }
    intent = {
        "factId": fact_id,
        "revision": 1,
        "conceptId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "targetKey": "primary",
        "semanticRole": "protocol concept",
        "resourceFamily": family,
        "source": source,
        "evidence": {"locator": "synthetic"},
        "searchStrings": ["Age at least 18 years"],
        "searchCodes": [],
        "createOption": {"allowed": True, "requestedNativeType": "governed-extension"},
    }
    if extra:
        intent.update(extra)
    return intent


def _census_row(index: int, member: dict, intent: dict, snapshot_version_id: str, request_id: str):
    return {
        "unitId": f'{intent["factId"]}@{intent["revision"]}:primary',
        "source": {
            "artifactId": snapshot_version_id,
            "contract": "accuratrials.csl.SemanticSnapshotV1@1.0.0",
            "type": "active-claim-revision",
            "path": f"#/activeClaimRevisions/{index}",
            "valueHash": member["valueHash"],
        },
        "target": {
            "artifactId": request_id,
            "contract": "accuratrials.osb.OsbCandidateRequestV1@1.0.0",
            "type": "typed-source-intent",
            "path": f"#/typedSourceIntents/{index}",
            "valueHash": _hash(intent, "OsbTypedSourceIntentV1@1.0.0"),
        },
        "multiplicity": {"source": 1, "target": 1},
        "splitMergeGroup": None,
        "splitMergeRule": None,
        "ordering": {"significant": True, "sourceIndex": index, "targetIndex": index},
        "disposition": "native",
        "exclusionPolicy": None,
        "evidenceRefs": [f'source-fact:{intent["factId"]}@{intent["revision"]}'],
        "receiptRefs": [],
    }


def _payload(intents=None, family: str = "controlled_terminology"):
    intents = intents or [_intent(family=family)]
    members = [
        {
            "sourceFactId": intent["factId"],
            "revision": intent["revision"],
            "lifecycle": "accepted",
            "valueHash": _hash({"factId": intent["factId"]}, "FactDto@1.0.0"),
        }
        for intent in intents
    ]
    request_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    snapshot_version_id = "77777777-7777-4777-8777-777777777777"
    rows = [
        _census_row(index, member, intent, snapshot_version_id, request_id)
        for index, (member, intent) in enumerate(zip(members, intents, strict=True))
    ]
    snapshot_hash = _hash({"snapshot": "synthetic"}, "SemanticSnapshotV1@1.0.0")
    package_hash = _hash({"package": "synthetic"}, "SourceFactPackageV1@1.0.0")
    identity = {
        "contractVersion": "1.0.0",
        "system": "osb",
        "tenantId": "11111111-1111-4111-8111-111111111111",
        "platformStudyId": "22222222-2222-4222-8222-222222222222",
        "namespace": "accuratrials-osb",
        "objectType": "study-draft-root",
        "bindingId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "nativeIdentity": "Study_990001",
        "nativeVersion": "0.1",
        "verificationStatus": "verified",
    }
    return {
        "requestId": request_id,
        "requestVersionId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "tenantId": identity["tenantId"],
        "platformStudyId": identity["platformStudyId"],
        "osbStudyIdentity": identity,
        "sourceFactPackage": {
            "packageVersionId": "66666666-6666-4666-8666-666666666666",
            "payloadHash": package_hash,
            "factSetHash": _hash(["fact-1"], "FactSetV1@1.0.0"),
        },
        "semanticSnapshot": {
            "snapshotVersionId": snapshot_version_id,
            "payloadHash": snapshot_hash,
            "memberSetHash": _hash(members, "SemanticSnapshotMemberSetV1@1.0.0"),
        },
        "activeClaimRevisions": members,
        "requestedObjectFamilies": sorted({intent["resourceFamily"] for intent in intents}),
        "typedSourceIntents": intents,
        "evidenceArtifactRefs": [{
            "artifactVersionId": "66666666-6666-4666-8666-666666666666",
            "payloadHash": package_hash,
            "tenantId": identity["tenantId"],
        }],
        "projectionRuleset": {
            "id": "csl-to-osb-candidate-request",
            "version": "1.0.0",
            "hash": _hash({"mapping": "fail-closed-family-router", "version": "1.0.0"}, "ProjectionRulesetV1@1.0.0"),
        },
        "inputConservation": {
            "contractVersion": "ConservationCensusV1@1.0.0",
            "rows": rows,
            "rowSetHash": _hash(rows, "ConservationCensusRowsV1@1.0.0"),
            "counts": {
                "rows": len(members),
                "native": len(intents),
                "governedExtension": 0,
                "excludedSigned": 0,
                "deferredBlocking": 0,
                "quarantined": 0,
                "rejected": 0,
            },
        },
        "checkpointPreconditions": {
            "osbNativeVersion": identity["nativeVersion"],
            "semanticSnapshotHash": snapshot_hash,
        },
    }


def test_valid_projection_is_accepted():
    _assert_request_projection(_payload(), {})


def test_unsupported_family_is_rejected():
    payload = _payload(family="not-a-family")
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_FAMILY_UNSUPPORTED"


def test_target_selection_is_rejected():
    payload = _payload(intents=[_intent(extra={"nativeIdentity": "Study_990001"})])
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_TARGET_SELECTION_FORBIDDEN"


def test_reordered_members_are_rejected():
    payload = _payload(intents=[_intent("fact-2"), _intent("fact-1")])
    payload["activeClaimRevisions"] = list(reversed(payload["activeClaimRevisions"]))
    payload["semanticSnapshot"]["memberSetHash"] = _hash(
        payload["activeClaimRevisions"], "SemanticSnapshotMemberSetV1@1.0.0"
    )
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code in {
        "OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH",
        "OSB_CANDIDATE_REQUEST_MEMBER_HASH_MISMATCH",
    }


def test_mutated_census_is_rejected():
    payload = deepcopy(_payload())
    payload["inputConservation"]["counts"]["rejected"] = 1
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH"


def test_missing_intent_is_rejected():
    payload = _payload(intents=[_intent("fact-1"), _intent("fact-2")])
    payload["typedSourceIntents"] = payload["typedSourceIntents"][:1]
    payload["requestedObjectFamilies"] = ["controlled_terminology"]
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH"


def test_extra_intent_is_rejected():
    payload = _payload()
    payload["typedSourceIntents"].append(_intent("fact-2"))
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH"


def test_duplicate_member_is_rejected():
    payload = _payload(intents=[_intent("fact-1"), _intent("fact-1")])
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_DUPLICATE_MEMBER"


def test_stale_snapshot_checkpoint_is_rejected():
    payload = deepcopy(_payload())
    payload["checkpointPreconditions"]["semanticSnapshotHash"] = _hash(
        {"stale": True}, "SemanticSnapshotV1@1.0.0"
    )
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_CHECKPOINT_MISMATCH"


def _signed_transfer_envelope(payload: dict, payload_hash: str) -> dict:
    hash_ref = {
        "algorithm": "sha-256",
        "canonicalizationVersion": "canonical-json/1.0",
        "value": payload_hash,
        "mediaType": "application/vnd.accuratrials.osb-candidate-request-v1+json",
        "schemaVersion": "OsbCandidateRequestV1@1.0.0",
        "excludedPaths": [],
    }
    descriptor = {
        "kind": "osb-candidate-request",
        "payloadContract": "accuratrials.osb.OsbCandidateRequestV1",
        "payloadContractVersion": "1.0.0",
        "producerService": "csl.attestation",
        "purpose": "osb-candidate-generation",
        "artifactId": payload["requestId"],
        "artifactVersionId": payload["requestVersionId"],
        "tenantId": payload["tenantId"],
        "payloadHash": hash_ref,
    }
    return {
        "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
        "signatureProfile": "jws-detached-rfc7797/1.0",
        "artifactDescriptor": descriptor,
        "payloadHash": hash_ref,
        "signingStatement": {
            "signingPurpose": "osb-candidate-request",
            "producerService": "csl.attestation",
            "payloadContract": "accuratrials.osb.OsbCandidateRequestV1",
            "payloadHash": hash_ref,
        },
    }


def test_unsigned_transfer_envelope_is_rejected():
    payload = _payload()
    with pytest.raises(OsbCandidateSetError) as error:
        assert_candidate_request_transfer_envelope(payload, "sha256:" + "a" * 64, None)
    assert error.value.code == "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED"


def test_mismatched_transfer_envelope_is_rejected():
    payload = _payload()
    envelope = _signed_transfer_envelope(payload, "sha256:" + "a" * 64)
    envelope["signingStatement"]["signingPurpose"] = "osb-candidate-generation"
    with pytest.raises(OsbCandidateSetError) as error:
        assert_candidate_request_transfer_envelope(payload, "sha256:" + "a" * 64, envelope)
    assert error.value.code == "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID"


def test_matching_transfer_envelope_is_accepted():
    payload = _payload()
    digest = "sha256:" + "a" * 64
    envelope = _signed_transfer_envelope(payload, digest)
    bound = assert_candidate_request_transfer_envelope(payload, digest, envelope)
    assert bound["artifactDescriptor"]["artifactId"] == payload["requestId"]


def test_missing_signed_envelope_header_is_rejected():
    with pytest.raises(OsbCandidateSetError) as error:
        decode_signed_artifact_envelope_header(None)
    assert error.value.code == "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED"


def test_zero_or_multiple_osb_bindings_are_rejected():
    with pytest.raises(OsbCandidateSetError) as error:
        require_exactly_one_active_osb_binding([])
    assert error.value.code == "OSB_NATIVE_IDENTITY_BINDING_REQUIRED"
    with pytest.raises(OsbCandidateSetError) as error:
        require_exactly_one_active_osb_binding([
            ["b1", "Study_A", "0.1"],
            ["b2", "Study_B", "0.1"],
        ])
    assert error.value.code == "OSB_NATIVE_IDENTITY_BINDING_REQUIRED"
    selected = require_exactly_one_active_osb_binding([["b1", "Study_A", "0.1"]])
    assert selected == {"bindingId": "b1", "nativeIdentity": "Study_A", "nativeVersion": "0.1"}
