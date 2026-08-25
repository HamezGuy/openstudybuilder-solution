from __future__ import annotations

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

TENANT = "11111111-1111-4111-8111-111111111111"
STUDY = "22222222-2222-4222-8222-222222222222"
OPENAPI_HASH = "sha256:" + "ab" * 32


class FakeQuery:
    def __init__(self, binding=None):
        self.binding = binding or (
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "Study_990001", "0.1",
        )
        self.sets: dict[tuple[str, str], list] = {}

    def cypher_query(self, query, params=None):
        params = params or {}
        if "PlatformNativeStudyBinding" in query:
            return ([list(self.binding)], None)
        if "OsbCandidateRequestLock" in query:
            return ([[1]], None)
        if "MATCH (candidate:OsbCandidateSetV1" in query:
            stored = self.sets.get((params["tenant_id"], params["request_hash"]))
            return ([stored], None) if stored else ([], None)
        if "MERGE (request:OsbCandidateRequestV1" in query:
            row = [
                params["payload_json"], params["payload_hash"], params["artifact_ref_json"],
                params["set_id"], params["set_version_id"], params["native_study_id"],
                params["native_version"], params["signed_envelope_json"],
            ]
            self.sets[(params["tenant_id"], params["request_hash"])] = row
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


def test_create_option_without_native_target_is_governed_extension(monkeypatch):
    generated, *_ = _generate(monkeypatch, mapping=FakeMapping(native=False))
    assert generated["payload"]["conservation"]["counts"]["governedExtension"] == 1
    assert generated["payload"]["candidateRecords"][0]["createOption"]["allowed"] is True


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
