"""Consume the one CSL decision and materialize lossless OSB managed concepts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
    hash_refs_equal,
    sha256_bytes,
)
from clinical_mdr_api.services.integrations.candidate_set import (
    CANDIDATE_SET_MEDIA_TYPE,
    OsbCandidateSetError,
    assert_candidate_set_current,
)

STUDY_FAMILIES = frozenset({
    "activities", "compound_product_relationships", "controlled_terminology",
    "criteria_templates", "endpoint_templates", "objective_templates",
    "study_compound_dosing_relationships", "units",
})
CAPTURE_FAMILIES = frozenset({"odm_forms", "odm_item_groups", "odm_items"})


def executor_kind_for_family(family: str) -> str:
    if family in STUDY_FAMILIES:
        return "study"
    if family in CAPTURE_FAMILIES:
        return "capture"
    raise OsbCandidateSetError(
        "OSB_FAMILY_EXECUTOR_UNSUPPORTED",
        f"Resource family {family} has no executor.",
        422,
    )


def _record(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OsbCandidateSetError(code, "Expected an object.", 422)
    return value


def _list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise OsbCandidateSetError(code, "Expected an array.", 422)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OsbCandidateSetError("OSB_MAPPING_DECISION_DUPLICATE_KEY", f"Duplicate JSON key {key}.", 422)
        result[key] = value
    return result


def _artifact_ref(fields: dict[str, Any]) -> dict[str, Any]:
    descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}
    return {"contractVersion": "ArtifactRefV1@1.0.0", **fields, "descriptorHash": descriptor_hash(descriptor)}


def store_mapping_decision_bytes(
    *, tenant_id: str, platform_study_id: str, bytes_value: bytes, expected_hash: str
) -> dict[str, Any]:
    if sha256_bytes(bytes_value) != expected_hash:
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_TRANSFER_HASH_MISMATCH", "Decision bytes differ.", 422)
    try:
        payload = json.loads(bytes_value.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_JSON_INVALID", "Decision is not UTF-8 JSON.", 422) from error
    if not isinstance(payload, dict) or canonical_json(payload).encode("utf-8") != bytes_value:
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_NONCANONICAL", "Decision bytes are not canonical.", 422)
    statement = _record(payload.get("statement"), "OSB_MAPPING_DECISION_STATEMENT_REQUIRED")
    if (
        payload.get("contractVersion") != "StudyMappingDecisionV1@1.0.0"
        or statement.get("tenantId") != tenant_id
        or statement.get("platformStudyId") != platform_study_id
    ):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_TRANSFER_SCOPE_MISMATCH", "Decision scope differs.", 422)
    rows, _ = db.cypher_query(
        """MERGE (artifact:OsbInboundArtifact {tenant_id: $tenant_id, payload_hash: $payload_hash})
           ON CREATE SET artifact.platform_study_id=$platform_study_id,
             artifact.artifact_version_id=$artifact_version_id,
             artifact.kind='study-mapping-decision',artifact.payload_json=$payload_json,
             artifact.byte_size=$byte_size,artifact.created_at=datetime()
           RETURN artifact.platform_study_id,artifact.artifact_version_id,
                  artifact.payload_json,artifact.byte_size""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
         "payload_hash": expected_hash, "artifact_version_id": statement["decisionId"],
         "payload_json": canonical_json(payload), "byte_size": len(bytes_value)},
    )
    row = rows[0] if rows else None
    if not row or str(row[0]) != platform_study_id or str(row[1]) != statement["decisionId"] \
            or str(row[2]) != canonical_json(payload) or int(row[3]) != len(bytes_value):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_TRANSFER_CONFLICT", "Decision hash names different content.")
    return {"contractVersion": "ArtifactTransferReceiptV1@prototype", "kind": "study-mapping-decision",
            "tenantId": tenant_id, "platformStudyId": platform_study_id,
            "contentHash": expected_hash, "byteSize": len(bytes_value)}


