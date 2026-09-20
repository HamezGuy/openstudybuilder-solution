import copy
from abc import ABC, abstractmethod
from typing import Any, Generic

from neomodel import db

from clinical_mdr_api.domain_repositories._generic_repository_interface import (
    _AggregateRootType,
)
from clinical_mdr_api.domain_repositories.library_item_repository import (
    LibraryItemRepositoryImplBase,
)
from clinical_mdr_api.domain_repositories.models._utils import (
    format_generic_header_values,
)
from clinical_mdr_api.domain_repositories.models.concepts import UnitDefinitionRoot
from clinical_mdr_api.domain_repositories.models.controlled_terminology import (
    CTTermRoot,
)
from clinical_mdr_api.domain_repositories.models.generic import (
    Library,
    VersionRelationship,
    VersionRoot,
    VersionValue,
)
from clinical_mdr_api.domain_repositories.models.odm import (
    OdmAlias,
    OdmFormalExpression,
    OdmFormRoot,
    OdmFormValue,
    OdmItemGroupRoot,
    OdmItemGroupValue,
    OdmItemRoot,
    OdmItemValue,
    OdmTranslatedText,
    OdmVendorAttributeRoot,
    OdmVendorAttributeValue,
    OdmVendorElementRoot,
    OdmVendorElementValue,
)
from clinical_mdr_api.domains.enums import OdmTranslatedTextTypeEnum
from clinical_mdr_api.domains.odms.utils import RelationType
from clinical_mdr_api.models.odms.common_models import (
    OdmAliasModel,
    OdmElementWithParentUid,
    OdmFormalExpressionModel,
    OdmTranslatedTextModel,
)
from clinical_mdr_api.models.utils import BaseModel
from clinical_mdr_api.repositories._utils import (
    CypherQueryBuilder,
    FilterDict,
    FilterOperator,
    calculate_total_count_from_query_result,
    sb_clear_cache,
    validate_filters_and_add_search_string,
)
from common.exceptions import BusinessLogicException


