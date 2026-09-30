"""Exact native timing facts for the portable execution draft."""

from decimal import Decimal, InvalidOperation
from enum import Enum
from fractions import Fraction

from neomodel import db

from clinical_mdr_api.domain_repositories.models.concepts import UnitDefinitionRoot
from clinical_mdr_api.services.studies.study_native_library_snapshot import StudyNativeLibrarySnapshot
from clinical_mdr_api.models.study_selections.visit_timing import timing_is_repeating
from common.config import settings


def read_visit_units(visit, *, study_uid, study_value_version, as_of):
    snapshot = StudyNativeLibrarySnapshot(study_uid, study_value_version, as_of=as_of)
    # Native visit readback uses the configured day convention. Bind the
    # independently dated unit to that convention instead of guessing a label.
    rows, _ = db.cypher_query("""
        MATCH (root:UnitDefinitionRoot)-[state:HAS_VERSION]->(value:UnitDefinitionValue)
        WHERE value.name = $day_unit_name AND state.status IN ['Final', 'Retired']
          AND state.start_date <= $as_of
          AND (state.end_date IS NULL OR $as_of < state.end_date)
        RETURN DISTINCT root.uid
    """, {"as_of": snapshot.as_of, "day_unit_name": settings.day_unit_name})
    day_uid = rows[0][0] if len(rows) == 1 else None
    if day_uid is None:
        snapshot.missing("dayUnit", None, "NATIVE_DAY_REFERENCE_NOT_UNIQUE")
    window_uid = visit.get("visit_window_unit_uid")
    return {
        "studyUid": study_uid, "studyValueVersion": study_value_version,
        "visitUid": visit["uid"], "asOf": snapshot.as_of.isoformat(),
        "nativeReadConvention": {
            "dayUnitName": settings.day_unit_name,
            "dayUnitConversionFactorToMaster": settings.day_unit_conversion_factor_to_master,
        },
        "window": snapshot.unit(UnitDefinitionRoot.nodes.get_or_none(uid=window_uid)) if window_uid else None,
        "day": snapshot.unit(UnitDefinitionRoot.nodes.get_or_none(uid=day_uid)) if day_uid else None,
        "issues": snapshot.issues, "records": snapshot.records,
    }


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a native timing quantity")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as error:
        raise ValueError("Invalid native timing quantity") from error
    if not result.is_finite():
        raise ValueError("Non-finite native timing quantity")
    return result


def _enum(value):
    return value.value if isinstance(value, Enum) else value


def project_visit_timing(visit, *, read_units, issue):
    """Return supported portable facts and the exact unit evidence, if needed."""
    result = {}
    if _enum(visit.get("timing_mode")) == "UNTIMED":
        # Exact nominal/calendar rules remain in the native visit record. They
        # cannot supply an enrollment-relative day or an executable window.
        return {"repeating": timing_is_repeating(visit.get("untimed_timing"))}, None
    visit_class, subclass = _enum(visit.get("visit_class")), _enum(visit.get("visit_subclass"))
    repeating = subclass == "REPEATING_VISIT" or visit.get("repeating_frequency_uid") is not None or visit.get("repeating_frequency") is not None
    if repeating:
        result["repeating"] = True
    elif visit_class in {
        "SINGLE_VISIT", "SPECIAL_VISIT", "NON_VISIT", "UNSCHEDULED_VISIT",
        "MANUALLY_DEFINED_VISIT",
    } and subclass in {None, "SINGLE_VISIT", "ADDITIONAL_SUBVISIT_IN_A_GROUP_OF_SUBV", "ANCHOR_VISIT_IN_GROUP_OF_SUBV"}:
        result["repeating"] = False
    else:
        issue("OSB_VISIT_REPETITION_UNKNOWN", "repeating", "Resolve the native visit class/subclass; no repetition boolean was inferred.")
    day = visit.get("study_day_number")
    if day is None:
        return result, None
    day = _number(day)
    if day != day.to_integral_value():
        issue("OSB_VISIT_DAY_NOT_REPRESENTABLE", "scheduleDay", "A portable day ordinal cannot represent this native day exactly.")
        return result, None
    result["scheduleDay"] = int(day)
    bounds = {
        target: _number(value) for target, value, sentinel in (
            ("minDay", visit.get("min_visit_window_value"), -9999),
            ("maxDay", visit.get("max_visit_window_value"), 9999),
        ) if value is not None and value != sentinel
    }
    evidence = read_units() if any(value != 0 for value in bounds.values()) else None
    factor = None
    if evidence is not None:
        window, reference = evidence.get("window") or {}, evidence.get("day") or {}
        source, day_source = window.get("source") or {}, reference.get("source") or {}
        properties, day_properties = source.get("properties", {}), day_source.get("properties", {})
        try:
            if not source or not day_source or window.get("uid") != visit.get("visit_window_unit_uid"):
                raise ValueError("Unresolved or mismatched unit identity")
            dimension = ((window.get("ctDimension") or {}).get("term") or {}).get("uid")
            day_dimension = ((reference.get("ctDimension") or {}).get("term") or {}).get("uid")
            if dimension is None or dimension != day_dimension:
                raise ValueError("Window and day units do not have the same exact native dimension")
            if any(item.get(flag) is not False
                   for item in (properties, day_properties)
                   for flag in ("use_complex_unit_conversion", "use_molecular_weight")):
                raise ValueError("Nonlinear or unknown native conversion cannot supply a day bound")
            unit_factor = _number(properties.get("conversion_factor_to_master"))
            day_factor = _number(day_properties.get("conversion_factor_to_master"))
            if unit_factor <= 0 or day_factor <= 0:
                raise ValueError("Nonpositive unit factor")
            expected = _number((evidence.get("nativeReadConvention") or {}).get(
                "dayUnitConversionFactorToMaster",
            ))
            if day_factor != expected:
                raise ValueError("The unit disagrees with the actual native day readback convention")
            factor = Fraction(unit_factor) / Fraction(day_factor)
        except ValueError:
            issue("OSB_VISIT_WINDOW_UNIT_REVIEW_REQUIRED", "visit_window_unit_uid",
                  "Resolve the exact native window/day units and linear conversion before using nonzero day bounds.")
    for target, value in bounds.items():
        if value == 0:
            result[target] = int(day)
        elif factor is not None:
            bound = Fraction(day) + Fraction(value) * factor
            if bound.denominator != 1:
                issue("OSB_VISIT_WINDOW_DAY_NOT_REPRESENTABLE", target,
                      "The exact native window is a fractional day; retain its canonical timing and author a supported execution window.")
            else:
                result[target] = int(bound)
    return result, evidence
