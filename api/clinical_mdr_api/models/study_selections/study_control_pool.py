"""DTOs for authored control pools and adaptation references (plan item O7).

References pin published CSL statistical facts; their values are never copied.
"""

from datetime import datetime
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from clinical_mdr_api.domains.study_selections.study_control_pool import (
    CONTROL_POOL_REPORT,
    CslStatisticalReferenceVO,
    StudyAdaptationReferenceVO,
    StudyControlPoolVO,
)

NativeUid = Annotated[str, Field(min_length=1, pattern=r"^\S(.*\S)?$")]
Digest = Annotated[str, Field(pattern=r"^(sha256:)?[0-9a-f]{64}$")]
NOT_VERIFIED = (
    "OSB records these pins and does not verify the referenced fact; CSL and "
    "Workbench own verification and statistical authority."
)


class CslStatisticalReference(BaseModel):
    """Pins of one entry of a published `csl-biometrics-publication`.

    For a control pool the entry is a `controlPools` record of the
    `control-pool-report/1` carried by `csl-biometrics-analysis-model/4`.
    """

    model_config = ConfigDict(extra="forbid")

    publication_entity_id: NativeUid
    publication_hash: Digest
    analysis_model_hash: Digest
    collection: NativeUid
    entry_id: NativeUid
    entry_hash: Digest

    def to_vo(self) -> CslStatisticalReferenceVO:
        return CslStatisticalReferenceVO(**self.model_dump())

    @classmethod
    def from_vo(cls, vo: CslStatisticalReferenceVO | None) -> Self | None:
        return None if vo is None else cls(**vo.__dict__)


class StudyControlPoolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_study_version: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1, pattern=r"\S")]
    description: str | None = None
    donor_arm_uids: Annotated[list[NativeUid], Field(min_length=1)]
    focal_arm_uids: Annotated[list[NativeUid], Field(min_length=1)]
    csl_reference: CslStatisticalReference | None = Field(
        default=None,
        description=f"Exact {CONTROL_POOL_REPORT} entry pins; null means not yet "
        "referenced. " + NOT_VERIFIED,
    )


class StudyAdaptationReferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_study_version: Annotated[str, Field(min_length=1)]
    adaptation_type: Annotated[
        str,
        Field(min_length=1, description="Authored label; no governed CT is inferred."),
    ]
    csl_reference: CslStatisticalReference = Field(description=NOT_VERIFIED)
    affected_arm_uids: list[NativeUid] = Field(default_factory=list)
    affected_epoch_uids: list[NativeUid] = Field(default_factory=list)
    trigger_reference: Annotated[str, Field(min_length=1)] | None = None
    description: str | None = None


class StudyRecordDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_study_version: Annotated[str, Field(min_length=1)]


class StudyControlPool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    study_uid: str
    name: str
    description: str | None
    donor_arm_uids: list[str]
    focal_arm_uids: list[str]
    csl_reference: CslStatisticalReference | None
    reference_verified_by_osb: bool = False
    start_date: Annotated[datetime, AwareDatetime]
    author_username: str | None

    @classmethod
    def from_vo(cls, vo: StudyControlPoolVO) -> Self:
        return cls(
            uid=vo.uid,
            study_uid=vo.study_uid,
            name=vo.name,
            description=vo.description,
            donor_arm_uids=list(vo.donor_arm_uids),
            focal_arm_uids=list(vo.focal_arm_uids),
            csl_reference=CslStatisticalReference.from_vo(vo.csl_reference),
            start_date=vo.start_date,
            author_username=vo.author_username,
        )


class StudyAdaptationReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    study_uid: str
    adaptation_type: str
    csl_reference: CslStatisticalReference
    reference_verified_by_osb: bool = False
    affected_arm_uids: list[str]
    affected_epoch_uids: list[str]
    trigger_reference: str | None
    description: str | None
    start_date: Annotated[datetime, AwareDatetime]
    author_username: str | None

    @classmethod
    def from_vo(cls, vo: StudyAdaptationReferenceVO) -> Self:
        reference = CslStatisticalReference.from_vo(vo.csl_reference)
        assert reference is not None and vo.start_date is not None
        return cls(
            uid=vo.uid,
            study_uid=vo.study_uid,
            adaptation_type=vo.adaptation_type,
            csl_reference=reference,
            affected_arm_uids=list(vo.affected_arm_uids),
            affected_epoch_uids=list(vo.affected_epoch_uids),
            trigger_reference=vo.trigger_reference,
            description=vo.description,
            start_date=vo.start_date,
            author_username=vo.author_username,
        )
