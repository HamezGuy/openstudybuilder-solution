"""Ordinary native APIs on a reserved disposable graph, using current source."""

import json
import os
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.study_selections.study_visit_repository import (
    StudyVisitRepository,
)
from clinical_mdr_api.models.listings.listings_study import StudyVisitListingModel
from clinical_mdr_api.models.study_selections.study_selection import (
    StudyActivityScheduleCreateInput,
)
from clinical_mdr_api.models.study_selections.study_visit import (
    StudyVisitCreateInput,
    StudyVisitEditInput,
)
from clinical_mdr_api.services.studies.study_activity_schedule import (
    StudyActivityScheduleService,
)
from clinical_mdr_api.services.studies.study_visit import StudyVisitService
from clinical_mdr_api.tests.fixtures.null_adjudication_neo4j import graph_fingerprint
from clinical_mdr_api.tests.integration.services import (
    test_study_visits as visit_fixtures,
)
from clinical_mdr_api.tests.integration.utils.factory_activity import (
    create_study_activity,
)
from clinical_mdr_api.tests.integration.utils.factory_epoch import create_study_epoch
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from clinical_mdr_api.tests.unit.models.test_untimed_visit_contract import manual_input
from common.config import settings
from common.exceptions import (
    BusinessLogicException,
    NotFoundException,
    ValidationException,
)


@pytest.fixture(scope="module")
def visit_library():
    assert os.environ.get("OSB_UNTIMED_FIXTURE") == "disposable-igs22"
    assert urlsplit(settings.neo4j_dsn).hostname == "igs22-graph"
    existing = visit_fixtures.TestStudyVisitManagement()
    existing.setUp()
    return existing


@pytest.fixture
def case(visit_library):
    # Share the real library fixture, but isolate every case in a fresh study.
    project = db.cypher_query("MATCH (p:Project) RETURN p.project_number LIMIT 1")[0][
        0
    ][0]
    study = TestUtils.create_study(project_number=project)
    TestUtils.set_study_standard_version(
        study.uid, create_codelists_and_terms_for_package=False
    )
    epoch = create_study_epoch("EpochSubType_0001", study_uid=study.uid)
    return SimpleNamespace(
        study=study,
        epoch1=epoch,
        day_uid=visit_library.day_uid,
        flowchart_group=visit_library.flowchart_group,
    )


def payload(case, number, timing=None, **changes):
    values = dict(
        study_epoch_uid=case.epoch1.uid,
        visit_type={"term_uid": "VisitType_0003"},
        visit_contact_mode={"term_uid": "VisitContactMode_0001"},
        visit_name=f"Source event {number}",
        visit_short_name=f"SRC{number}",
        visit_number=number,
        unique_visit_number=number * 100,
        untimed_timing=timing or {"kind": "manual_date", "repeating": False},
        description="Source-supported observation; no mandated intervention.",
        start_rule="Actual dates are recorded manually.",
        end_rule="Preserve the source clinical review hold.",
    )
    values.update(changes)
    return manual_input(**values)


def create(case, number, timing=None):
    body = payload(case, number, timing)
    result = StudyVisitService(case.study.uid).create(
        case.study.uid, StudyVisitCreateInput(**body)
    )
    return result, body


def assert_untimed(visit, expected):
    data = visit if isinstance(visit, dict) else visit.model_dump(mode="json")
    assert data["timing_mode"] == "UNTIMED"
    assert data["untimed_timing"] == expected
    for field in (
        "time_reference",
        "time_value",
        "time_unit_uid",
        "time_unit_name",
        "duration_time",
        "duration_time_unit",
        "study_day_number",
        "study_week_number",
        "study_duration_days",
        "study_duration_weeks",
        "min_visit_window_value",
        "max_visit_window_value",
        "visit_window_unit_uid",
    ):
        assert data.get(field) is None, (field, data.get(field))
    assert data["is_global_anchor_visit"] is False


def graph_records():
    records = {}
    for kind, query in (
        ("node", "MATCH (n) RETURN elementId(n), labels(n), properties(n)"),
        (
            "relationship",
            "MATCH (a)-[r]->(b) RETURN elementId(r), elementId(a), type(r), elementId(b), properties(r)",
        ),
    ):
        rows, _ = db.cypher_query(query)
        for row in rows:
            if kind == "node":
                row[1].sort()
            records[f"{kind}:{row[0]}"] = json.dumps(
                row[1:], sort_keys=True, default=str
            )
    return records


def assert_graph_records_unchanged(before):
    after = graph_records()
    diff = {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    }
    assert not diff, json.dumps(diff, indent=2)


