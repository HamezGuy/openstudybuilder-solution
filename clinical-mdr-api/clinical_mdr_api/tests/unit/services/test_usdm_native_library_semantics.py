"""Native model type, not UID spelling, determines library reference kind."""

import pytest

from clinical_mdr_api.services.integrations.edc_native_library_definitions import (
    collect_native_library_definitions,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource, native_odm_graph


@pytest.mark.parametrize("field", [
    "therapeutic_area_codes", "disease_condition_or_indication_codes", "diagnosis_group_codes",
])
def test_native_population_dictionary_fields_never_query_ct_definitions(field):
    calls = []

    def dictionary(uid, version):
        calls.append((uid, version))
        return {"term_uid": uid, "version": "1.0", "name": "Native dictionary term"}

    source = [{"kind": "study", "uid": "Study_1", "record": {"current_metadata": {
        "study_population": {field: [{"term_uid": "C123", "name": "Native dictionary term"}]}
    }}}]
    definitions, associations, issues = collect_native_library_definitions(
        source, readers={"dictionaryTerm": dictionary}
    )
    assert calls == [("C123", None)]
    assert [row["kind"] for row in definitions] == ["dictionaryTerm"]
    assert [row["targetKind"] for row in associations] == ["dictionaryTerm"]
    assert associations[0]["reading"] == "current_reading"
    assert issues == []


def test_population_null_flavor_stays_ct_even_when_uid_looks_like_dictionary():
    records = [{"kind": "study", "uid": "Study_1", "record": {"current_metadata": {
        "study_population": {"diagnosis_group_null_value_code": {"term_uid": "Dictionary_spelling"}}
    }}}]
    _, associations, _ = collect_native_library_definitions(records, readers={})
    assert {row["targetKind"] for row in associations} == {
        "ctTermAttributes", "ctTermName", "ctTermMemberships",
    }


def test_actual_native_producer_retains_full_exact_unit_and_dictionary_readings():
    bundle = NativeStudySource().export(native_odm_graph())
    report = bundle["extensions"]["_osbExport"]
    units = [row for row in report["native"]["records"] if row["kind"] == "unitDefinition"]
    assert len(units) == 1
    assert units[0]["uid"] == "Unit_1"
    assert units[0]["record"]["version"] == "1.0"
    assert units[0]["record"]["conversion_factor_to_master"] is None
    assert units[0]["record"]["use_molecular_weight"] is False
    assert units[0]["record"]["order"] == 0
    assert units[0]["record"]["comment"] == ""
    dictionaries = [row for row in report["native"]["records"] if row["kind"] == "dictionaryTerm"]
    assert [row["uid"] for row in dictionaries] == ["Dictionary_1"]
    assert not [row for row in report["census"]["rows"] if row.get("association", {}).get("status") == "unresolved"]


def test_missing_unit_reader_preserves_exact_reference_and_explicit_failure():
    source = NativeStudySource()
    readers = source.library_readers()
    del readers["unitDefinition"]
    source.library_readers = lambda: readers
    bundle = source.export(native_odm_graph())
    report = bundle["extensions"]["_osbExport"]
    failures = [row for row in report["census"]["rows"] if row.get("association", {}).get("status") == "unresolved"]
    assert len(failures) == 1
    association = failures[0]["association"]
    assert association["targetKind"] == "unitDefinition"
    assert association["targetUid"] == "Unit_1"
    assert association["requestedVersion"] == "1.0"
    assert association["status"] == "unresolved"
    assert association["sourceReference"]["name"] == "10^9/L"
