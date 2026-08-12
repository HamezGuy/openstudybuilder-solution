"""OSB-owned Proposal V2 intake and item-level mapping review endpoints."""

from typing import Annotated

from fastapi import APIRouter, Body, Path, Request

from clinical_mdr_api.models.integrations.proposal_review import (
    ProposalObjectDecisionInput,
    ProposalReviewIntake,
    ProposalReviewStatus,
)
from clinical_mdr_api.routers.integrations.mapping_context import canonical_openapi_hash
from clinical_mdr_api.services.integrations.proposal_review import ProposalReviewService
from common.auth import rbac
from common.auth.dependencies import security
from common.auth.user import user

router = APIRouter()


@router.post(
    "",
    dependencies=[security, rbac.STUDY_WRITE],
    response_model=ProposalReviewStatus,
    response_model_exclude_none=False,
    status_code=202,
    summary="Accept a validated Proposal V2 into OSB's mapping-review inbox",
)
def intake_proposal_review(
    intake: Annotated[ProposalReviewIntake, Body()],
    request: Request,
) -> ProposalReviewStatus:
    return ProposalReviewService().intake(
        intake,
        live_openapi_hash=canonical_openapi_hash(request.app.openapi()),
    )


@router.get(
    "/{proposal_hash}",
    dependencies=[security, rbac.STUDY_READ],
    response_model=ProposalReviewStatus,
    response_model_exclude_none=False,
    summary="Return OSB-owned item-level review status for one proposal",
)
def get_proposal_review(
    proposal_hash: Annotated[str, Path(min_length=64, max_length=64)],
) -> ProposalReviewStatus:
    return ProposalReviewService().get_status(proposal_hash)


@router.post(
    "/{proposal_hash}/objects/{proposal_object_id}/decisions",
    dependencies=[security, rbac.STUDY_WRITE],
    response_model=ProposalReviewStatus,
    response_model_exclude_none=False,
    status_code=201,
    summary="Append an OSB-owned reviewer decision for one proposal object",
)
def decide_proposal_object(
    proposal_hash: Annotated[str, Path(min_length=64, max_length=64)],
    proposal_object_id: Annotated[str, Path(min_length=1)],
    decision: Annotated[ProposalObjectDecisionInput, Body()],
) -> ProposalReviewStatus:
    return ProposalReviewService().decide(
        proposal_hash,
        proposal_object_id,
        decision,
        actor_id=user().id(),
    )
