"""Fail-closed privacy primitives for logs, traces, metrics, and retry metadata.

These helpers operate before values reach an observability exporter. They are
not a replacement for domain authorization or the regulated audit trail.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any
from uuid import uuid4


_PROHIBITED_KEYS = {
    "subjectid",
    "studysubjectid",
    "subjectidentifier",
    "subjecttoken",
    "patientid",
    "participantid",
    "participantidentifier",
    "pseudonymoussubjectidentifier",
    "pseudonym",
    "dob",
    "dateofbirth",
    "mrn",
    "medicalrecordnumber",
    "itemvalue",
    "labvalue",
    "originalvalue",
    "normalizedvalue",
    "referencerange",
    "querytext",
    "querynarrative",
    "deviationtext",
    "deviationnarrative",
    "narrative",
    "freetext",
}
_SECRET_KEYS = {
    "authorization",
    "authorizationheader",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "cookie",
    "setcookie",
    "password",
    "clientsecret",
    "privatekey",
    "presignedurl",
    "accessgrantcompact",
}
_VALUE_PATTERNS = (
    re.compile(r"\bPHI[_-]?CANARY\b", re.IGNORECASE),
    re.compile(
        r"\b(?:MRN|medical\s+record\s+number|date\s+of\s+birth|DOB|"
        r"subject\s+(?:id|token)|patient\s+id)\s*[:=#-]",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:query|deviation)\s+narrative\s*[:=#-]", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]+", re.IGNORECASE),
)
_STANDARD_LOG_RECORD_FIELDS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}


def _key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).lower()


def _contains_prohibited_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _VALUE_PATTERNS)


def safe_error(error: object) -> dict[str, str]:
    """Return non-sensitive correlation metadata for an exception."""

    candidate = getattr(error, "code", None) or type(error).__name__
    error_code = re.sub(r"[^A-Za-z0-9_.-]", "_", str(candidate))[:128]
    return {
        "errorCode": error_code or "UNEXPECTED_ERROR",
        "rejectionId": str(uuid4()),
        "safeMessage": "Operation failed; use the rejection ID for controlled diagnostics.",
    }


def assert_no_observability_phi(
    value: object, path: str = "$", seen: set[int] | None = None
) -> None:
    """Reject prohibited keys and synthetic PHI canaries recursively."""

    if seen is None:
        seen = set()
    if isinstance(value, str):
        if _contains_prohibited_value(value):
            raise ValueError(f"OBSERVABILITY_PHI_VALUE_PROHIBITED:{path}")
        return
    if value is None or isinstance(value, (bool, int, float, bytes)):
        return
    identity = id(value)
    if identity in seen:
        raise ValueError(f"OBSERVABILITY_CYCLE_PROHIBITED:{path}")
    seen.add(identity)
    if isinstance(value, Mapping):
        for field, child in value.items():
            normalized = _key(str(field))
            if normalized in _PROHIBITED_KEYS:
                raise ValueError(f"OBSERVABILITY_PHI_FIELD_PROHIBITED:{path}.{field}")
            if normalized in _SECRET_KEYS:
                raise ValueError(f"OBSERVABILITY_SECRET_FIELD_PROHIBITED:{path}.{field}")
            assert_no_observability_phi(child, f"{path}.{field}", seen)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, child in enumerate(value):
            assert_no_observability_phi(child, f"{path}[{index}]", seen)


def sanitize_text(value: str) -> str:
    sanitized = value
    for pattern in _VALUE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized[:512] + ("..." if len(sanitized) > 512 else "")


def sanitize_value(
    value: Any, depth: int = 0, seen: set[int] | None = None
) -> Any:
    """Create a bounded, exporter-safe representation of an arbitrary value."""

    if depth > 8:
        return "[TRUNCATED_DEPTH]"
    if seen is None:
        seen = set()
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, bytes):
        return f"[BYTES:{len(value)}]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, BaseException):
        return safe_error(value)
    identity = id(value)
    if identity in seen:
        return "[REDACTED_CYCLE]"
    seen.add(identity)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for field, child in list(value.items())[:50]:
            normalized = _key(str(field))
            result[str(field)] = (
                "[REDACTED]"
                if normalized in _PROHIBITED_KEYS or normalized in _SECRET_KEYS
                else sanitize_value(child, depth + 1, seen)
            )
        return result
    if isinstance(value, tuple):
        return tuple(sanitize_value(child, depth + 1, seen) for child in value[:50])
    if isinstance(value, (list, set, frozenset)):
        return [sanitize_value(child, depth + 1, seen) for child in list(value)[:50]]
    return f"[{type(value).__name__}]"


class ObservabilityPrivacyFilter(logging.Filter):
    """Sanitize a record and remove traceback material before any handler sees it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_value(record.msg)
        record.args = sanitize_value(record.args)
        for field, value in list(record.__dict__.items()):
            if field not in _STANDARD_LOG_RECORD_FIELDS:
                setattr(record, field, sanitize_value(value))
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True
