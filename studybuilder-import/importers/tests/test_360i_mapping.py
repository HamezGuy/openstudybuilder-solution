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


def test_anchor_is_earliest_visit_and_times_rebase_to_it_when_no_day_zero():
    # OSB requires the anchor at day 0 AND visit_number increasing with time.
    # With no day-0 visit, the EARLIEST dated visit is the anchor and its day
    # becomes the origin: it rebases to 0 and every other visit gets a positive
    # offset (never a negative time before the anchor, which OSB rejects).
    p = _payload(epochs=[], visits=[
        {"refKey": "V_D1", "name": "Day 1", "ordinal": 1, "type": "scheduled",
         "scheduleDay": 1},
        {"refKey": "V_DN10", "name": "Day -10", "ordinal": 2, "type": "scheduled",
         "scheduleDay": -10},
        {"refKey": "V_D20", "name": "Day 20", "ordinal": 3, "type": "scheduled",
         "scheduleDay": 20},
    ])
    plans = {pl["refKey"]: pl for pl in mapping.visit_plan(p, {})}
    # Earliest (day -10) is the anchor, rebased to 0.
    assert plans["V_DN10"]["is_global_anchor_visit"] is True
    assert plans["V_DN10"]["time_value"] == 0
    assert plans["V_DN10"]["visit_number"] == 1
    # Others rebased relative to -10: day 1 -> +11, day 20 -> +30.
    assert plans["V_D1"]["time_value"] == 11 and plans["V_D1"]["visit_number"] == 2
    assert plans["V_D20"]["time_value"] == 30 and plans["V_D20"]["visit_number"] == 3
    # No visit sits before the anchor.
    assert all(pl["time_value"] >= 0 for pl in plans.values())
    # visit_number strictly increases with time_value.
    ordered = sorted(plans.values(), key=lambda pl: pl["time_value"])
    assert [pl["visit_number"] for pl in ordered] == [1, 2, 3]


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


# ----------------------------------------------------------------------------
# Upsert diff — the pure classification the importer executes on re-import.
# ----------------------------------------------------------------------------


def _visit_current_from_plan(payload, uid_by_ref, epoch_uid_by_ref=None):
    """Build a 'current OSB state' snapshot that MATCHES the payload's plans,
    so a diff against it is all-unchanged unless the caller perturbs it. Mirrors
    the compare-field extraction the importer's _current_visits_by_ref does."""
    current = {}
    for plan in mapping.visit_plan(payload, epoch_uid_by_ref or {}):
        if plan.get("stop"):
            continue
        ref = plan["refKey"]
        if ref not in uid_by_ref:
            continue
        current[ref] = {"uid": uid_by_ref[ref]}
        for f in mapping.VISIT_COMPARE_FIELDS:
            current[ref][f] = plan.get(f)
    return current


def test_visit_diff_all_unchanged_when_state_matches_payload():
    # Idempotence at the diff level: re-import of the same payload against the
    # state it produced yields zero create/patch/delete — the no-op the
    # runbook's idempotence gate requires, now true for CHANGED-structure
    # re-imports too (not just the byte-identical hash-gate short-circuit).
    p = _payload()
    uids = {"V_SCREEN": "Visit_1", "V_BASE": "Visit_2", "V_W4": "Visit_3"}
    current = _visit_current_from_plan(p, uids)
    diff = mapping.visit_diff(p, current)
    assert diff["create"] == []
    assert diff["patch"] == []
    assert diff["delete"] == []
    assert {e["ref"] for e in diff["unchanged"]} == {"V_SCREEN", "V_BASE", "V_W4"}


def test_visit_diff_changed_window_becomes_targeted_patch():
    p = _payload()
    uids = {"V_SCREEN": "Visit_1", "V_BASE": "Visit_2", "V_W4": "Visit_3"}
    current = _visit_current_from_plan(p, uids)
    # OSB currently holds a different max window for Week 4 than the payload now
    # states -> exactly one PATCH, naming the changed field, on the right uid.
    current["V_W4"]["max_window"] = 99
    diff = mapping.visit_diff(p, current)
    assert len(diff["patch"]) == 1
    patch = diff["patch"][0]
    assert patch["ref"] == "V_W4"
    assert patch["uid"] == "Visit_3"
    assert "max_window" in patch["changed"]
    assert {e["ref"] for e in diff["unchanged"]} == {"V_SCREEN", "V_BASE"}
    assert diff["create"] == [] and diff["delete"] == []


