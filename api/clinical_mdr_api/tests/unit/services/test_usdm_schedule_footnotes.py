"""Native schedule-only annotations through actual mapper/export code.

Repository reads are isolated. These tests do not claim native Cypher or UI
execution; the exact source schedule/instance identities are explicit fixtures.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from clinical_mdr_api.models.study_selections.study_selection import (
    ReferencedItem,
    StudyActivitySchedule,
    StudySelectionActivity,
)
from clinical_mdr_api.models.study_selections.study_visit import StudyVisit
from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
    native_json,
)
from clinical_mdr_api.services.ddf.usdm_native_mapping import (
    SCHEDULE_FOOTNOTE_SCOPE_REQUIRED,
    SCHEDULE_FOOTNOTE_SCOPE_URL,
)
from clinical_mdr_api.services.ddf.usdm_service import NativeVisitSnapshot
from clinical_mdr_api.services.integrations.edc_study_exchange import verify_source_exchange
from clinical_mdr_api.services.integrations.edc_native_study_records import (
    collect_study_native_records,
    NativeStudyRecordError,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource, native_odm_graph
from clinical_mdr_api.tests.fixtures.usdm_native_study import native_study_graph, STUDY_UID, VERSION


TEXT = "Use <i>local</i> sample handling."
SCHEMA = Path(__file__).parents[2] / "fixtures/usdm_native_model4_schema.json"


class ScheduleSource(NativeStudySource):
    def definition(self, uid, version=None):
        self.calls.append(("definition", uid, version))
        definitions = self.graph.get("definitions", {self.graph["definition"].uid: self.graph["definition"]})
        result = definitions[uid]
        assert result.version == version
        return result


def schedule_only():
    graph = native_study_graph()
    # The actual repository emits the SAME schedule UID for planning and its
    # operational instance expansion; it does not mint an operational UID.
    graph["operational"][0].study_activity_schedule_uid = "Plan_1"
    graph["footnotes"][0].referenced_items = [
        ReferencedItem(item_type="StudyActivitySchedule", item_uid="Plan_1")
    ]
    return graph


def map_graph(graph, monkeypatch):
    source = ScheduleSource(graph)
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    report = source.mapper().map_with_report(graph["study"], VERSION)
    return report, source


def annotation(document):
    return next(note for note in document["study"]["versions"][0]["notes"] if note["text"] == TEXT)


def scopes(document):
    return [
        item for item in annotation(document)["extensionAttributes"]
        if item["url"] == SCHEDULE_FOOTNOTE_SCOPE_URL
    ]


def scope_values(scope):
    fields = {
        child["url"][len(SCHEDULE_FOOTNOTE_SCOPE_URL) + 1:]: child
        for child in scope["extensionAttributes"]
        if child["url"].startswith(SCHEDULE_FOOTNOTE_SCOPE_URL + "/")
    }
    return {
        **{name: item["valueId"] for name, item in fields.items() if "valueId" in item},
        "activityIds": [item["valueId"] for item in fields["activityIds"]["extensionAttributes"]],
    }


def assert_no_broad_annotation(document):
    design = document["study"]["versions"][0]["studyDesigns"][0]
    assert all(note["text"] != TEXT for item in design["activities"] for note in item["notes"])
    assert all(note["text"] != TEXT for item in design["encounters"] for note in item["notes"])


def assert_unresolved(report):
    assert scopes(report["document"]) == []
    assert any(issue["code"] == SCHEDULE_FOOTNOTE_SCOPE_REQUIRED for issue in report["mappingReport"]["issues"])
    assert annotation(report["document"])["text"] == TEXT
    assert report["mappingReport"]["state"] == "incomplete"
    assert_no_broad_annotation(report["document"])


def assert_native_timing_anchors(report):
    expected = {"Visit_1": "Visit_1", "Visit_2": "Visit_1", "Visit_3": "Visit_2"}
    retained = {entry["uid"]: entry["record"]["resolved_anchor_visit_uid"]
                for entry in report["nativeRecords"] if entry["kind"] == "studyVisit"}
    assert retained == expected
    timeline = report["document"]["study"]["versions"][0]["studyDesigns"][0]["scheduleTimelines"][0]
    instances = {}
    for item in timeline["instances"]:
        source = next(extension for extension in item["extensionAttributes"]
                      if extension["url"].endswith("/native/scheduledVisit"))
        fields = {child["url"].rsplit("/", 1)[-1]: child for child in source["extensionAttributes"]}
        uid = fields["uid"]["valueString"]
        assert fields["resolved_anchor_visit_uid"]["valueString"] == expected[uid]
        instances[uid] = item["id"]
    assert set(instances) == set(expected)
    assert len(timeline["timings"]) == len(expected)
    assert {(timing["relativeFromScheduledInstanceId"], timing["relativeToScheduledInstanceId"])
            for timing in timeline["timings"]} == {
        (instances[uid], instances[anchor]) for uid, anchor in expected.items()
    }
    assert not any(issue["code"] == "USDM_VISIT_TIMING_ANCHOR_UNRESOLVED"
                   for issue in report["mappingReport"]["issues"])


def two_instances():
    graph = schedule_only()
    second = graph["instances"][0].model_copy(deep=True)
    second.study_activity_instance_uid = "InstanceSelection_2"
    second.activity_instance.uid = "LibraryInstance_2"
    second.latest_activity_instance.uid = "LibraryInstance_2"
    # Equal selected labels cannot collapse either native identity.
    definition = graph["definition"].model_copy(deep=True)
    definition.uid = "LibraryInstance_2"
    graph["definitions"] = {
        graph["definition"].uid: graph["definition"],
        definition.uid: definition,
    }
    graph["instances"].append(second)
    row = graph["operational"][0].model_copy(deep=True)
    row.study_activity_instance_uid = second.study_activity_instance_uid
    graph["operational"].append(row)
    return graph


@pytest.mark.parametrize("anchor_uid", [None, "Visit_1"])
def test_deepcopy_preserves_native_visit_wrapper_anchor_and_shared_model_identity(anchor_uid):
    snapshot = native_study_graph()["visits"][1]
    snapshot.resolved_anchor_visit_uid = anchor_uid
    before = snapshot.model_dump(mode="json")
    memo = {}
    copied = deepcopy([snapshot, snapshot, snapshot.value], memo)
    clone = copied[0]
    assert type(clone) is NativeVisitSnapshot
    assert type(clone.value) is StudyVisit
    assert clone is not snapshot and clone.value is not snapshot.value
    assert clone is copied[1] and clone.value is copied[2]
    assert memo[id(snapshot)] is clone
    assert memo[id(snapshot.value)] is clone.value
    assert clone.resolved_anchor_visit_uid == anchor_uid
    assert clone.model_dump(mode="json") == before
    clone.value.visit_name = "Copied graph local edit"
    clone.resolved_anchor_visit_uid = "Copied_graph_local_anchor"
    assert snapshot.model_dump(mode="json") == before


def test_schedule_only_note_binds_one_occurrence_when_instance_repeats_at_two_visits(monkeypatch):
    graph = schedule_only()
    graph["planning"].append(StudyActivitySchedule(
        study_activity_schedule_uid="Plan_2", study_activity_uid="Activity_1", study_visit_uid="Visit_3",
    ))
    graph["operational"].append(StudyActivitySchedule(
        study_activity_schedule_uid="Plan_2", study_activity_uid="Activity_1",
        study_activity_instance_uid="InstanceSelection_1", study_visit_uid="Visit_3",
    ))
    before = native_json(graph)
    report, _ = map_graph(graph, monkeypatch)
    assert native_json(graph) == before
    document = report["document"]
    scoped = scopes(document)
    assert len(scoped) == 1
    values = scope_values(scoped[0])
    design = document["study"]["versions"][0]["studyDesigns"][0]
    timeline = next(item for item in design["scheduleTimelines"] if item["id"] == values["timelineId"])
    target = next(item for item in timeline["instances"] if item["id"] == values["scheduledInstanceId"])
    assert target["encounterId"] == values["encounterId"]
    assert values["activityIds"] == target["activityIds"]
    other = next(item for item in timeline["instances"] if item["label"] == "Visit 3")
    assert other["activityIds"] == values["activityIds"] and other["id"] != values["scheduledInstanceId"]
    assert_no_broad_annotation(document)
    assert not any(issue["code"] == SCHEDULE_FOOTNOTE_SCOPE_REQUIRED for issue in report["mappingReport"]["issues"])
    assert annotation(document)["text"] == TEXT
    raw = next(entry["record"] for entry in report["nativeRecords"] if entry["kind"] == "studySoAFootnote")
    assert raw["latest_footnote"]["name"] == "UNSELECTED FOOTNOTE"


def test_schedule_scope_does_not_cover_another_activity_at_the_same_visit(monkeypatch):
    graph = schedule_only()
    values = graph["activities"][0].model_dump(mode="json")
    values["study_activity_uid"] = "Activity_2"
    values["activity"]["uid"] = "LibraryActivity_2"
    graph["activities"].append(StudySelectionActivity(**values))
    graph["planning"].append(StudyActivitySchedule(
        study_activity_schedule_uid="Other_Plan", study_activity_uid="Activity_2", study_visit_uid="Visit_2",
    ))
    report, _ = map_graph(graph, monkeypatch)
    document = report["document"]
    values = scope_values(scopes(document)[0])
    design = document["study"]["versions"][0]["studyDesigns"][0]
    occurrence = next(item for timeline in design["scheduleTimelines"] for item in timeline["instances"]
                      if item["id"] == values["scheduledInstanceId"])
    assert len(occurrence["activityIds"]) == 2 and len(values["activityIds"]) == 1
    assert set(values["activityIds"]) < set(occurrence["activityIds"])
    assert_no_broad_annotation(document)


def test_exact_same_schedule_uid_operational_expansion_preserves_all_instances_and_source_rows(monkeypatch):
    graph = two_instances()
    before = native_json(graph)
    report, source = map_graph(graph, monkeypatch)
    assert native_json(graph) == before
    values = scope_values(scopes(report["document"])[0])
    assert len(values["activityIds"]) == 2 and len(set(values["activityIds"])) == 2
    records = [entry for entry in report["nativeRecords"] if entry["kind"] == "studyOperationalActivitySchedule"]
    assert [entry["uid"] for entry in records] == ["Plan_1", "Plan_1"]
    assert {entry["scope"]["studyActivityInstanceUid"] for entry in records} == {
        "InstanceSelection_1", "InstanceSelection_2",
    }
    assert [entry["record"] for entry in records] == native_json(graph["operational"])
    assert all(entry["scope"]["studyUid"] == STUDY_UID and entry["scope"]["studyValueVersion"] == VERSION
               for entry in records)
    assert ("definition", "LibraryInstance_1", "1.0") in source.calls
    assert ("definition", "LibraryInstance_2", "1.0") in source.calls
    assert_no_broad_annotation(report["document"])
    assert_native_timing_anchors(report)
    reordered = deepcopy(graph)
    assert native_json(reordered) == before
    assert all(type(visit) is NativeVisitSnapshot for visit in reordered["visits"])
    reordered["operational"].reverse()
    other, _ = map_graph(reordered, monkeypatch)
    assert_native_timing_anchors(other)
    assert other["document"] == report["document"]
    assert other["nativeRecords"] == report["nativeRecords"]
    assert native_json(graph) == before


def test_native_collector_keeps_same_schedule_distinct_instances_without_false_ambiguity():
    graph = two_instances()
    before = native_json(graph["operational"])
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return graph["operational"]

    records, census = collect_study_native_records(
        STUDY_UID, study_value_version=VERSION,
        readers={"studyOperationalActivitySchedule": read},
    )
    assert calls == [{"study_uid": STUDY_UID, "study_value_version": VERSION, "operational": True}]
    assert [entry["record"] for entry in records] == before
    assert native_json(graph["operational"]) == before
    assert [entry["uid"] for entry in records] == ["Plan_1", "Plan_1"]
    assert {entry["scope"]["studyActivityInstanceUid"] for entry in records} == {
        "InstanceSelection_1", "InstanceSelection_2",
    }
    assert all(entry["scope"]["studyUid"] == STUDY_UID
               and entry["scope"]["studyValueVersion"] == VERSION for entry in records)
    assert [entry["kind"] for entry in census] == ["native_study_collection"]
    assert census[0]["readCount"] == census[0]["retainedReadings"] == 2


@pytest.mark.parametrize("conflicting_field", ["study_activity_uid", "study_visit_uid"])
def test_native_collector_still_reports_same_schedule_conflicting_parent_or_visit(conflicting_field):
    graph = two_instances()
    setattr(graph["operational"][1], conflicting_field, "Other_native_identity")
    before = native_json(graph["operational"])
    records, census = collect_study_native_records(
        STUDY_UID, study_value_version=VERSION,
        readers={"studyOperationalActivitySchedule": lambda **_: graph["operational"]},
    )
    assert [entry["record"] for entry in records] == before
    assert native_json(graph["operational"]) == before
    assert [entry["kind"] for entry in census] == ["ambiguous_native_reading", "native_study_collection"]
    assert census[0]["ref"] == "studyOperationalActivitySchedule/Plan_1"
    assert census[1]["retainedReadings"] == 2


@pytest.mark.parametrize("invalid_instance", ["", " ", 1, False, []])
def test_native_collector_does_not_coerce_invalid_instance_qualifiers(invalid_instance):
    record = native_json(schedule_only()["operational"][0])
    record["study_activity_instance_uid"] = invalid_instance
    with pytest.raises(NativeStudyRecordError, match="instance identity is invalid"):
        collect_study_native_records(
            STUDY_UID, study_value_version=VERSION,
            readers={"studyOperationalActivitySchedule": lambda **_: [record]},
        )


def test_planning_only_schedule_retains_its_exact_occurrence_scope(monkeypatch):
    graph = schedule_only()
    graph["operational"] = []
    report, _ = map_graph(graph, monkeypatch)
    values = scope_values(scopes(report["document"])[0])
    assert values["activityIds"] == [values["sourceActivityId"]]
    assert_no_broad_annotation(report["document"])


def test_missing_schedule_preserves_text_reference_and_actionable_issue(monkeypatch):
    graph = schedule_only()
    graph["footnotes"][0].referenced_items[0].item_uid = "Missing_Schedule"
    report, _ = map_graph(graph, monkeypatch)
    assert_unresolved(report)
    retained = next(entry["record"] for entry in report["nativeRecords"] if entry["kind"] == "studySoAFootnote")
    assert retained["referenced_items"][0]["item_uid"] == "Missing_Schedule"
    assert retained["footnote"]["version"] == "1.0"


def test_another_schedule_uid_with_equal_activity_visit_pair_is_not_a_fallback(monkeypatch):
    graph = schedule_only()
    graph["operational"][0].study_activity_schedule_uid = "Different_Schedule"
    report, _ = map_graph(graph, monkeypatch)
    assert_unresolved(report)


def test_one_unresolved_operational_target_prevents_a_partial_annotation_scope(monkeypatch):
    graph = two_instances()
    graph["operational"][1].study_activity_instance_uid = "Missing_Instance"
    report, _ = map_graph(graph, monkeypatch)
    assert_unresolved(report)


def test_one_schedule_uid_cannot_merge_conflicting_native_visit_scopes(monkeypatch):
    graph = two_instances()
    graph["operational"][1].study_visit_uid = "Visit_3"
    with pytest.raises(USDMMappingAuthorityRequired, match="USDM_OPERATIONAL_SCHEDULE_SCOPE_CONFLICT"):
        map_graph(graph, monkeypatch)


def test_actual_export_keeps_scoped_comment_custody_and_passes_pinned_annotation_shape():
    source = ScheduleSource(schedule_only())
    odm = native_odm_graph()
    before = source.source_input(odm)
    bundle = source.export(odm)
    assert source.source_input(odm) == before
    verify_source_exchange(bundle)
    document = bundle["definition"]["document"]
    assert len(scopes(document)) == 1
    assert_no_broad_annotation(document)
    assert bundle["profile"]["mode"] == "draft"
    assert bundle["extensions"]["_osbExport"]["mappingReport"]["state"] == "incomplete"
    assert sha256(SCHEMA.read_bytes()).hexdigest() == "dc4303bca26256c56e5cb83222e898a09a5244472f7fd092b425cc2b7568fe19"
    schema = json.loads(SCHEMA.read_bytes())
    validator = Draft202012Validator({
        "$ref": "#/components/schemas/CommentAnnotation-Input",
        "components": schema["components"],
    })
    assert list(validator.iter_errors(annotation(document))) == []
