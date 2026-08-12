"""Canonical JSON compatible with the Intelligence Layer TypeScript contract."""

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

CANONICAL_JSON_VERSION = "canonical-json/1.0"


def _utf16_sort_key(value: str) -> bytes:
    """Return JavaScript's lexicographic UTF-16 code-unit ordering key."""
    return value.encode("utf-16-be", errors="surrogatepass")


def _javascript_number(value: int | float) -> str:
    if isinstance(value, int) and abs(value) > 9_007_199_254_740_991:
        # TypeScript/JSON.parse represents every JSON number as IEEE-754 double.
        # Emulate that rounding before formatting an integer Python preserved
        # exactly, or cross-language hashes diverge above Number.MAX_SAFE_INTEGER.
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("CANONICAL_JSON_NON_FINITE_NUMBER")
    if value == 0:
        return "0"
    absolute = abs(value)
    raw = repr(value).lower()
    if 1e-6 <= absolute < 1e21:
        if "e" in raw:
            raw = format(Decimal(raw), "f")
        if "." in raw:
            raw = raw.rstrip("0").rstrip(".")
        return raw
    if "e" not in raw:
        raw = format(float(value), ".15e")
    mantissa, exponent = raw.split("e")
    mantissa = mantissa.rstrip("0").rstrip(".")
    exponent_value = int(exponent)
    sign = "+" if exponent_value >= 0 else "-"
    return f"{mantissa}e{sign}{abs(exponent_value)}"


def canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _javascript_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("CANONICAL_JSON_OBJECT_KEY_NOT_STRING")
        return (
            "{"
            + ",".join(
                f"{canonical_json(key)}:{canonical_json(value[key])}"
                for key in sorted(value, key=_utf16_sort_key)
            )
            + "}"
        )
    raise TypeError(f"CANONICAL_JSON_UNSUPPORTED_TYPE:{type(value).__name__}")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
