"""Prototype-complete OSB-native Package V2 release from exact governed artifacts."""

from __future__ import annotations

import json
import re
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
from clinical_mdr_api.services.integrations.native_package_state_v2 import (
    STATE_SCHEMA,
    load_checkpoint_native_state,
    require_native_lock,
    require_source_projection,
)

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


def _approval_schema_version(artifact: dict[str, Any]) -> str:
    version = artifact.get("payloadContractVersion")
    payload_hash = _record(artifact.get("payloadHash"), "OSB_PRE_RELEASE_APPROVAL_CONTRACT_VERSION_INVALID")
    if version not in ("1.0.0", "1.1.0") \
            or artifact.get("payloadContract") != "accuratrials.cc.PreReleaseApprovalV1" \
            or payload_hash.get("schemaVersion") != f"PreReleaseApprovalV1@{version}":
        raise OsbCandidateSetError("OSB_PRE_RELEASE_APPROVAL_CONTRACT_VERSION_INVALID",
                                   "Approval descriptor and payload hash must name the same supported version.", 422)
    return f"PreReleaseApprovalV1@{version}"


def _assert_approval_readiness(approval: dict[str, Any], schema_version: str) -> None:
    if approval.get("approval_version") != "PreReleaseApprovalV1":
        raise OsbCandidateSetError("OSB_PRE_RELEASE_APPROVAL_CONTRACT_VERSION_INVALID",
                                   "Approval contract differs.", 422)
    if schema_version == "PreReleaseApprovalV1@1.0.0" and "readiness_basis" not in approval:
        return
    code = "OSB_PRE_RELEASE_APPROVAL_READINESS_INVALID"
    basis = _record(approval.get("readiness_basis"), code)
    counts = _record(basis.get("counts"), code)
    gates = {"unaccountedClaims", "evidenceLessClaims", "unresolvedCritical", "unverifiedMappingDecisions"}
    basis_fields = {"cslStudyId", "ready", "lossless", "counts", "expectedMappingSetHash", "fetchedAt"}
    try:
        fetched_at = datetime.fromisoformat(basis["fetchedAt"].replace("Z", "+00:00").replace("z", "+00:00"))
        valid_time = fetched_at.tzinfo is not None and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
            basis["fetchedAt"], re.IGNORECASE) is not None
    except (KeyError, AttributeError, TypeError, ValueError):
        valid_time = False
    if basis.get("ready") is not True or basis.get("lossless") is not True \
            or not isinstance(basis.get("cslStudyId"), str) or not basis["cslStudyId"].strip() \
            or not isinstance(basis.get("expectedMappingSetHash"), str) \
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", basis["expectedMappingSetHash"]) \
            or not valid_time or type(counts.get("claims")) is not int or not 0 <= counts["claims"] <= 2**53 - 1 \
            or any(type(counts.get(field)) is not int or counts[field] != 0 for field in gates) \
            or set(counts) - (gates | {"claims"}) or set(basis) - basis_fields:
        raise OsbCandidateSetError(code, "Approval must retain its ready, lossless source basis.", 422)


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
    if len(rows) != 1:
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_NOT_TRANSFERRED", f"Exact {kind} bytes are unavailable.", 404)
    return _parse_canonical(str(rows[0][0]).encode("utf-8"), "OSB_RELEASE_ARTIFACT_STORED_INVALID")


