"""Verify canonical V2 definition/execution preservation and retained source custody."""

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
    execution = bundle.get("execution", bundle)  # read-only historical report support
    forms = execution.get("forms", {}).get("forms", [])
    return {
        "visits": len(execution.get("visits", [])),
        "assignments": len(execution.get("visitFormAssignments", [])),
        "forms": len(forms),
        "fields": sum(len(form.get("fields", [])) for form in forms),
        "groupClasses": len(execution.get("studyGroupClasses", [])),
        "tasks": len(execution.get("studyTasks", [])),
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
    export_census_document = actual.get("extensions", {}).get("_osbExport", {}).get("census", {})
    export_census = (
        export_census_document.get("rows", [])
        if isinstance(export_census_document, dict)
        else export_census_document
    )

    if expected.get("formatVersion") != "2.0" or actual.get("formatVersion") != "2.0":
        raise SystemExit("Current parity requires canonical V2 input and output; historical source requires the private decoder.")
    differences = _diff(expected["definition"], actual["definition"], "$.definition")
    differences += _diff(expected["execution"], actual["execution"], "$.execution")
    actual_extensions = actual.get("extensions", {})
    for key, value in expected.get("extensions", {}).items():
        path = "$.extensions." + key
        if key not in actual_extensions:
            differences.append({"path": path, "kind": "missing"})
            continue
        actual_value = actual_extensions[key]
        if key == "_osbExport":
            if not isinstance(actual_value, dict) or "previous" not in actual_value:
                differences.append({"path": path, "kind": "missing"})
                continue
            actual_value = actual_value["previous"]
        differences += _diff(value, actual_value, path)
    for collection, identity in (("artifacts", "artifactId"), ("payloads", "sha256")):
        for source in expected["source"].get(collection, []):
            path = "$.source." + collection + "." + source[identity]
            matches = [row for row in actual["source"].get(collection, []) if row[identity] == source[identity]]
            if len(matches) != 1:
                differences.append({"path": path, "kind": "missing" if not matches else "duplicate"})
            else:
                differences += _diff(source, matches[0], path)
    differences += _diff(expected["source"]["normalizations"], actual["source"]["normalizations"], "$.source.normalizations")
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
