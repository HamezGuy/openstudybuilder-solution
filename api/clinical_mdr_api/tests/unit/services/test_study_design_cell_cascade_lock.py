"""Cascade deletes recheck study mutability before accessing any selections."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.study_selections.study_design_cell_repository import (
    StudyDesignCellRepository,
)
from clinical_mdr_api.domain_repositories.study_selections.study_selection_scope_repository import (
    StudySelectionScopeRepository,
)
from clinical_mdr_api.domain_repositories.study_selections.study_visit_repository import (
    StudyVisitRepository,
)
from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyStatus,
)
from clinical_mdr_api.domains.study_selections.study_epoch import StudyEpochVO
from clinical_mdr_api.models.controlled_terminologies.ct_term import (
    SimpleCTTermNameWithConflictFlag,
)
from clinical_mdr_api.services.studies import study_selection_base
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from clinical_mdr_api.services.studies.study_element_selection import (
    StudyElementSelectionService,
)
from clinical_mdr_api.services.studies.study_epoch import StudyEpochService
from common.exceptions import BusinessLogicException, NotFoundException

STUDY_UID = "Study_cascade_lock"
AUTHOR = "Synthetic cascade author"
SERVICES = {
    "arm": StudyArmSelectionService,
    "element": StudyElementSelectionService,
    "epoch": StudyEpochService,
}


def _epoch(uid, order):
    term = SimpleCTTermNameWithConflictFlag(
        term_uid="Subtype_1", sponsor_preferred_name="", date_conflict=False
    )
    return StudyEpochVO(
        uid=uid,
        study_uid=STUDY_UID,
        start_rule="Existing start rule",
        end_rule=None,
        epoch=term,
        subtype=term,
        epoch_type=term,
        description="Existing epoch description",
        order=order,
        status=StudyStatus.DRAFT,
        start_date=datetime(2026, 10, 15, tzinfo=timezone.utc),
        author_id=AUTHOR,
        author_username=AUTHOR,
    )


@pytest.fixture(name="cascade", params=SERVICES)
def fixture_cascade(request, monkeypatch):
    kind = request.param
    events = []
    state = SimpleNamespace(locked=False, missing=False, lock_on_acquire=False)
    cells = [
        SimpleNamespace(
            uid=f"Cell_{index}",
            study_uid=STUDY_UID,
            order=index,
            transition_rule=f"Unchanged rule {index}",
        )
        for index in (1, 2, 3)
    ]

    def begin(**_kwargs):
        assert db._active_transaction is None
        events.append("begin")
        db._active_transaction = object()

    def finish(event):
        assert db._active_transaction is not None
        events.append(event)
        db._active_transaction = None

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

    def read_cells(study_uid, **selection):
        assert study_uid == STUDY_UID
        events.append("read-cells")
        if selection:
            assert selection == {f"study_{kind}_uid": "Selection_1"}
            return deepcopy([cells[0], cells[2]])
        return deepcopy(cells)

    def read_cell(study_uid, uid):
        assert study_uid == STUDY_UID
        events.append("read-cell")
        return deepcopy(next(cell for cell in cells if cell.uid == uid))

    def delete_cell(study_uid, uid, author_id):
        assert study_uid == STUDY_UID
        assert author_id == AUTHOR
        events.append(("delete-cell", uid))
        cells[:] = [cell for cell in cells if cell.uid != uid]

    def save_cell(cell, author_id, create):
        assert author_id == AUTHOR
        assert create is False
        events.append(("save-cell", cell.uid))
        index = next(i for i, item in enumerate(cells) if item.uid == cell.uid)
        cells[index] = deepcopy(cell)

    def observed(result, name):
        def call(*_args, **_kwargs):
            events.append(name)
            return result

        return Mock(side_effect=call)

    monkeypatch.setattr(db, "_active_transaction", None)
    monkeypatch.setattr(db, "begin", begin)
    monkeypatch.setattr(db, "commit", lambda: finish("commit"))
    monkeypatch.setattr(db, "rollback", lambda: finish("rollback"))
    monkeypatch.setattr(study_selection_base, "acquire_write_lock_study_value", acquire)
    monkeypatch.setattr(
        StudyDesignCellRepository, "find_all_design_cells_by_study", read_cells
    )
    visits = observed([], "read-visits")
    monkeypatch.setattr(StudyVisitRepository, "find_all_visits_by_study_uid", visits)
    # O5 scope guard: a referenced arm, cohort or epoch refuses deletion.
    monkeypatch.setattr(
        StudySelectionScopeRepository,
        "current_references",
        observed([], "read-scope-references"),
    )
    selection = Mock()
    selection_repository = SimpleNamespace(
        find_by_study=observed(selection, "read-selection"),
        save=observed(None, "save-selection"),
    )
    setattr(
        selection_repository,
        f"{kind}_specific_has_connected_cell",
        observed(True, "read-cell-membership"),
    )
    branches = SimpleNamespace(
        get_branch_arms_connected_to_arm=observed([], "read-branches")
    )
    dosing = SimpleNamespace(
        study_compound_dosings_selection=[
            SimpleNamespace(
                study_selection_uid="Dose_1", study_element_uid="Selection_1"
            ),
            SimpleNamespace(study_selection_uid="Dose_2", study_element_uid="Other"),
        ],
        remove_compound_dosing_selection=Mock(),
    )
    dosing_repository = SimpleNamespace(
        find_by_study=observed(dosing, "read-dosings"),
        save=observed(None, "save-dosings"),
    )
    deleted_epoch = _epoch("Selection_1", 1)
    retained_epoch = _epoch("Epoch_retained", 2)
    epochs = SimpleNamespace(
        epoch_specific_has_connected_design_cell=observed(True, "read-cell-membership"),
        find_by_uid=observed(deleted_epoch, "read-selection"),
        save=observed(None, "save-selection"),
        find_all_epochs_by_study=observed([retained_epoch], "read-epochs"),
    )
    service = SERVICES[kind].__new__(SERVICES[kind])
    service.author = AUTHOR
    service._repos = SimpleNamespace(
        study_definition_repository=SimpleNamespace(check_if_study_is_locked=is_locked),
        study_design_cell_repository=SimpleNamespace(
            find_all_design_cells_by_study=read_cells,
            find_by_uid=read_cell,
            delete=delete_cell,
            save=save_cell,
        ),
        study_arm_repository=selection_repository,
        study_element_repository=selection_repository,
        study_branch_arm_repository=branches,
        study_compound_dosing_repository=dosing_repository,
        close=lambda: events.append("close"),
    )
    service.repo = epochs
    return SimpleNamespace(
        kind=kind,
        service=service,
        state=state,
        events=events,
        cells=cells,
        selection=selection,
        selection_repository=selection_repository,
        dosing=dosing,
        dosing_repository=dosing_repository,
        deleted_epoch=deleted_epoch,
        retained_epoch=retained_epoch,
        epochs=epochs,
        visits=visits,
    )


def _invoke(cascade):
    method = "delete" if cascade.kind == "epoch" else "delete_selection"
    return getattr(cascade.service, method)(STUDY_UID, "Selection_1")


def test_stale_unlocked_admission_refuses_before_any_cascade_access(cascade):
    before = deepcopy(cascade.cells)
    # Admission saw DRAFT; another transaction commits LOCKED while this request
    # waits for the StudyRoot lock. The fresh check must precede all cascade I/O.
    assert cascade.state.locked is False
    cascade.state.lock_on_acquire = True
    with pytest.raises(
        BusinessLogicException, match=f"Study with UID '{STUDY_UID}' is locked."
    ):
        _invoke(cascade)
    assert cascade.cells == before
    cleanup = [] if cascade.kind == "epoch" else ["close"]
    assert cascade.events == [
        "begin",
        "root-lock",
        "current-status",
        *cleanup,
        "rollback",
    ]


def test_mutable_cascade_preserves_delete_ordering_and_related_operations(cascade):
    assert _invoke(cascade) is None
    assert cascade.events[:3] == ["begin", "root-lock", "current-status"]
    assert cascade.events[-1] == "commit"
    assert [(cell.uid, cell.order, cell.transition_rule) for cell in cascade.cells] == [
        ("Cell_2", 1, "Unchanged rule 2")
    ]
    if cascade.kind == "epoch":
        assert cascade.deleted_epoch.is_deleted
        assert cascade.retained_epoch.order == 1
        assert cascade.retained_epoch.description == "Existing epoch description"
        assert cascade.epochs.save.call_args_list[0].args == (cascade.deleted_epoch,)
        assert cascade.epochs.save.call_args_list[1].args == (cascade.retained_epoch,)
        cascade.visits.assert_called_once_with(STUDY_UID)
    else:
        getattr(
            cascade.selection, f"remove_{cascade.kind}_selection"
        ).assert_called_once_with("Selection_1")
        cascade.selection_repository.find_by_study.assert_called_once_with(
            study_uid=STUDY_UID, for_update=True
        )
        cascade.selection_repository.save.assert_called_once_with(
            cascade.selection, AUTHOR
        )
        assert cascade.events[-2] == "close"
        if cascade.kind == "element":
            cascade.dosing.remove_compound_dosing_selection.assert_called_once_with(
                "Dose_1"
            )
            cascade.dosing_repository.save.assert_called_once_with(
                cascade.dosing, AUTHOR
            )


def test_missing_study_remains_not_found_before_any_cascade_access(cascade):
    cascade.state.missing = True
    with pytest.raises(NotFoundException) as error:
        _invoke(cascade)
    assert error.value.status_code == 404
    cleanup = [] if cascade.kind == "epoch" else ["close"]
    assert cascade.events == [
        "begin",
        "root-lock",
        "current-status",
        *cleanup,
        "rollback",
    ]
