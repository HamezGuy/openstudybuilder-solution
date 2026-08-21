"""OpenStudyBuilder-authoritative study-definition integration endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from clinical_mdr_api.models.integrations.study_authority import (
    StudyAuthoritySnapshot,
)
from clinical_mdr_api.routers import _generic_descriptions
from clinical_mdr_api.services.integrations.study_authority import (
    StudyAuthorityService,
)
from common.auth import rbac
from common.auth.dependencies import security

from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

router = APIRouter(dependencies=[Depends(enforce_visible_study)])


@router.get(
    "/studies/{study_uid}/snapshot",
    dependencies=[security, rbac.STUDY_READ],
    response_model=StudyAuthoritySnapshot,
    response_model_exclude_none=False,
    summary="Return the authoritative OSB study-definition and USDM snapshot",
    description=(
        "Returns native OpenStudyBuilder study setup, objectives, endpoints, "
        "eligibility criteria, selected CT packages, integrity results, and "
        "OpenStudyBuilder's own CDISC USDM v4 projection under one content hash. "
        "Release blockers make native-to-USDM losses explicit. This endpoint "
        "does not read the legacy x360i source bundle."
    ),
    status_code=200,
    responses={
        403: _generic_descriptions.ERROR_403,
        404: _generic_descriptions.ERROR_404,
    },
)
def get_study_authority_snapshot(
    study_uid: Annotated[str, Path(description="The unique OSB study uid")],
    study_value_version: Annotated[
        str | None,
        Query(description="Optional explicit OSB study value version"),
    ] = None,
) -> StudyAuthoritySnapshot:
    return StudyAuthorityService().get_snapshot(
        study_uid=study_uid,
        study_value_version=study_value_version,
    )
