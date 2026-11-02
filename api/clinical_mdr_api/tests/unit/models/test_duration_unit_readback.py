# pytest fixtures requested for their side effects
# pylint: disable=unused-argument

from types import SimpleNamespace

from clinical_mdr_api.models.utils import from_duration_object_to_value_and_unit


def test_months_read_back_from_time_unit_when_not_a_study_time_unit():
    month = SimpleNamespace(name="months", uid="month-unit")
    calls = []

    def lookup(*, subset):
        calls.append(subset)
        return ([month] if subset == "Time Unit" else []), 1

    value, unit = from_duration_object_to_value_and_unit("P12M", lookup)
    assert value == 12
    assert unit is month
    assert calls == ["Study Time", "Time Unit"]


def test_months_do_not_match_minutes_or_milliseconds():
    def lookup(*, subset):
        return [
            SimpleNamespace(name="minutes", uid="minutes"),
            SimpleNamespace(name="milliseconds", uid="milliseconds"),
        ], 2

    assert from_duration_object_to_value_and_unit("P6M", lookup) == (6, None)


def test_singular_unit_remains_visible_when_plural_is_not_configured():
    day = SimpleNamespace(name="day", uid="day-unit")
    assert from_duration_object_to_value_and_unit(
        "P14D", lambda **kwargs: ([day], 1)
    ) == (14, day)


def test_study_time_precedence_and_plural_preference_are_preserved():
    day = SimpleNamespace(name="day", uid="a")
    days = SimpleNamespace(name="days", uid="b")

    def lookup(**kwargs):
        return ([day, days], 2)

    assert from_duration_object_to_value_and_unit("P1D", lookup) == (1, day)
    assert from_duration_object_to_value_and_unit("P14D", lookup) == (14, days)


def _write(unit, lookup, value=6):
    from clinical_mdr_api.services._utils import create_duration_object_from_api_input

    return create_duration_object_from_api_input(
        value=value,
        unit=unit.uid,
        find_duration_name_by_code=lambda _: unit,
        find_all_study_time_units=lookup,
    )


def test_write_accepts_months_from_time_unit_after_study_time():
    month = SimpleNamespace(name="months", uid="month-unit")
    calls = []

    def lookup(*, subset):
        calls.append(subset)
        return ([month] if subset == "Time Unit" else []), 1

    assert _write(month, lookup) == "P6M"
    assert calls == ["Study Time", "Time Unit"]


def test_write_reads_only_study_time_for_a_study_time_unit():
    week = SimpleNamespace(name="weeks", uid="week-unit")
    calls = []

    def lookup(*, subset):
        calls.append(subset)
        return [week], 1

    assert _write(week, lookup) == "P6W"
    assert calls == ["Study Time"]


def test_write_refuses_a_unit_outside_every_read_back_subset():
    import pytest

    from common.exceptions import ValidationException

    kilogram = SimpleNamespace(name="kg", uid="kg-unit")
    with pytest.raises(ValidationException):
        _write(kilogram, lambda *, subset: ([], 0))
