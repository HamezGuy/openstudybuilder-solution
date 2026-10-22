"""Synthetic graph evidence for bounded exact native version comparison."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from neo4j.time import DateTime

from clinical_mdr_api.domain_repositories.study_selections.study_version_snapshot_repository import (
    StudySnapshotGraph,
    StudyVersionSnapshotRepository,
)
from clinical_mdr_api.models.study_selections.study_version_snapshot import (
    StudyVersionDiff,
)
from clinical_mdr_api.services.studies.study_version_snapshot import (
    build_snapshot,
    compare_snapshots,
    typed_value,
)
from common.exceptions import ValidationException

INSTANT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def add_node(graph, identity, labels, **properties):
    graph.nodes[identity] = {"id": identity, "labels": labels, "properties": properties}


def add_edge(graph, source, target, kind, **properties):
    graph.edges.append(
        {
            "id": str(len(graph.edges)),
            "source": source,
            "target": target,
            "type": kind,
            "properties": properties,
        }
    )


def graph_fixture():
    graph = StudySnapshotGraph("Study_1", "1.0", "value")
    add_node(graph, "value", ["StudyValue"], study_number="SYNTHETIC")
    add_node(
        graph,
        "arm",
        ["StudyArm", "StudySelection"],
        uid="Arm_1",
        name="Synthetic arm",
        order=1,
    )
    add_node(
        graph,
        "epoch",
        ["StudyEpoch", "StudySelection"],
        uid="Epoch_1",
        status="Final",
        order=1,
    )
    add_node(
        graph,
        "field",
        ["StudyTextField", "StudyField"],
        field_name="study_title",
        value="Synthetic protocol",
    )
    for target, kind in (
        ("arm", "HAS_STUDY_ARM"),
        ("epoch", "HAS_STUDY_EPOCH"),
        ("field", "HAS_TEXT_FIELD"),
    ):
        add_edge(graph, "value", target, kind)
        graph.owners[target] = ["Study_1"]
    graph.version_states = [
        {"version": "1.0", "status": "LOCKED", "start_date": INSTANT}
    ]
    return graph


def test_exact_structure_is_independent_of_graph_ids_order_and_version_provenance():
    original = graph_fixture()
    revised = deepcopy(original)
    revised.version = "2.0"
    revised.version_states = [
        {
            "version": "2.0",
            "status": "RELEASED",
            "start_date": INSTANT,
            "author_id": "another-author",
        }
    ]
    renamed = {identity: "copied-" + identity for identity in revised.nodes}
    revised.nodes = {
        renamed[key]: {**value, "id": renamed[key]}
        for key, value in reversed(list(revised.nodes.items()))
    }
    revised.owners = {renamed[key]: value for key, value in revised.owners.items()}
    revised.value_id = renamed[revised.value_id]
    revised.edges = [
        {
            **edge,
            "id": "new-" + edge["id"],
            "source": renamed[edge["source"]],
            "target": renamed[edge["target"]],
        }
        for edge in reversed(revised.edges)
    ]
    result = compare_snapshots(build_snapshot(original), build_snapshot(revised))
    assert result.structurally_equal
    assert result.semantic_comparison_complete
    assert not result.changes
    assert result.before.structural_hash == result.after.structural_hash
    assert result.before.provenance != result.after.provenance
    assert StudyVersionDiff.model_validate_json(result.model_dump_json()) == result


def test_clinical_status_false_zero_null_and_missing_remain_distinct():
    before = graph_fixture()
    after = deepcopy(before)
    after.nodes["epoch"]["properties"]["status"] = "Retired"
    after.nodes["arm"]["properties"].update(
        number_of_subjects=0, enabled=False, reason=None, array=[]
    )
    result = compare_snapshots(build_snapshot(before), build_snapshot(after))
    assert not result.structurally_equal
    assert result.semantic_comparison_complete
    assert any(change.path.endswith("/status") for change in result.changes)
    addition = next(
        change for change in result.changes if change.path.endswith("/reason")
    )
    assert not addition.before_present and addition.after_present
    assert addition.after == {"type": "null", "value": None}
    assert any(
        change.after == {"type": "boolean", "value": False} for change in result.changes
    )
    assert any(
        change.after == {"type": "integer", "value": "0"} for change in result.changes
    )


def test_driver_property_types_and_full_precision_cannot_collapse():
    values = [
        1,
        1.0,
        "1",
        True,
        date(2026, 1, 1),
        "2026-01-01",
        DateTime(2026, 1, 1, nanosecond=123456789, tzinfo=timezone.utc),
        "2026-01-01T00:00:00.123456789+00:00",
        9007199254740993,
        9007199254740992,
    ]
    encoded = [typed_value(value) for value in values]
    assert len({repr(value) for value in encoded}) == len(values)
    assert encoded[-2]["value"] == "9007199254740993"
    assert ".123456789" in encoded[6]["value"]
    assert typed_value(-0.0) != typed_value(0.0)
    with pytest.raises(ValidationException, match="SERIALIZATION_UNSUPPORTED"):
        typed_value(float("nan"))
    with pytest.raises(ValidationException, match="SERIALIZATION_UNSUPPORTED"):
        typed_value(object())


def test_layout_and_semantic_edge_properties_and_multiplicity_are_content():
    before = graph_fixture()
    add_node(before, "conjunction", ["Conjunction"], string="and")
    add_edge(before, "arm", "conjunction", "HAS_CONJUNCTION", position=1, set_number=0)
    add_edge(
        before,
        "value",
        "arm",
        "HAS_PROTOCOL_SOA_CELL",
        row=1,
        column=2,
        span=1,
        is_propagated=False,
        order=1,
    )
    add_edge(
        before,
        "value",
        "arm",
        "HAS_PROTOCOL_SOA_CELL",
        row=1,
        column=2,
        span=1,
        is_propagated=False,
        order=1,
    )
    after = deepcopy(before)
    after.edges[-1]["properties"]["column"] = 3
    result = compare_snapshots(build_snapshot(before), build_snapshot(after))
    assert not result.structurally_equal
    assert (
        len(
            [
                edge
                for edge in result.before.content["relationships"]
                if edge["type"] == "HAS_PROTOCOL_SOA_CELL"
            ]
        )
        == 2
    )
    assert next(
        edge
        for edge in result.before.content["relationships"]
        if edge["type"] == "HAS_CONJUNCTION"
    )["properties"]["set_number"] == {"type": "integer", "value": "0"}
    assert any(change.path == "/relationships" for change in result.changes)


def test_unpinned_library_metadata_changes_do_not_enter_exact_hash_or_claim_equality():
    before = graph_fixture()
    add_node(before, "unit", ["UnitDefinitionRoot"], uid="Unit_1", mutable_label="days")
    add_edge(before, "arm", "unit", "HAS_UNIT", index=0)
    after = deepcopy(before)
    after.nodes["unit"]["properties"]["mutable_label"] = "a changed present-day label"
    result = compare_snapshots(build_snapshot(before), build_snapshot(after))
    assert result.structurally_equal
    assert not result.semantic_comparison_complete
    assert not result.changes
    assert result.before.observations != result.after.observations
    assert result.before.coverage_issues[0].code == "library_value_not_pinned"
    assert all(
        "mutable_label" not in record["properties"]
        for record in result.before.content["nodes"].values()
    )


def test_pinned_library_values_and_versions_are_content_without_future_history():
    before = graph_fixture()
    add_node(before, "library-value", ["ObjectiveValue"], name="Pinned objective")
    add_node(before, "library-root", ["ObjectiveRoot"], uid="Objective_1")
    add_edge(before, "arm", "library-value", "HAS_SELECTED_OBJECTIVE")
    add_edge(
        before,
        "library-root",
        "library-value",
        "HAS_VERSION",
        version="1.0",
        status="Final",
        start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    after = deepcopy(before)
    add_edge(
        after,
        "library-root",
        "library-value",
        "HAS_VERSION",
        version="2.0",
        status="Final",
        start_date=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    result = compare_snapshots(build_snapshot(before), build_snapshot(after))
    assert result.structurally_equal and result.semantic_comparison_complete
    value = next(
        row
        for row in result.before.content["nodes"].values()
        if "ObjectiveValue" in row["labels"]
    )
    assert value["library_pin"]["version"] == "1.0"
    after.nodes["library-value"]["properties"]["name"] = "Changed pinned value"
    assert not compare_snapshots(
        build_snapshot(before), build_snapshot(after)
    ).structurally_equal


@pytest.mark.parametrize(
    "corruption", ["membership", "identity", "foreign", "layout", "orphan"]
)
def test_ambiguous_or_foreign_selected_records_refuse(corruption):
    graph = graph_fixture()
    if corruption == "membership":
        add_edge(graph, "value", "arm", "HAS_STUDY_ARM")
    elif corruption == "identity":
        add_node(graph, "other-arm", ["StudyArm", "StudySelection"], uid="Arm_1")
        graph.owners["other-arm"] = ["Study_1"]
        add_edge(graph, "value", "other-arm", "HAS_STUDY_ARM")
    elif corruption == "foreign":
        graph.owners["arm"] = ["OtherStudy", "Study_1"]
    else:
        add_node(graph, "other-arm", ["StudyArm", "StudySelection"], uid="Arm_2")
        add_edge(
            graph,
            "value" if corruption == "layout" else "arm",
            "other-arm",
            "HAS_PROTOCOL_SOA_CELL" if corruption == "layout" else "CONNECTED_TO",
        )
    with pytest.raises(ValidationException, match="STUDY_SNAPSHOT_"):
        build_snapshot(graph)


def test_copied_navigation_keeps_only_exact_selected_target_and_detects_orphan():
    before = graph_fixture()
    add_node(before, "branch", ["StudyBranchArm", "StudySelection"], uid="Branch_1")
    before.owners["branch"] = ["Study_1"]
    add_edge(before, "value", "branch", "HAS_STUDY_BRANCH_ARM")
    add_edge(before, "arm", "branch", "STUDY_ARM_HAS_BRANCH_ARM")
    after = deepcopy(before)
    add_node(
        after,
        "old-branch",
        ["StudyBranchArm", "StudySelection"],
        uid="Branch_1",
        name="Historical",
    )
    after.owners["old-branch"] = ["Study_1"]
    add_edge(after, "arm", "old-branch", "STUDY_ARM_HAS_BRANCH_ARM")
    assert compare_snapshots(
        build_snapshot(before), build_snapshot(after)
    ).structurally_equal
    assert {
        "element_id": after.edges[-1]["id"],
        "reason": "other_study_version_revision",
    } in build_snapshot(after).provenance["excluded_edges"]
    # Without the selected edge the version honestly has no arm link. The
    # revision owned by another version never substitutes for it.
    unlinked = deepcopy(after)
    unlinked.edges = [
        edge
        for edge in unlinked.edges
        if not (edge["source"] == "arm" and edge["target"] == "branch")
    ]
    diff = compare_snapshots(build_snapshot(before), build_snapshot(unlinked))
    assert not diff.structurally_equal
    assert [(change.path, change.kind) for change in diff.changes] == [
        ("/relationships", "changed")
    ]
    assert "STUDY_ARM_HAS_BRANCH_ARM" in str(diff.changes[0].before)
    assert "STUDY_ARM_HAS_BRANCH_ARM" not in str(diff.changes[0].after)
    for owners in ([], ["OtherStudy"], ["OtherStudy", "Study_1"]):
        orphan = deepcopy(after)
        if owners:
            orphan.owners["old-branch"] = owners
        else:
            del orphan.owners["old-branch"]
        with pytest.raises(ValidationException, match="TARGET_NOT_SELECTED"):
            build_snapshot(orphan)


def test_shared_arm_cohort_reassignment_is_a_relationship_only_change():
    """A shared unchanged arm keeps copied edges to another version's cohort."""
    base = graph_fixture()
    add_node(base, "arm-2", ["StudyArm", "StudySelection"], uid="Arm_2", order=2)
    base.owners["arm-2"] = ["Study_1"]
    add_edge(base, "value", "arm-2", "HAS_STUDY_ARM")
    old_cohort = {"uid": "Cohort_1", "name": "Synthetic cohort", "order": 1}
    add_node(base, "cohort-v1", ["StudyCohort", "StudySelection"], **old_cohort)
    add_node(base, "cohort-v2", ["StudyCohort", "StudySelection"], **old_cohort)
    base.owners["cohort-v1"] = base.owners["cohort-v2"] = ["Study_1"]
    add_edge(base, "arm", "cohort-v1", "STUDY_ARM_HAS_COHORT")
    add_edge(base, "arm-2", "cohort-v2", "STUDY_ARM_HAS_COHORT")
    first, second = deepcopy(base), deepcopy(base)
    add_edge(first, "value", "cohort-v1", "HAS_STUDY_COHORT")
    add_edge(second, "value", "cohort-v2", "HAS_STUDY_COHORT")
    before, after = build_snapshot(first), build_snapshot(second)
    cohort_key = "selection/StudyCohort/Cohort_1"
    assert before.content["nodes"][cohort_key] == after.content["nodes"][cohort_key]
    links = [
        [
            row
            for row in snapshot.content["relationships"]
            if row["type"] == "STUDY_ARM_HAS_COHORT"
        ]
        for snapshot in (before, after)
    ]
    assert [row["source"] for row in links[0]] == ["selection/StudyArm/Arm_1"]
    assert [row["source"] for row in links[1]] == ["selection/StudyArm/Arm_2"]
    diff = compare_snapshots(before, after)
    assert not diff.structurally_equal
    assert {change.kind for change in diff.changes} == {"changed"}
    assert all(change.path == "/relationships" for change in diff.changes)


