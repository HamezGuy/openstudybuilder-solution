# RESTful API endpoints used by consumers that want to extract data from OpenStudyBuilder
# pylint: disable=invalid-name
# pylint: disable=redefined-builtin
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response

from common.auth import rbac
from common.auth.dependencies import security
from common.config import settings
from common.models.error import ErrorResponse
from common.utils import BaseTimelineAR
from consumer_api.shared.common import PAGE_NUMBER_QUERY, PAGE_SIZE_QUERY
from consumer_api.shared.responses import (
    PaginatedResponse,
    PaginatedResponseWithStudyVersion,
)
from consumer_api.v1 import db as DB
from consumer_api.v1 import models

router = APIRouter()


# GET endpoint to retrieve a list of studies
@router.get(
    "/studies",
    tags=["[V1] Studies"],
    dependencies=[security, rbac.STUDY_READ],
    status_code=200,
)
def get_studies(
    request: Request,
    sort_by: models.SortByStudies = models.SortByStudies.UID,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.default_page_size,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    id: Annotated[
        str | None,
        Query(
            description="Filter by study ID (case-insensitive partial match), for example `NN1234-5678`."
        ),
    ] = None,
) -> PaginatedResponse[models.Study]:
    """
    Returns a paginated list of studies, sorted by the specified sort criteria and order.

    Each returned study contains a full list of corresponding study versions, sorted by version start date in descending order.

    Returned `version_number` value can be used in other endpoints to retrieve study entities (e.g. visits, activities, etc.)
    associated with a specific study version.

    Codelist details can be retrieved from the `GET /v1/library/ct/codelists` endpoint.

    Details related to the Data Supplier type can be retrieved from the `GET /v1/library/ct/codelist-terms?codelist_submission_value=DATA_SUPPLIER_TYPE` endpoint.
    """
    studies = DB.get_studies(
        sort_by=sort_by,
        sort_order=sort_order,
        page_size=page_size,
        page_number=page_number,
        id=id,
    )

    return PaginatedResponse.from_input(
        request=request,
        sort_by=sort_by.value,
        sort_order=sort_order.value,
        page_size=page_size,
        page_number=page_number,
        items=[models.Study.from_input(study) for study in studies],
        query_param_names=["id"],
    )


