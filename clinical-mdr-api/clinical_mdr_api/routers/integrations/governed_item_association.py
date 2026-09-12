"""Bounded human association-review and current native observation endpoints."""
import asyncio
import json
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from common.auth.dependencies import platform_security
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.models.integrations.governed_item_association import GovernedItemAssociationRead, GovernedItemAssociationReview, GovernedItemAssociationResponse
from clinical_mdr_api.services.integrations.governed_item_association import GovernedItemAssociationService
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError
from clinical_mdr_api.routers.integrations.native_item_observation import run_bounded_native_item_observation

router = APIRouter()


async def perform(request, proposal_hash, proposal_object_id, *, review):
    try:
        payload, chunks = bytearray(), 0
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                chunks += 1
                if chunks > 128 or len(payload) + len(chunk) > 16384:
                    raise HTTPException(413, detail="OSB_ASSOCIATION_REQUEST_LIMIT")
                payload.extend(chunk)
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result: raise ValueError("duplicate key")
                result[key] = value
            return result
        model = GovernedItemAssociationReview if review else GovernedItemAssociationRead
        pins = model.model_validate(json.loads(payload.decode("utf-8"), object_pairs_hook=pairs))
        if pins.selector.proposalHash != proposal_hash or pins.selector.proposalObjectId != proposal_object_id:
            raise ValueError("path pins differ")
    except (ValueError, UnicodeError, ValidationError, RecursionError) as error:
        raise HTTPException(422, detail="OSB_ASSOCIATION_REQUEST_INVALID") from error
    except TimeoutError as error:
        raise HTTPException(408, detail="OSB_ASSOCIATION_REQUEST_TIMEOUT") from error
    try:
        result = await run_bounded_native_item_observation(GovernedItemAssociationService(review=review), pins, request)
        return Response(canonical_json(result), status_code=201 if review else 200,
                        media_type="application/json", headers={"cache-control": "no-store"})
    except NativeItemObservationError as error:
        raise HTTPException(error.status, detail=error.code) from error
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, detail="OSB_ASSOCIATION_SOURCE_UNAVAILABLE") from error


@router.post("/{proposal_hash}/objects/{proposal_object_id}/item-associations", dependencies=[platform_security],
    response_model=GovernedItemAssociationResponse, status_code=201,
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": GovernedItemAssociationReview.model_json_schema(ref_template="#/components/schemas/{model}")}}}})
async def review_item_association(request: Request, proposal_hash: str, proposal_object_id: str):
    return await perform(request, proposal_hash, proposal_object_id, review=True)


@router.post("/{proposal_hash}/objects/{proposal_object_id}/item-associations/current-observation", dependencies=[platform_security],
    response_model=GovernedItemAssociationResponse,
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": GovernedItemAssociationRead.model_json_schema(ref_template="#/components/schemas/{model}")}}}})
async def observe_item_association(request: Request, proposal_hash: str, proposal_object_id: str):
    return await perform(request, proposal_hash, proposal_object_id, review=False)
