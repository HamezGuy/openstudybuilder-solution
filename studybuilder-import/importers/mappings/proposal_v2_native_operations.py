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


def _idempotency_key(proposal_hash, object_id, operation):
    return hashlib.sha256(
        f"{proposal_hash}\0{object_id}\0{operation}".encode("utf-8")
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
):
    return {
        "proposal_object_id": object_id,
        "family": family,
        "idempotency_key": _idempotency_key(proposal_hash, object_id, family),
        "method": "POST",
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
        },
    }


def native_operation_plan(proposal, review, target_study_uid, target_study_version):
    """Build existing-API operations from an OSB-authoritative review receipt.

    A complete review is not enough: OSB must explicitly mark the receipt native
    execution ready after signature and study ownership/version verification.
    """
    if not target_study_uid or not str(target_study_uid).strip():
        raise NativeOperationPlanError("OSB_NATIVE_V2_TARGET_STUDY_REQUIRED")
    if not target_study_version or not str(target_study_version).strip():
        raise NativeOperationPlanError("OSB_NATIVE_V2_TARGET_STUDY_VERSION_REQUIRED")
    if review.get("proposal_hash") != proposal.get("proposalHash"):
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_PROPOSAL_MISMATCH")
    if not review.get("native_execution_ready"):
        blockers = ",".join(review.get("execution_blockers") or [])
        raise NativeOperationPlanError(
            f"OSB_NATIVE_V2_AUTHORITY_NOT_READY:{blockers or 'unspecified'}"
        )

    proposal_objects = _object_index(proposal)
    review_objects = {
        item["proposal_object_id"]: item for item in review.get("objects") or []
    }
    if set(proposal_objects) != set(review_objects):
        raise NativeOperationPlanError("OSB_NATIVE_V2_REVIEW_OBJECT_SET_MISMATCH")
    selected = _selected_candidates(review)
    selected_by_fact_target = {}
    for object_id, item in proposal_objects.items():
        fact_id = (item.get("mapping") or {}).get("factIds", [None])[0]
        if fact_id is not None and object_id in selected:
            selected_by_fact_target[(fact_id, item.get("targetKey"))] = selected[
                object_id
            ]

    operations = []
    blockers = []
    proposal_hash = proposal["proposalHash"]
    for object_id, item in proposal_objects.items():
        mapping = item.get("mapping") or {}
        resource_type = mapping.get("proposedResourceType")
        if resource_type not in {
            "StudySelectionObjective",
            "StudySelectionCriteria",
            "StudySelectionActivity",
        }:
            capability = target_capability(resource_type)
            if capability and capability[0] == "native_study_mutation":
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_FAMILY_EXECUTOR_UNAVAILABLE",
                        "details": [resource_type],
                    }
                )
            elif capability and capability[0] in {
                "governed_extension",
                "retained_narrative",
                "unresolved",
            }:
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_NON_NATIVE_TARGET",
                        "details": [resource_type],
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
        if candidate is None or missing:
            blockers.append(
                {
                    "proposal_object_id": object_id,
                    "code": "OSB_NATIVE_V2_SELECTED_DEPENDENCY_MISSING",
                    "details": missing or ["selected-candidate"],
                }
            )
            continue

        path = f"/studies/{target_study_uid}"
        if resource_type == "StudySelectionObjective":
            level = dependencies.get("objective-level")
            if (
                candidate.get("resourceType") != "ObjectiveTemplate"
                or candidate.get("parameterCount") != 0
                or not candidate.get("libraryName")
                or not level
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
                            "objective_template_uid": candidate["uid"],
                            "library_name": candidate["libraryName"],
                        },
                    },
                    {"create_objective": True},
                    {
                        "objective.template.uid": candidate["uid"],
                        "objective_level.term_uid": level["uid"],
                    },
                    target_study_uid,
                    target_study_version,
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
        else:
            flowchart = dependencies.get("flowchart-group")
            if (
                candidate.get("resourceType") != "Activity"
                or not flowchart
                or flowchart.get("resourceType") != "CTTerm"
                or flowchart.get("parentSubmissionValue") != "Flowchart Group"
            ):
                blockers.append(
                    {
                        "proposal_object_id": object_id,
                        "code": "OSB_NATIVE_V2_ACTIVITY_DTO_INCOMPLETE",
                        "details": [],
                    }
                )
                continue
            path += "/study-activities"
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
                )
            )

    return {"operations": operations, "blockers": blockers}
