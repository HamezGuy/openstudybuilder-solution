import pytest

from ..mappings.proposal_v2_native_operations import (
    NativeOperationPlanError,
    _source_values,
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
            "signature_verified": True,
        },
    }


def envelope(objects):
    return {
        "proposalHash": "p" * 64,
        "sections": {"objects": objects},
    }


def receipt(objects, candidates, **overrides):
    decision_set_hash = "d" * 64
    value = {
        "proposal_hash": "p" * 64,
        "review_complete": True,
        "rejected_object_count": 0,
        "native_execution_ready": True,
        "execution_blockers": [],
        "decision_set_hash": decision_set_hash,
        "target_study_uid": "Study_1",
        "target_study_version": "DRAFT",
        "target_study_status": "DRAFT",
        "target_study_owner_id": "reviewer-1",
        "target_ownership_verified": True,
        "execution_authorization": {
            "proposal_hash": "p" * 64,
            "target_study_uid": "Study_1",
            "target_study_version": "DRAFT",
            "target_study_status": "DRAFT",
            "decision_set_hash": decision_set_hash,
            "signature_verified": True,
            "actor_id": "reviewer-1",
            "authorization_content_hash": "a" * 64,
        },
        "objects": [
            reviewed(item, candidate_value)
            for item, candidate_value in zip(objects, candidates)
        ],
    }
    value.update(overrides)
    if "execution_authorization" not in overrides:
        value["execution_authorization"].update(
            {
                "target_study_uid": value["target_study_uid"],
                "target_study_version": value["target_study_version"],
                "target_study_status": value["target_study_status"],
                "decision_set_hash": value["decision_set_hash"],
                "actor_id": value["target_study_owner_id"],
            }
        )
    return value


def mark_create_request(review, object_id):
    item = next(
        value for value in review["objects"]
        if value["proposal_object_id"] == object_id
    )
    item["candidates"] = []
    item["latest_decision"] = {
        "action": "create_request",
        "candidate_key": None,
        "signature_verified": True,
    }


def test_duplicate_typed_source_names_fail_closed_instead_of_overwriting():
    item = {
        "proposalObjectId": "object-1",
        "source": {
            "values": [
                {"name": "name", "sourcePath": "/first/name", "value": "First"},
                {"name": "name", "sourcePath": "/second/name", "value": "Second"},
            ]
        },
    }
    with pytest.raises(NativeOperationPlanError, match="SOURCE_NAME_DUPLICATE"):
        _source_values(item)


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
        envelope(objects), receipt(objects, [activity, flowchart]), "Study_1", "DRAFT"
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
                "study_value_version": "DRAFT",
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


