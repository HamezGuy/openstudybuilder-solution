from datetime import datetime
from typing import Annotated, Self

from pydantic import Field

from clinical_mdr_api.descriptions.general import CHANGES_FIELD_DESC
from clinical_mdr_api.domains.controlled_terminologies.ct_term_name import CTTermNameAR
from clinical_mdr_api.models.libraries.library import Library
from clinical_mdr_api.models.utils import BaseModel, PatchInputModel


class CTTermName(BaseModel):
    @classmethod
    def from_ct_term_ar(cls, ct_term_name_ar: CTTermNameAR) -> Self:
        catalogue_names = (
            ct_term_name_ar.ct_term_vo.catalogue_names
            if ct_term_name_ar.ct_term_vo.catalogue_names
            else []
        )
        return cls(
            term_uid=ct_term_name_ar.uid,
            catalogue_names=catalogue_names,
            sponsor_preferred_name=ct_term_name_ar.ct_term_vo.name,
            sponsor_preferred_name_sentence_case=ct_term_name_ar.ct_term_vo.name_sentence_case,
            library_name=Library.from_library_vo(ct_term_name_ar.library).name,
            possible_actions=sorted(
                [_.value for _ in ct_term_name_ar.get_possible_actions()]
            ),
            start_date=ct_term_name_ar.item_metadata.start_date,
            end_date=ct_term_name_ar.item_metadata.end_date,
            status=ct_term_name_ar.item_metadata.status.value,
            version=ct_term_name_ar.item_metadata.version,
            change_description=ct_term_name_ar.item_metadata.change_description,
            author_username=ct_term_name_ar.item_metadata.author_username,
            queried_effective_date=ct_term_name_ar.ct_term_vo.queried_effective_date,
            date_conflict=ct_term_name_ar.ct_term_vo.date_conflict,
        )

    @classmethod
    def from_ct_term_ar_without_common_term_fields(
        cls, ct_term_name_ar: CTTermNameAR
    ) -> Self:
        return cls(
            sponsor_preferred_name=ct_term_name_ar.ct_term_vo.name,
            sponsor_preferred_name_sentence_case=ct_term_name_ar.ct_term_vo.name_sentence_case,
            possible_actions=sorted(
                [_.value for _ in ct_term_name_ar.get_possible_actions()]
            ),
            start_date=ct_term_name_ar.item_metadata.start_date,
            end_date=ct_term_name_ar.item_metadata.end_date,
            status=ct_term_name_ar.item_metadata.status.value,
            version=ct_term_name_ar.item_metadata.version,
            change_description=ct_term_name_ar.item_metadata.change_description,
            author_username=ct_term_name_ar.item_metadata.author_username,
            queried_effective_date=ct_term_name_ar.ct_term_vo.queried_effective_date,
            date_conflict=ct_term_name_ar.ct_term_vo.date_conflict,
        )

    term_uid: Annotated[str, Field()] = ""

    catalogue_names: Annotated[
        list[str], Field(json_schema_extra={"remove_from_wildcard": True})
    ] = []

    sponsor_preferred_name: Annotated[str, Field()]

    sponsor_preferred_name_sentence_case: Annotated[str, Field()]

    library_name: Annotated[str | None, Field(json_schema_extra={"nullable": True})] = (
        None
    )
    start_date: Annotated[
        datetime | None, Field(json_schema_extra={"nullable": True})
    ] = None
    end_date: Annotated[
        datetime | None, Field(json_schema_extra={"nullable": True})
    ] = None
    status: Annotated[str | None, Field(json_schema_extra={"nullable": True})] = None
    version: Annotated[str | None, Field(json_schema_extra={"nullable": True})] = None
    change_description: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ] = None
    author_username: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ] = None
    queried_effective_date: Annotated[
        datetime | None,
        Field(
            json_schema_extra={"nullable": True},
            description="Indicates the actual date at which the term was queried.",
        ),
    ] = None
    date_conflict: Annotated[
        bool,
        Field(
            description="Indicates if the term had a date conflict upon retrieval. If True, then the Latest Final was returned.",
        ),
    ] = False
    possible_actions: list[str] = Field(
        description=(
            "Holds those actions that can be performed on the CTTermName. "
            "Actions are: 'approve', 'edit', 'new_version'."
        ),
        default_factory=list,
    )


class CTTermNameSimple(BaseModel):
    term_uid: Annotated[str, Field()]
    sponsor_preferred_name: Annotated[str, Field()]


class CTTermNameVersion(CTTermName):
    """
    Class for storing CTTermName and calculation of differences
    """

    changes: list[str] = Field(description=CHANGES_FIELD_DESC, default_factory=list)


class CTTermNameEditInput(PatchInputModel):
    sponsor_preferred_name: Annotated[str | None, Field(min_length=1)] = None
    sponsor_preferred_name_sentence_case: Annotated[str | None, Field(min_length=1)] = (
        None
    )
    change_description: Annotated[str, Field(min_length=1)]
