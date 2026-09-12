"""Native identity comparison used only inside StudyService.patch's write lock."""

from dataclasses import dataclass
from typing import Any, Callable

from clinical_mdr_api.models.study_selections.duration import DurationJsonModel
from clinical_mdr_api.models.study_selections.null_adjudication import (
    NULL_ADJUDICATION_COMPARISON,
    NULL_ADJUDICATION_CONTRACT,
    AppliedNullAdjudication,
    NullAdjudicationField,
    StudyNullAdjudicationCapability,
    StudyNullAdjudicationReceipt,
    StudyNullAdjudicationRequest,
)
from clinical_mdr_api.models.study_selections.study import (
    HighLevelStudyDesignJsonModel,
    RegistryIdentifiersJsonModel,
    StudyInterventionJsonModel,
    StudyMetadataJsonModel,
    StudyPatchRequestJsonModel,
    StudyPopulationJsonModel,
)
from clinical_mdr_api.services.integrations.canonical_json import (
    canonical_hash,
    canonical_json,
)
from common.exceptions import BusinessLogicException

_GROUPS = {
    "id_metadata.registry_identifiers": (
        "identification_metadata.registry_identifiers",
        RegistryIdentifiersJsonModel,
    ),
    "high_level_study_design": (
        "high_level_study_design",
        HighLevelStudyDesignJsonModel,
    ),
    "study_population": ("study_population", StudyPopulationJsonModel),
    "study_intervention": ("study_intervention", StudyInterventionJsonModel),
}
# The existing domain/configuration uses the singular name; the actual API DTO
# and both its reader and writer use this plural spelling.
_COMPANION_API_NAMES = {
    "trial_intent_type_null_value_code": "trial_intent_types_null_value_code",
}
# Exact native slots. Configuration may select terminology, but cannot redirect
# an expected-value check to a different domain property or patch companion.
_VALUE_NAMES = {
    "id_metadata.registry_identifiers": (
        "ct_gov_id",
        "eudract_id",
        "universal_trial_number_utn",
        "japanese_trial_registry_id_japic",
        "investigational_new_drug_application_number_ind",
        "eu_trial_number",
        "civ_id_sin_number",
        "national_clinical_trial_number",
        "japanese_trial_registry_number_jrct",
        "national_medical_products_administration_nmpa_number",
        "eudamed_srn_number",
        "investigational_device_exemption_ide_number",
        "eu_pas_number",
    ),
    "high_level_study_design": (
        "study_type_code",
        "trial_phase_code",
        "trial_type_codes",
        "is_extension_trial",
        "study_stop_rules",
        "is_adaptive_design",
        "post_auth_indicator",
        "confirmed_response_minimum_duration",
    ),
    "study_population": (
        "therapeutic_area_codes",
        "disease_condition_or_indication_codes",
        "diagnosis_group_codes",
        "sex_of_participants_code",
        "rare_disease_indicator",
        "healthy_subject_indicator",
        "planned_maximum_age_of_subjects",
        "planned_minimum_age_of_subjects",
        "stable_disease_minimum_duration",
        "pediatric_study_indicator",
        "pediatric_postmarket_study_indicator",
        "pediatric_investigation_plan_indicator",
        "relapse_criteria",
        "number_of_expected_subjects",
    ),
    "study_intervention": (
        "intervention_type_code",
        "add_on_to_existing_treatments",
        "control_type_code",
        "intervention_model_code",
        "is_trial_randomised",
        "stratification_factor",
        "trial_blinding_schema_code",
        "planned_study_length",
        "trial_intent_types_codes",
    ),
}
_DURATION_NAMES = {
    "confirmed_response_minimum_duration",
    "planned_maximum_age_of_subjects",
    "planned_minimum_age_of_subjects",
    "stable_disease_minimum_duration",
    "planned_study_length",
}


class NullAdjudicationInvalid(BusinessLogicException):
    status_code = 400


class NullAdjudicationConflict(BusinessLogicException):
    status_code = 412


@dataclass(frozen=True)
class _Field:
    paths: NullAdjudicationField
    group: str
    value_name: str
    companion_name: str


def _fields() -> dict[str, _Field]:
    result = {}
    for group, names in _VALUE_NAMES.items():
        section, model = _GROUPS[group]
        api_fields = model.model_fields.keys()
        for name in names:
            stem = name.removesuffix("_codes").removesuffix("_code")
            domain_companion = (
                "trial_intent_type_null_value_code"
                if name == "trial_intent_types_codes"
                else f"{stem}_null_value_code"
            )
            companion = _COMPANION_API_NAMES.get(domain_companion, domain_companion)
            if name not in api_fields or companion not in api_fields:
                raise NullAdjudicationInvalid(msg="NULL_ADJUDICATION_API_MAP_MISMATCH")
            paths = NullAdjudicationField(
                value_path=f"{section}.{name}", companion_path=f"{section}.{companion}"
            )
            result[paths.value_path] = _Field(paths, group, name, domain_companion)
    return result


