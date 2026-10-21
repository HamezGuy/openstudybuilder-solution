"""Synthetic lifecycle evidence through the production projection and collection paths."""

# Empty collections and dictionaries are exact contract assertions.
# pylint: disable=use-implicit-booleaness-not-comparison

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from clinical_mdr_api.models.study_selections.study_arm_lifecycle import (
    StudyArmLifecycleEvent,
)
from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
    native_json,
)
from clinical_mdr_api.services.integrations.edc_export import (
    EdcExportError,
    EdcExportService,
)
from clinical_mdr_api.services.integrations.edc_native_study_records import (
    NativeStudyRecordError,
    collect_study_native_records,
)
from clinical_mdr_api.services.integrations.native_observation import (
    NativeObservationError,
    canonical_hash,
    collect_native_observation,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import (
    NativeStudySource,
    native_odm_graph,
)
from clinical_mdr_api.tests.fixtures.usdm_native_study import (
    STUDY_UID,
    VERSION,
    native_study_graph,
)

LIFECYCLE_URL = (
    "https://openstudybuilder.org/usdm/extensions/native/studyArmLifecycleEvent"
)


def lifecycle_events():
    return [
        StudyArmLifecycleEvent(
            uid="Lifecycle_future",
            study_uid=STUDY_UID,
            target_kind="arm",
            target_uid="Arm_1",
            lifecycle_state="closed for efficacy",
            effective_date=datetime(2099, 1, 1, tzinfo=timezone.utc),
            decision_reference="synthetic-decision/future",
            change_reason="Synthetic prospective authored closure",
            recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            author_id="synthetic-author",
        ),
        StudyArmLifecycleEvent(
            uid="Lifecycle_enter",
            study_uid=STUDY_UID,
            target_kind="arm",
            target_uid="Arm_1",
            lifecycle_state="enrolling",
            effective_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            decision_reference="synthetic-decision/enter",
            change_reason="Synthetic authored entry",
            recorded_at=datetime(2024, 12, 1, tzinfo=timezone.utc),
            author_id="synthetic-author",
        ),
        StudyArmLifecycleEvent(
            uid="Lifecycle_branch",
            study_uid=STUDY_UID,
            target_kind="branch_arm",
            target_uid="Branch_1",
            lifecycle_state="paused",
            effective_date=datetime(2027, 1, 1, tzinfo=timezone.utc),
            decision_reference="synthetic-decision/branch",
            change_reason="Synthetic prospective branch pause",
            recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            author_id="synthetic-reviewer",
        ),
    ]


def map_events(monkeypatch, events):
    graph = native_study_graph()
    graph["lifecycle_events"] = events
    source = NativeStudySource(graph)
    monkeypatch.setattr(
        "clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query
    )
    report = source.mapper().map_with_report(graph["study"], VERSION)
    return report, source


def mapped_arms(report):
    return report["document"]["study"]["versions"][0]["studyDesigns"][0]["arms"]


def lifecycle_extensions(arm):
    return [
        extension
        for extension in arm["extensionAttributes"]
        if extension["url"] == LIFECYCLE_URL
    ]


def extension_values(extension):
    return {
        child["url"].removeprefix(LIFECYCLE_URL + "/"): child["valueString"]
        for child in extension["extensionAttributes"]
        if "valueString" in child and child["url"].startswith(LIFECYCLE_URL + "/")
    }


def native_events(records):
    return [entry for entry in records if entry["kind"] == "studyArmLifecycleEvent"]


def test_usdm_keeps_full_chronological_history_and_exact_selected_version(monkeypatch):
    events = lifecycle_events()
    before = native_json(events)
    report, source = map_events(monkeypatch, events)
    assert native_json(events) == before
    assert [call for call in source.calls if call[0] == "lifecycle_events"] == [
        ("lifecycle_events", STUDY_UID, VERSION, 0, {})
    ]
    expected = [before[1], before[2], before[0]]
    retained = native_events(report["nativeRecords"])
    assert [entry["record"] for entry in retained] == expected
    assert all(
        entry["scope"] == {"studyUid": STUDY_UID, "studyValueVersion": VERSION}
        for entry in retained
    )
    arms = mapped_arms(report)
    parent = next(arm for arm in arms if arm["name"] == "Native arm")
    branch = next(arm for arm in arms if arm["name"] == "Native branch")
    assert [extension_values(item) for item in lifecycle_extensions(parent)] == [
        before[1],
        before[0],
    ]
    assert [extension_values(item) for item in lifecycle_extensions(branch)] == [
        before[2]
    ]
    assert "lifecycle_state" not in parent and "lifecycle_state" not in branch
    assert "status" not in parent and "status" not in branch
    # Native row order is not an implicit effective state or an ID allocation input.
    reversed_report, _ = map_events(monkeypatch, list(reversed(events)))
    assert mapped_arms(reversed_report) == arms


def test_no_authored_events_leaves_lifecycle_unknown(monkeypatch):
    report, _ = map_events(monkeypatch, [])
    assert native_events(report["nativeRecords"]) == []
    assert all(lifecycle_extensions(arm) == [] for arm in mapped_arms(report))
    assert all("status" not in arm for arm in mapped_arms(report))


@pytest.mark.parametrize("target_kind", ["arm", "branch_arm"])
def test_usdm_orphan_is_retained_as_unresolved_without_name_matching(
    monkeypatch, target_kind
):
    event = lifecycle_events()[0]
    event.target_kind = target_kind
    event.target_uid = "Native arm"  # Display names cannot authorize identity joins.
    report, _ = map_events(monkeypatch, [event])
    assert native_events(report["nativeRecords"])[0]["record"] == native_json(event)
    assert any(
        issue["code"] == "USDM_ARM_LIFECYCLE_TARGET_UNRESOLVED"
        for issue in report["mappingReport"]["issues"]
    )
    assert all(lifecycle_extensions(arm) == [] for arm in mapped_arms(report))


def test_usdm_conflicting_event_identity_and_foreign_scope_fail(monkeypatch):
    first = lifecycle_events()[0]
    conflicting = deepcopy(first)
    conflicting.decision_reference = "synthetic-decision/conflicting"
    with pytest.raises(
        USDMMappingAuthorityRequired, match="USDM_SOURCE_SNAPSHOT_CONFLICT"
    ):
        map_events(monkeypatch, [first, conflicting])
    first.study_uid = "AnotherStudy"
    with pytest.raises(
        USDMMappingAuthorityRequired, match="USDM_SOURCE_STUDY_MISMATCH"
    ):
        map_events(monkeypatch, [first])


@pytest.mark.parametrize("version", [None, VERSION])
def test_native_collector_retains_every_event_without_paging_or_clock_filter(version):
    expected = native_json(lifecycle_events())
    calls = []

    def read(study_uid, study_value_version=None):
        calls.append((study_uid, study_value_version))
        return deepcopy(expected)

    records, census = collect_study_native_records(
        STUDY_UID,
        study_value_version=version,
        readers={"studyArmLifecycleEvent": read},
    )
    assert calls == [(STUDY_UID, version)]
    assert [entry["record"] for entry in records] == [
        expected[1],
        expected[2],
        expected[0],
    ]
    assert census[0]["readCount"] == 3
    if version:
        assert all(entry["scope"]["studyValueVersion"] == version for entry in records)
    assert all("status" not in entry["record"] for entry in records)


def test_native_lifecycle_reader_must_honor_selected_history():
    with pytest.raises(NativeStudyRecordError, match="cannot honor"):
        collect_study_native_records(
            STUDY_UID,
            study_value_version=VERSION,
            readers={"studyArmLifecycleEvent": lambda study_uid: []},
        )


def test_full_edc_bundle_retains_branch_and_future_events_without_execution_state():
    graph = native_study_graph()
    graph["lifecycle_events"] = lifecycle_events()
    source = NativeStudySource(graph)
    bundle = source.export(native_odm_graph())
    records = native_events(bundle["extensions"]["_osbExport"]["native"]["records"])
    expected = native_json(graph["lifecycle_events"])
    assert [row["record"] for row in records] == [expected[1], expected[2], expected[0]]
    groups = bundle["execution"]["studyGroupClasses"][0]["groups"]
    assert groups[0]["refKey"] == "Arm_1"
    assert groups[0]["extensions"]["osbArmLifecycle"] == {
        "events": [expected[1], expected[0]]
    }
    assert "status" not in groups[0] and "lifecycle_state" not in groups[0]
    assert all(row["scope"]["studyValueVersion"] == VERSION for row in records)


def test_semantic_source_groups_remain_authoritative_and_all_events_are_retained():
    service = object.__new__(EdcExportService)
    service.census = []
    events = native_json(lifecycle_events())
    records, _ = collect_study_native_records(
        STUDY_UID,
        study_value_version=VERSION,
        readers={
            "studyArmLifecycleEvent": lambda **_: events,
            "studyBranchArm": lambda **_: [
                {"study_uid": STUDY_UID, "branch_arm_uid": "Branch_1"}
            ],
        },
    )
    for entry in records:
        service._retain_native(
            entry["kind"], entry["record"], uid=entry["uid"], scope=entry.get("scope")
        )
    service._retain_native(
        "studyArm", {"study_uid": STUDY_UID, "arm_uid": "Arm_1"}, uid="Arm_1"
    )
    projected = [
        {
            "name": "Arms",
            "groupClassTypeName": "Arm",
            "groups": [{"refKey": "Arm_1", "name": "Same name"}],
        }
    ]
    original = [
        {
            "name": "Source arms",
            "groupClassTypeName": "Arm",
            "groups": [
                {
                    "refKey": "Arm_1",
                    "name": "Same name",
                    "extensions": {"sourceOnly": True},
                },
                {"refKey": "source-only", "name": "Same name"},
            ],
        }
    ]
    service.source_bundle_meta = {
        "formatVersion": "2.0",
        "execution": {"studyGroupClasses": deepcopy(original)},
    }
    service._project_arm_lifecycle(projected)
    assert projected[0]["groups"][0]["extensions"]["osbArmLifecycle"]["events"] == [
        events[1],
        events[0],
    ]
    assert service._restore_source_group_classes(projected) == {
        "studyGroupClasses": original
    }
    assert len(native_events(service._native_records)) == 3


@pytest.mark.parametrize(
    "corruption", ["orphan", "ambiguous-target", "ambiguous-event"]
)
def test_edc_refuses_unresolved_lifecycle_targets_and_conflicting_events(corruption):
    service = object.__new__(EdcExportService)
    event = native_json(lifecycle_events()[0])
    service._retain_native("studyArmLifecycleEvent", event, uid=event["uid"])
    if corruption != "orphan":
        service._retain_native("studyArm", {"arm_uid": "Arm_1"}, uid="Arm_1")
    if corruption == "ambiguous-target":
        service._retain_native(
            "studyArm", {"arm_uid": "Arm_1", "name": "Conflicting"}, uid="Arm_1"
        )
    if corruption == "ambiguous-event":
        service._retain_native(
            "studyArmLifecycleEvent",
            {**event, "decision_reference": "conflict"},
            uid=event["uid"],
        )
    with pytest.raises(EdcExportError, match="OSB_ARM_LIFECYCLE"):
        service._project_arm_lifecycle([{"groups": [{"refKey": "Arm_1"}]}])


def test_observation_retains_immutable_event_provenance_and_reports_audit_coverage():
    events = native_json(lifecycle_events())
    calls = []

    def read(study_uid):
        calls.append(study_uid)
        return deepcopy(events)

    result = collect_native_observation(
        STUDY_UID,
        native_study={"uid": STUDY_UID},
        readers={"study_arm_lifecycle_events": (read, None)},
    )
    assert calls == [STUDY_UID]
    retained = [
        row
        for row in result["records"]
        if row["collection"] == "study_arm_lifecycle_events"
    ]
    assert {row["nativeUid"]: row["record"] for row in retained} == {
        event["uid"]: event for event in events
    }
    assert all(row["resourceType"] == "StudyArmLifecycleEvent" for row in retained)
    assert all(
        row["comparisonHash"] == canonical_hash(row["record"]) for row in retained
    )
    coverage = next(
        row
        for row in result["coverage"]["collections"]
        if row["collection"] == "study_arm_lifecycle_events"
    )
    assert coverage["complete"] is True and coverage["recordCount"] == 3
    assert coverage["auditStatus"] == "unavailable" and coverage["auditCount"] == 0
    assert coverage["auditReason"]
    assert result["coverage"]["releaseAuthority"] is False
    assert result["auditRecords"] == []


def test_observation_rejects_foreign_and_duplicate_event_identity():
    event = native_json(lifecycle_events()[0])
    for records in ([{**event, "study_uid": "Other"}], [event, event]):
        with pytest.raises(NativeObservationError):
            collect_native_observation(
                STUDY_UID,
                native_study={"uid": STUDY_UID},
                readers={
                    "study_arm_lifecycle_events": (
                        lambda selected=records, **_: selected,
                        None,
                    )
                },
            )
