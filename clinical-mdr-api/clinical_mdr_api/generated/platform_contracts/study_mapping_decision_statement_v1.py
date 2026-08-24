"""AUTO-GENERATED from CSL-owned StudyMappingDecisionStatementV1.
Schema sha256:71dbafe38c4deb179a3f826627d76ce5e8516751e28dc1eb661b69134817d58b
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

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
