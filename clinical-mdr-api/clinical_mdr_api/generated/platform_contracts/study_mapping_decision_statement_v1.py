"""AUTO-GENERATED from CSL-owned StudyMappingDecisionStatementV1.
Schema sha256:fcd7995f343a24bfbc5bd267ba8c1233139fda926d021bf3f9bc2f86c82a3052
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

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

class StudyMappingSelectionV1(TypedDict):
    factId: str
    revision: int
    targetKey: str
    action: Literal["select", "create", "reject", "defer"]
    candidateIdentity: None | OsbNativeCandidateIdentityV1
    rationale: str
