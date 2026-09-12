"""Independent study/epoch scopes become explicit editable pending decisions."""

from copy import deepcopy

import pytest

from clinical_mdr_api.models.study_selections.study_epoch import StudyEpoch
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import STUDY_UID, VERSION, native_study_graph
from clinical_mdr_api.tests.unit.services.test_usdm_cell_transitions import at


def run(graph, monkeypatch):
    source = NativeStudySource(graph)
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    return source.mapper().map_with_report(graph["study"], VERSION)


def test_two_epochs_and_global_stop_keep_distinct_native_narratives_and_edit_targets(monkeypatch):
    graph = native_study_graph()
    graph["study"].current_metadata.high_level_study_design.study_stop_rules = "Stop for safety; preserve 0. "
    first = graph["epochs"][0]
    first.start_rule, first.end_rule = "First epoch entry", "First epoch stop"
    second = first.model_dump(mode="json")
    second.update(uid="Epoch_2", order=2, start_rule="Second epoch entry", end_rule="Second epoch stop")
    graph["epochs"].append(StudyEpoch(**second))
    before = deepcopy(graph["study"].model_dump(mode="json"))
    report = run(graph, monkeypatch)
    reviews = [row["executionReview"] for row in report["mappingReport"]["issues"]
               if row["code"] == "USDM_SCOPED_TRANSITION_EXECUTION_REVIEW_REQUIRED"]
    assert len(reviews) == 5
    assert len({row["draftTargets"]["conditionAssignmentId"] for row in reviews}) == 5
    narratives = set()
    for review in reviews:
        assert review["sourceScope"]["studyUid"] == STUDY_UID
        assert review["sourceScope"]["studyValueVersion"] == VERSION
        decision = at(report["document"], review["documentPointers"]["decision"])
        timeline = at(report["document"], review["documentPointers"]["timeline"])
        assignment = decision["conditionAssignments"][0]
        narratives.add(assignment["condition"])
        assert "conditionTargetId" not in assignment
        assert decision.get("defaultConditionId") is None
        assert not {"mainTimeline", "entryId", "entryCondition"} & timeline.keys()
        assert "reviewed-executable-predicate" in review["requiredBindings"]
        if review["kind"] == "study-stop":
            assert review["canonicalScope"] == {}
        else:
            assert review["canonicalScope"]["epochId"] == decision["epochId"]
    assert narratives == {"Stop for safety; preserve 0. ", "First epoch entry", "First epoch stop",
                          "Second epoch entry", "Second epoch stop"}
    design = report["document"]["study"]["versions"][0]["studyDesigns"][0]
    assert len(design["elements"]) == 1
    assert design["elements"][0]["transitionEndRule"]["text"] == "Treatment completed"
    assert graph["study"].model_dump(mode="json") == before


@pytest.mark.parametrize("value", [None, ""])
def test_absent_study_and_epoch_rules_do_not_create_predicates(monkeypatch, value):
    graph = native_study_graph()
    graph["study"].current_metadata.high_level_study_design.study_stop_rules = value
    graph["epochs"][0].start_rule = value
    graph["epochs"][0].end_rule = value
    report = run(graph, monkeypatch)
    assert not any(row["code"] == "USDM_SCOPED_TRANSITION_EXECUTION_REVIEW_REQUIRED"
                   for row in report["mappingReport"]["issues"])
