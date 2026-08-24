"""Canonical Phase 4 OSB family map. Study and capture sections share one vocabulary."""

from __future__ import annotations

FAMILY_ALIASES = {
    "edit_checks": "odm_methods",
    "conditions": "odm_conditions",
    "branching": "odm_aliases",
    "assignments": "activity_schedules",
}

STUDY_SECTION_FAMILIES: dict[str, tuple[str, ...]] = {
    "standards": ("controlled_terminology", "controlled_terminology_codelists", "units"),
    "compounds": ("compound_product_relationships",),
    "dosing": ("study_compound_dosing_relationships",),
    "instructions": ("activity_instruction_templates",),
    "criteria": ("criteria_templates",),
    "objectives": ("objective_templates",),
    "endpoints": ("endpoint_templates",),
    "timeframes": ("timeframe_templates", "timeframes"),
    "activities": ("activities",),
}

CAPTURE_SECTION_FAMILIES: dict[str, tuple[str, ...]] = {
    "forms": ("odm_forms",),
    "sections_groups": ("odm_item_groups",),
    "items": ("odm_items",),
    "checks": ("odm_methods",),
    "conditions": ("odm_conditions",),
    "branching": ("odm_aliases",),
    "assignments": ("activity_schedules",),
    "collection_standards": ("cdash_variables",),
}

BLOCKER_ONLY_FAMILIES = frozenset({
    "compound_product_relationships",
    "study_compound_dosing_relationships",
})

STUDY_FAMILIES = frozenset(
    family for families in STUDY_SECTION_FAMILIES.values() for family in families
)
CAPTURE_FAMILIES = frozenset(
    family for families in CAPTURE_SECTION_FAMILIES.values() for family in families
)
SUPPORTED_RESOURCE_FAMILIES = STUDY_FAMILIES | CAPTURE_FAMILIES | frozenset(FAMILY_ALIASES)

NATIVE_READ_MODELS: dict[str, tuple[str, str | None]] = {
    "objective_templates": ("ObjectiveTemplateRoot", "ObjectiveTemplateValue"),
    "endpoint_templates": ("EndpointTemplateRoot", "EndpointTemplateValue"),
    "criteria_templates": ("CriteriaTemplateRoot", "CriteriaTemplateValue"),
    "activity_instruction_templates": (
        "ActivityInstructionTemplateRoot", "ActivityInstructionTemplateValue",
    ),
    "timeframe_templates": ("TimeframeTemplateRoot", "TimeframeTemplateValue"),
    "timeframes": ("TimeframeRoot", "TimeframeValue"),
    "activities": ("ActivityRoot", "ActivityValue"),
    "units": ("UnitDefinitionRoot", "UnitDefinitionValue"),
    "odm_forms": ("OdmFormRoot", "OdmFormValue"),
    "odm_item_groups": ("OdmItemGroupRoot", "OdmItemGroupValue"),
    "odm_items": ("OdmItemRoot", "OdmItemValue"),
    "odm_conditions": ("OdmConditionRoot", "OdmConditionValue"),
    "odm_methods": ("OdmMethodRoot", "OdmMethodValue"),
    "odm_aliases": ("OdmAlias", None),
    "activity_schedules": ("StudyActivitySchedule", None),
    "controlled_terminology": ("CTTermRoot", None),
    "controlled_terminology_codelists": ("CTCodelistRoot", None),
    "cdash_variables": ("DatasetVariable", None),
}

NATIVE_CREATE_FAMILIES = frozenset(
    family for family, model in NATIVE_READ_MODELS.items() if model[1] is not None
)


def canonicalize_family(family: str) -> str:
    return FAMILY_ALIASES.get(family, family)


def executor_kind_for_canonical_family(family: str) -> str | None:
    canonical = canonicalize_family(family)
    if canonical in STUDY_FAMILIES:
        return "study"
    if canonical in CAPTURE_FAMILIES:
        return "capture"
    return None
