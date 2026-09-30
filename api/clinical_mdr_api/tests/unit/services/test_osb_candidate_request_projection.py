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


def _routed(
    fact_id: str,
    disposition: str = "deferred_blocking",
    reason: str = "routing:OSB_RESOURCE_TYPE_WITHOUT_CANDIDATE_FAMILY",
    revision: int = 1,
):
    """A claim CSL's family router sent away from native execution."""
    return {
        "factId": fact_id,
        "revision": revision,
        "disposition": disposition,
        "reasonCodes": [reason],
    }


def _census_row(member_index: int, intent_index: int, member: dict, intent: dict,
                snapshot_version_id: str, request_id: str):
    return {
        "unitId": f'{intent["factId"]}@{intent["revision"]}:primary',
        "source": {
            "artifactId": snapshot_version_id,
            "contract": "accuratrials.csl.SemanticSnapshotV1@1.0.0",
            "type": "active-claim-revision",
            "path": f"#/activeClaimRevisions/{member_index}",
            "valueHash": member["valueHash"],
        },
        "target": {
            "artifactId": request_id,
            "contract": "accuratrials.osb.OsbCandidateRequestV1@1.0.0",
            "type": "typed-source-intent",
            "path": f"#/typedSourceIntents/{intent_index}",
            "valueHash": _hash(intent, "OsbTypedSourceIntentV1@1.0.0"),
        },
        "multiplicity": {"source": 1, "target": 1},
        "splitMergeGroup": None,
        "splitMergeRule": None,
        "ordering": {"significant": True, "sourceIndex": member_index, "targetIndex": intent_index},
        "disposition": "native",
        "exclusionPolicy": None,
        "evidenceRefs": [f'source-fact:{intent["factId"]}@{intent["revision"]}'],
        "receiptRefs": [],
    }


def _routed_census_row(member_index: int, member: dict, item: dict, snapshot_version_id: str):
    return {
        "unitId": f'{item["factId"]}@{item["revision"]}:primary',
        "source": {
            "artifactId": snapshot_version_id,
            "contract": "accuratrials.csl.SemanticSnapshotV1@1.0.0",
            "type": "active-claim-revision",
            "path": f"#/activeClaimRevisions/{member_index}",
            "valueHash": member["valueHash"],
        },
        "target": None,
        "multiplicity": {"source": 1, "target": 0},
        "splitMergeGroup": None,
        "splitMergeRule": None,
        "ordering": {"significant": False, "sourceIndex": member_index, "targetIndex": None},
        "disposition": item["disposition"],
        "exclusionPolicy": None,
        "evidenceRefs": [f'source-fact:{item["factId"]}@{item["revision"]}', *item["reasonCodes"]],
        "receiptRefs": [],
    }


_COUNT_KEYS = {
    "native": "native", "governed_extension": "governedExtension",
    "excluded_signed": "excludedSigned", "deferred_blocking": "deferredBlocking",
    "quarantined": "quarantined", "rejected": "rejected",
}


def _counts(rows: list[dict]) -> dict:
    counts = {"rows": len(rows), "native": 0, "governedExtension": 0, "excludedSigned": 0,
              "deferredBlocking": 0, "quarantined": 0, "rejected": 0}
    for row in rows:
        counts[_COUNT_KEYS[row["disposition"]]] += 1
    return counts


def _refresh_census_hash(payload: dict) -> None:
    census = payload["inputConservation"]
    census["rowSetHash"] = _hash(census["rows"], "ConservationCensusRowsV1@1.0.0")


def _payload(intents=None, family: str = "controlled_terminology", routed=None):
    intents = intents or [_intent(family=family)]
    routed = list(routed or [])
    units = sorted(
        [("native", intent) for intent in intents]
        + [("routed", item) for item in routed],
        key=lambda unit: f'{unit[1]["factId"]}@{unit[1]["revision"]}',
    )
    members = [
        {
            "sourceFactId": item["factId"],
            "revision": item["revision"],
            "lifecycle": "accepted",
            "valueHash": _hash({"factId": item["factId"]}, "FactDto@1.0.0"),
        }
        for _, item in units
    ]
    request_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    snapshot_version_id = "77777777-7777-4777-8777-777777777777"
    rows = []
    for member_index, ((kind, item), member) in enumerate(zip(units, members, strict=True)):
        if kind == "native":
            rows.append(_census_row(
                member_index, intents.index(item), member, item,
                snapshot_version_id, request_id,
            ))
        else:
            rows.append(_routed_census_row(member_index, member, item, snapshot_version_id))
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
        "contractVersion": "OsbCandidateRequestV1@1.0.0",
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
            "counts": _counts(rows),
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


