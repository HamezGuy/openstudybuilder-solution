from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import Field

from clinical_mdr_api.domains.controlled_terminologies.ct_codelist_attributes import (
    DEFAULT_CODELIST_TYPE,
    CTCodelistAttributesAR,
)
from clinical_mdr_api.domains.controlled_terminologies.ct_codelist_name import (
    CTCodelistNameAR,
)
from clinical_mdr_api.domains.controlled_terminologies.ct_codelist_term import (
    CTCodelistTermAR,
    CTPairedCodelistTermAR,
)
from clinical_mdr_api.models.controlled_terminologies.ct_codelist_attributes import (
    CTCodelistAttributes,
)
from clinical_mdr_api.models.controlled_terminologies.ct_codelist_name import (
    CTCodelistName,
)
from clinical_mdr_api.models.libraries.library import Library
from clinical_mdr_api.models.utils import BaseModel, PostInputModel
from common.config import settings


class CTCodelist(BaseModel):
    @classmethod
    def from_ct_codelist_ar(
        cls,
        ct_codelist_name_ar: CTCodelistNameAR,
        ct_codelist_attributes_ar: CTCodelistAttributesAR,
        paired_codes_codelist_uid: str | None = None,
        paired_names_codelist_uid: str | None = None,
    ) -> Self:
        return cls(
            catalogue_names=ct_codelist_attributes_ar.ct_codelist_vo.catalogue_names,
            codelist_uid=ct_codelist_attributes_ar.uid,
            parent_codelist_uid=ct_codelist_attributes_ar._ct_codelist_attributes_vo.parent_codelist_uid,
            child_codelist_uids=(
                ct_codelist_attributes_ar._ct_codelist_attributes_vo.child_codelist_uids
                if ct_codelist_attributes_ar._ct_codelist_attributes_vo.child_codelist_uids
                else []
            ),
            paired_codes_codelist_uid=paired_codes_codelist_uid,
            paired_names_codelist_uid=paired_names_codelist_uid,
            name=ct_codelist_attributes_ar.name,
            submission_value=ct_codelist_attributes_ar.ct_codelist_vo.submission_value,
            nci_preferred_name=ct_codelist_attributes_ar.ct_codelist_vo.preferred_term,
            definition=ct_codelist_attributes_ar.ct_codelist_vo.definition,
            extensible=ct_codelist_attributes_ar.ct_codelist_vo.extensible,
            is_ordinal=ct_codelist_attributes_ar.ct_codelist_vo.is_ordinal,
            codelist_type=ct_codelist_attributes_ar.ct_codelist_vo.codelist_type,
            library_name=Library.from_library_vo(
                ct_codelist_attributes_ar.library
            ).name,
            sponsor_preferred_name=ct_codelist_name_ar.name,
            template_parameter=ct_codelist_name_ar.ct_codelist_vo.is_template_parameter,
            possible_actions=sorted(
                [_.value for _ in ct_codelist_attributes_ar.get_possible_actions()]
            ),
        )

    catalogue_names: Annotated[
        list[str], Field(json_schema_extra={"remove_from_wildcard": True})
    ]

    codelist_uid: Annotated[str, Field()]

    parent_codelist_uid: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ] = None

    child_codelist_uids: list[str] = Field(default_factory=list)

    paired_codes_codelist_uid: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ] = None

    paired_names_codelist_uid: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ] = None

    name: Annotated[str, Field()]

    submission_value: Annotated[str, Field()]

    nci_preferred_name: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ] = None

    definition: Annotated[str, Field()]

    extensible: Annotated[bool, Field()]

    is_ordinal: Annotated[bool, Field()]

    codelist_type: Annotated[str, Field()] = DEFAULT_CODELIST_TYPE

    sponsor_preferred_name: Annotated[str, Field()]

    template_parameter: Annotated[bool, Field()]

    library_name: Annotated[str, Field()]
    possible_actions: list[str] = Field(
        description=(
            "Holds those actions that can be performed on the CTCodelistAttributes. "
            "Actions are: 'approve', 'edit', 'new_version'."
        ),
        default_factory=list,
    )


class CTCodelistTermInput(PostInputModel):
    term_uid: Annotated[str, Field(min_length=1)]
    order: Annotated[
        int | None,
        Field(gt=0, lt=settings.max_int_neo4j),
    ] = 999999
    ordinal: Annotated[float | None, Field()] = None
    submission_value: Annotated[str, Field(min_length=1)]


class CTCodelistCreateInput(PostInputModel):
    catalogue_names: Annotated[list[str], Field()]
    name: Annotated[str, Field(min_length=1)]
    submission_value: Annotated[str, Field(min_length=1)]
    nci_preferred_name: Annotated[str | None, Field(min_length=1)] = None
    definition: Annotated[str, Field(min_length=1)]
    extensible: Annotated[bool, Field()]
    is_ordinal: Annotated[bool, Field()]
    codelist_type: Annotated[str, Field()] = DEFAULT_CODELIST_TYPE
    sponsor_preferred_name: Annotated[str, Field(min_length=1)]
    template_parameter: Annotated[bool, Field()]
    parent_codelist_uid: Annotated[str | None, Field(min_length=1)] = None
    paired_codes_codelist_uid: Annotated[str | None, Field(min_length=1)] = None
    paired_names_codelist_uid: Annotated[str | None, Field(min_length=1)] = None
    terms: Annotated[list[CTCodelistTermInput], Field()]
    library_name: Annotated[str, Field(min_length=1)]


