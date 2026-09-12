# pylint: disable=invalid-name
# pylint: disable=redefined-builtin
from datetime import datetime
from typing import Any

from common.config import settings
from common.exceptions import NotFoundException, ValidationException
from common.utils import validate_page_number_and_page_size
from consumer_api.shared.common import (
    SortByType,
    db_pagination_clause,
    db_sort_clause,
    query,
)
from consumer_api.v1 import models


def get_base_query_for_study_root_and_value(study_version_number: str | None) -> str:
    if study_version_number:
        return """
        MATCH (study_root:StudyRoot {uid: $study_uid})-[hv:HAS_VERSION {version: $study_version_number}]->(study_value:StudyValue)
        WITH study_root, study_value, hv ORDER BY hv.end_date DESC LIMIT 1
        """

    return """
    MATCH (study_root:StudyRoot {uid: $study_uid})-[latest:LATEST]->(study_value:StudyValue)
    MATCH (study_root)-[hv:HAS_VERSION]->(study_value)
    WITH study_root, study_value, hv ORDER BY hv.end_date DESC LIMIT 1
    """


def get_base_query_for_study_root_and_value_with_study_id(
    study_version_number: str | None, subpart: str | None
) -> str:
    base_query = "MATCH (study_root:StudyRoot)-[rel"
    if study_version_number:
        base_query += (
            ":HAS_VERSION {version: $study_version_number, status: 'RELEASED'}"
        )
    else:
        base_query += ":LATEST_RELEASED"
    if subpart:
        base_query += "]->(study_value:StudyValue {study_id_prefix: $project, study_number: $study_number, study_subpart_acronym: $subpart})"
    else:
        base_query += "]->(study_value:StudyValue {study_id_prefix: $project, study_number: $study_number})"
    base_query += "WITH study_root, study_value, rel ORDER BY rel.end_date DESC LIMIT 1"
    return base_query


def get_latest_version_from_datetime(
    project: str, study_number: str, date_time: str, subpart: str | None
) -> str:
    params = {
        "project": project,
        "study_number": study_number,
        "datetime": date_time,
        "subpart": subpart,
    }
    full_query = "MATCH (study_root:StudyRoot)-[hv:HAS_VERSION {status:'RELEASED'}]->"
    if subpart:
        full_query += "(study_value:StudyValue {study_id_prefix: $project, study_number: $study_number, study_subpart_acronym: $subpart})"
    else:
        full_query += "(study_value:StudyValue {study_id_prefix: $project, study_number: $study_number})"
    full_query += """
        where datetime(hv.end_date) <= datetime($datetime)
        with hv ORDER BY hv.end_date DESC LIMIT 1
        return hv.version AS version
        """

    res = query(full_query, params)

    NotFoundException.raise_if_not(
        res,
        msg=f"Study has no RELEASED version before {date_time}.",
    )

    return res[0]["version"]


