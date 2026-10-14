"""Exact retained carrier artifacts; authentication belongs to the calling route.

Only already transferred bytes are resolved here. A locator is part of the
immutable descriptor, never an instruction to fetch a URL. Callers own the
transaction; the composite uniqueness constraint makes concurrent intake of the
same tenant/artifact/version serialize before the immutable comparison.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, NoReturn

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    sha256_bytes,
)
from clinical_mdr_api.services.integrations import native_package_v2
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.native_package_carrier_contracts import (
    validate_artifact_ref,
    validate_carrier_payload,
)

MAX_CARRIER_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_ARTIFACT_REFERENCE_HEADER_BYTES = 8192


def _fail(code: str, status: int = 422) -> NoReturn:
    raise OsbCandidateSetError(
        code, "The exact scoped carrier artifact is unavailable or invalid.", status
    ) from None


def _canonical_payload(value: bytes, code: str) -> dict[str, Any]:
    try:
        return native_package_v2._parse_canonical(value, code)
    except (
        OsbCandidateSetError,
        ValueError,
        TypeError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        _fail(code)


def _reference(artifact_ref: dict[str, Any], tenant_id: str) -> str:
    try:
        validate_artifact_ref(artifact_ref)
        descriptor_json = canonical_json(artifact_ref)
    except ValueError, TypeError, UnicodeError, OverflowError, RecursionError:
        _fail("OSB_CARRIER_ARTIFACT_REFERENCE_INVALID")
    if artifact_ref["tenantId"] != tenant_id:
        _fail("OSB_CARRIER_ARTIFACT_SCOPE_MISMATCH", 403)
    if artifact_ref["byteSize"] > MAX_CARRIER_ARTIFACT_BYTES:
        _fail("OSB_CARRIER_ARTIFACT_SIZE_LIMIT", 413)
    return descriptor_json


def _verified_payload(
    *,
    artifact_ref: dict[str, Any],
    tenant_id: str,
    platform_study_id: str,
    bytes_value: bytes,
    expected_hash: str,
) -> dict[str, Any]:
    if not isinstance(bytes_value, bytes):
        _fail("OSB_CARRIER_ARTIFACT_BYTES_INVALID")
    if len(bytes_value) > MAX_CARRIER_ARTIFACT_BYTES:
        _fail("OSB_CARRIER_ARTIFACT_SIZE_LIMIT", 413)
    _reference(artifact_ref, tenant_id)
    if (
        len(bytes_value) != artifact_ref["byteSize"]
        or expected_hash != artifact_ref["payloadHash"]["value"]
        or sha256_bytes(bytes_value) != expected_hash
    ):
        _fail("OSB_CARRIER_ARTIFACT_TRANSFER_HASH_MISMATCH")
    payload = _canonical_payload(bytes_value, "OSB_CARRIER_ARTIFACT_JSON_INVALID")
    try:
        validate_carrier_payload(payload, artifact_ref, tenant_id, platform_study_id)
    except ValueError, TypeError, UnicodeError, OverflowError, RecursionError:
        _fail("OSB_CARRIER_ARTIFACT_PAYLOAD_INVALID")
    return payload


def decode_artifact_reference_header(text: str) -> dict[str, Any]:
    """Decode a canonical, unpadded base64url full ArtifactRef header."""
    if not isinstance(text, str) or not text:
        _fail("OSB_CARRIER_ARTIFACT_HEADER_INVALID")
    if len(text) > MAX_ARTIFACT_REFERENCE_HEADER_BYTES:
        _fail("OSB_CARRIER_ARTIFACT_HEADER_SIZE_LIMIT", 413)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text) or len(text) % 4 == 1:
        _fail("OSB_CARRIER_ARTIFACT_HEADER_INVALID")
    try:
        encoded = text.encode("ascii")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except UnicodeError, ValueError, binascii.Error:
        _fail("OSB_CARRIER_ARTIFACT_HEADER_INVALID")
    # Refuse alternate encodings with nonzero unused trailing bits.
    if base64.urlsafe_b64encode(decoded).rstrip(b"=") != encoded:
        _fail("OSB_CARRIER_ARTIFACT_HEADER_INVALID")
    artifact = _canonical_payload(decoded, "OSB_CARRIER_ARTIFACT_HEADER_INVALID")
    try:
        validate_artifact_ref(artifact)
    except ValueError, TypeError, UnicodeError, OverflowError, RecursionError:
        _fail("OSB_CARRIER_ARTIFACT_HEADER_INVALID")
    return artifact


def encode_artifact_reference_header(artifact: dict[str, Any]) -> str:
    """Encode the retained descriptor using the exact shared header profile."""
    validate_artifact_ref(artifact)
    encoded = (
        base64.urlsafe_b64encode(canonical_json(artifact).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    if len(encoded) > MAX_ARTIFACT_REFERENCE_HEADER_BYTES:
        _fail("OSB_CARRIER_ARTIFACT_HEADER_SIZE_LIMIT", 413)
    return encoded


def retained_artifact_headers(
    *,
    payload_bytes,
    artifact_ref_json,
    tenant_id,
    platform_study_id,
    requested_header=None,
):
    """Verify published owner bytes and echo their complete retained descriptor."""
    artifact = _canonical_payload(
        str(artifact_ref_json).encode("utf-8"), "OSB_CARRIER_ARTIFACT_STORED_INVALID"
    )
    if requested_header is not None and canonical_json(
        decode_artifact_reference_header(requested_header)
    ) != canonical_json(artifact):
        _fail("OSB_CARRIER_ARTIFACT_REFERENCE_CONFLICT", 409)
    _verified_payload(
        artifact_ref=artifact,
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        bytes_value=payload_bytes,
        expected_hash=artifact["payloadHash"]["value"],
    )
    return {
        "x-artifact-reference": encode_artifact_reference_header(artifact),
        "etag": f'"{artifact["payloadHash"]["value"]}"',
        "content-length": str(len(payload_bytes)),
        "cache-control": "no-store",
    }


def _query(statement: str, parameters: dict[str, Any]) -> list[Any]:
    try:
        rows, _ = db.cypher_query(statement, parameters)
        return rows
    except Exception:  # pylint: disable=broad-exception-caught
        # Driver errors can include statements/parameters. Never surface them.
        raise OsbCandidateSetError(
            "OSB_CARRIER_ARTIFACT_CUSTODY_UNAVAILABLE",
            "Carrier artifact custody is unavailable.",
            503,
        ) from None


def _stored_bytes(value: Any, expected_size: int) -> bytes:
    if not isinstance(value, str) or len(value) > expected_size:
        _fail("OSB_CARRIER_ARTIFACT_STORED_INVALID", 409)
    try:
        # Count bounded chunks before making a full UTF-8 allocation. A corrupt
        # Unicode value must not expand beyond the descriptor's admitted limit.
        byte_size = 0
        for start in range(0, len(value), 65536):
            byte_size += len(value[start : start + 65536].encode("utf-8"))
            if byte_size > expected_size:
                _fail("OSB_CARRIER_ARTIFACT_STORED_INVALID", 409)
        if byte_size != expected_size:
            _fail("OSB_CARRIER_ARTIFACT_STORED_INVALID", 409)
        return value.encode("utf-8")
    except UnicodeError:
        _fail("OSB_CARRIER_ARTIFACT_STORED_INVALID", 409)


def store_carrier_artifact_bytes(
    *,
    artifact_ref: dict[str, Any],
    tenant_id: str,
    platform_study_id: str,
    bytes_value: bytes,
    expected_hash: str,
) -> dict[str, Any]:
    """Retain original descriptor and bytes, rejecting identity substitutions."""
    _verified_payload(
        artifact_ref=artifact_ref,
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        bytes_value=bytes_value,
        expected_hash=expected_hash,
    )
    descriptor_json = canonical_json(artifact_ref)
    payload_json = bytes_value.decode("utf-8")
    parameters = {
        "tenant_id": tenant_id,
        "artifact_id": artifact_ref["artifactId"],
        "artifact_version_id": artifact_ref["artifactVersionId"],
        "platform_study_id": platform_study_id,
        "artifact_ref_json": descriptor_json,
        "payload_json": payload_json,
        "byte_size": len(bytes_value),
        "kind": artifact_ref["kind"],
        "payload_hash": expected_hash,
        "descriptor_hash": artifact_ref["descriptorHash"]["value"],
    }
    rows = _query(
        """MERGE (artifact:OsbInboundCarrierArtifact {
             tenant_id:$tenant_id,artifact_id:$artifact_id,
             artifact_version_id:$artifact_version_id})
           ON CREATE SET artifact.platform_study_id=$platform_study_id,
             artifact.artifact_ref_json=$artifact_ref_json,
             artifact.payload_json=$payload_json,artifact.byte_size=$byte_size,
             artifact.kind=$kind,artifact.payload_hash=$payload_hash,
             artifact.descriptor_hash=$descriptor_hash,artifact.created_at=datetime()
           RETURN artifact.platform_study_id,artifact.artifact_ref_json,
                  artifact.payload_json,artifact.byte_size,artifact.kind,
                  artifact.payload_hash,artifact.descriptor_hash""",
        parameters,
    )
    expected = [
        platform_study_id,
        descriptor_json,
        payload_json,
        len(bytes_value),
        artifact_ref["kind"],
        expected_hash,
        artifact_ref["descriptorHash"]["value"],
    ]
    if (
        len(rows) != 1
        or not isinstance(rows[0], (list, tuple))
        or list(rows[0]) != expected
    ):
        _fail("OSB_CARRIER_ARTIFACT_TRANSFER_CONFLICT", 409)
    return {
        "contractVersion": "ArtifactTransferReceiptV1@prototype",
        "kind": artifact_ref["kind"],
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "artifactId": artifact_ref["artifactId"],
        "artifactVersionId": artifact_ref["artifactVersionId"],
        "artifactRef": artifact_ref,
        "contentHash": expected_hash,
        "byteSize": len(bytes_value),
    }


def read_carrier_artifact(
    *, artifact_ref: dict[str, Any], tenant_id: str, platform_study_id: str
) -> dict[str, Any]:
    """Read only the exact authenticated scope and retained complete descriptor."""
    descriptor_json = _reference(artifact_ref, tenant_id)
    rows = _query(
        """MATCH (artifact:OsbInboundCarrierArtifact {
             tenant_id:$tenant_id,platform_study_id:$platform_study_id,
             artifact_id:$artifact_id,artifact_version_id:$artifact_version_id})
           RETURN artifact.artifact_ref_json,artifact.payload_json,
                  artifact.byte_size,artifact.kind,
                  artifact.payload_hash,artifact.descriptor_hash""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "artifact_id": artifact_ref["artifactId"],
            "artifact_version_id": artifact_ref["artifactVersionId"],
        },
    )
    if not rows:
        _fail("OSB_CARRIER_ARTIFACT_NOT_TRANSFERRED", 404)
    if len(rows) != 1 or not isinstance(rows[0], (list, tuple)) or len(rows[0]) != 6:
        _fail("OSB_CARRIER_ARTIFACT_STORED_INVALID", 409)
    row = rows[0]
    if row[0] != descriptor_json or list(row[2:]) != [
        artifact_ref["byteSize"],
        artifact_ref["kind"],
        artifact_ref["payloadHash"]["value"],
        artifact_ref["descriptorHash"]["value"],
    ]:
        _fail("OSB_CARRIER_ARTIFACT_REFERENCE_CONFLICT", 409)
    payload_bytes = _stored_bytes(row[1], artifact_ref["byteSize"])
    payload = _verified_payload(
        artifact_ref=artifact_ref,
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        bytes_value=payload_bytes,
        expected_hash=artifact_ref["payloadHash"]["value"],
    )
    retained_reference = _canonical_payload(
        str(row[0]).encode("utf-8"), "OSB_CARRIER_ARTIFACT_STORED_INVALID"
    )
    return {
        "payload": payload,
        "artifactRef": retained_reference,
        "bytes": payload_bytes,
    }


def load_carrier_artifact(*, artifact_ref, tenant_id, platform_study_id):
    return read_carrier_artifact(
        artifact_ref=artifact_ref,
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
    )["payload"]
