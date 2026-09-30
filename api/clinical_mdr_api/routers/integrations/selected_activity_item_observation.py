"""Additive selected-activity endpoint, sharing the native reader's worker cap."""
import asyncio
import json

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from clinical_mdr_api.models.integrations.selected_activity_item_observation import SelectedActivityItemRequest, SelectedActivityItemResponse
from clinical_mdr_api.routers.integrations.native_item_observation import run_bounded_native_item_observation
from clinical_mdr_api.services.integrations.selected_activity_item_observation import SelectedActivityItemService
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from common.auth.dependencies import platform_security

router = APIRouter()


@router.post("/selected-activity-items/current-observation", dependencies=[platform_security],
             response_model=SelectedActivityItemResponse,
             openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
                 "schema": SelectedActivityItemRequest.model_json_schema()}}}},
             summary="Observe one exact selected native activity path; no semantic or form approval")
async def current_selected_activity_item(request: Request) -> Response:
    try:
        body, count = bytearray(), 0
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                count += 1
                if count > 128 or len(body) + len(chunk) > 8192:
                    raise HTTPException(413, detail="OSB_SELECTED_REQUEST_LIMIT")
                body.extend(chunk)
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        pins = SelectedActivityItemRequest.model_validate(json.loads(body.decode("utf-8"), object_pairs_hook=pairs))
    except (ValueError, UnicodeError, ValidationError, RecursionError) as error:
        raise HTTPException(422, detail="OSB_SELECTED_REQUEST_INVALID") from error
    except TimeoutError as error:
        raise HTTPException(408, detail="OSB_SELECTED_REQUEST_TIMEOUT") from error
    try:
        result = await run_bounded_native_item_observation(SelectedActivityItemService(), pins, request)
        return Response(canonical_json(result), media_type="application/json", headers={"cache-control": "no-store"})
    except NativeItemObservationError as error:
        raise HTTPException(error.status, detail=error.code) from error
