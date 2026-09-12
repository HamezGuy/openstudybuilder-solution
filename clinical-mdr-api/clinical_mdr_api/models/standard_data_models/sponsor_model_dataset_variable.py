from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from clinical_mdr_api.domains.standard_data_models.sponsor_model_dataset_variable import (
    SponsorModelDatasetVariableAR,
)
from clinical_mdr_api.models.libraries.library import Library
from clinical_mdr_api.models.standard_data_models.sponsor_model import SponsorModelBase
from clinical_mdr_api.models.utils import InputModel


class ReferencedCodelist(BaseModel):
    uid: str
    submission_value: str


class ReferencedTerm(BaseModel):
    uid: str
    submission_value: str


class SimpleSponsorModelDataset(SponsorModelBase):
    uid: str
    ordinal: Annotated[
        int | None,
        Field(
            json_schema_extra={
                "nullable": True,
            }
        ),
    ] = None
    key_order: Annotated[int | None, Field(json_schema_extra={"nullable": True})] = None
    version_number: int
    sponsor_model_name: str


class SponsorModelDatasetVariable(SponsorModelBase):
    model_config = ConfigDict(from_attributes=True, extra="allow")

    uid: Annotated[str | None, Field(json_schema_extra={"nullable": True})] = None
    dataset: Annotated[
        SimpleSponsorModelDataset | None,
        Field(json_schema_extra={"nullable": True}),
    ] = None
    is_basic_std: Annotated[
        bool | None,
        Field(
            json_schema_extra={
                "nullable": True,
                "remove_from_wildcard": True,
            },
        ),
    ] = None
    label: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    variable_type: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    length: Annotated[
        int | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    display_format: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    xml_datatype: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    core: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    origin: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    origin_type: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    origin_source: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    role: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    term: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    algorithm: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    qualifiers: Annotated[
        list[str] | None,
        Field(
            json_schema_extra={
                "nullable": True,
                "remove_from_wildcard": True,
            },
        ),
    ] = None
    is_cdisc_std: Annotated[
        bool | None,
        Field(
            json_schema_extra={
                "nullable": True,
                "remove_from_wildcard": True,
            },
        ),
    ] = None
    comment: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    ig_comment: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    class_table: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    class_column: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    map_var_flag: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    fixed_mapping: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    include_in_raw: Annotated[
        bool | None,
        Field(
            json_schema_extra={
                "nullable": True,
                "remove_from_wildcard": True,
            },
        ),
    ] = None
    nn_internal: Annotated[
        bool | None,
        Field(
            json_schema_extra={
                "nullable": True,
                "remove_from_wildcard": True,
            },
        ),
    ] = None
    value_lvl_where_cols: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    value_lvl_label_col: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    value_lvl_collect_ct_val: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    value_lvl_ct_codelist_id_col: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    enrich_build_order: Annotated[
        int | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    enrich_rule: Annotated[
        str | None,
        Field(
            json_schema_extra={
                "nullable": True,
            },
        ),
    ] = None
    referenced_codelists: list[ReferencedCodelist] = Field(default_factory=list)
    referenced_terms: list[ReferencedTerm] = Field(default_factory=list)

    @classmethod
    def from_repository_output(cls, input_dict: dict[str, Any]):
        return cls(
            uid=input_dict["uid"],
            dataset=SimpleSponsorModelDataset(
                uid=input_dict.get("dataset", {}).get("uid"),
                ordinal=input_dict.get("dataset", {}).get("ordinal"),
                key_order=input_dict.get("dataset", {}).get("key_order"),
                version_number=input_dict.get("dataset", {}).get("version_number"),
                sponsor_model_name=input_dict.get("dataset", {}).get(
                    "sponsor_model_name"
                ),
            ),
            is_basic_std=input_dict.get("is_basic_std"),
            label=input_dict.get("label"),
            variable_type=input_dict.get("variable_type"),
            length=input_dict.get("length"),
            display_format=input_dict.get("display_format"),
            xml_datatype=input_dict.get("xml_datatype"),
            core=input_dict.get("core"),
            origin=input_dict.get("origin"),
            origin_type=input_dict.get("origin_type"),
            origin_source=input_dict.get("origin_source"),
            role=input_dict.get("role"),
            term=input_dict.get("term"),
            algorithm=input_dict.get("algorithm"),
            qualifiers=input_dict.get("qualifiers"),
            is_cdisc_std=input_dict.get("is_cdisc_std"),
            comment=input_dict.get("comment"),
            ig_comment=input_dict.get("ig_comment"),
            class_table=input_dict.get("class_table"),
            class_column=input_dict.get("class_column"),
            map_var_flag=input_dict.get("map_var_flag"),
            fixed_mapping=input_dict.get("fixed_mapping"),
            include_in_raw=input_dict.get("include_in_raw"),
            nn_internal=input_dict.get("nn_internal"),
            value_lvl_where_cols=input_dict.get("value_lvl_where_cols"),
            value_lvl_label_col=input_dict.get("value_lvl_label_col"),
            value_lvl_collect_ct_val=input_dict.get("value_lvl_collect_ct_val"),
            value_lvl_ct_codelist_id_col=input_dict.get("value_lvl_ct_codelist_id_col"),
            enrich_build_order=input_dict.get("enrich_build_order"),
            enrich_rule=input_dict.get("enrich_rule"),
            referenced_codelists=[
                ReferencedCodelist(
                    uid=cl["uid"], submission_value=cl["submission_value"]
                )
                for cl in input_dict.get("referenced_codelists", [])
                if cl is not None
            ],
            referenced_terms=[
                ReferencedTerm(uid=t["uid"], submission_value=t["submission_value"])
                for t in input_dict.get("referenced_terms", [])
                if t is not None
            ],
        )

    @classmethod
    def from_sponsor_model_dataset_variable_ar(
        cls,
        sponsor_model_dataset_variable_ar: SponsorModelDatasetVariableAR,
    ) -> "SponsorModelDatasetVariable":
        base_data = {
            "uid": sponsor_model_dataset_variable_ar.uid,
            "is_basic_std": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.is_basic_std,
            "label": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.label,
            "order": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.order,
            "variable_type": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.variable_type,
            "length": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.length,
            "display_format": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.display_format,
            "xml_datatype": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.xml_datatype,
            "core": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.core,
            "origin": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.origin,
            "origin_type": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.origin_type,
            "origin_source": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.origin_source,
            "role": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.role,
            "term": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.term,
            "algorithm": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.algorithm,
            "qualifiers": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.qualifiers,
            "is_cdisc_std": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.is_cdisc_std,
            "comment": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.comment,
            "ig_comment": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.ig_comment,
            "class_table": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.class_table,
            "class_column": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.class_column,
            "map_var_flag": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.map_var_flag,
            "fixed_mapping": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.fixed_mapping,
            "include_in_raw": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.include_in_raw,
            "nn_internal": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.nn_internal,
            "value_lvl_where_cols": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.value_lvl_where_cols,
            "value_lvl_label_col": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.value_lvl_label_col,
            "value_lvl_collect_ct_val": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.value_lvl_collect_ct_val,
            "value_lvl_ct_codelist_id_col": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.value_lvl_ct_codelist_id_col,
            "enrich_build_order": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.enrich_build_order,
            "enrich_rule": sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.enrich_rule,
            "library_name": Library.from_library_vo(
                sponsor_model_dataset_variable_ar.library
            ).name,
        }

        # Add extra properties if they exist
        if (
            sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.extra_properties
        ):
            base_data.update(
                sponsor_model_dataset_variable_ar.sponsor_model_dataset_variable_vo.extra_properties
            )

        return cls(**base_data)  # type: ignore[arg-type]


class SponsorModelDatasetVariableInput(InputModel):
    model_config = ConfigDict(extra="allow")  # Allow extra fields

    target_data_model_catalogue: Annotated[str | None, Field()] = "SDTMIG"
    dataset_uid: Annotated[
        str,
        Field(
            description="Uid of the dataset in which to create the variable. E.g AE",
            min_length=1,
        ),
    ]
    dataset_variable_uid: Annotated[str, Field(min_length=1)]
    sponsor_model_name: Annotated[
        str,
        Field(
            description="Name of the sponsor model in which to create the variable. E.g sdtmig_sponsormodel...",
            min_length=1,
        ),
    ]
    sponsor_model_version_number: Annotated[
        str,
        Field(
            description="Version number of the sponsor model in which to create the variable",
            min_length=1,
        ),
    ]
    is_basic_std: Annotated[bool, Field()]
    implemented_parent_dataset_class: Annotated[
        str | None,
        Field(
            description="The uid of the dataset class that the variable class belongs to, e.g. Findings",
            json_schema_extra={"nullable": True},
        ),
    ]
    implemented_variable_class: Annotated[
        str | None,
        Field(
            description="The uid of the implemented dataset variable, e.g. --ORRES",
            json_schema_extra={"nullable": True},
        ),
    ]
    label: Annotated[str | None, Field()] = None
    order: Annotated[int | None, Field()] = None
    variable_type: Annotated[str | None, Field()] = None
    length: Annotated[int | None, Field()] = None
    display_format: Annotated[str | None, Field()] = None
    xml_datatype: Annotated[str | None, Field()] = None
    references_codelists: Annotated[list[str] | None, Field()] = None
    references_terms: Annotated[list[str] | None, Field()] = None
    core: Annotated[str | None, Field()] = None
    origin: Annotated[str | None, Field()] = None
    origin_type: Annotated[str | None, Field()] = None
    origin_source: Annotated[str | None, Field()] = None
    role: Annotated[str | None, Field()] = None
    term: Annotated[str | None, Field()] = None
    algorithm: Annotated[str | None, Field()] = None
    qualifiers: Annotated[list[str] | None, Field()] = None
    is_cdisc_std: Annotated[bool, Field()]
    comment: Annotated[str | None, Field()] = None
    ig_comment: Annotated[str | None, Field()] = None
    class_table: Annotated[str | None, Field()] = None
    class_column: Annotated[str | None, Field()] = None
    map_var_flag: Annotated[str | None, Field()] = None
    fixed_mapping: Annotated[str | None, Field()] = None
    include_in_raw: Annotated[bool, Field()] = False
    nn_internal: Annotated[bool, Field()] = False
    value_lvl_where_cols: Annotated[str | None, Field()] = None
    value_lvl_label_col: Annotated[str | None, Field()] = None
    value_lvl_collect_ct_val: Annotated[str | None, Field()] = None
    value_lvl_ct_codelist_id_col: Annotated[str | None, Field()] = None
    enrich_build_order: Annotated[int | None, Field()] = None
    enrich_rule: Annotated[str | None, Field()] = None
    library_name: Annotated[
        str | None, Field(description="Defaults to CDISC", min_length=1)
    ] = "CDISC"

    def get_extra_fields(self) -> dict[str, Any]:
        """Return fields that were passed but aren't in the defined model."""
        defined_fields = set(self.model_fields.keys())
        all_fields = set(self.model_dump().keys())
        extra_fields = all_fields - defined_fields
        return {field: getattr(self, field) for field in extra_fields}