def test_ct_context_requires_both_exact_identities():
    graph = graph_fixture()
    add_node(graph, "context", ["CTTermContext"])
    add_node(graph, "term", ["CTTermRoot"], uid="Term_1")
    add_edge(graph, "arm", "context", "HAS_ARM_TYPE")
    add_edge(graph, "context", "term", "HAS_SELECTED_TERM")
    with pytest.raises(ValidationException, match="CT_CONTEXT_INVALID"):
        build_snapshot(graph)
    add_node(graph, "codelist", ["CTCodelistRoot"], uid="Codelist_1")
    add_edge(graph, "context", "codelist", "HAS_SELECTED_CODELIST")
    report = build_snapshot(graph)
    assert len(report.coverage_issues) == 2
    assert any(
        row["type"] == "HAS_SELECTED_CODELIST"
        for row in report.content["relationships"]
    )


def test_auxiliary_cycle_refuses_without_recursive_graph_walk():
    graph = graph_fixture()
    add_node(graph, "one", ["Conjunction"], string="one")
    add_node(graph, "two", ["Conjunction"], string="two")
    add_edge(graph, "arm", "one", "HAS_CONJUNCTION")
    add_edge(graph, "one", "two", "HAS_CONJUNCTION")
    add_edge(graph, "two", "one", "HAS_CONJUNCTION")
    with pytest.raises(ValidationException, match="DEPENDENCY_CYCLE"):
        build_snapshot(graph)


