"""Offline checks for exact version ownership and authored amendment facts."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from clinical_mdr_api.domain_repositories.study_definitions import (
    study_definition_repository_impl as definition_repository_module,
)
from clinical_mdr_api.domain_repositories.study_definitions.study_definition_repository_impl import (
    StudyDefinitionRepositoryImpl,
)
from clinical_mdr_api.domain_repositories.study_selections import (
    study_version_repository as repository_module,
)
from clinical_mdr_api.domain_repositories.study_selections.study_version_repository import (
    StudyVersionRepository,
)
from clinical_mdr_api.domains.study_selections.study_amendment import StudyAmendmentVO
from clinical_mdr_api.models.study_selections.study import (
    StudyAmendment,
    StudyLockInput,
)
from common.exceptions import NotFoundException, ValidationException


def authored_amendment():
    return {
        "amendment_type": "Authored synthetic regimen revision",
        "affected_arm_uids": ["StudyArm_1"],
        "authorizing_decision_ref": "urn:synthetic:decision:one",
    }


def repository():
    result = StudyVersionRepository.__new__(StudyVersionRepository)
    result.author_id = "synthetic-author"
    return result


@pytest.mark.parametrize("field", list(authored_amendment()))
def test_supplied_amendment_requires_all_authored_fields(field):
    payload = authored_amendment()
    del payload[field]
    with pytest.raises(ValidationError):
        StudyLockInput(reason_for_change_uid="CT_1", amendment=payload)


@pytest.mark.parametrize("field", ["amendment_type", "authorizing_decision_ref"])
@pytest.mark.parametrize("value", [None, "", " \t\n", 5])
def test_authored_text_cannot_be_missing_blank_or_coerced(field, value):
    payload = authored_amendment()
    payload[field] = value
    with pytest.raises(ValidationError):
        StudyAmendment(**payload)


@pytest.mark.parametrize(
    "value", [None, [""], [" \t"], [5], ["StudyArm_1", "StudyArm_1"]]
)
def test_arm_identifiers_must_be_explicit_unique_nonblank_strings(value):
    payload = authored_amendment()
    payload["affected_arm_uids"] = value
    with pytest.raises(ValidationError):
        StudyAmendment(**payload)


def test_unknown_amendment_and_explicit_empty_arm_scope_remain_distinct():
    assert StudyLockInput(reason_for_change_uid="CT_1").amendment is None
    assert (
        StudyLockInput(reason_for_change_uid="CT_1", amendment=None).amendment is None
    )
    payload = authored_amendment()
    payload["affected_arm_uids"] = []
    amendment = StudyAmendment(**payload)
    assert amendment.to_vo().to_dict() == payload
    assert StudyAmendmentVO.from_json(amendment.to_vo().to_json()).to_dict() == payload


def selected_row(record=None):
    return [
        SimpleNamespace(uid="Study_1"),
        SimpleNamespace(element_id="value-1"),
        record,
        ["Study_1", "Study_1"],
        ["value-1"] if record is not None else [],
    ]


def read_rows(monkeypatch, rows):
    query = Mock(return_value=(rows, []))
    monkeypatch.setattr(repository_module, "db", SimpleNamespace(cypher_query=query))
    return query


def test_historical_selected_record_retains_later_audit_history(monkeypatch):
    record = SimpleNamespace(
        amendment_json=StudyAmendment(**authored_amendment()).to_vo().to_json(),
        has_before=object(),
    )
    row = selected_row(record)
    query = read_rows(monkeypatch, [row])
    result = StudyVersionRepository.get_selected_version.__wrapped__(
        repository(), "Study_1", "1"
    )
    assert result == tuple(row[:3])
    assert query.call_args.args[1] == {"study_uid": "Study_1", "version": "1"}


@pytest.mark.parametrize("duplicate_same_record", [True, False])
def test_selected_duplicate_membership_or_values_are_refused(
    monkeypatch, duplicate_same_record
):
    first = selected_row(SimpleNamespace(amendment_json=None))
    second = first if duplicate_same_record else selected_row()
    if not duplicate_same_record:
        second[1].element_id = "another-value"
    read_rows(monkeypatch, [first, second])
    with pytest.raises(ValidationException, match="ambiguous"):
        StudyVersionRepository.get_selected_version.__wrapped__(
            repository(), "Study_1", "1"
        )


@pytest.mark.parametrize("foreign_record", [False, True])
def test_selected_ownership_cannot_cross_values_or_studies(monkeypatch, foreign_record):
    row = selected_row(SimpleNamespace(amendment_json=None))
    row[4 if foreign_record else 3].append(
        "another-value" if foreign_record else "AnotherStudy"
    )
    read_rows(monkeypatch, [row])
    with pytest.raises(ValidationException):
        StudyVersionRepository.get_selected_version.__wrapped__(repository(), "Study_1")


def test_missing_selected_value_is_not_an_empty_version(monkeypatch):
    read_rows(monkeypatch, [])
    with pytest.raises(NotFoundException):
        StudyVersionRepository.get_selected_version.__wrapped__(repository(), "Study_1")


@pytest.mark.parametrize("raw", ["{}", '{"affected_arm_uids":[]}', "null", "bad-json"])
def test_invalid_stored_amendments_are_not_reported_as_unknown(raw):
    with pytest.raises(ValidationException, match="amendment record is invalid"):
        StudyVersionRepository.amendment_from_record(
            SimpleNamespace(amendment_json=raw)
        )


def arm_validation(monkeypatch, rows):
    repo = repository()
    repo.get_selected_version = Mock(
        side_effect=[
            (None, SimpleNamespace(element_id="current"), None),
            (None, SimpleNamespace(element_id="previous"), None),
        ]
    )
    read_rows(monkeypatch, rows)
    monkeypatch.setattr(repository_module, "acquire_write_lock_study_value", Mock())
    return repo


def test_removed_arm_is_validated_against_exact_preceding_lock(monkeypatch):
    repo = arm_validation(
        monkeypatch, [["previous", "StudyArm_1", 1, [["Study_1", "Study_1"]]]]
    )
    StudyVersionRepository.validate_amendment_arms.__wrapped__(
        repo, "Study_1", StudyAmendment(**authored_amendment()).to_vo(), "1"
    )
    assert repo.get_selected_version.call_args_list[1].args == ("Study_1", "1")


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [["current", "StudyArm_1", 2, [["Study_1"], ["Study_1"]]]],
        [["previous", None, 1, [["Study_1"]]]],
    ],
)
def test_unknown_or_ambiguous_affected_arms_are_refused(monkeypatch, rows):
    repo = arm_validation(monkeypatch, rows)
    with pytest.raises(ValidationException):
        StudyVersionRepository.validate_amendment_arms.__wrapped__(
            repo, "Study_1", StudyAmendment(**authored_amendment()).to_vo(), "1"
        )


@pytest.mark.parametrize("selected_value", ["current", "previous"])
def test_selected_arm_shared_with_another_study_is_refused(monkeypatch, selected_value):
    repo = arm_validation(
        monkeypatch,
        [[selected_value, "StudyArm_1", 1, [["Study_1", "ForeignStudy"]]]],
    )
    with pytest.raises(ValidationException, match="foreign study ownership"):
        StudyVersionRepository.validate_amendment_arms.__wrapped__(
            repo, "Study_1", StudyAmendment(**authored_amendment()).to_vo(), "1"
        )


def captured_history_row():
    return {
        "statuses": ["DRAFT"],
        "metadata_version": None,
        "reason_for_lock": None,
        "reason_for_unlock": "Captured unlock reason",
        "other_reason_for_lock_release": None,
        "other_reason_for_unlocking": "Captured draft reason text",
        "protocol_header_major_version": None,
        "protocol_header_minor_version": None,
        "_selected_value_id": "captured-draft",
        "_value_owners": ["Study_1"],
        "_record_memberships": 1,
        "_record_id": "captured-record",
        "_record_values": ["captured-draft"],
        "_amendment_json": None,
        "_version_values": ["captured-draft"],
    }


def history_read(monkeypatch, rows):
    columns = list(rows[0])
    query = Mock(
        return_value=([[row[column] for column in columns] for row in rows], columns)
    )
    monkeypatch.setattr(repository_module, "db", SimpleNamespace(cypher_query=query))
    repo = repository()
    repo.get_selected_version = Mock(
        side_effect=AssertionError(
            "History must not reselect a concurrently changed LATEST"
        )
    )
    return repo, query


def test_history_amendment_stays_bound_to_captured_draft_even_if_latest_locks(
    monkeypatch,
):
    captured = captured_history_row()
    repo, query = history_read(monkeypatch, [captured])
    history, _ = repo.retrieve_study_snapshot_history("Study_1", page_size=0)
    assert history[0]["statuses"] == ["DRAFT"]
    assert history[0]["reason_for_unlock"] == "Captured unlock reason"
    assert history[0]["other_reason_for_unlocking"] == "Captured draft reason text"
    assert history[0]["amendment"] is None
    assert not any(key.startswith("_") for key in history[0])
    query.assert_called_once()
    repo.get_selected_version.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("_value_owners", ["Study_1", "ForeignStudy"]),
        ("_version_values", ["captured-draft", "another-value"]),
        ("_record_memberships", 2),
        ("_record_id", None),
        ("_record_values", ["captured-draft", "another-value"]),
        ("_amendment_json", "bad-json"),
    ],
)
def test_history_validates_ownership_and_amendment_from_same_query_row(
    monkeypatch, field, value
):
    captured = captured_history_row()
    captured[field] = value
    repo, _ = history_read(monkeypatch, [captured])
    with pytest.raises(ValidationException):
        repo.retrieve_study_snapshot_history("Study_1", page_size=0)
    repo.get_selected_version.assert_not_called()


def test_major_protocol_history_filter_retains_bound_amendment(monkeypatch):
    captured = captured_history_row()
    captured.update(
        statuses=["LOCKED", "RELEASED"],
        metadata_version="2",
        protocol_header_major_version=1,
        protocol_header_minor_version=0,
        _version_values=["captured-draft", "captured-draft"],
        _amendment_json=StudyAmendment(**authored_amendment()).to_vo().to_json(),
    )
    repo, query = history_read(monkeypatch, [captured])
    history, total = repo.retrieve_study_snapshot_history(
        "Study_1", page_size=1, only_latest_major_protocol_version=True
    )
    assert total == 1
    assert history[0]["amendment"] == authored_amendment()
    assert history[0]["original_metadata_version"] == "2"
    query.assert_called_once()
    repo.get_selected_version.assert_not_called()


def write_harness(monkeypatch, selected_before=None, release=False):
    created = []

    class Version:
        has_reason_for_lock = "lock-reason"
        has_reason_for_unlock = "unlock-reason"

        def __init__(self, **properties):
            self.uid = properties.get("uid") or "StudyVersion_1"
            self.amendment_json = None
            self.previous_locked_version = None
            self.other_reason_for_locking = None
            self.other_reason_for_unlocking = None
            self.__dict__.update(properties)
            if self.uid is None:
                self.uid = "StudyVersion_1"

        def save(self):
            created.append(self)
            return self

    root = SimpleNamespace(uid="Study_1")
    value = SimpleNamespace(element_id="selected", has_study_version=Mock())
    draft = SimpleNamespace(element_id="draft", has_study_version=Mock())
    repo = repository()
    repo.get_selected_version = Mock(
        side_effect=[(root, value, selected_before)]
        + ([(root, draft, None)] if release else [])
    )
    repo._connect_reason_relationships = Mock()
    audit = Mock()
    monkeypatch.setattr(repository_module, "StudyVersion", Version)
    monkeypatch.setattr(repository_module, "_manage_versioning_with_relations", audit)
    monkeypatch.setattr(repository_module, "acquire_write_lock_study_value", Mock())
    return repo, created, value, draft, audit


def previous_record():
    return SimpleNamespace(
        uid="StudyVersion_previous",
        amendment_json=StudyAmendment(**authored_amendment()).to_vo().to_json(),
        other_reason_for_unlocking="Previous unlock text",
    )


def test_lock_replaces_only_selected_membership_and_preserves_unlock_facts(monkeypatch):
    previous = previous_record()
    original = deepcopy(previous.__dict__)
    repo, created, value, _, audit = write_harness(monkeypatch, previous)
    amendment = StudyAmendment(**authored_amendment()).to_vo()
    result = StudyVersionRepository.create_or_update_study_version.__wrapped__(
        repo,
        "Study_1",
        version="2",
        reason_for_lock_term_uid="lock-term",
        other_reason_for_locking="New lock text",
        previous_version=previous,
        amendment=amendment,
        previous_locked_version="1",
    )
    assert result is created[0]
    assert result.amendment_json == amendment.to_json()
    assert result.other_reason_for_unlocking == "Previous unlock text"
    assert result.other_reason_for_locking == "New lock text"
    assert result.previous_locked_version == "1"
    assert previous.__dict__ == original
    value.has_study_version.disconnect.assert_called_once_with(previous)
    value.has_study_version.connect.assert_called_once_with(result)
    assert audit.call_args.kwargs["before"] is previous
    assert audit.call_args.kwargs["exclude_relationships"] == ["lock-reason"]


def test_unlock_carries_audit_lineage_without_previous_amendment(monkeypatch):
    previous = previous_record()
    repo, _, value, _, audit = write_harness(monkeypatch)
    result = StudyVersionRepository.create_or_update_study_version.__wrapped__(
        repo,
        "Study_1",
        reason_for_unlock_term_uid="unlock-term",
        other_reason_for_unlocking="New unlock text",
        previous_version=previous,
    )
    assert result.amendment_json is None
    assert result.previous_locked_version is None
    assert result.other_reason_for_locking is None
    assert result.other_reason_for_unlocking == "New unlock text"
    value.has_study_version.disconnect.assert_not_called()
    assert audit.call_args.kwargs["exclude_relationships"] == [
        "lock-reason",
        "unlock-reason",
    ]


@pytest.mark.parametrize("has_previous", [False, True])
def test_first_and_later_release_always_create_a_separate_unamended_draft(
    monkeypatch, has_previous
):
    previous = previous_record() if has_previous else None
    repo, created, value, draft, audit = write_harness(monkeypatch, previous, True)
    result = StudyVersionRepository.create_or_update_study_version.__wrapped__(
        repo,
        "Study_1",
        version="1.1",
        reason_for_lock_term_uid="release-term",
        other_reason_for_locking="Release text",
        release=True,
        previous_version=previous,
    )
    assert len(created) == 2
    assert result is created[1]
    assert all(record.amendment_json is None for record in created)
    assert result.other_reason_for_locking is None
    assert created[0].other_reason_for_locking == "Release text"
    assert result.other_reason_for_unlocking == (
        "Previous unlock text" if has_previous else None
    )
    value.has_study_version.connect.assert_called_once_with(created[0])
    draft.has_study_version.connect.assert_called_once_with(created[1])
    assert audit.call_args.kwargs["before"] is created[0]
    assert audit.call_args.kwargs["exclude_relationships"] == ["lock-reason"]


def test_draft_cannot_receive_an_authored_amendment(monkeypatch):
    repo, created, value, _, _ = write_harness(monkeypatch)
    with pytest.raises(ValidationException, match="locked study version"):
        StudyVersionRepository.create_or_update_study_version.__wrapped__(
            repo, "Study_1", amendment=StudyAmendment(**authored_amendment()).to_vo()
        )
    assert not created
    value.has_study_version.connect.assert_not_called()


def test_draft_metadata_successor_copies_only_exact_predecessor_reason_facts(
    monkeypatch,
):
    previous = previous_record()
    previous.other_reason_for_locking = None
    repo, created, value, _, audit = write_harness(monkeypatch)
    repo._resolve_selected_version = Mock(return_value=(None, None, previous))
    result = StudyVersionRepository.carry_forward_draft_reasons.__wrapped__(
        repo, "Study_1", "previous-physical-value", "selected"
    )
    assert result is created[0]
    assert result.other_reason_for_unlocking == "Previous unlock text"
    assert result.other_reason_for_locking is None
    assert result.amendment_json is None
    assert result.previous_locked_version is None
    assert repo._resolve_selected_version.call_args.args[2] == {
        "study_uid": "Study_1",
        "previous_value_id": "previous-physical-value",
    }
    value.has_study_version.disconnect.assert_not_called()
    value.has_study_version.connect.assert_called_once_with(result)
    assert audit.call_args.kwargs["before"] is previous
    assert audit.call_args.kwargs["author_id"] == "synthetic-author"


def test_missing_predecessor_reason_record_remains_unknown(monkeypatch):
    repo, created, value, _, audit = write_harness(monkeypatch)
    repo._resolve_selected_version = Mock(return_value=(None, None, None))
    result = StudyVersionRepository.carry_forward_draft_reasons.__wrapped__(
        repo, "Study_1", "previous-physical-value", "selected"
    )
    assert result is None
    assert not created
    value.has_study_version.connect.assert_not_called()
    audit.assert_not_called()


@pytest.mark.parametrize(
    "previous_status,current_status,new_release,same_value,expected",
    [
        ("DRAFT", "DRAFT", False, False, True),
        ("DRAFT", "DRAFT", True, False, False),
        ("DRAFT", "DRAFT", False, True, False),
        ("LOCKED", "DRAFT", False, False, False),
        ("DRAFT", "LOCKED", False, False, False),
        ("LOCKED", "LOCKED", False, False, False),
    ],
)
def test_definition_copy_is_limited_to_ordinary_draft_successors(
    monkeypatch, previous_status, current_status, new_release, same_value, expected
):
    version_repository = Mock()
    factory = Mock(return_value=version_repository)
    monkeypatch.setattr(definition_repository_module, "StudyVersionRepository", factory)
    StudyDefinitionRepositoryImpl._maintain_draft_version_reasons(
        SimpleNamespace(audit_info=SimpleNamespace(author_id="synthetic-author")),
        SimpleNamespace(uid="Study_1"),
        SimpleNamespace(study_status=previous_status, released_metadata="old-release"),
        SimpleNamespace(
            study_status=current_status,
            released_metadata="new-release" if new_release else "old-release",
        ),
        SimpleNamespace(element_id="previous"),
        SimpleNamespace(element_id="previous" if same_value else "current"),
    )
    if expected:
        factory.assert_called_once_with(author_id="synthetic-author")
        version_repository.carry_forward_draft_reasons.assert_called_once_with(
            "Study_1", "previous", "current"
        )
    else:
        factory.assert_not_called()


@pytest.mark.parametrize(
    "previous_id,current_id", [(None, "current"), ("previous", None)]
)
def test_definition_reason_copy_rejects_unpersisted_values(
    monkeypatch, previous_id, current_id
):
    factory = Mock()
    monkeypatch.setattr(definition_repository_module, "StudyVersionRepository", factory)
    with pytest.raises(ValidationException, match="persisted study values"):
        StudyDefinitionRepositoryImpl._maintain_draft_version_reasons(
            SimpleNamespace(audit_info=SimpleNamespace(author_id="synthetic-author")),
            SimpleNamespace(uid="Study_1"),
            SimpleNamespace(study_status="DRAFT", released_metadata=None),
            SimpleNamespace(study_status="DRAFT", released_metadata=None),
            SimpleNamespace(element_id=previous_id),
            SimpleNamespace(element_id=current_id),
        )
    factory.assert_not_called()
