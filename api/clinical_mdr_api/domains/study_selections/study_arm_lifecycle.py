"""Immutable authored arm lifecycle history, without execution authority."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from common.exceptions import ValidationException

LifecycleTargetKind = Literal["arm", "branch_arm"]


def validate_lifecycle_target(target_kind: str, target_uid: str) -> None:
    ValidationException.raise_if(
        target_kind not in ("arm", "branch_arm"),
        msg="Lifecycle target kind must be arm or branch_arm.",
    )
    ValidationException.raise_if(
        not isinstance(target_uid, str) or not target_uid.strip(),
        msg="Lifecycle target UID must be provided.",
    )


@dataclass(frozen=True)
class StudyArmLifecycleEventVO:
    uid: str
    study_uid: str
    target_kind: LifecycleTargetKind
    target_uid: str
    lifecycle_state: str
    effective_date: datetime
    decision_reference: str
    change_reason: str
    recorded_at: datetime
    author_id: str

    def __post_init__(self) -> None:
        validate_lifecycle_target(self.target_kind, self.target_uid)
        for field in (
            "uid",
            "study_uid",
            "lifecycle_state",
            "decision_reference",
            "change_reason",
            "author_id",
        ):
            value = getattr(self, field)
            ValidationException.raise_if(
                not isinstance(value, str) or not value.strip(),
                msg=f"Lifecycle event {field} must be provided.",
            )
        for field in ("effective_date", "recorded_at"):
            value = getattr(self, field)
            ValidationException.raise_if(
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None,
                msg=f"Lifecycle event {field} must include a timezone.",
            )
        # Native ZonedDateTimeProperty persists UTC. Normalize after validation
        # so create responses and hydrated records retain the same wire shape.
        for field in ("effective_date", "recorded_at"):
            object.__setattr__(
                self, field, getattr(self, field).astimezone(timezone.utc)
            )

    @property
    def sort_key(self) -> tuple[datetime, datetime, str]:
        return self.effective_date, self.recorded_at, self.uid
