"""Scoped source-draft staging, separate from human mapping/release commands."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from neomodel import db

from common.auth.dependencies import platform_security
from common.config import settings
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandError, PlatformCommandPrincipalV1,
)
from clinical_mdr_api.services.integrations.atomic_signed_command import execute_osb_atomic_signed_command
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.services.integrations.candidate_set import (
    OsbCandidateSetError, decode_signed_artifact_envelope_header,
)
from clinical_mdr_api.services.integrations.source_draft_stage import (
    MAX_STAGE_BYTES, RECEIPT_MEDIA_TYPE, NativeSourceDraftStageStore,
    stage_native_source_draft, store_source_draft_stage_bytes,
)

router = APIRouter()


def _enabled():
    if not settings.platform_commands_prototype_enabled \
            or settings.deployment_environment.strip().lower() in {"prod", "production"}:
        raise HTTPException(status_code=503, detail="OSB_PLATFORM_COMMANDS_DISABLED")


def _dependencies():
    # Resolve after the parent router is initialized, avoiding an import cycle.
    from clinical_mdr_api.routers.integrations.native_identity import (
        _platform_principal, _verify_candidate_request_signature, platform_command_store,
    )

    return _platform_principal, _verify_candidate_request_signature, platform_command_store


def _error(error):
    return HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)})


@router.post("/artifacts/source-draft-stages", dependencies=[platform_security],
             summary="Retain exact signed CSL source-draft-stage bytes")
async def upload_source_draft_stage(request: Request):
    _enabled()
    principal_for, verify_signature, _ = _dependencies()
    study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    principal = principal_for("draft:stage", study_id)
    try:
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_STAGE_BYTES:
                raise HTTPException(status_code=413, detail="OSB_SOURCE_DRAFT_STAGE_TOO_LARGE")
            chunks.append(chunk)
        # Never suspend inside a neomodel transaction.
        payload_bytes = b"".join(chunks)
        envelope = decode_signed_artifact_envelope_header(request.headers.get("x-signed-artifact-envelope"))
        with db.transaction:
            result = store_source_draft_stage_bytes(
                tenant_id=principal.tenant_id, platform_study_id=study_id,
                bytes_value=payload_bytes, expected_hash=str(request.headers.get("x-content-sha256") or ""),
                signed_envelope=envelope, verify_signature=verify_signature)
        return {"ok": True, "data": result}
    except OsbCandidateSetError as error:
        raise _error(error) from error


@router.post("/commands/source-draft", dependencies=[platform_security],
             summary="Stage native source drafts without creating clinical approval")
def execute_source_draft_stage(body: dict[str, Any], request: Request):
    _enabled()
    if body.get("action") != "osb.source-draft.stage" or body.get("targetCapability") != "draft:stage":
        raise HTTPException(status_code=422, detail="OSB_SOURCE_DRAFT_STAGE_COMMAND_UNSUPPORTED")
    principal_for, verify_signature, command_store = _dependencies()
    principal = principal_for("draft:stage", str(body.get("platformStudyId") or ""))

    def handler(_tx):
        inputs = body.get("inputPayload")
        if not isinstance(inputs, dict) or set(inputs) != {"stageArtifact"} \
                or not isinstance(inputs["stageArtifact"], dict):
            raise OsbCandidateSetError("OSB_SOURCE_DRAFT_STAGE_ARTIFACT_REQUIRED",
                                       "Only the exact retained stage artifact is accepted.", 422)
        result = stage_native_source_draft(
            tenant_id=principal.tenant_id, platform_study_id=body["platformStudyId"],
            stage_artifact=inputs["stageArtifact"], verify_signature=verify_signature)
        receipt = result["payload"]
        return {
            "status": "no_op" if result["replay"] else "succeeded",
            "targetIdentity": receipt["nativeStudyId"], "targetVersion": receipt["nativeStudyVersion"],
            "targetState": {"sourceDraftStageReceiptHash": result["artifactRef"]["payloadHash"],
                            "clinicalApproval": False, "releaseEligible": False},
            "consumedArtifacts": [inputs["stageArtifact"], receipt["candidateRequestArtifact"]],
            "producedArtifacts": [result["artifactRef"]],
            "conservationCounts": {**receipt["summary"], "dropped": 0},
            "blockers": [{"code": "SOURCE_DRAFT_CLINICAL_REVIEW_REQUIRED"}],
            "effectPayload": {
                "stageId": receipt["stageArtifact"]["artifactId"], "receiptId": receipt["receiptId"],
                "receiptVersionId": receipt["receiptVersionId"], "stageReceiptArtifact": result["artifactRef"],
                "clinicalApproval": False, "releaseEligible": False,
            },
        }

    try:
        result = execute_osb_atomic_signed_command(
            body, PlatformCommandPrincipalV1(
                tenant_id=principal.tenant_id, study_ids=tuple(sorted(principal.study_ids)),
                subject=principal.sub, actor_chain=tuple(principal.actor_chain),
                roles=tuple(sorted(principal.roles)), purpose=principal.purpose,
                capabilities=tuple(sorted(principal.capabilities))),
            "osb", command_store, handler)
        return {"ok": True, "data": result}
    except (PlatformCommandError, OsbCandidateSetError) as error:
        raise _error(error) from error


@router.get("/artifacts/source-draft-stage-receipts/{receipt_version_id}", dependencies=[platform_security],
            summary="Read an immutable native source-draft staging receipt")
def get_source_draft_stage_receipt(receipt_version_id: str, request: Request):
    principal_for, _, _ = _dependencies()
    study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    principal = principal_for("draft:read", study_id)
    try:
        result = NativeSourceDraftStageStore().receipt(
            principal.tenant_id, study_id, receipt_version=receipt_version_id)
        if result is None:
            raise HTTPException(status_code=404, detail="OSB_SOURCE_DRAFT_STAGE_RECEIPT_NOT_FOUND")
        payload = canonical_json(result["payload"]).encode("utf-8")
        return Response(content=payload, media_type=RECEIPT_MEDIA_TYPE,
                        headers={"etag": f'"{result["artifactRef"]["payloadHash"]["value"]}"',
                                 "content-length": str(len(payload))})
    except OsbCandidateSetError as error:
        raise _error(error) from error
