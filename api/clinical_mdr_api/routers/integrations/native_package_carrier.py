"""Bounded, authenticated transport and observation for authored carriers."""

# JSON integer claims must exclude booleans and custom numeric subclasses.
# pylint: disable=unidiomatic-typecheck

import asyncio
import base64
import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandError,
    PlatformCommandPrincipalV1,
)
from clinical_mdr_api.routers.integrations import native_identity
from clinical_mdr_api.routers.integrations.native_item_observation import (
    run_bounded_native_item_observation,
)
from clinical_mdr_api.routers.integrations.native_package_review_context import (
    _invalid_constant,
    _unique_pairs,
)
from clinical_mdr_api.services.integrations.atomic_signed_command import (
    execute_osb_atomic_signed_command,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.native_package_artifact_custody import (
    MAX_CARRIER_ARTIFACT_BYTES,
    decode_artifact_reference_header,
    encode_artifact_reference_header,
    read_carrier_artifact,
    store_carrier_artifact_bytes,
)
from clinical_mdr_api.services.integrations.native_package_carrier_contracts import (
    validate_carrier_contract,
)
from clinical_mdr_api.services.integrations.native_package_carrier_v2 import (
    capture_carrier_native_state,
    carrier_review_context,
    carrier_review_entry,
    carrier_review_page,
)
from common.auth.dependencies import platform_security
from common.auth.user import auth
from common.config import settings

router = APIRouter()
NO_STORE = {"cache-control": "no-store"}


def _principal(study_id):
    if (
        not settings.platform_commands_prototype_enabled
        or settings.deployment_environment.strip().lower() in {"prod", "production"}
    ):
        raise HTTPException(
            503, detail="OSB_PLATFORM_COMMANDS_DISABLED", headers=NO_STORE
        )
    return native_identity._platform_principal("package:release", study_id)


def _command_store():
    return native_identity.platform_command_store


class _CarrierOperation:
    def __init__(self, operation):
        original = auth()
        expiry = getattr(getattr(original, "access_token_claims", None), "exp", None)
        if (
            getattr(original, "authentication_verified", False) is not True
            or type(expiry) is not int
        ):
            raise HTTPException(
                403, detail="OSB_CARRIER_AUTH_REQUIRED", headers=NO_STORE
            )
        self.expiry, self.operation = expiry, operation
        self.deadline = time.monotonic() + 30
        self.cancellation = None

    def request_budget(self):
        budget = min(self.expiry - time.time(), self.deadline - time.monotonic())
        if budget <= 0:
            raise HTTPException(
                408, detail="OSB_CARRIER_DEADLINE_EXCEEDED", headers=NO_STORE
            )
        return budget

    def check_active(self):
        self.request_budget()
        if self.cancellation is not None and self.cancellation.is_set():
            raise HTTPException(
                499, detail="OSB_CARRIER_REQUEST_CANCELLED", headers=NO_STORE
            )

    def clock(self):
        self.check_active()
        return datetime.now(UTC)

    def observe(self, value, cancellation):
        self.cancellation = cancellation
        self.check_active()
        result = self.operation(value)
        self.check_active()
        return result


class _DeadlineCommandStore:
    """Check cancellation/expiry inside the existing transaction before commit."""

    def __init__(self, store, reader):
        self.store, self.reader = store, reader

    def serializable(self, command, callback):
        def checked(transaction):
            self.reader.check_active()
            result = callback(transaction)
            self.reader.check_active()
            return result

        return self.store.serializable(command, checked)


async def _bytes(request: Request, reader: _CarrierOperation, maximum: int) -> bytes:
    payload, chunks = bytearray(), 0
    async with asyncio.timeout(reader.request_budget()):
        async for chunk in request.stream():
            chunks += 1
            if chunks > 8192 or len(payload) + len(chunk) > maximum:
                raise HTTPException(
                    413, detail="OSB_CARRIER_REQUEST_LIMIT", headers=NO_STORE
                )
            payload.extend(chunk)
    return bytes(payload)


async def upload_carrier_prerequisite(artifact_kind: str, request: Request) -> Response:
    study_id = str(request.headers.get("x-platform-study-id") or "").strip()
    principal = _principal(study_id)
    reference = decode_artifact_reference_header(
        request.headers.get("x-artifact-reference", "")
    )
    if reference["kind"] != artifact_kind:
        raise HTTPException(
            422, detail="OSB_CARRIER_ARTIFACT_KIND_MISMATCH", headers=NO_STORE
        )
    expected_hash = request.headers.get("x-content-sha256", "")

    def store(payload):
        with db.transaction:
            result = store_carrier_artifact_bytes(
                artifact_ref=reference,
                tenant_id=principal.tenant_id,
                platform_study_id=study_id,
                bytes_value=payload,
                expected_hash=expected_hash,
            )
            encoded = canonical_json({"ok": True, "data": result}).encode("utf-8")
            reader.check_active()
        return encoded

    reader = _CarrierOperation(store)
    payload = await _bytes(
        request, reader, min(MAX_CARRIER_ARTIFACT_BYTES, reference["byteSize"])
    )
    encoded = await run_bounded_native_item_observation(reader, payload, request)
    return Response(encoded, media_type="application/json", headers=NO_STORE)


@router.get(
    "/artifacts/carrier/{artifact_id}/{artifact_version_id}",
    dependencies=[platform_security],
)
async def get_carrier_artifact(
    artifact_id: str, artifact_version_id: str, request: Request
) -> Response:
    try:
        study_id = str(request.headers.get("x-platform-study-id") or "").strip()
        principal = _principal(study_id)
        reference = decode_artifact_reference_header(
            request.headers.get("x-artifact-reference", "")
        )
        if (reference["artifactId"], reference["artifactVersionId"]) != (
            artifact_id,
            artifact_version_id,
        ):
            raise HTTPException(
                422, detail="OSB_CARRIER_ARTIFACT_IDENTITY_MISMATCH", headers=NO_STORE
            )

        def read(_value):
            retained = read_carrier_artifact(
                artifact_ref=reference,
                tenant_id=principal.tenant_id,
                platform_study_id=study_id,
            )
            return retained["bytes"], retained["artifactRef"]

        result, retained_reference = await run_bounded_native_item_observation(
            _CarrierOperation(read), None, request
        )
        return Response(
            result,
            media_type=reference["payloadHash"]["mediaType"],
            headers={
                **NO_STORE,
                "etag": f'"{retained_reference["payloadHash"]["value"]}"',
                "x-artifact-reference": encode_artifact_reference_header(
                    retained_reference
                ),
            },
        )
    except OsbCandidateSetError as error:
        raise HTTPException(
            error.status_code,
            detail={"code": error.code, "message": str(error)},
            headers=NO_STORE,
        ) from error
    except HTTPException as error:
        error.headers = {**(error.headers or {}), **NO_STORE}
        raise
    except TimeoutError as error:
        raise HTTPException(
            408, detail="OSB_CARRIER_REQUEST_TIMEOUT", headers=NO_STORE
        ) from error


@router.post("/commands/carrier-native-state", dependencies=[platform_security])
async def execute_carrier_native_state_command(request: Request) -> Response:
    try:
        study_id = str(request.headers.get("x-platform-study-id") or "").strip()
        principal = _principal(study_id)

        def execute(body: dict[str, Any]):
            if (
                body.get("action") != "osb.carrier-native-state.capture"
                or body.get("targetCapability") != "package:release"
                or body.get("platformStudyId") != study_id
            ):
                raise HTTPException(
                    422,
                    detail="OSB_CARRIER_CAPTURE_COMMAND_UNSUPPORTED",
                    headers=NO_STORE,
                )

            def handler(_transaction):
                observed = capture_carrier_native_state(
                    tenant_id=principal.tenant_id,
                    platform_study_id=study_id,
                    input_payload=body.get("inputPayload"),
                    expected_target_state=body.get("expectedTargetState"),
                )
                output = {
                    key: observed[key]
                    for key in ("nativeStateArtifact", "canonicalDefinitionArtifact")
                }
                return {
                    "status": "succeeded",
                    "targetIdentity": observed["nativeStudyId"],
                    "targetVersion": observed["nativeVersion"],
                    "targetState": {"nativeStateHash": observed["nativeStateHash"]},
                    "consumedArtifacts": [
                        body["inputPayload"]["carrierArtifact"],
                        body["inputPayload"]["sourceBasis"]["authorityArtifact"],
                    ],
                    "producedArtifacts": list(output.values()),
                    "conservationCounts": {
                        "nativeStates": 1,
                        "canonicalDefinitions": 1,
                        "dropped": 0,
                    },
                    "effectPayload": output,
                }

            result = execute_osb_atomic_signed_command(
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
                _DeadlineCommandStore(_command_store(), reader),
                handler,
                clock=reader.clock,
            )
            return canonical_json({"ok": True, "data": result}).encode("utf-8")

        reader = _CarrierOperation(execute)
        encoded = await _bytes(request, reader, 128 * 1024)
        body = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_invalid_constant,
        )
        if not isinstance(body, dict):
            raise ValueError("Command must be an object.")
        canonical_json(body)
        result = await run_bounded_native_item_observation(reader, body, request)
        return Response(result, media_type="application/json", headers=NO_STORE)
    except (OsbCandidateSetError, PlatformCommandError) as error:
        raise HTTPException(
            error.status_code,
            detail={"code": error.code, "message": str(error)},
            headers=NO_STORE,
        ) from error
    except HTTPException as error:
        error.headers = {**(error.headers or {}), **NO_STORE}
        raise
    except (ValueError, UnicodeError, OverflowError, RecursionError) as error:
        raise HTTPException(
            422, detail="OSB_CARRIER_CAPTURE_REQUEST_INVALID", headers=NO_STORE
        ) from error
    except TimeoutError as error:
        raise HTTPException(
            408, detail="OSB_CARRIER_REQUEST_TIMEOUT", headers=NO_STORE
        ) from error


