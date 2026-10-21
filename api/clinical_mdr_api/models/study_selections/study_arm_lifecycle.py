"""Authored lifecycle records do not constitute executed allocation decisions."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from clinical_mdr_api.domains.study_selections.study_arm_lifecycle import (
    LifecycleTargetKind,
    StudyArmLifecycleEventVO,
)
from clinical_mdr_api.models.utils import PostInputModel


class StudyArmLifecycleEventInput(PostInputModel):
    model_config = ConfigDict(extra="forbid")

    expected_study_version: Annotated[str, Field(min_length=1)]
    lifecycle_state: Annotated[
        str,
        Field(
            min_length=1,
            description="Authored state label. No governed CT or operational state is inferred.",
        ),
    ]
    effective_date: Annotated[datetime, AwareDatetime]
    decision_reference: Annotated[str, Field(min_length=1)]
    change_reason: Annotated[str, Field(min_length=1)]


class StudyArmLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    study_uid: str
    target_kind: LifecycleTargetKind
    target_uid: str
    lifecycle_state: str
    effective_date: Annotated[datetime, AwareDatetime]
    decision_reference: str
    change_reason: str
    recorded_at: Annotated[datetime, AwareDatetime]
    author_id: str

    @classmethod
    def from_vo(cls, event: StudyArmLifecycleEventVO) -> Self:
        return cls(**asdict(event))
