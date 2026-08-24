"""AUTO-GENERATED from CSL-owned StudyMappingDecisionV1.
Schema sha256:79b51d8e4e45b3da44ee58063408e24f4c8986e01aab04b9a705afac5bcf078c
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

OsbCandidateSetResourceFamilyV1 = Literal["activities", "compound_product_relationships", "controlled_terminology", "criteria_templates", "endpoint_templates", "objective_templates", "odm_forms", "odm_item_groups", "odm_items", "study_compound_dosing_relationships", "units"]

class OsbCandidateSetStudyIdentityV1(TypedDict):
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
