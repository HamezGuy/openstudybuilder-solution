"""Isolated cross-language V2 contract tests; no API, auth context or database."""

import base64
import gzip
import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.integrations import edc_export
from clinical_mdr_api.services.integrations.edc_export import EdcExportService
from clinical_mdr_api.services.integrations.edc_study_exchange import StudyExchangeError, _encode_ledger, artifact_bytes, build_study_exchange, ledger_entries, verify_source_exchange


def test_prefix_ledger_preserves_unicode_empty_segments_and_extra_evidence():
    entries = [{"sourceArtifactId": "source-A", "sourcePointer": pointer, "type": "string",
                "valueSha256": "sha256:" + "a" * 64, "disposition": "retained-source", "targetPointers": []}
               for pointer in ["", "/", "/μg/😀/a~1b", "/μg/😀/a~0b", "/μg//"]]
    entries.append({**entries[-1], "sourceArtifactId": "source-B", "evidenceRef": "",
                    "future": {"null": None, "empty": []}})
    encoded = _encode_ledger(entries)
    assert encoded["encoding"] == "gzip-jsonl-dictionary-prefix-chunks"
    assert list(ledger_entries({"valueLedger": encoded})) == entries


def test_prefix_ledger_resets_at_chunk_boundaries():
    entries = [{"sourceArtifactId": "source-A", "sourcePointer": f"/claims/{index}/nested/🧬/value", "type": "string",
                "valueSha256": "sha256:" + "a" * 64, "disposition": "retained-source", "targetPointers": []}
               for index in range(36000)]
    encoded = _encode_ledger(entries)
    assert len(encoded["chunks"]) > 1
    assert list(ledger_entries({"valueLedger": encoded})) == entries


def test_prefix_ledger_rejects_reference_outside_current_chunk():
    raw = json.dumps([1, ["value"], "string", "sha256:" + "a" * 64, "retained-source", []]).encode() + b"\n"
    encoded = {"encoding": "gzip-jsonl-prefix-chunks", "byteLength": len(raw), "entryCount": 1,
               "sha256": hashlib.sha256(raw).hexdigest(),
               "chunks": [{"byteLength": len(raw), "sourceArtifactId": "source-A",
                           "payload": base64.b64encode(gzip.compress(raw)).decode()}]}
    with pytest.raises(StudyExchangeError, match="EDC_SOURCE_LEDGER_PREFIX"):
        list(ledger_entries({"valueLedger": encoded}))


def document():
    return {
        "usdmVersion": "4.0.0",
        "systemName": None,
        "systemVersion": None,
        "study": {
            "id": "11111111-1111-4111-8111-111111111111",
            "name": "Exact native study",
            "instanceType": "Study",
            "versions": [
                {"id": "version-1", "studyDesigns": [{"id": "design-1", "instanceType": "ObservationalStudyDesign", "name": "Source design", "future": {"empty": [], "null": None, "value": False}}]},
                {"id": "version-2", "studyDesigns": []},
            ],
        },
    }


def execution():
    return {
        "visits": [],
        "visitFormAssignments": [],
        "forms": {"formatVersion": "1.0", "exportedAt": "2026-09-09T00:00:00.000Z", "exportedBy": "fixture", "forms": []},
        "studyTasks": [],
        "deviationSpec": None,
        "extensions": {"future": []},
    }


def exchange(source=None, **overrides):
    arguments = {
        "study_uid": "Study_1",
        "document": document(),
        "execution": execution(),
        "native": {"records": [{"unknown": None, "unit": " μg / m² ", "regex": "^exact\\s+text{1,512}$"}]},
        "source_bundle": source or {},
        "exported_at": "2026-09-09T00:00:00.000Z",
        "disclosure": {"deploymentAllowed": False, "authoritative": False},
        "census": {"rows": [], "counts": {}},
    }
    return build_study_exchange(**{**arguments, **overrides})


def retained(bundle, system):
    artifact = next(row for row in bundle["source"]["artifacts"] if row["sourceSystem"] == system)
    return json.loads(artifact_bytes(artifact))


