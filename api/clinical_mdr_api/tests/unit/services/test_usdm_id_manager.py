from operator import setitem
from uuid import UUID

import pytest

from clinical_mdr_api.services.ddf.usdm_utils import IdManager, IdTraceEvent


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


def test_tracing_is_opt_in_and_does_not_change_ids_or_allocation_order():
    ordinary = IdManager()
    traced = IdManager(record_trace=True)
    calls = [
        ("Study", "Study_000017"),
        ("StudyArm", "arm:first"),
        ("StudyEpoch", "arm:first"),
        ("Code", None),
        ("StudyArm", "arm:first"),
        ("Code", ""),
        ("Code", ""),
        ("Code", None),
        ("StudyArm", "arm:second"),
    ]

    ordinary_ids = [ordinary.get_id(*call) for call in calls]
    traced_ids = [traced.get_id(*call) for call in calls]

    assert traced_ids == ordinary_ids
    assert traced_ids[1:] == [
        "StudyArm_1",
        "StudyEpoch_1",
        "Code_1",
        "StudyArm_1",
        "Code_2",
        "Code_2",
        "Code_3",
        "StudyArm_2",
    ]
    assert isinstance(ordinary.trace_snapshot(), tuple)
    assert not ordinary.trace_snapshot()
    assert len(traced.trace_snapshot()) == len(calls)


def test_forward_and_repeated_resolution_keep_the_class_qualified_opaque_key():
    manager = IdManager(record_trace=True)
    opaque_key = "field:name/branch:7~selection"

    forward = manager.get_id("StudyArm", opaque_key)
    other_class = manager.get_id("StudyEpoch", opaque_key)
    constructed = manager.get_id("StudyArm", opaque_key)
    repeated = manager.get_id("StudyArm", opaque_key)

    assert forward == constructed == repeated == "StudyArm_1"
    assert other_class == "StudyEpoch_1"
    assert manager.trace_snapshot() == (
        IdTraceEvent(1, "allocate", "StudyArm", opaque_key, "StudyArm_1"),
        IdTraceEvent(2, "allocate", "StudyEpoch", opaque_key, "StudyEpoch_1"),
        IdTraceEvent(3, "resolve", "StudyArm", opaque_key, "StudyArm_1"),
        IdTraceEvent(4, "resolve", "StudyArm", opaque_key, "StudyArm_1"),
    )


def test_generated_ids_keep_null_keys_distinct_from_an_empty_association_key():
    manager = IdManager(record_trace=True)

    assert manager.get_id("Code") == "Code_1"
    assert manager.get_id("Code", None) == "Code_2"
    assert manager.get_id("Code", "") == "Code_3"
    assert manager.get_id("Code", "") == "Code_3"
    assert manager.trace_snapshot() == (
        IdTraceEvent(1, "allocate", "Code", None, "Code_1"),
        IdTraceEvent(2, "allocate", "Code", None, "Code_2"),
        IdTraceEvent(3, "allocate", "Code", "", "Code_3"),
        IdTraceEvent(4, "resolve", "Code", "", "Code_3"),
    )


def test_clear_all_starts_a_new_trace_without_changing_an_existing_snapshot():
    manager = IdManager(record_trace=True)
    first = manager.get_id("Study", "Study_000017")
    manager.get_id("StudyArm", "arm:first")
    snapshot = manager.trace_snapshot()

    manager.clear_all_ids()

    assert not manager.trace_snapshot()
    assert manager.get_id("Study", "Study_000017") == first
    assert manager.get_id("StudyArm", "arm:second") == "StudyArm_1"
    assert manager.trace_snapshot() == (
        IdTraceEvent(1, "allocate", "Study", "Study_000017", first),
        IdTraceEvent(2, "allocate", "StudyArm", "arm:second", "StudyArm_1"),
    )
    assert snapshot == (
        IdTraceEvent(1, "allocate", "Study", "Study_000017", first),
        IdTraceEvent(2, "allocate", "StudyArm", "arm:first", "StudyArm_1"),
    )


def test_clear_entity_resets_only_its_ids_and_trace_without_renumbering_other_events():
    ordinary = IdManager()
    manager = IdManager(record_trace=True)
    for candidate in (ordinary, manager):
        candidate.get_id("StudyArm", "shared")
        candidate.get_id("StudyEpoch", "shared")
        candidate.get_id("StudyArm", "shared")
    snapshot = manager.trace_snapshot()

    ordinary.clear_entity_id("StudyArm")
    manager.clear_entity_id("StudyArm")

    assert manager.trace_snapshot() == (
        IdTraceEvent(2, "allocate", "StudyEpoch", "shared", "StudyEpoch_1"),
    )
    calls = [("StudyArm", "new"), ("StudyEpoch", "shared"), ("StudyArm", "shared")]
    assert (
        [manager.get_id(*call) for call in calls]
        == [ordinary.get_id(*call) for call in calls]
        == ["StudyArm_1", "StudyEpoch_1", "StudyArm_2"]
    )
    assert manager.trace_snapshot() == (
        IdTraceEvent(2, "allocate", "StudyEpoch", "shared", "StudyEpoch_1"),
        IdTraceEvent(4, "allocate", "StudyArm", "new", "StudyArm_1"),
        IdTraceEvent(5, "resolve", "StudyEpoch", "shared", "StudyEpoch_1"),
        IdTraceEvent(6, "allocate", "StudyArm", "shared", "StudyArm_2"),
    )
    assert snapshot == (
        IdTraceEvent(1, "allocate", "StudyArm", "shared", "StudyArm_1"),
        IdTraceEvent(2, "allocate", "StudyEpoch", "shared", "StudyEpoch_1"),
        IdTraceEvent(3, "resolve", "StudyArm", "shared", "StudyArm_1"),
    )
    assert not ordinary.trace_snapshot()


def test_trace_snapshot_and_events_cannot_mutate_the_managers_observations():
    manager = IdManager(record_trace=True)
    manager.get_id("StudyArm", "arm:first")
    snapshot = manager.trace_snapshot()
    expected = (IdTraceEvent(1, "allocate", "StudyArm", "arm:first", "StudyArm_1"),)

    with pytest.raises(TypeError):
        setitem(snapshot, 0, snapshot[0])
    with pytest.raises(AttributeError):
        setattr(snapshot[0], "assigned_id", "changed")
    copied_events = list(snapshot)
    copied_events.clear()
    manager.get_id("StudyEpoch", "epoch:first")

    assert snapshot == expected
    assert manager.trace_snapshot() == (
        *expected,
        IdTraceEvent(2, "allocate", "StudyEpoch", "epoch:first", "StudyEpoch_1"),
    )
