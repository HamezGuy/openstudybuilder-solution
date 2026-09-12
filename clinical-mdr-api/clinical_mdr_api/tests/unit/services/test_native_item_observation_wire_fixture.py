"""Authored complete native wire input; original schemas are the conformance oracle."""
import copy
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json, canonical_json_hash_ref as hash_ref, descriptor_hash
from clinical_mdr_api.tests.unit.services.test_candidate_set_v1 import _fixture


def h(value, schema):
    return hash_ref(value, schema_version=schema)


def complete_original_wire(source, candidate, decision, native, context, context_hash):
    complete = _fixture()[0]
    complete["requestVersionId"] = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    complete["osbStudyIdentity"] = native
    intent = complete["typedSourceIntents"][0]
    intent.update({"factId": "fact-1", "resourceFamily": "odm_items"})
    intent["source"]["label"] = "Authored Item concept"
    complete["typedSourceIntents"] = [intent]
    complete["activeClaimRevisions"] = [complete["activeClaimRevisions"][0]]
    complete["activeClaimRevisions"][0]["sourceFactId"] = "fact-1"
    complete["requestedObjectFamilies"] = ["odm_items"]
    artifact = complete["evidenceArtifactRefs"][0]
    artifact.update({"kind": "source-fact-package", "stableLocator": f"artifact://authored/source/{artifact['artifactVersionId']}",
        "byteSize": len(canonical_json({"source": True}).encode()), "classification": "regulated-non-phi",
        "region": "us-central-1", "producerService": "authored.source", "producerEnvironment": "prototype",
        "producerVersion": "fixture", "payloadContract": "accuratrials.il.SourceFactPackageV1", "payloadContractVersion": "1.0.0",
        "purpose": "source-fact-publication", "createdAt": complete["createdAt"]})
    artifact["descriptorHash"] = descriptor_hash({**artifact, "contractVersion": "ArtifactDescriptorV1@1.0.0"})
    complete["semanticSnapshot"]["memberSetHash"] = h(complete["activeClaimRevisions"], "SemanticSnapshotMemberSetV1@1.0.0")
    rows = complete["inputConservation"]["rows"][:1]
    rows[0]["unitId"] = "fact-1@1:primary"
    rows[0]["target"]["valueHash"] = h(intent, "OsbTypedSourceIntentV1@1.0.0")
    rows[0]["evidenceRefs"] = ["source-fact:fact-1@1"]
    complete["inputConservation"].update({"rows": rows, "rowSetHash": h(rows, "ConservationCensusRowsV1@1.0.0")})
    complete["inputConservation"]["counts"].update({"rows": 1, "native": 1})
    complete["expiresAt"] = "2099-01-01T00:00:00Z"
    source.clear()
    source.update(complete)
    record = candidate["candidateRecords"][0]
    record.update({"conceptId": intent["conceptId"], "complete": True, "truncated": False, "blockers": []})
    census = copy.deepcopy(rows)
    census[0]["source"] = {"artifactId": complete["requestId"], "contract": "accuratrials.osb.OsbCandidateRequestV1",
        "type": "OsbTypedSourceIntentV1", "path": "#/typedSourceIntents/0", "valueHash": h(intent, "OsbTypedSourceIntentV1@1.0.0")}
    census[0]["target"] = {"artifactId": candidate["candidateSetId"], "contract": "accuratrials.osb.OsbCandidateSetV1",
        "type": "OsbCandidateRecordV1", "path": "#/candidateRecords/0", "valueHash": h(record, "OsbCandidateRecordV1@1.0.0")}
    census[0]["evidenceRefs"] = [f"osb-context:{context_hash}"]
    candidate.update({"semanticSnapshot": complete["semanticSnapshot"], "sourceFactPackage": complete["sourceFactPackage"],
        "mappingContext": {**context, "mappingAuthority": "OpenStudyBuilder", "contextHash": context_hash}, "conservation": {"contractVersion": "ConservationCensusV1@1.0.0", "rows": census,
        "rowSetHash": h(census, "ConservationCensusRowsV1@1.0.0"), "counts": complete["inputConservation"]["counts"]},
        "assignment": {"contractVersion": "CandidateAssignmentProjectionV1@1.0.0", "assignmentId": "abababab-abab-4bab-8bab-abababababab",
          "kind": "mapping-adjudication", "tenantId": complete["tenantId"], "platformStudyId": complete["platformStudyId"],
          "candidateSetVersionId": candidate["candidateSetVersionId"]}, "blockers": [],
        "createdAt": complete["createdAt"], "createdBy": "authored-fixture"})
    candidate["capabilityCheckpoint"]["governed"] = True
    decision["statement"]["semanticSnapshotHash"] = complete["semanticSnapshot"]["payloadHash"]
    decision["humanSignature"].update({"issuerQualifiedIdentity": {"issuer": "https://authored.invalid", "subject": "authored-human", "tenantId": complete["tenantId"]},
        "signerNameSnapshot": "Authored fixture reviewer", "nativeUserBindingId": None, "rolesAtSigning": ["Study.Write"],
        "assignmentsAtSigning": [candidate["assignment"]["assignmentId"]],
        "reauthentication": {"authTime": None, "acr": None, "amr": [], "sessionIdentifierHash": h({"session": "authored"}, "SessionIdentifierV1@1.0.0")},
        "tenantId": complete["tenantId"], "platformStudyId": complete["platformStudyId"], "failedAttemptAuditRefs": []})


