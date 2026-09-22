"""Plan and apply native study properties through the governed mapping decision.

The request supplies a source-pinned CSL plan. OSB resolves its library references
and offers an exact value with a native precondition. Only the existing signed
decision executor calls apply_metadata_selections; preparation never writes.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, NoReturn

from fastapi.encoders import jsonable_encoder
from neomodel import db
from pydantic import TypeAdapter, ValidationError

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    hash_refs_equal,
)
from common.exceptions import (
    BusinessLogicException,
    NotFoundException,
    ValidationException,
)

FAMILY = "study_metadata"
PLAN_CONTRACT = "OsbStudyMetadataPlanV1@1.0.0"
OFFER_CONTRACT = "OsbStudyMetadataOfferV1@1.0.0"

# Match the native writable metadata model. Derived study_id and version fields
# are deliberately absent: a successful PATCH to them is not a persisted value.
_SECTIONS = {
    "study_description": {"study_title", "study_short_title"},
    "high_level_study_design": {
        "study_type_code",
        "trial_phase_code",
        "trial_type_codes",
        "development_stage_code",
        "observational_model_code",
        "observational_time_perspective_code",
        "is_extension_trial",
        "is_adaptive_design",
        "post_auth_indicator",
        "study_stop_rules",
        "confirmed_response_minimum_duration",
    },
    "study_population": {
        "number_of_expected_subjects",
        "therapeutic_area_codes",
        "disease_condition_or_indication_codes",
        "diagnosis_group_codes",
        "sex_of_participants_code",
        "healthy_subject_indicator",
        "rare_disease_indicator",
        "pediatric_study_indicator",
        "pediatric_postmarket_study_indicator",
        "pediatric_investigation_plan_indicator",
        "planned_minimum_age_of_subjects",
        "planned_maximum_age_of_subjects",
        "stable_disease_minimum_duration",
        "relapse_criteria",
    },
    "study_intervention": {
        "intervention_type_code",
        "intervention_model_code",
        "control_type_code",
        "trial_intent_types_codes",
        "trial_blinding_schema_code",
        "is_trial_randomised",
        "add_on_to_existing_treatments",
        "stratification_factor",
        "planned_study_length",
    },
    "identification_metadata.registry_identifiers": {
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
    },
}
METADATA_PATHS = frozenset(
    f"{section}.{field}" for section, fields in _SECTIONS.items() for field in fields
)
JOINED_TEXT_PATHS = frozenset(
    {
        "high_level_study_design.study_stop_rules",
        "study_intervention.stratification_factor",
    }
)


def _candidate_error_type() -> type[Exception]:
    # Imported lazily because candidate_set prepares these offers.
    from clinical_mdr_api.services.integrations.candidate_set import (
        OsbCandidateSetError,
    )

    return OsbCandidateSetError


def _fail(code: str, message: str) -> NoReturn:
    raise _candidate_error_type()(code, message, 409)


def _get(value: dict[str, Any], path: str):
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _put(value: dict[str, Any], path: str, item: Any):
    parts = path.split(".")
    for part in parts[:-1]:
        value = value.setdefault(part, {})
    value[parts[-1]] = item


def _normalized(value: Any):
    """Compare native DTO values without display-only CT labels."""
    if isinstance(value, list):
        values = [_normalized(item) for item in value]
        # Native *_codes fields are sets; OSB may return a different term order.
        return sorted(values, key=canonical_json)
    if isinstance(value, dict):
        if isinstance(value.get("term_uid"), str):
            return {"term_uid": value["term_uid"]}
        if "duration_value" in value:
            unit = value.get("duration_unit_code") or {}
            return {
                "duration_value": value["duration_value"],
                "duration_unit_code": {"uid": unit.get("uid")},
            }
        return {key: _normalized(child) for key, child in value.items()}
    return value


def _null_path(path: str) -> str:
    if path.endswith("_codes"):
        return path[:-6] + "_null_value_code"
    if path.endswith("_code"):
        return path[:-5] + "_null_value_code"
    return path + "_null_value_code"


def _parent_uid(study: dict[str, Any]):
    return (study.get("study_parent_part") or {}).get("uid")


def _precondition(study: dict[str, Any], path: str):
    metadata = study.get("current_metadata") or {}
    return canonical_json_hash_ref(
        {
            "nativeStudyId": study["uid"],
            "metadataPath": path,
            "metadataValue": _normalized(_get(metadata, path)),
            "nullValue": _normalized(_get(metadata, _null_path(path))),
            "parentStudyUid": _parent_uid(study),
        },
        schema_version="OsbStudyMetadataPreconditionV1@1.0.0",
    )


def _assert_draft(study: dict[str, Any], uid: str):
    version = _get(study, "current_metadata.version_metadata") or {}
    if (
        study.get("uid") != uid
        or str(version.get("study_status", "")).upper() != "DRAFT"
    ):
        _fail(
            "OSB_STUDY_METADATA_DRAFT_REQUIRED",
            "The exact native study must be a readable DRAFT.",
        )


def _validate_metadata_value(path: str, value: Any):
    """Check the same native DTO used by PATCH before offering a write."""
    from clinical_mdr_api.models.study_selections.study import (
        StudyPatchRequestJsonModel,
    )

    metadata: dict[str, Any] = {}
    if value is None:
        _fail(
            "OSB_STUDY_METADATA_VALUE_INVALID",
            "Missing source content is not a native property value.",
        )
    _put(metadata, path, value)
    try:
        # OSB overrides model_validate for graph-node projection. JSON input
        # follows the normal Pydantic DTO validator, also used by PATCH.
        validated = TypeAdapter(StudyPatchRequestJsonModel).validate_python(
            {"current_metadata": metadata}, strict=True
        )
    except ValidationError:
        _fail(
            "OSB_STUDY_METADATA_VALUE_INVALID",
            f"The proposed value does not satisfy the native type for {path}.",
        )
    actual = _get(
        jsonable_encoder(validated, exclude_unset=True), f"current_metadata.{path}"
    )
    if canonical_json(actual) != canonical_json(value):
        _fail(
            "OSB_STUDY_METADATA_VALUE_INVALID",
            "The native DTO would change or discard part of the proposed value.",
        )


class NativeStudyMetadataPort:
    def stage_context(self, uid: str) -> dict[str, Any]:
        """Read the study's actual CT selections for unapproved source drafts."""
        from clinical_mdr_api.models.integrations.mapping_context import MappingContextRequest
        from clinical_mdr_api.services.integrations.mapping_context import MappingContextService

        warnings: list[str] = []
        blockers: list[str] = []
        packages = MappingContextService()._selected_packages(
            MappingContextRequest(study_uid=uid), warnings, blockers
        )
        if blockers:
            _fail(
                "OSB_STUDY_METADATA_CONTEXT_REQUIRED",
                "The native study terminology selections are unavailable.",
            )
        return {"selectedPackages": [
            {"packageUid": package.package_uid,
             "catalogueName": package.catalogue_name,
             "effectiveDate": package.effective_date}
            for package in packages
        ]}

    def read(self, uid: str) -> dict[str, Any]:
        from clinical_mdr_api.services.studies.study import StudyService

        return jsonable_encoder(StudyService().get_by_uid(uid), exclude_none=False)

    def lock(self, uid: str):
        rows, _ = db.cypher_query(
            """MATCH (study:StudyRoot {uid:$uid})
               SET study.platform_mapping_lock_revision =
                   coalesce(study.platform_mapping_lock_revision, 0) + 1
               RETURN study.uid""",
            {"uid": uid},
        )
        if not rows:
            _fail(
                "OSB_STUDY_METADATA_STUDY_MISSING", "The native study no longer exists."
            )

    @staticmethod
    def lock_references(bindings: list[dict[str, Any]]):
        from clinical_mdr_api.services.integrations.study_metadata_reference_lock import (
            lock_study_metadata_references,
        )

        lock_study_metadata_references(bindings)

    def patch(self, uid: str, body: dict[str, Any]):
        from clinical_mdr_api.models.study_selections.study import (
            StudyPatchRequestJsonModel,
        )
        from clinical_mdr_api.services.studies.study import StudyService

        StudyService().patch(
            uid=uid, dry=False, study_patch_request=StudyPatchRequestJsonModel(**body)
        )

    def preview(self, uid: str, body: dict[str, Any]) -> dict[str, Any]:
        from clinical_mdr_api.models.study_selections.study import (
            StudyPatchRequestJsonModel,
        )
        from clinical_mdr_api.services.studies.study import StudyService

        return jsonable_encoder(
            StudyService().patch(
                uid=uid,
                dry=True,
                study_patch_request=StudyPatchRequestJsonModel(**body),
            ),
            exclude_none=False,
        )

    def resolve(
        self, kind: str, reference: dict[str, Any], context: Any, plan: dict[str, Any]
    ):
        from clinical_mdr_api.services.integrations.mapping_context import (
            MappingContextService,
        )

        if kind == "unitRef":
            from clinical_mdr_api.services.integrations.study_metadata_unit_binding import (
                resolve_study_metadata_unit,
            )

            return resolve_study_metadata_unit(reference, plan)
        if kind == "dictionaryRef":
            return self._resolve_dictionary(reference)
        if kind != "termRef":
            _fail(
                "OSB_STUDY_METADATA_REFERENCE_INVALID",
                "The metadata reference kind is unsupported.",
            )
        name = str(reference.get("termName") or "").strip().casefold()
        parent = str(reference.get("codelistName") or "").strip().casefold()
        if not name or not parent:
            _fail(
                "OSB_STUDY_METADATA_TERM_REFERENCE_INVALID",
                "A term and governing codelist are required.",
            )
        if context is None:
            _fail(
                "OSB_STUDY_METADATA_CONTEXT_REQUIRED",
                "The native study terminology selections are required.",
            )
        packages = (
            [item.get("packageUid") for item in context.get("selectedPackages", [])]
            if isinstance(context, dict)
            else [item.package_uid for item in context.selected_packages]
        )
        matches, incomplete = MappingContextService._controlled_terminology_v2(
            [name],
            [name],
            [parent],
            packages,
            51,
        )
        if not matches and not incomplete:
            matches, incomplete = (
                MappingContextService._controlled_terminology_sponsor_v2(
                    [name],
                    [name],
                    [parent],
                    51,
                )
            )
        exact = [
            item
            for item in matches
            if name
            in {
                str(item.label or "").strip().casefold(),
                str(item.submission_value or "").strip().casefold(),
                str(item.uid or "").strip().casefold(),
            }
        ]
        identities = {(item.uid, item.version) for item in exact}
        if incomplete or len(matches) >= 51 or len(identities) != 1:
            _fail(
                "OSB_STUDY_METADATA_TERM_UNRESOLVED",
                f"An exact, unambiguous {parent} binding is required for {name}.",
            )
        selected = exact[0]
        return {
            "value": {"term_uid": selected.uid},
            "identity": jsonable_encoder(selected, exclude_none=False),
        }

    @staticmethod
    def _resolve_dictionary(reference: dict[str, Any]):
        from clinical_mdr_api.services.dictionaries.dictionary_codelist_generic_service import (
            DictionaryCodelistGenericService,
        )
        from clinical_mdr_api.services.dictionaries.dictionary_term_generic_service import (
            DictionaryTermGenericService,
        )

        name = str(reference.get("termName") or "").strip()
        library = str(reference.get("libraryName") or "").strip()
        codelist = str(reference.get("dictionaryCodelistName") or "").strip()
        if not all((name, library, codelist)):
            _fail(
                "OSB_STUDY_METADATA_DICTIONARY_REFERENCE_INVALID",
                "A source term, library and dictionary codelist are required.",
            )
        parents = DictionaryCodelistGenericService().get_all_dictionary_codelists(
            library=library,
            page_size=2,
            total_count=True,
            filter_by={
                "name": {"v": [codelist], "op": "eq"},
                "status": {"v": ["Final"], "op": "eq"},
            },
        )
        exact_parents = [
            item
            for item in parents.items
            if item.name.casefold() == codelist.casefold()
            and item.library_name.casefold() == library.casefold()
            and item.status == "Final"
            and item.version
        ]
        if len(exact_parents) != 1 or (
            parents.total is not None and parents.total != 1
        ):
            _fail(
                "OSB_STUDY_METADATA_DICTIONARY_UNRESOLVED",
                "The source dictionary codelist has no unique final binding.",
            )
        parent = exact_parents[0]
        terms = DictionaryTermGenericService().get_all_dictionary_terms(
            codelist_uid=parent.codelist_uid,
            page_size=2,
            total_count=True,
            filter_by={
                "name": {"v": [name], "op": "eq"},
                "status": {"v": ["Final"], "op": "eq"},
            },
        )
        exact = [
            item
            for item in terms.items
            if item.name.casefold() == name.casefold()
            and item.library_name.casefold() == library.casefold()
            and item.status == "Final"
            and item.version
        ]
        if len(exact) != 1 or (terms.total is not None and terms.total != 1):
            _fail(
                "OSB_STUDY_METADATA_DICTIONARY_UNRESOLVED",
                "The source term has no unique final binding in its dictionary codelist.",
            )
        selected = exact[0]
        return {
            "value": {"term_uid": selected.term_uid},
            "identity": {
                "resourceType": "DictionaryTerm",
                "uid": selected.term_uid,
                "version": selected.version,
                "parentUid": parent.codelist_uid,
                "parentVersion": parent.version,
                "library": library,
                "valueHash": canonical_json_hash_ref(
                    jsonable_encoder(selected, exclude_none=False),
                    schema_version="OsbMetadataDictionaryBindingV1@1.0.0",
                ),
            },
        }