def test_visit_diff_new_visit_creates_removed_visit_deletes():
    p = _payload()
    # OSB holds an extra visit (V_OLD) the payload no longer mentions, and is
    # missing V_W4 which the payload still has -> one create, one delete.
    uids = {"V_SCREEN": "Visit_1", "V_BASE": "Visit_2", "V_OLD": "Visit_9"}
    current = _visit_current_from_plan(p, uids)
    current["V_OLD"] = {"uid": "Visit_9"}  # present in OSB, absent from payload
    diff = mapping.visit_diff(p, current)
    assert [pl["refKey"] for pl in diff["create"]] == ["V_W4"]
    assert [e["ref"] for e in diff["delete"]] == ["V_OLD"]


def test_visit_diff_stop_passes_through_untouched():
    p = _payload()
    p["visits"][0] = {**p["visits"][0], "type": "telepathic"}
    diff = mapping.visit_diff(p, {})
    assert len(diff["stop"]) == 1
    assert diff["stop"][0]["refKey"] == "V_SCREEN"


def test_arm_diff_rename_is_patch_not_recreate():
    p = _payload()
    # OSB holds the arm under its name with an old description -> a PATCH on the
    # same uid, never a duplicate create.
    current = {
        "Widgetinib 10 mg": {
            "uid": "StudyArm_1",
            "name": "Widgetinib 10 mg",
            "short_name": "Widgetinib 10 mg"[:20],
            "description": "OLD description",
        }
    }
    diff = mapping.arm_diff(p, current)
    assert len(diff["patch"]) == 1
    assert diff["patch"][0]["uid"] == "StudyArm_1"
    assert "description" in diff["patch"][0]["changed"]
    assert diff["create"] == [] and diff["delete"] == []


def test_arm_diff_removed_arm_deletes():
    p = _payload()
    current = {
        "Widgetinib 10 mg": {
            "uid": "StudyArm_1",
            "name": "Widgetinib 10 mg",
            "short_name": "Widgetinib 10 mg"[:20],
            "description": "Active",
        },
        "Placebo": {"uid": "StudyArm_2", "name": "Placebo", "short_name": "Placebo", "description": None},
    }
    diff = mapping.arm_diff(p, current)
    assert [e["ref"] for e in diff["delete"]] == ["Placebo"]
    assert {e["ref"] for e in diff["unchanged"]} == {"Widgetinib 10 mg"}


def test_odm_concept_diff_content_compare_skips_unchanged():
    # Same content sha on both sides -> unchanged (no version-churn); a changed
    # sha -> patch on the existing uid; a brand-new ref -> create; a vanished
    # ref -> delete. This is the ODM version->PATCH->approve gate's decision.
    desired = {
        "IT.A": {"content": "sha_same"},
        "IT.B": {"content": "sha_new"},
        "IT.C": {"content": "sha_c"},
    }
    current = {
        "IT.A": {"uid": "OdmItem_1", "content": "sha_same"},
        "IT.B": {"uid": "OdmItem_2", "content": "sha_OLD"},
        "IT.GONE": {"uid": "OdmItem_9", "content": "sha_x"},
    }
    diff = mapping.odm_concept_diff(desired, current)
    assert [e["ref"] for e in diff["unchanged"]] == ["IT.A"]
    assert [e["ref"] for e in diff["patch"]] == ["IT.B"]
    assert [e["ref"] for e in diff["create"]] == ["IT.C"]
    assert [e["ref"] for e in diff["delete"]] == ["IT.GONE"]


def test_odm_concept_diff_missing_stored_sha_forces_patch():
    # A concept imported before content stamping (no stored sha) must be
    # conservatively PATCHed, never silently treated as unchanged.
    desired = {"IT.A": {"content": "sha_a"}}
    current = {"IT.A": {"uid": "OdmItem_1", "content": None}}
    diff = mapping.odm_concept_diff(desired, current)
    assert [e["ref"] for e in diff["patch"]] == ["IT.A"]
    assert diff["unchanged"] == []


