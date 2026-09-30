"""Runtime binding and prototype processor for CC-owned CommandEnvelopeV1."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
)

T = TypeVar("T")


class PlatformCommandError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class PlatformCommandPrincipalV1:
    tenant_id: str
    study_ids: tuple[str, ...]
    subject: str
    actor_chain: tuple[dict[str, str], ...]
    roles: tuple[str, ...]
    purpose: str
    capabilities: tuple[str, ...]


class PlatformCommandTransactionV1(Protocol):
    def find_by_command_id(self, command_id: str) -> dict[str, Any] | None: ...
    def find_by_idempotency_key(self, capability: str, action: str, key: str) -> dict[str, Any] | None: ...
    def publish(self, result: dict[str, Any], actor_subject: str, purpose: str) -> None: ...
    def find_preparation_by_command_id(self, command_id: str) -> dict[str, Any] | None: ...
    def find_preparation_by_idempotency_key(self, capability: str, action: str, key: str) -> dict[str, Any] | None: ...
    def reserve_preparation(self, preparation: dict[str, Any]) -> None: ...
    def mark_preparation_signed(
        self, preparation_id: str, preparation_hash: str,
        envelope: dict[str, Any], verification: dict[str, Any],
    ) -> dict[str, Any]: ...
    def publish_signed(self, preparation: dict[str, Any], actor_subject: str, purpose: str) -> None: ...


class PlatformCommandStoreV1(Protocol):
    def serializable(self, command: dict[str, Any], callback: Callable[[PlatformCommandTransactionV1], T]) -> T: ...
    def claim_recoverable(
        self, tenant_id: str, platform_study_id: str, lease_owner: str,
        limit: int = 20, lease_seconds: int = 60,
    ) -> list[dict[str, Any]]: ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request: Any, file_pointer: Any, code: int, message: str,
                         headers: Any, new_url: str) -> None:
        return None


class RemotePlatformCommandReceiptPublisherV1:
    """Short-lived signing call; credentials and grants never enter command custody."""

    def __init__(
        self, endpoint: str, producer_service: str, environment: str, *,
        allow_insecure_prototype: bool = False, timeout_seconds: float = 15.0,
        authorization: Callable[[], str | None] | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        production = environment.strip().lower() in {"prod", "production"}
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (production and parsed.scheme != "https")
                or (not production and parsed.scheme != "https"
                    and not (allow_insecure_prototype and parsed.scheme == "http"))):
            raise PlatformCommandError(
                "SIGNED_PUBLICATION_ENDPOINT_INVALID", "Signing endpoint transport is not allowed.", 503
            )
        self.endpoint = endpoint
        self.producer_service = producer_service
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self.authorization = authorization
        self.opener = build_opener(_RejectRedirects())

    def publish(self, receipt: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json", "Accept": "application/json",
            "X-Platform-Producer-Service": self.producer_service,
        }
        authorization = self.authorization() if self.authorization else None
        if authorization:
            headers["Authorization"] = authorization
        request = Request(
            self.endpoint,
            data=json.dumps({"receipt": receipt, "producerService": self.producer_service},
                            separators=(",", ":")).encode(),
            headers=headers, method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                if int(response.headers.get("Content-Length") or 0) > 2_000_000:
                    raise PlatformCommandError(
                        "SIGNED_PUBLICATION_RESPONSE_TOO_LARGE", "Signing response is too large.", 503
                    )
                payload = response.read(2_000_001)
        except (HTTPError, URLError, TimeoutError) as error:
            raise PlatformCommandError(
                "SIGNED_PUBLICATION_DEPENDENCY_UNAVAILABLE", "Signing service is unavailable.", 503
            ) from error
        if len(payload) > 2_000_000:
            raise PlatformCommandError(
                "SIGNED_PUBLICATION_RESPONSE_TOO_LARGE", "Signing response is too large.", 503
            )
        try:
            result = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PlatformCommandError(
                "SIGNED_PUBLICATION_RESPONSE_INVALID", "Signing response is invalid.", 503
            ) from error
        if not result.get("signedReceiptEnvelope") or not result.get("verification"):
            raise PlatformCommandError(
                "SIGNED_PUBLICATION_RESPONSE_INVALID", "Signing response is incomplete.", 503
            )
        return {
            "signedReceiptEnvelope": result["signedReceiptEnvelope"],
            "verification": result["verification"],
        }


SECRET_KEYS = {"accesstoken", "refreshtoken", "bearertoken", "authorizationheader", "password", "clientsecret", "privatekey", "cookie", "setcookie"}
PHI_KEYS = {"subjectid", "studysubjectid", "subjectidentifier", "subjecttoken", "patientid", "dob", "dateofbirth", "mrn", "medicalrecordnumber", "itemvalue", "labvalue", "narrative", "querytext", "deviationtext", "freetext"}


def _key(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _forbidden(value: Any, forbidden: set[str], path: str = "$") -> str | None:
    if isinstance(value, list):
        for index, child in enumerate(value):
            found = _forbidden(child, forbidden, f"{path}[{index}]")
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    for name, child in value.items():
        if _key(str(name)) in forbidden:
            return f"{path}.{name}"
        found = _forbidden(child, forbidden, f"{path}.{name}")
        if found:
            return found
    return None


def _instant(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError) as error:
        raise PlatformCommandError("COMMAND_TIME_INVALID", "Command time is invalid.", 422) from error


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def command_intent(command: dict[str, Any]) -> dict[str, Any]:
    return {
        "contractVersion": "CommandIntentV1@1.0.0",
        "tenantId": command["tenantId"],
        "platformStudyId": command["platformStudyId"],
        "requestingActor": command["requestingActor"],
        "purpose": command["purpose"],
        "targetSystem": command["targetSystem"],
        "targetCapability": command["targetCapability"],
        "action": command["action"],
        "expectedSourceState": command.get("expectedSourceState"),
        "expectedTargetState": command.get("expectedTargetState"),
        "inputHash": command["inputHash"],
        "authorizationDecisionId": command["authorizationDecisionRef"]["decisionId"],
    }


def validate_platform_command(command: dict[str, Any], principal: PlatformCommandPrincipalV1, target_system: str, now: datetime) -> None:
    if command.get("contractVersion") != "CommandEnvelopeV1@1.0.0":
        raise PlatformCommandError("COMMAND_VERSION_UNSUPPORTED", "Unsupported command contract.", 422)
    if command.get("targetSystem") != target_system:
        raise PlatformCommandError("COMMAND_TARGET_MISMATCH", "Command target differs.", 422)
    if principal.tenant_id != command.get("tenantId") or command.get("platformStudyId") not in principal.study_ids:
        raise PlatformCommandError("COMMAND_SCOPE_DENIED", "Tenant or platform study differs.", 403)
    if principal.purpose != command.get("purpose") or principal.purpose != "workflow-orchestration":
        raise PlatformCommandError("COMMAND_PURPOSE_DENIED", "Command purpose differs.", 403)
    if command.get("targetCapability") not in principal.capabilities:
        raise PlatformCommandError("COMMAND_CAPABILITY_DENIED", "Delegated token lacks target capability.", 403)
    actor_chain = command.get("requestingActor", {}).get("actorChain", [])
    actor = actor_chain[0].get("subject") if actor_chain else command.get("requestingActor", {}).get("issuerQualifiedSubject")
    principal_actor = principal.actor_chain[0].get("subject") if principal.actor_chain else principal.subject
    if actor != principal_actor:
        raise PlatformCommandError("COMMAND_ACTOR_MISMATCH", "Requesting actor chain differs.", 403)
    if _instant(command.get("deadlineAt")) <= now or _instant(command.get("authorizationDecisionRef", {}).get("expiresAt")) <= now:
        raise PlatformCommandError("COMMAND_EXPIRED", "Command or authorization expired.")
    if command.get("notBefore") and _instant(command["notBefore"]) > now:
        raise PlatformCommandError("COMMAND_NOT_READY", "Command not-before time has not arrived.", 425)
    secret = _forbidden(command, SECRET_KEYS)
    if secret:
        raise PlatformCommandError("COMMAND_EPHEMERAL_CREDENTIAL_PROHIBITED", f"Secret field at {secret}.", 422)
    phi = _forbidden(command.get("inputPayload", {}), PHI_KEYS)
    if phi:
        raise PlatformCommandError("COMMAND_CENTRAL_PHI_PROHIBITED", f"Central PHI field at {phi}.", 422)
    expected_input = canonical_json_hash_ref(command.get("inputPayload", {}), schema_version="CommandInputV1@1.0.0")
    if canonical_json(command.get("inputHash")) != canonical_json(expected_input):
        raise PlatformCommandError("COMMAND_INPUT_HASH_MISMATCH", "Input hash differs.", 422)
    expected_intent = canonical_json_hash_ref(command_intent(command), schema_version="CommandIntentV1@1.0.0")
    if canonical_json(command.get("commandIntentHash")) != canonical_json(expected_intent):
        raise PlatformCommandError("COMMAND_INTENT_HASH_MISMATCH", "Command intent hash differs.", 422)


def execute_platform_command(
    command: dict[str, Any],
    principal: PlatformCommandPrincipalV1,
    target_system: str,
    store: PlatformCommandStoreV1,
    handler: Callable[[PlatformCommandTransactionV1], dict[str, Any]],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    validate_platform_command(command, principal, target_system, clock())

    def execute(tx: PlatformCommandTransactionV1) -> dict[str, Any]:
        existing = tx.find_by_command_id(command["commandId"]) or tx.find_by_idempotency_key(
            command["targetCapability"], command["action"], command["idempotencyKey"]
        )
        if existing:
            if existing["commandIntentHashValue"] != command["commandIntentHash"]["value"]:
                raise PlatformCommandError("COMMAND_IDEMPOTENCY_CONFLICT", "Idempotency key identifies different intent.")
            # Replay is transport metadata. The original receipt is immutable and its
            # canonical bytes must not change after publication.
            return {**existing, "replay": True, "publicationMode": "prototype_unsigned"}
        started_at = _iso(clock())
        effect = handler(tx)
        status = effect.get("status", "succeeded")
        target_state = effect.get("targetState")
        receipt = {
            "contractVersion": "ReceiptEnvelopeV1@1.0.0",
            "receiptId": str(uuid4()), "revision": 1, "supersedesReceiptId": None,
            "commandId": command["commandId"], "workflowId": command["workflowId"],
            "workflowStepId": command["workflowStepId"], "correlationId": command["correlationId"],
            "tenantId": command["tenantId"], "platformStudyId": command["platformStudyId"],
            "targetSystem": target_system, "status": status, "replay": False,
            "startedAt": started_at,
            "completedAt": None if status in {"accepted", "running", "blocked", "quarantined"} else _iso(clock()),
            "actor": command["requestingActor"],
            "consumedArtifacts": effect.get("consumedArtifacts", []),
            "producedArtifacts": effect.get("producedArtifacts", []),
            "targetIdentity": effect.get("targetIdentity"), "targetVersion": effect.get("targetVersion"),
            "targetStateHash": None if target_state is None else canonical_json_hash_ref(target_state, schema_version=f"{target_system.upper()}TargetStateV1@1.0.0"),
            "conservationCounts": effect.get("conservationCounts", {}),
            "blockers": effect.get("blockers", []),
            "warnings": [*effect.get("warnings", []), {"code": "PROTOTYPE_UNSIGNED_RECEIPT", "productionEligible": False}],
            "error": effect.get("error"),
        }
        result = {
            "commandIntentHashValue": command["commandIntentHash"]["value"],
            "targetEffectId": str(uuid4()), "receipt": receipt,
            "effectPayload": effect.get("effectPayload", {}),
        }
        try:
            tx.publish(result, command["requestingActor"]["issuerQualifiedSubject"], command["purpose"])
        except Exception as error:
            code = str(getattr(error, "code", ""))
            if "ConstraintValidationFailed" in code or error.__class__.__name__ == "ConstraintError":
                raise PlatformCommandError(
                    "COMMAND_IDEMPOTENCY_CONFLICT",
                    "Command identity or scoped idempotency key already identifies another result.",
                ) from error
            raise
        return {**result, "replay": False, "publicationMode": "prototype_unsigned"}

    return store.serializable(command, execute)


def _normalized_effect(effect: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": effect.get("status", "succeeded"),
        "targetIdentity": effect.get("targetIdentity"),
        "targetVersion": effect.get("targetVersion"),
        "targetState": effect.get("targetState"),
        "consumedArtifacts": effect.get("consumedArtifacts", []),
        "producedArtifacts": effect.get("producedArtifacts", []),
        "conservationCounts": effect.get("conservationCounts", {}),
        "blockers": effect.get("blockers", []),
        "warnings": effect.get("warnings", []),
        "error": effect.get("error"),
        "effectPayload": effect.get("effectPayload", {}),
    }


def _signed_receipt(
    command: dict[str, Any], target_system: str, effect: dict[str, Any],
    started_at: str, completed_at: str, receipt_id: str,
) -> dict[str, Any]:
    target_state = effect["targetState"]
    return {
        "contractVersion": "ReceiptEnvelopeV1@1.0.0",
        "receiptId": receipt_id, "revision": 1, "supersedesReceiptId": None,
        "commandId": command["commandId"], "workflowId": command["workflowId"],
        "workflowStepId": command["workflowStepId"], "correlationId": command["correlationId"],
        "tenantId": command["tenantId"], "platformStudyId": command["platformStudyId"],
        "targetSystem": target_system, "status": effect["status"], "replay": False,
        "startedAt": started_at,
        "completedAt": None if effect["status"] in {"accepted", "running", "blocked", "quarantined"} else completed_at,
        "actor": command["requestingActor"],
        "consumedArtifacts": effect["consumedArtifacts"],
        "producedArtifacts": effect["producedArtifacts"],
        "targetIdentity": effect["targetIdentity"], "targetVersion": effect["targetVersion"],
        "targetStateHash": None if target_state is None else canonical_json_hash_ref(
            target_state, schema_version=f"{target_system.upper()}TargetStateV1@1.0.0"
        ),
        "conservationCounts": effect["conservationCounts"], "blockers": effect["blockers"],
        "warnings": effect["warnings"], "error": effect["error"],
    }


def _validate_receipt_publication(
    preparation: dict[str, Any], publication: dict[str, Any], producer_service: str,
) -> None:
    envelope = publication.get("signedReceiptEnvelope") or {}
    verification = publication.get("verification") or {}
    descriptor = envelope.get("artifactDescriptor") or {}
    statement = envelope.get("signingStatement") or {}
    expected_payload_hash = canonical_json_hash_ref(
        preparation["receipt"], schema_version="ReceiptEnvelopeV1@1.0.0"
    )
    expected_envelope_hash = canonical_json_hash_ref(
        envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"
    )
    if (
        envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0"
        or envelope.get("signatureProfile") != "jws-detached-rfc7797/1.0"
        or descriptor.get("kind") != "platform-command-receipt"
        or descriptor.get("payloadContract") != "accuratrials.cc.ReceiptEnvelopeV1"
        or descriptor.get("payloadContractVersion") != "1.0.0"
        or descriptor.get("producerService") != producer_service
        or descriptor.get("tenantId") != preparation["receipt"]["tenantId"]
        or descriptor.get("purpose") != "command-receipt"
        or statement.get("signingPurpose") != "command-receipt"
        or canonical_json(descriptor.get("payloadHash")) != canonical_json(expected_payload_hash)
        or verification.get("verified") is not True
        or canonical_json(verification.get("payloadHash")) != canonical_json(expected_payload_hash)
        or canonical_json(verification.get("envelopeHash")) != canonical_json(expected_envelope_hash)
        or not verification.get("trustedTime")
    ):
        raise PlatformCommandError(
            "SIGNED_PUBLICATION_INVALID",
            "KMS/RFC3161 publisher returned an invalid receipt envelope.", 503,
        )


def execute_signed_platform_command(
    command: dict[str, Any],
    principal: PlatformCommandPrincipalV1,
    target_system: str,
    producer_service: str,
    store: PlatformCommandStoreV1,
    handler: Any,
    publisher: Any,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    new_uuid: Callable[[], str] = lambda: str(uuid4()),
) -> dict[str, Any]:
    """Fail-closed mutation path with durable preparation and signed final commit."""
    validate_platform_command(command, principal, target_system, clock())

    def find_custody(tx: PlatformCommandTransactionV1) -> dict[str, Any] | None:
        published = tx.find_by_command_id(command["commandId"]) or tx.find_by_idempotency_key(
            command["targetCapability"], command["action"], command["idempotencyKey"]
        )
        if published:
            if (published["commandIntentHashValue"] != command["commandIntentHash"]["value"]
                    or published.get("publicationMode") != "signed"
                    or not published.get("signedReceiptEnvelope")):
                raise PlatformCommandError("COMMAND_IDEMPOTENCY_CONFLICT", "Command identity is already owned.")
            return {"published": published}
        existing = tx.find_preparation_by_command_id(command["commandId"]) or tx.find_preparation_by_idempotency_key(
            command["targetCapability"], command["action"], command["idempotencyKey"]
        )
        if existing and existing["commandIntentHashValue"] != command["commandIntentHash"]["value"]:
            raise PlatformCommandError("COMMAND_IDEMPOTENCY_CONFLICT", "Preparation owns different intent.")
        return {"preparation": existing} if existing else None

    custody = store.serializable(command, find_custody)
    if custody and "published" in custody:
        return {**custody["published"], "replay": True, "publicationMode": "signed"}

    preparation = custody["preparation"] if custody else None
    if preparation is None:
        # Recovery resumes exact durable bytes; preparation runs only for a new
        # command and is required to be deterministic and side-effect free.
        effect = _normalized_effect(handler.prepare())
        started_at = _iso(clock())
        proposed = {
            "preparationId": new_uuid(), "state": "prepared", "command": command,
            "commandIntentHashValue": command["commandIntentHash"]["value"],
            "targetEffectId": new_uuid(),
            "receipt": _signed_receipt(command, target_system, effect, started_at, _iso(clock()), new_uuid()),
            "effect": effect, "preparationHashValue": "",
            "signedReceiptEnvelope": None, "signatureVerification": None,
        }
        proposed["preparationHashValue"] = canonical_json_hash_ref({
            "contractVersion": "SignedMutationPreparationV1@1.0.0",
            "preparationId": proposed["preparationId"], "commandId": command["commandId"],
            "commandIntentHashValue": proposed["commandIntentHashValue"],
            "targetEffectId": proposed["targetEffectId"], "receipt": proposed["receipt"],
            "effect": proposed["effect"],
        }, schema_version="SignedMutationPreparationV1@1.0.0")["value"]

        def reserve(tx: PlatformCommandTransactionV1) -> dict[str, Any]:
            published = tx.find_by_command_id(command["commandId"]) or tx.find_by_idempotency_key(
                command["targetCapability"], command["action"], command["idempotencyKey"]
            )
            if published:
                if (published["commandIntentHashValue"] != command["commandIntentHash"]["value"]
                        or published.get("publicationMode") != "signed"
                        or not published.get("signedReceiptEnvelope")):
                    raise PlatformCommandError("COMMAND_IDEMPOTENCY_CONFLICT", "Command identity is already owned.")
                return {"published": published}
            existing = tx.find_preparation_by_command_id(command["commandId"]) or tx.find_preparation_by_idempotency_key(
                command["targetCapability"], command["action"], command["idempotencyKey"]
            )
            if existing:
                if existing["commandIntentHashValue"] != command["commandIntentHash"]["value"]:
                    raise PlatformCommandError("COMMAND_IDEMPOTENCY_CONFLICT", "Preparation owns different intent.")
                return {"preparation": existing}
            tx.reserve_preparation(proposed)
            return {"preparation": proposed}

        custody = store.serializable(command, reserve)
        if "published" in custody:
            return {**custody["published"], "replay": True, "publicationMode": "signed"}
        preparation = custody["preparation"]
    if preparation["state"] == "prepared":
        publication = publisher.publish(preparation["receipt"])
        _validate_receipt_publication(preparation, publication, producer_service)

        def attach(tx: PlatformCommandTransactionV1) -> dict[str, Any]:
            latest = tx.find_preparation_by_command_id(command["commandId"])
            if not latest:
                raise PlatformCommandError("SIGNED_PREPARATION_LOST", "Prepared mutation is missing.", 503)
            if latest["state"] != "prepared":
                return latest
            return tx.mark_preparation_signed(
                latest["preparationId"], latest["preparationHashValue"],
                publication["signedReceiptEnvelope"], publication["verification"],
            )
        preparation = store.serializable(command, attach)
    if (preparation["state"] != "signed" or not preparation.get("signedReceiptEnvelope")):
        raise PlatformCommandError("SIGNED_PREPARATION_NOT_PUBLISHABLE", "Mutation preparation is not signed.", 503)

    def commit(tx: PlatformCommandTransactionV1) -> dict[str, Any]:
        published = tx.find_by_command_id(command["commandId"]) or tx.find_by_idempotency_key(
            command["targetCapability"], command["action"], command["idempotencyKey"]
        )
        if published:
            if (published["commandIntentHashValue"] != command["commandIntentHash"]["value"]
                    or published.get("publicationMode") != "signed"):
                raise PlatformCommandError("COMMAND_IDEMPOTENCY_CONFLICT", "Publication owns another result.")
            return {**published, "replay": True, "publicationMode": "signed"}
        latest = tx.find_preparation_by_command_id(command["commandId"])
        if not latest or latest["state"] != "signed":
            raise PlatformCommandError("SIGNED_PREPARATION_NOT_PUBLISHABLE", "Signed preparation is unavailable.", 503)
        committed = _normalized_effect(handler.commit(tx, latest["effect"]))
        if canonical_json(committed) != canonical_json(latest["effect"]):
            raise PlatformCommandError("SIGNED_EFFECT_PREPARATION_MISMATCH", "Final effect differs from signed preparation.")
        tx.publish_signed(latest, command["requestingActor"]["issuerQualifiedSubject"], command["purpose"])
        return {
            "commandIntentHashValue": latest["commandIntentHashValue"],
            "targetEffectId": latest["targetEffectId"], "receipt": latest["receipt"],
            "effectPayload": latest["effect"]["effectPayload"],
            "signedReceiptEnvelope": latest["signedReceiptEnvelope"],
            "signatureVerification": latest["signatureVerification"],
            "replay": False, "publicationMode": "signed",
        }

    return store.serializable(command, commit)


def reclaim_prepared_platform_commands(
    store: PlatformCommandStoreV1,
    tenant_id: str,
    platform_study_id: str,
    lease_owner: str,
    resolve: Callable[[dict[str, Any]], dict[str, Any]],
    *, limit: int = 20, lease_seconds: int = 60,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for preparation in store.claim_recoverable(
        tenant_id, platform_study_id, lease_owner, limit, lease_seconds
    ):
        try:
            context = resolve(preparation["command"])
            execute_signed_platform_command(
                preparation["command"], context["principal"],
                preparation["command"]["targetSystem"], context["producerService"],
                store, context["handler"], context["publisher"],
            )
            results.append({"preparationId":preparation["preparationId"],
                            "commandId":preparation["command"]["commandId"],"status":"published"})
        except Exception as error:  # safe retry metadata only
            results.append({"preparationId":preparation["preparationId"],
                            "commandId":preparation["command"]["commandId"],"status":"retryable",
                            "code":str(getattr(error,"code","SIGNED_PUBLICATION_RECOVERY_FAILED"))[:128]})
    return results


__all__ = [
    "PlatformCommandError", "PlatformCommandPrincipalV1", "PlatformCommandTransactionV1",
    "RemotePlatformCommandReceiptPublisherV1",
    "execute_platform_command", "execute_signed_platform_command", "reclaim_prepared_platform_commands",
]
