import json
from typing import Any

from neomodel import db
from neomodel.sync_.match import NodeNameResolver, Path

from clinical_mdr_api.domain_repositories.concepts.concept_generic_repository import (
    ConceptGenericRepository,
)
from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_codelist_attributes_repository import (
    CTCodelistAttributesRepository,
)
from clinical_mdr_api.domain_repositories.models.biomedical_concepts import (
    ActivityInstanceClassRoot,
    ActivityItemClassRoot,
    ActivityItemClassValue,
)
from clinical_mdr_api.domain_repositories.models.controlled_terminology import (
    CTCodelistRoot,
    CTTermRoot,
)
from clinical_mdr_api.domain_repositories.models.generic import (
    Library,
    VersionRelationship,
)
from clinical_mdr_api.domain_repositories.models.standard_data_model import (
    VariableClass,
)
from clinical_mdr_api.domains.biomedical_concepts.activity_item_class import (
    ActivityInstanceClassActivityItemClassRelVO,
    ActivityItemClassAR,
    ActivityItemClassVO,
    CTTermItem,
)
from clinical_mdr_api.domains.versioned_object_aggregate import (
    LibraryItemMetadataVO,
    LibraryItemStatus,
    LibraryVO,
)
from clinical_mdr_api.models.biomedical_concepts.activity_item_class import (
    ActivityItemClass,
)
from clinical_mdr_api.repositories._utils import sb_clear_cache
from common.config import settings
from common.utils import convert_to_datetime