def _resolved_value(
    value: Any, plan: dict[str, Any], port: Any, context: Any, bindings: list
):
    if isinstance(value, list):
        return [_resolved_value(item, plan, port, context, bindings) for item in value]
    if isinstance(value, dict):
        if "uid" in value or "term_uid" in value:
            _fail(
                "OSB_STUDY_METADATA_REFERENCE_INVALID",
                "Source plans name references; only OSB supplies native identities.",
            )
        for kind, collection in (
            ("termRef", "termRefs"),
            ("unitRef", "unitRefs"),
            ("dictionaryRef", "dictionaryRefs"),
        ):
            if kind in value:
                if len(value) != 1 or value[kind] not in (plan.get(collection) or {}):
                    _fail(
                        "OSB_STUDY_METADATA_REFERENCE_INVALID",
                        "The metadata reference has no source-pinned definition.",
                    )
                resolved = port.resolve(
                    kind, plan[collection][value[kind]], context, plan
                )
                bindings.append(
                    {
                        "kind": kind,
                        "reference": plan[collection][value[kind]],
                        "identity": resolved["identity"],
                    }
                )
                return resolved["value"]
        return {
            key: _resolved_value(item, plan, port, context, bindings)
            for key, item in value.items()
        }
    return value


def _patch_body(study: dict[str, Any], values: dict[str, Any]):
    metadata: dict[str, Any] = {}
    for path, value in values.items():
        _put(metadata, path, deepcopy(value))
        if _get(study.get("current_metadata") or {}, _null_path(path)) is not None:
            _put(metadata, _null_path(path), None)
    return {"current_metadata": metadata, "study_parent_part_uid": _parent_uid(study)}


