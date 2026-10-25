"""A study arm write's reason goes to that arm's own action only (FX-OSB-09)."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from clinical_mdr_api.domain_repositories.study_selections import (
    study_arm_repository as module,
)


def _save(closure, current, **kwargs):
    aggregate = SimpleNamespace(
        study_uid="Study_isolated",
        repository_closure_data=closure,
        study_arms_selection=current,
    )
    root = MagicMock()
    root.latest_locked.get_or_none.return_value = None
    repository = module.StudySelectionArmRepository()
    with (
        patch.object(module, "StudyRoot") as root_type,
        patch.object(repository, "_add_new_selection") as add,
    ):
        root_type.nodes.get.return_value = root
        # The undecorated save runs without a database driver.
        module.StudySelectionArmRepository.save.__wrapped__(  # type: ignore[attr-defined]
            repository, aggregate, "current-editor", **kwargs
        )
    return {
        (call.args[3].study_selection_uid, type(call.args[4]).__name__): call.kwargs[
            "action_properties"
        ]
        for call in add.call_args_list
    }


class StudyArmChangeReasonTest(TestCase):
    def test_delete_reason_is_on_the_delete_action_not_on_the_shifted_arm(self):
        deleted = SimpleNamespace(study_selection_uid="arm1")
        shifted = SimpleNamespace(study_selection_uid="arm2")
        actions = _save(
            (deleted, shifted), (shifted,), change_reasons={"arm1": "Drop arm 1"}
        )
        self.assertEqual(actions[("arm1", "Delete")], {"change_reason": "Drop arm 1"})
        self.assertEqual(actions[("arm2", "Edit")], {})

    def test_create_reason_joins_the_copy_provenance(self):
        existing = SimpleNamespace(study_selection_uid="arm1")
        created = SimpleNamespace(study_selection_uid="arm2")
        actions = _save(
            (existing,),
            (existing, created),
            create_action_properties={"arm2": {"source_study_uid": "Study_src"}},
            change_reasons={"arm2": "Copy arm 2"},
        )
        self.assertEqual(
            actions,
            {
                ("arm2", "Create"): {
                    "source_study_uid": "Study_src",
                    "change_reason": "Copy arm 2",
                }
            },
        )

    def test_no_reason_adds_no_property(self):
        existing = SimpleNamespace(study_selection_uid="arm1")
        created = SimpleNamespace(study_selection_uid="arm2")
        actions = _save((existing,), (existing, created))
        self.assertEqual(actions, {("arm2", "Create"): {}})

    def test_the_reason_is_a_property_every_arm_action_type_stores(self):
        for action_type in (module.Create, module.Edit, module.Delete):
            with self.subTest(action_type=action_type.__name__):
                self.assertEqual(
                    action_type.deflate({"change_reason": "Why"}, skip_empty=True),
                    {"change_reason": "Why"},
                )
