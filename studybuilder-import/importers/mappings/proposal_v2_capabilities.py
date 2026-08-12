"""Closed Proposal V2 target taxonomy backed by existing OSB APIs/UI surfaces."""

TARGET_CAPABILITIES = {
    "StudyMetadata": ("native_study_mutation", "/studies/{study_uid}"),
    "StudyStandardVersion": (
        "native_study_mutation",
        "/studies/{study_uid}/study-standard-versions",
    ),
    "StudySelectionObjective": (
        "native_study_mutation",
        "/studies/{study_uid}/study-objectives",
    ),
    "StudySelectionEndpoint": (
        "native_study_mutation",
        "/studies/{study_uid}/study-endpoints",
    ),
    "StudySelectionCriteria": (
        "native_study_mutation",
        "/studies/{study_uid}/study-criteria",
    ),
    "StudySelectionCompound": (
        "native_study_mutation",
        "/studies/{study_uid}/study-compounds",
    ),
    "StudyCompoundDosing": (
        "native_study_mutation",
        "/studies/{study_uid}/study-compound-dosings",
    ),
    "StudySelectionArm": ("native_study_mutation", "/studies/{study_uid}/study-arms"),
    "StudySelectionElement": (
        "native_study_mutation",
        "/studies/{study_uid}/study-elements",
    ),
    "StudyEpoch": ("native_study_mutation", "/studies/{study_uid}/study-epochs"),
    "StudyDesignCell": (
        "native_study_mutation",
        "/studies/{study_uid}/study-design-cells",
    ),
    "StudyVisit": ("native_study_mutation", "/studies/{study_uid}/study-visits"),
    "StudySelectionActivity": (
        "native_study_mutation",
        "/studies/{study_uid}/study-activities",
    ),
    "StudyActivitySchedule": (
        "native_study_mutation",
        "/studies/{study_uid}/study-activity-schedules",
    ),
    "StudyActivityInstruction": (
        "native_study_mutation",
        "/studies/{study_uid}/study-activity-instructions",
    ),
    "CTTerm": ("governed_library_reference", "/ct/terms"),
    "CTCodelist": ("governed_library_reference", "/ct/codelists"),
    "UnitDefinition": ("governed_library_reference", "/concepts/unit-definitions"),
    "DatasetVariable": ("governed_library_reference", "/standards/dataset-variables"),
    "Activity": ("governed_library_reference", "/concepts/activities/activities"),
    "ActivityInstance": (
        "governed_library_reference",
        "/concepts/activities/activity-instances",
    ),
    "Timeframe": ("governed_library_reference", "/timeframes"),
    "MedicinalProduct": ("governed_library_reference", "/concepts/medicinal-products"),
    "PharmaceuticalProduct": (
        "governed_library_reference",
        "/concepts/pharmaceutical-products",
    ),
    "OdmForm": ("governed_library_reference", "/odms/forms"),
    "OdmItemGroup": ("governed_library_reference", "/odms/item-groups"),
    "OdmItem": ("governed_library_reference", "/odms/items"),
    "IntegrationExtension": ("governed_extension", None),
    "RetainedNarrative": ("retained_narrative", None),
    "Unresolved": ("unresolved", None),
}


def target_capability(resource_type):
    return TARGET_CAPABILITIES.get(resource_type)


def require_target_capability(resource_type):
    value = target_capability(resource_type)
    if value is None:
        raise ValueError(f"OSB_PROPOSAL_RESOURCE_TYPE_UNSUPPORTED:{resource_type}")
    return value