def test_soa_activity_visit_and_schedule_create_a_receipt_resolved_native_graph():
    activity_candidate = candidate("activity-candidate", "Activity", "Activity_1")
    flowchart = candidate(
        "flowchart-candidate", "CTTerm", "FlowchartGroup_Safety",
        parentSubmissionValue="Flowchart Group",
    )
    epoch_subtype = candidate(
        "epoch-subtype", "CTTerm", "EpochSubtype_Treatment",
        parentSubmissionValue="Epoch Sub Type",
    )
    visit_type = candidate(
        "visit-type", "CTTerm", "VisitType_Treatment",
        parentSubmissionValue="VisitType",
    )
    contact_mode = candidate(
        "contact-mode", "CTTerm", "VisitContact_OnSite",
        parentSubmissionValue="Visit Contact Mode",
    )
    time_reference = candidate(
        "time-reference", "CTTerm", "TimeReference_GlobalAnchor",
        parentSubmissionValue="Time Point Reference",
    )
    day_unit = candidate("day-unit", "UnitDefinition", "UnitDefinition_day")
    placeholder = candidate("create", "CreateRequest", "not-selected")

    activity = proposal_object(
        "activity-object", "soa-activity", "StudySelectionActivity",
        activity_candidate, dependencies=["soa-activity-flowchart-group"],
    )
    activity["mapping"]["factIds"] = ["fact-activity"]
    activity["source"] = {"values": [
        {"name": "activityId", "value": "soa-activity-bp"},
        {"name": "name", "value": "Blood pressure"},
    ]}
    flowchart_object = proposal_object(
        "flowchart-object", "soa-activity-flowchart-group", "CTTerm", flowchart
    )
    flowchart_object["mapping"]["factIds"] = ["fact-activity"]

    epoch = proposal_object(
        "epoch-object", "study-epoch", "StudyEpoch", placeholder,
        dependencies=["epoch-subtype"],
    )
    epoch["mapping"]["factIds"] = ["fact-epoch"]
    epoch["mapping"]["candidates"] = []
    epoch["source"] = {"values": [
        {"name": "epochId", "value": "epoch-treatment"},
        {"name": "name", "value": "Treatment"},
        {"name": "order", "value": 1},
    ]}
    epoch_subtype_object = proposal_object(
        "epoch-subtype-object", "epoch-subtype", "CTTerm", epoch_subtype
    )
    epoch_subtype_object["mapping"]["factIds"] = ["fact-epoch"]

    visit = proposal_object(
        "visit-object", "study-visit", "StudyVisit", placeholder,
        dependencies=[
            "visit-type", "visit-contact-mode", "visit-time-reference",
            "visit-time-unit",
        ],
    )
    visit["mapping"]["factIds"] = ["fact-visit"]
    visit["mapping"]["candidates"] = []
    visit["source"] = {"values": [
        {"name": "visitId", "value": "visit-day-1"},
        {"name": "epochId", "value": "epoch-treatment"},
        {"name": "name", "value": "Day 1"},
        {"name": "visitClass", "value": "MANUALLY_DEFINED_VISIT"},
        {"name": "showVisit", "value": True},
        {"name": "isGlobalAnchorVisit", "value": True},
        {"name": "sequenceOrder", "value": 1},
        {"name": "visitName", "value": "Day 1"},
        {"name": "visitShortName", "value": "Day 1"},
        {"name": "visitNumber", "value": 1},
        {"name": "uniqueVisitNumber", "value": 100},
        {"name": "timeValue", "value": 0},
        {"name": "nativeTimingReady", "value": True},
    ]}
    visit_dependency_objects = [
        proposal_object("visit-type-object", "visit-type", "CTTerm", visit_type),
        proposal_object(
            "contact-object", "visit-contact-mode", "CTTerm", contact_mode
        ),
        proposal_object(
            "time-reference-object", "visit-time-reference", "CTTerm",
            time_reference,
        ),
        proposal_object(
            "time-unit-object", "visit-time-unit", "UnitDefinition", day_unit
        ),
    ]
    for item in visit_dependency_objects:
        item["mapping"]["factIds"] = ["fact-visit"]

    schedule = proposal_object(
        "schedule-object", "activity-schedule", "StudyActivitySchedule", placeholder
    )
    schedule["mapping"]["factIds"] = ["fact-schedule"]
    schedule["mapping"]["candidates"] = []
    schedule["source"] = {"values": [
        {"name": "scheduleId", "value": "sf-1"},
        {"name": "activityId", "value": "soa-activity-bp"},
        {"name": "visitId", "value": "visit-day-1"},
    ]}

    objects = [
        activity, flowchart_object, epoch, epoch_subtype_object, visit,
        *visit_dependency_objects, schedule,
    ]
    candidates = [
        activity_candidate, flowchart, placeholder, epoch_subtype, placeholder,
        visit_type, contact_mode, time_reference, day_unit, placeholder,
    ]
    review = receipt(objects, candidates)
    for object_id in ("epoch-object", "visit-object", "schedule-object"):
        mark_create_request(review, object_id)

    plan = native_operation_plan(envelope(objects), review, "Study_1", "DRAFT")

    assert plan["blockers"] == []
    assert plan["deferred_objects"] == []
    assert [operation["family"] for operation in plan["operations"]] == [
        "StudyEpoch", "StudySelectionActivity", "StudyVisit",
        "StudyActivitySchedule",
    ]
    activity_operation = plan["operations"][1]
    schedule_operation = plan["operations"][3]
    assert activity_operation["source_identity"] == {
        "activityId": "soa-activity-bp"
    }
    assert schedule_operation["path"] == \
        "/studies/Study_1/study-activity-schedules"
    assert schedule_operation["body"] == {}
    assert schedule_operation["source_identity"] == {"scheduleId": "sf-1"}
    assert schedule_operation["record_hash_scope"] == "match"
    assert schedule_operation["body_references"] == [
        {
            "family": "StudySelectionActivity",
            "identity_name": "activityId",
            "identity_value": "soa-activity-bp",
            "body_path": "study_activity_uid",
            "read_match_path": "study_activity_uid",
            "proposal_object_id": "activity-object",
        },
        {
            "family": "StudyVisit",
            "identity_name": "visitId",
            "identity_value": "visit-day-1",
            "body_path": "study_visit_uid",
            "read_match_path": "study_visit_uid",
            "proposal_object_id": "visit-object",
        },
    ]


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
        envelope(objects), receipt(objects, [template, level]), "Study_1", "DRAFT"
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