def test_native_document_is_complete_and_no_clinical_defaults_are_added():
    source_document = document()
    bundle = exchange(document=source_document)
    assert bundle["formatVersion"] == "2.0"
    assert bundle["profile"]["mode"] == "draft"
    assert bundle["definition"]["document"] == source_document
    assert bundle["definition"]["selection"] == {"versionId": None, "designId": None}
    assert bundle["execution"] == execution()
    assert bundle["definition"]["execution"]["enrollment"] == {"parameters": {}}
    assert not {"study", "visits", "forms"} & bundle.keys()
    assert retained(bundle, "openstudybuilder.edc-native-observations")["native"]["records"][0]["unit"] == " μg / m² "
    assert retained(bundle, "openstudybuilder.edc-native-observations")["native"]["records"][0]["regex"] == "^exact\\s+text{1,512}$"
    assert bundle["extensions"]["_osbExport"]["mappingAuthority"]["deploymentAllowed"] is False
    verify_source_exchange(bundle)


def test_current_graph_execution_unknowns_and_historical_hashes_survive_noop():
    original = exchange()
    original["extensions"]["unknown"] = {"invalidSourceEnum": "not-a-real-enum", "empty": "", "null": None, "flags": [False, 0]}
    original["extensions"]["oldReceipt"] = {"profile": "canonical-json/1.0", "hash": "sha256:" + "a" * 64}
    before = deepcopy(original)
    output = exchange(original, document=None, execution={**execution(), "studyTasks": [{"title": "Native divergence"}]})
    assert original == before
    assert output["definition"] == original["definition"]
    assert output["execution"] == original["execution"]
    assert output["extensions"]["unknown"] == original["extensions"]["unknown"]
    assert output["extensions"]["oldReceipt"] == original["extensions"]["oldReceipt"]
    assert output["source"]["artifacts"][: len(original["source"]["artifacts"])] == original["source"]["artifacts"]
    assert retained(output, "openstudybuilder.edc-source-snapshot") == original
    assert list(ledger_entries(output["source"]))[: len(list(ledger_entries(original["source"])))] == list(ledger_entries(original["source"]))
    verify_source_exchange(output)


def test_superseded_report_targets_retain_the_exact_source_and_old_ledger():
    original = exchange()
    value = "  exact historical report  "
    original["extensions"]["_osbExport"]["obsoleteSummary"] = value
    raw = json.dumps(value).encode("utf-8")
    artifact = {
        "artifactId": "33333333-3333-4333-8333-333333333333",
        "kind": "source-evidence",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byteLength": len(raw),
        "mediaType": "application/json",
        "sourceSystem": "test.historical-report",
        "encoding": "base64",
        "payload": base64.b64encode(raw).decode("ascii"),
    }
    entry = {
        "sourceArtifactId": artifact["artifactId"],
        "sourcePointer": "",
        "type": "string",
        "valueSha256": "sha256:" + artifact["sha256"],
        "disposition": "execution",
        "targetPointers": ["/extensions/_osbExport/obsoleteSummary"],
    }
    original["source"]["artifacts"].append(artifact)
    original["source"]["valueLedger"] = [*ledger_entries(original["source"]), entry]
    before = deepcopy(original)
    verify_source_exchange(original)

    output = exchange(original)

    assert original == before
    assert output["definition"] == original["definition"]
    assert output["execution"] == original["execution"]
    assert output["extensions"]["_osbExport"]["previous"] == original["extensions"]["_osbExport"]
    assert retained(output, "openstudybuilder.edc-source-snapshot") == original
    output_entry = next(row for row in ledger_entries(output["source"]) if row["sourceArtifactId"] == artifact["artifactId"])
    assert output_entry == {**entry, "disposition": "retained-source", "targetPointers": []}
    verify_source_exchange(output)


def test_native_api_dates_and_decimal_lexemes_survive_retention():
    record = {
        "uid": "Native_1",
        "created": datetime(2026, 9, 9, 10, 11, 12, tzinfo=timezone.utc),
        "date": date(2026, 9, 9),
        "decimal": Decimal("0.10000000000000001"),
        "unit": " μg / m² ",
        "empty": None,
    }
    before = deepcopy(record)
    service = object.__new__(EdcExportService)
    service._retain_native("native", record)
    service._retain_native("native", record)
    output = exchange(native={"records": service._native_records})
    retained_records = retained(output, "openstudybuilder.edc-native-observations")["native"]["records"]
    assert record == before
    assert retained_records == [
        {
            "kind": "native",
            "uid": "Native_1",
            "record": {**record, "created": "2026-09-09T10:11:12Z", "date": "2026-09-09", "decimal": "0.10000000000000001"},
        }
    ]
    verify_source_exchange(output)


