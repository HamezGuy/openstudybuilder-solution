"""Machine-authored native drafts from retained, signed CSL source artifacts.

Staging is separate from a mapping decision, transformation checkpoint and
clinical release. Its receipt always records clinicalApproval=false and
releaseEligible=false, including when every native field can be populated.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from neomodel import db
from pydantic import ValidationError

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json, canonical_json_hash_ref, descriptor_hash, hash_refs_equal, sha256_bytes,
)
from clinical_mdr_api.services.integrations.candidate_set import (
    OsbCandidateSetError, _artifact_ref, _assert_identity_binding,
    active_osb_binding, verify_candidate_request_artifact,
)
from clinical_mdr_api.services.integrations.native_capture_mapping import (
    NativeCapturePort, apply_native_capture_selections, plan_native_capture,
)
from clinical_mdr_api.services.integrations.native_capture_projection import (
    CAPTURE_FAMILY_TYPES, CAPTURE_READBACK_SCHEMA, capture_field_receipts,
)
from clinical_mdr_api.services.integrations.study_metadata_mapping import (
    NativeStudyMetadataPort, apply_metadata_selections, compose_metadata_values, prepare_metadata_offers,
)

STAGE_CONTRACT = "SourceDraftStageV1@1.0.0"
STAGE_MEDIA_TYPE = "application/vnd.accuratrials.source-draft-stage-v1+json"
RECEIPT_CONTRACT = "OsbSourceDraftStageReceiptV1@1.0.0"
RECEIPT_MEDIA_TYPE = "application/vnd.accuratrials.osb-source-draft-stage-receipt-v1+json"
MAX_STAGE_BYTES = 128 * 1024 * 1024
_STAGE_FIELDS = {
    "contractVersion", "stageId", "stageVersionId", "tenantId", "platformStudyId",
    "candidateRequestArtifact", "semanticSnapshotHash", "sourceFactPackageHash",
    "osbStudyIdentity", "selectedSourceKeys", "createdAt", "expiresAt",
    "createdBy", "clinicalApproval", "purpose",
}
_ARTIFACT_FIELDS = {
    "contractVersion", "artifactId", "artifactVersionId", "kind", "stableLocator",
    "payloadHash", "descriptorHash", "byteSize", "classification", "tenantId",
    "region", "producerService", "producerEnvironment", "producerVersion",
    "payloadContract", "payloadContractVersion", "purpose", "createdAt",
}


def _require(value, code="OSB_SOURCE_DRAFT_STAGE_INVALID", status=422):
    if not value:
        raise OsbCandidateSetError(code, "Source draft scope, content or custody differs.", status)


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _key(intent):
    return f'{intent["factId"]}@{intent["revision"]}:{intent["targetKey"]}'


def _time(value, *, canonical=True):
    _require(isinstance(value, str), "OSB_SOURCE_DRAFT_STAGE_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OsbCandidateSetError("OSB_SOURCE_DRAFT_STAGE_TIME_INVALID", "Invalid stage timestamp.", 422) from error
    _require(parsed.tzinfo is not None and (not canonical or parsed.astimezone(UTC).isoformat(
        timespec="milliseconds").replace("+00:00", "Z") == value), "OSB_SOURCE_DRAFT_STAGE_TIME_INVALID")
    return parsed.astimezone(UTC)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "OSB_SOURCE_DRAFT_STAGE_DUPLICATE_KEY")
        result[key] = value
    return result


def _json_bytes(value):
    try:
        payload = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_object)
        _require(isinstance(payload, dict) and canonical_json(payload).encode("utf-8") == value,
                 "OSB_SOURCE_DRAFT_STAGE_NONCANONICAL")
        return payload
    except (UnicodeError, ValueError) as error:
        raise OsbCandidateSetError("OSB_SOURCE_DRAFT_STAGE_JSON_INVALID", "Canonical UTF-8 JSON is required.", 422) from error


def _hash(value, schema=STAGE_CONTRACT, media=STAGE_MEDIA_TYPE):
    return canonical_json_hash_ref(value, schema_version=schema, media_type=media)


def _descriptor(artifact):
    _require(isinstance(artifact, dict) and set(artifact) == _ARTIFACT_FIELDS,
             "OSB_SOURCE_DRAFT_STAGE_ARTIFACT_INVALID")
    descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0", **{
        key: value for key, value in artifact.items() if key not in {"contractVersion", "descriptorHash"}}}
    _require(artifact.get("contractVersion") == "ArtifactRefV1@1.0.0"
             and hash_refs_equal(descriptor_hash(descriptor), artifact.get("descriptorHash")),
             "OSB_SOURCE_DRAFT_STAGE_DESCRIPTOR_MISMATCH")
    return descriptor


def _validate_stage(payload, artifact, tenant_id, platform_study_id, *, now=None):
    _require(isinstance(payload, dict) and set(payload) == _STAGE_FIELDS)
    _descriptor(artifact)
    for key in ("stageId", "stageVersionId", "tenantId", "platformStudyId"):
        _require(isinstance(payload[key], str) and bool(re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", payload[key])))
    creator = payload["createdBy"]
    _require(isinstance(creator, dict) and set(creator) == {"actorType", "issuerQualifiedSubject"}
             and creator["actorType"] in {"service", "ai-assistant"}
             and isinstance(creator["issuerQualifiedSubject"], str)
             and creator["issuerQualifiedSubject"].strip() == creator["issuerQualifiedSubject"]
             and bool(creator["issuerQualifiedSubject"]), "OSB_SOURCE_DRAFT_STAGE_MACHINE_PRINCIPAL_REQUIRED")
    _require(payload["contractVersion"] == STAGE_CONTRACT and payload["tenantId"] == tenant_id
             and payload["platformStudyId"] == platform_study_id
             and payload["clinicalApproval"] is False and payload["purpose"] == "source-draft-review",
             "OSB_SOURCE_DRAFT_STAGE_SCOPE_INVALID")
    selected = payload["selectedSourceKeys"]
    _require(isinstance(selected, list) and 0 < len(selected) <= 1_000_000
             and all(isinstance(key, str) and key and key == key.strip() and len(key) <= 4096 for key in selected)
             and len(set(selected)) == len(selected), "OSB_SOURCE_DRAFT_STAGE_SELECTION_INVALID")
    created, expires = _time(payload["createdAt"]), _time(payload["expiresAt"])
    clock = now or datetime.now(UTC)
    _require(created <= clock and expires > clock and 0 < (expires - created).total_seconds() <= 3600,
             "OSB_SOURCE_DRAFT_STAGE_EXPIRED", 409)
    _require(artifact["artifactId"] == payload["stageId"]
             and artifact["artifactVersionId"] == payload["stageVersionId"]
             and artifact["kind"] == "source-draft-stage" and artifact["tenantId"] == tenant_id
             and artifact["producerService"] == "csl.semantic-api"
             and artifact["payloadContract"] == "accuratrials.csl.SourceDraftStageV1"
             and artifact["payloadContractVersion"] == "1.0.0"
             and artifact["purpose"] == "source-draft-review"
             and artifact["classification"] == "regulated-non-phi"
             and artifact["stableLocator"] == f'artifact://csl/source-draft-stage/{payload["stageVersionId"]}'
             and artifact["createdAt"] == payload["createdAt"]
             and type(artifact["byteSize"]) is int
             and artifact["byteSize"] == len(canonical_json(payload).encode("utf-8"))
             and hash_refs_equal(artifact["payloadHash"], _hash(payload)),
             "OSB_SOURCE_DRAFT_STAGE_ARTIFACT_MISMATCH")


def verify_stage_signature(payload, artifact, envelope, verification):
    """Exact machine-purpose envelope; generic verified=true is insufficient."""
    _require(isinstance(envelope, dict) and isinstance(verification, dict),
             "OSB_SOURCE_DRAFT_STAGE_SIGNATURE_REQUIRED")
    statement = envelope.get("signingStatement")
    _require(isinstance(statement, dict)
             and envelope.get("contractVersion") == "SignedArtifactEnvelopeV1@1.0.0"
             and _same(envelope.get("artifactDescriptor"), _descriptor(artifact))
             and _same(envelope.get("payloadHash"), artifact["payloadHash"])
             and statement.get("signingPurpose") == "source-draft-stage"
             and statement.get("producerService") == "csl.semantic-api"
             and statement.get("payloadContract") == "accuratrials.csl.SourceDraftStageV1"
             and statement.get("payloadContractVersion") == "1.0.0"
             and verification.get("verified") is True
             and _same(verification.get("payloadHash"), _hash(payload))
             and _same(verification.get("envelopeHash"), _hash(
                 envelope, "SignedArtifactEnvelopeV1@1.0.0", "application/json"))
             and isinstance(verification.get("signerKeyId"), str)
             and bool(verification["signerKeyId"])
             and isinstance(verification.get("trustedTime"), str),
             "OSB_SOURCE_DRAFT_STAGE_SIGNATURE_UNVERIFIED")


def verify_stage_request(payload, artifact, request, request_envelope, request_verification,
                         envelope, verification, tenant_id, platform_study_id, *, now=None):
    _validate_stage(payload, artifact, tenant_id, platform_study_id, now=now)
    verify_stage_signature(payload, artifact, envelope, verification)
    request_artifact = payload["candidateRequestArtifact"]
    verify_candidate_request_artifact(request, request_artifact, tenant_id, platform_study_id,
                                      request_envelope, request_verification)
    _require(_same(payload["semanticSnapshotHash"], request["semanticSnapshot"]["payloadHash"])
             and _same(payload["sourceFactPackageHash"], request["sourceFactPackage"]["payloadHash"])
             and _same(payload["osbStudyIdentity"], request["osbStudyIdentity"])
             and _time(payload["expiresAt"]) <= _time(request["expiresAt"], canonical=False)
             and _time(payload["createdAt"]) >= _time(request["createdAt"], canonical=False),
             "OSB_SOURCE_DRAFT_STAGE_SOURCE_CHANGED", 409)
    intents = request["typedSourceIntents"]
    by_key = {_key(intent): intent for intent in intents}
    _require(len(by_key) == len(intents)
             and all(key in by_key for key in payload["selectedSourceKeys"]),
             "OSB_SOURCE_DRAFT_STAGE_SELECTION_INVALID")
    return [deepcopy(by_key[key]) for key in payload["selectedSourceKeys"]]


class NativeSourceDraftStageStore:
    def load(self, tenant_id, study_id, payload_hash, kind):
        rows, _ = db.cypher_query(
            """MATCH (artifact:OsbInboundArtifact {tenant_id:$tenant,
                 platform_study_id:$study,payload_hash:$hash,kind:$kind})
               RETURN artifact.payload_json,artifact.signed_envelope_json LIMIT 2""",
            {"tenant": tenant_id, "study": study_id, "hash": payload_hash, "kind": kind})
        _require(len(rows) == 1, "OSB_SOURCE_DRAFT_STAGE_SOURCE_NOT_TRANSFERRED", 404)
        payload = _json_bytes(rows[0][0].encode("utf-8"))
        _require(sha256_bytes(rows[0][0].encode("utf-8")) == payload_hash,
                 "OSB_SOURCE_DRAFT_STAGE_CUSTODY_MISMATCH")
        return payload, _json_bytes(rows[0][1].encode("utf-8"))

    def store(self, payload, envelope, payload_bytes, expected_hash):
        rows, _ = db.cypher_query(
            """MERGE (artifact:OsbInboundArtifact {tenant_id:$tenant,
                 platform_study_id:$study,payload_hash:$hash,kind:'source-draft-stage'})
               ON CREATE SET artifact.artifact_version_id=$version,
                 artifact.payload_json=$payload,artifact.signed_envelope_json=$envelope,
                 artifact.byte_size=$size,artifact.created_at=datetime()
               RETURN artifact.artifact_version_id,artifact.payload_json,
                 artifact.signed_envelope_json,artifact.byte_size""",
            {"tenant": payload["tenantId"], "study": payload["platformStudyId"], "hash": expected_hash,
             "version": payload["stageVersionId"], "payload": payload_bytes.decode("utf-8"),
             "envelope": canonical_json(envelope), "size": len(payload_bytes)})
        _require(len(rows) == 1 and rows[0] == [
            payload["stageVersionId"], payload_bytes.decode("utf-8"), canonical_json(envelope), len(payload_bytes)],
            "OSB_SOURCE_DRAFT_STAGE_TRANSFER_CONFLICT", 409)

    def lock_scope(self, stage):
        _require(db._active_transaction is not None, "OSB_SOURCE_DRAFT_STAGE_TRANSACTION_REQUIRED", 500)
        identity = stage["osbStudyIdentity"]
        NativeCapturePort().lock_study(stage["tenantId"], stage["platformStudyId"], identity["nativeIdentity"])
        binding = active_osb_binding(stage["tenantId"], stage["platformStudyId"])
        _assert_identity_binding(identity, binding, tenant_id=stage["tenantId"],
                                 platform_study_id=stage["platformStudyId"],
                                 error_code="OSB_SOURCE_DRAFT_STAGE_BINDING_CHANGED")
        # Resolve the actual native head, not just the retained external binding.
        from clinical_mdr_api.services.integrations.native_package_state_v2 import _current_study

        native, _ = _current_study(stage["tenantId"], stage["platformStudyId"], identity)
        _require(native["nativeStatus"].upper() == "DRAFT", "OSB_SOURCE_DRAFT_STAGE_DRAFT_REQUIRED", 409)
        return native

    def receipt(self, tenant_id, study_id, stage_version=None, receipt_version=None):
        rows, _ = db.cypher_query(
            """MATCH (receipt:OsbSourceDraftStageReceiptV1 {tenant_id:$tenant,platform_study_id:$study})
               WHERE ($stage_version IS NOT NULL AND receipt.stage_version_id=$stage_version)
                  OR ($receipt_version IS NOT NULL AND receipt.receipt_version_id=$receipt_version)
               RETURN receipt.payload_json,receipt.artifact_ref_json LIMIT 2""",
            {"tenant": tenant_id, "study": study_id, "stage_version": stage_version, "receipt_version": receipt_version})
        _require(len(rows) <= 1, "OSB_SOURCE_DRAFT_STAGE_RECEIPT_AMBIGUOUS", 409)
        if not rows:
            return None
        payload, artifact = _json_bytes(rows[0][0].encode("utf-8")), _json_bytes(rows[0][1].encode("utf-8"))
        _descriptor(artifact)
        _require(hash_refs_equal(artifact["payloadHash"], _hash(payload, RECEIPT_CONTRACT, RECEIPT_MEDIA_TYPE))
                 and artifact["byteSize"] == len(rows[0][0].encode("utf-8"))
                 and payload["tenantId"] == tenant_id and payload["platformStudyId"] == study_id
                 and payload["clinicalApproval"] is False and payload["releaseEligible"] is False,
                 "OSB_SOURCE_DRAFT_STAGE_CUSTODY_MISMATCH")
        return {"payload": payload, "artifactRef": artifact}

    def save_receipt(self, payload, artifact):
        db.cypher_query(
            """CREATE (receipt:OsbSourceDraftStageReceiptV1 {tenant_id:$tenant,
                 platform_study_id:$study,stage_version_id:$stage_version,
                 receipt_id:$id,receipt_version_id:$version,payload_json:$payload,
                 artifact_ref_json:$artifact,payload_hash:$hash,created_at:datetime()})""",
            {"tenant": payload["tenantId"], "study": payload["platformStudyId"],
             "stage_version": payload["stageArtifact"]["artifactVersionId"],
             "id": payload["receiptId"], "version": payload["receiptVersionId"],
             "payload": canonical_json(payload), "artifact": canonical_json(artifact),
             "hash": artifact["payloadHash"]["value"]})


def store_source_draft_stage_bytes(*, tenant_id, platform_study_id, bytes_value,
                                   expected_hash, signed_envelope, verify_signature, store=None):
    _require(0 < len(bytes_value) <= MAX_STAGE_BYTES, "OSB_SOURCE_DRAFT_STAGE_SIZE_INVALID")
    _require(sha256_bytes(bytes_value) == expected_hash, "OSB_SOURCE_DRAFT_STAGE_TRANSFER_HASH_MISMATCH")
    payload = _json_bytes(bytes_value)
    _require(isinstance(signed_envelope, dict) and isinstance(signed_envelope.get("artifactDescriptor"), dict),
             "OSB_SOURCE_DRAFT_STAGE_SIGNATURE_REQUIRED")
    descriptor = signed_envelope["artifactDescriptor"]
    _require(descriptor.get("contractVersion") == "ArtifactDescriptorV1@1.0.0")
    artifact = _artifact_ref({key: value for key, value in descriptor.items() if key != "contractVersion"})
    _validate_stage(payload, artifact, tenant_id, platform_study_id)
    verify_stage_signature(payload, artifact, signed_envelope, verify_signature(payload, signed_envelope))
    _validate_stage(payload, artifact, tenant_id, platform_study_id)
    (store or NativeSourceDraftStageStore()).store(payload, signed_envelope, bytes_value, expected_hash)
    _validate_stage(payload, artifact, tenant_id, platform_study_id)
    return {"contractVersion": "ArtifactTransferReceiptV1@prototype", "kind": "source-draft-stage",
            "tenantId": tenant_id, "platformStudyId": platform_study_id,
            "contentHash": expected_hash, "byteSize": len(bytes_value), "signedEnvelopeBound": True}


def _prepare_stage_metadata(intents, members, native_study_id, port):
    """Keep independent metadata paths available when another path conflicts."""
    by_path, ready = {}, []
    context_loader = getattr(port, "stage_context", None)
    context = context_loader(native_study_id) if intents and callable(context_loader) else None
    for intent in intents:
        key = _key(intent)
        try:
            offer = prepare_metadata_offers([intent], native_study_id, context, port=port)[key]
            if offer["createOption"] is None:
                members[key]["blockers"] = [{"code": code} for code in offer["blockers"]]
                continue
            operation = offer["createOption"]["nativeStudyOperation"]
            by_path.setdefault(operation["metadataPath"], []).append(
                {"intent": intent, "candidate": offer, "selection": {"action": "create"}})
        except (OsbCandidateSetError, ValidationError) as error:
            members[key]["blockers"] = [{"code": getattr(error, "code", "OSB_STUDY_METADATA_VALUE_INVALID")}]
    for path, items in by_path.items():
        try:
            compose_metadata_values({path: [
                item["candidate"]["createOption"]["nativeStudyOperation"] for item in items]})
        except (OsbCandidateSetError, ValidationError) as error:
            for item in items:
                members[_key(item["intent"])]["blockers"] = [{
                    "code": getattr(error, "code", "OSB_STUDY_METADATA_VALUE_INVALID")}]
        else:
            ready.extend(items)
    return ready


def stage_native_source_draft(*, tenant_id, platform_study_id, stage_artifact,
                              verify_signature, store=None, capture_port=None, metadata_port=None):
    store = store or NativeSourceDraftStageStore()
    _descriptor(stage_artifact)
    stage, stage_envelope = store.load(
        tenant_id, platform_study_id, stage_artifact["payloadHash"]["value"], "source-draft-stage")
    request, request_envelope = store.load(
        tenant_id, platform_study_id, stage["candidateRequestArtifact"]["payloadHash"]["value"], "osb-candidate-request")
    intents = verify_stage_request(
        stage, stage_artifact, request, request_envelope, verify_signature(request, request_envelope),
        stage_envelope, verify_signature(stage, stage_envelope), tenant_id, platform_study_id)
    native = store.lock_scope(stage)
    # Lock waits and native service calls can outlive the source authorization.
    # Raising at any boundary rolls back the owning command transaction.
    _validate_stage(stage, stage_artifact, tenant_id, platform_study_id)
    previous = store.receipt(tenant_id, platform_study_id, stage_version=stage["stageVersionId"])
    if previous:
        _require(_same(previous["payload"]["stageArtifact"], stage_artifact),
                 "OSB_SOURCE_DRAFT_STAGE_REPLAY_CONFLICT", 409)
        _validate_stage(stage, stage_artifact, tenant_id, platform_study_id)
        return {**previous, "replay": True}
    capture_port = capture_port or NativeCapturePort()
    metadata_port = metadata_port or NativeStudyMetadataPort()
    members, captures, metadata = {}, [], []
    for intent in intents:
        key, family = _key(intent), intent["resourceFamily"]
        members[key] = {
            "sourceKey": key, "factId": intent["factId"], "revision": intent["revision"],
            "targetKey": intent["targetKey"], "resourceFamily": family,
            "sourceInputHash": _hash(intent, "OsbTypedSourceIntentV1@1.0.0", "application/json"),
            "outcome": "blocked", "nativeTarget": None, "readBackHash": None,
            "readBack": None, "fieldReceipts": [], "blockers": [],
        }
        if family in CAPTURE_FAMILY_TYPES:
            try:
                plan = plan_native_capture(intent, allow_pending_relationships=True)
                capture_port.validate(plan)
                captures.append({"intent": intent, "selection": {"action": "create", "candidateIdentity": None}})
            except (OsbCandidateSetError, ValidationError) as error:
                members[key]["blockers"].append({"code": getattr(error, "code", "OSB_CAPTURE_NATIVE_DTO_INVALID")})
        elif family == "study_metadata":
            metadata.append(intent)
        else:
            members[key]["blockers"].append({"code": "OSB_SOURCE_DRAFT_STAGE_FAMILY_UNSUPPORTED"})
    metadata_ready = _prepare_stage_metadata(metadata, members, native["nativeStudyId"], metadata_port)
    _validate_stage(stage, stage_artifact, tenant_id, platform_study_id)
    outcomes = {}
    observations = apply_native_capture_selections(
        captures, tenant_id=tenant_id, platform_study_id=platform_study_id,
        native_study_id=native["nativeStudyId"], port=capture_port,
        allow_pending_relationships=True, outcomes=outcomes)
    _validate_stage(stage, stage_artifact, tenant_id, platform_study_id)
    for item in captures:
        intent, key = item["intent"], _key(item["intent"])
        observed = observations[key]
        fields, blockers = capture_field_receipts(
            intent, observed, binding_key=f"{tenant_id}|{platform_study_id}|{key}",
            native_study_id=native["nativeStudyId"])
        members[key].update(
            outcome=outcomes[key]["outcome"],
            nativeTarget={name: observed[name] for name in ("uid", "version", "resourceFamily", "resourceType")},
            readBack=observed, readBackHash=_hash(observed, CAPTURE_READBACK_SCHEMA, "application/json"),
            fieldReceipts=fields, blockers=outcomes[key]["blockers"] + blockers)
    if metadata_ready:
        _validate_stage(stage, stage_artifact, tenant_id, platform_study_id)
        applied = apply_metadata_selections(metadata_ready, native["nativeStudyId"], port=metadata_port)
        for item in metadata_ready:
            key = _key(item["intent"])
            observed = applied[key]
            members[key].update(
                outcome="created",
                nativeTarget={name: observed[name] for name in ("uid", "version", "resourceFamily", "resourceType")},
                readBack=observed, readBackHash=_hash(observed, "OsbStudyMetadataReadBackV1@1.0.0", "application/json"),
                blockers=[{"code": "NATIVE_METADATA_SOURCE_FIELD_REVIEW_REQUIRED"}])
    _validate_stage(stage, stage_artifact, tenant_id, platform_study_id)
    receipt_id = str(uuid5(NAMESPACE_URL, f'accuratrials:source-draft-stage:{stage["stageId"]}'))
    receipt_version = str(uuid5(NAMESPACE_URL, f'{receipt_id}:{stage_artifact["payloadHash"]["value"]}'))
    member_list = [members[key] for key in stage["selectedSourceKeys"]]
    created = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    receipt = {
        "contractVersion": RECEIPT_CONTRACT, "receiptId": receipt_id, "receiptVersionId": receipt_version,
        "stageArtifact": stage_artifact, "candidateRequestArtifact": stage["candidateRequestArtifact"],
        "tenantId": tenant_id, "platformStudyId": platform_study_id,
        "nativeStudyId": native["nativeStudyId"], "nativeStudyVersion": native["nativeVersion"],
        "semanticSnapshotHash": stage["semanticSnapshotHash"], "sourceFactPackageHash": stage["sourceFactPackageHash"],
        "createdAt": created,
        "performedBy": {"actorType": "service", "issuerQualifiedSubject": "osb.clinical-mdr-api"},
        "clinicalApproval": False, "releaseEligible": False,
        "summary": {"selected": len(member_list),
                    **{status: sum(member["outcome"] == status for member in member_list)
                       for status in ("created", "reused", "blocked")},
                    "withBlockers": sum(bool(member["blockers"]) for member in member_list)},
        "members": member_list,
    }
    artifact = _artifact_ref({
        "artifactId": receipt_id, "artifactVersionId": receipt_version, "kind": "source-draft-stage-receipt",
        "stableLocator": f"artifact://osb/source-draft-stage-receipt/{receipt_version}",
        "payloadHash": _hash(receipt, RECEIPT_CONTRACT, RECEIPT_MEDIA_TYPE),
        "byteSize": len(canonical_json(receipt).encode("utf-8")),
        "classification": "regulated-non-phi", "tenantId": tenant_id, "region": stage_artifact["region"],
        "producerService": "osb.clinical-mdr-api", "producerEnvironment": "prototype",
        "producerVersion": "source-draft-stage/1.0.0", "payloadContract": "accuratrials.osb.OsbSourceDraftStageReceiptV1",
        "payloadContractVersion": "1.0.0", "purpose": "source-draft-review", "createdAt": created,
    })
    store.save_receipt(receipt, artifact)
    _validate_stage(stage, stage_artifact, tenant_id, platform_study_id)
    return {"payload": receipt, "artifactRef": artifact, "replay": False}
