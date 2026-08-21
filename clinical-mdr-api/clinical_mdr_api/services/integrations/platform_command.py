"""Neo4j target-side idempotency for CC CommandEnvelopeV1 mutations."""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar
from uuid import uuid4

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
)
from clinical_mdr_api.generated.platform_contracts.domain_audit_v1 import (
    attest_audit_root,
    build_command_event,
    export_audit_bundle,
    verify_audit_restore,
    verify_audit_root_chain,
)
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandTransactionV1,
)

T = TypeVar("T")
RETENTION_POLICY_VERSION = "domain-command-retention/1.0"


def _effect_record(
    *, target_effect_id: str, tenant_id: str, platform_study_id: str,
    command_id: str, workflow_id: str, workflow_step_id: str,
    target_capability: str, action: str, idempotency_key: str,
    command_intent_hash: str, status: str, receipt_id: str,
    receipt: dict[str, Any], effect_payload: dict[str, Any],
    actor_subject: str, purpose: str,
    publication_mode: str = "prototype_unsigned",
    signed_envelope_hash: str | None = None,
    preparation_id: str | None = None,
) -> dict[str, Any]:
    record = {
        "contractVersion": "PlatformCommandEffectRecordV1@1.0.0",
        "targetEffectId": target_effect_id, "tenantId": tenant_id,
        "platformStudyId": platform_study_id, "commandId": command_id,
        "workflowId": workflow_id, "workflowStepId": workflow_step_id,
        "targetCapability": target_capability, "action": action,
        "idempotencyKey": idempotency_key, "commandIntentHash": command_intent_hash,
        "status": status, "receiptId": receipt_id, "receipt": receipt,
        "effectPayload": effect_payload, "actorSubject": actor_subject, "purpose": purpose,
    }
    if publication_mode != "prototype_unsigned":
        record["publicationMode"] = publication_mode
    if signed_envelope_hash:
        record["signedEnvelopeHash"] = signed_envelope_hash
    if preparation_id:
        record["preparationId"] = preparation_id
    return record


def _effect_record_hash(record: dict[str, Any]) -> str:
    return canonical_json_hash_ref(
        record, schema_version="PlatformCommandEffectRecordV1@1.0.0"
    )["value"]


def ensure_platform_command_schema() -> None:
    """Install the uniqueness guarantees required before the prototype is enabled."""
    db.cypher_query(
        """MATCH (effect:PlatformCommandEffect)
           WHERE effect.command_scope_key IS NULL OR effect.idempotency_scope_key IS NULL
              OR effect.retention_policy_version IS NULL OR effect.publication_mode IS NULL
              OR effect.retain_until IS NULL
           SET effect.command_scope_key = coalesce(
                 effect.command_scope_key, effect.tenant_id + '|' + effect.command_id),
               effect.idempotency_scope_key = coalesce(
                 effect.idempotency_scope_key,
                 effect.tenant_id + '|' + effect.target_capability + '|' +
                 effect.action + '|' + effect.idempotency_key),
               effect.retention_policy_version = coalesce(
                 effect.retention_policy_version, $retention_policy_version),
               effect.publication_mode = coalesce(effect.publication_mode, 'prototype_unsigned'),
               effect.retain_until = coalesce(effect.retain_until, 'infinity')""",
        {"retention_policy_version": RETENTION_POLICY_VERSION},
    )
    unhashed, _ = db.cypher_query(
        """MATCH (effect:PlatformCommandEffect)
           WHERE effect.effect_record_hash IS NULL
           RETURN effect.target_effect_id,effect.tenant_id,effect.platform_study_id,
                  effect.command_id,effect.workflow_id,effect.workflow_step_id,
                  effect.target_capability,effect.action,effect.idempotency_key,
                  effect.command_intent_hash,effect.status,effect.receipt_id,
                  effect.receipt_json,effect.effect_payload_json,
                  effect.actor_subject,effect.purpose"""
    )
    for row in unhashed:
        record = _effect_record(
            target_effect_id=str(row[0]), tenant_id=str(row[1]),
            platform_study_id=str(row[2]), command_id=str(row[3]),
            workflow_id=str(row[4]), workflow_step_id=str(row[5]),
            target_capability=str(row[6]), action=str(row[7]),
            idempotency_key=str(row[8]), command_intent_hash=str(row[9]),
            status=str(row[10]), receipt_id=str(row[11]),
            receipt=json.loads(str(row[12])), effect_payload=json.loads(str(row[13])),
            actor_subject=str(row[14]), purpose=str(row[15]),
        )
        db.cypher_query(
            """MATCH (effect:PlatformCommandEffect {target_effect_id:$target_effect_id})
               WHERE effect.effect_record_hash IS NULL
               SET effect.effect_record_hash=$effect_record_hash""",
            {"target_effect_id": record["targetEffectId"],
             "effect_record_hash": _effect_record_hash(record)},
        )
    db.cypher_query(
        """MATCH (audit:PlatformCommandAudit)
           WHERE audit.chain_key IS NULL
           SET audit.chain_key = coalesce(
                 audit.chain_key, audit.tenant_id + '|' + toString(audit.chain_sequence))"""
    )
    db.cypher_query(
        """MATCH (outbox:PlatformCommandOutbox)
           WHERE outbox.publication_protocol IS NULL
           SET outbox.publication_protocol = coalesce(
                 outbox.publication_protocol, 'legacy-unpositioned/1.0')"""
    )
    historical, _ = db.cypher_query(
        """MATCH (effect:PlatformCommandEffect)
           MATCH (audit:PlatformCommandAudit {target_effect_id: effect.target_effect_id})
           WHERE NOT EXISTS {
             MATCH (:PlatformCommandOutbox {target_effect_id: effect.target_effect_id})
           }
           RETURN effect.tenant_id,effect.platform_study_id,effect.command_id,
                  effect.target_effect_id,effect.receipt_id,audit.receipt_hash,effect.status"""
    )
    for row in historical:
        payload = {
            "contractVersion": "PlatformCommandResultOutboxV1@1.0.0",
            "tenantId": str(row[0]), "platformStudyId": str(row[1]),
            "commandId": str(row[2]), "targetEffectId": str(row[3]),
            "receiptId": str(row[4]), "receiptHash": str(row[5]),
            "status": str(row[6]),
        }
        db.cypher_query(
            """CREATE (:PlatformCommandOutbox {
                 outbox_id: $outbox_id, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id, command_id: $command_id,
                 target_effect_id: $target_effect_id, receipt_id: $receipt_id,
                 receipt_hash: $receipt_hash, event_type: 'platform.command.result.v1',
                  event_payload_json: $event_payload_json,
                  retention_policy_version: $retention_policy_version,
                  publication_protocol: 'legacy-unpositioned/1.0',
                  retain_until: 'infinity', created_at: datetime()})""",
            {"outbox_id": str(uuid4()), "tenant_id": payload["tenantId"],
             "platform_study_id": payload["platformStudyId"],
             "command_id": payload["commandId"],
             "target_effect_id": payload["targetEffectId"],
             "receipt_id": payload["receiptId"], "receipt_hash": payload["receiptHash"],
             "event_payload_json": canonical_json(payload),
             "retention_policy_version": RETENTION_POLICY_VERSION},
        )
    statements = (
        "CREATE CONSTRAINT platform_command_lock_key IF NOT EXISTS FOR (n:PlatformCommandLock) REQUIRE n.key IS UNIQUE",
        "CREATE CONSTRAINT platform_command_audit_lock_key IF NOT EXISTS FOR (n:PlatformCommandAuditLock) REQUIRE n.key IS UNIQUE",
        "CREATE CONSTRAINT platform_command_effect_command_scope IF NOT EXISTS FOR (n:PlatformCommandEffect) REQUIRE n.command_scope_key IS UNIQUE",
        "CREATE CONSTRAINT platform_command_effect_idempotency_scope IF NOT EXISTS FOR (n:PlatformCommandEffect) REQUIRE n.idempotency_scope_key IS UNIQUE",
        "CREATE CONSTRAINT platform_command_audit_chain IF NOT EXISTS FOR (n:PlatformCommandAudit) REQUIRE n.chain_key IS UNIQUE",
        "CREATE CONSTRAINT platform_command_outbox_effect IF NOT EXISTS FOR (n:PlatformCommandOutbox) REQUIRE n.target_effect_id IS UNIQUE",
        "CREATE CONSTRAINT platform_command_preparation_command IF NOT EXISTS FOR (n:PlatformCommandPreparation) REQUIRE n.command_scope_key IS UNIQUE",
        "CREATE CONSTRAINT platform_command_preparation_idempotency IF NOT EXISTS FOR (n:PlatformCommandPreparation) REQUIRE n.idempotency_scope_key IS UNIQUE",
        "CREATE CONSTRAINT platform_command_preparation_effect IF NOT EXISTS FOR (n:PlatformCommandPreparation) REQUIRE n.target_effect_id IS UNIQUE",
        "CREATE CONSTRAINT platform_command_publication_stream_key IF NOT EXISTS FOR (n:PlatformCommandPublicationStream) REQUIRE n.stream_key IS UNIQUE",
        "CREATE CONSTRAINT platform_command_outbox_stream_position IF NOT EXISTS FOR (n:PlatformCommandOutbox) REQUIRE n.stream_position_key IS UNIQUE",
        "CREATE CONSTRAINT platform_domain_audit_id IF NOT EXISTS FOR (n:DomainAuditEvent) REQUIRE n.audit_event_id IS UNIQUE",
        "CREATE CONSTRAINT platform_domain_audit_chain IF NOT EXISTS FOR (n:DomainAuditEvent) REQUIRE n.chain_key IS UNIQUE",
        "CREATE CONSTRAINT platform_domain_audit_root_id IF NOT EXISTS FOR (n:DomainAuditRootCheckpoint) REQUIRE n.checkpoint_id IS UNIQUE",
        "CREATE CONSTRAINT platform_domain_audit_root_chain IF NOT EXISTS FOR (n:DomainAuditRootCheckpoint) REQUIRE n.checkpoint_scope_key IS UNIQUE",
        "CREATE CONSTRAINT platform_domain_audit_root_hash IF NOT EXISTS FOR (n:DomainAuditRootCheckpoint) REQUIRE n.payload_scope_key IS UNIQUE",
        "CREATE CONSTRAINT platform_domain_audit_export_id IF NOT EXISTS FOR (n:DomainAuditExport) REQUIRE n.export_id IS UNIQUE",
        "CREATE CONSTRAINT platform_domain_audit_export_scope IF NOT EXISTS FOR (n:DomainAuditExport) REQUIRE n.export_scope_key IS UNIQUE",
    )
    for statement in statements:
        db.cypher_query(statement)


