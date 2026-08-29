"""Typed Proposal V2 operations against existing StudyBuilder API surfaces.

This module only plans families whose complete native DTO inputs are present.
It never emits arbitrary proposal fields as an API body. Executing these routes
updates the same study selections consumed by the existing StudyBuilder UI.
"""

from __future__ import annotations

import hashlib

from .proposal_v2_capabilities import target_capability


class NativeOperationPlanError(ValueError):
    pass


def _object_index(proposal):
    return {
        item["proposalObjectId"]: item
        for rows in (proposal.get("sections") or {}).values()
        for item in rows
    }


def _selected_candidates(review):
    selected = {}
    for item in review.get("objects") or []:
        decision = item.get("latest_decision") or {}
        if decision.get("action") != "selected_candidate":
            continue
        candidate_key = decision.get("candidate_key")
        candidate = next(
            (
                value
                for value in item.get("candidates") or []
                if value.get("candidateKey") == candidate_key
            ),
            None,
        )
        if candidate is None:
            raise NativeOperationPlanError(
                f"OSB_NATIVE_V2_REVIEW_CANDIDATE_MISSING:{item.get('proposal_object_id')}"
            )
        selected[item["proposal_object_id"]] = candidate
    return selected


def _review_decisions(review):
    return {
        item["proposal_object_id"]: item.get("latest_decision") or {}
        for item in review.get("objects") or []
    }


def _source_values(item):
    result = {}
    for value in (item.get("source") or {}).get("values") or []:
        name = value.get("name")
        if not name:
            continue
        if name in result:
            raise NativeOperationPlanError(
                f"OSB_NATIVE_V2_SOURCE_NAME_DUPLICATE:{item.get('proposalObjectId')}:{name}"
            )
        result[name] = value.get("value")
    return result


def _nested_value(path, value):
    result = value
    for part in reversed(path.split(".")):
        result = {part: result}
    return result


_METADATA_PATHS = {
    "high_level_study_design.trial_phase_code",
    "high_level_study_design.study_type_code",
    # CSL ruleset 1.8.0 (M4): stopping rules route to the dedicated
    # free-text field; the string fallback below carries it verbatim.
    "high_level_study_design.study_stop_rules",
    # OsbCollectionContractV1: the full writable-protocol metadata surface.
    # CT-coded slots ride the _code branch, durations the duration branch,
    # booleans/strings their typed branches, and *_codes multiselects the
    # multi-term branch below.
    "high_level_study_design.development_stage_code",
    "high_level_study_design.is_extension_trial",
    "high_level_study_design.is_adaptive_design",
    "high_level_study_design.post_auth_indicator",
    "high_level_study_design.confirmed_response_minimum_duration",
    "high_level_study_design.trial_type_codes",
    "study_intervention.intervention_type_code",
    "study_intervention.add_on_to_existing_treatments",
    "study_intervention.trial_intent_types_codes",
    "study_population.therapeutic_area_codes",
    "study_population.disease_condition_or_indication_codes",
    "study_population.diagnosis_group_codes",
    "study_population.rare_disease_indicator",
    "study_population.stable_disease_minimum_duration",
    "study_population.pediatric_study_indicator",
    "study_population.pediatric_postmarket_study_indicator",
    "study_population.pediatric_investigation_plan_indicator",
    "study_population.relapse_criteria",
    "study_intervention.intervention_model_code",
    "study_intervention.trial_blinding_schema_code",
    "study_intervention.control_type_code",
    "study_population.sex_of_participants_code",
    "study_intervention.is_trial_randomised",
    "study_population.healthy_subject_indicator",
    "study_population.number_of_expected_subjects",
    "study_intervention.stratification_factor",
    "study_description.study_title",
    "study_description.study_short_title",
    "identification_metadata.study_id",
    # CSL 1.8.0 (M5/C4): all 13 registry-identifier slots accept the id the
    # protocol stated — the CSL planner classifies the slot, the value rides
    # verbatim through the plain-string branch.
    "identification_metadata.registry_identifiers.ct_gov_id",
    "identification_metadata.registry_identifiers.eudract_id",
    "identification_metadata.registry_identifiers.universal_trial_number_utn",
    "identification_metadata.registry_identifiers.japanese_trial_registry_id_japic",
    "identification_metadata.registry_identifiers.investigational_new_drug_application_number_ind",
    "identification_metadata.registry_identifiers.eu_trial_number",
    "identification_metadata.registry_identifiers.civ_id_sin_number",
    "identification_metadata.registry_identifiers.national_clinical_trial_number",
    "identification_metadata.registry_identifiers.japanese_trial_registry_number_jrct",
    "identification_metadata.registry_identifiers.national_medical_products_administration_nmpa_number",
    "identification_metadata.registry_identifiers.eudamed_srn_number",
    "identification_metadata.registry_identifiers.investigational_device_exemption_ide_number",
    "identification_metadata.registry_identifiers.eu_pas_number",
    "study_population.planned_minimum_age_of_subjects",
    "study_population.planned_maximum_age_of_subjects",
    "study_intervention.planned_study_length",
}


