"""Live non-production conformance for atomic signed package publication."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from neomodel import db

from common.config import settings
from common.database import configure_database
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json_hash_ref,
)
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandPrincipalV1,
    RemotePlatformCommandReceiptPublisherV1,
    command_intent,
    execute_signed_platform_command,
)
from clinical_mdr_api.services.integrations.platform_command import (
    Neo4jOsbPlatformCommandStore,
    ensure_platform_command_schema,
)


class PackageHandler:
    @staticmethod
    def prepare():
        return {
            "status": "succeeded", "targetIdentity": "osb-package:fixture",
            "targetVersion": "1", "targetState": {"fixtureKind": "package", "state": "released"},
            "consumedArtifacts": [], "producedArtifacts": [],
            "conservationCounts": {"input": 1, "output": 1},
            "blockers": [], "warnings": [], "error": None,
            "effectPayload": {"fixtureKind": "package"},
        }

    @staticmethod
    def commit(_transaction, prepared_effect):
        return prepared_effect


class CountingPublisher:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def publish(self, receipt):
        self.calls += 1
        return self.delegate.publish(receipt)


class FailAfterPublicationTransaction:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def publish_signed(self, preparation, actor_subject, purpose):
        self.delegate.publish_signed(preparation, actor_subject, purpose)
        raise RuntimeError("P2_INJECTED_FINAL_COMMIT_FAILURE")


class FailAfterPublicationStore:
    def __init__(self, delegate):
        self.delegate = delegate

    def serializable(self, command, callback):
        return self.delegate.serializable(
            command, lambda transaction: callback(FailAfterPublicationTransaction(transaction))
        )


def make_command(tenant_id: str, platform_study_id: str, actor_subject: str):
    now = datetime.now(UTC)
    input_payload = {"fixtureKind": "package"}
    decision_id = str(uuid4())
    command = {
        "contractVersion": "CommandEnvelopeV1@1.0.0", "commandId": str(uuid4()),
        "workflowId": str(uuid4()), "workflowStepId": str(uuid4()),
        "correlationId": str(uuid4()), "causationId": None,
        "tenantId": tenant_id, "platformStudyId": platform_study_id,
        "targetSystem": "osb", "targetCapability": "package:release",
        "action": "osb.package-v2.release", "idempotencyKey": f"p2-package-{uuid4()}",
        "requestingActor": {"issuerQualifiedSubject": actor_subject, "subjectType": "human",
            "actorChain": [{"subject": actor_subject, "type": "human"}]},
        "purpose": "workflow-orchestration", "requestedAt": now.isoformat(),
        "notBefore": None, "deadlineAt": (now + timedelta(minutes=5)).isoformat(),
        "expectedSourceState": None, "expectedTargetState": None,
        "inputPayload": input_payload,
        "inputHash": canonical_json_hash_ref(input_payload, schema_version="CommandInputV1@1.0.0"),
        "authorizationDecisionRef": {"decisionId": decision_id,
            "decisionHash": canonical_json_hash_ref(
                {"decisionId": decision_id, "outcome": "allow"},
                schema_version="AuthorizationDecisionV1@1.0.0"),
            "expiresAt": (now + timedelta(minutes=5)).isoformat()},
        "commandIntentHash": canonical_json_hash_ref({}, schema_version="CommandIntentV1@1.0.0"),
    }
    command["commandIntentHash"] = canonical_json_hash_ref(
        command_intent(command), schema_version="CommandIntentV1@1.0.0"
    )
    return command


def main():
    driver = configure_database(
        settings.neo4j_dsn, soft_cardinality_check=settings.soft_cardinality_check,
        max_connection_lifetime=settings.neo4j_connection_lifetime,
        liveness_check_timeout=settings.neo4j_liveness_check_timeout,
    )
    try:
        ensure_platform_command_schema()
        tenant_id = str(uuid4())
        platform_study_id = str(uuid4())
        actor_subject = "https://idp.accuratrials.invalid|p2-osb-specialist-verifier"
        command = make_command(tenant_id, platform_study_id, actor_subject)
        principal = PlatformCommandPrincipalV1(
            tenant_id=tenant_id, study_ids=(platform_study_id,), subject=actor_subject,
            actor_chain=({"subject": actor_subject, "type": "human"},),
            roles=("osb-specialist",), purpose="workflow-orchestration",
            capabilities=("package:release",),
        )
        publisher = CountingPublisher(RemotePlatformCommandReceiptPublisherV1(
            "http://host.docker.internal:8765/v1/sign-command-receipt", "osb.package",
            "development", allow_insecure_prototype=True,
        ))
        store = Neo4jOsbPlatformCommandStore()
        try:
            execute_signed_platform_command(
                command, principal, "osb", "osb.package", FailAfterPublicationStore(store),
                PackageHandler(), publisher,
            )
            raise AssertionError("injected final commit failure was not observed")
        except RuntimeError as error:
            assert str(error) == "P2_INJECTED_FINAL_COMMIT_FAILURE"
        rolled_back, _ = db.cypher_query(
            """MATCH (outbox:PlatformCommandOutbox {tenant_id:$tenant_id,
                     platform_study_id:$platform_study_id}) RETURN count(outbox)""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id},
        )
        assert int(rolled_back[0][0]) == 0
        first = execute_signed_platform_command(
            command, principal, "osb", "osb.package", store, PackageHandler(), publisher
        )
        assert publisher.calls == 1
        replay = execute_signed_platform_command(
            command, principal, "osb", "osb.package", store, PackageHandler(), publisher
        )
        assert first["publicationMode"] == "signed"
        assert replay["replay"] is True and replay["targetEffectId"] == first["targetEffectId"]
        rows, _ = db.cypher_query(
            """MATCH (outbox:PlatformCommandOutbox {tenant_id:$tenant_id,
                     platform_study_id:$platform_study_id})
               RETURN outbox.stream_epoch,outbox.stream_position,outbox.publication_protocol
               ORDER BY outbox.stream_position""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id},
        )
        assert len(rows) == 1 and int(rows[0][1]) == 1
        assert str(rows[0][2]) == "signed-positioned/1.0"
        reconciliation = store.reconcile(tenant_id, platform_study_id)
        assert reconciliation["integrity_mismatches"] == 0
        print(json.dumps({"fixtureKind": "package", "tenantId": tenant_id,
            "platformStudyId": platform_study_id,
            "positions": [{"streamEpoch": str(row[0]), "streamPosition": int(row[1]),
                "publicationProtocol": str(row[2])} for row in rows],
            "finalCommitRollback": True, "signingCalls": publisher.calls,
            "replay": replay["replay"], "reconciliation": reconciliation}, separators=(",", ":")))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
