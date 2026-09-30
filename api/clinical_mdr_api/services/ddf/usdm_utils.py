from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5


# USDM 4.0.0 types most entity identifiers as non-empty strings, but the root
# Study.id is a UUID. Keep this explicit rather than changing every identifier:
# subordinate ids are cross-referenced as their existing stable strings.
UUID_ENTITY_CLASSES = frozenset({"Study"})
UUID_ID_NAMESPACE = "https://openstudybuilder.com/usdm/v4/id"


class IdManager:
    def __init__(self):
        self._ids = defaultdict(int)
        # Include the USDM entity class in the association key. An OSB UID may
        # legitimately be projected into more than one USDM entity family, and
        # those families must not accidentally share an ID.
        self._associated_ids: dict[tuple[str, str], str] = {}

    def clear_all_ids(self) -> None:
        """Reset one complete USDM mapping run.

        Both counters and OSB-id associations are run-local. Keeping the latter
        across two studies makes a reused mapper resolve an OSB UID to an ID minted
        in a previous document, which breaks deterministic release hashing and can
        create dangling references.
        """
        self._ids = defaultdict(int)
        self._associated_ids = {}

    def clear_entity_id(self, entity_class: str) -> None:
        self._ids[entity_class] = 0
        self._associated_ids = {
            key: value
            for key, value in self._associated_ids.items()
            if key[0] != entity_class
        }

    def get_id(self, entity_class: str, original_sb_id: str | None = None) -> str:
        association_key = (
            (entity_class, original_sb_id) if original_sb_id is not None else None
        )
        if association_key is not None and association_key in self._associated_ids:
            return self._associated_ids[association_key]

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
        return generated_id
