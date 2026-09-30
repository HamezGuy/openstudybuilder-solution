from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

import pytest

from clinical_mdr_api.generated.platform_contracts.native_identity_command_processor_v1 import (
    NativeIdentityCommandError,
    NativeIdentityCommandProcessorV1,
    NativeIdentityPrincipalV1,
    platform_identity_hash,
)
from clinical_mdr_api.services.integrations.native_identity import NAMESPACE, OBJECT_TYPE


TENANT = "11111111-1111-4111-8111-111111111111"
STUDY = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)


class MemoryOsbNativeIdentityStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.bindings: dict[str, dict[str, Any]] = {}
        self.effects_by_intent: dict[str, dict[str, Any]] = {}
        self.effects_by_key: dict[str, dict[str, Any]] = {}
        self.created = 0

    def serializable(self, tenant_id: str, platform_study_id: str, callback):
        with self._lock:
            return callback(_MemoryTransaction(self, tenant_id, platform_study_id))


class _MemoryTransaction:
    def __init__(self, store: MemoryOsbNativeIdentityStore, tenant_id: str, platform_study_id: str):
        self.store = store
        self.tenant_id = tenant_id
        self.platform_study_id = platform_study_id
        self.key = f"{tenant_id}|{platform_study_id}|{NAMESPACE}|{OBJECT_TYPE}"

    def find_by_intent(self, intent_id: str) -> dict[str, Any] | None:
        return self.store.effects_by_intent.get(intent_id)

    def find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        return self.store.effects_by_key.get(key)

    def apply_intent(self, intent: dict[str, Any]) -> dict[str, Any]:
        initial = intent["requestedInitialState"]
        operation = str(initial.get("operation") or ("create" if intent["expectedAbsence"] else "claim_existing"))
        active = self.store.bindings.get(self.key)
        if operation == "version_rollover":
            raise NativeIdentityCommandError("IDENTITY_VERSION_ROLLOVER_PRECONDITION_FAILED", "Rollover is not used in this fixture.")
        if active:
            raise NativeIdentityCommandError(
                "IDENTITY_MULTIPLE_NATIVE_ROOTS",
                "Platform study already has an active OSB root.",
            )
        supplied = str(initial.get("nativeIdentity") or "").strip()
        if intent["expectedAbsence"]:
            if supplied:
                raise NativeIdentityCommandError(
                    "IDENTITY_NATIVE_ID_ASSIGNMENT_UNSUPPORTED",
                    "OSB assigns the native draft identity during expected-absence creation.",
                    422,
                )
            self.store.created += 1
            native_identity = f"Study_MEMORY_{self.store.created}"
            effect_type = "created"
        else:
            if not supplied:
                raise NativeIdentityCommandError("IDENTITY_NATIVE_ROOT_NOT_FOUND", "Exact OSB root identity is required.", 404)
            native_identity = supplied
            effect_type = "claimed_existing"
        native_version = str(initial.get("nativeVersion") or "0.1")
        binding_id = str(uuid4())
        self.store.bindings[self.key] = {
            "bindingId": binding_id,
            "nativeIdentity": native_identity,
            "nativeVersion": native_version,
        }
        return {
            "nativeIdentity": native_identity,
            "nativeVersion": native_version,
            "effectType": effect_type,
            "creationEffectId": str(uuid4()),
            "targetState": {
                "nativeIdentity": native_identity,
                "nativeVersion": native_version,
                "status": "draft",
                "domainBindingId": binding_id,
            },
            "evidenceRefs": [f"osb-study:{native_identity}"],
        }

    def publish(self, result: dict[str, Any]) -> None:
        receipt = result["receipt"]
        self.store.effects_by_intent[receipt["intentId"]] = result
        self.store.effects_by_key[receipt["idempotencyKey"]] = result


class StubPublisher:
    def publish(self, receipt: dict[str, Any]) -> dict[str, Any]:
        payload_hash = platform_identity_hash(receipt, "NativeIdentityBindingReceiptV1@1.0.0")
        return {
            "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
            "signatureProfile": "jws-detached-rfc7797/1.0",
            "artifactDescriptor": {
                "kind": "native-identity-binding-receipt",
                "payloadContract": "accuratrials.cc.NativeIdentityBindingReceiptV1",
                "payloadContractVersion": "1.0.0",
                "producerService": "osb.package",
                "tenantId": receipt["tenantId"],
                "purpose": "native-identity-binding",
                "payloadHash": payload_hash,
            },
            "payloadHash": payload_hash,
            "signingStatement": {
                "signingPurpose": "native-identity-binding",
                "signerAssertedIat": receipt["producedAt"],
                "payloadHash": payload_hash,
            },
        }