def test_endpoint_operation_uses_template_level_and_timeframe_dto():
    objective_template = candidate(
        "objective-template",
        "ObjectiveTemplate",
        "ObjectiveTemplate_1",
        parameterCount=0,
        libraryName="Sponsor",
    )
    objective_level = candidate(
        "objective-level",
        "CTTerm",
        "ObjectiveLevel_1",
        parentSubmissionValue="Objective Level",
    )
    objective = proposal_object(
        "objective-object",
        "objective-template",
        "StudySelectionObjective",
        objective_template,
        dependencies=["objective-level"],
    )
    objective["source"] = {
        "values": [{"name": "objectiveId", "value": "OBJ-1"}]
    }
    template = candidate(
        "endpoint-template",
        "EndpointTemplate",
        "EndpointTemplate_1",
        parameterCount=0,
        libraryName="Sponsor",
    )
    level = candidate(
        "endpoint-level",
        "CTTerm",
        "EndpointLevel_1",
        parentSubmissionValue="Endpoint Level",
    )
    timeframe = candidate("endpoint-timeframe", "Timeframe", "Timeframe_1")
    endpoint = proposal_object(
        "endpoint-object",
        "endpoint-selection",
        "StudySelectionEndpoint",
        template,
        dependencies=["endpoint-level", "endpoint-timeframe"],
    )
    endpoint["source"] = {
        "values": [{"name": "objectiveId", "value": "OBJ-1"}]
    }
    objects = [
        objective,
        proposal_object(
            "objective-level-object", "objective-level", "CTTerm", objective_level
        ),
        endpoint,
        proposal_object("level-object", "endpoint-level", "CTTerm", level),
        proposal_object(
            "timeframe-object", "endpoint-timeframe", "Timeframe", timeframe
        ),
    ]

    plan = native_operation_plan(
        envelope(objects),
        receipt(
            objects,
            [objective_template, objective_level, template, level, timeframe],
        ),
        "Study_1",
        "DRAFT",
    )

    assert plan["blockers"] == []
    operation = next(
        row for row in plan["operations"] if row["family"] == "StudySelectionEndpoint"
    )
    assert operation["path"] == "/studies/Study_1/study-endpoints"
    assert operation["params"] == {"create_endpoint": True}
    assert operation["body"] == {
        "endpoint_level_uid": "EndpointLevel_1",
        "endpoint_data": {
            "parameter_terms": [],
            "endpoint_template_uid": "EndpointTemplate_1",
            "library_name": "Sponsor",
        },
        "timeframe_uid": "Timeframe_1",
    }
    assert operation["body_references"][0]["identity_value"] == "OBJ-1"
    assert operation["read_after_write"]["match"] == {
        "endpoint.template.uid": "EndpointTemplate_1",
        "endpoint_level.term_uid": "EndpointLevel_1",
        "timeframe.uid": "Timeframe_1",
    }


