"""AUTO-GENERATED from OSB-owned OsbCandidateRequestV1.
Schema sha256:e23d31a0ada365fd254fcc7a1856537e8da1e6a5e98daee20ba856efc31828df
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

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
    rowSetHash: OsbCandidateHashRefV1
    counts: ConservationCensusCountsV1

class ConservationEndpointV1(TypedDict):
    artifactId: str
    contract: str
    type: str
    path: str
    valueHash: OsbCandidateHashRefV1

class ConservationExclusionPolicyV1(TypedDict):
    policyId: str
    policyVersion: str
    approver: str
    reason: str
    sourcePath: str

class OsbCandidateActiveClaimRevisionV1(TypedDict):
    sourceFactId: str
    revision: int
    lifecycle: str
    valueHash: OsbCandidateHashRefV1

class OsbCandidateCheckpointV1(TypedDict):
    osbNativeVersion: str | None
    semanticSnapshotHash: OsbCandidateHashRefV1

class OsbCandidateEvidenceArtifactRefV1(TypedDict):
    contractVersion: Literal["ArtifactRefV1@1.0.0"]
    artifactId: str
    artifactVersionId: str
    kind: str
    stableLocator: str
    payloadHash: OsbCandidateHashRefV1
    descriptorHash: OsbCandidateHashRefV1
    byteSize: int
    classification: str
    tenantId: str
    region: str
    producerService: str
    producerEnvironment: str
    producerVersion: str
    payloadContract: str
    payloadContractVersion: str
    purpose: str
    createdAt: str

class OsbCandidateHashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0", "raw-bytes/1.0"]
    value: str
    mediaType: str
    schemaVersion: str
    excludedPaths: list[str]

class OsbCandidateRequestV1(TypedDict):
    contractVersion: Literal["OsbCandidateRequestV1@1.0.0"]
    requestId: str
    requestVersionId: str
    tenantId: str
    platformStudyId: str
    osbStudyIdentity: OsbStudyIdentityV1
    sourceFactPackage: OsbCandidateSourcePackageRefV1
    semanticSnapshot: OsbCandidateSnapshotRefV1
    activeClaimRevisions: list[OsbCandidateActiveClaimRevisionV1]
    requestedObjectFamilies: list[OsbResourceFamilyV1]
    typedSourceIntents: list[OsbTypedSourceIntentV1]
    evidenceArtifactRefs: list[OsbCandidateEvidenceArtifactRefV1]
    projectionRuleset: OsbProjectionRulesetV1
    inputConservation: ConservationCensusV1
    checkpointPreconditions: OsbCandidateCheckpointV1
    expiresAt: str
    createdAt: str
    createdBy: str

class OsbCandidateSnapshotRefV1(TypedDict):
    snapshotVersionId: str
    payloadHash: OsbCandidateHashRefV1
    memberSetHash: OsbCandidateHashRefV1

class OsbCandidateSourcePackageRefV1(TypedDict):
    packageVersionId: str
    payloadHash: OsbCandidateHashRefV1
    factSetHash: OsbCandidateHashRefV1

class OsbCreateOptionV1(TypedDict):
    allowed: Literal[True]
    requestedNativeType: str

class OsbProjectionRulesetV1(TypedDict):
    id: Literal["csl-to-osb-candidate-request"]
    version: Literal["1.0.0"]
    hash: OsbCandidateHashRefV1

OsbResourceFamilyV1 = Literal["activities", "compound_product_relationships", "controlled_terminology", "criteria_templates", "endpoint_templates", "objective_templates", "odm_forms", "odm_item_groups", "odm_items", "study_compound_dosing_relationships", "units"]

class OsbStudyIdentityV1(TypedDict):
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

class OsbTypedSourceIntentSourceV1(TypedDict):
    assertionType: str | None
    clinicalDomain: str | None
    candidateType: str | None
    exactQuote: str | None
    label: str | None
    values: list[OsbTypedSourceValueV1]

class OsbTypedSourceIntentV1(TypedDict):
    factId: str
    revision: int
    conceptId: str
    targetKey: Literal["primary"]
    semanticRole: str
    resourceFamily: OsbResourceFamilyV1
    source: OsbTypedSourceIntentSourceV1
    evidence: Any
    searchStrings: list[str]
    searchCodes: list[str]
    createOption: OsbCreateOptionV1

class OsbTypedSourceValueV1(TypedDict):
    name: str
    sourcePath: str
    valueType: str
    value: Any
