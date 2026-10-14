from collections import defaultdict
from typing import Literal, NamedTuple
from uuid import NAMESPACE_URL, uuid5

# USDM 4.0.0 types most entity identifiers as non-empty strings, but the root
# Study.id is a UUID. Keep this explicit rather than changing every identifier:
# subordinate ids are cross-referenced as their existing stable strings.
UUID_ENTITY_CLASSES = frozenset({"Study"})
UUID_ID_NAMESPACE = "https://openstudybuilder.com/usdm/v4/id"


class IdTraceEvent(NamedTuple):
    """Internal allocation observation; association keys are opaque caller values.

    These events do not establish native identity, correspondence or authority.
    """

    sequence: int
    operation: Literal["allocate", "resolve"]
    entity_class: str
    association_key: str | None
    assigned_id: str


class IdManager:
    def __init__(self, *, record_trace: bool = False):
        self._ids = defaultdict(int)
        # Include the USDM entity class in the association key. An OSB UID may
        # legitimately be projected into more than one USDM entity family, and
        # those families must not accidentally share an ID.
        self._associated_ids: dict[tuple[str, str], str] = {}
        self._record_trace_enabled = record_trace
        self._trace_events: list[IdTraceEvent] = []
        self._trace_sequence = 0

    def trace_snapshot(self) -> tuple[IdTraceEvent, ...]:
        """Return immutable observations without exposing the mutable event list.

        An empty tuple is returned when tracing is disabled. Later allocations
        and resets cannot change a previously returned snapshot.
        """
        return tuple(self._trace_events)

    def _record_event(
        self,
        operation: Literal["allocate", "resolve"],
        entity_class: str,
        association_key: str | None,
        assigned_id: str,
    ) -> None:
        if self._record_trace_enabled:
            self._trace_sequence += 1
            self._trace_events.append(
                IdTraceEvent(
                    self._trace_sequence,
                    operation,
                    entity_class,
                    association_key,
                    assigned_id,
                )
            )

    def clear_all_ids(self) -> None:
        """Reset one complete USDM mapping run.

        Both counters and OSB-id associations are run-local. Keeping the latter
        across two studies makes a reused mapper resolve an OSB UID to an ID minted
        in a previous document, which breaks deterministic release hashing and can
        create dangling references. Trace observations and their sequence also
        start fresh; previously returned immutable snapshots are unchanged.
        """
        self._ids = defaultdict(int)
        self._associated_ids = {}
        self._trace_events.clear()
        self._trace_sequence = 0

    def clear_entity_id(self, entity_class: str) -> None:
        """Reset this class and discard its observations from the active trace.

        Other classes keep their associations and event sequence values. New
        events continue the run's sequence, so a snapshot can contain gaps.
        """
        self._ids[entity_class] = 0
        self._associated_ids = {
            key: value
            for key, value in self._associated_ids.items()
            if key[0] != entity_class
        }
        self._trace_events = [
            event for event in self._trace_events if event.entity_class != entity_class
        ]

    def get_id(self, entity_class: str, original_sb_id: str | None = None) -> str:
        association_key = (
            (entity_class, original_sb_id) if original_sb_id is not None else None
        )
        if association_key is not None and association_key in self._associated_ids:
            assigned_id = self._associated_ids[association_key]
            self._record_event("resolve", entity_class, original_sb_id, assigned_id)
            return assigned_id

        entity_number = self._ids[entity_class]
        self._ids[entity_class] += 1
        source_identity = (
            original_sb_id
            if original_sb_id is not None
            else f"generated:{entity_number + 1}"
        )
        generated_id = (
            str(
                uuid5(
                    NAMESPACE_URL,
                    f"{UUID_ID_NAMESPACE}/{entity_class}/{source_identity}",
                )
            )
            if entity_class in UUID_ENTITY_CLASSES
            else f"{entity_class}_{entity_number + 1}"
        )
        if association_key is not None:
            self._associated_ids[association_key] = generated_id
        self._record_event("allocate", entity_class, original_sb_id, generated_id)
        return generated_id
