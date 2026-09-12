"""Registered native routes with authored token provider; no IdP/crypto claim."""
import asyncio
import threading
import time
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from common.auth.dependencies import validate_token
from clinical_mdr_api.routers.integrations import governed_item_association as route
from clinical_mdr_api.tests.unit.services.test_governed_item_association import association_fixture, current_request
from clinical_mdr_api.tests.unit.services.test_native_item_observation_bounds import Request
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationService, NativeItemObservationError


def client():
    from clinical_mdr_api.routers.integrations.proposal_review import router
    app = FastAPI()
    app.include_router(router, prefix="/integrations/proposal-reviews")
    async def verified_provider(): return None
    app.dependency_overrides[validate_token] = verified_provider
    return TestClient(app)


def path(f):
    return f"/integrations/proposal-reviews/{f.review.selector.proposalHash}/objects/{f.review.selector.proposalObjectId}/item-associations"


def test_actual_registered_review_and_current_reader_return_only_safe_pins():
    f = association_fixture()
    with patch.object(route, "GovernedItemAssociationService", return_value=f.service):
        response = client().post(path(f), json=f.review.model_dump())
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["assurance"] == "native-session-review" and result["clinicalApprovalVerified"] is False
        assert response.headers["cache-control"] == "no-store"
        assert "session-1" not in response.text and "sourceFactRefs" not in response.text and "reviewedAt" not in response.text
        f.service.review = False
        current = client().post(path(f)+"/current-observation", json=current_request(f,result).model_dump())
        assert current.status_code == 200 and current.json()["associationHash"] == result["associationHash"]


def test_body_cannot_change_purpose_action_authority_or_path():
    f = association_fixture()
    with patch.object(route, "GovernedItemAssociationService", return_value=f.service):
        c = client()
        for extra in [{"allowed_purposes":["anything"]}, {"capabilities":["governed-item-association:review"]}, {"review":False}]:
            assert c.post(path(f), json={**f.review.model_dump(), **extra}).status_code == 422
        assert c.post(path(f).replace(f.review.selector.proposalHash,"0"*64), json=f.review.model_dump()).status_code == 422
        assert c.post(path(f), content=b"x"*16385).status_code == 413
        f.auth.access_token_claims.oid = "not-the-authenticated-human"
        assert c.post(path(f), json=f.review.model_dump()).status_code == 403
    assert f.repository.writes == 0


def test_real_machine_dependency_has_no_auth_disabled_fallback():
    app = FastAPI()
    app.include_router(route.router)
    assert TestClient(app).post("/proposal/objects/object/item-associations",json={}).status_code in {401,403}


def test_existing_library_default_does_not_accept_association_interactive_purpose():
    f = association_fixture()
    from clinical_mdr_api.models.integrations.native_item_observation import NativeItemObservationRequest
    pins = NativeItemObservationRequest.model_validate({**{k:v for k,v in f.request.model_dump().items() if k in NativeItemObservationRequest.model_fields},
        "contractVersion":"OsbNativeItemObservationRequestV1@1.0.0", "scope":"library-item"})
    service = NativeItemObservationService(f.repository, lambda:f.auth, lambda:f.now[0], lambda:f.now[0])
    import pytest
    with pytest.raises(NativeItemObservationError, match="SCOPE_DENIED"): service.observe(pins)


def test_pending_original_review_custody_respects_original_expiry_and_never_appends():
    async def run():
        f = association_fixture()
        f.service.clock, f.service.monotonic = time.time, time.monotonic
        f.auth.access_token_claims.exp = int(time.time())+2
        f.auth.access_token_claims.iat = int(time.time())-1
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = f.repository.review_custody
        def pending(p, timeout):
            entered.set()
            try:
                assert release.wait(5)
                return original(p,timeout)
            finally: finished.set()
        f.repository.review_custody = pending
        with patch.object(route,"GovernedItemAssociationService",return_value=f.service):
            task=asyncio.create_task(route.review_item_association(Request(f.review), f.review.selector.proposalHash, f.review.selector.proposalObjectId))
            assert await asyncio.to_thread(entered.wait,1)
            try:
                await asyncio.sleep(max(0,f.auth.access_token_claims.exp-time.time())+0.2)
                assert task.done()
                try: await task
                except HTTPException as error: assert error.detail=="OSB_ITEM_READ_TIMEOUT"
                else: assert False
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait,1)
        assert f.repository.writes==0
    asyncio.run(run())