def test_endpoint_objective_link_resolves_through_prior_native_receipt_identity():
    objective_template = candidate(
        "objective-template", "ObjectiveTemplate", "ObjectiveTemplate_1",
        parameterCount=0, libraryName="Sponsor",
    )
    objective_level = candidate(
        "objective-level", "CTTerm", "ObjectiveLevel_1",
        parentSubmissionValue="Objective Level",
    )
    endpoint_template = candidate(
        "endpoint-template", "EndpointTemplate", "EndpointTemplate_1",
        parameterCount=0, libraryName="Sponsor",
    )
    endpoint_level = candidate(
        "endpoint-level", "CTTerm", "EndpointLevel_1",
        parentSubmissionValue="Endpoint Level",
    )
    objective = proposal_object(
        "objective-object", "objective-template", "StudySelectionObjective",
        objective_template, dependencies=["objective-level"],
    )
    objective["source"] = {"values": [{"name": "objectiveId", "value": "OBJ-1"}]}
    endpoint = proposal_object(
        "endpoint-object", "endpoint-selection", "StudySelectionEndpoint",
        endpoint_template, dependencies=["endpoint-level"],
    )
    endpoint["source"] = {"values": [{"name": "objectiveId", "value": "OBJ-1"}]}
    objects = [
        objective,
        proposal_object(
            "objective-level-object", "objective-level", "CTTerm", objective_level
        ),
        endpoint,
        proposal_object(
            "endpoint-level-object", "endpoint-level", "CTTerm", endpoint_level
        ),
    ]

    plan = native_operation_plan(
        envelope(objects),
        receipt(
            objects,
            [objective_template, objective_level, endpoint_template, endpoint_level],
        ),
        "Study_1",
        "DRAFT",
    )

    assert plan["blockers"] == []
    assert [operation["family"] for operation in plan["operations"]] == [
        "StudySelectionObjective",
        "StudySelectionEndpoint",
    ]
    assert plan["operations"][1]["body_references"] == [
        {
            "family": "StudySelectionObjective",
            "identity_name": "objectiveId",
            "identity_value": "OBJ-1",
            "body_path": "study_objective_uid",
            "read_match_path": "study_objective.study_objective_uid",
            "proposal_object_id": "objective-object",
        }
    ]


def test_endpoint_objective_link_blocks_when_objective_identity_is_missing():
    endpoint_template = candidate(
        "endpoint-template", "EndpointTemplate", "EndpointTemplate_1",
        parameterCount=0, libraryName="Sponsor",
    )
    endpoint_level = candidate(
        "endpoint-level", "CTTerm", "EndpointLevel_1",
        parentSubmissionValue="Endpoint Level",
    )
    endpoint = proposal_object(
        "endpoint-object", "endpoint-selection", "StudySelectionEndpoint",
        endpoint_template, dependencies=["endpoint-level"],
    )
    endpoint["source"] = {
        "values": [{"name": "objectiveId", "value": "OBJ-MISSING"}]
    }
    objects = [
        endpoint,
        proposal_object(
            "endpoint-level-object", "endpoint-level", "CTTerm", endpoint_level
        ),
    ]

    plan = native_operation_plan(
        envelope(objects),
        receipt(objects, [endpoint_template, endpoint_level]),
        "Study_1",
        "DRAFT",
    )

    assert [blocker["code"] for blocker in plan["blockers"]] == [
        "OSB_NATIVE_V2_REFERENCE_UNRESOLVED"
    ]


def test_endpoint_without_objective_identity_is_blocked_before_native_write():
    endpoint_template = candidate(
        "endpoint-template", "EndpointTemplate", "EndpointTemplate_1",
        parameterCount=0, libraryName="Sponsor",
    )
    endpoint_level = candidate(
        "endpoint-level", "CTTerm", "EndpointLevel_1",
        parentSubmissionValue="Endpoint Level",
    )
    endpoint = proposal_object(
        "endpoint-object", "endpoint-selection", "StudySelectionEndpoint",
        endpoint_template, dependencies=["endpoint-level"],
    )
    objects = [
        endpoint,
        proposal_object(
            "endpoint-level-object", "endpoint-level", "CTTerm", endpoint_level
        ),
    ]

    plan = native_operation_plan(
        envelope(objects),
        receipt(objects, [endpoint_template, endpoint_level]),
        "Study_1",
        "DRAFT",
    )

    assert plan["operations"] == []
    assert plan["blockers"] == [
        {
            "proposal_object_id": "endpoint-object",
            "code": "OSB_NATIVE_V2_ENDPOINT_OBJECTIVE_REQUIRED",
            "details": [],
        }
    ]


