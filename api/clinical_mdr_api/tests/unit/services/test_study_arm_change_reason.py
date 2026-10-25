"""The optional study arm change reason and OSB_ARM_WRITE_REASON_REQUIRED
(FX-OSB-09), without a database."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionArmBatchInput,
    StudySelectionArmBatchUpdateInput,
    StudySelectionArmCreateInput,
    StudySelectionArmInput,
    StudySelectionArmVersion,
)
from clinical_mdr_api.services.studies import study_arm_selection as service_module
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from common.config import settings
from common.exceptions import ValidationException


@pytest.mark.parametrize(
    "model", [StudySelectionArmCreateInput, StudySelectionArmInput]
)
def test_reason_is_optional_trimmed_and_at_most_4000_characters(model):
    assert model(name="A").change_reason is None
    assert model(change_reason="  Why  ").change_reason == "Why"
    assert model(change_reason="   ").change_reason is None
    assert model(change_reason="x" * 4000).change_reason == "x" * 4000
    with pytest.raises(ValidationError):
        model(change_reason="x" * 4001)


def test_batch_items_carry_their_reason():
    post = StudySelectionArmBatchInput(
        method="POST", content={"name": "A", "change_reason": "Add A"}
    )
    patch_item = StudySelectionArmBatchInput(
        method="PATCH", content={"arm_uid": "StudyArm_1", "change_reason": "Edit A"}
    )
    assert post.content.change_reason == "Add A"
    assert isinstance(patch_item.content, StudySelectionArmBatchUpdateInput)
    assert patch_item.content.change_reason == "Edit A"


def test_reason_is_required_only_behind_the_flag(monkeypatch):
    assert settings.arm_write_reason_required is False
    service_module._require_change_reason(None)
    monkeypatch.setattr(settings, "arm_write_reason_required", True)
    service_module._require_change_reason("Why")
    with pytest.raises(ValidationException) as error:
        service_module._require_change_reason(None)
    assert error.value.status_code == 400
    assert "STUDY_ARM_CHANGE_REASON_REQUIRED" in error.value.msg


def test_flag_on_refuses_a_reasonless_write_before_any_repository_call(monkeypatch):
    monkeypatch.setattr(settings, "arm_write_reason_required", True)
    service = StudyArmSelectionService.__new__(StudyArmSelectionService)
    service._repos = MagicMock()
    service.author = "current-editor"
    with pytest.raises(ValidationException):
        StudyArmSelectionService.make_selection.__wrapped__(  # type: ignore[attr-defined]
            service, "Study_1", StudySelectionArmCreateInput(name="A")
        )
    with pytest.raises(ValidationException):
        StudyArmSelectionService.patch_selection.__wrapped__(  # type: ignore[attr-defined]
            service, "Study_1", "StudyArm_1", StudySelectionArmInput(name="B")
        )
    with pytest.raises(ValidationException):
        StudyArmSelectionService.delete_selection.__wrapped__(  # type: ignore[attr-defined]
            service, "Study_1", "StudyArm_1"
        )
    assert not service._repos.mock_calls


def test_audit_trail_returns_a_reason_only_where_the_action_has_one():
    versions = [
        StudySelectionArmVersion.model_construct(changes=[], name="A"),
        StudySelectionArmVersion.model_construct(changes=[], name="A"),
    ]
    histories = [
        SimpleNamespace(change_reason="Describe A"),
        SimpleNamespace(change_reason=None),
    ]
    service_module._with_change_reasons(versions, histories)
    edited, created = (version.model_dump(exclude_unset=True) for version in versions)
    assert edited["change_reason"] == "Describe A"
    assert "change_reason" not in created


def test_refuse_mode_resubmission_ignores_the_reason():
    existing = SimpleNamespace(
        study_selection_uid="StudyArm_1", name="A", short_name="AS"
    )
    entry = service_module._ArmConflictIndexEntry(vo=existing)
    indexes = service_module._ArmConflictIndexes(
        by_name={"A": entry},
        by_short_name={"AS": entry},
        by_code={},
        by_randomization_group={},
    )
    spec = StudySelectionArmCreateInput(
        name="A", short_name="AS", change_reason="Resubmit A"
    )
    service = StudyArmSelectionService.__new__(StudyArmSelectionService)
    assert service._refuse_identity_conflict(spec, indexes) is existing
