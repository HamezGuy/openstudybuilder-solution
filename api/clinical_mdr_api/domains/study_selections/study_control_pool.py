"""Authored shared-control pools and thin adaptation references (plan item O7).

A control pool records which native arms donate controls and which focal arms
the pool serves. An adaptation reference records an authored adaptation and
the exact published CSL statistical fact it relies on. Neither copies an
alpha, boundary, borrowing, concurrency window, denominator or allocation rule:
those stay in the referenced CSL and Workbench facts. A reference grants no
statistical, allocation or execution authority, and OSB does not verify the
referenced fact; verification belongs to CSL and Workbench.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

from clinical_mdr_api.domains.study_selections.study_selection_scope import (
    canonical_scope,
)
from common.exceptions import ValidationException

HASH_PATTERN = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
CONTROL_POOL_COLLECTION = "controlPools"
CONTROL_POOL_REPORT = "control-pool-report/1"


def _uids(values: Any, name: str) -> tuple[str, ...]:
    try:
        return canonical_scope(values)
    except ValueError as error:
        raise ValidationException(msg=f"{name}: {error}") from error


def _text(value: Any, name: str) -> str:
    ValidationException.raise_if(
        not isinstance(value, str) or not value.strip(),
        msg=f"{name} must be explicit nonblank text",
    )
    return value


@dataclass(frozen=True)
class CslStatisticalReferenceVO:
    """Exact pins of one published CSL biometrics publication entry.

    The fields mirror the Workbench `cslSource` pin. A hash identifies bytes;
    it does not establish approval.
    """

    publication_entity_id: str
    publication_hash: str
    analysis_model_hash: str
    collection: str
    entry_id: str
    entry_hash: str

    def __post_init__(self) -> None:
        for name in ("publication_entity_id", "collection", "entry_id"):
            _text(getattr(self, name), name)
        for name in ("publication_hash", "analysis_model_hash", "entry_hash"):
            value = getattr(self, name)
            ValidationException.raise_if(
                not isinstance(value, str) or not HASH_PATTERN.match(value),
                msg=f"{name} must be a lowercase SHA-256 digest",
            )

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str | None) -> Self | None:
        if value is None:
            return None
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValidationException(msg="A stored CSL reference has an invalid shape")
        return cls(**payload)


@dataclass(frozen=True)
class StudyControlPoolVO:
    uid: str
    study_uid: str
    name: str
    donor_arm_uids: tuple[str, ...]
    focal_arm_uids: tuple[str, ...]
    csl_reference: CslStatisticalReferenceVO | None
    description: str | None
    author_id: str
    start_date: datetime
    author_username: str | None = None

    def __post_init__(self) -> None:
        _text(self.name, "Control pool name")
        donors = _uids(self.donor_arm_uids, "Donor arms")
        focal = _uids(self.focal_arm_uids, "Focal arms")
        ValidationException.raise_if(
            not donors or not focal,
            msg="A control pool names at least one donor arm and one focal arm",
        )
        ValidationException.raise_if(
            bool(set(donors) & set(focal)),
            msg="An arm cannot be both a donor and a focal arm of one pool",
        )
        object.__setattr__(self, "donor_arm_uids", donors)
        object.__setattr__(self, "focal_arm_uids", focal)
        if self.csl_reference is not None:
            ValidationException.raise_if(
                self.csl_reference.collection != CONTROL_POOL_COLLECTION,
                msg=f"A control pool references a CSL {CONTROL_POOL_COLLECTION} entry",
            )

    def arm_uids(self) -> tuple[str, ...]:
        return tuple(sorted({*self.donor_arm_uids, *self.focal_arm_uids}))


@dataclass(frozen=True)
class StudyAdaptationReferenceVO:
    uid: str
    study_uid: str
    adaptation_type: str
    csl_reference: CslStatisticalReferenceVO
    affected_arm_uids: tuple[str, ...] = field(default=())
    affected_epoch_uids: tuple[str, ...] = field(default=())
    trigger_reference: str | None = None
    description: str | None = None
    author_id: str = ""
    start_date: datetime | None = None
    author_username: str | None = None

    def __post_init__(self) -> None:
        _text(self.adaptation_type, "Adaptation type")
        ValidationException.raise_if(
            not isinstance(self.csl_reference, CslStatisticalReferenceVO),
            msg="An adaptation reference requires an exact CSL statistical reference",
        )
        if self.trigger_reference is not None:
            _text(self.trigger_reference, "Trigger reference")
        object.__setattr__(
            self, "affected_arm_uids", _uids(self.affected_arm_uids, "Affected arms")
        )
        object.__setattr__(
            self,
            "affected_epoch_uids",
            _uids(self.affected_epoch_uids, "Affected epochs"),
        )
