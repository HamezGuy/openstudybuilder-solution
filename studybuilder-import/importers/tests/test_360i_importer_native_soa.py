"""Governed native Schedule of Activities mapping and reconciliation tests."""

import copy

import pytest

from ..mappings import payload_to_osb as mapping
from ..run_import_360i import Import360i, ImportCensus


def _payload(*, second_cell=True, joined=True):
    visits = [
        {
            "sourceVisitId": "SV1",
            "name": "Screening",
            "order": 1,
            "payloadVisitRef": "V1" if joined else None,
            "payloadVisitOrdinal": 1 if joined else None,
        },
        {
            "sourceVisitId": "SV2",
            "name": "Baseline",
            "order": 2,
            "payloadVisitRef": "V2",
            "payloadVisitOrdinal": 2,
        },
    ]
    schedules = [
        {
            "activityRef": "ACT-DEMO",
            "sourceVisitId": "SV1",
            "payloadVisitRef": "V1" if joined else None,
            "value": "X",
            "required": True,
            "conditional": None,
            "footnoteRefs": [],
        }
    ]
    if second_cell:
        schedules.append(
            {
                "activityRef": "ACT-DEMO",
                "sourceVisitId": "SV2",
                "payloadVisitRef": "V2",
                "value": "X",
                "required": True,
                "conditional": None,
                "footnoteRefs": [],
            }
        )
    joined_visits = sum(1 for visit in visits if visit["payloadVisitRef"])
    joined_cells = sum(1 for cell in schedules if cell["payloadVisitRef"])
    return {
        "scheduleOfActivities": {
            "visits": visits,
            "activities": [
                {
                    "refKey": "ACT-DEMO",
                    "name": "Demographics",
                    "order": 1,
                    "category": None,
                    "cdashDomain": "DM",
                }
            ],
            "schedules": schedules,
            "footnotes": [],
            "reconciliation": {
                "sourceVisits": len(visits),
                "sourceActivities": 1,
                "sourceScheduleCells": len(schedules),
                "joinedVisits": joined_visits,
                "unjoinedVisitIds": [
                    visit["sourceVisitId"]
                    for visit in visits
                    if not visit["payloadVisitRef"]
                ],
                "joinedScheduleCells": joined_cells,
                "unjoinedScheduleCells": len(schedules) - joined_cells,
                "balanced": True,
            },
        }
    }


def _library_activity(uid="Activity_1", name="Demographics", status="Final"):
    return {
        "uid": uid,
        "name": name,
        "status": status,
        "activity_groupings": [],
    }


def test_native_soa_plan_uses_one_final_exact_normalized_activity_only():
    plan = mapping.native_soa_plan(
        _payload(second_cell=False),
        [
            _library_activity("Activity_Draft", "Demographics", "Draft"),
            _library_activity("Activity_Final", " DEMOGRAPHICS! ", "Final"),
        ],
    )

    assert plan["blocked"] == []
    assert plan["activities"] == [
        {
            "ref": "ACT-DEMO",
            "name": "Demographics",
            "activity_uid": "Activity_Final",
            "activity_name": " DEMOGRAPHICS! ",
            "flowchart_group_name": "SUBJECT RELATED INFORMATION",
            "activity_group_uid": None,
            "activity_subgroup_uid": None,
        }
    ]
    assert plan["schedules"][0]["ref"] == "ACT-DEMO::V1"


def test_native_soa_plan_blocks_ambiguous_and_unmatched_activity_names():
    ambiguous = mapping.native_soa_plan(
        _payload(second_cell=False),
        [
            _library_activity("Activity_1"),
            _library_activity("Activity_2", "DEMOGRAPHICS"),
        ],
    )
    unmatched = mapping.native_soa_plan(
        _payload(second_cell=False),
        [_library_activity("Activity_1", "Medical History")],
    )

    assert ambiguous["activities"] == []
    assert ambiguous["blocked"][0]["reason"].startswith("multiple Final exact")
    assert unmatched["activities"] == []
    assert unmatched["blocked"][0]["reason"].startswith("no unique Final exact")


