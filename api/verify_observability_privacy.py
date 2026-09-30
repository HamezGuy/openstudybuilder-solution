"""Synthetic, non-PHI observability privacy conformance check."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from common.observability_privacy import (
    ObservabilityPrivacyFilter,
    assert_no_observability_phi,
    sanitize_value,
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("fixture path is required")
    fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    assert_no_observability_phi(fixture["allowedCentralPayload"])

    privacy_filter = ObservabilityPrivacyFilter()
    for case in fixture["prohibitedCases"]:
        try:
            assert_no_observability_phi(case["payload"])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{case['id']} was not rejected")
        rendered = json.dumps(sanitize_value(case["payload"]), sort_keys=True)
        if case["marker"] in rendered:
            raise AssertionError(f"{case['id']} marker escaped sanitizer")
        record = logging.LogRecord(
            name="privacy-check",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="synthetic failure %s",
            args=(case["payload"],),
            exc_info=(ValueError, ValueError(case["marker"]), None),
        )
        privacy_filter.filter(record)
        if case["marker"] in record.getMessage() or record.exc_info is not None:
            raise AssertionError(f"{case['id']} escaped logging filter")

    root = Path(__file__).parent
    traceback_middleware = (root / "common/telemetry/traceback_middleware.py").read_text(encoding="utf-8")
    tracing_middleware = (root / "common/telemetry/tracing_middleware.py").read_text(encoding="utf-8")
    request_metrics = (root / "common/telemetry/request_metrics.py").read_text(encoding="utf-8")
    logger = (root / "common/logger.py").read_text(encoding="utf-8")
    assert "STACKTRACE" not in traceback_middleware
    assert "http.request_body" not in tracing_middleware
    assert 'span.add_attribute("cypher.query"' not in request_metrics
    assert "request.url.path" in logger
    assert "Reproduce with" not in logger

    print(json.dumps({"ok": True, "system": "osb", "cases": len(fixture["prohibitedCases"])}))


if __name__ == "__main__":
    main()