# GET endpoint to retrieve a study's visits
@router.get(
    "/studies/{uid}/study-visits",
    tags=["[V1] Studies"],
    dependencies=[security, rbac.STUDY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_study_visits(
    request: Request,
    uid: Annotated[str, Path(description="Study UID")],
    sort_by: models.SortByStudyVisits = models.SortByStudyVisits.UNIQUE_VISIT_NUMBER,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    study_version_number: Annotated[
        str | None,
        Query(
            description="Study Version Number",
            openapi_examples={"2.1": {"value": "2.1"}},
        ),
    ] = None,
) -> PaginatedResponseWithStudyVersion[models.StudyVisit]:
    """
    Returns a paginated list of study visits, sorted by the specified sort criteria and order.

    If `study_version_number` query parameter is provided, study visits
    associated with the specified study version will be returned.
    Otherwise, visits for the latest study version will be returned.
    """
    study_version = DB.get_study_version(
        study_uid=uid,
        study_version_number=study_version_number,
    )

    # We need to retrieve all study visits to correctly assign `visit_order` and `visit_number` to each visit after timeline generation
    study_visits_all = DB.get_study_visits(
        study_uid=uid,
        sort_by=sort_by,
        sort_order=sort_order,
        page_size=settings.max_page_size,
        page_number=1,
        study_version_number=study_version_number,
    )
    items_all = [
        models.StudyVisit.from_input(study_visit) for study_visit in study_visits_all
    ]

    # Generate timeline to assign `visit_order` and `visit_number` to all study visits
    BaseTimelineAR(study_uid=uid, _visits=items_all)._generate_timeline()

    # Return the requested page of study visits
    items = items_all[(page_number - 1) * page_size : page_number * page_size]
    return PaginatedResponseWithStudyVersion.from_input(
        request=request,
        study_version=study_version,
        sort_by=sort_by.value,
        sort_order=sort_order.value,
        page_size=page_size,
        page_number=page_number,
        items=items,
        query_param_names=["study_version_number"],
    )


# GET endpoint to retrieve a study's activities
@router.get(
    "/studies/{uid}/study-activities",
    tags=["[V1] Studies"],
    dependencies=[security, rbac.STUDY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_study_activities(
    request: Request,
    uid: Annotated[str, Path(description="Study UID")],
    sort_by: models.SortByStudyActivities = models.SortByStudyActivities.UID,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    study_version_number: Annotated[
        str | None,
        Query(
            description="Study Version Number",
            openapi_examples={"2.1": {"value": "2.1"}},
        ),
    ] = None,
) -> PaginatedResponseWithStudyVersion[models.StudyActivity]:
    """
    Returns a paginated list of study activities, sorted by the specified sort criteria and order.

    If `study_version_number` query parameter is provided, study activities
    associated with the specified study version will be returned.
    Otherwise, activities for the latest study version will be returned.
    """
    study_version = DB.get_study_version(
        study_uid=uid,
        study_version_number=study_version_number,
    )

    study_activities = DB.get_study_activities(
        study_uid=uid,
        sort_by=sort_by,
        sort_order=sort_order,
        page_size=page_size,
        page_number=page_number,
        study_version_number=study_version_number,
    )

    return PaginatedResponseWithStudyVersion.from_input(
        request=request,
        study_version=study_version,
        sort_by=sort_by.value,
        sort_order=sort_order.value,
        page_size=page_size,
        page_number=page_number,
        items=[
            models.StudyActivity.from_input(study_activity)
            for study_activity in study_activities
        ],
        query_param_names=["study_version_number"],
    )


# GET endpoint to retrieve a study's activity instances
@router.get(
    "/studies/{uid}/study-activity-instances",
    tags=["[V1] Studies"],
    dependencies=[security, rbac.STUDY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_study_activity_instances(
    request: Request,
    uid: Annotated[str, Path(description="Study UID")],
    sort_by: models.SortByStudyActivityInstances = models.SortByStudyActivityInstances.UID,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    study_version_number: Annotated[
        str | None,
        Query(
            description="Study Version Number",
            openapi_examples={"2.1": {"value": "2.1"}},
        ),
    ] = None,
) -> PaginatedResponseWithStudyVersion[models.StudyActivityInstance]:
    """
    Returns a paginated list of study activity instances, sorted by the specified sort criteria and order.

    If `study_version_number` query parameter is provided, study activity instances
    associated with the specified study version will be returned.
    Otherwise, activity instances for the latest study version will be returned.
    """
    study_version = DB.get_study_version(
        study_uid=uid,
        study_version_number=study_version_number,
    )

    study_activity_instances = DB.get_study_activity_instances(
        study_uid=uid,
        sort_by=sort_by,
        sort_order=sort_order,
        page_size=page_size,
        page_number=page_number,
        study_version_number=study_version_number,
    )

    return PaginatedResponseWithStudyVersion.from_input(
        request=request,
        study_version=study_version,
        sort_by=sort_by.value,
        sort_order=sort_order.value,
        page_size=page_size,
        page_number=page_number,
        items=[
            models.StudyActivityInstance.from_input(study_activity_instance)
            for study_activity_instance in study_activity_instances
        ],
        query_param_names=["study_version_number"],
    )


# GET endpoint to retrieve a study's detailed soa
@router.get(
    "/studies/{uid}/detailed-soa",
    tags=["[V1] Studies"],
    dependencies=[security, rbac.STUDY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_study_detailed_soa(
    request: Request,
    uid: Annotated[str, Path(description="Study UID")],
    sort_by: models.SortByStudyDetailedSoA = models.SortByStudyDetailedSoA.ACTIVITY_NAME,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    study_version_number: Annotated[
        str | None,
        Query(
            description="Study Version Number",
            openapi_examples={"2.1": {"value": "2.1"}},
        ),
    ] = None,
) -> PaginatedResponseWithStudyVersion[models.StudyDetailedSoA]:
    """
    Returns a paginated list of detailed SoA items representing a point in the activities/visits matrix.
    SoA items are sorted by the specified sort criteria and order.

    If `study_version_number` query parameter is provided, detailed SoA
    associated with the specified study version will be returned.
    Otherwise, detailed SoA items for the latest study version will be returned.
    """
    study_version = DB.get_study_version(
        study_uid=uid,
        study_version_number=study_version_number,
    )

    study_detailed_soas = DB.get_study_detailed_soa(
        study_uid=uid,
        sort_by=sort_by,
        sort_order=sort_order,
        page_size=page_size,
        page_number=page_number,
        study_version_number=study_version_number,
    )

    return PaginatedResponseWithStudyVersion.from_input(
        request=request,
        study_version=study_version,
        sort_by=sort_by.value,
        sort_order=sort_order.value,
        page_size=page_size,
        page_number=page_number,
        items=[
            models.StudyDetailedSoA.from_input(study_detailed_soa)
            for study_detailed_soa in study_detailed_soas
        ],
        query_param_names=["study_version_number"],
    )


# GET endpoint to retrieve a study's operational soa
@router.get(
    "/studies/{uid}/operational-soa",
    tags=["[V1] Studies"],
    dependencies=[security, rbac.STUDY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_study_operational_soa(
    request: Request,
    uid: Annotated[str, Path(description="Study UID")],
    sort_by: models.SortByStudyOperationalSoA = models.SortByStudyOperationalSoA.ACTIVITY_NAME,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    study_version_number: Annotated[
        str | None,
        Query(
            description="Study Version Number",
            openapi_examples={"2.1": {"value": "2.1"}},
        ),
    ] = None,
) -> PaginatedResponseWithStudyVersion[models.StudyOperationalSoA]:
    """
    Returns a paginated list of operational SoA items representing a point in the activities/visits matrix.
    SoA items are sorted by the specified sort criteria and order.

    If `study_version_number` query parameter is provided, operational SoA
    associated with the specified study version will be returned.
    Otherwise, operational SoA items for the latest study version will be returned.
    """
    study_version = DB.get_study_version(
        study_uid=uid,
        study_version_number=study_version_number,
    )

    study_operational_soas = DB.get_study_operational_soa(
        study_uid=uid,
        sort_by=sort_by,
        sort_order=sort_order,
        page_size=page_size,
        page_number=page_number,
        study_version_number=study_version_number,
    )

    return PaginatedResponseWithStudyVersion.from_input(
        request=request,
        study_version=study_version,
        sort_by=sort_by.value,
        sort_order=sort_order.value,
        page_size=page_size,
        page_number=page_number,
        items=[
            models.StudyOperationalSoA.from_input(study_operational_soa)
            for study_operational_soa in study_operational_soas
        ],
        query_param_names=["study_version_number"],
    )


# GET endpoint to retrieve a library of activities
@router.get(
    "/library/activities",
    tags=["[V1] Library"],
    dependencies=[security, rbac.LIBRARY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_library_activities(
    request: Request,
    sort_by: Annotated[
        models.SortByLibraryItem, Query()
    ] = models.SortByLibraryItem.NAME,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    library: models.Library | None = None,
    status: models.LibraryItemStatus | None = None,
) -> PaginatedResponse[models.LibraryActivity]:
    """
    Returns a paginated list of library activities, sorted by the specified sort field and order.

    Activities can be filtered by  `library` (_Sponsor, Requested_) and/or `status` (_Final, Draft, Retired_).
    """

    library_activities = DB.get_library_activities(
        sort_by=sort_by,
        sort_order=sort_order,
        page_size=page_size,
        page_number=page_number,
        library=library,
        status=status,
    )

    return PaginatedResponse.from_input(
        request=request,
        sort_by=sort_by.value,
        sort_order=sort_order.value,
        page_size=page_size,
        page_number=page_number,
        items=[
            models.LibraryActivity.from_input(library_activity)
            for library_activity in library_activities
        ],
        query_param_names=["status", "library"],
    )


# GET endpoint to retrieve a library of activity instances
@router.get(
    "/library/activity-instances",
    tags=["[V1] Library"],
    dependencies=[security, rbac.LIBRARY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_library_activity_instances(
    request: Request,
    sort_by: Annotated[
        models.SortByLibraryItem, Query()
    ] = models.SortByLibraryItem.NAME,
    sort_order: models.SortOrder = models.SortOrder.ASC,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    library: models.Library | None = None,
    status: models.LibraryItemStatus | None = None,
    activity_uid: Annotated[
        str | None, Query(description="Filter by activity UID")
    ] = None,
) -> PaginatedResponse[models.LibraryActivityInstance]:
    """
    Returns a paginated list of library activity instances, sorted by the specified sort field and order.

    Activity instances can be filtered by:
      - **library**: Sponsor, Requested
      - **status**: Final, Draft, Retired
      - **activity_uid**: case-sensitive match, for example 'Activity_000251'
    """

    library_activity_instances = DB.get_library_activity_instances(
        sort_by=sort_by,
        sort_order=sort_order,
        page_size=page_size,
        page_number=page_number,
        library=library,
        status=status,
        activity_uid=activity_uid,
    )

    return PaginatedResponse.from_input(
        request=request,
        sort_by=sort_by.value,
        sort_order=sort_order.value,
        page_size=page_size,
        page_number=page_number,
        items=[
            models.LibraryActivityInstance.from_input(library_activity_instance)
            for library_activity_instance in library_activity_instances
        ],
        query_param_names=["status", "library", "activity_uid"],
    )


# GET endpoint to retrieve a study's soa in papillons required structure
@router.get(
    "/papillons/soa",
    tags=["[V1] Papillons"],
    dependencies=[security, rbac.STUDY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_papillons_soa(
    project: Annotated[str, Query(description="Project")],
    study_number: Annotated[str, Query(description="Study Number")],
    subpart: Annotated[
        str | None,
        Query(
            description="Study Subpart Identifier, for example `SAD, MAD, EXT, etc..`"
        ),
    ] = None,
    study_version_number: Annotated[
        str | None, Query(description="Study Version Number, for example `2.1`")
    ] = None,
    date_time: Annotated[
        str | None,
        Query(
            alias="datetime",
            description="If specified, study data with latest released version of specified datetime is returned. "
            "format in YYYY-MM-DDThh:mm:ssZ. ",
        ),
    ] = None,
) -> models.PapillonsSoA:
    papilons_soa_res = DB.get_papillons_soa(
        project=project,
        study_number=study_number,
        subpart=subpart,
        date_time=date_time,
        study_version_number=study_version_number,
    )

    return models.PapillonsSoA.from_input(papilons_soa_res)


# GET endpoint to retrieve study audit trail
@router.get(
    "/studies/audit-trail",
    tags=["[V1] Audit trail"],
    dependencies=[security, rbac.STUDY_READ],
    status_code=200,
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "CSV of study audit trail data",
        },
    },
)
# pylint: disable=dangerous-default-value
def get_studies_audit_trail(
    from_ts: Annotated[
        datetime,
        Query(
            description="Start timestamp in ISO format with timezone, e.g. 2024-01-01T00:00:00Z"
        ),
    ] = datetime.fromisoformat("2024-01-01T00:00:00Z"),
    to_ts: Annotated[
        datetime,
        Query(
            description="End timestamp in ISO format with timezone, e.g. 2024-01-05T00:00:00Z"
        ),
    ] = datetime.fromisoformat("2024-01-05T00:00:00Z"),
    study_id: Annotated[
        str | None,
        Query(
            description="Filter by study ID (case-insensitive partial match), for example `NN1234-5678`."
        ),
    ] = None,
    entity_type: Annotated[
        models.StudyAuditTrailEntity | None,
        Query(description="Filter by entity type, for example `StudyActivity`."),
    ] = None,
    exclude_study_ids: Annotated[
        list[str] | None,
        Query(
            description="List of study IDs to exclude (case-insensitive partial match), for example `CDISC DEV`."
        ),
    ] = ["CDISC DEV"],
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
) -> Response:
    """
    Returns study audit trail entries between `from_ts` timestamp (including) and `to_ts` timestamp (excluding).

    The audit trail is returned in CSV format with the following columns:
      - **ts**: Timestamp of the action
      - **study_uid**: Study UID
      - **study_id**: Study ID
      - **action**: Action performed (Create, Edit, Delete)
      - **entity_uid**: UID of the entity affected by the action
      - **entity_type**: Type (i.e node labels) of the entity affected by the action (*StudyVisit*, *StudyActivity*, etc..). Multiple labels are separated by '**|**' character.
      - **changed_properties**: List of properties that were changed during the Edit action
      - **author**: Hashed (MD5) value of the ID of a user that performed the action

    Audit trail can be filtered by:
      - `study_id` - returns study audit trail entries for the specified study ID (case-insensitive partial match)
      - `entity_type` - returns study audit trail entries for the specified entity type (e.g. *StudyActivity*)
      - `exclude_study_ids` - returns audit trail without the specified study IDs (case-insensitive partial match)

    Note: the maximum number of rows returned is limited to 10.000.
    """

    audit_trail = DB.get_studies_audit_trail(
        from_ts=from_ts,
        to_ts=to_ts,
        study_id=study_id,
        entity_type=entity_type,
        exclude_study_ids=exclude_study_ids,
        page_number=page_number,
    )

    # Convert audit trail to CSV format
    keys = [
        "ts",
        "study_uid",
        "study_id",
        "action",
        "entity_uid",
        "entity_type",
        "changed_properties",
        "author",
    ]
    csv_output = ",".join(keys) + "\n"
    for entry in audit_trail:
        # None values are returned as empty string
        # lists are returned as val1|val2
        for key in keys:
            if entry[key] is None:
                entry[key] = ""
            elif isinstance(entry[key], list):
                entry[key] = "|".join(entry[key])

        csv_output += ",".join(str(entry[key]) for key in keys)
        csv_output += "\n"

    return Response(content=csv_output, media_type="text/csv")


# GET endpoint to retrieve a list of codelists
@router.get(
    "/library/ct/codelists",
    tags=["[V1] Library"],
    dependencies=[security, rbac.LIBRARY_READ],
    status_code=200,
)
def get_codelists(
    request: Request,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    name_status: models.LibraryItemStatus | None = models.LibraryItemStatus.FINAL,
    attributes_status: models.LibraryItemStatus | None = models.LibraryItemStatus.FINAL,
) -> PaginatedResponse[models.Codelist]:
    """
    Returns a paginated list of CT codelists, sorted by ascending name.

    Codelists can be filtered by `name_status` and `attributes_status` (_Final, Draft, Retired_). Both default to _Final_.
    """
    codelists = DB.get_codelists(
        page_size=page_size,
        page_number=page_number,
        name_status=name_status,
        attributes_status=attributes_status,
    )

    return PaginatedResponse.from_input(
        request=request,
        sort_by=None,
        sort_order=None,
        page_size=page_size,
        page_number=page_number,
        items=[models.Codelist.from_input(codelist) for codelist in codelists],
        query_param_names=["name_status", "attributes_status"],
    )


# GET endpoint /library/ct/codelist-terms that returns list of codelist terms, optionally filtered by codelist submission value and/or codelist UID
@router.get(
    "/library/ct/codelist-terms",
    tags=["[V1] Library"],
    dependencies=[security, rbac.LIBRARY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_codelist_terms(
    request: Request,
    codelist_submission_value: Annotated[
        str | None,
        Query(
            description="Codelist submission value to filter by, for example `TIMELB`, `TIMEREF`, `VISCNTMD`, `FLWCRTGRP`, `EPOCHSTP` etc."
        ),
    ] = None,
    codelist_uid: Annotated[
        str | None,
        Query(description="Codelist UID to filter by."),
    ] = None,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    name_status: models.LibraryItemStatus | None = models.LibraryItemStatus.FINAL,
    attributes_status: models.LibraryItemStatus | None = models.LibraryItemStatus.FINAL,
) -> PaginatedResponse[models.CodelistTerm]:
    """
    Returns a paginated list of CT codelist terms, sorted by ascending by codelist UID, then term UID.

    If neither `codelist_submission_value` nor `codelist_uid` is provided, all terms are returned.
    If either is provided, terms are filtered accordingly.

    Terms can also be filtered by `name_status` and `attributes_status` (_Final, Draft, Retired_). Both default to _Final_.
    """
    codelist_terms = DB.get_codelist_terms(
        codelist_submission_value=codelist_submission_value,
        codelist_uid=codelist_uid,
        page_size=page_size,
        page_number=page_number,
        name_status=name_status,
        attributes_status=attributes_status,
    )

    return PaginatedResponse.from_input(
        request=request,
        sort_by=None,
        sort_order=None,
        page_size=page_size,
        page_number=page_number,
        items=[
            models.CodelistTerm.from_input(codelist_term)
            for codelist_term in codelist_terms
        ],
        query_param_names=[
            "codelist_submission_value",
            "codelist_uid",
            "name_status",
            "attributes_status",
        ],
    )


# GET endpoint /library/unit-definitions?subset=XYZ that returns list of unit definitions for the specified subset
@router.get(
    "/library/unit-definitions",
    tags=["[V1] Library"],
    dependencies=[security, rbac.LIBRARY_READ],
    status_code=200,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Item not found",
        },
    },
)
def get_unit_definitions(
    request: Request,
    subset: Annotated[
        str | None,
        Query(
            description="Unit definition subset to filter by, for example `Study Time`. If omitted, all unit definitions are returned."
        ),
    ] = None,
    page_size: Annotated[int, PAGE_SIZE_QUERY] = settings.page_size_100,
    page_number: Annotated[
        int, PAGE_NUMBER_QUERY
    ] = settings.default_page_number_consumer_api,
    status: models.LibraryItemStatus | None = models.LibraryItemStatus.FINAL,
) -> PaginatedResponse[models.UnitDefinition]:
    """
    Returns a paginated list of unit definitions, sorted by ascending name.

    Unit definitions can optionally be filtered by `subset` name (e.g. `Study Time`).
    Unit definitions can be filtered by `status` (_Final, Draft, Retired_). Defaults to _Final_.
    """
    unit_definitions = DB.get_unit_definitions(
        subset=subset,
        page_size=page_size,
        page_number=page_number,
        status=status,
    )

    return PaginatedResponse.from_input(
        request=request,
        sort_by=None,
        sort_order=None,
        page_size=page_size,
        page_number=page_number,
        items=[
            models.UnitDefinition.from_input(unit_definition)
            for unit_definition in unit_definitions
        ],
        query_param_names=["subset", "status"],
    )