class CTCodelistCodelistInformation(PostInputModel):
    name: Annotated[str, Field(min_length=1)]
    submission_value: Annotated[str, Field(min_length=1)]
    nci_preferred_name: Annotated[str | None, Field(min_length=1)] = None
    definition: Annotated[str, Field(min_length=1)]
    sponsor_preferred_name: Annotated[str, Field(min_length=1)]


class CTPairedCodelistCreateInput(PostInputModel):
    catalogue_names: Annotated[list[str], Field()]
    name_information: Annotated[CTCodelistCodelistInformation, Field()]
    code_information: Annotated[CTCodelistCodelistInformation, Field()]
    extensible: Annotated[bool, Field()]
    is_ordinal: Annotated[bool, Field()]
    codelist_type: Annotated[str, Field()] = DEFAULT_CODELIST_TYPE
    template_parameter: Annotated[bool, Field()]
    parent_codelist_uid: Annotated[str | None, Field(min_length=1)] = None
    library_name: Annotated[str, Field(min_length=1)]


class CTPairedCodelistInfo(BaseModel):
    uid: Annotated[str, Field()]
    name: Annotated[str | None, Field(json_schema_extra={"nullable": True})] = None


class CTCodelistNameAndAttributes(BaseModel):
    @classmethod
    def from_ct_codelist_ar(
        cls,
        ct_codelist_name_ar: CTCodelistNameAR,
        ct_codelist_attributes_ar: CTCodelistAttributesAR,
        paired_codelist: CTPairedCodelistInfo | None = None,
    ) -> Self:
        codelist_name_and_attributes = cls(
            catalogue_names=ct_codelist_attributes_ar.ct_codelist_vo.catalogue_names,
            codelist_uid=ct_codelist_attributes_ar.uid,
            parent_codelist_uid=ct_codelist_attributes_ar._ct_codelist_attributes_vo.parent_codelist_uid,
            child_codelist_uids=ct_codelist_attributes_ar._ct_codelist_attributes_vo.child_codelist_uids,
            library_name=Library.from_library_vo(
                ct_codelist_attributes_ar.library
            ).name,
            name=CTCodelistName.from_ct_codelist_ar_without_common_codelist_fields(
                ct_codelist_name_ar
            ),
            attributes=CTCodelistAttributes.from_ct_codelist_ar_without_common_codelist_fields(
                ct_codelist_attributes_ar
            ),
            paired_codelist=paired_codelist,
        )

        return codelist_name_and_attributes

    @classmethod
    def from_name_and_attributes(
        cls,
        ct_codelist_name: CTCodelistName,
        ct_codelist_attributes: CTCodelistAttributes,
        paired_codelist: CTPairedCodelistInfo | None = None,
    ) -> Self:
        codelist_name_and_attributes = cls(
            catalogue_names=ct_codelist_attributes.catalogue_names,
            codelist_uid=ct_codelist_attributes.codelist_uid or "",
            parent_codelist_uid=ct_codelist_attributes.parent_codelist_uid,
            child_codelist_uids=ct_codelist_attributes.child_codelist_uids,
            library_name=ct_codelist_attributes.library_name,
            name=ct_codelist_name,
            attributes=ct_codelist_attributes,
            paired_codelist=paired_codelist,
        )

        return codelist_name_and_attributes

    catalogue_names: Annotated[
        list[str], Field(json_schema_extra={"remove_from_wildcard": True})
    ]

    codelist_uid: Annotated[str, Field()]

    parent_codelist_uid: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ] = None

    child_codelist_uids: list[Any] = Field(default_factory=list)

    library_name: Annotated[str | None, Field(json_schema_extra={"nullable": True})] = (
        None
    )

    name: Annotated[CTCodelistName, Field()]

    attributes: Annotated[CTCodelistAttributes, Field()]

    paired_codelist: Annotated[
        CTPairedCodelistInfo | None, Field(json_schema_extra={"nullable": True})
    ] = None


class CTCodelistPaired(BaseModel):
    @classmethod
    def from_ct_codelists(
        cls,
        paired_names_codelist_name: CTCodelistName | None,
        paired_names_codelist_attrs: CTCodelistAttributes | None,
        paired_codes_codelist_name: CTCodelistName | None,
        paired_codes_codelist_attrs: CTCodelistAttributes | None,
    ) -> Self:
        if paired_names_codelist_name and paired_names_codelist_attrs:
            paired_names = CTCodelistNameAndAttributes.from_name_and_attributes(
                paired_names_codelist_name, paired_names_codelist_attrs
            )
        else:
            paired_names = None
        if paired_codes_codelist_name and paired_codes_codelist_attrs:
            paired_codes = CTCodelistNameAndAttributes.from_name_and_attributes(
                paired_codes_codelist_name, paired_codes_codelist_attrs
            )
        else:
            paired_codes = None

        paired_codelist = cls(
            names=paired_names,
            codes=paired_codes,
        )

        return paired_codelist

    names: Annotated[
        CTCodelistNameAndAttributes | None,
        Field(),
    ]

    codes: Annotated[
        CTCodelistNameAndAttributes | None,
        Field(),
    ]