def test_metadata_create_request_builds_typed_patch_and_single_record_readback():
    metadata = {
        "proposalObjectId": "metadata-object",
        "targetKey": "study-metadata:study_population.number_of_expected_subjects",
        "dependencyTargetKeys": [],
        "source": {
            "values": [
                {
                    "name": "numericValue",
                    "sourcePath": "/numericValue",
                    "valueType": "number",
                    "value": 120,
                }
            ]
        },
        "mapping": {
            "factIds": ["fact-1"],
            "proposedResourceType": "StudyMetadata",
            "candidates": [],
        },
    }
    proposal = envelope([metadata])
    review = receipt([metadata], [candidate("unused", "Unused", "Unused_1")])
    review["objects"][0]["candidates"] = []
    review["objects"][0]["latest_decision"] = {
        "action": "create_request",
        "candidate_key": None,
        "signature_verified": True,
    }

    plan = native_operation_plan(proposal, review, "Study_1", "DRAFT")

    assert plan["blockers"] == []
    operation = plan["operations"][0]
    assert operation["family"] == "StudyMetadata"
    assert operation["method"] == "PATCH"
    assert operation["path"] == "/studies/Study_1"
    assert operation["body"] == {
        "current_metadata": {
            "study_population": {"number_of_expected_subjects": 120}
        }
    }
    assert operation["read_after_write"] == {
        "method": "GET",
        "path": "/studies/Study_1",
        "params": {"page_size": 0},
        "match": {
            "current_metadata.study_population.number_of_expected_subjects": 120
        },
        "collection": False,
    }


def test_epoch_and_visit_create_requests_use_native_routes_and_receipt_reference():
    epoch_placeholder = candidate("epoch-create", "StudyEpoch", "not-selected")
    epoch_subtype = candidate(
        "epoch-subtype", "CTTerm", "EpochSubtype_Treatment",
        parentSubmissionValue="Epoch Sub Type",
    )
    visit_placeholder = candidate("visit-create", "StudyVisit", "not-selected")
    visit_type = candidate(
        "visit-type", "CTTerm", "VisitType_Treatment",
        parentSubmissionValue="VisitType",
    )
    contact_mode = candidate(
        "contact", "CTTerm", "VisitContact_OnSite",
        parentSubmissionValue="Visit Contact Mode",
    )
    time_reference = candidate(
        "time-reference", "CTTerm", "TimeReference_GlobalAnchor",
        parentSubmissionValue="Time Point Reference",
    )
    day_unit = candidate("day-unit", "UnitDefinition", "UnitDefinition_day")

    epoch = proposal_object(
        "epoch-object", "study-epoch", "StudyEpoch", epoch_placeholder,
        dependencies=["epoch-subtype"],
    )
    epoch["mapping"]["candidates"] = []
    epoch["source"] = {"values": [
        {"name": "epochId", "value": "epoch-treatment"},
        {"name": "name", "value": "Treatment Period"},
        {"name": "order", "value": 1},
    ]}
    subtype_object = proposal_object(
        "epoch-subtype-object", "epoch-subtype", "CTTerm", epoch_subtype
    )
    visit = proposal_object(
        "visit-object", "study-visit", "StudyVisit", visit_placeholder,
        dependencies=[
            "visit-type", "visit-contact-mode", "visit-time-reference",
            "visit-time-unit",
        ],
    )
    visit["mapping"]["factIds"] = ["fact-visit"]
    visit["mapping"]["candidates"] = []
    visit["source"] = {"values": [
        {"name": "visitId", "value": "visit-day-1"},
        {"name": "epochId", "value": "epoch-treatment"},
        {"name": "name", "value": "Day 1"},
        {"name": "visitClass", "value": "MANUALLY_DEFINED_VISIT"},
        {"name": "showVisit", "value": True},
        {"name": "isGlobalAnchorVisit", "value": True},
        {"name": "sequenceOrder", "value": 1},
        {"name": "visitName", "value": "Day 1"},
        {"name": "visitShortName", "value": "Day 1"},
        {"name": "visitNumber", "value": 1},
        {"name": "uniqueVisitNumber", "value": 100},
        {"name": "timeValue", "value": 0},
        {"name": "nativeTimingReady", "value": True},
    ]}
    dependency_objects = [
        proposal_object("visit-type-object", "visit-type", "CTTerm", visit_type),
        proposal_object(
            "contact-object", "visit-contact-mode", "CTTerm", contact_mode
        ),
        proposal_object(
            "time-reference-object", "visit-time-reference", "CTTerm",
            time_reference,
        ),
        proposal_object(
            "time-unit-object", "visit-time-unit", "UnitDefinition", day_unit
        ),
    ]
    for item in dependency_objects:
        item["mapping"]["factIds"] = ["fact-visit"]
    objects = [epoch, subtype_object, visit, *dependency_objects]
    candidates = [
        epoch_placeholder, epoch_subtype, visit_placeholder, visit_type,
        contact_mode, time_reference, day_unit,
    ]
    review = receipt(objects, candidates)
    mark_create_request(review, "epoch-object")
    mark_create_request(review, "visit-object")

    plan = native_operation_plan(envelope(objects), review, "Study_1", "DRAFT")

    assert plan["blockers"] == []
    assert plan["deferred_objects"] == []
    assert [operation["family"] for operation in plan["operations"]] == [
        "StudyEpoch", "StudyVisit"
    ]
    epoch_operation, visit_operation = plan["operations"]
    assert epoch_operation["path"] == "/studies/Study_1/study-epochs"
    assert epoch_operation["body"] == {
        "study_uid": "Study_1",
        "epoch_subtype": "EpochSubtype_Treatment",
        "order": 1,
        "description": "Treatment Period",
    }
    assert epoch_operation["source_identity"] == {
        "epochId": "epoch-treatment"
    }
    assert epoch_operation["record_hash_scope"] == "match"
    assert visit_operation["path"] == "/studies/Study_1/study-visits"
    assert visit_operation["body"] == {
        "visit_type": {"term_uid": "VisitType_Treatment"},
        "show_visit": True,
        "description": "Day 1",
        "visit_contact_mode": {"term_uid": "VisitContact_OnSite"},
        "visit_class": "MANUALLY_DEFINED_VISIT",
        "is_global_anchor_visit": True,
        "is_soa_milestone": False,
        "time_reference": {"term_uid": "TimeReference_GlobalAnchor"},
        "time_value": 0,
        "time_unit_uid": "UnitDefinition_day",
        "visit_name": "Day 1",
        "visit_short_name": "Day 1",
        "visit_number": 1,
        "unique_visit_number": 100,
    }
    assert visit_operation["body_references"] == [{
        "family": "StudyEpoch",
        "identity_name": "epochId",
        "identity_value": "epoch-treatment",
        "body_path": "study_epoch_uid",
        "read_match_path": "study_epoch_uid",
        "proposal_object_id": "epoch-object",
    }]
    assert visit_operation["record_hash_scope"] == "match"


