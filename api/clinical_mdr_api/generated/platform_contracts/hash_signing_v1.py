"""Generated Python conformance runtime for P1-HASH-001.

The normative definitions live in Command Center JSON Schemas and the
cross-language fixture. This module intentionally performs no key custody or
RFC 3161 token validation; P1-TRUST-001 owns those operations. It does pin the
exact bytes, ES256 JOSE encoding, trusted-time preimage, and validity semantics
that later trust implementations must consume.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import copy
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

CANONICAL_JSON_VERSION = "canonical-json/1.0"
RAW_BYTES_VERSION = "raw-bytes/1.0"
SIGNATURE_PROFILE = "jws-detached-rfc7797/1.0"
STATEMENT_CONTRACT_VERSION = "ArtifactSigningStatementV1@1.0.0"
STATEMENT_MEDIA_TYPE = (
    "application/vnd.accuratrials.artifact-signing-statement-v1+json"
)
PACKAGE_V2_MEDIA_TYPE = "application/vnd.accuratrials.osb-native-package-v2+json"
TIMESTAMP_DOMAIN = b"accuratrials-signature-time/v1\0"


class PlatformHashError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise PlatformHashError(code, message)


def _assert_scalar_string(value: str, path: str = "") -> None:
    for character in value:
        if 0xD800 <= ord(character) <= 0xDFFF:
            _fail(
                "CANONICAL_JSON_INVALID_UNICODE",
                f"Surrogate code point at {path or '/'}.",
            )


def _utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def _javascript_number(value: int | float) -> str:
    if isinstance(value, int) and abs(value) > 9_007_199_254_740_991:
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        _fail("CANONICAL_JSON_NON_FINITE_NUMBER", "Non-finite number.")
    if value == 0:
        return "0"
    absolute = abs(value)
    raw = repr(value).lower()
    if 1e-6 <= absolute < 1e21:
        if "e" in raw:
            raw = format(Decimal(raw), "f")
        if "." in raw:
            raw = raw.rstrip("0").rstrip(".")
        return raw
    if "e" not in raw:
        raw = format(float(value), ".15e")
    mantissa, exponent = raw.split("e")
    mantissa = mantissa.rstrip("0").rstrip(".")
    exponent_value = int(exponent)
    sign = "+" if exponent_value >= 0 else "-"
    return f"{mantissa}e{sign}{abs(exponent_value)}"


def canonical_json(value: Any, _path: str = "", _seen: set[int] | None = None) -> str:
    seen = _seen if _seen is not None else set()
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _javascript_number(value)
    if isinstance(value, str):
        _assert_scalar_string(value, _path)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        identity = id(value)
        if identity in seen:
            _fail("CANONICAL_JSON_CYCLE", f"Cycle at {_path or '/'}.")
        seen.add(identity)
        result = "[" + ",".join(
            canonical_json(item, f"{_path}/{index}", seen)
            for index, item in enumerate(value)
        ) + "]"
        seen.remove(identity)
        return result
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            _fail("CANONICAL_JSON_CYCLE", f"Cycle at {_path or '/'}.")
        seen.add(identity)
        if any(not isinstance(key, str) for key in value):
            _fail("CANONICAL_JSON_OBJECT_KEY_NOT_STRING", "Object key is not a string.")
        fields = []
        for key in sorted(value, key=_utf16_sort_key):
            _assert_scalar_string(key, f"{_path}/<key>")
            fields.append(
                f"{json.dumps(key, ensure_ascii=False, separators=(',', ':'))}:"
                f"{canonical_json(value[key], f'{_path}/{key}', seen)}"
            )
        seen.remove(identity)
        return "{" + ",".join(fields) + "}"
    _fail(
        "CANONICAL_JSON_UNSUPPORTED_TYPE",
        f"Unsupported {type(value).__name__} at {_path or '/'}.",
    )


def sha256_bytes(value: bytes | bytearray | memoryview | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _hash_bytes(value: str) -> bytes:
    if len(value) != 71 or not value.startswith("sha256:"):
        _fail("HASH_VALUE_INVALID", "Expected a lowercase sha256-prefixed digest.")
    try:
        decoded = bytes.fromhex(value[7:])
    except ValueError:
        _fail("HASH_VALUE_INVALID", "Expected a lowercase sha256-prefixed digest.")
    if value[7:] != value[7:].lower() or len(decoded) != 32:
        _fail("HASH_VALUE_INVALID", "Expected a lowercase sha256-prefixed digest.")
    return decoded


def hash_ref_from_digest(
    *,
    value: str,
    canonicalization_version: str,
    media_type: str,
    schema_version: str,
    excluded_paths: list[str] | None = None,
) -> dict[str, Any]:
    if canonicalization_version not in {CANONICAL_JSON_VERSION, RAW_BYTES_VERSION}:
        _fail("HASH_CANONICALIZATION_UNSUPPORTED", "Unsupported canonicalization profile.")
    _hash_bytes(value)
    exclusions = list(excluded_paths or [])
    if len(set(exclusions)) != len(exclusions):
        _fail("HASH_EXCLUSIONS_INVALID", "Excluded paths must be unique.")
    return {
        "algorithm": "sha-256",
        "canonicalizationVersion": canonicalization_version,
        "value": value,
        "mediaType": media_type,
        "schemaVersion": schema_version,
        "excludedPaths": exclusions,
    }


def canonical_json_hash_ref(
    value: Any,
    *,
    schema_version: str,
    media_type: str = "application/json",
    excluded_paths: list[str] | None = None,
) -> dict[str, Any]:
    exclusions = list(excluded_paths or [])
    if exclusions:
        _fail("HASH_EXCLUSIONS_REQUIRE_PROFILE", "Exclusions are not applied implicitly.")
    return hash_ref_from_digest(
        value=sha256_bytes(canonical_json(value).encode("utf-8")),
        canonicalization_version=CANONICAL_JSON_VERSION,
        media_type=media_type,
        schema_version=schema_version,
        excluded_paths=exclusions,
    )


def raw_bytes_hash_ref(
    value: bytes,
    *,
    media_type: str,
    schema_version: str,
    excluded_paths: list[str] | None = None,
) -> dict[str, Any]:
    exclusions = list(excluded_paths or [])
    if exclusions:
        _fail("RAW_BYTES_EXCLUSIONS_PROHIBITED", "Raw-byte identities cannot exclude paths.")
    return hash_ref_from_digest(
        value=sha256_bytes(value),
        canonicalization_version=RAW_BYTES_VERSION,
        media_type=media_type,
        schema_version=schema_version,
        excluded_paths=exclusions,
    )


def hash_refs_equal(left: Any, right: Any) -> bool:
    try:
        return hmac.compare_digest(canonical_json(left), canonical_json(right))
    except (TypeError, ValueError):
        return False


def descriptor_hash(descriptor: dict[str, Any]) -> dict[str, Any]:
    return canonical_json_hash_ref(
        descriptor,
        schema_version="ArtifactDescriptorV1@1.0.0",
    )


def create_signing_statement(
    descriptor: dict[str, Any], *, signing_purpose: str, signer_asserted_iat: str
) -> dict[str, Any]:
    return {
        "contractVersion": STATEMENT_CONTRACT_VERSION,
        "descriptorHash": descriptor_hash(descriptor),
        "payloadHash": descriptor["payloadHash"],
        "artifactId": descriptor["artifactId"],
        "artifactVersionId": descriptor["artifactVersionId"],
        "payloadContract": descriptor["payloadContract"],
        "payloadContractVersion": descriptor["payloadContractVersion"],
        "tenantId": descriptor["tenantId"],
        "region": descriptor["region"],
        "classification": descriptor["classification"],
        "producerService": descriptor["producerService"],
        "producerEnvironment": descriptor["producerEnvironment"],
        "signingPurpose": signing_purpose,
        "signerAssertedIat": signer_asserted_iat,
    }


def assert_signing_statement_matches_descriptor(
    statement: dict[str, Any], descriptor: dict[str, Any]
) -> bool:
    expected = create_signing_statement(
        descriptor,
        signing_purpose=statement["signingPurpose"],
        signer_asserted_iat=statement["signerAssertedIat"],
    )
    if not hash_refs_equal(statement.get("descriptorHash"), expected["descriptorHash"]):
        _fail("SIGNING_STATEMENT_DESCRIPTOR_MISMATCH", "Descriptor hash mismatch.")
    if canonical_json(statement) != canonical_json(expected):
        _fail("SIGNING_STATEMENT_DESCRIPTOR_MISMATCH", "Statement metadata mismatch.")
    if not hash_refs_equal(statement.get("payloadHash"), descriptor.get("payloadHash")):
        _fail("SIGNING_STATEMENT_PAYLOAD_MISMATCH", "Payload hash mismatch.")
    return True


def statement_hash(statement: dict[str, Any]) -> dict[str, Any]:
    return canonical_json_hash_ref(
        statement,
        schema_version=STATEMENT_CONTRACT_VERSION,
    )


def protected_header_for(statement: dict[str, Any], kid: str) -> dict[str, Any]:
    return {
        "alg": "ES256",
        "kid": kid,
        "typ": "accuratrials-artifact-signing-statement+jws",
        "cty": STATEMENT_MEDIA_TYPE,
        "b64": False,
        "crit": ["b64"],
        "statement_contract_version": STATEMENT_CONTRACT_VERSION,
        "statement_hash": statement_hash(statement)["value"],
    }


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def encode_protected_header(header: dict[str, Any]) -> str:
    return _base64url(canonical_json(header).encode("utf-8"))


def jws_signing_input(statement: dict[str, Any], header: dict[str, Any]) -> bytes:
    if (
        header.get("statement_hash") != statement_hash(statement)["value"]
        or header.get("statement_contract_version") != STATEMENT_CONTRACT_VERSION
        or header.get("b64") is not False
        or header.get("crit") != ["b64"]
    ):
        _fail("JWS_PROTECTED_HEADER_MISMATCH", "Protected header mismatch.")
    return (
        encode_protected_header(header).encode("ascii")
        + b"."
        + canonical_json(statement).encode("utf-8")
    )


def der_to_jose_raw(der_signature: bytes) -> bytes:
    try:
        r_value, s_value = utils.decode_dss_signature(der_signature)
        return r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    except (ValueError, OverflowError) as exc:
        raise PlatformHashError("ECDSA_DER_INVALID", "Invalid ES256 DER signature.") from exc


def jose_raw_to_der(raw_signature: bytes) -> bytes:
    if len(raw_signature) != 64:
        _fail("ECDSA_JOSE_INVALID", "ES256 JOSE signature must contain 64 raw bytes.")
    return utils.encode_dss_signature(
        int.from_bytes(raw_signature[:32], "big"),
        int.from_bytes(raw_signature[32:], "big"),
    )


def verify_es256_raw(public_key_pem: str, signing_input: bytes, raw_signature: bytes) -> bool:
    if len(raw_signature) != 64:
        return False
    key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        return False
    try:
        key.verify(jose_raw_to_der(raw_signature), signing_input, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def compact_detached_jws(header: dict[str, Any], raw_signature: bytes) -> str:
    return f"{encode_protected_header(header)}..{_base64url(raw_signature)}"


def timestamp_preimage(statement_hash_ref: dict[str, Any], compact_jws: str) -> bytes:
    compact_hash = hashlib.sha256(compact_jws.encode("ascii")).digest()
    return TIMESTAMP_DOMAIN + _hash_bytes(statement_hash_ref["value"]) + compact_hash


def timestamp_message_imprint(
    statement_hash_ref: dict[str, Any], compact_jws: str
) -> dict[str, Any]:
    return raw_bytes_hash_ref(
        timestamp_preimage(statement_hash_ref, compact_jws),
        media_type="application/octet-stream",
        schema_version="SignatureTimePreimageV1@1.0.0",
    )


def _parse_time(value: str | None) -> float:
    if value is None:
        return math.inf
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        _fail("TRUST_TIME_INVALID", "Trust time metadata is invalid.")


def assert_trusted_signing_time(value: dict[str, Any]) -> bool:
    if value.get("timestampVerified") is not True:
        _fail(
            "TIMESTAMP_EVIDENCE_REQUIRED",
            "Signer-asserted time cannot replace verified trusted time.",
        )
    if (
        not isinstance(value.get("trustBundleVersion"), int)
        or value["trustBundleVersion"] < value["minimumTrustBundleVersion"]
    ):
        _fail("TRUST_BUNDLE_ROLLBACK", "Trust bundle is stale or rolled back.")
    if value.get("compromiseWindowUnknown") is True:
        _fail("KEY_HISTORY_INVALIDATED", "Unknown compromise start invalidates history.")
    gen_time = _parse_time(value.get("genTime"))
    valid_from = _parse_time(value.get("keyValidFrom"))
    valid_to = _parse_time(value.get("keyValidTo"))
    revoked = _parse_time(value.get("revokedEffectiveAt"))
    if gen_time < valid_from or gen_time >= valid_to:
        _fail("KEY_NOT_VALID_AT_TRUSTED_TIME", "Trusted time is outside key validity.")
    if gen_time >= revoked:
        _fail("KEY_REVOKED_AT_TRUSTED_TIME", "Trusted time is after revocation.")
    return True


FORBIDDEN_INTENT_KEYS = {
    "accessToken",
    "refreshToken",
    "bearerToken",
    "authorizationHeader",
    "attemptId",
    "leaseId",
    "dispatchTime",
    "retryCount",
    "notBeforeAttempt",
}


def _forbidden_intent_path(value: Any, path: str = "") -> str | None:
    if isinstance(value, list):
        for index, child in enumerate(value):
            found = _forbidden_intent_path(child, f"{path}/{index}")
            if found:
                return found
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_INTENT_KEYS:
                return f"{path}/{key}"
            found = _forbidden_intent_path(child, f"{path}/{key}")
            if found:
                return found
    return None


def command_intent_hash(intent: dict[str, Any]) -> dict[str, Any]:
    forbidden = _forbidden_intent_path(intent)
    if forbidden:
        _fail("COMMAND_INTENT_EPHEMERAL_FIELD", f"Ephemeral field at {forbidden}.")
    return canonical_json_hash_ref(intent, schema_version="CommandIntentV1@1.0.0")


COUNT_KEYS = {
    "native": "native",
    "governed_extension": "governedExtension",
    "excluded_signed": "excludedSigned",
    "deferred_blocking": "deferredBlocking",
    "quarantined": "quarantined",
    "rejected": "rejected",
}


def census_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "rows": len(rows),
        "native": 0,
        "governedExtension": 0,
        "excludedSigned": 0,
        "deferredBlocking": 0,
        "quarantined": 0,
        "rejected": 0,
    }
    for row in rows:
        key = COUNT_KEYS.get(row.get("disposition"))
        if not key:
            _fail("CENSUS_DISPOSITION_INVALID", "Unknown census disposition.")
        counts[key] += 1
    return counts


def validate_and_canonicalize_census_rows(
    rows: list[dict[str, Any]], *, require_canonical_order: bool = True
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        _fail("CENSUS_ROWS_INVALID", "Census rows must be an array.")
    ordered = sorted(rows, key=lambda row: _utf16_sort_key(row.get("unitId", "")))
    if require_canonical_order and canonical_json(rows) != canonical_json(ordered):
        _fail("CENSUS_ORDER_INVALID", "Census rows are not in canonical order.")
    unit_ids: set[str] = set()
    target_paths: set[str] = set()
    sources: dict[str, tuple[Any, Any]] = {}
    for row in ordered:
        unit_id = row.get("unitId")
        if not isinstance(unit_id, str) or not unit_id:
            _fail("CENSUS_UNIT_ID_INVALID", "Census row has no stable unit ID.")
        if unit_id in unit_ids:
            _fail("CENSUS_DUPLICATE_UNIT", f"Duplicate census unit {unit_id}.")
        unit_ids.add(unit_id)
        grouped = bool(row.get("splitMergeGroup")) and bool(row.get("splitMergeRule"))
        if bool(row.get("splitMergeGroup")) != bool(row.get("splitMergeRule")):
            _fail("CENSUS_SPLIT_MERGE_INVALID", f"Incomplete split/merge for {unit_id}.")
        multiplicity = row.get("multiplicity") or {}
        if multiplicity.get("source", 0) < 1 or multiplicity.get("target", -1) < 0:
            _fail("CENSUS_MULTIPLICITY_INVALID", f"Invalid multiplicity for {unit_id}.")
        ordering = row.get("ordering") or {}
        if ordering.get("significant") and not (
            isinstance(ordering.get("sourceIndex"), int)
            and isinstance(ordering.get("targetIndex"), int)
        ):
            _fail("CENSUS_ORDERING_INDEX_REQUIRED", f"Ordering indices missing for {unit_id}.")
        if row.get("disposition") == "excluded_signed" and not row.get("exclusionPolicy"):
            _fail("CENSUS_EXCLUSION_POLICY_REQUIRED", f"Exclusion policy missing for {unit_id}.")
        if row.get("disposition") != "excluded_signed" and row.get("exclusionPolicy"):
            _fail("CENSUS_EXCLUSION_POLICY_UNEXPECTED", f"Unexpected exclusion for {unit_id}.")
        target = row.get("target")
        if target is None:
            if multiplicity.get("target") != 0:
                _fail("CENSUS_TARGET_MULTIPLICITY_MISMATCH", f"Missing target for {unit_id}.")
        else:
            target_key = "\0".join(
                str(target.get(key, ""))
                for key in ("artifactId", "contract", "type", "path")
            )
            if target_key in target_paths:
                _fail("CENSUS_TARGET_PATH_COLLISION", f"Target path collision for {unit_id}.")
            target_paths.add(target_key)
        source = row["source"]
        source_key = "\0".join(
            str(source.get(key, ""))
            for key in ("artifactId", "contract", "type", "path")
        )
        previous = sources.get(source_key)
        current = (row.get("splitMergeGroup"), row.get("splitMergeRule"))
        if previous and (not grouped or previous != current):
            _fail("CENSUS_UNDECLARED_MANY_TO_ONE", f"Undeclared repeated source for {unit_id}.")
        sources.setdefault(source_key, current)
    return ordered


def census_row_set_hash(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = validate_and_canonicalize_census_rows(rows)
    return canonical_json_hash_ref(
        ordered,
        schema_version="ConservationCensusRowsV1@1.0.0",
    )


def validate_census(census: dict[str, Any]) -> bool:
    rows = validate_and_canonicalize_census_rows(census["rows"])
    if not hash_refs_equal(census.get("rowSetHash"), census_row_set_hash(rows)):
        _fail("CENSUS_ROW_SET_HASH_MISMATCH", "Census row-set hash mismatch.")
    if canonical_json(census.get("counts")) != canonical_json(census_counts(rows)):
        _fail("CENSUS_COUNTS_MISMATCH", "Census counts mismatch.")
    return True


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON_DUPLICATE_KEY", f"Duplicate JSON key {key}.")
        result[key] = value
    return result


def parse_strict_json_bytes(value: bytes) -> tuple[str, Any]:
    if value.startswith(b"\xef\xbb\xbf"):
        _fail("JSON_BOM_PROHIBITED", "UTF-8 BOM is prohibited.")
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PlatformHashError("JSON_UTF8_INVALID", "JSON is not valid UTF-8.") from exc
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _token: _fail(
                "CANONICAL_JSON_NON_FINITE_NUMBER", "Non-finite JSON number."
            ),
        )
    except PlatformHashError:
        raise
    except json.JSONDecodeError as exc:
        raise PlatformHashError("JSON_VALUE_INVALID", "Invalid JSON bytes.") from exc
    return text, parsed


def assert_canonical_package_bytes(
    value: bytes,
    *,
    media_type: str,
    schema_version: str = "OsbNativePackageV2@2.0.0",
) -> dict[str, Any]:
    if media_type != PACKAGE_V2_MEDIA_TYPE:
        _fail("PACKAGE_MEDIA_TYPE_INVALID", "Package V2 media type mismatch.")
    text, parsed = parse_strict_json_bytes(value)
    canonical = canonical_json(parsed)
    if text != canonical or value != canonical.encode("utf-8"):
        _fail("PACKAGE_NONCANONICAL_BYTES", "Package V2 bytes are not canonical.")
    return {
        "value": parsed,
        "hash": raw_bytes_hash_ref(
            value,
            media_type=media_type,
            schema_version=schema_version,
        ),
    }


def _expect_error(code: str, callback: Callable[[], Any]) -> bool:
    try:
        callback()
    except PlatformHashError as exc:
        if exc.code == code:
            return True
        raise
    _fail("EXPECTED_NEGATIVE_DID_NOT_FAIL", f"Expected {code}.")


def verify_conformance_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    if (
        fixture.get("fixtureVersion")
        != "accuratrials.cross-language-signing-conformance/1.0"
        or fixture.get("prototypeOnly") is not True
    ):
        _fail("CONFORMANCE_FIXTURE_INVALID", "Unsupported fixture.")
    for vector in fixture["canonicalJsonVectors"]:
        canonical = canonical_json(vector["input"])
        if canonical != vector["canonical"] or sha256_bytes(canonical) != vector["hash"]:
            _fail("CANONICAL_VECTOR_MISMATCH", f"Canonical vector {vector['id']} failed.")

    artifact = fixture["artifactVector"]
    payload_hash = canonical_json_hash_ref(
        artifact["payload"],
        media_type=artifact["descriptor"]["payloadHash"]["mediaType"],
        schema_version=artifact["descriptor"]["payloadHash"]["schemaVersion"],
    )
    if not hash_refs_equal(payload_hash, artifact["descriptor"]["payloadHash"]):
        _fail("PAYLOAD_VECTOR_MISMATCH", "Payload vector mismatch.")
    if (
        canonical_json(artifact["descriptor"]) != artifact["descriptorCanonical"]
        or not hash_refs_equal(descriptor_hash(artifact["descriptor"]), artifact["descriptorHash"])
    ):
        _fail("DESCRIPTOR_VECTOR_MISMATCH", "Descriptor vector mismatch.")
    assert_signing_statement_matches_descriptor(artifact["statement"], artifact["descriptor"])
    statement_hash_ref = statement_hash(artifact["statement"])
    if (
        canonical_json(artifact["statement"]) != artifact["statementCanonical"]
        or not hash_refs_equal(statement_hash_ref, artifact["statementHash"])
    ):
        _fail("STATEMENT_VECTOR_MISMATCH", "Statement vector mismatch.")
    if (
        canonical_json(artifact["protectedHeader"]) != artifact["protectedCanonical"]
        or encode_protected_header(artifact["protectedHeader"])
        != artifact["protectedBase64Url"]
    ):
        _fail("PROTECTED_HEADER_VECTOR_MISMATCH", "Protected header mismatch.")
    signing_input = jws_signing_input(artifact["statement"], artifact["protectedHeader"])
    if (
        base64.b64encode(signing_input).decode("ascii") != artifact["signingInputBase64"]
        or sha256_bytes(signing_input) != artifact["signingInputHash"]
    ):
        _fail("SIGNING_INPUT_VECTOR_MISMATCH", "Signing input mismatch.")
    der = base64.b64decode(artifact["signatureDerBase64"])
    raw = der_to_jose_raw(der)
    if (
        _base64url(raw) != artifact["signatureJoseBase64Url"]
        or jose_raw_to_der(raw) != der
        or not verify_es256_raw(artifact["publicKeyPem"], signing_input, raw)
    ):
        _fail("ECDSA_VECTOR_MISMATCH", "ES256 vector mismatch.")
    compact = compact_detached_jws(artifact["protectedHeader"], raw)
    if compact != artifact["compactJws"]:
        _fail("JWS_COMPACT_VECTOR_MISMATCH", "Compact JWS mismatch.")
    preimage = timestamp_preimage(statement_hash_ref, compact)
    imprint = timestamp_message_imprint(statement_hash_ref, compact)
    if (
        preimage.hex() != artifact["timestampPreimageHex"]
        or not hash_refs_equal(imprint, artifact["timestampMessageImprint"])
    ):
        _fail("TIMESTAMP_VECTOR_MISMATCH", "Timestamp preimage mismatch.")

    command = fixture["commandIntentVector"]
    if (
        canonical_json(command["intent"]) != command["canonical"]
        or not hash_refs_equal(command_intent_hash(command["intent"]), command["hash"])
    ):
        _fail("COMMAND_INTENT_VECTOR_MISMATCH", "Command intent mismatch.")
    census = fixture["censusVector"]["census"]
    validate_census(census)
    if (
        canonical_json(census["rows"]) != fixture["censusVector"]["canonicalRows"]
        or not hash_refs_equal(census["rowSetHash"], fixture["censusVector"]["rowSetHash"])
    ):
        _fail("CENSUS_VECTOR_MISMATCH", "Census vector mismatch.")
    package_vector = fixture["packageVector"]
    package_bytes = base64.b64decode(package_vector["canonicalBase64"])
    package_result = assert_canonical_package_bytes(
        package_bytes,
        media_type=package_vector["mediaType"],
        schema_version=package_vector["schemaVersion"],
    )
    if not hash_refs_equal(package_result["hash"], package_vector["hash"]):
        _fail("PACKAGE_VECTOR_MISMATCH", "Package vector mismatch.")
    for negative in package_vector["negativeVectors"]:
        _expect_error(
            negative["code"],
            lambda vector=negative: assert_canonical_package_bytes(
                base64.b64decode(vector["bytesBase64"]),
                media_type=vector["mediaType"],
                schema_version=package_vector["schemaVersion"],
            ),
        )
    for negative in fixture["hashIdentityNegativeVectors"]:
        if hash_refs_equal(negative["left"], negative["right"]):
            _fail("HASH_METADATA_COLLISION", f"Metadata vector {negative['id']} collided.")
    for mutation in fixture.get("descriptorMutationVectors", []):
        descriptor = copy.deepcopy(artifact["descriptor"])
        descriptor[mutation["field"]] = mutation["value"]
        _expect_error(
            mutation["code"],
            lambda item=descriptor: assert_signing_statement_matches_descriptor(
                artifact["statement"], item
            ),
        )
    for vector in fixture["trustVectors"]:
        if vector["ok"]:
            assert_trusted_signing_time(vector["input"])
        else:
            _expect_error(
                vector["code"],
                lambda item=vector: assert_trusted_signing_time(item["input"]),
            )
    for negative in fixture["behaviorNegativeVectors"]:
        if negative["operation"] == "command-intent":
            callback = lambda item=negative: command_intent_hash(item["input"])
        elif negative["operation"] == "census":
            callback = lambda item=negative: validate_census(item["input"])
        elif negative["operation"] == "signing-statement":
            callback = lambda item=negative: assert_signing_statement_matches_descriptor(
                item["input"]["statement"], item["input"]["descriptor"]
            )
        else:
            _fail("CONFORMANCE_OPERATION_UNKNOWN", "Unknown negative operation.")
        _expect_error(negative["code"], callback)

    return {
        "canonicalVectorCount": len(fixture["canonicalJsonVectors"]),
        "descriptorHash": artifact["descriptorHash"]["value"],
        "statementHash": artifact["statementHash"]["value"],
        "protectedBase64Url": artifact["protectedBase64Url"],
        "signingInputHash": artifact["signingInputHash"],
        "signatureJoseBase64Url": artifact["signatureJoseBase64Url"],
        "timestampMessageImprint": artifact["timestampMessageImprint"]["value"],
        "commandIntentHash": command["hash"]["value"],
        "censusRowSetHash": census["rowSetHash"]["value"],
        "packageHash": package_vector["hash"]["value"],
        "packageNegativeCount": len(package_vector["negativeVectors"]),
        "metadataNegativeCount": len(fixture["hashIdentityNegativeVectors"]),
        "descriptorMutationCount": len(fixture.get("descriptorMutationVectors", [])),
        "behaviorNegativeCount": len(fixture["behaviorNegativeVectors"]),
        "trustVectorCount": len(fixture["trustVectors"]),
    }


__all__ = [name for name in globals() if not name.startswith("_")]