def test_parent_pin_and_incoming_project_reference_are_preserved():
    graph = graph_fixture()
    add_node(graph, "parent", ["StudyValue"], description="Unpinned external content")
    graph.external_versions["parent"] = [
        {
            "study_uid": "Parent_1",
            "state": {"version": "3.0", "status": "LOCKED", "start_date": INSTANT},
        }
    ]
    add_edge(graph, "parent", "value", "HAS_STUDY_SUBPART")
    add_node(graph, "project-field", ["StudyField", "StudyProjectField"])
    graph.owners["project-field"] = ["Study_1"]
    add_edge(graph, "value", "project-field", "HAS_PROJECT")
    add_node(graph, "project", ["Project"], uid="Project_1", name="Current project")
    add_edge(graph, "project", "project-field", "HAS_FIELD")
    report = build_snapshot(graph)
    assert any(
        row["type"] == "HAS_STUDY_SUBPART" for row in report.content["relationships"]
    )
    assert any(row["type"] == "HAS_FIELD" for row in report.content["relationships"])
    assert "Unpinned external content" not in str(report.content)
    assert "study_version/Parent_1/3.0" in report.content["nodes"]
    graph.external_versions["parent"].append(
        {"study_uid": "ForeignParent", "state": {"version": "4.0"}}
    )
    with pytest.raises(ValidationException, match="EXTERNAL_OWNER_AMBIGUOUS"):
        build_snapshot(graph)


