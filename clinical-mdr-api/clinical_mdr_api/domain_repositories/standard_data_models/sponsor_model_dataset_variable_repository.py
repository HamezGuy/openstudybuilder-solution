from typing import Any

from neomodel import db

from clinical_mdr_api.domain_repositories.library_item_repository import (
    LibraryItemRepositoryImplBase,
)
from clinical_mdr_api.domain_repositories.models.controlled_terminology import (
    CTCodelistRoot,
    CTTermContext,
    CTTermRoot,
)
from clinical_mdr_api.domain_repositories.models.generic import (
    Library,
    VersionRelationship,
)
from clinical_mdr_api.domain_repositories.models.standard_data_model import (
    DataModelCatalogue,
    Dataset,
    DatasetVariable,
    SponsorModelDatasetInstance,
    SponsorModelDatasetVariableInstance,
    SponsorModelValue,
)
from clinical_mdr_api.domain_repositories.standard_data_models.standard_data_model_repository import (
    StandardDataModelRepository,
)
from clinical_mdr_api.domains.standard_data_models.sponsor_model_dataset_variable import (
    SponsorModelDatasetVariableAR,
    SponsorModelDatasetVariableVO,
)
from clinical_mdr_api.domains.versioned_object_aggregate import LibraryVO
from clinical_mdr_api.models.standard_data_models.sponsor_model_dataset_variable import (
    SponsorModelDatasetVariable,
)
from common.exceptions import BusinessLogicException, ValidationException


