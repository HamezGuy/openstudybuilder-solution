"""Exact OsbCandidateRequestV1 intake and governed OsbCandidateSetV1 generation."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi.encoders import jsonable_encoder
from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
    hash_refs_equal,
    sha256_bytes,
)
from clinical_mdr_api.models.integrations.mapping_context import (
    MappingContextCandidateGroupRequest,
    MappingContextV2Request,
)
from clinical_mdr_api.services.integrations.mapping_context import MappingContextService

CANDIDATE_REQUEST_MEDIA_TYPE = (
    "application/vnd.accuratrials.osb-candidate-request-v1+json"
)
CANDIDATE_SET_MEDIA_TYPE = (
    "application/vnd.accuratrials.osb-candidate-set-v1+json"
)
SUPPORTED_RESOURCE_FAMILIES = frozenset({
    "activities", "compound_product_relationships", "controlled_terminology",
    "criteria_templates", "endpoint_templates", "objective_templates", "odm_forms",
    "odm_item_groups", "odm_items", "study_compound_dosing_relationships", "units",
})


class OsbCandidateSetError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _record(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OsbCandidateSetError(code, "Expected an object.", 422)
    return value


def _list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise OsbCandidateSetError(code, "Expected an array.", 422)
    return value


def _iso(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError) as error:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TIME_INVALID", "Invalid request time.", 422) from error


def _artifact_ref(descriptor_fields: dict[str, Any]) -> dict[str, Any]:
    descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0", **descriptor_fields}
    return {
        "contractVersion": "ArtifactRefV1@1.0.0",
        **descriptor_fields,
        "descriptorHash": descriptor_hash(descriptor),
    }


def _same(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def _fact_key(value: dict[str, Any], id_field: str) -> str:
    fact_id = value.get(id_field)
    revision = value.get("revision")
    if not isinstance(fact_id, str) or not fact_id.strip() \
            or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_MEMBER_INVALID", "Invalid source member identity.", 422)
    return f"{fact_id}@{revision}"


def _assert_signed_request(
    payload: dict[str, Any], artifact: dict[str, Any], signed_envelope: dict[str, Any] | None,
    signature_verification: dict[str, Any] | None,
) -> None:
    envelope = _record(signed_envelope, "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED")
    verification = _record(signature_verification, "OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_REQUIRED")
    descriptor = {
        "contractVersion": "ArtifactDescriptorV1@1.0.0",
        **{key: value for key, value in artifact.items() if key not in {"contractVersion", "descriptorHash"}},
    }
    statement = _record(envelope.get("signingStatement"), "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")
    if envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0" \
            or not _same(envelope.get("artifactDescriptor"), descriptor) \
            or not _same(envelope.get("payloadHash"), artifact.get("payloadHash")) \
            or statement.get("signingPurpose") != "osb-candidate-request" \
            or statement.get("producerService") != "csl.attestation" \
            or statement.get("payloadContract") != "accuratrials.osb.OsbCandidateRequestV1" \
            or statement.get("payloadContractVersion") != "1.0.0":
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID", "Signed request envelope differs.", 422)
    envelope_hash = canonical_json_hash_ref(
        envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"
    )
    if verification.get("verified") is not True \
            or not _same(verification.get("payloadHash"), artifact.get("payloadHash")) \
            or not _same(verification.get("envelopeHash"), envelope_hash) \
            or not isinstance(verification.get("signerKeyId"), str) \
            or not isinstance(verification.get("trustedTime"), str):
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_UNVERIFIED",
            "Candidate request does not have trusted signature verification.",
            422,
        )


def _assert_request_projection(payload: dict[str, Any], artifact: dict[str, Any]) -> None:
    source = _record(payload.get("sourceFactPackage"), "OSB_CANDIDATE_REQUEST_SOURCE_REQUIRED")
    snapshot = _record(payload.get("semanticSnapshot"), "OSB_CANDIDATE_REQUEST_SNAPSHOT_REQUIRED")
    identity = _record(payload.get("osbStudyIdentity"), "OSB_CANDIDATE_REQUEST_IDENTITY_REQUIRED")
    checkpoint = _record(payload.get("checkpointPreconditions"), "OSB_CANDIDATE_REQUEST_CHECKPOINT_REQUIRED")
    if identity.get("contractVersion") != "1.0.0" or identity.get("system") != "osb" \
            or identity.get("namespace") != "accuratrials-osb" \
            or identity.get("objectType") != "study-draft-root" \
            or identity.get("tenantId") != payload.get("tenantId") \
            or identity.get("platformStudyId") != payload.get("platformStudyId") \
            or identity.get("verificationStatus") != "verified" \
            or not all(isinstance(identity.get(name), str) and identity[name].strip()
                       for name in ("bindingId", "nativeIdentity", "nativeVersion")):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_IDENTITY_INVALID", "OSB identity binding is invalid.", 422)
    if not _same(checkpoint.get("semanticSnapshotHash"), snapshot.get("payloadHash")) \
            or checkpoint.get("osbNativeVersion") != identity.get("nativeVersion"):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CHECKPOINT_MISMATCH", "Request checkpoint differs.", 422)

    members = [_record(item, "OSB_CANDIDATE_REQUEST_MEMBER_INVALID") for item in
               _list(payload.get("activeClaimRevisions"), "OSB_CANDIDATE_REQUEST_MEMBERS_REQUIRED")]
    intents = [_record(item, "OSB_TYPED_SOURCE_INTENT_INVALID") for item in
               _list(payload.get("typedSourceIntents"), "OSB_TYPED_SOURCE_INTENTS_REQUIRED")]
    member_keys = [_fact_key(member, "sourceFactId") for member in members]
    intent_keys = [_fact_key(intent, "factId") for intent in intents]
    if len(set(member_keys)) != len(member_keys) or len(set(intent_keys)) != len(intent_keys):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_DUPLICATE_MEMBER", "Duplicate candidate request member.", 422)
    if member_keys != sorted(member_keys) or member_keys != intent_keys:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH", "Candidate work order differs from snapshot.", 422)
    expected_member_hash = canonical_json_hash_ref(
        members, schema_version="SemanticSnapshotMemberSetV1@1.0.0"
    )
    if not _same(expected_member_hash, snapshot.get("memberSetHash")):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_MEMBER_HASH_MISMATCH", "Snapshot member hash differs.", 422)

    families: list[str] = []
    for intent in intents:
        family = intent.get("resourceFamily")
        if family not in SUPPORTED_RESOURCE_FAMILIES:
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_FAMILY_UNSUPPORTED", "Unsupported OSB resource family.", 422)
        if intent.get("targetKey") != "primary" or any(
            key in intent for key in ("selectedCandidate", "nativeIdentity", "nativeVersion", "candidateId")
        ):
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TARGET_SELECTION_FORBIDDEN", "CSL may not select an OSB target.", 422)
        families.append(str(family))
    expected_families = sorted(set(families))
    if payload.get("requestedObjectFamilies") != expected_families:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_FAMILY_MISMATCH", "Requested families differ from intents.", 422)

    ruleset = _record(payload.get("projectionRuleset"), "OSB_CANDIDATE_REQUEST_RULESET_REQUIRED")
    expected_ruleset_hash = canonical_json_hash_ref(
        {"mapping": "fail-closed-family-router", "version": "1.0.0"},
        schema_version="ProjectionRulesetV1@1.0.0",
    )
    if ruleset.get("id") != "csl-to-osb-candidate-request" or ruleset.get("version") != "1.0.0" \
            or not _same(ruleset.get("hash"), expected_ruleset_hash):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_RULESET_UNSUPPORTED", "Projection ruleset is unsupported.", 422)

    request_id = payload.get("requestId")
    snapshot_version_id = snapshot.get("snapshotVersionId")
    if not isinstance(request_id, str) or not request_id.strip() \
            or not isinstance(snapshot_version_id, str) or not snapshot_version_id.strip():
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Candidate request census identity is missing.", 422)
    expected_rows = []
    for index, (member, intent) in enumerate(zip(members, intents, strict=True)):
        expected_rows.append({
            "unitId": f'{intent["factId"]}@{intent["revision"]}:primary',
            "source": {
                "artifactId": snapshot_version_id,
                "contract": "accuratrials.csl.SemanticSnapshotV1@1.0.0",
                "type": "active-claim-revision",
                "path": f"#/activeClaimRevisions/{index}",
                "valueHash": member.get("valueHash"),
            },
            "target": {
                "artifactId": request_id,
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
            "evidenceRefs": [f'source-fact:{intent["factId"]}@{intent["revision"]}'],
            "receiptRefs": [],
        })
    census = _record(payload.get("inputConservation"), "OSB_CANDIDATE_REQUEST_CENSUS_REQUIRED")
    expected_census_hash = canonical_json_hash_ref(
        expected_rows, schema_version="ConservationCensusRowsV1@1.0.0"
    )
    expected_counts = {
        "rows": len(members),
        "native": len(intents),
        "governedExtension": 0,
        "excludedSigned": 0,
        "deferredBlocking": 0,
        "quarantined": 0,
        "rejected": 0,
    }
    if census.get("contractVersion") != "ConservationCensusV1@1.0.0" \
            or not _same(census.get("rows"), expected_rows) \
            or not _same(census.get("rowSetHash"), expected_census_hash) \
            or census.get("counts") != expected_counts:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Candidate request census differs.", 422)

    evidence = _list(payload.get("evidenceArtifactRefs"), "OSB_CANDIDATE_REQUEST_EVIDENCE_REQUIRED")
    if len(evidence) != 1:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_EVIDENCE_MISMATCH", "Exactly one source artifact is required.", 422)
    source_artifact = _record(evidence[0], "OSB_CANDIDATE_REQUEST_EVIDENCE_MISMATCH")
    if source_artifact.get("artifactVersionId") != source.get("packageVersionId") \
            or not _same(source_artifact.get("payloadHash"), source.get("payloadHash")) \
            or source_artifact.get("tenantId") != payload.get("tenantId"):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_EVIDENCE_MISMATCH", "Source artifact reference differs.", 422)


def verify_candidate_request_artifact(
    payload: dict[str, Any],
    artifact: dict[str, Any],
    tenant_id: str,
    platform_study_id: str,
    signed_envelope: dict[str, Any] | None = None,
    signature_verification: dict[str, Any] | None = None,
) -> None:
    if (
        artifact.get("contractVersion") != "ArtifactRefV1@1.0.0"
        or artifact.get("kind") != "osb-candidate-request"
        or artifact.get("tenantId") != tenant_id
        or artifact.get("payloadContract") != "accuratrials.osb.OsbCandidateRequestV1"
        or artifact.get("payloadContractVersion") != "1.0.0"
        or artifact.get("producerService") != "csl.attestation"
        or artifact.get("purpose") != "osb-candidate-generation"
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_ARTIFACT_INVALID", "Candidate request artifact metadata differs.", 422)
    if (
        payload.get("contractVersion") != "OsbCandidateRequestV1@1.0.0"
        or payload.get("tenantId") != tenant_id
        or payload.get("platformStudyId") != platform_study_id
        or payload.get("requestVersionId") != artifact.get("artifactVersionId")
        or payload.get("requestId") != artifact.get("artifactId")
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_SCOPE_INVALID", "Candidate request scope or identity differs.", 422)
    actual_payload_hash = canonical_json_hash_ref(
        payload,
        schema_version="OsbCandidateRequestV1@1.0.0",
        media_type=CANDIDATE_REQUEST_MEDIA_TYPE,
    )
    if not hash_refs_equal(actual_payload_hash, artifact.get("payloadHash")):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_HASH_MISMATCH", "Candidate request hash differs.", 422)
    fields = {
        key: value
        for key, value in artifact.items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    expected_descriptor = descriptor_hash(
        {"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}
    )
    if not hash_refs_equal(expected_descriptor, artifact.get("descriptorHash")):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_DESCRIPTOR_MISMATCH", "Descriptor hash differs.", 422)
    payload_bytes = canonical_json(payload).encode("utf-8")
    if artifact.get("byteSize") != len(payload_bytes) \
            or artifact.get("stableLocator") != f'artifact://csl/osb-candidate-request/{payload.get("requestVersionId")}':
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_BYTES_MISMATCH", "Candidate request byte descriptor differs.", 422)
    created_at = _iso(payload.get("createdAt"))
    expires_at = _iso(payload.get("expiresAt"))
    if expires_at <= datetime.now(UTC):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_EXPIRED", "Candidate request expired.")
    if expires_at <= created_at or (expires_at - created_at).total_seconds() > 3600:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TIME_INVALID", "Candidate request time window is invalid.", 422)
    _assert_request_projection(payload, artifact)
    _assert_signed_request(payload, artifact, signed_envelope, signature_verification)


def assert_candidate_request_transfer_envelope(
    payload: dict[str, Any], expected_hash: str, signed_envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    envelope = _record(signed_envelope, "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED")
    descriptor = _record(envelope.get("artifactDescriptor"), "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")
    statement = _record(envelope.get("signingStatement"), "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")
    payload_hash = _record(descriptor.get("payloadHash"), "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")
    if (
        envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0"
        or envelope.get("signatureProfile") != "jws-detached-rfc7797/1.0"
        or descriptor.get("kind") != "osb-candidate-request"
        or descriptor.get("payloadContract") != "accuratrials.osb.OsbCandidateRequestV1"
        or descriptor.get("payloadContractVersion") != "1.0.0"
        or descriptor.get("producerService") != "csl.attestation"
        or descriptor.get("purpose") != "osb-candidate-generation"
        or descriptor.get("artifactId") != payload.get("requestId")
        or descriptor.get("artifactVersionId") != payload.get("requestVersionId")
        or descriptor.get("tenantId") != payload.get("tenantId")
        or payload_hash.get("value") != expected_hash
        or not _same(envelope.get("payloadHash"), descriptor.get("payloadHash"))
        or statement.get("signingPurpose") != "osb-candidate-request"
        or statement.get("producerService") != "csl.attestation"
        or statement.get("payloadContract") != "accuratrials.osb.OsbCandidateRequestV1"
        or not _same(statement.get("payloadHash"), descriptor.get("payloadHash"))
    ):
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID",
            "Transferred request is not bound by a matching signed envelope.",
            422,
        )
    return envelope


def decode_signed_artifact_envelope_header(value: str | None) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED",
            "Signed candidate-request envelope is required on transfer.",
            422,
        )
    try:
        decoded = json.loads(base64.b64decode(value, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID",
            "Signed envelope header is not valid base64 JSON.",
            422,
        ) from error
    return _record(decoded, "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")


def require_exactly_one_active_osb_binding(rows: list[Any]) -> dict[str, str]:
    if len(rows) != 1:
        raise OsbCandidateSetError("OSB_NATIVE_IDENTITY_BINDING_REQUIRED", "Exactly one active OSB binding is required.")
    return {
        "bindingId": str(rows[0][0]),
        "nativeIdentity": str(rows[0][1]),
        "nativeVersion": str(rows[0][2]),
    }


def store_candidate_request_bytes(
    *, tenant_id: str, platform_study_id: str, bytes_value: bytes, expected_hash: str,
    signed_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if sha256_bytes(bytes_value) != expected_hash:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TRANSFER_HASH_MISMATCH", "Transferred bytes differ.", 422)
    try:
        payload = json.loads(
            bytes_value.decode("utf-8"),
            object_pairs_hook=lambda pairs: _unique_object(pairs),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_JSON_INVALID", "Candidate request is not valid UTF-8 JSON.", 422) from error
    if not isinstance(payload, dict) or canonical_json(payload).encode("utf-8") != bytes_value:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_NONCANONICAL", "Candidate request bytes are not canonical.", 422)
    if (
        payload.get("contractVersion") != "OsbCandidateRequestV1@1.0.0"
        or payload.get("tenantId") != tenant_id
        or payload.get("platformStudyId") != platform_study_id
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TRANSFER_SCOPE_MISMATCH", "Transferred request scope differs.", 422)
    envelope = assert_candidate_request_transfer_envelope(payload, expected_hash, signed_envelope)
    envelope_json = canonical_json(envelope)
    rows, _ = db.cypher_query(
        """MERGE (artifact:OsbInboundArtifact {tenant_id: $tenant_id, payload_hash: $payload_hash})
           ON CREATE SET artifact.platform_study_id=$platform_study_id,
             artifact.artifact_version_id=$artifact_version_id,
             artifact.kind='osb-candidate-request', artifact.payload_json=$payload_json,
             artifact.byte_size=$byte_size, artifact.signed_envelope_json=$envelope_json,
             artifact.created_at=datetime()
           ON MATCH SET artifact.signed_envelope_json = CASE
             WHEN artifact.signed_envelope_json IS NULL THEN $envelope_json
             ELSE artifact.signed_envelope_json
           END
           RETURN artifact.platform_study_id,artifact.artifact_version_id,
                  artifact.payload_json,artifact.byte_size,artifact.signed_envelope_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "payload_hash": expected_hash,
            "artifact_version_id": payload["requestVersionId"],
            "payload_json": canonical_json(payload),
            "byte_size": len(bytes_value),
            "envelope_json": envelope_json,
        },
    )
    if not rows:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TRANSFER_FAILED", "Candidate request was not stored.", 500)
    row = rows[0]
    if (
        str(row[0]) != platform_study_id
        or str(row[1]) != payload["requestVersionId"]
        or str(row[2]) != canonical_json(payload)
        or int(row[3]) != len(bytes_value)
        or str(row[4]) != envelope_json
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TRANSFER_CONFLICT", "Content hash already names different bytes.")
    return {
        "contractVersion": "ArtifactTransferReceiptV1@prototype",
        "kind": "osb-candidate-request",
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "contentHash": expected_hash,
        "byteSize": len(bytes_value),
        "signedEnvelopeBound": True,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_DUPLICATE_KEY", f"Duplicate JSON key {key}.", 422)
        result[key] = value
    return result


def load_candidate_request(payload_hash: str, tenant_id: str, platform_study_id: str) -> dict[str, Any]:
    rows, _ = db.cypher_query(
        """MATCH (artifact:OsbInboundArtifact {tenant_id: $tenant_id,
             platform_study_id: $platform_study_id, payload_hash: $payload_hash,
             kind: 'osb-candidate-request'})
           RETURN artifact.payload_json""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "payload_hash": payload_hash},
    )
    if not rows:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_NOT_TRANSFERRED", "Exact candidate request bytes are unavailable.", 404)
    return _record(json.loads(str(rows[0][0])), "OSB_CANDIDATE_REQUEST_INVALID")


def _active_binding(tenant_id: str, platform_study_id: str) -> dict[str, str]:
    rows, _ = db.cypher_query(
        """MATCH (binding:PlatformNativeStudyBinding {tenant_id: $tenant_id,
             platform_study_id: $platform_study_id, namespace: 'accuratrials-osb',
             object_type: 'study-draft-root', status: 'active'})
           RETURN binding.binding_id,binding.native_study_id,binding.native_version""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id},
    )
    return require_exactly_one_active_osb_binding(rows)


def _census_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "rows": len(rows), "native": 0, "governedExtension": 0, "excludedSigned": 0,
        "deferredBlocking": 0, "quarantined": 0, "rejected": 0,
    }
    mapped = {
        "native": "native", "governed_extension": "governedExtension",
        "excluded_signed": "excludedSigned", "deferred_blocking": "deferredBlocking",
        "quarantined": "quarantined", "rejected": "rejected",
    }
    for row in rows:
        key = mapped.get(str(row.get("disposition") or ""))
        if key is None:
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_CENSUS_MISMATCH", "Census disposition is unsupported.", 422)
        counts[key] += 1
    return counts


