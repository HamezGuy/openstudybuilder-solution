"""AUTO-GENERATED from CSL-owned TransformationReceiptV2.
Schema sha256:7a3426bbba2af1c8d71cdef9344dcf9b782411742ef59c9e5294c824c3b4a802
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

NativeTargetIdentityV1 = NativeTargetIdentityV1

class PlatformHashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0", "raw-bytes/1.0"]
    value: str
    mediaType: str
    schemaVersion: str
    excludedPaths: list[str]

class TransformationAdapterV1(TypedDict):
    id: str
    version: str
    rulesetHash: PlatformHashRefV1

TransformationDispositionV1 = Literal["native", "governed_extension", "excluded_signed", "deferred_blocking", "quarantined", "rejected"]

class TransformationFieldReceiptV1(TypedDict):
    sourcePath: str
    targetPath: str | None
    sourceValueHash: PlatformHashRefV1
    targetValueHash: None | PlatformHashRefV1
    disposition: TransformationDispositionV1

class TransformationReceiptV2(TypedDict):
    contractVersion: Literal["TransformationReceiptV2@1.0.0"]
    receiptId: str
    decisionId: str
    operationId: str
    adapter: TransformationAdapterV1
    sourceFact: TransformationSourceFactV1
    osbEvidenceHash: PlatformHashRefV1
    expectedProjectionHash: None | PlatformHashRefV1
    observedNativeHash: None | PlatformHashRefV1
    fieldReceipts: list[TransformationFieldReceiptV1]
    targetIdentity: None | NativeTargetIdentityV1
    targetVersion: str | None
    disposition: TransformationDispositionV1
    completedAt: str

class TransformationSourceFactV1(TypedDict):
    factId: str
    revision: int
    sourceInputHash: PlatformHashRefV1
