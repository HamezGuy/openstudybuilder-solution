"""AUTO-GENERATED from CSL-owned StudyMappingDecisionV1.
Schema sha256:fdb7a71dacec74845d5e90e6fded2f7c4934c5f61aa3db8e81e78da78371e607
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

class HumanElectronicSignatureV1(TypedDict):
    contractVersion: Literal["HumanElectronicSignatureV1@1.0.0"]
    issuerQualifiedIdentity: dict[str, Any]
    signerNameSnapshot: str
    nativeUserBindingId: str | None
    rolesAtSigning: list[str]
    assignmentsAtSigning: list[str]
    reauthentication: dict[str, Any]
    recordHash: StudyMappingDecisionHashRefV1
    displayedStatement: str
    signatureMeaning: str
    reason: str
    signedAt: str
    tenantId: str | None
    platformStudyId: str
    failedAttemptAuditRefs: list[str]

OsbCandidateSetResourceFamilyV1 = Literal["activities", "activity_instruction_templates", "activity_schedules", "cdash_variables", "compound_product_relationships", "controlled_terminology", "controlled_terminology_codelists", "criteria_templates", "endpoint_templates", "objective_templates", "odm_aliases", "odm_conditions", "odm_forms", "odm_item_groups", "odm_items", "odm_methods", "study_compound_dosing_relationships", "study_metadata", "timeframe_templates", "timeframes", "units"]

class OsbLegacyStudyIdentityV1(TypedDict):
    contractVersion: Literal["1.0.0"]
    system: Literal["osb"]
    tenantId: str
    platformStudyId: str
    namespace: Literal["accuratrials-osb"]
    objectType: Literal["study-draft-root"]
    bindingId: str
    nativeIdentity: str
    nativeVersion: str
    verificationStatus: Literal["verified"]

class OsbExternalStudyIdentityV1(TypedDict):
    contractVersion: Literal["1.0.0", "1.1.0"]
    bindingId: str
    tenantId: str
    platformStudyId: str
    system: Literal["osb"]
    namespace: Literal["accuratrials-osb"]
    objectType: Literal["study-draft-root"]
    nativeIdentity: str
    nativeVersion: str
    verificationStatus: Literal["verified"]
    verifiedBy: str
    verifiedAt: str
    validFrom: str
    validTo: NotRequired[str | None]
    evidence: OsbExternalIdentityEvidenceV1
    supersedesBindingId: NotRequired[str | None]
    createdAt: str
    createdBy: str
    updatedAt: NotRequired[str]
    updatedBy: NotRequired[str]

OsbCandidateSetStudyIdentityV1 = OsbLegacyStudyIdentityV1 | OsbExternalStudyIdentityV1

class OsbExternalIdentityEvidenceV1(TypedDict):
    receiptId: str
    receiptPayloadHash: OsbExternalIdentityHashRefV1
    signedEnvelopeHash: NotRequired[OsbExternalIdentityHashRefV1]
    trustBundleVersion: NotRequired[int]
    trustBundleHash: NotRequired[OsbExternalIdentityHashRefV1]
    trustedSigningTime: NotRequired[str]
    evidenceRefs: NotRequired[list[str]]

class OsbExternalIdentityHashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0"]
    value: str
    mediaType: Literal["application/json"]
    schemaVersion: str
    excludedPaths: list[Any]

class OsbNativeCandidateIdentityV1(TypedDict):
    resourceFamily: OsbCandidateSetResourceFamilyV1
    resourceType: str
    uid: str
    version: str

class StudyMappingDecisionHashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0", "raw-bytes/1.0"]
    value: str
    mediaType: str
    schemaVersion: str
    excludedPaths: list[str]

class StudyMappingDecisionServiceAttestationV1(TypedDict):
    mode: Literal["prototype-session-attested", "kms-signed"]
    productionEligible: bool
    compositeHash: StudyMappingDecisionHashRefV1
    service: str
    environment: str
    attestedAt: str

class StudyMappingDecisionStatementV1(TypedDict):
    contractVersion: Literal["StudyMappingDecisionStatementV1@1.0.0"]
    decisionId: str
    tenantId: str
    platformStudyId: str
    semanticSnapshotHash: StudyMappingDecisionHashRefV1
    candidateRequestHash: StudyMappingDecisionHashRefV1
    candidateSetHash: StudyMappingDecisionHashRefV1
    mappingContextHash: str
    osbStudyIdentity: OsbCandidateSetStudyIdentityV1
    selections: list[StudyMappingSelectionV1]
    decisionSetHash: StudyMappingDecisionHashRefV1
    displayedStatement: str
    signatureMeaning: str
    reason: str
    supersedesDecisionId: str | None

class StudyMappingDecisionV1(TypedDict):
    contractVersion: Literal["StudyMappingDecisionV1@1.0.0"]
    statement: StudyMappingDecisionStatementV1
    humanSignature: HumanElectronicSignatureV1
    serviceAttestation: StudyMappingDecisionServiceAttestationV1

class StudyMappingSelectionV1(TypedDict):
    factId: str
    revision: int
    targetKey: str
    action: Literal["select", "create", "reject", "defer"]
    candidateIdentity: None | OsbNativeCandidateIdentityV1
    rationale: str
