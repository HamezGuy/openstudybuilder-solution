"""Non-production bridge used by Command Center's live P4 workflow verification."""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.generated.platform_contracts.native_identity_command_processor_v1 import (
    NativeIdentityCommandProcessorV1,
    NativeIdentityPrincipalV1,
    platform_identity_hash,
)
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandPrincipalV1,
    execute_platform_command,
)
from clinical_mdr_api.services.integrations.candidate_set import (
    generate_candidate_set,
    store_candidate_request_bytes,
)
from clinical_mdr_api.services.integrations.native_identity import Neo4jOsbNativeIdentityStoreV1
from clinical_mdr_api.services.integrations.mapping_context import MappingContextService
from clinical_mdr_api.services.integrations.platform_command import (
    Neo4jOsbPlatformCommandStore,
    ensure_platform_command_schema,
)


def deterministic_uuid(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))


class RemoteIdentityPublisher:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def publish(self, receipt: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps({
                "receipt": receipt,
                "producerService": "osb.package",
                "signingPurpose": "native-identity-binding",
            }, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "content-type": "application/json",
                "x-platform-producer-service": "osb.package",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
        envelope = body.get("signedArtifactEnvelope") or body.get("signedReceiptEnvelope")
        if not isinstance(envelope, dict) or not isinstance(body.get("verification"), dict) \
                or body["verification"].get("verified") is not True:
            raise RuntimeError("P4_OSB_IDENTITY_SIGNING_FAILED")
        return envelope


def ensure_binding(data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data["tenantId"]
    platform_study_id = data["platformStudyId"]
    native_identity = data.get("nativeIdentity") or "Study_990041"
    native_version = "0.1"
    db.cypher_query(
        """MERGE (study:StudyRoot {uid:$native_identity})
           MERGE (value:StudyValue {uid:$value_uid})
           MERGE (study)-[latest:LATEST_DRAFT]->(value)
           SET latest.version=$native_version,latest.status='DRAFT',
               value.study_title='P4 Command Center Workflow Study'
           MERGE (scope:DomainStudyScope {tenant_id:$tenant_id,study_uid:$native_identity})
           SET scope.status='active',scope.platform_study_id=$platform_study_id
           RETURN study.uid""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "native_identity": native_identity,
            "native_version": native_version,
            "value_uid": f"{native_identity}-0.1",
        },
    )
    statement = {
        "contractVersion": "1.0.0",
        "intentId": deterministic_uuid(f"p4-osb-binding:{tenant_id}:{platform_study_id}"),
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "targetSystem": "osb",
        "namespace": "accuratrials-osb",
        "objectType": "study-draft-root",
        "requestedInitialState": {
            "operation": "claim_existing",
            "nativeIdentity": native_identity,
            "nativeVersion": native_version,
        },
        "expectedAbsence": False,
        "commandId": deterministic_uuid(f"p4-osb-binding-command:{tenant_id}:{platform_study_id}"),
        "idempotencyKey": f"p4-osb-binding:{platform_study_id}",
        "actorSubject": "service:command-center",
        "purpose": "external-identity-create-intent",
        "expiresAt": "2099-12-31T23:59:59.000Z",
    }
    intent = {
        **statement,
        "intentHash": platform_identity_hash(statement, "ExternalIdentityCreateIntentV1@1.0.0"),
    }
    identity_clock = datetime.now(UTC).replace(microsecond=0)
    processor = NativeIdentityCommandProcessorV1(
        target_system="osb",
        producer_service="osb.package",
        namespaces=("accuratrials-osb",),
        object_types=("study-draft-root",),
        allowed_roles=("service",),
        store=Neo4jOsbNativeIdentityStoreV1(),
        publisher=RemoteIdentityPublisher(data["identitySigningUrl"]),
        clock=lambda: identity_clock,
    )
    principal = NativeIdentityPrincipalV1(
        tenant_id=tenant_id,
        study_ids=(platform_study_id,),
        subject="service:command-center",
        human_subject=None,
        actor_chain=({"subject": "service:command-center", "type": "service"},),
        roles=("service",),
        purpose="workflow-orchestration",
        capabilities=("native-identity:bind",),
    )
    first = processor.process(intent, principal)
    replay = processor.process(intent, principal)
    rows, _ = db.cypher_query(
        """MATCH (binding:PlatformNativeStudyBinding {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,namespace:'accuratrials-osb',
             object_type:'study-draft-root',status:'active'})
           RETURN binding.binding_id,binding.native_study_id,binding.native_version""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id},
    )
    if len(rows) != 1 or replay.get("replay") is not True \
            or replay["receipt"]["receiptId"] != first["receipt"]["receiptId"]:
        raise RuntimeError("P4_OSB_BINDING_REPLAY_FAILED")
    return {
        "identity": {
            "contractVersion": "1.0.0",
            "system": "osb",
            "tenantId": tenant_id,
            "platformStudyId": platform_study_id,
            "namespace": "accuratrials-osb",
            "objectType": "study-draft-root",
            "bindingId": str(rows[0][0]),
            "nativeIdentity": str(rows[0][1]),
            "nativeVersion": str(rows[0][2]),
            "verificationStatus": "verified",
            "bindingReceiptId": first["receipt"]["receiptId"],
        },
        "bindingReceipt": first["receipt"],
        "signedBindingEnvelope": first["signedReceiptEnvelope"],
        "replay": replay["replay"],
        "activeBindingCount": len(rows),
    }


def verify_signature(data: dict[str, Any]) -> dict[str, Any]:
    payload_bytes = canonical_json(data["requestPayload"]).encode("utf-8")
    request = urllib.request.Request(
        data["signatureVerificationUrl"],
        data=json.dumps({
            "payloadBase64": base64.b64encode(payload_bytes).decode("ascii"),
            "envelope": data["signedArtifactEnvelope"],
        }, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    verification = body.get("verification")
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        raise RuntimeError("P4_OSB_REQUEST_SIGNATURE_UNVERIFIED")
    return verification


def consume(data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data["tenantId"]
    platform_study_id = data["platformStudyId"]
    request_payload = data["requestPayload"]
    artifact = data["candidateRequestArtifact"]
    payload_bytes = base64.b64decode(data["requestBytesBase64"], validate=True)
    if payload_bytes != canonical_json(request_payload).encode("utf-8"):
        raise RuntimeError("P4_CC_MODIFIED_CANDIDATE_REQUEST_BYTES")
    signature_verification = verify_signature(data)
    with db.transaction:
        transfer = store_candidate_request_bytes(
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
            bytes_value=payload_bytes,
            expected_hash=artifact["payloadHash"]["value"],
            signed_envelope=data["signedArtifactEnvelope"],
        )
    ensure_platform_command_schema()
    store = Neo4jOsbPlatformCommandStore(
        domain_audit_mode="disabled", environment="prototype", region="us-central1"
    )
    command = data["command"]
    principal = PlatformCommandPrincipalV1(
        tenant_id=tenant_id,
        study_ids=(platform_study_id,),
        subject="service:command-center",
        actor_chain=({"subject": "service:command-center", "type": "service"},),
        roles=("service",),
        purpose="workflow-orchestration",
        capabilities=("candidate:generate",),
    )

    def handler(_transaction: Any) -> dict[str, Any]:
        generated = generate_candidate_set(
            request_payload=request_payload,
            artifact=artifact,
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
            osb_openapi_hash=data["osbOpenApiHash"],
            actor="service:command-center",
            signed_envelope=data["signedArtifactEnvelope"],
            signature_verification=signature_verification,
            mapping_context_service=MappingContextService(
                standard_version_loader=lambda **_kwargs: []
            ),
        )
        records = generated["payload"]["candidateRecords"]
        blockers = generated["payload"].get("blockers") or []
        return {
            "status": "no_op" if generated.get("replay") else "succeeded",
            "targetIdentity": generated["nativeIdentity"],
            "targetVersion": generated["nativeVersion"],
            "targetState": {
                "candidateSetHash": generated["payloadHash"],
                "candidateSetVersionId": generated["candidateSetVersionId"],
                "blockerCount": len(blockers),
            },
            "consumedArtifacts": [artifact],
            "producedArtifacts": [generated["artifactRef"]],
            "conservationCounts": {
                "sourceIntents": len(records), "candidateRecords": len(records), "dropped": 0,
            },
            "blockers": blockers,
            "effectPayload": {
                "candidateSetId": generated["candidateSetId"],
                "candidateSetVersionId": generated["candidateSetVersionId"],
                "candidateSetHash": generated["payloadHash"],
                "candidateSetArtifact": generated["artifactRef"],
                "candidateSetPayload": generated["payload"],
            },
        }

    first = execute_platform_command(command, principal, "osb", store, handler)
    replay = execute_platform_command(command, principal, "osb", store, handler)
    lookup = store.lookup(tenant_id, platform_study_id, command["commandId"])
    if replay.get("replay") is not True or lookup is None \
            or first["receipt"]["receiptId"] != replay["receipt"]["receiptId"] \
            or first["receipt"]["receiptId"] != lookup["receipt"]["receiptId"]:
        raise RuntimeError("P4_OSB_COMMAND_REPLAY_FAILED")
    counts, _ = db.cypher_query(
        """MATCH (request:OsbCandidateRequestV1 {tenant_id:$tenant_id,request_hash:$request_hash})
           OPTIONAL MATCH (candidate:OsbCandidateSetV1 {tenant_id:$tenant_id,request_hash:$request_hash})
           OPTIONAL MATCH (effect:PlatformCommandEffect {tenant_id:$tenant_id,command_id:$command_id})
           RETURN count(DISTINCT request),count(DISTINCT candidate),count(DISTINCT effect)""",
        {
            "tenant_id": tenant_id,
            "request_hash": artifact["payloadHash"]["value"],
            "command_id": command["commandId"],
        },
    )
    return {
        "transfer": transfer,
        "first": first,
        "replay": replay,
        "lookupReceiptId": lookup["receipt"]["receiptId"],
        "counts": {
            "requests": int(counts[0][0]),
            "candidateSets": int(counts[0][1]),
            "effects": int(counts[0][2]),
        },
        "signatureVerification": signature_verification,
    }


def main() -> None:
    if os.getenv("OSB_DEPLOYMENT_ENVIRONMENT", "prototype").lower() in {"prod", "production"}:
        raise RuntimeError("P4_OSB_REQUEST_BRIDGE_FORBIDDEN_IN_PRODUCTION")
    database_url = os.environ["NEO4J_DSN"]
    db.set_connection(database_url)
    data = json.load(__import__("sys").stdin)
    result = ensure_binding(data) if data.get("action") == "ensure-binding" else consume(data)
    print(json.dumps({"ok": True, **result}, separators=(",", ":")))


if __name__ == "__main__":
    main()