class CTCodelistPairedInput(PostInputModel):
    paired_codes_codelist_uid: Annotated[str | None, Field(min_length=1)] = None
    paired_names_codelist_uid: Annotated[str | None, Field(min_length=1)] = None


class CTCodelistCompact(BaseModel):
    uid: Annotated[str, Field()]
    sponsor_preferred_name: Annotated[str, Field()]
    submission_value: Annotated[str, Field()]
    library_name: Annotated[str, Field()]


class CTCodelistTermBase(BaseModel):
    term_uid: Annotated[str, Field()]
    order: Annotated[
        int | None,
        Field(json_schema_extra={"nullable": True}),
    ]
    ordinal: Annotated[float | None, Field(json_schema_extra={"nullable": True})] = None
    library_name: Annotated[str | None, Field()]
    sponsor_preferred_name: Annotated[str, Field()]

    sponsor_preferred_name_sentence_case: Annotated[str, Field()]

    concept_id: Annotated[
        str | None,
        Field(json_schema_extra={"nullable": True}),
    ]

    nci_preferred_name: Annotated[
        str | None,
        Field(json_schema_extra={"nullable": True}),
    ]

    definition: Annotated[str, Field(json_schema_extra={"remove_from_wildcard": True})]

    name_date: datetime
    name_status: str

    attributes_date: datetime
    attributes_status: str

    start_date: datetime
    end_date: Annotated[datetime | None, Field(json_schema_extra={"nullable": True})]


class CTCodelistTerm(CTCodelistTermBase):
    @classmethod
    def from_ct_codelist_term_ar(
        cls,
        ct_codelist_term_ar: CTCodelistTermAR,
    ) -> Self:
        codelist_terms = cls(
            term_uid=ct_codelist_term_ar.ct_codelist_term_vo.term_uid,
            submission_value=ct_codelist_term_ar.ct_codelist_term_vo.submission_value,
            order=ct_codelist_term_ar.ct_codelist_term_vo.order,
            ordinal=ct_codelist_term_ar.ct_codelist_term_vo.ordinal,
            library_name=ct_codelist_term_ar.ct_codelist_term_vo.library_name,
            sponsor_preferred_name=ct_codelist_term_ar.ct_codelist_term_vo.sponsor_preferred_name,
            sponsor_preferred_name_sentence_case=ct_codelist_term_ar.ct_codelist_term_vo.sponsor_preferred_name_sentence_case,
            concept_id=ct_codelist_term_ar.ct_codelist_term_vo.concept_id,
            nci_preferred_name=ct_codelist_term_ar.ct_codelist_term_vo.nci_preferred_name,
            definition=ct_codelist_term_ar.ct_codelist_term_vo.definition,
            name_date=ct_codelist_term_ar.ct_codelist_term_vo.name_date,
            name_status=ct_codelist_term_ar.ct_codelist_term_vo.name_status.value,
            attributes_date=ct_codelist_term_ar.ct_codelist_term_vo.attributes_date,
            attributes_status=ct_codelist_term_ar.ct_codelist_term_vo.attributes_status.value,
            start_date=ct_codelist_term_ar.ct_codelist_term_vo.start_date,
            end_date=ct_codelist_term_ar.ct_codelist_term_vo.end_date,
        )

        return codelist_terms

    submission_value: Annotated[str, Field()]


class CTPairedCodelistTerm(CTCodelistTermBase):
    @classmethod
    def from_ct_paired_codelist_term_ar(
        cls,
        ct_paired_codelist_term_ar: CTPairedCodelistTermAR,
    ) -> Self:
        vo = ct_paired_codelist_term_ar.ct_paired_codelist_term_vo
        return cls(
            term_uid=vo.term_uid,
            code_submission_value=vo.code_submission_value,
            name_submission_value=vo.name_submission_value,
            order=vo.order,
            ordinal=vo.ordinal,
            library_name=vo.library_name,
            sponsor_preferred_name=vo.sponsor_preferred_name,
            sponsor_preferred_name_sentence_case=vo.sponsor_preferred_name_sentence_case,
            concept_id=vo.concept_id,
            nci_preferred_name=vo.nci_preferred_name,
            definition=vo.definition,
            name_date=vo.name_date,
            name_status=vo.name_status.value,
            attributes_date=vo.attributes_date,
            attributes_status=vo.attributes_status.value,
            start_date=vo.start_date,
            end_date=vo.end_date,
        )

    code_submission_value: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ]
    name_submission_value: Annotated[
        str | None, Field(json_schema_extra={"nullable": True})
    ]