def validate_original_wire(f):
    native = Path(__file__).resolve().parents[3] / "schemas/platform"
    csl = Path("C:/Projects/ClinicalSemanticLayer/packages/contracts/schema/platform")
    platform = Path("C:/Projects/CommandCenter/contracts/platform-control-plane/v1")
    resources = [json.loads((platform / name).read_text()) for name in ["artifact-ref-v1.schema.json", "hash-ref-v1.schema.json"]]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in resources)
    for value, path in [(f.blobs[2], native / "osb-candidate-request-v1.schema.json"),
                        (f.blobs[1], native / "osb-candidate-set-v1.schema.json"),
                        (f.blobs[0], csl / "study-mapping-decision-v1.schema.json"),
                        (f.blobs[6], native / "native-operation-evidence-v1.schema.json"),
                        (f.blobs[5], platform / "artifact-ref-v1.schema.json"),
                        (f.blobs[2]["evidenceArtifactRefs"][0], platform / "artifact-ref-v1.schema.json")]:
        Draft202012Validator(json.loads(path.read_text()), format_checker=FormatChecker(), registry=registry).validate(value)
    # Native has no generated evidence-set wrapper schema. Validate every
    # operation with its original schema and conserve actual producer wrappers.
    for wrapped in f.blobs[3]["evidenceRecords"]:
        assert wrapped["payloadHash"] == h(wrapped["evidence"], "NativeOperationEvidenceV1@1.0.0")
    assert f.blobs[3] == f.applied["payload"]
    assert f.blobs[5] == f.applied["artifactRef"]


def test_complete_original_inputs_before_observer():
    from clinical_mdr_api.tests.unit.services.test_native_item_observation import fixture
    f = fixture()
    validate_original_wire(f)
    result = f.service.observe(f.request)
    assert result["semanticApprovalVerified"] is False
    assert f.blobs[1]["osbStudyIdentity"]["contractVersion"] == "1.0.0"


def test_retained_legacy_scalar_operation_is_not_current_companion_evidence():
    from clinical_mdr_api.tests.unit.services.test_native_item_observation import fixture, _hash
    from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError
    import pytest
    f = fixture()
    operation = f.blobs[3]["evidenceRecords"][0]["evidence"]
    operation["nativeTargetIdentity"] = f.request.itemUid
    f.blobs[6].clear()
    f.blobs[6].update(copy.deepcopy(operation))
    f.blobs[3]["evidenceRecords"][0]["payloadHash"] = h(operation, "NativeOperationEvidenceV1@1.0.0")
    artifact = f.blobs[5]
    artifact["payloadHash"] = _hash(f.blobs[3], "OsbNativeEvidenceSetV1@1.0.0", "application/vnd.accuratrials.osb-native-evidence-set-v1+json")
    artifact["byteSize"] = len(canonical_json(f.blobs[3]).encode())
    artifact["descriptorHash"] = descriptor_hash({"contractVersion": "ArtifactDescriptorV1@1.0.0",
        **{k: v for k, v in artifact.items() if k not in {"contractVersion", "descriptorHash"}}})
    f.request = f.request.model_copy(update={"evidenceSetHash": artifact["payloadHash"]["value"]})
    with pytest.raises(NativeItemObservationError, match="OSB_ITEM_CUSTODY_MISMATCH"):
        f.service.observe(f.request)
