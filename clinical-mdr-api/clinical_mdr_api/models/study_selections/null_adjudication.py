"""Closed request and receipt for atomic, metadata-only null adjudication."""

from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

NULL_ADJUDICATION_CONTRACT: Final = "StudyNullAdjudicationV1@1.0.0"
NULL_ADJUDICATION_COMPARISON: Final = "native-json-identity-v1"
MetadataPath = Annotated[
    str,
    Field(
        strict=True, min_length=1, max_length=256, pattern=r"^[a-z_]+(?:\.[a-z_]+)+$"
    ),
]
TermUid = Annotated[str, Field(strict=True, min_length=1, max_length=256)]


class NullAdjudicationModel(BaseModel):
    # Do not inherit InputModel: trimming/coercing expected values would alter
    # the observation whose identity the caller is asking us to compare.
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class AbsentMetadataValue(NullAdjudicationModel):
    present: Literal[False]

    @field_validator("present", mode="before")
    @classmethod
    def strict_presence(cls, value):
        if not isinstance(value, bool):
            raise ValueError("present must be a Boolean")
        return value


class PresentMetadataValue(NullAdjudicationModel):
    present: Literal[True]
    value: JsonValue

    @field_validator("present", mode="before")
    @classmethod
    def strict_presence(cls, value):
        if not isinstance(value, bool):
            raise ValueError("present must be a Boolean")
        return value


ExpectedMetadataValue = AbsentMetadataValue | PresentMetadataValue


class NullAdjudicationField(NullAdjudicationModel):
    value_path: MetadataPath
    companion_path: MetadataPath


class NullAdjudication(NullAdjudicationField):
    expected_value: ExpectedMetadataValue
    expected_null_companion: ExpectedMetadataValue
    null_term_uid: TermUid

    @field_validator("null_term_uid")
    @classmethod
    def exact_term_uid(cls, value):
        if value != value.strip():
            raise ValueError("null_term_uid must be an exact nonblank term UID")
        return value


class StudyNullAdjudicationRequest(NullAdjudicationModel):
    contract_version: Literal["StudyNullAdjudicationV1@1.0.0"]
    adjudications: list[NullAdjudication] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def distinct_fields(self):
        paths = [row.value_path for row in self.adjudications]
        companions = [row.companion_path for row in self.adjudications]
        if len(set(paths)) != len(paths) or len(set(companions)) != len(companions):
            raise ValueError("Duplicate null-adjudication target or companion")
        return self


class StudyNullAdjudicationCapability(NullAdjudicationModel):
    contract_version: Literal["StudyNullAdjudicationV1@1.0.0"]
    comparison: Literal["native-json-identity-v1"]
    study_uid: str
    atomic_preconditions: Literal[True]
    fields: list[NullAdjudicationField]


class AppliedNullAdjudication(NullAdjudicationField):
    null_term_uid: TermUid


class StudyNullAdjudicationReceipt(NullAdjudicationModel):
    contract_version: Literal["StudyNullAdjudicationV1@1.0.0"]
    study_uid: str
    request_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    preconditions_verified: Literal[True]
    checked_paths: list[MetadataPath]
    applied: list[AppliedNullAdjudication]