def _preview_values(port: Any, uid: str, study: dict[str, Any], values: dict[str, Any]):
    try:
        preview = port.preview(uid, _patch_body(study, values))
    except (BusinessLogicException, NotFoundException, ValidationException) as error:
        _fail("OSB_STUDY_METADATA_NATIVE_RULE_REJECTED", str(error))
    if preview.get("uid") != uid:
        _fail(
            "OSB_STUDY_METADATA_PREVIEW_MISMATCH",
            "The native preview returned a different study.",
        )
    for path, expected in values.items():
        if canonical_json(
            _normalized(_get(preview.get("current_metadata") or {}, path))
        ) != canonical_json(_normalized(expected)):
            _fail(
                "OSB_STUDY_METADATA_PREVIEW_MISMATCH",
                "The native preview would change or discard the proposed value.",
            )


def prepare_metadata_offers(
    intents: list[dict[str, Any]], uid: str, context: Any, *, port=None
):
    selected = [item for item in intents if item.get("resourceFamily") == FAMILY]
    if not selected:
        return {}
    port = port or NativeStudyMetadataPort()
    study = port.read(uid)
    _assert_draft(study, uid)
    result = {}
    for intent in selected:
        key = f'{intent["factId"]}@{intent["revision"]}:{intent["targetKey"]}'
        plan = intent.get("nativeStudyOperation") or {}
        required = {
            "contractVersion": PLAN_CONTRACT,
            "kind": "native-metadata",
            "osbResourceType": "StudyMetadata",
            "method": "PATCH",
            "route": "/studies/{study_uid}",
        }
        if (
            any(plan.get(field) != value for field, value in required.items())
            or plan.get("metadataPath") not in METADATA_PATHS
            or "metadataValue" not in plan
        ):
            _fail(
                "OSB_STUDY_METADATA_PLAN_INVALID",
                "The request must contain a supported, source-pinned metadata plan.",
            )
        if (
            plan.get("metadataJoinedText") is True
            and plan["metadataPath"] not in JOINED_TEXT_PATHS
        ) or (
            plan.get("metadataMultiValued") is True
            and not plan["metadataPath"].endswith("_codes")
        ):
            _fail(
                "OSB_STUDY_METADATA_PLAN_INVALID",
                "The request must contain a supported, source-pinned metadata plan.",
            )
        bindings: list[dict[str, Any]] = []
        try:
            value = _resolved_value(
                plan["metadataValue"], plan, port, context, bindings
            )
            if plan.get("metadataMultiValued") is True and not isinstance(value, list):
                value = [value]
            _validate_metadata_value(plan["metadataPath"], value)
            _preview_values(port, uid, study, {plan["metadataPath"]: value})
        except _candidate_error_type() as error:
            result[key] = {"createOption": None, "blockers": [error.code]}
            continue
        offer = {
            "contractVersion": OFFER_CONTRACT,
            "nativeStudyId": uid,
            "metadataPath": plan["metadataPath"],
            "metadataValue": value,
            "joinedText": plan.get("metadataJoinedText") is True,
            "multiValued": plan.get("metadataMultiValued") is True,
            "sourcePlanHash": canonical_json_hash_ref(
                plan, schema_version=PLAN_CONTRACT
            ),
            "nativePreconditionHash": _precondition(study, plan["metadataPath"]),
            "referenceBindings": bindings,
        }
        result[key] = {
            "createOption": {
                "allowed": True,
                "requestedNativeType": "StudyMetadata",
                "nativeStudyOperation": offer,
                "nativeStudyOperationHash": canonical_json_hash_ref(
                    offer, schema_version=OFFER_CONTRACT
                ),
            },
            "blockers": [],
        }
    return result


