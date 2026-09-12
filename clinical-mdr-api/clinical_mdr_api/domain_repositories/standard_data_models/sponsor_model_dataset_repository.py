from typing import Any

from neomodel import NodeSet, db
from neomodel.sync_.match import Collect, NodeNameResolver, Path, RelationNameResolver

from clinical_mdr_api.domain_repositories.library_item_repository import (
    LibraryItemRepositoryImplBase,
)
from clinical_mdr_api.domain_repositories.models.generic import (
    Library,
    VersionRelationship,
)
from clinical_mdr_api.domain_repositories.models.standard_data_model import (
    DataModelCatalogue,
    Dataset,
    DatasetClass,
    DatasetVariable,
    SponsorModelDatasetInstance,
    SponsorModelValue,
)
from clinical_mdr_api.domain_repositories.neomodel_ext_item_repository import (
    NeomodelExtBaseRepository,
)
from clinical_mdr_api.domain_repositories.standard_data_models.utils import (
    get_sponsor_model_info_from_dataset,
)
from clinical_mdr_api.domains.standard_data_models.sponsor_model_dataset import (
    SponsorModelDatasetAR,
    SponsorModelDatasetVO,
)
from clinical_mdr_api.domains.versioned_object_aggregate import LibraryVO
from clinical_mdr_api.models.standard_data_models.sponsor_model_dataset import (
    SponsorModelDataset,
)
from clinical_mdr_api.repositories._utils import FilterOperator
from common.exceptions import BusinessLogicException


