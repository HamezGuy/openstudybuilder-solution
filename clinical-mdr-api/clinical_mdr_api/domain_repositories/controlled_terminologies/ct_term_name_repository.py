from typing import Any

from neomodel import db

from clinical_mdr_api.domain_repositories._generic_repository_interface import (
    _AggregateRootType,
)
from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_get_all_query_utils import (
    create_term_name_aggregate_instances_from_cypher_result,
)
from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_term_aggregated_repository import (
    CTTermAggregatedRepository,
)
from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_term_generic_repository import (
    CTTermGenericRepository,
)
from clinical_mdr_api.domain_repositories.models.controlled_terminology import (
    CTTermNameRoot,
    CTTermNameValue,
    CTTermRoot,
)
from clinical_mdr_api.domain_repositories.models.generic import (
    Library,
    VersionRelationship,
    VersionRoot,
    VersionValue,
)
from clinical_mdr_api.domain_repositories.models.template_parameter import (
    TemplateParameterTermRoot,
)
from clinical_mdr_api.domains.controlled_terminologies.ct_term_name import (
    CTTermNameAR,
    CTTermNameVO,
)
from clinical_mdr_api.domains.versioned_object_aggregate import (
    LibraryItemMetadataVO,
    LibraryVO,
)


class CTTermNameRepository(  # type: ignore[misc]
    CTTermGenericRepository[CTTermNameAR], CTTermAggregatedRepository
):
    root_class = CTTermNameRoot
    value_class = CTTermNameValue
    relationship_from_root = "has_name_root"

    def term_specific_exists_by_name_in_codelists(
        self, term_name: str, term_uid: str
    ) -> bool:
        """
        We allow duplicates in the following scenarios:
            - the conflicting term is retired
            - the conflicting term belongs to another codelist
        """
        query = """
            MATCH (term_root:CTTermRoot {uid: $term_uid})<-[:HAS_TERM_ROOT]-(:CTCodelistTerm)<-[ht:HAS_TERM WHERE ht.end_date IS NULL]-(clr:CTCodelistRoot)
            MATCH (clr)-[clht:HAS_TERM WHERE clht.end_date IS NULL]->(:CTCodelistTerm)-[:HAS_TERM_ROOT]->(cttr:CTTermRoot WHERE cttr.uid <> $term_uid)-[:HAS_NAME_ROOT]->(cttnr:CTTermNameRoot)-[hnv:HAS_VERSION WHERE hnv.end_date IS NULL AND hnv.status <> "Retired"]->(cttnv:CTTermNameValue {name: $term_name})
            RETURN cttnv
            """
        result, _ = db.cypher_query(
            query, {"term_name": term_name, "term_uid": term_uid}
        )

        return len(result) > 0

    def _create_aggregate_root_instance_from_cypher_result(
        self, term_dict: dict[str, Any]
    ) -> CTTermNameAR:
        return create_term_name_aggregate_instances_from_cypher_result(
            term_dict=term_dict, is_aggregated_query=False
        )

    def _create_ar(
        self,
        root: CTTermNameRoot,
        library: Library,
        relationship: VersionRelationship,
        value: CTTermNameValue,
        study_count: int = 0,
        **_kwargs,
    ) -> CTTermNameAR:
        ct_term_root_node = root.has_root.single()
        ct_codelist_term = ct_term_root_node.has_term_root.single()
        ct_codelist_root_node = (
            ct_codelist_term.has_term.single() if ct_codelist_term else None
        )

        catalogue_names = (
            [cat.name for cat in ct_codelist_root_node.has_codelist.all()]
            if ct_codelist_root_node
            else []
        )

        return CTTermNameAR.from_repository_values(
            uid=_kwargs["ctterm_names"]["ctterm_root_uid"],
            ct_term_name_vo=CTTermNameVO.from_repository_values(
                name=value.name,
                name_sentence_case=value.name_sentence_case,
                catalogue_names=catalogue_names,
                queried_effective_date=_kwargs["ctterm_names"][
                    "queried_effective_date"
                ],
                date_conflict=_kwargs["ctterm_names"]["date_conflict"],
            ),
            library=LibraryVO.from_input_values_2(
                library_name=library.name,
                is_library_editable_callback=lambda _: library.is_editable,
            ),
            item_metadata=self._library_item_metadata_vo_from_relation(relationship),
        )

    def _create_aggregate_root_instance_from_version_root_relationship_and_value(
        self,
        root: CTTermNameRoot,
        library: Library,
        relationship: VersionRelationship,
        value: CTTermNameValue,
        **_kwargs,
    ) -> CTTermNameAR:
        ct_term_root_node = root.has_root.single()
        ct_codelist_term = ct_term_root_node.has_term_root.single()

        ct_codelist_root_node = (
            ct_codelist_term.has_term.single() if ct_codelist_term else None
        )

        catalogue_names = (
            [cat.name for cat in ct_codelist_root_node.has_codelist.all()]
            if ct_codelist_root_node
            else []
        )

        return CTTermNameAR.from_repository_values(
            uid=ct_term_root_node.uid,
            ct_term_name_vo=CTTermNameVO.from_repository_values(
                name=value.name,
                name_sentence_case=value.name_sentence_case,
                catalogue_names=catalogue_names,
            ),
            library=LibraryVO.from_input_values_2(
                library_name=library.name,
                is_library_editable_callback=lambda _: library.is_editable,
            ),
            item_metadata=self._library_item_metadata_vo_from_relation(relationship),
        )

    def _is_new_version_necessary(self, ar: CTTermNameAR, value: VersionValue) -> bool:
        return self._has_data_changed(ar, value)

    def _get_or_create_value(
        self, root: CTTermNameRoot, ar: CTTermNameAR, force_new_value_node: bool = False
    ) -> CTTermNameValue:
        if not force_new_value_node:
            for itm in root.has_version.filter(
                name=ar.ct_term_vo.name,
                name_sentence_case=ar.ct_term_vo.name_sentence_case,
            ):
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

        new_value = self.value_class(
            name=ar.ct_term_vo.name, name_sentence_case=ar.ct_term_vo.name_sentence_case
        )
        self._db_save_node(new_value)
        return new_value

    def _has_data_changed(self, ar: CTTermNameAR, value: VersionValue):
        return (
            ar.ct_term_vo.name != value.name
            or ar.ct_term_vo.name_sentence_case != value.name_sentence_case
        )

    def _create(self, item: CTTermNameAR) -> CTTermNameAR:
        """
        Creates new CTTermNameAR, checks possibility based on library setting, then creates database representation,
        Creates CTTermNameRoot and CTTermNameValue database objects,
        recreates AR based on created database model and returns created AR.
        Saving into database is necessary due to uid creation process that require saving object to database.
        """
        relation_data: LibraryItemMetadataVO = item.item_metadata
        root = self.root_class()
        value = self.value_class(
            name=item.ct_term_vo.name,
            name_sentence_case=item.ct_term_vo.name_sentence_case,
        )
        self._db_save_node(root)

        (
            root,
            value,
            _,
            _,
            _,
        ) = self._db_create_and_link_nodes(
            root, value, self._library_item_metadata_vo_to_datadict(relation_data)
        )

        ct_term_root_node = CTTermRoot.nodes.get_or_none(uid=item.uid)
        ct_term_root_node.has_name_root.connect(root)
        self._maintain_parameters(item, root, value)

        return item

    def _maintain_parameters(
        self,
        versioned_object: _AggregateRootType,
        root: VersionRoot,
        value: VersionValue,
    ) -> None:
        """
        Method maintains TemplateParameterTermRoot and TemplateParameterTermValue labels when saving CTTermNameAR.
        :param versioned_object:
        :param root:
        :param value:
        :return None:
        """

        maintain_template_parameter_query = """
            MATCH (term_root:CTTermRoot {uid: $term_uid})-[:HAS_NAME_ROOT]->(term_ver_root)-[:LATEST]->(term_ver_value)
            MATCH (term_root)<-[:HAS_TERM_ROOT]-(codelist_term:CTCodelistTerm)<-[:HAS_TERM]-
              (codelist_root:CTCodelistRoot)-[:HAS_NAME_ROOT]->(:CTCodelistNameRoot)-[:LATEST]->(codelist_ver_value:TemplateParameter)
            MERGE (codelist_ver_value)-[hpt:HAS_PARAMETER_TERM]->(term_ver_root)
            SET term_ver_root:TemplateParameterTermRoot
            SET term_ver_value:TemplateParameterTermValue
        """
        db.cypher_query(
            maintain_template_parameter_query,
            {
                "term_uid": versioned_object.uid,
            },
        )
        TemplateParameterTermRoot.generate_node_uids_if_not_present()

    def is_repository_related_to_attributes(self) -> bool:
        """
        The method created to allow CTTermGenericRepository interface to handle filtering by package
        in different way for CTTermAttributesRepository and for CTTermNameRepository.
        :return:
        """
        return False
