from copy import deepcopy

import pytest

from clinical_mdr_api.services.integrations.edc_native_library_definitions import collect_native_library_definitions


@pytest.mark.parametrize("version", [1, False, "", "  ", ["1.0"], {"selected": "1.0"}])
def test_invalid_explicit_version_never_turns_into_latest_lookup(version):
    calls = []
    reference = {"uid": "Unit1", "version": version, "fullSource": {"false": False, "zero": 0}}
    original = deepcopy(reference)
    source = {"kind": "item", "uid": "Item1", "record": {"unit_definitions": [reference]}}
    def read(uid, version):
        calls.append((uid, version))
        return {"uid": uid, "version": "latest"}
    definitions, associations, census = collect_native_library_definitions([source], readers={"unitDefinition": read})
    assert not calls
    assert not definitions
    assert len(census) == 1
    assert associations[0]["status"] == "unresolved"
    assert associations[0]["sourceReference"] == original
    assert reference == original


@pytest.mark.parametrize("returned", [None, {"uid": "Foreign", "version": "1.0"}, {"uid": "Unit1", "version": "2.0"}])
def test_missing_or_mismatched_selected_definition_retains_each_exact_unresolved_occurrence(returned):
    calls = []
    reference = {"uid": "Unit1", "version": "1.0", "future": [0, False, None, ""]}
    records = [{"kind": "item", "uid": uid, "record": {"unit_definitions": [reference]}} for uid in ("Item1", "Item2")]
    def read(uid, version):
        calls.append((uid, version))
        return returned
    definitions, associations, census = collect_native_library_definitions(records, readers={"unitDefinition": read})
    assert calls == [("Unit1", "1.0")]
    assert not definitions
    assert len(associations) == len(census) == 2
    assert {row["sourceUid"] for row in associations} == {"Item1", "Item2"}
    assert all(row["sourceReference"] == reference and row["status"] == "unresolved" for row in associations)


def test_unavailable_reader_keeps_the_reference_and_never_claims_full_retention():
    source = {"kind": "studyEndpoint", "uid": "SE1", "record": {"timeframe": {"uid": "Time1", "version": "3.0", "text": "Complete selected reference"}}}
    definitions, associations, census = collect_native_library_definitions([source], readers={})
    assert not definitions
    assert len(census) == 1
    assert associations[0]["sourceReference"] == source["record"]["timeframe"]
    assert "unavailable" in associations[0]["reason"]
