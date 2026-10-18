"""Design-cell writers recheck study mutability inside their transaction."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from neomodel import db

from clinical_mdr_api.domains.study_selections.study_design_cell import (
    StudyDesignCellVO,
)
from clinical_mdr_api.models.study_selections.study_selection import (
    StudyDesignCellBatchInput,
    StudyDesignCellCreateInput,
    StudyDesignCellDeleteInput,
    StudyDesignCellEditInput,
)
from clinical_mdr_api.services import _utils
from clinical_mdr_api.services.studies import study_selection_base
from clinical_mdr_api.services.studies.study_design_cell import StudyDesignCellService
from clinical_mdr_api.services.user_info import UserInfoService
from common.exceptions import BusinessLogicException, NotFoundException

STUDY_UID = "Study_lock_regression"
OPERATIONS = (
    "create",
    "patch",
    "delete",
    "batch-create",
    "batch-patch",
    "batch-delete",
)


def _invoke(service, operation):
    method = operation.removeprefix("batch-")
    if method == "create":
        payload = StudyDesignCellCreateInput(
            study_arm_uid="Arm_new",
            study_epoch_uid="Epoch_1",
            study_element_uid="Element_1",
            transition_rule="Verbatim source rule",
            order=1,
        )
    elif method == "patch":
        payload = StudyDesignCellEditInput(
            study_design_cell_uid="Cell_1", transition_rule="Updated rule"
        )
    else:
        payload = StudyDesignCellDeleteInput(uid="Cell_1")
    if operation.startswith("batch-"):
        return service.handle_batch_operations(
            STUDY_UID,
            [
                StudyDesignCellBatchInput(
                    method={"create": "POST", "patch": "PATCH", "delete": "DELETE"}[
                        method
                    ],
                    content=payload,
                )
            ],
        )
    return getattr(service, method)(
        STUDY_UID, payload.uid if method == "delete" else payload
    )


@pytest.fixture(name="writer")
def fixture_writer(monkeypatch):
    events = []
    state = SimpleNamespace(locked=False, missing=False, lock_on_acquire=False)
    cells = [
        StudyDesignCellVO(
            uid=f"Cell_{index}",
            study_uid=STUDY_UID,
            study_arm_uid=f"Arm_{index}",
            study_epoch_uid="Epoch_1",
            study_element_uid="Element_1",
            transition_rule="Original rule",
            order=index,
            start_date=datetime(2026, 10, 14, tzinfo=timezone.utc),
            author_id="Original author",
            author_username="Original author",
        )
        for index in (1, 2)
    ]

    @contextmanager
    def transaction(database):
        assert database._active_transaction is None
        events.append("begin")
        database._active_transaction = object()
        try:
            yield
        except BaseException:
            events.append("rollback")
            raise
        else:
            events.append("commit")
        finally:
            database._active_transaction = None

    def acquire(uid):
        assert uid == STUDY_UID
        assert db._active_transaction is not None
        events.append("root-lock")
        if state.lock_on_acquire:
            state.locked = True

    def is_locked(study_uid):
        assert study_uid == STUDY_UID
        assert db._active_transaction is not None
        events.append("current-status")
        if state.missing:
            raise NotFoundException("Study", study_uid)
        return state.locked

    def all_cells(study_uid):
        assert study_uid == STUDY_UID
        events.append("read-cells")
        return deepcopy(cells)

    def find(study_uid, uid):
        assert study_uid == STUDY_UID
        events.append("read-cell")
        return deepcopy(next(cell for cell in cells if cell.uid == uid))

    def save(cell, author_id, create=False, previous_vo=None):
        assert author_id == "Current author"
        events.append("save-cell")
        if create:
            cell.uid = "Cell_new"
            cells.append(deepcopy(cell))
        else:
            index = next(i for i, item in enumerate(cells) if item.uid == cell.uid)
            cells[index] = deepcopy(cell)
            if previous_vo is not None:
                assert previous_vo.transition_rule == "Original rule"
        return deepcopy(cell)

    def delete(study_uid, uid, author_id):
        assert study_uid == STUDY_UID
        assert author_id == "Current author"
        events.append("delete-cell")
        cells[:] = [cell for cell in cells if cell.uid != uid]

    monkeypatch.setattr(db, "_active_transaction", None)
    monkeypatch.setattr(_utils, "TransactionProxy", transaction)
    monkeypatch.setattr(study_selection_base, "acquire_write_lock_study_value", acquire)
    monkeypatch.setattr(UserInfoService, "get_author_username_from_id", lambda uid: uid)
    service = StudyDesignCellService.__new__(StudyDesignCellService)
    service.author = "Current author"
    service._repos = SimpleNamespace(
        study_definition_repository=SimpleNamespace(check_if_study_is_locked=is_locked),
        study_design_cell_repository=SimpleNamespace(
            find_all_design_cells_by_study=all_cells,
            find_by_uid=find,
            save=save,
            delete=delete,
        ),
    )
    return SimpleNamespace(service=service, events=events, state=state, cells=cells)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_stale_unlocked_admission_cannot_write_after_lock_wait(writer, operation):
    before = deepcopy(writer.cells)
    # The router admitted the request while the study was mutable. A different
    # transaction commits LOCKED before this writer acquires the root lock.
    assert writer.state.locked is False
    writer.state.lock_on_acquire = True

    with pytest.raises(
        BusinessLogicException, match=f"Study with UID '{STUDY_UID}' is locked."
    ):
        _invoke(writer.service, operation)

    assert writer.cells == before
    assert writer.events == ["begin", "root-lock", "current-status", "rollback"]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_mutable_writes_keep_values_order_and_batch_responses(writer, operation):
    result = _invoke(writer.service, operation)
    assert writer.events[:3] == ["begin", "root-lock", "current-status"]
    assert writer.events[-1] == "commit"
    method = operation.removeprefix("batch-")
    if operation.startswith("batch-"):
        assert len(result) == 1
        assert (
            result[0].response_code
            == {"create": 201, "patch": 200, "delete": 204}[method]
        )
        result = result[0].content
    if method == "create":
        assert result.design_cell_uid == "Cell_new"
        assert result.transition_rule == "Verbatim source rule"
        assert result.order == 1
        assert {cell.uid: cell.order for cell in writer.cells} == {
            "Cell_1": 2,
            "Cell_2": 3,
            "Cell_new": 1,
        }
    elif method == "patch":
        assert result.transition_rule == "Updated rule"
        assert result.study_arm_uid == "Arm_1"
        assert result.study_element_uid == "Element_1"
        assert result.order == 1
        assert writer.cells[1].transition_rule == "Original rule"
    else:
        assert result is None
        assert [(cell.uid, cell.order) for cell in writer.cells] == [("Cell_2", 1)]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_missing_study_retains_not_found_before_selection_access(writer, operation):
    writer.state.missing = True
    with pytest.raises(NotFoundException) as error:
        _invoke(writer.service, operation)
    assert error.value.status_code == 404
    assert writer.events == ["begin", "root-lock", "current-status", "rollback"]


def test_existing_transaction_is_reused_without_releasing_its_root_lock(
    writer, monkeypatch
):
    outer_transaction = object()
    monkeypatch.setattr(db, "_active_transaction", outer_transaction)
    _invoke(writer.service, "batch-create")
    assert writer.events[:2] == ["root-lock", "current-status"]
    assert "begin" not in writer.events
    assert "commit" not in writer.events
    assert db._active_transaction is outer_transaction
