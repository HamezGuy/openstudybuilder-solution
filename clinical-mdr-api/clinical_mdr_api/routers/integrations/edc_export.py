"""AccuraTrial EDC export integration router.

GET  /integrations/edc/studies/{study_uid}/study-bundle       -> StudyBundleV1
POST /integrations/edc/studies/{study_uid}/study-bundle/send  -> push to the EDC

The bundle is the EDC's own import contract (.ecrfstudy / StudyBundleV1); the
GET doubles as the file-download fallback (save the JSON as study.ecrfstudy).
The push uses the EDC's ONLY machine-to-machine surface: x-api-key scoped to
forms:import-study-bundle, landing the study in the EDC's quarantine ->
review -> activate lifecycle. Configure EDC_BASE_URL and EDC_API_KEY.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Path
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
router = APIRouter()


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
    summary="Project this study into an AccuraTrial EDC StudyBundleV1 (.ecrfstudy)",
    description="The response body IS the importable file — save it as "
    "`study.ecrfstudy`. `_exportCensus` names every field-type downgrade and "
    "ambiguous join; an empty census means the projection was exact.",
    status_code=200,
    responses={
        403: _generic_descriptions.ERROR_403,
        404: _generic_descriptions.ERROR_404,
        422: {"description": "The study cannot produce a valid bundle (e.g. no forms)."},
    },
)
def get_edc_study_bundle(
    study_uid: Annotated[str, Path(description="OSB study uid")],
) -> dict[str, Any]:
    try:
        return EdcExportService().build_bundle(study_uid)
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
) -> dict[str, Any]:
    try:
        return EdcExportService().send_to_edc(study_uid, dry_run=send_input.dry_run)
    except EdcExportError as exc:
        raise ValidationException(msg=str(exc)) from exc