def test_routed_claims_validate():
    """The family router's governed_extension / deferred_blocking rows (target
    null, multiplicity.target 0, routing reason codes) are a valid request."""
    payload = _payload(
        intents=[_intent("fact-native-a"), _intent("fact-native-b")],
        routed=[
            _routed("fact-x-deferred", "deferred_blocking"),
            _routed("fact-y-governed", "governed_extension"),
        ],
    )
    counts = payload["inputConservation"]["counts"]
    assert counts == {"rows": 4, "native": 2, "governedExtension": 1,
                      "excludedSigned": 0, "deferredBlocking": 1,
                      "quarantined": 0, "rejected": 0}
    _assert_request_projection(payload, {})


def test_routed_census_counts_must_match_rows():
    payload = deepcopy(_payload(
        intents=[_intent("fact-native-a")],
        routed=[_routed("fact-x-deferred", "deferred_blocking")],
    ))
    payload["inputConservation"]["counts"]["deferredBlocking"] = 2
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH"


def test_routed_row_with_target_is_rejected():
    payload = deepcopy(_payload(
        intents=[_intent("fact-native-a")],
        routed=[_routed("fact-x-deferred", "deferred_blocking")],
    ))
    for row in payload["inputConservation"]["rows"]:
        if row["disposition"] == "deferred_blocking":
            row["target"] = {"artifactId": "x", "contract": "y", "type": "z", "path": "#/x"}
            row["multiplicity"] = {"source": 1, "target": 1}
    _refresh_census_hash(payload)
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH"


def test_native_row_without_target_is_rejected():
    payload = deepcopy(_payload())
    payload["inputConservation"]["rows"][0]["target"] = None
    _refresh_census_hash(payload)
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH"


def test_unknown_census_disposition_is_rejected():
    payload = deepcopy(_payload())
    payload["inputConservation"]["rows"][0]["disposition"] = "vaporized"
    _refresh_census_hash(payload)
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH"


def test_intent_for_routed_member_is_rejected():
    """A member whose census row is deferred must not also carry an intent."""
    payload = deepcopy(_payload(intents=[_intent("fact-a"), _intent("fact-b")]))
    for row in payload["inputConservation"]["rows"]:
        if row["unitId"].startswith("fact-b@"):
            row["target"] = None
            row["multiplicity"] = {"source": 1, "target": 0}
            row["disposition"] = "deferred_blocking"
    census = payload["inputConservation"]
    census["counts"] = _counts(census["rows"])
    _refresh_census_hash(payload)
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH"


def test_additive_change_window_fields_are_accepted():
    intent = _intent(extra={
        "semanticRole": "eligibility_criterion",
        "searchStringsOmitted": 2,
        "searchCodesOmitted": 0,
        "createOption": {"allowed": True, "requestedNativeType": None},
    })
    intent["source"] = {
        **intent["source"],
        "classification": {"assertionType": "eligibility", "clinicalDomain": "demographics"},
    }
    payload = _payload(
        intents=[intent],
        routed=[_routed("fact-x-deferred", "deferred_blocking")],
    )
    payload["inputConservation"]["upstreamExclusions"] = {
        "sourcePackageCensusHash": "sha256:" + "e" * 64,
        "excludedSigned": 3,
        "quarantined": 1,
    }
    _assert_request_projection(payload, {})


@pytest.mark.parametrize("name,value", [
    ("searchStringsOmitted", -1),
    ("searchStringsOmitted", "2"),
    ("searchCodesOmitted", True),
])
def test_omission_counter_wrong_types_are_rejected(name, value):
    payload = _payload(intents=[_intent(extra={name: value})])
    with pytest.raises(OsbCandidateSetError) as error:
        _assert_request_projection(payload, {})
    assert error.value.code == "OSB_TYPED_SOURCE_INTENT_INVALID"


def test_upstream_exclusions_wrong_types_are_rejected():
    payload = deepcopy(_payload())
    payload["inputConservation"]["upstreamExclusions"] = {
        "sourcePackageCensusHash": "sha256:" + "e" * 64,
        "excludedSigned": "3",
        "quarantined": 0,
    }
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
    contract_version = payload.get("contractVersion", "OsbCandidateRequestV1@1.0.0")
    hash_ref = {
        "algorithm": "sha-256",
        "canonicalizationVersion": "canonical-json/1.0",
        "value": payload_hash,
        "mediaType": "application/vnd.accuratrials.osb-candidate-request-v1+json",
        "schemaVersion": contract_version,
        "excludedPaths": [],
    }
    descriptor = {
        "kind": "osb-candidate-request",
        "payloadContract": "accuratrials.osb.OsbCandidateRequestV1",
        "payloadContractVersion": contract_version.split("@", 1)[1],
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


@pytest.mark.parametrize(
    "contract_version",
    ["OsbCandidateRequestV1@1.0.0", "OsbCandidateRequestV1@1.1.0"],
)
def test_matching_transfer_envelope_is_accepted(contract_version):
    payload = _payload()
    payload["contractVersion"] = contract_version
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
