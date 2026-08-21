"""Authoritative OpenStudyBuilder study-definition snapshot.

This is the first cut of the release boundary used by the protocol pipeline and
future EDC V2 package. It deliberately combines OSB's editable native study
models with OSB's own CDISC USDM v4 projection. The current carrier-compatible
EDC bundle is not an input and is never allowed to overwrite this snapshot.
"""

import hashlib
import json
from typing import Any

from fastapi.encoders import jsonable_encoder
from neomodel import db

from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyComponentEnum,
)
from clinical_mdr_api.models.integrations.study_authority import (
    AuthorityMode,
    StudyAuthorityBlocker,
    StudyAuthorityCounts,
    StudyAuthorityReconciliationRow,
    StudyAuthoritySnapshot,
)
from clinical_mdr_api.services.ddf.usdm_service import USDMService
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.services.studies.study_activity_instance_selection import (
    StudyActivityInstanceSelectionService,
)
from clinical_mdr_api.services.studies.study_activity_schedule import (
    StudyActivityScheduleService,
)
from clinical_mdr_api.services.studies.study_activity_selection import (
    StudyActivitySelectionService,
)
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from clinical_mdr_api.services.studies.study_compound_dosing_selection import (
    StudyCompoundDosingSelectionService,
)
from clinical_mdr_api.services.studies.study_compound_selection import (
    StudyCompoundSelectionService,
)
from clinical_mdr_api.services.studies.study_criteria_selection import (
    StudyCriteriaSelectionService,
)
from clinical_mdr_api.services.studies.study_design_cell import StudyDesignCellService
from clinical_mdr_api.services.studies.study_element_selection import (
    StudyElementSelectionService,
)
from clinical_mdr_api.services.studies.study_endpoint_selection import (
    StudyEndpointSelectionService,
)
from clinical_mdr_api.services.studies.study_epoch import StudyEpochService
from clinical_mdr_api.services.studies.study_objective_selection import (
    StudyObjectiveSelectionService,
)
from clinical_mdr_api.services.studies.study_standard_version_selection import (
    StudyStandardVersionService,
)
from clinical_mdr_api.services.studies.study_visit import StudyVisitService
from common.config import settings


def _as_json(value: Any) -> Any:
    """Convert Pydantic/USDM values to stable JSON-compatible data."""
    return jsonable_encoder(value, exclude_none=False)


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _append_unique(items: list[dict[str, Any]], value: dict[str, Any]) -> None:
    if value not in items:
        items.append(value)


