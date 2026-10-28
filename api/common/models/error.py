import datetime
import re
from typing import Annotated, Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.exceptions import VersionConflictException


class ValidationDetail(BaseModel):
    error_code: Annotated[str, Field()]
    field: Annotated[tuple[str | int, ...], Field()]
    msg: Annotated[str, Field()]
    ctx: Annotated[Any, Field()]


# A machine code as the platform-control routes raise it, for example
# OSB_REGIMEN_EXPECTED_STATE_MISMATCH.
MACHINE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


def machine_code(exception: Exception) -> str | None:
    """The machine code an HTTPException carries, as a string or detail["code"],
    or the code a VersionConflictException carries."""
    if isinstance(exception, VersionConflictException):
        return exception.code if MACHINE_CODE.fullmatch(exception.code) else None
    if not isinstance(exception, StarletteHTTPException):
        return None
    detail = exception.detail
    if isinstance(detail, dict):
        detail = detail.get("code")
    if isinstance(detail, str) and MACHINE_CODE.fullmatch(detail):
        return detail
    return None


class ErrorResponse(BaseModel):
    time: Annotated[datetime.datetime, Field()]
    path: Annotated[str, Field()]
    method: Annotated[str, Field()]
    type: Annotated[str, Field()]
    message: Annotated[str, Field()]
    details: list[ValidationDetail] = Field(default_factory=list)
    code: Annotated[
        str | None,
        Field(
            description="Machine code of the refusal, as the platform-control routes "
            "raise it (for example OSB_REGIMEN_EXPECTED_STATE_MISMATCH). The same code "
            "also appears in message. Omitted when the error carries none.",
            exclude_if=lambda value: value is None,
        ),
    ] = None

    def __init__(
        self,
        request: Request,
        exception: Exception | RequestValidationError | ValidationError,
        **data,
    ) -> None:
        data["message"] = (
            data.get("message", None)
            or str(
                getattr(exception, "msg", None)
                or getattr(exception, "detail", None)
                or exception
            )
            or "An error occurred"
        )

        data.setdefault("code", machine_code(exception))

        if isinstance(exception, (RequestValidationError, ValidationError)):
            data["message"] = "The request failed due to validation errors"

            data["details"] = [
                ValidationDetail(
                    error_code=error["type"],
                    field=error["loc"],
                    msg=error["msg"],
                    ctx=jsonable_encoder(error["ctx"]) if "ctx" in error else {},
                )
                for error in exception.errors()
            ]
        elif isinstance(exception, VersionConflictException):
            data["details"] = [
                ValidationDetail(
                    error_code=exception.code,
                    field=("body", exception.conflict_field),
                    msg=exception.conflict_msg,
                    ctx=jsonable_encoder(exception.current),
                )
            ]

        super().__init__(
            time=datetime.datetime.now(datetime.UTC),
            path=str(request.url),
            method=request.method,
            type=type(exception).__name__,
            **data,
        )
