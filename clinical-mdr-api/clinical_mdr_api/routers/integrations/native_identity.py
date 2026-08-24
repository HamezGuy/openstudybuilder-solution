"""Default-off CC-commanded OSB draft-root create/bind endpoint."""

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any, cast

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from neomodel import db

from clinical_mdr_api.generated.platform_contracts.models_v1 import (
    ExternalIdentityCreateIntentV1,
    NativeIdentityBindingReceiptV1,
)
from clinical_mdr_api.generated.platform_contracts.native_identity_command_processor_v1 import (
    NativeIdentityCommandError,
    NativeIdentityCommandProcessorV1,
    NativeIdentityPrincipalV1,
)
from clinical_mdr_api.services.integrations.native_identity import Neo4jOsbNativeIdentityStoreV1
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandError,
    PlatformCommandPrincipalV1,
    RemotePlatformCommandReceiptPublisherV1,
    execute_signed_platform_command,
    execute_platform_command,
)
from clinical_mdr_api.services.integrations.platform_command import Neo4jOsbPlatformCommandStore
from clinical_mdr_api.services.integrations.candidate_set import (
    CANDIDATE_SET_MEDIA_TYPE,
    OsbCandidateSetError,
    decode_signed_artifact_envelope_header,
    generate_candidate_set,
    load_candidate_request,
    store_candidate_request_bytes,
)
from clinical_mdr_api.services.integrations.mapping_decision_v1 import (
    DECISION_MEDIA_TYPE,
    EVIDENCE_SET_MEDIA_TYPE,
    apply_mapping_decision,
    store_mapping_decision_bytes,
)
from clinical_mdr_api.services.integrations.native_package_v2 import (
    PACKAGE_V2_MEDIA_TYPE,
    SPECIALIST_REVIEW_MEDIA_TYPE,
    generate_native_package_v2,
    record_specialist_review,
    store_release_artifact_bytes,
)
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from common.auth.dependencies import security
from common.auth.user import user
from common.config import settings

router = APIRouter()


class _UnavailablePublisher:
    @staticmethod
    def publish(_receipt: NativeIdentityBindingReceiptV1) -> dict[str, Any]:
        raise NativeIdentityCommandError(
            "OSB_SIGNED_PUBLICATION_UNAVAILABLE",
            "P2 KMS/RFC3161 signed mutation publication is not configured.",
            503,
        )


processor = NativeIdentityCommandProcessorV1(
    target_system="osb",
    producer_service="osb.package",
    namespaces=("accuratrials-osb",),
    object_types=("study-draft-root",),
    allowed_roles=("Admin.Write", "Study.Write", "service"),
    store=Neo4jOsbNativeIdentityStoreV1(),
    publisher=_UnavailablePublisher(),
)
platform_command_store = Neo4jOsbPlatformCommandStore()


class _SignedConformanceHandler:
    def __init__(self, effect: dict[str, Any]):
        self.effect = effect

    def prepare(self) -> dict[str, Any]:
        return self.effect

    def commit(self, _transaction: Any, prepared_effect: dict[str, Any]) -> dict[str, Any]:
        return prepared_effect


def _platform_principal(capability: str, platform_study_id: str):
    principal = user()
    if (
        principal.purpose != "workflow-orchestration"
        or capability not in principal.capabilities
        or not principal.tenant_id
        or platform_study_id not in principal.study_ids
    ):
        raise HTTPException(status_code=403, detail="OSB_PLATFORM_COMMAND_SCOPE_DENIED")
    return principal


def _verify_candidate_request_signature(
    payload: dict[str, Any], signed_envelope: dict[str, Any]
) -> dict[str, Any]:
    endpoint = os.getenv("OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_URL", "").strip()
    if not endpoint:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFIER_UNAVAILABLE",
            "Candidate-request signature verification is not configured.",
            503,
        )
    request_body = json.dumps({
        "payloadBase64": base64.b64encode(canonical_json(payload).encode("utf-8")).decode("ascii"),
        "envelope": signed_envelope,
    }, separators=(",", ":")).encode("utf-8")
    remote_request = urllib.request.Request(
        endpoint,
        data=request_body,
        method="POST",
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(remote_request, timeout=10) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_FAILED",
            "Candidate-request signature verification failed.",
            503,
        ) from error
    verification = response_body.get("verification") if isinstance(response_body, dict) else None
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_UNVERIFIED",
            "Candidate-request signature is not trusted.",
            422,
        )
    return verification


