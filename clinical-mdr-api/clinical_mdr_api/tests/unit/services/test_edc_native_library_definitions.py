"""Associated definitions remain exact without guessing study or version links."""

from copy import deepcopy

from clinical_mdr_api.services.integrations.edc_native_library_definitions import (
    collect_native_library_definitions,
)


def readers_and_calls():
    calls = []

    def reader(kind):
        def read(uid, version):
            calls.append((kind, uid, version))
            key = "codelist_uid" if kind.startswith("ctCodelist") else "term_uid" if kind.startswith("ctTerm") or kind == "dictionaryTerm" else "uid"
            return {key: uid, "version": version or "current-7", "definition": "Full definition. " * 80,
                    "unknown": {"zero": 0, "false": False, "null": None, "ordered": [3, 1]},
                    **({"ucum": {"term_uid": "dictionary-ucum"}, "ct_units": [{"term_uid": "unit-ct"}]} if kind == "unitDefinition" else {})}
        return read

    kinds = ["ctCodelistAttributes", "ctCodelistName", "ctCodelistTerms", "ctTermAttributes", "ctTermName", "ctTermMemberships", "unitDefinition", "dictionaryTerm", "timeframe"]
    return {kind: reader(kind) for kind in kinds}, calls


def test_retains_exact_item_ct_versions_names_memberships_and_units_once():
    readers, calls = readers_and_calls()
    item = {"kind": "item", "uid": "Item_1", "record": {
        "codelist": {"uid": "List_1", "version": "2.0", "allows_multi_choice": False},
        "terms": [{"term_uid": "Term_1", "version": "1.0", "order": 0, "mandatory": False}],
        "unit_definitions": [{"uid": "Unit_1", "version": "3.0", "mandatory": True}],
        "vendor_attributes": [{"name": "ext", "value": {"term_uid": "not-a-native-reference"}}],
    }}
    original = deepcopy(item)
    definitions, associations, census = collect_native_library_definitions([item, deepcopy(item)], readers=readers)
    assert item == original
    assert not census
    assert len(calls) == len(set(calls))
    assert ("ctCodelistAttributes", "List_1", "2.0") in calls
    assert ("ctCodelistName", "List_1", None) in calls
    assert ("ctCodelistTerms", "List_1", None) in calls
    assert ("ctTermAttributes", "Term_1", "1.0") in calls
    assert ("ctTermName", "Term_1", None) in calls
    assert ("unitDefinition", "Unit_1", "3.0") in calls
    assert ("dictionaryTerm", "dictionary-ucum", None) in calls
    assert all(uid != "not-a-native-reference" for _, uid, _ in calls)
    assert next(row for row in definitions if row["kind"] == "unitDefinition")["record"]["unknown"] == original["record"].get("unknown", {"zero": 0, "false": False, "null": None, "ordered": [3, 1]})
    term = next(row for row in associations if row["targetKind"] == "ctTermAttributes" and row["targetUid"] == "Term_1")
    assert term["sourcePath"] == "/terms/0"
    assert term["sourceReference"] == item["record"]["terms"][0]
    assert term["reading"] == "referenced_version"
    assert next(row for row in associations if row["targetKind"] == "ctTermName")["reading"] == "current_reading"


def test_failed_version_lookup_never_retries_latest_or_accepts_a_different_identity():
    readers, calls = readers_and_calls()
    readers["unitDefinition"] = lambda uid, version: {"uid": uid, "version": "wrong"}
    readers["ctCodelistAttributes"] = lambda uid, version: {"codelist_uid": "foreign", "version": version}
    records = [{"kind": "item", "uid": "I", "record": {"codelist": {"uid": "C", "version": "1.0"}, "unit_definitions": [{"uid": "U", "version": "1.0"}]}}]
    definitions, associations, census = collect_native_library_definitions(records, readers=readers)
    assert not any(row["kind"] in {"unitDefinition", "ctCodelistAttributes"} for row in definitions)
    assert len(census) == 2
    assert all(row["kind"] == "unresolved_native_reference" for row in census)
    assert len([row for row in associations if row["status"] == "unresolved"]) == 2


def test_exact_embedded_selected_and_latest_timeframes_never_requery_or_collapse():
    readers, calls = readers_and_calls()
    timeframe = {"uid": "T", "version": "1.0", "name_plain": "Day 1", "parameter_terms": [{"uid": "opaque-syntax-parameter"}], "template": {"uid": "Template_1"}, "library": {"name": "Sponsor"}, "future": [False, 0, None]}
    latest = {**timeframe, "version": "2.0", "name_plain": "Day 2"}
    records = [{"kind": "studyEndpoint", "uid": "SE", "record": {"timeframe": timeframe, "latest_timeframe": latest}}]
    definitions, associations, census = collect_native_library_definitions(records, readers=readers)
    assert not calls
    assert not census
    assert [row["record"] for row in definitions] == [timeframe, latest]
    assert [row["sourcePath"] for row in associations] == ["/timeframe", "/latest_timeframe"]
    assert all(row["reading"] == "embedded_selection" for row in associations)


def test_compact_timeframe_reads_its_exact_version_and_keeps_failure_disclosed():
    readers, calls = readers_and_calls()
    def missing(uid, version):
        calls.append(("timeframe", uid, version))
        raise LookupError("requested timeframe version unavailable")
    readers["timeframe"] = missing
    record = {"kind": "studyEndpoint", "uid": "E", "record": {"timeframe": {"uid": "T", "version": "9.0", "unknown": False}}}
    definitions, associations, census = collect_native_library_definitions([record], readers=readers)
    assert not definitions
    assert calls == [("timeframe", "T", "9.0")]
    assert associations[0]["sourceReference"] == record["record"]["timeframe"]
    assert associations[0]["status"] == "unresolved"
    assert len(census) == 1


def test_source_carriers_and_unscoped_record_kinds_are_never_reference_inputs():
    readers, calls = readers_and_calls()
    records = [{"kind": "sourceSnapshotCarrier", "uid": "storage", "record": {"term_uid": "hidden"}}, {"kind": "unknown", "record": {"term_uid": "foreign"}}]
    assert collect_native_library_definitions(records, readers=readers) == ([], [], [])
    assert not calls