def read_metadata_target(selected: dict[str, Any], *, port=None):
    uid = str(selected.get("uid") or selected.get("nativeStudyId") or "")
    path = selected.get("metadataPath")
    if not uid or path not in METADATA_PATHS:
        _fail(
            "OSB_STUDY_METADATA_READBACK_IDENTITY_INVALID",
            "Native study and metadata path are required.",
        )
    study = (port or NativeStudyMetadataPort()).read(uid)
    if study.get("uid") != uid:
        _fail(
            "OSB_STUDY_METADATA_READBACK_IDENTITY_INVALID",
            "The native read returned a different study.",
        )
    version = _get(study, "current_metadata.version_metadata.version_timestamp")
    if not isinstance(version, str) or not version:
        _fail(
            "OSB_STUDY_METADATA_READBACK_VERSION_MISSING",
            "The native study timestamp is missing.",
        )
    return {
        "uid": uid,
        "version": version,
        "label": path,
        "resourceType": "StudyMetadata",
        "resourceFamily": FAMILY,
        "metadataPath": path,
        "metadataValue": _normalized(_get(study.get("current_metadata") or {}, path)),
    }


def verify_metadata_reference_bindings(
    offer: dict[str, Any], plan: dict[str, Any], context: Any, *, port=None
):
    port = port or NativeStudyMetadataPort()
    for binding in offer.get("referenceBindings", []):
        if context is None:
            _fail(
                "OSB_STUDY_METADATA_REFERENCE_CONTEXT_REQUIRED",
                "The reviewed mapping context is required for library references.",
            )
        current = port.resolve(binding["kind"], binding["reference"], context, plan)
        if canonical_json(current["identity"]) != canonical_json(binding["identity"]):
            _fail(
                "OSB_STUDY_METADATA_REFERENCE_CHANGED",
                "A library reference changed after this value was offered.",
            )


