"""Commit a native mutation, its signed receipt and outbox in one transaction.

Unlike the prepare/commit executor, this variant supports native services that
assign IDs while writing. A failed signer or final commit rolls back all native
writes. Unsigned historical effects remain unsigned and cannot be promoted by
replaying their command identity.
"""

from datetime import UTC, datetime
import os
from typing import Any, Callable
from uuid import uuid4

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json_hash_ref
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandError, PlatformCommandPrincipalV1,
    RemotePlatformCommandReceiptPublisherV1, _normalized_effect, _signed_receipt,
    _validate_receipt_publication, validate_platform_command,
)
from common.config import settings


def execute_osb_atomic_signed_command(
    command: dict[str, Any], principal: PlatformCommandPrincipalV1,
    target_system: str, store: Any, handler: Callable,
    *, publisher: Any = None, clock: Callable = lambda: datetime.now(UTC),
):
    producer = "osb.package"
    validate_platform_command(command, principal, target_system, clock())
    if target_system != "osb":
        raise PlatformCommandError("COMMAND_TARGET_MISMATCH", "Only the OSB native owner may execute.", 422)
    if publisher is None:
        endpoint = os.getenv("OSB_PLATFORM_COMMAND_SIGNING_URL", "").strip()
        if not endpoint:
            raise PlatformCommandError("OSB_SIGNED_PUBLICATION_UNAVAILABLE", "Receipt signing is required.", 503)
        publisher = RemotePlatformCommandReceiptPublisherV1(
            endpoint, producer, settings.deployment_environment,
            allow_insecure_prototype=True,
        )

    def execute(tx):
        validate_platform_command(command, principal, target_system, clock())
        previous = tx.find_by_command_id(command["commandId"]) or tx.find_by_idempotency_key(
            command["targetCapability"], command["action"], command["idempotencyKey"]
        )
        if previous:
            if previous["commandIntentHashValue"] != command["commandIntentHash"]["value"] \
                    or previous.get("publicationMode") != "signed" \
                    or not previous.get("signedReceiptEnvelope"):
                raise PlatformCommandError("COMMAND_IDEMPOTENCY_CONFLICT", "An existing effect owns this command identity.")
            return {**previous, "replay": True, "publicationMode": "signed"}
        if tx.find_preparation_by_command_id(command["commandId"]) or tx.find_preparation_by_idempotency_key(
            command["targetCapability"], command["action"], command["idempotencyKey"]
        ):
            raise PlatformCommandError("COMMAND_PREPARATION_ALREADY_OWNED", "A separately prepared command must use its original recovery path.")
        started = clock().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        effect = _normalized_effect(handler(tx))
        validate_platform_command(command, principal, target_system, clock())
        completed = clock().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        preparation = {
            "preparationId": str(uuid4()), "state": "prepared", "command": command,
            "commandIntentHashValue": command["commandIntentHash"]["value"],
            "targetEffectId": str(uuid4()),
            "receipt": _signed_receipt(command, target_system, effect, started, completed, str(uuid4())),
            "effect": effect, "signedReceiptEnvelope": None, "signatureVerification": None,
        }
        preparation["preparationHashValue"] = canonical_json_hash_ref({
            "contractVersion": "SignedMutationPreparationV1@1.0.0",
            "preparationId": preparation["preparationId"], "commandId": command["commandId"],
            "commandIntentHashValue": preparation["commandIntentHashValue"],
            "targetEffectId": preparation["targetEffectId"],
            "receipt": preparation["receipt"], "effect": effect,
        }, schema_version="SignedMutationPreparationV1@1.0.0")["value"]
        tx.reserve_preparation(preparation)
        publication = publisher.publish(preparation["receipt"])
        _validate_receipt_publication(preparation, publication, producer)
        validate_platform_command(command, principal, target_system, clock())
        signed = tx.mark_preparation_signed(
            preparation["preparationId"], preparation["preparationHashValue"],
            publication["signedReceiptEnvelope"], publication["verification"],
        )
        tx.publish_signed(signed, command["requestingActor"]["issuerQualifiedSubject"], command["purpose"])
        return {
            "commandIntentHashValue": signed["commandIntentHashValue"],
            "targetEffectId": signed["targetEffectId"], "receipt": signed["receipt"],
            "effectPayload": signed["effect"]["effectPayload"],
            "signedReceiptEnvelope": signed["signedReceiptEnvelope"],
            "signatureVerification": signed["signatureVerification"],
            "replay": False, "publicationMode": "signed",
        }

    return store.serializable(command, execute)