def test_external_pin_keeps_exact_value_immutable_version_despite_draft_history():
    graph = graph_fixture()
    add_node(graph, "child", ["StudyValue"])
    add_edge(graph, "value", "child", "HAS_STUDY_SUBPART")
    graph.external_versions["child"] = [
        {
            "study_uid": "Child_1",
            "state": {
                "version": version,
                "status": status,
                "start_date": start,
            },
        }
        for version, status, start in (
            ("0.1", "DRAFT", INSTANT - timedelta(days=1)),
            ("1.0", "RELEASED", INSTANT),
            ("1.0", "LOCKED", INSTANT),
        )
    ]
    before = build_snapshot(graph)
    graph.external_versions["child"].append(
        {
            "study_uid": "Child_1",
            "state": {
                "version": "2.0",
                "status": "LOCKED",
                "start_date": INSTANT + timedelta(days=1),
            },
        }
    )
    after = build_snapshot(graph)
    assert "study_version/Child_1/1.0" in after.content["nodes"]
    result = compare_snapshots(before, after)
    assert result.structurally_equal and result.semantic_comparison_complete
    assert before.provenance != after.provenance


@pytest.mark.parametrize("statuses", [("DRAFT",), ("RELEASED", "LOCKED")])
def test_unresolved_external_pin_is_explicit_without_hashing_candidate_history(
    statuses,
):
    graph = graph_fixture()
    add_node(graph, "child", ["StudyValue"])
    add_edge(graph, "value", "child", "HAS_STUDY_SUBPART")
    graph.external_versions["child"] = [
        {
            "study_uid": "Child_1",
            "state": {
                "version": str(index),
                "status": status,
                "start_date": INSTANT,
            },
        }
        for index, status in enumerate(statuses)
    ]
    before = build_snapshot(graph)
    assert "study_reference/Child_1" in before.content["nodes"]
    assert before.coverage_issues[0].code == "external_study_version_not_pinned"
    graph.external_versions["child"].append(
        {
            "study_uid": "Child_1",
            "state": {
                "version": "future",
                "status": "LOCKED",
                "start_date": INSTANT + timedelta(days=1),
            },
        }
    )
    result = compare_snapshots(before, build_snapshot(graph))
    assert result.structurally_equal
    assert not result.semantic_comparison_complete