def _published(row: list[Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "targetEffectId": str(row[0]),
        "commandIntentHashValue": str(row[1]),
        "receipt": json.loads(str(row[2])),
        "effectPayload": json.loads(str(row[3])),
        "signedReceiptEnvelope": json.loads(str(row[4])) if len(row) > 4 and row[4] else None,
        "signatureVerification": json.loads(str(row[5])) if len(row) > 5 and row[5] else None,
        "publicationMode": str(row[6] or "prototype_unsigned") if len(row) > 6 else "prototype_unsigned",
    }


def _preparation(row: list[Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "preparationId": str(row[0]), "state": str(row[1]),
        "command": json.loads(str(row[2])), "commandIntentHashValue": str(row[3]),
        "targetEffectId": str(row[4]), "receipt": json.loads(str(row[5])),
        "effect": json.loads(str(row[6])), "preparationHashValue": str(row[7]),
        "signedReceiptEnvelope": json.loads(str(row[8])) if row[8] else None,
        "signatureVerification": json.loads(str(row[9])) if row[9] else None,
    }


class Neo4jPlatformCommandTransaction(PlatformCommandTransactionV1):
    def __init__(
        self, command: dict[str, Any], *, domain_audit_mode: str,
        environment: str, region: str,
    ):
        self.command = command
        self.domain_audit_mode = domain_audit_mode
        self.environment = environment
        self.region = region

    def find_by_command_id(self, command_id: str) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (effect:PlatformCommandEffect)
               WHERE effect.command_scope_key = $command_scope_key OR
                     (effect.tenant_id = $tenant_id AND effect.command_id = $command_id)
               RETURN effect.target_effect_id, effect.command_intent_hash,
                      effect.receipt_json, effect.effect_payload_json,
                      effect.signed_envelope_json,effect.signature_verification_json,
                      effect.publication_mode
               ORDER BY effect.created_at LIMIT 1""",
            {"tenant_id": self.command["tenantId"], "command_id": command_id,
             "command_scope_key": f'{self.command["tenantId"]}|{command_id}'},
        )
        return _published(rows[0] if rows else None)

    def find_by_idempotency_key(self, capability: str, action: str, key: str) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (effect:PlatformCommandEffect)
               WHERE effect.idempotency_scope_key = $scope_key OR
                     (effect.tenant_id = $tenant_id AND effect.target_capability = $capability
                      AND effect.action = $action AND effect.idempotency_key = $key)
               RETURN effect.target_effect_id, effect.command_intent_hash,
                      effect.receipt_json, effect.effect_payload_json,
                      effect.signed_envelope_json,effect.signature_verification_json,
                      effect.publication_mode
               ORDER BY effect.created_at LIMIT 1""",
            {"tenant_id": self.command["tenantId"], "capability": capability,
             "action": action, "key": key,
             "scope_key": f'{self.command["tenantId"]}|{capability}|{action}|{key}'},
        )
        return _published(rows[0] if rows else None)

    def find_preparation_by_command_id(self, command_id: str) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (p:PlatformCommandPreparation {command_scope_key:$scope_key})
               RETURN p.preparation_id,p.state,p.command_json,p.command_intent_hash,
                      p.target_effect_id,p.receipt_json,p.effect_json,p.preparation_hash,
                      p.signed_envelope_json,p.signature_verification_json LIMIT 1""",
            {"scope_key": f'{self.command["tenantId"]}|{command_id}'},
        )
        return _preparation(rows[0] if rows else None)

    def find_preparation_by_idempotency_key(
        self, capability: str, action: str, key: str,
    ) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (p:PlatformCommandPreparation {idempotency_scope_key:$scope_key})
               RETURN p.preparation_id,p.state,p.command_json,p.command_intent_hash,
                      p.target_effect_id,p.receipt_json,p.effect_json,p.preparation_hash,
                      p.signed_envelope_json,p.signature_verification_json LIMIT 1""",
            {"scope_key": f'{self.command["tenantId"]}|{capability}|{action}|{key}'},
        )
        return _preparation(rows[0] if rows else None)

    def reserve_preparation(self, preparation: dict[str, Any]) -> None:
        db.cypher_query(
            """CREATE (:PlatformCommandPreparation {
                 preparation_id:$preparation_id,state:'prepared',tenant_id:$tenant_id,
                 platform_study_id:$platform_study_id,command_id:$command_id,
                 command_scope_key:$command_scope_key,idempotency_scope_key:$idempotency_scope_key,
                 workflow_id:$workflow_id,workflow_step_id:$workflow_step_id,
                 target_capability:$target_capability,action:$action,idempotency_key:$idempotency_key,
                 command_intent_hash:$command_intent_hash,command_json:$command_json,
                 target_effect_id:$target_effect_id,receipt_id:$receipt_id,receipt_json:$receipt_json,
                 effect_json:$effect_json,preparation_hash:$preparation_hash,
                 prepared_at:datetime($prepared_at),attempt_count:0,
                 retention_policy_version:$retention_policy_version,retain_until:'infinity',
                 created_at:datetime(),updated_at:datetime()})""",
            {"preparation_id":preparation["preparationId"],"tenant_id":self.command["tenantId"],
             "platform_study_id":self.command["platformStudyId"],"command_id":self.command["commandId"],
             "command_scope_key":f'{self.command["tenantId"]}|{self.command["commandId"]}',
             "idempotency_scope_key":f'{self.command["tenantId"]}|{self.command["targetCapability"]}|{self.command["action"]}|{self.command["idempotencyKey"]}',
             "workflow_id":self.command["workflowId"],"workflow_step_id":self.command["workflowStepId"],
             "target_capability":self.command["targetCapability"],"action":self.command["action"],
             "idempotency_key":self.command["idempotencyKey"],
             "command_intent_hash":preparation["commandIntentHashValue"],
             "command_json":canonical_json(preparation["command"]),"target_effect_id":preparation["targetEffectId"],
             "receipt_id":preparation["receipt"]["receiptId"],"receipt_json":canonical_json(preparation["receipt"]),
             "effect_json":canonical_json(preparation["effect"]),"preparation_hash":preparation["preparationHashValue"],
             "prepared_at":preparation["receipt"]["startedAt"],"retention_policy_version":RETENTION_POLICY_VERSION},
        )

    def mark_preparation_signed(
        self, preparation_id: str, preparation_hash: str,
        envelope: dict[str, Any], verification: dict[str, Any],
    ) -> dict[str, Any]:
        rows, _ = db.cypher_query(
            """MATCH (p:PlatformCommandPreparation {preparation_id:$preparation_id})
               WHERE p.preparation_hash=$preparation_hash AND p.state='prepared'
               SET p.state='signed',p.signed_envelope_json=$signed_envelope_json,
                   p.signed_envelope_hash=$signed_envelope_hash,
                   p.signature_verification_json=$signature_verification_json,
                   p.signed_at=datetime($signed_at),p.updated_at=datetime(),
                   p.attempt_count=coalesce(p.attempt_count,0)+1,p.last_error_code=null
               RETURN p.preparation_id,p.state,p.command_json,p.command_intent_hash,
                      p.target_effect_id,p.receipt_json,p.effect_json,p.preparation_hash,
                      p.signed_envelope_json,p.signature_verification_json""",
            {"preparation_id":preparation_id,"preparation_hash":preparation_hash,
             "signed_envelope_json":canonical_json(envelope),
             "signed_envelope_hash":verification["envelopeHash"]["value"],
             "signature_verification_json":canonical_json(verification),"signed_at":verification["trustedTime"]},
        )
        if rows:
            return _preparation(rows[0]) or {}
        existing = self.find_preparation_by_command_id(self.command["commandId"])
        if not existing or existing["preparationHashValue"] != preparation_hash or existing["state"] not in {"signed", "published"}:
            raise RuntimeError("OSB_SIGNED_PREPARATION_CONFLICT")
        return existing

    def publish(self, result: dict[str, Any], actor_subject: str, purpose: str) -> None:
        self._publish_result(result, actor_subject, purpose, None)

    def publish_signed(self, preparation: dict[str, Any], actor_subject: str, purpose: str) -> None:
        if preparation.get("state") != "signed" or not preparation.get("signedReceiptEnvelope") or not preparation.get("signatureVerification"):
            raise RuntimeError("OSB_SIGNED_PREPARATION_REQUIRED")
        self._publish_result({
            "targetEffectId":preparation["targetEffectId"],
            "commandIntentHashValue":preparation["commandIntentHashValue"],
            "receipt":preparation["receipt"],"effectPayload":preparation["effect"]["effectPayload"],
        },actor_subject,purpose,preparation)

    def _publish_result(
        self, result: dict[str, Any], actor_subject: str, purpose: str,
        preparation: dict[str, Any] | None,
    ) -> None:
        receipt = result["receipt"]
        publication_mode = "signed" if preparation else "prototype_unsigned"
        publication_protocol = "signed-positioned/1.0" if preparation else "legacy-unpositioned/1.0"
        envelope_hash = preparation["signatureVerification"]["envelopeHash"]["value"] if preparation else None
        effect_record = _effect_record(
            target_effect_id=result["targetEffectId"], tenant_id=receipt["tenantId"],
            platform_study_id=receipt["platformStudyId"], command_id=receipt["commandId"],
            workflow_id=receipt["workflowId"], workflow_step_id=receipt["workflowStepId"],
            target_capability=self.command["targetCapability"], action=self.command["action"],
            idempotency_key=self.command["idempotencyKey"],
            command_intent_hash=result["commandIntentHashValue"], status=receipt["status"],
            receipt_id=receipt["receiptId"], receipt=receipt,
            effect_payload=result["effectPayload"], actor_subject=actor_subject, purpose=purpose,
            publication_mode=publication_mode,signed_envelope_hash=envelope_hash,
            preparation_id=preparation["preparationId"] if preparation else None,
        )
        db.cypher_query(
            """MERGE (lock:PlatformCommandAuditLock {key: $tenant_id})
               ON CREATE SET lock.revision = 0, lock.created_at = datetime()
               SET lock.revision = lock.revision + 1, lock.touched_at = datetime()""",
            {"tenant_id": receipt["tenantId"]},
        )
        stream_id = None
        stream_epoch = None
        stream_position = None
        stream_position_key = None
        if preparation:
            stream_id = "platform-command-results"
            stream_key = f'{receipt["tenantId"]}|{receipt["platformStudyId"]}|{stream_id}'
            stream_rows, _ = db.cypher_query(
                """MERGE (stream:PlatformCommandPublicationStream {stream_key:$stream_key})
                   ON CREATE SET stream.tenant_id=$tenant_id,
                     stream.platform_study_id=$platform_study_id,stream.stream_id=$stream_id,
                     stream.stream_epoch=$new_epoch,stream.last_position=0,
                     stream.created_at=datetime()
                   SET stream.last_position=stream.last_position+1,
                       stream.updated_at=datetime()
                   RETURN stream.stream_epoch,stream.last_position""",
                {"stream_key":stream_key,"tenant_id":receipt["tenantId"],
                 "platform_study_id":receipt["platformStudyId"],"stream_id":stream_id,
                 "new_epoch":str(uuid4())},
            )
            if not stream_rows:
                raise RuntimeError("OSB_PLATFORM_COMMAND_STREAM_POSITION_FAILED")
            stream_epoch = str(stream_rows[0][0])
            stream_position = int(stream_rows[0][1])
            if not stream_epoch or stream_position < 1:
                raise RuntimeError("OSB_PLATFORM_COMMAND_STREAM_POSITION_INVALID")
            stream_position_key = f"{stream_key}|{stream_epoch}|{stream_position}"
        previous_rows, _ = db.cypher_query(
            """MATCH (audit:PlatformCommandAudit {tenant_id: $tenant_id})
               RETURN audit.chain_sequence, audit.event_hash
               ORDER BY audit.chain_sequence DESC LIMIT 1""",
            {"tenant_id": receipt["tenantId"]},
        )
        previous = previous_rows[0] if previous_rows else None
        sequence = int(previous[0]) + 1 if previous else 1
        previous_hash = str(previous[1]) if previous else None
        receipt_hash = canonical_json_hash_ref(receipt, schema_version="ReceiptEnvelopeV1@1.0.0")["value"]
        domain_audit_record = None
        if self.domain_audit_mode == "enforced":
            domain_previous_rows, _ = db.cypher_query(
                """MATCH (event:DomainAuditEvent {
                     tenant_id: $tenant_id, platform_study_id: $platform_study_id})
                   RETURN event.sequence,event.event_hash_json
                   ORDER BY event.sequence DESC LIMIT 1""",
                {"tenant_id": receipt["tenantId"],
                 "platform_study_id": receipt["platformStudyId"]},
            )
            domain_previous = domain_previous_rows[0] if domain_previous_rows else None
            domain_sequence = int(domain_previous[0]) + 1 if domain_previous else 1
            domain_previous_hash = (
                json.loads(str(domain_previous[1])) if domain_previous and domain_previous[1] else None
            )
            domain_audit_record = build_command_event(
                environment=self.environment,
                region=self.region,
                sequence=domain_sequence,
                previous_event_hash=domain_previous_hash,
                command=self.command,
                receipt=receipt,
                target_effect_id=result["targetEffectId"],
                receipt_hash=receipt_hash,
                details={
                    "targetCapability": self.command["targetCapability"],
                    "publicationMode": publication_mode,
                    "signedEnvelopeHash": envelope_hash,
                },
            )
        audit_id = str(uuid4())
        details = {"targetCapability": self.command["targetCapability"], "action": self.command["action"],
                   "status": receipt["status"], "publicationMode": publication_mode,
                   "signedEnvelopeHash": envelope_hash,
                   "publicationProtocol":publication_protocol,"streamId":stream_id,
                   "streamEpoch":stream_epoch,"streamPosition":stream_position}
        event_hash = canonical_json_hash_ref({
            "auditId": audit_id, "tenantId": receipt["tenantId"],
            "platformStudyId": receipt["platformStudyId"], "commandId": receipt["commandId"],
            "targetEffectId": result["targetEffectId"], "actorSubject": actor_subject,
            "action": self.command["action"], "outcome": receipt["status"],
            "receiptHash": receipt_hash, "details": details,
            "chainSequence": sequence, "previousEventHash": previous_hash,
        }, schema_version="OsbPlatformCommandAuditV1@1.0.0")["value"]
        outbox_payload = {
            "contractVersion": "PlatformCommandResultOutboxV1@1.0.0",
            "tenantId": receipt["tenantId"], "platformStudyId": receipt["platformStudyId"],
            "commandId": receipt["commandId"], "targetEffectId": result["targetEffectId"],
            "receiptId": receipt["receiptId"], "receiptHash": receipt_hash,
            "status": receipt["status"], "publicationMode":publication_mode,
            "signedEnvelopeHash":envelope_hash,
            "publicationProtocol":publication_protocol,"streamId":stream_id,
            "streamEpoch":stream_epoch,"streamPosition":stream_position,
        }
        rows, _ = db.cypher_query(
            """CREATE (effect:PlatformCommandEffect {
                 target_effect_id: $target_effect_id, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id, command_id: $command_id,
                 command_scope_key: $command_scope_key,
                 workflow_id: $workflow_id, workflow_step_id: $workflow_step_id,
                 target_capability: $target_capability, action: $action,
                 idempotency_key: $idempotency_key, command_intent_hash: $command_intent_hash,
                 idempotency_scope_key: $idempotency_scope_key,
                 status: $status, receipt_id: $receipt_id, receipt_json: $receipt_json,
                 effect_payload_json: $effect_payload_json, actor_subject: $actor_subject,
                 purpose: $purpose, retention_policy_version: $retention_policy_version,
                 publication_mode:$publication_mode,signed_envelope_json:$signed_envelope_json,
                 signed_envelope_hash:$signed_envelope_hash,
                 signature_verification_json:$signature_verification_json,
                 trusted_signing_time:$trusted_signing_time,preparation_id:$preparation_id,
                 effect_record_hash: $effect_record_hash,
                 retain_until: 'infinity', created_at: datetime()})
               CREATE (audit:PlatformCommandAudit {
                 audit_id: $audit_id, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id, command_id: $command_id,
                 target_effect_id: $target_effect_id, actor_subject: $actor_subject,
                 action: $action, outcome: $status, receipt_hash: $receipt_hash,
                 details_json: $details_json, chain_sequence: $chain_sequence,
                 chain_key: $chain_key,
                 previous_event_hash: $previous_event_hash, event_hash: $event_hash,
                 created_at: datetime()})
               CREATE (outbox:PlatformCommandOutbox {
                 outbox_id: $outbox_id, tenant_id: $tenant_id,
                 platform_study_id: $platform_study_id, command_id: $command_id,
                 target_effect_id: $target_effect_id, receipt_id: $receipt_id,
                 receipt_hash: $receipt_hash, event_type: 'platform.command.result.v1',
                 event_payload_json: $outbox_payload_json,
                 retention_policy_version: $retention_policy_version,
                 publication_mode:$publication_mode,signed_envelope_hash:$signed_envelope_hash,
                 publication_protocol:$publication_protocol,stream_id:$stream_id,
                 stream_epoch:$stream_epoch,stream_position:$stream_position,
                 stream_position_key:$stream_position_key,
                 retain_until: 'infinity', created_at: datetime()})
               CREATE (audit)-[:AUDITS_EFFECT]->(effect)
               CREATE (outbox)-[:PUBLISHES_EFFECT]->(effect)
               FOREACH (_ IN CASE WHEN $domain_audit_enabled THEN [1] ELSE [] END |
                 CREATE (domainAudit:DomainAuditEvent {
                   audit_event_id:$domain_audit_event_id,
                   tenant_id:$tenant_id,platform_study_id:$platform_study_id,
                   source_system:'osb',environment:$domain_audit_environment,
                   region:$domain_audit_region,stream_id:$domain_audit_stream_id,
                   sequence:$domain_audit_sequence,chain_key:$domain_audit_chain_key,
                   previous_event_hash_json:$domain_audit_previous_hash_json,
                   event_hash_json:$domain_audit_event_hash_json,
                   event_json:$domain_audit_event_json,
                   actor_subject:$domain_audit_actor_subject,
                   subject_type:$domain_audit_subject_type,
                   human_subject:$domain_audit_human_subject,
                   service_actor:$domain_audit_service_actor,
                   purpose:$purpose,action:$action,outcome:$domain_audit_outcome,
                   correlation_id:$domain_audit_correlation_id,
                   causation_id:$domain_audit_causation_id,
                   command_id:$command_id,effect_id:$target_effect_id,
                   retention_policy_version:'regulated-audit/1.0',
                   retain_until:'infinity',created_at:datetime($domain_audit_occurred_at)
                 })
                 CREATE (domainAudit)-[:AUDITS_EFFECT]->(effect)
               )
               RETURN effect.target_effect_id""",
            {"target_effect_id": result["targetEffectId"], "tenant_id": receipt["tenantId"],
             "platform_study_id": receipt["platformStudyId"], "command_id": receipt["commandId"],
             "command_scope_key": f'{receipt["tenantId"]}|{receipt["commandId"]}',
             "workflow_id": receipt["workflowId"], "workflow_step_id": receipt["workflowStepId"],
             "target_capability": self.command["targetCapability"], "action": self.command["action"],
             "idempotency_key": self.command["idempotencyKey"],
             "idempotency_scope_key": f'{receipt["tenantId"]}|{self.command["targetCapability"]}|{self.command["action"]}|{self.command["idempotencyKey"]}',
             "command_intent_hash": result["commandIntentHashValue"], "status": receipt["status"],
             "receipt_id": receipt["receiptId"], "receipt_json": canonical_json(receipt),
             "effect_payload_json": canonical_json(result["effectPayload"]), "actor_subject": actor_subject,
             "purpose": purpose, "audit_id": audit_id, "receipt_hash": receipt_hash,
             "details_json": canonical_json(details), "chain_sequence": sequence,
             "chain_key": f'{receipt["tenantId"]}|{sequence}',
             "previous_event_hash": previous_hash, "event_hash": event_hash,
             "outbox_id": str(uuid4()), "outbox_payload_json": canonical_json(outbox_payload),
             "retention_policy_version": RETENTION_POLICY_VERSION,
             "effect_record_hash": _effect_record_hash(effect_record),
             "publication_mode":publication_mode,
             "publication_protocol":publication_protocol,"stream_id":stream_id,
             "stream_epoch":stream_epoch,"stream_position":stream_position,
             "stream_position_key":stream_position_key,
             "signed_envelope_json":canonical_json(preparation["signedReceiptEnvelope"]) if preparation else None,
             "signed_envelope_hash":envelope_hash,
             "signature_verification_json":canonical_json(preparation["signatureVerification"]) if preparation else None,
             "trusted_signing_time":preparation["signatureVerification"]["trustedTime"] if preparation else None,
             "preparation_id":preparation["preparationId"] if preparation else None,
             "domain_audit_enabled":domain_audit_record is not None,
             "domain_audit_event_id":domain_audit_record["event"]["auditEventId"] if domain_audit_record else None,
             "domain_audit_environment":domain_audit_record["event"]["environment"] if domain_audit_record else None,
             "domain_audit_region":domain_audit_record["event"]["region"] if domain_audit_record else None,
             "domain_audit_stream_id":domain_audit_record["event"]["streamId"] if domain_audit_record else None,
             "domain_audit_sequence":domain_audit_record["event"]["sequence"] if domain_audit_record else None,
             "domain_audit_chain_key":(
                 f'{domain_audit_record["event"]["streamId"]}|{domain_audit_record["event"]["sequence"]}'
                 if domain_audit_record else None
             ),
             "domain_audit_previous_hash_json":(
                 canonical_json(domain_audit_record["event"]["previousEventHash"])
                 if domain_audit_record else None
             ),
             "domain_audit_event_hash_json":(
                 canonical_json(domain_audit_record["eventHash"]) if domain_audit_record else None
             ),
             "domain_audit_event_json":(
                 canonical_json(domain_audit_record["event"]) if domain_audit_record else None
             ),
             "domain_audit_actor_subject":(
                 domain_audit_record["event"]["actor"]["issuerQualifiedSubject"]
                 if domain_audit_record else None
             ),
             "domain_audit_subject_type":(
                 domain_audit_record["event"]["actor"]["subjectType"] if domain_audit_record else None
             ),
             "domain_audit_human_subject":(
                 domain_audit_record["event"]["actor"]["humanSubject"] if domain_audit_record else None
             ),
             "domain_audit_service_actor":(
                 domain_audit_record["event"]["actor"]["serviceActor"] if domain_audit_record else None
             ),
             "domain_audit_outcome":domain_audit_record["event"]["outcome"] if domain_audit_record else None,
             "domain_audit_correlation_id":(
                 domain_audit_record["event"]["correlationId"] if domain_audit_record else None
             ),
             "domain_audit_causation_id":(
                 domain_audit_record["event"]["causationId"] if domain_audit_record else None
             ),
             "domain_audit_occurred_at":(
                 domain_audit_record["event"]["occurredAt"] if domain_audit_record else None
             )},
        )
        if not rows:
            raise RuntimeError("OSB_PLATFORM_COMMAND_PUBLICATION_FAILED")
        if preparation:
            updated, _ = db.cypher_query(
                """MATCH (p:PlatformCommandPreparation {preparation_id:$preparation_id,state:'signed'})
                   WHERE p.preparation_hash=$preparation_hash
                   SET p.state='published',p.published_at=datetime(),p.updated_at=datetime(),
                       p.lease_owner=null,p.lease_expires_at=null
                   RETURN p.preparation_id""",
                {"preparation_id":preparation["preparationId"],
                 "preparation_hash":preparation["preparationHashValue"]},
            )
            if not updated:
                raise RuntimeError("OSB_SIGNED_PREPARATION_PUBLISH_CONFLICT")