def _assemble_study_odm_metadata(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the exact ODM graph reachable from selected activity items."""
    activity_items: dict[str, dict[str, Any]] = {}
    odm_items: dict[tuple[str, str], dict[str, Any]] = {}
    item_groups: dict[tuple[str, str], dict[str, Any]] = {}
    forms: dict[tuple[str, str], dict[str, Any]] = {}
    study_events: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        activity_item = row.get("activity_item") or {}
        # The same semantic item may legitimately occur twice. Use Neo4j's
        # run-local element identity only for grouping query fan-out; it is not
        # emitted into the authority payload or content hash.
        activity_key = str(
            row.get("activity_item_key") or _canonical_hash(activity_item)
        )
        if activity_key not in activity_items:
            activity_items[activity_key] = {
                **activity_item,
                "odmItemRefs": [],
            }

        odm_item = row.get("odm_item")
        if odm_item:
            item_key = (
                str(odm_item.get("uid") or ""),
                str(odm_item.get("version") or ""),
            )
            item = odm_items.setdefault(
                item_key,
                {**odm_item, "activityItemLinks": []},
            )
            item_ref = {"uid": item_key[0], "version": item_key[1]}
            _append_unique(activity_items[activity_key]["odmItemRefs"], item_ref)
            _append_unique(
                item["activityItemLinks"],
                {
                    "activityItem": activity_item,
                    **(row.get("activity_item_link") or {}),
                },
            )

        odm_group = row.get("odm_item_group")
        if odm_group:
            group_key = (
                str(odm_group.get("uid") or ""),
                str(odm_group.get("version") or ""),
            )
            group = item_groups.setdefault(
                group_key,
                {**odm_group, "itemRefs": []},
            )
            if odm_item:
                _append_unique(
                    group["itemRefs"],
                    {
                        "uid": str(odm_item.get("uid") or ""),
                        "version": str(odm_item.get("version") or ""),
                        **(row.get("item_ref") or {}),
                    },
                )

        odm_form = row.get("odm_form")
        if odm_form:
            form_key = (
                str(odm_form.get("uid") or ""),
                str(odm_form.get("version") or ""),
            )
            form = forms.setdefault(
                form_key,
                {**odm_form, "itemGroupRefs": []},
            )
            if odm_group:
                _append_unique(
                    form["itemGroupRefs"],
                    {
                        "uid": str(odm_group.get("uid") or ""),
                        "version": str(odm_group.get("version") or ""),
                        **(row.get("item_group_ref") or {}),
                    },
                )

        odm_event = row.get("odm_study_event")
        if odm_event:
            event_key = (
                str(odm_event.get("uid") or ""),
                str(odm_event.get("version") or ""),
            )
            event = study_events.setdefault(
                event_key,
                {**odm_event, "formRefs": []},
            )
            if odm_form:
                _append_unique(
                    event["formRefs"],
                    {
                        "uid": str(odm_form.get("uid") or ""),
                        "version": str(odm_form.get("version") or ""),
                        **(row.get("form_ref") or {}),
                    },
                )

    def ordered(values: dict) -> list[dict[str, Any]]:
        return sorted(
            values.values(),
            key=lambda item: (
                str(item.get("uid") or item.get("studyActivityInstanceUid") or ""),
                str(item.get("version") or item.get("activityInstanceUid") or ""),
                _canonical_hash(item),
            ),
        )

    activity_item_values = ordered(activity_items)
    return {
        "scope": "study-reachable-native-odm",
        "activityItems": activity_item_values,
        "items": ordered(odm_items),
        "itemGroups": ordered(item_groups),
        "forms": ordered(forms),
        "studyEvents": ordered(study_events),
        "unmappedActivityItemCount": sum(
            not item["odmItemRefs"] for item in activity_item_values
        ),
    }


def _get_study_odm_metadata(
    study_uid: str, study_value_version: str | None
) -> dict[str, Any]:
    """Load ODM forms/groups/items/events reachable from one native study version."""
    query = """
        MATCH (study_root:StudyRoot {uid: $study_uid})
              -[study_version:HAS_VERSION|LATEST]->(study_value:StudyValue)
        WHERE ($study_value_version IS NULL AND type(study_version) = 'LATEST')
           OR ($study_value_version IS NOT NULL
               AND type(study_version) = 'HAS_VERSION'
               AND study_version.version = $study_value_version)
        MATCH (study_value)-[:HAS_STUDY_ACTIVITY_INSTANCE]
              ->(selection:StudyActivityInstance)
              -[:HAS_SELECTED_ACTIVITY_INSTANCE]
              ->(activity_instance:ActivityInstanceValue)
        MATCH (activity_instance_root:ActivityInstanceRoot)
              -[activity_instance_version:HAS_VERSION]->(activity_instance)
        MATCH (activity_instance)-[:CONTAINS_ACTIVITY_ITEM]
              ->(activity_item:ActivityItem)
        OPTIONAL MATCH (activity_item)<-[:HAS_ACTIVITY_ITEM]
              -(activity_item_class_root:ActivityItemClassRoot)-[:LATEST]
              ->(activity_item_class:ActivityItemClassValue)
        OPTIONAL MATCH (odm_item:OdmItemValue)
              -[activity_item_link:LINKS_TO_ACTIVITY_ITEM]->(activity_item)
        OPTIONAL MATCH (odm_item_root:OdmItemRoot)
              -[odm_item_version:HAS_VERSION]->(odm_item)
        OPTIONAL MATCH (odm_item_group:OdmItemGroupValue)
              -[item_ref:ITEM_REF]->(odm_item)
        OPTIONAL MATCH (odm_item_group_root:OdmItemGroupRoot)
              -[odm_item_group_version:HAS_VERSION]->(odm_item_group)
        OPTIONAL MATCH (odm_form:OdmFormValue)
              -[item_group_ref:ITEM_GROUP_REF]->(odm_item_group)
        OPTIONAL MATCH (odm_form_root:OdmFormRoot)
              -[odm_form_version:HAS_VERSION]->(odm_form)
        OPTIONAL MATCH (odm_study_event:OdmStudyEventValue)
              -[form_ref:FORM_REF]->(odm_form)
        OPTIONAL MATCH (odm_study_event_root:OdmStudyEventRoot)
              -[odm_study_event_version:HAS_VERSION]->(odm_study_event)
        RETURN {
            studyActivityInstanceUid: selection.uid,
            activityInstanceUid: activity_instance_root.uid,
            activityInstanceVersion: activity_instance_version.version,
            activityInstanceVersionMetadata: properties(activity_instance_version),
            activityItemClassUid: activity_item_class_root.uid,
            activityItemClassName: activity_item_class.display_name,
            activityItemClassDefinition: activity_item_class.definition,
            activityItemClassNciConceptId: activity_item_class.nci_concept_id,
            textValue: activity_item.text_value,
            sourceProperties: properties(activity_item)
        } AS activity_item,
        elementId(activity_item) AS activity_item_key,
        CASE WHEN odm_item IS NULL THEN NULL ELSE {
            uid: odm_item_root.uid,
            version: odm_item_version.version,
            versionMetadata: properties(odm_item_version),
            name: odm_item.name,
            oid: odm_item.oid,
            prompt: odm_item.prompt,
            datatype: odm_item.datatype,
            length: odm_item.length,
            significantDigits: odm_item.significant_digits,
            sasFieldName: odm_item.sas_field_name,
            sdsVarName: odm_item.sds_var_name,
            origin: odm_item.origin,
            comment: odm_item.comment,
            sourceProperties: properties(odm_item)
        } END AS odm_item,
        CASE WHEN activity_item_link IS NULL THEN NULL ELSE {
            order: activity_item_link.order,
            primary: activity_item_link.primary,
            presetResponseValue: activity_item_link.preset_response_value,
            valueCondition: activity_item_link.value_condition,
            valueDependentMap: activity_item_link.value_dependent_map,
            sourceProperties: properties(activity_item_link)
        } END AS activity_item_link,
        CASE WHEN odm_item_group IS NULL THEN NULL ELSE {
            uid: odm_item_group_root.uid,
            version: odm_item_group_version.version,
            versionMetadata: properties(odm_item_group_version),
            name: odm_item_group.name,
            oid: odm_item_group.oid,
            repeating: odm_item_group.repeating,
            isReferenceData: odm_item_group.is_reference_data,
            sasDatasetName: odm_item_group.sas_dataset_name,
            origin: odm_item_group.origin,
            purpose: odm_item_group.purpose,
            comment: odm_item_group.comment,
            sourceProperties: properties(odm_item_group)
        } END AS odm_item_group,
        CASE WHEN item_ref IS NULL THEN NULL ELSE {
            orderNumber: item_ref.order_number,
            mandatory: item_ref.mandatory,
            keySequence: item_ref.key_sequence,
            methodOid: item_ref.method_oid,
            imputationMethodOid: item_ref.imputation_method_oid,
            role: item_ref.role,
            roleCodelistOid: item_ref.role_codelist_oid,
            collectionExceptionConditionOid: item_ref.collection_exception_condition_oid,
            vendor: item_ref.vendor,
            sourceProperties: properties(item_ref)
        } END AS item_ref,
        CASE WHEN odm_form IS NULL THEN NULL ELSE {
            uid: odm_form_root.uid,
            version: odm_form_version.version,
            versionMetadata: properties(odm_form_version),
            name: odm_form.name,
            oid: odm_form.oid,
            repeating: odm_form.repeating,
            sdtmVersion: odm_form.sdtm_version,
            sourceProperties: properties(odm_form)
        } END AS odm_form,
        CASE WHEN item_group_ref IS NULL THEN NULL ELSE {
            orderNumber: item_group_ref.order_number,
            mandatory: item_group_ref.mandatory,
            collectionExceptionConditionOid: item_group_ref.collection_exception_condition_oid,
            vendor: item_group_ref.vendor,
            sourceProperties: properties(item_group_ref)
        } END AS item_group_ref,
        CASE WHEN odm_study_event IS NULL THEN NULL ELSE {
            uid: odm_study_event_root.uid,
            version: odm_study_event_version.version,
            versionMetadata: properties(odm_study_event_version),
            name: odm_study_event.name,
            oid: odm_study_event.oid,
            effectiveDate: odm_study_event.effective_date,
            retiredDate: odm_study_event.retired_date,
            description: odm_study_event.description,
            displayInTree: odm_study_event.display_in_tree,
            sourceProperties: properties(odm_study_event)
        } END AS odm_study_event,
        CASE WHEN form_ref IS NULL THEN NULL ELSE {
            orderNumber: form_ref.order_number,
            mandatory: form_ref.mandatory,
            locked: form_ref.locked,
            collectionExceptionConditionOid: form_ref.collection_exception_condition_oid,
            sourceProperties: properties(form_ref)
        } END AS form_ref
        ORDER BY selection.uid, activity_instance_root.uid,
                 odm_study_event_root.uid, odm_form_root.uid,
                 odm_item_group_root.uid, odm_item_root.uid
    """
    result, columns = db.cypher_query(
        query,
        {
            "study_uid": study_uid,
            "study_value_version": study_value_version,
        },
    )
    return _assemble_study_odm_metadata([dict(zip(columns, row)) for row in result])


def _usdm_designs(usdm: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return usdm["study"]["versions"][0]["studyDesigns"] or []
    except (KeyError, IndexError, TypeError):
        return []


def _usdm_version(usdm: dict[str, Any]) -> dict[str, Any]:
    try:
        return usdm["study"]["versions"][0] or {}
    except (KeyError, IndexError, TypeError):
        return {}


def _usdm_counts(usdm: dict[str, Any]) -> dict[str, int]:
    version = _usdm_version(usdm)
    designs = _usdm_designs(usdm)
    objectives = [
        objective
        for design in designs
        for objective in (design.get("objectives") or [])
    ]
    endpoints = [
        endpoint
        for objective in objectives
        for endpoint in (objective.get("endpoints") or [])
    ]
    timelines = [
        timeline
        for design in designs
        for timeline in (design.get("scheduleTimelines") or [])
    ]
    eligibility_criteria = [
        criterion
        for design in designs
        for criterion in (design.get("eligibilityCriteria") or [])
    ]
    administrations = [
        administration
        for intervention in (version.get("studyInterventions") or [])
        for administration in (intervention.get("administrations") or [])
    ]
    return {
        "objectives": len(objectives),
        "endpoints": len(endpoints),
        "arms": sum(len(design.get("arms") or []) for design in designs),
        "epochs": sum(len(design.get("epochs") or []) for design in designs),
        "elements": sum(len(design.get("elements") or []) for design in designs),
        "design_cells": sum(len(design.get("studyCells") or []) for design in designs),
        "encounters": sum(len(design.get("encounters") or []) for design in designs),
        "activities": sum(len(design.get("activities") or []) for design in designs),
        "interventions": len(version.get("studyInterventions") or []),
        "administrations": len(administrations),
        "eligibility_criteria": len(eligibility_criteria),
        "eligibility_criterion_items": len(
            version.get("eligibilityCriterionItems") or []
        ),
        "population_criterion_links": sum(
            len((design.get("population") or {}).get("criterionIds") or [])
            for design in designs
        ),
        "scheduled_activity_links": sum(
            len(instance.get("activityIds") or [])
            for timeline in timelines
            for instance in (timeline.get("instances") or [])
        ),
    }


def _usdm_void_code_count(value: Any) -> int:
    if isinstance(value, dict):
        own = int(
            value.get("instanceType") == "Code"
            and not value.get("code")
            and not value.get("decode")
        )
        return own + sum(_usdm_void_code_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_usdm_void_code_count(item) for item in value)
    return 0


def _usdm_entities(usdm: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    version = _usdm_version(usdm)
    designs = _usdm_designs(usdm)
    objectives = [
        objective
        for design in designs
        for objective in (design.get("objectives") or [])
    ]
    timelines = [
        timeline
        for design in designs
        for timeline in (design.get("scheduleTimelines") or [])
    ]
    return {
        "objectives": objectives,
        "endpoints": [
            endpoint
            for objective in objectives
            for endpoint in (objective.get("endpoints") or [])
        ],
        "arms": [item for design in designs for item in (design.get("arms") or [])],
        "epochs": [item for design in designs for item in (design.get("epochs") or [])],
        "elements": [
            item for design in designs for item in (design.get("elements") or [])
        ],
        "design_cells": [
            item for design in designs for item in (design.get("studyCells") or [])
        ],
        "encounters": [
            item for design in designs for item in (design.get("encounters") or [])
        ],
        "activities": [
            item for design in designs for item in (design.get("activities") or [])
        ],
        "eligibility_criteria": [
            item
            for design in designs
            for item in (design.get("eligibilityCriteria") or [])
        ],
        "eligibility_criterion_items": list(
            version.get("eligibilityCriterionItems") or []
        ),
        "population_criterion_ids": [
            criterion_id
            for design in designs
            for criterion_id in (
                (design.get("population") or {}).get("criterionIds") or []
            )
        ],
        "scheduled_instances": [
            item for timeline in timelines for item in (timeline.get("instances") or [])
        ],
    }


def _identity_row(
    *,
    resource_class: str,
    native_uid: str | None,
    usdm_item: dict[str, Any] | None,
    native_identity: dict[str, Any],
    expected_usdm_identity: dict[str, Any],
    relationship_identity: dict[str, Any] | None = None,
    blocker_code: str,
) -> StudyAuthorityReconciliationRow:
    if not native_uid:
        status = "unresolved"
    elif usdm_item is None:
        status = "missing"
    elif all(
        usdm_item.get(key) == expected
        for key, expected in expected_usdm_identity.items()
    ):
        status = "matched"
    else:
        status = "changed"
    return StudyAuthorityReconciliationRow(
        resource_class=resource_class,
        native_uid=native_uid,
        usdm_id=(
            usdm_item.get("id") or usdm_item.get("extensionId") if usdm_item else None
        ),
        native_identity=native_identity,
        usdm_identity=usdm_item or {},
        relationship_identity=relationship_identity or {},
        status=status,
        blocker_code=None if status == "matched" else blocker_code,
    )


def _build_usdm_extensions(
    endpoints: list[dict[str, Any]],
    criteria: list[dict[str, Any]],
    design_cells: list[dict[str, Any]] | None = None,
    activities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Versioned standards-extension coverage for native detail USDM omits.

    This is a governed projection of explicit native OSB selections, not a
    source-carrier blob. It is emitted only beside the official USDM document
    and is reconciled by native selection UID.
    """
    endpoint_semantics = []
    for index, row in enumerate(endpoints, start=1):
        endpoint = row.get("endpoint") or {}
        objective = row.get("study_objective") or {}
        endpoint_semantics.append(
            {
                "extensionId": f"EndpointSemanticsExtension_{index}",
                "studyEndpointUid": row.get("study_endpoint_uid"),
                "endpointUid": endpoint.get("uid"),
                "objectiveSelectionUid": objective.get("study_objective_uid"),
                "endpointSublevel": row.get("endpoint_sublevel"),
                "timeframe": row.get("timeframe"),
                "endpointUnits": row.get("endpoint_units"),
                "collectionDisposition": row.get("collection_disposition"),
            }
        )

    eligibility_criteria = []
    for index, row in enumerate(criteria, start=1):
        criterion = row.get("criteria") or {}
        eligibility_criteria.append(
            {
                "extensionId": f"EligibilityCriterionExtension_{index}",
                "studyCriteriaUid": row.get("study_criteria_uid"),
                "criteriaUid": criterion.get("uid"),
                "text": criterion.get("name_plain"),
                "textHtml": criterion.get("name"),
                "category": row.get("criteria_type"),
                "keyCriterion": row.get("key_criteria"),
                "criteriaVersion": criterion.get("version"),
            }
        )
    design_cell_semantics = [
        {
            "extensionId": f"StudyCellSemanticsExtension_{index}",
            "studyDesignCellUid": row.get("design_cell_uid"),
            "transitionRule": row.get("transition_rule"),
        }
        for index, row in enumerate(design_cells or [], start=1)
    ]
    activity_semantics = []
    for index, row in enumerate(activities or [], start=1):
        activity = row.get("activity") or {}
        activity_semantics.append(
            {
                "extensionId": f"ActivitySemanticsExtension_{index}",
                "studyActivityUid": row.get("study_activity_uid"),
                "activityUid": activity.get("uid"),
                "isConditional": row.get("is_conditional"),
                "procedureType": row.get("procedure_type"),
                "procedureCode": row.get("procedure_code")
                or activity.get("nci_concept_id"),
            }
        )
    return {
        "schemaVersion": "osb-usdm-extension/1.0",
        "mappingAuthority": "OpenStudyBuilder",
        "endpointSemantics": endpoint_semantics,
        "eligibilityCriteria": eligibility_criteria,
        "studyCellSemantics": design_cell_semantics,
        "activitySemantics": activity_semantics,
    }


def _build_reconciliation(
    *,
    usdm: dict[str, Any],
    objectives: list[dict[str, Any]],
    endpoints: list[dict[str, Any]],
    criteria: list[dict[str, Any]],
    arms: list[dict[str, Any]],
    epochs: list[dict[str, Any]],
    elements: list[dict[str, Any]],
    design_cells: list[dict[str, Any]],
    visits: list[dict[str, Any]],
    activities: list[dict[str, Any]],
    schedules: list[dict[str, Any]],
    usdm_extensions: dict[str, Any],
) -> list[StudyAuthorityReconciliationRow]:
    entities = _usdm_entities(usdm)
    rows: list[StudyAuthorityReconciliationRow] = []
    consumed: dict[str, set[str]] = {
        key: set()
        for key in (
            "objectives",
            "endpoints",
            "arms",
            "epochs",
            "elements",
            "design_cells",
            "encounters",
            "activities",
            "eligibility_criteria",
            "eligibility_criterion_items",
        )
    }

    objective_id_by_selection_uid: dict[str, str] = {}
    for index, native in enumerate(objectives, start=1):
        objective = native.get("objective") or {}
        actual = next(
            (
                item
                for item in entities["objectives"]
                if item.get("id") == f"Objective_{index}"
            ),
            None,
        )
        expected = {
            "label": objective.get("name_plain"),
            "text": objective.get("name_plain"),
        }
        row = _identity_row(
            resource_class="Objective",
            native_uid=native.get("study_objective_uid"),
            usdm_item=actual,
            native_identity={
                "objectiveUid": objective.get("uid"),
                "name": objective.get("name_plain"),
            },
            expected_usdm_identity=expected,
            blocker_code="USDM_OBJECTIVE_IDENTITY_MISMATCH",
        )
        rows.append(row)
        if row.usdm_id:
            consumed["objectives"].add(row.usdm_id)
            if native.get("study_objective_uid"):
                objective_id_by_selection_uid[native["study_objective_uid"]] = (
                    row.usdm_id
                )

    for index, native in enumerate(endpoints, start=1):
        endpoint = native.get("endpoint") or {}
        objective = native.get("study_objective") or {}
        actual = next(
            (
                item
                for item in entities["endpoints"]
                if item.get("id") == f"Endpoint_{index}"
            ),
            None,
        )
        objective_selection_uid = objective.get("study_objective_uid")
        actual_objective = next(
            (
                objective_row
                for objective_row in entities["objectives"]
                if any(
                    item.get("id") == (actual or {}).get("id")
                    for item in (objective_row.get("endpoints") or [])
                )
            ),
            None,
        )
        expected_objective_id = objective_id_by_selection_uid.get(
            objective_selection_uid
        )
        actual_with_relationship = (
            {**actual, "objectiveId": (actual_objective or {}).get("id")}
            if actual
            else None
        )
        row = _identity_row(
            resource_class="Endpoint",
            native_uid=native.get("study_endpoint_uid"),
            usdm_item=actual_with_relationship,
            native_identity={
                "endpointUid": endpoint.get("uid"),
                "name": endpoint.get("name_plain"),
            },
            expected_usdm_identity={
                "label": endpoint.get("name_plain") or "",
                "text": endpoint.get("name_plain") or "",
                "objectiveId": expected_objective_id,
            },
            relationship_identity={
                "objectiveSelectionUid": objective_selection_uid,
                "expectedUsdmObjectiveId": expected_objective_id,
                "actualUsdmObjectiveId": (actual_objective or {}).get("id"),
            },
            blocker_code="USDM_ENDPOINT_IDENTITY_MISMATCH",
        )
        rows.append(row)
        if row.usdm_id:
            consumed["endpoints"].add(row.usdm_id)

    families = (
        (
            "Arm",
            arms,
            "arms",
            "arm_uid",
            lambda native: {
                "name": native.get("name"),
                "label": native.get("name"),
                "description": native.get("description"),
            },
        ),
        (
            "StudyEpoch",
            epochs,
            "epochs",
            "uid",
            lambda native: {
                "name": native.get("epoch_name") or " ",
                "label": native.get("epoch_name"),
                "description": native.get("description"),
            },
        ),
        (
            "StudyElement",
            elements,
            "elements",
            "element_uid",
            lambda native: {
                "name": native.get("name"),
                "label": native.get("name"),
                "description": native.get("description"),
            },
        ),
        (
            "Encounter",
            visits,
            "encounters",
            "uid",
            lambda native: {
                "name": native.get("visit_short_name"),
                "label": native.get("visit_name"),
                "description": native.get("description"),
            },
        ),
    )
    id_prefix = {
        "Arm": "StudyArm",
        "StudyEpoch": "StudyEpoch",
        "StudyElement": "StudyElement",
        "Encounter": "Encounter",
    }
    for (
        resource_class,
        native_items,
        entity_key,
        uid_key,
        expected_identity,
    ) in families:
        for index, native in enumerate(native_items, start=1):
            actual = next(
                (
                    item
                    for item in entities[entity_key]
                    if item.get("id") == f"{id_prefix[resource_class]}_{index}"
                ),
                None,
            )
            row = _identity_row(
                resource_class=resource_class,
                native_uid=native.get(uid_key),
                usdm_item=actual,
                native_identity=expected_identity(native),
                expected_usdm_identity=expected_identity(native),
                blocker_code=f"USDM_{resource_class.upper()}_IDENTITY_MISMATCH",
            )
            rows.append(row)
            if row.usdm_id:
                consumed[entity_key].add(row.usdm_id)

    for index, native in enumerate(design_cells, start=1):
        actual = next(
            (
                item
                for item in entities["design_cells"]
                if item.get("id") == f"StudyCell_{index}"
            ),
            None,
        )
        expected = {
            "armId": _expected_id(
                "StudyArm", arms, "arm_uid", native.get("study_arm_uid")
            ),
            "epochId": _expected_id(
                "StudyEpoch", epochs, "uid", native.get("study_epoch_uid")
            ),
            "elementIds": [
                _expected_id(
                    "StudyElement",
                    elements,
                    "element_uid",
                    native.get("study_element_uid"),
                )
            ],
        }
        row = _identity_row(
            resource_class="StudyCell",
            native_uid=native.get("design_cell_uid"),
            usdm_item=actual,
            native_identity={"transitionRule": native.get("transition_rule")},
            expected_usdm_identity=expected,
            relationship_identity=expected,
            blocker_code="USDM_STUDYCELL_IDENTITY_MISMATCH",
        )
        rows.append(row)
        if row.usdm_id:
            consumed["design_cells"].add(row.usdm_id)

    for index, native in enumerate(activities, start=1):
        activity = native.get("activity") or {}
        subgroup = native.get("study_activity_subgroup") or {}
        actual = next(
            (
                item
                for item in entities["activities"]
                if item.get("id") == f"Activity_{index}"
            ),
            None,
        )
        expected = {
            "name": activity.get("name")
            or subgroup.get("activity_subgroup_name")
            or f"Unresolved activity {native.get('study_activity_uid')}",
            "label": activity.get("name_sentence_case"),
            "description": activity.get("definition"),
        }
        row = _identity_row(
            resource_class="Activity",
            native_uid=native.get("study_activity_uid"),
            usdm_item=actual,
            native_identity={
                "activityUid": activity.get("uid"),
                **expected,
            },
            expected_usdm_identity=expected,
            blocker_code="USDM_ACTIVITY_IDENTITY_MISMATCH",
        )
        rows.append(row)
        if row.usdm_id:
            consumed["activities"].add(row.usdm_id)

    native_schedule_pairs = set()
    actual_schedule_pairs = {
        (instance.get("encounterId"), activity_id)
        for instance in entities["scheduled_instances"]
        for activity_id in (instance.get("activityIds") or [])
    }
    for native in schedules:
        encounter_id = _expected_id(
            "Encounter", visits, "uid", native.get("study_visit_uid")
        )
        activity_id = _expected_id(
            "Activity",
            activities,
            "study_activity_uid",
            native.get("study_activity_uid"),
        )
        pair = (encounter_id, activity_id)
        native_schedule_pairs.add(pair)
        matched = pair in actual_schedule_pairs and all(pair)
        rows.append(
            StudyAuthorityReconciliationRow(
                resource_class="StudyActivitySchedule",
                native_uid=native.get("study_activity_schedule_uid"),
                usdm_id=(f"{encounter_id}:{activity_id}" if matched else None),
                native_identity={
                    "visitUid": native.get("study_visit_uid"),
                    "studyActivityUid": native.get("study_activity_uid"),
                },
                usdm_identity={
                    "encounterId": encounter_id,
                    "activityId": activity_id,
                },
                relationship_identity={
                    "encounterId": encounter_id,
                    "activityId": activity_id,
                },
                status="matched" if matched else "missing",
                blocker_code=(
                    None if matched else "USDM_ACTIVITY_SCHEDULE_IDENTITY_MISMATCH"
                ),
            )
        )

    for encounter_id, activity_id in sorted(
        actual_schedule_pairs - native_schedule_pairs,
        key=lambda pair: (str(pair[0]), str(pair[1])),
    ):
        rows.append(
            StudyAuthorityReconciliationRow(
                resource_class="StudyActivitySchedule",
                native_uid=None,
                usdm_id=f"{encounter_id}:{activity_id}",
                usdm_identity={
                    "encounterId": encounter_id,
                    "activityId": activity_id,
                },
                relationship_identity={
                    "encounterId": encounter_id,
                    "activityId": activity_id,
                },
                status="extra",
                blocker_code="USDM_ACTIVITY_SCHEDULE_EXTRA",
            )
        )

    # Eligibility is a paired USDM v4 shape: StudyDesign.eligibilityCriteria
    # references StudyVersion.eligibilityCriterionItems, and the population
    # references every criterion. Reconcile all three links so a superficially
    # present criterion cannot hide a broken or lossy export.
    for native in (row for row in criteria if row.get("criteria")):
        native_uid = native.get("study_criteria_uid")
        native_criterion = native.get("criteria") or {}
        expected_text = native_criterion.get("name_plain") or native_criterion.get(
            "name"
        )
        expected_category = native.get("criteria_type") or {}
        expected_category_uid = (
            expected_category.get("term_uid")
            or expected_category.get("termUid")
            or expected_category.get("uid")
        )
        expected_category_code = (
            str(expected_category_uid).split("_", 1)[0]
            if expected_category_uid
            else None
        )
        actual = next(
            (
                item
                for item in entities["eligibility_criteria"]
                if item.get("label") == native_uid
                or item.get("identifier") == native_uid
            ),
            None,
        )
        actual_item = next(
            (
                item
                for item in entities["eligibility_criterion_items"]
                if item.get("id") == (actual or {}).get("criterionItemId")
            ),
            None,
        )
        actual_category = (actual or {}).get("category") or {}
        comparable = (
            {
                **actual,
                "criterionItemResolved": actual_item is not None,
                "criterionItemLabel": (actual_item or {}).get("label"),
                "criterionItemText": (actual_item or {}).get("text"),
                "populationLinked": (actual or {}).get("id")
                in entities["population_criterion_ids"],
                "categoryCode": actual_category.get("code"),
            }
            if actual
            else None
        )
        row = _identity_row(
            resource_class="EligibilityCriterion",
            native_uid=native_uid,
            usdm_item=comparable,
            native_identity={
                "criteriaUid": native_criterion.get("uid"),
                "text": expected_text,
                "categoryCode": expected_category_code,
            },
            expected_usdm_identity={
                "label": native_uid,
                "identifier": native_uid,
                "description": expected_text,
                "criterionItemResolved": True,
                "criterionItemLabel": native_uid,
                "criterionItemText": expected_text,
                "populationLinked": True,
                "categoryCode": expected_category_code,
            },
            relationship_identity={
                "criterionItemId": (actual or {}).get("criterionItemId"),
                "criterionItemResolved": actual_item is not None,
                "populationLinked": (actual or {}).get("id")
                in entities["population_criterion_ids"],
            },
            blocker_code="USDM_ELIGIBILITY_PAIRED_SHAPE_MISMATCH",
        )
        rows.append(row)
        if actual and actual.get("id"):
            consumed["eligibility_criteria"].add(actual["id"])
        if actual_item and actual_item.get("id"):
            consumed["eligibility_criterion_items"].add(actual_item["id"])

    extension_criteria = {
        item.get("studyCriteriaUid"): item
        for item in usdm_extensions.get("eligibilityCriteria", [])
    }
    for native in criteria:
        native_uid = native.get("study_criteria_uid")
        criterion = native.get("criteria") or {}
        actual = extension_criteria.get(native_uid)
        rows.append(
            _identity_row(
                resource_class="EligibilityCriterionExtension",
                native_uid=native_uid,
                usdm_item=actual,
                native_identity={
                    "criteriaUid": criterion.get("uid"),
                    "text": criterion.get("name_plain"),
                    "keyCriterion": native.get("key_criteria"),
                },
                expected_usdm_identity={
                    "criteriaUid": criterion.get("uid"),
                    "text": criterion.get("name_plain"),
                    "keyCriterion": native.get("key_criteria"),
                },
                blocker_code="USDM_CRITERIA_EXTENSION_IDENTITY_MISMATCH",
            )
        )

    for entity_key, resource_class in (
        ("objectives", "Objective"),
        ("endpoints", "Endpoint"),
        ("arms", "Arm"),
        ("epochs", "StudyEpoch"),
        ("elements", "StudyElement"),
        ("design_cells", "StudyCell"),
        ("encounters", "Encounter"),
        ("activities", "Activity"),
        ("eligibility_criteria", "EligibilityCriterion"),
        ("eligibility_criterion_items", "EligibilityCriterionItem"),
    ):
        for item in entities[entity_key]:
            if item.get("id") not in consumed[entity_key]:
                rows.append(
                    StudyAuthorityReconciliationRow(
                        resource_class=resource_class,
                        usdm_id=item.get("id"),
                        usdm_identity=item,
                        status="extra",
                        blocker_code=f"USDM_{resource_class.upper()}_EXTRA",
                    )
                )
    return rows


def _expected_id(
    prefix: str,
    native_items: list[dict[str, Any]],
    uid_key: str,
    native_uid: str | None,
) -> str | None:
    for index, item in enumerate(native_items, start=1):
        if item.get(uid_key) == native_uid:
            return f"{prefix}_{index}"
    return None


def _mapping_blockers(
    *,
    authority_mode: AuthorityMode,
    native_study: dict[str, Any],
    usdm: dict[str, Any],
    standards: list[dict[str, Any]],
    objectives: list[dict[str, Any]],
    endpoints: list[dict[str, Any]],
    criteria: list[dict[str, Any]],
    integrity: dict[str, Any],
    visits: list[dict[str, Any]] | None = None,
    activities: list[dict[str, Any]] | None = None,
    design_cells: list[dict[str, Any]] | None = None,
    compounds: list[dict[str, Any]] | None = None,
    compound_dosings: list[dict[str, Any]] | None = None,
    usdm_extensions: dict[str, Any] | None = None,
    reconciliation: list[StudyAuthorityReconciliationRow] | None = None,
    structure_statistics: dict[str, Any] | None = None,
    study_value_version: str | None = None,
) -> list[StudyAuthorityBlocker]:
    """Release blockers for the OSB-authoritative mapping boundary.

    These checks are intentionally stricter than OSB's ordinary Draft editing
    rules. A source fact may remain unresolved while a study is Draft, but an EDC
    release may not omit or flatten a native OSB concept silently.
    """
    blockers: list[StudyAuthorityBlocker] = []
    metadata = native_study.get("current_metadata") or {}
    version = metadata.get("version_metadata") or {}
    status = str(version.get("study_status") or "")
    if status not in {"LOCKED", "RELEASED"}:
        blockers.append(
            StudyAuthorityBlocker(
                code="OSB_STUDY_NOT_RELEASED",
                path="nativeStudy.current_metadata.version_metadata.study_status",
                detail="EDC release requires an explicit locked or released OSB study version.",
            )
        )

    if not standards:
        blockers.append(
            StudyAuthorityBlocker(
                code="OSB_STANDARD_VERSION_MISSING",
                path="studyStandardVersions",
                detail="Select the study CT package(s) before resolving USDM, CDASH, SDTM, units or ODM metadata.",
            )
        )
    elif any(not row.get("ct_package") for row in standards):
        blockers.append(
            StudyAuthorityBlocker(
                code="OSB_STANDARD_PACKAGE_UNRESOLVED",
                path="studyStandardVersions[].ct_package",
                detail="Every StudyStandardVersion must resolve to a concrete OSB CT package.",
            )
        )
    else:
        selected_catalogues = {
            row["ct_package"].get("catalogue_name")
            for row in standards
            if row.get("ct_package")
        }
        for required_catalogue in ("DDF CT", "SDTM CT", "CDASH CT"):
            if required_catalogue not in selected_catalogues:
                blockers.append(
                    StudyAuthorityBlocker(
                        code="OSB_REQUIRED_STANDARD_CATALOGUE_MISSING",
                        path="studyStandardVersions",
                        detail=f"Select an explicit {required_catalogue} package before release.",
                    )
                )
        for row in standards:
            uid = row.get("uid") or "unknown"
            package = row.get("ct_package") or {}
            if row.get("automatically_created"):
                blockers.append(
                    StudyAuthorityBlocker(
                        code="OSB_STANDARD_VERSION_AUTO_SELECTED",
                        path=f"studyStandardVersions[{uid}]",
                        detail="A lock-time auto-selected package is not a reviewed standards decision; select the package explicitly.",
                    )
                )
            if not package.get("uid") or not package.get("effective_date"):
                blockers.append(
                    StudyAuthorityBlocker(
                        code="OSB_STANDARD_PACKAGE_IDENTITY_INCOMPLETE",
                        path=f"studyStandardVersions[{uid}].ct_package",
                        detail="The CT package must carry a durable UID and effective date.",
                    )
                )

    wrapper_version = usdm.get("usdmVersion")
    if not wrapper_version:
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_VERSION_MISSING",
                path="usdm.usdmVersion",
                detail="OSB's CDISC USDM projection must declare its model version.",
            )
        )

    # Reconcile native study selections against the USDM projection.
    usdm_counts = _usdm_counts(usdm)
    usdm_objectives = usdm_counts["objectives"]
    usdm_endpoints = usdm_counts["endpoints"]
    native_objective_instances = [row for row in objectives if row.get("objective")]
    native_endpoint_instances = [row for row in endpoints if row.get("endpoint")]
    native_interventions = [
        row
        for row in (compounds or [])
        if row.get("medicinal_product")
        or row.get("compound_alias")
        or row.get("compound")
    ]
    if len(native_objective_instances) != len(objectives):
        blockers.append(
            StudyAuthorityBlocker(
                code="OBJECTIVE_TEMPLATE_NOT_INSTANTIATED",
                path="studyObjectives",
                detail="Every selected objective template must be instantiated or explicitly excluded before release.",
            )
        )
    if len(native_endpoint_instances) != len(endpoints):
        blockers.append(
            StudyAuthorityBlocker(
                code="ENDPOINT_TEMPLATE_NOT_INSTANTIATED",
                path="studyEndpoints",
                detail="Every selected endpoint template must be instantiated or explicitly excluded before release.",
            )
        )

    for row in endpoints:
        uid = row.get("study_endpoint_uid") or "unknown"
        for field, code, detail in (
            ("endpoint_level", "ENDPOINT_LEVEL_MISSING", "Endpoint level is required."),
            (
                "timeframe",
                "ENDPOINT_TIMEFRAME_MISSING",
                "Endpoint timeframe is required.",
            ),
        ):
            if not row.get(field):
                blockers.append(
                    StudyAuthorityBlocker(
                        code=code,
                        path=f"studyEndpoints[{uid}].{field}",
                        detail=detail,
                    )
                )
        endpoint_units = row.get("endpoint_units") or {}
        if not endpoint_units.get("units"):
            blockers.append(
                StudyAuthorityBlocker(
                    code="ENDPOINT_UNITS_MISSING",
                    path=f"studyEndpoints[{uid}].endpoint_units.units",
                    detail="Endpoint unit selection is required.",
                )
            )
        if not row.get("endpoint_sublevel"):
            blockers.append(
                StudyAuthorityBlocker(
                    code="ENDPOINT_SUBLEVEL_MISSING",
                    path=f"studyEndpoints[{uid}].endpoint_sublevel",
                    detail="Endpoint sublevel is required for the governed endpoint semantics extension.",
                )
            )
        extension_rows = (usdm_extensions or {}).get("endpointSemantics", [])
        extension = next(
            (item for item in extension_rows if item.get("studyEndpointUid") == uid),
            None,
        )
        if not extension:
            blockers.append(
                StudyAuthorityBlocker(
                    code="ENDPOINT_SEMANTICS_EXTENSION_MISSING",
                    path=f"usdmExtensions.endpointSemantics[{uid}]",
                    detail="Endpoint timeframe, units and sublevel must be retained in the versioned governed extension.",
                )
            )
        elif extension.get("collectionDisposition") not in {"direct", "derived"}:
            blockers.append(
                StudyAuthorityBlocker(
                    code="ENDPOINT_COLLECTION_DISPOSITION_MISSING",
                    path=f"usdmExtensions.endpointSemantics[{uid}].collectionDisposition",
                    detail="Classify the endpoint as direct or derived; an endpoint is never assumed to be an EDC field.",
                )
            )

    if any(not row.get("criteria") for row in criteria):
        blockers.append(
            StudyAuthorityBlocker(
                code="CRITERIA_TEMPLATE_NOT_INSTANTIATED",
                path="studyCriteria",
                detail="Every selected eligibility-criteria template must be instantiated or explicitly excluded before release.",
            )
        )

    if usdm_objectives != len(native_objective_instances):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_OBJECTIVE_COVERAGE_GAP",
                path="usdm.study.versions[0].studyDesigns[].objectives",
                detail=f"USDM contains {usdm_objectives} objective(s), but OSB has {len(native_objective_instances)} instantiated objective(s).",
            )
        )
    if usdm_endpoints != len(native_endpoint_instances):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_ENDPOINT_COVERAGE_GAP",
                path="usdm.study.versions[0].studyDesigns[].objectives[].endpoints",
                detail=f"USDM contains {usdm_endpoints} endpoint(s), but OSB has {len(native_endpoint_instances)} instantiated endpoint(s).",
            )
        )
    if usdm_counts["interventions"] != len(native_interventions):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_INTERVENTION_COVERAGE_GAP",
                path="usdm.study.versions[0].studyInterventions",
                detail=(
                    f"USDM contains {usdm_counts['interventions']} intervention(s), "
                    f"but OSB has {len(native_interventions)} compound/product selection(s)."
                ),
            )
        )
    extension_criteria = (usdm_extensions or {}).get("eligibilityCriteria", [])
    native_criteria_instances = [row for row in criteria if row.get("criteria")]
    if usdm_counts["eligibility_criteria"] != len(native_criteria_instances):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_ELIGIBILITY_CRITERION_COVERAGE_GAP",
                path="usdm.study.versions[0].studyDesigns[].eligibilityCriteria",
                detail=(
                    f"USDM contains {usdm_counts['eligibility_criteria']} eligibility criterion/criteria, "
                    f"but OSB has {len(native_criteria_instances)} instantiated criterion/criteria."
                ),
            )
        )
    native_dosings = compound_dosings or []
    if usdm_counts["administrations"] != len(native_dosings):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_ADMINISTRATION_COVERAGE_GAP",
                path="usdm.study.versions[0].studyInterventions[].administrations",
                detail=(
                    f"USDM contains {usdm_counts['administrations']} administration(s), "
                    f"but OSB has {len(native_dosings)} compound dosing selection(s)."
                ),
            )
        )
    if usdm_counts["eligibility_criterion_items"] != len(native_criteria_instances):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_ELIGIBILITY_ITEM_COVERAGE_GAP",
                path="usdm.study.versions[0].eligibilityCriterionItems",
                detail=(
                    f"USDM contains {usdm_counts['eligibility_criterion_items']} eligibility item(s), "
                    f"but OSB has {len(native_criteria_instances)} instantiated criterion/criteria."
                ),
            )
        )
    if usdm_counts["population_criterion_links"] != len(native_criteria_instances):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_ELIGIBILITY_POPULATION_LINK_COVERAGE_GAP",
                path="usdm.study.versions[0].studyDesigns[].population.criterionIds",
                detail=(
                    f"USDM population(s) contain {usdm_counts['population_criterion_links']} criterion link(s), "
                    f"but OSB has {len(native_criteria_instances)} instantiated criterion/criteria."
                ),
            )
        )
    if len(extension_criteria) != len(native_criteria_instances):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_CRITERIA_EXTENSION_COVERAGE_GAP",
                path="usdmExtensions.eligibilityCriteria",
                detail=(
                    f"The governed extension contains {len(extension_criteria)} criterion/criteria, "
                    f"but OSB has {len(native_criteria_instances)} instantiated criterion/criteria."
                ),
            )
        )

    extension_cells = (usdm_extensions or {}).get("studyCellSemantics", [])
    if len(extension_cells) != len(design_cells or []):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_DESIGN_CELL_EXTENSION_COVERAGE_GAP",
                path="usdmExtensions.studyCellSemantics",
                detail="Every native design-cell transition rule must be retained in the governed extension.",
            )
        )

    global_anchors = [
        visit for visit in (visits or []) if visit.get("is_global_anchor_visit") is True
    ]
    if len(global_anchors) > 1:
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_MULTIPLE_GLOBAL_ANCHORS_UNSUPPORTED",
                path="studyVisits",
                detail="The current USDM projection supports at most one global anchor timeline.",
            )
        )
    if not global_anchors and any(
        visit.get("time_value") not in (None, 0) for visit in (visits or [])
    ):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_RELATIVE_TIMING_WITHOUT_ANCHOR",
                path="studyVisits",
                detail="Relative non-zero visit timing requires an explicit anchor; no synthetic anchor may be minted.",
            )
        )
    for visit in visits or []:
        uid = visit.get("uid") or "unknown"
        if not visit.get("visit_type"):
            blockers.append(
                StudyAuthorityBlocker(
                    code="VISIT_TYPE_MISSING",
                    path=f"studyVisits[{uid}].visit_type",
                    detail="Every visit requires a governed visit-type term before release.",
                )
            )
        if not visit.get("visit_contact_mode"):
            blockers.append(
                StudyAuthorityBlocker(
                    code="VISIT_CONTACT_MODE_MISSING",
                    path=f"studyVisits[{uid}].visit_contact_mode",
                    detail="Visit contact mode must be native governed data or explicitly unresolved.",
                )
            )
        if visit.get("time_value") is not None and not visit.get("time_unit_name"):
            blockers.append(
                StudyAuthorityBlocker(
                    code="VISIT_TIME_UNIT_MISSING",
                    path=f"studyVisits[{uid}].time_unit_name",
                    detail="A stated visit time value requires a governed time unit.",
                )
            )
        has_window = (
            visit.get("min_visit_window_value") is not None
            or visit.get("max_visit_window_value") is not None
        )
        if has_window and not visit.get("visit_window_unit_name"):
            blockers.append(
                StudyAuthorityBlocker(
                    code="VISIT_WINDOW_UNIT_MISSING",
                    path=f"studyVisits[{uid}].visit_window_unit_name",
                    detail="A stated visit window requires a governed window unit.",
                )
            )

    activity_extensions = {
        item.get("studyActivityUid"): item
        for item in (usdm_extensions or {}).get("activitySemantics", [])
    }
    for activity_row in activities or []:
        uid = activity_row.get("study_activity_uid") or "unknown"
        extension = activity_extensions.get(uid) or {}
        for field, code in (
            ("isConditional", "ACTIVITY_CONDITIONALITY_MISSING"),
            ("procedureType", "ACTIVITY_PROCEDURE_TYPE_MISSING"),
            ("procedureCode", "ACTIVITY_PROCEDURE_CODE_MISSING"),
        ):
            if extension.get(field) is None:
                blockers.append(
                    StudyAuthorityBlocker(
                        code=code,
                        path=f"usdmExtensions.activitySemantics[{uid}].{field}",
                        detail="Activity procedure semantics may not be hardcoded or silently defaulted.",
                    )
                )

    void_code_count = _usdm_void_code_count(usdm)
    if void_code_count:
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_VOID_CODE_PRESENT",
                path="usdm",
                detail=f"USDM contains {void_code_count} empty Code projection(s); each mandatory coded value must be resolved or explicitly excluded.",
            )
        )
    if "UNPINNED" in json.dumps(usdm, sort_keys=True, default=str):
        blockers.append(
            StudyAuthorityBlocker(
                code="USDM_CT_PACKAGE_UNPINNED",
                path="usdm",
                detail="USDM terminology was resolved without a study-pinned CT package.",
            )
        )

    if structure_statistics is not None:
        if study_value_version and integrity.get("version_consistent") is False:
            blockers.append(
                StudyAuthorityBlocker(
                    code="OSB_HISTORICAL_INTEGRITY_UNAVAILABLE",
                    path="integrity",
                    detail="Native selections are version-scoped, but the full OSB integrity suite is not yet version-aware for the requested historical study version.",
                )
            )
        native_to_usdm = (
            ("arm_count", "arms", "USDM_ARM_COVERAGE_GAP"),
            ("epoch_count", "epochs", "USDM_EPOCH_COVERAGE_GAP"),
            ("element_count", "elements", "USDM_ELEMENT_COVERAGE_GAP"),
            (
                "design_cell_count",
                "design_cells",
                "USDM_DESIGN_CELL_COVERAGE_GAP",
            ),
            ("visit_count", "encounters", "USDM_ENCOUNTER_COVERAGE_GAP"),
            ("study_activity_count", "activities", "USDM_ACTIVITY_COVERAGE_GAP"),
            (
                "study_activity_schedule_count",
                "scheduled_activity_links",
                "USDM_ACTIVITY_SCHEDULE_COVERAGE_GAP",
            ),
        )
        for native_key, usdm_key, code in native_to_usdm:
            native_count = structure_statistics.get(native_key)
            if native_count is not None and native_count != usdm_counts[usdm_key]:
                blockers.append(
                    StudyAuthorityBlocker(
                        code=code,
                        path=f"structureStatistics.{native_key}",
                        detail=f"Native OSB has {native_count}; USDM projects {usdm_counts[usdm_key]}.",
                    )
                )

    for index, row in enumerate(reconciliation or []):
        if row.status == "matched":
            continue
        blockers.append(
            StudyAuthorityBlocker(
                code=row.blocker_code or "USDM_IDENTITY_RECONCILIATION_FAILED",
                path=f"reconciliation[{index}]",
                detail=(
                    f"{row.resource_class} native UID {row.native_uid or '<none>'} "
                    f"and USDM ID {row.usdm_id or '<none>'} reconcile as {row.status}."
                ),
            )
        )

    if not integrity or not isinstance(integrity.get("all_passed"), bool):
        blockers.append(
            StudyAuthorityBlocker(
                code="OSB_INTEGRITY_CHECK_UNAVAILABLE",
                path="integrity",
                detail="A release requires a completed native OSB integrity check.",
            )
        )
    elif integrity.get("all_passed") is False:
        blockers.append(
            StudyAuthorityBlocker(
                code="OSB_INTEGRITY_CHECK_FAILED",
                path="integrity.checks",
                detail="One or more native OSB study integrity checks failed.",
            )
        )

    if authority_mode == "legacy":
        blockers.append(
            StudyAuthorityBlocker(
                code="LEGACY_AUTHORITY_MODE",
                path="authorityMode",
                detail="Legacy mode is available for comparison only and cannot establish an OSB-authoritative release.",
            )
        )

    return blockers


