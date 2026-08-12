"""Verify exact StudyBundleV1 parity across 360i -> OSB -> EDC projection."""

import argparse
import json
import os
from collections import Counter

import requests

from .utils.ecrf_platform_db import EcrfPlatformDb
from .utils.mapping_authority import assert_legacy_comparison_allowed


def _diff(expected, actual, path="$"):
    differences = []
    if type(expected) is not type(actual):
        return [
            {
                "path": path,
                "kind": "type",
                "expected": type(expected).__name__,
                "actual": type(actual).__name__,
            }
        ]
    if isinstance(expected, dict):
        for key in sorted(expected.keys() - actual.keys()):
            differences.append({"path": f"{path}.{key}", "kind": "missing"})
        for key in sorted(actual.keys() - expected.keys()):
            differences.append({"path": f"{path}.{key}", "kind": "unexpected"})
        for key in sorted(expected.keys() & actual.keys()):
            differences.extend(_diff(expected[key], actual[key], f"{path}.{key}"))
        return differences
    if isinstance(expected, list):
        if len(expected) != len(actual):
            differences.append(
                {
                    "path": path,
                    "kind": "length",
                    "expected": len(expected),
                    "actual": len(actual),
                }
            )
        for index, (expected_item, actual_item) in enumerate(
            zip(expected, actual, strict=False)
        ):
            differences.extend(
                _diff(expected_item, actual_item, f"{path}[{index}]")
            )
        return differences
    if expected != actual:
        differences.append(
            {
                "path": path,
                "kind": "value",
                "expected": expected,
                "actual": actual,
            }
        )
    return differences


def _counts(bundle):
    forms = bundle.get("forms", {}).get("forms", [])
    return {
        "visits": len(bundle.get("visits", [])),
        "assignments": len(bundle.get("visitFormAssignments", [])),
        "forms": len(forms),
        "fields": sum(len(form.get("fields", [])) for form in forms),
        "groupClasses": len(bundle.get("studyGroupClasses", [])),
        "tasks": len(bundle.get("studyTasks", [])),
    }


def main():
    assert_legacy_comparison_allowed("verify_edc_roundtrip")
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", required=True, help="360i study id")
    parser.add_argument("--osb-study", required=True, help="OSB study uid")
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("API_BASE_URL", "http://localhost:5005/api"),
    )
    parser.add_argument("--max-differences", type=int, default=50)
    args = parser.parse_args()

    with EcrfPlatformDb() as db:
        record = db.read_latest_payload(args.study)
    if record is None:
        raise SystemExit(f"No source payload found for {args.study}")
    expected = record["payload"]["sourceBundle"]

    url = (
        f"{args.api_base_url.rstrip('/')}/integrations/edc/studies/"
        f"{args.osb_study}/study-bundle"
    )
    response = requests.get(url, timeout=900)
    response.raise_for_status()
    actual = response.json()
    export_census_document = actual.pop("_exportCensus", {})
    export_census = (
        export_census_document.get("rows", [])
        if isinstance(export_census_document, dict)
        else export_census_document
    )

    differences = _diff(expected, actual)
    report = {
        "studyId": args.study,
        "osbStudyUid": args.osb_study,
        "sourcePayloadHash": record["payload_hash"],
        "sourceCounts": _counts(expected),
        "exportCounts": _counts(actual),
        "exportCensusCount": len(export_census),
        "exportCensus": export_census[: args.max_differences],
        "differenceCount": len(differences),
        "differenceKinds": dict(Counter(item["kind"] for item in differences)),
        "differences": differences[: args.max_differences],
    }
    print(json.dumps(report, indent=2, default=str))
    raise SystemExit(1 if differences or export_census else 0)


if __name__ == "__main__":
    main()
