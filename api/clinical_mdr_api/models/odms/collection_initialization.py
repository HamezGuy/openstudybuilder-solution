"""Exact native snapshots for an empty-or-identical collection initialization."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class OdmCollectionInitializationInput(BaseModel):
    # Snapshots are evidence bytes expressed as JSON. Do not apply input HTML
    # sanitization, aliases, defaults, or native DTO field filtering to them.
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_parent: dict[str, JsonValue]
    expected_children: dict[str, dict[str, JsonValue]]
    children: list[dict[str, JsonValue]] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_child_inventory(self) -> Self:
        uids = [child.get("uid") for child in self.children]
        if any(not isinstance(uid, str) or not uid.strip() for uid in uids):
            raise ValueError("ODM_COLLECTION_CHILD_ID_REQUIRED")
        if len(set(uids)) != len(uids):
            raise ValueError("ODM_COLLECTION_CHILD_ID_DUPLICATE")
        if set(self.expected_children) != set(uids):
            raise ValueError("ODM_COLLECTION_CHILD_SNAPSHOT_SET_MISMATCH")
        if any(value.get("uid") != uid for uid, value in self.expected_children.items()):
            raise ValueError("ODM_COLLECTION_CHILD_SNAPSHOT_ID_MISMATCH")
        return self