def get_studies(
    sort_by: models.SortByStudies = models.SortByStudies.UID,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: int = 10,
    page_number: int = 1,
    id: str | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params = {}
    filter_clause = ""

    if id is not None:
        params["id"] = id.strip()
        filter_clause = "WHERE toUpper(id) CONTAINS toUpper($id)"

    base_query = f"""
        MATCH (study_root:StudyRoot)-[:LATEST]->(study_value:StudyValue)
        OPTIONAL MATCH (study_root)-[hv:HAS_VERSION|LATEST_DRAFT]->(:StudyValue)
        OPTIONAL MATCH (study_root)-[hv_ld:LATEST_DRAFT]->(:StudyValue)
        OPTIONAL MATCH (author:User) WHERE author.user_id = hv.author_id
        WITH *,
            COLLECT ({{
                user_id: author.user_id,
                username: author.username
            }}) AS authors
        ORDER BY hv.start_date DESC
        WITH
            study_root,
            study_value,
            study_root.uid as uid,
            study_value.study_acronym as acronym,
            study_value.study_id_prefix as id_prefix,
            study_value.study_number as number,
            CASE study_value.study_subpart_acronym
                WHEN IS NULL THEN COALESCE(study_value.study_id_prefix, '') + "-" + COALESCE(study_value.study_number, '')
                ELSE COALESCE(study_value.study_id_prefix, '') + "-" + COALESCE(study_value.study_number, '') + "-" + study_value.study_subpart_acronym
            END AS id,
            hv_ld as version_latest_draft,
            COLLECT(DISTINCT {{
                version_status: hv.status,
                version_number: hv.version,
                version_started_at: hv.start_date,
                version_ended_at: hv.end_date,
                version_author_id: hv.author_id,
                all_authors: authors,
                version_description: hv.change_description
            }}) as versions_all

        {filter_clause}

        WITH *,
            [v IN versions_all 
                WHERE v.version_status IN ['RELEASED', 'LOCKED']
                OR (v.version_started_at = version_latest_draft.start_date AND v.version_ended_at is null)] as versions

        OPTIONAL MATCH (study_value)-[hsds:HAS_STUDY_DATA_SUPPLIER]->(sds:StudyDataSupplier)
        OPTIONAL MATCH (sds)-[hds:HAS_DATA_SUPPLIER]->(dsv:DataSupplierValue)
        OPTIONAL MATCH (sds)-[:HAS_STUDY_DATA_SUPPLIER_TYPE]->(ctc:CTTermContext)
        OPTIONAL MATCH (ctc)-[:HAS_SELECTED_TERM]->(ctr:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(ctnv:CTTermNameValue)
        OPTIONAL MATCH (ctr)<-[:HAS_TERM_ROOT]-(:CTCodelistTerm)<-[:HAS_TERM]-(ccr:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->
        (:CTCodelistAttributesRoot)-[:LATEST]->(:CTCodelistAttributesValue {{submission_value: "DATA_SUPPLIER_TYPE"}})

        RETURN uid,
            acronym,
            id_prefix,
            number,
            id,
            versions,
            [(study_root)-[:HAS_COMPLETENESS_TAG]->(t:DataCompletenessTag) | t.name] as data_completeness_tags,
            [ds IN COLLECT(DISTINCT {{
                uid: sds.uid,
                name: dsv.name,
                type_uid: ctr.uid,
                type_codelist_uid: ccr.uid,
                order: sds.order
            }}) WHERE ds.uid IS NOT NULL] AS study_data_suppliers
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause(sort_by.value, sort_order.value),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)


def get_study_version(
    study_uid: str, study_version_number: str | None
) -> dict[str, Any]:
    params = {"study_uid": study_uid, "study_version_number": study_version_number}

    base_query = get_base_query_for_study_root_and_value(study_version_number)
    full_query = f"""
        {base_query}
        RETURN  hv.version AS version_number,
                hv.status AS version_status,
                hv.start_date AS version_started_at,
                hv.end_date AS version_ended_at,
                hv.change_description AS version_description,
                hv.author_id AS version_author_id
        """

    res = query(full_query, params)

    NotFoundException.raise_if_not(
        res,
        msg=(
            f"Study version {study_version_number} does not exist."
            if study_version_number
            else f"Study {study_uid} does not exist."
        ),
    )
    return res[0]


def get_study_visits(
    study_uid: str,
    sort_by: models.SortByStudyVisits = models.SortByStudyVisits.UID,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: int = 10,
    page_number: int = 1,
    study_version_number: str | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params = {"study_uid": study_uid, "study_version_number": study_version_number}
    base_query = get_base_query_for_study_root_and_value(study_version_number)

    base_query += """
        MATCH (study_value)-[:HAS_STUDY_VISIT]-(study_visit:StudyVisit)
        OPTIONAL MATCH (study_visit)-[:HAS_VISIT_NAME]->(:VisitNameRoot)-[:LATEST]->(visit_name_value:VisitNameValue)
        OPTIONAL MATCH (study_visit)-[:HAS_VISIT_TYPE]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(visit_type_ct_term_root:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(visit_type_ct_term_name_value:CTTermNameValue)
        OPTIONAL MATCH (study_visit)<-[:STUDY_EPOCH_HAS_STUDY_VISIT]-(study_epoch:StudyEpoch)<-[:HAS_STUDY_EPOCH]-(study_value)
        OPTIONAL MATCH (study_epoch)-[:HAS_EPOCH]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(epoch_ct_term_root:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(epoch_term:CTTermNameValue)
        OPTIONAL MATCH (study_visit)-[:HAS_TIMEPOINT]->(:TimePointRoot)-[:LATEST]->(:TimePointValue)-[:HAS_UNIT_DEFINITION]->(time_unit_unit_definition_root:UnitDefinitionRoot)-[:LATEST]->(time_unit_unit_definition_value:UnitDefinitionValue)
        OPTIONAL MATCH (study_visit)-[:HAS_TIMEPOINT]->(:TimePointRoot)-[:LATEST]->(:TimePointValue)-[:HAS_VALUE]->(time_value_root:NumericValueRoot)-[:LATEST]->(time_value_value:NumericValue)
        OPTIONAL MATCH (study_visit)-[:HAS_TIMEPOINT]->(:TimePointRoot)-[:LATEST]->(:TimePointValue)-[:HAS_TIME_REFERENCE]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(time_ref_ct_root:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(time_ref_ct_term_name_value:CTTermNameValue)
        OPTIONAL MATCH (study_visit)-[:HAS_WINDOW_UNIT]->(window_unit_unit_definition_root:UnitDefinitionRoot)-[:LATEST]->(window_unit_unit_definition_value:UnitDefinitionValue)

        WITH
            study_root.uid AS study_uid,
            study_visit.uid AS uid,
            study_visit.unique_visit_number AS unique_visit_number,
            study_visit.visit_number AS visit_number,
            study_visit.short_visit_label AS visit_short_name,
            study_visit.visit_window_min AS visit_window_min,
            study_visit.visit_window_max AS visit_window_max,
            study_visit.is_global_anchor_visit AS is_global_anchor_visit,
            study_visit.visit_class AS visit_class,
            study_visit.visit_subclass AS visit_subclass,
            study_visit.visit_sublabel_reference AS anchor_visit_uid,
            visit_name_value.name AS visit_name,
            visit_type_ct_term_root.uid AS visit_type_uid,
            visit_type_ct_term_name_value.name AS visit_type_name,
            window_unit_unit_definition_root.uid AS visit_window_unit_uid,
            window_unit_unit_definition_value.name AS visit_window_unit_name,
            study_epoch.uid AS study_epoch_uid,
            epoch_term.name AS study_epoch_name,
            time_unit_unit_definition_root.uid AS time_unit_uid,
            time_unit_unit_definition_value.name AS time_unit_name,
            time_unit_unit_definition_value.conversion_factor_to_master AS time_unit_conversion_factor_to_master,
            time_value_root.uid AS time_value_uid,
            time_value_value.value AS time_value_value,
            time_ref_ct_term_name_value.name AS time_reference_name
        RETURN *
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause(
                sort_by.value,
                sort_order.value,
                sort_by_type=(
                    SortByType.NUMBER
                    if sort_by == models.SortByStudyVisits.UNIQUE_VISIT_NUMBER
                    else SortByType.STRING
                ),
            ),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)


def get_study_activities(
    study_uid: str,
    sort_by: models.SortByStudyActivities = models.SortByStudyActivities.UID,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: int = 10,
    page_number: int = 1,
    study_version_number: str | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params = {"study_uid": study_uid, "study_version_number": study_version_number}
    base_query = get_base_query_for_study_root_and_value(study_version_number)

    base_query += """
        WITH study_root, study_value, hv
        MATCH (study_value)-[:HAS_STUDY_ACTIVITY]->(sa:StudyActivity)-[:HAS_SELECTED_ACTIVITY]->(av:ActivityValue)<-[:HAS_VERSION]-(ar:ActivityRoot)
        MATCH (sa)-[:STUDY_ACTIVITY_HAS_STUDY_SOA_GROUP]->(soa_group:StudySoAGroup)-[:HAS_FLOWCHART_GROUP]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(soa_group_term:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(soa_group_term_value:CTTermNameValue)
        MATCH (ar)<-[:CONTAINS_CONCEPT]-(lib:Library)

        WITH DISTINCT *
        CALL {
            WITH ar, av
            MATCH (ar)-[hv:HAS_VERSION]-(av)
            WHERE hv.status in ['Final', 'Retired']
            WITH hv
            ORDER BY
                toInteger(split(hv.version, '.')[0]) ASC,
                toInteger(split(hv.version, '.')[1]) ASC,
                hv.end_date ASC,
                hv.start_date ASC
            WITH collect(hv) as hvs
            RETURN last(hvs) as hv_ver
        }

        WITH DISTINCT *
        ORDER BY sa.order ASC
        MATCH (sa)<-[:AFTER]-(:StudyAction)
        RETURN DISTINCT
            study_root.uid AS study_uid,
            sa.uid AS uid,
            head([(sa)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_SUBGROUP]->(study_activity_subgroup_selection)
            -[:HAS_SELECTED_ACTIVITY_SUBGROUP]->(activity_subgroup_value:ActivitySubGroupValue)<-[:HAS_VERSION]-(activity_subgroup_root:ActivitySubGroupRoot) | 
                {
                    selection_uid: study_activity_subgroup_selection.uid, 
                    activity_subgroup_uid:activity_subgroup_root.uid,
                    activity_subgroup_name:activity_subgroup_value.name
                }]) AS study_activity_subgroup,
            head([(sa)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_GROUP]->(study_activity_group_selection)
                -[:HAS_SELECTED_ACTIVITY_GROUP]->(activity_group_value:ActivityGroupValue)<-[:HAS_VERSION]-(activity_group_root:ActivityGroupRoot) | 
                {
                    selection_uid: study_activity_group_selection.uid, 
                    activity_group_uid: activity_group_root.uid,
                    activity_group_name:activity_group_value.name
                }]) AS study_activity_group,
            {
                study_soa_group_uid: soa_group.uid,
                soa_group_term_uid: soa_group_term.uid,
                soa_group_name: soa_group_term_value.name
            } AS soa_group,
            ar.uid AS activity_uid,
            av.name AS activity_name,
            av.nci_concept_id AS nci_concept_id,
            av.nci_concept_name AS nci_concept_name,
            coalesce(av.is_data_collected, False) AS is_data_collected
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause(sort_by.value, sort_order.value),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)


def get_study_activity_instances(
    study_uid: str,
    sort_by: models.SortByStudyActivityInstances = models.SortByStudyActivityInstances.UID,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: int = 10,
    page_number: int = 1,
    study_version_number: str | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params = {"study_uid": study_uid, "study_version_number": study_version_number}
    base_query = get_base_query_for_study_root_and_value(study_version_number)

    base_query += """
        MATCH (study_value)-[:HAS_STUDY_ACTIVITY_INSTANCE]->(sa:StudyActivityInstance)
            <-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_INSTANCE]-(study_activity:StudyActivity)<-[:HAS_STUDY_ACTIVITY]-(study_value)

        WITH DISTINCT *
        MATCH (sa)<-[:AFTER]-(sac:StudyAction)

        RETURN DISTINCT
            study_root.uid AS study_uid,
            sa.uid AS uid,
            study_activity.uid AS study_activity_uid,
            head(apoc.coll.sortMulti([(study_activity)-[:HAS_SELECTED_ACTIVITY]->(activity_value:ActivityValue)<-[has_version:HAS_VERSION]
            -(activity_root:ActivityRoot) WHERE has_version.status IN ['Final', 'Retired'] | 
                {
                    uid: activity_root.uid,
                    name: activity_value.name,
                    nci_concept_id: activity_value.nci_concept_id,
                    nci_concept_name: activity_value.nci_concept_name,
                    version: has_version.version,
                    major_version: toInteger(split(has_version.version,'.')[0]),
                    minor_version: toInteger(split(has_version.version,'.')[1]),
                    order: study_activity.order
                }], ['major_version', 'minor_version'])) AS activity,
            head(apoc.coll.sortMulti([(sa)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(activity_instance_val:ActivityInstanceValue)<-[has_version:HAS_VERSION]
            -(activity_instance_root:ActivityInstanceRoot) WHERE has_version.status IN ['Final', 'Retired'] |  
                { 
                    uid: activity_instance_root.uid, 
                    name: activity_instance_val.name,
                    nci_concept_id: activity_instance_val.nci_concept_id,
                    nci_concept_name: activity_instance_val.nci_concept_name,
                    param_code: activity_instance_val.adam_param_code,
                    topic_code: activity_instance_val.topic_code,
                    version: has_version.version,
                    major_version: toInteger(split(has_version.version,'.')[0]),
                    minor_version: toInteger(split(has_version.version,'.')[1]),
                    order: sa.order
                }], ['major_version', 'minor_version'])) AS activity_instance,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_SUBGROUP]->(study_activity_subgroup_selection)
                -[:HAS_SELECTED_ACTIVITY_SUBGROUP]->(activity_subgroup_value:ActivitySubGroupValue)<-[:HAS_VERSION]-(activity_subgroup_root:ActivitySubGroupRoot)
                WHERE (study_value)-[:HAS_STUDY_ACTIVITY_SUBGROUP]->(study_activity_subgroup_selection) | 
                {
                    selection_uid: study_activity_subgroup_selection.uid, 
                    uid:activity_subgroup_root.uid,
                    name: activity_subgroup_value.name,
                    order: study_activity_subgroup_selection.order
                }]) AS study_activity_subgroup,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_GROUP]->(study_activity_group_selection)
                -[:HAS_SELECTED_ACTIVITY_GROUP]->(activity_group_value:ActivityGroupValue)<-[:HAS_VERSION]-(activity_group_root:ActivityGroupRoot)
                 WHERE (study_value)-[:HAS_STUDY_ACTIVITY_GROUP]->(study_activity_group_selection) | 
                {
                    selection_uid: study_activity_group_selection.uid, 
                    uid: activity_group_root.uid,
                    name: activity_group_value.name,
                    order: study_activity_group_selection.order
                }]) AS study_activity_group,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_SOA_GROUP]->(study_soa_group_selection)
                -[:HAS_FLOWCHART_GROUP]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(ct_term_root:CTTermRoot)-[:HAS_NAME_ROOT]-(:CTTermNameRoot)-[:LATEST]->(flowchart_value:CTTermNameValue)
                WHERE (study_value)-[:HAS_STUDY_SOA_GROUP]->(study_soa_group_selection) | 
                {
                    selection_uid: study_soa_group_selection.uid, 
                    uid: ct_term_root.uid,
                    name: flowchart_value.name,
                    order: study_soa_group_selection.order
                }]) AS study_soa_group
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause(sort_by.value, sort_order.value),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)