def test_native_soa_plan_blocks_unjoined_cells_and_rejects_duplicate_cells():
    plan = mapping.native_soa_plan(
        _payload(second_cell=False, joined=False), [_library_activity()]
    )
    assert plan["schedules"] == []
    assert plan["blocked"] == [
        {
            "kind": "activity_schedule",
            "ref": "ACT-DEMO::SV1",
            "reason": "source SoA visit did not verify against a payload visit",
        }
    ]

    duplicated = _payload(second_cell=False)
    duplicated["scheduleOfActivities"]["schedules"].append(
        copy.deepcopy(duplicated["scheduleOfActivities"]["schedules"][0])
    )
    duplicated["scheduleOfActivities"]["reconciliation"].update(
        sourceScheduleCells=2, joinedScheduleCells=2
    )
    with pytest.raises(ValueError, match="OSB_NATIVE_SOA_SCHEDULE_DUPLICATE"):
        mapping.native_soa_plan(duplicated, [_library_activity()])


class _SoaApi:
    def __init__(
        self, *, selected=None, schedules=None, library=None, flowchart_terms=None
    ):
        self.library = library or [_library_activity()]
        self.selected = list(selected or [])
        self.schedules = list(schedules or [])
        self.posts = []
        self.deletes = []
        self.flowchart_terms = flowchart_terms or [
            {
                "term_uid": "FlowchartGroup_1",
                "name": {
                    "sponsor_preferred_name": "SUBJECT RELATED INFORMATION",
                    "status": "Final",
                },
                "attributes": {"status": "Final"},
            }
        ]

    def get_all_from_api(self, path, params=None):
        if path == "/concepts/activities/activities":
            return self.library
        if path == "/studies/Study_1/study-activities":
            return self.selected
        if path == "/studies/Study_1/study-activity-schedules":
            return self.schedules
        if path == "/ct/codelists/names":
            return [
                {
                    "codelist_uid": "FlowchartGroupCodelist_1",
                    "name": "Flowchart Group",
                }
            ]
        if path == "/ct/terms":
            assert params["codelist_uid"] == "FlowchartGroupCodelist_1"
            return self.flowchart_terms
        raise AssertionError(f"unexpected GET {path}")

    def simple_post_to_api(self, path, body, simple_path=None, params=None):
        self.posts.append((path, body))
        if path == "/studies/Study_1/study-activities":
            row = {
                "study_activity_uid": f"StudyActivity_{len(self.selected) + 1}",
                "activity": {"uid": body["activity_uid"]},
                "study_soa_group": {
                    "soa_group_term_uid": body["soa_group_term_uid"]
                },
            }
            self.selected.append(row)
            return row
        if path == "/studies/Study_1/study-activity-schedules":
            row = {
                "study_activity_schedule_uid": f"Schedule_{len(self.schedules) + 1}",
                "study_activity_uid": body["study_activity_uid"],
                "study_visit_uid": body["study_visit_uid"],
            }
            self.schedules.append(row)
            return row
        raise AssertionError(f"unexpected POST {path}")

    def simple_delete(self, path, simple_path=None):
        uid = path.rsplit("/", 1)[-1]
        self.deletes.append(uid)
        self.schedules = [
            row
            for row in self.schedules
            if row["study_activity_schedule_uid"] != uid
        ]
        return True


def _importer(api):
    importer = object.__new__(Import360i)
    importer.api = api
    importer.census = ImportCensus()
    importer.uid_map = {
        "visits": {"V1": "StudyVisit_1", "V2": "StudyVisit_2"},
        "native_soa_activities": {},
        "native_soa_schedules": {},
        "native_soa_owned_schedules": {},
    }
    return importer


def test_native_soa_create_replay_and_owned_stale_schedule_removal():
    api = _SoaApi()
    importer = _importer(api)
    original = _payload()

    importer.ensure_native_soa(original, "Study_1")

    assert len(api.selected) == 1
    assert len(api.schedules) == 2
    assert len(api.posts) == 3
    assert set(importer.uid_map["native_soa_owned_schedules"]) == {
        "ACT-DEMO::V1",
        "ACT-DEMO::V2",
    }

    importer.ensure_native_soa(original, "Study_1")
    assert len(api.posts) == 3
    assert api.deletes == []

    reduced = _payload(second_cell=False)
    importer.ensure_native_soa(reduced, "Study_1")

    assert api.deletes == ["Schedule_2"]
    assert len(api.schedules) == 1
    assert importer.uid_map["native_soa_schedules"] == {
        "ACT-DEMO::V1": "Schedule_1"
    }
    assert importer.uid_map["native_soa_owned_schedules"] == {
        "ACT-DEMO::V1": "Schedule_1"
    }


