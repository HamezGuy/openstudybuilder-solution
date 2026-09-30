"""TypedDict bindings generated from the CC-owned platform v1 JSON Schemas."""

from typing import Any, Literal, NotRequired, TypedDict


class HashRefV1(TypedDict):
    algorithm: Literal["sha-256"]
    canonicalizationVersion: Literal["canonical-json/1.0", "raw-bytes/1.0"]
    value: str
    mediaType: str
    schemaVersion: str
    excludedPaths: list[str]


PlatformDomainSystemV1 = Literal["il", "csl", "osb", "edc"]


class ExternalIdentityCreateIntentV1(TypedDict):
    contractVersion: Literal["1.0.0"]
    intentId: str
    tenantId: str
    platformStudyId: str
    targetSystem: PlatformDomainSystemV1
    namespace: str
    objectType: str
    requestedInitialState: dict[str, Any]
    expectedAbsence: bool
    commandId: str
    idempotencyKey: str
    actorSubject: str
    purpose: str
    expiresAt: str
    intentHash: HashRefV1


NativeIdentityBindingEffectV1 = Literal[
    "created", "claimed_existing", "version_rollover", "no_op"
]


class NativeIdentityBindingReceiptV1(TypedDict):
    contractVersion: Literal["1.0.0"]
    receiptId: str
    intentId: str
    tenantId: str
    platformStudyId: str
    targetSystem: PlatformDomainSystemV1
    namespace: str
    objectType: str
    nativeIdentity: str
    nativeVersion: NotRequired[str | None]
    effectType: NativeIdentityBindingEffectV1
    creationEffectId: str
    idempotencyKey: str
    targetStateHash: HashRefV1
    createIntentHash: HashRefV1
    previousBindingId: NotRequired[str | None]
    evidenceRefs: NotRequired[list[str]]
    producedAt: str


ClassificationV1 = Literal[
    "public", "internal", "confidential", "regulated-non-phi", "regional-phi"
]


class ArtifactDescriptorV1(TypedDict):
    contractVersion: Literal["ArtifactDescriptorV1@1.0.0"]
    artifactId: str
    artifactVersionId: str
    kind: str
    stableLocator: str
    payloadHash: HashRefV1
    byteSize: int
    classification: ClassificationV1
    tenantId: str
    region: str
    producerService: str
    producerEnvironment: str
    producerVersion: str
    payloadContract: str
    payloadContractVersion: str
    purpose: str
    createdAt: str


class ArtifactRefV1(TypedDict):
    contractVersion: Literal["ArtifactRefV1@1.0.0"]
    artifactId: str
    artifactVersionId: str
    kind: str
    stableLocator: str
    payloadHash: HashRefV1
    descriptorHash: HashRefV1
    byteSize: int
    classification: ClassificationV1
    tenantId: str
    region: str
    producerService: str
    producerEnvironment: str
    producerVersion: str
    payloadContract: str
    payloadContractVersion: str
    purpose: str
    createdAt: str


class ArtifactAccessGrantV1(TypedDict):
    contractVersion: Literal["ArtifactAccessGrantV1@1.0.0"]
    grantId: str
    artifactId: str
    artifactVersionId: str
    descriptorHash: HashRefV1
    payloadHash: HashRefV1
    producerService: str
    producerEnvironment: str
    audience: str
    tenantId: str
    platformStudyId: str
    region: str
    purpose: str
    allowedOperation: Literal["read", "verify", "transfer"]
    issuedAt: str
    notBefore: str
    expiresAt: str
    oneUse: bool
    revocationReference: str


class ArtifactTransferReceiptV1(TypedDict):
    contractVersion: Literal["ArtifactTransferReceiptV1@1.0.0"]
    transferId: str
    tenantId: str
    platformStudyId: str
    sourceArtifact: ArtifactRefV1
    destinationArtifact: ArtifactRefV1
    sourceRegion: str
    destinationRegion: str
    sourceEncryptionKeyId: str
    destinationEncryptionKeyId: str
    legalBasisApprovalId: str
    providerPlacementEvidence: HashRefV1
    bytesTransferred: int
    verifiedPayloadHash: HashRefV1
    startedAt: str
    completedAt: str
    actorSubject: str
    disposition: Literal["copied", "already_present"]


