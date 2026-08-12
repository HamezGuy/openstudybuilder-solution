"""Bounded, content-hashed OSB mapping context for Proposal V2."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator

from clinical_mdr_api.models.utils import BaseModel

MappingResourceFamily = Literal[
    "controlled_terminology",
    "controlled_terminology_codelists",
    "units",
    "cdash_variables",
    "objective_templates",
    "endpoint_templates",
    "criteria_templates",
    "timeframe_templates",
    "activities",
    "odm_forms",
    "odm_item_groups",
    "odm_items",
]


class RequestedMappingContextPackage(BaseModel):
    catalogue_name: Literal["DDF CT", "SDTM CT", "CDASH CT"]
    package_uid: Annotated[str, Field(min_length=1, max_length=128)]
    effective_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", max_length=10)]


class RequestedDataModelSelection(BaseModel):
    family: Literal["SDTM", "CDASH"]
    model_uid: Annotated[str, Field(min_length=1, max_length=128)]
    model_version: Annotated[str, Field(min_length=1, max_length=64)]
    implementation_guide_uid: Annotated[str, Field(min_length=1, max_length=128)]
    implementation_guide_version: Annotated[str, Field(min_length=1, max_length=64)]


class MappingContextRequest(BaseModel):
    study_uid: str | None = None
    study_value_version: str | None = None
    requested_packages: list[RequestedMappingContextPackage] = Field(
        default_factory=list, max_length=3
    )
    requested_data_models: list[RequestedDataModelSelection] = Field(
        default_factory=list, max_length=2
    )
    resource_families: list[MappingResourceFamily] = Field(
        default_factory=list, max_length=12
    )
    search_strings: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=50
    )
    search_codes: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=50
    )
    maximum_candidates_per_family: Annotated[int, Field(ge=1, le=50)] = 10


class MappingContextPackage(BaseModel):
    study_standard_version_uid: str | None = None
    catalogue_name: str
    package_uid: str
    effective_date: str
    automatically_created: bool = False


class MappingContextDataModel(BaseModel):
    family: Literal["SDTM", "CDASH"]
    model_uid: str
    model_version: str
    implementation_guide_uid: str
    implementation_guide_version: str


class MappingContextCandidate(BaseModel):
    identity_schema_version: Literal["osb-candidate-key/2.0"] = "osb-candidate-key/2.0"
    resource_family: MappingResourceFamily
    resource_type: str
    uid: str
    version: str
    status: str
    library_name: str | None = None
    label: str
    code: str | None = None
    submission_value: str | None = None
    package_uid: str | None = None
    catalogue_name: str | None = None
    package_version: str | None = None
    package_effective_date: str | None = None
    parent_resource_type: str | None = None
    parent_uid: str | None = None
    parent_version: str | None = None
    parent_submission_value: str | None = None
    model_uid: str | None = None
    model_version: str | None = None
    implementation_guide_uid: str | None = None
    implementation_guide_version: str | None = None
    mapping_target_uid: str | None = None
    mapping_target_version: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    extensible: bool | None = None
    ucum_expression: str | None = None
    dimension: str | None = None
    conversion_factor_to_master: float | None = None
    stable_oid: str | None = None
    criteria_type_uid: str | None = None
    parameter_count: int | None = None


class MappingContextCandidateGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: Annotated[str, Field(min_length=1, max_length=128)]
    concept_id: Annotated[str, Field(min_length=1, max_length=128)]
    target_key: Annotated[str, Field(min_length=1, max_length=256)]
    semantic_role: Annotated[str, Field(min_length=1, max_length=256)]
    resource_family: MappingResourceFamily
    search_strings: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=20
    )
    search_codes: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=20
    )
    parent_resource_type: Literal["CTCodelist"] | None = None
    parent_search_strings: list[Annotated[str, Field(min_length=1, max_length=256)]] = (
        Field(default_factory=list, max_length=10)
    )


class MappingContextV2Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_uid: str | None = Field(default=None, max_length=128)
    study_value_version: str | None = Field(default=None, max_length=64)
    as_of: datetime | None = None
    requested_packages: list[RequestedMappingContextPackage] = Field(
        default_factory=list, max_length=3
    )
    requested_data_models: list[RequestedDataModelSelection] = Field(
        default_factory=list, max_length=2
    )
    candidate_groups: list[MappingContextCandidateGroupRequest] = Field(
        default_factory=list, max_length=10_000
    )
    maximum_candidates_per_group: Annotated[int, Field(ge=1, le=25)] = 10

    @field_validator("candidate_groups")
    @classmethod
    def unique_group_identity(cls, groups):
        identities = [
            (group.fact_id, group.concept_id, group.target_key) for group in groups
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("MAPPING_CONTEXT_DUPLICATE_CANDIDATE_GROUP")
        return groups


class MappingContextCandidateGroup(BaseModel):
    fact_id: str
    concept_id: str
    target_key: str
    semantic_role: str
    resource_family: MappingResourceFamily
    parent_resource_type: Literal["CTCodelist"] | None = None
    parent_search_strings: list[str] = Field(default_factory=list)
    retrieval_policy_version: Literal["osb-retrieval/2.0"] = "osb-retrieval/2.0"
    complete: bool
    truncated: bool
    candidates: list[MappingContextCandidate] = Field(default_factory=list)
    release_blockers: list[str] = Field(default_factory=list)


class MappingContextV2Response(BaseModel):
    schema_version: Literal["osb-mapping-context/2.0"] = "osb-mapping-context/2.0"
    mapping_authority: Literal["OpenStudyBuilder"] = "OpenStudyBuilder"
    study_uid: str | None = None
    study_value_version: str | None = None
    as_of: datetime | None = None
    generated_at: datetime
    context_hash: str
    osb_openapi_hash: str
    governed: bool
    selected_packages: list[MappingContextPackage] = Field(default_factory=list)
    selected_data_models: list[MappingContextDataModel] = Field(default_factory=list)
    candidate_groups: list[MappingContextCandidateGroup] = Field(default_factory=list)
    release_blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MappingContextResponse(BaseModel):
    schema_version: Literal["osb-mapping-context/1.0"] = "osb-mapping-context/1.0"
    mapping_authority: Literal["OpenStudyBuilder"] = "OpenStudyBuilder"
    study_uid: str | None = None
    study_value_version: str | None = None
    generated_at: datetime
    context_hash: str
    osb_openapi_hash: str
    governed: bool
    selected_packages: list[MappingContextPackage] = Field(default_factory=list)
    selected_data_models: list[MappingContextDataModel] = Field(default_factory=list)
    candidates: dict[MappingResourceFamily, list[MappingContextCandidate]] = Field(
        default_factory=dict
    )
    release_blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