def test_seven_manual_date_definitions_through_ordinary_http_create_and_read(
    case, api_client
):
    rows = []
    for number in range(1, 8):
        timing = {"kind": "manual_date", "repeating": number == 7}
        if number in (4, 5):
            timing = {
                "kind": "event_relative",
                "anchor_visit_uid": rows[number - 3]["uid"],
                "nominal_offset_days": 15,
            }
        elif number == 6:
            timing = {
                "kind": "calendar_repeat",
                "anchor_visit_uid": rows[2]["uid"],
                "interval_months": 1,
                "first_occurrence": 1,
                "last_occurrence": 12,
            }
        body = payload(case, number, timing)
        if number <= 3:
            body["visit_contact_mode"] = None
        response = api_client.post(f"/studies/{case.study.uid}/study-visits", json=body)
        assert response.status_code == 201, response.text
        row = response.json()
        assert_untimed(row, timing)
        assert row["visit_name"] == body["visit_name"]
        assert row["visit_short_name"] == body["visit_short_name"]
        if number <= 3:
            assert row["visit_contact_mode"] is None
        read = api_client.get(f"/studies/{case.study.uid}/study-visits/{row['uid']}")
        assert read.status_code == 200, read.text
        assert_untimed(read.json(), timing)
        assert read.json()["visit_contact_mode"] == row["visit_contact_mode"]
        rows.append(row)
    for lite in (False, True):
        response = api_client.get(
            f"/studies/{case.study.uid}/study-visits",
            params={"page_size": 0, "lite": str(lite).lower()},
        )
        assert response.status_code == 200, response.text
        actual = {row["uid"]: row for row in response.json()["items"]}
        assert len(actual) == 7
        for expected in rows:
            assert_untimed(actual[expected["uid"]], expected["untimed_timing"])
            if not lite:
                assert (
                    actual[expected["uid"]]["visit_contact_mode"]
                    == expected["visit_contact_mode"]
                )
    listings = StudyVisitListingModel.from_all_study_visits(
        StudyVisitRepository.find_all_visits_by_study_uid(case.study.uid)
    )
    assert len(listings) == 7
    assert {item.name for item in listings if item.contact_model is None} == {
        rows[index]["visit_name"] for index in range(3)
    }
    assert db.cypher_query(
        "MATCH (:StudyVisit)-[:HAS_TIMEPOINT|HAS_STUDY_DAY|HAS_STUDY_WEEK]->() RETURN count(*)"
    )[0] == [[0]]


def test_untimed_preview_has_zero_graph_changes(case):
    service = StudyVisitService(case.study.uid)
    before = graph_records()
    result = service.preview(
        case.study.uid,
        StudyVisitCreateInput(**payload(case, 1, visit_contact_mode=None)),
    )
    assert_untimed(result, {"kind": "manual_date", "repeating": False})
    assert_graph_records_unchanged(before)


def test_cross_study_anchor_and_nested_child_are_refused_without_changes(
    case, api_client
):
    own, _ = create(case, 1)
    project = db.cypher_query("MATCH (p:Project) RETURN p.project_number LIMIT 1")[0][
        0
    ][0]
    other = TestUtils.create_study(number="9951", project_number=project)
    TestUtils.set_study_standard_version(
        other.uid, create_codelists_and_terms_for_package=False
    )
    epoch = create_study_epoch("EpochSubType_0001", study_uid=other.uid)
    foreign_body = payload(case, 2)
    foreign_body["study_epoch_uid"] = epoch.uid
    foreign = StudyVisitService(other.uid).create(
        other.uid, StudyVisitCreateInput(**foreign_body)
    )
    body = payload(
        case,
        3,
        {
            "kind": "event_relative",
            "anchor_visit_uid": foreign.uid,
            "nominal_offset_days": 15,
        },
    )
    before = graph_records()
    with pytest.raises(NotFoundException):
        StudyVisitService(case.study.uid).create(
            case.study.uid, StudyVisitCreateInput(**body)
        )
    assert_graph_records_unchanged(before)
    # Authentication normally persists User.updated on its first request after
    # the cache expires. Authenticate before measuring native read side effects.
    authorized = api_client.get(f"/studies/{case.study.uid}/study-visits/{own.uid}")
    assert authorized.status_code == 200, authorized.text
    before = graph_records()
    response = api_client.get(f"/studies/{other.uid}/study-visits/{own.uid}")
    assert response.status_code == 404
    assert_graph_records_unchanged(before)


