"""Cross-language processor for the disabled native identity boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol, TypeVar
from uuid import uuid4

from .hash_signing_v1 import canonical_json, canonical_json_hash_ref
from .models_v1 import (
    ExternalIdentityCreateIntentV1,
    NativeIdentityBindingReceiptV1,
)

T = TypeVar("T")


class NativeIdentityCommandError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class NativeIdentityPrincipalV1:
    tenant_id: str
    study_ids: tuple[str, ...]
    subject: str
    human_subject: str | None
    actor_chain: tuple[dict[str, str], ...]
    roles: tuple[str, ...]
    purpose: str
    capabilities: tuple[str, ...]


class NativeIdentityTransactionV1(Protocol):
    def find_by_intent(self, intent_id: str) -> dict[str, Any] | None: ...
    def find_by_idempotency_key(self, key: str) -> dict[str, Any] | None: ...
    def apply_intent(self, intent: ExternalIdentityCreateIntentV1) -> dict[str, Any]: ...
    def publish(self, result: dict[str, Any]) -> None: ...


class NativeIdentityStoreV1(Protocol):
    def serializable(
        self,
        tenant_id: str,
        platform_study_id: str,
        callback: Callable[[NativeIdentityTransactionV1], T],
    ) -> T: ...


class NativeIdentityReceiptPublisherV1(Protocol):
    def publish(self, receipt: NativeIdentityBindingReceiptV1) -> dict[str, Any]: ...


def platform_identity_hash(value: Any, schema_version: str) -> dict[str, Any]:
    return canonical_json_hash_ref(
        value,
        media_type="application/json",
        schema_version=schema_version,
    )


def _instant(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError) as error:
        raise NativeIdentityCommandError(
            "IDENTITY_INTENT_INVALID", "Invalid intent expiry.", 422
        ) from error


def validate_native_identity_intent(
    intent: ExternalIdentityCreateIntentV1,
    principal: NativeIdentityPrincipalV1,
    *,
    target_system: str,
    namespaces: tuple[str, ...],
    object_types: tuple[str, ...],
    allowed_roles: tuple[str, ...],
    now: datetime,
) -> None:
    if intent.get("contractVersion") != "1.0.0":
        raise NativeIdentityCommandError(
            "IDENTITY_INTENT_VERSION_UNSUPPORTED", "Unsupported identity intent.", 422
        )
    required = (
        "intentId", "tenantId", "platformStudyId", "namespace", "objectType",
        "commandId", "idempotencyKey", "actorSubject", "purpose", "expiresAt",
    )
    if any(not str(intent.get(field, "")).strip() for field in required):
        raise NativeIdentityCommandError(
            "IDENTITY_INTENT_INVALID", "Identity intent is incomplete.", 422
        )
    if (
        intent["targetSystem"] != target_system
        or intent["namespace"] not in namespaces
        or intent["objectType"] not in object_types
    ):
        raise NativeIdentityCommandError(
            "IDENTITY_INTENT_TARGET_MISMATCH", "Intent targets another domain contract.", 422
        )
    initial_state = intent.get("requestedInitialState")
    if not isinstance(initial_state, dict):
        raise NativeIdentityCommandError(
            "IDENTITY_INITIAL_STATE_INVALID",
            "requestedInitialState must be an object.",
            422,
        )
    supplied_operation = initial_state.get("operation")
    if supplied_operation is not None and (
        not isinstance(supplied_operation, str) or not supplied_operation.strip()
    ):
        raise NativeIdentityCommandError(
            "IDENTITY_OPERATION_INVALID",
            "Identity operation must be a non-empty string.",
            422,
        )
    operation = supplied_operation if supplied_operation is not None else (
        "create" if intent["expectedAbsence"] else "claim_existing"
    )
    if (
        (intent["expectedAbsence"] and operation != "create")
        or (
            not intent["expectedAbsence"]
            and operation not in {"claim_existing", "version_rollover"}
        )
    ):
        raise NativeIdentityCommandError(
            "IDENTITY_OPERATION_PRECONDITION_MISMATCH",
            "Identity operation differs from expectedAbsence.",
            422,
        )
    if _instant(intent["expiresAt"]) <= now:
        raise NativeIdentityCommandError("IDENTITY_INTENT_EXPIRED", "Identity intent expired.")
    statement = {key: value for key, value in intent.items() if key != "intentHash"}
    expected_hash = platform_identity_hash(
        statement, "ExternalIdentityCreateIntentV1@1.0.0"
    )
    if canonical_json(intent["intentHash"]) != canonical_json(expected_hash):
        raise NativeIdentityCommandError(
            "IDENTITY_INTENT_HASH_MISMATCH",
            "Intent hash does not bind the exact statement.",
            422,
        )
    origin = (
        principal.actor_chain[0].get("subject")
        if principal.actor_chain
        else principal.human_subject or principal.subject
    )
    if (
        principal.tenant_id != intent["tenantId"]
        or intent["platformStudyId"] not in principal.study_ids
        or principal.purpose != "workflow-orchestration"
        or "native-identity:bind" not in principal.capabilities
        or not set(principal.roles).intersection(allowed_roles)
        or origin != intent["actorSubject"]
        or intent["purpose"] != "external-identity-create-intent"
    ):
        raise NativeIdentityCommandError(
            "IDENTITY_INTENT_AUTHORIZATION_DENIED",
            "Tenant, study, actor, role, purpose, or capability differs.",
            403,
        )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _signer_skew_seconds(
    statement: dict[str, Any], receipt: NativeIdentityBindingReceiptV1
) -> float | None:
    """Distance between when the signer says it signed and when the receipt was
    produced, or None when either instant is unreadable."""
    try:
        asserted = datetime.fromisoformat(
            str(statement.get("signerAssertedIat") or "").replace("Z", "+00:00")
        )
        produced = datetime.fromisoformat(str(receipt["producedAt"]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return abs((asserted - produced).total_seconds())


def _validate_signed_publication(
    receipt: NativeIdentityBindingReceiptV1,
    envelope: dict[str, Any],
    producer_service: str,
) -> None:
    descriptor = envelope.get("artifactDescriptor", {})
    statement = envelope.get("signingStatement", {})
    expected_hash = platform_identity_hash(
        receipt, "NativeIdentityBindingReceiptV1@1.0.0"
    )
    # THE SIGNER ASSERTS ITS OWN CLOCK, NOT THE RECEIPT'S.
    #
    # This required `signerAssertedIat == producedAt` exactly. That held only
    # while every signature was made in the same instant as the receipt it
    # covers — so the moment the platform signer stopped restating producedAt
    # (re-attesting an existing receipt's custody would otherwise claim the
    # signature was made days ago, which the trust bundle's anti-backdating
    # check rightly refuses), EVERY native-identity binding here began failing
    # with IDENTITY_SIGNED_PUBLICATION_INVALID, and no study could be bound to
    # a platform tenant at all. The CSL port of this same processor already
    # carries the resolution — a bounded skew window — and this is that rule,
    # stated identically. The receipt's own producedAt stays inside the signed
    # bytes; the envelope says when it was signed.
    skew_seconds = _signer_skew_seconds(statement, receipt)
    if (
        envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0"
        or envelope.get("signatureProfile") != "jws-detached-rfc7797/1.0"
        or descriptor.get("kind") != "native-identity-binding-receipt"
        or descriptor.get("payloadContract")
        != "accuratrials.cc.NativeIdentityBindingReceiptV1"
        or descriptor.get("payloadContractVersion") != "1.0.0"
        or descriptor.get("producerService") != producer_service
        or descriptor.get("tenantId") != receipt["tenantId"]
        or descriptor.get("purpose") != "native-identity-binding"
        or canonical_json(descriptor.get("payloadHash")) != canonical_json(expected_hash)
        or statement.get("signingPurpose") != "native-identity-binding"
        or skew_seconds is None
        or skew_seconds > 300
    ):
        raise NativeIdentityCommandError(
            "IDENTITY_SIGNED_PUBLICATION_INVALID",
            "Publisher returned an incorrectly scoped envelope.",
            503,
        )


class NativeIdentityCommandProcessorV1:
    def __init__(
        self,
        *,
        target_system: str,
        producer_service: str,
        namespaces: tuple[str, ...],
        object_types: tuple[str, ...],
        allowed_roles: tuple[str, ...],
        store: NativeIdentityStoreV1,
        publisher: NativeIdentityReceiptPublisherV1,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_uuid: Callable[[], str] = lambda: str(uuid4()),
    ):
        self.target_system = target_system
        self.producer_service = producer_service
        self.namespaces = namespaces
        self.object_types = object_types
        self.allowed_roles = allowed_roles
        self.store = store
        self.publisher = publisher
        self.clock = clock
        self.new_uuid = new_uuid

    def process(
        self,
        intent: ExternalIdentityCreateIntentV1,
        principal: NativeIdentityPrincipalV1,
    ) -> dict[str, Any]:
        validate_native_identity_intent(
            intent,
            principal,
            target_system=self.target_system,
            namespaces=self.namespaces,
            object_types=self.object_types,
            allowed_roles=self.allowed_roles,
            now=self.clock(),
        )

        def execute(tx: NativeIdentityTransactionV1) -> dict[str, Any]:
            existing = tx.find_by_intent(intent["intentId"])
            if existing is None:
                existing = tx.find_by_idempotency_key(intent["idempotencyKey"])
            if existing is not None:
                if existing["intentHashValue"] != intent["intentHash"]["value"]:
                    raise NativeIdentityCommandError(
                        "IDENTITY_IDEMPOTENCY_CONFLICT",
                        "Intent/key identifies different bytes.",
                    )
                return {**existing, "replay": True}
            effect = tx.apply_intent(intent)
            produced_at = _iso(self.clock())
            receipt: NativeIdentityBindingReceiptV1 = {
                "contractVersion": "1.0.0",
                "receiptId": self.new_uuid(),
                "intentId": intent["intentId"],
                "tenantId": intent["tenantId"],
                "platformStudyId": intent["platformStudyId"],
                "targetSystem": self.target_system,  # type: ignore[typeddict-item]
                "namespace": intent["namespace"],
                "objectType": intent["objectType"],
                "nativeIdentity": effect["nativeIdentity"],
                "effectType": effect["effectType"],
                "creationEffectId": effect["creationEffectId"],
                "idempotencyKey": intent["idempotencyKey"],
                "targetStateHash": platform_identity_hash(
                    effect["targetState"],
                    f"{self.target_system.upper()}NativeStudyRootStateV1@1.0.0",
                ),
                "createIntentHash": intent["intentHash"],
                "producedAt": produced_at,
            }
            for optional in ("nativeVersion", "previousBindingId"):
                if optional in effect:
                    receipt[optional] = effect[optional]  # type: ignore[literal-required]
            if effect.get("evidenceRefs"):
                receipt["evidenceRefs"] = sorted(set(effect["evidenceRefs"]))
            envelope = self.publisher.publish(receipt)
            _validate_signed_publication(receipt, envelope, self.producer_service)
            result = {
                "intentHashValue": intent["intentHash"]["value"],
                "receipt": receipt,
                "signedReceiptEnvelope": envelope,
            }
            tx.publish(result)
            return {**result, "replay": False}

        return self.store.serializable(
            intent["tenantId"], intent["platformStudyId"], execute
        )


__all__ = [
    "NativeIdentityCommandError",
    "NativeIdentityCommandProcessorV1",
    "NativeIdentityPrincipalV1",
    "platform_identity_hash",
    "validate_native_identity_intent",
]