def _metadata_operation_value(item, dependencies):
    target_key = item.get("targetKey") or ""
    prefix = "study-metadata:"
    if not target_key.startswith(prefix):
        return None, None, "OSB_NATIVE_V2_METADATA_TARGET_INVALID"
    path = target_key[len(prefix) :]
    if path not in _METADATA_PATHS:
        return None, None, "OSB_NATIVE_V2_METADATA_PATH_UNSUPPORTED"

    values = _source_values(item)
    selected_dependencies = [value for value in dependencies.values() if value]
    ct_term = next(
        (
            value
            for value in selected_dependencies
            if value.get("resourceType") == "CTTerm"
        ),
        None,
    )
    unit = next(
        (
            value
            for value in selected_dependencies
            if value.get("resourceType") == "UnitDefinition"
        ),
        None,
    )

    if path.endswith("_code"):
        if not ct_term or not ct_term.get("uid"):
            return None, None, "OSB_NATIVE_V2_METADATA_CT_TERM_REQUIRED"
        value = {"term_uid": ct_term["uid"]}
        match = {f"current_metadata.{path}.term_uid": ct_term["uid"]}
        return value, match, None

    if path.endswith("_codes"):
        # OSB's multiselect study fields take a LIST of term references; every
        # resolved CT or dictionary term dependency contributes one element,
        # ordered by uid so the patch is deterministic.
        term_uids = sorted(
            {
                value.get("uid")
                for value in selected_dependencies
                if value.get("resourceType") in ("CTTerm", "DictionaryTerm") and value.get("uid")
            }
        )
        if not term_uids:
            return None, None, "OSB_NATIVE_V2_METADATA_CT_TERM_REQUIRED"
        value = [{"term_uid": uid} for uid in term_uids]
        match = {f"current_metadata.{path}": [{"term_uid": uid} for uid in term_uids]}
        return value, match, None

    if path in {
        "study_population.planned_minimum_age_of_subjects",
        "study_population.planned_maximum_age_of_subjects",
        "study_intervention.planned_study_length",
        "study_population.stable_disease_minimum_duration",
        "high_level_study_design.confirmed_response_minimum_duration",
    }:
        numeric = values.get("numericValue")
        if (
            isinstance(numeric, bool)
            or not isinstance(numeric, (int, float))
            or int(numeric) != numeric
            or numeric < 0
            or not unit
            or not unit.get("uid")
        ):
            return None, None, "OSB_NATIVE_V2_METADATA_DURATION_INCOMPLETE"
        value = {
            "duration_value": int(numeric),
            "duration_unit_code": {"uid": unit["uid"]},
        }
        match = {
            f"current_metadata.{path}.duration_value": int(numeric),
            f"current_metadata.{path}.duration_unit_code.uid": unit["uid"],
        }
        return value, match, None

    if path in {
        "study_intervention.is_trial_randomised",
        "study_population.healthy_subject_indicator",
        "high_level_study_design.is_extension_trial",
        "high_level_study_design.is_adaptive_design",
        "high_level_study_design.post_auth_indicator",
        "study_intervention.add_on_to_existing_treatments",
        "study_population.rare_disease_indicator",
        "study_population.pediatric_study_indicator",
        "study_population.pediatric_postmarket_study_indicator",
        "study_population.pediatric_investigation_plan_indicator",
    }:
        value = values.get("booleanValue")
        if not isinstance(value, bool):
            return None, None, "OSB_NATIVE_V2_METADATA_BOOLEAN_REQUIRED"
    elif path == "study_population.number_of_expected_subjects":
        value = values.get("numericValue")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or int(value) != value
            or value < 0
        ):
            return None, None, "OSB_NATIVE_V2_METADATA_INTEGER_REQUIRED"
        value = int(value)
    else:
        value = values.get("value")
        if not isinstance(value, str) or not value.strip():
            return None, None, "OSB_NATIVE_V2_METADATA_STRING_REQUIRED"
        value = value.strip()
    return value, {f"current_metadata.{path}": value}, None


def _integer(value, minimum=None):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or int(value) != value
        or (minimum is not None and value < minimum)
    ):
        return None
    return int(value)


def _ct_dependency(dependencies, target_key, parent_submission_value):
    candidate = dependencies.get(target_key)
    if (
        not candidate
        or candidate.get("resourceType") != "CTTerm"
        or candidate.get("parentSubmissionValue") != parent_submission_value
        or not candidate.get("uid")
    ):
        return None
    return candidate


