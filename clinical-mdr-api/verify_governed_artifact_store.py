"""Live non-production conformance for governed OSB artifact custody."""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json_hash_ref,
    raw_bytes_hash_ref,
)
from clinical_mdr_api.services.integrations.governed_artifact_store import (
    GovernedArtifactStoreError,
    GovernedRegionalArtifactStoreV1,
)


tenant_id, platform_study_id = str(uuid4()), str(uuid4())
payload = b'{"domain":"osb","phi":false}'
placement = canonical_json_hash_ref(
    {"provider": "development", "region": "us-central-1"},
    schema_version="ProviderPlacementEvidenceV1@1.0.0",
)


def descriptor(store):
    artifact_id, version_id = str(uuid4()), str(uuid4())
    payload_hash = raw_bytes_hash_ref(
        payload,
        schema_version="SyntheticArtifactV1@1.0.0",
        media_type="application/json",
    )
    return {
        "contractVersion": "ArtifactDescriptorV1@1.0.0",
        "artifactId": artifact_id,
        "artifactVersionId": version_id,
        "kind": "synthetic-governed-artifact",
        "stableLocator": store.stable_locator(tenant_id, version_id, payload_hash["value"]),
        "payloadHash": payload_hash,
        "byteSize": len(payload),
        "classification": "regulated-non-phi",
        "tenantId": tenant_id,
        "region": store.region,
        "producerService": store.config.service,
        "producerEnvironment": store.config.environment,
        "producerVersion": "p2-artifact-conformance",
        "payloadContract": "accuratrials.osb.SyntheticArtifactV1",
        "payloadContractVersion": "1.0.0",
        "purpose": "workflow-orchestration",
        "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def grant(artifact_ref, operation, *, one_use=False, seconds=60):
    now = datetime.now(UTC)
    return {
        "contractVersion": "ArtifactAccessGrantV1@1.0.0",
        "grantId": str(uuid4()),
        "artifactId": artifact_ref["artifactId"],
        "artifactVersionId": artifact_ref["artifactVersionId"],
        "descriptorHash": artifact_ref["descriptorHash"],
        "payloadHash": artifact_ref["payloadHash"],
        "producerService": artifact_ref["producerService"],
        "producerEnvironment": artifact_ref["producerEnvironment"],
        "audience": "accuratrial-command-center",
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "region": artifact_ref["region"],
        "purpose": "workflow-orchestration",
        "allowedOperation": operation,
        "issuedAt": now.isoformat().replace("+00:00", "Z"),
        "notBefore": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
        "oneUse": one_use,
        "revocationReference": f"urn:accuratrials:artifact-grant:{uuid4()}",
    }


with tempfile.TemporaryDirectory(prefix="accuratrials-osb-artifact-") as directory:
    root = Path(directory)
    source = GovernedRegionalArtifactStoreV1(
        root=root / "source",
        service="osb.package",
        environment="development",
        region="us-central-1",
        encryption_key_id="dev-kms/osb/us-central-1/tenant",
        encryption_key=bytes([61]) * 32,
        storage_provider="development-encrypted-filesystem",
        provider_placement_evidence=placement,
        permitted_transfer_regions={"us-central-1", "us-east-1"},
    )
    destination = GovernedRegionalArtifactStoreV1(
        root=root / "destination",
        service="csl.attestation",
        environment="development",
        region="us-east-1",
        encryption_key_id="dev-kms/csl/us-east-1/tenant",
        encryption_key=bytes([62]) * 32,
        storage_provider="development-encrypted-filesystem",
        provider_placement_evidence=placement,
    )
    published = source.publish(
        descriptor(source),
        payload,
        retention_until=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    read_grant = grant(published["artifactRef"], "read", one_use=True)
    actual = source.read(
        published["artifactRef"],
        verified=True,
        grant=read_grant,
        audience=read_grant["audience"],
        purpose=read_grant["purpose"],
        operation="read",
    )
    assert actual == payload
    try:
        source.read(
            published["artifactRef"],
            verified=True,
            grant=read_grant,
            audience=read_grant["audience"],
            purpose=read_grant["purpose"],
            operation="read",
        )
        raise AssertionError("one-use grant replay was accepted")
    except GovernedArtifactStoreError as error:
        assert error.code == "ARTIFACT_GRANT_REPLAYED"
    expired = grant(published["artifactRef"], "read", seconds=-1)
    try:
        source.read(
            published["artifactRef"],
            verified=True,
            grant=expired,
            audience=expired["audience"],
            purpose=expired["purpose"],
            operation="read",
        )
        raise AssertionError("expired grant was accepted")
    except GovernedArtifactStoreError as error:
        assert error.code == "ARTIFACT_GRANT_SCOPE_DENIED"
    source.add_reference(published["artifactRef"], "osb-package:fixture")
    gc_result = source.garbage_collect(
        published["artifactRef"],
        cc_eligible=True,
        reference_count=1,
        now=datetime.now(UTC) + timedelta(days=2),
    )
    assert "references" in gc_result["blockers"]
    transfer_grant = grant(published["artifactRef"], "transfer")
    transferred = source.transfer_to(
        destination,
        published["artifactRef"],
        grant_verification={
            "verified": True,
            "grant": transfer_grant,
            "audience": transfer_grant["audience"],
            "purpose": transfer_grant["purpose"],
        },
        platform_study_id=platform_study_id,
        legal_basis_approval_id="approval:synthetic",
        actor_subject="service:p2-verifier",
    )
    assert (
        transferred["receipt"]["sourceEncryptionKeyId"]
        != transferred["receipt"]["destinationEncryptionKeyId"]
    )
    print(
        json.dumps(
            {
                "domain": "osb",
                "tenantId": tenant_id,
                "artifactVersionId": published["artifactRef"]["artifactVersionId"],
                "oneUseReplayRejected": True,
                "expiredRejected": True,
                "gcBlockers": gc_result["blockers"],
                "transferId": transferred["receipt"]["transferId"],
                "reencrypted": True,
            },
            separators=(",", ":"),
        )
    )
