"""Read the exact locked native content a specialist is asked to review."""

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request, Response

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.routers.integrations.native_item_observation import (
    run_bounded_native_item_observation,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.native_package_v2 import review_context
from common.auth.dependencies import platform_security
from common.auth.user import auth
from common.config import settings

router = APIRouter()
MAX_REVIEW_CONTEXT_BYTES = 128 * 1024 * 1024
MAX_REVIEW_CONTEXT_CHUNKS = 8192
REVIEW_CONTEXT_READ_SECONDS = 30
NO_STORE = {"cache-control": "no-store"}


def _principal_for(study_id):
    # The parent imports this companion before defining its principal helper.
    from clinical_mdr_api.routers.integrations.native_identity import (
        _platform_principal,
    )

    return _platform_principal("package:release", study_id)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Non-finite JSON number.")


class _ReviewContextRead:
    """Use the existing bounded native-reader pool and original token lifetime."""

    def __init__(self, tenant_id, study_id):
        original = auth()
        claims = getattr(original, "access_token_claims", None)
        expiry = getattr(claims, "exp", None)
        if (
            getattr(original, "authentication_verified", False) is not True
            or not isinstance(expiry, int)
            or isinstance(expiry, bool)
        ):
            raise HTTPException(403, detail="OSB_REVIEW_CONTEXT_AUTH_REQUIRED")
        self.expiry = expiry
        self.tenant_id, self.study_id = tenant_id, study_id

    def request_budget(self):
        remaining = min(REVIEW_CONTEXT_READ_SECONDS, self.expiry - time.time())
        if remaining <= 0:
            raise HTTPException(403, detail="OSB_REVIEW_CONTEXT_AUTH_EXPIRED")
        return remaining

    def observe(self, value, cancellation):
        self.request_budget()
        if cancellation.is_set():
            raise HTTPException(499, detail="OSB_REVIEW_CONTEXT_READ_CANCELLED")
        result = review_context(
            tenant_id=self.tenant_id,
            platform_study_id=self.study_id,
            transformation_checkpoint_artifact=value[
                "transformationCheckpointArtifact"
            ],
            transformation_checkpoint=value["transformationCheckpoint"],
        )
        payload = canonical_json({"ok": True, "data": result}).encode("utf-8")
        if len(payload) > MAX_REVIEW_CONTEXT_BYTES:
            raise HTTPException(413, detail="OSB_REVIEW_CONTEXT_RESPONSE_LIMIT")
        self.request_budget()
        if cancellation.is_set():
            raise HTTPException(499, detail="OSB_REVIEW_CONTEXT_READ_CANCELLED")
        return payload


@router.post(
    "/native-packages-v2/review-context",
    dependencies=[platform_security],
    summary="Read exact locked native study and capture content without approval",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "transformationCheckpointArtifact",
                            "transformationCheckpoint",
                        ],
                        "properties": {
                            "transformationCheckpointArtifact": {"type": "object"},
                            "transformationCheckpoint": {"type": "object"},
                        },
                    }
                }
            },
        }
    },
)
async def read_native_package_review_context(request: Request) -> Response:
    try:
        if (
            not settings.platform_commands_prototype_enabled
            or settings.deployment_environment.strip().lower() in {"prod", "production"}
        ):
            raise HTTPException(503, detail="OSB_PLATFORM_COMMANDS_DISABLED")
        study_id = str(request.headers.get("x-platform-study-id") or "").strip()
        principal = _principal_for(study_id)
        reader = _ReviewContextRead(principal.tenant_id, study_id)
        payload, chunks = bytearray(), 0
        async with asyncio.timeout(REVIEW_CONTEXT_READ_SECONDS):
            async for chunk in request.stream():
                chunks += 1
                if (
                    chunks > MAX_REVIEW_CONTEXT_CHUNKS
                    or len(payload) + len(chunk) > MAX_REVIEW_CONTEXT_BYTES
                ):
                    raise HTTPException(413, detail="OSB_REVIEW_CONTEXT_REQUEST_LIMIT")
                payload.extend(chunk)
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_invalid_constant,
        )
        if (
            not isinstance(value, dict)
            or set(value)
            != {"transformationCheckpointArtifact", "transformationCheckpoint"}
            or any(not isinstance(item, dict) for item in value.values())
        ):
            raise ValueError("Only the exact checkpoint and artifact are accepted.")
        # Validate all JSON scalars before any native read, including surrogates
        # and overflowed numbers. No await occurs within a native transaction.
        canonical_json(value)
        result = await run_bounded_native_item_observation(
            reader,
            value,
            request,
        )
        return Response(
            result,
            media_type="application/json",
            headers=NO_STORE,
        )
    except OsbCandidateSetError as error:
        raise HTTPException(
            error.status_code,
            detail={"code": error.code, "message": str(error)},
            headers=NO_STORE,
        ) from error
    except (ValueError, UnicodeError, RecursionError, OverflowError) as error:
        raise HTTPException(
            422, detail="OSB_REVIEW_CONTEXT_REQUEST_INVALID", headers=NO_STORE
        ) from error
    except TimeoutError as error:
        raise HTTPException(
            408, detail="OSB_REVIEW_CONTEXT_REQUEST_TIMEOUT", headers=NO_STORE
        ) from error
    except HTTPException as error:
        if isinstance(error.detail, str) and error.detail.startswith("OSB_ITEM_READ_"):
            error.detail = error.detail.replace(
                "OSB_ITEM_READ_", "OSB_REVIEW_CONTEXT_READ_", 1
            )
        error.headers = {**(error.headers or {}), **NO_STORE}
        raise
