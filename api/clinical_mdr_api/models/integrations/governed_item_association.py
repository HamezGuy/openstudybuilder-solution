"""An explicit native human association review, distinct from the earlier decision."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field
from clinical_mdr_api.models.integrations.selected_activity_item_observation import SelectedActivityItemRequest

Hash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Hash64 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Instant = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")]
UUID = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CslPlanAssociationPins(Closed):
    nativeTenantId: UUID
    nativeStudyId: UUID
    canonicalRevisionId: UUID
    canonicalRevisionHash: Hash
    planHash: Hash
    reviewCommitmentHash: Hash


class GovernedItemAssociationSelector(Closed):
    contractVersion: Literal["OsbGovernedItemAssociationSelectorV1@1.0.0"]
    proposalHash: Hash64
    proposalObjectId: Hash64
    reviewDecisionId: UUID
    reviewDecisionHash: Hash64
    candidateKey: Hash64
    selected: SelectedActivityItemRequest
    selectedPathHash: Hash
    libraryItemHash: Hash
    operationHash: Hash
    csl: CslPlanAssociationPins


class GovernedItemAssociationReview(Closed):
    contractVersion: Literal["OsbGovernedItemAssociationReviewV1@1.0.0"]
    associationId: UUID
    selector: GovernedItemAssociationSelector
    signatureId: Annotated[str, Field(min_length=1, max_length=512)]
    displayedStatement: Literal["I reviewed this exact proposal decision, CSL plan and native selected Item association."]


class GovernedItemAssociationRead(Closed):
    contractVersion: Literal["OsbGovernedItemAssociationReadV1@1.0.0"]
    associationId: UUID
    associationHash: Hash
    selector: GovernedItemAssociationSelector


Text = Annotated[str, Field(min_length=1, max_length=512)]


class NativeAssociationReviewAssurance(Closed):
    assurance: Literal["native-session-review"]
    action: Literal["governed-item-association:review"]
    issuer: Text
    subject: Text
    humanSubject: Text
    tenantId: Text
    roles: list[Text] = Field(max_length=256)
    studyIds: list[Text] = Field(max_length=4096)
    capabilities: list[Text] = Field(max_length=256)
    purpose: Literal["interactive-domain-access", "workflow-orchestration"]
    sessionHash: Hash64
    credentialIssuedAt: int
    credentialExpiresAt: int
    reviewedAt: Instant
    displayedStatement: Literal["I reviewed this exact proposal decision, CSL plan and native selected Item association."]


class GovernedItemAssociationRecord(Closed):
    contractVersion: Literal["OsbGovernedItemAssociationV1@1.0.0"]
    associationId: UUID
    nativeTenantId: Text
    selector: GovernedItemAssociationSelector
    review: NativeAssociationReviewAssurance
    sourceObservedAt: Instant
    cslPlanCustodyVerified: Literal[False]
    clinicalApprovalVerified: Literal[False]
    detachedSignatureVerified: Literal[False]


class GovernedItemAssociationResponse(Closed):
    contractVersion: Literal["OsbGovernedItemAssociationObservationV1@1.0.0"]
    associationId: UUID
    associationHash: Hash
    nativeTenantId: Annotated[str, Field(min_length=1, max_length=128)]
    selector: GovernedItemAssociationSelector
    assurance: Literal["native-session-review"]
    associationReviewAction: Literal["governed-item-association:review"]
    currentOriginalDecisionVerified: Literal[True]
    selectedActivityReachabilityVerified: Literal[True]
    cslPlanCustodyVerified: Literal[False]
    clinicalApprovalVerified: Literal[False]
    detachedSignatureVerified: Literal[False]
    observedAt: Instant
    authorityCheckedAt: Instant
    expiresAt: Instant