def test_odm_item_text_gets_default_length_when_unstated():
    # OSB rejects a text/string item with null length; the mapper must supply
    # a default so the item validates instead of being censused as failed.
    body = mapping.odm_item_body(
        {"name": "Comment", "refKey": "IT.CMT", "datatype": "text"}, {}, {}
    )
    assert body["length"] == 200
    # A stated length is honored, not overridden.
    body2 = mapping.odm_item_body(
        {"name": "Comment", "refKey": "IT.CMT", "datatype": "string", "length": 40}, {}, {}
    )
    assert body2["length"] == 40
    # Non-text datatypes keep whatever was stated (including None).
    body3 = mapping.odm_item_body(
        {"name": "Dose", "refKey": "IT.DOSE", "datatype": "float"}, {}, {}
    )
    assert body3["length"] is None


def test_content_sha_is_stable_and_order_independent():
    a = mapping.content_sha({"x": 1, "y": [1, 2], "z": "q"})
    b = mapping.content_sha({"z": "q", "y": [1, 2], "x": 1})
    assert a == b
    assert a != mapping.content_sha({"x": 2, "y": [1, 2], "z": "q"})


# Item-group ownership — OSB enforces one-item-one-group and rejects an item-ref
# batch atomically. A catch-all group that re-lists domain fields must NOT steal
# them, and the items unique to it must still wire (regression: a payload's
# Medical History "MH_OTHER" catch-all silently dropped all 140 of its items).

def _odm_two_groups():
    # AE is a domain form (one shared + one unique item); MH_OTHER is a catch-all
    # (mostly shared items re-listed from other forms, plus one unique item).
    return {
        "forms": [
            {
                "refKey": "F_AE",
                "itemGroups": [
                    {
                        "refKey": "G_AE",
                        "orderNumber": 1,
                        "items": [
                            {"refKey": "AETERM", "orderNumber": 1},
                            {"refKey": "AESER", "orderNumber": 2},
                        ],
                    }
                ],
            },
            {
                "refKey": "F_MH",
                "itemGroups": [
                    {
                        "refKey": "G_MH_OTHER",
                        "orderNumber": 1,
                        "items": [
                            {"refKey": "AETERM", "orderNumber": 1},  # shared w/ AE
                            {"refKey": "MH_ONLY", "orderNumber": 2},  # unique here
                        ],
                    }
                ],
            },
        ]
    }


def test_ownership_domain_form_wins_over_catch_all():
    plan = mapping.item_group_ownership(_odm_two_groups())
    # AETERM is shared; the group with FEWER shared items (the AE domain form) owns
    # it, not the catch-all.
    assert plan["owner"]["AETERM"] == "G_AE"
    assert plan["owner"]["AESER"] == "G_AE"
    assert plan["owner"]["MH_ONLY"] == "G_MH_OTHER"


def test_ownership_unique_catch_all_item_still_wires():
    plan = mapping.item_group_ownership(_odm_two_groups())
    # The item unique to the catch-all must still be wired into it — the whole
    # point of the fix (previously the atomic-batch rejection dropped it too).
    assert "MH_ONLY" in plan["wired"]["G_MH_OTHER"]
    # AE keeps both of its items.
    assert plan["wired"]["G_AE"] == ["AETERM", "AESER"]


def test_ownership_duplicate_reference_is_carried_not_wired():
    plan = mapping.item_group_ownership(_odm_two_groups())
    # The duplicate (AETERM re-listed under the catch-all) is censused, never
    # posted — so the no-data-loss accounting stays honest.
    carried = plan["carried"]
    assert carried == [
        {"item": "AETERM", "group": "G_MH_OTHER", "owner": "G_AE"}
    ]
    assert "AETERM" not in plan["wired"].get("G_MH_OTHER", [])


def test_ownership_every_item_wired_exactly_once():
    plan = mapping.item_group_ownership(_odm_two_groups())
    wired = [ref for refs in plan["wired"].values() for ref in refs]
    # No item wired twice; every distinct item wired once.
    assert len(wired) == len(set(wired))
    assert set(wired) == {"AETERM", "AESER", "MH_ONLY"}


def test_ownership_is_deterministic():
    odm = _odm_two_groups()
    assert mapping.item_group_ownership(odm) == mapping.item_group_ownership(odm)
