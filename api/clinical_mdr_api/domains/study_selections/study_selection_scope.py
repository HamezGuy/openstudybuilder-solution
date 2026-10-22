"""Authored arm, cohort and epoch applicability of a study selection.

Each dimension is an explicit set of native UIDs from the same study version.
An empty dimension records no authored scope in that dimension; it never
means "every arm". Several populated dimensions are retained side by side
with their combination not stated: no union, intersection, inheritance from
an objective, estimand or execution authority is inferred.
"""

from dataclasses import dataclass
from typing import Any, Iterable, Self

SCOPE_FIELDS = (
    "applies_to_arm_uids",
    "applies_to_cohort_uids",
    "applies_to_epoch_uids",
)
SCOPE_TARGETS = {
    "applies_to_arm_uids": ("HAS_STUDY_ARM", "StudyArm"),
    "applies_to_cohort_uids": ("HAS_STUDY_COHORT", "StudyCohort"),
    "applies_to_epoch_uids": ("HAS_STUDY_EPOCH", "StudyEpoch"),
}


def canonical_scope(values: Iterable[str] | None) -> tuple[str, ...]:
    """Sorted unique native UIDs; order carries no meaning in a scope set."""
    if values is None:
        return ()
    items = tuple(values)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError("Scope targets must be explicit nonblank native UIDs")
    if len(set(items)) != len(items):
        raise ValueError("Scope targets must be unique within each dimension")
    return tuple(sorted(items))


@dataclass(frozen=True)
class StudySelectionScopeVO:
    applies_to_arm_uids: tuple[str, ...] = ()
    applies_to_cohort_uids: tuple[str, ...] = ()
    applies_to_epoch_uids: tuple[str, ...] = ()

    @classmethod
    def from_values(
        cls,
        applies_to_arm_uids: Iterable[str] | None = None,
        applies_to_cohort_uids: Iterable[str] | None = None,
        applies_to_epoch_uids: Iterable[str] | None = None,
    ) -> Self:
        return cls(
            applies_to_arm_uids=canonical_scope(applies_to_arm_uids),
            applies_to_cohort_uids=canonical_scope(applies_to_cohort_uids),
            applies_to_epoch_uids=canonical_scope(applies_to_epoch_uids),
        )

    @classmethod
    def from_record(cls, record: Any) -> Self:
        """Read stored lists; a selection saved before O5 has no authored scope."""
        getter = (
            record.get
            if isinstance(record, dict)
            else lambda name: getattr(record, name, None)
        )
        return cls.from_values(**{name: getter(name) for name in SCOPE_FIELDS})

    def identity(self) -> tuple[tuple[str, ...], ...]:
        return (
            self.applies_to_arm_uids,
            self.applies_to_cohort_uids,
            self.applies_to_epoch_uids,
        )

    def is_unscoped(self) -> bool:
        return not any(self.identity())

    def description(self) -> str:
        """Each populated dimension listed in order; empty when none is authored."""
        return "; ".join(
            f"{label} {', '.join(uids)}"
            for label, uids in (
                ("arms", self.applies_to_arm_uids),
                ("cohorts", self.applies_to_cohort_uids),
                ("epochs", self.applies_to_epoch_uids),
            )
            if uids
        )

    def as_lists(self) -> dict[str, list[str]]:
        return {name: list(getattr(self, name)) for name in SCOPE_FIELDS}