class SponsorModelDatasetRepository(  # type: ignore[misc]
    NeomodelExtBaseRepository, LibraryItemRepositoryImplBase[SponsorModelDatasetAR]
):
    root_class = Dataset
    value_class = SponsorModelDatasetInstance
    return_model = SponsorModelDataset

    def get_neomodel_extension_query(self) -> NodeSet:
        return (
            Dataset.nodes.traverse(
                "has_sponsor_model_instance__has_dataset",
                Path(
                    value="has_sponsor_model_instance__has_key",
                    optional=True,
                    include_rels_in_return=False,
                ),
                Path(
                    value="has_sponsor_model_instance__has_sort_key",
                    optional=True,
                    include_rels_in_return=False,
                ),
            )
            .unique_variables("has_sponsor_model_instance")
            .annotate(
                Collect(
                    NodeNameResolver("has_sponsor_model_instance__has_key"),
                    distinct=True,
                ),
                Collect(
                    RelationNameResolver("has_sponsor_model_instance__has_key"),
                    distinct=True,
                ),
                Collect(
                    NodeNameResolver("has_sponsor_model_instance__has_sort_key"),
                    distinct=True,
                ),
                Collect(
                    RelationNameResolver("has_sponsor_model_instance__has_sort_key"),
                    distinct=True,
                ),
            )
            .order_by("has_sponsor_model_instance__has_dataset|ordinal")
        )

    def find_all(
        self,
        sort_by: dict[str, bool] | None = None,
        page_number: int = 1,
        page_size: int = 0,
        filter_by: dict[str, dict[str, Any]] | None = None,
        filter_operator: FilterOperator = FilterOperator.AND,
        total_count: bool = False,
        **kwargs,
    ):
        sponsor_model_name = kwargs.get("sponsor_model_name")
        if sponsor_model_name:
            if filter_by is None:
                filter_by = {}
            filter_by["sponsor_model.name"] = {"v": [sponsor_model_name], "op": "eq"}
        return super().find_all(
            sort_by=sort_by,
            page_number=page_number,
            page_size=page_size,
            filter_by=filter_by,
            filter_operator=filter_operator,
            total_count=total_count,
        )

    def get_distinct_headers(
        self,
        field_name: str,
        search_string: str = "",
        filter_by: dict[str, dict[str, Any]] | None = None,
        filter_operator: FilterOperator = FilterOperator.AND,
        page_size: int = 10,
        **kwargs,
    ) -> list[Any]:
        sponsor_model_name = kwargs.get("sponsor_model_name")
        if sponsor_model_name:
            if filter_by is None:
                filter_by = {}
            filter_by["sponsor_model.name"] = {"v": [sponsor_model_name], "op": "eq"}
        return super().get_distinct_headers(
            field_name=field_name,
            search_string=search_string,
            filter_by=filter_by,
            filter_operator=filter_operator,
            page_size=page_size,
        )

    def _has_data_changed(
        self, ar: SponsorModelDatasetAR, value: SponsorModelDatasetInstance
    ) -> bool:
        return (
            ar.sponsor_model_dataset_vo.is_basic_std != value.is_basic_std
            or ar.sponsor_model_dataset_vo.xml_path != value.xml_path
            or ar.sponsor_model_dataset_vo.xml_title != value.xml_title
            or ar.sponsor_model_dataset_vo.structure != value.structure
            or ar.sponsor_model_dataset_vo.purpose != value.purpose
            or ar.sponsor_model_dataset_vo.is_cdisc_std != value.is_cdisc_std
            or ar.sponsor_model_dataset_vo.source_ig != value.source_ig
            or ar.sponsor_model_dataset_vo.standard_ref != value.standard_ref
            or ar.sponsor_model_dataset_vo.comment != value.comment
            or ar.sponsor_model_dataset_vo.ig_comment != value.ig_comment
            or ar.sponsor_model_dataset_vo.map_domain_flag != value.map_domain_flag
            or ar.sponsor_model_dataset_vo.suppl_qual_flag != value.suppl_qual_flag
            or ar.sponsor_model_dataset_vo.include_in_raw != value.include_in_raw
            or ar.sponsor_model_dataset_vo.gen_raw_seqno_flag
            != value.gen_raw_seqno_flag
            or ar.sponsor_model_dataset_vo.label != value.label
            or ar.sponsor_model_dataset_vo.state != value.state
            or ar.sponsor_model_dataset_vo.extended_domain != value.extended_domain
        )

    def _create(self, item: SponsorModelDatasetAR) -> SponsorModelDatasetAR:
        """
        Overrides generic LibraryItemRepository method
        """
        root = Dataset.nodes.get_or_none(uid=item.uid)

        if not root:
            # Create a new "root" node with uid
            root = Dataset(uid=item.uid).save()
            # Link it with the DataModelCatalogue node
            catalogue = DataModelCatalogue.nodes.get_or_none(
                name=item.sponsor_model_dataset_vo.target_data_model_catalogue
            )
            root.has_dataset.connect(catalogue)

        instance = self._get_or_create_instance(root=root, ar=item)

        # Connect with SponsorModelValue node
        parent_node = SponsorModelValue.nodes.get_or_none(
            name=item.sponsor_model_dataset_vo.sponsor_model_name
        )

        BusinessLogicException.raise_if_not(
            parent_node,
            msg=f"Sponsor Model with Name '{item.sponsor_model_dataset_vo.sponsor_model_name}' doesn't exist.",
        )

        instance.has_dataset.connect(
            parent_node,
            {"ordinal": item.sponsor_model_dataset_vo.enrich_build_order},
        )

        return item

    def _get_or_create_instance(
        self, root: Dataset, ar: SponsorModelDatasetAR
    ) -> SponsorModelDatasetInstance:
        for itm in root.has_sponsor_model_instance.all():
            if not self._has_data_changed(ar, itm):
                return itm

        new_instance = SponsorModelDatasetInstance(
            is_basic_std=ar.sponsor_model_dataset_vo.is_basic_std,
            xml_path=ar.sponsor_model_dataset_vo.xml_path,
            xml_title=ar.sponsor_model_dataset_vo.xml_title,
            structure=ar.sponsor_model_dataset_vo.structure,
            purpose=ar.sponsor_model_dataset_vo.purpose,
            is_cdisc_std=ar.sponsor_model_dataset_vo.is_cdisc_std,
            source_ig=ar.sponsor_model_dataset_vo.source_ig,
            standard_ref=ar.sponsor_model_dataset_vo.standard_ref,
            comment=ar.sponsor_model_dataset_vo.comment,
            ig_comment=ar.sponsor_model_dataset_vo.ig_comment,
            map_domain_flag=ar.sponsor_model_dataset_vo.map_domain_flag,
            suppl_qual_flag=ar.sponsor_model_dataset_vo.suppl_qual_flag,
            include_in_raw=ar.sponsor_model_dataset_vo.include_in_raw,
            gen_raw_seqno_flag=ar.sponsor_model_dataset_vo.gen_raw_seqno_flag,
            label=ar.sponsor_model_dataset_vo.label,
            state=ar.sponsor_model_dataset_vo.state,
            extended_domain=ar.sponsor_model_dataset_vo.extended_domain,
        )

        self._db_save_node(new_instance)

        # Add extra properties using Cypher (neomodel only saves defined properties)
        if ar.sponsor_model_dataset_vo.extra_properties:
            # Sanitize key names for Neo4j (replace spaces and dashes with underscores)
            sanitized_props = {
                key.replace(" ", "_").replace("-", "_"): value
                for key, value in ar.sponsor_model_dataset_vo.extra_properties.items()
            }
            db.cypher_query(
                "MATCH (n) WHERE elementId(n) = $element_id SET n += $extra_props",
                {"element_id": new_instance.element_id, "extra_props": sanitized_props},
            )

        # Connect with root
        root.has_sponsor_model_instance.connect(new_instance)

        # Create relations
        # Find key & sort-key variable nodes
        if ar.sponsor_model_dataset_vo.keys is not None:
            keys = DatasetVariable.nodes.filter(
                uid__in=ar.sponsor_model_dataset_vo.keys
            )
            keys_dict = {key.uid: key for key in keys}
            for index, key in enumerate(ar.sponsor_model_dataset_vo.keys):
                if key in keys_dict:
                    new_instance.has_key.connect(keys_dict[key], {"order": index})

        if ar.sponsor_model_dataset_vo.sort_keys is not None:
            sort_keys = DatasetVariable.nodes.filter(
                uid__in=ar.sponsor_model_dataset_vo.sort_keys
            )
            sort_keys_dict = {key.uid: key for key in sort_keys}
            for index, key in enumerate(ar.sponsor_model_dataset_vo.sort_keys):
                if key in sort_keys_dict:
                    new_instance.has_sort_key.connect(
                        sort_keys_dict[key], {"order": index}
                    )

        # Connect with implemented dataset class - if provided
        if ar.sponsor_model_dataset_vo.implemented_dataset_class:
            implemented_dataset_class = DatasetClass.nodes.filter(
                uid=ar.sponsor_model_dataset_vo.implemented_dataset_class,
                has_instance__has_dataset_class__implements__extended_by__name=ar.sponsor_model_dataset_vo.sponsor_model_name,
            ).traverse("has_instance")
            BusinessLogicException.raise_if_not(
                implemented_dataset_class,
                msg=f"Dataset class with uid '{ar.sponsor_model_dataset_vo.implemented_dataset_class}' does not exist.",
            )
            implemented_dataset_class_instance = (
                implemented_dataset_class.resolve_subgraph()[0]._relations[
                    "has_instance"
                ]
            )
            new_instance.implements_dataset_class.connect(
                implemented_dataset_class_instance
            )

        return new_instance

    def _create_aggregate_root_instance_from_version_root_relationship_and_value(
        self,
        root: Dataset,
        library: Library,
        relationship: VersionRelationship,
        value: SponsorModelDatasetInstance,
        **_kwargs,
    ) -> SponsorModelDatasetAR:
        sponsor_model_name = None
        sponsor_model_version = None
        enrich_build_order = None
        # Get sponsor model-related info
        (
            sponsor_model_name,
            sponsor_model_version,
            enrich_build_order,
        ) = get_sponsor_model_info_from_dataset(value)

        # Extract extra properties from the Neo4j node
        known_fields = {
            "is_basic_std",
            "xml_path",
            "xml_title",
            "structure",
            "purpose",
            "is_cdisc_std",
            "source_ig",
            "standard_ref",
            "comment",
            "ig_comment",
            "map_domain_flag",
            "suppl_qual_flag",
            "include_in_raw",
            "gen_raw_seqno_flag",
            "label",
            "state",
            "extended_domain",
            "id",
            "element_id",
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

        return SponsorModelDatasetAR.from_repository_values(
            dataset_uid=root.uid,
            sponsor_model_dataset_vo=SponsorModelDatasetVO.from_repository_values(
                sponsor_model_name=sponsor_model_name,
                sponsor_model_version_number=sponsor_model_version,
                dataset_uid=root.uid,
                is_basic_std=value.is_basic_std,
                implemented_dataset_class=value.implemented_dataset_class,
                xml_path=value.xml_path,
                xml_title=value.xml_title,
                structure=value.structure,
                purpose=value.purpose,
                keys=None,
                sort_keys=None,
                is_cdisc_std=value.is_cdisc_std,
                source_ig=value.source_ig,
                standard_ref=value.standard_ref,
                comment=value.comment,
                ig_comment=value.ig_comment,
                map_domain_flag=value.map_domain_flag,
                suppl_qual_flag=value.suppl_qual_flag,
                include_in_raw=value.include_in_raw,
                gen_raw_seqno_flag=value.gen_raw_seqno_flag,
                enrich_build_order=enrich_build_order,
                label=value.label,
                state=value.state,
                extended_domain=value.extended_domain,
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
        versioned_object: SponsorModelDatasetAR,
        root: Dataset,
        value: SponsorModelDatasetInstance,
    ) -> None:
        # This method from parent repo is not needed for this repo
        # So we use pass to skip implementation
        pass
