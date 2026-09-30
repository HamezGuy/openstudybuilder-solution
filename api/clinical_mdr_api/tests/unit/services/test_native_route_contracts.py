"""Contracts every native observation route states once and honours three times.

The governed item association, native item observation and selected activity
item routes each transcribed the same two tests: the platform security
dependency is the real machine dependency (no auth-disabled fallback when no
token is sent), and a pending native read waits no longer than the credential
that authorised it. Each behaviour lives here once and runs for all three.
"""

import asyncio
import threading
import time
from dataclasses import dataclass
from types import ModuleType
from typing import Callable
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from clinical_mdr_api.routers.integrations import (
    governed_item_association,
    native_item_observation,
    selected_activity_item_observation,
)
from clinical_mdr_api.tests.unit.services.test_governed_item_association import (
    association_fixture,
)
from clinical_mdr_api.tests.unit.services.test_native_item_observation import (
    fixture as native_fixture,
)
from clinical_mdr_api.tests.unit.services.test_selected_activity_item_observation import (
    selected_fixture,
)


class Request:
    """The request a route reads: the pinned model's JSON, streamed once."""

    def __init__(self, pins):
        self.body = pins.model_dump_json().encode()

    async def stream(self):
        yield self.body


@dataclass(frozen=True)
class RouteCase:
    name: str
    module: (
        ModuleType  # router module: `router` and the patched service class live here
    )
    path: str  # route path under a bare app that includes only `module.router`
    service: str  # service class name patched on the module
    fixture: Callable  # authored fixture with .service, .auth, .repository and the request model
    io: str  # repository method that performs the credential-bound native I/O
    call: Callable  # (module, fixture) -> the route coroutine under test

    def __str__(self):
        return self.name


ROUTES = [
    RouteCase(
        "governed-item-association",
        governed_item_association,
        "/proposal/objects/object/item-associations",
        "GovernedItemAssociationService",
        association_fixture,
        "review_custody",
        lambda route, f: route.review_item_association(
            Request(f.review),
            f.review.selector.proposalHash,
            f.review.selector.proposalObjectId,
        ),
    ),
    RouteCase(
        "native-item-observation",
        native_item_observation,
        "/native-items/current-observation",
        "NativeItemObservationService",
        native_fixture,
        "scope",
        lambda route, f: route.current_native_item(Request(f.request)),
    ),
    RouteCase(
        "selected-activity-item",
        selected_activity_item_observation,
        "/selected-activity-items/current-observation",
        "SelectedActivityItemService",
        selected_fixture,
        "path_projection",
        lambda route, f: route.current_selected_activity_item(Request(f.request)),
    ),
]


@pytest.mark.parametrize("case", ROUTES, ids=str)
def test_real_machine_dependency_has_no_auth_disabled_fallback(case):
    app = FastAPI()
    app.include_router(case.module.router)
    assert TestClient(app).post(case.path, json={}).status_code in {401, 403}


@pytest.mark.parametrize("case", ROUTES, ids=str)
def test_native_io_wait_is_bounded_by_the_original_credential(case):
    async def run():
        f = case.fixture()
        f.service.clock, f.service.monotonic = time.time, time.monotonic
        f.auth.access_token_claims.exp = int(time.time()) + 2
        f.auth.access_token_claims.iat = int(time.time()) - 1
        entered, release, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        original = getattr(f.repository, case.io)

        def pending(params, timeout):
            entered.set()
            try:
                assert release.wait(5), "Fixture must release pending native I/O"
                return original(params, timeout)
            finally:
                finished.set()

        setattr(f.repository, case.io, pending)
        with patch.object(case.module, case.service, return_value=f.service):
            task = asyncio.create_task(case.call(case.module, f))
            assert await asyncio.to_thread(entered.wait, 1)
            try:
                await asyncio.sleep(
                    max(0, f.auth.access_token_claims.exp - time.time()) + 0.2
                )
                assert (
                    task.done()
                ), "A pending native read must not prolong the original authority"
                with pytest.raises(HTTPException) as error:
                    await task
                assert error.value.detail == "OSB_ITEM_READ_TIMEOUT"
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
                await asyncio.sleep(0.05)
        assert getattr(f.repository, "writes", 0) == 0

    asyncio.run(run())
