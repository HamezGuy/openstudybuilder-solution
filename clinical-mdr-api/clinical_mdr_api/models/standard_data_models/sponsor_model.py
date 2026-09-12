from datetime import datetime
from typing import Annotated, Self

from pydantic import ConfigDict, Field

from clinical_mdr_api.domains.standard_data_models.sponsor_model import SponsorModelAR
from clinical_mdr_api.models import _generic_descriptions
from clinical_mdr_api.models.utils import BaseModel, InputModel


class SponsorModelBase(BaseModel):
    pass


class SponsorModel(SponsorModelBase):
    model_config = ConfigDict(from_attributes=True)

    uid: Annotated[
        str | None,
        Field(json_schema_extra={"source": "uid"}),
    ]
    name: Annotated[
        str,
        Field(
            description="The name or the sponsor model. E.g. sdtm_sponsormodel_3.2-NN15",
            json_schema_extra={"source": "has_sponsor_model_version.name"},
        ),
    ]
    start_date: Annotated[
        datetime | None,
        Field(
            description=_generic_descriptions.START_DATE,
            json_schema_extra={
                "source": "has_sponsor_model_version|start_date",
                "nullable": True,
            },
        ),
    ] = None
    end_date: Annotated[
        datetime | None,
        Field(
            description=_generic_descriptions.END_DATE,
            json_schema_extra={
                "source": "has_sponsor_model_version|end_date",
                "nullable": True,
            },
        ),
    ] = None
    status: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "source": "has_sponsor_model_version|status",
                "nullable": True,
            },
        ),
    ] = None
    version: Annotated[
        str,
        Field(
            description="Version of the sponsor model.",
            json_schema_extra={"source": "has_sponsor_model_version|version"},
        ),
    ]
    extended_implementation_guide: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "source": "has_sponsor_model_version.extends_version.name",
                "nullable": True,
            },
        ),
    ] = None

    @classmethod
    def from_sponsor_model_ar(
        cls,
        sponsor_model_ar: SponsorModelAR,
    ) -> Self:
        return cls(
            uid=sponsor_model_ar.uid,
            name=sponsor_model_ar.name,
            start_date=sponsor_model_ar.item_metadata.start_date,
            end_date=sponsor_model_ar.item_metadata.end_date,
            status=sponsor_model_ar.item_metadata.status.value,
            version=sponsor_model_ar.item_metadata.version,
        )


class SponsorModelCreateInput(InputModel):
    ig_uid: Annotated[
        str,
        Field(
            description="Unique identifier of the implementation guide to create the sponsor model from. E.g. SDTMIG",
            min_length=1,
        ),
    ] = "SDTMIG"
    ig_version_number: Annotated[
        str,
        Field(
            description="the version number of the Implementation Guide which the sponsor model is based on",
            min_length=1,
        ),
    ]
    version_number: Annotated[
        str,
        Field(
            description="Version number of the sponsor model to use - will be concatenated at the end of the full name",
            min_length=1,
        ),
    ]
    library_name: Annotated[
        str | None, Field(description="Defaults to CDISC", min_length=1)
    ] = "CDISC"


class SponsorModelEditInput(SponsorModelCreateInput):
    change_description: Annotated[
        str, Field(description="Optionally, provide a change description.")
    ] = "Imported new version"
