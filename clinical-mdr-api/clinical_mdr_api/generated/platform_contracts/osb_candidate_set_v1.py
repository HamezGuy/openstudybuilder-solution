"""AUTO-GENERATED from OSB-owned OsbCandidateSetV1.
Schema sha256:71da7c466f0f5e45d43c65fb59950bb1c180753aeabde19fea8e8fb9f732fbf8
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

class CandidateAssignmentProjectionV1(TypedDict):
    contractVersion: Literal["CandidateAssignmentProjectionV1@1.0.0"]
    assignmentId: str
    kind: Literal["mapping-adjudication"]
    tenantId: str
    platformStudyId: str
    candidateSetVersionId: str

class ConservationCensusCountsV1(TypedDict):
    rows: int
    native: int
    governedExtension: int
    excludedSigned: int
    deferredBlocking: int
    quarantined: int
    rejected: int

class ConservationCensusMultiplicityV1(TypedDict):
    source: int
    target: int

class ConservationCensusOrderingV1(TypedDict):
    significant: bool
    sourceIndex: int | None
    targetIndex: int | None

class ConservationCensusRowV1(TypedDict):
    unitId: str
    source: ConservationEndpointV1
    target: None | ConservationEndpointV1
    multiplicity: ConservationCensusMultiplicityV1
    splitMergeGroup: str | None
    splitMergeRule: str | None
    ordering: ConservationCensusOrderingV1
    disposition: Literal["native", "governed_extension", "excluded_signed", "deferred_blocking", "quarantined", "rejected"]
    exclusionPolicy: None | ConservationExclusionPolicyV1
    evidenceRefs: list[str]
    receiptRefs: list[str]

class ConservationCensusV1(TypedDict):
    contractVersion: Literal["ConservationCensusV1@1.0.0"]
    rows: list[ConservationCensusRowV1]
    rowSetHash: OsbCandidateSetHashRefV1
    counts: ConservationCensusCountsV1

class ConservationEndpointV1(TypedDict):
    artifactId: str
    contract: str
    type: str
    path: str
    valueHash: OsbCandidateSetHashRefV1

class ConservationExclusionPolicyV1(TypedDict):
    policyId: str
    policyVersion: str
    approver: str
    reason: str
    sourcePath: str

class OsbCandidateCreateOptionV1(TypedDict):
    allowed: Literal[True]
    requestedNativeType: str

class OsbCandidateRecordV1(TypedDict):
    factId: str
    revision: int
    conceptId: str
    targetKey: str
    semanticRole: str
    resourceFamily: OsbCandidateSetResourceFamilyV1
    nativeCandidates: list[OsbNativeCandidateIdentityV1]
    createOption: None | OsbCandidateCreateOptionV1
    complete: bool
    truncated: bool
    blockers: list[str]

class OsbCandidateSetHashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0", "raw-bytes/1.0"]
    value: str
    mediaType: str
    schemaVersion: str
    excludedPaths: list[str]

class OsbCandidateSetRequestRefV1(TypedDict):
    requestVersionId: str
    payloadHash: OsbCandidateSetHashRefV1

OsbCandidateSetResourceFamilyV1 = Literal["activities", "compound_product_relationships", "controlled_terminology", "criteria_templates", "endpoint_templates", "objective_templates", "odm_forms", "odm_item_groups", "odm_items", "study_compound_dosing_relationships", "units"]

class OsbCandidateSetSnapshotRefV1(TypedDict):
    snapshotVersionId: str
    payloadHash: OsbCandidateSetHashRefV1
    memberSetHash: OsbCandidateSetHashRefV1

class OsbCandidateSetSourcePackageRefV1(TypedDict):
    packageVersionId: str
    payloadHash: OsbCandidateSetHashRefV1
    factSetHash: OsbCandidateSetHashRefV1

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

class OsbCandidateSetV1(TypedDict):
    contractVersion: Literal["OsbCandidateSetV1@1.0.0"]
    candidateSetId: str
    candidateSetVersionId: str
    tenantId: str
    platformStudyId: str
    request: OsbCandidateSetRequestRefV1
    semanticSnapshot: OsbCandidateSetSnapshotRefV1
    sourceFactPackage: OsbCandidateSetSourcePackageRefV1
    osbStudyIdentity: OsbCandidateSetStudyIdentityV1
    capabilityCheckpoint: OsbCapabilityCheckpointV1
    mappingContext: OsbMappingContextSnapshotV1
    candidateRecords: list[OsbCandidateRecordV1]
    conservation: ConservationCensusV1
    assignment: CandidateAssignmentProjectionV1
    blockers: list[str]
    expiresAt: str
    createdAt: str
    createdBy: str

class OsbCapabilityCheckpointV1(TypedDict):
    osbOpenApiHash: str
    mappingContextHash: str
    nativeVersion: str
    governed: bool

class OsbMappingContextSnapshotV1(TypedDict):
    schemaVersion: Literal["osb-mapping-context/2.0"]
    mappingAuthority: Literal["OpenStudyBuilder"]
    contextHash: str

class OsbNativeCandidateIdentityV1(TypedDict):
    resourceFamily: OsbCandidateSetResourceFamilyV1
    resourceType: str
    uid: str
    version: str
