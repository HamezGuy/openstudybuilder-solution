"""Prototype-complete OSB-native Package V2 release from exact governed artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    PACKAGE_V2_MEDIA_TYPE,
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
    hash_refs_equal,
    raw_bytes_hash_ref,
    sha256_bytes,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError

CHECKPOINT_MEDIA_TYPE = "application/json"
PLATFORM_MANIFEST_MEDIA_TYPE = "application/vnd.accuratrials.platform-manifest-v1+json"
PRE_RELEASE_APPROVAL_MEDIA_TYPE = "application/vnd.accuratrials.pre-release-approval-v1+json"
SPECIALIST_REVIEW_MEDIA_TYPE = "application/vnd.accuratrials.osb-specialist-review-evidence-v1+json"

INBOUND_RELEASE_CONTRACTS = {
    "transformation-checkpoint": ("TransformationCheckpointV1@1.0.0", CHECKPOINT_MEDIA_TYPE),
    "platform-manifest-v1": ("PlatformManifestV1@1.0.0", PLATFORM_MANIFEST_MEDIA_TYPE),
    "pre-release-approval-v1": ("PreReleaseApprovalV1", PRE_RELEASE_APPROVAL_MEDIA_TYPE),
}

CAPTURE_FAMILIES = {
    "odm_forms", "odm_item_groups", "odm_items", "odm_methods", "odm_conditions",
    "odm_aliases", "activity_schedules", "cdash_variables",
}


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
            raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_DUPLICATE_KEY", f"Duplicate JSON key {key}.", 422)
        result[key] = value
    return result


def _artifact_ref(fields: dict[str, Any]) -> dict[str, Any]:
    descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}
    return {"contractVersion": "ArtifactRefV1@1.0.0", **fields,
            "descriptorHash": descriptor_hash(descriptor)}


def _parse_canonical(bytes_value: bytes, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(bytes_value.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OsbCandidateSetError(code, "Artifact is not strict UTF-8 JSON.", 422) from error
    if not isinstance(payload, dict) or canonical_json(payload).encode("utf-8") != bytes_value:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_NONCANONICAL", "Artifact bytes are not canonical.", 422)
    return payload


def _artifact_scope(payload: dict[str, Any], kind: str) -> tuple[str, str]:
    if kind == "pre-release-approval-v1":
        human = _record(payload.get("human_signature"), "OSB_PRE_RELEASE_HUMAN_SIGNATURE_REQUIRED")
        return str(human.get("tenant_id") or ""), str(human.get("platform_study_id") or "")
    return str(payload.get("tenantId") or ""), str(payload.get("platformStudyId") or "")


def store_release_artifact_bytes(
    *, kind: str, tenant_id: str, platform_study_id: str,
    bytes_value: bytes, expected_hash: str,
) -> dict[str, Any]:
    contract = INBOUND_RELEASE_CONTRACTS.get(kind)
    if not contract:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_KIND_UNSUPPORTED", f"Unsupported artifact kind {kind}.", 422)
    if sha256_bytes(bytes_value) != expected_hash:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_TRANSFER_HASH_MISMATCH", "Artifact bytes differ.", 422)
    payload = _parse_canonical(bytes_value, "OSB_RELEASE_ARTIFACT_JSON_INVALID")
    if payload.get("contractVersion", payload.get("approval_version")) != contract[0]:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_CONTRACT_MISMATCH", "Artifact contract differs.", 422)
    if _artifact_scope(payload, kind) != (tenant_id, platform_study_id):
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_SCOPE_MISMATCH", "Artifact tenant/study scope differs.", 422)
    version_id = str(
        payload.get("checkpointVersionId")
        or payload.get("manifestVersionId")
        or payload.get("approval_id")
        or ""
    )
    if not version_id:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_VERSION_REQUIRED", "Artifact version identity is missing.", 422)
    rows, _ = db.cypher_query(
        """MERGE (artifact:OsbInboundArtifact {tenant_id:$tenant_id,payload_hash:$payload_hash})
           ON CREATE SET artifact.platform_study_id=$platform_study_id,
             artifact.artifact_version_id=$artifact_version_id,artifact.kind=$kind,
             artifact.payload_json=$payload_json,artifact.byte_size=$byte_size,
             artifact.media_type=$media_type,artifact.created_at=datetime()
           RETURN artifact.platform_study_id,artifact.artifact_version_id,artifact.kind,
                  artifact.payload_json,artifact.byte_size""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
         "payload_hash": expected_hash, "artifact_version_id": version_id, "kind": kind,
         "payload_json": canonical_json(payload), "byte_size": len(bytes_value), "media_type": contract[1]},
    )
    row = rows[0] if rows else None
    if not row or [str(row[0]), str(row[1]), str(row[2]), str(row[3]), int(row[4])] != [
        platform_study_id, version_id, kind, canonical_json(payload), len(bytes_value)
    ]:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_TRANSFER_CONFLICT", "Artifact identity names different bytes.", 409)
    return {"contractVersion": "ArtifactTransferReceiptV1@prototype", "kind": kind,
            "tenantId": tenant_id, "platformStudyId": platform_study_id,
            "artifactVersionId": version_id, "contentHash": expected_hash,
            "byteSize": len(bytes_value)}


