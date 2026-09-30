"""Pure signed-command test ports without importing API router composition."""

from copy import deepcopy
from datetime import UTC, datetime

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json_hash_ref
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import PlatformCommandError
from clinical_mdr_api.tests.unit.services.test_osb_candidate_set_generation import STUDY, TENANT


class MemoryCommandStore:
    def __init__(self):
        self.preparation = None
        self.published = None

    def serializable(self, command, callback):
        before = deepcopy((self.preparation, self.published))
        try:
            return callback(self)
        except Exception:
            self.preparation, self.published = before
            raise

    def find_by_command_id(self, *_):
        return self.published

    def find_by_idempotency_key(self, *_):
        return self.published

    def find_preparation_by_command_id(self, *_):
        return self.preparation

    def find_preparation_by_idempotency_key(self, *_):
        return self.preparation

    def reserve_preparation(self, preparation):
        self.preparation = deepcopy(preparation)

    def mark_preparation_signed(self, preparation_id, preparation_hash, envelope, verification):
        assert self.preparation["preparationId"] == preparation_id
        assert self.preparation["preparationHashValue"] == preparation_hash
        self.preparation.update(state="signed", signedReceiptEnvelope=envelope, signatureVerification=verification)
        return self.preparation

    def publish_signed(self, preparation, *_):
        self.published = {
            "commandIntentHashValue": preparation["commandIntentHashValue"],
            "targetEffectId": preparation["targetEffectId"],
            "receipt": preparation["receipt"], "effectPayload": preparation["effect"]["effectPayload"],
            "signedReceiptEnvelope": preparation["signedReceiptEnvelope"],
            "signatureVerification": preparation["signatureVerification"], "publicationMode": "signed",
        }


class StubPublisher:
    def __init__(self):
        self.fail = False
        self.after_sign = lambda: None
        self.calls = 0

    def publish(self, receipt):
        self.calls += 1
        if self.fail:
            raise PlatformCommandError("SIGNED_PUBLICATION_DEPENDENCY_UNAVAILABLE", "Test signer unavailable.", 503)
        payload_hash = canonical_json_hash_ref(receipt, schema_version="ReceiptEnvelopeV1@1.0.0")
        envelope = {
            "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
            "signatureProfile": "jws-detached-rfc7797/1.0",
            "artifactDescriptor": {
                "kind": "platform-command-receipt", "payloadContract": "accuratrials.cc.ReceiptEnvelopeV1",
                "payloadContractVersion": "1.0.0", "producerService": "osb.package",
                "tenantId": TENANT, "purpose": "command-receipt", "payloadHash": payload_hash,
            },
            "signingStatement": {"signingPurpose": "command-receipt"},
        }
        verification = {
            "verified": True, "payloadHash": payload_hash,
            "envelopeHash": canonical_json_hash_ref(envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"),
            "trustedTime": datetime.now(UTC).isoformat(),
        }
        self.after_sign()
        return {"signedReceiptEnvelope": envelope, "verification": verification}