def _verify_artifact_ref(
    payload: dict[str, Any], artifact: dict[str, Any], *, kind: str,
    tenant_id: str, platform_study_id: str, schema_version: str, media_type: str,
) -> None:
    if kind == "pre-release-approval-v1":
        if schema_version != _approval_schema_version(artifact):
            raise OsbCandidateSetError("OSB_PRE_RELEASE_APPROVAL_CONTRACT_VERSION_INVALID",
                                       "Approval verification uses a different contract version.", 422)
        _assert_approval_readiness(payload, schema_version)
    identity_fields = {
        "transformation-checkpoint": ("checkpointId", "checkpointVersionId"),
        "platform-manifest-v1": ("manifestId", "manifestVersionId"),
        # CommandCenter's artifact version is derived from the payload hash; the
        # approval payload contains its record ID, not an artifact version ID.
        "pre-release-approval-v1": ("approval_id", None),
        "osb-specialist-review-evidence": ("reviewId", "reviewVersionId"),
        "osb-native-package-v2": ("packageId", "packageVersionId"),
    }
    id_field, version_field = identity_fields[kind]
    expected_version = payload.get(version_field) if version_field else artifact.get("artifactVersionId")
    if artifact.get("contractVersion") != "ArtifactRefV1@1.0.0" \
            or artifact.get("kind") != kind or artifact.get("tenantId") != tenant_id \
            or _artifact_scope(payload, kind) != (tenant_id, platform_study_id) \
            or not payload.get(id_field) or not isinstance(expected_version, str) or not expected_version \
            or artifact.get("artifactId") != payload[id_field] \
            or artifact.get("artifactVersionId") != expected_version \
            or artifact.get("byteSize") != len(canonical_json(payload).encode("utf-8")):
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_REF_INVALID", f"{kind} artifact reference differs.", 422)
    expected = (raw_bytes_hash_ref(canonical_json(payload).encode("utf-8"), schema_version=schema_version, media_type=media_type)
                if kind == "osb-native-package-v2" else
                canonical_json_hash_ref(payload, schema_version=schema_version, media_type=media_type))
    if not hash_refs_equal(expected, artifact.get("payloadHash")):
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_HASH_MISMATCH", f"{kind} payload hash differs.", 422)
    fields = {key: value for key, value in artifact.items() if key not in {"contractVersion", "descriptorHash"}}
    actual_descriptor = descriptor_hash({"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields})
    if not hash_refs_equal(actual_descriptor, artifact.get("descriptorHash")):
        raise OsbCandidateSetError("OSB_RELEASE_ARTIFACT_DESCRIPTOR_MISMATCH", f"{kind} descriptor differs.", 422)


def _assert_state_unchanged(state: dict[str, Any], *, tenant_id: str, platform_study_id: str,
                            checkpoint: dict[str, Any]) -> None:
    current = load_checkpoint_native_state(tenant_id=tenant_id, platform_study_id=platform_study_id, checkpoint=checkpoint)
    if not hash_refs_equal(state["stateHash"], current["stateHash"]):
        raise OsbCandidateSetError("OSB_POST_REVIEW_NATIVE_EDIT", "Native content changed during review/package preparation.", 409)


def _package_content(state: dict[str, Any]) -> dict[str, Any]:
    """Pure assembly; callers must separately enforce review/release gates."""
    records = state["records"]
    families = sorted({item["resourceFamily"] for item in records})
    return {
        "studyDesign": {"root": state["root"],
                        "managedConcepts": [item for item in records if item["kind"] == "managed" and item["resourceFamily"] not in CAPTURE_FAMILIES],
                        "nativeRecords": [item for item in records if item["kind"] == "native" and item["resourceFamily"] not in CAPTURE_FAMILIES]},
        "captureDesign": {"managedConcepts": [item for item in records if item["kind"] == "managed" and item["resourceFamily"] in CAPTURE_FAMILIES],
                          "nativeRecords": [item for item in records if item["kind"] == "native" and item["resourceFamily"] in CAPTURE_FAMILIES]},
        "contentIndex": state["contentIndex"], "contentIndexHash": state["contentIndexHash"],
        "terminologyPins": families,
        "capabilityManifest": {"executionMode": "checkpoint-native-evidence/1.0.0",
                               "resourceFamilies": families,
                               "requestedObjectFamilies": state["request"].get("requestedObjectFamilies") or []},
    }


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
                         tenant_id=tenant_id, platform_study_id=platform_study_id, schema_version="TransformationCheckpointV1@1.0.0",
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
    state = load_checkpoint_native_state(tenant_id=tenant_id, platform_study_id=platform_study_id, checkpoint=checkpoint)
    require_source_projection(state)
    lock = require_native_lock(state)
    root, records = state["root"], state["records"]
    native_study_id = root["nativeStudyId"]
    expected_authority = canonical_json_hash_ref(managed, schema_version="OsbManagedTargetCheckpointV1@1.0.0")
    if not hash_refs_equal(expected_authority, authority.get("managedTargetCheckpointHash")):
        raise OsbCandidateSetError("OSB_CHECKPOINT_AUTHORITY_HASH_MISMATCH", "Managed checkpoint hash differs.", 422)
    reviewed_fields = {
        "specialistSubject": actor,
        "displayedStatement": str(input_payload.get("displayedStatement") or "I reviewed the exact OSB study and capture configuration."),
        "meaning": str(input_payload.get("meaning") or "approved for prototype package generation"),
        "reason": str(input_payload.get("reason") or "prototype specialist review completed"),
    }
    review_seed = canonical_json_hash_ref({"checkpointHash": checkpoint_artifact["payloadHash"],
                                           "authorityHash": expected_authority, "nativeStateHash": state["stateHash"],
                                           **reviewed_fields},
                                          schema_version="OsbSpecialistReviewSeedV1@1.0.0")
    review_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-specialist-review:v1:{review_seed['value']}"))
    review_version_id = str(uuid5(NAMESPACE_URL, f"{review_id}:{state['stateHash']['value']}"))
    reviewed_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    review = {"contractVersion": "OsbSpecialistReviewEvidenceV1@1.0.0",
              "reviewId": review_id, "reviewVersionId": review_version_id,
              "tenantId": tenant_id, "platformStudyId": platform_study_id,
              "osbStudyIdentity": {"nativeIdentity": native_study_id,
                                   "nativeVersion": root["nativeVersion"]},
              "transformationCheckpointHash": checkpoint_artifact["payloadHash"],
              "osbAuthorityHash": expected_authority,
              "managedConceptCount": sum(item["kind"] == "managed" for item in records),
              "nativeObjectCount": sum(item["kind"] == "native" for item in records),
              "nativeStateHash": state["stateHash"], "nativeLockEvidence": lock, **reviewed_fields,
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
        "MATCH (review:OsbSpecialistReviewEvidenceV1 {tenant_id:$tenant_id,platform_study_id:$platform_study_id,review_version_id:$version_id}) "
        "RETURN review.payload_json,review.artifact_ref_json",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "version_id": review_version_id},
    )
    _assert_state_unchanged(state, tenant_id=tenant_id, platform_study_id=platform_study_id, checkpoint=checkpoint)
    if prior:
        if len(prior) != 1:
            raise OsbCandidateSetError("OSB_SPECIALIST_REVIEW_REPLAY_CONFLICT", "Review identity is ambiguous.", 409)
        retained = _parse_canonical(str(prior[0][0]).encode("utf-8"), "OSB_SPECIALIST_REVIEW_STORED_INVALID")
        retained_artifact = _parse_canonical(str(prior[0][1]).encode("utf-8"), "OSB_SPECIALIST_REVIEW_STORED_INVALID")
        _verify_artifact_ref(retained, retained_artifact, kind="osb-specialist-review-evidence",
                             tenant_id=tenant_id, platform_study_id=platform_study_id,
                             schema_version="OsbSpecialistReviewEvidenceV1@1.0.0", media_type=SPECIALIST_REVIEW_MEDIA_TYPE)
        if canonical_json({key: value for key, value in retained.items() if key != "reviewedAt"}) != \
                canonical_json({key: value for key, value in review.items() if key != "reviewedAt"}):
            raise OsbCandidateSetError("OSB_SPECIALIST_REVIEW_REPLAY_CONFLICT", "Retained review differs.", 409)
        return {"payload": retained, "payloadHash": retained_artifact["payloadHash"],
                "artifactRef": retained_artifact, "replay": True}
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
    if len(rows) != 1:
        raise OsbCandidateSetError("OSB_SPECIALIST_REVIEW_NOT_FOUND", "Exact specialist review is unavailable.", 404)
    return _parse_canonical(str(rows[0][0]).encode("utf-8"), "OSB_SPECIALIST_REVIEW_STORED_INVALID")


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
                         platform_study_id=platform_study_id, schema_version="TransformationCheckpointV1@1.0.0", media_type=CHECKPOINT_MEDIA_TYPE)
    _verify_artifact_ref(manifest, manifest_artifact, kind="platform-manifest-v1", tenant_id=tenant_id,
                         platform_study_id=platform_study_id, schema_version="PlatformManifestV1@1.0.0", media_type=PLATFORM_MANIFEST_MEDIA_TYPE)
    _verify_artifact_ref(approval, approval_artifact, kind="pre-release-approval-v1", tenant_id=tenant_id,
                         platform_study_id=platform_study_id, schema_version=_approval_schema_version(approval_artifact),
                         media_type=PRE_RELEASE_APPROVAL_MEDIA_TYPE)
    _verify_artifact_ref(review, review_artifact, kind="osb-specialist-review-evidence", tenant_id=tenant_id,
                         platform_study_id=platform_study_id, schema_version="OsbSpecialistReviewEvidenceV1@1.0.0", media_type=SPECIALIST_REVIEW_MEDIA_TYPE)
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
    state = load_checkpoint_native_state(tenant_id=tenant_id, platform_study_id=platform_study_id, checkpoint=checkpoint)
    require_source_projection(state)
    lock = require_native_lock(state)
    if not hash_refs_equal(review.get("nativeStateHash"), state["stateHash"]) \
            or review.get("nativeLockEvidence") != lock \
            or review.get("osbStudyIdentity") != checkpoint.get("osbStudyIdentity"):
        raise OsbCandidateSetError("OSB_POST_REVIEW_NATIVE_EDIT", "Specialist review does not bind the current native content/lock.", 409)
    root, request = state["root"], state["request"]
    native_study_id = root["nativeStudyId"]
    source_fact = _record(request.get("sourceFactPackage"), "OSB_SOURCE_FACT_PACKAGE_REQUIRED")
    content = _package_content(state)
    content_index_hash = state["contentIndexHash"]
    seed = canonical_json_hash_ref({"checkpoint": checkpoint_artifact["payloadHash"],
                                    "manifest": manifest_artifact["payloadHash"],
                                    "approval": approval_artifact["payloadHash"],
                                    "review": review_artifact["payloadHash"],
                                    "contentIndex": content_index_hash, "nativeStateHash": state["stateHash"],
                                    "actor": actor},
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
                            "exclusionPolicy": checkpoint.get("exclusionPolicy"), "nativeState": STATE_SCHEMA},
               **content,
               "conservation": census,
               "provenancePins": {"candidateRequestId": request.get("requestId"),
                                  "candidateRequestHash": state["requestHash"],
                                  "candidateSetHash": state["candidateSetHash"], "decisionHash": state["decisionHash"],
                                  "nativeStateHash": state["stateHash"],
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
        "MATCH (package:OsbNativePackageV2 {tenant_id:$tenant_id,platform_study_id:$platform_study_id,package_version_id:$version_id}) "
        "RETURN package.payload_json,package.artifact_ref_json,package.package_version_id,package.payload_hash,package.byte_size",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "version_id": package_version_id},
    )
    _assert_state_unchanged(state, tenant_id=tenant_id, platform_study_id=platform_study_id, checkpoint=checkpoint)
    if prior:
        if len(prior) != 1:
            raise OsbCandidateSetError("OSB_PACKAGE_REPLAY_CONFLICT", "Package identity is ambiguous.", 409)
        retained_bytes = str(prior[0][0]).encode("utf-8")
        retained = _parse_canonical(retained_bytes, "OSB_PACKAGE_REPLAY_INVALID")
        retained_artifact = _parse_canonical(str(prior[0][1]).encode("utf-8"), "OSB_PACKAGE_REPLAY_INVALID")
        _verify_artifact_ref(retained, retained_artifact, kind="osb-native-package-v2",
                             tenant_id=tenant_id, platform_study_id=platform_study_id,
                             schema_version="OsbNativePackageV2@2.0.0", media_type=PACKAGE_V2_MEDIA_TYPE)
        if prior[0][2] != package_version_id or prior[0][3] != retained_artifact["payloadHash"]["value"] \
                or prior[0][4] != len(retained_bytes) \
                or canonical_json({key: value for key, value in retained.items() if key != "createdAt"}) != \
                   canonical_json({key: value for key, value in package.items() if key != "createdAt"}):
            raise OsbCandidateSetError("OSB_PACKAGE_REPLAY_CONFLICT", "Retained package content differs.", 409)
        return {"payload": retained, "bytes": retained_bytes,
                "payloadHash": retained_artifact["payloadHash"], "artifactRef": retained_artifact,
                "packageVersionId": package_version_id, "replay": True}
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