def _load_inbound(tenant_id: str, platform_study_id: str, kind: str, payload_hash: str) -> dict[str, Any]:
    rows, _ = db.cypher_query(
        """MATCH (artifact:OsbInboundArtifact {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,kind:$kind,payload_hash:$payload_hash})
           RETURN artifact.payload_json""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
         "kind": kind, "payload_hash": payload_hash},
    )
    if not rows:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_NOT_TRANSFERRED", f"Exact {kind} bytes are unavailable.", 404)
    return _record(json.loads(str(rows[0][0])), "OSB_RELEASE_ARTIFACT_STORED_INVALID")


def _verify_artifact_ref(
    payload: dict[str, Any], artifact: dict[str, Any], *, kind: str,
    tenant_id: str, schema_version: str, media_type: str,
) -> None:
    if artifact.get("contractVersion") != "ArtifactRefV1@1.0.0" \
            or artifact.get("kind") != kind or artifact.get("tenantId") != tenant_id:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_REF_INVALID", f"{kind} artifact reference differs.", 422)
    expected = canonical_json_hash_ref(payload, schema_version=schema_version, media_type=media_type)
    if not hash_refs_equal(expected, artifact.get("payloadHash")):
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_HASH_MISMATCH", f"{kind} payload hash differs.", 422)
    fields = {key: value for key, value in artifact.items() if key not in {"contractVersion", "descriptorHash"}}
    actual_descriptor = descriptor_hash({"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields})
    if not hash_refs_equal(actual_descriptor, artifact.get("descriptorHash")):
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_DESCRIPTOR_MISMATCH", f"{kind} descriptor differs.", 422)


def _study_state(tenant_id: str, platform_study_id: str, native_study_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    roots, _ = db.cypher_query(
        """MATCH (scope:DomainStudyScope {tenant_id:$tenant_id,status:'active',study_uid:$study_uid})
           MATCH (study:StudyRoot {uid:$study_uid})
           OPTIONAL MATCH (study)-[version_rel:LATEST_DRAFT|LATEST|LATEST_LOCKED|LATEST_RELEASED]->(value:StudyValue)
           RETURN study.uid,type(version_rel),version_rel.version,version_rel.status,
                  value.study_title,value.study_number,value.study_acronym,value.project_number
           ORDER BY CASE type(version_rel) WHEN 'LATEST_RELEASED' THEN 0 WHEN 'LATEST_LOCKED' THEN 1
                    WHEN 'LATEST' THEN 2 WHEN 'LATEST_DRAFT' THEN 3 ELSE 4 END LIMIT 1""",
        {"tenant_id": tenant_id, "study_uid": native_study_id},
    )
    if not roots:
        raise OsbCandidateSetError("OSB_PACKAGE_NATIVE_STUDY_NOT_FOUND", "Bound OSB study root is unavailable.", 404)
    row = roots[0]
    root = {"nativeStudyId": str(row[0]), "relationship": str(row[1] or ""),
            "nativeVersion": str(row[2] or "unknown"), "nativeStatus": str(row[3] or "draft"),
            "title": str(row[4] or row[6] or row[5] or row[0]),
            "studyNumber": str(row[5] or ""), "acronym": str(row[6] or ""),
            "projectNumber": str(row[7] or "")}
    rows, _ = db.cypher_query(
        """MATCH (:StudyRoot {uid:$study_uid})-[:HAS_PLATFORM_MANAGED_CONCEPT]->(concept:PlatformManagedStudyConcept
             {tenant_id:$tenant_id,platform_study_id:$platform_study_id})
           RETURN concept.managed_key,concept.resource_family,concept.payload_json,
                  concept.content_hash,concept.version,concept.fact_id,concept.revision,concept.target_key
           ORDER BY concept.managed_key""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "study_uid": native_study_id},
    )
    concepts = [{"managedKey": str(item[0]), "resourceFamily": str(item[1]),
                 "payload": json.loads(str(item[2])), "contentHash": str(item[3]),
                 "nativeVersion": str(item[4]), "factId": str(item[5]),
                 "revision": int(item[6]), "targetKey": str(item[7])} for item in rows]
    return root, concepts