class ArtifactSigningStatementV1(TypedDict):
    contractVersion: Literal["ArtifactSigningStatementV1@1.0.0"]
    descriptorHash: HashRefV1
    payloadHash: HashRefV1
    artifactId: str
    artifactVersionId: str
    payloadContract: str
    payloadContractVersion: str
    tenantId: str
    region: str
    classification: ClassificationV1
    producerService: str
    producerEnvironment: str
    signingPurpose: str
    signerAssertedIat: str


class ArtifactJwsProtectedHeaderV1(TypedDict):
    alg: Literal["ES256"]
    kid: str
    typ: Literal["accuratrials-artifact-signing-statement+jws"]
    cty: Literal["application/vnd.accuratrials.artifact-signing-statement-v1+json"]
    b64: Literal[False]
    crit: list[Literal["b64"]]
    statement_contract_version: Literal["ArtifactSigningStatementV1@1.0.0"]
    statement_hash: str


class DetachedJwsV1(TypedDict):
    protectedHeader: ArtifactJwsProtectedHeaderV1
    protectedBase64Url: str
    signatureBase64Url: str


class SigningTimeEvidenceV1(TypedDict):
    profile: Literal["rfc3161/1.0"]
    tsaPolicyOid: str
    timestampTokenBase64: str
    messageImprint: HashRefV1


class SignedArtifactEnvelopeV1(TypedDict):
    contractVersion: Literal["SignedArtifactEnvelopeV1@1.0.0"]
    artifactDescriptor: ArtifactDescriptorV1
    payloadHash: HashRefV1
    signingStatement: ArtifactSigningStatementV1
    signatureProfile: Literal["jws-detached-rfc7797/1.0"]
    detachedJws: DetachedJwsV1
    signingTimeEvidence: SigningTimeEvidenceV1


SigningCustodyProviderV1 = Literal[
    "prototype-memory-kms",
    "aws-kms",
    "azure-key-vault-hsm",
    "gcp-cloud-kms-hsm",
]


class SigningTrustKeyV1(TypedDict):
    kid: str
    service: str
    environment: str
    region: str
    purposes: list[str]
    algorithm: Literal["ES256"]
    publicKeyPem: str
    publicKeyFingerprint: str
    validFrom: str
    validTo: str | None
    revokedEffectiveAt: str | None
    compromiseWindowUnknown: bool
    custodyProvider: SigningCustodyProviderV1
    custodyKeyRef: str
    exportable: Literal[False]


class TimestampAuthorityV1(TypedDict):
    tsaId: str
    policyOid: str
    rootCertificatePem: str
    rootCertificateFingerprint: str
    leafCertificateFingerprint: str
    requiredEkuOid: Literal["1.3.6.1.5.5.7.3.8"]
    requireCriticalEku: Literal[True]
    validFrom: str
    validTo: str
    revokedEffectiveAt: str | None
    maxAccuracyMillis: int


class SigningTrustBundleV1(TypedDict):
    contractVersion: Literal["SigningTrustBundleV1@1.0.0"]
    bundleId: str
    bundleVersion: int
    environment: str
    region: str
    previousBundleHash: HashRefV1 | None
    createdAt: str
    validFrom: str
    expiresAt: str
    maxSignerClockSkewSeconds: int
    keys: list[SigningTrustKeyV1]
    timestampAuthorities: list[TimestampAuthorityV1]
    revocationEpoch: int


class ConservationEndpointV1(TypedDict):
    artifactId: str
    contract: str
    type: str
    path: str
    valueHash: HashRefV1


class ExclusionPolicyV1(TypedDict):
    policyId: str
    policyVersion: str
    approver: str
    reason: str
    sourcePath: str


class ConservationMultiplicityV1(TypedDict):
    source: int
    target: int


class ConservationOrderingV1(TypedDict):
    significant: bool
    sourceIndex: int | None
    targetIndex: int | None


class ConservationCensusRowV1(TypedDict):
    unitId: str
    source: ConservationEndpointV1
    target: ConservationEndpointV1 | None
    multiplicity: ConservationMultiplicityV1
    splitMergeGroup: str | None
    splitMergeRule: str | None
    ordering: ConservationOrderingV1
    disposition: Literal[
        "native",
        "governed_extension",
        "excluded_signed",
        "deferred_blocking",
        "quarantined",
        "rejected",
    ]
    exclusionPolicy: ExclusionPolicyV1 | None
    evidenceRefs: list[str]
    receiptRefs: list[str]


