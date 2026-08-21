"""OSB-owned Proposal V2 intake and item-level mapping review endpoints."""

from typing import Annotated

from fastapi import APIRouter, Body, Path, Request

from clinical_mdr_api.models.integrations.proposal_review import (
    ProposalExecutionAuthorizationInput,
    ProposalObjectDecisionInput,
    ProposalReviewIntake,
    ProposalReviewStatus,
)
from clinical_mdr_api.routers.integrations.mapping_context import canonical_openapi_hash
from clinical_mdr_api.services.integrations.proposal_review import (
    ProposalReviewPrincipal,
    ProposalReviewService,
)
from clinical_mdr_api.services.studies.study_visibility import (
    assert_study_uid_visible,
)
from common.auth import rbac
from common.auth.dependencies import security
from common.auth.user import auth
from common.config import settings

router = APIRouter()


def _review_principal() -> ProposalReviewPrincipal:
    authenticated = auth()
    claims = authenticated.access_token_claims
    return ProposalReviewPrincipal(
        actor_id=authenticated.user.id(),
        human_user_id=claims.oid or "",
        token_id=claims.sid or claims.jti or claims.uti or "",
        tenant_id=claims.tenant_id or "",
        scoped_study_ids=frozenset(str(value) for value in claims.study_ids),
        organization_ids=frozenset(
            str(value) for value in claims.organization_ids
        ),
        roles=frozenset(claims.roles or set()),
        authentication_verified=authenticated.authentication_verified,
        purpose=claims.purpose or "",
        capabilities=frozenset(claims.capabilities or []),
        enforce_delegated_scope=settings.delegated_claims_required,
        development_access=(
            not settings.oauth_enabled
            and settings.deployment_environment.strip().lower() == "development"
        ),
    )


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
        principal=_review_principal(),
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
    return ProposalReviewService().get_status(
        proposal_hash,
        principal=_review_principal(),
    )


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
        principal=_review_principal(),
    )


@router.post(
    "/{proposal_hash}/execution-authorizations",
    dependencies=[security, rbac.STUDY_WRITE],
    response_model=ProposalReviewStatus,
    response_model_exclude_none=False,
    status_code=201,
    summary="Authorize one reviewed proposal for an exact owned DRAFT study version",
)
def authorize_proposal_execution(
    proposal_hash: Annotated[str, Path(min_length=64, max_length=64)],
    authorization: Annotated[ProposalExecutionAuthorizationInput, Body()],
) -> ProposalReviewStatus:
    assert_study_uid_visible(
        authorization.target_study_uid,
        require_write=True,
    )
    return ProposalReviewService().authorize_execution(
        proposal_hash,
        authorization,
        principal=_review_principal(),
    )