def _checkpoint_ref(input_payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    artifact = _record(input_payload.get("transformationCheckpointArtifact"), "OSB_TRANSFORMATION_CHECKPOINT_ARTIFACT_REQUIRED")
    payload_hash = _record(artifact.get("payloadHash"), "OSB_TRANSFORMATION_CHECKPOINT_HASH_REQUIRED").get("value")
    if not isinstance(payload_hash, str):
        raise OsbCandidateSetError("OSB_TRANSFORMATION_CHECKPOINT_HASH_REQUIRED", "Checkpoint hash is required.", 422)
    return artifact, payload_hash


def record_specialist_review(
    *, tenant_id: str, platform_study_id: str, input_payload: dict[str, Any], actor: str,
) -> dict[str, Any]:
    checkpoint_artifact, checkpoint_hash = _checkpoint_ref(input_payload)
    checkpoint = _load_inbound(tenant_id, platform_study_id, "transformation-checkpoint", checkpoint_hash)
    _verify_artifact_ref(checkpoint, checkpoint_artifact, kind="transformation-checkpoint",
                         tenant_id=tenant_id, schema_version="TransformationCheckpointV1@1.0.0",
                         media_type=CHECKPOINT_MEDIA_TYPE)
    blockers = _list(checkpoint.get("blockers"), "OSB_TRANSFORMATION_CHECKPOINT_BLOCKERS_REQUIRED")
    conservation = _record(checkpoint.get("conservation"), "OSB_TRANSFORMATION_CHECKPOINT_CENSUS_REQUIRED")
    counts = _record(conservation.get("counts"), "OSB_TRANSFORMATION_CHECKPOINT_COUNTS_REQUIRED")
    rows = _list(conservation.get("rows"), "OSB_TRANSFORMATION_CHECKPOINT_ROWS_REQUIRED")
    if blockers or counts.get("dropped") != 0 or any(
        _record(row, "OSB_TRANSFORMATION_CENSUS_ROW_INVALID").get("disposition")
        not in {"native", "governed_extension"} for row in rows
    ):
        raise OsbCandidateSetError("OSB_TRANSFORMATION_CHECKPOINT_RELEASE_BLOCKED", "Checkpoint is not zero-loss.", 409)
    authority = _record(checkpoint.get("osbAuthority"), "OSB_TRANSFORMATION_AUTHORITY_REQUIRED")
    managed = _record(authority.get("managedTargetCheckpoint"), "OSB_MANAGED_TARGET_CHECKPOINT_REQUIRED")
    native = _record(checkpoint.get("osbStudyIdentity"), "OSB_CHECKPOINT_NATIVE_IDENTITY_REQUIRED")
    native_study_id = str(native.get("nativeIdentity") or "")
    root, concepts = _study_state(tenant_id, platform_study_id, native_study_id)
    if sorted(item["managedKey"] for item in concepts) != sorted(_list(managed.get("managedKeys"), "OSB_MANAGED_KEYS_REQUIRED")):
        raise OsbCandidateSetError("OSB_POST_CHECKPOINT_NATIVE_EDIT", "Managed OSB target membership changed after checkpoint.", 409)
    expected_authority = canonical_json_hash_ref(managed, schema_version="OsbManagedTargetCheckpointV1@1.0.0")
    if not hash_refs_equal(expected_authority, authority.get("managedTargetCheckpointHash")):
        raise OsbCandidateSetError("OSB_CHECKPOINT_AUTHORITY_HASH_MISMATCH", "Managed checkpoint hash differs.", 422)
    review_seed = canonical_json_hash_ref({"checkpointHash": checkpoint_artifact["payloadHash"],
                                           "authorityHash": expected_authority, "actor": actor,
                                           "meaning": input_payload.get("meaning"),
                                           "reason": input_payload.get("reason")},
                                          schema_version="OsbSpecialistReviewSeedV1@1.0.0")
    review_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-specialist-review:v1:{review_seed['value']}"))
    review_version_id = str(uuid5(NAMESPACE_URL, f"{review_id}:{expected_authority['value']}"))
    reviewed_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    review = {"contractVersion": "OsbSpecialistReviewEvidenceV1@1.0.0",
              "reviewId": review_id, "reviewVersionId": review_version_id,
              "tenantId": tenant_id, "platformStudyId": platform_study_id,
              "osbStudyIdentity": {"nativeIdentity": native_study_id,
                                   "nativeVersion": root["nativeVersion"]},
              "transformationCheckpointHash": checkpoint_artifact["payloadHash"],
              "osbAuthorityHash": expected_authority,
              "managedConceptCount": len(concepts), "specialistSubject": actor,
              "displayedStatement": str(input_payload.get("displayedStatement") or "I reviewed the exact OSB study and capture configuration."),
              "meaning": str(input_payload.get("meaning") or "approved for prototype package generation"),
              "reason": str(input_payload.get("reason") or "prototype specialist review completed"),
              "lockState": "checkpoint-locked", "productionEligible": False,
              "reviewedAt": reviewed_at}
    review_hash = canonical_json_hash_ref(review, schema_version="OsbSpecialistReviewEvidenceV1@1.0.0",
                                          media_type=SPECIALIST_REVIEW_MEDIA_TYPE)
    artifact = _artifact_ref({"artifactId": review_id, "artifactVersionId": review_version_id,
                              "kind": "osb-specialist-review-evidence",
                              "stableLocator": f"artifact://osb/specialist-review/{review_version_id}",
                              "payloadHash": review_hash,
                              "byteSize": len(canonical_json(review).encode("utf-8")),
                              "classification": "regulated-non-phi", "tenantId": tenant_id,
                              "region": "us-central1", "producerService": "osb.clinical-mdr-api",
                              "producerEnvironment": "prototype", "producerVersion": "prototype",
                              "payloadContract": "accuratrials.osb.OsbSpecialistReviewEvidenceV1",
                              "payloadContractVersion": "1.0.0", "purpose": "osb-specialist-review-lock",
                              "createdAt": reviewed_at})
    prior, _ = db.cypher_query(
        "MATCH (review:OsbSpecialistReviewEvidenceV1 {tenant_id:$tenant_id,payload_hash:$payload_hash}) "
        "RETURN review.payload_json,review.artifact_ref_json",
        {"tenant_id": tenant_id, "payload_hash": review_hash["value"]},
    )
    if prior:
        return {"payload": json.loads(str(prior[0][0])), "payloadHash": review_hash,
                "artifactRef": json.loads(str(prior[0][1])), "replay": True}
    db.cypher_query(
        """CREATE (review:OsbSpecialistReviewEvidenceV1 {review_id:$review_id,
             review_version_id:$review_version_id,tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,payload_hash:$payload_hash,
             payload_json:$payload_json,artifact_ref_json:$artifact_ref_json,created_at:datetime()})""",
        {"review_id": review_id, "review_version_id": review_version_id, "tenant_id": tenant_id,
         "platform_study_id": platform_study_id, "payload_hash": review_hash["value"],
         "payload_json": canonical_json(review), "artifact_ref_json": canonical_json(artifact)},
    )
    return {"payload": review, "payloadHash": review_hash, "artifactRef": artifact, "replay": False}


def _load_review(tenant_id: str, platform_study_id: str, payload_hash: str) -> dict[str, Any]:
    rows, _ = db.cypher_query(
        """MATCH (review:OsbSpecialistReviewEvidenceV1 {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,payload_hash:$payload_hash})
           RETURN review.payload_json""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "payload_hash": payload_hash},
    )
    if not rows:
        raise OsbCandidateSetError("OSB_SPECIALIST_REVIEW_NOT_FOUND", "Exact specialist review is unavailable.", 404)
    return _record(json.loads(str(rows[0][0])), "OSB_SPECIALIST_REVIEW_STORED_INVALID")


def _candidate_request(tenant_id: str, platform_study_id: str, semantic_snapshot_hash: dict[str, Any]) -> dict[str, Any]:
    rows, _ = db.cypher_query(
        """MATCH (request:OsbCandidateRequestV1 {tenant_id:$tenant_id,platform_study_id:$platform_study_id})
           RETURN request.payload_json ORDER BY request.created_at DESC""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id},
    )
    for row in rows:
        request = _record(json.loads(str(row[0])), "OSB_CANDIDATE_REQUEST_STORED_INVALID")
        snapshot = _record(request.get("semanticSnapshot"), "OSB_CANDIDATE_REQUEST_SNAPSHOT_REQUIRED")
        if hash_refs_equal(snapshot.get("payloadHash"), semantic_snapshot_hash):
            return request
    raise OsbCandidateSetError("OSB_PACKAGE_CANDIDATE_REQUEST_NOT_FOUND", "Candidate request for checkpoint is unavailable.", 404)