class SponsorModelDatasetVariableRepository(  # type: ignore[misc]
    StandardDataModelRepository,
    LibraryItemRepositoryImplBase[SponsorModelDatasetVariableAR],
):
    root_class = DatasetVariable
    value_class = SponsorModelDatasetVariableInstance
    return_model = SponsorModelDatasetVariable

    # pylint: disable=unused-argument
    def generic_match_clause(self, versioning_relationship: str):
        standard_data_model_label = self.root_class.__label__
        standard_data_model_value_label = self.value_class.__label__
        return f"""MATCH (standard_root:{standard_data_model_label})-[:HAS_INSTANCE]->
                (standard_value:{standard_data_model_value_label})
                <-[has_dataset_variable_rel:HAS_DATASET_VARIABLE]-(dataset_instance:SponsorModelDatasetInstance)
                <-[:HAS_DATASET]-(sponsor_model_value:SponsorModelValue)
                MATCH (dataset_root:Dataset)-[:HAS_INSTANCE]->(dataset_instance)"""

    def specific_alias_clause(self) -> str:
        return """
        *
        OPTIONAL MATCH (standard_value)-[:REFERENCES_CODELIST]->(ref_cl_root:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)-[:LATEST]->(ref_cl:CTCodelistAttributesValue)
        OPTIONAL MATCH (standard_value)-[:REFERENCES_TERM]->(ref_t_root:CTTermRoot)-[:HAS_ATTRIBUTES_ROOT]->(:CTTermAttributesRoot)-[:LATEST]->(ref_t:CTTermAttributesValue)
        OPTIONAL MATCH (standard_root)<-[key:HAS_KEY]-(dataset_instance)
        WITH
            standard_root.uid AS uid,
            standard_value.is_basic_std AS is_basic_std,
            standard_value.label AS label,
            standard_value.variable_type AS variable_type,
            standard_value.length AS length,
            standard_value.display_format AS display_format,
            standard_value.xml_datatype AS xml_datatype,
            standard_value.core AS core,
            standard_value.origin AS origin,
            standard_value.origin_type AS origin_type,
            standard_value.origin_source AS origin_source,
            standard_value.role AS role,
            standard_value.term AS term,
            standard_value.algorithm AS algorithm,
            standard_value.qualifiers AS qualifiers,
            standard_value.is_cdisc_std AS is_cdisc_std,
            standard_value.comment AS comment,
            standard_value.ig_comment AS ig_comment,
            standard_value.class_table AS class_table,
            standard_value.class_column AS class_column,
            standard_value.map_var_flag AS map_var_flag,
            standard_value.fixed_mapping AS fixed_mapping,
            standard_value.include_in_raw AS include_in_raw,
            standard_value.nn_internal AS nn_internal,
            standard_value.value_lvl_where_cols AS value_lvl_where_cols,
            standard_value.value_lvl_label_col AS value_lvl_label_col,
            standard_value.value_lvl_collect_ct_val AS value_lvl_collect_ct_val,
            standard_value.value_lvl_ct_codelist_id_col AS value_lvl_ct_codelist_id_col,
            standard_value.enrich_build_order AS enrich_build_order,
            standard_value.enrich_rule AS enrich_rule,
            collect(DISTINCT CASE WHEN ref_cl IS NOT NULL THEN {uid: ref_cl_root.uid, submission_value: ref_cl.submission_value} END) AS referenced_codelists,
            collect(DISTINCT CASE WHEN ref_t IS NOT NULL THEN {uid: ref_t_root.uid, submission_value: ref_t.code_submission_value} END) AS referenced_terms,
            {ordinal: has_dataset_variable_rel.ordinal, key_order: key.order, version_number: has_dataset_variable_rel.version_number, uid: dataset_root.uid, sponsor_model_name: sponsor_model_value.name} AS dataset
        """

    def create_query_filter_statement(self, **kwargs) -> tuple[str, dict[Any, Any]]:
        (
            filter_statements_from_standard,
            filter_query_parameters,
        ) = super().create_query_filter_statement(**kwargs)
        filter_parameters = []

        ValidationException.raise_if(
            not kwargs.get("sponsor_model_name")
            or not kwargs.get("sponsor_model_version"),
            msg="Please provide sponsor_model_name and sponsor_model_version params",
        )

        sponsor_model_name = kwargs.get("sponsor_model_name")
        sponsor_model_version = kwargs.get("sponsor_model_version")

        filter_by_sponsor_model_name = "sponsor_model_value.name = $sponsor_model_name"
        filter_parameters.append(filter_by_sponsor_model_name)

        filter_by_sponsor_model_version = (
            "has_dataset_variable_rel.version_number = $sponsor_model_version"
        )
        filter_parameters.append(filter_by_sponsor_model_version)

        filter_query_parameters["sponsor_model_name"] = sponsor_model_name
        filter_query_parameters["sponsor_model_version"] = sponsor_model_version

        extended_filter_statements = " AND ".join(filter_parameters)
        if filter_statements_from_standard != "":
            if len(extended_filter_statements) > 0:
                filter_statements_to_return = " AND ".join(
                    [filter_statements_from_standard, extended_filter_statements]
                )
            else:
                filter_statements_to_return = filter_statements_from_standard
        else:
            filter_statements_to_return = (
                "WHERE " + extended_filter_statements
                if len(extended_filter_statements) > 0
                else ""
            )
        return filter_statements_to_return, filter_query_parameters

    def sort_by(self) -> dict[str, bool] | None:
        return {"dataset.ordinal": True}

    def _has_data_changed(
        self,
        ar: SponsorModelDatasetVariableAR,
        value: SponsorModelDatasetVariableInstance,
    ) -> bool:
        return (
            ar.sponsor_model_dataset_variable_vo.is_basic_std != value.is_basic_std
            or ar.sponsor_model_dataset_variable_vo.label != value.label
            or ar.sponsor_model_dataset_variable_vo.variable_type != value.variable_type
            or ar.sponsor_model_dataset_variable_vo.length != value.length
            or ar.sponsor_model_dataset_variable_vo.display_format
            != value.display_format
            or ar.sponsor_model_dataset_variable_vo.xml_datatype != value.xml_datatype
            or ar.sponsor_model_dataset_variable_vo.core != value.core
            or ar.sponsor_model_dataset_variable_vo.origin != value.origin
            or ar.sponsor_model_dataset_variable_vo.origin_type != value.origin_type
            or ar.sponsor_model_dataset_variable_vo.origin_source != value.origin_source
            or ar.sponsor_model_dataset_variable_vo.role != value.role
            or ar.sponsor_model_dataset_variable_vo.term != value.term
            or ar.sponsor_model_dataset_variable_vo.algorithm != value.algorithm
            or ar.sponsor_model_dataset_variable_vo.qualifiers != value.qualifiers
            or ar.sponsor_model_dataset_variable_vo.is_cdisc_std != value.is_cdisc_std
            or ar.sponsor_model_dataset_variable_vo.comment != value.comment
            or ar.sponsor_model_dataset_variable_vo.ig_comment != value.ig_comment
            or ar.sponsor_model_dataset_variable_vo.class_table != value.class_table
            or ar.sponsor_model_dataset_variable_vo.class_column != value.class_column
            or ar.sponsor_model_dataset_variable_vo.map_var_flag != value.map_var_flag
            or ar.sponsor_model_dataset_variable_vo.fixed_mapping != value.fixed_mapping
            or ar.sponsor_model_dataset_variable_vo.include_in_raw
            != value.include_in_raw
            or ar.sponsor_model_dataset_variable_vo.nn_internal != value.nn_internal
            or ar.sponsor_model_dataset_variable_vo.value_lvl_where_cols
            != value.value_lvl_where_cols
            or ar.sponsor_model_dataset_variable_vo.value_lvl_label_col
            != value.value_lvl_label_col
            or ar.sponsor_model_dataset_variable_vo.value_lvl_collect_ct_val
            != value.value_lvl_collect_ct_val
            or ar.sponsor_model_dataset_variable_vo.value_lvl_ct_codelist_id_col
            != value.value_lvl_ct_codelist_id_col
            or ar.sponsor_model_dataset_variable_vo.enrich_build_order
            != value.enrich_build_order
            or ar.sponsor_model_dataset_variable_vo.enrich_rule != value.enrich_rule
        )

    def _create(
        self, item: SponsorModelDatasetVariableAR
    ) -> SponsorModelDatasetVariableAR:
        """
        Overrides generic LibraryItemRepository method
        """
        root = DatasetVariable.nodes.get_or_none(uid=item.uid)

        if not root:
            # Create a new "root" node with uid
            root = DatasetVariable(uid=item.uid).save()
            # Link it with the DataModelCatalogue node
            catalogue = DataModelCatalogue.nodes.get_or_none(
                name=item.sponsor_model_dataset_variable_vo.target_data_model_catalogue
            )
            root.has_dataset_variable.connect(catalogue)

        instance = self._get_or_create_instance(root=root, ar=item)

        # Connect with SponsorModelDatasetInstance
        parent_dataset_instance = SponsorModelDatasetInstance.nodes.filter(
            is_instance_of__uid=item.sponsor_model_dataset_variable_vo.dataset_uid,
            has_dataset__name=item.sponsor_model_dataset_variable_vo.sponsor_model_name,
        ).resolve_subgraph()
        BusinessLogicException.raise_if_not(
            parent_dataset_instance,
            msg=f"Dataset with UID '{item.sponsor_model_dataset_variable_vo.dataset_uid}' is not instantiated in this version of the sponsor model.",
        )
        instance.has_variable.connect(
            parent_dataset_instance[0],
            {
                "ordinal": item.sponsor_model_dataset_variable_vo.order,
                "version_number": item.sponsor_model_dataset_variable_vo.sponsor_model_version_number,
            },
        )

        return item

    def _get_or_create_instance(
        self, root: DatasetVariable, ar: SponsorModelDatasetVariableAR
    ) -> SponsorModelDatasetVariableInstance:
        for itm in root.has_sponsor_model_instance.all():
            if not self._has_data_changed(ar, itm):
                return itm

        new_instance = SponsorModelDatasetVariableInstance(
            is_basic_std=ar.sponsor_model_dataset_variable_vo.is_basic_std,
            label=ar.sponsor_model_dataset_variable_vo.label,
            variable_type=ar.sponsor_model_dataset_variable_vo.variable_type,
            length=ar.sponsor_model_dataset_variable_vo.length,
            display_format=ar.sponsor_model_dataset_variable_vo.display_format,
            xml_datatype=ar.sponsor_model_dataset_variable_vo.xml_datatype,
            core=ar.sponsor_model_dataset_variable_vo.core,
            origin=ar.sponsor_model_dataset_variable_vo.origin,
            origin_type=ar.sponsor_model_dataset_variable_vo.origin_type,
            origin_source=ar.sponsor_model_dataset_variable_vo.origin_source,
            role=ar.sponsor_model_dataset_variable_vo.role,
            term=ar.sponsor_model_dataset_variable_vo.term,
            algorithm=ar.sponsor_model_dataset_variable_vo.algorithm,
            qualifiers=ar.sponsor_model_dataset_variable_vo.qualifiers,
            is_cdisc_std=ar.sponsor_model_dataset_variable_vo.is_cdisc_std,
            comment=ar.sponsor_model_dataset_variable_vo.comment,
            ig_comment=ar.sponsor_model_dataset_variable_vo.ig_comment,
            class_table=ar.sponsor_model_dataset_variable_vo.class_table,
            class_column=ar.sponsor_model_dataset_variable_vo.class_column,
            map_var_flag=ar.sponsor_model_dataset_variable_vo.map_var_flag,
            fixed_mapping=ar.sponsor_model_dataset_variable_vo.fixed_mapping,
            include_in_raw=ar.sponsor_model_dataset_variable_vo.include_in_raw,
            nn_internal=ar.sponsor_model_dataset_variable_vo.nn_internal,
            value_lvl_where_cols=ar.sponsor_model_dataset_variable_vo.value_lvl_where_cols,
            value_lvl_label_col=ar.sponsor_model_dataset_variable_vo.value_lvl_label_col,
            value_lvl_collect_ct_val=ar.sponsor_model_dataset_variable_vo.value_lvl_collect_ct_val,
            value_lvl_ct_codelist_id_col=ar.sponsor_model_dataset_variable_vo.value_lvl_ct_codelist_id_col,
            enrich_build_order=ar.sponsor_model_dataset_variable_vo.enrich_build_order,
            enrich_rule=ar.sponsor_model_dataset_variable_vo.enrich_rule,
        )

        self._db_save_node(new_instance)

        # Add extra properties using Cypher (neomodel only saves defined properties)
        if ar.sponsor_model_dataset_variable_vo.extra_properties:
            # Sanitize key names for Neo4j (replace spaces and dashes with underscores)
            sanitized_props = {
                key.replace(" ", "_").replace("-", "_"): value
                for key, value in ar.sponsor_model_dataset_variable_vo.extra_properties.items()
            }
            db.cypher_query(
                "MATCH (n) WHERE elementId(n) = $element_id SET n += $extra_props",
                {"element_id": new_instance.element_id, "extra_props": sanitized_props},
            )

        # Connect with root
        root.has_sponsor_model_instance.connect(new_instance)

        # Connect with implemented variable class - if provided
        # Note : This is done through Cypher because the neomodel version with
        # chained traversal-based filters was creating a bad query
        if ar.sponsor_model_dataset_variable_vo.implemented_variable_class:
            results, _ = db.cypher_query(
                """
                    MATCH (vc:VariableClass)-[:`HAS_INSTANCE`]->(vci:VariableClassInstance)<-[:`HAS_VARIABLE_CLASS`]-(dci:DatasetClassInstance)
                    <-[:`HAS_DATASET_CLASS`]-(:DataModelValue)
                    <-[:`IMPLEMENTS`]-(ig:DataModelIGValue)<-[:`EXTENDS_VERSION`]-(smv:SponsorModelValue)
                    MATCH (dci)<-[:`HAS_INSTANCE`]-(dc:DatasetClass)
                    WHERE
                        smv.name = $smv_name
                        AND dc.uid = $dc_uid
                        AND vc.uid = $vc_uid
                    RETURN vci
                """,
                params={
                    "smv_name": ar.sponsor_model_dataset_variable_vo.sponsor_model_name,
                    "dc_uid": ar.sponsor_model_dataset_variable_vo.implemented_parent_dataset_class,
                    "vc_uid": ar.sponsor_model_dataset_variable_vo.implemented_variable_class,
                },
                resolve_objects=True,
            )

            if results:
                implemented_variable_class_instance = results[0][0]
                new_instance.implements_variable_class.connect(
                    implemented_variable_class_instance
                )
            else:
                # If the target variable class is not found
                # Either because it does not exist or exists but not in the target parent dataset class
                # Do not raise an exception, but store information about the inconsistency on the node
                new_instance.implemented_variable_class_inconsistency = True
                new_instance.implemented_variable_class_uid = (
                    ar.sponsor_model_dataset_variable_vo.implemented_variable_class
                )
                if (
                    ar.sponsor_model_dataset_variable_vo.implemented_parent_dataset_class
                ):
                    new_instance.implemented_parent_dataset_class_uid = (
                        ar.sponsor_model_dataset_variable_vo.implemented_parent_dataset_class
                    )
                self._db_save_node(new_instance)

        # Connect with Codelists & Terms
        for codelist_uid in (
            ar.sponsor_model_dataset_variable_vo.references_codelists or []
        ):
            codelist_node = CTCodelistRoot.nodes.get_or_none(uid=codelist_uid)
            BusinessLogicException.raise_if_not(
                codelist_node,
                msg=f"Could not find codelist with uid '{codelist_uid}'.",
            )
            new_instance.references_codelist.connect(codelist_node)
        for term_uid in ar.sponsor_model_dataset_variable_vo.references_terms or []:
            term_node = CTTermRoot.nodes.get_or_none(uid=term_uid)
            BusinessLogicException.raise_if_not(
                term_node,
                msg=f"Could not find term with uid '{term_uid}'.",
            )
            for codelist_uid in (
                ar.sponsor_model_dataset_variable_vo.references_codelists or []
            ):
                codelist_node = CTCodelistRoot.nodes.get_or_none(uid=codelist_uid)
                term_context = CTTermContext()
                self._db_save_node(term_context)
                term_context.has_selected_codelist.connect(codelist_node)
                term_context.has_selected_term.connect(term_node)
                new_instance.references_term.connect(term_context)
        return new_instance

    def _create_aggregate_root_instance_from_version_root_relationship_and_value(
        self,
        root: DatasetVariable,
        library: Library,
        relationship: VersionRelationship,
        value: SponsorModelDatasetVariableInstance,
        **_kwargs,
    ) -> SponsorModelDatasetVariableAR:
        # Get parent dataset-related info
        dataset_value: SponsorModelDatasetInstance = value.has_variable.get_or_none()
        dataset_uid = None
        ordinal = 0
        # Get parent dataset uid
        dataset: Dataset = dataset_value.has_sponsor_model_instance.single()
        if dataset is not None:
            dataset_uid = dataset.uid

        # Get order in parent class
        dataset_rel = value.has_variable.relationship(dataset_value)
        ordinal = dataset_rel.ordinal
        sponsor_model_version = dataset_rel.version_number

        # Get sponsor model-related info
        sponsor_model_value: SponsorModelValue = dataset_value.has_dataset.single()
        sponsor_model_name = sponsor_model_value.name

        # Extract extra properties from the Neo4j node
        known_fields = {
            "is_basic_std",
            "label",
            "variable_type",
            "length",
            "display_format",
            "xml_datatype",
            "core",
            "origin",
            "origin_type",
            "origin_source",
            "role",
            "term",
            "algorithm",
            "qualifiers",
            "is_cdisc_std",
            "comment",
            "ig_comment",
            "class_table",
            "class_column",
            "map_var_flag",
            "fixed_mapping",
            "include_in_raw",
            "nn_internal",
            "value_lvl_where_cols",
            "value_lvl_label_col",
            "value_lvl_collect_ct_val",
            "value_lvl_ct_codelist_id_col",
            "enrich_build_order",
            "enrich_rule",
            "id",
            "element_id",
            "implemented_variable_class_inconsistency",
            "implemented_variable_class_uid",
            "implemented_parent_dataset_class_uid",
            "implemented_parent_dataset_class",
            "implemented_variable_class",
            "references_codelist",
            "references_terms",
        }
        extra_props = {}
        for key in dir(value):
            if not key.startswith("_") and hasattr(value, key):
                attr = getattr(value, key)
                # Only include simple data types (not methods, relationships, etc.)
                if (
                    key not in known_fields
                    and not callable(attr)
                    and not hasattr(attr, "_all")
                ):
                    extra_props[key] = attr

        return SponsorModelDatasetVariableAR.from_repository_values(
            variable_uid=root.uid,
            sponsor_model_dataset_variable_vo=SponsorModelDatasetVariableVO.from_repository_values(
                dataset_uid=dataset_uid,
                variable_uid=root.uid,
                sponsor_model_name=sponsor_model_name,
                sponsor_model_version_number=sponsor_model_version,
                is_basic_std=value.is_basic_std,
                implemented_parent_dataset_class=value.implemented_parent_dataset_class,
                implemented_variable_class=value.implemented_variable_class,
                label=value.label,
                order=ordinal,
                variable_type=value.variable_type,
                length=value.length,
                display_format=value.display_format,
                xml_datatype=value.xml_datatype,
                references_codelists=value.references_codelist,  # type: ignore[arg-type]
                references_terms=value.references_terms,
                core=value.core,
                origin=value.origin,
                origin_type=value.origin_type,
                origin_source=value.origin_source,
                role=value.role,
                term=value.term,
                algorithm=value.algorithm,
                qualifiers=value.qualifiers,
                is_cdisc_std=value.is_cdisc_std,
                comment=value.comment,
                ig_comment=value.ig_comment,
                class_table=value.class_table,
                class_column=value.class_column,
                map_var_flag=value.map_var_flag,
                fixed_mapping=value.fixed_mapping,
                include_in_raw=value.include_in_raw,
                nn_internal=value.nn_internal,
                value_lvl_where_cols=value.value_lvl_where_cols,
                value_lvl_label_col=value.value_lvl_label_col,
                value_lvl_collect_ct_val=value.value_lvl_collect_ct_val,
                value_lvl_ct_codelist_id_col=value.value_lvl_ct_codelist_id_col,
                enrich_build_order=value.enrich_build_order,
                enrich_rule=value.enrich_rule,
                extra_properties=extra_props if extra_props else None,
            ),
            library=LibraryVO.from_input_values_2(
                library_name=library.name,
                is_library_editable_callback=lambda _: library.is_editable,
            ),
            item_metadata=self._library_item_metadata_vo_from_relation(relationship),
        )

    def _maintain_parameters(
        self,
        versioned_object: SponsorModelDatasetVariableAR,
        root: DatasetVariable,
        value: SponsorModelDatasetVariableInstance,
    ) -> None:
        # This method is not needed for this repo, so we use pass to skip implementation
        pass
