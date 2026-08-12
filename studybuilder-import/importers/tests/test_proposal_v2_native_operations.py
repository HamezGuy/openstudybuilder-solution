import pytest

from ..mappings.proposal_v2_native_operations import (
    NativeOperationPlanError,
    native_operation_plan,
)


def candidate(key, resource_type, uid, **extra):
    return {
        "candidateKey": key,
        "resourceType": resource_type,
        "uid": uid,
        **extra,
    }


def proposal_object(
    object_id, target_key, resource_type, candidate_value, dependencies=()
):
    return {
        "proposalObjectId": object_id,
        "targetKey": target_key,
        "dependencyTargetKeys": list(dependencies),
        "mapping": {
            "factIds": ["fact-1"],
            "proposedResourceType": resource_type,
            "candidates": [candidate_value],
        },
    }


def reviewed(item, candidate_value):
    return {
        "proposal_object_id": item["proposalObjectId"],
        "candidates": [candidate_value],
        "latest_decision": {
            "action": "selected_candidate",
            "candidate_key": candidate_value["candidateKey"],
        },
    }


def envelope(objects):
    return {
        "proposalHash": "p" * 64,
        "sections": {"objects": objects},
    }


def receipt(objects, candidates, **overrides):
    value = {
        "proposal_hash": "p" * 64,
        "native_execution_ready": True,
        "execution_blockers": [],
        "objects": [
            reviewed(item, candidate_value)
            for item, candidate_value in zip(objects, candidates)
        ],
    }
    value.update(overrides)
    return value


def test_activity_operation_uses_existing_ui_backed_route_and_complete_dto():
    activity = candidate("activity-candidate", "Activity", "Activity_1")
    flowchart = candidate(
        "flowchart-candidate",
        "CTTerm",
        "FlowchartGroup_1",
        parentSubmissionValue="Flowchart Group",
    )
    activity_object = proposal_object(
        "activity-object",
        "activity",
        "StudySelectionActivity",
        activity,
        dependencies=["flowchart-group"],
    )
    flowchart_object = proposal_object(
        "flowchart-object", "flowchart-group", "CTTerm", flowchart
    )
    objects = [activity_object, flowchart_object]

    plan = native_operation_plan(
        envelope(objects), receipt(objects, [activity, flowchart]), "Study_1", "0.1"
    )

    assert plan["blockers"] == []
    assert plan["operations"] == [
        {
            "proposal_object_id": "activity-object",
            "family": "StudySelectionActivity",
            "idempotency_key": plan["operations"][0]["idempotency_key"],
            "method": "POST",
            "path": "/studies/Study_1/study-activities",
            "params": None,
            "body": {
                "activity_uid": "Activity_1",
                "soa_group_term_uid": "FlowchartGroup_1",
                "show_activity_in_protocol_flowchart": True,
            },
            "preconditions": {
                "study_uid": "Study_1",
                "study_value_version": "0.1",
                "study_status": "DRAFT",
            },
            "read_after_write": {
                "method": "GET",
                "path": "/studies/Study_1/study-activities",
                "params": {"page_size": 0},
                "match": {
                    "activity.uid": "Activity_1",
                    "study_soa_group.soa_group_term_uid": "FlowchartGroup_1",
                },
            },
        }
    ]
    assert len(plan["operations"][0]["idempotency_key"]) == 64


def test_objective_operation_uses_native_create_and_selection_dto():
    template = candidate(
        "objective-template",
        "ObjectiveTemplate",
        "ObjectiveTemplate_1",
        parameterCount=0,
        libraryName="Sponsor",
    )
    level = candidate(
        "objective-level",
        "CTTerm",
        "ObjectiveLevel_1",
        parentSubmissionValue="Objective Level",
    )
    objective = proposal_object(
        "objective-object",
        "objective-template",
        "StudySelectionObjective",
        template,
        dependencies=["objective-level"],
    )
    level_object = proposal_object("level-object", "objective-level", "CTTerm", level)
    objects = [objective, level_object]

    operation = native_operation_plan(
        envelope(objects), receipt(objects, [template, level]), "Study_1", "0.1"
    )["operations"][0]

    assert operation["path"] == "/studies/Study_1/study-objectives"
    assert operation["params"] == {"create_objective": True}
    assert operation["body"] == {
        "objective_level_uid": "ObjectiveLevel_1",
        "objective_data": {
            "parameter_terms": [],
            "objective_template_uid": "ObjectiveTemplate_1",
            "library_name": "Sponsor",
        },
    }
    assert operation["read_after_write"]["match"] == {
        "objective.template.uid": "ObjectiveTemplate_1",
        "objective_level.term_uid": "ObjectiveLevel_1",
    }