def _load_decision(tenant_id: str, platform_study_id: str, payload_hash: str) -> dict[str, Any]:
    rows, _ = db.cypher_query(
        """MATCH (artifact:OsbInboundArtifact {tenant_id: $tenant_id,
             platform_study_id: $platform_study_id,payload_hash: $payload_hash,
             kind: 'study-mapping-decision'}) RETURN artifact.payload_json""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "payload_hash": payload_hash},
    )
    if not rows:
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_NOT_TRANSFERRED", "Exact decision bytes are unavailable.", 404)
    return _record(json.loads(str(rows[0][0])), "OSB_MAPPING_DECISION_STORED_INVALID")


def _verify_artifact(payload: dict[str, Any], artifact: dict[str, Any], tenant_id: str) -> None:
    statement = _record(payload.get("statement"), "OSB_MAPPING_DECISION_STATEMENT_REQUIRED")
    if (
        artifact.get("contractVersion") != "ArtifactRefV1@1.0.0"
        or artifact.get("kind") != "study-mapping-decision"
        or artifact.get("tenantId") != tenant_id
        or artifact.get("artifactId") != statement.get("decisionId")
        or artifact.get("payloadContract") != "accuratrials.csl.StudyMappingDecisionV1"
    ):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_ARTIFACT_INVALID", "Decision artifact differs.", 422)
    expected_hash = canonical_json_hash_ref(payload, schema_version="StudyMappingDecisionV1@1.0.0")
    if not hash_refs_equal(expected_hash, artifact.get("payloadHash")):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_HASH_MISMATCH", "Decision payload hash differs.", 422)
    fields = {key: value for key, value in artifact.items() if key not in {"contractVersion", "descriptorHash"}}
    if not hash_refs_equal(descriptor_hash({"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}), artifact.get("descriptorHash")):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_DESCRIPTOR_MISMATCH", "Decision descriptor differs.", 422)
    statement_hash = canonical_json_hash_ref(statement, schema_version="StudyMappingDecisionStatementV1@1.0.0")
    signature = _record(payload.get("humanSignature"), "OSB_MAPPING_DECISION_SIGNATURE_REQUIRED")
    if not hash_refs_equal(statement_hash, signature.get("recordHash")):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_HUMAN_SIGNATURE_MISMATCH", "Human record does not bind the statement.", 422)
    unsigned = {"contractVersion": payload["contractVersion"], "statement": statement, "humanSignature": signature}
    attestation = _record(payload.get("serviceAttestation"), "OSB_MAPPING_DECISION_ATTESTATION_REQUIRED")
    if not hash_refs_equal(canonical_json_hash_ref(unsigned, schema_version="StudyMappingDecisionCompositeV1@1.0.0"),
                           attestation.get("compositeHash")):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_ATTESTATION_MISMATCH", "CSL attestation differs.", 422)


def apply_mapping_decision(
    *, tenant_id: str, platform_study_id: str, decision_artifact: dict[str, Any], actor: str
) -> dict[str, Any]:
    decision_hash = _record(decision_artifact.get("payloadHash"), "OSB_MAPPING_DECISION_HASH_REQUIRED").get("value")
    if not isinstance(decision_hash, str):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_HASH_REQUIRED", "Decision hash is required.", 422)
    payload = _load_decision(tenant_id, platform_study_id, decision_hash)
    _verify_artifact(payload, decision_artifact, tenant_id)
    statement = _record(payload["statement"], "OSB_MAPPING_DECISION_STATEMENT_REQUIRED")
    candidate_set_hash = _record(statement.get("candidateSetHash"), "OSB_MAPPING_DECISION_CANDIDATE_HASH_REQUIRED")["value"]
    rows, _ = db.cypher_query(
        """MATCH (candidate:OsbCandidateSetV1 {tenant_id: $tenant_id,
             platform_study_id: $platform_study_id,payload_hash: $candidate_set_hash})
           MATCH (candidate)-[:GENERATED_FROM]->(request:OsbCandidateRequestV1)
           RETURN candidate.payload_json,request.payload_json,candidate.native_study_id,
                  candidate.native_version,candidate.candidate_set_version_id""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
         "candidate_set_hash": candidate_set_hash},
    )
    if len(rows) != 1:
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_CANDIDATE_SET_NOT_FOUND", "Exact candidate set is unavailable.")
    candidate_set = _record(json.loads(str(rows[0][0])), "OSB_CANDIDATE_SET_STORED_INVALID")
    candidate_request = _record(json.loads(str(rows[0][1])), "OSB_CANDIDATE_REQUEST_STORED_INVALID")
    native_study_id, native_version, candidate_set_version_id = str(rows[0][2]), str(rows[0][3]), str(rows[0][4])
    expected_candidate_hash = canonical_json_hash_ref(
        candidate_set, schema_version="OsbCandidateSetV1@1.0.0",
        media_type=CANDIDATE_SET_MEDIA_TYPE,
    )
    if not hash_refs_equal(expected_candidate_hash, statement.get("candidateSetHash")):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_CANDIDATE_SET_MISMATCH", "Decision does not bind the stored candidate set.", 422)
    checkpoint = _record(candidate_set.get("capabilityCheckpoint"), "OSB_CANDIDATE_CHECKPOINT_REQUIRED")
    if statement.get("mappingContextHash") != checkpoint.get("mappingContextHash"):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_CONTEXT_MISMATCH", "Decision mapping context differs.", 422)
    assert_candidate_set_current(
        candidate_set,
        binding={
            "bindingId": str(_record(candidate_set.get("osbStudyIdentity"), "OSB_CANDIDATE_SET_IDENTITY_REQUIRED").get("bindingId") or ""),
            "nativeIdentity": native_study_id,
            "nativeVersion": native_version,
        },
        osb_openapi_hash=str(checkpoint.get("osbOpenApiHash") or ""),
    )
    db.cypher_query(
        """MERGE (lock:OsbCandidateRequestLock {key: $key})
           ON CREATE SET lock.created_at=datetime(),lock.revision=0
           SET lock.revision=lock.revision+1,lock.touched_at=datetime() RETURN lock.revision""",
        {"key": f"mapping-decision|{tenant_id}|{decision_hash}"},
    )
    prior, _ = db.cypher_query(
        """MATCH (evidence:OsbNativeEvidenceSetV1 {tenant_id: $tenant_id,decision_hash: $decision_hash})
           RETURN evidence.payload_json,evidence.payload_hash,evidence.artifact_ref_json,
                  evidence.evidence_set_id,evidence.evidence_set_version_id""",
        {"tenant_id": tenant_id, "decision_hash": decision_hash},
    )
    if prior:
        row = prior[0]
        return {"payload": json.loads(str(row[0])), "payloadHash": {"algorithm": "sha-256",
                "canonicalizationVersion": "canonical-json/1.0", "value": str(row[1]),
                "mediaType": EVIDENCE_SET_MEDIA_TYPE, "schemaVersion": "OsbNativeEvidenceSetV1@1.0.0",
                "excludedPaths": []}, "artifactRef": json.loads(str(row[2])),
                "evidenceSetId": str(row[3]), "evidenceSetVersionId": str(row[4]),
                "nativeIdentity": native_study_id, "nativeVersion": native_version, "replay": True}
    record_values = [_record(value, "OSB_CANDIDATE_RECORD_INVALID") for value in _list(
        candidate_set.get("candidateRecords"), "OSB_CANDIDATE_RECORDS_REQUIRED"
    )]
    intent_values = [_record(value, "OSB_SOURCE_INTENT_INVALID") for value in _list(
        candidate_request.get("typedSourceIntents"), "OSB_SOURCE_INTENTS_REQUIRED"
    )]
    records = {_key(value): value for value in record_values}
    intents = {_key(value): value for value in intent_values}
    selections = [_record(item, "OSB_MAPPING_SELECTION_INVALID") for item in _list(statement.get("selections"), "OSB_MAPPING_SELECTIONS_REQUIRED")]
    if len(records) != len(record_values) or len(intents) != len(intent_values) \
            or len(selections) != len(records) or {_key(value) for value in selections} != set(records):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_COVERAGE_MISMATCH", "Decision must cover every candidate record exactly once.", 422)
    expected_decision_set_hash = canonical_json_hash_ref(
        selections, schema_version="StudyMappingSelectionSetV1@1.0.0"
    )
    if not hash_refs_equal(expected_decision_set_hash, statement.get("decisionSetHash")):
        raise OsbCandidateSetError("OSB_MAPPING_DECISION_SET_HASH_MISMATCH", "Decision-set hash differs.", 422)
    for selection in selections:
        offered = records[_key(selection)]
        action = str(selection.get("action") or "")
        selected = selection.get("candidateIdentity")
        if action == "select" and not any(
            canonical_json(candidate) == canonical_json(selected)
            for candidate in _list(offered.get("nativeCandidates"), "OSB_NATIVE_CANDIDATES_REQUIRED")
        ):
            raise OsbCandidateSetError("OSB_MAPPING_DECISION_CANDIDATE_NOT_OFFERED", "Selected target was not offered.", 422)
        if action == "create" and not _record(offered.get("createOption"), "OSB_CREATE_OPTION_REQUIRED").get("allowed"):
            raise OsbCandidateSetError("OSB_MAPPING_DECISION_CREATE_NOT_OFFERED", "Create was not offered.", 422)
        if action not in {"select", "create", "reject", "defer"}:
            raise OsbCandidateSetError("OSB_MAPPING_DECISION_ACTION_INVALID", "Unsupported decision action.", 422)
    evidence_records: list[dict[str, Any]] = []
    managed_keys: list[str] = []
    operation_time = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    for selection in selections:
        key = _key(selection)
        candidate = records.get(key)
        source_intent = intents.get(key)
        if not candidate or not source_intent:
            raise OsbCandidateSetError("OSB_MAPPING_DECISION_SELECTION_STALE", f"Selection {key} is stale.")
        executor_kind = executor_kind_for_family(str(candidate["resourceFamily"]))
        action = str(selection.get("action"))
        operation_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-operation:v1:{statement['decisionId']}:{key}"))
        managed_key = f"{tenant_id}|{platform_study_id}|{key}"
        source_hash = canonical_json_hash_ref(source_intent, schema_version="OsbTypedSourceIntentV1@1.0.0")
        selected = selection.get("candidateIdentity")
        target_payload = {
            "managedKey": managed_key, "nativeStudyId": native_study_id,
            "factId": selection["factId"], "revision": selection["revision"],
            "targetKey": selection["targetKey"], "resourceFamily": candidate["resourceFamily"],
            "semanticRole": candidate["semanticRole"], "action": action,
            "source": source_intent.get("source"), "selectedNativeTarget": selected,
            "rationale": selection["rationale"], "decisionId": statement["decisionId"],
        }
        target_hash = canonical_json_hash_ref(target_payload, schema_version="OsbManagedStudyConceptV1@1.0.0")
        if action in {"select", "create"}:
            created, _ = db.cypher_query(
                """MATCH (study:StudyRoot {uid: $study_uid})
                   MERGE (concept:PlatformManagedStudyConcept {managed_key: $managed_key})
                   ON CREATE SET concept.tenant_id=$tenant_id,concept.platform_study_id=$platform_study_id,
                     concept.study_uid=$study_uid,concept.fact_id=$fact_id,concept.revision=$revision,
                     concept.target_key=$target_key,concept.action=$action,concept.resource_family=$resource_family,
                     concept.payload_json=$payload_json,concept.content_hash=$content_hash,
                     concept.version=1,concept.created_at=datetime(),concept.created_by=$actor
                   MERGE (study)-[:HAS_PLATFORM_MANAGED_CONCEPT]->(concept)
                   RETURN concept.payload_json,concept.content_hash,concept.version""",
                {"study_uid": native_study_id, "managed_key": managed_key, "tenant_id": tenant_id,
                 "platform_study_id": platform_study_id, "fact_id": selection["factId"],
                 "revision": selection["revision"], "target_key": selection["targetKey"],
                 "action": action, "resource_family": candidate["resourceFamily"],
                 "payload_json": canonical_json(target_payload), "content_hash": target_hash["value"],
                 "actor": actor},
            )
            if not created or str(created[0][0]) != canonical_json(target_payload) or str(created[0][1]) != target_hash["value"]:
                raise OsbCandidateSetError("OSB_NATIVE_OPERATION_CONFLICT", f"Managed concept {key} differs.")
            observed_payload, observed_hash, post_version = target_payload, target_hash, str(created[0][2])
            managed_keys.append(managed_key)
            disposition = "governed_extension"
        else:
            observed_payload, observed_hash, post_version = None, None, None
            disposition = "deferred_blocking" if action == "defer" else "excluded_signed"
        evidence_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-native-evidence:v1:{operation_id}"))
        evidence = {
            "contractVersion": "NativeOperationEvidenceV1@1.0.0",
            "evidenceId": evidence_id, "decisionId": statement["decisionId"],
            "operationId": operation_id, "idempotencyKey": managed_key,
            "effectId": operation_id, "adapterVersion": f"platform-managed-{executor_kind}/1.0.0",
            "executorVersion": "osb-prototype/1.0.0", "executorKind": executor_kind,
            "expectedTargetPrecondition": {"nativeStudyId": native_study_id, "nativeVersion": native_version,
                                           "candidateSetVersionId": candidate_set_version_id},
            "sourceInputHash": source_hash, "nativeTargetIdentity": managed_key,
            "preTargetVersion": None, "postTargetVersion": post_version,
            "normalizedReadBack": observed_payload, "normalizedReadBackHash": observed_hash,
            "disposition": disposition, "operationTime": operation_time,
        }
        evidence_hash = canonical_json_hash_ref(evidence, schema_version="NativeOperationEvidenceV1@1.0.0")
        db.cypher_query(
            """CREATE (evidence:NativeOperationEvidenceV1 {evidence_id:$evidence_id,
                 tenant_id:$tenant_id,platform_study_id:$platform_study_id,
                 operation_id:$operation_id,decision_id:$decision_id,payload_hash:$payload_hash,
                 payload_json:$payload_json,created_at:datetime()})""",
            {"evidence_id": evidence_id, "tenant_id": tenant_id, "platform_study_id": platform_study_id,
             "operation_id": operation_id, "decision_id": statement["decisionId"],
             "payload_hash": evidence_hash["value"], "payload_json": canonical_json(evidence)},
        )
        evidence_records.append({"evidence": evidence, "payloadHash": evidence_hash})
    checkpoint = {"nativeStudyId": native_study_id, "nativeVersion": native_version,
                  "managedKeys": sorted(managed_keys), "operationCount": len(evidence_records)}
    checkpoint_hash = canonical_json_hash_ref(checkpoint, schema_version="OsbManagedTargetCheckpointV1@1.0.0")
    evidence_set_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-evidence-set:v1:{decision_hash}"))
    evidence_set_version_id = str(uuid5(NAMESPACE_URL, f"{evidence_set_id}:{checkpoint_hash['value']}"))
    result_payload = {"contractVersion": "OsbNativeEvidenceSetV1@1.0.0",
                      "evidenceSetId": evidence_set_id, "evidenceSetVersionId": evidence_set_version_id,
                      "tenantId": tenant_id, "platformStudyId": platform_study_id,
                      "decisionHash": decision_artifact["payloadHash"],
                      "candidateSetHash": statement["candidateSetHash"],
                      "nativeStudyIdentity": {"nativeIdentity": native_study_id, "nativeVersion": native_version},
                      "evidenceRecords": evidence_records, "managedTargetCheckpoint": checkpoint,
                      "managedTargetCheckpointHash": checkpoint_hash,
                      "createdAt": operation_time, "createdBy": actor}
    result_hash = canonical_json_hash_ref(result_payload, schema_version="OsbNativeEvidenceSetV1@1.0.0",
                                          media_type=EVIDENCE_SET_MEDIA_TYPE)
    artifact_ref = _artifact_ref({"artifactId": evidence_set_id, "artifactVersionId": evidence_set_version_id,
                                  "kind": "osb-native-evidence-set",
                                  "stableLocator": f"artifact://osb/native-evidence-set/{evidence_set_version_id}",
                                  "payloadHash": result_hash,
                                  "byteSize": len(canonical_json(result_payload).encode("utf-8")),
                                  "classification": "regulated-non-phi", "tenantId": tenant_id,
                                  "region": "us-central1", "producerService": "osb.clinical-mdr-api",
                                  "producerEnvironment": "prototype", "producerVersion": "prototype",
                                  "payloadContract": "accuratrials.osb.OsbNativeEvidenceSetV1",
                                  "payloadContractVersion": "1.0.0", "purpose": "transformation-verification",
                                  "createdAt": operation_time})
    db.cypher_query(
        """CREATE (decision:StudyMappingDecisionV1 {decision_id:$decision_id,tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,candidate_set_hash:$candidate_set_hash,
             decision_hash:$decision_hash,payload_json:$decision_json,created_at:datetime()})
           CREATE (evidence:OsbNativeEvidenceSetV1 {evidence_set_id:$evidence_set_id,
             evidence_set_version_id:$evidence_set_version_id,tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,decision_hash:$decision_hash,
             payload_hash:$payload_hash,payload_json:$payload_json,
             artifact_ref_json:$artifact_ref_json,created_at:datetime()})
           CREATE (evidence)-[:EXECUTED_DECISION]->(decision)""",
        {"decision_id": statement["decisionId"], "tenant_id": tenant_id,
         "platform_study_id": platform_study_id, "candidate_set_hash": candidate_set_hash,
         "decision_hash": decision_hash, "decision_json": canonical_json(payload),
         "evidence_set_id": evidence_set_id, "evidence_set_version_id": evidence_set_version_id,
         "payload_hash": result_hash["value"], "payload_json": canonical_json(result_payload),
         "artifact_ref_json": canonical_json(artifact_ref)},
    )
    return {"payload": result_payload, "payloadHash": result_hash, "artifactRef": artifact_ref,
            "evidenceSetId": evidence_set_id, "evidenceSetVersionId": evidence_set_version_id,
            "nativeIdentity": native_study_id, "nativeVersion": native_version, "replay": False}


def _key(value: dict[str, Any]) -> str:
    return f'{value.get("factId")}@{value.get("revision")}:{value.get("targetKey")}'


__all__ = [
    "CAPTURE_FAMILIES", "DECISION_MEDIA_TYPE", "EVIDENCE_SET_MEDIA_TYPE", "STUDY_FAMILIES",
    "apply_mapping_decision", "executor_kind_for_family", "store_mapping_decision_bytes",
]
