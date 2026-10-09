"""Read-only M11 presentation of one native mapping result, without authoring facts."""

from typing import Any

from clinical_mdr_api.models.utils import sanitize_html
from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
)

MISSING = "Not available in the selected source"


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _objects(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _text(value: Any) -> str:
    return value if isinstance(value, str) and value.strip() else MISSING


def _objective_for_preview(value: dict[str, Any]) -> dict[str, Any]:
    """Sanitize rich text without changing the retained native mapping evidence."""
    return {
        **value,
        "description": sanitize_html(_text(value.get("description"))),
        "endpoints": [
            {
                **endpoint,
                "description": sanitize_html(_text(endpoint.get("description"))),
            }
            for endpoint in _objects(value.get("endpoints"))
        ],
    }


def m11_preview_context(
    report: dict[str, Any], study_uid: str, study_value_version: str | None
) -> dict[str, Any]:
    """Project retained native headers and selected USDM values, never latest fallbacks.

    The native mapper currently has no sponsor-role source. Registry organizations
    and identifier scopes therefore cannot authorize a sponsor name or address.
    A mapping-complete result is still a draft protocol preview, not an approval.
    """
    mapping = _object(report.get("mappingReport"))
    if (
        mapping.get("studyUid") != study_uid
        or mapping.get("studyValueVersion") != study_value_version
        or mapping.get("state") not in {"complete", "incomplete"}
        or not isinstance(mapping.get("issues"), list)
    ):
        raise USDMMappingAuthorityRequired(
            "M11_SOURCE_SCOPE_MISMATCH: mapping report does not match the requested study and version."
        )
    native = [
        row
        for row in _objects(report.get("nativeRecords"))
        if row.get("kind") == "study" and row.get("uid") == study_uid
    ]
    if len(native) != 1 or _object(native[0].get("record")).get("uid") != study_uid:
        raise USDMMappingAuthorityRequired(
            "M11_NATIVE_STUDY_REQUIRED: one exact retained native study is required."
        )
    metadata = _object(_object(native[0].get("record")).get("current_metadata"))
    identification = _object(metadata.get("identification_metadata"))
    source_version = _object(metadata.get("version_metadata"))
    native_version = source_version.get("version_number")
    native_version = str(native_version) if native_version is not None else None
    if study_value_version is not None and native_version != study_value_version:
        raise USDMMappingAuthorityRequired(
            "M11_SOURCE_VERSION_MISMATCH: native study does not match the requested version."
        )
    study = _object(_object(report.get("document")).get("study"))
    versions = [
        row
        for row in _objects(study.get("versions"))
        if row.get("versionIdentifier") == native_version
    ]
    if len(versions) != 1:
        raise USDMMappingAuthorityRequired(
            "M11_DOCUMENT_VERSION_MISMATCH: one mapped version must match the retained native study."
        )
    version = versions[0]
    issues = list(_objects(mapping.get("issues")))

    def missing(code: str, message: str) -> None:
        issues.append({"code": code, "message": message})

    designs = version.get("studyDesigns")
    if isinstance(designs, list) and len(designs) == 1 and isinstance(designs[0], dict):
        design = designs[0]
    else:
        design = {}
        missing(
            "M11_DESIGN_UNRESOLVED",
            "The selected version does not contain one unambiguous study design; design-dependent fields are unavailable.",
        )
    references = version.get("documentVersionIds")
    reference_ids: set[str] = set()
    if (
        isinstance(references, list)
        and all(isinstance(ref, str) and ref for ref in references)
        and len(references) == len(set(references))
    ):
        reference_ids = set(references)
    referenced = [
        item
        for document in _objects(study.get("documentedBy"))
        if _object(document.get("type")).get("code") == "C70817"
        for item in _objects(document.get("versions"))
        if isinstance(item.get("id"), str) and item["id"] in reference_ids
    ]
    protocol_version = referenced[0].get("version") if len(referenced) == 1 else None
    if protocol_version is None:
        missing(
            "M11_PROTOCOL_VERSION_UNRESOLVED",
            "The selected source does not resolve one referenced protocol document version. No current-version fallback was used.",
        )
    missing(
        "M11_SPONSOR_SOURCE_UNAVAILABLE",
        "The native source does not yet provide an authoritative sponsor role and legal address. Registry organizations are not sponsors.",
    )
    population = _object(design.get("population"))
    age = _object(population.get("plannedAge"))
    minimum, maximum = _object(age.get("minValue")), _object(age.get("maxValue"))
    objectives = [
        _objective_for_preview(row) for row in _objects(design.get("objectives"))
    ]
    phase = _object(_object(design.get("studyPhase")).get("standardCode"))
    registry = _object(identification.get("registry_identifiers"))
    # These are native registry fields, not USDM Organization IDs. The selected
    # study record is the same retained source used by the mapper above.
    registry_fields = (
        ("ct_gov_id", "ClinicalTrials.gov ID"),
        ("eudract_id", "EudraCT ID"),
        ("eu_trial_number", "EU Trial Number"),
        ("civ_id_sin_number", "CIV-ID/SIN"),
        ("eudamed_srn_number", "EUDAMED SRN"),
        ("investigational_device_exemption_ide_number", "IDE Number"),
        ("investigational_new_drug_application_number_ind", "IND Number"),
        ("japanese_trial_registry_id_japic", "JAPIC ID"),
        ("japanese_trial_registry_number_jrct", "jRCT Number"),
        ("national_clinical_trial_number", "National Clinical Trial Number"),
        ("national_medical_products_administration_nmpa_number", "NMPA Number"),
        ("universal_trial_number_utn", "WHO/UTN Number"),
        ("eu_pas_number", "EU PAS Number"),
    )

    def value_or_missing(value: Any) -> Any:
        return MISSING if value is None else value

    return {
        "study_id": study_uid,
        "source_version": native_version or MISSING,
        "source_status": _text(source_version.get("study_status")),
        "current_preview": study_value_version is None,
        "mapping_state": mapping["state"],
        "preview_issues": issues,
        "study_name": _text(study.get("name")),
        "protocol_full_title": _text(
            _object(metadata.get("study_description")).get("study_title")
        ),
        "protocol_short_title": _text(
            _object(metadata.get("study_description")).get("study_short_title")
        ),
        "trial_acronym": _text(identification.get("study_acronym")),
        "sponsor_protocol_identifier": _text(identification.get("study_id")),
        "sponsor_name": MISSING,
        "sponsor_legal_address": MISSING,
        "protocol_version": _text(protocol_version),
        "version_date": MISSING,
        "original_protocol": MISSING,
        "amendment_identifier": MISSING,
        "registry_identifiers": [
            {"label": label, "value": registry[key]}
            for key, label in registry_fields
            if isinstance(registry.get(key), str) and registry[key].strip()
        ],
        "trial_phase": {"decode": _text(phase.get("decode"))},
        "study_indications": _objects(design.get("indications")),
        "primary_objectives": [
            row
            for row in objectives
            if "primary" in str(_object(row.get("level")).get("decode", "")).lower()
        ],
        "secondary_objectives": [
            row
            for row in objectives
            if "secondary" in str(_object(row.get("level")).get("decode", "")).lower()
        ],
        "population_healthy_subjects": population.get("includesHealthySubjects"),
        "population_planned_minimum_age": value_or_missing(minimum.get("value")),
        "population_planned_minimum_age_unit": _text(
            _object(_object(minimum.get("unit")).get("standardCode")).get("decode")
        ),
        "population_planned_maximum_age": value_or_missing(maximum.get("value")),
        "population_planned_maximum_age_unit": _text(
            _object(_object(maximum.get("unit")).get("standardCode")).get("decode")
        ),
        "population_planned_enrollment_number_quantity_value": value_or_missing(
            _object(population.get("plannedEnrollmentNumber")).get("value")
        ),
        "number_of_arms": (
            len(design["arms"]) if isinstance(design.get("arms"), list) else MISSING
        ),
        # Last timing is not necessarily total trial duration.
        "trial_intervention_total_duration": None,
    }
