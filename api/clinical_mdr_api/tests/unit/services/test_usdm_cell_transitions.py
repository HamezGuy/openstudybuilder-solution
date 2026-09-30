"""Actual native-model cell rules -> typed USDM drafts; no database calls."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from clinical_mdr_api.models.study_selections.study_epoch import StudyEpoch
from clinical_mdr_api.models.study_selections.study_selection import (
    StudyDesignCell,
    StudySelectionBranchArm,
)
from clinical_mdr_api.services.ddf.usdm_cell_transitions import REVIEW_CODE, SCOPE_URL
from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION, native_study_graph


SCHEMA = Path(__file__).parents[2] / "fixtures/usdm_native_model4_schema.json"
SCHEMA_SHA256 = "dc4303bca26256c56e5cb83222e898a09a5244472f7fd092b425cc2b7568fe19"


def run(graph, monkeypatch):
    source = NativeStudySource(graph)
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    report = source.mapper().map_with_report(graph["study"], VERSION)
    return report, source.calls


def at(value, pointer):
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def reviews(report):
    return {
        issue["executionReview"]["sourceScope"]["designCellUid"]: issue["executionReview"]
        for issue in report["mappingReport"]["issues"] if issue["code"] == REVIEW_CODE
    }


def two_cells(scope):
    graph = native_study_graph()
    first = graph["cells"][0]
    first.transition_rule = "Responders go to washout; preserve 0, <b>yes</b> and trailing space. "
    values = first.model_dump(mode="json")
    values.update(
        design_cell_uid="Cell_2", order=2,
        transition_rule="Nonresponders continue treatment.\nNo destination UID was authored.",
    )
    if scope == "branch":
        branch = graph["branches"][0].model_dump(mode="json")
        # The two native branch labels deliberately match. Only UIDs bind scope.
        branch.update(branch_arm_uid="Branch_2", order=2)
        graph["branches"].append(StudySelectionBranchArm(**branch))
        values.update(study_branch_arm_uid="Branch_2", study_arm_uid=None)
    else:
        epoch = graph["epochs"][0].model_dump(mode="json")
        # Matching epoch labels must not collapse the two native identities.
        epoch.update(uid="Epoch_2", order=2)
        graph["epochs"].append(StudyEpoch(**epoch))
        values["study_epoch_uid"] = "Epoch_2"
    graph["cells"].append(StudyDesignCell(**values))
    return graph


@pytest.mark.parametrize("scope", ["branch", "epoch"])
def test_two_cells_share_one_element_without_sharing_transition_rules(monkeypatch, scope):
    graph = two_cells(scope)
    before = native_json(graph)
    report, calls = run(graph, monkeypatch)
    assert native_json(graph) == before
    assert len([call for call in calls if call[0] == "cells"]) == 1
    document = report["document"]
    design = document["study"]["versions"][0]["studyDesigns"][0]
    assert len(design["elements"]) == 1
    element = design["elements"][0]
    assert element["transitionStartRule"]["text"] == "Begin assigned treatment"
    assert element["transitionEndRule"]["text"] == "Treatment completed"
    by_cell = reviews(report)
    assert set(by_cell) == {"Cell_1", "Cell_2"}
    assert report["mappingReport"]["state"] == "incomplete"
    native = {cell.design_cell_uid: cell for cell in graph["cells"]}
    for uid, review in by_cell.items():
        assert review["kind"] == "cell-transition"
        assert review["state"] == "requires-review"
        assert review["sourceScope"]["studyUid"] == STUDY_UID
        assert review["sourceScope"]["studyValueVersion"] == VERSION
        assert review["sourceScope"]["nativeAsOf"] == AS_OF.isoformat()
        assert review["sourceScope"]["studyElementUid"] == "Element_1"
        assert review["sourceScope"]["studyEpochUid"] == native[uid].study_epoch_uid
        assert review["sourceScope"]["studyBranchArmUid"] == native[uid].study_branch_arm_uid
        assert review["canonicalScope"]["elementId"] == element["id"]
        cell = next(cell for cell in design["studyCells"] if cell["id"] == review["canonicalScope"]["studyCellId"])
        assert cell["armId"] == review["canonicalScope"]["armId"]
        assert cell["epochId"] == review["canonicalScope"]["epochId"]
        assert cell["elementIds"] == [element["id"]]
        timeline = at(document, review["documentPointers"]["timeline"])
        decision = at(document, review["documentPointers"]["decision"])
        assignment = decision["conditionAssignments"][0]
        assert timeline["id"] == review["draftTargets"]["timelineId"]
        assert decision["id"] == review["draftTargets"]["scheduledDecisionInstanceId"]
        assert assignment["id"] == review["draftTargets"]["conditionAssignmentId"]
        assert assignment["condition"] == native[uid].transition_rule
        assert at(document, review["documentPointers"]["condition"]) == native[uid].transition_rule
        assert decision["epochId"] == cell["epochId"]
        assert decision.get("defaultConditionId") is None
        assert "conditionTargetId" not in assignment
        assert not {"mainTimeline", "entryId", "entryCondition"} & timeline.keys()
        assert "reviewed-executable-predicate" in review["requiredBindings"]
        assert not {"predicate", "expression", "evaluator"} & assignment.keys()
        references = {
            item["url"][len(SCOPE_URL):]: item["valueId"]
            for item in assignment["extensionAttributes"] if item["url"].startswith(SCOPE_URL)
        }
        assert references == review["canonicalScope"]
    left, right = by_cell["Cell_1"], by_cell["Cell_2"]
    assert left["draftTargets"]["conditionAssignmentId"] != right["draftTargets"]["conditionAssignmentId"]
    if scope == "branch":
        assert left["canonicalScope"]["armId"] != right["canonicalScope"]["armId"]
        assert left["canonicalScope"]["epochId"] == right["canonicalScope"]["epochId"]
        assert left["canonicalScope"]["parentArmId"] == right["canonicalScope"]["parentArmId"]
    else:
        assert left["canonicalScope"]["armId"] == right["canonicalScope"]["armId"]
        assert left["canonicalScope"]["epochId"] != right["canonicalScope"]["epochId"]
    # The existing visit timeline is left intact; no new edge is silently added.
    main = next(timeline for timeline in design["scheduleTimelines"] if timeline.get("mainTimeline") is True)
    assert len(main["instances"]) == 3
    assert all(instance["instanceType"] == "ScheduledActivityInstance" for instance in main["instances"])


def test_identical_rule_text_does_not_merge_distinct_native_cell_identities(monkeypatch):
    graph = two_cells("branch")
    graph["cells"][1].transition_rule = graph["cells"][0].transition_rule
    report, _ = run(graph, monkeypatch)
    by_cell = reviews(report)
    assert len(by_cell) == 2
    assert len({value["draftTargets"]["conditionAssignmentId"] for value in by_cell.values()}) == 2
    assert len({value["canonicalScope"]["studyCellId"] for value in by_cell.values()}) == 2


@pytest.mark.parametrize("narrative", [None, ""])
def test_absent_optional_rule_has_no_invented_decision_and_preserves_source_value(monkeypatch, narrative):
    graph = native_study_graph()
    graph["cells"][0].transition_rule = narrative
    report, _ = run(graph, monkeypatch)
    assert reviews(report) == {}
    design = report["document"]["study"]["versions"][0]["studyDesigns"][0]
    assert len(design["scheduleTimelines"]) == 1
    cell = next(record["record"] for record in report["nativeRecords"] if record["kind"] == "studyDesignCell")
    assert cell["transition_rule"] == narrative


def test_missing_native_cell_relationship_preserves_narrative_without_a_guessed_canonical_scope(monkeypatch):
    graph = native_study_graph()
    graph["cells"][0].study_element_uid = "Unselected_Element"
    report, _ = run(graph, monkeypatch)
    review = reviews(report)["Cell_1"]
    assert review["canonicalScope"] == {}
    assert review["sourceScope"]["studyElementUid"] == "Unselected_Element"
    decision = at(report["document"], review["documentPointers"]["decision"])
    assignment = decision["conditionAssignments"][0]
    assert assignment["condition"] == graph["cells"][0].transition_rule
    assert decision.get("epochId") is None
    assert not any(item["url"].startswith(SCOPE_URL) for item in assignment["extensionAttributes"])
    assert any(issue["code"] == "USDM_CELL_SELECTION_UNRESOLVED" for issue in report["mappingReport"]["issues"])


def test_real_pinned_condition_assignment_requires_explicit_target_review(monkeypatch):
    assert sha256(SCHEMA.read_bytes()).hexdigest() == SCHEMA_SHA256
    schema = json.loads(SCHEMA.read_bytes())
    validator = Draft202012Validator({
        "$ref": "#/components/schemas/ConditionAssignment-Input",
        "components": schema["components"],
    })
    report, _ = run(native_study_graph(), monkeypatch)
    review = reviews(report)["Cell_1"]
    decision = at(report["document"], review["documentPointers"]["decision"])
    assignment = decision["conditionAssignments"][0]
    errors = list(validator.iter_errors(assignment))
    assert len(errors) == 1 and errors[0].validator == "required"
    assert "conditionTargetId" in errors[0].message
    # Explicit synthetic author review of an actual target identity in this
    # document. This validates shape only, never native or clinical execution.
    reviewed = deepcopy(assignment)
    design = report["document"]["study"]["versions"][0]["studyDesigns"][0]
    target = next(timeline for timeline in design["scheduleTimelines"] if timeline.get("mainTimeline") is True)["instances"][1]
    reviewed["conditionTargetId"] = target["id"]
    assert validator.is_valid(reviewed)
    assert "conditionTargetId" not in assignment
    assert reviewed["condition"] == assignment["condition"]