class ConservationCountsV1(TypedDict):
    rows: int
    native: int
    governedExtension: int
    excludedSigned: int
    deferredBlocking: int
    quarantined: int
    rejected: int


class ConservationCensusV1(TypedDict):
    contractVersion: Literal["ConservationCensusV1@1.0.0"]
    rows: list[ConservationCensusRowV1]
    counts: ConservationCountsV1
    rowSetHash: HashRefV1


class CommandIntentActorV1(TypedDict):
    issuer: str
    subject: str
    actorType: Literal["human", "service"]
    clientId: NotRequired[str]


class CommandIntentV1(TypedDict):
    contractVersion: Literal["CommandIntentV1@1.0.0"]
    tenantId: str
    platformStudyId: str
    actorChain: list[CommandIntentActorV1]
    purpose: str
    targetSystem: Literal["il", "csl", "osb", "edc"]
    action: str
    capability: str
    expectedSourceState: HashRefV1 | None
    expectedTargetState: HashRefV1 | None
    inputHash: HashRefV1
    authorizationDecisionId: str


class ErrorV1(TypedDict):
    contractVersion: Literal["ErrorV1@1.0.0"]
    code: str
    category: Literal[
        "authorization",
        "validation",
        "conflict",
        "dependency",
        "timeout",
        "integrity",
        "privacy",
        "internal",
    ]
    retryability: Literal["retryable", "terminal", "unknown"]
    safeMessage: str
    details: dict[str, Any]
    dependency: str | None
    correlationId: str


class DomainAuditActorEntryV1(TypedDict):
    subject: str
    type: Literal["human", "service"]
    issuer: NotRequired[str]
    clientId: NotRequired[str]


class DomainAuditActorV1(TypedDict):
    issuerQualifiedSubject: str
    subjectType: Literal["human", "service"]
    humanSubject: str | None
    serviceActor: str | None
    actorChain: list[DomainAuditActorEntryV1]


class DomainAuditObjectV1(TypedDict):
    type: str
    id: str
    version: str | None


class DomainAuditEventV1(TypedDict):
    contractVersion: Literal["DomainAuditEventV1@1.0.0"]
    auditEventId: str
    tenantId: str
    platformStudyId: str | None
    sourceSystem: Literal["cc", "il", "csl", "osb", "edc"]
    environment: str
    region: str
    streamId: str
    sequence: int
    previousEventHash: HashRefV1 | None
    occurredAt: str
    actor: DomainAuditActorV1
    purpose: str
    action: str
    outcome: Literal["accepted", "succeeded", "rejected", "failed", "cancelled", "quarantined"]
    object: DomainAuditObjectV1
    correlationId: str
    causationId: str | None
    commandId: str | None
    effectId: str
    classification: Literal["platform-metadata", "governance-non-phi"]
    retentionPolicyVersion: str
    details: dict[str, Any]


class AuditRootCreatedByV1(TypedDict):
    issuerQualifiedSubject: str
    subjectType: Literal["service"]


class AuditRootCheckpointV1(TypedDict):
    contractVersion: Literal["AuditRootCheckpointV1@1.0.0"]
    checkpointId: str
    tenantId: str
    sourceSystem: Literal["cc", "il", "csl", "osb", "edc"]
    environment: str
    region: str
    streamId: str
    sequenceStart: int
    sequenceEnd: int
    eventCount: int
    firstEventHash: HashRefV1
    lastEventHash: HashRefV1
    orderedEventSetHash: HashRefV1
    previousCheckpointHash: HashRefV1 | None
    createdAt: str
    createdBy: AuditRootCreatedByV1
    retentionPolicyVersion: str
    retainUntil: str
    legalHold: bool
    exportSchemaVersion: Literal["DomainAuditExportV1@1.0.0"]


class AuditRootVerificationV1(TypedDict):
    verified: Literal[True]
    payloadHash: HashRefV1
    trustedTime: str


class DomainAuditExportV1(TypedDict):
    contractVersion: Literal["DomainAuditExportV1@1.0.0"]
    checkpoint: AuditRootCheckpointV1
    checkpointPayloadHash: HashRefV1
    signedEnvelope: dict[str, Any]
    verification: AuditRootVerificationV1
    eventCount: int
