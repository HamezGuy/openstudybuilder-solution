"""Emit reviewable native inputs and the ACTUAL mapper/export output.

Run in the network-disabled, source-mounted OSB API image. Only the chosen
fixture output directory needs a writable mount. See the generated evidence.
"""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from clinical_mdr_api.services.integrations.edc_study_exchange import (
    ledger_entries, verify_source_exchange,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import (
    NativeStudySource, native_odm_graph, EXPORT_TIME,
)
from clinical_mdr_api.tests.fixtures.usdm_native_study import STUDY_UID, VERSION


def encode(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def generate(output_directory: Path):
    source = NativeStudySource()
    odm = native_odm_graph()
    native_input = source.source_input(odm)
    bundle = source.export(odm)
    verify_source_exchange(bundle)
    report = bundle["extensions"]["_osbExport"]
    values = {
        "synthetic-native-study.input.json": native_input,
        "synthetic-native-study.ecrfstudy": bundle,
        "synthetic-native-study.mapping-report.json": report["mappingReport"],
        "synthetic-native-study.read-calls.json": source.calls,
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for filename, value in values.items():
        raw = encode(value)
        (output_directory / filename).write_bytes(raw)
        files[filename] = {"byteLength": len(raw), "sha256": sha256(raw).hexdigest()}
    implementation_root = Path(__file__).parents[2]
    implementation_paths = [
        "services/ddf/usdm_mapper.py", "services/ddf/usdm_service.py",
        "services/ddf/usdm_mapping_context.py", "services/ddf/usdm_native_mapping.py",
        "services/ddf/usdm_utils.py", "services/integrations/edc_export.py",
        "services/integrations/edc_native_odm_candidates.py",
        "services/integrations/study_authority.py",
        "services/integrations/edc_field_types.py",
        "services/integrations/edc_source_snapshot.py",
        "services/integrations/edc_study_exchange.py",
        "services/integrations/edc_native_study_records.py",
        "services/integrations/edc_native_library_definitions.py",
        "services/integrations/canonical_json.py",
        "tests/fixtures/usdm_native_study.py", "tests/fixtures/usdm_native_source.py",
        "tests/fixtures/usdm_native_terminology.json",
        "tests/fixtures/usdm_native_fixture_export.py",
    ]
    tests = [
        "test_usdm_native_export.py", "test_usdm_native_library_semantics.py",
        "test_usdm_native_domains.py", "test_usdm_mapper_semantics.py",
        "test_usdm_arm_origin_generality.py", "test_usdm_id_manager.py",
        "test_edc_native_study_records.py", "test_edc_export_losslessness.py",
        "test_edc_native_record_retention.py", "test_edc_native_library_definitions.py",
        "test_edc_native_library_adversarial.py", "test_edc_study_exchange.py",
    ]
    evidence = {
        "formatVersion": "synthetic-native-osb-producer-evidence/1",
        "checkpoint": "exact-native-form-candidates",
        "supersedesArtifactSha256": "757db42d3a93fea22f4e9299625608016436099e971d6426cff9dfedcd7e99dd",
        "studyUid": STUDY_UID, "studyValueVersion": VERSION,
        "exportedAt": EXPORT_TIME.isoformat(),
        "activityInstance": {"selectionUid": "InstanceSelection_1", "nativeUid": "LibraryInstance_1", "version": "1.0"},
        "files": files,
        "implementation": {
            name: {"sha256": sha256((implementation_root / name).read_bytes()).hexdigest(),
                   "byteLength": (implementation_root / name).stat().st_size}
            for name in implementation_paths
        },
        "testSources": {
            f"tests/unit/services/{name}": {
                "sha256": sha256((implementation_root / "tests/unit/services" / name).read_bytes()).hexdigest(),
            } for name in tests
        },
        "pinnedSchema": {
            "path": "tests/fixtures/usdm_native_model4_schema.json",
            "sha256": sha256((implementation_root / "tests/fixtures/usdm_native_model4_schema.json").read_bytes()).hexdigest(),
        },
        "result": {
            "profile": bundle["profile"],
            "mappingState": report["mappingReport"]["state"],
            "mappingIssueCount": len(report["mappingReport"]["issues"]),
            "mappingIssues": report["mappingReport"]["issues"],
            "censusCounts": report["census"]["counts"],
            "nativeRecordCounts": dict(sorted(Counter(row["kind"] for row in report["native"]["records"]).items())),
            "mappingRecordCounts": dict(sorted(Counter(row["kind"] for row in report["native"]["usdmMappingRecords"]).items())),
            "sourceArtifacts": [
                {key: row[key] for key in ("artifactId", "sha256", "byteLength", "sourceSystem", "kind") if key in row}
                for row in bundle["source"]["artifacts"]
            ],
            "sourceLedgerEntries": sum(1 for _ in ledger_entries(bundle["source"])),
            "nativeFormCount": len(bundle["execution"]["forms"]["forms"]),
            "nativeFieldCount": sum(len(form["fields"]) for form in bundle["execution"]["forms"]["forms"]),
            "nativeCandidateIdentities": [
                {"refKey": form["refKey"], **form["_nativeCandidate"]}
                for form in bundle["execution"]["forms"]["forms"]
                if "_nativeCandidate" in form
            ],
            "visitAssignmentCount": len(bundle["execution"]["visitFormAssignments"]),
            "custodyVerification": "actual verify_source_exchange passed",
        },
        "limits": [
            "Synthetic native Pydantic model inputs and isolated service/repository reads; no native DB or live calls.",
            "Actual USDM mapper/service, native collectors, EDC exporter and custody assembly; no USDM output or imported carrier is supplied as input.",
            "Only native read boundaries, selected terminology rows, shadow-preview configuration and the export clock are substituted.",
            "Primary CDISC term subsets exercise selected package reads, not the complete terminology catalog or CORE validator.",
            "Exact selected-activity reachability supplies full versioned native ODM candidates. It does not select a clinical form version or bind it to a visit.",
            "Native candidates ignore historical vendor field authority; the complete vendor payload remains source evidence. Reviewed assignments must come from the CSL/EDC build specification and UI.",
            "The source includes the full native Unit_1@1.0 definition. Unversioned library observations are labelled current_reading and never treated as selected historical authority.",
            "Unknown required native facts remain omitted draft values; no test-only author resolutions are included.",
            "No release, acceptance, deployment, native entry or cross-project lifecycle success is claimed by fixture generation.",
            "Objectives, endpoints, eligibility criteria and compound dosing are empty in this fixture; their separate tests are not coverage from this producer artifact.",
        ],
    }
    (output_directory / "synthetic-native-study.evidence.json").write_bytes(encode(evidence))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.output_dir)
    print(json.dumps({
        "studyUid": STUDY_UID, "studyValueVersion": VERSION,
        "files": result["files"], "mappingIssueCount": result["result"]["mappingIssueCount"],
        "censusCounts": result["result"]["censusCounts"],
    }, indent=2))
