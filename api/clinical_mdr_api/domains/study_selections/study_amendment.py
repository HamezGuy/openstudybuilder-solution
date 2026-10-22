"""Explicit authored amendment facts, without inferred scope or authority."""

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StudyAmendmentVO:
    amendment_type: str
    affected_arm_uids: tuple[str, ...]
    authorizing_decision_ref: str

    def __post_init__(self) -> None:
        for value in (self.amendment_type, self.authorizing_decision_ref):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    "Amendment type and decision reference must be nonblank"
                )
        if not isinstance(self.affected_arm_uids, tuple) or any(
            not isinstance(uid, str) or not uid.strip()
            for uid in self.affected_arm_uids
        ):
            raise ValueError(
                "Affected arm identifiers must be explicit nonblank strings"
            )
        if len(set(self.affected_arm_uids)) != len(self.affected_arm_uids):
            raise ValueError("Affected arm identifiers must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "amendment_type": self.amendment_type,
            "affected_arm_uids": list(self.affected_arm_uids),
            "authorizing_decision_ref": self.authorizing_decision_ref,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "StudyAmendmentVO":
        payload = json.loads(value)
        if not isinstance(payload, dict) or set(payload) != {
            "amendment_type",
            "affected_arm_uids",
            "authorizing_decision_ref",
        }:
            raise ValueError("Stored amendment has an invalid shape")
        if not isinstance(payload["affected_arm_uids"], list):
            raise ValueError("Stored affected arm identifiers must be an array")
        return cls(
            amendment_type=payload["amendment_type"],
            affected_arm_uids=tuple(payload["affected_arm_uids"]),
            authorizing_decision_ref=payload["authorizing_decision_ref"],
        )
