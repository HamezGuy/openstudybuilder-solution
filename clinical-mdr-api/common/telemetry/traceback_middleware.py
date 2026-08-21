import logging

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from opencensus.trace import execution_context
from opencensus.trace.attributes_helper import COMMON_ATTRIBUTES
from starlette.types import ASGIApp, Receive, Scope, Send

from common.exceptions import InternalServerError
from common.models.error import ErrorResponse
from common.observability_privacy import safe_error

log = logging.getLogger(__name__)


class ExceptionTracebackMiddleware:
    """Middleware for unhandled exceptions: sets tracing attributes, logs exception traceback, returns error response"""

    def __init__(
        self,
        app: ASGIApp,
    ) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
            return
        except Exception as exc:  # pylint: disable=broad-except
            safe = safe_error(exc)
            self.add_traceback_attributes(exc, safe["rejectionId"])
            log.error(
                "%s %s failed errorCode=%s rejectionId=%s",
                scope.get("method"),
                scope.get("path"),
                safe["errorCode"],
                safe["rejectionId"],
            )

            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=jsonable_encoder(
                    ErrorResponse(Request(scope), InternalServerError())
                ),
            )

            await response(scope, receive, send)
            return

    @staticmethod
    def add_traceback_attributes(exception, rejection_id: str):
        """Adds non-sensitive failure metadata to the active tracing span."""

        if span := execution_context.get_current_span():
            span.add_attribute(
                COMMON_ATTRIBUTES["ERROR_NAME"], exception.__class__.__name__
            )

            span.add_attribute("error.rejection_id", rejection_id)
