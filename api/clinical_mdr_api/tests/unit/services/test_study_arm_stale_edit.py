"""The optional stale-edit check of a study arm PATCH (SO-OSB-1, SE-6, SE-7),
without a database."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionArmBatchInput,
    StudySelectionArmBatchUpdateInput,
    StudySelectionArmInput,
)
from clinical_mdr_api.services.studies import study_arm_selection as service_module
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from common.exception_handlers import register_exception_handlers
from common.exceptions import AlreadyExistsException, VersionConflictException

READ_AT = datetime(2026, 10, 27, 12, 0, 0, 123456, tzinfo=timezone.utc)
WRITTEN_AT = READ_AT + timedelta(seconds=7)
STORED_AT = WRITTEN_AT + timedelta(milliseconds=3)


def harness(monkeypatch, current_start_date=READ_AT):
    """A service whose repository holds one synthetic arm, StudyArm_1."""
    current = SimpleNamespace(
        study_selection_uid="StudyArm_1", start_date=current_start_date
    )
    updated = SimpleNamespace(study_selection_uid="StudyArm_1", start_date=None)
    stored = SimpleNamespace(study_selection_uid="StudyArm_1", start_date=STORED_AT)
    locked = MagicMock()
    locked.get_specific_object_selection.side_effect = [(current, 1), (updated, 1)]
    reread = MagicMock()
    reread.get_specific_object_selection.return_value = (stored, 1)
    service = StudyArmSelectionService.__new__(StudyArmSelectionService)
    service._repos = MagicMock()
    service._repos.study_arm_repository.find_by_study.side_effect = [locked, reread]
    service.author = "synthetic-editor"
    monkeypatch.setattr(
        service, "_patch_prepare_new_study_arm", Mock(return_value=updated)
    )
    monkeypatch.setattr(
        service, "_extract_study_standards_effective_date", Mock(return_value=None)
    )
    answer = Mock(return_value="answer")
    monkeypatch.setattr(
        service_module,
        "StudySelectionArmWithConnectedBranchArms",
        SimpleNamespace(
            from_study_selection_arm_ar__order__connected_branch_arms=answer
        ),
    )
    return service, SimpleNamespace(
        current=current, updated=updated, stored=stored, locked=locked, answer=answer
    )


def patch_arm(service, payload):
    return StudyArmSelectionService.patch_selection.__wrapped__(  # type: ignore[attr-defined]
        service, "Study_1", "StudyArm_1", payload
    )


def test_stale_token_is_refused_with_the_current_token_and_nothing_written(
    monkeypatch,
):
    service, state = harness(monkeypatch, current_start_date=WRITTEN_AT)
    with pytest.raises(VersionConflictException) as error:
        patch_arm(
            service, StudySelectionArmInput(name="Renamed", expected_start_date=READ_AT)
        )
    assert error.value.status_code == 409
    assert isinstance(error.value, AlreadyExistsException)
    assert error.value.code == "STUDY_ARM_VERSION_CONFLICT"
    assert error.value.msg.startswith("STUDY_ARM_VERSION_CONFLICT: ")
    assert error.value.conflict_field == "expected_start_date"
    assert error.value.current == {
        "study_arm_uid": "StudyArm_1",
        "current_start_date": WRITTEN_AT.isoformat(),
    }
    # Checked under the study write lock, before anything is merged or saved.
    service._repos.study_arm_repository.find_by_study.assert_called_once_with(
        study_uid="Study_1", for_update=True
    )
    service._patch_prepare_new_study_arm.assert_not_called()
    state.locked.update_selection.assert_not_called()
    service._repos.study_arm_repository.save.assert_not_called()


def test_same_instant_in_another_time_zone_is_the_current_token(monkeypatch):
    service, _ = harness(monkeypatch)
    offset = READ_AT.astimezone(timezone(timedelta(hours=-4)))
    assert patch_arm(
        service, StudySelectionArmInput(name="Renamed", expected_start_date=offset)
    )
    service._repos.study_arm_repository.save.assert_called_once()


def test_current_token_is_accepted_and_answers_the_stored_token(monkeypatch):
    service, state = harness(monkeypatch)
    result = patch_arm(
        service,
        StudySelectionArmInput(
            name="Renamed",
            change_reason="Synthetic rename",
            expected_start_date=READ_AT,
        ),
    )
    assert result == "answer"
    state.locked.update_selection.assert_called_once()
    save = service._repos.study_arm_repository.save
    save.assert_called_once()
    # change_reason handling is unchanged by the token.
    assert save.call_args.kwargs["change_reasons"] == {"StudyArm_1": "Synthetic rename"}
    finds = service._repos.study_arm_repository.find_by_study.call_args_list
    assert [call.kwargs for call in finds] == [
        {"study_uid": "Study_1", "for_update": True},
        {"study_uid": "Study_1"},
    ]
    assert state.answer.call_args.kwargs["selection"] is state.stored


def test_no_token_behaves_as_before(monkeypatch):
    service, state = harness(monkeypatch, current_start_date=WRITTEN_AT)
    assert patch_arm(service, StudySelectionArmInput(name="Renamed")) == "answer"
    service._repos.study_arm_repository.save.assert_called_once()
    service._repos.study_arm_repository.find_by_study.assert_called_once_with(
        study_uid="Study_1", for_update=True
    )
    assert state.answer.call_args.kwargs["selection"] is state.updated


def test_token_is_optional_needs_a_time_zone_and_survives_json():
    assert StudySelectionArmInput(name="A").expected_start_date is None
    assert (
        "expected_start_date" not in StudySelectionArmInput(name="A").model_fields_set
    )
    with pytest.raises(ValidationError):
        StudySelectionArmInput(expected_start_date="2026-10-27T12:00:00.123456")
    # The start_date an arm read returns is accepted back unchanged.
    as_read = TypeAdapter(datetime).dump_json(READ_AT).decode().strip('"')
    assert StudySelectionArmInput(expected_start_date=as_read).expected_start_date == (
        READ_AT
    )


def test_batch_patch_items_carry_the_token():
    item = StudySelectionArmBatchInput(
        method="PATCH",
        content={"arm_uid": "StudyArm_1", "expected_start_date": READ_AT.isoformat()},
    )
    assert isinstance(item.content, StudySelectionArmBatchUpdateInput)
    assert item.content.expected_start_date == READ_AT


def test_conflict_answer_names_the_code_and_the_current_token():
    app = FastAPI()
    register_exception_handlers(app)

    @app.patch("/arm")
    def conflict():
        service_module._refuse_stale_arm_edit(
            READ_AT,
            SimpleNamespace(study_selection_uid="StudyArm_1", start_date=WRITTEN_AT),
        )

    with patch(
        "common.exception_handlers.log_exception",
        AsyncMock(return_value={"rejectionId": "unit-rejection"}),
    ):
        response = TestClient(app).patch("/arm")

    body = response.json()
    assert response.status_code == 409
    assert body["type"] == "VersionConflictException"
    assert body["code"] == "STUDY_ARM_VERSION_CONFLICT"
    assert body["message"].startswith(
        "STUDY_ARM_VERSION_CONFLICT: study arm StudyArm_1"
    )
    assert body["details"] == [
        {
            "error_code": "STUDY_ARM_VERSION_CONFLICT",
            "field": ["body", "expected_start_date"],
            "msg": body["message"].removeprefix("STUDY_ARM_VERSION_CONFLICT: "),
            "ctx": {
                "study_arm_uid": "StudyArm_1",
                "current_start_date": WRITTEN_AT.isoformat(),
            },
        }
    ]
