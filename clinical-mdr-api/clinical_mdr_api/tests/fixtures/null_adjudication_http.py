"""Install native exception handlers without importing application startup."""

import ast
from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from common.exceptions import MDRApiBaseException
from common.logger import log_exception
from common.models.error import ErrorResponse
from common.telemetry.traceback_middleware import ExceptionTracebackMiddleware


def install_native_exception_handlers(app):
    # Execute the actual four handler definitions/decorators, preserving their
    # 400/403/404/412 serialization and logging. Do not execute the module's DB
    # configuration, application registry or OAuth lifespan.
    source = Path(__file__).resolve().parents[2] / "main.py"
    names = {
        "http_exception_handler",
        "mdr_api_exception_handler",
        "handle_validation_error",
        "handle_request_validation_error",
    }
    definitions = [
        node
        for node in ast.parse(source.read_text(encoding="utf-8")).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in names
    ]
    assert {node.name for node in definitions} == names
    namespace = {
        "app": app,
        "Request": Request,
        "HTTPException": HTTPException,
        "MDRApiBaseException": MDRApiBaseException,
        "ValidationError": ValidationError,
        "RequestValidationError": RequestValidationError,
        "JSONResponse": JSONResponse,
        "status": status,
        "ErrorResponse": ErrorResponse,
        "jsonable_encoder": jsonable_encoder,
        "log_exception": log_exception,
        "ExceptionTracebackMiddleware": ExceptionTracebackMiddleware,
    }
    exec(
        compile(ast.Module(body=definitions, type_ignores=[]), str(source), "exec"),
        namespace,
    )
