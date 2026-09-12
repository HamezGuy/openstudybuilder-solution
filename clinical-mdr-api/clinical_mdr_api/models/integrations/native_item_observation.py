"""Closed, read-only companion to the original native mapping evidence.

It deliberately does not extend NativeOperationEvidenceV1 or assert study use.
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identity = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
Hash = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
ContextHash = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class NativeItemObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contractVersion: Literal["OsbNativeItemObservationRequestV1@1.0.0"]
    scope: Literal["library-item", "study-selection"]
    platformStudyId: Identity
    nativeStudyId: Identity
    nativeStudyVersion: Identity
    bindingId: Identity
    decisionId: Identity
    decisionHash: Hash
    candidateSetVersionId: Identity
    candidateSetHash: Hash
    mappingContextHash: ContextHash
    evidenceSetVersionId: Identity
    evidenceSetHash: Hash
    evidenceId: Identity
    factId: Identity
    revision: Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]
    targetKey: Identity
    itemUid: Identity
    itemVersion: Identity


class ObservationHash(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0"]
    value: Hash
    mediaType: Annotated[str, Field(strict=True, max_length=128)]
    schemaVersion: Annotated[str, Field(strict=True, max_length=128)]
    excludedPaths: Annotated[list[str], Field(max_length=0)]


ScalarText = Annotated[str, Field(strict=True, max_length=4096)]
ScalarInteger = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
Instant = Annotated[str, Field(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")]


class NativeLibraryItemScalars(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: ScalarText | None
    oid: ScalarText | None
    prompt: ScalarText | None
    datatype: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    length: ScalarInteger | None
    significantDigits: ScalarInteger | None
    sasFieldName: ScalarText | None
    sdsVarName: ScalarText | None
    origin: ScalarText | None
    comment: ScalarText | None


class NativeLibraryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    uid: Identity
    version: Identity
    status: Literal["Final"]
    fields: NativeLibraryItemScalars


class NativeItemObservationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contractVersion: Literal["OsbNativeItemObservationV1@1.1.0"]
    scope: Literal["library-item"]
    pins: NativeItemObservationRequest
    tenantId: Identity
    item: NativeLibraryItem
    itemHash: ObservationHash
    decisionHash: ObservationHash
    candidateSetHash: ObservationHash
    evidenceSetHash: ObservationHash
    operationHash: ObservationHash
    decisionAssurance: Literal["retained-prototype-attestation"]
    contextAssurance: Literal["retained-exact-context"]
    studySelectionVerified: Literal[False]
    semanticApprovalVerified: Literal[False]
    observedAt: Instant
    authorityCheckedAt: Instant
    expiresAt: Instant