def test_arm_element_epoch_and_design_cell_create_native_relationship_graph():
    arm_placeholder = candidate("arm-create", "StudySelectionArm", "not-selected")
    arm_type = candidate(
        "arm-type", "CTTerm", "ArmType_Experimental",
        parentSubmissionValue="Arm Type",
    )
    element_placeholder = candidate(
        "element-create", "StudySelectionElement", "not-selected"
    )
    element_subtype = candidate(
        "element-subtype", "CTTerm", "ElementSubtype_Treatment",
        parentSubmissionValue="Element Sub Type",
    )
    week_unit = candidate("week-unit", "UnitDefinition", "UnitDefinition_week")
    epoch_placeholder = candidate("epoch-create", "StudyEpoch", "not-selected")
    epoch_subtype = candidate(
        "epoch-subtype", "CTTerm", "EpochSubtype_Treatment",
        parentSubmissionValue="Epoch Sub Type",
    )
    cell_placeholder = candidate("cell-create", "StudyDesignCell", "not-selected")

    arm = proposal_object(
        "arm-object", "study-arm", "StudySelectionArm", arm_placeholder,
        dependencies=["arm-type"],
    )
    arm["mapping"]["factIds"] = ["fact-arm"]
    arm["mapping"]["candidates"] = []
    arm["source"] = {"values": [
        {"name": "armId", "value": "arm-a"},
        {"name": "name", "value": "Arm A"},
        {"name": "shortName", "value": "A"},
        {"name": "description", "value": "Experimental arm"},
        {"name": "numberOfSubjects", "value": 20},
    ]}
    arm_type_object = proposal_object(
        "arm-type-object", "arm-type", "CTTerm", arm_type
    )
    arm_type_object["mapping"]["factIds"] = ["fact-arm"]

    element = proposal_object(
        "element-object", "study-element", "StudySelectionElement",
        element_placeholder,
        dependencies=["element-subtype", "element-duration-unit"],
    )
    element["mapping"]["factIds"] = ["fact-element"]
    element["mapping"]["candidates"] = []
    element["source"] = {"values": [
        {"name": "elementId", "value": "element-active"},
        {"name": "name", "value": "Active treatment"},
        {"name": "plannedDurationValue", "value": 12},
        {"name": "startRule", "value": "After randomization"},
    ]}
    element_subtype_object = proposal_object(
        "element-subtype-object", "element-subtype", "CTTerm", element_subtype
    )
    element_subtype_object["mapping"]["factIds"] = ["fact-element"]
    element_unit_object = proposal_object(
        "element-unit-object", "element-duration-unit", "UnitDefinition", week_unit
    )
    element_unit_object["mapping"]["factIds"] = ["fact-element"]

    epoch = proposal_object(
        "epoch-object", "study-epoch", "StudyEpoch", epoch_placeholder,
        dependencies=["epoch-subtype"],
    )
    epoch["mapping"]["factIds"] = ["fact-epoch"]
    epoch["mapping"]["candidates"] = []
    epoch["source"] = {"values": [
        {"name": "epochId", "value": "epoch-treatment"},
        {"name": "name", "value": "Treatment"},
        {"name": "order", "value": 1},
        {"name": "durationValue", "value": 12},
        {"name": "durationUnit", "value": "week"},
        {"name": "endRule", "value": "End of treatment"},
    ]}
    epoch_subtype_object = proposal_object(
        "epoch-subtype-object", "epoch-subtype", "CTTerm", epoch_subtype
    )
    epoch_subtype_object["mapping"]["factIds"] = ["fact-epoch"]

    cell = proposal_object(
        "cell-object", "study-design-cell", "StudyDesignCell", cell_placeholder
    )
    cell["mapping"]["factIds"] = ["fact-cell"]
    cell["mapping"]["candidates"] = []
    cell["source"] = {"values": [
        {"name": "designCellId", "value": "cell-a-treatment"},
        {"name": "armId", "value": "arm-a"},
        {"name": "epochId", "value": "epoch-treatment"},
        {"name": "elementId", "value": "element-active"},
        {"name": "transitionRule", "value": "Proceed after randomization"},
        {"name": "order", "value": 1},
    ]}

    objects = [
        arm, arm_type_object, element, element_subtype_object,
        element_unit_object, epoch, epoch_subtype_object, cell,
    ]
    review = receipt(
        objects,
        [
            arm_placeholder, arm_type, element_placeholder, element_subtype,
            week_unit, epoch_placeholder, epoch_subtype, cell_placeholder,
        ],
    )
    for object_id in ("arm-object", "element-object", "epoch-object", "cell-object"):
        mark_create_request(review, object_id)

    plan = native_operation_plan(envelope(objects), review, "Study_1", "DRAFT")

    assert plan["blockers"] == []
    assert plan["deferred_objects"] == []
    assert [operation["family"] for operation in plan["operations"]] == [
        "StudySelectionArm", "StudySelectionElement", "StudyEpoch", "StudyDesignCell"
    ]
    arm_operation, element_operation, epoch_operation, cell_operation = plan["operations"]
    assert arm_operation["body"] == {
        "name": "Arm A",
        "short_name": "A",
        "description": "Experimental arm",
        "number_of_subjects": 20,
        "arm_type_uid": "ArmType_Experimental",
        "merge_branch_for_this_arm_for_sdtm_adam": False,
    }
    assert arm_operation["source_identity"] == {"armId": "arm-a"}
    assert element_operation["body"] == {
        "name": "Active treatment",
        "start_rule": "After randomization",
        "planned_duration": {
            "duration_value": 12,
            "duration_unit_code": {"uid": "UnitDefinition_week"},
        },
        "element_subtype_uid": "ElementSubtype_Treatment",
    }
    assert epoch_operation["body"] == {
        "study_uid": "Study_1",
        "epoch_subtype": "EpochSubtype_Treatment",
        "description": "Treatment",
        "order": 1,
        "end_rule": "End of treatment",
        "duration": 12,
        "duration_unit": "week",
    }
    assert cell_operation["path"] == "/studies/Study_1/study-design-cells"
    assert cell_operation["body"] == {
        "transition_rule": "Proceed after randomization",
        "order": 1,
    }
    assert [reference["proposal_object_id"] for reference in cell_operation["body_references"]] == [
        "arm-object", "epoch-object", "element-object"
    ]


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
        envelope(objects), receipt(objects, [template, criteria_type]), "Study_1", "DRAFT"
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
        envelope(objects), conflict_receipt, "Study_1", "DRAFT"
    )
    assert blocked["operations"] == []
    assert blocked["blockers"][0]["code"] == (
        "OSB_NATIVE_V2_CRITERIA_DTO_INCOMPLETE_OR_TYPE_CONFLICT"
    )