class ActivityItemClassRepository(ConceptGenericRepository[ActivityItemClassAR]):
    root_class = ActivityItemClassRoot
    value_class = ActivityItemClassValue
    aggregate_class = ActivityItemClassAR
    value_object_class = ActivityItemClassVO
    return_model = ActivityItemClass

    def generic_alias_clause(self, *, return_all_versions: bool = False, **kwargs):
        """Override to use ActivityItemClass-specific library relationship."""
        version_return = (
            "RETURN hv AS version_rel"
            if return_all_versions
            else """WITH collect(hv) as hvs
                RETURN last(hvs) AS version_rel"""
        )
        return f"""
            DISTINCT concept_root, concept_value,
            head([(library:Library)-[:CONTAINS]->(concept_root) | library]) AS library
            CALL {{
                WITH concept_root, concept_value
                MATCH (concept_root)-[hv:HAS_VERSION]-(concept_value)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.end_date ASC,
                    hv.start_date ASC
                {version_return}
            }}
            WITH
                concept_root,
                concept_root.uid AS uid,
                concept_value as concept_value,
                library.name AS library_name,
                library.is_editable AS is_library_editable,
                version_rel
                CALL {{
                    WITH version_rel
                    OPTIONAL MATCH (author: User)
                    WHERE author.user_id = version_rel.author_id
                    RETURN author
                }}
            WITH
                uid,
                concept_root,
                concept_value,
                concept_value.nci_concept_id AS nci_concept_id,
                concept_value.name AS name,
                concept_value.definition AS definition,
                concept_value.display_name AS display_name,
                library_name,
                is_library_editable,
                version_rel.version AS version,
                version_rel.start_date AS start_date,
                version_rel.end_date AS end_date,
                version_rel.status AS status,
                version_rel.change_description AS change_description,
                version_rel.author_id AS author_id,
                COALESCE(author.username, version_rel.author_id) AS author_username
        """

    def generic_alias_clause_all_versions(self):
        return self.generic_alias_clause(return_all_versions=True)

    def _create_aggregate_root_instance_from_cypher_result(
        self, input_dict: dict[str, Any]
    ) -> ActivityItemClassAR:
        """Build ActivityItemClassAR from Cypher query result dictionary."""
        activity_instance_classes = [
            ActivityInstanceClassActivityItemClassRelVO(
                uid=aic["uid"],
                mandatory=aic.get("mandatory", False),
                is_adam_param_specific_enabled=aic.get(
                    "is_adam_param_specific_enabled", False
                ),
                is_additional_optional=aic.get("is_additional_optional", False),
                is_default_linked=aic.get("is_default_linked", False),
            )
            for aic in input_dict.get("activity_instance_classes", [])
        ]

        role = CTTermItem(
            uid=input_dict["role"]["uid"],
            name=input_dict["role"]["name"],
            codelist_uid=input_dict["role"]["codelist_uid"],
        )

        data_type = CTTermItem(
            uid=input_dict["data_type"]["uid"],
            name=input_dict["data_type"]["name"],
            codelist_uid=input_dict["data_type"]["codelist_uid"],
        )

        # Split version into major and minor components
        major, minor = input_dict["version"].split(".")

        return self.aggregate_class.from_repository_values(
            uid=input_dict["uid"],
            activity_item_class_vo=self.value_object_class.from_repository_values(
                name=input_dict["name"],
                definition=input_dict["definition"],
                nci_concept_id=input_dict.get("nci_concept_id"),
                order=input_dict["order"],
                activity_instance_classes=activity_instance_classes,
                role=role,
                data_type=data_type,
                variable_class_uids=input_dict.get("variable_class_uids", []),
                display_name=input_dict.get("display_name"),
            ),
            library=LibraryVO.from_input_values_2(
                library_name=input_dict.get("library_name", "Unknown"),
                is_library_editable_callback=lambda _: input_dict.get(
                    "is_library_editable", False
                ),
            ),
            item_metadata=LibraryItemMetadataVO.from_repository_values(
                change_description=input_dict["change_description"],
                status=LibraryItemStatus(input_dict.get("status")),
                author_id=input_dict["author_id"],
                author_username=input_dict.get("author_username"),
                start_date=convert_to_datetime(value=input_dict["start_date"]),
                end_date=convert_to_datetime(value=input_dict.get("end_date")),
                major_version=int(major),
                minor_version=int(minor),
            ),
        )

    def specific_alias_clause(self, **kwargs) -> str:
        """Add ActivityItemClass-specific fields to the Cypher query."""
        return """
        WITH *,
            concept_value.order AS order,

            [(concept_root)<-[rel:HAS_ITEM_CLASS]-(activity_instance_class_root:ActivityInstanceClassRoot)-[:LATEST]->(activity_instance_class_value:ActivityInstanceClassValue) | {
                uid: activity_instance_class_root.uid,
                name: activity_instance_class_value.name,
                mandatory: rel.mandatory,
                is_adam_param_specific_enabled: rel.is_adam_param_specific_enabled,
                is_additional_optional: rel.is_additional_optional,
                is_default_linked: rel.is_default_linked
            }] AS activity_instance_classes,

            COLLECT {
                MATCH (concept_value)-[:HAS_ROLE]->(role_context)-[:HAS_SELECTED_TERM]->(role_term:CTTermRoot)-[:HAS_NAME_ROOT]->(role_name_root:CTTermNameRoot)-[:LATEST]->(role_name_value:CTTermNameValue)
                MATCH (role_context)-[:HAS_SELECTED_CODELIST]->(role_codelist:CTCodelistRoot)
                RETURN {uid: role_term.uid, name: role_name_value.name, codelist_uid: role_codelist.uid}
            }[0] AS role,

            COLLECT {
                MATCH (concept_value)-[:HAS_DATA_TYPE]->(data_type_context)-[:HAS_SELECTED_TERM]->(data_type_term:CTTermRoot)-[:HAS_NAME_ROOT]->(data_type_name_root:CTTermNameRoot)-[:LATEST]->(data_type_name_value:CTTermNameValue)
                MATCH (data_type_context)-[:HAS_SELECTED_CODELIST]->(data_type_codelist:CTCodelistRoot)
                RETURN {uid: data_type_term.uid, name: data_type_name_value.name, codelist_uid: data_type_codelist.uid}
            }[0] AS data_type,

            [(concept_root)-[:MAPS_VARIABLE_CLASS]->(variable_class:VariableClass) | variable_class.uid] AS variable_class_uids
        """

    def _create_new_value_node(self, ar: ActivityItemClassAR) -> ActivityItemClassValue:
        """Override to handle ActivityItemClass-specific value node creation."""
        new_value = ActivityItemClassValue(
            name=ar.activity_item_class_vo.name,
            order=ar.activity_item_class_vo.order,
            definition=ar.activity_item_class_vo.definition,
            nci_concept_id=ar.activity_item_class_vo.nci_concept_id,
            display_name=ar.activity_item_class_vo.display_name,
        )
        return new_value

    def get_all_for_activity_instance_class(
        self,
        activity_instance_class_uid: str,
        ig_uid: str | None = None,
        dataset_uid: str | None = None,
    ) -> set[ActivityItemClassRoot]:
        """
        Return all Activity Item Class nodes linked to given Activity Instance Class
        and its parents.
        """

        # Building a Cypher query for performance optimization
        # Also, using some or on node labels and relationship types
        base_match = """
            MATCH (aicv:ActivityItemClassValue)<-[:LATEST]-(aicr:ActivityItemClassRoot)
            <-[has_activity_instance_class:HAS_ITEM_CLASS]-(:ActivityInstanceClassRoot{uid:$activity_instance_class_uid})
        """

        base_parent_match = """
            MATCH (aic:ActivityInstanceClassRoot {uid:$activity_instance_class_uid})-[:PARENT_CLASS]->{1,3}(p_aic:ActivityInstanceClassRoot)
            -[has_activity_instance_class:HAS_ITEM_CLASS]->(aicr:ActivityItemClassRoot)-[:LATEST]->(aicv:ActivityItemClassValue)
        """

        match_for_filter = """
            MATCH (aicr)-[:MAPS_VARIABLE_CLASS]->(:VariableClass)-[:HAS_INSTANCE]->(:VariableClassInstance)
            <-[:IMPLEMENTS_VARIABLE|IMPLEMENTS_VARIABLE_CLASS]-(:DatasetVariableInstance|SponsorModelDatasetVariableInstance)
            <-[:HAS_DATASET_VARIABLE]-(di:DatasetInstance|SponsorModelDatasetInstance)
        """

        dataset_uid_filter = (
            "EXISTS((di)<-[:HAS_INSTANCE]-(:Dataset {uid: $dataset_uid}))"
        )
        ig_uid_filter = """
            (
                EXISTS((di)<-[:HAS_DATASET]-(:DataModelIGValue)<-[:HAS_VERSION]-(:DataModelIGRoot {uid: $ig_uid}))
                OR
                EXISTS((di)<-[:HAS_DATASET]-(:SponsorModelValue)-[:EXTENDS_VERSION]->(:DataModelIGValue)<-[:HAS_VERSION]-(:DataModelIGRoot {uid: $ig_uid}))
            )
        """

        data_type_match = """
            OPTIONAL MATCH (aicv)-[:HAS_DATA_TYPE]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(data_type_root:CTTermRoot)
            -[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(data_type_name_value)
        """

        return_clause = "RETURN DISTINCT aicr, aicv, has_activity_instance_class, data_type_root.uid AS data_type_uid, data_type_name_value.name AS data_type_name"

        query_elements = [base_match]
        filter_clause = ""
        if dataset_uid or ig_uid:
            query_elements.append(match_for_filter)

            filter_elements = []
            if dataset_uid:
                filter_elements.append(dataset_uid_filter)
            if ig_uid:
                filter_elements.append(ig_uid_filter)
            filter_clause = "WHERE " + " AND ".join(filter_elements)
            query_elements.append(filter_clause)

        query_elements.append(data_type_match)
        query_elements.append(return_clause)
        query_elements.append("UNION")
        query_elements.append(base_parent_match)
        if filter_clause:
            query_elements.append(match_for_filter)
            query_elements.append(filter_clause)
        query_elements.append(data_type_match)
        query_elements.append(return_clause)

        query = " ".join(query_elements)

        results, meta = db.cypher_query(
            query,
            params={
                "activity_instance_class_uid": activity_instance_class_uid,
                "dataset_uid": dataset_uid,
                "ig_uid": ig_uid,
            },
        )

        return [dict(zip(meta, row)) for row in results]

    def _has_data_changed(
        self, ar: ActivityItemClassAR, value: ActivityItemClassValue
    ) -> bool:
        existing_activity_instance_classes = []
        root = value.has_version.single()
        for node in root.has_activity_instance_class.all():
            rel = root.has_activity_instance_class.relationship(node)
            existing_activity_instance_classes.append(
                {
                    "uid": node.uid,
                    "mandatory": rel.mandatory,
                    "is_adam_param_specific_enabled": rel.is_adam_param_specific_enabled,
                    "is_additional_optional": rel.is_additional_optional,
                    "is_default_linked": rel.is_default_linked,
                }
            )
        existing_activity_instance_classes.sort(key=json.dumps)

        new_activity_instance_classes = sorted(
            [
                item.__dict__
                for item in ar.activity_item_class_vo.activity_instance_classes
            ],
            key=json.dumps,
        )

        if _role := value.has_role.single():
            _role = (
                _role.has_selected_term.single().uid,
                _role.has_selected_codelist.single().uid,
            )
        else:
            _role = (None, None)

        if _data_type := value.has_data_type.single():
            _data_type = (
                _data_type.has_selected_term.single().uid,
                _data_type.has_selected_codelist.single().uid,
            )
        else:
            _data_type = (None, None)

        return (
            ar.activity_item_class_vo.name != value.name
            or ar.activity_item_class_vo.definition != value.definition
            or ar.activity_item_class_vo.nci_concept_id != value.nci_concept_id
            or ar.activity_item_class_vo.order != value.order
            or ar.activity_item_class_vo.display_name != value.display_name
            or new_activity_instance_classes != existing_activity_instance_classes
            or (
                ar.activity_item_class_vo.role.uid,
                ar.activity_item_class_vo.role.codelist_uid,
            )
            != _role
            or (
                ar.activity_item_class_vo.data_type.uid,
                ar.activity_item_class_vo.data_type.codelist_uid,
            )
            != _data_type
        )

    def _get_or_create_value(
        self,
        root: ActivityItemClassRoot,
        ar: ActivityItemClassAR,
        force_new_value_node: bool = False,
    ) -> ActivityItemClassValue:
        if not force_new_value_node:
            for itm in root.has_version.all():
                if not self._has_data_changed(ar, itm):
                    return itm

        new_value = ActivityItemClassValue(
            name=ar.activity_item_class_vo.name,
            order=ar.activity_item_class_vo.order,
            definition=ar.activity_item_class_vo.definition,
            nci_concept_id=ar.activity_item_class_vo.nci_concept_id,
            display_name=ar.activity_item_class_vo.display_name,
        )
        self._db_save_node(new_value)
        for (
            activity_instance_class_uid
        ) in ar.activity_item_class_vo.activity_instance_classes:
            activity_instance_class = ActivityInstanceClassRoot.nodes.get_or_none(
                uid=activity_instance_class_uid.uid
            )
            rel = root.has_activity_instance_class.relationship(activity_instance_class)
            if rel:
                rel.mandatory = activity_instance_class_uid.mandatory
                rel.is_adam_param_specific_enabled = (
                    activity_instance_class_uid.is_adam_param_specific_enabled
                )
                rel.is_additional_optional = (
                    activity_instance_class_uid.is_additional_optional
                )
                rel.is_default_linked = activity_instance_class_uid.is_default_linked
                rel.save()
            else:
                root.has_activity_instance_class.connect(
                    activity_instance_class,
                    {
                        "mandatory": activity_instance_class_uid.mandatory,
                        "is_adam_param_specific_enabled": activity_instance_class_uid.is_adam_param_specific_enabled,
                        "is_additional_optional": activity_instance_class_uid.is_additional_optional,
                        "is_default_linked": activity_instance_class_uid.is_default_linked,
                    },
                )

        data_type = CTTermRoot.nodes.get(uid=ar.activity_item_class_vo.data_type.uid)
        selected_term_node = (
            CTCodelistAttributesRepository().get_or_create_selected_term(
                data_type,
                codelist_submission_value=settings.stdm_odm_data_type_cl_submval,
            )
        )
        new_value.has_data_type.connect(selected_term_node)

        role = CTTermRoot.nodes.get(uid=ar.activity_item_class_vo.role.uid)
        selected_term_node = (
            CTCodelistAttributesRepository().get_or_create_selected_term(
                role, codelist_submission_value=settings.stdm_role_cl_submval
            )
        )
        new_value.has_role.connect(selected_term_node)

        return new_value

    def generate_uid(self) -> str:
        return ActivityItemClassRoot.get_next_free_uid_and_increment_counter()

    def _create_aggregate_root_instance_from_version_root_relationship_and_value(
        self,
        root: ActivityItemClassRoot,
        library: Library,
        relationship: VersionRelationship,
        value: ActivityItemClassValue,
        **_kwargs,
    ) -> ActivityItemClassAR:
        activity_instance_classes = root.has_activity_instance_class.all()

        role_term_context = value.has_role.get()
        term_root = role_term_context.has_selected_term.single()
        role = CTTermItem(
            uid=term_root.uid,
            name=term_root.has_name_root.single().has_version.single().name,
            codelist_uid=role_term_context.has_selected_codelist.single().uid,
        )

        data_type_term_context = value.has_data_type.get()
        term_root = data_type_term_context.has_selected_term.single()
        data_type = CTTermItem(
            uid=term_root.uid,
            name=term_root.has_name_root.single().has_version.single().name,
            codelist_uid=data_type_term_context.has_selected_codelist.single().uid,
        )

        variable_class_uids = [node.uid for node in root.maps_variable_class.all()]
        return ActivityItemClassAR.from_repository_values(
            uid=root.uid,
            activity_item_class_vo=ActivityItemClassVO.from_repository_values(
                name=value.name,
                definition=value.definition,
                nci_concept_id=value.nci_concept_id,
                order=value.order,
                display_name=value.display_name,
                activity_instance_classes=[
                    ActivityInstanceClassActivityItemClassRelVO(
                        uid=activity_instance_class.uid,
                        mandatory=activity_instance_class.has_activity_item_class.relationship(
                            root
                        ).mandatory,
                        is_adam_param_specific_enabled=activity_instance_class.has_activity_item_class.relationship(
                            root
                        ).is_adam_param_specific_enabled,
                        is_additional_optional=activity_instance_class.has_activity_item_class.relationship(
                            root
                        ).is_additional_optional,
                        is_default_linked=activity_instance_class.has_activity_item_class.relationship(
                            root
                        ).is_default_linked,
                    )
                    for activity_instance_class in activity_instance_classes
                ],
                role=role,
                data_type=data_type,
                variable_class_uids=variable_class_uids,
            ),
            library=LibraryVO.from_input_values_2(
                library_name=library.name,
                is_library_editable_callback=lambda _: library.is_editable,
            ),
            item_metadata=self._library_item_metadata_vo_from_relation(relationship),
        )

    @sb_clear_cache(caches=["cache_store_item_by_uid"])
    def patch_mappings(self, uid: str, variable_class_uids: list[str]) -> None:
        root = ActivityItemClassRoot.nodes.get(uid=uid)
        root.maps_variable_class.disconnect_all()
        for variable_class in variable_class_uids:
            variable_class = VariableClass.nodes.get(uid=variable_class)
            root.maps_variable_class.connect(variable_class)

    @sb_clear_cache(caches=["cache_store_item_by_uid"])
    def patch_valid_codelist_mappings(
        self, uid: str, valid_codelist_uids: list[str]
    ) -> None:
        root = ActivityItemClassRoot.nodes.get(uid=uid)
        root.has_valid_codelist_for_items.disconnect_all()
        for codelist_uid in valid_codelist_uids:
            codelist = CTCodelistRoot.nodes.get(uid=codelist_uid)
            root.has_valid_codelist_for_items.connect(codelist)

    def _maintain_parameters(
        self,
        versioned_object: ActivityItemClassAR,
        root: ActivityItemClassRoot,
        value: ActivityItemClassValue,
    ) -> None:
        # This method from parent repo is not needed for this repo
        # So we use pass to skip implementation
        pass

    def get_referenced_codelist_and_term_uids(
        self,
        activity_item_class_uid: str,
        dataset_uid: str,
        use_sponsor_model: bool,
        ct_catalogue_name: str | None = None,
    ) -> dict[str, list[str] | None]:
        if ct_catalogue_name:
            extra_filter_kwargs = {
                "maps_variable_class__has_instance__implemented_by_variable__references_codelist__has_codelist__name": ct_catalogue_name,
            }
        else:
            extra_filter_kwargs = {}
        uids_for_standard_model = (
            ActivityItemClassRoot.nodes.filter(
                uid=activity_item_class_uid,
                maps_variable_class__has_instance__implemented_by_variable__has_dataset_variable__is_instance_of__uid=dataset_uid,
                **extra_filter_kwargs,
            )
            .traverse(
                "maps_variable_class__has_instance__implemented_by_variable__references_codelist",
                Path(
                    value="maps_variable_class__has_instance__implemented_by_variable__references_term__has_selected_term",
                    optional=True,
                    include_rels_in_return=False,
                ),
            )
            .unique_variables(
                "maps_variable_class__has_instance__implemented_by_variable"
            )
            .intermediate_transform(
                {
                    "codelist_uid": {
                        "source": NodeNameResolver(
                            "maps_variable_class__has_instance__implemented_by_variable__references_codelist"
                        ),
                        "source_prop": "uid",
                        "include_in_return": True,
                    },
                    "term_uid": {
                        "source": NodeNameResolver(
                            "maps_variable_class__has_instance__implemented_by_variable__references_term__has_selected_term"
                        ),
                        "source_prop": "uid",
                        "include_in_return": True,
                    },
                },
                distinct=True,
            )
            .all()
        )
        codelist_term_sets: dict[str, set[str] | None] = {}
        for cl_uid, term_uid in uids_for_standard_model:
            if cl_uid not in codelist_term_sets:
                if term_uid is None:
                    codelist_term_sets[cl_uid] = None
                else:
                    codelist_term_sets[cl_uid] = {term_uid}
            elif term_uid is not None:
                if codelist_term_sets[cl_uid] is None:
                    codelist_term_sets[cl_uid] = set()
                codelist_term_sets[cl_uid].add(term_uid)

        if use_sponsor_model:
            if ct_catalogue_name:
                extra_sponsor_filter_kwargs = {
                    "maps_variable_class__has_instance__implemented_by_sponsor_variable__references_codelist__has_codelist__name": ct_catalogue_name,
                }
            else:
                extra_sponsor_filter_kwargs = {}
            uids_for_sponsor_model = (
                ActivityItemClassRoot.nodes.filter(
                    uid=activity_item_class_uid,
                    maps_variable_class__has_instance__implemented_by_sponsor_variable__has_variable__is_instance_of__uid=dataset_uid,
                    **extra_sponsor_filter_kwargs,
                )
                .traverse(
                    "maps_variable_class__has_instance__implemented_by_sponsor_variable__references_codelist",
                    Path(
                        value="maps_variable_class__has_instance__implemented_by_sponsor_variable__references_term__has_selected_term",
                        optional=True,
                        include_rels_in_return=False,
                    ),
                )
                .unique_variables(
                    "maps_variable_class__has_instance__implemented_by_sponsor_variable"
                )
                .intermediate_transform(
                    {
                        "codelist_uid": {
                            "source": NodeNameResolver(
                                "maps_variable_class__has_instance__implemented_by_sponsor_variable__references_codelist"
                            ),
                            "source_prop": "uid",
                            "include_in_return": True,
                        },
                        "term_uid": {
                            "source": NodeNameResolver(
                                "maps_variable_class__has_instance__implemented_by_sponsor_variable__references_term__has_selected_term"
                            ),
                            "source_prop": "uid",
                            "include_in_return": True,
                        },
                    },
                    distinct=True,
                )
                .all()
            )
            for cl_uid, term_uid in uids_for_sponsor_model:
                if cl_uid not in codelist_term_sets:
                    if term_uid is None:
                        codelist_term_sets[cl_uid] = None
                    else:
                        codelist_term_sets[cl_uid] = {term_uid}
                elif term_uid is not None:
                    if codelist_term_sets[cl_uid] is None:
                        codelist_term_sets[cl_uid] = set()
                    codelist_term_sets[cl_uid].add(term_uid)

        # Convert the sets to lists before returning
        codelists_and_terms: dict[str, list[str] | None] = {}
        for cl_uid, term_uids in codelist_term_sets.items():
            if isinstance(term_uids, set):
                codelists_and_terms[cl_uid] = list(term_uids)
            else:
                codelists_and_terms[cl_uid] = term_uids
        return codelists_and_terms

    def get_valid_codelists_and_terms(
        self, activity_item_class_uid: str, ct_catalogue_name: str | None
    ) -> dict[str, list[str] | None]:
        if ct_catalogue_name:
            extra_filter_kwargs = {
                "has_valid_codelist_for_items__has_codelist__name": ct_catalogue_name,
            }
        else:
            extra_filter_kwargs = {}
        codelist_uids = (
            ActivityItemClassRoot()
            .nodes.filter(
                uid=activity_item_class_uid,
                **extra_filter_kwargs,
            )
            .traverse(
                Path(
                    value="has_valid_codelist_for_items",
                    include_rels_in_return=False,
                    include_nodes_in_return=False,
                ),
            )
            .unique_variables("has_valid_codelist_for_items")
            .intermediate_transform(
                {
                    "codelist_uid": {
                        "source": NodeNameResolver("has_valid_codelist_for_items"),
                        "source_prop": "uid",
                        "include_in_return": True,
                    },
                },
                distinct=True,
            )
            .all()
        )
        # Return a dictionary of codelist uids and filtered terms
        # Note that for now, this model does not cater for filtering down to a subset of terms
        return {uid: None for uid in codelist_uids}

    def get_activity_instance_classes_using_item(
        self,
        activity_item_class_uid: str,
        version: str | None = None,
        page_number: int = 1,
        page_size: int = 10,
        total_count: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get paginated Activity Instance Classes that use this Activity Item Class."""
        if version:
            # Get version-specific Activity Instance Classes
            query = """
                // First get the activity item class at the specific version
                MATCH (aitc:ActivityItemClassRoot {uid: $uid})
                MATCH (aitc)-[av:HAS_VERSION {version: $version}]->(aitcValue:ActivityItemClassValue)

                // Get all Activity Instance Classes that have this item class
                MATCH (aic:ActivityInstanceClassRoot)-[rel:HAS_ITEM_CLASS]->(aitc)

                // For each instance class, get the version that was active at the item class's version date
                MATCH (aic)-[iv:HAS_VERSION]->(aicValue:ActivityInstanceClassValue)
                WHERE iv.start_date <= av.start_date AND (iv.end_date IS NULL OR iv.end_date > av.start_date)

                // Get the most recent valid version for each instance class
                WITH aic, aicValue, iv, rel
                ORDER BY
                    toInteger(split(iv.version, '.')[0]) DESC,
                    toInteger(split(iv.version, '.')[1]) DESC,
                    iv.start_date DESC
                WITH aic, collect({value: aicValue, ver: iv, rel: rel})[0] as instanceData

                WITH aic.uid as uid,
                       instanceData.value.name as name,
                       instanceData.rel.is_adam_param_specific_enabled as adam_param_specific_enabled,
                       instanceData.rel.is_additional_optional as is_additional_optional,
                       instanceData.rel.is_default_linked as is_default_linked,
                       instanceData.rel.mandatory as mandatory,
                       instanceData.ver.status as status,
                       instanceData.ver.version as version,
                       instanceData.ver.start_date as modified_date,
                       instanceData.ver.author_username as modified_by
                ORDER BY name
            """
        else:
            # Get latest version of Activity Instance Classes
            query = """
                MATCH (aitc:ActivityItemClassRoot {uid: $uid})
                MATCH (aic:ActivityInstanceClassRoot)-[rel:HAS_ITEM_CLASS]->(aitc)
                MATCH (aic)-[:LATEST]->(aicValue:ActivityInstanceClassValue)
                // Get version info from HAS_VERSION relationship
                OPTIONAL MATCH (aic)-[ver:HAS_VERSION]->(aicValue)
                WITH aic, aicValue, ver, rel
                ORDER BY ver.start_date DESC
                WITH aic, aicValue, COLLECT(ver)[0] as latest_ver, rel
                WITH aic.uid as uid,
                       aicValue.name as name,
                       rel.is_adam_param_specific_enabled as adam_param_specific_enabled,
                       rel.is_additional_optional as is_additional_optional,
                       rel.is_default_linked as is_default_linked,
                       rel.mandatory as mandatory,
                       latest_ver.status as status,
                       latest_ver.version as version,
                       latest_ver.start_date as modified_date,
                       latest_ver.author_username as modified_by
                ORDER BY name
            """

        # Get total count if requested
        if total_count:
            count_query = query + " RETURN COUNT(*) as total"
            count_params = {"uid": activity_item_class_uid}
            if version:
                count_params["version"] = version
            count_results, _ = db.cypher_query(count_query, params=count_params)
            total = count_results[0][0] if count_results else 0
        else:
            total = 0

        # Add pagination to main query
        if page_size > 0:
            query += "\n                SKIP $skip LIMIT $limit"

        # Build final query
        final_query = query + """
                RETURN uid, name, adam_param_specific_enabled, is_additional_optional, is_default_linked, mandatory,
                       status, version, modified_date, modified_by
            """

        params: dict[str, Any] = {"uid": activity_item_class_uid}
        if version:
            params["version"] = version
        if page_size > 0:
            params["skip"] = (page_number - 1) * page_size
            params["limit"] = page_size

        results, meta = db.cypher_query(final_query, params=params)

        return [dict(zip(meta, row)) for row in results], total
