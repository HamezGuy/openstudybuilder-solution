"""OpenStudyBuilder study-definition authority snapshot contract.

The Intelligence Layer owns source facts and evidence. This contract is the
machine-readable boundary at which OpenStudyBuilder becomes authoritative for
study setup and standards mappings. It deliberately carries both the native OSB
selection models and the CDISC USDM v4 projection: neither is an EDC DTO.
"""

from typing import Annotated, Any, Literal

from pydantic import Field

from clinical_mdr_api.models.utils import BaseModel

AuthorityMode = Literal["legacy", "shadow", "enforced"]
ReconciliationStatus = Literal["matched", "missing", "extra", "changed", "unresolved"]


class StudyAuthorityBlocker(BaseModel):
    code: Annotated[str, Field(description="Stable machine-readable blocker code")]
    path: Annotated[str, Field(description="Path in the authority snapshot")]
    detail: Annotated[str, Field(description="Actionable release-blocking detail")]


class StudyAuthorityCounts(BaseModel):
    standard_versions: int = 0
    objectives: int = 0
    endpoints: int = 0
    criteria: int = 0
    arms: int = 0
    epochs: int = 0
    elements: int = 0
    design_cells: int = 0
    visits: int = 0
    activities: int = 0
    activity_schedules: int = 0
    usdm_objectives: int = 0
    usdm_endpoints: int = 0
    usdm_arms: int = 0
    usdm_epochs: int = 0
    usdm_elements: int = 0
    usdm_design_cells: int = 0
    usdm_encounters: int = 0
    usdm_activities: int = 0
    usdm_scheduled_activity_links: int = 0
    usdm_void_codes: int = 0


class StudyAuthorityReconciliationRow(BaseModel):
    resource_class: str
    native_uid: str | None = None
    usdm_id: str | None = None
    native_identity: dict[str, Any] = Field(default_factory=dict)
    usdm_identity: dict[str, Any] = Field(default_factory=dict)
    relationship_identity: dict[str, Any] = Field(default_factory=dict)
    status: ReconciliationStatus
    blocker_code: str | None = None


class StudyAuthoritySnapshot(BaseModel):
    schema_version: Literal["osb-authority/1.1"] = "osb-authority/1.1"
    mapping_authority: Literal["OpenStudyBuilder"] = "OpenStudyBuilder"
    study_definition_standard: Literal["CDISC USDM 4"] = "CDISC USDM 4"
    crf_metadata_standard: Literal["CDISC ODM 1.3.2"] = "CDISC ODM 1.3.2"
    authority_mode: AuthorityMode
    study_uid: str
    study_version: str | None = None
    release_eligible: bool
    blockers: list[StudyAuthorityBlocker] = Field(default_factory=list)
    counts: StudyAuthorityCounts
    content_hash: Annotated[
        str,
        Field(
            description="sha256 over the canonical snapshot content excluding this field"
        ),
    ]

    # Native OSB is the editable source; USDM is its standards projection.
    native_study: dict[str, Any]
    usdm: dict[str, Any]
    study_standard_versions: list[dict[str, Any]] = Field(default_factory=list)
    study_objectives: list[dict[str, Any]] = Field(default_factory=list)
    study_endpoints: list[dict[str, Any]] = Field(default_factory=list)
    study_criteria: list[dict[str, Any]] = Field(default_factory=list)
    study_arms: list[dict[str, Any]] = Field(default_factory=list)
    study_epochs: list[dict[str, Any]] = Field(default_factory=list)
    study_elements: list[dict[str, Any]] = Field(default_factory=list)
    study_design_cells: list[dict[str, Any]] = Field(default_factory=list)
    study_visits: list[dict[str, Any]] = Field(default_factory=list)
    study_activities: list[dict[str, Any]] = Field(default_factory=list)
    study_activity_schedules: list[dict[str, Any]] = Field(default_factory=list)
    usdm_extensions: dict[str, Any] = Field(default_factory=dict)
    reconciliation: list[StudyAuthorityReconciliationRow] = Field(
        default_factory=list
    )
    structure_statistics: dict[str, Any]
    integrity: dict[str, Any]
