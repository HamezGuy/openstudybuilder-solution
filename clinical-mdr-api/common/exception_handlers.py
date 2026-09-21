"""Shared error logging, tracing and serialization for the API applications."""

from collections.abc import Mapping

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from common.exceptions import MDRApiBaseException
from common.logger import log_exception
from common.models.error import ErrorResponse
from common.telemetry.traceback_middleware import ExceptionTracebackMiddleware


async def _rejection(
    request: Request,
    exception: Exception,
    status_code: int,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Log the failure once, stamp its rejection id on the span, serialise it."""

    safe = await log_exception(request, exception)

    ExceptionTracebackMiddleware.add_traceback_attributes(
        exception, safe["rejectionId"]
    )

    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(ErrorResponse(request, exception)),
        headers=headers,
    )


async def http_exception_handler(
    request: Request, exception: HTTPException
) -> JSONResponse:
    """Returns the HTTP error code carried by the exception."""

    return await _rejection(
        request, exception, exception.status_code, exception.headers
    )


async def mdr_api_exception_handler(
    request: Request, exception: MDRApiBaseException
) -> JSONResponse:
    """Returns the HTTP error code carried by the exception."""

    return await _rejection(
        request, exception, exception.status_code, exception.headers
    )


async def validation_error_handler(
    request: Request, exception: ValidationError
) -> JSONResponse:
    """Returns `400 Bad Request` when Pydantic rejects a payload or parameter."""

    return await _rejection(request, exception, status.HTTP_400_BAD_REQUEST)


async def request_validation_error_handler(
    request: Request, exception: RequestValidationError
) -> JSONResponse:
    """Returns `400 Bad Request` when FastAPI rejects a body, query or path."""

    return await _rejection(request, exception, status.HTTP_400_BAD_REQUEST)


async def value_error_handler(request: Request, exception: ValueError) -> JSONResponse:
    """Returns `400 Bad Request` in case ValueError is raised."""

    return await _rejection(request, exception, status.HTTP_400_BAD_REQUEST)


def register_exception_handlers(
    app: FastAPI, *, http_exception: bool = True, value_error: bool = True
) -> None:
    """Register the shared handlers on `app`.

    Every application maps MDRApiBaseException, pydantic ValidationError and
    RequestValidationError. The main API registers all five handlers; the
    consumer API never turned ValueError into 400 and the extensions API never
    claimed HTTPException, so each keeps its flag off and its behaviour.
    """

    if http_exception:
        app.exception_handler(HTTPException)(http_exception_handler)
    app.exception_handler(MDRApiBaseException)(mdr_api_exception_handler)
    app.exception_handler(ValidationError)(validation_error_handler)
    app.exception_handler(RequestValidationError)(request_validation_error_handler)
    if value_error:
        app.exception_handler(ValueError)(value_error_handler)