def generate_native_package_v2(
    *, tenant_id: str, platform_study_id: str, input_payload: dict[str, Any], actor: str,
) -> dict[str, Any]:
    checkpoint_artifact, checkpoint_hash = _checkpoint_ref(input_payload)
    manifest_artifact = _record(input_payload.get("platformManifestArtifact"), "OSB_PLATFORM_MANIFEST_ARTIFACT_REQUIRED")
    approval_artifact = _record(input_payload.get("preReleaseApprovalArtifact"), "OSB_PRE_RELEASE_APPROVAL_ARTIFACT_REQUIRED")
    review_artifact = _record(input_payload.get("specialistReviewArtifact"), "OSB_SPECIALIST_REVIEW_ARTIFACT_REQUIRED")
    manifest_hash = str(_record(manifest_artifact.get("payloadHash"), "OSB_PLATFORM_MANIFEST_HASH_REQUIRED").get("value") or "")
    approval_hash = str(_record(approval_artifact.get("payloadHash"), "OSB_PRE_RELEASE_APPROVAL_HASH_REQUIRED").get("value") or "")
    review_hash = str(_record(review_artifact.get("payloadHash"), "OSB_SPECIALIST_REVIEW_HASH_REQUIRED").get("value") or "")
    checkpoint = _load_inbound(tenant_id, platform_study_id, "transformation-checkpoint", checkpoint_hash)
    manifest = _load_inbound(tenant_id, platform_study_id, "platform-manifest-v1", manifest_hash)
    approval = _load_inbound(tenant_id, platform_study_id, "pre-release-approval-v1", approval_hash)
    review = _load_review(tenant_id, platform_study_id, review_hash)
    _verify_artifact_ref(checkpoint, checkpoint_artifact, kind="transformation-checkpoint", tenant_id=tenant_id,
                         schema_version="TransformationCheckpointV1@1.0.0", media_type=CHECKPOINT_MEDIA_TYPE)
    _verify_artifact_ref(manifest, manifest_artifact, kind="platform-manifest-v1", tenant_id=tenant_id,
                         schema_version="PlatformManifestV1@1.0.0", media_type=PLATFORM_MANIFEST_MEDIA_TYPE)
    _verify_artifact_ref(approval, approval_artifact, kind="pre-release-approval-v1", tenant_id=tenant_id,
                         schema_version="PreReleaseApprovalV1@1.0.0", media_type=PRE_RELEASE_APPROVAL_MEDIA_TYPE)
    _verify_artifact_ref(review, review_artifact, kind="osb-specialist-review-evidence", tenant_id=tenant_id,
                         schema_version="OsbSpecialistReviewEvidenceV1@1.0.0", media_type=SPECIALIST_REVIEW_MEDIA_TYPE)
    authority = _record(checkpoint.get("osbAuthority"), "OSB_TRANSFORMATION_AUTHORITY_REQUIRED")
    authority_hash = _record(authority.get("managedTargetCheckpointHash"), "OSB_TRANSFORMATION_AUTHORITY_HASH_REQUIRED")
    if approval.get("transformation_checkpoint_hash") != checkpoint_hash \
            or approval.get("platform_manifest_hash") != manifest_hash \
            or approval.get("osb_authority_hash") != authority_hash.get("value") \
            or approval.get("specialist_review_evidence_hash") != review_hash:
        raise OsbCandidateSetError("OSB_PRE_RELEASE_APPROVAL_BINDING_MISMATCH", "Approval does not bind exact release prerequisites.", 409)
    if _record(review.get("transformationCheckpointHash"), "OSB_REVIEW_CHECKPOINT_HASH_REQUIRED").get("value") != checkpoint_hash \
            or not hash_refs_equal(review.get("osbAuthorityHash"), authority_hash) \
            or review.get("lockState") != "checkpoint-locked":
        raise OsbCandidateSetError("OSB_SPECIALIST_REVIEW_BINDING_MISMATCH", "Specialist review does not lock the checkpoint.", 409)
    blockers = _list(checkpoint.get("blockers"), "OSB_TRANSFORMATION_CHECKPOINT_BLOCKERS_REQUIRED")
    census = _record(checkpoint.get("conservation"), "OSB_TRANSFORMATION_CHECKPOINT_CENSUS_REQUIRED")
    counts = _record(census.get("counts"), "OSB_TRANSFORMATION_CHECKPOINT_COUNTS_REQUIRED")
    census_rows = _list(census.get("rows"), "OSB_TRANSFORMATION_CHECKPOINT_ROWS_REQUIRED")
    if blockers or counts.get("dropped") != 0 or any(
        _record(row, "OSB_TRANSFORMATION_CENSUS_ROW_INVALID").get("disposition")
        not in {"native", "governed_extension"} for row in census_rows
    ):
        raise OsbCandidateSetError("OSB_PACKAGE_ZERO_LOSS_REQUIRED", "Package release requires zero loss.", 409)
    native = _record(checkpoint.get("osbStudyIdentity"), "OSB_CHECKPOINT_NATIVE_IDENTITY_REQUIRED")
    native_study_id = str(native.get("nativeIdentity") or "")
    root, concepts = _study_state(tenant_id, platform_study_id, native_study_id)
    managed = _record(authority.get("managedTargetCheckpoint"), "OSB_MANAGED_TARGET_CHECKPOINT_REQUIRED")
    if sorted(item["managedKey"] for item in concepts) != sorted(_list(managed.get("managedKeys"), "OSB_MANAGED_KEYS_REQUIRED")):
        raise OsbCandidateSetError("OSB_POST_REVIEW_NATIVE_EDIT", "Managed target membership changed after specialist review.", 409)
    request = _candidate_request(tenant_id, platform_study_id,
                                 _record(checkpoint.get("semanticSnapshotHash"), "OSB_SEMANTIC_SNAPSHOT_HASH_REQUIRED"))
    source_fact = _record(request.get("sourceFactPackage"), "OSB_SOURCE_FACT_PACKAGE_REQUIRED")
    study_design = [item for item in concepts if item["resourceFamily"] not in CAPTURE_FAMILIES]
    capture_design = [item for item in concepts if item["resourceFamily"] in CAPTURE_FAMILIES]
    content_index = [{"managedKey": item["managedKey"], "contentHash": item["contentHash"],
                      "resourceFamily": item["resourceFamily"]} for item in concepts]
    content_index_hash = canonical_json_hash_ref(content_index, schema_version="OsbPackageContentIndexV1@1.0.0")
    seed = canonical_json_hash_ref({"checkpoint": checkpoint_artifact["payloadHash"],
                                    "manifest": manifest_artifact["payloadHash"],
                                    "approval": approval_artifact["payloadHash"],
                                    "review": review_artifact["payloadHash"],
                                    "contentIndex": content_index_hash},
                                   schema_version="OsbNativePackageV2Seed@1.0.0")
    package_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-native-package-v2:{seed['value']}"))
    package_version_id = str(uuid5(NAMESPACE_URL, f"{package_id}:{content_index_hash['value']}"))
    created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    package = {"contractVersion": "OsbNativePackageV2@2.0.0",
               "packageId": package_id, "packageVersionId": package_version_id,
               "tenantId": tenant_id, "platformStudyId": platform_study_id,
               "osbStudyIdentity": {"nativeIdentity": native_study_id,
                                    "nativeVersion": root["nativeVersion"]},
               "releasedVersion": root["nativeVersion"], "authorityHash": authority_hash,
               "sourceFactPackage": source_fact,
               "semanticSnapshotHash": checkpoint["semanticSnapshotHash"],
               "decisionSetHash": checkpoint["decisionSetHash"],
               "transformationCheckpointHash": checkpoint_artifact["payloadHash"],
               "specialistReviewLockReceipt": review_artifact,
               "preReleaseApproval": approval_artifact,
               "platformManifest": manifest_artifact,
               "profiles": {"projectionRuleset": request.get("projectionRuleset"),
                            "exclusionPolicy": checkpoint.get("exclusionPolicy")},
               "terminologyPins": sorted({str(item["resourceFamily"]) for item in concepts}),
               "capabilityManifest": {"executionMode": "platform-managed-concept/1.0.0",
                                      "resourceFamilies": sorted({str(item["resourceFamily"]) for item in concepts}),
                                      "requestedObjectFamilies": request.get("requestedObjectFamilies") or []},
               "studyDesign": {"root": root, "managedConcepts": study_design},
               "captureDesign": {"managedConcepts": capture_design},
               "contentIndex": content_index, "contentIndexHash": content_index_hash,
               "conservation": census,
               "provenancePins": {"candidateRequestId": request.get("requestId"),
                                  "sourceFactPackageHash": source_fact.get("payloadHash"),
                                  "nativeEvidenceSetHash": checkpoint.get("nativeEvidenceSetHash")},
               "productionEligible": False, "createdAt": created_at, "createdBy": actor}
    package_bytes = canonical_json(package).encode("utf-8")
    package_hash = raw_bytes_hash_ref(package_bytes, media_type=PACKAGE_V2_MEDIA_TYPE,
                                      schema_version="OsbNativePackageV2@2.0.0")
    artifact = _artifact_ref({"artifactId": package_id, "artifactVersionId": package_version_id,
                              "kind": "osb-native-package-v2",
                              "stableLocator": f"artifact://osb/native-package-v2/{package_version_id}",
                              "payloadHash": package_hash, "byteSize": len(package_bytes),
                              "classification": "regulated-non-phi", "tenantId": tenant_id,
                              "region": "us-central1", "producerService": "osb.clinical-mdr-api",
                              "producerEnvironment": "prototype", "producerVersion": "prototype",
                              "payloadContract": "accuratrials.osb.OsbNativePackageV2",
                              "payloadContractVersion": "2.0.0", "purpose": "edc-deployment",
                              "createdAt": created_at})
    prior, _ = db.cypher_query(
        "MATCH (package:OsbNativePackageV2 {tenant_id:$tenant_id,payload_hash:$payload_hash}) "
        "RETURN package.payload_json,package.artifact_ref_json,package.package_version_id",
        {"tenant_id": tenant_id, "payload_hash": package_hash["value"]},
    )
    if prior:
        return {"payload": json.loads(str(prior[0][0])), "bytes": str(prior[0][0]).encode("utf-8"),
                "payloadHash": package_hash, "artifactRef": json.loads(str(prior[0][1])),
                "packageVersionId": str(prior[0][2]), "replay": True}
    db.cypher_query(
        """CREATE (package:OsbNativePackageV2 {package_id:$package_id,
             package_version_id:$package_version_id,tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,payload_hash:$payload_hash,
             payload_json:$payload_json,byte_size:$byte_size,
             artifact_ref_json:$artifact_ref_json,created_at:datetime()})""",
        {"package_id": package_id, "package_version_id": package_version_id,
         "tenant_id": tenant_id, "platform_study_id": platform_study_id,
         "payload_hash": package_hash["value"], "payload_json": package_bytes.decode("utf-8"),
         "byte_size": len(package_bytes), "artifact_ref_json": canonical_json(artifact)},
    )
    return {"payload": package, "bytes": package_bytes, "payloadHash": package_hash,
            "artifactRef": artifact, "packageVersionId": package_version_id, "replay": False}


__all__ = ["PACKAGE_V2_MEDIA_TYPE", "PRE_RELEASE_APPROVAL_MEDIA_TYPE",
           "PLATFORM_MANIFEST_MEDIA_TYPE", "CHECKPOINT_MEDIA_TYPE", "SPECIALIST_REVIEW_MEDIA_TYPE",
           "generate_native_package_v2", "record_specialist_review", "store_release_artifact_bytes"]
