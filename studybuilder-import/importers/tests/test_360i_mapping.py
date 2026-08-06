"""Pure-mapper tests for the 360i payload -> OSB API-plan mapping.

No network, no database: these pin the doctrine the importer must not drift
from — deterministic identity minting, the carrier-epoch honesty rule, STOP
on unknown vocabulary, and window offsets relative to the scheduled day.
"""

from ..mappings import payload_to_osb as mapping


def _payload(**over):
    base = {
        "formatVersion": "osb360i/1.0",
        "source": {
            "studyId": "proj-05bb9341-study",
            "buildHash": "b" * 64,
            "projectId": "proj-05bb9341",
            "projectName": "Widget Phase 3",
        },
        "study": {
            "name": "WID-301",
            "studyNumberSource": "WID-301",
            "acronym": "WID301",
            "registryIdentifiers": {},
            "attributes": {"sponsor": "Widget Pharma"},
        },
        "epochs": [
            {"name": "Screening", "ordinal": 1, "visitRefs": ["V_SCREEN"]},
            {"name": "Treatment Period", "ordinal": 2, "visitRefs": ["V_BASE", "V_W4"]},
        ],
        "visits": [
            {"refKey": "V_SCREEN", "name": "Screening", "ordinal": 1, "type": "scheduled",
             "epochRef": 0, "scheduleDay": -14, "minDay": -16, "maxDay": -12},
            {"refKey": "V_BASE", "name": "Baseline", "ordinal": 2, "type": "scheduled",
             "epochRef": 1, "scheduleDay": 0},
            {"refKey": "V_W4", "name": "Week 4", "ordinal": 3, "type": "scheduled",
             "epochRef": 1, "scheduleDay": 28, "minDay": 25, "maxDay": 31},
        ],
        "arms": [{"name": "Widgetinib 10 mg", "description": "Active"}],
        "nonArmGroupClasses": [],
        "odm": {
            "forms": [],
            "codelists": [
                {"name": "X360I_CL_abcd1234", "terms": [
                    {"decode": "Mild", "value": "MILD", "order": 1},
                    {"decode": "Severe", "value": "SEVERE", "order": 2},
                ]}
            ],
            "units": ["mmHg"],
        },
        "formVisitMatrix": [],
        "sourceBundle": {},
        "_mappingCensus": {"observed": {"unmapped": 0}},
    }
    base.update(over)
    return base


def test_project_number_is_deterministic_and_project_scoped():
    p = _payload()
    assert mapping.project_number_for(p) == mapping.project_number_for(p)
    assert mapping.project_number_for(p).startswith("360I-")
    # A different project yields a different number.
    q = _payload(source={**p["source"], "projectId": "proj-other"})
    assert mapping.project_number_for(q) != mapping.project_number_for(p)


def test_study_number_is_deterministic_numeric():
    p = _payload()
    n1 = mapping.study_number_for(p)
    assert n1 == mapping.study_number_for(p)
    assert n1.isdigit()


def test_stated_epochs_pass_through_without_scaffolding():
    plans, scaffolded = mapping.epochs_plan(_payload())
    assert not scaffolded
    assert [p["name"] for p in plans] == ["Screening", "Treatment Period"]
    assert all(not p["scaffolding"] for p in plans)


def test_no_epochs_yields_one_declared_carrier():
    # The honesty rule: OSB structurally requires an epoch per visit; when the
    # protocol stated none the importer creates ONE carrier and DECLARES it —
    # never derived from visit categories, never passed off as protocol content.
    plans, scaffolded = mapping.epochs_plan(_payload(epochs=[]))
    assert scaffolded
    assert len(plans) == 1
    assert plans[0]["scaffolding"]
    assert plans[0]["visit_refs"] == ["V_SCREEN", "V_BASE", "V_W4"]


def test_visit_plan_windows_are_offsets_and_anchor_is_day_zero():
    plans = mapping.visit_plan(_payload(), {})
    by_ref = {p["refKey"]: p for p in plans}
    assert by_ref["V_SCREEN"]["min_window"] == -2
    assert by_ref["V_SCREEN"]["max_window"] == 2
    assert by_ref["V_W4"]["min_window"] == -3
    assert by_ref["V_W4"]["max_window"] == 3
    # Baseline (day 0) is the global anchor, not the first visit.
    assert by_ref["V_BASE"]["is_global_anchor_visit"]
    assert not by_ref["V_SCREEN"]["is_global_anchor_visit"]
    # Protocol-stated names ride verbatim (visit-naming doctrine).
    assert by_ref["V_W4"]["visit_name"] == "Week 4"


def test_unknown_visit_type_stops_instead_of_coercing():
    p = _payload()
    p["visits"][0] = {**p["visits"][0], "type": "telepathic"}
    plans = mapping.visit_plan(p, {})
    stopped = [x for x in plans if x.get("stop")]
    assert len(stopped) == 1
    assert stopped[0]["refKey"] == "V_SCREEN"
    assert "telepathic" in stopped[0]["stop"]


def test_epoch_subtype_candidates_try_stated_name_first():
    candidates = mapping.epoch_subtype_candidates("Treatment Period")
    assert candidates[0] == "Treatment Period"
    assert "Treatment" in candidates  # the Period-stripped fallback


def test_codelists_and_units_plans():
    p = _payload()
    cls = mapping.codelists_plan(p)
    assert cls[0]["name"] == "X360I_CL_abcd1234"
    assert [t["submission_value"] for t in cls[0]["terms"]] == ["MILD", "SEVERE"]
    assert mapping.units_plan(p) == ["mmHg"]


def test_vendor_ext_is_one_sorted_json_blob():
    item = {"vendorExtensions": {"b": "2", "a": "1"}}
    assert mapping.vendor_ext_value(item) == '{"a": "1", "b": "2"}'
    assert mapping.vendor_ext_value({"vendorExtensions": {}}) is None
