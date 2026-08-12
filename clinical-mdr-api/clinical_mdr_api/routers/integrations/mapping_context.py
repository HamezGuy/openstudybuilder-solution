"""Bounded live OSB mapping context for constrained Proposal V2 retrieval."""

from fastapi import APIRouter, Request

from clinical_mdr_api.models.integrations.mapping_context import (
    MappingContextRequest,
    MappingContextResponse,
    MappingContextV2Request,
    MappingContextV2Response,
)
from clinical_mdr_api.services.integrations.mapping_context import MappingContextService
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from common.auth import rbac
from common.auth.dependencies import security

router = APIRouter()


def canonical_openapi_hash(openapi: dict) -> str:
    return canonical_hash(openapi)


@router.post(
    "/contexts",
    dependencies=[security, rbac.STUDY_READ],
    response_model=MappingContextResponse,
    response_model_exclude_none=False,
    summary="Return a bounded, versioned OSB mapping candidate context",
)
def create_mapping_context(
    body: MappingContextRequest,
    request: Request,
) -> MappingContextResponse:
    return MappingContextService().get_context(
        body,
        osb_openapi_hash=canonical_openapi_hash(request.app.openapi()),
    )


@router.post(
    "/contexts/v2",
    dependencies=[security, rbac.STUDY_WRITE],
    response_model=MappingContextV2Response,
    response_model_exclude_none=False,
    summary="Persist bounded per-concept Mapping Context V2 candidate groups",
)
def create_mapping_context_v2(
    body: MappingContextV2Request,
    request: Request,
) -> MappingContextV2Response:
    return MappingContextService().get_context_v2(
        body,
        osb_openapi_hash=canonical_openapi_hash(request.app.openapi()),
    )