def get_study_detailed_soa(
    study_uid: str,
    sort_by: models.SortByStudyDetailedSoA = models.SortByStudyDetailedSoA.ACTIVITY_NAME,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: int = 10,
    page_number: int = 1,
    study_version_number: str | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params = {"study_uid": study_uid, "study_version_number": study_version_number}
    base_query = get_base_query_for_study_root_and_value(study_version_number)

    base_query += """
        MATCH (study_activity_schedule:StudyActivitySchedule)<-[:HAS_STUDY_ACTIVITY_SCHEDULE]-(study_value)
        MATCH (study_activity_schedule)<-[:STUDY_VISIT_HAS_SCHEDULE]-(study_visit:StudyVisit)<-[:HAS_STUDY_VISIT]-(study_value)
        MATCH (study_visit)<-[:STUDY_EPOCH_HAS_STUDY_VISIT]-(study_epoch:StudyEpoch)<-[:HAS_STUDY_EPOCH]-(study_value)
        MATCH (study_activity_schedule)<-[:STUDY_ACTIVITY_HAS_SCHEDULE]-(study_activity:StudyActivity)<-[:HAS_STUDY_ACTIVITY]-(study_value)
        WHERE NOT (study_activity)<-[:BEFORE]-()
        
        WITH
            hv,
            study_root,
            study_value,
            study_activity_schedule,
            study_visit,
            study_epoch,
            study_activity,
            head([(study_activity)-[:HAS_SELECTED_ACTIVITY]->(activity_value:ActivityValue)<-[:HAS_VERSION]-(activity_root:ActivityRoot) | {value: activity_value, uid: activity_root.uid}]) AS activity,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_SUBGROUP]->(:StudyActivitySubGroup)-[:HAS_SELECTED_ACTIVITY_SUBGROUP]->(activity_subgroup_value:ActivitySubGroupValue)<-[:HAS_VERSION]-(activity_subgroup_root:ActivitySubGroupRoot) | {value: activity_subgroup_value, uid: activity_subgroup_root.uid}]) AS activity_subgroup,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_GROUP]->(:StudyActivityGroup)-[:HAS_SELECTED_ACTIVITY_GROUP]->(activity_group_value:ActivityGroupValue)<-[:HAS_VERSION]-(activity_group_root:ActivityGroupRoot) | {value: activity_group_value, uid: activity_group_root.uid}]) AS activity_group,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_SOA_GROUP]->(:StudySoAGroup)-[:HAS_FLOWCHART_GROUP]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(term_name_value:CTTermNameValue) | term_name_value]) AS term_name_value,
            head([(study_epoch)-[:HAS_EPOCH]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]-(epoch_term:CTTermNameValue) | epoch_term.name]) AS epoch_name

        RETURN DISTINCT
            study_root.uid AS study_uid,
            study_visit.uid AS visit_uid,
            study_visit.short_visit_label AS visit_short_name,
            study_activity.uid AS study_activity_uid,
            epoch_name AS epoch_name,
            activity.uid AS activity_uid,
            activity.value.name AS activity_name,
            activity.value.nci_concept_id AS activity_nci_concept_id,
            activity.value.nci_concept_name AS activity_nci_concept_name,
            activity_subgroup.value.name AS activity_subgroup_name,
            activity_subgroup.uid AS activity_subgroup_uid,
            activity_group.value.name AS activity_group_name,
            activity_group.uid AS activity_group_uid,
            term_name_value.name AS soa_group_name,
            coalesce(activity.value.is_data_collected, False) AS is_data_collected
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause(
                sort_by.value,
                sort_order.value,
                secondary_sort_fields="visit_uid, soa_group_name, activity_group_uid, activity_subgroup_uid, activity_uid, study_activity_uid",
            ),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)


def get_study_operational_soa(
    study_uid: str,
    sort_by: models.SortByStudyOperationalSoA = models.SortByStudyOperationalSoA.ACTIVITY_NAME,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: int = 10,
    page_number: int = 1,
    study_version_number: str | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params = {"study_uid": study_uid, "study_version_number": study_version_number}
    base_query = get_base_query_for_study_root_and_value(study_version_number)

    base_query += """
        MATCH (study_activity_schedule:StudyActivitySchedule)<-[:HAS_STUDY_ACTIVITY_SCHEDULE]-(study_value)
        MATCH (study_activity_schedule)<-[:STUDY_VISIT_HAS_SCHEDULE]-(study_visit:StudyVisit)<-[:HAS_STUDY_VISIT]-(study_value)
        MATCH (study_visit)<-[:STUDY_EPOCH_HAS_STUDY_VISIT]-(study_epoch:StudyEpoch)<-[:HAS_STUDY_EPOCH]-(study_value)
        MATCH (study_activity_schedule)<-[:STUDY_ACTIVITY_HAS_SCHEDULE]-(study_activity:StudyActivity)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_INSTANCE]->(study_activity_instance:StudyActivityInstance)<-[:HAS_STUDY_ACTIVITY_INSTANCE]-(study_value)
        WHERE NOT (study_activity)<-[:BEFORE]-() AND (study_activity_instance)-[:HAS_SELECTED_ACTIVITY_INSTANCE]-()
        
        WITH
            hv,
            study_root,
            study_value,
            study_visit,
            head([(study_activity)-[:HAS_SELECTED_ACTIVITY]->(activity_value:ActivityValue)<-[:HAS_VERSION]-(activity_root:ActivityRoot) | { uid: activity_root.uid, study_activity_uid: study_activity.uid, name: activity_value.name, nci_concept_id: activity_value.nci_concept_id, nci_concept_name: activity_value.nci_concept_name }]) as activity,
            head([(study_activity_instance)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(activity_instance_value:ActivityInstanceValue)<-[:HAS_VERSION]-(activity_instance_root:ActivityInstanceRoot) | { uid: activity_instance_root.uid, name: activity_instance_value.name, topic_code: activity_instance_value.topic_code, adam_param_code: activity_instance_value.adam_param_code, nci_concept_id: activity_instance_value.nci_concept_id, nci_concept_name: activity_instance_value.nci_concept_name }]) as activity_instance,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_SUBGROUP]->(:StudyActivitySubGroup)-[:HAS_SELECTED_ACTIVITY_SUBGROUP]->(activity_subgroup_value:ActivitySubGroupValue)<-[:LATEST]-(activity_subgroup_root:ActivitySubGroupRoot) | { uid: activity_subgroup_root.uid, name: activity_subgroup_value.name }]) as activity_subgroup,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_GROUP]->(:StudyActivityGroup)-[:HAS_SELECTED_ACTIVITY_GROUP]->(activity_group_value:ActivityGroupValue)<-[:LATEST]-(activity_group_root:ActivityGroupRoot) | { uid: activity_group_root.uid, name: activity_group_value.name }]) as activity_group,
            head([(study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_SOA_GROUP]->(:StudySoAGroup)-[:HAS_FLOWCHART_GROUP]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(term_name_value:CTTermNameValue) | term_name_value]) as term_name_value,
            head([(study_epoch)-[:HAS_EPOCH]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]-(epoch_term:CTTermNameValue) | epoch_term.name]) as epoch_name

        RETURN DISTINCT
            study_root.uid AS study_uid,
            CASE study_value.study_subpart_acronym
                WHEN IS NULL THEN toUpper(COALESCE(study_value.study_id_prefix, '') + "-" + COALESCE(study_value.study_number, ''))
                ELSE toUpper(COALESCE(study_value.study_id_prefix, '') + "-" + COALESCE(study_value.study_number, '')) + "-" + study_value.study_subpart_acronym
            END AS study_id,
            study_visit.uid AS visit_uid,
            study_visit.short_visit_label AS visit_short_name,
            epoch_name AS epoch_name,
            activity.name AS activity_name,
            activity.nci_concept_id AS activity_nci_concept_id,
            activity.nci_concept_name AS activity_nci_concept_name,
            activity.uid AS activity_uid,
            activity.study_activity_uid AS study_activity_uid,
            activity_instance.name AS activity_instance_name,
            activity_instance.nci_concept_id AS activity_instance_nci_concept_id,
            activity_instance.nci_concept_name AS activity_instance_nci_concept_name,
            activity_instance.uid AS activity_instance_uid,
            activity_instance.topic_code AS topic_code,
            activity_instance.adam_param_code AS param_code,
            activity_subgroup.name AS activity_subgroup_name,
            activity_subgroup.uid AS activity_subgroup_uid,
            activity_group.name AS activity_group_name,
            activity_group.uid AS activity_group_uid,
            term_name_value.name as soa_group_name
    """
    full_query = " ".join(
        [
            base_query,
            db_sort_clause(
                sort_by.value,
                sort_order.value,
                secondary_sort_fields="visit_uid, soa_group_name, activity_group_uid, activity_subgroup_uid, activity_uid, study_activity_uid, activity_instance_uid",
            ),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)


def get_library_activities(
    sort_by: models.SortByLibraryItem = models.SortByLibraryItem.NAME,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: int = 10,
    page_number: int = 1,
    library: models.Library | None = None,
    status: models.LibraryItemStatus | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params = {
        "status": status.value if status else None,
        "library": library.value if library else None,
    }
    status_filter = "WHERE last_version_rel.status = $status " if status else ""

    base_query = (
        """
            MATCH (lib:Library {name: $library})-[:CONTAINS_CONCEPT]->(act_root:ActivityRoot)
        """
        if library
        else """
            MATCH (lib:Library)-[:CONTAINS_CONCEPT]->(act_root:ActivityRoot)
        """
    )

    base_query += f"""
        -[ver:LATEST]->(act_val:ActivityValue)
        WITH lib, act_root, act_val
        CALL {{
                WITH act_root, act_val
                MATCH (act_root)-[hv:HAS_VERSION]-(act_val)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS last_version_rel
            }}
        WITH lib, act_root, act_val, last_version_rel

        {status_filter}

        WITH lib, act_root, act_val, last_version_rel,
            apoc.coll.toSet([(act_val)-[:HAS_GROUPING]->(activity_grouping:ActivityGrouping)
             | {{
                 activity_subgroup: head(apoc.coll.sortMulti([(activity_grouping)-[:HAS_SELECTED_SUBGROUP]->(activity_subgroup_value:ActivitySubGroupValue)
                 <-[has_version:HAS_VERSION]-(activity_subgroup_root:ActivitySubGroupRoot)
                    | {{
                        uid:activity_subgroup_root.uid,
                        major_version: toInteger(split(has_version.version,'.')[0]),
                        minor_version: toInteger(split(has_version.version,'.')[1]),
                        name:activity_subgroup_value.name
                    }}], ['major_version', 'minor_version'])),
                    activity_group: head(apoc.coll.sortMulti([(activity_grouping)-[:HAS_SELECTED_GROUP]->(activity_group_value:ActivityGroupValue)
                    <-[has_version:HAS_VERSION]-(activity_group_root:ActivityGroupRoot)
                    | {{
                        uid:activity_group_root.uid,
                        major_version: toInteger(split(has_version.version,'.')[0]),
                        minor_version: toInteger(split(has_version.version,'.')[1]),
                        name:activity_group_value.name
                    }}], ['major_version', 'minor_version']))
                }}]) AS groupings

        RETURN DISTINCT
            lib.name AS library,
            act_root.uid AS uid,
            act_val.name AS name,
            groupings,
            act_val.definition AS definition,
            act_val.nci_concept_id AS nci_concept_id,
            act_val.nci_concept_name AS nci_concept_name,
            coalesce(act_val.is_data_collected, False) AS is_data_collected,
            last_version_rel.version AS version,
            last_version_rel.status AS status
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause(sort_by.value, sort_order.value),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)


