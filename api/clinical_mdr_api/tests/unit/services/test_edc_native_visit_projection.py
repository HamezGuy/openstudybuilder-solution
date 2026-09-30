"""Native visit DTO facts through the actual portable exporter."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.integrations.edc_export import EdcExportError, EdcExportService
from clinical_mdr_api.services.integrations.edc_native_visit_projection import project_visit_timing
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION, native_study_graph
from common.utils import VisitSubclass


def units(visit, *, factor=604800, **scope):
    def unit(uid, conversion):
        return {
            "uid": uid, "source": {
                "uid": uid, "version": "1.0", "asOf": AS_OF.isoformat(),
                "properties": {"conversion_factor_to_master": conversion,
                               "use_complex_unit_conversion": False, "use_molecular_weight": False},
            },
            "ctDimension": {"term": {"uid": "NativeTimeDimension"}},
        }
    return {
        "studyUid": STUDY_UID, "studyValueVersion": VERSION, "visitUid": visit["uid"],
        "asOf": AS_OF.isoformat(),
        "nativeReadConvention": {"dayUnitConversionFactorToMaster": 86400, "dayUnitName": "day"},
        "window": unit(visit.get("visit_window_unit_uid"), factor),
        "day": unit("NativeDayUnit", 86400), "issues": [],
    }


def export_visit(value, reader=units):
    service = object.__new__(EdcExportService)
    service.census = []
    service._study_value_version = VERSION
    service._native_study_as_of = AS_OF
    service.native_visit_unit_reader = reader
    service.visit_service_cls = SimpleNamespace(get_all_visits=lambda *_args, **_kwargs: [value])
    output = service._visits(STUDY_UID)[0][0]
    return output, service


@pytest.mark.parametrize("factor,minimum,maximum,expected", [
    (604800, -1, 1, (8, 22)),
    (3600, -24, 48, (14, 17)),
    (86400, 0, 0, (15, 15)),
])
def test_actual_visit_dto_export_converts_selected_units_without_truncation(factor, minimum, maximum, expected):
    value = native_study_graph()["visits"][1].value
    value.study_day_number = 15
    value.min_visit_window_value, value.max_visit_window_value = minimum, maximum
    value.visit_window_unit_uid = "NativeWindowUnit"
    before = value.model_dump(mode="json")
    output, service = export_visit(value, lambda row, **kwargs: units(row, factor=factor, **kwargs))
    assert (output["minDay"], output["maxDay"]) == expected
    assert output["scheduleDay"] == 15
    assert value.model_dump(mode="json") == before
    assert not service.census


def test_repeating_native_visit_remains_repeating_and_is_not_an_inferred_loop():
    value = native_study_graph()["visits"][1].value
    value.visit_subclass = VisitSubclass.REPEATING_VISIT
    value.repeating_frequency_uid = "NativeWeeklyFrequency"
    output, _ = export_visit(value)
    assert output["repeating"] is True
    assert not {"condition", "predicate", "defaultTarget"} & output.keys()


@pytest.mark.parametrize("minimum,maximum", [(-9999, 9999), (None, None)])
def test_absent_native_windows_do_not_become_operational_sentinels(minimum, maximum):
    value = native_study_graph()["visits"][1].value
    value.study_day_number = 15
    value.min_visit_window_value, value.max_visit_window_value = minimum, maximum
    output, _ = export_visit(value, lambda *_args, **_kwargs: pytest.fail("No unit lookup is needed for absent windows"))
    assert "minDay" not in output and "maxDay" not in output


def test_fractional_day_window_requires_review_instead_of_rounding():
    visit = {"uid": "Visit_1", "visit_class": "SINGLE_VISIT", "study_day_number": 15,
             "visit_window_unit_uid": "Hour", "min_visit_window_value": -1, "max_visit_window_value": 1}
    issues = []
    result, evidence = project_visit_timing(
        visit, read_units=lambda: units(visit, factor=3600),
        issue=lambda *value: issues.append(value),
    )
    assert "minDay" not in result and "maxDay" not in result
    assert [row[0] for row in issues] == ["OSB_VISIT_WINDOW_DAY_NOT_REPRESENTABLE"] * 2
    assert evidence["window"]["uid"] == "Hour"


@pytest.mark.parametrize("mutation", [
    "missing", "other-dimension", "nonlinear", "unknown-linearity", "different-native-day-convention",
])
def test_unsupported_unit_evidence_never_supplies_a_guessed_bound(mutation):
    visit = {"uid": "Visit_1", "visit_class": "SINGLE_VISIT", "study_day_number": 15,
             "visit_window_unit_uid": "Window", "min_visit_window_value": -1, "max_visit_window_value": 1}
    evidence = units(visit)
    if mutation == "missing":
        evidence["window"]["source"] = None
    elif mutation == "other-dimension":
        evidence["window"]["ctDimension"]["term"]["uid"] = "NativeVolume"
    elif mutation == "nonlinear":
        evidence["window"]["source"]["properties"]["use_complex_unit_conversion"] = True
    elif mutation == "unknown-linearity":
        evidence["window"]["source"]["properties"]["use_complex_unit_conversion"] = None
    else:
        evidence["nativeReadConvention"]["dayUnitConversionFactorToMaster"] = 3600
    issues = []
    result, _ = project_visit_timing(visit, read_units=lambda: evidence, issue=lambda *row: issues.append(row))
    assert "minDay" not in result and "maxDay" not in result
    assert issues[0][0] == "OSB_VISIT_WINDOW_UNIT_REVIEW_REQUIRED"


@pytest.mark.parametrize("field,other_value", [
    ("studyValueVersion", "1.0"), ("asOf", "2025-01-01T00:00:00+00:00"),
])
def test_exporter_refuses_unit_evidence_from_another_selected_snapshot(field, other_value):
    value = native_study_graph()["visits"][1].value
    value.study_day_number = 15
    value.visit_window_unit_uid = "Window"
    def wrong(row, **kwargs):
        evidence = deepcopy(units(row, **kwargs))
        evidence[field] = other_value
        return evidence
    with pytest.raises(EdcExportError, match="SOURCE_SCOPE_MISMATCH"):
        export_visit(value, wrong)
