from clinical_mdr_api.services.integrations.mapping_decision_v1 import executor_kind_for_family
from clinical_mdr_api.services.integrations.osb_family_map import (
    BLOCKER_ONLY_FAMILIES,
    CAPTURE_SECTION_FAMILIES,
    NATIVE_CREATE_FAMILIES,
    NATIVE_READ_MODELS,
    STUDY_SECTION_FAMILIES,
    canonicalize_family,
)


def test_every_phase4_study_section_has_a_study_executor() -> None:
    required = ("standards", "compounds", "dosing", "instructions")
    for section in required:
        families = STUDY_SECTION_FAMILIES[section]
        assert families, section
        for family in families:
            assert executor_kind_for_family(family) == "study"


def test_every_phase4_capture_section_has_a_capture_executor() -> None:
    required = (
        "forms", "sections_groups", "items", "checks", "conditions",
        "branching", "assignments",
    )
    for section in required:
        families = CAPTURE_SECTION_FAMILIES[section]
        assert families, section
        for family in families:
            assert executor_kind_for_family(family) == "capture"
            assert family in NATIVE_READ_MODELS


def test_plan_aliases_canonicalize_onto_capture_sections() -> None:
    assert canonicalize_family("edit_checks") == "odm_methods"
    assert canonicalize_family("conditions") == "odm_conditions"
    assert canonicalize_family("branching") == "odm_aliases"
    assert canonicalize_family("assignments") == "activity_schedules"
    assert executor_kind_for_family("edit_checks") == "capture"
    assert executor_kind_for_family("branching") == "capture"
    assert executor_kind_for_family("assignments") == "capture"


def test_compounds_and_dosing_are_mapped_but_blocker_only() -> None:
    assert STUDY_SECTION_FAMILIES["compounds"] == ("compound_product_relationships",)
    assert STUDY_SECTION_FAMILIES["dosing"] == ("study_compound_dosing_relationships",)
    assert BLOCKER_ONLY_FAMILIES == {
        "compound_product_relationships",
        "study_compound_dosing_relationships",
    }
    assert "compound_product_relationships" not in NATIVE_CREATE_FAMILIES
    assert "study_compound_dosing_relationships" not in NATIVE_CREATE_FAMILIES


def test_instructions_and_checks_are_native_create_capable() -> None:
    assert "activity_instruction_templates" in NATIVE_CREATE_FAMILIES
    assert "odm_methods" in NATIVE_CREATE_FAMILIES
    assert "odm_conditions" in NATIVE_CREATE_FAMILIES
    assert "odm_forms" in NATIVE_CREATE_FAMILIES