def test_cycles_and_anchor_deletion_roll_back_all_native_writes(case):
    first, first_body = create(case, 1)
    second, _ = create(
        case,
        2,
        {
            "kind": "event_relative",
            "anchor_visit_uid": first.uid,
            "nominal_offset_days": 15,
        },
    )
    service = StudyVisitService(case.study.uid)
    cyclic = {
        **first_body,
        "uid": first.uid,
        "visit_name": "Rejected cycle name",
        "untimed_timing": {
            "kind": "event_relative",
            "anchor_visit_uid": second.uid,
            "nominal_offset_days": 15,
        },
    }
    before = graph_fingerprint()
    with pytest.raises(ValidationException, match="Circular"):
        service.edit(case.study.uid, first.uid, StudyVisitEditInput(**cyclic))
    assert graph_fingerprint() == before
    with pytest.raises(ValidationException, match="anchor"):
        service.delete(case.study.uid, first.uid)
    assert graph_fingerprint() == before


def test_failure_after_actual_repository_save_rolls_back_history_names_and_counters(
    case, monkeypatch
):
    service = StudyVisitService(case.study.uid)
    save = service.repo.save

    def fail_after_save(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError("Injected after actual native persistence")

    monkeypatch.setattr(service.repo, "save", fail_after_save)
    before = graph_fingerprint()
    with pytest.raises(RuntimeError, match="after actual native"):
        service.create(case.study.uid, StudyVisitCreateInput(**payload(case, 1)))
    assert graph_fingerprint() == before


def test_native_edit_and_history_preserve_source_rules_and_absent_mode(case):
    first, _ = create(case, 1)
    timing = {
        "kind": "calendar_repeat",
        "anchor_visit_uid": first.uid,
        "interval_months": 1,
        "first_occurrence": 1,
        "last_occurrence": 12,
    }
    monthly, body = create(case, 2, timing)
    service = StudyVisitService(case.study.uid)
    patch = deepcopy(body)
    del patch["timing_mode"]
    del patch["untimed_timing"]
    patch.update(uid=monthly.uid, description="Source description clarified")
    updated = service.edit(case.study.uid, monthly.uid, StudyVisitEditInput(**patch))
    assert_untimed(updated, timing)
    assert updated.start_rule == body["start_rule"]
    assert updated.end_rule == body["end_rule"]
    unknown = service.edit(
        case.study.uid,
        monthly.uid,
        StudyVisitEditInput(**{**body, "uid": monthly.uid, "visit_contact_mode": None}),
    )
    assert unknown.visit_contact_mode is None
    read = StudyVisitService.find_by_uid(case.study.uid, monthly.uid)
    assert read.visit_contact_mode is None
    history = service.audit_trail(monthly.uid, case.study.uid)
    assert len(history) >= 3
    for version in history:
        assert_untimed(version, timing)
    assert any(version.visit_contact_mode is None for version in history)
    assert any(version.visit_contact_mode is not None for version in history)


def test_standard_manual_timing_and_mode_conversion_retain_original_history(case):
    original, body = create(case, 1)
    service = StudyVisitService(case.study.uid)
    timed = {
        **body,
        "uid": original.uid,
        "timing_mode": "STANDARD",
        "untimed_timing": None,
        "time_reference": {"term_uid": "VisitSubType_0005"},
        "time_value": 0,
        "time_unit_uid": case.day_uid,
        "is_global_anchor_visit": True,
    }
    result = service.edit(case.study.uid, original.uid, StudyVisitEditInput(**timed))
    assert result.time_value == 0 and result.study_day_number == 1
    restored = service.edit(
        case.study.uid, original.uid, StudyVisitEditInput(uid=original.uid, **body)
    )
    assert_untimed(restored, body["untimed_timing"])
    history = service.audit_trail(original.uid, case.study.uid)
    assert {row.timing_mode.value for row in history} == {"STANDARD", "UNTIMED"}


def test_activity_schedule_and_duplicate_constraints_work_for_untimed_visits(case):
    visit, _ = create(case, 1)
    group = TestUtils.create_activity_group(name="Untimed native activity group")
    subgroup = TestUtils.create_activity_subgroup(
        name="Untimed native activity subgroup"
    )
    library_activity = TestUtils.create_activity(
        name="Untimed native observation",
        activity_groups=[group.uid],
        activity_subgroups=[subgroup.uid],
    )
    activity = create_study_activity(
        case.study.uid,
        activity_uid=library_activity.uid,
        activity_group_uid=group.uid,
        activity_subgroup_uid=subgroup.uid,
        soa_group_term_uid=case.flowchart_group.term_uid,
    )
    service = StudyActivityScheduleService()
    body = StudyActivityScheduleCreateInput(
        study_activity_uid=activity.study_activity_uid,
        study_visit_uid=visit.uid,
    )
    saved = service.create(case.study.uid, body)
    read = service.get_all_schedules(case.study.uid)
    assert len(read) == 1
    assert read[0].study_activity_schedule_uid == saved.study_activity_schedule_uid
    assert read[0].study_visit_uid == visit.uid
    before = graph_fingerprint()
    with pytest.raises(BusinessLogicException):
        service.create(case.study.uid, body)
    assert graph_fingerprint() == before