def test_native_soa_never_claims_or_deletes_a_reused_external_schedule():
    selected = [
        {
            "study_activity_uid": "StudyActivity_external",
            "activity": {"uid": "Activity_1"},
            "study_soa_group": {"soa_group_term_uid": "FlowchartGroup_1"},
        }
    ]
    schedules = [
        {
            "study_activity_schedule_uid": "Schedule_external",
            "study_activity_uid": "StudyActivity_external",
            "study_visit_uid": "StudyVisit_1",
        }
    ]
    api = _SoaApi(selected=selected, schedules=schedules)
    importer = _importer(api)

    importer.ensure_native_soa(_payload(second_cell=False), "Study_1")
    assert importer.uid_map["native_soa_owned_schedules"] == {}
    assert importer.uid_map["native_soa_schedules"] == {
        "ACT-DEMO::V1": "Schedule_external"
    }

    empty = _payload(second_cell=False)
    empty["scheduleOfActivities"]["schedules"] = []
    empty["scheduleOfActivities"]["reconciliation"].update(
        sourceScheduleCells=0, joinedScheduleCells=0, unjoinedScheduleCells=0
    )
    importer.ensure_native_soa(empty, "Study_1")

    assert api.deletes == []
    assert len(api.schedules) == 1
    assert importer.uid_map["native_soa_schedules"] == {}
    assert importer.census.release_blockers[-1] == {
        "kind": "activity_schedule_external",
        "ref": "Schedule_external",
        "reason": "pre-existing native schedule is outside the source SoA matrix",
    }


def test_native_soa_unmatched_activity_is_release_blocking_and_creates_nothing():
    api = _SoaApi(library=[_library_activity(name="Medical History")])
    importer = _importer(api)

    importer.ensure_native_soa(_payload(second_cell=False), "Study_1")

    assert api.posts == []
    assert importer.census.release_blockers == [
        {
            "kind": "activity",
            "ref": "ACT-DEMO",
            "reason": "no unique Final exact-normalized OSB Activity match",
        }
    ]


def test_native_soa_requires_one_unique_final_flowchart_group_term():
    api = _SoaApi(
        flowchart_terms=[
            {
                "term_uid": "FlowchartGroup_Draft",
                "name": {
                    "sponsor_preferred_name": "SUBJECT RELATED INFORMATION",
                    "status": "Draft",
                },
                "attributes": {"status": "Draft"},
            }
        ]
    )
    importer = _importer(api)

    importer.ensure_native_soa(_payload(second_cell=False), "Study_1")

    assert api.posts == []
    assert importer.census.release_blockers == [
        {
            "kind": "study_activity",
            "ref": "ACT-DEMO",
            "reason": (
                "expected one Final OSB term named 'SUBJECT RELATED INFORMATION' "
                "in codelist 'Flowchart Group', found 0"
            ),
        }
    ]


def test_native_soa_blocks_reuse_with_conflicting_flowchart_group():
    api = _SoaApi(
        selected=[
            {
                "study_activity_uid": "StudyActivity_external",
                "activity": {"uid": "Activity_1"},
                "study_soa_group": {"soa_group_term_uid": "FlowchartGroup_other"},
            }
        ]
    )
    importer = _importer(api)

    importer.ensure_native_soa(_payload(second_cell=False), "Study_1")

    assert api.posts == []
    assert importer.census.release_blockers == [
        {
            "kind": "study_activity",
            "ref": "ACT-DEMO",
            "reason": (
                "matched study activity has a conflicting or missing "
                "Flowchart Group term"
            ),
        }
    ]


def test_age_year_unit_prefers_canonical_year_when_duplicates_exist():
    class _UnitApi:
        def get_all_from_api(self, path, params=None):
            assert path == "/concepts/unit-definitions"
            return [
                {
                    "uid": "Unit_years",
                    "name": "Years",
                    "status": "Final",
                    "unit_subsets": [{"name": "Age Unit"}],
                    "ct_units": [{"term_uid": "C29848", "name": "Year"}],
                },
                {
                    "uid": "Unit_year",
                    "name": "Year",
                    "status": "Final",
                    "unit_subsets": [{"name": "Age Unit"}],
                    "ct_units": [{"term_uid": "C29848", "name": "Year"}],
                },
            ]

    importer = object.__new__(Import360i)
    importer.api = _UnitApi()
    unit, error = importer._lookup_age_year_unit()
    assert error is None
    assert unit["uid"] == "Unit_years"
