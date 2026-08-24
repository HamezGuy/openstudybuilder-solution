"""AUTO-GENERATED from CSL-owned SemanticSnapshotV1.
Schema sha256:5df0855651e84f760ba433d7ae0afd1af0977d4d1501786b59705ebc2b1d72e0
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

class SemanticSnapshotActiveClaimRevisionV1(TypedDict):
    sourceFactId: str
    revision: int
    lifecycle: str
    valueHash: SemanticSnapshotHashRefV1

class SemanticSnapshotDispositionV1(TypedDict):
    factId: str
    revision: int

class SemanticSnapshotHashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0", "raw-bytes/1.0"]
    value: str
    mediaType: str
    schemaVersion: str
    excludedPaths: list[str]

class SemanticSnapshotPolicyPinsV1(TypedDict):
    authorityPolicy: str
    profileSet: str

class SemanticSnapshotSourcePackageRefV1(TypedDict):
    packageVersionId: str
    payloadHash: SemanticSnapshotHashRefV1
    factSetHash: SemanticSnapshotHashRefV1
    sourceWatermark: str

class SemanticSnapshotV1(TypedDict):
    contractVersion: Literal["SemanticSnapshotV1@1.0.0"]
    snapshotId: str
    snapshotVersionId: str
    tenantId: str
    platformStudyId: str
    semanticStudyIdentity: SemanticStudyIdentityV1
    sourceFactPackage: SemanticSnapshotSourcePackageRefV1
    producingBuild: NotRequired[None | dict[str, Any]]
    activeClaimRevisions: list[SemanticSnapshotActiveClaimRevisionV1]
    exclusions: list[SemanticSnapshotDispositionV1]
    quarantine: list[SemanticSnapshotDispositionV1]
    policyPins: SemanticSnapshotPolicyPinsV1
    memberSetHash: SemanticSnapshotHashRefV1
    conservation: dict[str, Any]
    blockers: list[dict[str, Any]]
    createdAt: str
    createdBy: str

class SemanticStudyIdentityV1(TypedDict):
    namespace: Literal["accuratrials-csl"]
    objectType: Literal["semantic-study-root"]
    nativeIdentity: str
    nativeVersion: str
    bindingId: str
