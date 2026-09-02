"""Closed Proposal V2 target taxonomy backed by installed OSB native models."""

TARGET_CAPABILITIES = {
    "StudyMetadata": "native_study_mutation",
    "StudyStandardVersion": "native_study_mutation",
    "StudySelectionObjective": "native_study_mutation",
    "StudySelectionEndpoint": "native_study_mutation",
    "StudySelectionCriteria": "native_study_mutation",
    "StudySelectionCompound": "native_study_mutation",
    "StudyCompoundDosing": "native_study_mutation",
    "StudySelectionArm": "native_study_mutation",
    "StudySelectionElement": "native_study_mutation",
    "StudyEpoch": "native_study_mutation",
    "StudyDesignCell": "native_study_mutation",
    "StudyVisit": "native_study_mutation",
    "StudySelectionActivity": "native_study_mutation",
    "StudyActivitySchedule": "native_study_mutation",
    "StudyActivityInstruction": "native_study_mutation",
    "CTTerm": "governed_library_reference",
    "CTCodelist": "governed_library_reference",
    "UnitDefinition": "governed_library_reference",
    "DatasetVariable": "governed_library_reference",
    "Activity": "governed_library_reference",
    "ActivityInstance": "governed_library_reference",
    "Timeframe": "governed_library_reference",
    "MedicinalProduct": "governed_library_reference",
    "PharmaceuticalProduct": "governed_library_reference",
    "OdmForm": "governed_library_reference",
    "OdmItemGroup": "governed_library_reference",
    "OdmItem": "governed_library_reference",
    "IntegrationExtension": "governed_extension",
    "RetainedNarrative": "retained_narrative",
    "Unresolved": "unresolved",
}

# Families with a complete typed existing-route operation/reconciliation plan.
# A valid OSB model name outside this set remains native but non-executable.
NATIVE_EXECUTOR_RESOURCE_TYPES = frozenset(
    {
        "StudyMetadata",
        "StudySelectionArm",
        "StudySelectionElement",
        "StudyEpoch",
        "StudyDesignCell",
        "StudyVisit",
        "StudySelectionObjective",
        "StudySelectionEndpoint",
        "StudySelectionCriteria",
        "StudySelectionActivity",
        "StudyActivitySchedule",
        # IL register GAP-8 (2026-09-02): the importer builds typed operations
        # for these four through the routes named in TARGET_CAPABILITIES.
        "StudyStandardVersion",
        "StudySelectionCompound",
        "StudyCompoundDosing",
        "StudyActivityInstruction",
    }
)

NATIVE_SELECTION_RESOURCE_TYPES = frozenset(
    {
        "StudySelectionObjective",
        "StudySelectionEndpoint",
        "StudySelectionCriteria",
        "StudySelectionActivity",
        "StudySelectionCompound",
        "StudyActivityInstruction",
    }
)

NATIVE_DUAL_MODE_RESOURCE_TYPES = frozenset()

NATIVE_CREATE_REQUEST_RESOURCE_TYPES = frozenset(
    {
        "StudyMetadata",
        "StudySelectionArm",
        "StudySelectionElement",
        "StudyEpoch",
        "StudyDesignCell",
        "StudyVisit",
        "StudyActivitySchedule",
        "StudyStandardVersion",
        "StudyCompoundDosing",
    }
)

# Study attributes a reviewer may decline: a signed not_applicable decision on
# one of these is a recorded deferral, not an execution blocker. The study's
# spine (metadata, design, visits, activities, schedule) stays all-or-nothing.
NATIVE_DECLINABLE_RESOURCE_TYPES = frozenset(
    {
        "StudyStandardVersion",
        "StudySelectionCompound",
        "StudyCompoundDosing",
        "StudyActivityInstruction",
    }
)


def target_capability(resource_type: str) -> str | None:
    return TARGET_CAPABILITIES.get(resource_type)
