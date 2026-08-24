import logging
import traceback

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

_MAX_LOGGED_FRAMES = 12


def _safe_frames(exc: BaseException) -> str:
    """Where the failure happened, with nothing about WHAT was being processed.

    THIS CLASS PROMISED A TRACEBACK AND NEVER LOGGED ONE. An unhandled exception
    produced a rejection id, an error code, and no way whatsoever to find out
    what had failed: not in the log, not in the response, not on the span. Every
    such failure had to be reproduced by hand before it could even be located.

    The reason the traceback was left out is real, though, so this does not just
    switch `exc_info` on: an exception MESSAGE can carry request data, and this
    service handles regulated clinical content under an explicit
    observability-privacy discipline. Frame locations cannot - a file, a line and
    a function name describe the code, never the payload. So the location is
    logged and the message is not, which is the part that was actually needed to
    diagnose anything.
    """

    frames = traceback.extract_tb(exc.__traceback__)
    # OUR frames, plus the innermost one wherever it landed. A stack through this
    # service is mostly framework and driver code; keeping all of it truncates the
    # log line before it reaches the application frames, which are the ones that
    # say where the bug is.
    own = [frame for frame in frames if "/site-packages/" not in frame.filename]
    selected = (own or frames)[-_MAX_LOGGED_FRAMES:]
    if frames and frames[-1] not in selected:
        selected = [*selected, frames[-1]]
    # Innermost first: the log line is length-bounded, and the frame that raised
    # is the one worth keeping when the tail gets cut.
    rendered = " <- ".join(
        f"{frame.filename}:{frame.lineno}:{frame.name}" for frame in reversed(selected)
    )
    return f"{type(exc).__name__} @ {rendered}" if rendered else type(exc).__name__


class ExceptionTracebackMiddleware:
    """Middleware for unhandled exceptions: sets tracing attributes, logs the failure location, returns error response"""

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
                "%s %s failed errorCode=%s rejectionId=%s frames=%s",
                scope.get("method"),
                scope.get("path"),
                safe["errorCode"],
                safe["rejectionId"],
                _safe_frames(exc),
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