def test_criteria_operation_requires_template_native_type_to_match_reviewed_ct():
    template = candidate(
        "criteria-template",
        "CriteriaTemplate",
        "CriteriaTemplate_1",
        parameterCount=0,
        libraryName="Sponsor",
        criteriaTypeUid="CriteriaType_1",
    )
    criteria_type = candidate(
        "criteria-type",
        "CTTerm",
        "CriteriaType_1",
        parentSubmissionValue="Criteria Type",
    )
    criterion = proposal_object(
        "criteria-object",
        "inclusion-criterion",
        "StudySelectionCriteria",
        template,
        dependencies=["criteria-type"],
    )
    type_object = proposal_object(
        "type-object", "criteria-type", "CTTerm", criteria_type
    )
    objects = [criterion, type_object]

    plan = native_operation_plan(
        envelope(objects), receipt(objects, [template, criteria_type]), "Study_1", "0.1"
    )

    assert plan["blockers"] == []
    assert plan["operations"][0]["path"] == "/studies/Study_1/study-criteria"
    assert plan["operations"][0]["params"] == {"create_criteria": True}
    assert plan["operations"][0]["body"] == {
        "criteria_data": {
            "parameter_terms": [],
            "criteria_template_uid": "CriteriaTemplate_1",
            "library_name": "Sponsor",
        }
    }

    conflict = {**template, "criteriaTypeUid": "CriteriaType_Exclusion"}
    conflict_receipt = receipt(objects, [conflict, criteria_type])
    conflict_receipt["objects"][0]["candidates"] = [conflict]
    conflict_receipt["objects"][0]["latest_decision"]["candidate_key"] = conflict[
        "candidateKey"
    ]
    blocked = native_operation_plan(
        envelope(objects), conflict_receipt, "Study_1", "0.1"
    )
    assert blocked["operations"] == []
    assert blocked["blockers"][0]["code"] == (
        "OSB_NATIVE_V2_CRITERIA_DTO_INCOMPLETE_OR_TYPE_CONFLICT"
    )


def test_parameterized_template_and_unimplemented_native_family_block():
    template = candidate(
        "objective-template",
        "ObjectiveTemplate",
        "ObjectiveTemplate_1",
        parameterCount=1,
        libraryName="Sponsor",
    )
    level = candidate(
        "objective-level",
        "CTTerm",
        "ObjectiveLevel_1",
        parentSubmissionValue="Objective Level",
    )
    objective = proposal_object(
        "objective-object",
        "objective-template",
        "StudySelectionObjective",
        template,
        dependencies=["objective-level"],
    )
    level_object = proposal_object("level-object", "objective-level", "CTTerm", level)
    endpoint_candidate = candidate("endpoint", "EndpointTemplate", "EndpointTemplate_1")
    endpoint = proposal_object(
        "endpoint-object",
        "endpoint-selection",
        "StudySelectionEndpoint",
        endpoint_candidate,
    )
    objects = [objective, level_object, endpoint]
    plan = native_operation_plan(
        envelope(objects),
        receipt(objects, [template, level, endpoint_candidate]),
        "Study_1",
        "0.1",
    )
    assert plan["operations"] == []
    assert {item["code"] for item in plan["blockers"]} == {
        "OSB_NATIVE_V2_OBJECTIVE_DTO_INCOMPLETE",
        "OSB_NATIVE_V2_FAMILY_EXECUTOR_UNAVAILABLE",
    }


def test_authority_and_target_study_are_required_before_planning_mutations():
    with pytest.raises(NativeOperationPlanError, match="TARGET_STUDY_REQUIRED"):
        native_operation_plan(envelope([]), receipt([], []), "", "0.1")
    with pytest.raises(NativeOperationPlanError, match="TARGET_STUDY_VERSION_REQUIRED"):
        native_operation_plan(envelope([]), receipt([], []), "Study_1", "")
    with pytest.raises(NativeOperationPlanError, match="AUTHORITY_NOT_READY"):
        native_operation_plan(
            envelope([]),
            receipt(
                [],
                [],
                native_execution_ready=False,
                execution_blockers=["OSB_STUDY_OWNERSHIP_VERSION_UNRESOLVED"],
            ),
            "Study_1",
            "0.1",
        )