def _string(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _source_description(values):
    """Conserve both legacy `detail` and typed `description` without invention."""
    description = _string(values.get("description"))
    detail = _string(values.get("detail"))
    if description and detail and description != detail:
        return f"{description}\n\n{detail}"
    return description or detail


def _unit_dependency(dependencies, target_key):
    candidate = dependencies.get(target_key)
    if (
        not candidate
        or candidate.get("resourceType") != "UnitDefinition"
        or not candidate.get("uid")
    ):
        return None
    return candidate


def _arm_operation_values(item, dependencies):
    values = _source_values(item)
    arm_id = _string(values.get("armId"))
    name = _string(values.get("name"))
    if not arm_id or not name:
        return None, None, None, "OSB_NATIVE_V2_ARM_DTO_INCOMPLETE"

    number_source = values.get("numberOfSubjects")
    number_of_subjects = (
        _integer(number_source, minimum=0) if number_source is not None else None
    )
    if number_source is not None and number_of_subjects is None:
        return None, None, None, "OSB_NATIVE_V2_ARM_SUBJECT_COUNT_INVALID"

    body = {
        "name": name,
        "merge_branch_for_this_arm_for_sdtm_adam": False,
    }
    reconcile = {
        "name": name,
        "merge_branch_for_this_arm_for_sdtm_adam": False,
    }
    for source_key, body_key in (
        ("shortName", "short_name"),
        ("label", "label"),
        ("code", "code"),
        ("randomizationGroup", "randomization_group"),
    ):
        value = _string(values.get(source_key))
        if value:
            body[body_key] = value
            reconcile[body_key] = value
    description = _source_description(values)
    if description:
        body["description"] = description
        reconcile["description"] = description
    if number_of_subjects is not None:
        body["number_of_subjects"] = number_of_subjects
        reconcile["number_of_subjects"] = number_of_subjects

    if "arm-type" in dependencies:
        arm_type = _ct_dependency(dependencies, "arm-type", "Arm Type")
        if arm_type is None:
            return None, None, None, "OSB_NATIVE_V2_ARM_TYPE_INVALID"
        body["arm_type_uid"] = arm_type["uid"]
        reconcile["arm_type.term_uid"] = arm_type["uid"]
    return body, reconcile, {"armId": arm_id}, None


def _element_operation_values(item, dependencies):
    values = _source_values(item)
    element_id = _string(values.get("elementId"))
    name = _string(values.get("name"))
    if not element_id or not name:
        return None, None, None, "OSB_NATIVE_V2_ELEMENT_DTO_INCOMPLETE"

    body = {"name": name}
    reconcile = {"name": name}
    for source_key, body_key in (
        ("shortName", "short_name"),
        ("code", "code"),
        ("startRule", "start_rule"),
        ("endRule", "end_rule"),
    ):
        value = _string(values.get(source_key))
        if value:
            body[body_key] = value
            reconcile[body_key] = value
    description = _source_description(values)
    if description:
        body["description"] = description
        reconcile["description"] = description

    duration_source = values.get("plannedDurationValue")
    duration = (
        _integer(duration_source, minimum=0) if duration_source is not None else None
    )
    if duration_source is not None:
        unit = _unit_dependency(dependencies, "element-duration-unit")
        if duration is None or unit is None:
            return None, None, None, "OSB_NATIVE_V2_ELEMENT_DURATION_INCOMPLETE"
        body["planned_duration"] = {
            "duration_value": duration,
            "duration_unit_code": {"uid": unit["uid"]},
        }
        reconcile.update(
            {
                "planned_duration.duration_value": duration,
                "planned_duration.duration_unit_code.uid": unit["uid"],
            }
        )

    if "element-subtype" in dependencies:
        subtype = _ct_dependency(dependencies, "element-subtype", "Element Sub Type")
        if subtype is None:
            return None, None, None, "OSB_NATIVE_V2_ELEMENT_SUBTYPE_INVALID"
        body["element_subtype_uid"] = subtype["uid"]
        reconcile["element_subtype.term_uid"] = subtype["uid"]
    return (
        body,
        reconcile,
        {"elementId": element_id},
        None,
    )




def _design_cell_operation_values(item):
    values = _source_values(item)
    design_cell_id = _string(values.get("designCellId"))
    arm_id = _string(values.get("armId"))
    epoch_id = _string(values.get("epochId"))
    element_id = _string(values.get("elementId"))
    if not all((design_cell_id, arm_id, epoch_id, element_id)):
        return None, None, None, None, "OSB_NATIVE_V2_DESIGN_CELL_DTO_INCOMPLETE"

    body = {}
    reconcile = {}
    transition_rule = _string(values.get("transitionRule"))
    if transition_rule:
        body["transition_rule"] = transition_rule
        reconcile["transition_rule"] = transition_rule
    order_source = values.get("order")
    if order_source is not None:
        order = _integer(order_source, minimum=1)
        if order is None:
            return None, None, None, None, "OSB_NATIVE_V2_DESIGN_CELL_ORDER_INVALID"
        body["order"] = order
        reconcile["order"] = order

    references = [
        {
            "family": "StudySelectionArm",
            "identity_name": "armId",
            "identity_value": arm_id,
            "body_path": "study_arm_uid",
            "read_match_path": "study_arm_uid",
        },
        {
            "family": "StudyEpoch",
            "identity_name": "epochId",
            "identity_value": epoch_id,
            "body_path": "study_epoch_uid",
            "read_match_path": "study_epoch_uid",
        },
        {
            "family": "StudySelectionElement",
            "identity_name": "elementId",
            "identity_value": element_id,
            "body_path": "study_element_uid",
            "read_match_path": "study_element_uid",
        },
    ]
    return body, reconcile, {"designCellId": design_cell_id}, references, None


def _epoch_operation_values(item, dependencies, target_study_uid):
    values = _source_values(item)
    epoch_id = values.get("epochId")
    name = values.get("name")
    order_source = values.get("order")
    order = _integer(order_source, minimum=1) if order_source is not None else None
    subtype = _ct_dependency(dependencies, "epoch-subtype", "Epoch Sub Type")
    if (
        not isinstance(epoch_id, str)
        or not epoch_id.strip()
        or not isinstance(name, str)
        or not name.strip()
        or (order_source is not None and order is None)
        or subtype is None
    ):
        return None, None, None, "OSB_NATIVE_V2_EPOCH_DTO_INCOMPLETE"
    body = {
        "study_uid": target_study_uid,
        "epoch_subtype": subtype["uid"],
        # OSB derives its display epoch CT term from the governed subtype. Keep
        # the exact protocol band label in description so Allocation and
        # Post-allocation are not falsely renamed to the same source value.
        "description": name.strip(),
    }
    reconcile = {
        "epoch_subtype_ctterm.term_uid": subtype["uid"],
        "description": name.strip(),
    }
    if order is not None:
        body["order"] = order
        reconcile["order"] = order
    for source_key, body_key in (("startRule", "start_rule"), ("endRule", "end_rule")):
        value = _string(values.get(source_key))
        if value:
            body[body_key] = value
            reconcile[body_key] = value
    duration_source = values.get("durationValue")
    duration_unit = _string(values.get("durationUnit"))
    if duration_source is not None or duration_unit is not None:
        duration = _integer(duration_source, minimum=0)
        if duration is None or duration_unit is None:
            return None, None, None, "OSB_NATIVE_V2_EPOCH_DURATION_INCOMPLETE"
        body["duration"] = duration
        body["duration_unit"] = duration_unit
        reconcile["duration"] = duration
        reconcile["duration_unit"] = duration_unit
    return body, reconcile, {"epochId": epoch_id.strip()}, None


def _visit_operation_values(item, dependencies):
    values = _source_values(item)
    visit_id = values.get("visitId")
    epoch_id = values.get("epochId")
    name = values.get("name")
    visit_class = values.get("visitClass")
    show_visit = values.get("showVisit")
    is_global_anchor = values.get("isGlobalAnchorVisit")
    sequence = _integer(values.get("sequenceOrder"), minimum=1)
    visit_type = _ct_dependency(dependencies, "visit-type", "VisitType")
    contact_mode = _ct_dependency(
        dependencies, "visit-contact-mode", "Visit Contact Mode"
    )
    if (
        values.get("nativeTimingReady") is not True
        or not isinstance(visit_id, str)
        or not visit_id.strip()
        or not isinstance(epoch_id, str)
        or not epoch_id.strip()
        or not isinstance(name, str)
        or not name.strip()
        or visit_class not in {"MANUALLY_DEFINED_VISIT", "UNSCHEDULED_VISIT"}
        or not isinstance(show_visit, bool)
        or not isinstance(is_global_anchor, bool)
        or sequence is None
        or visit_type is None
        or contact_mode is None
    ):
        return None, None, None, None, "OSB_NATIVE_V2_VISIT_DTO_INCOMPLETE"

    body = {
        # study_epoch_uid is resolved from the signed epoch create receipt.
        "visit_type": {"term_uid": visit_type["uid"]},
        "show_visit": show_visit,
        "description": name.strip(),
        "visit_contact_mode": {"term_uid": contact_mode["uid"]},
        "visit_class": visit_class,
        "is_global_anchor_visit": is_global_anchor,
        "is_soa_milestone": False,
    }
    reconcile = {
        "visit_type.term_uid": visit_type["uid"],
        "show_visit": show_visit,
        "description": name.strip(),
        "visit_contact_mode.term_uid": contact_mode["uid"],
        "visit_class": visit_class,
        "is_global_anchor_visit": is_global_anchor,
    }

    if visit_class == "MANUALLY_DEFINED_VISIT":
        visit_name = values.get("visitName")
        short_name = values.get("visitShortName")
        visit_number = _integer(values.get("visitNumber"), minimum=1)
        unique_number = _integer(values.get("uniqueVisitNumber"), minimum=1)
        time_value = _integer(values.get("timeValue"))
        time_reference = _ct_dependency(
            dependencies, "visit-time-reference", "Time Point Reference"
        )
        time_unit = dependencies.get("visit-time-unit")
        if (
            not isinstance(visit_name, str)
            or not visit_name.strip()
            or not isinstance(short_name, str)
            or not short_name.strip()
            or visit_number is None
            or unique_number is None
            or time_value is None
            or time_reference is None
            or not time_unit
            or time_unit.get("resourceType") != "UnitDefinition"
            or not time_unit.get("uid")
        ):
            return None, None, None, None, "OSB_NATIVE_V2_VISIT_TIMING_INCOMPLETE"
        body.update(
            {
                "time_reference": {"term_uid": time_reference["uid"]},
                "time_value": time_value,
                "time_unit_uid": time_unit["uid"],
                "visit_name": visit_name.strip(),
                "visit_short_name": short_name.strip(),
                "visit_number": visit_number,
                "unique_visit_number": unique_number,
            }
        )
        reconcile.update(
            {
                "time_reference.term_uid": time_reference["uid"],
                "time_value": time_value,
                "time_unit_uid": time_unit["uid"],
                "visit_name": visit_name.strip(),
                "visit_short_name": short_name.strip(),
                "visit_number": visit_number,
                "unique_visit_number": unique_number,
            }
        )
    elif is_global_anchor:
        return None, None, None, None, "OSB_NATIVE_V2_UNSCHEDULED_ANCHOR_INVALID"

    min_window = values.get("minVisitWindowValue")
    max_window = values.get("maxVisitWindowValue")
    if min_window is not None or max_window is not None:
        min_window = _integer(min_window)
        max_window = _integer(max_window)
        window_unit = dependencies.get("visit-window-unit")
        if (
            min_window is None
            or max_window is None
            or not window_unit
            or window_unit.get("resourceType") != "UnitDefinition"
            or not window_unit.get("uid")
        ):
            return None, None, None, None, "OSB_NATIVE_V2_VISIT_WINDOW_INCOMPLETE"
        body.update(
            {
                "min_visit_window_value": min_window,
                "max_visit_window_value": max_window,
                "visit_window_unit_uid": window_unit["uid"],
            }
        )
        reconcile.update(
            {
                "min_visit_window_value": min_window,
                "max_visit_window_value": max_window,
                "visit_window_unit_uid": window_unit["uid"],
            }
        )

    reference = {
        "family": "StudyEpoch",
        "identity_name": "epochId",
        "identity_value": epoch_id.strip(),
        "body_path": "study_epoch_uid",
        "read_match_path": "study_epoch_uid",
    }
    return (
        body,
        reconcile,
        {"visitId": visit_id.strip()},
        [reference],
        None,
    )


def _activity_schedule_operation_values(item):
    values = _source_values(item)
    schedule_id = _string(values.get("scheduleId"))
    activity_id = _string(values.get("activityId"))
    visit_id = _string(values.get("visitId"))
    if not all((schedule_id, activity_id, visit_id)):
        return (
            None,
            None,
            None,
            None,
            "OSB_NATIVE_V2_ACTIVITY_SCHEDULE_DTO_INCOMPLETE",
        )
    references = [
        {
            "family": "StudySelectionActivity",
            "identity_name": "activityId",
            "identity_value": activity_id,
            "body_path": "study_activity_uid",
            "read_match_path": "study_activity_uid",
        },
        {
            "family": "StudyVisit",
            "identity_name": "visitId",
            "identity_value": visit_id,
            "body_path": "study_visit_uid",
            "read_match_path": "study_visit_uid",
        },
    ]
    return {}, {}, {"scheduleId": schedule_id}, references, None


def _idempotency_key(
    proposal_hash, object_id, operation, target_study_uid, target_study_version
):
    return hashlib.sha256(
        (
            f"{proposal_hash}\0{target_study_uid}\0{target_study_version}"
            f"\0{object_id}\0{operation}"
        ).encode("utf-8")
    ).hexdigest()


def _operation(
    proposal_hash,
    object_id,
    family,
    path,
    body,
    params,
    reconcile,
    target_study_uid,
    target_study_version,
    method="POST",
    read_collection=True,
    source_identity=None,
    body_references=None,
    record_hash_scope="record",
):
    operation = {
        "proposal_object_id": object_id,
        "family": family,
        "idempotency_key": _idempotency_key(
            proposal_hash,
            object_id,
            family,
            target_study_uid,
            target_study_version,
        ),
        "method": method,
        "path": path,
        "params": params,
        "body": body,
        "preconditions": {
            "study_uid": target_study_uid,
            "study_value_version": target_study_version,
            "study_status": "DRAFT",
        },
        "read_after_write": {
            "method": "GET",
            "path": path,
            "params": {"page_size": 0},
            "match": reconcile,
            **({"collection": False} if not read_collection else {}),
        },
    }
    # Selection receipts bind the whole native record by default. StudyMetadata
    # PATCHes share one /studies/{uid} record, so each receipt binds only its
    # governed match projection; a later metadata field must not invalidate it.
    if record_hash_scope != "record":
        operation["record_hash_scope"] = record_hash_scope
    if source_identity:
        operation["source_identity"] = source_identity
    if body_references:
        operation["body_references"] = body_references
    return operation


def native_operation_plan(
    proposal,
    review,
    target_study_uid,
    target_study_version,
    allow_stale_target_snapshot=False,
):
    """Build existing-API operations from an OSB-authoritative review receipt.

    A complete review is not enough: OSB must explicitly mark the receipt native
    execution ready after signature and study ownership/version verification.
    """
    if not target_study_uid or not str(target_study_uid).strip():
        raise NativeOperationPlanError("OSB_NATIVE_V2_TARGET_STUDY_REQUIRED")
    if not target_study_version or not str(target_study_version).strip():
        raise NativeOperationPlanError("OSB_NATIVE_V2_TARGET_STUDY_VERSION_REQUIRED")
    if str(target_study_version).strip() != "DRAFT":
        raise NativeOperationPlanError("OSB_NATIVE_V2_TARGET_STUDY_VERSION_INVALID")
    if review.get("proposal_hash") != proposal.get("proposalHash"):
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_PROPOSAL_MISMATCH")
    if not review.get("review_complete"):
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_INCOMPLETE")
    if review.get("rejected_object_count", 0):
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_REJECTED")
    authority_blockers = review.get("execution_blockers") or []
    effective_authority_blockers = [
        blocker
        for blocker in authority_blockers
        if not (
            allow_stale_target_snapshot and blocker == "OSB_STUDY_DRAFT_SNAPSHOT_STALE"
        )
    ]
    if effective_authority_blockers:
        blockers = ",".join(effective_authority_blockers)
        raise NativeOperationPlanError(
            f"OSB_NATIVE_V2_AUTHORITY_NOT_READY:{blockers or 'unspecified'}"
        )
    if not review.get("native_execution_ready"):
        stale_retry_only = (
            allow_stale_target_snapshot
            and bool(authority_blockers)
            and all(
                blocker == "OSB_STUDY_DRAFT_SNAPSHOT_STALE"
                for blocker in authority_blockers
            )
        )
        if not stale_retry_only:
            raise NativeOperationPlanError(
                "OSB_NATIVE_V2_AUTHORITY_NOT_READY:unspecified"
            )
    if review.get("target_study_uid") != target_study_uid:
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_TARGET_STUDY_MISMATCH")
    if str(review.get("target_study_version")) != str(target_study_version):
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_TARGET_VERSION_MISMATCH")
    if review.get("target_study_status") != "DRAFT":
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_TARGET_NOT_DRAFT")
    if not review.get("target_ownership_verified"):
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_TARGET_NOT_OWNED")
    authorization = review.get("execution_authorization") or {}
    if (
        authorization.get("proposal_hash") != proposal.get("proposalHash")
        or authorization.get("target_study_uid") != target_study_uid
        or str(authorization.get("target_study_version")) != str(target_study_version)
        or authorization.get("target_study_status") != "DRAFT"
        or authorization.get("decision_set_hash") != review.get("decision_set_hash")
        or not authorization.get("signature_verified")
        or authorization.get("actor_id") != review.get("target_study_owner_id")
        or not authorization.get("authorization_content_hash")
    ):
        raise NativeOperationPlanError(
            "OSB_NATIVE_V2_EXECUTION_AUTHORIZATION_BINDING_INVALID"
        )
    proposal_objects = _object_index(proposal)
    review_objects = {
        item["proposal_object_id"]: item for item in review.get("objects") or []
    }
    if set(proposal_objects) != set(review_objects):
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_OBJECT_SET_MISMATCH")
    unsigned = sorted(
        object_id
        for object_id, item in review_objects.items()
        if not (item.get("latest_decision") or {}).get("signature_verified")
    )
    if unsigned:
        raise NativeOperationPlanError(
            "OSB_NATIVE_V2_REVIEW_SIGNATURE_NOT_VERIFIED:" + ",".join(unsigned)
        )
    selected = _selected_candidates(review)
    decisions = _review_decisions(review)
    selected_by_fact_target = {}
    for object_id, item in proposal_objects.items():
        fact_id = (item.get("mapping") or {}).get("factIds", [None])[0]
        if fact_id is not None and object_id in selected:
            selected_by_fact_target[(fact_id, item.get("targetKey"))] = selected[
                object_id
            ]

    executable_resource_types = {
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
    }
    required_governed_dependencies = {
        (fact_id, dependency_target_key)
        for item in proposal_objects.values()
        if (item.get("mapping") or {}).get("proposedResourceType")
        in executable_resource_types
        for fact_id in ((item.get("mapping") or {}).get("factIds") or [])[:1]
        for dependency_target_key in item.get("dependencyTargetKeys") or []
    }
    operations = []
    blockers = []
    deferred_objects = []
    proposal_hash = proposal["proposalHash"]
    for object_id, item in proposal_objects.items():
        mapping = item.get("mapping") or {}
        resource_type = mapping.get("proposedResourceType")
        if resource_type not in executable_resource_types:
            capability = target_capability(resource_type)
            if capability and capability[0] == "governed_library_reference":
                fact_id = (mapping.get("factIds") or [None])[0]
                if (
                    fact_id,
                    item.get("targetKey"),
                ) in required_governed_dependencies:
                    continue
            deferred_objects.append(
                {
                    "proposal_object_id": object_id,
                    "resource_type": resource_type,
                    "capability_kind": capability[0] if capability else "unsupported",
                }
            )
            continue
        fact_id = (mapping.get("factIds") or [None])[0]
        candidate = selected.get(object_id)
        dependencies = {
            target_key: selected_by_fact_target.get((fact_id, target_key))
            for target_key in item.get("dependencyTargetKeys") or []
        }
        missing = sorted(
            target_key for target_key, value in dependencies.items() if value is None
        )
        selection_resource_types = {
            "StudySelectionObjective",
            "StudySelectionEndpoint",
            "StudySelectionCriteria",
            "StudySelectionActivity",
        }
        decision_action = decisions.get(object_id, {}).get("action")
        requires_candidate = resource_type in selection_resource_types
        allowed_actions = {
            "selected_candidate" if requires_candidate else "create_request"
        }
        if decision_action not in allowed_actions:
            blockers.append(
                {
                    "proposal_object_id": object_id,
                    "code": "OSB_NATIVE_V2_REVIEW_ACTION_INVALID",
                    "details": sorted(allowed_actions),
                }
            )
            continue
        if (requires_candidate and candidate is None) or missing:
            blockers.append(
                {
                    "proposal_object_id": object_id,
                    "code": "OSB_NATIVE_V2_SELECTED_DEPENDENCY_MISSING",
                    "details": missing or ["selected-candidate"],
                }
            )
            continue

        path = f"/studies/{target_study_uid}"
        if resource_type == "StudyMetadata":
            value, reconcile, error = _metadata_operation_value(item, dependencies)
            if error:
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": error,
                        "details": [],
                    }
                )
                continue
            metadata_path = item["targetKey"].split(":", 1)[1]
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudyMetadata",
                    path,
                    {"current_metadata": _nested_value(metadata_path, value)},
                    None,
                    reconcile,
                    target_study_uid,
                    target_study_version,
                    method="PATCH",
                    read_collection=False,
                    record_hash_scope="match",
                )
            )
        elif resource_type == "StudySelectionArm":
            body, reconcile, source_identity, error = _arm_operation_values(
                item, dependencies
            )
            if error:
                blockers.append(
                    {"proposal_object_id": object_id, "code": error, "details": []}
                )
                continue
            path += "/study-arms"
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudySelectionArm",
                    path,
                    body,
                    None,
                    reconcile,
                    target_study_uid,
                    target_study_version,
                    source_identity=source_identity,
                    record_hash_scope="match",
                )
            )
        elif resource_type == "StudySelectionElement":
            body, reconcile, source_identity, error = _element_operation_values(
                item, dependencies
            )
            if error:
                blockers.append(
                    {"proposal_object_id": object_id, "code": error, "details": []}
                )
                continue
            path += "/study-elements"
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudySelectionElement",
                    path,
                    body,
                    None,
                    reconcile,
                    target_study_uid,
                    target_study_version,
                    source_identity=source_identity,
                    record_hash_scope="match",
                )
            )
        elif resource_type == "StudyEpoch":
            body, reconcile, source_identity, error = _epoch_operation_values(
                item, dependencies, target_study_uid
            )
            if error:
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": error,
                        "details": [],
                    }
                )
                continue
            path += "/study-epochs"
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudyEpoch",
                    path,
                    body,
                    None,
                    reconcile,
                    target_study_uid,
                    target_study_version,
                    source_identity=source_identity,
                    record_hash_scope="match",
                )
            )
        elif resource_type == "StudyDesignCell":
            (
                body,
                reconcile,
                source_identity,
                body_references,
                error,
            ) = _design_cell_operation_values(item)
            if error:
                blockers.append(
                    {"proposal_object_id": object_id, "code": error, "details": []}
                )
                continue
            path += "/study-design-cells"
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudyDesignCell",
                    path,
                    body,
                    None,
                    reconcile,
                    target_study_uid,
                    target_study_version,
                    source_identity=source_identity,
                    body_references=body_references,
                    record_hash_scope="match",
                )
            )
        elif resource_type == "StudyVisit":
            (
                body,
                reconcile,
                source_identity,
                body_references,
                error,
            ) = _visit_operation_values(item, dependencies)
            if error:
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": error,
                        "details": [],
                    }
                )
                continue
            path += "/study-visits"
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudyVisit",
                    path,
                    body,
                    None,
                    reconcile,
                    target_study_uid,
                    target_study_version,
                    source_identity=source_identity,
                    body_references=body_references,
                    record_hash_scope="match",
                )
            )
        elif resource_type == "StudySelectionObjective":
            level = dependencies.get("objective-level")
            if (
                not level
                or level.get("resourceType") != "CTTerm"
                or level.get("parentSubmissionValue") != "Objective Level"
            ):
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_OBJECTIVE_DTO_INCOMPLETE",
                        "details": [],
                    }
                )
                continue
            path += "/study-objectives"
            objective_id = _source_values(item).get("objectiveId")
            if (
                candidate.get("resourceType") != "ObjectiveTemplate"
                or candidate.get("parameterCount") != 0
                or not candidate.get("uid")
                or not candidate.get("libraryName")
            ):
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_OBJECTIVE_DTO_INCOMPLETE",
                        "details": [],
                    }
                )
                continue
            objective_template_uid = candidate["uid"]
            objective_library_name = candidate["libraryName"]
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudySelectionObjective",
                    path,
                    {
                        "objective_level_uid": level["uid"],
                        "objective_data": {
                            "parameter_terms": [],
                            "objective_template_uid": objective_template_uid,
                            "library_name": objective_library_name,
                        },
                    },
                    {"create_objective": True},
                    {
                        "objective.template.uid": objective_template_uid,
                        "objective_level.term_uid": level["uid"],
                    },
                    target_study_uid,
                    target_study_version,
                    source_identity=(
                        {"objectiveId": str(objective_id).strip()}
                        if objective_id is not None and str(objective_id).strip()
                        else None
                    ),
                )
            )
        elif resource_type == "StudySelectionEndpoint":
            level = dependencies.get("endpoint-level")
            timeframe = dependencies.get("endpoint-timeframe")
            if (
                not level
                or level.get("resourceType") != "CTTerm"
                or level.get("parentSubmissionValue") != "Endpoint Level"
                or (
                    timeframe is not None
                    and timeframe.get("resourceType") != "Timeframe"
                )
            ):
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_ENDPOINT_DTO_INCOMPLETE",
                        "details": [],
                    }
                )
                continue
            path += "/study-endpoints"
            body_references = []
            if (
                candidate.get("resourceType") != "EndpointTemplate"
                or candidate.get("parameterCount") != 0
                or not candidate.get("uid")
                or not candidate.get("libraryName")
            ):
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_ENDPOINT_DTO_INCOMPLETE",
                        "details": [],
                    }
                )
                continue
            endpoint_template_uid = candidate["uid"]
            endpoint_library_name = candidate["libraryName"]
            body = {
                "endpoint_level_uid": level["uid"],
                "endpoint_data": {
                    "parameter_terms": [],
                    "endpoint_template_uid": endpoint_template_uid,
                    "library_name": endpoint_library_name,
                },
            }
            reconcile = {
                "endpoint.template.uid": endpoint_template_uid,
                "endpoint_level.term_uid": level["uid"],
            }
            if timeframe is not None:
                body["timeframe_uid"] = timeframe["uid"]
                reconcile["timeframe.uid"] = timeframe["uid"]
            objective_id = _source_values(item).get("objectiveId")
            if objective_id is None or not str(objective_id).strip():
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_ENDPOINT_OBJECTIVE_REQUIRED",
                        "details": [],
                    }
                )
                continue
            objective_reference = {
                "family": "StudySelectionObjective",
                "identity_name": "objectiveId",
                "identity_value": str(objective_id).strip(),
                "body_path": "study_objective_uid",
                "read_match_path": "study_objective.study_objective_uid",
            }
            body_references.append(objective_reference)
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudySelectionEndpoint",
                    path,
                    body,
                    {"create_endpoint": True},
                    reconcile,
                    target_study_uid,
                    target_study_version,
                    body_references=body_references,
                )
            )
        elif resource_type == "StudySelectionCriteria":
            criteria_type = dependencies.get("criteria-type")
            if (
                candidate.get("resourceType") != "CriteriaTemplate"
                or candidate.get("parameterCount") != 0
                or not candidate.get("libraryName")
                or not criteria_type
                or criteria_type.get("resourceType") != "CTTerm"
                or criteria_type.get("parentSubmissionValue") != "Criteria Type"
                or candidate.get("criteriaTypeUid") != criteria_type.get("uid")
            ):
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_CRITERIA_DTO_INCOMPLETE_OR_TYPE_CONFLICT",
                        "details": [],
                    }
                )
                continue
            path += "/study-criteria"
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudySelectionCriteria",
                    path,
                    {
                        "criteria_data": {
                            "parameter_terms": [],
                            "criteria_template_uid": candidate["uid"],
                            "library_name": candidate["libraryName"],
                        }
                    },
                    {"create_criteria": True},
                    {
                        "criteria.template.uid": candidate["uid"],
                        "criteria_type.term_uid": criteria_type["uid"],
                    },
                    target_study_uid,
                    target_study_version,
                )
            )
        elif resource_type == "StudySelectionActivity":
            flowcharts = [
                value
                for value in dependencies.values()
                if value
                and value.get("resourceType") == "CTTerm"
                and value.get("parentSubmissionValue") == "Flowchart Group"
                and value.get("uid")
            ]
            flowchart = flowcharts[0] if len(flowcharts) == 1 else None
            if candidate.get("resourceType") != "Activity" or not flowchart:
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_ACTIVITY_DTO_INCOMPLETE",
                        "details": [],
                    }
                )
                continue
            path += "/study-activities"
            activity_id = _string(_source_values(item).get("activityId"))
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudySelectionActivity",
                    path,
                    {
                        "activity_uid": candidate["uid"],
                        "soa_group_term_uid": flowchart["uid"],
                        "show_activity_in_protocol_flowchart": True,
                    },
                    None,
                    {
                        "activity.uid": candidate["uid"],
                        "study_soa_group.soa_group_term_uid": flowchart["uid"],
                    },
                    target_study_uid,
                    target_study_version,
                    source_identity=(
                        {"activityId": activity_id} if activity_id else None
                    ),
                )
            )
        else:
            (
                body,
                reconcile,
                source_identity,
                body_references,
                error,
            ) = _activity_schedule_operation_values(item)
            if error:
                blockers.append(
                    {"proposal_object_id": object_id, "code": error, "details": []}
                )
                continue
            path += "/study-activity-schedules"
            operations.append(
                _operation(
                    proposal_hash,
                    object_id,
                    "StudyActivitySchedule",
                    path,
                    body,
                    None,
                    reconcile,
                    target_study_uid,
                    target_study_version,
                    source_identity=source_identity,
                    body_references=body_references,
                    record_hash_scope="match",
                )
            )

    identity_operations = {}
    for operation in operations:
        for identity_name, identity_value in (
            operation.get("source_identity") or {}
        ).items():
            key = (operation["family"], identity_name, identity_value)
            identity_operations.setdefault(key, []).append(operation)
    for operation in operations:
        for reference in operation.get("body_references") or []:
            if reference.get("proposal_object_id"):
                continue
            matches = identity_operations.get(
                (
                    reference["family"],
                    reference["identity_name"],
                    reference["identity_value"],
                ),
                [],
            )
            if len(matches) != 1:
                blockers.append(
                    {
                        "proposal_object_id": operation["proposal_object_id"],
                        "code": (
                            "OSB_NATIVE_V2_REFERENCE_UNRESOLVED"
                            if not matches
                            else "OSB_NATIVE_V2_REFERENCE_AMBIGUOUS"
                        ),
                        "details": [
                            reference["family"],
                            reference["identity_name"],
                            reference["identity_value"],
                        ],
                    }
                )
                continue
            reference["proposal_object_id"] = matches[0]["proposal_object_id"]

    # A StudyMetadata PATCH rotates the StudyValue snapshot bound by the signed
    # authorization.  Run it last so all other writes remain protected by the
    # original optimistic-concurrency token; a persisted metadata receipt then
    # permits only a fully hash-verified retry/finalization path.
    family_rank = {
        "StudySelectionObjective": 0,
        "StudySelectionArm": 0,
        "StudySelectionElement": 0,
        "StudyEpoch": 0,
        "StudySelectionEndpoint": 1,
        "StudyDesignCell": 1,
        "StudySelectionActivity": 1,
        "StudyVisit": 2,
        "StudyActivitySchedule": 3,
        "StudyMetadata": 5,
    }
    operations.sort(key=lambda operation: family_rank.get(operation["family"], 3))
    return {
        "operations": operations,
        "blockers": blockers,
        "deferred_objects": deferred_objects,
    }
