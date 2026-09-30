"""Regional encrypted content-addressed custody for platform artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    raw_bytes_hash_ref,
)


class GovernedArtifactStoreError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _fail(code: str, message: str, status_code: int = 422):
    raise GovernedArtifactStoreError(code, message, status_code)


def _same(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def _sha256(value: bytes | str) -> str:
    payload = value.encode() if isinstance(value, str) else value
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError) as error:
        raise GovernedArtifactStoreError(
            "ARTIFACT_TIME_INVALID", "Artifact custody time is invalid."
        ) from error


@dataclass(frozen=True)
class GovernedStoreConfiguration:
    root: Path
    service: str
    environment: str
    region: str
    encryption_key_id: str
    encryption_key: bytes
    storage_provider: str
    provider_placement_evidence: dict[str, Any]
    permitted_transfer_regions: frozenset[str]
    production_eligible: bool = False


class GovernedRegionalArtifactStoreV1:
    def __init__(
        self,
        *,
        root: str | Path,
        service: str,
        environment: str,
        region: str,
        encryption_key_id: str,
        encryption_key: bytes,
        storage_provider: str,
        provider_placement_evidence: dict[str, Any],
        permitted_transfer_regions: set[str] | frozenset[str] | None = None,
        production_eligible: bool = False,
    ):
        if (
            not re.fullmatch(r"[a-z][a-z0-9._:-]{2,127}", service)
            or not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]{1,16})+", region)
            or len(encryption_key) != 32
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(provider_placement_evidence.get("value", "")),
            )
        ):
            _fail(
                "ARTIFACT_STORE_CONFIGURATION_INVALID",
                "Governed regional artifact custody is not configured.",
                500,
            )
        self.config = GovernedStoreConfiguration(
            root=Path(root).resolve(),
            service=service,
            environment=environment,
            region=region,
            encryption_key_id=encryption_key_id,
            encryption_key=bytes(encryption_key),
            storage_provider=storage_provider,
            provider_placement_evidence=provider_placement_evidence,
            permitted_transfer_regions=frozenset(permitted_transfer_regions or {region}),
            production_eligible=production_eligible,
        )
        self._cipher = AESGCM(self.config.encryption_key)

    @property
    def region(self) -> str:
        return self.config.region

    @property
    def encryption_key_id(self) -> str:
        return self.config.encryption_key_id

    @property
    def provider_placement_evidence(self) -> dict[str, Any]:
        return self.config.provider_placement_evidence

    def stable_locator(
        self, tenant_id: str, artifact_version_id: str, payload_hash: str
    ) -> str:
        return (
            f"artifact://{self.config.service}/{tenant_id}/{self.region}/"
            f"{artifact_version_id}/sha256/{payload_hash[7:]}"
        )

    def publish(
        self,
        descriptor: dict[str, Any],
        payload_bytes: bytes,
        *,
        retention_until: str,
        legal_hold: bool = False,
    ) -> dict[str, Any]:
        payload_hash = raw_bytes_hash_ref(
            payload_bytes,
            schema_version=descriptor["payloadHash"]["schemaVersion"],
            media_type=descriptor["payloadHash"]["mediaType"],
        )
        if (
            descriptor.get("contractVersion") != "ArtifactDescriptorV1@1.0.0"
            or descriptor.get("producerService") != self.config.service
            or descriptor.get("producerEnvironment") != self.config.environment
            or descriptor.get("region") != self.region
            or descriptor.get("byteSize") != len(payload_bytes)
            or descriptor.get("stableLocator")
            != self.stable_locator(
                descriptor["tenantId"],
                descriptor["artifactVersionId"],
                payload_hash["value"],
            )
            or not _same(descriptor.get("payloadHash"), payload_hash)
        ):
            _fail(
                "ARTIFACT_DESCRIPTOR_MISMATCH",
                "Descriptor does not bind exact bytes and regional custody.",
            )
        retention = _parse_time(retention_until)
        if retention <= datetime.now(UTC):
            _fail(
                "ARTIFACT_RETENTION_INVALID",
                "Artifact retention must end in the future.",
            )
        descriptor_hash = canonical_json_hash_ref(
            descriptor, schema_version="ArtifactDescriptorV1@1.0.0"
        )
        artifact_ref = {
            "contractVersion": "ArtifactRefV1@1.0.0",
            **{
                key: value
                for key, value in descriptor.items()
                if key != "contractVersion"
            },
            "descriptorHash": descriptor_hash,
        }
        object_path = self._object_path(
            descriptor["tenantId"], payload_hash["value"]
        )
        metadata_path = self._metadata_path(
            descriptor["tenantId"], descriptor["artifactVersionId"]
        )
        replay = False
        if object_path.exists():
            if _sha256(self._decrypt(object_path.read_bytes())) != payload_hash["value"]:
                _fail(
                    "ARTIFACT_STORE_CORRUPTION",
                    "Stored ciphertext differs from its content address.",
                    500,
                )
            replay = True
        else:
            self._atomic_create(object_path, self._encrypt(payload_bytes))
        custody = {
            "version": "governed-artifact-custody/1.0",
            "descriptor": descriptor,
            "artifactRef": artifact_ref,
            "encryptionKeyId": self.config.encryption_key_id,
            "storageProvider": self.config.storage_provider,
            "providerPlacementEvidence": self.config.provider_placement_evidence,
            "physicalRegion": self.region,
            "retentionUntil": retention.isoformat().replace("+00:00", "Z"),
            "legalHold": bool(legal_hold),
            "productionEligible": self.config.production_eligible,
        }
        if metadata_path.exists():
            if not _same(json.loads(metadata_path.read_text()), custody):
                _fail(
                    "ARTIFACT_VERSION_CONFLICT",
                    "Artifact version already identifies different custody.",
                    409,
                )
            replay = True
        else:
            self._atomic_create(metadata_path, canonical_json(custody).encode())
        if legal_hold:
            self._marker(Path(f"{metadata_path}.legal-hold"), "active")
        return {
            "artifactRef": artifact_ref,
            "custody": self._custody_projection(custody),
            "replay": replay,
        }

    def read(
        self,
        artifact_ref: dict[str, Any],
        *,
        verified: bool,
        grant: dict[str, Any],
        audience: str,
        purpose: str,
        operation: str,
        now: datetime | None = None,
    ) -> bytes:
        custody = self._load(artifact_ref)
        current = now or datetime.now(UTC)
        if (
            not verified
            or grant.get("contractVersion") != "ArtifactAccessGrantV1@1.0.0"
            or grant.get("audience") != audience
            or grant.get("purpose") != purpose
            or grant.get("allowedOperation") != operation
            or any(
                grant.get(key) != artifact_ref.get(key)
                for key in (
                    "artifactId",
                    "artifactVersionId",
                    "tenantId",
                    "region",
                    "producerService",
                    "producerEnvironment",
                )
            )
            or not _same(grant.get("payloadHash"), artifact_ref.get("payloadHash"))
            or not _same(
                grant.get("descriptorHash"), artifact_ref.get("descriptorHash")
            )
            or _parse_time(grant["notBefore"]).timestamp() > current.timestamp() + 5
            or _parse_time(grant["expiresAt"]) <= current
        ):
            _fail(
                "ARTIFACT_GRANT_SCOPE_DENIED",
                "Artifact capability is unverified, expired, or scope-mismatched.",
                403,
            )
        if grant.get("oneUse"):
            marker = Path(
                f"{self._metadata_path(artifact_ref['tenantId'], artifact_ref['artifactVersionId'])}"
                f".grant-{grant['grantId']}.used"
            )
            try:
                self._atomic_create(marker, current.isoformat().encode())
            except FileExistsError:
                _fail(
                    "ARTIFACT_GRANT_REPLAYED",
                    "One-use artifact capability was already consumed.",
                    403,
                )
        plaintext = self._decrypt(
            self._object_path(
                artifact_ref["tenantId"], artifact_ref["payloadHash"]["value"]
            ).read_bytes()
        )
        if (
            not _same(custody["artifactRef"], artifact_ref)
            or len(plaintext) != artifact_ref["byteSize"]
            or _sha256(plaintext) != artifact_ref["payloadHash"]["value"]
        ):
            _fail(
                "ARTIFACT_INTEGRITY_FAILURE",
                "Stored artifact differs from its exact reference.",
                500,
            )
        return plaintext

    def add_reference(self, artifact_ref: dict[str, Any], reference_id: str) -> None:
        self._load(artifact_ref)
        marker = Path(
            f"{self._metadata_path(artifact_ref['tenantId'], artifact_ref['artifactVersionId'])}.refs"
        ) / hashlib.sha256(reference_id.encode()).hexdigest()
        self._marker(marker, reference_id)

    def set_legal_hold(self, artifact_ref: dict[str, Any], reason: str) -> None:
        self._load(artifact_ref)
        if not reason.strip():
            _fail(
                "ARTIFACT_LEGAL_HOLD_REASON_REQUIRED",
                "Legal hold requires a reason.",
            )
        self._marker(
            Path(
                f"{self._metadata_path(artifact_ref['tenantId'], artifact_ref['artifactVersionId'])}.legal-hold"
            ),
            reason,
        )

    def garbage_collect(
        self,
        artifact_ref: dict[str, Any],
        *,
        cc_eligible: bool,
        reference_count: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        custody = self._load(artifact_ref)
        metadata_path = self._metadata_path(
            artifact_ref["tenantId"], artifact_ref["artifactVersionId"]
        )
        reference_path = Path(f"{metadata_path}.refs")
        references = list(reference_path.iterdir()) if reference_path.exists() else []
        blockers: list[str] = []
        if custody["legalHold"] or Path(f"{metadata_path}.legal-hold").exists():
            blockers.append("legal_hold")
        if _parse_time(custody["retentionUntil"]) > (now or datetime.now(UTC)):
            blockers.append("retention")
        if references or reference_count > 0:
            blockers.append("references")
        if not cc_eligible:
            blockers.append("cc_not_eligible")
        if blockers:
            return {"deleted": False, "blockers": blockers}
        metadata_path.unlink()
        metadata_directory = metadata_path.parent
        shared_content = False
        for candidate_path in metadata_directory.glob("*.json"):
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            if (
                candidate["artifactRef"]["payloadHash"]["value"]
                == artifact_ref["payloadHash"]["value"]
            ):
                shared_content = True
                break
        if not shared_content:
            self._object_path(
                artifact_ref["tenantId"], artifact_ref["payloadHash"]["value"]
            ).unlink()
        return {"deleted": True, "blockers": []}

    def transfer_to(
        self,
        destination: "GovernedRegionalArtifactStoreV1",
        artifact_ref: dict[str, Any],
        *,
        grant_verification: dict[str, Any],
        platform_study_id: str,
        legal_basis_approval_id: str,
        actor_subject: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if (
            destination.region not in self.config.permitted_transfer_regions
            or not legal_basis_approval_id.strip()
        ):
            _fail(
                "ARTIFACT_TRANSFER_REGION_DENIED",
                "Destination region or legal basis is not approved.",
                403,
            )
        if (
            destination.region != self.region
            and destination.encryption_key_id == self.encryption_key_id
        ):
            _fail(
                "ARTIFACT_TRANSFER_REENCRYPTION_REQUIRED",
                "Cross-region transfer requires destination re-encryption.",
                409,
            )
        started = now or datetime.now(UTC)
        payload_bytes = self.read(
            artifact_ref, operation="transfer", now=started, **grant_verification
        )
        version_id = str(uuid.uuid4())
        descriptor = {
            "contractVersion": "ArtifactDescriptorV1@1.0.0",
            "artifactId": artifact_ref["artifactId"],
            "artifactVersionId": version_id,
            "kind": artifact_ref["kind"],
            "stableLocator": destination.stable_locator(
                artifact_ref["tenantId"],
                version_id,
                artifact_ref["payloadHash"]["value"],
            ),
            "payloadHash": artifact_ref["payloadHash"],
            "byteSize": artifact_ref["byteSize"],
            "classification": artifact_ref["classification"],
            "tenantId": artifact_ref["tenantId"],
            "region": destination.region,
            "producerService": destination.config.service,
            "producerEnvironment": destination.config.environment,
            "producerVersion": artifact_ref["producerVersion"],
            "payloadContract": artifact_ref["payloadContract"],
            "payloadContractVersion": artifact_ref["payloadContractVersion"],
            "purpose": "regional-transfer",
            "createdAt": started.isoformat().replace("+00:00", "Z"),
        }
        source_custody = self._load(artifact_ref)
        published = destination.publish(
            descriptor,
            payload_bytes,
            retention_until=source_custody["retentionUntil"],
            legal_hold=source_custody["legalHold"],
        )
        completed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return {
            "destinationArtifact": published["artifactRef"],
            "receipt": {
                "contractVersion": "ArtifactTransferReceiptV1@1.0.0",
                "transferId": str(uuid.uuid4()),
                "tenantId": artifact_ref["tenantId"],
                "platformStudyId": platform_study_id,
                "sourceArtifact": artifact_ref,
                "destinationArtifact": published["artifactRef"],
                "sourceRegion": self.region,
                "destinationRegion": destination.region,
                "sourceEncryptionKeyId": self.encryption_key_id,
                "destinationEncryptionKeyId": destination.encryption_key_id,
                "legalBasisApprovalId": legal_basis_approval_id,
                "providerPlacementEvidence": destination.provider_placement_evidence,
                "bytesTransferred": len(payload_bytes),
                "verifiedPayloadHash": artifact_ref["payloadHash"],
                "startedAt": started.isoformat().replace("+00:00", "Z"),
                "completedAt": completed_at,
                "actorSubject": actor_subject,
                "disposition": (
                    "already_present" if published["replay"] else "copied"
                ),
            },
        }

    def _object_path(self, tenant_id: str, payload_hash: str) -> Path:
        key_partition = hashlib.sha256(
            self.config.encryption_key_id.encode()
        ).hexdigest()[:16]
        digest = payload_hash[7:]
        return (
            self.config.root
            / "objects"
            / tenant_id
            / self.region
            / key_partition
            / "sha256"
            / digest[:2]
            / f"{digest}.enc"
        )

    def _metadata_path(self, tenant_id: str, version_id: str) -> Path:
        return self.config.root / "metadata" / tenant_id / f"{version_id}.json"

    def _load(self, artifact_ref: dict[str, Any]) -> dict[str, Any]:
        metadata = json.loads(
            self._metadata_path(
                artifact_ref["tenantId"], artifact_ref["artifactVersionId"]
            ).read_text()
        )
        if (
            not _same(metadata["artifactRef"], artifact_ref)
            or metadata["physicalRegion"] != self.region
            or metadata["encryptionKeyId"] != self.encryption_key_id
        ):
            _fail(
                "ARTIFACT_REFERENCE_SUBSTITUTION",
                "Artifact reference differs from custody metadata.",
                409,
            )
        return metadata

    def _encrypt(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return b"\x01" + nonce + self._cipher.encrypt(nonce, plaintext, None)

    def _decrypt(self, container: bytes) -> bytes:
        if len(container) < 30 or container[0] != 1:
            _fail(
                "ARTIFACT_CIPHERTEXT_INVALID",
                "Artifact ciphertext profile is invalid.",
                500,
            )
        try:
            return self._cipher.decrypt(container[1:13], container[13:], None)
        except Exception as error:
            raise GovernedArtifactStoreError(
                "ARTIFACT_DECRYPTION_FAILED",
                "Artifact ciphertext failed authenticated decryption.",
                500,
            ) from error

    @staticmethod
    def _atomic_create(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{path}.{os.getpid()}.{uuid.uuid4()}.tmp")
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _marker(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            return

    @staticmethod
    def _custody_projection(custody: dict[str, Any]) -> dict[str, Any]:
        return {
            "physicalRegion": custody["physicalRegion"],
            "encryptionKeyId": custody["encryptionKeyId"],
            "storageProvider": custody["storageProvider"],
            "providerPlacementEvidence": custody["providerPlacementEvidence"],
            "immutable": True,
            "productionEligible": custody["productionEligible"],
        }
