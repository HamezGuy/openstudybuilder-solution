"""Action attribution must belong to the current writer, including deletion/reorder."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from clinical_mdr_api.domain_repositories.study_selections import study_arm_repository as module


class StudyArmActionAuthorTest(TestCase):
    def test_current_actor_is_passed_to_every_new_action_without_changing_old_record(self):
        for deleting in (True, False):
            with self.subTest(deleting=deleting):
                selection = SimpleNamespace(study_selection_uid="arm1", author_id="original-editor")
                other = SimpleNamespace(study_selection_uid="arm2", author_id="other-editor")
                aggregate = SimpleNamespace(study_uid="Study_isolated", repository_closure_data=(selection, other),
                                            study_arms_selection=(other,) if deleting else (other, selection))
                root = MagicMock()
                root.latest_locked.get_or_none.return_value = None
                repository = module.StudySelectionArmRepository()
                with patch.object(module, "StudyRoot") as root_type, patch.object(repository, "_add_new_selection") as add:
                    root_type.nodes.get.return_value = root
                    repository.save(aggregate, "current-editor")
                self.assertGreater(len(add.call_args_list), 0)
                self.assertTrue(all(call.kwargs["author_id"] == "current-editor" for call in add.call_args_list))
                self.assertEqual(selection.author_id, "original-editor")
                self.assertEqual(other.author_id, "other-editor")

    def test_native_version_writer_receives_action_author_and_preserves_full_selection(self):
        selection = SimpleNamespace(study_selection_uid="arm1", author_id="original-editor", name="arm", short_name="A", label="",
                                    code=None, description="Full 文🧪 " * 1500, randomization_group=None, number_of_subjects=0,
                                    merge_branch_for_this_arm_for_sdtm_adam=False, accepted_version=False, arm_type_uid=None)
        with patch.object(module, "StudyArm") as node, patch.object(module, "_manage_versioning_with_relations") as version:
            module.StudySelectionArmRepository._add_new_selection(MagicMock(), MagicMock(), 1, selection, module.Delete(),
                                                                 author_id="deleting-editor", for_deletion=True, before_node=MagicMock())
        self.assertEqual(version.call_args.kwargs["author_id"], "deleting-editor")
        self.assertEqual(node.call_args.kwargs["description"], selection.description)
        self.assertEqual(node.call_args.kwargs["number_of_subjects"], 0)
        self.assertIs(node.call_args.kwargs["merge_branch_for_this_arm_for_sdtm_adam"], False)