class Neo4jOsbPlatformCommandStore:
    def __init__(
        self, *, domain_audit_mode: str | None = None,
        environment: str | None = None, region: str | None = None,
        audit_attestor: Any | None = None, audit_service_actor: str | None = None,
        audit_retention_years: int = 25,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.domain_audit_mode = (
            domain_audit_mode or os.getenv("PLATFORM_DOMAIN_AUDIT_MODE", "disabled")
        ).strip().lower()
        if self.domain_audit_mode not in {"disabled", "enforced"}:
            raise RuntimeError("OSB_PLATFORM_DOMAIN_AUDIT_MODE_INVALID")
        self.environment = (
            environment or os.getenv("DEPLOYMENT_ENVIRONMENT", "development")
        ).strip().lower()
        self.region = (region or os.getenv("PLATFORM_REGION", "local")).strip()
        self.audit_attestor = audit_attestor
        self.audit_service_actor = audit_service_actor
        self.audit_retention_years = audit_retention_years
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if not self.environment or not self.region or not isinstance(audit_retention_years, int) \
                or audit_retention_years < 1:
            raise RuntimeError("OSB_PLATFORM_DOMAIN_AUDIT_CONTEXT_INVALID")

    @staticmethod
    def _audit_stream(tenant_id: str, platform_study_id: str) -> str:
        return f"osb:audit:{tenant_id}:{platform_study_id}"

    def read_audit_records(
        self, tenant_id: str, platform_study_id: str, through_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        rows, _ = db.cypher_query(
            """MATCH (event:DomainAuditEvent {
                 tenant_id:$tenant_id,platform_study_id:$platform_study_id,stream_id:$stream_id})
               WHERE $through_sequence IS NULL OR event.sequence <= $through_sequence
               RETURN event.event_json,event.event_hash_json ORDER BY event.sequence""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
             "stream_id": self._audit_stream(tenant_id, platform_study_id),
             "through_sequence": through_sequence},
        )
        return [{"event": json.loads(str(row[0])), "eventHash": json.loads(str(row[1]))}
                for row in rows]

    def latest_audit_checkpoint(
        self, tenant_id: str, platform_study_id: str,
    ) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (root:DomainAuditRootCheckpoint {
                 tenant_id:$tenant_id,platform_study_id:$platform_study_id,stream_id:$stream_id})
               RETURN root.checkpoint_json,root.payload_hash_json,root.signed_envelope_json,
                      root.signature_verification_json
               ORDER BY root.sequence_end DESC LIMIT 1""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
             "stream_id": self._audit_stream(tenant_id, platform_study_id)},
        )
        if not rows:
            return None
        return {"checkpoint": json.loads(str(rows[0][0])), "payloadHash": json.loads(str(rows[0][1])),
                "signedEnvelope": json.loads(str(rows[0][2])), "verification": json.loads(str(rows[0][3]))}

    def read_audit_checkpoints(
        self, tenant_id: str, platform_study_id: str, through_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        rows, _ = db.cypher_query(
            """MATCH (root:DomainAuditRootCheckpoint {
                 tenant_id:$tenant_id,platform_study_id:$platform_study_id,stream_id:$stream_id})
               WHERE $through_sequence IS NULL OR root.sequence_end <= $through_sequence
               RETURN root.checkpoint_json,root.payload_hash_json,root.signed_envelope_json,
                      root.signature_verification_json
               ORDER BY root.sequence_end""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
             "stream_id": self._audit_stream(tenant_id, platform_study_id),
             "through_sequence": through_sequence},
        )
        return [{"checkpoint": json.loads(str(row[0])), "payloadHash": json.loads(str(row[1])),
                 "signedEnvelope": json.loads(str(row[2])), "verification": json.loads(str(row[3]))}
                for row in rows]

    async def _verify_audit_root_chain(
        self, records: list[dict[str, Any]], roots: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self.audit_attestor is None:
            raise RuntimeError("OSB_DOMAIN_AUDIT_ATTESTOR_REQUIRED")
        verified_roots = []
        for root in roots:
            verification = self.audit_attestor.verify({
                "payloadBytes": canonical_json(root["checkpoint"]).encode("utf-8"),
                "envelope": root["signedEnvelope"],
            })
            if hasattr(verification, "__await__"):
                verification = await verification
            verified_roots.append({**root, "verification": verification})
        if verified_roots:
            verify_audit_root_chain(
                records, verified_roots, tenant_id=records[0]["event"]["tenantId"],
                source_system="osb", stream_id=records[0]["event"]["streamId"],
            )
        return verified_roots

    async def create_audit_checkpoint(
        self, tenant_id: str, platform_study_id: str, *, legal_hold: bool | None = None,
        retain_until: str | None = None,
    ) -> dict[str, Any]:
        if self.audit_attestor is None or not self.audit_service_actor:
            raise RuntimeError("OSB_DOMAIN_AUDIT_ATTESTOR_REQUIRED")
        records = self.read_audit_records(tenant_id, platform_study_id)
        if not records:
            raise RuntimeError("OSB_DOMAIN_AUDIT_CHECKPOINT_EMPTY")
        stored_roots = self.read_audit_checkpoints(tenant_id, platform_study_id)
        verified_roots = await self._verify_audit_root_chain(records, stored_roots)
        prior = verified_roots[-1] if verified_roots else None
        if prior and prior["checkpoint"]["sequenceEnd"] == records[-1]["event"]["sequence"]:
            normalized_retain_until = (
                datetime.fromisoformat(retain_until.replace("Z", "+00:00"))
                .astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if retain_until else None
            )
            if (legal_hold is not None and legal_hold != prior["checkpoint"]["legalHold"]) \
                    or (normalized_retain_until is not None
                        and normalized_retain_until != prior["checkpoint"]["retainUntil"]):
                raise RuntimeError("OSB_DOMAIN_AUDIT_CHECKPOINT_ADVANCE_REQUIRED")
            return prior
        now = self.clock().astimezone(timezone.utc)
        retained = retain_until or (now + timedelta(days=365 * self.audit_retention_years)).isoformat().replace("+00:00", "Z")
        stream_id = self._audit_stream(tenant_id, platform_study_id)
        root = await attest_audit_root(
            records, attestor=self.audit_attestor, tenant_id=tenant_id, source_system="osb",
            environment=self.environment, region=self.region, stream_id=stream_id,
            previous_checkpoint_hash=prior["payloadHash"] if prior else None,
            created_at=now.isoformat().replace("+00:00", "Z"), created_by=self.audit_service_actor,
            retain_until=retained, legal_hold=bool(legal_hold),
        )
        with db.transaction:
            db.cypher_query(
                """MERGE (lock:PlatformCommandAuditLock {key:$tenant_id})
                   ON CREATE SET lock.revision=0,lock.created_at=datetime()
                   SET lock.revision=lock.revision+1,lock.touched_at=datetime()""",
                {"tenant_id": tenant_id},
            )
            current, _ = db.cypher_query(
                """MATCH (event:DomainAuditEvent {
                     tenant_id:$tenant_id,platform_study_id:$platform_study_id,stream_id:$stream_id})
                   WITH event ORDER BY event.sequence
                   WITH collect(event) AS events
                   RETURN size(events),events[0].sequence,events[-1].sequence,
                          events[0].event_hash_json,events[-1].event_hash_json""",
                {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "stream_id": stream_id},
            )
            latest = self.latest_audit_checkpoint(tenant_id, platform_study_id)
            row = current[0] if current else [0, None, None, None, None]
            if int(row[0]) != root["checkpoint"]["eventCount"] \
                    or int(row[1]) != root["checkpoint"]["sequenceStart"] \
                    or int(row[2]) != root["checkpoint"]["sequenceEnd"] \
                    or canonical_json(json.loads(str(row[3]))) != canonical_json(root["checkpoint"]["firstEventHash"]) \
                    or canonical_json(json.loads(str(row[4]))) != canonical_json(root["checkpoint"]["lastEventHash"]) \
                    or canonical_json(latest["payloadHash"] if latest else None) \
                    != canonical_json(prior["payloadHash"] if prior else None):
                raise RuntimeError("OSB_DOMAIN_AUDIT_CHECKPOINT_STALE")
            checkpoint = root["checkpoint"]
            db.cypher_query(
                """CREATE (:DomainAuditRootCheckpoint {
                     checkpoint_id:$checkpoint_id,tenant_id:$tenant_id,
                     platform_study_id:$platform_study_id,source_system:'osb',stream_id:$stream_id,
                     sequence_start:$sequence_start,sequence_end:$sequence_end,event_count:$event_count,
                     checkpoint_scope_key:$checkpoint_scope_key,payload_scope_key:$payload_scope_key,
                     checkpoint_json:$checkpoint_json,payload_hash:$payload_hash,
                     payload_hash_json:$payload_hash_json,signed_envelope_json:$signed_envelope_json,
                     signature_verification_json:$signature_verification_json,
                     trusted_signing_time:datetime($trusted_signing_time),
                     previous_checkpoint_hash:$previous_checkpoint_hash,
                     retention_policy_version:$retention_policy_version,
                     retain_until:datetime($retain_until),legal_hold:$legal_hold,
                     created_at:datetime($created_at),created_by:$created_by})""",
                {"checkpoint_id": checkpoint["checkpointId"], "tenant_id": tenant_id,
                 "platform_study_id": platform_study_id, "stream_id": stream_id,
                 "sequence_start": checkpoint["sequenceStart"], "sequence_end": checkpoint["sequenceEnd"],
                 "event_count": checkpoint["eventCount"],
                 "checkpoint_scope_key": f'{tenant_id}|{stream_id}|{checkpoint["sequenceEnd"]}',
                 "payload_scope_key": f'{tenant_id}|{root["payloadHash"]["value"]}',
                 "checkpoint_json": canonical_json(checkpoint),
                 "payload_hash": root["payloadHash"]["value"],
                 "payload_hash_json": canonical_json(root["payloadHash"]),
                 "signed_envelope_json": canonical_json(root["signedEnvelope"]),
                 "signature_verification_json": canonical_json(root["verification"]),
                 "trusted_signing_time": root["verification"]["trustedTime"],
                 "previous_checkpoint_hash": checkpoint["previousCheckpointHash"]["value"]
                 if checkpoint["previousCheckpointHash"] else None,
                 "retention_policy_version": checkpoint["retentionPolicyVersion"],
                 "retain_until": checkpoint["retainUntil"], "legal_hold": checkpoint["legalHold"],
                 "created_at": checkpoint["createdAt"], "created_by": self.audit_service_actor},
            )
        return root

    async def export_audit_checkpoint(
        self, tenant_id: str, platform_study_id: str, checkpoint_id: str, *,
        exported_by: str, export_format: str = "jsonl",
    ) -> dict[str, Any]:
        if self.audit_attestor is None or export_format not in {"jsonl", "human-readable"}:
            raise RuntimeError("OSB_DOMAIN_AUDIT_EXPORT_INVALID")
        rows, _ = db.cypher_query(
            """MATCH (root:DomainAuditRootCheckpoint {
                 tenant_id:$tenant_id,platform_study_id:$platform_study_id,checkpoint_id:$checkpoint_id})
               RETURN root.checkpoint_json,root.payload_hash_json,root.signed_envelope_json,
                      root.signature_verification_json""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "checkpoint_id": checkpoint_id},
        )
        if not rows:
            raise RuntimeError("OSB_DOMAIN_AUDIT_CHECKPOINT_NOT_FOUND")
        root = {"checkpoint": json.loads(str(rows[0][0])), "payloadHash": json.loads(str(rows[0][1])),
                "signedEnvelope": json.loads(str(rows[0][2])), "verification": json.loads(str(rows[0][3]))}
        records = self.read_audit_records(tenant_id, platform_study_id, root["checkpoint"]["sequenceEnd"])
        roots = self.read_audit_checkpoints(
            tenant_id, platform_study_id, root["checkpoint"]["sequenceEnd"]
        )
        verified_roots = await self._verify_audit_root_chain(records, roots)
        verified_root = verified_roots[-1] if verified_roots else None
        if verified_root is None or verified_root["checkpoint"]["checkpointId"] != checkpoint_id:
            raise RuntimeError("OSB_DOMAIN_AUDIT_ROOT_CHAIN_INTEGRITY_FAILED")
        bundle = export_audit_bundle(records, verified_root)
        export_hash = bundle["jsonLinesHash"] if export_format == "jsonl" else bundle["humanReadableHash"]
        export_id = str(uuid4())
        db.cypher_query(
            """CREATE (:DomainAuditExport {
                 export_id:$export_id,tenant_id:$tenant_id,platform_study_id:$platform_study_id,
                 checkpoint_id:$checkpoint_id,export_scope_key:$export_scope_key,
                 payload_hash:$payload_hash,event_count:$event_count,format:$format,
                 export_hash:$export_hash,exported_by:$exported_by,exported_at:datetime($exported_at)})""",
            {"export_id": export_id, "tenant_id": tenant_id, "platform_study_id": platform_study_id,
             "checkpoint_id": checkpoint_id,
             "export_scope_key": f'{tenant_id}|{checkpoint_id}|{export_format}|{export_hash["value"]}',
             "payload_hash": verified_root["payloadHash"]["value"], "event_count": len(records),
             "format": export_format, "export_hash": export_hash["value"], "exported_by": exported_by,
             "exported_at": self.clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")},
        )
        return {"checkpoint": verified_root["checkpoint"], "format": export_format,
                "bytes": (bundle["jsonLines"] if export_format == "jsonl"
                          else bundle["humanReadable"]).encode("utf-8"), "exportHash": export_hash}

    async def verify_audit_restore(self, source: list[dict[str, Any]], restored: list[dict[str, Any]],
                                   roots: list[dict[str, Any]]) -> dict[str, Any]:
        verified_roots = await self._verify_audit_root_chain(source, roots)
        return verify_audit_restore(source, restored, verified_roots)

    @staticmethod
    def lookup(tenant_id: str, platform_study_id: str, command_id: str) -> dict[str, Any] | None:
        rows, _ = db.cypher_query(
            """MATCH (effect:PlatformCommandEffect {
                 tenant_id: $tenant_id, platform_study_id: $platform_study_id,
                 command_id: $command_id})
               RETURN effect.target_effect_id, effect.command_intent_hash,
                      effect.receipt_json, effect.effect_payload_json,
                      effect.signed_envelope_json,effect.signature_verification_json,
                      effect.publication_mode
               ORDER BY effect.created_at LIMIT 1""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
             "command_id": command_id},
        )
        return _published(rows[0] if rows else None)

    @staticmethod
    def reconcile(tenant_id: str, platform_study_id: str) -> dict[str, int]:
        rows, _ = db.cypher_query(
            """MATCH (effect:PlatformCommandEffect {
                 tenant_id: $tenant_id, platform_study_id: $platform_study_id})
               OPTIONAL MATCH (audit:PlatformCommandAudit {target_effect_id: effect.target_effect_id})
               OPTIONAL MATCH (outbox:PlatformCommandOutbox {target_effect_id: effect.target_effect_id})
               OPTIONAL MATCH (preparation:PlatformCommandPreparation {preparation_id: effect.preparation_id})
               OPTIONAL MATCH (stream:PlatformCommandPublicationStream)
                 WHERE stream.stream_key = outbox.tenant_id + '|' + outbox.platform_study_id + '|' + outbox.stream_id
               RETURN count(DISTINCT effect),count(DISTINCT audit),count(DISTINCT outbox),
                      count(DISTINCT CASE WHEN audit IS NULL THEN effect END),
                      count(DISTINCT CASE WHEN outbox IS NULL THEN effect END),
                      count(DISTINCT CASE WHEN effect.publication_mode='signed' AND (
                        coalesce(outbox.publication_mode,'') <> 'signed'
                        OR coalesce(outbox.signed_envelope_hash,'') <> coalesce(effect.signed_envelope_hash,'')
                        OR coalesce(outbox.publication_protocol,'') <> 'signed-positioned/1.0'
                        OR outbox.stream_id IS NULL OR outbox.stream_epoch IS NULL
                        OR outbox.stream_position IS NULL OR outbox.stream_position < 1
                        OR coalesce(outbox.stream_position_key,'') <> (
                          outbox.tenant_id + '|' + outbox.platform_study_id + '|' +
                          outbox.stream_id + '|' + outbox.stream_epoch + '|' +
                          toString(outbox.stream_position))
                        OR stream.stream_epoch IS NULL
                        OR stream.stream_epoch <> outbox.stream_epoch
                        OR stream.last_position < outbox.stream_position
                        OR coalesce(preparation.state,'') <> 'published'
                        OR coalesce(preparation.signed_envelope_hash,'') <> coalesce(effect.signed_envelope_hash,'')
                      ) THEN effect END)""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id},
        )
        row = rows[0] if rows else [0, 0, 0, 0, 0, 0]
        result = dict(zip(("effects", "audits", "outbox", "missing_audit", "missing_outbox",
                           "publication_mismatches"), map(int, row)))
        effect_rows, _ = db.cypher_query(
            """MATCH (effect:PlatformCommandEffect {
                 tenant_id:$tenant_id,platform_study_id:$platform_study_id})
               RETURN effect.target_effect_id,effect.tenant_id,effect.platform_study_id,
                      effect.command_id,effect.workflow_id,effect.workflow_step_id,
                      effect.target_capability,effect.action,effect.idempotency_key,
                      effect.command_intent_hash,effect.status,effect.receipt_id,
                      effect.receipt_json,effect.effect_payload_json,
                      effect.actor_subject,effect.purpose,effect.effect_record_hash,
                      effect.publication_mode,effect.signed_envelope_hash,effect.preparation_id""",
            {"tenant_id": tenant_id, "platform_study_id": platform_study_id},
        )
        mismatches = result.pop("publication_mismatches", 0)
        for effect in effect_rows:
            record = _effect_record(
                target_effect_id=str(effect[0]), tenant_id=str(effect[1]),
                platform_study_id=str(effect[2]), command_id=str(effect[3]),
                workflow_id=str(effect[4]), workflow_step_id=str(effect[5]),
                target_capability=str(effect[6]), action=str(effect[7]),
                idempotency_key=str(effect[8]), command_intent_hash=str(effect[9]),
                status=str(effect[10]), receipt_id=str(effect[11]),
                receipt=json.loads(str(effect[12])), effect_payload=json.loads(str(effect[13])),
                actor_subject=str(effect[14]), purpose=str(effect[15]),
                publication_mode=str(effect[17] or "prototype_unsigned"),
                signed_envelope_hash=str(effect[18]) if effect[18] else None,
                preparation_id=str(effect[19]) if effect[19] else None,
            )
            if str(effect[16] or "") != _effect_record_hash(record):
                mismatches += 1
        result["integrity_mismatches"] = mismatches
        return result

    @staticmethod
    def claim_recoverable(
        tenant_id: str, platform_study_id: str, lease_owner: str,
        limit: int = 20, lease_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 100))
        bounded_seconds = max(10, min(int(lease_seconds), 900))
        rows, _ = db.cypher_query(
            """MATCH (p:PlatformCommandPreparation {tenant_id:$tenant_id,platform_study_id:$platform_study_id})
               WHERE p.state IN ['prepared','signed']
                 AND (p.lease_expires_at IS NULL OR p.lease_expires_at <= datetime())
               WITH p ORDER BY p.updated_at,p.preparation_id LIMIT $limit
               SET p.lease_owner=$lease_owner,
                   p.lease_expires_at=datetime()+duration({seconds:$lease_seconds}),
                   p.updated_at=datetime(),p.attempt_count=coalesce(p.attempt_count,0)+1
               RETURN p.preparation_id,p.state,p.command_json,p.command_intent_hash,
                      p.target_effect_id,p.receipt_json,p.effect_json,p.preparation_hash,
                      p.signed_envelope_json,p.signature_verification_json""",
            {"tenant_id":tenant_id,"platform_study_id":platform_study_id,
             "lease_owner":lease_owner,"limit":bounded_limit,"lease_seconds":bounded_seconds},
        )
        return [item for row in rows if (item := _preparation(row)) is not None]

    def serializable(self, command: dict[str, Any], callback: Callable[[PlatformCommandTransactionV1], T]) -> T:
        if self.environment in {"prod", "production"} and self.domain_audit_mode != "enforced":
            raise RuntimeError("OSB_PLATFORM_DOMAIN_AUDIT_REQUIRED")
        key = f'{command["tenantId"]}|{command["targetCapability"]}|{command["action"]}|{command["idempotencyKey"]}'
        with db.transaction:
            db.cypher_query(
                """MERGE (lock:PlatformCommandLock {key: $key})
                   ON CREATE SET lock.created_at = datetime(), lock.revision = 0
                   SET lock.revision = lock.revision + 1, lock.touched_at = datetime()
                   RETURN lock.revision""",
                {"key": key},
            )
            return callback(Neo4jPlatformCommandTransaction(
                command,
                domain_audit_mode=self.domain_audit_mode,
                environment=self.environment,
                region=self.region,
            ))


__all__ = ["Neo4jOsbPlatformCommandStore", "ensure_platform_command_schema"]