@pytest.mark.parametrize("alter", ["payload", "canonical", "ledger", "extra", "reference", "gzip", "flat"])
def test_source_tampering_is_rejected_without_repair(alter):
    original = exchange()
    if alter == "payload":
        original["source"]["artifacts"][0]["payload"] = base64.b64encode(gzip.compress(b"{}")).decode()
    elif alter == "canonical":
        original["definition"]["document"]["study"]["name"] = "changed"
    elif alter == "ledger":
        original["source"]["valueLedger"]["sha256"] = "0" * 64
    elif alter == "extra":
        entries = list(ledger_entries(original["source"]))
        original["source"]["valueLedger"] = [*entries, entries[-1]]
    elif alter == "reference":
        original["definition"]["sourceArtifacts"][0]["sha256"] = "0" * 64
    elif alter == "gzip":
        original["source"]["artifacts"][0]["payload"] = base64.b64encode(b"invalid gzip").decode()
    else:
        original["study"] = {"name": "Hidden flat writer"}
    before = deepcopy(original)
    with pytest.raises(StudyExchangeError):
        exchange(original)
    assert original == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.0, 9007199254740993])
def test_native_numbers_fail_capacity_without_rounding(value):
    with pytest.raises(StudyExchangeError, match="EDC_NATIVE_NUMBER_UNREPRESENTABLE"):
        exchange(native={"value": value})


def test_historical_source_is_only_retained_evidence_and_writer_is_always_v2():
    old = {
        "formatVersion": "1.0",
        "study": {"name": "Historical", "phase": "invalid", "studyParameters": {"zero": 0, "false": False}},
        "visits": [],
        "forms": {"forms": []},
        "_old": {"profile": "canonical-json/1.0", "hash": "retained"},
    }
    bundle = exchange(old)
    assert bundle["definition"]["document"] == document()
    assert retained(bundle, "openstudybuilder.edc-source-snapshot") == old
    assert bundle["extensions"]["_old"] == old["_old"]
    assert bundle["formatVersion"] == "2.0"
    assert "study" not in bundle


@pytest.mark.parametrize("key,value", [("unit", "μ" * 65), ("validationPattern", "^" + "a" * 1000 + "$")])
def test_native_execution_capacity_is_rejected_without_shortening(key, value):
    original = execution()
    original["forms"]["forms"] = [{"refKey": "F", "fields": [{"refKey": "I", key: value}]}]
    before = deepcopy(original)
    with pytest.raises(StudyExchangeError, match="EDC_NATIVE_COLUMN_UNREPRESENTABLE"):
        exchange(execution=original)
    assert original == before


@pytest.mark.parametrize("with_source_snapshot", [False, True])
def test_actual_exporter_uses_usdm_service_or_exact_current_snapshot_and_accepts_structure_only(monkeypatch, with_source_snapshot):
    monkeypatch.setattr(edc_export.config.settings, "mapping_authority_mode", "shadow")
    service = object.__new__(EdcExportService)
    service.study_service = SimpleNamespace(get_by_uid=lambda *a, **k: {"uid": "Study_1"})
    calls = []
    service.usdm_service = SimpleNamespace(
        get_by_uid_with_report=lambda uid: calls.append(uid) or {
            "document": document(),
            "mappingReport": {
                "state": "complete", "studyUid": uid,
                "studyValueVersion": None, "issues": [],
            },
            "nativeRecords": [],
        }
    )
    service._visits = lambda *a: ([], {}, {})
    service._study_event_form_uids = lambda *a: (set(), set())
    source = exchange()
    snapshot = {"formatVersion": "osb-edc-source-snapshot/2", "studyExchange": source, "semanticSourceCustody": {"unknown": [], "status": None}}
    before = deepcopy(snapshot)

    def forms(*args):
        if with_source_snapshot:
            service._set_source_snapshot(snapshot)
        return [], {}, {}

    service._forms = forms
    service._assignments = lambda *a: []
    service._group_classes = lambda *a: []
    service._retain_source_owned_odm_definitions = lambda *a: None
    service._retain_native_associated_records = lambda *a: None
    service._retain_linked_definitions = lambda *a: None
    result = service.build_bundle("Study_1")
    assert calls == ([] if with_source_snapshot else ["Study_1"])
    assert result["definition"]["document"] == document()
    assert result["execution"]["forms"]["forms"] == []
    assert result["profile"]["mode"] == "draft"
    if with_source_snapshot:
        assert snapshot == before
        assert result["definition"] == source["definition"]
        assert result["execution"] == source["execution"]
        assert retained(result, "openstudybuilder.edc-source-snapshot") == source
        observations = retained(result, "openstudybuilder.edc-native-observations")
        # The inherited export has an earlier observation artifact as well.
        current = result["extensions"]["_osbExport"]
        assert current["native"]["sourceSnapshotMetadata"] == {
            "formatVersion": snapshot["formatVersion"],
            "semanticSourceCustody": snapshot["semanticSourceCustody"],
        }
        assert observations["native"]["records"][0]["unit"] == " μg / m² "


