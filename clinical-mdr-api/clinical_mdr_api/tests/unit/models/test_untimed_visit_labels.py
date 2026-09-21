"""Unknown fixed timing must not be presented to users as day/week zero."""
import datetime

import pytest

from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import StudyStatus
from clinical_mdr_api.domains.study_selections.study_visit import NumericValue, StudyVisitVO
from common.utils import VisitClass, VisitTimingMode


def visit(mode):
    return StudyVisitVO(
        visit_window_min=None, visit_window_max=None, window_unit_uid=None,
        description=None, start_rule=None, end_rule=None, visit_contact_mode=None,
        visit_type=None, status=StudyStatus.DRAFT,
        start_date=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        author_id="test", author_username="test",
        visit_class=VisitClass.MANUALLY_DEFINED_VISIT, visit_subclass=None,
        is_global_anchor_visit=False, visit_number=1, visit_order=1, show_visit=True,
        timing_mode=mode,
    )


@pytest.mark.parametrize(
    "field,label,zero_label",
    [
        ("study_day", "study_day_label", "Day 0"),
        ("study_week", "study_week_label", "Week 0"),
        ("week_in_study", "week_in_study_label", "Week 0"),
        ("study_duration_days", "study_duration_days_label", "0 days"),
        ("study_duration_weeks", "study_duration_weeks_label", "0 weeks"),
    ],
)
def test_unfixed_timing_and_a_known_zero_have_distinct_labels(field, label, zero_label):
    untimed = visit(VisitTimingMode.UNTIMED)
    assert getattr(untimed, field) is None
    assert getattr(untimed, label) == "Not fixed"
    assert untimed.get_absolute_duration() is None

    standard = visit(VisitTimingMode.STANDARD)
    setattr(standard, field, NumericValue(uid="Value_zero", value=0))
    assert getattr(standard, label) == zero_label
