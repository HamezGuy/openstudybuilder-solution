from copy import deepcopy

import pytest
from pydantic import ValidationError

from clinical_mdr_api.models.study_selections.study_visit import StudyVisitCreateInput
from common.utils import VisitTimingMode


def manual_input(**changes):
    return {
        "study_epoch_uid": "Epoch_source",
        "visit_type": {"term_uid": "VisitType_source"},
        "visit_contact_mode": {"term_uid": "ContactMode_source"},
        "visit_class": "MANUALLY_DEFINED_VISIT",
        "timing_mode": "UNTIMED",
        "untimed_timing": {"kind": "manual_date", "repeating": False},
        "visit_name": "Actual first-dose date reference",
        "visit_short_name": "DOSE1",
        "visit_number": 2,
        "unique_visit_number": 200,
        "show_visit": True,
        "is_global_anchor_visit": False,
        **changes,
    }


@pytest.mark.parametrize(
    "timing",
    [
        {"kind": "manual_date", "repeating": False},
        {"kind": "manual_date", "repeating": True},
        {
            "kind": "event_relative",
            "anchor_visit_uid": "Dose_1",
            "nominal_offset_days": 15,
        },
        {
            "kind": "calendar_repeat",
            "anchor_visit_uid": "Dose_last",
            "interval_months": 1,
            "first_occurrence": 1,
            "last_occurrence": 12,
        },
    ],
)
def test_source_timing_roundtrips_without_absolute_days_or_windows(timing):
    payload = manual_input(untimed_timing=timing)
    before = deepcopy(payload)
    visit = StudyVisitCreateInput(**payload)
    assert payload == before
    assert visit.timing_mode == VisitTimingMode.UNTIMED
    assert visit.untimed_timing.model_dump(mode="json") == timing
    assert all(
        getattr(visit, field) is None
        for field in (
            "time_value",
            "time_unit_uid",
            "time_reference",
            "min_visit_window_value",
            "max_visit_window_value",
            "visit_window_unit_uid",
        )
    )
    assert (
        StudyVisitCreateInput(**visit.model_dump()).model_dump() == visit.model_dump()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"timing_mode": "STANDARD"},
        {"timing_mode": None},
        {"visit_class": "SINGLE_VISIT"},
        {"untimed_timing": None},
        {"visit_name": " "},
        {"visit_short_name": None},
        {"visit_number": None},
        {"unique_visit_number": None},
        {"visit_number": True},
        {"visit_number": float("nan")},
        {"is_global_anchor_visit": True},
        {"time_value": 0},
        {"time_unit_uid": "Month"},
        {"time_reference": {"term_uid": "Anchor"}},
        {"min_visit_window_value": -9999},
        {"max_visit_window_value": 0},
        {"visit_window_unit_uid": "Day"},
        {"visit_sublabel_reference": "OtherStudyVisit"},
        {"visit_subclass": "REPEATING_VISIT"},
        {"repeating_frequency_uid": "Weekly"},
        {"untimed_timing": {"kind": "manual_date", "repeating": "false"}},
        {"untimed_timing": {"kind": "manual_date", "repeating": False, "offset": 0}},
        {
            "untimed_timing": {
                "kind": "event_relative",
                "anchor_visit_uid": " ",
                "nominal_offset_days": 15,
            }
        },
        {
            "untimed_timing": {
                "kind": "event_relative",
                "anchor_visit_uid": "Dose",
                "nominal_offset_days": True,
            }
        },
        {
            "untimed_timing": {
                "kind": "event_relative",
                "anchor_visit_uid": "Dose",
                "nominal_offset_days": 15.5,
            }
        },
        {
            "untimed_timing": {
                "kind": "calendar_repeat",
                "anchor_visit_uid": "Dose",
                "interval_months": 0,
                "first_occurrence": 1,
                "last_occurrence": 12,
            }
        },
        {
            "untimed_timing": {
                "kind": "calendar_repeat",
                "anchor_visit_uid": "Dose",
                "interval_months": 1,
                "first_occurrence": 12,
                "last_occurrence": 1,
            }
        },
        {
            "untimed_timing": {
                "kind": "calendar_repeat",
                "anchor_visit_uid": "Dose",
                "interval_months": 1,
                "first_occurrence": 1,
            }
        },
    ],
)
def test_untimed_mode_rejects_missing_or_invented_timing(change):
    with pytest.raises(ValidationError):
        StudyVisitCreateInput(**manual_input(**change))


def test_existing_standard_input_defaults_are_unchanged():
    payload = manual_input()
    del payload["timing_mode"]
    del payload["untimed_timing"]
    visit = StudyVisitCreateInput(**payload)
    assert visit.timing_mode == VisitTimingMode.STANDARD
    assert visit.min_visit_window_value == -9999
    assert visit.max_visit_window_value == 9999


def test_unknown_contact_mode_is_explicitly_preserved_for_untimed_only():
    payload = manual_input(visit_contact_mode=None)
    visit = StudyVisitCreateInput(**payload)
    assert visit.visit_contact_mode is None
    assert StudyVisitCreateInput(**visit.model_dump()).visit_contact_mode is None
    payload.update(timing_mode="STANDARD", untimed_timing=None)
    with pytest.raises(ValidationError, match="visit_contact_mode"):
        StudyVisitCreateInput(**payload)
    del payload["timing_mode"]
    with pytest.raises(ValidationError, match="visit_contact_mode"):
        StudyVisitCreateInput(**payload)
