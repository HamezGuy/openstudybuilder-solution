"""Deterministic bounded retrieval from OSB's governed mapping libraries."""

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from neomodel import db

from clinical_mdr_api.models.integrations.mapping_context import (
    MappingContextCandidate,
    MappingContextCandidateGroup,
    MappingContextDataModel,
    MappingContextPackage,
    MappingContextRequest,
    MappingContextResponse,
    MappingContextV2Request,
    MappingContextV2Response,
)
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.services.integrations.proposal_review import (
    Neo4jProposalReviewRepository,
)
from clinical_mdr_api.services.studies.study_standard_version_selection import (
    StudyStandardVersionService,
)

_FAMILY_NODE_MODELS = {
    "objective_templates": (
        "ObjectiveTemplateRoot",
        "ObjectiveTemplateValue",
        "ObjectiveTemplate",
    ),
    "endpoint_templates": (
        "EndpointTemplateRoot",
        "EndpointTemplateValue",
        "EndpointTemplate",
    ),
    "criteria_templates": (
        "CriteriaTemplateRoot",
        "CriteriaTemplateValue",
        "CriteriaTemplate",
    ),
    "timeframe_templates": (
        "TimeframeTemplateRoot",
        "TimeframeTemplateValue",
        "TimeframeTemplate",
    ),
    # StudyEndpoint.timeframe_uid references an approved Timeframe INSTANCE,
    # never its template. Keeping instances as their own governed family stops a
    # TimeframeTemplate UID from passing review and then failing the native DTO.
    "timeframes": ("TimeframeRoot", "TimeframeValue", "Timeframe"),
    "activities": ("ActivityRoot", "ActivityValue", "Activity"),
    "odm_forms": ("OdmFormRoot", "OdmFormValue", "OdmForm"),
    "odm_item_groups": ("OdmItemGroupRoot", "OdmItemGroupValue", "OdmItemGroup"),
    "odm_items": ("OdmItemRoot", "OdmItemValue", "OdmItem"),
}

_BLOCKER_ONLY_FAMILY_CODES = {
    "compound_product_relationships": (
        "MAPPING_CONTEXT_COMPOUND_PRODUCT_RELATIONSHIP_UNAVAILABLE"
    ),
    "study_compound_dosing_relationships": (
        "MAPPING_CONTEXT_STUDY_COMPOUND_DOSING_RELATIONSHIP_UNAVAILABLE"
    ),
}


def _canonical_hash(value: Any) -> str:
    return canonical_hash(jsonable_encoder(value))


def _normalized(values: list[str]) -> list[str]:
    return sorted({value.strip().lower() for value in values if value.strip()})