def compose_metadata_values(grouped: dict[str, list[dict[str, Any]]]):
    """Compose exact contributors, refusing conflicting scalar proposals."""
    result = {}
    for path, contributors in grouped.items():
        values = list({canonical_json(item["metadataValue"]): item["metadataValue"]
                       for item in contributors}.values())
        if len(values) == 1:
            value = values[0]
        elif all(item.get("joinedText") is True for item in contributors) and all(
            isinstance(item, str) for item in values
        ):
            value = "\n".join(values)
        elif all(item.get("multiValued") is True for item in contributors) and all(
            isinstance(item, list) for item in values
        ):
            value = list({canonical_json(item): item for items_value in values for item in items_value}.values())
        else:
            _fail("OSB_STUDY_METADATA_CONFLICT", f"Selected source facts disagree on {path}.")
        _validate_metadata_value(path, value)
        result[path] = value
    return result


def apply_metadata_selections(
    items: list[dict[str, Any]], uid: str, *, context=None, port=None,
    result_pre_versions: dict[str, str | None] | None = None,
):
    """Apply all selected metadata contributors in one native PATCH.

    Caller owns the signed-decision transaction. Every precondition is checked
    under the native study lock before that PATCH; any readback mismatch raises
    into the same transaction rather than creating successful evidence.

    `result_pre_versions`, when given, receives per selection key the study's
    version timestamp read under the lock BEFORE the PATCH, so the evidence can
    state the target version the write started from rather than null.
    """
    if not items:
        return {}
    port = port or NativeStudyMetadataPort()
    port.lock(uid)
    study = port.read(uid)
    _assert_draft(study, uid)
    pre_version = _get(study, "current_metadata.version_metadata.version_timestamp")
    pre_version = pre_version if isinstance(pre_version, str) and pre_version else None
    grouped: dict[str, list[dict[str, Any]]] = {}
    offers = {}
    for item in items:
        intent, candidate = item["intent"], item["candidate"]
        key = f'{intent["factId"]}@{intent["revision"]}:{intent["targetKey"]}'
        option = candidate.get("createOption") or {}
        offer = option.get("nativeStudyOperation") or {}
        plan = intent.get("nativeStudyOperation") or {}
        if (
            offer.get("contractVersion") != OFFER_CONTRACT
            or offer.get("nativeStudyId") != uid
            or offer.get("metadataPath") not in METADATA_PATHS
            or not hash_refs_equal(
                canonical_json_hash_ref(offer, schema_version=OFFER_CONTRACT),
                option.get("nativeStudyOperationHash"),
            )
            or not hash_refs_equal(
                canonical_json_hash_ref(plan, schema_version=PLAN_CONTRACT),
                offer.get("sourcePlanHash"),
            )
        ):
            _fail(
                "OSB_STUDY_METADATA_OFFER_MISMATCH",
                "The reviewed metadata offer or its source plan changed.",
            )
        if not hash_refs_equal(
            _precondition(study, offer["metadataPath"]),
            offer.get("nativePreconditionHash"),
        ):
            _fail(
                "OSB_STUDY_METADATA_PRECONDITION_FAILED",
                "A native property changed after this candidate was offered.",
            )
        grouped.setdefault(offer["metadataPath"], []).append(offer)
        offers[key] = offer
    port.lock_references(
        [binding for offer in offers.values() for binding in offer["referenceBindings"]]
    )
    for item in items:
        offer = offers[
            f'{item["intent"]["factId"]}@{item["intent"]["revision"]}:{item["intent"]["targetKey"]}'
        ]
        verify_metadata_reference_bindings(
            offer, item["intent"]["nativeStudyOperation"], context, port=port
        )
    values_by_path = compose_metadata_values(grouped)
    expected = {path: _normalized(value) for path, value in values_by_path.items()}
    _preview_values(port, uid, study, values_by_path)
    port.patch(uid, _patch_body(study, values_by_path))
    for item in items:
        offer = offers[
            f'{item["intent"]["factId"]}@{item["intent"]["revision"]}:{item["intent"]["targetKey"]}'
        ]
        verify_metadata_reference_bindings(
            offer, item["intent"]["nativeStudyOperation"], context, port=port
        )
    observed = {}
    for key, offer in offers.items():
        target = read_metadata_target(
            {"uid": uid, "metadataPath": offer["metadataPath"]}, port=port
        )
        if canonical_json(target["metadataValue"]) != canonical_json(
            expected[offer["metadataPath"]]
        ):
            _fail(
                "OSB_STUDY_METADATA_READBACK_MISMATCH",
                "The native property does not match the reviewed value.",
            )
        observed[key] = target
        if result_pre_versions is not None:
            result_pre_versions[key] = pre_version
    return observed
