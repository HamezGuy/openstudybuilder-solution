from uuid import UUID

from clinical_mdr_api.services.ddf.usdm_utils import IdManager


def test_root_study_id_is_a_deterministic_uuid_for_the_osb_study():
    first = IdManager()
    second = IdManager()

    first_id = first.get_id("Study", "Study_000017")

    assert UUID(first_id).version == 5
    assert first_id == first.get_id("Study", "Study_000017")
    assert first_id == second.get_id("Study", "Study_000017")
    assert first_id != second.get_id("Study", "Study_000018")


def test_subordinate_usdm_ids_remain_typed_strings_and_class_isolated():
    manager = IdManager()

    assert manager.get_id("StudyArm", "Selection_1") == "StudyArm_1"
    assert manager.get_id("StudyEpoch", "Selection_1") == "StudyEpoch_1"
    assert manager.get_id("StudyArm", "Selection_1") == "StudyArm_1"


def test_clear_all_ids_preserves_deterministic_root_identity():
    manager = IdManager()
    before = manager.get_id("Study", "Study_000017")

    manager.clear_all_ids()

    assert manager.get_id("Study", "Study_000017") == before
