"""Transaction boundaries for native mutations that assign their own IDs."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandError,
    PlatformCommandPrincipalV1,
)
from clinical_mdr_api.services.integrations.atomic_signed_command import (
    execute_osb_atomic_signed_command,
)
from clinical_mdr_api.tests.unit.services.test_candidate_signed_publication import (
    MemoryCommandStore,
    StubPublisher,
    TENANT,
    STUDY,
)
from verify_signed_command_publication import make_command


class NativeMemoryStore(MemoryCommandStore):
    def __init__(self):
        super().__init__()
        self.native = []
        self.fail_commit = False

    def serializable(self, command, callback):
        before = deepcopy((self.preparation, self.published, self.native))
        try:
            result = callback(self)
            if self.fail_commit:
                raise RuntimeError("test final commit failure")
            return result
        except Exception:
            self.preparation, self.published, self.native = before
            raise


def setup():
    actor = "test:authorized-operator"
    command = make_command(TENANT, STUDY, actor)
    principal = PlatformCommandPrincipalV1(
        tenant_id=TENANT, study_ids=(STUDY,), subject=actor,
        actor_chain=({"subject": actor, "type": "human"},),
        roles=("osb-specialist",), purpose="workflow-orchestration",
        capabilities=("package:release",),
    )
    store, publisher = NativeMemoryStore(), StubPublisher()
    current = [datetime.now(UTC)]

    def mutate(tx):
        tx.native.append({"uid": "native-assigned-1", "source": "unchanged"})
        return {
            "targetIdentity": "native-assigned-1", "targetVersion": "0.1",
            "targetState": {"drafts": len(tx.native), "clinicalApproval": False},
            "conservationCounts": {"input": 1, "output": 1, "dropped": 0},
            "effectPayload": {"nativeId": "native-assigned-1"},
        }

    def invoke():
        return execute_osb_atomic_signed_command(
            command, principal, "osb", store, mutate,
            publisher=publisher, clock=lambda: current[0],
        )

    return command, store, publisher, current, invoke


def test_success_commits_native_effect_and_signed_receipt_once():
    _, store, publisher, _, invoke = setup()
    result = invoke()
    assert result["publicationMode"] == "signed"
    assert result["receipt"]["targetIdentity"] == store.native[0]["uid"]
    assert result["receipt"]["conservationCounts"] == {"input": 1, "output": 1, "dropped": 0}
    assert result["signedReceiptEnvelope"] == store.published["signedReceiptEnvelope"]
    replay = invoke()
    assert replay["replay"] is True
    assert replay["receipt"] == result["receipt"]
    assert replay["signedReceiptEnvelope"] == result["signedReceiptEnvelope"]
    assert len(store.native) == 1 and publisher.calls == 1


def test_signer_failure_rolls_back_native_writes_and_retry_succeeds():
    _, store, publisher, _, invoke = setup()
    publisher.fail = True
    with pytest.raises(PlatformCommandError, match="Test signer unavailable"):
        invoke()
    assert not store.native and store.preparation is None and store.published is None
    publisher.fail = False
    assert invoke()["publicationMode"] == "signed"
    assert len(store.native) == 1


def test_invalid_signature_result_rolls_back_native_writes():
    _, store, publisher, _, invoke = setup()
    original = publisher.publish

    def tampered(receipt):
        result = original(receipt)
        result["verification"]["verified"] = False
        return result

    publisher.publish = tampered
    with pytest.raises(PlatformCommandError) as error:
        invoke()
    assert error.value.code == "SIGNED_PUBLICATION_INVALID"
    assert not store.native and store.preparation is None and store.published is None


def test_authorization_expiry_after_signing_rolls_back_everything():
    _, store, publisher, current, invoke = setup()
    publisher.after_sign = lambda: current.__setitem__(0, current[0] + timedelta(hours=1))
    with pytest.raises(PlatformCommandError) as error:
        invoke()
    assert error.value.code == "COMMAND_EXPIRED"
    assert not store.native and store.preparation is None and store.published is None


def test_final_commit_failure_rolls_back_native_effect_and_publication():
    _, store, publisher, _, invoke = setup()
    store.fail_commit = True
    with pytest.raises(RuntimeError, match="test final commit failure"):
        invoke()
    assert publisher.calls == 1
    assert not store.native and store.preparation is None and store.published is None
    store.fail_commit = False
    invoke()
    assert len(store.native) == 1


def test_foreign_tenant_is_rejected_before_mutation_or_signing():
    command, store, publisher, _, invoke = setup()
    command["tenantId"] = "another-tenant"
    with pytest.raises(PlatformCommandError) as error:
        invoke()
    assert error.value.code == "COMMAND_SCOPE_DENIED"
    assert not store.native and publisher.calls == 0


@pytest.mark.parametrize("custody", ["unsigned", "different_intent", "separate_preparation"])
def test_existing_custody_cannot_be_rewritten_as_new_signed_effect(custody):
    command, store, publisher, _, invoke = setup()
    if custody == "separate_preparation":
        store.preparation = {"commandIntentHashValue": command["commandIntentHash"]["value"]}
    else:
        store.published = {
            "commandIntentHashValue": command["commandIntentHash"]["value"]
            if custody == "unsigned" else "different",
            "publicationMode": "prototype_unsigned",
        }
    with pytest.raises(PlatformCommandError) as error:
        invoke()
    assert error.value.code == (
        "COMMAND_PREPARATION_ALREADY_OWNED"
        if custody == "separate_preparation" else "COMMAND_IDEMPOTENCY_CONFLICT"
    )
    assert not store.native and publisher.calls == 0