def get_library_activity_instances(
    sort_by: models.SortByLibraryItem = models.SortByLibraryItem.NAME,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: int = 10,
    page_number: int = 1,
    library: models.Library | None = None,
    status: models.LibraryItemStatus | None = None,
    activity_uid: str | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params = {
        "status": status.value if status else None,
        "library": library.value if library else None,
        "activity_uid": activity_uid.strip() if activity_uid else None,
    }
    status_filter = "WHERE last_version_rel.status = $status " if status else ""
    activity_uid_filter = (
        "WHERE $activity_uid IN [activity_grouping IN activity_groupings | activity_grouping.activity.uid] "
        if activity_uid
        else ""
    )

    base_query = (
        """
            MATCH (library:Library {name: $library})-[:CONTAINS_CONCEPT]->(concept_root:ActivityInstanceRoot)-[:LATEST]->(concept_value:ActivityInstanceValue)
        """
        if library
        else """
            MATCH (library:Library)-[:CONTAINS_CONCEPT]->(concept_root:ActivityInstanceRoot)-[:LATEST]->(concept_value:ActivityInstanceValue)
        """
    )
    base_query += """
        MATCH (concept_root)-[:HAS_GROUPING_ROOT]->(grouping_root:ActivityInstanceGroupingRoot)-[:LATEST]->(grouping_value:ActivityInstanceGroupingValue)
    """

    base_query += f"""
        WITH 
            DISTINCT concept_root, concept_value, grouping_root, grouping_value, library
            CALL {{
                WITH concept_root, concept_value
                MATCH (concept_root)-[hv:HAS_VERSION]-(concept_value)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.end_date ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS last_version_rel
            }}
            CALL {{
                WITH grouping_root, grouping_value
                MATCH (grouping_root)-[hv:HAS_VERSION]-(grouping_value)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.end_date ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS last_grouping_version_rel
            }}
            WITH concept_root, concept_value, grouping_root, grouping_value, last_version_rel, last_grouping_version_rel, library

            {status_filter}

            WITH
                concept_root.uid AS uid,
                library.name AS library_name,
                last_version_rel,
                last_grouping_version_rel,
                concept_value.nci_concept_id AS nci_concept_id,
                concept_value.nci_concept_name AS nci_concept_name,
                concept_value.name AS name,
                concept_value.definition AS definition,
                last_version_rel.status AS status,
                last_version_rel.version AS version,
                last_grouping_version_rel.status AS groupings_status,
                last_grouping_version_rel.version AS groupings_version,
                concept_value.topic_code AS topic_code,
                concept_value.adam_param_code AS param_code,
                head([(concept_value)-[:ACTIVITY_INSTANCE_CLASS]->(aic_root:ActivityInstanceClassRoot) | aic_root.uid]) AS activity_instance_class_uid,
                head([(concept_value)-[:ACTIVITY_INSTANCE_CLASS]->(:ActivityInstanceClassRoot)-[:LATEST]->(aic_val:ActivityInstanceClassValue) | aic_val.name]) AS activity_instance_class_name,
                apoc.coll.toSet([(grouping_value)-[:HAS_ACTIVITY]->(activity_grouping:ActivityGrouping)
                | {{
                    activity: head(apoc.coll.sortMulti([(activity_grouping)<-[:HAS_GROUPING]-(activity_value:ActivityValue)<-[has_version:HAS_VERSION]-
                        (activity_root:ActivityRoot) |
                        {{
                            uid: activity_root.uid,
                            name: activity_value.name,
                            major_version: toInteger(split(has_version.version,'.')[0]),
                            minor_version: toInteger(split(has_version.version,'.')[1])
                        }}], ['major_version', 'minor_version'])),
                    activity_subgroup: head(apoc.coll.sortMulti([(activity_grouping)-[:HAS_SELECTED_SUBGROUP]->(activity_subgroup_value:ActivitySubGroupValue)<-[has_version:HAS_VERSION]-
                        (activity_subgroup_root:ActivitySubGroupRoot) |
                        {{
                            uid: activity_subgroup_root.uid,
                            name: activity_subgroup_value.name,
                            major_version: toInteger(split(has_version.version,'.')[0]),
                            minor_version: toInteger(split(has_version.version,'.')[1])
                        }}], ['major_version', 'minor_version'])),
                    activity_group: head(apoc.coll.sortMulti([(activity_grouping)-[:HAS_SELECTED_GROUP]->(activity_group_value:ActivityGroupValue)<-[has_version:HAS_VERSION]-
                        (activity_group_root:ActivityGroupRoot) |
                        {{
                            uid: activity_group_root.uid,
                            name: activity_group_value.name,
                            major_version: toInteger(split(has_version.version,'.')[0]),
                            minor_version: toInteger(split(has_version.version,'.')[1])
                        }}], ['major_version', 'minor_version']))
                }}]) AS activity_groupings,
                [(concept_value)-[:CONTAINS_ACTIVITY_ITEM]->(ai)
                    <-[:HAS_ACTIVITY_ITEM]-(aic_root)-[:LATEST]->(aic_val) | {{
                    activity_item_class_uid: aic_root.uid,
                    activity_item_class_name: aic_val.name,
                    data_type: head([(aic_val)-[:HAS_DATA_TYPE]->(:CTTermContext)
                        -[:HAS_SELECTED_TERM]->(:CTTermRoot)-[:HAS_NAME_ROOT]->
                        (:CTTermNameRoot)-[:LATEST]->(dtv:CTTermNameValue) | dtv.name]),
                    ct_codelist: head([(ai)-[:HAS_CODELIST]->(clr:CTCodelistRoot)
                        -[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
                        -[:LATEST]->(clav:CTCodelistAttributesValue)
                        | {{uid: clr.uid, submission_value: clav.submission_value}}]),
                    ct_terms: [(ai)-[:HAS_CT_TERM]->(:CTTermContext)-[:HAS_SELECTED_TERM]->
                        (tr:CTTermRoot)<-[:HAS_TERM_ROOT]-(:CTCodelistTerm)
                        <-[:HAS_TERM]-(clr:CTCodelistRoot)
                        | {{uid: tr.uid, codelist_uid: clr.uid}}],
                    unit_definitions: [(ai)-[:HAS_UNIT_DEFINITION]->(udr:UnitDefinitionRoot)
                        | {{uid: udr.uid}}],
                    text_value: ai.text_value,
                    is_adam_param_specific: ai.is_adam_param_specific,
                    is_activity_instance_id_specific: ai.is_activity_instance_id_specific
                }}] AS activity_items

                {activity_uid_filter}

                RETURN  uid,
                        library_name,
                        name,
                        definition,
                        nci_concept_id,
                        nci_concept_name,
                        topic_code,
                        param_code,
                        activity_instance_class_uid,
                        activity_instance_class_name,
                        activity_groupings,
                        activity_items,
                        status,
                        version,
                        groupings_status,
                        groupings_version
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause(sort_by.value, sort_order.value),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)


def get_papillons_soa(
    project: str,
    study_number: str,
    subpart: str | None = None,
    date_time: str | None = None,
    study_version_number: str | None = None,
) -> dict[str, Any]:
    if date_time:
        study_version_number = get_latest_version_from_datetime(
            project=project,
            study_number=study_number,
            date_time=date_time,
            subpart=subpart,
        )
    api_version = "v1"
    params = {
        "project": project,
        "study_number": study_number,
        "subpart": subpart,
        "study_version_number": study_version_number,
        "api_version": api_version,
        "specified_dt": date_time,
    }
    full_query = get_base_query_for_study_root_and_value_with_study_id(
        study_version_number=study_version_number, subpart=subpart
    )
    full_query += """
        MATCH (study_value)-[:HAS_STUDY_ACTIVITY]->(study_activity:StudyActivity)
        MATCH (study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_ACTIVITY_INSTANCE]->(study_activity_instance:StudyActivityInstance)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(activity_instance_value:ActivityInstanceValue)
        MATCH (study_activity)-[:STUDY_ACTIVITY_HAS_STUDY_SOA_GROUP]->(soa_group:StudySoAGroup)-[:HAS_FLOWCHART_GROUP]->(cttc:CTTermContext)-[:HAS_SELECTED_TERM]->(soa_group_term:CTTermRoot)-[:HAS_NAME_ROOT]->(cttnr:CTTermNameRoot)-[:LATEST_FINAL]->(soa_group_term_value:CTTermNameValue)
        MATCH (study_value)-[:HAS_STUDY_ACTIVITY_INSTANCE]->(study_activity_instance)
        OPTIONAL MATCH (study_activity)-[:STUDY_ACTIVITY_HAS_SCHEDULE]->(study_activity_schedule:StudyActivitySchedule)<-[:HAS_STUDY_ACTIVITY_SCHEDULE]-(study_value)
        OPTIONAL MATCH (study_value)-[:HAS_STUDY_VISIT]->(study_visit:StudyVisit)-[:STUDY_VISIT_HAS_SCHEDULE]->(study_activity_schedule)
        OPTIONAL MATCH (study_activity_instance)-[:HAS_BASELINE]->(baseline_visit:StudyVisit)
        WITH
            study_value, rel, study_activity_instance, activity_instance_value, study_visit, study_activity, study_activity_schedule, soa_group_term_value, baseline_visit
            order by toInteger(study_visit.unique_visit_number), toInteger(baseline_visit.unique_visit_number)

        WHERE NOT (study_visit)--(:Delete) AND NOT (baseline_visit)--(:Delete) AND NOT (study_activity_schedule)--(:Delete)
        AND NOT (study_activity)--(:Delete) AND NOT (study_activity_instance)--(:Delete)
        AND NOT (soa_group)-[:BEFORE]-(:StudyAction)
        WITH
            study_value, rel, study_activity_instance, activity_instance_value, soa_group_term_value,
            {topic_cd: activity_instance_value.topic_code,
            soa_grp: collect(DISTINCT soa_group_term_value.name),
            important: study_activity_instance.is_important,
            baseline_visits: collect(DISTINCT baseline_visit.unique_visit_number),
            visits: collect(DISTINCT study_visit.unique_visit_number)} AS activities

        RETURN 
        study_value.study_id_prefix             AS project,
        study_value.study_number                AS study_number,
        study_value.study_subpart_acronym       AS subpart,
        COALESCE(study_value.study_id_prefix,"") + '-' + COALESCE(study_value.study_number ,"") + COALESCE(study_value.study_subpart_acronym ,"") AS full_study_id,
        $api_version                            AS api_version,
        rel.version                             AS study_version,
        $specified_dt                           AS specified_dt,
        toString(datetime())                    AS fetch_dt,
        collect(activities)                     AS soa
        """
    res = query(full_query, params)

    NotFoundException.raise_if(
        len(res) == 0,
        msg="Study SoA not found, please ensure the study has at least one released version.",
    )
    ValidationException.raise_if(len(res) > 1, msg="Too many results.")
    return res[0]


def get_studies_audit_trail(
    from_ts: datetime,
    to_ts: datetime,
    study_id: str | None,
    entity_type: models.StudyAuditTrailEntity | None = None,
    exclude_study_ids: list[str] | None = None,
    page_number: int = 1,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(
        page_number, settings.consumer_api_audit_trail_max_rows
    )
    params = {"from_ts": from_ts.isoformat(), "to_ts": to_ts.isoformat()}

    filters = []
    if study_id:
        params["study_id"] = study_id.upper().strip()
        filters.append("toUpper(study_id) CONTAINS $study_id")

    if entity_type:
        params["entity_type"] = entity_type.value.strip()
        entity_type_filter = "$entity_type IN entity_labels"
    else:
        entity_type_filter = ""

    if exclude_study_ids:
        for idx, sid in enumerate(exclude_study_ids):
            if sid.upper().strip():
                params[f"exclude_study_ids_{idx}"] = sid.upper().strip()
                filters.append(f"NOT study_id CONTAINS $exclude_study_ids_{idx}")

    base_query = f"""
        MATCH 
            (sr:StudyRoot)-[:LATEST]->
            (sv:StudyValue)<-[:AFTER]-
            (study_sa:StudyAction)
        
        WITH DISTINCT
            sr,
            sv,
            sr.uid AS study_uid,
            CASE sv.subpart_id
                WHEN IS NULL 
                    THEN 
                        toUpper(
                            COALESCE(sv.study_id_prefix, '') 
                            + "-" 
                            + COALESCE(sv.study_number, '')
                        )
                ELSE toUpper(
                        COALESCE(sv.study_id_prefix, '') 
                        + "-" 
                        + COALESCE(sv.study_number, '')
                    ) 
                    + "-" 
                    + sv.subpart_id
            END AS study_id
            
        { 'WHERE ' + ' AND '.join(filters) if filters else ''}

        WITH distinct sr, sv, study_uid, study_id

        MATCH 
            (obj_after)<-[:AFTER]-
            (sa:StudyAction)<-[:AUDIT_TRAIL]- 
            (sr)-[:LATEST]->
            (sv)
            WHERE 
                sa.date >= datetime($from_ts) 
                AND 
                sa.date < datetime($to_ts)

        OPTIONAL MATCH 
            (sa)-[:BEFORE]->
            (obj_before)

        WITH DISTINCT
            sa.date AS ts,
            study_uid,
            study_id,
            [
                label IN labels(sa) 
                    WHERE label <> 'StudyAction'
            ][0] as action,
            obj_after.uid as entity_uid,
            labels(obj_after) as entity_labels,
            [
                key IN keys(obj_after) 
                    WHERE obj_after[key] <> obj_before[key]
            ] AS changed_properties,
            CASE WHEN sa.author_id IS NOT NULL AND sa.author_id <> ''
                THEN apoc.util.md5([sa.author_id])
                ELSE ''
            END AS author
            { 'WHERE ' + entity_type_filter if entity_type_filter else ''}

        RETURN DISTINCT
            ts,
            study_uid,
            study_id,
            action,
            entity_uid,
            apoc.text.join(entity_labels, '|') AS entity_type,
            changed_properties,
            author
        ORDER BY ts ASC
        """

    full_query = " ".join(
        [
            base_query,
            db_pagination_clause(
                settings.consumer_api_audit_trail_max_rows, page_number
            ),
        ]
    )
    return query(full_query, params)


def get_codelists(
    page_size: int = 10,
    page_number: int = 1,
    name_status: models.LibraryItemStatus | None = None,
    attributes_status: models.LibraryItemStatus | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    attributes_status_filter = (
        "last_version_rel_attributes.status = $status_attributes "
        if attributes_status
        else ""
    )

    name_status_filter = (
        "last_version_rel_name.status = $status_name " if name_status else ""
    )

    status_filter = ""

    if attributes_status_filter and name_status_filter:
        status_filter = f"WHERE {attributes_status_filter} AND {name_status_filter}"
    elif attributes_status_filter:
        status_filter = f"WHERE {attributes_status_filter}"
    elif name_status_filter:
        status_filter = f"WHERE {name_status_filter}"

    params = {
        "status_attributes": attributes_status.value if attributes_status else None,
        "status_name": name_status.value if name_status else None,
    }

    base_query = f"""
        MATCH (codelist_root:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->
        (codelist_attributes_root:CTCodelistAttributesRoot)-[:LATEST]->(codelist_attributes_value:CTCodelistAttributesValue)

        MATCH (codelist_root)-[:HAS_NAME_ROOT]->(codelist_name_root:CTCodelistNameRoot)-[:LATEST]->(codelist_name_value:CTCodelistNameValue)

        WITH
        DISTINCT codelist_root, codelist_attributes_root, codelist_attributes_value, codelist_name_root, codelist_name_value,
        head([(library:Library)-[:CONTAINS_CODELIST]->(codelist_root) | library]) AS library

        CALL {{
                WITH codelist_attributes_root, codelist_attributes_value
                MATCH (codelist_attributes_root)-[hv:HAS_VERSION]-(codelist_attributes_value)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS last_version_rel_attributes
            }}
        CALL {{
                WITH codelist_name_root, codelist_name_value
                MATCH (codelist_name_root)-[hv:HAS_VERSION]-(codelist_name_value)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS last_version_rel_name
            }}

        WITH codelist_root, codelist_attributes_value, codelist_name_value, library,
             last_version_rel_attributes, last_version_rel_name

        {status_filter}

        WITH
        codelist_root.uid AS uid,
        codelist_attributes_value.name AS name,
        codelist_attributes_value.submission_value AS submission_value,
        codelist_attributes_value.preferred_term AS nci_preferred_name,
        codelist_attributes_value.definition AS definition,
        codelist_attributes_value.extensible AS is_extensible,
        codelist_name_value.name AS sponsor_preferred_name,
        library.name AS library_name,
        last_version_rel_attributes.version AS attributes_version,
        last_version_rel_attributes.status AS attributes_status,
        last_version_rel_name.version AS name_version,
        last_version_rel_name.status AS name_status

        RETURN *
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause("name", "ASC"),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params=params)


def get_codelist_terms(
    codelist_submission_value: str | None = None,
    codelist_uid: str | None = None,
    page_size: int = 10,
    page_number: int = 1,
    name_status: models.LibraryItemStatus | None = None,
    attributes_status: models.LibraryItemStatus | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    attributes_status_filter = (
        "last_version_rel_attributes.status = $status_attributes "
        if attributes_status
        else ""
    )

    name_status_filter = (
        "last_version_rel_name.status = $status_name " if name_status else ""
    )

    status_filter = ""

    if attributes_status_filter and name_status_filter:
        status_filter = f"WHERE {attributes_status_filter} AND {name_status_filter}"
    elif attributes_status_filter:
        status_filter = f"WHERE {attributes_status_filter}"
    elif name_status_filter:
        status_filter = f"WHERE {name_status_filter}"

    params: dict[str, Any] = {
        "status_attributes": attributes_status.value if attributes_status else None,
        "status_name": name_status.value if name_status else None,
    }

    # Build the codelist match clause depending on which filters are provided
    codelist_match_lines = [
        "MATCH (codelist_root:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)-[:LATEST]->(clav:CTCodelistAttributesValue)"
    ]
    codelist_where_parts = []

    if codelist_submission_value is not None:
        codelist_where_parts.append(
            "clav.submission_value = $codelist_submission_value"
        )
        params["codelist_submission_value"] = codelist_submission_value

    if codelist_uid is not None:
        codelist_where_parts.append("codelist_root.uid = $codelist_uid")
        params["codelist_uid"] = codelist_uid

    if codelist_where_parts:
        codelist_match_lines.append("WHERE " + " AND ".join(codelist_where_parts))

    codelist_match = "\n        ".join(codelist_match_lines)

    base_query = f"""
        {codelist_match}
                            
        MATCH (codelist_root)-[ht:HAS_TERM]->(ct_cl_term:CTCodelistTerm)-[:HAS_TERM_ROOT]->(ct_term_root:CTTermRoot)<-[:CONTAINS_TERM]-(library:Library)
        WHERE ht.end_date IS NULL
        MATCH (ct_term_root)-[:HAS_NAME_ROOT]->(tnr:CTTermNameRoot)-[:LATEST]->(tnv:CTTermNameValue)
        MATCH (ct_term_root)-[:HAS_ATTRIBUTES_ROOT]->(tar:CTTermAttributesRoot)-[:LATEST]->(tav:CTTermAttributesValue)
        
        WITH 
        DISTINCT codelist_root, ht, ct_cl_term, ct_term_root, tnr, tnv, tar, tav, library

        CALL {{
                WITH tar, tav
                MATCH (tar)-[hv:HAS_VERSION]-(tav)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS last_version_rel_attributes
            }}
        CALL {{
                WITH tnr, tnv
                MATCH (tnr)-[hv:HAS_VERSION]-(tnv)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS last_version_rel_name
            }}

        WITH codelist_root, ct_term_root, ct_cl_term, ht, tnv, tav, library,
             last_version_rel_attributes, last_version_rel_name

        {status_filter}

        WITH
        codelist_root.uid as codelist_uid,
        ct_term_root.uid AS term_uid,
        codelist_root.uid + '_' + ct_term_root.uid AS sort_field,
        ht.order AS order,
        ht.ordinal AS ordinal,
        ct_cl_term.submission_value AS submission_value,
        tav.concept_id AS concept_id,
        tav.preferred_term AS nci_preferred_name,
        tnv.name AS sponsor_preferred_name,
        library.name AS library_name,
        last_version_rel_attributes.version AS attributes_version,
        last_version_rel_attributes.status AS attributes_status,
        last_version_rel_name.version AS name_version,
        last_version_rel_name.status AS name_status

        RETURN *
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause("sort_field", "ASC", secondary_sort_fields="sort_field"),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params=params)


def get_unit_definitions(
    page_size: int = 10,
    page_number: int = 1,
    subset: str | None = None,
    status: models.LibraryItemStatus | None = None,
) -> list[dict[Any, Any]]:
    validate_page_number_and_page_size(page_number, page_size)

    params: dict[str, Any] = {
        "status": status.value if status else None,
        "unit_dimension_cl_submval": settings.unit_dimension_cl_submval,
        "unit_subset_cl_submval": settings.unit_subset_cl_submval,
    }

    subset_filter = ""
    if subset:
        params["subset"] = subset
        subset_filter = """WHERE $subset IN [(concept_value)-[:HAS_UNIT_SUBSET]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)-[:HAS_NAME_ROOT]->
        (:CTTermNameRoot)-[:LATEST]->(term_name_value) | term_name_value.name]"""

    status_filter = "WHERE last_version_rel.status = $status " if status else ""

    base_query = f"""
        MATCH (concept_root:UnitDefinitionRoot)-[:LATEST]->(concept_value:UnitDefinitionValue)
        {subset_filter}

        WITH 
            DISTINCT concept_root, concept_value,
            head([(library)-[:CONTAINS_CONCEPT]->(concept_root) | library]) AS library

        CALL {{
                WITH concept_root, concept_value
                MATCH (concept_root)-[hv:HAS_VERSION]-(concept_value)
                WITH hv
                ORDER BY
                    toInteger(split(hv.version, '.')[0]) ASC,
                    toInteger(split(hv.version, '.')[1]) ASC,
                    hv.start_date ASC
                WITH collect(hv) as hvs
                RETURN last(hvs) AS last_version_rel
            }}

        WITH concept_root, concept_value, library, last_version_rel

        {status_filter}

        CALL {{
            WITH concept_value
            OPTIONAL MATCH (concept_value)-[:HAS_UNIT_SUBSET]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(sub_tr:CTTermRoot)
            OPTIONAL MATCH (sub_tr)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(sub_tnv:CTTermNameValue)
            OPTIONAL MATCH (sub_tr)<-[:HAS_TERM_ROOT]-(sub_cl_term:CTCodelistTerm)<-[:HAS_TERM]-(sub_cl_root:CTCodelistRoot)
                -[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)-[:LATEST]->(sub_cl_attrs:CTCodelistAttributesValue {{submission_value: $unit_subset_cl_submval}})
            WITH sub_tr, sub_tnv, sub_cl_term, sub_cl_root, sub_cl_attrs
            WHERE sub_tr IS NOT NULL
            RETURN COLLECT({{
                term_uid: sub_tr.uid,
                term_name: sub_tnv.name,
                term_submission_value: sub_cl_term.submission_value,
                codelist_uid: sub_cl_root.uid,
                codelist_name: sub_cl_attrs.name,
                codelist_submission_value: sub_cl_attrs.submission_value
            }}) AS subsets
        }}

        OPTIONAL MATCH (concept_value)-[:HAS_CT_DIMENSION]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(dim_tr:CTTermRoot)
        OPTIONAL MATCH (dim_tr)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->(dim_tnv:CTTermNameValue)
        OPTIONAL MATCH (dim_tr)<-[:HAS_TERM_ROOT]-(dim_cl_term:CTCodelistTerm)<-[:HAS_TERM]-(dim_cl_root:CTCodelistRoot)
            -[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)-[:LATEST]->(dim_cl_attrs:CTCodelistAttributesValue {{submission_value: $unit_dimension_cl_submval}})
        OPTIONAL MATCH (concept_value)-[:HAS_UCUM_TERM]->(ucum_root)-[:LATEST]->(ucum_val)

        WITH
            concept_root.uid AS uid,
            concept_value.nci_concept_id AS nci_concept_id,
            concept_value.nci_concept_name AS nci_concept_name,
            concept_value.name AS name,
            library.name AS library_name,
            last_version_rel.status AS status,
            last_version_rel.version AS version,
            subsets,
            concept_value.convertible_unit AS is_convertible_unit,
            concept_value.master_unit AS is_master_unit,
            concept_value.si_unit AS is_si_unit,
            concept_value.display_unit AS is_display_unit,
            concept_value.us_conventional_unit AS is_us_conventional_unit,
            concept_value.use_complex_unit_conversion AS use_complex_unit_conversion,
            concept_value.use_molecular_weight AS use_molecular_weight,
            ucum_val.name AS ucum_unit_name,
            CASE WHEN dim_tr IS NOT NULL THEN {{
                term_uid: dim_tr.uid,
                term_name: dim_tnv.name,
                term_submission_value: dim_cl_term.submission_value,
                codelist_uid: dim_cl_root.uid,
                codelist_name: dim_cl_attrs.name,
                codelist_submission_value: dim_cl_attrs.submission_value
            }} ELSE null END AS unit_dimension,
            concept_value.legacy_code AS legacy_code,
            concept_value.conversion_factor_to_master AS conversion_factor_to_master

        RETURN *
        """

    full_query = " ".join(
        [
            base_query,
            db_sort_clause("name", "ASC"),
            db_pagination_clause(page_size, page_number),
        ]
    )
    return query(full_query, params)
