"""Prove a metadata duration's source unit survives native ISO storage.

Native study metadata stores an ISO duration, not a UnitDefinition UID. Its
reader chooses a unit from the repository's original order, including singular
and plural definitions. Predict that choice with the native converters, then
prove equivalence without inferring it from names.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, NoReturn

from fastapi.encoders import jsonable_encoder

from clinical_mdr_api.domain_repositories.concepts.unit_definitions.unit_definition_repository import (
    UnitDefinitionRepository,
)
from clinical_mdr_api.domains.concepts.unit_definitions.unit_definition import (
    UnitDefinitionAR,
)
from clinical_mdr_api.domains.versioned_object_aggregate import LibraryItemStatus
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
)
from clinical_mdr_api.models.utils import from_duration_object_to_value_and_unit
from clinical_mdr_api.services._utils import create_duration_object_from_api_input
from common.config import settings

CORRESPONDENCE_CONTRACT = "OsbStudyMetadataUnitCorrespondenceV1@1.0.0"
DEFINITION_CONTRACT = "OsbMetadataUnitDefinitionV1@1.0.0"
SEMANTICS_CONTRACT = "OsbMetadataUnitSemanticsV1@1.0.0"


def _fail(code: str, message: str) -> NoReturn:
    # The candidate producer imports the metadata mapper; avoid an import cycle.
    from clinical_mdr_api.services.integrations.candidate_set import (
        OsbCandidateSetError,
    )

    raise OsbCandidateSetError(code, message, 409)


def _definition_identity(unit: UnitDefinitionAR) -> dict[str, Any]:
    metadata = unit.item_metadata
    identified = bool(unit.uid and unit.name and metadata.version)
    current_version = (
        metadata.status == LibraryItemStatus.FINAL and metadata.end_date is None
    )
    effective = (
        isinstance(metadata.start_date, datetime)
        and metadata.start_date.tzinfo is not None
        and metadata.start_date <= datetime.now(UTC)
    )
    in_subset = any(
        subset.name == settings.study_time_unit_subset
        for subset in unit.concept_vo.unit_subsets
    )
    if not (identified and current_version and effective and in_subset):
        _fail(
            "OSB_STUDY_METADATA_UNIT_NOT_CURRENT",
            "Both duration units must be current final definitions in the native Study Time subset.",
        )
    definition = jsonable_encoder(
        {
            "uid": unit.uid,
            "version": metadata.version,
            "status": metadata.status.value,
            "validFrom": metadata.start_date,
            "validTo": metadata.end_date,
            "authorId": metadata.author_id,
            "authorUsername": metadata.author_username,
            "changeDescription": metadata.change_description,
            "library": asdict(unit.library),
            "definition": asdict(unit.concept_vo),
        },
        exclude_none=False,
    )
    return {
        "resourceType": "UnitDefinition",
        "uid": unit.uid,
        "version": metadata.version,
        "name": unit.name,
        "library": unit.library.name,
        "valueHash": canonical_json_hash_ref(
            definition, schema_version=DEFINITION_CONTRACT
        ),
    }


def _semantics(unit: UnitDefinitionAR) -> dict[str, Any]:
    value = unit.concept_vo
    factor = value.conversion_factor_to_master
    identified = all(
        isinstance(item, str) and item.strip()
        for item in (
            value.ucum_uid,
            value.ucum_name,
            value.unit_dimension_uid,
            value.unit_dimension_name,
        )
    )
    simple_conversion = (
        value.convertible_unit is True
        and value.use_complex_unit_conversion is False
        and value.use_molecular_weight is False
    )
    valid_factor = (
        isinstance(factor, (int, float))
        and not isinstance(factor, bool)
        and math.isfinite(factor)
        and factor > 0
    )
    if not (identified and simple_conversion and valid_factor):
        _fail(
            "OSB_STUDY_METADATA_UNIT_SEMANTICS_UNPROVEN",
            "Duration correspondence requires explicit UCUM, dimension and simple finite conversion semantics.",
        )
    return {
        "ucumUid": value.ucum_uid,
        "ucumCode": value.ucum_name,
        "dimensionUid": value.unit_dimension_uid,
        "dimensionName": value.unit_dimension_name,
        "convertibleUnit": value.convertible_unit,
        "conversionFactorToMaster": factor,
        "useComplexUnitConversion": value.use_complex_unit_conversion,
        "useMolecularWeight": value.use_molecular_weight,
    }


def _assert_current(
    repository: UnitDefinitionRepository, identities: list[dict[str, Any]]
) -> None:
    expected = {item["uid"]: item for item in identities}
    if any(
        canonical_json(item) != canonical_json(expected[item["uid"]])
        for item in identities
    ):
        _fail(
            "OSB_STUDY_METADATA_UNIT_CHANGED",
            "One native unit identity names different source and read-back definitions.",
        )
    current, _ = repository.find_all(
        subset=settings.study_time_unit_subset, uids=sorted(expected)
    )
    if len(current) != len(expected) or {unit.uid for unit in current} != set(expected):
        _fail(
            "OSB_STUDY_METADATA_UNIT_CHANGED",
            "A duration definition changed or disappeared during correspondence resolution.",
        )
    for unit in current:
        # find_all's aggregate projection does not preserve the native version
        # relationship's end_date. Check an open Final relationship explicitly.
        if not repository.latest_concept_is_final(unit.uid):
            _fail(
                "OSB_STUDY_METADATA_UNIT_NOT_CURRENT",
                "A duration definition no longer has a current final native version.",
            )
        _semantics(unit)
        if canonical_json(_definition_identity(unit)) != canonical_json(
            expected[unit.uid]
        ):
            _fail(
                "OSB_STUDY_METADATA_UNIT_CHANGED",
                "A duration definition changed during correspondence resolution.",
            )


def resolve_study_metadata_unit(
    reference: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Return the native read-back UID plus exact source/correspondence evidence.

    Only reads are performed. Callers revalidate by resolving the same plan and
    comparing the entire identity, including the original source unit hash.
    """
    name = reference.get("unitName") if isinstance(reference, dict) else None
    if not isinstance(name, str) or not name.strip():
        _fail(
            "OSB_STUDY_METADATA_UNIT_REFERENCE_INVALID",
            "A source unit name is required.",
        )
    metadata_value = plan.get("metadataValue") if isinstance(plan, dict) else None
    amount = (
        metadata_value.get("duration_value")
        if isinstance(metadata_value, dict)
        else None
    )
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        _fail(
            "OSB_STUDY_METADATA_UNIT_DURATION_INVALID",
            "A duration correspondence requires the plan's non-negative integer duration value.",
        )

    repository = UnitDefinitionRepository()
    try:
        # This is the exact unfiltered, unsorted repository call used by the
        # native reader. Service.get_all adds sorting and must not replace it.
        native_units, total = repository.find_all(
            subset=settings.study_time_unit_subset
        )
        matches = [
            unit
            for unit in native_units
            if isinstance(unit.name, str)
            and unit.name.casefold() == name.strip().casefold()
            and unit.item_metadata.status == LibraryItemStatus.FINAL
        ]
        if len(matches) != 1:
            _fail(
                "OSB_STUDY_METADATA_UNIT_UNRESOLVED",
                "The source unit has no unique final definition in the native Study Time subset.",
            )
        source = matches[0]
        semantics = _semantics(source)
        source_identity = _definition_identity(source)

        def read_units(**kwargs):
            if kwargs != {"subset": settings.study_time_unit_subset}:
                _fail(
                    "OSB_STUDY_METADATA_UNIT_NATIVE_READBACK_UNAVAILABLE",
                    "The native duration reader requested an unsupported unit selection.",
                )
            return native_units, total

        try:
            persisted = create_duration_object_from_api_input(
                amount, source.uid, lambda uid: source if uid == source.uid else None
            )
            observed_amount, canonical = from_duration_object_to_value_and_unit(
                persisted, read_units
            )
            if canonical is None or observed_amount != amount:
                _fail(
                    "OSB_STUDY_METADATA_UNIT_NATIVE_READBACK_UNAVAILABLE",
                    "The native duration reader cannot preserve the proposed duration.",
                )
            if canonical_json(semantics) != canonical_json(_semantics(canonical)):
                _fail(
                    "OSB_STUDY_METADATA_UNIT_CORRESPONDENCE_MISMATCH",
                    "The source and native read-back units have different UCUM, dimension or conversion semantics.",
                )
            canonical_identity = _definition_identity(canonical)
            canonical_persisted = create_duration_object_from_api_input(
                amount,
                canonical.uid,
                lambda uid: canonical if uid == canonical.uid else None,
            )
            repeated_amount, repeated_unit = from_duration_object_to_value_and_unit(
                canonical_persisted, read_units
            )
            if (
                canonical_persisted != persisted
                or repeated_amount != amount
                or repeated_unit is None
                or repeated_unit.uid != canonical.uid
            ):
                _fail(
                    "OSB_STUDY_METADATA_UNIT_READBACK_UNSTABLE",
                    "The canonical duration unit does not survive the native write/read conversion.",
                )
        except (IndexError, KeyError, TypeError, ValueError) as error:
            _fail(
                "OSB_STUDY_METADATA_UNIT_NATIVE_READBACK_UNAVAILABLE",
                f"The native duration conversion could not be verified ({type(error).__name__}).",
            )

        _assert_current(repository, [source_identity, canonical_identity])
        return {
            "value": {"uid": canonical.uid},
            "identity": {
                **canonical_identity,
                "subset": settings.study_time_unit_subset,
                "sourceUnitIdentity": source_identity,
                "correspondence": {
                    "contractVersion": CORRESPONDENCE_CONTRACT,
                    "durationValue": amount,
                    "persistedDuration": persisted,
                    "nativeReadBackValue": {
                        "duration_value": observed_amount,
                        "duration_unit_code": {"uid": canonical.uid},
                    },
                    "semantics": semantics,
                    "semanticsHash": canonical_json_hash_ref(
                        semantics, schema_version=SEMANTICS_CONTRACT
                    ),
                },
            },
        }
    finally:
        repository.close()