def _principal() -> NativeIdentityPrincipalV1:
    return NativeIdentityPrincipalV1(
        tenant_id=TENANT,
        study_ids=(STUDY,),
        subject="service:command-center",
        human_subject=None,
        actor_chain=({"subject": "service:command-center", "type": "service"},),
        roles=("service",),
        purpose="workflow-orchestration",
        capabilities=("native-identity:bind",),
    )


def _processor(store: MemoryOsbNativeIdentityStore) -> NativeIdentityCommandProcessorV1:
    return NativeIdentityCommandProcessorV1(
        target_system="osb",
        producer_service="osb.package",
        namespaces=(NAMESPACE,),
        object_types=(OBJECT_TYPE,),
        allowed_roles=("service",),
        store=store,
        publisher=StubPublisher(),
        clock=lambda: NOW,
        new_uuid=lambda: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    )


def _intent(**overrides: Any) -> dict[str, Any]:
    statement = {
        "contractVersion": "1.0.0",
        "intentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "tenantId": TENANT,
        "platformStudyId": STUDY,
        "targetSystem": "osb",
        "namespace": NAMESPACE,
        "objectType": OBJECT_TYPE,
        "requestedInitialState": {"operation": "create"},
        "expectedAbsence": True,
        "commandId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "idempotencyKey": "p4-osb-binding-create-1",
        "actorSubject": "service:command-center",
        "purpose": "external-identity-create-intent",
        "expiresAt": "2099-12-31T23:59:59.000Z",
    }
    statement.update(overrides)
    return {**statement, "intentHash": platform_identity_hash(statement, "ExternalIdentityCreateIntentV1@1.0.0")}


def test_concurrent_create_replay_produces_one_signed_binding():
    store = MemoryOsbNativeIdentityStore()
    processor = _processor(store)
    principal = _principal()
    first = processor.process(_intent(), principal)
    replay = processor.process(_intent(), principal)
    assert first["replay"] is False
    assert replay["replay"] is True
    assert replay["receipt"]["receiptId"] == first["receipt"]["receiptId"]
    assert len(store.bindings) == 1
    assert first["receipt"]["nativeIdentity"] == "Study_MEMORY_1"
    assert first["signedReceiptEnvelope"]["signingStatement"]["signingPurpose"] == "native-identity-binding"


def test_second_create_for_the_same_study_is_multiple_root():
    store = MemoryOsbNativeIdentityStore()
    processor = _processor(store)
    principal = _principal()
    processor.process(_intent(), principal)
    second = _intent(
        intentId="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        commandId="ffffffff-ffff-4fff-8fff-ffffffffffff",
        idempotencyKey="p4-osb-binding-create-2",
    )
    with pytest.raises(NativeIdentityCommandError) as error:
        processor.process(second, principal)
    assert error.value.code == "IDENTITY_MULTIPLE_NATIVE_ROOTS"
    assert len(store.bindings) == 1


def test_spoofed_namespace_is_rejected():
    store = MemoryOsbNativeIdentityStore()
    processor = _processor(store)
    spoofed = _intent(namespace="accuratrials-csl", objectType="semantic-study-root")
    with pytest.raises(NativeIdentityCommandError) as error:
        processor.process(spoofed, _principal())
    assert error.value.code == "IDENTITY_INTENT_TARGET_MISMATCH"
    assert store.bindings == {}


def test_expected_absence_cannot_assign_a_native_id():
    store = MemoryOsbNativeIdentityStore()
    processor = _processor(store)
    assigned = _intent(requestedInitialState={"operation": "create", "nativeIdentity": "Study_SPOOFED"})
    with pytest.raises(NativeIdentityCommandError) as error:
        processor.process(assigned, _principal())
    assert error.value.code == "IDENTITY_NATIVE_ID_ASSIGNMENT_UNSUPPORTED"


def test_claim_existing_binds_the_supplied_root_once():
    store = MemoryOsbNativeIdentityStore()
    processor = _processor(store)
    intent = _intent(
        expectedAbsence=False,
        requestedInitialState={"operation": "claim_existing", "nativeIdentity": "Study_990001", "nativeVersion": "0.1"},
    )
    first = processor.process(intent, _principal())
    replay = processor.process(intent, _principal())
    assert first["receipt"]["nativeIdentity"] == "Study_990001"
    assert replay["replay"] is True
    assert len(store.bindings) == 1
