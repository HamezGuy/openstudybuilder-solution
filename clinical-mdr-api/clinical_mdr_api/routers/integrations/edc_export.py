"""AccuraTrial EDC export integration router.

GET  /integrations/edc/studies/{study_uid}/study-bundle       -> StudyExchangeBundle
POST /integrations/edc/studies/{study_uid}/study-bundle/send  -> push to the EDC

The bundle is the EDC's V2 import contract (.ecrfstudy); the
GET doubles as the file-download fallback (save the JSON as study.ecrfstudy).
The push uses the EDC's ONLY machine-to-machine surface: x-api-key scoped to
forms:import-study-bundle, landing the study in the EDC's quarantine ->
review -> activate lifecycle. Configure EDC_BASE_URL and EDC_API_KEY.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, Field

from clinical_mdr_api.routers import _generic_descriptions
from clinical_mdr_api.services.integrations.edc_export import (
    EdcExportError,
    EdcExportService,
)
from common.auth import rbac
from common.auth.dependencies import security
from common.exceptions import ValidationException

# Prefixed with "/integrations/edc"
from clinical_mdr_api.routers.studies.study_access import enforce_visible_study

router = APIRouter(dependencies=[Depends(enforce_visible_study)])


class EdcSendInput(BaseModel):
    dry_run: Annotated[
        bool,
        Field(
            description="True: the EDC validates and reports its import census "
            "without creating anything. Always dry-run before a real send."
        ),
    ] = True


@router.get(
    "/studies/{study_uid}/study-bundle",
    dependencies=[security, rbac.STUDY_READ],
    summary="Export an AccuraTrial EDC V2 draft (.ecrfstudy)",
    description="The response body IS the importable file — save it as "
    "`study.ecrfstudy`. `extensions._osbExport` retains native observations, "
    "reconciliation and authority constraints. The canonical definition is "
    "preserved unchanged; this preview does not authorize release or deployment.",
    status_code=200,
    responses={
        403: _generic_descriptions.ERROR_403,
        404: _generic_descriptions.ERROR_404,
        422: {"description": "The study cannot produce a valid V2 draft or its source custody cannot be verified."},
    },
)
def get_edc_study_bundle(
    study_uid: Annotated[str, Path(description="OSB study uid")],
    study_value_version: Annotated[
        str | None, Query(description="Exact selected OSB study value version")
    ] = None,
) -> dict[str, Any]:
    try:
        return EdcExportService().build_bundle(
            study_uid, study_value_version=study_value_version
        )
    except EdcExportError as exc:
        raise ValidationException(msg=str(exc)) from exc


@router.post(
    "/studies/{study_uid}/study-bundle/send",
    dependencies=[security, rbac.STUDY_WRITE],
    summary="Push this study's bundle to the configured AccuraTrial EDC",
    description="POSTs to the EDC's `/api/forms/import-study-bundle` with the "
    "configured x-api-key. A real send lands the study in the EDC's quarantine "
    "for review/activation there; nothing goes live on the EDC unreviewed.",
    status_code=200,
    responses={
        403: _generic_descriptions.ERROR_403,
        404: _generic_descriptions.ERROR_404,
        422: {"description": "Bundle invalid, or the EDC push is not configured."},
    },
)
def send_edc_study_bundle(
    study_uid: Annotated[str, Path(description="OSB study uid")],
    send_input: Annotated[EdcSendInput, Body()],
    study_value_version: Annotated[
        str | None, Query(description="Exact selected OSB study value version")
    ] = None,
) -> dict[str, Any]:
    try:
        return EdcExportService().send_to_edc(
            study_uid, dry_run=send_input.dry_run,
            study_value_version=study_value_version,
        )
    except EdcExportError as exc:
        raise ValidationException(msg=str(exc)) from exc