def null_adjudication_capability(study_uid: str) -> StudyNullAdjudicationCapability:
    fields = _fields()
    return StudyNullAdjudicationCapability(
        contract_version=NULL_ADJUDICATION_CONTRACT,
        comparison=NULL_ADJUDICATION_COMPARISON,
        study_uid=study_uid,
        atomic_preconditions=True,
        fields=[fields[key].paths for key in sorted(fields)],
    )


def metadata_identity(value: Any) -> Any:
    """Drop only term/unit presentation labels; retain JSON types and presence."""
    if isinstance(value, list):
        return [metadata_identity(item) for item in value]
    if isinstance(value, dict):
        if "term_uid" in value:
            return {"term_uid": value["term_uid"]}
        if "duration_value" in value and "duration_unit_code" in value:
            unit = value["duration_unit_code"]
            return {
                "duration_value": value["duration_value"],
                "duration_unit_code": (
                    {"uid": unit["uid"]}
                    if isinstance(unit, dict) and "uid" in unit
                    else unit
                ),
            }
        return {key: metadata_identity(item) for key, item in value.items()}
    return value


def _current_state(
    metadata, field: _Field, companion: bool, find_all_units: Callable
) -> tuple[dict, Any]:
    parent = metadata
    for part in field.group.split("."):
        parent = getattr(parent, part, None)
        if parent is None:
            return {"present": False}, None
    name = field.companion_name if companion else field.value_name
    if not hasattr(parent, name):
        raise NullAdjudicationInvalid(msg="NULL_ADJUDICATION_CONFIG_DOMAIN_MISMATCH")
    raw = getattr(parent, name)
    value = raw
    if raw is not None:
        if companion or name.endswith("_code"):
            value = {"term_uid": raw}
        elif name.endswith("_codes"):
            value = [{"term_uid": uid} for uid in raw]
        elif name in _DURATION_NAMES:
            value = DurationJsonModel.from_duration_object(
                raw, find_all_units
            ).model_dump(mode="json")
    return {"present": True, "value": metadata_identity(value)}, raw


def prepare_guarded_null_patch(
    metadata, request: StudyNullAdjudicationRequest, find_all_units: Callable
) -> StudyPatchRequestJsonModel:
    """Validate every expected pair against the locked aggregate before editing."""
    fields = _fields()
    updates: dict[str, Any] = {}
    for row in request.adjudications:
        field = fields.get(row.value_path)
        if field is None or field.paths.companion_path != row.companion_path:
            raise NullAdjudicationInvalid(
                msg="NULL_ADJUDICATION_UNKNOWN_VALUE_COMPANION_PAIR"
            )
        current_value, raw_value = _current_state(
            metadata, field, False, find_all_units
        )
        current_companion, raw_companion = _current_state(
            metadata, field, True, find_all_units
        )
        expected_value = metadata_identity(row.expected_value.model_dump(mode="json"))
        expected_companion = metadata_identity(
            row.expected_null_companion.model_dump(mode="json")
        )
        # Python False == 0; compare canonical JSON instead of Python equality.
        if canonical_json(current_value) != canonical_json(
            expected_value
        ) or canonical_json(current_companion) != canonical_json(expected_companion):
            raise NullAdjudicationConflict(
                msg=f"NULL_ADJUDICATION_PRECONDITION_FAILED: {row.value_path}"
            )
        empty_value = (
            raw_value is None
            or isinstance(raw_value, (str, list, tuple, set, frozenset))
            and len(raw_value) == 0
        )
        if not empty_value or raw_companion is not None:
            raise NullAdjudicationConflict(
                msg=f"NULL_ADJUDICATION_SLOT_NOT_EMPTY: {row.value_path}"
            )
        parts = row.companion_path.split(".")
        cursor = updates
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = {"term_uid": row.null_term_uid}
    # Native BaseModel.model_validate is a graph-node projection override;
    # construct the API input normally, as the existing HTTP route does.
    return StudyPatchRequestJsonModel(
        current_metadata=StudyMetadataJsonModel(**updates)
    )


def null_adjudication_receipt(
    study_uid: str, request: StudyNullAdjudicationRequest
) -> StudyNullAdjudicationReceipt:
    """Called only after the guarded StudyService.patch returns successfully."""
    return StudyNullAdjudicationReceipt(
        contract_version=NULL_ADJUDICATION_CONTRACT,
        study_uid=study_uid,
        request_hash=f"sha256:{canonical_hash(request.model_dump(mode='json'))}",
        preconditions_verified=True,
        checked_paths=[
            path
            for row in request.adjudications
            for path in (row.value_path, row.companion_path)
        ],
        applied=[
            AppliedNullAdjudication(
                value_path=row.value_path,
                companion_path=row.companion_path,
                null_term_uid=row.null_term_uid,
            )
            for row in request.adjudications
        ],
    )