def test_mutable_version_cannot_claim_an_exact_multi_query_snapshot():
    graph = graph_fixture()
    graph.version_states[0]["status"] = "DRAFT"
    with pytest.raises(ValidationException, match="IMMUTABLE_VERSION_REQUIRED"):
        build_snapshot(graph)


def test_amendment_and_lock_state_are_provenance_only():
    before = graph_fixture()
    add_node(
        before,
        "version-record",
        ["StudyVersion", "StudySelection"],
        uid="Version_1",
        amendment_json='{"synthetic":true}',
    )
    add_edge(before, "value", "version-record", "HAS_STUDY_VERSION")
    after = deepcopy(before)
    after.nodes["version-record"]["properties"][
        "amendment_json"
    ] = '{"synthetic":false}'
    result = compare_snapshots(build_snapshot(before), build_snapshot(after))
    assert result.structurally_equal
    assert (
        result.before.provenance["study_version_records"]
        != result.after.provenance["study_version_records"]
    )


def test_unknown_attachment_is_visible_and_incomplete():
    graph = graph_fixture()
    add_node(graph, "future", ["FutureRoot"], uid="Future_1", clinical="unknown")
    add_edge(graph, "value", "future", "HAS_FUTURE_SELECTION")
    report = build_snapshot(graph)
    assert any(
        issue.code == "attachment_not_supported" for issue in report.coverage_issues
    )
    assert any(
        row["type"] == "HAS_FUTURE_SELECTION" for row in report.content["relationships"]
    )


def test_query_bound_is_explicit_not_truncated_success(monkeypatch):
    monkeypatch.setattr(
        "clinical_mdr_api.domain_repositories.study_selections.study_version_snapshot_repository.MAX_SNAPSHOT_EDGES",
        1,
    )
    monkeypatch.setattr(
        "clinical_mdr_api.domain_repositories.study_selections.study_version_snapshot_repository.db.cypher_query",
        lambda *args: ([[1], [2]], ["value"]),
    )
    with pytest.raises(ValidationException, match="BOUND_EXCEEDED"):
        StudyVersionSnapshotRepository._query("synthetic", {})
