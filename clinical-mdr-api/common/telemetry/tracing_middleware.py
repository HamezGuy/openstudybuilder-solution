import logging
from fnmatch import fnmatch
from typing import Iterable

from opencensus.log import get_log_attrs
from opencensus.trace import Span, execution_context
from opencensus.trace.attributes_helper import COMMON_ATTRIBUTES
from opencensus.trace.base_exporter import Exporter
from opencensus.trace.base_span import BaseSpan
from opencensus.trace.print_exporter import PrintExporter
from opencensus.trace.propagation.trace_context_http_header_format import (
    TraceContextPropagator,
)
from opencensus.trace.samplers import AlwaysOnSampler, Sampler
from opencensus.trace.span import SpanKind
from opencensus.trace.tracer import Tracer
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.config import settings
from common.telemetry.request_metrics import (
    add_request_metrics_header,
    include_request_metrics,
    init_request_metrics,
)

TRACE_RESPONSE_HEADER_NAME = "traceresponse"

log = logging.getLogger(__name__)


class TracingMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        exclude_paths: Iterable[str] | None = None,
        exclude_hosts: Iterable[str] | None = None,
        exclude_clients: Iterable[str] | None = None,
        sampler: Sampler | None = None,
        exporter: Exporter | None = None,
        propagator=None,
    ) -> None:
        self.app = app
        self.exclude_paths = tuple(exclude_paths or [])
        self.exclude_hosts = set(exclude_hosts or [])
        self.exclude_clients = set(exclude_clients or [])
        self.sampler = sampler or AlwaysOnSampler()
        self.exporter = exporter or PrintExporter()
        self.propagator = propagator or TraceContextPropagator()

        log.info("Initializing TracingMiddleware")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):  # pragma: no cover
            log.debug(
                "Bypassing middleware %s because of request type is %s",
                type(self).__name__,
                scope["type"],
            )
            await self.app(scope, receive, send)
            return

        # Skip tracing if service host is in the exclusion list (mind value also can be host:port)
        headers = Headers(scope=scope)
        host: str | None = headers.get(
            "host", None
        )  # always lowercase, may contain :port

        host_name = host or ""
        if host_name in self.exclude_hosts or host_name.split(":", 1)[0] in self.exclude_hosts:
            log.debug("Bypassing %s for an excluded host", type(self).__name__)
            await self.app(scope, receive, send)
            return

        # Skip tracing if client IPv4 or IPv6 is in the exclusion list
        client_ip = scope.get("client", [None])[0]
        if client_ip and client_ip in self.exclude_clients:
            log.debug("Bypassing %s for an excluded client", type(self).__name__)
            await self.app(scope, receive, send)
            return

        # Skip tracing if URL matches the exclusion list
        path = scope.get("path", "")
        for exclude_path in self.exclude_paths:
            if fnmatch(path, exclude_path):
                log.debug("Bypassing %s for an excluded route", type(self).__name__)
                await self.app(scope, receive, send)
                return

        # noinspection PyTypeChecker
        span_context = self.propagator.from_headers(headers)

        tracer = Tracer(
            span_context=span_context,
            sampler=self.sampler,
            exporter=self.exporter,
            propagator=self.propagator,
        )

        init_request_metrics()

        request_body_size = 0

        async def _receive() -> Message:
            """Saves the first portion of request body and counts the total size of response body"""

            nonlocal request_body_size

            message = await receive()

            if message.get("type") == "http.request":
                if (body := message.get("body")) is not None:
                    request_body_size += len(body)

            return message

        span: Span

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.add_traceresponse_header(message)
                if settings.tracing_metrics_header:
                    add_request_metrics_header(message)
                self.log_access(scope, message)
                self.add_attributes_form_request_body(
                    request_size=request_body_size,
                )
                self.add_attributes_from_response(message, span)

                include_request_metrics(span)

            await send(message)

        with tracer.span(f"{scope.get('method')} request") as span:
            span.span_kind = SpanKind.SERVER

            self.add_attributes_form_request_scope(span, scope, headers=headers)

            await self.app(scope, _receive, _send)

    @staticmethod
    def add_attributes_form_request_scope(
        span: BaseSpan, scope: Scope, headers: Headers | None = None
    ) -> None:
        """Adds attributes and properties from request scope to current tracing span.

        Known attribute names can be looked up from `opencensus.trace.attributes_helper.COMMON_ATTRIBUTES` and
        https://github.com/microsoft/ApplicationInsights-JS/blob/17ef50442f73fd02a758fbd74134933d92607ecf/legacy/JavaScript/JavaScriptSDK.Interfaces/Contracts/Generated/ContextTagKeys.ts#L208-L262
        however isn't guaranteed that they will work as expected in Application Insights
        """

        if headers is None:
            headers = Headers(scope=scope)

        path = TracingMiddleware.get_path(scope)

        span.add_attribute(COMMON_ATTRIBUTES["HTTP_METHOD"], scope.get("method"))
        span.add_attribute(COMMON_ATTRIBUTES["HTTP_PATH"], path)
        span.add_attribute(COMMON_ATTRIBUTES["HTTP_URL"], path)
        span.add_attribute(
            COMMON_ATTRIBUTES["HTTP_CLIENT_PROTOCOL"],
            f"{scope.get('type', '').upper()}/{scope.get('http_version')}",
        )

    @staticmethod
    def add_attributes_form_request_body(
        request_size: int | None = None,
    ) -> None:
        """Adds only the byte count; body content is never captured."""

        if span := execution_context.get_current_span():
            if request_size is not None:
                span.add_attribute(COMMON_ATTRIBUTES["HTTP_REQUEST_SIZE"], request_size)


    @staticmethod
    def add_attributes_from_response(
        response: Response | Message, span: Span | None = None
    ) -> None:
        """Adds attributes and properties to the current tracing span from the response and request state"""

        if not span:
            span = execution_context.get_current_span()

        headers: Headers | MutableHeaders
        if isinstance(response, Response):
            headers = response.headers
            status_code = response.status_code
        else:
            headers = Headers(raw=response.get("headers", []))
            status_code = response["status"]

        # noinspection PyTypeChecker
        span.add_attribute(COMMON_ATTRIBUTES["HTTP_STATUS_CODE"], int(status_code))

        content_length = headers.get("content-length")
        if content_length is not None:
            span.add_attribute(COMMON_ATTRIBUTES["HTTP_RESPONSE_SIZE"], content_length)

        content_type = headers.get("content-type")
        if content_type:
            content_type = content_type.split(";", 1)[0]
            span.add_attribute("http.content_type", content_type)

    @staticmethod
    def log_access(scope: Scope, response: Response | Message) -> None:
        """Logs an access-log style line"""

        headers: Headers | MutableHeaders
        if isinstance(response, Response):
            headers = response.headers
            status = response.status_code
        else:
            headers = Headers(raw=response.get("headers", []))
            status = response["status"]

        path = TracingMiddleware.get_path(scope)

        protocol = f"{scope.get('type', '').upper()}/{scope.get('http_version')}"

        content_type = headers.get("content-type", "-")
        content_type = content_type.split(";", 1)[0] if content_type else "-"

        content_length = headers.get("content-length", "-")

        log.info(
            '"%s %s %s" %s %s %s',
            scope.get("method"),
            path,
            protocol,
            status,
            content_type,
            content_length,
        )

    @staticmethod
    def get_path_qs(scope) -> list[str]:
        """Compatibility helper that intentionally excludes the query string."""
        return [TracingMiddleware.get_path(scope)]

    @staticmethod
    def get_path(scope):
        route = scope.get("route")
        route_path = getattr(route, "path_format", None) or getattr(route, "path", None)
        if not route_path:
            return "[unmatched]"
        return "".join(filter(None, (scope.get("root_path"), route_path)))

    @staticmethod
    def add_traceresponse_header(
        response: Response | Message,
        expose_header: bool = False,
        span: Span | None = None,
        flags: int | float | None = None,
    ) -> None:
        """Add trace id either from trace context or `traceparent` request header to `traceresponse` response header.

        `traceresponse` response header is proposed by W3C - Trace Context Level 2 - Editor's Draft 13 April 2022
        """
        if span:
            log_attrs = (span.context_tracer.trace_id, span.span_id, flags)
        else:
            log_attrs = get_log_attrs()
        value = f"00-{log_attrs[0]:s}-{log_attrs[1]:s}-{log_attrs[2]:02d}"

        if isinstance(response, Response):
            headers = response.headers
        else:
            headers = MutableHeaders(scope=response)

        headers.append(TRACE_RESPONSE_HEADER_NAME, value)
        if expose_header:
            headers.setdefault(
                "Access-Control-Expose-Headers", TRACE_RESPONSE_HEADER_NAME
            )