class OdmGenericRepository(
    LibraryItemRepositoryImplBase, Generic[_AggregateRootType], ABC
):
    root_class = type
    value_class = type
    return_model: type = BaseModel
    filter_query_parameters: dict[Any, Any] = {}
    sort_by: dict[Any, Any] | None = None

    @abstractmethod
    def _create_aggregate_root_instance_from_cypher_result(
        self, input_dict: dict[str, Any]
    ) -> _AggregateRootType:
        raise NotImplementedError

    @abstractmethod
    def _create_aggregate_root_instance_from_version_root_relationship_and_value(
        self,
        root: VersionRoot,
        library: Library,
        relationship: VersionRelationship,
        value: VersionValue,
        **_kwargs,
    ) -> _AggregateRootType:
        raise NotImplementedError

    @abstractmethod
    def specific_alias_clause(self, **kwargs) -> str:
        """
        Methods is overridden in the OdmGenericRepository subclasses
        and it contains matches and traversals specific for domain object represented by subclass repository.
        :return str:
        """

    def specific_header_match_clause(self) -> str | None:
        return None

    # pylint: disable=unused-argument
    def specific_header_match_clause_lite(self, field_name: str) -> str | None:
        return None

    def _create_new_value_node(self, ar: _AggregateRootType) -> VersionValue:
        return self.value_class(  # type: ignore[call-overload]
            name=ar.name,
        )

    def _has_data_changed(self, ar: _AggregateRootType, value: VersionValue) -> bool:
        return ar.name != value.name

    def generate_uid(self) -> str:
        return self.root_class.get_next_free_uid_and_increment_counter()

    def generic_match_clause(self, **kwargs):
        odm_label = self.root_class.__label__
        odm_value_label = self.value_class.__label__

        version = kwargs.get("version", None)
        rel = (
            "hv:HAS_VERSION WHERE hv.version = $requested_version"
            if version is not None
            else ":LATEST"
        )

        return f"""CYPHER runtime=slotted MATCH (odm_root:{odm_label})-[{rel}]->(odm_value:{odm_value_label})"""

    def generic_alias_clause(self, **kwargs):
        version = kwargs.get("version", None)
        where_version = (
            "WHERE hv.version = $requested_version" if version is not None else ""
        )

        return f"""
            DISTINCT odm_root, odm_value,
            head([(library)-[:CONTAINS_ODM]->(odm_root) | library]) AS library
            CALL {{
                WITH odm_root, odm_value
                MATCH (odm_root)-[hv:HAS_VERSION]-(odm_value)
                {where_version}
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.end_date ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS version_rel
            }}
            WITH
                odm_root,
                odm_root.uid AS uid,
                odm_value as odm_value,
                library,
                version_rel
                CALL {{
                    WITH version_rel
                    OPTIONAL MATCH (author: User)
                    WHERE author.user_id = version_rel.author_id
                    RETURN author
                }}
            WITH
                uid,
                odm_root,
                odm_value.name AS name,
                library,
                library.name AS library_name,
                library.is_editable AS is_library_editable,
                version_rel.start_date AS start_date,
                version_rel.end_date AS end_date,
                version_rel.status AS status,
                version_rel.version AS version,
                version_rel.change_description AS change_description,
                version_rel.author_id AS author_id,
                coalesce(author.username, version_rel.author_id) AS author_username,
                odm_value
        """

    def create_query_filter_statement(
        self, library: str | None = None, **kwargs
    ) -> tuple[str, dict[Any, Any]]:
        filter_parameters = []
        filter_query_parameters = {}
        uids = kwargs.get("uids")
        if library:
            filter_by_library_name = """
            head([(library:Library)-[:CONTAINS_ODM]->(odm_root) | library.name])=$library_name"""
            filter_parameters.append(filter_by_library_name)
            filter_query_parameters["library_name"] = library
        if uids:
            filter_by_uids = "odm_root.uid IN $uids"
            filter_parameters.append(filter_by_uids)
            filter_query_parameters["uids"] = uids

        filter_statements = " AND ".join(filter_parameters)
        filter_statements = (
            "WHERE " + filter_statements if len(filter_statements) > 0 else ""
        )
        return filter_statements, filter_query_parameters

    @classmethod
    # pylint: disable=unused-argument
    def format_filter_sort_keys(cls, key: str):
        return key

    @classmethod
    def format_filter_sort_keys_for_headers_lite(cls, key: str):
        return key.replace(".", "_")

    def find_all(
        self,
        library: str | None = None,
        sort_by: dict[str, bool] | None = None,
        page_number: int = 1,
        page_size: int = 0,
        filter_by: dict[str, dict[str, Any]] | None = None,
        filter_operator: FilterOperator = FilterOperator.AND,
        total_count: bool = False,
        return_all_versions: bool = False,
        **kwargs,
    ) -> tuple[list[_AggregateRootType], int]:
        match_clause = self.generic_match_clause(**kwargs)

        filter_statements, filter_query_parameters = self.create_query_filter_statement(
            library=library, **kwargs
        )
        match_clause += filter_statements

        alias_clause = (
            self.generic_alias_clause(**kwargs) + self.specific_alias_clause()
        )

        query = CypherQueryBuilder(
            match_clause=match_clause,
            alias_clause=alias_clause,
            sort_by=sort_by,
            page_number=page_number,
            page_size=page_size,
            filter_by=FilterDict.model_validate({"elements": filter_by}),
            filter_operator=filter_operator,
            total_count=total_count,
            return_model=self.return_model,
        )

        if kwargs.get("version", None) is not None:
            query.parameters.update({"requested_version": kwargs["version"]})

        query.parameters.update(filter_query_parameters)
        result_array, attributes_names = query.execute()
        extracted_items = self._retrieve_odm_items_from_cypher_res(
            result_array, attributes_names
        )
        total_amount = calculate_total_count_from_query_result(
            len(extracted_items), page_number, page_size, total_count
        )
        if total_amount is None:
            count_result, _ = db.cypher_query(
                query=query.count_query, params=query.parameters
            )
            total_amount = count_result[0][0] if len(count_result) > 0 else 0

        return extracted_items, total_amount

    def _retrieve_odm_items_from_cypher_res(
        self, result_array, attribute_names
    ) -> list[_AggregateRootType]:
        odm_ars = []
        for item in result_array:
            item_dictionary = {}
            for item_property, attribute_name in zip(item, attribute_names):
                item_dictionary[attribute_name] = item_property
            odm_ars.append(
                self._create_aggregate_root_instance_from_cypher_result(item_dictionary)
            )

        return odm_ars

    def get_distinct_headers(
        self,
        field_name: str,
        search_string: str = "",
        library: str | None = None,
        filter_by: dict[str, dict[str, Any]] | None = None,
        filter_operator: FilterOperator = FilterOperator.AND,
        page_size: int = 10,
        **kwargs,
    ) -> list[Any]:
        # pylint: disable=unused-argument
        filter_by = validate_filters_and_add_search_string(
            search_string, field_name, filter_by
        )
        match_clause = self.generic_match_clause(**kwargs)
        if self.specific_header_match_clause():
            match_clause += self.specific_header_match_clause()

        filter_statements, filter_query_parameters = self.create_query_filter_statement(
            library=library, **kwargs
        )
        match_clause += filter_statements

        alias_clause = (
            self.generic_alias_clause(**kwargs) + self.specific_alias_clause()
        )

        query = CypherQueryBuilder(
            filter_by=FilterDict.model_validate({"elements": filter_by}),
            filter_operator=filter_operator,
            match_clause=match_clause,
            alias_clause=alias_clause,
            return_model=self.return_model,
            format_filter_sort_keys=self.format_filter_sort_keys,
        )

        query.parameters.update(filter_query_parameters)

        query.full_query = query.build_header_query(
            header_alias=field_name, page_size=page_size
        )
        result_array, _ = query.execute()

        return (
            format_generic_header_values(result_array[0][0])
            if len(result_array) > 0
            else []
        )

    def get_distinct_headers_lite(
        self,
        field_name: str,
        search_string: str = "",
        library: str | None = None,
        page_size: int = 10,
        filter_by: dict[str, Any] | None = None,
        filter_operator: FilterOperator = FilterOperator.AND,
        **kwargs,
    ) -> list[Any]:
        match_clause = self.generic_match_clause()

        filter_by = validate_filters_and_add_search_string(
            search_string, field_name, filter_by=filter_by
        )

        filter_statements, filter_query_parameters = self.create_query_filter_statement(
            library=library, kwargs=kwargs
        )

        match_clause += filter_statements

        if field_name in [
            "comment",
            "datatype",
            "length",
            "name",
            "oid",
            "origin",
            "prompt",
            "sas_field_name",
            "sds_var_name",
            "significant_digits",
        ]:
            match_clause += f"""
                WITH odm_value.{field_name} AS {field_name}
            """

        elif field_name in ["version", "start_date", "status"]:
            match_clause += """
                CALL {
                    WITH odm_root, odm_value
                    MATCH (odm_root)-[hv:HAS_VERSION]-(odm_value)
                    WITH hv
                    ORDER BY
                        toInteger(split(hv.version, '.')[0]) ASC,
                        toInteger(split(hv.version, '.')[1]) ASC,
                        hv.start_date ASC
                    WITH collect(hv) as hvs
                    RETURN last(hvs) AS version_rel
                }
                WITH version_rel.version AS version,
                     version_rel.start_date AS start_date,
                     version_rel.status AS status
            """

        elif field_name == "library_name":
            match_clause += """
                WITH DISTINCT odm_root,
                    head([(library)-[:CONTAINS_ODM]->(odm_root) | library]) AS library
                WITH library.name AS library_name
            """

        elif field_name == "author_username":
            match_clause += """
                CALL {
                    WITH odm_root, odm_value
                    MATCH (odm_root)-[hv:HAS_VERSION]-(odm_value)
                    WITH hv
                    ORDER BY
                        toInteger(split(hv.version, '.')[0]) ASC,
                        toInteger(split(hv.version, '.')[1]) ASC,
                        hv.start_date ASC
                    WITH collect(hv) as hvs
                    RETURN last(hvs) AS version_rel
                }
                CALL {
                    WITH version_rel
                    OPTIONAL MATCH (author: User)
                    WHERE author.user_id = version_rel.author_id
                    RETURN author
                }
                WITH author.username AS author_username
            """

        else:
            if self.specific_header_match_clause_lite(field_name):
                match_clause += self.specific_header_match_clause_lite(field_name)

        query = CypherQueryBuilder(
            filter_by=FilterDict.model_validate({"elements": filter_by}),
            match_clause=match_clause,
            alias_clause=field_name.replace(".", "_"),
            return_model=self.return_model,
            format_filter_sort_keys=self.format_filter_sort_keys_for_headers_lite,
        )

        query.parameters.update(filter_query_parameters)

        query.full_query = query.build_header_query(
            header_alias=field_name.replace(".", "_"), page_size=page_size
        )
        result_array, _ = query.execute()

        return (
            format_generic_header_values(result_array[0][0])
            if len(result_array) > 0
            else []
        )

    @sb_clear_cache(caches=["cache_store_item_by_uid"])
    def save(
        self, item: _AggregateRootType, force_new_value_node: bool = False
    ) -> None:
        if item.uid is not None and item.repository_closure_data is None:
            self._create(item)
        elif item.uid is not None and not item.is_deleted:
            self._update(item, force_new_value_node)
        elif item.is_deleted:
            assert item.uid is not None
            self._soft_delete(item.uid)
        if item.repository_closure_data is not None:
            (
                root,
                _,
                library,
                _,
            ) = item.repository_closure_data
            value = root.has_latest_value.single()

            item.repository_closure_data = (
                root,
                value,
                library,
                copy.deepcopy(item),
            )

    def _soft_delete(self, uid: str) -> None:
        label = self.root_class.__label__
        db.cypher_query(
            f"""
            MATCH (otr:{label} {{uid: $uid}})-[latest_draft:LATEST_DRAFT|LATEST_RETIRED]->(otv)
            WHERE NOT (otr)-[:HAS_VERSION {{version:'Final'}}]->()
            SET otr:Deleted{label}
            WITH otr
            REMOVE otr:{label}
            WITH otr
            MATCH (otr)-[v:HAS_VERSION]->()
            WHERE v.end_date IS NULL
            SET v.end_date = datetime(apoc.date.toISO8601(datetime().epochSeconds, 's'))
            """,
            {"uid": uid},
        )

    def _is_new_version_necessary(
        self, ar: _AggregateRootType, value: VersionValue
    ) -> bool:
        return self._has_data_changed(ar, value)

    def _get_or_create_value(
        self,
        root: VersionRoot,
        ar: _AggregateRootType,
        force_new_value_node: bool = False,
    ) -> VersionValue:
        if not force_new_value_node:
            for itm in root.has_version.all():
                if not self._has_data_changed(ar, itm):
                    return itm
            latest_draft = root.latest_draft.get_or_none()
            if latest_draft and not self._has_data_changed(ar, latest_draft):
                return latest_draft
            latest_final = root.latest_final.get_or_none()
            if latest_final and not self._has_data_changed(ar, latest_final):
                return latest_final
            latest_retired = root.latest_retired.get_or_none()
            if latest_retired and not self._has_data_changed(ar, latest_retired):
                return latest_retired
        new_value = self._create_new_value_node(ar=ar)
        self._db_save_node(new_value)
        return new_value

    def _maintain_parameters(
        self,
        versioned_object: _AggregateRootType,
        root: VersionRoot,
        value: VersionValue,
    ) -> None:
        # ODM items do not use template parameters - no-op
        pass

    def _update_versioning_relationship_query(
        self, status: str, merge_query: str | None = None
    ) -> str:
        query = f"""
            MATCH (library:Library)-[:CONTAINS_ODM]->(odm_root)-[latest_has_version:HAS_VERSION]->(odm_value)
            WHERE latest_has_version.end_date is null
            MATCH (odm_root)-[latest_status_relationship:LATEST_{status.upper()}]->(:{self.value_class.__label__})
            WITH *
            """
        if merge_query:
            query += merge_query
        else:
            query += f"""
            CREATE (odm_root)-[:LATEST]->(odm_value)
            CREATE (odm_root)-[:LATEST_{status.upper()}]->(odm_value)
            CREATE (odm_root)-[new_has_version:HAS_VERSION]->(odm_value)
            """
        query += """
            SET new_has_version.start_date = $start_date
            SET new_has_version.end_date = null
            SET new_has_version.change_description = $change_description
            SET new_has_version.version = $new_version
            SET new_has_version.status = $new_status
            SET new_has_version.author_id = $author_id
            SET latest_has_version.end_date = $start_date
            WITH *
            DELETE status_relationship, latest_status_relationship
        """
        return query

    # ODM-specific methods ported from current OdmGenericRepository

    @classmethod
    def _get_origin_and_relation_node(
        cls,
        uid: str,
        relation_uid: str | None,
        relationship_type: RelationType,
        zero_or_one_relation: bool = False,
    ):
        def _has_relationship_to_others() -> bool:
            root = ""
            value = ""
            if relation_node.__label__.endswith("Root"):
                root = f":{relation_node.__label__}"
            else:
                value = f":{relation_node.__label__}"

            query = f"""
                MATCH (source:OdmRoot{root})-[:LATEST]->(:OdmValue{value})
                -[:{origin_label.upper()}]-(:{cls.value_class.__label__})<-[:HAS_VERSION]-(target:{cls.root_class.__label__})
                WHERE source.uid = $source_uid AND target.uid <> $target_uid
                RETURN COUNT(*) > 0
            """
            result, _ = db.cypher_query(
                query=query,
                params={
                    "source_uid": relation_uid,
                    "target_uid": uid,
                },
            )
            BusinessLogicException.raise_if(
                not isinstance(result, list)
                or len(result) != 1
                or not isinstance(result[0], list)
                or len(result[0]) != 1
                or type(result[0][0]) is not bool,
                msg="ODM_RELATIONSHIP_OWNERSHIP_QUERY_UNPROVEN",
            )
            return result[0][0]

        cls.lock_for_relationship_update(uid)
        root_class_node = cls.root_class.nodes.get_or_none(uid=uid)
        value_class_node = root_class_node.has_latest_value.single()

        relation_mapping = {
            RelationType.ITEM_GROUP: (
                OdmItemGroupRoot,
                OdmItemGroupValue,
                "item_group_ref",
            ),
            RelationType.ITEM: (OdmItemRoot, OdmItemValue, "item_ref"),
            RelationType.FORM: (OdmFormRoot, OdmFormValue, "form_ref"),
            RelationType.TERM: (CTTermRoot, None, "has_codelist_term"),
            RelationType.UNIT_DEFINITION: (
                UnitDefinitionRoot,
                None,
                "has_unit_definition",
            ),
            RelationType.VENDOR_ELEMENT: (
                OdmVendorElementRoot,
                OdmVendorElementValue,
                "has_vendor_element",
            ),
            RelationType.VENDOR_ATTRIBUTE: (
                OdmVendorAttributeRoot,
                OdmVendorAttributeValue,
                "has_vendor_attribute",
            ),
            RelationType.VENDOR_ELEMENT_ATTRIBUTE: (
                OdmVendorAttributeRoot,
                OdmVendorAttributeValue,
                "has_vendor_element_attribute",
            ),
        }

        BusinessLogicException.raise_if(
            relationship_type not in relation_mapping, msg="Invalid relation type."
        )

        relation_node_root_cls, relation_node_value_cls, origin_label = (
            relation_mapping[relationship_type]
        )
        relation_node = relation_node_root_cls.nodes.get_or_none(uid=relation_uid)

        if relation_node is not None and relation_node_value_cls is not None:
            relation_node = relation_node.has_latest_value.single()

        if not relation_node and relation_uid:
            raise BusinessLogicException(
                msg=f"Object with UID '{relation_uid}' doesn't exist.",
            )

        if relation_uid and zero_or_one_relation and _has_relationship_to_others():
            raise BusinessLogicException(
                msg=f"{relation_node.__label__.removesuffix('Value').removesuffix('Root')} with UID '{relation_uid}'"
                f" is already connected to another {root_class_node.__label__.removesuffix('Root')}."
            )

        return getattr(value_class_node, origin_label), relation_node

    @classmethod
    def validate_capture_item_custody(cls, uid: str, item_uid: str) -> None:
        """Apply the ordinary item writer's ownership validation without a write."""
        BusinessLogicException.raise_if(
            cls.root_class is not OdmItemGroupRoot,
            msg="Unsupported capture item owner.",
        )
        cls._get_origin_and_relation_node(
            uid=uid, relation_uid=item_uid, relationship_type=RelationType.ITEM,
            zero_or_one_relation=True,
        )

    @classmethod
    def lock_for_relationship_update(cls, uid: str) -> None:
        """Serialize native definition/collection checks with relation writes."""
        db.cypher_query(
            f"""
            MATCH (root:{cls.root_class.__label__} {{uid: $uid}})
            CALL apoc.lock.nodes([root])
            RETURN root.uid
            """,
            {"uid": uid},
        )

    @classmethod
    def read_capture_collection(cls, uid: str, collection: str) -> list[dict[str, Any]]:
        """Read every current edge and property, including unmodelled properties.

        The caller owns the transaction and root locks. Do not use the ODM DTO
        projection here: it can omit unknown properties or unresolvable targets.
        """
        kinds = {
            (OdmFormRoot, "item_groups"): ("ITEM_GROUP_REF", OdmItemGroupRoot),
            (OdmItemGroupRoot, "items"): ("ITEM_REF", OdmItemRoot),
        }
        BusinessLogicException.raise_if(
            (cls.root_class, collection) not in kinds,
            msg="Unsupported capture collection.",
        )
        relation_type, child_root = kinds[(cls.root_class, collection)]
        rows, _ = db.cypher_query(
            f"""
            MATCH (root:{cls.root_class.__label__} {{uid: $uid}})
                  -[:LATEST]->(value)-[relation:{relation_type}]->(child)
            OPTIONAL MATCH (child)<-[:HAS_VERSION]-(owner:{child_root.__label__})
            WITH relation, child, collect(DISTINCT owner.uid) AS owner_uids
            RETURN properties(relation) AS properties, owner_uids,
                   [(current:{child_root.__label__})-[:LATEST]->(child) |
                    current.uid] AS current_owner_uids
            """,
            {"uid": uid},
        )
        return [
            {"properties": row[0], "owner_uids": row[1], "current_owner_uids": row[2]}
            for row in rows
        ]

    @sb_clear_cache(caches=["cache_store_item_by_uid"])
    def add_relation(
        self,
        uid: str,
        relation_uid: str,
        relationship_type: RelationType,
        parameters: dict[str, Any] | None = None,
        zero_or_one_relation: bool = False,
    ) -> None:
        origin, relation_node = self.__class__._get_origin_and_relation_node(
            uid,
            relation_uid,
            relationship_type,
            zero_or_one_relation=zero_or_one_relation,
        )

        origin.disconnect(relation_node)

        if isinstance(parameters, dict):
            origin.connect(relation_node, parameters)
        else:
            origin.connect(relation_node)

    @sb_clear_cache(caches=["cache_store_item_by_uid"])
    def remove_relation(
        self,
        uid: str,
        relation_uid: str | None,
        relationship_type: RelationType,
        disconnect_all: bool = False,
    ) -> None:
        origin, relation_node = self.__class__._get_origin_and_relation_node(
            uid, relation_uid, relationship_type
        )

        if disconnect_all:
            origin.disconnect_all()
        else:
            origin.disconnect(relation_node)

    def has_active_relationships(
        self, uid: str, relationships: list[Any], all_exist: bool = False
    ) -> bool:
        root = self.root_class.nodes.get_or_none(uid=uid)
        value = root.has_latest_value.single()

        try:
            if not all_exist:
                for relationship in relationships:
                    if getattr(value, relationship):
                        return True

                return False
            for relationship in relationships:
                if not getattr(value, relationship):
                    return False

            return True
        except AttributeError as exc:
            raise AttributeError(
                f"{relationship} relationship was not found on {value}."
            ) from exc

    def get_active_relationships(
        self, uid: str, relationships: list[Any]
    ) -> dict[str, list[str]]:
        root_node = self.root_class.nodes.get_or_none(uid=uid)
        source_node = root_node.has_latest_value.single()

        try:
            rs: dict[str, list[str]] = {}
            for relationship in relationships:
                rel = getattr(source_node, relationship)
                if rel:
                    for target_node in rel.all():
                        target_node_without_suffix = target_node.__label__.removesuffix(
                            "Value"
                        )

                        target_node_roots = target_node.has_root.all()
                        if not target_node_roots:
                            continue

                        if target_node_without_suffix not in rs:
                            rs[target_node_without_suffix] = [target_node_roots[0].uid]
                        else:
                            rs[target_node_without_suffix].append(
                                target_node_roots[0].uid
                            )
            return rs
        except AttributeError as exc:
            raise AttributeError(
                f"{relationship} relationship was not found on {source_node}."
            ) from exc

    def get_if_has_relationship(self, relationship: str):
        values = self.value_class.nodes.has(**{relationship: True})

        rs = []
        for value in values:
            parents = getattr(value, relationship).all()

            rs.append(
                OdmElementWithParentUid(
                    uid=value.has_root.single().uid,
                    name=value.name,
                    parent_uids=[parent.has_root.single().uid for parent in parents],
                )
            )

        return rs

    def odm_object_exists(
        self,
        library_name: str | None = None,
        sdtm_domain_uids: list[str] | None = None,
        term_uids: list[str] | None = None,
        unit_definition_uids: list[str] | None = None,
        codelist_uid: str | None = None,
        **value_attributes,
    ):
        if not sdtm_domain_uids:
            sdtm_domain_uids = []
        if not term_uids:
            term_uids = []
        if not unit_definition_uids:
            unit_definition_uids = []

        query = f"""
            MATCH (root:{self.root_class.__label__})-[:LATEST]->(value:{self.value_class.__label__})
        """

        params: dict[str, Any] = {}

        if library_name:
            query += " MATCH (:Library {name: $library_name})-[:CONTAINS_ODM]->(root) "
            params["library_name"] = library_name

        if sdtm_domain_uids:
            query += " MATCH (ct_term_root:CTTermRoot)<-[:HAS_SELECTED_TERM]-(:CTTermContext)<-[:HAS_SDTM_DOMAIN]-(value) "
            params["sdtm_domain_uids"] = sdtm_domain_uids

        if codelist_uid:
            query += " MATCH (:CTCodelistRoot {uid: $codelist_uid})<-[:HAS_CODELIST]-(value) "
            params["codelist_uid"] = codelist_uid

        if term_uids:
            params["term_uids"] = term_uids
            query += " MATCH (ct_term_root:CTTermRoot)<-[:HAS_SELECTED_TERM]-(:CTTermContext)<-[:HAS_CODELIST_TERM]-(value) "

        if unit_definition_uids:
            params["unit_definition_uids"] = unit_definition_uids
            query += " MATCH (unit_definition_root:UnitDefinitionRoot)<-[:HAS_UNIT_DEFINITION]-(value) "

        wheres = []
        for key, value in value_attributes.items():
            if value is not None:
                wheres.append(f"value.{key} = ${key}")

                params[key] = value
            else:
                wheres.append(f"value.{key} IS NULL")

        query += " WHERE " + " AND ".join(wheres)

        _where = []
        # pylint: disable=too-many-boolean-expressions
        if sdtm_domain_uids or term_uids or unit_definition_uids:
            query += " WITH root"

            if sdtm_domain_uids:
                query += ", apoc.coll.sort(COLLECT(DISTINCT ct_term_root.uid)) AS sdtm_domain_uids"
                _where.append("sdtm_domain_uids = apoc.coll.sort($sdtm_domain_uids)")

            if term_uids:
                query += (
                    ", apoc.coll.sort(COLLECT(DISTINCT ct_term_root.uid)) AS term_uids"
                )
                _where.append("term_uids = apoc.coll.sort($term_uids)")

            if unit_definition_uids:
                query += ", apoc.coll.sort(COLLECT(DISTINCT unit_definition_root.uid)) AS unit_definition_uids"
                _where.append(
                    "unit_definition_uids = apoc.coll.sort($unit_definition_uids)"
                )

        if _where:
            query += " WHERE " + " AND ".join(_where)

        query += " RETURN root.uid"

        rs = db.cypher_query(query=query, params=params)

        if rs[0]:
            return rs[0][0]

        return None

    def connect_translated_texts(
        self, translated_texts: list[OdmTranslatedTextModel], new_value: VersionValue
    ):
        new_value.has_translated_text.disconnect_all()

        for translated_text in translated_texts:
            params: dict[str, str | OdmTranslatedTextTypeEnum] = {
                "text": translated_text.text,
                "language": translated_text.language,
                "text_type": translated_text.text_type.value,
            }

            translated_text_node = OdmTranslatedText.nodes.get_or_none(**params)
            if not translated_text_node:
                translated_text_node = OdmTranslatedText(**params)
                translated_text_node.save()
            new_value.has_translated_text.connect(translated_text_node)

    def connect_aliases(self, aliases: list[OdmAliasModel], new_value: VersionValue):
        new_value.has_alias.disconnect_all()

        for alias in aliases:
            alias_node = OdmAlias.nodes.get_or_none(
                name=alias.name, context=alias.context
            )
            if not alias_node:
                alias_node = OdmAlias(name=alias.name, context=alias.context)
                alias_node.save()
            new_value.has_alias.connect(alias_node)

    def connect_formal_expressions(
        self,
        formal_expressions: list[OdmFormalExpressionModel],
        new_value: VersionValue,
    ):
        new_value.has_formal_expression.disconnect_all()

        for formal_expression in formal_expressions:
            formal_expression_node = OdmFormalExpression.nodes.get_or_none(
                context=formal_expression.context,
                expression=formal_expression.expression,
            )
            if not formal_expression_node:
                formal_expression_node = OdmFormalExpression(
                    context=formal_expression.context,
                    expression=formal_expression.expression,
                )
                formal_expression_node.save()
            new_value.has_formal_expression.connect(formal_expression_node)

    def manage_vendor_relationships(
        self,
        current_latest: VersionValue,
        new_value: VersionValue,
        disconnect_old: bool,
    ) -> None:
        old_has_vendor_element_nodes = (
            current_latest.has_vendor_element.all() if current_latest else []
        )
        new_has_vendor_element_nodes = [
            old_vendor_element_root.has_latest_value.single()
            for old_has_vendor_element_node in old_has_vendor_element_nodes
            if (
                old_vendor_element_root := old_has_vendor_element_node.has_root.single()
            )
        ]

        old_has_vendor_attribute_nodes = (
            current_latest.has_vendor_attribute.all() if current_latest else []
        )
        new_has_vendor_attribute_nodes = [
            old_vendor_attribute_root.has_latest_value.single()
            for old_has_vendor_attribute_node in old_has_vendor_attribute_nodes
            if (
                old_vendor_attribute_root := old_has_vendor_attribute_node.has_root.single()
            )
        ]

        old_has_vendor_element_attribute_nodes = (
            current_latest.has_vendor_element_attribute.all() if current_latest else []
        )
        new_has_vendor_element_attribute_nodes = [
            old_vendor_element_attribute_root.has_latest_value.single()
            for old_has_vendor_element_attribute_node in old_has_vendor_element_attribute_nodes
            if (
                old_vendor_element_attribute_root := old_has_vendor_element_attribute_node.has_root.single()
            )
        ]

        for old_has_vendor_element_node, new_has_vendor_element_node in zip(
            old_has_vendor_element_nodes, new_has_vendor_element_nodes
        ):
            params = current_latest.has_vendor_element.relationship(
                old_has_vendor_element_node
            )
            new_value.has_vendor_element.connect(
                new_has_vendor_element_node,
                {"value": params.value},
            )

        for old_has_vendor_attribute_node, new_has_vendor_attribute_node in zip(
            old_has_vendor_attribute_nodes, new_has_vendor_attribute_nodes
        ):
            params = current_latest.has_vendor_attribute.relationship(
                old_has_vendor_attribute_node
            )
            new_value.has_vendor_attribute.connect(
                new_has_vendor_attribute_node,
                {"value": params.value},
            )

        for (
            old_has_vendor_element_attribute_node,
            new_has_vendor_element_attribute_node,
        ) in zip(
            old_has_vendor_element_attribute_nodes,
            new_has_vendor_element_attribute_nodes,
        ):
            params = current_latest.has_vendor_element_attribute.relationship(
                old_has_vendor_element_attribute_node
            )
            new_value.has_vendor_element_attribute.connect(
                new_has_vendor_element_attribute_node,
                {"value": params.value},
            )

        if disconnect_old:
            for old_has_vendor_element_node in old_has_vendor_element_nodes:
                current_latest.has_vendor_element.disconnect(
                    old_has_vendor_element_node
                )
            for old_has_vendor_attribute_node in old_has_vendor_attribute_nodes:
                current_latest.has_vendor_attribute.disconnect(
                    old_has_vendor_attribute_node
                )
            for (
                old_has_vendor_element_attribute_node
            ) in old_has_vendor_element_attribute_nodes:
                current_latest.has_vendor_element_attribute.disconnect(
                    old_has_vendor_element_attribute_node
                )
