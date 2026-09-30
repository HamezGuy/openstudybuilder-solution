"""Machine-authenticated, bounded read of one exact native library Item."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore, Event

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError

from clinical_mdr_api.models.integrations.native_item_observation import NativeItemObservationRequest, NativeItemObservationResponse
from clinical_mdr_api.services.integrations.native_item_observation import (
    NativeItemObservationError, NativeItemObservationService,
)
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from common.auth.dependencies import platform_security

router = APIRouter()
# Slots remain occupied until real worker completion, including after an HTTP
# timeout. The synchronous driver has no supported cross-thread cancel API.
# A stalled native backend therefore exhausts this bounded reader, never an
# unbounded abandoned-work queue or the application's shared worker pool.
_workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="osb-item-observer")
_slots = BoundedSemaphore(4)


async def _bounded_observation(service, pins, request):
    budget = service.request_budget()
    if not _slots.acquire(blocking=False):
        raise HTTPException(503, detail="OSB_ITEM_READ_CAPACITY_UNAVAILABLE")
    cancellation, context = Event(), copy_context()
    def work():
        try:
            return context.run(service.observe, pins, cancellation)
        finally:
            _slots.release()
    try:
        future = asyncio.get_running_loop().run_in_executor(_workers, work)
    except BaseException:
        _slots.release()
        raise
    # Retrieve an abandoned worker's eventual exception without awaiting it.
    future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    deadline = asyncio.get_running_loop().time() + budget
    try:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError()
            done, _ = await asyncio.wait([future], timeout=min(0.05, remaining))
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError()
            if future in done:
                return future.result()
            disconnected = getattr(request, "is_disconnected", None)
            if callable(disconnected) and await disconnected():
                raise HTTPException(499, detail="OSB_ITEM_READ_CANCELLED")
    except TimeoutError as error:
        cancellation.set()
        raise HTTPException(408, detail="OSB_ITEM_READ_TIMEOUT") from error
    except BaseException:
        cancellation.set()
        raise


# Public composition seam: sibling observations share this exact capacity and
# original-credential await boundary. Existing library behavior is unchanged.
run_bounded_native_item_observation = _bounded_observation


@router.post("/native-items/current-observation", dependencies=[platform_security],
             response_model=NativeItemObservationResponse,
             openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
                 "schema": NativeItemObservationRequest.model_json_schema()}}}},
             summary="Observe exact current scalar library Item; does not verify study selection")
async def current_native_item(request: Request) -> Response:
    try:
        payload = bytearray()
        chunks = 0
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                chunks += 1
                if chunks > 128 or len(payload) + len(chunk) > 8192:
                    raise HTTPException(413, detail="OSB_ITEM_REQUEST_LIMIT")
                payload.extend(chunk)
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
        pins = NativeItemObservationRequest.model_validate(value)
    except (ValueError, UnicodeError, ValidationError, RecursionError) as error:
        raise HTTPException(422, detail="OSB_ITEM_REQUEST_INVALID") from error
    except TimeoutError as error:
        raise HTTPException(408, detail="OSB_ITEM_REQUEST_TIMEOUT") from error
    try:
        result = await _bounded_observation(NativeItemObservationService(), pins, request)
        return Response(canonical_json(result), media_type="application/json",
                        headers={"cache-control": "no-store"})
    except NativeItemObservationError as error:
        raise HTTPException(error.status, detail=error.code) from error
