"""Actual route/worker saturation, timeout and cancellation semantics."""
import asyncio
import threading
import time
from unittest.mock import patch

from fastapi import HTTPException

from clinical_mdr_api.routers.integrations import native_item_observation as route
from clinical_mdr_api.tests.unit.services.test_native_item_observation import fixture


class Request:
    def __init__(self, pins): self.body = pins.model_dump_json().encode()
    async def stream(self): yield self.body


def stalled():
    f = fixture()
    f.service.clock, f.service.monotonic = time.time, time.monotonic
    f.auth.access_token_claims.exp = int(time.time()) + 2
    f.auth.access_token_claims.iat = int(time.time()) - 1
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = f.repository.scope
    def scope(params, timeout):
        entered.set()
        assert release.wait(5), "Fixture must release pending native I/O"
        return original(params, timeout)
    f.repository.scope = scope
    original_observe = f.service.observe
    def observe(pins, cancellation=None):
        try: return original_observe(pins, cancellation)
        finally: finished.set()
    f.service.observe = observe
    return f, entered, release, finished


def test_expired_abandoned_workers_are_bounded_until_real_completion_and_recover():
    async def run():
        blocked = [stalled() for _ in range(4)]
        control = fixture()
        services = [value[0].service for value in blocked]
        tasks = []
        with patch.object(route, "NativeItemObservationService", side_effect=[*services, control.service, control.service]):
            try:
                tasks = [asyncio.create_task(route.current_native_item(Request(value[0].request))) for value in blocked]
                for _, entered, _, _ in blocked:
                    assert await asyncio.to_thread(entered.wait, 1)
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
                assert all(isinstance(value, HTTPException) and value.detail == "OSB_ITEM_READ_TIMEOUT" for value in results)
                # Timed-out HTTP requests have not freed the still-blocked native workers.
                try:
                    await route.current_native_item(Request(control.request))
                    assert False, "No new work may accumulate behind stalled reads"
                except HTTPException as error:
                    assert error.status_code == 503 and error.detail == "OSB_ITEM_READ_CAPACITY_UNAVAILABLE"
            finally:
                for _, _, release, _ in blocked: release.set()
                for _, _, _, finished in blocked:
                    assert await asyncio.to_thread(finished.wait, 2)
                await asyncio.sleep(0.05)
            response = await route.current_native_item(Request(control.request))
            assert response.status_code == 200
        assert all(value[0].repository.calls == ["scope"] for value in blocked)
    asyncio.run(run())


def test_request_cancellation_prevents_more_native_queries_after_pending_read_returns():
    async def run():
        f, entered, release, finished = stalled()
        with patch.object(route, "NativeItemObservationService", return_value=f.service):
            task = asyncio.create_task(route.current_native_item(Request(f.request)))
            assert await asyncio.to_thread(entered.wait, 1)
            try:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
                else: assert False, "Actual request cancellation must propagate"
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
                await asyncio.sleep(0.05)
        assert f.repository.calls == ["scope"]
    asyncio.run(run())
