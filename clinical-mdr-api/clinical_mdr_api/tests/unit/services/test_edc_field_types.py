"""The EDC field-type resolution must never degrade silently.

The AccuraTrial EDC maps any unknown field type to 'text' without a word
(resolveFieldType). These tests pin the exporter's counter-doctrine: an
x360i-stamped type is restored exactly; an unstamped ODM datatype resolves
through a CLOSED map; anything outside either path ships as 'text' WITH a
downgrade reason the caller must census.
"""

from clinical_mdr_api.services.integrations.edc_field_types import (
    EDC_CANONICAL_FIELD_TYPES,
    ODM_TO_EDC,
    resolve_edc_field_type,
)


def test_x360i_stamp_wins_and_is_exact():
    edc_type, downgrade = resolve_edc_field_type(
        "text", "blood_pressure", has_codelist=False, multi_choice=False
    )
    assert edc_type == "blood_pressure"
    assert downgrade is None


def test_unknown_stamp_downgrades_loudly():
    edc_type, downgrade = resolve_edc_field_type(
        "text", "hologram", has_codelist=False, multi_choice=False
    )
    assert edc_type == "text"
    assert downgrade is not None
    assert "hologram" in downgrade


def test_coded_items_become_choice_controls():
    assert resolve_edc_field_type("text", None, True, False) == ("select", None)
    assert resolve_edc_field_type("text", None, True, True) == ("checkbox", None)


def test_odm_datatypes_resolve_through_the_closed_map():
    assert resolve_edc_field_type("integer", None, False, False) == ("number", None)
    assert resolve_edc_field_type("float", None, False, False) == ("decimal", None)
    assert resolve_edc_field_type("boolean", None, False, False) == ("yesno", None)
    assert resolve_edc_field_type("datetime", None, False, False) == ("datetime", None)
    assert resolve_edc_field_type("base64Binary", None, False, False) == ("file", None)


def test_unknown_odm_datatype_downgrades_loudly():
    edc_type, downgrade = resolve_edc_field_type(
        "quantumState", None, has_codelist=False, multi_choice=False
    )
    assert edc_type == "text"
    assert "quantumState".lower() in downgrade


def test_every_map_target_is_edc_canonical():
    # The closed map must only emit values the EDC recognizes — anything else
    # would silently become 'text' on the EDC side, the exact hazard this
    # module exists to prevent.
    for target in ODM_TO_EDC.values():
        assert target in EDC_CANONICAL_FIELD_TYPES
