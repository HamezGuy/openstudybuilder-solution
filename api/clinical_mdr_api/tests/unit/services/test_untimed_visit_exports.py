from copy import deepcopy

import pytest

from clinical_mdr_api.models.study_selections.study_visit import StudyVisit
from clinical_mdr_api.services.integrations.edc_study_exchange import (
    verify_source_exchange,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import (
    NativeStudySource,
    native_odm_graph,
)
from clinical_mdr_api.tests.fixtures.usdm_native_study import native_study_graph
from clinical_mdr_api.tests.unit.services.test_usdm_native_domains import design


@pytest.mark.parametrize(
    "source_timing,repeating",
    [
        ({"kind": "manual_date", "repeating": False}, False),
        ({"kind": "manual_date", "repeating": True}, True),
        ({"kind": "event_relative", "nominal_offset_days": 15}, False),
        (
            {
                "kind": "calendar_repeat",
                "interval_months": 1,
                "first_occurrence": 1,
                "last_occurrence": 12,
            },
            True,
        ),
    ],
)
def test_native_untimed_visit_survives_full_exchange_without_execution_dates(
    source_timing, repeating
):
    graph = native_study_graph()
    original = graph["visits"][1].value
    source_timing = deepcopy(source_timing)
    if source_timing["kind"] != "manual_date":
        source_timing["anchor_visit_uid"] = graph["visits"][0].value.uid
    values = original.model_dump(mode="json")
    for field in (
        "time_reference",
        "time_value",
        "time_unit_uid",
        "time_unit_name",
        "duration_time",
        "duration_time_unit",
        "study_day_number",
        "study_day_label",
        "study_week_number",
        "study_week_label",
        "study_duration_days",
        "study_duration_days_label",
        "study_duration_weeks",
        "study_duration_weeks_label",
        "week_in_study_label",
        "min_visit_window_value",
        "max_visit_window_value",
        "visit_window_unit_uid",
        "visit_window_unit_name",
    ):
        values[field] = None
    values.update(
        timing_mode="UNTIMED",
        untimed_timing=source_timing,
        visit_class="MANUALLY_DEFINED_VISIT",
        is_global_anchor_visit=False,
        visit_contact_mode=None,
        description="Source schedule; actual contact date is recorded manually.",
        start_rule="No enrollment-derived date.",
        end_rule="Original clinical review hold remains.",
    )
    graph["visits"][1].value = StudyVisit(**values)
    source = NativeStudySource(graph)
    odm = native_odm_graph()
    before = source.source_input(odm)
    bundle = source.export(odm)
    assert source.source_input(odm) == before
    verify_source_exchange(bundle)
    portable = next(
        v for v in bundle["execution"]["visits"] if v["name"] == original.visit_name
    )
    assert portable["type"] == "unscheduled"
    assert portable["repeating"] is repeating
    assert not {"scheduleDay", "minDay", "maxDay"} & portable.keys()
    retained = next(
        row["record"]
        for row in bundle["extensions"]["_osbExport"]["native"]["records"]
        if row["kind"] == "visit" and row["uid"] == original.uid
    )
    assert retained["untimed_timing"] == source_timing
    assert retained["visit_contact_mode"] is None
    assert retained["start_rule"] == values["start_rule"]
    assert retained["end_rule"] == values["end_rule"]
    assert retained["min_visit_window_value"] is None
    assert retained["max_visit_window_value"] is None
    report = bundle["extensions"]["_osbExport"]["mappingReport"]
    assert report["state"] == "incomplete"
    assert any(
        issue["code"] == "USDM_UNTIMED_VISIT_REQUIRES_MANUAL_DATE"
        for issue in report["issues"]
    )
    mapped = design({"document": bundle["definition"]["document"]})
    for timeline in mapped["scheduleTimelines"]:
        for instance in timeline["instances"]:
            assert not instance.get("defaultConditionId")
            assert not instance.get("timelineExitId")