async def _review_read(request: Request, kind: str) -> Response:
    try:
        study_id = str(request.headers.get("x-platform-study-id") or "").strip()
        principal = _principal(study_id)

        def read(body):
            validate_carrier_contract(body, kind + "Request", interface=True)
            arguments = {
                "tenant_id": principal.tenant_id,
                "platform_study_id": study_id,
                "transformation_checkpoint_artifact": body[
                    "transformationCheckpointArtifact"
                ],
                "osb_study_identity": body["osbStudyIdentity"],
            }
            if kind == "entry":
                result = carrier_review_entry(
                    **arguments, section=body["section"], position=body["position"]
                )
                proof = {
                    key: result[key]
                    for key in ("section", "position", "valueHash", "source")
                }
                header = (
                    base64.urlsafe_b64encode(canonical_json(proof).encode("utf-8"))
                    .rstrip(b"=")
                    .decode("ascii")
                )
                if (
                    len(header) > 32768
                    or len(result["bytes"]) > MAX_CARRIER_ARTIFACT_BYTES
                ):
                    raise HTTPException(
                        413,
                        detail="OSB_CARRIER_INSPECTION_RESPONSE_LIMIT",
                        headers=NO_STORE,
                    )
                return result["bytes"], {
                    **NO_STORE,
                    "x-osb-inspection-proof": header,
                    "etag": f'"{result["valueHash"]["value"]}"',
                }
            result = (
                carrier_review_page(
                    **arguments,
                    section=body["section"],
                    offset=body["offset"],
                    limit=body["limit"],
                )
                if kind == "page"
                else carrier_review_context(**arguments)
            )
            encoded = canonical_json({"ok": True, "data": result}).encode("utf-8")
            if len(encoded) > 262144:
                raise HTTPException(
                    413,
                    detail="OSB_CARRIER_INSPECTION_RESPONSE_LIMIT",
                    headers=NO_STORE,
                )
            return encoded, NO_STORE

        reader = _CarrierOperation(read)
        encoded = await _bytes(request, reader, 128 * 1024)
        body = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_invalid_constant,
        )
        if not isinstance(body, dict):
            raise ValueError("Object required.")
        result, headers = await run_bounded_native_item_observation(
            reader, body, request
        )
        return Response(result, media_type="application/json", headers=headers)
    except OsbCandidateSetError as error:
        detail = {"code": error.code, "message": str(error)}
        if error.code == "OSB_CARRIER_INSPECTION_ENTRY_TOO_LARGE" and hasattr(
            error, "details"
        ):
            validate_carrier_contract(error.details, "entryProof", interface=True)
            detail["entry"] = error.details
        raise HTTPException(
            error.status_code, detail=detail, headers=NO_STORE
        ) from error
    except HTTPException as error:
        error.headers = {**(error.headers or {}), **NO_STORE}
        raise
    except (ValueError, UnicodeError, OverflowError, RecursionError) as error:
        raise HTTPException(
            422, detail="OSB_CARRIER_REVIEW_REQUEST_INVALID", headers=NO_STORE
        ) from error
    except TimeoutError as error:
        raise HTTPException(
            408, detail="OSB_CARRIER_REQUEST_TIMEOUT", headers=NO_STORE
        ) from error


@router.post(
    "/native-packages-v2/carrier-review-context", dependencies=[platform_security]
)
async def get_carrier_review_context(request: Request) -> Response:
    return await _review_read(request, "context")


@router.post(
    "/native-packages-v2/carrier-review-page", dependencies=[platform_security]
)
async def get_carrier_review_page(request: Request) -> Response:
    return await _review_read(request, "page")


@router.post(
    "/native-packages-v2/carrier-review-entry", dependencies=[platform_security]
)
async def get_carrier_review_entry(request: Request) -> Response:
    return await _review_read(request, "entry")