def test_incomplete_native_mapping_remains_an_authorable_draft_with_exact_source_issues():
    source_document = document()
    source_document["study"]["versions"][0]["studyDesigns"][0]["population"] = {
        "id": "population", "name": "Unknown healthy-subject status",
        "instanceType": "StudyDesignPopulation",
    }
    report = {
        "state": "incomplete", "studyUid": "Study_1", "studyValueVersion": "2.0",
        "issues": [{
            "code": "SOURCE_VALUE_REQUIRED",
            "sourcePath": "/study_population/healthy_subject_indicator",
            "targetPath": "/study/versions/0/studyDesigns/0/population/includesHealthySubjects",
            "message": "Resolve the source value without substituting false.",
            "resolution": "Review the study population.",
        }],
    }
    before = deepcopy(report)
    output = exchange(document=source_document, mapping_report=report)
    assert output["profile"]["mode"] == "draft"
    assert "includesHealthySubjects" not in output["definition"]["document"]["study"]["versions"][0]["studyDesigns"][0]["population"]
    assert output["extensions"]["_osbExport"]["mappingReport"] == report
    assert retained(output, "openstudybuilder.edc-native-observations")["mappingReport"] == report
    assert report == before
    verify_source_exchange(output)


def test_mapping_report_from_another_native_study_cannot_be_attached_to_this_draft():
    with pytest.raises(StudyExchangeError, match="MAPPING_REPORT_INVALID"):
        exchange(mapping_report={
            "state": "complete", "studyUid": "AnotherStudy",
            "studyValueVersion": "1.0", "issues": [],
        })


def test_actual_exporter_passes_selected_version_and_preserves_mapping_report(monkeypatch):
    monkeypatch.setattr(edc_export.config.settings, "mapping_authority_mode", "shadow")
    service = object.__new__(EdcExportService)
    calls = []
    service.study_service = SimpleNamespace(
        get_by_uid=lambda uid, **kwargs: calls.append(("study", uid, kwargs)) or {"uid": uid}
    )
    report = {
        "state": "incomplete", "studyUid": "Study_1", "studyValueVersion": "2.0",
        "issues": [{"code": "UNKNOWN_REQUIRED_FACT", "sourcePath": "/population", "message": "Requires source review"}],
    }
    service.usdm_service = SimpleNamespace(
        get_by_uid_with_report=lambda uid, **kwargs: calls.append(("usdm", uid, kwargs)) or {
            "document": document(), "mappingReport": report,
            "nativeRecords": [{"kind": "studyCohort", "uid": "cohort", "record": {"name": "", "count": 0, "unknown": None}}],
        }
    )
    service._visits = lambda *args: ([], {}, {})
    service._study_event_form_uids = lambda *args: (set(), set())
    service._forms = lambda *args: ([], {}, {})
    service._assignments = lambda *args: []
    service._group_classes = lambda *args: []
    service._retain_source_owned_odm_definitions = lambda *args: None
    service._retain_native_associated_records = lambda *args: None
    service._retain_linked_definitions = lambda *args: None
    result = service.build_bundle("Study_1", study_value_version="2.0")
    assert calls[0][2]["study_value_version"] == "2.0"
    assert calls[1] == ("usdm", "Study_1", {"study_value_version": "2.0"})
    assert result["extensions"]["_osbExport"]["mappingReport"] == report
    assert result["extensions"]["_osbExport"]["native"]["usdmMappingRecords"][0]["record"] == {
        "name": "", "count": 0, "unknown": None,
    }
    report["studyValueVersion"] = "3.0"
    with pytest.raises(edc_export.EdcExportError, match="SOURCE_SCOPE_MISMATCH"):
        service.build_bundle("Study_1", study_value_version="2.0")
