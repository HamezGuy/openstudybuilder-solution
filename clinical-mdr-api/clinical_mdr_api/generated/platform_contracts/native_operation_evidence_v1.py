"""AUTO-GENERATED from OSB-owned NativeOperationEvidenceV1.
Schema sha256:40054c3aab19f8c501bec5d047eb878f2bf40ed080e316d342a198ab8fe65f4b
Do not edit by hand. Run generate-p4-request-contracts.mjs.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

class NativeOperationEvidenceV1(TypedDict):
    contractVersion: Literal["NativeOperationEvidenceV1@1.0.0"]
    evidenceId: str
    decisionId: str
    operationId: str
    idempotencyKey: str | None
    effectId: str
    adapterVersion: str
    executorVersion: str
    executorKind: str
    expectedTargetPrecondition: NativeOperationPreconditionV1
    sourceInputHash: PlatformHashRefV1
    nativeTargetIdentity: None | NativeTargetIdentityV1
    preTargetVersion: str | None
    postTargetVersion: str | None
    normalizedReadBack: dict[str, Any] | None
    normalizedReadBackHash: None | PlatformHashRefV1
    disposition: TransformationDispositionV1
    operationTime: str

class NativeOperationPreconditionV1(TypedDict):
    nativeStudyId: str | None
    nativeVersion: str | None
    candidateSetVersionId: str | None

NativeTargetIdentityV1 = NativeTargetIdentityV1

class PlatformHashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0", "raw-bytes/1.0"]
    value: str
    mediaType: str
    schemaVersion: str
    excludedPaths: list[str]

TransformationDispositionV1 = Literal["native", "governed_extension", "excluded_signed", "deferred_blocking", "quarantined", "rejected"]
