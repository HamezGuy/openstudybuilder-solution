"""AUTO-GENERATED from OSB-owned OsbCandidateSetV1.
Schema sha256:f497ffafe284d890ea82eb52e4174283f0f90b59773baa41a673061d589cd30e
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
    nativeStudyOperation: NotRequired[OsbStudyMetadataOfferV1]
    nativeStudyOperationHash: NotRequired[OsbCandidateSetHashRefV1]
    allowed: Literal[True]
    requestedNativeType: str | None

class OsbCandidateRecordSourceV1(TypedDict):
    assertionType: str | None
    clinicalDomain: str | None
    candidateType: str | None
    exactQuote: str | None
    label: str | None
    classification: NotRequired[dict[str, Any] | None]
    values: list[OsbTypedSourceValueV1]

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
    requestedResourceFamily: NotRequired[Literal["activities", "activity_instruction_templates", "activity_schedules", "assignments", "branching", "cdash_variables", "compound_product_relationships", "conditions", "controlled_terminology", "controlled_terminology_codelists", "criteria_templates", "edit_checks", "endpoint_templates", "objective_templates", "odm_aliases", "odm_conditions", "odm_forms", "odm_item_groups", "odm_items", "odm_methods", "study_compound_dosing_relationships", "study_metadata", "timeframe_templates", "timeframes", "units"]]
    source: NotRequired[None | OsbCandidateRecordSourceV1]
    evidence: NotRequired[Any]

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

OsbCandidateSetResourceFamilyV1 = Literal["activities", "activity_instruction_templates", "activity_schedules", "cdash_variables", "compound_product_relationships", "controlled_terminology", "controlled_terminology_codelists", "criteria_templates", "endpoint_templates", "objective_templates", "odm_aliases", "odm_conditions", "odm_forms", "odm_item_groups", "odm_items", "odm_methods", "study_compound_dosing_relationships", "study_metadata", "timeframe_templates", "timeframes", "units"]

class OsbCandidateSetSnapshotRefV1(TypedDict):
    snapshotVersionId: str
    payloadHash: OsbCandidateSetHashRefV1
    memberSetHash: OsbCandidateSetHashRefV1

class OsbCandidateSetSourcePackageRefV1(TypedDict):
    packageVersionId: str
    payloadHash: OsbCandidateSetHashRefV1
    factSetHash: OsbCandidateSetHashRefV1

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
    deferredMembers: NotRequired[list[OsbDeferredMemberV1]]
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

class OsbDeferredMemberV1(TypedDict):
    factId: str
    revision: int
    disposition: Literal["governed_extension", "deferred_blocking"]
    reasonCodes: list[str]

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

class OsbMappingContextSnapshotV1(TypedDict):
    schemaVersion: Literal["osb-mapping-context/2.0"]
    mappingAuthority: Literal["OpenStudyBuilder"]
    contextHash: str

class OsbNativeCandidateIdentityV1(TypedDict):
    resourceFamily: OsbCandidateSetResourceFamilyV1
    resourceType: str
    uid: str
    version: str

class OsbStudyMetadataOfferV1(TypedDict):
    contractVersion: Literal["OsbStudyMetadataOfferV1@1.0.0"]
    nativeStudyId: str
    metadataPath: str
    metadataValue: Any
    joinedText: bool
    multiValued: bool
    sourcePlanHash: OsbCandidateSetHashRefV1
    nativePreconditionHash: OsbCandidateSetHashRefV1
    referenceBindings: list[dict[str, Any]]

class OsbTypedSourceValueV1(TypedDict):
    name: str
    sourcePath: str
    valueType: str
    value: Any
