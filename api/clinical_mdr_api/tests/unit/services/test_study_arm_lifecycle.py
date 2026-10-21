"""Offline contract checks for authored, version-owned lifecycle history."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from clinical_mdr_api.domain_repositories.models._utils import (
    convert_to_tz_aware_datetime,
)
from clinical_mdr_api.domain_repositories.study_selections import (
    study_arm_repository as repository_module,
)
from clinical_mdr_api.domain_repositories.study_selections.study_arm_repository import (
    StudySelectionArmRepository,
)
from clinical_mdr_api.domain_repositories.study_selections.study_branch_arm_repository import (
    StudySelectionBranchArmRepository,
)
from clinical_mdr_api.models.study_selections.study_arm_lifecycle import (
    StudyArmLifecycleEvent,
    StudyArmLifecycleEventInput,
)
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from common.exceptions import (
    AlreadyExistsException,
    BusinessLogicException,
    ValidationException,
)


def event_input():
    return {
        "expected_study_version": "None",
        "lifecycle_state": "Temporarily paused by protocol committee",
        "effective_date": "2026-10-21T09:00:00-05:00",
        "decision_reference": "urn:synthetic:decision:123",
        "change_reason": "Synthetic engineering qualification only",
    }


def event_record(uid="StudyArmLifecycleEvent_1"):
    instant = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)
    return {
        "event": {
            "uid": uid,
            "study_uid": "Study_1",
            "target_kind": "arm",
            "target_uid": "StudyArm_1",
            "lifecycle_state": "Temporarily paused by protocol committee",
            "effective_date": instant,
            "decision_reference": "urn:synthetic:decision:123",
            "change_reason": "Synthetic engineering qualification only",
        },
        "targets": [
            {
                "uid": "StudyArm_1",
                "relation": "STUDY_ARM_HAS_LIFECYCLE_EVENT",
                "labels": ["StudyArm", "StudySelection"],
                "owners": ["Study_1"],
                "selected": True,
            }
        ],
        "owners": ["Study_1", "Study_1"],
        "actions": [
            {
                "author_id": "synthetic-author",
                "recorded_at": instant + timedelta(hours=1),
                "labels": ["Create", "StudyAction"],
                "owners": ["Study_1"],
            }
        ],
        "has_before": False,
    }


def event_vo():
    return StudySelectionArmRepository._restore_lifecycle_event(
        "Study_1", event_record()
    )


@pytest.mark.parametrize(
    "field",
    [
        "expected_study_version",
        "lifecycle_state",
        "decision_reference",
        "change_reason",
    ],
)
@pytest.mark.parametrize("value", [None, "", " \t\n"])
def test_input_requires_explicit_nonblank_authored_fields(field, value):
    payload = event_input()
    payload[field] = value
    with pytest.raises(ValidationError):
        StudyArmLifecycleEventInput(**payload)


def test_input_preserves_authored_label_and_requires_aware_effective_time():
    payload = event_input()
    model = StudyArmLifecycleEventInput(**payload)
    assert model.lifecycle_state == payload["lifecycle_state"]
    assert model.effective_date == datetime(2026, 10, 21, 14, tzinfo=timezone.utc)
    payload["effective_date"] = "2026-10-21T14:00:00"
    with pytest.raises(ValidationError):
        StudyArmLifecycleEventInput(**payload)
    payload = event_input()
    payload["operational_status"] = "active"
    with pytest.raises(ValidationError):
        StudyArmLifecycleEventInput(**payload)


def test_domain_is_immutable_and_sorts_by_aware_instants_without_status_inference():
    event = event_vo()
    with pytest.raises(FrozenInstanceError):
        event.lifecycle_state = "Changed"
    earlier = replace(
        event,
        uid="Earlier",
        effective_date=datetime(
            2026, 10, 21, 8, 30, tzinfo=timezone(timedelta(hours=-5))
        ),
    )
    assert sorted([event, earlier], key=lambda item: item.sort_key) == [earlier, event]
    with pytest.raises(ValidationException, match="timezone"):
        replace(event, recorded_at=datetime(2026, 10, 21))


@pytest.mark.parametrize("offset_minutes", [330, -300])
def test_created_and_hydrated_events_have_identical_utc_wire_timestamps(offset_minutes):
    offset = timezone(timedelta(minutes=offset_minutes))
    authored_time = datetime(2045, 1, 2, 3, 4, 5, 123456, tzinfo=offset)
    recorded_time = datetime(2026, 10, 21, 9, 10, 11, 654321, tzinfo=offset)
    created = replace(
        event_vo(), effective_date=authored_time, recorded_at=recorded_time
    )
    persisted = event_record()
    # These are the same conversions used by native ZonedDateTimeProperty.
    persisted["event"]["effective_date"] = convert_to_tz_aware_datetime(authored_time)
    persisted["actions"][0]["recorded_at"] = convert_to_tz_aware_datetime(recorded_time)
    hydrated = StudySelectionArmRepository._restore_lifecycle_event(
        "Study_1", persisted
    )
    created_wire = StudyArmLifecycleEvent.from_vo(created).model_dump(mode="json")
    hydrated_wire = StudyArmLifecycleEvent.from_vo(hydrated).model_dump(mode="json")
    assert created_wire == hydrated_wire
    assert created.effective_date == authored_time
    assert created.recorded_at == recorded_time
    assert created_wire["effective_date"].endswith("Z")
    assert created_wire["recorded_at"].endswith("Z")


@pytest.mark.parametrize("field", ["effective_date", "recorded_at"])
def test_domain_rejects_naive_timestamps_before_utc_normalization(field):
    with pytest.raises(ValidationException, match=f"{field} must include a timezone"):
        replace(event_vo(), **{field: datetime(2045, 1, 2, 3, 4, 5)})


def test_historical_target_revisions_share_one_immutable_event():
    record = event_record()
    previous = deepcopy(record["targets"][0])
    previous["selected"] = False
    record["targets"].append(previous)
    restored = StudySelectionArmRepository._restore_lifecycle_event("Study_1", record)
    assert restored == event_vo()


@pytest.mark.parametrize(
    "change",
    [
        "missing_target",
        "two_selected_targets",
        "wrong_target_identity",
        "wrong_target_kind",
        "foreign_target_owner",
        "foreign_event_owner",
        "event_study_mismatch",
        "missing_create",
        "multiple_create",
        "foreign_audit_owner",
        "duplicate_audit_owner",
        "non_create_action",
        "mutation_history",
        "missing_decision",
    ],
)
def test_corrupt_target_or_audit_ownership_is_reported(change):
    record = event_record()
    if change == "missing_target":
        record["targets"] = []
    elif change == "two_selected_targets":
        record["targets"].append(deepcopy(record["targets"][0]))
    elif change == "wrong_target_identity":
        record["targets"][0]["uid"] = "StudyArm_other"
    elif change == "wrong_target_kind":
        record["event"]["target_kind"] = "branch_arm"
    elif change == "foreign_target_owner":
        record["targets"][0]["owners"].append("Study_other")
    elif change == "foreign_event_owner":
        record["owners"].append("Study_other")
    elif change == "event_study_mismatch":
        record["event"]["study_uid"] = "Study_other"
    elif change == "missing_create":
        record["actions"] = []
    elif change == "multiple_create":
        record["actions"].append(deepcopy(record["actions"][0]))
    elif change == "foreign_audit_owner":
        record["actions"][0]["owners"].append("Study_other")
    elif change == "duplicate_audit_owner":
        record["actions"][0]["owners"].append("Study_1")
    elif change == "non_create_action":
        record["actions"][0]["labels"] = ["Edit", "StudyAction"]
    elif change == "mutation_history":
        record["has_before"] = True
    else:
        record["event"]["decision_reference"] = None
    with pytest.raises(ValidationException):
        StudySelectionArmRepository._restore_lifecycle_event("Study_1", record)


def test_service_preserves_all_fields_and_exact_version_filter():
    service = StudyArmSelectionService.__new__(StudyArmSelectionService)
    service.author = "synthetic-author"
    native = Mock()
    native.create_lifecycle_event.return_value = event_vo()
    native.find_lifecycle_events.return_value = [event_vo()]
    service._repos = SimpleNamespace(study_arm_repository=native, close=Mock())
    payload = StudyArmLifecycleEventInput(**event_input())
    created = StudyArmSelectionService.create_lifecycle_event.__wrapped__(
        service, "Study_1", "arm", "StudyArm_1", payload
    )
    native.create_lifecycle_event.assert_called_once_with(
        study_uid="Study_1",
        target_kind="arm",
        target_uid="StudyArm_1",
        author_id="synthetic-author",
        **payload.model_dump(),
    )
    events = StudyArmSelectionService.get_lifecycle_events.__wrapped__(
        service, "Study_1", "1.0", "arm", "StudyArm_1"
    )
    native.find_lifecycle_events.assert_called_once_with(
        "Study_1",
        study_value_version="1.0",
        target_kind="arm",
        target_uid="StudyArm_1",
    )
    assert events == [created]
    assert created.model_dump() == vars(event_vo())
    assert service._repos.close.call_count == 2


def mock_event_read(monkeypatch, records):
    repository = StudySelectionArmRepository()
    monkeypatch.setattr(
        repository,
        "_lifecycle_study_value",
        Mock(return_value=(Mock(), SimpleNamespace(element_id="selected-value"))),
    )
    monkeypatch.setattr(repository, "_lifecycle_target", Mock())
    monkeypatch.setattr(
        repository_module, "db", Mock(get_id_method=lambda: "elementId")
    )
    monkeypatch.setattr(
        repository_module.utils, "db_result_to_list", Mock(return_value=records)
    )
    return repository


def test_reader_sorts_events_and_rejects_duplicate_selected_identity(monkeypatch):
    first = event_record("first")
    last = event_record("last")
    first["event"]["effective_date"] -= timedelta(days=2)
    repository = mock_event_read(monkeypatch, [last, first])
    assert [event.uid for event in repository.find_lifecycle_events("Study_1")] == [
        "first",
        "last",
    ]
    repository_module.utils.db_result_to_list.return_value = [first, first]
    with pytest.raises(ValidationException, match="duplicate"):
        repository.find_lifecycle_events("Study_1")


def test_target_filter_cannot_hide_unrelated_corrupt_event(monkeypatch):
    other = event_record("other")
    other["event"]["target_uid"] = "StudyArm_other"
    repository = mock_event_read(monkeypatch, [event_record(), other])
    with pytest.raises(ValidationException, match="ownership"):
        repository.find_lifecycle_events(
            "Study_1", target_kind="arm", target_uid="StudyArm_1"
        )


@pytest.mark.parametrize(
    "state,expected_version,error",
    [
        (["DRAFT", None], "1.0", AlreadyExistsException),
        (["LOCKED", "1.0"], "1.0", BusinessLogicException),
    ],
)
def test_append_rejects_stale_or_locked_value_before_creating_event(
    monkeypatch, state, expected_version, error
):
    repository = mock_event_read(monkeypatch, [])
    repository_module.db.cypher_query.return_value = ([state], [])
    acquire = Mock()
    graph_event = Mock()
    monkeypatch.setattr(repository_module, "acquire_write_lock_study_value", acquire)
    monkeypatch.setattr(repository_module, "StudyArmLifecycleEvent", graph_event)
    payload = StudyArmLifecycleEventInput(**event_input()).model_dump()
    payload["expected_study_version"] = expected_version
    with pytest.raises(error):
        StudySelectionArmRepository.create_lifecycle_event.__wrapped__(
            repository,
            "Study_1",
            "arm",
            "StudyArm_1",
            author_id="synthetic-author",
            **payload,
        )
    acquire.assert_called_once_with("Study_1")
    repository._lifecycle_target.assert_not_called()
    graph_event.assert_not_called()
    graph_event.get_next_free_uid_and_increment_counter.assert_not_called()


@pytest.mark.parametrize("target_kind", ["arm", "branch_arm"])
def test_direct_repository_delete_cannot_bypass_lifecycle_guard(
    monkeypatch, target_kind
):
    guard = Mock(side_effect=BusinessLogicException(msg="Retain lifecycle history"))
    monkeypatch.setattr(
        StudySelectionArmRepository, "assert_lifecycle_target_can_be_deleted", guard
    )
    arguments = {
        "study_root": SimpleNamespace(uid="Study_1"),
        "latest_study_value_node": Mock(),
        "order": 1,
        "selection": SimpleNamespace(study_selection_uid="target"),
        "audit_node": Mock(),
        "for_deletion": True,
    }
    if target_kind == "arm":
        arguments["author_id"] = "synthetic-author"
        repository = StudySelectionArmRepository
    else:
        repository = StudySelectionBranchArmRepository
    with pytest.raises(BusinessLogicException, match="Retain lifecycle history"):
        repository._add_new_selection(**arguments)
    guard.assert_called_once_with("Study_1", target_kind, "target")


def test_arm_delete_also_protects_events_on_selected_child_branches(monkeypatch):
    branch_event = replace(event_vo(), target_kind="branch_arm", target_uid="branch")
    monkeypatch.setattr(
        StudySelectionArmRepository,
        "find_lifecycle_events",
        Mock(return_value=[branch_event]),
    )
    monkeypatch.setattr(repository_module, "acquire_write_lock_study_value", Mock())
    monkeypatch.setattr(
        repository_module,
        "db",
        Mock(cypher_query=Mock(return_value=([["branch"]], []))),
    )
    with pytest.raises(BusinessLogicException, match="cannot be deleted"):
        StudySelectionArmRepository.assert_lifecycle_target_can_be_deleted(
            "Study_1", "arm", "StudyArm_1"
        )