class StudyAuthorityService:
    def get_snapshot(
        self,
        study_uid: str,
        study_value_version: str | None = None,
    ) -> StudyAuthoritySnapshot:
        study_service = StudyService()
        native_model = study_service.get_by_uid(
            uid=study_uid,
            include_sections=list(StudyComponentEnum),
            exclude_sections=None,
            at_specified_date_time=None,
            status=None,
            study_value_version=study_value_version,
        )
        native_study = _as_json(native_model)
        usdm = _as_json(
            USDMService().get_by_uid(study_uid, study_value_version=study_value_version)
        )
        standards = _as_json(
            StudyStandardVersionService().get_standard_versions_in_study(
                study_uid=study_uid, study_value_version=study_value_version
            )
        )
        objectives = _as_json(
            StudyObjectiveSelectionService()
            .get_all_selection(
                study_uid=study_uid,
                no_brackets=True,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )
        endpoints = _as_json(
            StudyEndpointSelectionService()
            .get_all_selection(
                study_uid=study_uid,
                no_brackets=True,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )
        criteria = _as_json(
            StudyCriteriaSelectionService()
            .get_all_selection(
                study_uid=study_uid,
                no_brackets=True,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )
        arms = _as_json(
            StudyArmSelectionService()
            .get_all_selection(
                study_uid=study_uid,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )
        epochs = _as_json(
            StudyEpochService.get_all_epochs(
                study_uid=study_uid,
                page_size=0,
                study_value_version=study_value_version,
            ).items
        )
        elements = _as_json(
            StudyElementSelectionService()
            .get_all_selection(
                study_uid=study_uid,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )
        design_cells = _as_json(
            StudyDesignCellService().get_all_design_cells(
                study_uid=study_uid,
                study_value_version=study_value_version,
            )
        )
        visits = _as_json(
            StudyVisitService.get_all_visits(
                study_uid=study_uid,
                page_size=0,
                study_value_version=study_value_version,
            ).items
        )
        activities = _as_json(
            StudyActivitySelectionService()
            .get_all_selection(
                study_uid=study_uid,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )

        activity_instances = _as_json(
            StudyActivityInstanceSelectionService()
            .get_all_selection(
                study_uid=study_uid,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )
        schedules = _as_json(
            StudyActivityScheduleService().get_all_schedules(
                study_uid=study_uid,
                study_value_version=study_value_version,
            )
        )
        compounds = _as_json(
            StudyCompoundSelectionService()
            .get_all_selection(
                study_uid=study_uid,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )
        compound_dosings = _as_json(
            StudyCompoundDosingSelectionService()
            .get_all_compound_dosings(
                study_uid=study_uid,
                page_size=0,
                study_value_version=study_value_version,
            )
            .items
        )
        odm_metadata = _as_json(_get_study_odm_metadata(study_uid, study_value_version))

        usdm_extensions = _build_usdm_extensions(
            endpoints,
            criteria,
            design_cells=design_cells,
            activities=activities,
        )
        reconciliation = _build_reconciliation(
            usdm=usdm,
            objectives=objectives,
            endpoints=endpoints,
            criteria=criteria,
            arms=arms,
            epochs=epochs,
            elements=elements,
            design_cells=design_cells,
            visits=visits,
            activities=activities,
            schedules=schedules,
            usdm_extensions=usdm_extensions,
        )
        current_version_model = StudyService().get_by_uid(
            uid=study_uid,
            include_sections=[StudyComponentEnum.VERSION_METADATA],
        )
        current_version = str(
            current_version_model.current_metadata.version_metadata.version_number
        )
        version_consistent = (
            study_value_version is None or study_value_version == current_version
        )
        versioned_structure_counts = {
            "arm_count": len(arms),
            "epoch_count": len(epochs),
            "element_count": len(elements),
            "design_cell_count": len(design_cells),
            "visit_count": len(visits),
            "study_activity_count": len(activities),
            "study_activity_instance_count": len(activity_instances),
            "study_activity_schedule_count": len(schedules),
            "version_consistent": True,
        }
        if version_consistent:
            structure_statistics = _as_json(
                study_service.get_study_structure_statistics(study_uid)
            )
            structure_statistics.update(versioned_structure_counts)
            integrity = _as_json(
                study_service.run_integrity_check_for_study(study_uid=study_uid)
            )
            integrity["version_consistent"] = True
        else:
            structure_statistics = versioned_structure_counts
            integrity = {
                "all_passed": None,
                "version_consistent": False,
                "error": "Historical integrity checks are not version-aware.",
            }
        authority_mode: AuthorityMode = settings.mapping_authority_mode
        blockers = _mapping_blockers(
            authority_mode=authority_mode,
            native_study=native_study,
            usdm=usdm,
            standards=standards,
            objectives=objectives,
            endpoints=endpoints,
            criteria=criteria,
            integrity=integrity,
            visits=visits,
            activities=activities,
            design_cells=design_cells,
            compounds=compounds,
            compound_dosings=compound_dosings,
            usdm_extensions=usdm_extensions,
            reconciliation=reconciliation,
            structure_statistics=structure_statistics,
            study_value_version=study_value_version,
        )
        usdm_counts = _usdm_counts(usdm)
        counts = StudyAuthorityCounts(
            standard_versions=len(standards),
            objectives=len(objectives),
            endpoints=len(endpoints),
            criteria=len(criteria),
            arms=len(arms),
            epochs=len(epochs),
            elements=len(elements),
            design_cells=len(design_cells),
            visits=len(visits),
            activities=len(activities),
            activity_instances=len(activity_instances),
            activity_schedules=len(schedules),
            compounds=len(compounds),
            compound_dosings=len(compound_dosings),
            activity_items=len(odm_metadata.get("activityItems") or []),
            odm_items=len(odm_metadata.get("items") or []),
            odm_item_groups=len(odm_metadata.get("itemGroups") or []),
            odm_forms=len(odm_metadata.get("forms") or []),
            odm_study_events=len(odm_metadata.get("studyEvents") or []),
            usdm_objectives=usdm_counts["objectives"],
            usdm_endpoints=usdm_counts["endpoints"],
            usdm_arms=usdm_counts["arms"],
            usdm_epochs=usdm_counts["epochs"],
            usdm_elements=usdm_counts["elements"],
            usdm_design_cells=usdm_counts["design_cells"],
            usdm_encounters=usdm_counts["encounters"],
            usdm_activities=usdm_counts["activities"],
            usdm_interventions=usdm_counts["interventions"],
            usdm_administrations=usdm_counts["administrations"],
            usdm_eligibility_criteria=usdm_counts["eligibility_criteria"],
            usdm_eligibility_criterion_items=usdm_counts[
                "eligibility_criterion_items"
            ],
            usdm_population_criterion_links=usdm_counts[
                "population_criterion_links"
            ],
            usdm_scheduled_activity_links=usdm_counts["scheduled_activity_links"],
            usdm_void_codes=_usdm_void_code_count(usdm),
        )
        release_eligible = len(blockers) == 0
        content = {
            "schema_version": "osb-authority/1.2",
            "mapping_authority": "OpenStudyBuilder",
            "study_definition_standard": "CDISC USDM 4",
            "crf_metadata_standard": "CDISC ODM 1.3.2",
            "authority_mode": authority_mode,
            "study_uid": study_uid,
            "study_version": study_value_version,
            "release_eligible": release_eligible,
            "blockers": _as_json(blockers),
            "counts": _as_json(counts),
            "native_study": native_study,
            "usdm": usdm,
            "study_standard_versions": standards,
            "study_objectives": objectives,
            "study_endpoints": endpoints,
            "study_criteria": criteria,
            "study_arms": arms,
            "study_epochs": epochs,
            "study_elements": elements,
            "study_design_cells": design_cells,
            "study_visits": visits,
            "study_activities": activities,
            "study_activity_instances": activity_instances,
            "study_activity_schedules": schedules,
            "study_compounds": compounds,
            "study_compound_dosings": compound_dosings,
            "study_odm_metadata": odm_metadata,
            "usdm_extensions": usdm_extensions,
            "reconciliation": _as_json(reconciliation),
            "structure_statistics": structure_statistics,
            "integrity": integrity,
        }
        return StudyAuthoritySnapshot(
            **content,
            content_hash=_canonical_hash(content),
        )
