"""AUTO-GENERATED from CSL-owned TransformationCheckpointV1.
Schema sha256:daf8c0ee26ab279ebdcd61bcefa6ec94a36d4ddb16da60df59980201de9741a7
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

class ConservationCensusRowV1(TypedDict):
    unitId: str
    sourcePath: str
    targetPath: str | None
    sourceValueHash: None | PlatformHashRefV1
    targetValueHash: None | PlatformHashRefV1
    multiplicity: dict[str, Any]
    ordering: dict[str, Any]
    disposition: TransformationDispositionV1
    evidenceRefs: list[str]

class ConservationCensusV1(TypedDict):
    contractVersion: Literal["ConservationCensusV1@1.0.0"]
    rows: list[ConservationCensusRowV1]
    rowSetHash: PlatformHashRefV1
    counts: dict[str, Any]

class PlatformHashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0", "raw-bytes/1.0"]
    value: str
    mediaType: str
    schemaVersion: str
    excludedPaths: list[str]

class TransformationBlockerV1(TypedDict):
    code: str
    operationId: str

class TransformationCheckpointV1(TypedDict):
    contractVersion: Literal["TransformationCheckpointV1@1.0.0"]
    checkpointId: str
    checkpointVersionId: str
    tenantId: str
    platformStudyId: str
    osbStudyIdentity: dict[str, Any]
    semanticSnapshotHash: PlatformHashRefV1
    decisionSetHash: PlatformHashRefV1
    receiptMembership: list[TransformationReceiptMemberV1]
    receiptSetHash: PlatformHashRefV1
    nativeEvidenceSetHash: PlatformHashRefV1
    osbAuthority: TransformationOsbAuthorityV1
    conservation: ConservationCensusV1
    exclusionPolicy: TransformationExclusionPolicyV1
    blockers: list[TransformationBlockerV1]
    createdAt: str
    createdBy: str

TransformationDispositionV1 = Literal["native", "governed_extension", "excluded_signed", "deferred_blocking", "quarantined", "rejected"]

class TransformationExclusionPolicyV1(TypedDict):
    id: str
    version: str

class TransformationOsbAuthorityV1(TypedDict):
    managedTargetCheckpoint: dict[str, Any]
    managedTargetCheckpointHash: None | PlatformHashRefV1

class TransformationReceiptMemberV1(TypedDict):
    receiptId: str
    operationId: str
    payloadHash: PlatformHashRefV1
