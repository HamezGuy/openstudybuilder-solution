"""Preserve native per-cell transition narratives as pending USDM decisions.

OSB's cell TATRANS rule describes an alternative element/sequence, not the
shared element's TEENRL end rule. The native editor stores prose and cell
identities; it does not store an executable predicate or scheduled destination.
"""

from usdm_model.extension import ExtensionAttribute
from usdm_model.schedule_timeline import ScheduleTimeline
from usdm_model.scheduled_instance import ConditionAssignment, ScheduledDecisionInstance

from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    CellTransitionExecutionReview,
    USDMMappingAuthorityRequired,
    native_json,
)


SCOPE_URL = "https://openstudybuilder.org/usdm/extensions/cell-transition-scope/"
REVIEW_CODE = "USDM_CELL_TRANSITION_EXECUTION_REVIEW_REQUIRED"


def project_cell_transitions(mapper, study, version, design):
    """Add an explicitly incomplete, individually scoped draft for each rule.

    A separate draft timeline keeps an unplaced decision out of the existing
    visit sequence. Its entry, main role, destination and executable evaluation
    require review. No source narrative is parsed into an executable predicate.
    """
    from clinical_mdr_api.services.ddf.usdm_mapper import _items, _stable_selection_order

    context = mapper._context
    rows = _stable_selection_order(
        _items(mapper._call(mapper._get_osb_study_design_cells, study.uid)),
        "design_cell_uid",
    )
    identifier = mapper._id_manager.get_id
    for row in rows:
        narrative = getattr(row, "transition_rule", None)
        # Empty optional native text asserts no transition rule. Its original
        # empty/null value remains in the exact native cell record.
        if narrative is None or narrative == "":
            continue
        uid = row.design_cell_uid
        if not isinstance(uid, str) or not uid or not isinstance(narrative, str):
            raise USDMMappingAuthorityRequired("USDM_CELL_TRANSITION_SOURCE_INVALID")
        source_path = f"study-design-cells/{uid}/transition_rule"
        scope = context.cell_scopes.get(uid, {})
        timeline_id = identifier("ScheduleTimeline", f"cell-transition:{uid}")
        decision_id = identifier("ScheduledDecisionInstance", f"cell-transition:{uid}")
        assignment_id = identifier("ConditionAssignment", f"cell-transition:{uid}")
        timeline_index = len(design.scheduleTimelines)
        # The native mapper creates one selected version/design. These pointers
        # accompany exact entity IDs; a later edit must re-resolve those IDs.
        timeline_pointer = (
            f"/study/versions/0/studyDesigns/0/scheduleTimelines/{timeline_index}"
        )
        decision_pointer = f"{timeline_pointer}/instances/0"
        review: CellTransitionExecutionReview = {
            "kind": "cell-transition",
            "state": "requires-review",
            "sourceScope": {
                "studyUid": study.uid,
                "studyValueVersion": mapper._study_value_version,
                "nativeAsOf": native_json(mapper._study_as_of),
                "designCellUid": uid,
                "studyArmUid": getattr(row, "study_arm_uid", None),
                "studyBranchArmUid": getattr(row, "study_branch_arm_uid", None),
                "studyEpochUid": getattr(row, "study_epoch_uid", None),
                "studyElementUid": getattr(row, "study_element_uid", None),
            },
            "canonicalScope": dict(scope),
            "draftTargets": {
                "versionId": version.id,
                "designId": design.id,
                "timelineId": timeline_id,
                "scheduledDecisionInstanceId": decision_id,
                "conditionAssignmentId": assignment_id,
            },
            "documentPointers": {
                "timeline": timeline_pointer,
                "entryId": f"{timeline_pointer}/entryId",
                "entryCondition": f"{timeline_pointer}/entryCondition",
                "mainTimeline": f"{timeline_pointer}/mainTimeline",
                "decision": decision_pointer,
                "defaultConditionId": f"{decision_pointer}/defaultConditionId",
                "condition": f"{decision_pointer}/conditionAssignments/0/condition",
                "conditionTargetId": f"{decision_pointer}/conditionAssignments/0/conditionTargetId",
            },
            "requiredBindings": [
                "exact-cell-and-arm-applicability",
                "timeline-placement-and-entry",
                "condition-target",
                "default-target",
                "reviewed-executable-predicate",
            ],
        }
        context.unresolved(
            REVIEW_CODE, source_path, "ConditionAssignment/conditionTargetId",
            "The exact cell transition narrative is preserved as a pending decision. "
            "The native source supplies no scheduled destination, default path or executable predicate.",
            "Review the displayed native cell, arm/branch, epoch and element; place this decision in its "
            "intended timeline and select exact scheduled targets. Preserve the source narrative while "
            "authoring its executable evaluation. The current EDC build compiler rejects scheduled decisions "
            "until supported reviewed execution is available.",
            execution_review=review,
        )
        scope_extensions = [
            ExtensionAttribute(
                id=identifier("ExtensionAttribute", f"cell-transition:{uid}:{name}"),
                url=SCOPE_URL + name,
                valueId=target,
                instanceType="ExtensionAttribute",
            )
            for name, target in scope.items()
        ]
        assignment = context.build(
            ConditionAssignment, source_path,
            id=assignment_id,
            condition=narrative,
            # conditionTargetId is required by USDM, but absent in this source.
            extensionAttributes=[
                *scope_extensions,
                mapper._native_extension("cellTransitionSource", uid, row),
            ],
        )
        decision = context.build(
            ScheduledDecisionInstance, source_path,
            id=decision_id,
            name=f"Cell transition {uid}",
            epochId=scope.get("epochId"),
            conditionAssignments=[assignment],
        )
        timeline = context.build(
            ScheduleTimeline, source_path,
            id=timeline_id,
            name=f"Unplaced cell transition {uid}",
            instances=[decision],
            # No native mainTimeline, entryCondition or entryId is available.
            # Their required omissions keep this review draft non-conformant.
        )
        design.scheduleTimelines.append(timeline)
