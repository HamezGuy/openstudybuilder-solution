from typing import Annotated, Literal

from pydantic import Field

from clinical_mdr_api.models.utils import BaseModel, PatchInputModel, PostInputModel


class FeatureFlag(BaseModel):
    sn: Annotated[int, Field()]
    section: Annotated[str, Field()]
    feature: Annotated[str, Field()]
    name: Annotated[str, Field()]
    enabled: Annotated[bool, Field()]
    description: Annotated[str | None, Field(json_schema_extra={"nullable": True})] = (
        None
    )


class FeatureFlagInput(PostInputModel):
    section: Annotated[Literal["admin", "library", "studies"], Field()]
    feature: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    enabled: Annotated[bool, Field()]
    description: Annotated[str | None, Field(min_length=1)] = None


class FeatureFlagPatchInput(PatchInputModel):
    section: Annotated[Literal["admin", "library", "studies"] | None, Field()] = None
    feature: Annotated[str | None, Field(min_length=1)] = None
    enabled: Annotated[bool | None, Field()] = None
