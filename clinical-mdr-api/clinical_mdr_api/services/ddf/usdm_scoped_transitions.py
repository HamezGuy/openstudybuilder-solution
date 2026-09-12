"""Editable pending study/epoch control flow, preserving each native scope."""

from usdm_model.extension import ExtensionAttribute
from usdm_model.schedule_timeline import ScheduleTimeline
from usdm_model.scheduled_instance import ConditionAssignment, ScheduledDecisionInstance

from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    NativeExecutionReview, USDMMappingAuthorityRequired, native_json,
)


SCOPE_URL = "https://openstudybuilder.org/usdm/extensions/transition-scope/"


def project_scoped_transitions(mapper, study, version, design):
    from clinical_mdr_api.services.ddf.usdm_mapper import _items, _stable_selection_order

    metadata = getattr(study.current_metadata, "high_level_study_design", None)
    rules = [(
        "study-stop", study.uid, "study/current_metadata/high_level_study_design/study_stop_rules",
        getattr(metadata, "study_stop_rules", None), {},
        {"studyUid": study.uid},
    )]
    epochs = _stable_selection_order(
        _items(mapper._call(mapper._get_osb_study_epochs, study.uid)), "uid",
    )
    for epoch in epochs:
        for field, kind in (("start_rule", "epoch-start"), ("end_rule", "epoch-end")):
            rules.append((
                kind, epoch.uid, f"study-epochs/{epoch.uid}/{field}",
                getattr(epoch, field, None),
                {"epochId": mapper._id_manager.get_id("StudyEpoch", epoch.uid)},
                {"studyUid": study.uid, "studyEpochUid": epoch.uid, "field": field},
            ))
    identifier, context = mapper._id_manager.get_id, mapper._context
    for kind, uid, source_path, narrative, scope, source_scope in rules:
        if narrative is None or narrative == "":
            continue
        if not isinstance(narrative, str):
            raise USDMMappingAuthorityRequired("USDM_NATIVE_TRANSITION_TEXT_INVALID")
        source_key = f"{kind}:{uid}"
        timeline_id = identifier("ScheduleTimeline", source_key)
        decision_id = identifier("ScheduledDecisionInstance", source_key)
        assignment_id = identifier("ConditionAssignment", source_key)
        path = f"/study/versions/0/studyDesigns/0/scheduleTimelines/{len(design.scheduleTimelines)}"
        decision_path = path + "/instances/0"
        review: NativeExecutionReview = {
            "kind": kind, "state": "requires-review",
            "sourceScope": {
                **source_scope, "studyValueVersion": mapper._study_value_version,
                "nativeAsOf": native_json(mapper._study_as_of),
            },
            "canonicalScope": scope,
            "draftTargets": {
                "versionId": version.id, "designId": design.id,
                "timelineId": timeline_id, "scheduledDecisionInstanceId": decision_id,
                "conditionAssignmentId": assignment_id,
            },
            "documentPointers": {
                "timeline": path, "entryId": path + "/entryId",
                "entryCondition": path + "/entryCondition", "mainTimeline": path + "/mainTimeline",
                "decision": decision_path, "defaultConditionId": decision_path + "/defaultConditionId",
                "condition": decision_path + "/conditionAssignments/0/condition",
                "conditionTargetId": decision_path + "/conditionAssignments/0/conditionTargetId",
            },
            "requiredBindings": [
                "exact-native-scope", "timeline-placement-and-entry", "condition-target",
                "default-target", "reviewed-executable-predicate",
            ],
        }
        context.unresolved(
            "USDM_SCOPED_TRANSITION_EXECUTION_REVIEW_REQUIRED", source_path,
            "ConditionAssignment/conditionTargetId",
            "The native narrative has an exact study/epoch scope but no executable predicate or scheduled destination.",
            "Review this source scope and narrative, then explicitly author placement, targets and execution. An epoch rule cannot become a shared element rule.",
            execution_review=review,
        )
        extensions = [
            ExtensionAttribute(
                id=identifier("ExtensionAttribute", f"{source_key}:{field}"),
                url=SCOPE_URL + field, valueId=value, instanceType="ExtensionAttribute",
            ) for field, value in scope.items()
        ]
        extensions.append(mapper._native_extension(
            "scopedTransitionSource", source_key,
            {"kind": kind, "text": narrative, "sourceScope": review["sourceScope"]},
        ))
        assignment = context.build(
            ConditionAssignment, source_path,
            id=assignment_id, condition=narrative, extensionAttributes=extensions,
        )
        decision = context.build(
            ScheduledDecisionInstance, source_path, id=decision_id,
            name=f"Unplaced {kind} {uid}", epochId=scope.get("epochId"),
            conditionAssignments=[assignment],
        )
        design.scheduleTimelines.append(context.build(
            ScheduleTimeline, source_path, id=timeline_id,
            name=f"Unplaced {kind} {uid}", instances=[decision],
        ))
