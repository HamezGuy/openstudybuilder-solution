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
    lookup = lambda **kwargs: ([day, days], 2)
    assert from_duration_object_to_value_and_unit("P1D", lookup) == (1, day)
    assert from_duration_object_to_value_and_unit("P14D", lookup) == (14, days)
