"""Actual history selection/projection with native graph storage replaced."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json, USDMMappingAuthorityRequired
from clinical_mdr_api.services.studies.study_activity_instance_snapshot import (
    read_study_activity_instance_definition, resolve_candidate_class_history, _selected_value,
)
from clinical_mdr_api.services.studies.study_compound_snapshot import StudyCompoundSourceError
from clinical_mdr_api.tests.fixtures.usdm_native_activity import NativeActivitySource
from clinical_mdr_api.tests.fixtures.usdm_native_compound import Relations, Value
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource, native_odm_graph
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION


def test_exact_selected_instance_retains_dated_class_codelist_unit_and_metadata():
    source = NativeActivitySource()
    before = native_json(source.source_input())
    result = source.definition("LibraryInstance_1", "1.0")
    item = result["activity_items"][0]
    assert item["activity_item_class"]["source"]["properties"]["name"] == "Result"
    assert item["activity_item_class"]["source"]["version"] == "1.0"
    assert item["text_value"] == "" and item["is_adam_param_specific"] is False
    assert item["is_activity_instance_id_specific"] is None
    assert item["unit_definitions"][0]["source"]["version"] == "1.0"
    assert item["ct_terms"][0]["codelist"]["terms"][0]["properties"]["submission_value"] == "A"
    assert item["codelists"] == []
    assert result["nativeSnapshot"]["issues"] == []
    assert "UNSELECTED" not in str(result)
    assert native_json(source.source_input()) == before


def test_native_whole_codelist_alternative_retains_all_exact_memberships():
    source = NativeActivitySource()
    source.context("CHOICE_B", "NativeChoices", "B")
    source.item.has_ct_term = Relations()
    source.item.has_codelist = Relations(source.codelists["NativeChoices"])
    result = source.definition("LibraryInstance_1", "1.0")["activity_items"][0]
    assert result["ct_terms"] == []
    assert [member["properties"]["submission_value"]
            for member in result["codelists"][0]["terms"]] == ["A", "B"]


@pytest.mark.parametrize("kind,uid", [
    ("activityItemClass", "ItemClass_1"),
    ("ctCodelistName", "NativeChoices"),
    ("unitDefinition", "Unit_1"),
])
def test_later_nested_publication_cannot_change_the_old_selected_definition(kind, uid):
    source = NativeActivitySource()
    before = source.definition("LibraryInstance_1", "1.0")
    later = source.library.roots[kind, uid].has_version.rows[1][0]
    later.__properties__.update(name="Changed again", definition="Later unpublished values")
    assert source.definition("LibraryInstance_1", "1.0") == before


def test_ambiguous_historical_class_is_missing_but_both_candidate_values_are_retained():
    source = NativeActivitySource()
    source.item_class.has_version.rows[0][1].end_date = None
    source.item_class.has_version.rows[1][1].start_date = AS_OF - timedelta(hours=1)
    result = source.definition("LibraryInstance_1", "1.0")
    assert result["activity_items"][0]["activity_item_class"]["source"] is None
    evidence = next(row for row in result["nativeSnapshot"]["records"]
                    if row["kind"] == "activityItemClass" and row.get("state") == "unresolved")
    assert len(evidence["candidates"]) == 2
    assert result["nativeSnapshot"]["issues"]


def test_actual_selected_query_binds_study_selection_and_outer_value(monkeypatch):
    source = NativeActivitySource()
    calls = []
    def query(text, params):
        calls.append((text, deepcopy(params)))
        assert "selection:StudyActivityInstance {uid:$selection_uid}" in text
        assert "study_version.version=$study_value_version" in text
        assert "study_version.start_date=$as_of" in text
        assert params["as_of"] == AS_OF
        assert "LATEST" not in text
        return [[
            "native-study-value-2", "native-selection-1",
            source.instance.has_version.rows[0][0].element_id, {"uid": "InstanceSelection_1"},
        ]], []
    monkeypatch.setattr("clinical_mdr_api.services.studies.study_activity_instance_snapshot.db.cypher_query", query)
    monkeypatch.setattr("clinical_mdr_api.services.studies.study_activity_instance_snapshot.ActivityInstanceRoot",
                        SimpleNamespace(nodes=SimpleNamespace(get_or_none=lambda uid: source.instance)))
    with source.isolated():
        result = read_study_activity_instance_definition(
            "LibraryInstance_1", "1.0", study_uid=STUDY_UID, study_value_version=VERSION,
            study_activity_instance_uid="InstanceSelection_1", as_of=AS_OF,
        )
    assert result["nativeSnapshot"]["selection"]["studyValueIdentity"] == "native-study-value-2"
    assert calls[0][1]["selection_uid"] == "InstanceSelection_1"


def test_candidate_metadata_uses_same_historical_class_value(monkeypatch):
    source = NativeActivitySource()
    monkeypatch.setattr("clinical_mdr_api.services.studies.study_activity_instance_snapshot.ActivityItemClassRoot",
                        SimpleNamespace(nodes=SimpleNamespace(get_or_none=lambda uid: source.item_class)))
    rows = [{"activity_item": {"activityItemClassUid": "ItemClass_1"}}]
    result = resolve_candidate_class_history(rows, STUDY_UID, VERSION, snapshot=source.snapshot())[0]["activity_item"]
    assert result["activityItemClassName"] == "Result label"
    assert result["activityItemClassDefinition"] == "Exact class definition. "
    assert result["activityItemClassSource"]["version"] == "1.0"


def test_selected_grouping_child_can_have_multiple_state_edges_to_one_parent():
    source = NativeActivitySource()
    root = source.root("activity", "Activity_1", {"name": "Selected activity"})
    value = root.has_version.rows[0][0]
    value.has_version = Relations(root, root)
    evidence = _selected_value(source.snapshot(), value, "activity")
    assert evidence["uid"] == "Activity_1"
    assert evidence["valueIdentity"] == value.element_id and evidence["version"] == "1.0"
    other = source.root("activity", "Activity_2", {"name": "Another parent"})
    value.has_version = Relations(root, other)
    snapshot = source.snapshot()
    assert _selected_value(snapshot, value, "activity") is None
    assert snapshot.issues[0]["reason"] == "STUDY_LIBRARY_SELECTED_PARENT_NOT_UNIQUE"


def test_actual_mapper_exposes_typed_property_candidates_without_inventing_clinical_facts(monkeypatch):
    native = NativeActivitySource()
    source = NativeStudySource()
    source.definition = native.definition
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    report = source.mapper().map_with_report(source.graph["study"], VERSION)
    version = report["document"]["study"]["versions"][0]
    concept = version["biomedicalConcepts"][0]
    prop = concept["properties"][0]
    assert prop["name"] == "Result" and prop["label"] == "Result label"
    assert prop["datatype"] == "float"
    assert "isRequired" not in prop and "isEnabled" not in prop and "code" not in prop
    assert len(prop["responseCodes"]) == 1
    assert prop["responseCodes"][0]["name"] == "A"
    assert "isEnabled" not in prop["responseCodes"][0]
    review = next(issue["executionReview"] for issue in report["mappingReport"]["issues"]
                  if issue["code"] == "USDM_PROPERTY_ACQUISITION_REVIEW_REQUIRED")
    assert review["canonicalScope"]["propertyId"] == prop["id"]
    assert review["sourceScope"]["activityItemClassVersion"] == "1.0"
    assert review["sourceScope"]["activityItemIdentity"] == "native-activity-item-1"
    assert report["mappingReport"]["state"] == "incomplete"


@pytest.mark.parametrize("scope,expected", [
    ("selected-term", ["A"]),
    ("selected-term-same-label", ["A"]),
    ("selected-terms", ["A", "B"]),
    ("whole-codelist", ["A", "B"]),
])
def test_mapper_response_candidates_preserve_exact_native_selection_scope(monkeypatch, scope, expected):
    native = NativeActivitySource()
    second = native.context("CHOICE_B", "NativeChoices", "B")
    if scope == "selected-term-same-label":
        native.library.roots["ctTermName", "CHOICE_B"].has_version.rows[0][0].__properties__["name"] = "A"
    elif scope == "selected-terms":
        native.item.has_ct_term = Relations(native.choice, second)
    elif scope == "whole-codelist":
        native.item.has_ct_term = Relations()
        native.item.has_codelist = Relations(native.codelists["NativeChoices"])
    before = native_json(native.source_input())
    source = NativeStudySource()
    source.definition = native.definition
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    report = source.mapper().map_with_report(source.graph["study"], VERSION)
    prop = report["document"]["study"]["versions"][0]["biomedicalConcepts"][0]["properties"][0]
    assert [response["name"] for response in prop["responseCodes"]] == expected
    assert "isRequired" not in prop and "isEnabled" not in prop
    assert all("isEnabled" not in response for response in prop["responseCodes"])
    assert report["mappingReport"]["state"] == "incomplete"
    assert any(issue["code"] == "USDM_PROPERTY_ACQUISITION_REVIEW_REQUIRED"
               for issue in report["mappingReport"]["issues"])
    assert not any(issue["code"] == "USDM_ACQUISITION_SELECTED_RESPONSE_MEMBERSHIP_REQUIRED"
                   for issue in report["mappingReport"]["issues"])
    retained = next(entry["record"] for entry in report["nativeRecords"]
                    if entry["kind"] == "activityInstanceDefinition")["activity_items"][0]
    if scope == "whole-codelist":
        assert retained["ct_terms"] == []
        codelist = retained["codelists"][0]
    else:
        assert retained["codelists"] == []
        assert [entry["term"]["uid"] for entry in retained["ct_terms"]] == (
            ["CHOICE_A", "CHOICE_B"] if scope == "selected-terms" else ["CHOICE_A"]
        )
        codelist = retained["ct_terms"][0]["codelist"]
    # The wider dated context remains custody, even when only A was selected.
    assert [member["term"]["uid"] for member in codelist["terms"]] == ["CHOICE_A", "CHOICE_B"]
    assert native_json(native.source_input()) == before


@pytest.mark.parametrize("change", [
    "missing-membership", "multiple-memberships", "ambiguous-membership-history",
])
def test_selected_response_with_unresolved_membership_never_uses_other_codelist_terms(monkeypatch, change):
    native = NativeActivitySource()
    native.context("CHOICE_B", "NativeChoices", "B")
    memberships = native.codelists["NativeChoices"].has_term
    member, state = memberships.rows[0]
    if change == "missing-membership":
        memberships.nodes = [value for value in memberships.nodes if value is not member]
        memberships.rows = [row for row in memberships.rows if row[0] is not member]
    elif change == "multiple-memberships":
        duplicate = Value("native-conflicting-membership", submission_value="alternate A")
        duplicate.has_term_root = Relations(native.terms["CHOICE_A"])
        memberships.nodes.append(duplicate)
        memberships.rows.append((duplicate, deepcopy(state)))
    else:
        conflicting_state = deepcopy(state)
        conflicting_state.start_date = AS_OF - timedelta(days=1)
        conflicting_state.__properties__["start_date"] = conflicting_state.start_date
        memberships.rows.append((member, conflicting_state))
    before = native_json(native.source_input())
    source = NativeStudySource()
    source.definition = native.definition
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    report = source.mapper().map_with_report(source.graph["study"], VERSION)
    prop = report["document"]["study"]["versions"][0]["biomedicalConcepts"][0]["properties"][0]
    assert prop["responseCodes"] == []
    assert "isRequired" not in prop and "isEnabled" not in prop
    issue = next(issue for issue in report["mappingReport"]["issues"]
                 if issue["code"] == "USDM_ACQUISITION_SELECTED_RESPONSE_MEMBERSHIP_REQUIRED")
    assert issue["sourcePath"].endswith("/activity_items/0/ct_terms/0")
    assert report["mappingReport"]["state"] == "incomplete"
    retained = next(entry["record"] for entry in report["nativeRecords"]
                    if entry["kind"] == "activityInstanceDefinition")["activity_items"][0]
    assert retained["ct_terms"][0]["term"]["uid"] == "CHOICE_A"
    context = retained["ct_terms"][0]["codelist"]
    assert any(row["term"]["uid"] == "CHOICE_B" for row in context["terms"])
    if change == "multiple-memberships":
        assert {row["memberIdentity"] for row in context["terms"]
                if row["term"]["uid"] == "CHOICE_A"} == {
            member.element_id, "native-conflicting-membership",
        }
    elif change == "ambiguous-membership-history":
        assert context["unresolvedMemberships"][0]["memberIdentity"] == member.element_id
        assert len(context["unresolvedMemberships"][0]["relationships"]) == 2
    assert native_json(native.source_input()) == before


def test_conflicting_native_whole_and_selected_scopes_are_retained_for_review_without_union(monkeypatch):
    native = NativeActivitySource()
    native.context("CHOICE_B", "NativeChoices", "B")
    native.item.has_codelist = Relations(native.codelists["NativeChoices"])
    source = NativeStudySource()
    source.definition = native.definition
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    report = source.mapper().map_with_report(source.graph["study"], VERSION)
    prop = report["document"]["study"]["versions"][0]["biomedicalConcepts"][0]["properties"][0]
    assert prop["responseCodes"] == []
    assert any(issue["code"] == "USDM_ACQUISITION_RESPONSE_SCOPE_CONFLICT"
               for issue in report["mappingReport"]["issues"])
    retained = next(entry["record"] for entry in report["nativeRecords"]
                    if entry["kind"] == "activityInstanceDefinition")["activity_items"][0]
    assert retained["ct_terms"][0]["term"]["uid"] == "CHOICE_A"
    assert [row["term"]["uid"] for row in retained["codelists"][0]["terms"]] == ["CHOICE_A", "CHOICE_B"]
    assert report["mappingReport"]["state"] == "incomplete"


@pytest.mark.parametrize("datatype", ["partialDate", "durationDatetime", "base64Binary", "FLOAT"])
def test_native_machine_datatype_is_not_lost_or_replaced_by_a_display_label(monkeypatch, datatype):
    native = NativeActivitySource()
    member = native.codelists["NativeDataTypes"].has_term.rows[0][0]
    member.__properties__["submission_value"] = datatype
    source = NativeStudySource()
    source.definition = native.definition
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    report = source.mapper().map_with_report(source.graph["study"], VERSION)
    prop = report["document"]["study"]["versions"][0]["biomedicalConcepts"][0]["properties"][0]
    assert prop["datatype"] == datatype
    assert report["mappingReport"]["state"] == "incomplete"


@pytest.mark.parametrize("whole_codelist", [False, True])
def test_actual_export_uses_native_snapshot_mapping_and_retains_same_class_evidence(whole_codelist):
    source = NativeStudySource()
    source.native_activity.context("CHOICE_B", "NativeChoices", "B")
    if whole_codelist:
        source.native_activity.item.has_ct_term = Relations()
        source.native_activity.item.has_codelist = Relations(source.native_activity.codelists["NativeChoices"])
    source.definition = source.native_activity.definition
    bundle = source.export(native_odm_graph())
    prop = bundle["definition"]["document"]["study"]["versions"][0]["biomedicalConcepts"][0]["properties"][0]
    assert prop["datatype"] == "float"
    assert "isRequired" not in prop
    assert [response["name"] for response in prop["responseCodes"]] == (
        ["A", "B"] if whole_codelist else ["A"]
    )
    assert all("isEnabled" not in response for response in prop["responseCodes"])
    assert bundle["execution"]["forms"]["forms"]
    assert bundle["execution"]["visitFormAssignments"] == []
    assert bundle["extensions"]["_osbExport"]["mappingReport"]["state"] == "incomplete"


@pytest.mark.parametrize("changed", ["selection", "timestamp"])
def test_mismatched_snapshot_selection_cannot_be_replayed_as_the_current_definition(monkeypatch, changed):
    source = NativeStudySource()
    real = source.native_activity.definition
    def stale(uid, version=None, **kwargs):
        result = real(uid, version, **kwargs)
        if changed == "selection":
            result["nativeSnapshot"]["selection"]["properties"]["uid"] = "AnotherSelection"
        else:
            result["nativeSnapshot"]["asOf"] = (AS_OF + timedelta(seconds=1)).isoformat()
        return result
    source.definition = stale
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    with pytest.raises(USDMMappingAuthorityRequired, match="SOURCE_SCOPE_MISMATCH"):
        source.mapper().map_with_report(source.graph["study"], VERSION)


@pytest.mark.parametrize("rows", [[], [
    ["value-A", "selection-A", "instance-A", {"uid": "InstanceSelection_1"}],
    ["value-B", "selection-B", "instance-B", {"uid": "InstanceSelection_1"}],
]])
def test_missing_or_ambiguous_selected_path_cannot_fall_back_to_current(monkeypatch, rows):
    monkeypatch.setattr("clinical_mdr_api.services.studies.study_activity_instance_snapshot.db.cypher_query",
                        lambda *_args, **_kwargs: (rows, []))
    with pytest.raises(StudyCompoundSourceError, match="SELECTED_PATH_NOT_UNIQUE"):
        read_study_activity_instance_definition(
            "LibraryInstance_1", "1.0", study_uid=STUDY_UID, study_value_version=VERSION,
            study_activity_instance_uid="InstanceSelection_1", as_of=AS_OF,
        )
