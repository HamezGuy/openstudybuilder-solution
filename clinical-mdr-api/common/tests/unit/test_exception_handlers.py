from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from common.exception_handlers import register_exception_handlers
from common.exceptions import BusinessLogicException
from common.telemetry.traceback_middleware import ExceptionTracebackMiddleware


class Payload(BaseModel):
    amount: int


def invalid_payload():
    try:
        Payload(amount="invalid")
    except ValidationError as exception:
        return exception
    raise AssertionError("Invalid payload unexpectedly accepted")


@pytest.mark.parametrize(
    "factory,status_code",
    [
        (lambda: HTTPException(409, "Conflict", headers={"Retry-After": "5"}), 409),
        (lambda: BusinessLogicException(msg="Invalid study transition"), 400),
        (lambda: ValueError("Invalid value"), 400),
        (invalid_payload, 400),
    ],
)
def test_error_responses_preserve_status_metadata_and_one_rejection_id(
    factory, status_code
):
    app = FastAPI()
    register_exception_handlers(app)
    exception = factory()

    @app.get("/failure")
    def failure():
        raise exception

    with patch(
        "common.exception_handlers.log_exception",
        AsyncMock(return_value={"rejectionId": "unit-rejection"}),
    ) as logged, patch.object(
        ExceptionTracebackMiddleware, "add_traceback_attributes"
    ) as traced:
        response = TestClient(app).get("/failure")

    assert response.status_code == status_code
    assert response.json()["type"] == type(exception).__name__
    assert response.json()["method"] == "GET"
    assert response.json()["path"] == "http://testserver/failure"
    if isinstance(exception, HTTPException):
        assert response.headers["Retry-After"] == "5"
    if isinstance(exception, ValidationError):
        assert response.json()["details"][0]["field"] == ["amount"]
    logged.assert_awaited_once()
    traced.assert_called_once_with(exception, "unit-rejection")


@pytest.mark.parametrize(
    "profile",
    [{}, {"value_error": False}, {"http_exception": False}],
    ids=["main", "consumer", "extensions"],
)
def test_each_application_keeps_request_validation_at_400(profile):
    app = FastAPI()
    register_exception_handlers(app, **profile)

    @app.get("/amount")
    def amount(value: int):
        return {"value": value}

    with patch(
        "common.exception_handlers.log_exception",
        AsyncMock(return_value={"rejectionId": "query-rejection"}),
    ) as logged:
        response = TestClient(app).get("/amount", params={"value": "invalid"})

    assert response.status_code == 400
    assert response.json()["details"][0]["field"] == ["query", "value"]
    logged.assert_awaited_once()


def test_consumer_value_errors_and_extension_http_errors_keep_their_original_handlers():
    consumer = FastAPI()
    extensions = FastAPI()
    register_exception_handlers(consumer, value_error=False)
    register_exception_handlers(extensions, http_exception=False)

    @consumer.get("/failure")
    def consumer_failure():
        raise ValueError("Internal failure")

    @extensions.get("/failure")
    def extension_failure():
        raise HTTPException(403, "Forbidden")

    assert (
        TestClient(consumer, raise_server_exceptions=False).get("/failure").status_code
        == 500
    )
    response = TestClient(extensions).get("/failure")
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
