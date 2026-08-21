"""Atomic, default-off OSB native draft-root identity boundary."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar
from uuid import uuid4

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.generated.platform_contracts.models_v1 import ExternalIdentityCreateIntentV1
from clinical_mdr_api.generated.platform_contracts.native_identity_command_processor_v1 import NativeIdentityCommandError
from clinical_mdr_api.models.study_selections.study import StudyCreateInput
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.services.studies.study import StudyService

T = TypeVar("T")
NAMESPACE = "accuratrials-osb"
OBJECT_TYPE = "study-draft-root"


def _text(value: Any, name: str, *, required: bool = True) -> str | None:
    result = value.strip() if isinstance(value, str) else ""
    if required and not result:
        raise NativeIdentityCommandError("IDENTITY_INITIAL_STATE_INVALID", f"{name} is required.", 422)
    return result or None


def _state(intent: ExternalIdentityCreateIntentV1) -> dict[str, Any]:
    value = intent.get("requestedInitialState")
    if not isinstance(value, dict):
        raise NativeIdentityCommandError(
            "IDENTITY_INITIAL_STATE_INVALID", "requestedInitialState must be an object.", 422
        )
    return value


def _platform_key(tenant_id: str, platform_study_id: str) -> str:
    return f"{tenant_id}|{platform_study_id}|{NAMESPACE}|{OBJECT_TYPE}"


def _native_key(tenant_id: str, native_study_id: str) -> str:
    return f"{tenant_id}|{NAMESPACE}|{OBJECT_TYPE}|{native_study_id}"


class Neo4jOsbNativeIdentityTransactionV1:
    def __init__(self, tenant_id: str, platform_study_id: str):
        self.tenant_id = tenant_id
        self.platform_study_id = platform_study_id
        self.actor_subject: str | None = None

    @staticmethod
    def _published(row: list[Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "intentHashValue": row[0],
            "receipt": json.loads(row[1]),
            "signedReceiptEnvelope": json.loads(row[2]),
        }

    def find_by_intent(self, intent_id: str) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (e:PlatformNativeIdentityEffect {
                 intent_id: $intent_id, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id})
               RETURN e.intent_hash_value, e.receipt_json, e.signed_envelope_json LIMIT 1""",
            {
                "intent_id": intent_id,
                "tenant_id": self.tenant_id,
                "platform_study_id": self.platform_study_id,
            },
        )
        return self._published(rows[0] if rows else None)

    def find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (e:PlatformNativeIdentityEffect {
                 tenant_id: $tenant_id, namespace: $namespace,
                 object_type: $object_type, idempotency_key: $key})
               RETURN e.intent_hash_value, e.receipt_json, e.signed_envelope_json LIMIT 1""",
            {
                "tenant_id": self.tenant_id,
                "namespace": NAMESPACE,
                "object_type": OBJECT_TYPE,
                "key": key,
            },
        )
        return self._published(rows[0] if rows else None)

    def _active_binding(self) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (key:PlatformNativeStudyPlatformKey {key: $key})
               MATCH (binding:PlatformNativeStudyBinding {binding_id: key.binding_id})
               RETURN binding.binding_id, binding.native_study_id,
                      binding.native_version, binding.status LIMIT 1""",
            {"key": _platform_key(self.tenant_id, self.platform_study_id)},
        )
        if not rows:
            return None
        return {
            "bindingId": rows[0][0],
            "nativeIdentity": rows[0][1],
            "nativeVersion": rows[0][2],
            "status": rows[0][3],
        }

    @staticmethod
    def _native_checkpoint(native_identity: str) -> tuple[str, str]:
        rows, _ = db.cypher_query(
            """MATCH (study:StudyRoot {uid: $uid})
               OPTIONAL MATCH (study)-[version_rel:LATEST_DRAFT|LATEST|LATEST_LOCKED|LATEST_RELEASED]->(:StudyValue)
               WITH study, version_rel,
                    CASE type(version_rel)
                      WHEN 'LATEST_DRAFT' THEN 0
                      WHEN 'LATEST' THEN 1
                      WHEN 'LATEST_LOCKED' THEN 2
                      WHEN 'LATEST_RELEASED' THEN 3
                      ELSE 4
                    END AS preference
               ORDER BY preference
               RETURN study.uid, version_rel.version, version_rel.status LIMIT 1""",
            {"uid": native_identity},
        )
        if not rows:
            raise NativeIdentityCommandError(
                "IDENTITY_NATIVE_ROOT_NOT_FOUND", "Exact OSB root does not exist.", 404
            )
        native_version = _text(rows[0][1], "nativeVersion", required=False)
        native_status = _text(rows[0][2], "nativeStatus", required=False)
        if not native_version or not native_status:
            raise NativeIdentityCommandError(
                "IDENTITY_NATIVE_VERSION_UNAVAILABLE",
                "OSB root has no verifiable current native version checkpoint.",
                409,
            )
        return native_version, native_status.lower()

    def _assert_native_available(self, native_identity: str) -> None:
        rows, _ = db.cypher_query(
            """OPTIONAL MATCH (retired:PlatformNativeStudyBinding {
                 tenant_id: $tenant_id, namespace: $namespace,
                 object_type: $object_type, native_study_id: $native_study_id,
                 status: 'retired'})
               OPTIONAL MATCH (key:PlatformNativeStudyNativeKey {key: $native_key})
               RETURN retired IS NOT NULL, key.binding_id""",
            {
                "tenant_id": self.tenant_id,
                "namespace": NAMESPACE,
                "object_type": OBJECT_TYPE,
                "native_study_id": native_identity,
                "native_key": _native_key(self.tenant_id, native_identity),
            },
        )
        if rows and rows[0][0]:
            raise NativeIdentityCommandError("IDENTITY_RETIRED_ROOT_REUSE", "Retired OSB root cannot be rebound.")
        if rows and rows[0][1]:
            raise NativeIdentityCommandError("IDENTITY_NATIVE_ROOT_ALREADY_BOUND", "OSB root is already bound.")

    def _assert_tenant_scope(self, native_identity: str) -> None:
        rows, _ = db.cypher_query(
            """MATCH (:StudyRoot {uid: $uid})
               MATCH (:DomainStudyScope {
                 study_uid: $uid, tenant_id: $tenant_id, status: 'active'})
               RETURN 1 LIMIT 1""",
            {"uid": native_identity, "tenant_id": self.tenant_id},
        )
        if not rows:
            raise NativeIdentityCommandError(
                "IDENTITY_TENANT_STUDY_SCOPE_MISMATCH",
                "OSB root is not actively bound to the exact platform tenant.",
                403,
            )

    def _create_binding(self, native_identity: str, native_version: str) -> str:
        binding_id = str(uuid4())
        parameters = {
            "platform_key": _platform_key(self.tenant_id, self.platform_study_id),
            "native_key": _native_key(self.tenant_id, native_identity),
            "binding_id": binding_id,
            "tenant_id": self.tenant_id,
            "platform_study_id": self.platform_study_id,
            "namespace": NAMESPACE,
            "object_type": OBJECT_TYPE,
            "native_study_id": native_identity,
            "native_version": native_version,
            "actor_subject": self.actor_subject,
        }
        rows, _ = db.cypher_query(
            """MERGE (platform:PlatformNativeStudyPlatformKey {key: $platform_key})
                 ON CREATE SET platform.created_at = datetime()
               MERGE (native:PlatformNativeStudyNativeKey {key: $native_key})
                 ON CREATE SET native.created_at = datetime()
               RETURN platform.binding_id, native.binding_id""",
            parameters,
        )
        if not rows or rows[0][0] is not None or rows[0][1] is not None:
            raise NativeIdentityCommandError(
                "IDENTITY_BINDING_CONFLICT", "An active platform or native OSB binding already exists."
            )
        created, _ = db.cypher_query(
            """MATCH (platform:PlatformNativeStudyPlatformKey {key: $platform_key})
               MATCH (native:PlatformNativeStudyNativeKey {key: $native_key})
               MATCH (study:StudyRoot {uid: $native_study_id})
               CREATE (binding:PlatformNativeStudyBinding {
                 binding_id: $binding_id, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id, namespace: $namespace,
                 object_type: $object_type, native_study_id: $native_study_id,
                 native_version: $native_version, status: 'active',
                 actor_subject: $actor_subject, created_at: datetime()
               })-[:BINDS_NATIVE_ROOT]->(study)
               SET platform.binding_id = $binding_id, native.binding_id = $binding_id
               RETURN binding.binding_id""",
            parameters,
        )
        if not created or created[0][0] != binding_id:
            raise NativeIdentityCommandError("IDENTITY_NATIVE_ROOT_NOT_FOUND", "Exact OSB root does not exist.", 404)
        return binding_id

    def _rollover(self, intent: ExternalIdentityCreateIntentV1, initial: dict[str, Any], active: dict[str, Any] | None) -> dict[str, Any]:
        native_identity = _text(initial.get("nativeIdentity"), "nativeIdentity")
        native_version = _text(initial.get("nativeVersion"), "nativeVersion")
        previous_binding_id = _text(initial.get("previousBindingId"), "previousBindingId")
        if (
            intent["expectedAbsence"]
            or not active
            or active["nativeIdentity"] != native_identity
            or active["nativeVersion"] == native_version
            or active["bindingId"] != previous_binding_id
        ):
            raise NativeIdentityCommandError(
                "IDENTITY_VERSION_ROLLOVER_PRECONDITION_FAILED", "Version rollover precondition differs."
            )
        actual_version, actual_status = self._native_checkpoint(native_identity)
        if actual_version != native_version:
            raise NativeIdentityCommandError(
                "IDENTITY_NATIVE_VERSION_MISMATCH",
                "OSB root has not reached the requested native version.",
            )
        binding_id = str(uuid4())
        rows, _ = db.cypher_query(
            """MATCH (old:PlatformNativeStudyBinding {binding_id: $previous, status: 'active'})
               MATCH (platform:PlatformNativeStudyPlatformKey {key: $platform_key, binding_id: $previous})
               MATCH (native:PlatformNativeStudyNativeKey {key: $native_key, binding_id: $previous})
               MATCH (study:StudyRoot {uid: $native_study_id})
               SET old.status = 'retired', old.retired_at = datetime()
               CREATE (next:PlatformNativeStudyBinding {
                 binding_id: $binding_id, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id, namespace: $namespace,
                 object_type: $object_type, native_study_id: $native_study_id,
                 native_version: $native_version, status: 'active',
                 previous_binding_id: $previous, actor_subject: $actor_subject,
                 created_at: datetime()
               })-[:BINDS_NATIVE_ROOT]->(study)
               SET platform.binding_id = $binding_id, native.binding_id = $binding_id
               RETURN next.binding_id""",
            {
                "previous": previous_binding_id,
                "platform_key": _platform_key(self.tenant_id, self.platform_study_id),
                "native_key": _native_key(self.tenant_id, native_identity),
                "binding_id": binding_id,
                "tenant_id": self.tenant_id,
                "platform_study_id": self.platform_study_id,
                "namespace": NAMESPACE,
                "object_type": OBJECT_TYPE,
                "native_study_id": native_identity,
                "native_version": native_version,
                "actor_subject": self.actor_subject,
            },
        )
        if not rows:
            raise NativeIdentityCommandError(
                "IDENTITY_VERSION_ROLLOVER_PRECONDITION_FAILED", "Active OSB binding changed concurrently."
            )
        return {
            "nativeIdentity": native_identity,
            "nativeVersion": native_version,
            "effectType": "version_rollover",
            "creationEffectId": str(uuid4()),
            "previousBindingId": previous_binding_id,
            "targetState": {
                "nativeIdentity": native_identity,
                "nativeVersion": native_version,
                "status": actual_status,
                "domainBindingId": binding_id,
            },
        }

    def apply_intent(self, intent: ExternalIdentityCreateIntentV1) -> dict[str, Any]:
        self.actor_subject = intent["actorSubject"]
        initial = _state(intent)
        operation = str(initial.get("operation") or ("create" if intent["expectedAbsence"] else "claim_existing"))
        active = self._active_binding()
        if operation == "version_rollover":
            return self._rollover(intent, initial, active)
        if active:
            raise NativeIdentityCommandError("IDENTITY_MULTIPLE_NATIVE_ROOTS", "Platform study already has an active OSB root.")

        supplied = _text(initial.get("nativeIdentity"), "nativeIdentity", required=False)
        if intent["expectedAbsence"]:
            if supplied:
                raise NativeIdentityCommandError(
                    "IDENTITY_NATIVE_ID_ASSIGNMENT_UNSUPPORTED",
                    "OSB assigns the native draft identity during expected-absence creation.",
                    422,
                )
            created = StudyService().create(
                StudyCreateInput(
                    project_number=_text(initial.get("projectNumber"), "projectNumber"),
                    study_number=_text(initial.get("studyNumber"), "studyNumber", required=False),
                    study_acronym=_text(initial.get("studyAcronym"), "studyAcronym", required=False),
                    description=_text(initial.get("description"), "description", required=False),
                )
            )
            native_identity, effect_type = created.uid, "created"
        else:
            if not supplied:
                raise NativeIdentityCommandError("IDENTITY_NATIVE_ROOT_NOT_FOUND", "Exact OSB root identity is required.", 404)
            native_identity, effect_type = supplied, "claimed_existing"

        self._assert_tenant_scope(native_identity)
        self._assert_native_available(native_identity)
        native_version, native_status = self._native_checkpoint(native_identity)
        requested_version = _text(initial.get("nativeVersion"), "nativeVersion", required=False)
        if requested_version and requested_version != native_version:
            raise NativeIdentityCommandError(
                "IDENTITY_NATIVE_VERSION_MISMATCH",
                "Requested OSB native version differs from the exact root checkpoint.",
            )
        binding_id = self._create_binding(native_identity, native_version)
        return {
            "nativeIdentity": native_identity,
            "nativeVersion": native_version,
            "effectType": effect_type,
            "creationEffectId": str(uuid4()),
            "targetState": {
                "nativeIdentity": native_identity,
                "nativeVersion": native_version,
                "status": native_status,
                "domainBindingId": binding_id,
            },
            "evidenceRefs": [f"osb-study:{native_identity}"],
        }

    def publish(self, result: dict[str, Any]) -> None:
        if not self.actor_subject:
            raise NativeIdentityCommandError("IDENTITY_AUDIT_ACTOR_MISSING", "Requesting actor was not retained.", 500)
        receipt = result["receipt"]
        event = {
            "tenantId": self.tenant_id,
            "platformStudyId": self.platform_study_id,
            "nativeStudyId": receipt["nativeIdentity"],
            "action": "PLATFORM_NATIVE_IDENTITY_PUBLISHED",
            "receiptId": receipt["receiptId"],
            "actorSubject": self.actor_subject,
            "outcome": "succeeded",
        }
        rows, _ = db.cypher_query(
            """CREATE (effect:PlatformNativeIdentityEffect {
                 intent_id: $intent_id, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id, namespace: $namespace,
                 object_type: $object_type, idempotency_key: $idempotency_key,
                 intent_hash_value: $intent_hash_value, native_study_id: $native_study_id,
                 effect_type: $effect_type, creation_effect_id: $creation_effect_id,
                 receipt_json: $receipt_json, signed_envelope_json: $envelope_json,
                 published_at: datetime()})
               CREATE (audit:PlatformNativeIdentityAudit {
                 audit_id: $audit_id, event_hash: $event_hash, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id, native_study_id: $native_study_id,
                 action: 'PLATFORM_NATIVE_IDENTITY_PUBLISHED', actor_subject: $actor_subject,
                 receipt_id: $receipt_id, outcome: 'succeeded', created_at: datetime()})
               CREATE (audit)-[:AUDITS_EFFECT]->(effect)
               RETURN effect.intent_id""",
            {
                "intent_id": receipt["intentId"],
                "tenant_id": receipt["tenantId"],
                "platform_study_id": receipt["platformStudyId"],
                "namespace": receipt["namespace"],
                "object_type": receipt["objectType"],
                "idempotency_key": receipt["idempotencyKey"],
                "intent_hash_value": result["intentHashValue"],
                "native_study_id": receipt["nativeIdentity"],
                "effect_type": receipt["effectType"],
                "creation_effect_id": receipt["creationEffectId"],
                "receipt_json": canonical_json(receipt),
                "envelope_json": canonical_json(result["signedReceiptEnvelope"]),
                "audit_id": str(uuid4()),
                "event_hash": canonical_hash(event),
                "actor_subject": self.actor_subject,
                "receipt_id": receipt["receiptId"],
            },
        )
        if not rows:
            raise NativeIdentityCommandError("IDENTITY_PUBLICATION_FAILED", "OSB identity publication failed.", 500)


class Neo4jOsbNativeIdentityStoreV1:
    def serializable(
        self,
        tenant_id: str,
        platform_study_id: str,
        callback: Callable[[Neo4jOsbNativeIdentityTransactionV1], T],
    ) -> T:
        with db.transaction:
            db.cypher_query(
                """MERGE (lock:PlatformNativeIdentityLock {key: $key})
                   ON CREATE SET lock.created_at = datetime(), lock.revision = 0
                   SET lock.revision = lock.revision + 1, lock.touched_at = datetime()
                   RETURN lock.revision""",
                {"key": f"{tenant_id}|{platform_study_id}"},
            )
            return callback(Neo4jOsbNativeIdentityTransactionV1(tenant_id, platform_study_id))


__all__ = ["Neo4jOsbNativeIdentityStoreV1"]