def test_parameterized_template_and_incomplete_endpoint_block():
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
        "DRAFT",
    )
    assert plan["operations"] == []
    assert {item["code"] for item in plan["blockers"]} == {
        "OSB_NATIVE_V2_OBJECTIVE_DTO_INCOMPLETE",
        "OSB_NATIVE_V2_ENDPOINT_DTO_INCOMPLETE",
    }


def test_authority_and_target_study_are_required_before_planning_mutations():
    with pytest.raises(NativeOperationPlanError, match="TARGET_STUDY_REQUIRED"):
        native_operation_plan(envelope([]), receipt([], []), "", "DRAFT")
    with pytest.raises(NativeOperationPlanError, match="TARGET_STUDY_VERSION_REQUIRED"):
        native_operation_plan(envelope([]), receipt([], []), "Study_1", "")
    with pytest.raises(NativeOperationPlanError, match="AUTHORITY_NOT_READY"):
        native_operation_plan(
            envelope([]),
            receipt(
                [],
                [],
                native_execution_ready=False,
                execution_blockers=["OSB_STUDY_OWNERSHIP_UNVERIFIED"],
            ),
            "Study_1",
            "DRAFT",
        )
    with pytest.raises(NativeOperationPlanError, match="REVIEW_TARGET_STUDY_MISMATCH"):
        native_operation_plan(
            envelope([]),
            receipt([], []),
            "Study_2",
            "DRAFT",
        )


