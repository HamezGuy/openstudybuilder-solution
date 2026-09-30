"""Generated runtime helpers for CC-owned DomainAuditEventV1."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    raw_bytes_hash_ref,
)


class DomainAuditError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_actor(actor: dict[str, Any]) -> dict[str, Any]:
    chain = actor.get("actorChain")
    subject = str(actor.get("issuerQualifiedSubject") or "")
    subject_type = actor.get("subjectType")
    if not subject or subject_type not in {"human", "service"} or not isinstance(chain, list) \
            or not 1 <= len(chain) <= 16:
        raise DomainAuditError("DOMAIN_AUDIT_ACTOR_INVALID")
    if any(not entry.get("subject") or entry.get("type") not in {"human", "service"} for entry in chain):
        raise DomainAuditError("DOMAIN_AUDIT_ACTOR_INVALID")
    human = next((entry for entry in chain if entry["type"] == "human"), None)
    service = next((entry for entry in reversed(chain) if entry["type"] == "service"), None)
    if subject_type == "human":
        if not human or human["subject"] != subject or chain[0]["type"] != "human":
            raise DomainAuditError("DOMAIN_AUDIT_HUMAN_CHAIN_INVALID")
    elif human or not service or service["subject"] != subject:
        raise DomainAuditError("DOMAIN_AUDIT_SERVICE_CHAIN_INVALID")
    return {
        "issuerQualifiedSubject": subject,
        "subjectType": subject_type,
        "humanSubject": human["subject"] if human else None,
        "serviceActor": service["subject"] if service else None,
        "actorChain": [dict(entry) for entry in chain],
    }


def assert_human_actor(actor: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_actor(actor)
    if normalized["subjectType"] != "human":
        raise DomainAuditError("DOMAIN_AUDIT_SERVICE_CANNOT_HUMAN_SIGN")
    return normalized


def _outcome(status: str) -> str:
    if status == "cancelled":
        return "cancelled"
    if status == "quarantined":
        return "quarantined"
    if status in {"failed_retryable", "failed_terminal", "partial"}:
        return "failed"
    if status in {"accepted", "running", "blocked"}:
        return "accepted"
    return "succeeded"


def build_command_event(*, environment: str, region: str, sequence: int,
                        previous_event_hash: dict[str, Any] | None,
                        command: dict[str, Any], receipt: dict[str, Any],
                        target_effect_id: str, receipt_hash: str,
                        details: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(sequence, int) or sequence < 1 or not environment or not region:
        raise DomainAuditError("DOMAIN_AUDIT_EVENT_INVALID")
    event = {
        "contractVersion": "DomainAuditEventV1@1.0.0",
        "auditEventId": str(uuid4()),
        "tenantId": command["tenantId"],
        "platformStudyId": command["platformStudyId"],
        "sourceSystem": "osb",
        "environment": environment,
        "region": region,
        "streamId": f'osb:audit:{command["tenantId"]}:{command["platformStudyId"]}',
        "sequence": sequence,
        "previousEventHash": previous_event_hash,
        "occurredAt": receipt.get("completedAt") or receipt["startedAt"],
        "actor": normalize_actor(command["requestingActor"]),
        "purpose": command["purpose"],
        "action": command["action"],
        "outcome": _outcome(receipt["status"]),
        "object": {"type": "platform-command-effect", "id": target_effect_id, "version": None},
        "correlationId": command["correlationId"],
        "causationId": command.get("causationId"),
        "commandId": command["commandId"],
        "effectId": target_effect_id,
        "classification": "governance-non-phi",
        "retentionPolicyVersion": "regulated-audit/1.0",
        "details": {"receiptHash": receipt_hash, **(details or {})},
    }
    return {"event": event, "eventHash": canonical_json_hash_ref(
        event, schema_version="DomainAuditEventV1@1.0.0"
    )}


def verify_chain(records: list[dict[str, Any]], *, tenant_id: str | None = None,
                 source_system: str | None = None,
                 stream_id: str | None = None) -> dict[str, Any]:
    if not records:
        raise DomainAuditError("DOMAIN_AUDIT_CHAIN_EMPTY")
    previous_sequence = 0
    previous_hash = None
    for record in records:
        event = record["event"]
        expected = canonical_json_hash_ref(event, schema_version="DomainAuditEventV1@1.0.0")
        if event["sequence"] != previous_sequence + 1 \
                or canonical_json(event["previousEventHash"]) != canonical_json(previous_hash) \
                or canonical_json(record["eventHash"]) != canonical_json(expected) \
                or (tenant_id is not None and event["tenantId"] != tenant_id) \
                or (source_system is not None and event["sourceSystem"] != source_system) \
                or (stream_id is not None and event["streamId"] != stream_id):
            raise DomainAuditError("DOMAIN_AUDIT_CHAIN_INTEGRITY_FAILED")
        normalize_actor(event["actor"])
        previous_sequence = event["sequence"]
        previous_hash = record["eventHash"]
    return {
        "sequenceStart": records[0]["event"]["sequence"],
        "sequenceEnd": records[-1]["event"]["sequence"],
        "eventCount": len(records),
        "firstEventHash": records[0]["eventHash"],
        "lastEventHash": records[-1]["eventHash"],
        "orderedEventSetHash": canonical_json_hash_ref(
            [record["eventHash"] for record in records],
            schema_version="DomainAuditOrderedEventSetV1@1.0.0",
        ),
    }


def _instant(value: str, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise DomainAuditError(code) from error


def create_audit_root_checkpoint(records: list[dict[str, Any]], *, tenant_id: str,
                                 source_system: str, environment: str, region: str,
                                 stream_id: str, created_at: str, created_by: str,
                                 retain_until: str, checkpoint_id: str | None = None,
                                 previous_checkpoint_hash: dict[str, Any] | None = None,
                                 legal_hold: bool = False,
                                 retention_policy_version: str = "regulated-audit/1.0") -> dict[str, Any]:
    verified = verify_chain(records, tenant_id=tenant_id, source_system=source_system,
                            stream_id=stream_id)
    created = _instant(created_at, "DOMAIN_AUDIT_ROOT_TIME_INVALID")
    if not environment or not region or not created_by \
            or created < _instant(records[-1]["event"]["occurredAt"], "DOMAIN_AUDIT_EVENT_TIME_INVALID"):
        raise DomainAuditError("DOMAIN_AUDIT_ROOT_TIME_INVALID")
    if _instant(retain_until, "DOMAIN_AUDIT_RETENTION_INVALID") <= created:
        raise DomainAuditError("DOMAIN_AUDIT_RETENTION_INVALID")
    checkpoint = {
        "contractVersion": "AuditRootCheckpointV1@1.0.0",
        "checkpointId": checkpoint_id or str(uuid4()),
        "tenantId": tenant_id,
        "sourceSystem": source_system,
        "environment": environment,
        "region": region,
        "streamId": stream_id,
        **verified,
        "previousCheckpointHash": previous_checkpoint_hash,
        "createdAt": created_at,
        "createdBy": {"issuerQualifiedSubject": created_by, "subjectType": "service"},
        "retentionPolicyVersion": retention_policy_version,
        "retainUntil": retain_until,
        "legalHold": bool(legal_hold),
        "exportSchemaVersion": "DomainAuditExportV1@1.0.0",
    }
    return {"checkpoint": checkpoint, "payloadHash": canonical_json_hash_ref(
        checkpoint, schema_version="AuditRootCheckpointV1@1.0.0"
    )}


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def attest_audit_root(records: list[dict[str, Any]], *, attestor: Any,
                            **checkpoint_input: Any) -> dict[str, Any]:
    if not callable(getattr(attestor, "attest", None)) or not callable(getattr(attestor, "verify", None)):
        raise DomainAuditError("DOMAIN_AUDIT_ROOT_ATTESTOR_REQUIRED")
    root = create_audit_root_checkpoint(records, **checkpoint_input)
    payload_bytes = canonical_json(root["checkpoint"]).encode("utf-8")
    signed = await _await(attestor.attest({
        "payloadBytes": payload_bytes,
        "payloadHash": root["payloadHash"]["value"],
        "payloadContract": "accuratrials.platform.AuditRootCheckpointV1",
        "payloadContractVersion": "1.0.0",
        "kind": "audit-root-checkpoint",
    }))
    verification = await _await(attestor.verify({
        "payloadBytes": payload_bytes, "envelope": signed["envelope"]
    }))
    if verification.get("verified") is not True \
            or canonical_json(verification.get("payloadHash")) != canonical_json(root["payloadHash"]) \
            or _instant(verification.get("trustedTime"), "DOMAIN_AUDIT_ROOT_ATTESTATION_INVALID") \
            < _instant(root["checkpoint"]["createdAt"], "DOMAIN_AUDIT_ROOT_ATTESTATION_INVALID"):
        raise DomainAuditError("DOMAIN_AUDIT_ROOT_ATTESTATION_INVALID")
    attested = {**root, "signedEnvelope": signed["envelope"], "verification": verification}
    verify_attested_audit_root(records, attested)
    return attested


def verify_attested_audit_root(records: list[dict[str, Any]], root: dict[str, Any]) -> dict[str, Any]:
    checkpoint = root.get("checkpoint")
    expected = canonical_json_hash_ref(
        checkpoint, schema_version="AuditRootCheckpointV1@1.0.0"
    ) if checkpoint else None
    verification = root.get("verification") or {}
    if not checkpoint or checkpoint.get("contractVersion") != "AuditRootCheckpointV1@1.0.0" \
            or not root.get("signedEnvelope") or verification.get("verified") is not True \
            or canonical_json(root.get("payloadHash")) != canonical_json(expected) \
            or canonical_json(verification.get("payloadHash")) != canonical_json(expected) \
            or _instant(verification.get("trustedTime"), "DOMAIN_AUDIT_ROOT_ATTESTATION_INVALID") \
            < _instant(checkpoint.get("createdAt"), "DOMAIN_AUDIT_ROOT_ATTESTATION_INVALID"):
        raise DomainAuditError("DOMAIN_AUDIT_ROOT_ATTESTATION_INVALID")
    verified = verify_chain(
        records, tenant_id=checkpoint["tenantId"], source_system=checkpoint["sourceSystem"],
        stream_id=checkpoint["streamId"],
    )
    if verified["sequenceStart"] != checkpoint["sequenceStart"] \
            or verified["sequenceEnd"] != checkpoint["sequenceEnd"] \
            or verified["eventCount"] != checkpoint["eventCount"] \
            or canonical_json(verified["firstEventHash"]) != canonical_json(checkpoint["firstEventHash"]) \
            or canonical_json(verified["lastEventHash"]) != canonical_json(checkpoint["lastEventHash"]) \
            or canonical_json(verified["orderedEventSetHash"]) != canonical_json(checkpoint["orderedEventSetHash"]):
        raise DomainAuditError("DOMAIN_AUDIT_ROOT_RECORD_MISMATCH")
    return {**verified, "payloadHash": expected, "trustedTime": verification["trustedTime"]}


def verify_audit_root_chain(records: list[dict[str, Any]], roots: list[dict[str, Any]], *,
                            tenant_id: str | None = None, source_system: str | None = None,
                            stream_id: str | None = None) -> dict[str, Any]:
    if not roots:
        raise DomainAuditError("DOMAIN_AUDIT_ROOT_CHAIN_EMPTY")
    previous = None
    previous_sequence_end = 0
    previous_created_at: datetime | None = None
    environment = None
    region = None
    for root in roots:
        checkpoint = root["checkpoint"]
        verify_attested_audit_root(records[:int(checkpoint["sequenceEnd"])], root)
        created_at = _instant(checkpoint["createdAt"], "DOMAIN_AUDIT_ROOT_CHAIN_INTEGRITY_FAILED")
        if (tenant_id is not None and checkpoint["tenantId"] != tenant_id) \
                or (source_system is not None and checkpoint["sourceSystem"] != source_system) \
                or (stream_id is not None and checkpoint["streamId"] != stream_id) \
                or canonical_json(checkpoint["previousCheckpointHash"]) != canonical_json(previous) \
                or checkpoint["sequenceStart"] != 1 \
                or checkpoint["sequenceEnd"] <= previous_sequence_end \
                or (previous_created_at is not None and created_at < previous_created_at) \
                or (environment is not None and checkpoint["environment"] != environment) \
                or (region is not None and checkpoint["region"] != region):
            raise DomainAuditError("DOMAIN_AUDIT_ROOT_CHAIN_INTEGRITY_FAILED")
        previous = root["payloadHash"]
        previous_sequence_end = checkpoint["sequenceEnd"]
        previous_created_at = created_at
        environment = environment or checkpoint["environment"]
        region = region or checkpoint["region"]
    return {"verified": True, "checkpointCount": len(roots),
            "finalSequence": previous_sequence_end, "finalCheckpointHash": previous}


def export_audit_bundle(records: list[dict[str, Any]], root: dict[str, Any]) -> dict[str, Any]:
    checkpoint = root["checkpoint"]
    verify_attested_audit_root(records, root)
    manifest = {
        "contractVersion": "DomainAuditExportV1@1.0.0",
        "checkpoint": checkpoint,
        "checkpointPayloadHash": root["payloadHash"],
        "signedEnvelope": root["signedEnvelope"],
        "verification": root["verification"],
        "eventCount": len(records),
    }
    json_lines = "\n".join([canonical_json(manifest), *[
        canonical_json(record) for record in records]]) + "\n"
    human_readable = "\n".join(
        f'{record["event"]["occurredAt"]} #{record["event"]["sequence"]} '
        f'{record["event"]["sourceSystem"]} {record["event"]["action"]} '
        f'{record["event"]["outcome"]} actor={record["event"]["actor"]["issuerQualifiedSubject"]} '
        f'correlation={record["event"]["correlationId"]} '
        f'object={record["event"]["object"]["type"]}:{record["event"]["object"]["id"]}'
        for record in records
    ) + "\n"
    return {
        "manifest": manifest,
        "jsonLines": json_lines,
        "humanReadable": human_readable,
        "jsonLinesHash": raw_bytes_hash_ref(json_lines.encode("utf-8"),
            media_type="application/x-ndjson", schema_version="DomainAuditExportV1@1.0.0"),
        "humanReadableHash": raw_bytes_hash_ref(human_readable.encode("utf-8"),
            media_type="text/plain", schema_version="DomainAuditExportV1@1.0.0"),
    }


def verify_audit_restore(source: list[dict[str, Any]], restored: list[dict[str, Any]],
                         roots: list[dict[str, Any]]) -> dict[str, Any]:
    verify_audit_root_chain(source, roots)
    root = roots[-1]
    verify_attested_audit_root(restored, root)
    left = verify_chain(source)
    right = verify_chain(restored)
    if canonical_json(left) != canonical_json(right) \
            or right["sequenceEnd"] != root["checkpoint"]["sequenceEnd"] \
            or right["eventCount"] != root["checkpoint"]["eventCount"]:
        raise DomainAuditError("DOMAIN_AUDIT_RESTORE_MISMATCH")
    return {"verified": True, "eventCount": right["eventCount"], "lastEventHash": right["lastEventHash"]}


def assert_audit_deletion_allowed(checkpoint: dict[str, Any], *, now: str) -> bool:
    if checkpoint["legalHold"] or _instant(checkpoint["retainUntil"], "DOMAIN_AUDIT_RETENTION_INVALID") > _instant(now, "DOMAIN_AUDIT_RETENTION_INVALID"):
        raise DomainAuditError("DOMAIN_AUDIT_RETENTION_DELETE_DENIED")
    return True


__all__ = [
    "DomainAuditError", "assert_audit_deletion_allowed", "assert_human_actor",
    "attest_audit_root", "build_command_event", "create_audit_root_checkpoint",
    "export_audit_bundle", "normalize_actor", "verify_attested_audit_root",
    "verify_audit_restore", "verify_audit_root_chain", "verify_chain",
]