def _census_row(
    *, unit_id: str, source_artifact_id: str, source_contract: str, source_type: str,
    source_path: str, source_hash: dict[str, Any], target_artifact_id: str,
    target_path: str, target_hash: dict[str, Any], index: int, disposition: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    return {
        "unitId": unit_id,
        "source": {
            "artifactId": source_artifact_id, "contract": source_contract, "type": source_type,
            "path": source_path, "valueHash": source_hash,
        },
        "target": {
            "artifactId": target_artifact_id, "contract": "accuratrials.osb.OsbCandidateSetV1",
            "type": "OsbCandidateRecordV1", "path": target_path, "valueHash": target_hash,
        },
        "multiplicity": {"source": 1, "target": 1},
        "splitMergeGroup": None, "splitMergeRule": None,
        "ordering": {"significant": True, "sourceIndex": index, "targetIndex": index},
        "disposition": disposition, "exclusionPolicy": None,
        "evidenceRefs": evidence_refs, "receiptRefs": [],
    }


def candidate_assignment_identity(
    *, tenant_id: str, platform_study_id: str, candidate_set_version_id: str,
) -> dict[str, str]:
    assignment_id = str(uuid5(
        NAMESPACE_URL,
        f"accuratrials:cc-candidate-assignment:v1:{tenant_id}:{platform_study_id}:{candidate_set_version_id}",
    ))
    return {
        "contractVersion": "CandidateAssignmentProjectionV1@1.0.0",
        "assignmentId": assignment_id,
        "kind": "mapping-adjudication",
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "candidateSetVersionId": candidate_set_version_id,
    }


def bind_candidate_set_envelope(artifact_ref: dict[str, Any]) -> dict[str, Any]:
    descriptor = {
        "contractVersion": "ArtifactDescriptorV1@1.0.0",
        **{key: value for key, value in artifact_ref.items() if key not in {"contractVersion", "descriptorHash"}},
    }
    if not hash_refs_equal(descriptor.get("payloadHash"), artifact_ref.get("payloadHash")):
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_SIGNATURE_INVALID", "Envelope payload hash differs.", 422)
    return {
        "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
        "artifactDescriptor": descriptor,
        "payloadHash": artifact_ref["payloadHash"],
        "signingStatement": {
            "signingPurpose": "osb-candidate-set",
            "producerService": "osb.package",
            "payloadContract": "accuratrials.osb.OsbCandidateSetV1",
            "payloadContractVersion": "1.0.0",
        },
    }


def assert_candidate_set_current(
    payload: dict[str, Any], *, binding: dict[str, str], osb_openapi_hash: str, now: datetime | None = None,
) -> None:
    identity = _record(payload.get("osbStudyIdentity"), "OSB_CANDIDATE_SET_IDENTITY_REQUIRED")
    checkpoint = _record(payload.get("capabilityCheckpoint"), "OSB_CANDIDATE_CHECKPOINT_REQUIRED")
    clock = now or datetime.now(UTC)
    if identity.get("nativeIdentity") != binding["nativeIdentity"] \
            or str(identity.get("nativeVersion") or "") != binding["nativeVersion"] \
            or identity.get("bindingId") != binding["bindingId"]:
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_STALE", "OSB binding or native version changed.")
    if str(checkpoint.get("nativeVersion") or "") != binding["nativeVersion"] \
            or str(checkpoint.get("osbOpenApiHash") or "") != osb_openapi_hash:
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_STALE", "Capability or native checkpoint changed.")
    if _iso(payload.get("expiresAt")) <= clock:
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_EXPIRED", "Candidate set expired.")


def _assert_readable_or_create(record: dict[str, Any]) -> str:
    native_candidates = _list(record.get("nativeCandidates"), "OSB_NATIVE_CANDIDATES_REQUIRED")
    create_option = record.get("createOption")
    create_allowed = isinstance(create_option, dict) and create_option.get("allowed") is True
    if native_candidates:
        for candidate in native_candidates:
            offered = _record(candidate, "OSB_NATIVE_CANDIDATE_INVALID")
            if not offered.get("uid") or not offered.get("version") or not offered.get("resourceType"):
                raise OsbCandidateSetError("OSB_CANDIDATE_SET_TARGET_UNREADABLE", "Native candidate identity is incomplete.", 422)
        return "native"
    if create_allowed:
        return "governed_extension"
    raise OsbCandidateSetError(
        "OSB_CANDIDATE_SET_TARGET_UNREADABLE",
        "Candidate must name a readable native target or an explicit create request.",
        422,
    )


def generate_candidate_set(
    *, request_payload: dict[str, Any], artifact: dict[str, Any], tenant_id: str,
    platform_study_id: str, osb_openapi_hash: str, actor: str,
    signed_envelope: dict[str, Any] | None = None,
    signature_verification: dict[str, Any] | None = None,
    mapping_context_service: Any | None = None,
    artifact_signer: Any | None = None,
) -> dict[str, Any]:
    verify_candidate_request_artifact(
        request_payload, artifact, tenant_id, platform_study_id,
        signed_envelope, signature_verification,
    )
    binding = _active_binding(tenant_id, platform_study_id)
    expected_identity = _record(request_payload.get("osbStudyIdentity"), "OSB_CANDIDATE_REQUEST_IDENTITY_REQUIRED")
    if (
        expected_identity.get("nativeIdentity") != binding["nativeIdentity"]
        or str(expected_identity.get("nativeVersion") or "") != binding["nativeVersion"]
        or expected_identity.get("bindingId") != binding["bindingId"]
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_NATIVE_PRECONDITION_FAILED", "OSB binding or version changed.")
    request_hash = _record(artifact.get("payloadHash"), "OSB_CANDIDATE_REQUEST_HASH_REQUIRED")["value"]
    db.cypher_query(
        """MERGE (lock:OsbCandidateRequestLock {key: $key})
           ON CREATE SET lock.created_at=datetime(),lock.revision=0
           SET lock.revision=lock.revision+1,lock.touched_at=datetime()
           RETURN lock.revision""",
        {"key": f"{tenant_id}|{request_hash}"},
    )
    prior, _ = db.cypher_query(
        """MATCH (candidate:OsbCandidateSetV1 {tenant_id: $tenant_id, request_hash: $request_hash})
           RETURN candidate.payload_json,candidate.payload_hash,candidate.artifact_ref_json,
                  candidate.candidate_set_id,candidate.candidate_set_version_id,
                  candidate.native_study_id,candidate.native_version,candidate.signed_envelope_json""",
        {"tenant_id": tenant_id, "request_hash": request_hash},
    )
    if prior:
        row = prior[0]
        prior_payload = _record(json.loads(str(row[0])), "OSB_CANDIDATE_SET_STORED_INVALID")
        expected_prior_hash = canonical_json_hash_ref(
            prior_payload, schema_version="OsbCandidateSetV1@1.0.0",
            media_type=CANDIDATE_SET_MEDIA_TYPE,
        )
        if expected_prior_hash["value"] != str(row[1]):
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_STORED_HASH_MISMATCH", "Stored candidate set is corrupt.", 500)
        assert_candidate_set_current(
            prior_payload, binding=binding, osb_openapi_hash=osb_openapi_hash,
        )
        envelope = json.loads(str(row[7])) if len(row) > 7 and row[7] else bind_candidate_set_envelope(
            json.loads(str(row[2]))
        )
        assignment = _record(prior_payload.get("assignment"), "OSB_CANDIDATE_SET_ASSIGNMENT_REQUIRED")
        return {
            "payload": prior_payload, "payloadHash": expected_prior_hash,
            "artifactRef": json.loads(str(row[2])), "candidateSetId": str(row[3]),
            "candidateSetVersionId": str(row[4]), "nativeIdentity": str(row[5]),
            "nativeVersion": str(row[6]), "assignment": assignment,
            "signedEnvelope": envelope, "replay": True,
        }
    intents = [_record(item, "OSB_TYPED_SOURCE_INTENT_INVALID") for item in _list(
        request_payload.get("typedSourceIntents"), "OSB_TYPED_SOURCE_INTENTS_REQUIRED"
    )]
    observed_keys: set[str] = set()
    groups: list[MappingContextCandidateGroupRequest] = []
    for index, intent in enumerate(intents):
        key = f'{intent.get("factId")}@{intent.get("revision")}:{intent.get("targetKey")}'
        if key in observed_keys:
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_DUPLICATE_MEMBER", "Duplicate candidate intent.", 422)
        observed_keys.add(key)
        family = str(intent.get("resourceFamily") or "")
        search_strings = [str(value) for value in _list(intent.get("searchStrings", []), "OSB_SEARCH_STRINGS_INVALID")]
        group: dict[str, Any] = {
            "fact_id": str(intent["factId"]),
            "concept_id": str(intent["conceptId"]),
            "target_key": str(intent["targetKey"]),
            "semantic_role": str(intent["semanticRole"]),
            "resource_family": family,
            "search_strings": search_strings,
            "search_codes": [str(value) for value in _list(intent.get("searchCodes", []), "OSB_SEARCH_CODES_INVALID")],
        }
        if family == "controlled_terminology":
            group["parent_resource_type"] = "CTCodelist"
            group["parent_search_strings"] = search_strings[:10] or [str(intent["semanticRole"])[:256]]
        groups.append(MappingContextCandidateGroupRequest(**group))
    context = (mapping_context_service or MappingContextService()).get_context_v2(
        MappingContextV2Request(
            study_uid=binding["nativeIdentity"],
            study_value_version=binding["nativeVersion"],
            candidate_groups=groups,
            maximum_candidates_per_group=10,
        ),
        osb_openapi_hash=osb_openapi_hash,
    )
    if len(context.candidate_groups) != len(intents):
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_MEMBER_MISMATCH", "Mapping context omitted or duplicated a source intent.", 422)
    context_value = _camelize(jsonable_encoder(context, by_alias=True, exclude_none=False))
    context_value["schemaVersion"] = "osb-mapping-context/2.0"
    context_value["mappingAuthority"] = "OpenStudyBuilder"
    context_value["contextHash"] = context.context_hash
    candidates: list[dict[str, Any]] = []
    census_rows: list[dict[str, Any]] = []
    request_id = str(request_payload["requestId"])
    for index, (intent, group) in enumerate(zip(intents, context.candidate_groups, strict=True)):
        if str(group.fact_id) != str(intent["factId"]) or str(group.target_key) != str(intent["targetKey"]):
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_MEMBER_MISMATCH", "Mapping context reordered source intents.", 422)
        candidate_records = _camelize(jsonable_encoder(group.candidates, by_alias=True, exclude_none=False))
        record = {
            "factId": intent["factId"], "revision": intent["revision"],
            "conceptId": intent["conceptId"], "targetKey": intent["targetKey"],
            "semanticRole": intent["semanticRole"], "resourceFamily": intent["resourceFamily"],
            "nativeCandidates": candidate_records,
            "createOption": intent.get("createOption") or None,
            "complete": group.complete, "truncated": group.truncated,
            "blockers": list(group.release_blockers),
        }
        disposition = _assert_readable_or_create(record)
        candidates.append(record)
        record_hash = canonical_json_hash_ref(record, schema_version="OsbCandidateRecordV1@1.0.0")
        census_rows.append(_census_row(
            unit_id=f'{intent["factId"]}@{intent["revision"]}:{intent["targetKey"]}',
            source_artifact_id=request_id,
            source_contract="accuratrials.osb.OsbCandidateRequestV1",
            source_type="OsbTypedSourceIntentV1",
            source_path=f"#/typedSourceIntents/{index}",
            source_hash=canonical_json_hash_ref(intent, schema_version="OsbTypedSourceIntentV1@1.0.0"),
            target_artifact_id="pending-candidate-set",
            target_path=f"#/candidateRecords/{index}",
            target_hash=record_hash,
            index=index,
            disposition=disposition,
            evidence_refs=[f"osb-context:{context.context_hash}"],
        ))
    candidate_set_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-candidate-set:v1:{request_hash}"))
    set_seed = canonical_json_hash_ref(
        {"requestHash": request_hash, "contextHash": context.context_hash, "candidates": candidates},
        schema_version="OsbCandidateSetSeedV1@1.0.0",
    )["value"]
    candidate_set_version_id = str(uuid5(NAMESPACE_URL, f"{candidate_set_id}:{set_seed}"))
    for row in census_rows:
        row["target"]["artifactId"] = candidate_set_id
    census_hash = canonical_json_hash_ref(census_rows, schema_version="ConservationCensusRowsV1@1.0.0")
    created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assignment = candidate_assignment_identity(
        tenant_id=tenant_id, platform_study_id=platform_study_id,
        candidate_set_version_id=candidate_set_version_id,
    )
    payload = {
        "contractVersion": "OsbCandidateSetV1@1.0.0",
        "candidateSetId": candidate_set_id,
        "candidateSetVersionId": candidate_set_version_id,
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "request": {"requestVersionId": request_payload["requestVersionId"], "payloadHash": artifact["payloadHash"]},
        "semanticSnapshot": request_payload["semanticSnapshot"],
        "sourceFactPackage": request_payload["sourceFactPackage"],
        "osbStudyIdentity": {**expected_identity, "bindingId": binding["bindingId"],
                             "nativeIdentity": binding["nativeIdentity"], "nativeVersion": binding["nativeVersion"]},
        "capabilityCheckpoint": {"osbOpenApiHash": osb_openapi_hash,
                                 "mappingContextHash": context.context_hash,
                                 "nativeVersion": binding["nativeVersion"], "governed": context.governed},
        "mappingContext": context_value,
        "candidateRecords": candidates,
        "conservation": {"contractVersion": "ConservationCensusV1@1.0.0", "rows": census_rows,
                         "rowSetHash": census_hash, "counts": _census_counts(census_rows)},
        "assignment": assignment,
        "blockers": list(context.release_blockers),
        "expiresAt": request_payload["expiresAt"],
        "createdAt": created_at,
        "createdBy": actor,
    }
    payload_hash = canonical_json_hash_ref(
        payload, schema_version="OsbCandidateSetV1@1.0.0", media_type=CANDIDATE_SET_MEDIA_TYPE
    )
    artifact_ref = _artifact_ref({
        "artifactId": candidate_set_id, "artifactVersionId": candidate_set_version_id,
        "kind": "osb-candidate-set", "stableLocator": f"artifact://osb/candidate-set/{candidate_set_version_id}",
        "payloadHash": payload_hash, "byteSize": len(canonical_json(payload).encode("utf-8")),
        "classification": "regulated-non-phi", "tenantId": tenant_id,
        "region": "us-central1", "producerService": "osb.package",
        "producerEnvironment": "prototype", "producerVersion": "prototype",
        "payloadContract": "accuratrials.osb.OsbCandidateSetV1", "payloadContractVersion": "1.0.0",
        "purpose": "mapping-adjudication", "createdAt": created_at,
    })
    envelope = (artifact_signer or bind_candidate_set_envelope)(artifact_ref)
    if not isinstance(envelope, dict) or envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0" \
            or not hash_refs_equal(envelope.get("payloadHash"), payload_hash) \
            or _record(envelope.get("signingStatement"), "OSB_CANDIDATE_SET_SIGNATURE_INVALID").get("signingPurpose") != "osb-candidate-set":
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_SIGNATURE_INVALID", "Candidate set envelope does not bind the payload.", 422)
    db.cypher_query(
        """MERGE (request:OsbCandidateRequestV1 {tenant_id: $tenant_id, request_hash: $request_hash})
           ON CREATE SET request.request_version_id=$request_version_id, request.request_id=$request_id,
             request.platform_study_id=$platform_study_id, request.payload_json=$request_json,
             request.received_at=datetime()
           MERGE (candidate:OsbCandidateSetV1 {tenant_id: $tenant_id, request_hash: $request_hash})
           ON CREATE SET candidate.candidate_set_version_id=$set_version_id,
             candidate.candidate_set_id=$set_id, candidate.platform_study_id=$platform_study_id,
             candidate.payload_hash=$payload_hash, candidate.context_hash=$context_hash,
             candidate.native_study_id=$native_study_id, candidate.native_version=$native_version,
             candidate.payload_json=$payload_json, candidate.artifact_ref_json=$artifact_ref_json,
             candidate.assignment_id=$assignment_id, candidate.signed_envelope_json=$signed_envelope_json,
             candidate.osb_openapi_hash=$osb_openapi_hash, candidate.created_at=datetime()
           MERGE (candidate)-[:GENERATED_FROM]->(request)
           RETURN candidate.candidate_set_version_id, candidate.payload_hash, candidate.assignment_id""",
        {"request_version_id": request_payload["requestVersionId"], "request_id": request_payload["requestId"],
         "tenant_id": tenant_id, "platform_study_id": platform_study_id, "request_hash": request_hash,
         "request_json": canonical_json(request_payload), "set_version_id": candidate_set_version_id,
         "set_id": candidate_set_id, "payload_hash": payload_hash["value"], "context_hash": context.context_hash,
         "native_study_id": binding["nativeIdentity"], "native_version": binding["nativeVersion"],
         "payload_json": canonical_json(payload), "artifact_ref_json": canonical_json(artifact_ref),
         "assignment_id": assignment["assignmentId"], "signed_envelope_json": canonical_json(envelope),
         "osb_openapi_hash": osb_openapi_hash},
    )
    return {"payload": payload, "payloadHash": payload_hash, "artifactRef": artifact_ref,
            "candidateSetId": candidate_set_id, "candidateSetVersionId": candidate_set_version_id,
            "nativeIdentity": binding["nativeIdentity"], "nativeVersion": binding["nativeVersion"],
            "assignment": assignment, "signedEnvelope": envelope}


def _camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, item in value.items():
            parts = str(key).split("_")
            camel = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:] if part)
            rendered[camel] = _camelize(item)
        return rendered
    return value


__all__ = [
    "CANDIDATE_REQUEST_MEDIA_TYPE", "CANDIDATE_SET_MEDIA_TYPE", "OsbCandidateSetError",
    "assert_candidate_request_transfer_envelope", "assert_candidate_set_current",
    "bind_candidate_set_envelope", "candidate_assignment_identity",
    "decode_signed_artifact_envelope_header",
    "generate_candidate_set", "load_candidate_request", "require_exactly_one_active_osb_binding",
    "store_candidate_request_bytes", "verify_candidate_request_artifact",
]