@router.get(
    "/commands/{command_id}/receipt",
    dependencies=[security],
    summary="Read the immutable original platform-command result",
)
def get_platform_command_receipt(command_id: str, request: Request) -> dict[str, Any]:
    platform_study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    principal = user()
    if (
        principal.purpose != "workflow-orchestration"
        or platform_study_id not in principal.study_ids
        or not principal.tenant_id
    ):
        raise HTTPException(status_code=403, detail="OSB_PLATFORM_COMMAND_RECEIPT_SCOPE_DENIED")
    result = platform_command_store.lookup(principal.tenant_id, platform_study_id, command_id)
    if result is None:
        raise HTTPException(status_code=404, detail="PLATFORM_COMMAND_RECEIPT_NOT_FOUND")
    return {"ok": True, "data": result}


@router.get(
    "/native-study-roots",
    dependencies=[security],
    summary="Inventory tenant-scoped legacy OSB roots for human review",
)
def inventory_native_study_roots() -> dict[str, Any]:
    if (
        not settings.native_identity_inventory_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_NATIVE_IDENTITY_INVENTORY_DISABLED")
    principal = user()
    if (
        principal.purpose != "workflow-orchestration"
        or "native-identity:inventory" not in principal.capabilities
        or "Admin.Read" not in principal.roles
    ):
        raise HTTPException(status_code=403, detail="NATIVE_IDENTITY_INVENTORY_DENIED")
    rows, _ = db.cypher_query(
        """
        MATCH (scope:DomainStudyScope {tenant_id: $tenant_id, status: 'active'})
        MATCH (study:StudyRoot {uid: scope.study_uid})
        OPTIONAL MATCH (study)-[version_rel:LATEST_DRAFT|LATEST|LATEST_LOCKED|LATEST_RELEASED]->(value:StudyValue)
        RETURN study.uid, type(version_rel), version_rel.version, version_rel.status,
               value.study_title, value.study_number, value.study_acronym,
               value.project_number
        ORDER BY study.uid,
          CASE type(version_rel)
            WHEN 'LATEST_DRAFT' THEN 0
            WHEN 'LATEST' THEN 1
            WHEN 'LATEST_LOCKED' THEN 2
            WHEN 'LATEST_RELEASED' THEN 3
            ELSE 4
          END
        """,
        {"tenant_id": principal.tenant_id},
    )
    roots: list[dict[str, Any]] = []
    observed: set[str] = set()
    for row in rows:
        native_identity = str(row[0] or "").strip()
        if not native_identity or native_identity in observed:
            continue
        observed.add(native_identity)
        version = str(row[2] or "").strip()
        status = str(row[3] or row[1] or "unknown").strip().lower()
        title = str(row[4] or row[6] or row[5] or row[7] or native_identity).strip()
        identifiers = [
            {"namespace": namespace, "value": str(value).strip()}
            for namespace, value in (
                ("study-number", row[5]),
                ("study-acronym", row[6]),
                ("project-number", row[7]),
            )
            if value is not None and str(value).strip()
        ]
        roots.append(
            {
                "nativeIdentity": native_identity,
                **({"nativeVersion": version} if version else {}),
                "status": status,
                "label": title,
                "identifiers": identifiers,
            }
        )
    return {
        "ok": True,
        "data": {
            "contractVersion": "prototype-legacy-identity-inventory/1.0",
            "system": "osb",
            "tenantId": principal.tenant_id,
            "observedAt": datetime.now(UTC).isoformat(),
            "roots": roots,
        },
    }


@router.post(
    "/native-study-roots/create-or-bind",
    dependencies=[security],
    summary="Create or bind one exact OSB draft root (disabled until P2)",
)
def create_or_bind_native_study_root(body: dict[str, Any]) -> dict[str, Any]:
    if (
        not settings.native_identity_endpoint_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_NATIVE_IDENTITY_ENDPOINT_DISABLED")
    principal = user()
    try:
        result = processor.process(
            cast(ExternalIdentityCreateIntentV1, body),
            NativeIdentityPrincipalV1(
                tenant_id=principal.tenant_id,
                study_ids=tuple(sorted(principal.study_ids)),
                subject=principal.sub,
                human_subject=principal.human_subject or None,
                actor_chain=tuple(principal.actor_chain),
                roles=tuple(sorted(principal.roles)),
                purpose=principal.purpose,
                capabilities=tuple(sorted(principal.capabilities)),
            ),
        )
        return {"ok": True, "data": result}
    except NativeIdentityCommandError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@router.post(
    "/commands/conformance",
    dependencies=[security],
    summary="Execute a durable prototype platform conformance command",
)
def execute_conformance_command(body: dict[str, Any]) -> dict[str, Any]:
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
    if body.get("action") != "platform.demo.conformance":
        raise HTTPException(status_code=422, detail="OSB_PLATFORM_COMMAND_ACTION_UNSUPPORTED")
    signing_endpoint = os.getenv("OSB_PLATFORM_COMMAND_SIGNING_URL", "").strip()
    if not signing_endpoint:
        raise HTTPException(status_code=503, detail="OSB_SIGNED_PUBLICATION_UNAVAILABLE")
    principal = user()
    try:
        effect = {
            "status": "succeeded", "targetIdentity": body["platformStudyId"],
            "targetVersion": "prototype-v1",
            "targetState": {"system": "osb", "capability": body["targetCapability"], "accepted": True},
            "conservationCounts": {"received": 1, "accepted": 1},
            "effectPayload": {"conformance": True},
        }
        result = execute_signed_platform_command(
            body,
            PlatformCommandPrincipalV1(
                tenant_id=principal.tenant_id,
                study_ids=tuple(sorted(principal.study_ids)),
                subject=principal.sub,
                actor_chain=tuple(principal.actor_chain),
                roles=tuple(sorted(principal.roles)),
                purpose=principal.purpose,
                capabilities=tuple(sorted(principal.capabilities)),
            ),
            "osb",
            "osb.package",
            platform_command_store,
            _SignedConformanceHandler(effect),
            RemotePlatformCommandReceiptPublisherV1(
                signing_endpoint, "osb.package", settings.deployment_environment,
                allow_insecure_prototype=True,
            ),
        )
        return {"ok": True, "data": result}
    except PlatformCommandError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@router.post(
    "/artifacts/osb-candidate-requests",
    dependencies=[security],
    summary="Store exact canonical CSL candidate-request bytes",
)
async def upload_candidate_request(request: Request) -> dict[str, Any]:
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
    platform_study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    expected_hash = str(request.headers.get("x-content-sha256") or "").strip()
    principal = _platform_principal("candidate:generate", platform_study_id)
    try:
        with db.transaction:
            result = store_candidate_request_bytes(
                tenant_id=principal.tenant_id,
                platform_study_id=platform_study_id,
                bytes_value=await request.body(),
                expected_hash=expected_hash,
                signed_envelope=decode_signed_artifact_envelope_header(
                    request.headers.get("x-signed-artifact-envelope")
                ),
            )
        return {"ok": True, "data": result}
    except OsbCandidateSetError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@router.post(
    "/commands/candidate-set",
    dependencies=[security],
    summary="Generate one immutable OSB candidate set from an exact CSL request",
)
def execute_candidate_set_command(body: dict[str, Any], request: Request) -> dict[str, Any]:
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
    if body.get("action") != "osb.candidate-set.generate" or body.get("targetCapability") != "candidate:generate":
        raise HTTPException(status_code=422, detail="OSB_CANDIDATE_SET_COMMAND_UNSUPPORTED")
    principal = _platform_principal("candidate:generate", str(body.get("platformStudyId") or ""))

    def handler(_tx) -> dict[str, Any]:
        input_payload = body.get("inputPayload")
        if not isinstance(input_payload, dict) or not isinstance(input_payload.get("candidateRequestArtifact"), dict):
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_ARTIFACT_REQUIRED", "Candidate request artifact is required.", 422)
        artifact = input_payload["candidateRequestArtifact"]
        signed_envelope = input_payload.get("candidateRequestSignedEnvelope")
        if not isinstance(signed_envelope, dict):
            raise OsbCandidateSetError(
                "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED",
                "Signed candidate-request envelope is required.",
                422,
            )
        payload_hash = artifact.get("payloadHash")
        if not isinstance(payload_hash, dict) or not isinstance(payload_hash.get("value"), str):
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_HASH_REQUIRED", "Candidate request hash is required.", 422)
        request_payload = load_candidate_request(
            payload_hash["value"], principal.tenant_id, body["platformStudyId"]
        )
        signature_verification = _verify_candidate_request_signature(
            request_payload, signed_envelope
        )
        generated = generate_candidate_set(
            request_payload=request_payload,
            artifact=artifact,
            tenant_id=principal.tenant_id,
            platform_study_id=body["platformStudyId"],
            osb_openapi_hash=canonical_hash(request.app.openapi()),
            actor=body["requestingActor"]["issuerQualifiedSubject"],
            signed_envelope=signed_envelope,
            signature_verification=signature_verification,
        )
        candidate_records = generated["payload"]["candidateRecords"]
        blocker_count = len(generated["payload"].get("blockers") or [])
        return {
            "status": "no_op" if generated.get("replay") else "succeeded",
            "targetIdentity": generated["nativeIdentity"],
            "targetVersion": generated["nativeVersion"],
            "targetState": {
                "candidateSetHash": generated["payloadHash"],
                "candidateSetVersionId": generated["candidateSetVersionId"],
                "blockerCount": blocker_count,
            },
            "consumedArtifacts": [artifact],
            "producedArtifacts": [generated["artifactRef"]],
            "conservationCounts": {
                "sourceIntents": len(candidate_records),
                "candidateRecords": len(candidate_records),
                "dropped": 0,
            },
            "blockers": generated["payload"].get("blockers") or [],
            "effectPayload": {
                "candidateSetId": generated["candidateSetId"],
                "candidateSetVersionId": generated["candidateSetVersionId"],
                "candidateSetHash": generated["payloadHash"],
                "candidateSetArtifact": generated["artifactRef"],
                "assignment": generated.get("assignment"),
                "signedArtifactEnvelope": generated.get("signedEnvelope"),
            },
        }

    try:
        result = execute_platform_command(
            body,
            PlatformCommandPrincipalV1(
                tenant_id=principal.tenant_id,
                study_ids=tuple(sorted(principal.study_ids)),
                subject=principal.sub,
                actor_chain=tuple(principal.actor_chain),
                roles=tuple(sorted(principal.roles)),
                purpose=principal.purpose,
                capabilities=tuple(sorted(principal.capabilities)),
            ),
            "osb",
            platform_command_store,
            handler,
        )
        return {"ok": True, "data": result}
    except (PlatformCommandError, OsbCandidateSetError) as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@router.get(
    "/artifacts/osb-candidate-sets/{candidate_set_version_id}",
    dependencies=[security],
    summary="Read exact canonical OSB candidate-set bytes",
)
def get_candidate_set(candidate_set_version_id: str, request: Request) -> Response:
    platform_study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    principal = _platform_principal("candidate:read", platform_study_id)
    rows, _ = db.cypher_query(
        """MATCH (candidate:OsbCandidateSetV1 {tenant_id: $tenant_id,
             platform_study_id: $platform_study_id,
             candidate_set_version_id: $candidate_set_version_id})
           RETURN candidate.payload_json,candidate.payload_hash""",
        {"tenant_id": principal.tenant_id, "platform_study_id": platform_study_id,
         "candidate_set_version_id": candidate_set_version_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="OSB_CANDIDATE_SET_NOT_FOUND")
    payload_bytes = str(rows[0][0]).encode("utf-8")
    return Response(
        content=payload_bytes,
        media_type=CANDIDATE_SET_MEDIA_TYPE,
        headers={"etag": f'"{rows[0][1]}"', "content-length": str(len(payload_bytes))},
    )


@router.post(
    "/artifacts/study-mapping-decisions",
    dependencies=[security],
    summary="Store exact canonical CSL mapping-decision bytes",
)
async def upload_mapping_decision(request: Request) -> dict[str, Any]:
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
    platform_study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    expected_hash = str(request.headers.get("x-content-sha256") or "").strip()
    principal = _platform_principal("candidate:apply", platform_study_id)
    try:
        with db.transaction:
            result = store_mapping_decision_bytes(
                tenant_id=principal.tenant_id,
                platform_study_id=platform_study_id,
                bytes_value=await request.body(),
                expected_hash=expected_hash,
            )
        return {"ok": True, "data": result}
    except OsbCandidateSetError as error:
        raise HTTPException(status_code=error.status_code,
                            detail={"code": error.code, "message": str(error)}) from error


@router.post(
    "/commands/mapping-decision",
    dependencies=[security],
    summary="Apply one CSL mapping decision and emit exact native read-back evidence",
)
def execute_mapping_decision_command(body: dict[str, Any], request: Request) -> dict[str, Any]:
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
    if body.get("action") != "osb.mapping-decision.apply" or body.get("targetCapability") != "candidate:apply":
        raise HTTPException(status_code=422, detail="OSB_MAPPING_DECISION_COMMAND_UNSUPPORTED")
    principal = _platform_principal("candidate:apply", str(body.get("platformStudyId") or ""))

    def handler(_tx) -> dict[str, Any]:
        input_payload = body.get("inputPayload")
        if not isinstance(input_payload, dict) or not isinstance(input_payload.get("decisionArtifact"), dict):
            raise OsbCandidateSetError("OSB_MAPPING_DECISION_ARTIFACT_REQUIRED", "Decision artifact is required.", 422)
        artifact = input_payload["decisionArtifact"]
        applied = apply_mapping_decision(
            tenant_id=principal.tenant_id,
            platform_study_id=body["platformStudyId"],
            decision_artifact=artifact,
            actor=body["requestingActor"]["issuerQualifiedSubject"],
            osb_openapi_hash=canonical_hash(request.app.openapi()),
        )
        evidence_records = applied["payload"]["evidenceRecords"]
        blockers = [
            {"code": "OSB_MAPPING_DECISION_DEFERRED", "evidenceId": record["evidence"]["evidenceId"]}
            for record in evidence_records
            if record["evidence"].get("disposition") == "deferred_blocking"
        ]
        return {
            "status": "no_op" if applied.get("replay") else "succeeded",
            "targetIdentity": applied["nativeIdentity"],
            "targetVersion": applied["nativeVersion"],
            "targetState": {"nativeEvidenceSetHash": applied["payloadHash"],
                            "managedTargetCheckpointHash": applied["payload"]["managedTargetCheckpointHash"]},
            "consumedArtifacts": [artifact], "producedArtifacts": [applied["artifactRef"]],
            "conservationCounts": {"decisions": len(evidence_records), "evidenceRecords": len(evidence_records), "dropped": 0},
            "blockers": blockers,
            "effectPayload": {"evidenceSetId": applied["evidenceSetId"],
                              "evidenceSetVersionId": applied["evidenceSetVersionId"],
                              "nativeEvidenceSetHash": applied["payloadHash"],
                              "nativeEvidenceSetArtifact": applied["artifactRef"]},
        }

    try:
        result = execute_platform_command(
            body,
            PlatformCommandPrincipalV1(
                tenant_id=principal.tenant_id, study_ids=tuple(sorted(principal.study_ids)),
                subject=principal.sub, actor_chain=tuple(principal.actor_chain),
                roles=tuple(sorted(principal.roles)), purpose=principal.purpose,
                capabilities=tuple(sorted(principal.capabilities)),
            ),
            "osb", platform_command_store, handler,
        )
        return {"ok": True, "data": result}
    except (PlatformCommandError, OsbCandidateSetError) as error:
        raise HTTPException(status_code=error.status_code,
                            detail={"code": error.code, "message": str(error)}) from error


@router.get(
    "/artifacts/native-evidence-sets/{evidence_set_version_id}",
    dependencies=[security],
    summary="Read exact canonical OSB native-evidence bytes",
)
def get_native_evidence_set(evidence_set_version_id: str, request: Request) -> Response:
    platform_study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    principal = _platform_principal("candidate:read", platform_study_id)
    rows, _ = db.cypher_query(
        """MATCH (evidence:OsbNativeEvidenceSetV1 {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,evidence_set_version_id:$version_id})
           RETURN evidence.payload_json,evidence.payload_hash""",
        {"tenant_id": principal.tenant_id, "platform_study_id": platform_study_id,
         "version_id": evidence_set_version_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="OSB_NATIVE_EVIDENCE_SET_NOT_FOUND")
    payload_bytes = str(rows[0][0]).encode("utf-8")
    return Response(content=payload_bytes, media_type=EVIDENCE_SET_MEDIA_TYPE,
                    headers={"etag": f'"{rows[0][1]}"', "content-length": str(len(payload_bytes))})


@router.post(
    "/artifacts/release-prerequisites/{artifact_kind}",
    dependencies=[security],
    summary="Store exact canonical release prerequisite bytes",
)
async def upload_release_prerequisite(artifact_kind: str, request: Request) -> dict[str, Any]:
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
    platform_study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    expected_hash = str(request.headers.get("x-content-sha256") or "").strip()
    principal = _platform_principal("package:release", platform_study_id)
    try:
        with db.transaction:
            result = store_release_artifact_bytes(
                kind=artifact_kind,
                tenant_id=principal.tenant_id,
                platform_study_id=platform_study_id,
                bytes_value=await request.body(),
                expected_hash=expected_hash,
            )
        return {"ok": True, "data": result}
    except OsbCandidateSetError as error:
        raise HTTPException(status_code=error.status_code,
                            detail={"code": error.code, "message": str(error)}) from error


@router.post(
    "/commands/specialist-review",
    dependencies=[security],
    summary="Record immutable OSB specialist review against a transformation checkpoint",
)
def execute_specialist_review_command(body: dict[str, Any]) -> dict[str, Any]:
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
    if body.get("action") != "osb.specialist-review.record" or body.get("targetCapability") != "package:release":
        raise HTTPException(status_code=422, detail="OSB_SPECIALIST_REVIEW_COMMAND_UNSUPPORTED")
    principal = _platform_principal("package:release", str(body.get("platformStudyId") or ""))

    def handler(_tx) -> dict[str, Any]:
        input_payload = body.get("inputPayload")
        if not isinstance(input_payload, dict):
            raise OsbCandidateSetError("OSB_SPECIALIST_REVIEW_INPUT_REQUIRED", "Review input is required.", 422)
        reviewed = record_specialist_review(
            tenant_id=principal.tenant_id,
            platform_study_id=body["platformStudyId"],
            input_payload=input_payload,
            actor=body["requestingActor"]["issuerQualifiedSubject"],
        )
        identity = reviewed["payload"]["osbStudyIdentity"]
        return {
            "status": "no_op" if reviewed.get("replay") else "succeeded",
            "targetIdentity": identity["nativeIdentity"],
            "targetVersion": identity["nativeVersion"],
            "targetState": {"specialistReviewHash": reviewed["payloadHash"], "lockState": "checkpoint-locked"},
            "consumedArtifacts": [input_payload["transformationCheckpointArtifact"]],
            "producedArtifacts": [reviewed["artifactRef"]],
            "conservationCounts": {"checkpoint": 1, "reviewEvidence": 1, "dropped": 0},
            "effectPayload": {"specialistReviewHash": reviewed["payloadHash"],
                              "specialistReviewArtifact": reviewed["artifactRef"]},
        }

    try:
        result = execute_platform_command(
            body,
            PlatformCommandPrincipalV1(
                tenant_id=principal.tenant_id, study_ids=tuple(sorted(principal.study_ids)),
                subject=principal.sub, actor_chain=tuple(principal.actor_chain),
                roles=tuple(sorted(principal.roles)), purpose=principal.purpose,
                capabilities=tuple(sorted(principal.capabilities)),
            ),
            "osb", platform_command_store, handler,
        )
        return {"ok": True, "data": result}
    except (PlatformCommandError, OsbCandidateSetError) as error:
        raise HTTPException(status_code=error.status_code,
                            detail={"code": error.code, "message": str(error)}) from error


@router.get(
    "/artifacts/specialist-reviews/{review_version_id}",
    dependencies=[security],
    summary="Read exact canonical specialist-review bytes",
)
def get_specialist_review(review_version_id: str, request: Request) -> Response:
    platform_study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    principal = _platform_principal("package:release", platform_study_id)
    rows, _ = db.cypher_query(
        """MATCH (review:OsbSpecialistReviewEvidenceV1 {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,review_version_id:$version_id})
           RETURN review.payload_json,review.payload_hash""",
        {"tenant_id": principal.tenant_id, "platform_study_id": platform_study_id,
         "version_id": review_version_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="OSB_SPECIALIST_REVIEW_NOT_FOUND")
    payload_bytes = str(rows[0][0]).encode("utf-8")
    return Response(content=payload_bytes, media_type=SPECIALIST_REVIEW_MEDIA_TYPE,
                    headers={"etag": f'"{rows[0][1]}"', "content-length": str(len(payload_bytes))})


@router.post(
    "/commands/native-package-v2",
    dependencies=[security],
    summary="Generate immutable raw-byte OSB-native Package V2",
)
def execute_native_package_v2_command(body: dict[str, Any]) -> dict[str, Any]:
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
    if body.get("action") != "osb.package-v2.release" or body.get("targetCapability") != "package:release":
        raise HTTPException(status_code=422, detail="OSB_NATIVE_PACKAGE_V2_COMMAND_UNSUPPORTED")
    principal = _platform_principal("package:release", str(body.get("platformStudyId") or ""))

    def handler(_tx) -> dict[str, Any]:
        input_payload = body.get("inputPayload")
        if not isinstance(input_payload, dict):
            raise OsbCandidateSetError("OSB_NATIVE_PACKAGE_V2_INPUT_REQUIRED", "Package input is required.", 422)
        generated = generate_native_package_v2(
            tenant_id=principal.tenant_id,
            platform_study_id=body["platformStudyId"],
            input_payload=input_payload,
            actor=body["requestingActor"]["issuerQualifiedSubject"],
        )
        identity = generated["payload"]["osbStudyIdentity"]
        consumed = [input_payload[key] for key in (
            "transformationCheckpointArtifact", "platformManifestArtifact",
            "preReleaseApprovalArtifact", "specialistReviewArtifact",
        )]
        census_counts = generated["payload"]["conservation"]["counts"]
        return {
            "status": "no_op" if generated.get("replay") else "succeeded",
            "targetIdentity": identity["nativeIdentity"],
            "targetVersion": identity["nativeVersion"],
            "targetState": {"packageHash": generated["payloadHash"],
                            "packageVersionId": generated["packageVersionId"],
                            "productionEligible": False},
            "consumedArtifacts": consumed, "producedArtifacts": [generated["artifactRef"]],
            "conservationCounts": census_counts, "blockers": [],
            "effectPayload": {"packageHash": generated["payloadHash"],
                              "packageArtifact": generated["artifactRef"],
                              "packageVersionId": generated["packageVersionId"]},
        }

    try:
        result = execute_platform_command(
            body,
            PlatformCommandPrincipalV1(
                tenant_id=principal.tenant_id, study_ids=tuple(sorted(principal.study_ids)),
                subject=principal.sub, actor_chain=tuple(principal.actor_chain),
                roles=tuple(sorted(principal.roles)), purpose=principal.purpose,
                capabilities=tuple(sorted(principal.capabilities)),
            ),
            "osb", platform_command_store, handler,
        )
        return {"ok": True, "data": result}
    except (PlatformCommandError, OsbCandidateSetError) as error:
        raise HTTPException(status_code=error.status_code,
                            detail={"code": error.code, "message": str(error)}) from error


@router.get(
    "/artifacts/native-packages-v2/{package_version_id}",
    dependencies=[security],
    summary="Read exact canonical raw Package V2 bytes",
)
def get_native_package_v2(package_version_id: str, request: Request) -> Response:
    platform_study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    principal = _platform_principal("package:release", platform_study_id)
    rows, _ = db.cypher_query(
        """MATCH (package:OsbNativePackageV2 {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,package_version_id:$version_id})
           RETURN package.payload_json,package.payload_hash,package.byte_size""",
        {"tenant_id": principal.tenant_id, "platform_study_id": platform_study_id,
         "version_id": package_version_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="OSB_NATIVE_PACKAGE_V2_NOT_FOUND")
    payload_bytes = str(rows[0][0]).encode("utf-8")
    if len(payload_bytes) != int(rows[0][2]):
        raise HTTPException(status_code=409, detail="OSB_NATIVE_PACKAGE_V2_STORED_SIZE_MISMATCH")
    return Response(content=payload_bytes, media_type=PACKAGE_V2_MEDIA_TYPE,
                    headers={"etag": f'"{rows[0][1]}"', "content-length": str(len(payload_bytes))})