def test_idempotency_key_is_bound_to_target_study_and_rejects_fake_version():
    activity = candidate("activity-candidate", "Activity", "Activity_1")
    flowchart = candidate(
        "flowchart-candidate",
        "CTTerm",
        "FlowchartGroup_1",
        parentSubmissionValue="Flowchart Group",
    )
    objects = [
        proposal_object(
            "activity-object",
            "activity",
            "StudySelectionActivity",
            activity,
            dependencies=["flowchart-group"],
        ),
        proposal_object("flowchart-object", "flowchart-group", "CTTerm", flowchart),
    ]
    first = native_operation_plan(
        envelope(objects), receipt(objects, [activity, flowchart]), "Study_1", "DRAFT"
    )
    other_study = native_operation_plan(
        envelope(objects),
        receipt(
            objects,
            [activity, flowchart],
            target_study_uid="Study_2",
        ),
        "Study_2",
        "DRAFT",
    )
    keys = {
        first["operations"][0]["idempotency_key"],
        other_study["operations"][0]["idempotency_key"],
    }
    assert len(keys) == 2
    with pytest.raises(NativeOperationPlanError, match="VERSION_INVALID"):
        native_operation_plan(
            envelope(objects), receipt(objects, [activity, flowchart]), "Study_1", "0.2"
        )


def test_standalone_governed_reference_is_deferred_without_blocking_native_subset():
    odm_item = candidate("odm-item", "OdmItem", "OdmItem_1")
    objects = [
        proposal_object(
            "odm-item-object",
            "odm-item",
            "OdmItem",
            odm_item,
        )
    ]

    plan = native_operation_plan(
        envelope(objects),
        receipt(objects, [odm_item]),
        "Study_1",
        "DRAFT",
    )

    assert plan["operations"] == []
    assert plan["blockers"] == []
    assert plan["deferred_objects"] == [
        {
            "proposal_object_id": "odm-item-object",
            "resource_type": "OdmItem",
            "capability_kind": "governed_library_reference",
        }
    ]