class MappingContextService:
    def __init__(
        self,
        standard_version_loader: Callable[..., list[Any]] | None = None,
        context_registry: Any | None = None,
    ) -> None:
        self._standard_version_loader = (
            standard_version_loader or self._load_standard_versions
        )
        self._context_registry = context_registry or Neo4jProposalReviewRepository()

    @staticmethod
    def _load_standard_versions(**kwargs) -> list[Any]:
        # Construct the auth-aware OSB service only when a study-scoped context
        # actually needs it. Requested-package contexts and pure unit tests must
        # not require a Starlette request merely to instantiate this service.
        return StudyStandardVersionService().get_standard_versions_in_study(**kwargs)

    def get_context(
        self,
        request: MappingContextRequest,
        osb_openapi_hash: str,
    ) -> MappingContextResponse:
        searches = _normalized(request.search_strings)
        codes = _normalized(request.search_codes)
        warnings: list[str] = []
        release_blockers: list[str] = []
        families = list(dict.fromkeys(request.resource_families))
        packages = self._selected_packages(request, warnings, release_blockers)
        data_models = self._selected_data_models(request, release_blockers)
        # The primary Proposal V2 contract is USDM study definition -> OSB.
        # DDF CT is its terminology snapshot. CDASH/SDTM packages and models are
        # prerequisites only for the separate collection-standard family; making
        # them unconditional blocked every pure-USDM study proposal.
        collection_mapping_requested = "cdash_variables" in families
        required_catalogues = {"DDF CT"}
        if collection_mapping_requested:
            required_catalogues.update({"SDTM CT", "CDASH CT"})
        selected_catalogues = {package.catalogue_name for package in packages}
        for catalogue in sorted(required_catalogues - selected_catalogues):
            release_blockers.append(
                f"MAPPING_CONTEXT_{catalogue.replace(' ', '_')}_PACKAGE_MISSING"
            )
        if any(package.automatically_created for package in packages):
            release_blockers.append("MAPPING_CONTEXT_AUTO_SELECTED_PACKAGE")
        selected_model_families = {model.family for model in data_models}
        required_model_families = (
            {"SDTM", "CDASH"} if collection_mapping_requested else set()
        )
        for family in sorted(required_model_families - selected_model_families):
            release_blockers.append(f"MAPPING_CONTEXT_{family}_MODEL_IG_MISSING")

        candidates: dict[str, list[MappingContextCandidate]] = {}
        governed = not release_blockers
        if not searches and not codes:
            warnings.append(
                "No bounded search string/code was supplied; candidate lists are empty by policy."
            )
        elif not governed:
            warnings.append(
                "Candidate retrieval was refused because standards packages and model/IG versions are not fully pinned."
            )
        else:
            package_uids = [package.package_uid for package in packages]
            for family in families:
                if family in _BLOCKER_ONLY_FAMILY_CODES:
                    rows = []
                    release_blockers.append(_BLOCKER_ONLY_FAMILY_CODES[family])
                elif family == "controlled_terminology":
                    rows = self._controlled_terminology(
                        searches,
                        codes,
                        package_uids,
                        request.maximum_candidates_per_family,
                    )
                elif family == "controlled_terminology_codelists":
                    rows, incomplete = self._controlled_terminology_codelists_v2(
                        searches,
                        codes,
                        package_uids,
                        request.maximum_candidates_per_family,
                    )
                    if incomplete:
                        release_blockers.append(
                            f"MAPPING_CONTEXT_CANDIDATE_IDENTITY_INCOMPLETE:{incomplete}"
                        )
                elif family == "units":
                    rows = self._units(
                        searches, codes, request.maximum_candidates_per_family
                    )
                elif family == "cdash_variables":
                    rows, incomplete = self._cdash_variables_v2(
                        searches,
                        codes,
                        request.maximum_candidates_per_family,
                        None,
                        data_models,
                    )
                    if incomplete:
                        release_blockers.append(
                            f"MAPPING_CONTEXT_CANDIDATE_IDENTITY_INCOMPLETE:{incomplete}"
                        )
                else:
                    rows = self._versioned_library_family(
                        family,
                        searches,
                        codes,
                        request.maximum_candidates_per_family,
                    )
                candidates[family] = rows

        release_blockers = sorted(set(release_blockers))
        content = {
            "schemaVersion": "osb-mapping-context/1.0",
            "mappingAuthority": "OpenStudyBuilder",
            "studyUid": request.study_uid,
            "studyValueVersion": request.study_value_version,
            "osbOpenApiHash": osb_openapi_hash,
            "governed": governed,
            "selectedPackages": jsonable_encoder(packages),
            "selectedDataModels": jsonable_encoder(data_models),
            "requestedFamilies": families,
            "searchStrings": searches,
            "searchCodes": codes,
            "maximumCandidatesPerFamily": request.maximum_candidates_per_family,
            "candidates": jsonable_encoder(candidates),
            "releaseBlockers": release_blockers,
            "warnings": warnings,
        }
        context_hash = _canonical_hash(content)
        if governed:
            self._context_registry.save_context(context_hash, content)
        return MappingContextResponse(
            study_uid=request.study_uid,
            study_value_version=request.study_value_version,
            generated_at=datetime.now(timezone.utc),
            context_hash=context_hash,
            osb_openapi_hash=osb_openapi_hash,
            governed=governed,
            selected_packages=packages,
            selected_data_models=data_models,
            candidates=candidates,
            release_blockers=release_blockers,
            warnings=warnings,
        )

    def get_context_v2(
        self,
        request: MappingContextV2Request,
        osb_openapi_hash: str,
    ) -> MappingContextV2Response:
        """Build independent bounded candidate groups for every concept target."""
        warnings: list[str] = []
        release_blockers: list[str] = []
        packages = self._selected_packages(request, warnings, release_blockers)
        data_models = self._selected_data_models(request, release_blockers)
        requested_families = {
            group.resource_family for group in request.candidate_groups
        }
        collection_mapping_requested = "cdash_variables" in requested_families
        required_catalogues = {"DDF CT"}
        if collection_mapping_requested:
            required_catalogues.update({"SDTM CT", "CDASH CT"})
        selected_catalogues = {package.catalogue_name for package in packages}
        for catalogue in sorted(required_catalogues - selected_catalogues):
            release_blockers.append(
                f"MAPPING_CONTEXT_{catalogue.replace(' ', '_')}_PACKAGE_MISSING"
            )
        selected_model_families = {model.family for model in data_models}
        required_model_families = (
            {"SDTM", "CDASH"} if collection_mapping_requested else set()
        )
        for family in sorted(required_model_families - selected_model_families):
            release_blockers.append(f"MAPPING_CONTEXT_{family}_MODEL_IG_MISSING")

        # Group outcomes must never starve later groups. Only immutable context
        # prerequisites may prevent retrieval for every group; truncation or an
        # incomplete candidate in one concept is recorded without skipping others.
        prerequisite_blockers = tuple(release_blockers)

        package_uids = [package.package_uid for package in packages]
        groups: list[MappingContextCandidateGroup] = []
        for requested in request.candidate_groups:
            searches = _normalized(requested.search_strings)
            codes = _normalized(requested.search_codes)
            parent_searches = _normalized(requested.parent_search_strings)
            group_blockers: list[str] = []
            candidates: list[MappingContextCandidate] = []
            truncated = False
            incomplete_count = 0
            if requested.resource_family in _BLOCKER_ONLY_FAMILY_CODES:
                group_blockers.append(
                    _BLOCKER_ONLY_FAMILY_CODES[requested.resource_family]
                )
            elif not searches and not codes:
                group_blockers.append("MAPPING_CONTEXT_GROUP_SEARCH_EMPTY")
            elif requested.resource_family == "controlled_terminology" and (
                requested.parent_resource_type != "CTCodelist" or not parent_searches
            ):
                group_blockers.append("MAPPING_CONTEXT_CT_PARENT_REQUIRED")
            elif not prerequisite_blockers:
                query_limit = request.maximum_candidates_per_group + 1
                if requested.resource_family == "controlled_terminology":
                    candidates, incomplete_count = self._controlled_terminology_v2(
                        searches, codes, parent_searches, package_uids, query_limit
                    )
                    # A pinned CDISC package is the authoritative source, so it is
                    # always consulted first. Sponsor study-design codelists belong
                    # to no package at all; without this fallback every concept
                    # governed by one is unresolvable regardless of the pin.
                    if not candidates:
                        candidates, incomplete_count = (
                            self._controlled_terminology_sponsor_v2(
                                searches, codes, parent_searches, query_limit
                            )
                        )
                elif requested.resource_family == "controlled_terminology_codelists":
                    candidates, incomplete_count = (
                        self._controlled_terminology_codelists_v2(
                            searches, codes, package_uids, query_limit
                        )
                    )
                    if not candidates:
                        candidates, incomplete_count = (
                            self._controlled_terminology_codelists_sponsor_v2(
                                searches, codes, query_limit
                            )
                        )
                elif requested.resource_family == "units":
                    candidates, incomplete_count = self._units_v2(
                        searches, codes, query_limit, request.as_of
                    )
                elif requested.resource_family == "cdash_variables":
                    candidates, incomplete_count = self._cdash_variables_v2(
                        searches, codes, query_limit, request.as_of, data_models
                    )
                else:
                    candidates, incomplete_count = self._versioned_library_family_v2(
                        requested.resource_family,
                        searches,
                        codes,
                        query_limit,
                        request.as_of,
                    )
                truncated = len(candidates) > request.maximum_candidates_per_group
                candidates = candidates[: request.maximum_candidates_per_group]
                if truncated:
                    group_blockers.append("MAPPING_CONTEXT_GROUP_TRUNCATED")
                if incomplete_count:
                    group_blockers.append(
                        f"MAPPING_CONTEXT_CANDIDATE_IDENTITY_INCOMPLETE:{incomplete_count}"
                    )
            complete = not group_blockers
            group = MappingContextCandidateGroup(
                fact_id=requested.fact_id,
                concept_id=requested.concept_id,
                target_key=requested.target_key,
                semantic_role=requested.semantic_role,
                resource_family=requested.resource_family,
                parent_resource_type=requested.parent_resource_type,
                parent_search_strings=requested.parent_search_strings,
                complete=complete,
                truncated=truncated,
                candidates=candidates,
                release_blockers=group_blockers,
            )
            groups.append(group)
            release_blockers.extend(
                f"{code}:{requested.fact_id}:{requested.concept_id}:{requested.target_key}"
                for code in group_blockers
            )

        release_blockers = sorted(set(release_blockers))
        # A bounded/pinned snapshot remains governed even when one candidate
        # group is unresolved or truncated. Group blockers prohibit release, but
        # the immutable context must still be persisted so OSB can review that
        # unresolved concept. Only missing global package/model prerequisites
        # make the snapshot itself ungoverned.
        governed = not prerequisite_blockers
        content = {
            "schemaVersion": "osb-mapping-context/2.0",
            "mappingAuthority": "OpenStudyBuilder",
            "studyUid": request.study_uid,
            "studyValueVersion": request.study_value_version,
            "asOf": request.as_of,
            "osbOpenApiHash": osb_openapi_hash,
            "governed": governed,
            "selectedPackages": jsonable_encoder(packages),
            "selectedDataModels": jsonable_encoder(data_models),
            "candidateGroups": jsonable_encoder(groups),
            "releaseBlockers": release_blockers,
            "warnings": warnings,
        }
        context_hash = _canonical_hash(content)
        if governed:
            self._context_registry.save_context(context_hash, content)
        return MappingContextV2Response(
            study_uid=request.study_uid,
            study_value_version=request.study_value_version,
            as_of=request.as_of,
            generated_at=datetime.now(timezone.utc),
            context_hash=context_hash,
            osb_openapi_hash=osb_openapi_hash,
            governed=governed,
            selected_packages=packages,
            selected_data_models=data_models,
            candidate_groups=groups,
            release_blockers=release_blockers,
            warnings=warnings,
        )

    def _selected_packages(
        self,
        request: MappingContextRequest,
        warnings: list[str],
        release_blockers: list[str],
    ) -> list[MappingContextPackage]:
        packages = []
        if request.study_uid:
            if request.requested_packages:
                release_blockers.append(
                    "MAPPING_CONTEXT_STUDY_AND_REQUESTED_PACKAGES_CONFLICT"
                )
            rows = self._standard_version_loader(
                study_uid=request.study_uid,
                study_value_version=request.study_value_version,
            )
            for row in rows:
                package = getattr(row, "ct_package", None)
                if package is None:
                    warnings.append(
                        f"StudyStandardVersion {row.uid} has no CT package."
                    )
                    continue
                packages.append(
                    MappingContextPackage(
                        study_standard_version_uid=row.uid,
                        catalogue_name=package.catalogue_name,
                        package_uid=package.uid,
                        effective_date=str(package.effective_date),
                        automatically_created=bool(
                            getattr(row, "automatically_created", False)
                        ),
                    )
                )
        else:
            for requested in request.requested_packages:
                query = """
                    MATCH (catalogue:CTCatalogue {name: $catalogue_name})
                          -[:CONTAINS_PACKAGE]->(package:CTPackage)
                    WHERE coalesce(package.uid, package.name) = $package_uid
                      AND toString(package.effective_date) STARTS WITH $effective_date
                    RETURN coalesce(package.uid, package.name)
                    LIMIT 1
                """
                result, _ = db.cypher_query(
                    query,
                    {
                        "catalogue_name": requested.catalogue_name,
                        "package_uid": requested.package_uid,
                        "effective_date": requested.effective_date,
                    },
                )
                if not result:
                    release_blockers.append(
                        f"MAPPING_CONTEXT_PACKAGE_NOT_FOUND:{requested.catalogue_name}:{requested.package_uid}"
                    )
                    continue
                packages.append(
                    MappingContextPackage(
                        catalogue_name=requested.catalogue_name,
                        package_uid=requested.package_uid,
                        effective_date=requested.effective_date,
                    )
                )
        return sorted(
            packages,
            key=lambda item: (
                item.catalogue_name,
                item.effective_date,
                item.package_uid,
            ),
        )

    @staticmethod
    def _selected_data_models(
        request: MappingContextRequest,
        release_blockers: list[str],
    ) -> list[MappingContextDataModel]:
        selected = []
        as_of = getattr(request, "as_of", None)
        for requested in request.requested_data_models:
            if as_of is None:
                validity = """
                    model_version.status = 'Final' AND model_version.end_date IS NULL
                    AND ig_version.status = 'Final' AND ig_version.end_date IS NULL
                """
                params = {
                    "model_catalogue": requested.family,
                    "model_uid": requested.model_uid,
                    "model_version": requested.model_version,
                    "ig_catalogue": f"{requested.family}IG",
                    "ig_uid": requested.implementation_guide_uid,
                    "ig_version": requested.implementation_guide_version,
                }
            else:
                validity = """
                    model_version.status = 'Final'
                    AND model_version.start_date <= datetime($as_of)
                    AND (model_version.end_date IS NULL OR model_version.end_date > datetime($as_of))
                    AND ig_version.status = 'Final'
                    AND ig_version.start_date <= datetime($as_of)
                    AND (ig_version.end_date IS NULL OR ig_version.end_date > datetime($as_of))
                """
                params = {
                    "model_catalogue": requested.family,
                    "model_uid": requested.model_uid,
                    "model_version": requested.model_version,
                    "ig_catalogue": f"{requested.family}IG",
                    "ig_uid": requested.implementation_guide_uid,
                    "ig_version": requested.implementation_guide_version,
                    "as_of": as_of.isoformat(),
                }
            query = (
                """
                MATCH (:DataModelCatalogue {name: $model_catalogue})
                      -[:HAS_DATA_MODEL]->(model_root:DataModelRoot {uid: $model_uid})
                      -[model_version:HAS_VERSION]->(model_value:DataModelValue {version_number: $model_version})
                MATCH (:DataModelCatalogue {name: $ig_catalogue})
                      -[:HAS_DATA_MODEL_IG]->(ig_root:DataModelIGRoot {uid: $ig_uid})
                      -[ig_version:HAS_VERSION]->(ig_value:DataModelIGValue {version_number: $ig_version})
                      -[:IMPLEMENTS]->(model_value)
                WHERE """
                + validity
                + """
                RETURN model_root.uid, ig_root.uid
                LIMIT 1
            """
            )
            result, _ = db.cypher_query(query, params)
            if not result:
                release_blockers.append(
                    f"MAPPING_CONTEXT_MODEL_IG_NOT_FOUND:{requested.family}:"
                    f"{requested.model_uid}:{requested.implementation_guide_uid}"
                )
                continue
            selected.append(
                MappingContextDataModel(
                    family=requested.family,
                    model_uid=requested.model_uid,
                    model_version=requested.model_version,
                    implementation_guide_uid=requested.implementation_guide_uid,
                    implementation_guide_version=requested.implementation_guide_version,
                )
            )
        return sorted(selected, key=lambda item: item.family)

    @staticmethod
    def _controlled_terminology(searches, codes, package_uids, limit):
        query = """
            MATCH (package:CTPackage)-[:CONTAINS_CODELIST]->(:CTPackageCodelist)
                  -[:CONTAINS_TERM]->(:CTPackageTerm)
                  -[:CONTAINS_ATTRIBUTES]->(package_attributes:CTTermAttributesValue)
                  <-[:HAS_VERSION]-(:CTTermAttributesRoot)<-[:HAS_ATTRIBUTES_ROOT]-(root:CTTermRoot)
            WHERE coalesce(package.uid, package.name) IN $package_uids
            MATCH (root)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)
                  -[version:HAS_VERSION]->(name:CTTermNameValue)
            WHERE version.status = 'Final'
              AND version.start_date <= datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z')
              AND (version.end_date IS NULL OR version.end_date > datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z'))
            WITH DISTINCT package, root, name, package_attributes, version,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(root.uid) = code
                         OR toLower(coalesce(package_attributes.concept_id, '')) = code)
                     THEN 0
                   WHEN any(needle IN $searches WHERE toLower(name.name) = needle
                         OR toLower(coalesce(package_attributes.preferred_term, '')) = needle)
                     THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE
                        toLower(name.name) CONTAINS needle
                        OR toLower(coalesce(package_attributes.preferred_term, '')) CONTAINS needle)
               OR any(code IN $codes WHERE
                        toLower(root.uid) = code
                        OR toLower(coalesce(package_attributes.concept_id, '')) = code)
            RETURN root.uid AS uid, name.name AS label,
                   package_attributes.concept_id AS code, version.status AS status,
                   version.version AS version, coalesce(package.uid, package.name),
                   toString(package.effective_date)
            ORDER BY match_rank, toLower(label), uid
            LIMIT $limit
        """
        result, _ = db.cypher_query(
            query,
            {
                "searches": searches,
                "codes": codes,
                "package_uids": package_uids,
                "limit": limit,
            },
        )
        return [
            MappingContextCandidate(
                resource_family="controlled_terminology",
                resource_type="CTTerm",
                uid=row[0],
                label=row[1],
                code=row[2],
                status=row[3],
                version=str(row[4]) if row[4] is not None else None,
                package_uid=row[5],
                package_effective_date=str(row[6])[:10],
            )
            for row in result
        ]

    @staticmethod
    def _controlled_terminology_v2(
        searches, codes, parent_searches, package_uids, limit
    ):
        query = """
            MATCH (catalogue:CTCatalogue)-[:CONTAINS_PACKAGE]->(package:CTPackage)
                  -[:CONTAINS_CODELIST]->(package_codelist:CTPackageCodelist)
                  -[:CONTAINS_ATTRIBUTES]->(codelist_attributes:CTCodelistAttributesValue)
                  <-[codelist_version:HAS_VERSION]-(codelist_attributes_root:CTCodelistAttributesRoot)
                  <-[:HAS_ATTRIBUTES_ROOT]-(codelist_root:CTCodelistRoot)
            WHERE coalesce(package.uid, package.name) IN $package_uids
              AND any(parent IN $parent_searches WHERE
                    toLower(codelist_root.uid) = parent
                    OR toLower(coalesce(codelist_attributes.name, '')) = parent
                    OR toLower(coalesce(codelist_attributes.submission_value, '')) = parent)
              AND codelist_version.status = 'Final'
              AND codelist_version.start_date <= datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z')
              AND (codelist_version.end_date IS NULL OR codelist_version.end_date > datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z'))
            MATCH (package_codelist)-[:CONTAINS_TERM]->(:CTPackageTerm)
                  -[:CONTAINS_ATTRIBUTES]->(term_attributes:CTTermAttributesValue)
                  <-[term_attributes_version:HAS_VERSION]-(:CTTermAttributesRoot)
                  <-[:HAS_ATTRIBUTES_ROOT]-(term_root:CTTermRoot)
            MATCH (codelist_root)-[owns:HAS_TERM]->(membership:CTCodelistTerm)
                  -[:HAS_TERM_ROOT]->(term_root)
            WHERE owns.end_date IS NULL
            MATCH (term_root)-[:HAS_NAME_ROOT]->(term_name_root:CTTermNameRoot)
                  -[:LATEST_FINAL]->(term_name:CTTermNameValue)
            MATCH (term_name_root)-[term_name_version:HAS_VERSION]->(term_name)
            WHERE term_name_version.status = 'Final'
              AND term_name_version.end_date IS NULL
              AND term_attributes_version.status = 'Final'
              AND term_attributes_version.start_date <= datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z')
              AND (term_attributes_version.end_date IS NULL OR term_attributes_version.end_date > datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z'))
            WITH DISTINCT catalogue, package, codelist_root, codelist_attributes,
                 codelist_version, membership, term_root, term_name,
                 term_name_version, term_attributes,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(term_root.uid) = code
                        OR toLower(coalesce(term_attributes.concept_id, '')) = code
                        OR toLower(coalesce(membership.submission_value, '')) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(term_name.name) = needle
                        OR toLower(coalesce(term_attributes.preferred_term, '')) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE
                       toLower(term_name.name) CONTAINS needle
                       OR toLower(coalesce(term_attributes.preferred_term, '')) CONTAINS needle)
               OR any(code IN $codes WHERE toLower(term_root.uid) = code
                       OR toLower(coalesce(term_attributes.concept_id, '')) = code
                       OR toLower(coalesce(membership.submission_value, '')) = code)
            RETURN term_root.uid, term_name.name, term_attributes.concept_id,
                   term_name_version.version, term_name_version.status,
                   toString(term_name_version.start_date), toString(term_name_version.end_date),
                   catalogue.name, coalesce(package.uid, package.name),
                   toString(package.effective_date), codelist_root.uid,
                   codelist_version.version, codelist_attributes.submission_value,
                   codelist_attributes.extensible, membership.submission_value
            ORDER BY match_rank, toLower(term_name.name), term_root.uid,
                     codelist_root.uid, package.effective_date DESC
            LIMIT $limit
        """
        result, _ = db.cypher_query(
            query,
            {
                "searches": searches,
                "codes": codes,
                "parent_searches": parent_searches,
                "package_uids": package_uids,
                "limit": limit,
            },
        )
        candidates = []
        incomplete = 0
        for row in result:
            if not all(
                (
                    row[0],
                    row[1],
                    row[3],
                    row[4],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[14],
                )
            ):
                incomplete += 1
                continue
            candidates.append(
                MappingContextCandidate(
                    resource_family="controlled_terminology",
                    resource_type="CTTerm",
                    uid=row[0],
                    label=row[1],
                    code=row[2],
                    version=str(row[3]),
                    status=row[4],
                    valid_from=row[5],
                    valid_to=row[6],
                    catalogue_name=row[7],
                    package_uid=row[8],
                    package_effective_date=str(row[9])[:10],
                    parent_resource_type="CTCodelist",
                    parent_uid=row[10],
                    parent_version=str(row[11]),
                    parent_submission_value=row[12],
                    submission_value=row[14],
                    extensible=row[13],
                )
            )
        return candidates, incomplete

    @staticmethod
    def _controlled_terminology_sponsor_v2(searches, codes, parent_searches, limit):
        """Resolve CT terms held in a non-CDISC library outside any CT package.

        The study-design vocabularies OSB's own study objects consume (Criteria
        Type, Flowchart Group, Objective/Endpoint Level, Visit Type, Visit
        Contact Mode, Epoch/Element Sub Type, Arm Type, Time Point Reference)
        are sponsor codelists. They are deliberately not members of a published
        CDISC package, so package-scoped retrieval cannot reach them at all and
        every such concept would be reported unresolved. Governance is preserved
        by pinning the codelist and term versions themselves: the candidate
        carries library provenance instead of package provenance, so a reviewer
        can always tell the two apart.
        """
        query = """
            MATCH (library:Library)-[:CONTAINS_CODELIST]->(codelist_root:CTCodelistRoot)
                  -[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
                  -[codelist_version:HAS_VERSION]->(codelist_attributes:CTCodelistAttributesValue)
            WHERE library.name <> 'CDISC'
              AND NOT (codelist_attributes)<-[:CONTAINS_ATTRIBUTES]-(:CTPackageCodelist)
              AND any(parent IN $parent_searches WHERE
                    toLower(codelist_root.uid) = parent
                    OR toLower(coalesce(codelist_attributes.name, '')) = parent
                    OR toLower(coalesce(codelist_attributes.submission_value, '')) = parent)
              AND codelist_version.status = 'Final'
              AND codelist_version.end_date IS NULL
            MATCH (codelist_root)-[owns:HAS_TERM]->(membership:CTCodelistTerm)
                  -[:HAS_TERM_ROOT]->(term_root:CTTermRoot)
            WHERE owns.end_date IS NULL
            MATCH (term_root)-[:HAS_NAME_ROOT]->(term_name_root:CTTermNameRoot)
                  -[:LATEST_FINAL]->(term_name:CTTermNameValue)
            MATCH (term_name_root)-[term_name_version:HAS_VERSION]->(term_name)
            WHERE term_name_version.status = 'Final'
              AND term_name_version.end_date IS NULL
            OPTIONAL MATCH (term_root)-[:HAS_ATTRIBUTES_ROOT]->(:CTTermAttributesRoot)
                           -[:LATEST_FINAL]->(term_attributes:CTTermAttributesValue)
            WITH DISTINCT library, codelist_root, codelist_attributes, codelist_version,
                 membership, term_root, term_name, term_name_version, term_attributes,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(term_root.uid) = code
                        OR toLower(coalesce(term_attributes.concept_id, '')) = code
                        OR toLower(coalesce(membership.submission_value, '')) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(term_name.name) = needle
                        OR toLower(coalesce(term_attributes.preferred_term, '')) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE
                       toLower(term_name.name) CONTAINS needle
                       OR toLower(coalesce(term_attributes.preferred_term, '')) CONTAINS needle)
               OR any(code IN $codes WHERE toLower(term_root.uid) = code
                       OR toLower(coalesce(term_attributes.concept_id, '')) = code
                       OR toLower(coalesce(membership.submission_value, '')) = code)
            RETURN term_root.uid, term_name.name, term_attributes.concept_id,
                   term_name_version.version, term_name_version.status,
                   toString(term_name_version.start_date), toString(term_name_version.end_date),
                   library.name, codelist_root.uid, codelist_version.version,
                   codelist_attributes.submission_value, codelist_attributes.extensible,
                   membership.submission_value
            ORDER BY match_rank, toLower(term_name.name), term_root.uid, codelist_root.uid
            LIMIT $limit
        """
        result, _ = db.cypher_query(
            query,
            {
                "searches": searches,
                "codes": codes,
                "parent_searches": parent_searches,
                "limit": limit,
            },
        )
        candidates = []
        incomplete = 0
        for row in result:
            if not all(
                (row[0], row[1], row[3], row[4], row[7], row[8], row[9], row[10], row[12])
            ):
                incomplete += 1
                continue
            candidates.append(
                MappingContextCandidate(
                    resource_family="controlled_terminology",
                    resource_type="CTTerm",
                    uid=row[0],
                    label=row[1],
                    code=row[2],
                    version=str(row[3]),
                    status=row[4],
                    valid_from=row[5],
                    valid_to=row[6],
                    library_name=row[7],
                    parent_resource_type="CTCodelist",
                    parent_uid=row[8],
                    parent_version=str(row[9]),
                    parent_submission_value=row[10],
                    submission_value=row[12],
                    extensible=row[11],
                )
            )
        return candidates, incomplete

    @staticmethod
    def _controlled_terminology_codelists_sponsor_v2(searches, codes, limit):
        """Resolve non-CDISC codelists that no published CT package contains."""
        query = """
            MATCH (library:Library)-[:CONTAINS_CODELIST]->(codelist_root:CTCodelistRoot)
                  -[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
                  -[attributes_version:HAS_VERSION]->(attributes:CTCodelistAttributesValue)
            MATCH (codelist_root)-[:HAS_NAME_ROOT]->(name_root:CTCodelistNameRoot)
                  -[name_version:HAS_VERSION]->(name:CTCodelistNameValue)
            WHERE library.name <> 'CDISC'
              AND NOT (attributes)<-[:CONTAINS_ATTRIBUTES]-(:CTPackageCodelist)
              AND attributes_version.status = 'Final'
              AND attributes_version.end_date IS NULL
              AND name_version.status = 'Final'
              AND name_version.end_date IS NULL
            WITH DISTINCT library, codelist_root, name, name_version, attributes,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(codelist_root.uid) = code
                        OR toLower(coalesce(attributes.concept_id, '')) = code
                        OR toLower(coalesce(attributes.submission_value, '')) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(name.name) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE
                       toLower(name.name) CONTAINS needle
                       OR toLower(coalesce(attributes.name, '')) CONTAINS needle)
               OR any(code IN $codes WHERE toLower(codelist_root.uid) = code
                       OR toLower(coalesce(attributes.concept_id, '')) = code
                       OR toLower(coalesce(attributes.submission_value, '')) = code)
            RETURN codelist_root.uid, name.name, attributes.concept_id,
                   name_version.version, name_version.status,
                   toString(name_version.start_date), toString(name_version.end_date),
                   library.name, attributes.submission_value, attributes.extensible
            ORDER BY match_rank, toLower(name.name), codelist_root.uid
            LIMIT $limit
        """
        result, _ = db.cypher_query(
            query,
            {"searches": searches, "codes": codes, "limit": limit},
        )
        candidates = []
        incomplete = 0
        for row in result:
            if not all((row[0], row[1], row[3], row[4], row[7], row[8])):
                incomplete += 1
                continue
            candidates.append(
                MappingContextCandidate(
                    resource_family="controlled_terminology_codelists",
                    resource_type="CTCodelist",
                    uid=row[0],
                    label=row[1],
                    code=row[2],
                    version=str(row[3]),
                    status=row[4],
                    valid_from=row[5],
                    valid_to=row[6],
                    library_name=row[7],
                    submission_value=row[8],
                    extensible=row[9],
                )
            )
        return candidates, incomplete

    @staticmethod
    def _controlled_terminology_codelists_v2(searches, codes, package_uids, limit):
        query = """
            MATCH (catalogue:CTCatalogue)-[:CONTAINS_PACKAGE]->(package:CTPackage)
                  -[:CONTAINS_CODELIST]->(package_codelist:CTPackageCodelist)
                  -[:CONTAINS_ATTRIBUTES]->(attributes:CTCodelistAttributesValue)
                  <-[attributes_version:HAS_VERSION]-(attributes_root:CTCodelistAttributesRoot)
                  <-[:HAS_ATTRIBUTES_ROOT]-(codelist_root:CTCodelistRoot)
                  -[:HAS_NAME_ROOT]->(name_root:CTCodelistNameRoot)
                  -[name_version:HAS_VERSION]->(name:CTCodelistNameValue)
            WHERE coalesce(package.uid, package.name) IN $package_uids
              AND attributes_version.status = 'Final'
              AND name_version.status = 'Final'
              AND attributes_version.start_date <= datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z')
              AND (attributes_version.end_date IS NULL OR attributes_version.end_date > datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z'))
              AND name_version.start_date <= datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z')
              AND (name_version.end_date IS NULL OR name_version.end_date > datetime(
                    toString(date(package.effective_date)) + 'T23:59:59.999999Z'))
            WITH DISTINCT catalogue, package, codelist_root, name, name_version,
                 attributes, attributes_version,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(codelist_root.uid) = code
                        OR toLower(coalesce(attributes.concept_id, '')) = code
                        OR toLower(coalesce(attributes.submission_value, '')) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(name.name) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE
                       toLower(name.name) CONTAINS needle
                       OR toLower(coalesce(attributes.name, '')) CONTAINS needle)
               OR any(code IN $codes WHERE toLower(codelist_root.uid) = code
                       OR toLower(coalesce(attributes.concept_id, '')) = code
                       OR toLower(coalesce(attributes.submission_value, '')) = code)
            RETURN codelist_root.uid, name.name, attributes.concept_id,
                   name_version.version, name_version.status,
                   toString(name_version.start_date), toString(name_version.end_date),
                   catalogue.name, coalesce(package.uid, package.name),
                   toString(package.effective_date), attributes.submission_value,
                   attributes.extensible
            ORDER BY match_rank, toLower(name.name), codelist_root.uid,
                     package.effective_date DESC
            LIMIT $limit
        """
        result, _ = db.cypher_query(
            query,
            {
                "searches": searches,
                "codes": codes,
                "package_uids": package_uids,
                "limit": limit,
            },
        )
        candidates = []
        incomplete = 0
        for row in result:
            if not all(
                (row[0], row[1], row[3], row[4], row[7], row[8], row[9], row[10])
            ):
                incomplete += 1
                continue
            candidates.append(
                MappingContextCandidate(
                    resource_family="controlled_terminology_codelists",
                    resource_type="CTCodelist",
                    uid=row[0],
                    label=row[1],
                    code=row[2],
                    version=str(row[3]),
                    status=row[4],
                    valid_from=row[5],
                    valid_to=row[6],
                    catalogue_name=row[7],
                    package_uid=row[8],
                    package_effective_date=str(row[9])[:10],
                    submission_value=row[10],
                    extensible=row[11],
                )
            )
        return candidates, incomplete

    @staticmethod
    def _units(searches, codes, limit):
        query = """
            MATCH (root:UnitDefinitionRoot)-[version:LATEST_FINAL]->(value:UnitDefinitionValue)
            OPTIONAL MATCH (value)-[:HAS_UCUM_TERM]->(ucum:UCUMTermRoot)
            WITH root, value, ucum, version,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(root.uid) = code
                         OR toLower(coalesce(ucum.uid, '')) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(value.name) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE toLower(value.name) CONTAINS needle)
               OR any(code IN $codes WHERE toLower(root.uid) = code
                        OR toLower(coalesce(ucum.uid, '')) = code)
            RETURN root.uid AS uid, value.name AS label, ucum.uid AS ucum,
                   value.version AS version
            ORDER BY match_rank, toLower(label), uid
            LIMIT $limit
        """
        result, _ = db.cypher_query(
            query,
            {"searches": searches, "codes": codes, "limit": limit},
        )
        return [
            MappingContextCandidate(
                resource_family="units",
                resource_type="UnitDefinition",
                uid=row[0],
                label=row[1],
                ucum_code=row[2],
                version=str(row[3]) if row[3] is not None else None,
                status="Final",
            )
            for row in result
        ]

    @staticmethod
    def _units_v2(searches, codes, limit, as_of):
        if as_of is None:
            version_match = """
                MATCH (root:UnitDefinitionRoot)-[:LATEST_FINAL]->(value:UnitDefinitionValue)
                MATCH (root)-[version:HAS_VERSION]->(value)
                WHERE version.status = 'Final' AND version.end_date IS NULL
            """
            params = {"searches": searches, "codes": codes, "limit": limit}
        else:
            version_match = """
                MATCH (root:UnitDefinitionRoot)-[version:HAS_VERSION]->(value:UnitDefinitionValue)
                WHERE version.status = 'Final'
                  AND version.start_date <= datetime($as_of)
                  AND (version.end_date IS NULL OR version.end_date > datetime($as_of))
            """
            params = {
                "searches": searches,
                "codes": codes,
                "limit": limit,
                "as_of": as_of.isoformat(),
            }
        query = version_match + """
            MATCH (value)-[:HAS_UCUM_TERM]->(ucum_root:UCUMTermRoot)
                  -[:LATEST_FINAL]->(ucum_value:UCUMTermValue)
            OPTIONAL MATCH (value)-[:HAS_CT_DIMENSION]->(:CTTermContext)
                  -[:HAS_SELECTED_TERM]->(:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)
                  -[:LATEST_FINAL]->(dimension_name:CTTermNameValue)
            WITH root, value, version, ucum_root, ucum_value, dimension_name,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(root.uid) = code
                        OR toLower(ucum_value.name) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(value.name) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE toLower(value.name) CONTAINS needle)
               OR any(code IN $codes WHERE toLower(root.uid) = code
                       OR toLower(ucum_value.name) = code)
            RETURN root.uid, value.name, version.version, version.status,
                   toString(version.start_date), toString(version.end_date),
                   ucum_value.name, dimension_name.name,
                   value.conversion_factor_to_master
            ORDER BY match_rank, toLower(value.name), root.uid
            LIMIT $limit
        """
        result, _ = db.cypher_query(query, params)
        candidates = []
        incomplete = 0
        for row in result:
            if not all((row[0], row[1], row[2], row[3], row[6])):
                incomplete += 1
                continue
            candidates.append(
                MappingContextCandidate(
                    resource_family="units",
                    resource_type="UnitDefinition",
                    uid=row[0],
                    label=row[1],
                    version=str(row[2]),
                    status=row[3],
                    valid_from=row[4],
                    valid_to=row[5],
                    ucum_expression=row[6],
                    dimension=row[7],
                    conversion_factor_to_master=row[8],
                )
            )
        return candidates, incomplete

    @staticmethod
    def _cdash_variables_v2(searches, codes, limit, as_of, selected_data_models):
        """Retrieve CDASH DatasetVariables only from the exact pinned model/IG.

        The optional HAS_MAPPING_TARGET edge is returned as part of candidate
        identity. A CDASH variable without an OSB-governed SDTM target is omitted
        and release-blocking rather than letting IL invent an SDTM variable.
        """
        cdash = next(
            (model for model in selected_data_models if model.family == "CDASH"),
            None,
        )
        sdtm = next(
            (model for model in selected_data_models if model.family == "SDTM"),
            None,
        )
        if cdash is None or sdtm is None:
            return [], 1
        if as_of is None:
            temporal = """
                  AND ig_version.status = 'Final' AND ig_version.end_date IS NULL
            """
            params = {
                "searches": searches,
                "codes": codes,
                "limit": limit,
                "as_of": None,
            }
        else:
            temporal = """
                  AND ig_version.status = 'Final'
                  AND ig_version.start_date <= datetime($as_of)
                  AND (ig_version.end_date IS NULL OR ig_version.end_date > datetime($as_of))
            """
            params = {
                "searches": searches,
                "codes": codes,
                "limit": limit,
                "as_of": as_of.isoformat(),
            }
        params.update(
            {
                "model_uid": cdash.model_uid,
                "model_version": cdash.model_version,
                "ig_uid": cdash.implementation_guide_uid,
                "ig_version": cdash.implementation_guide_version,
                "sdtm_ig_uid": sdtm.implementation_guide_uid,
                "sdtm_ig_version": sdtm.implementation_guide_version,
            }
        )
        query = (
            """
            MATCH (model_root:DataModelRoot {uid: $model_uid})
                  -[model_version:HAS_VERSION]->(model_value:DataModelValue {version_number: $model_version})
            MATCH (ig_root:DataModelIGRoot {uid: $ig_uid})
                  -[ig_version:HAS_VERSION]->(ig_value:DataModelIGValue {version_number: $ig_version})
                  -[:IMPLEMENTS]->(model_value)
            MATCH (ig_value)-[:HAS_DATASET]->(dataset_value:DatasetInstance)
                  <-[:HAS_INSTANCE]-(dataset_root:Dataset)
            MATCH (dataset_value)-[dataset_variable:HAS_DATASET_VARIABLE]->
                  (variable_value:DatasetVariableInstance)
                  <-[:HAS_INSTANCE]-(variable_root:DatasetVariable)
            WHERE dataset_variable.version_number = ig_value.version_number
        """
            + temporal
            + """
            MATCH (variable_value)-[mapping:HAS_MAPPING_TARGET]->
                  (mapping_value:DatasetVariableInstance)
                  <-[:HAS_INSTANCE]-(mapping_root:DatasetVariable)
            MATCH (mapping_value)<-[mapped_dataset_variable:HAS_DATASET_VARIABLE]-
                  (mapped_dataset:DatasetInstance)<-[:HAS_DATASET]-
                  (sdtm_ig_value:DataModelIGValue {version_number: $sdtm_ig_version})
                  <-[sdtm_ig_version:HAS_VERSION]-(:DataModelIGRoot {uid: $sdtm_ig_uid})
            WHERE mapping.version_number = ig_value.version_number
              AND mapped_dataset_variable.version_number = sdtm_ig_value.version_number
              AND sdtm_ig_version.status = 'Final'
              AND (
                ($as_of IS NULL AND sdtm_ig_version.end_date IS NULL)
                OR ($as_of IS NOT NULL
                    AND sdtm_ig_version.start_date <= datetime($as_of)
                    AND (sdtm_ig_version.end_date IS NULL
                         OR sdtm_ig_version.end_date > datetime($as_of)))
              )
            WITH variable_root, variable_value, ig_version, dataset_root, ig_root, ig_value,
                 model_root, model_value, mapping_root, mapping_value,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(variable_root.uid) = code
                        OR toLower(coalesce(variable_value.name, '')) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(coalesce(variable_value.label, '')) = needle
                        OR toLower(coalesce(variable_value.name, '')) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE
                    toLower(coalesce(variable_value.label, '')) CONTAINS needle
                    OR toLower(coalesce(variable_value.title, '')) CONTAINS needle
                    OR toLower(coalesce(variable_value.name, '')) CONTAINS needle)
               OR any(code IN $codes WHERE toLower(variable_root.uid) = code
                    OR toLower(coalesce(variable_value.name, '')) = code)
            RETURN variable_root.uid,
                   coalesce(variable_value.label, variable_value.title, variable_value.name),
                   variable_value.name,
                   ig_value.version_number, ig_version.status,
                   toString(ig_version.start_date), toString(ig_version.end_date),
                   dataset_root.uid, ig_value.version_number,
                   model_root.uid, model_value.version_number,
                   ig_root.uid, ig_value.version_number,
                   mapping_root.uid, sdtm_ig_value.version_number
            ORDER BY match_rank, toLower(coalesce(variable_value.name, '')),
                     toLower(coalesce(variable_value.label, '')), variable_root.uid
            LIMIT $limit
        """
        )
        result, _ = db.cypher_query(query, params)
        candidates = []
        incomplete = 0
        for row in result:
            if not all(
                (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                    row[14],
                )
            ):
                incomplete += 1
                continue
            candidates.append(
                MappingContextCandidate(
                    resource_family="cdash_variables",
                    resource_type="DatasetVariable",
                    uid=row[0],
                    label=row[1],
                    code=row[2],
                    version=str(row[3]),
                    status=row[4],
                    valid_from=row[5],
                    valid_to=row[6],
                    parent_resource_type="Dataset",
                    parent_uid=row[7],
                    parent_version=str(row[8]),
                    model_uid=row[9],
                    model_version=str(row[10]),
                    implementation_guide_uid=row[11],
                    implementation_guide_version=str(row[12]),
                    mapping_target_uid=row[13],
                    mapping_target_version=str(row[14]),
                )
            )
        return candidates, incomplete

    @staticmethod
    def _versioned_library_family(family, searches, codes, limit):
        root_label, value_label, resource_type = _FAMILY_NODE_MODELS[family]
        query = f"""
            MATCH (root:{root_label})-[version:HAS_VERSION]->(value:{value_label})
            WHERE version.status = 'Final'
              AND (
                any(needle IN $searches WHERE toLower(value.name) CONTAINS needle)
                OR any(code IN $codes WHERE toLower(root.uid) = code)
              )
            WITH root, version, value,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(root.uid) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(value.name) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            OPTIONAL MATCH (library:Library)-[]->(root)
            RETURN root.uid AS uid, value.name AS label,
                   version.version AS version, version.status AS status,
                   library.name AS library_name
            ORDER BY match_rank, toLower(label), uid
            LIMIT $limit
        """
        result, _ = db.cypher_query(
            query,
            {"searches": searches, "codes": codes, "limit": limit},
        )
        return [
            MappingContextCandidate(
                resource_family=family,
                resource_type=resource_type,
                uid=row[0],
                label=row[1],
                version=str(row[2]) if row[2] is not None else None,
                status=row[3],
                library_name=row[4],
            )
            for row in result
        ]

    @staticmethod
    def _versioned_library_family_v2(family, searches, codes, limit, as_of):
        root_label, value_label, resource_type = _FAMILY_NODE_MODELS[family]
        if as_of is None:
            relationship = """
                MATCH (root:{root_label})-[:LATEST_FINAL]->(value:{value_label})
                MATCH (root)-[version:HAS_VERSION]->(value)
                WHERE version.status = 'Final' AND version.end_date IS NULL
            """.format(root_label=root_label, value_label=value_label)
            params = {"searches": searches, "codes": codes, "limit": limit}
        else:
            relationship = """
                MATCH (root:{root_label})-[version:HAS_VERSION]->(value:{value_label})
                WHERE version.status = 'Final'
                  AND version.start_date <= datetime($as_of)
                  AND (version.end_date IS NULL OR version.end_date > datetime($as_of))
            """.format(root_label=root_label, value_label=value_label)
            params = {
                "searches": searches,
                "codes": codes,
                "limit": limit,
                "as_of": as_of.isoformat(),
            }
        query = relationship + """
            WITH root, version, value,
                 CASE
                   WHEN any(code IN $codes WHERE toLower(root.uid) = code) THEN 0
                   WHEN any(needle IN $searches WHERE toLower(value.name) = needle) THEN 1
                   ELSE 2
                 END AS match_rank
            WHERE any(needle IN $searches WHERE toLower(value.name) CONTAINS needle)
               OR any(code IN $codes WHERE toLower(root.uid) = code)
            OPTIONAL MATCH (library:Library)-[]->(root)
            OPTIONAL MATCH (root)-[:HAS_TYPE]->(:CTTermContext)
                           -[:HAS_SELECTED_TERM]->(criteria_type:CTTermRoot)
            OPTIONAL MATCH (root)-[:USES_PARAMETER]->(parameter:TemplateParameter)
            RETURN root.uid, value.name, version.version, version.status,
                   toString(version.start_date), toString(version.end_date),
                   library.name, value.oid, criteria_type.uid,
                   count(DISTINCT parameter), min(match_rank) AS resolved_match_rank
            ORDER BY resolved_match_rank, toLower(value.name), root.uid
            LIMIT $limit
        """
        result, _ = db.cypher_query(query, params)
        candidates = []
        incomplete = 0
        for row in result:
            stable_oid = row[7] if family.startswith("odm_") else None
            criteria_type_uid = row[8] if family == "criteria_templates" else None
            parameter_count = row[9]
            if (
                not all((row[0], row[1], row[2], row[3], row[6]))
                or (family.startswith("odm_") and not stable_oid)
                or (family == "criteria_templates" and not criteria_type_uid)
            ):
                incomplete += 1
                continue
            candidates.append(
                MappingContextCandidate(
                    resource_family=family,
                    resource_type=resource_type,
                    uid=row[0],
                    label=row[1],
                    version=str(row[2]),
                    status=row[3],
                    valid_from=row[4],
                    valid_to=row[5],
                    library_name=row[6],
                    parent_resource_type="Library",
                    parent_uid=row[6],
                    parent_version="unversioned-library-name",
                    stable_oid=stable_oid,
                    criteria_type_uid=criteria_type_uid,
                    parameter_count=parameter_count,
                )
            )
        return candidates, incomplete
