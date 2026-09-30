"""Cross-language signing trust implementation for P1-TRUST-001.

Production applications supply an external KMS/HSM signer and an RFC 3161 TSA.
The certificate/token construction helpers in this module exist only to create
the frozen synthetic non-PHI conformance fixture; private keys are never
serialized into that fixture.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID
from pyasn1.codec.der import decoder, encoder
from pyasn1.type import univ, useful
from pyasn1_modules import rfc3161, rfc5280, rfc5652

from .hash_signing_v1 import (
    CANONICAL_JSON_VERSION,
    RAW_BYTES_VERSION,
    SIGNATURE_PROFILE,
    assert_signing_statement_matches_descriptor,
    assert_trusted_signing_time,
    canonical_json,
    canonical_json_hash_ref,
    compact_detached_jws,
    create_signing_statement,
    descriptor_hash,
    encode_protected_header,
    hash_refs_equal,
    jose_raw_to_der,
    jws_signing_input,
    protected_header_for,
    raw_bytes_hash_ref,
    sha256_bytes,
    statement_hash,
    timestamp_message_imprint,
    verify_es256_raw,
)

OID_CMS_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_CMS_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_CMS_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_CMS_SIGNING_TIME = "1.2.840.113549.1.9.5"
OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"
OID_RFC3161_TST_INFO = "1.2.840.113549.1.9.16.1.4"
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_TIME_STAMPING_EKU = "1.3.6.1.5.5.7.3.8"
TRUST_BUNDLE_CONTRACT = "SigningTrustBundleV1@1.0.0"


class SigningTrustError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise SigningTrustError(code, message)


def _instant(value: str, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        _fail(code, "Invalid RFC 3339 instant.")
    if parsed.tzinfo is None:
        _fail(code, "Instant must include a timezone.")
    return parsed.astimezone(UTC)


def certificate_fingerprint(certificate: x509.Certificate) -> str:
    return sha256_bytes(certificate.public_bytes(serialization.Encoding.DER))


def public_key_fingerprint(public_key: ec.EllipticCurvePublicKey) -> str:
    return sha256_bytes(
        public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def _algorithm_identifier(oid: str) -> rfc5280.AlgorithmIdentifier:
    value = rfc5280.AlgorithmIdentifier()
    value["algorithm"] = univ.ObjectIdentifier(oid)
    value["parameters"] = encoder.encode(univ.Null(""))
    return value


def _cms_attribute(oid: str, encoded_value: bytes) -> rfc5652.Attribute:
    attribute = rfc5652.Attribute()
    attribute["attrType"] = univ.ObjectIdentifier(oid)
    attribute["attrValues"][0] = encoded_value
    return attribute


def issue_prototype_rfc3161_token(
    *,
    message_imprint: str,
    policy_oid: str,
    gen_time: datetime,
    serial_number: int,
    tsa_private_key: ec.EllipticCurvePrivateKey,
    tsa_certificate: x509.Certificate,
    accuracy_seconds: int = 1,
) -> bytes:
    """Issue a standards-shaped CMS TimeStampToken for a frozen test fixture."""
    if not message_imprint.startswith("sha256:") or len(message_imprint) != 71:
        _fail("RFC3161_EXPECTED_IMPRINT_INVALID", "Message imprint is invalid.")
    gen_time = gen_time.astimezone(UTC).replace(microsecond=0)
    tst_info = rfc3161.TSTInfo()
    tst_info["version"] = 1
    tst_info["policy"] = univ.ObjectIdentifier(policy_oid)
    tst_info["messageImprint"]["hashAlgorithm"] = _algorithm_identifier(OID_SHA256)
    tst_info["messageImprint"]["hashedMessage"] = bytes.fromhex(message_imprint[7:])
    tst_info["serialNumber"] = serial_number
    tst_info["genTime"] = useful.GeneralizedTime(gen_time.strftime("%Y%m%d%H%M%SZ"))
    tst_info["accuracy"]["seconds"] = accuracy_seconds
    tst_bytes = encoder.encode(tst_info)

    attributes = rfc5652.SignedAttributes()
    attributes[0] = _cms_attribute(
        OID_CMS_CONTENT_TYPE,
        encoder.encode(univ.ObjectIdentifier(OID_RFC3161_TST_INFO)),
    )
    attributes[1] = _cms_attribute(
        OID_CMS_MESSAGE_DIGEST,
        encoder.encode(univ.OctetString(hashlib.sha256(tst_bytes).digest())),
    )
    attributes[2] = _cms_attribute(
        OID_CMS_SIGNING_TIME,
        encoder.encode(useful.UTCTime(gen_time.strftime("%y%m%d%H%M%SZ"))),
    )
    signed_attributes_bytes = encoder.encode(attributes)
    signature = tsa_private_key.sign(
        signed_attributes_bytes,
        ec.ECDSA(hashes.SHA256()),
    )

    certificate_asn1, remainder = decoder.decode(
        tsa_certificate.public_bytes(serialization.Encoding.DER),
        asn1Spec=rfc5280.Certificate(),
    )
    if remainder:
        _fail("RFC3161_CERTIFICATE_INVALID", "TSA certificate has trailing bytes.")

    signed_data = rfc5652.SignedData()
    signed_data["version"] = 3
    signed_data["digestAlgorithms"][0] = _algorithm_identifier(OID_SHA256)
    signed_data["encapContentInfo"]["eContentType"] = rfc3161.id_ct_TSTInfo
    signed_data["encapContentInfo"]["eContent"] = tst_bytes
    certificate_choice = rfc5652.CertificateChoices()
    certificate_choice["certificate"] = certificate_asn1
    signed_data["certificates"][0] = certificate_choice

    signer = rfc5652.SignerInfo()
    signer["version"] = 1
    signer["sid"]["issuerAndSerialNumber"]["issuer"] = certificate_asn1[
        "tbsCertificate"
    ]["issuer"]
    signer["sid"]["issuerAndSerialNumber"]["serialNumber"] = (
        tsa_certificate.serial_number
    )
    signer["digestAlgorithm"] = _algorithm_identifier(OID_SHA256)
    for index, attribute in enumerate(attributes):
        signer["signedAttrs"][index] = attribute
    signer["signatureAlgorithm"]["algorithm"] = univ.ObjectIdentifier(
        OID_ECDSA_SHA256
    )
    signer["signature"] = signature
    signed_data["signerInfos"][0] = signer

    content_info = rfc5652.ContentInfo()
    content_info["contentType"] = rfc5652.id_signedData
    content_info["content"] = encoder.encode(signed_data)
    return encoder.encode(content_info)


def _certificate_time(certificate: x509.Certificate, name: str) -> datetime:
    modern = getattr(certificate, f"{name}_utc", None)
    if modern is not None:
        return modern
    return getattr(certificate, name).replace(tzinfo=UTC)


def _decode_attribute_value(attribute: rfc5652.Attribute, spec: Any) -> Any:
    values = attribute["attrValues"]
    if len(values) != 1:
        _fail("RFC3161_SIGNED_ATTRIBUTES_INVALID", "CMS attribute is ambiguous.")
    value, remainder = decoder.decode(bytes(values[0]), asn1Spec=spec)
    if remainder:
        _fail("RFC3161_SIGNED_ATTRIBUTES_INVALID", "CMS attribute has trailing bytes.")
    return value


def verify_rfc3161_token(
    *,
    token_base64: str,
    expected_message_imprint: str,
    authority: dict[str, Any],
) -> dict[str, Any]:
    try:
        token = base64.b64decode(token_base64, validate=True)
    except ValueError:
        _fail("RFC3161_TOKEN_ENCODING_INVALID", "Timestamp token is not valid base64.")
    if not token:
        _fail("RFC3161_TOKEN_ENCODING_INVALID", "Timestamp token is empty.")
    try:
        content_info, remainder = decoder.decode(
            token, asn1Spec=rfc5652.ContentInfo()
        )
    except Exception as error:
        raise SigningTrustError(
            "RFC3161_CMS_CONTENT_INVALID", "Timestamp token is not CMS SignedData."
        ) from error
    if remainder or str(content_info["contentType"]) != OID_CMS_SIGNED_DATA:
        _fail("RFC3161_CMS_CONTENT_INVALID", "Timestamp token is not CMS SignedData.")
    signed_data, remainder = decoder.decode(
        content_info["content"], asn1Spec=rfc5652.SignedData()
    )
    if remainder or int(signed_data["version"]) != 3:
        _fail("RFC3161_CMS_VERSION_INVALID", "Unsupported CMS version.")
    digest_algorithms = signed_data["digestAlgorithms"]
    if (
        len(digest_algorithms) != 1
        or str(digest_algorithms[0]["algorithm"]) != OID_SHA256
    ):
        _fail("RFC3161_CMS_DIGEST_INVALID", "CMS digest must be SHA-256 only.")
    encapsulated = signed_data["encapContentInfo"]
    if str(encapsulated["eContentType"]) != OID_RFC3161_TST_INFO:
        _fail("RFC3161_CMS_CONTENT_INVALID", "CMS content is not TSTInfo.")
    tst_bytes = bytes(encapsulated["eContent"])
    tst_info, remainder = decoder.decode(tst_bytes, asn1Spec=rfc3161.TSTInfo())
    if remainder or int(tst_info["version"]) != 1:
        _fail("RFC3161_TST_INFO_INVALID", "TSTInfo is invalid.")
    if str(tst_info["messageImprint"]["hashAlgorithm"]["algorithm"]) != OID_SHA256:
        _fail("RFC3161_HASH_ALGORITHM_INVALID", "TSA imprint must use SHA-256.")
    actual_imprint = "sha256:" + bytes(
        tst_info["messageImprint"]["hashedMessage"]
    ).hex()
    if actual_imprint != expected_message_imprint:
        _fail("RFC3161_MESSAGE_IMPRINT_MISMATCH", "Timestamp imprint mismatch.")
    if str(tst_info["policy"]) != authority["policyOid"]:
        _fail("RFC3161_POLICY_INVALID", "Timestamp policy is not approved.")
    accuracy = tst_info["accuracy"]
    accuracy_millis = 0.0
    if accuracy.hasValue():
        if accuracy["seconds"].hasValue():
            accuracy_millis += int(accuracy["seconds"]) * 1000
        if accuracy["millis"].hasValue():
            accuracy_millis += int(accuracy["millis"])
        if accuracy["micros"].hasValue():
            accuracy_millis += int(accuracy["micros"]) / 1000
    if accuracy_millis > authority["maxAccuracyMillis"]:
        _fail("RFC3161_ACCURACY_INVALID", "Timestamp accuracy is too broad.")

    certificates = signed_data["certificates"]
    if len(certificates) != 1 or not certificates[0]["certificate"].hasValue():
        _fail("RFC3161_CERTIFICATE_COUNT_INVALID", "Exactly one TSA leaf is required.")
    certificate_bytes = encoder.encode(certificates[0]["certificate"])
    leaf = x509.load_der_x509_certificate(certificate_bytes)
    root = x509.load_pem_x509_certificate(authority["rootCertificatePem"].encode())
    if certificate_fingerprint(leaf) != authority["leafCertificateFingerprint"]:
        _fail("RFC3161_LEAF_CERTIFICATE_INVALID", "TSA leaf is not pinned.")
    if certificate_fingerprint(root) != authority["rootCertificateFingerprint"]:
        _fail("RFC3161_ROOT_TRUST_INVALID", "TSA root is not pinned.")
    root_constraints = root.extensions.get_extension_for_class(x509.BasicConstraints)
    if not root_constraints.value.ca:
        _fail("RFC3161_ROOT_TRUST_INVALID", "TSA root is not a CA.")
    try:
        root.public_key().verify(
            root.signature,
            root.tbs_certificate_bytes,
            ec.ECDSA(root.signature_hash_algorithm),
        )
        root.public_key().verify(
            leaf.signature,
            leaf.tbs_certificate_bytes,
            ec.ECDSA(leaf.signature_hash_algorithm),
        )
    except Exception as error:
        raise SigningTrustError(
            "RFC3161_CERTIFICATE_CHAIN_INVALID", "TSA chain is invalid."
        ) from error
    eku_extension = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    if (
        authority["requireCriticalEku"]
        and not eku_extension.critical
        or list(eku_extension.value) != [ExtendedKeyUsageOID.TIME_STAMPING]
        or authority["requiredEkuOid"] != OID_TIME_STAMPING_EKU
    ):
        _fail("RFC3161_EKU_INVALID", "TSA EKU must be critical timeStamping-only.")

    signers = signed_data["signerInfos"]
    if len(signers) != 1:
        _fail("RFC3161_SIGNER_COUNT_INVALID", "Exactly one TSA signer is required.")
    signer = signers[0]
    if (
        int(signer["version"]) != 1
        or int(signer["sid"]["issuerAndSerialNumber"]["serialNumber"])
        != leaf.serial_number
        or str(signer["digestAlgorithm"]["algorithm"]) != OID_SHA256
        or str(signer["signatureAlgorithm"]["algorithm"]) != OID_ECDSA_SHA256
    ):
        _fail("RFC3161_SIGNER_INVALID", "CMS signer metadata is invalid.")
    attributes_by_oid = {
        str(attribute["attrType"]): attribute for attribute in signer["signedAttrs"]
    }
    if set(attributes_by_oid) != {
        OID_CMS_CONTENT_TYPE,
        OID_CMS_MESSAGE_DIGEST,
        OID_CMS_SIGNING_TIME,
    }:
        _fail("RFC3161_SIGNED_ATTRIBUTES_INVALID", "CMS attributes are incomplete.")
    content_type = _decode_attribute_value(
        attributes_by_oid[OID_CMS_CONTENT_TYPE], univ.ObjectIdentifier()
    )
    declared_digest = _decode_attribute_value(
        attributes_by_oid[OID_CMS_MESSAGE_DIGEST], univ.OctetString()
    )
    signing_time = _decode_attribute_value(
        attributes_by_oid[OID_CMS_SIGNING_TIME], useful.UTCTime()
    )
    if (
        str(content_type) != OID_RFC3161_TST_INFO
        or bytes(declared_digest) != hashlib.sha256(tst_bytes).digest()
    ):
        _fail("RFC3161_CONTENT_DIGEST_INVALID", "CMS content digest is invalid.")
    universal_attributes = rfc5652.SignedAttributes()
    for index, attribute in enumerate(signer["signedAttrs"]):
        universal_attributes[index] = attribute
    try:
        leaf.public_key().verify(
            bytes(signer["signature"]),
            encoder.encode(universal_attributes),
            ec.ECDSA(hashes.SHA256()),
        )
    except Exception as error:
        raise SigningTrustError(
            "RFC3161_SIGNATURE_INVALID", "Timestamp signature is invalid."
        ) from error

    gen_time = datetime.strptime(str(tst_info["genTime"]), "%Y%m%d%H%M%SZ").replace(
        tzinfo=UTC
    )
    cms_time = datetime.strptime(str(signing_time), "%y%m%d%H%M%SZ").replace(tzinfo=UTC)
    if abs((cms_time - gen_time).total_seconds() * 1000) > authority[
        "maxAccuracyMillis"
    ]:
        _fail("RFC3161_SIGNING_TIME_INVALID", "CMS signingTime disagrees with genTime.")
    if not (
        _certificate_time(leaf, "not_valid_before")
        <= gen_time
        < _certificate_time(leaf, "not_valid_after")
        and _instant(authority["validFrom"], "RFC3161_AUTHORITY_VALIDITY_INVALID")
        <= gen_time
        < _instant(authority["validTo"], "RFC3161_AUTHORITY_VALIDITY_INVALID")
    ):
        _fail("RFC3161_GENTIME_INVALID", "TSA genTime is outside validity.")
    revoked = authority.get("revokedEffectiveAt")
    if revoked and gen_time >= _instant(revoked, "RFC3161_REVOCATION_INVALID"):
        _fail("RFC3161_TSA_REVOKED", "TSA was revoked at genTime.")
    return {
        "timestampVerified": True,
        "genTime": gen_time.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "policyOid": str(tst_info["policy"]),
        "serialNumber": str(int(tst_info["serialNumber"])),
        "accuracyMillis": accuracy_millis,
        "leafCertificateFingerprint": certificate_fingerprint(leaf),
    }


def assert_bundle_shape(bundle: dict[str, Any]) -> None:
    if (
        bundle.get("contractVersion") != TRUST_BUNDLE_CONTRACT
        or not isinstance(bundle.get("bundleVersion"), int)
        or bundle["bundleVersion"] < 1
        or not bundle.get("keys")
        or not bundle.get("timestampAuthorities")
    ):
        _fail("TRUST_BUNDLE_INVALID", "Trust bundle is incomplete.")
    key_ids: set[str] = set()
    for key in bundle["keys"]:
        if key["kid"] in key_ids:
            _fail("TRUST_BUNDLE_KEY_DUPLICATE", "Trust key is duplicated.")
        key_ids.add(key["kid"])
        public_key = serialization.load_pem_public_key(key["publicKeyPem"].encode())
        if (
            key["algorithm"] != "ES256"
            or key["exportable"] is not False
            or public_key_fingerprint(public_key) != key["publicKeyFingerprint"]
        ):
            _fail("TRUST_BUNDLE_KEY_INVALID", "Trust key metadata is invalid.")


def trust_bundle_hash(bundle: dict[str, Any]) -> dict[str, Any]:
    assert_bundle_shape(bundle)
    return canonical_json_hash_ref(
        bundle, schema_version=TRUST_BUNDLE_CONTRACT, media_type="application/json"
    )


def _payload_hash(payload_bytes: bytes, identity: dict[str, Any]) -> dict[str, Any]:
    if identity["canonicalizationVersion"] == RAW_BYTES_VERSION:
        return raw_bytes_hash_ref(
            payload_bytes,
            media_type=identity["mediaType"],
            schema_version=identity["schemaVersion"],
        )
    if (
        identity["canonicalizationVersion"] != CANONICAL_JSON_VERSION
        or identity["excludedPaths"]
    ):
        _fail("PAYLOAD_HASH_PROFILE_UNSUPPORTED", "Unsupported payload hash profile.")
    try:
        text = payload_bytes.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SigningTrustError("SIGNED_PAYLOAD_INVALID", "Payload is not JSON.") from error
    if text != canonical_json(value):
        _fail("SIGNED_PAYLOAD_NONCANONICAL", "Signed JSON bytes are not canonical.")
    return canonical_json_hash_ref(
        value,
        media_type=identity["mediaType"],
        schema_version=identity["schemaVersion"],
    )


def verify_signed_artifact_envelope(
    *,
    payload_bytes: bytes,
    envelope: dict[str, Any],
    trust_bundle: dict[str, Any],
    expected_trust_bundle_hash: dict[str, Any] | None = None,
    minimum_trust_bundle_version: int = 1,
) -> dict[str, Any]:
    assert_bundle_shape(trust_bundle)
    if trust_bundle["bundleVersion"] < minimum_trust_bundle_version:
        _fail("TRUST_BUNDLE_ROLLBACK", "Trust bundle is stale.")
    bundle_hash = trust_bundle_hash(trust_bundle)
    if expected_trust_bundle_hash and not hash_refs_equal(
        bundle_hash, expected_trust_bundle_hash
    ):
        _fail("TRUST_BUNDLE_MANIFEST_MISMATCH", "Trust bundle pin mismatch.")
    if (
        envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0"
        or envelope.get("signatureProfile") != SIGNATURE_PROFILE
        or not hash_refs_equal(
            envelope["payloadHash"], envelope["artifactDescriptor"]["payloadHash"]
        )
    ):
        _fail("SIGNED_ENVELOPE_INVALID", "Signed envelope is invalid.")
    if (
        trust_bundle["environment"]
        != envelope["signingStatement"]["producerEnvironment"]
        or trust_bundle["region"] != envelope["signingStatement"]["region"]
    ):
        _fail(
            "TRUST_BUNDLE_SCOPE_MISMATCH",
            "Trust bundle does not cover the artifact environment and region.",
        )
    computed = _payload_hash(payload_bytes, envelope["payloadHash"])
    if (
        not hash_refs_equal(computed, envelope["payloadHash"])
        or envelope["artifactDescriptor"]["byteSize"] != len(payload_bytes)
    ):
        _fail("SIGNED_PAYLOAD_HASH_MISMATCH", "Payload identity mismatch.")
    assert_signing_statement_matches_descriptor(
        envelope["signingStatement"], envelope["artifactDescriptor"]
    )
    header = envelope["detachedJws"]["protectedHeader"]
    expected_header = protected_header_for(envelope["signingStatement"], header["kid"])
    if (
        canonical_json(header) != canonical_json(expected_header)
        or encode_protected_header(header)
        != envelope["detachedJws"]["protectedBase64Url"]
    ):
        _fail("JWS_PROTECTED_HEADER_MISMATCH", "Protected header mismatch.")
    try:
        signature_raw = base64.urlsafe_b64decode(
            envelope["detachedJws"]["signatureBase64Url"] + "=="
        )
    except ValueError:
        _fail("JWS_SIGNATURE_ENCODING_INVALID", "JWS signature is not base64url.")
    key = next(
        (item for item in trust_bundle["keys"] if item["kid"] == header["kid"]),
        None,
    )
    if key is None:
        _fail("SIGNING_KEY_UNKNOWN", "Signing key is unknown.")
    statement = envelope["signingStatement"]
    if (
        key["service"] != statement["producerService"]
        or key["environment"] != statement["producerEnvironment"]
        or key["region"] != statement["region"]
        or statement["signingPurpose"] not in key["purposes"]
    ):
        _fail("SIGNING_KEY_SCOPE_MISMATCH", "Signing key scope mismatch.")
    signing_input = jws_signing_input(statement, header)
    if not verify_es256_raw(key["publicKeyPem"], signing_input, signature_raw):
        _fail("JWS_SIGNATURE_INVALID", "JWS signature is invalid.")
    compact = compact_detached_jws(header, signature_raw)
    expected_imprint = timestamp_message_imprint(statement_hash(statement), compact)
    if not hash_refs_equal(
        expected_imprint, envelope["signingTimeEvidence"]["messageImprint"]
    ):
        _fail("RFC3161_MESSAGE_IMPRINT_MISMATCH", "Timestamp imprint metadata mismatch.")
    authority = next(
        (
            item
            for item in trust_bundle["timestampAuthorities"]
            if item["policyOid"]
            == envelope["signingTimeEvidence"]["tsaPolicyOid"]
        ),
        None,
    )
    if authority is None:
        _fail("RFC3161_AUTHORITY_UNKNOWN", "TSA is not approved.")
    trusted_time = verify_rfc3161_token(
        token_base64=envelope["signingTimeEvidence"]["timestampTokenBase64"],
        expected_message_imprint=expected_imprint["value"],
        authority=authority,
    )
    assert_trusted_signing_time(
        {
            "timestampVerified": True,
            "genTime": trusted_time["genTime"],
            "keyValidFrom": key["validFrom"],
            "keyValidTo": key["validTo"],
            "revokedEffectiveAt": key["revokedEffectiveAt"],
            "trustBundleVersion": trust_bundle["bundleVersion"],
            "minimumTrustBundleVersion": minimum_trust_bundle_version,
            "compromiseWindowUnknown": key["compromiseWindowUnknown"],
        }
    )
    assert_signer_asserted_time(
        statement["signerAssertedIat"],
        trusted_time["genTime"],
        trust_bundle["maxSignerClockSkewSeconds"],
    )
    return {
        "verified": True,
        "payloadHash": envelope["payloadHash"],
        "descriptorHash": envelope["signingStatement"]["descriptorHash"],
        "kid": key["kid"],
        "trustedTime": trusted_time["genTime"],
        "trustBundleVersion": trust_bundle["bundleVersion"],
        "trustBundleHash": bundle_hash,
    }


def assert_signer_asserted_time(
    signer_asserted_iat: str,
    trusted_gen_time: str,
    max_clock_skew_seconds: int,
) -> bool:
    asserted = _instant(signer_asserted_iat, "SIGNER_ASSERTED_TIME_INVALID")
    verified = _instant(trusted_gen_time, "RFC3161_GENTIME_INVALID")
    if (
        not isinstance(max_clock_skew_seconds, int)
        or max_clock_skew_seconds < 0
        or abs((asserted - verified).total_seconds()) > max_clock_skew_seconds
    ):
        _fail("SIGNER_BACKDATING_DETECTED", "Signer time is outside trusted skew.")
    return True


def validate_human_electronic_signature(
    record: dict[str, Any],
    *,
    expected_record_hash: str,
    approved_acr: set[str],
    approved_amr: set[str],
    max_reauthentication_age_seconds: int = 300,
) -> bool:
    required = [
        record.get("signature_version") == "HumanElectronicSignatureV1",
        record.get("issuer"),
        record.get("subject"),
        record.get("tenant_id"),
        record.get("signer_name_snapshot"),
        record.get("native_user_binding", {}).get("binding_id"),
        record.get("role_and_assignment", {}).get("role"),
        record.get("reauthentication", {}).get("auth_time"),
        record.get("displayed_statement"),
        record.get("meaning"),
        record.get("reason"),
        record.get("signed_at"),
    ]
    if not all(required):
        _fail("HUMAN_SIGNATURE_RECORD_INVALID", "Human signature is incomplete.")
    if any(
        key in record
        for key in ["privateKey", "private_key", "applicationSignature", "humanJws", "password"]
    ):
        _fail("HUMAN_HELD_KEY_PROHIBITED", "Human-held application key is prohibited.")
    if record["record_hash"] != expected_record_hash:
        _fail("HUMAN_SIGNATURE_RECORD_HASH_MISMATCH", "Record hash mismatch.")
    auth_time = _instant(
        record["reauthentication"]["auth_time"], "HUMAN_REAUTHENTICATION_INVALID"
    )
    signed_at = _instant(record["signed_at"], "HUMAN_SIGNATURE_TIME_INVALID")
    if (
        auth_time > signed_at
        or (signed_at - auth_time).total_seconds() > max_reauthentication_age_seconds
    ):
        _fail("HUMAN_REAUTHENTICATION_STALE", "Reauthentication is stale.")
    if record["reauthentication"]["acr"] not in approved_acr:
        _fail("HUMAN_REAUTHENTICATION_ACR_INVALID", "ACR is not approved.")
    if not approved_amr.intersection(record["reauthentication"].get("amr", [])):
        _fail("HUMAN_REAUTHENTICATION_AMR_INVALID", "AMR is not approved.")
    binding_from = _instant(
        record["native_user_binding"]["valid_from"], "HUMAN_BINDING_INVALID"
    )
    binding_to = (
        _instant(record["native_user_binding"]["valid_to"], "HUMAN_BINDING_INVALID")
        if record["native_user_binding"].get("valid_to")
        else datetime.max.replace(tzinfo=UTC)
    )
    if not binding_from <= signed_at < binding_to:
        _fail("HUMAN_BINDING_INACTIVE", "Native user binding is inactive.")
    return True


def _bootstrap_trust_bundle(anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        "contractVersion": TRUST_BUNDLE_CONTRACT,
        "bundleId": anchor["anchorId"],
        "bundleVersion": anchor["minimumTrustBundleVersion"],
        "environment": anchor["environment"],
        "region": anchor["region"],
        "previousBundleHash": None,
        "createdAt": anchor["validFrom"],
        "validFrom": anchor["validFrom"],
        "expiresAt": anchor["validTo"],
        "maxSignerClockSkewSeconds": anchor["maxSignerClockSkewSeconds"],
        "keys": [anchor["signingRootKey"]],
        "timestampAuthorities": anchor["timestampAuthorities"],
        "revocationEpoch": 0,
    }


def verify_trust_bundle_envelope(
    *,
    payload_bytes: bytes,
    envelope: dict[str, Any],
    bootstrap_anchor: dict[str, Any],
    previous_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        text = payload_bytes.decode("utf-8")
        bundle = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SigningTrustError(
            "TRUST_BUNDLE_INVALID", "Trust bundle is not JSON."
        ) from error
    if text != canonical_json(bundle):
        _fail("TRUST_BUNDLE_NONCANONICAL", "Trust bundle bytes are not canonical.")
    assert_bundle_shape(bundle)
    descriptor = envelope.get("artifactDescriptor", {})
    if (
        descriptor.get("payloadContract")
        != "accuratrials.cc.SigningTrustBundleV1"
        or descriptor.get("payloadContractVersion") != "1.0.0"
    ):
        _fail(
            "TRUST_BUNDLE_CONTRACT_MISMATCH",
            "Trust bundle envelope has the wrong payload contract.",
        )
    verify_signed_artifact_envelope(
        payload_bytes=payload_bytes,
        envelope=envelope,
        trust_bundle=_bootstrap_trust_bundle(bootstrap_anchor),
        minimum_trust_bundle_version=bootstrap_anchor["minimumTrustBundleVersion"],
    )
    if (
        bundle["environment"] != bootstrap_anchor["environment"]
        or bundle["region"] != bootstrap_anchor["region"]
    ):
        _fail(
            "TRUST_BUNDLE_SCOPE_MISMATCH",
            "Trust bundle environment/region differs from the bootstrap anchor.",
        )
    if bundle["bundleVersion"] < bootstrap_anchor["minimumTrustBundleVersion"]:
        _fail("TRUST_BUNDLE_ROLLBACK", "Trust bundle is below the bootstrap minimum.")
    if previous_bundle is None:
        if bundle["bundleVersion"] != 1 or bundle["previousBundleHash"] is not None:
            _fail(
                "TRUST_BUNDLE_CHAIN_INVALID",
                "Initial trust bundle must be version 1 with no predecessor.",
            )
    elif (
        bundle["bundleVersion"] != previous_bundle["bundleVersion"] + 1
        or not hash_refs_equal(
            bundle["previousBundleHash"], trust_bundle_hash(previous_bundle)
        )
    ):
        _fail(
            "TRUST_BUNDLE_CHAIN_INVALID",
            "Trust bundle does not extend the accepted monotonic chain.",
        )
    return {"bundle": bundle, "hash": trust_bundle_hash(bundle)}


class MonotonicTrustBundleCache:
    """Fail-closed public trust cache with an immutable version archive."""

    def __init__(self, bootstrap_anchor: dict[str, Any]):
        self.bootstrap_anchor = copy.deepcopy(bootstrap_anchor)
        self.current: dict[str, Any] | None = None
        self.archive: dict[int, dict[str, Any]] = {}

    def accept(
        self, *, payload_bytes: bytes, envelope: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            candidate = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SigningTrustError(
                "TRUST_BUNDLE_INVALID", "Trust bundle is not JSON."
            ) from error
        if (
            self.current is not None
            and candidate.get("bundleVersion", 0)
            <= self.current["bundle"]["bundleVersion"]
        ):
            _fail("TRUST_BUNDLE_ROLLBACK", "Trust bundle version did not increase.")
        verified = verify_trust_bundle_envelope(
            payload_bytes=payload_bytes,
            envelope=envelope,
            bootstrap_anchor=self.bootstrap_anchor,
            previous_bundle=self.current["bundle"] if self.current else None,
        )
        version = verified["bundle"]["bundleVersion"]
        existing = self.archive.get(version)
        if existing and not hash_refs_equal(existing["hash"], verified["hash"]):
            _fail(
                "TRUST_BUNDLE_FORK",
                "A trust bundle version identifies different bytes.",
            )
        if (
            self.current is not None
            and version <= self.current["bundle"]["bundleVersion"]
        ):
            _fail("TRUST_BUNDLE_ROLLBACK", "Trust bundle version did not increase.")
        self.archive[version] = verified
        self.current = verified
        return verified

    def get(self, version: int | None = None) -> dict[str, Any]:
        selected = (
            version
            if version is not None
            else self.current["bundle"]["bundleVersion"]
            if self.current
            else None
        )
        if selected not in self.archive:
            _fail(
                "TRUST_BUNDLE_VERSION_UNKNOWN",
                f"Trust bundle version {selected} is not archived.",
            )
        return self.archive[selected]


def _fixture_payload(item: dict[str, Any]) -> bytes:
    try:
        return base64.b64decode(item["payloadBase64"], validate=True)
    except (KeyError, ValueError) as error:
        raise SigningTrustError(
            "FIXTURE_PAYLOAD_INVALID", "Fixture payload is invalid."
        ) from error


def _expect_error(code: str, callback: Callable[[], Any]) -> None:
    try:
        callback()
    except Exception as error:
        if getattr(error, "code", None) == code:
            return
        raise AssertionError(
            f"Expected {code}, received {getattr(error, 'code', type(error).__name__)}: {error}"
        ) from error
    raise AssertionError(f"Expected {code}, but operation succeeded.")


def _mutated_signature(envelope: dict[str, Any]) -> dict[str, Any]:
    changed = copy.deepcopy(envelope)
    encoded = changed["detachedJws"]["signatureBase64Url"]
    raw = bytearray(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    raw[0] ^= 1
    changed["detachedJws"]["signatureBase64Url"] = (
        base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    )
    return changed


def _verify_fixture_negative_vectors(
    fixture: dict[str, Any], bundle: dict[str, Any]
) -> int:
    historical = fixture["artifacts"]["historicalBeforeRevocation"]
    base_payload = _fixture_payload(historical)
    base_envelope = historical["envelope"]
    authority = fixture["trustBundles"][1]["payload"]["timestampAuthorities"][0]

    for vector in fixture["negativeVectors"]:
        operation = vector["operation"]

        def verify_vector() -> None:
            if operation in {"payload-mutation", "canonicalization"}:
                value = json.loads(base_payload)
                if operation == "payload-mutation":
                    value["result"] = "mutated"
                else:
                    value["canonicalizationVersion"] = "canonical-json/substituted"
                verify_signed_artifact_envelope(
                    payload_bytes=canonical_json(value).encode(),
                    envelope=base_envelope,
                    trust_bundle=bundle,
                )
            elif operation == "wrong-key":
                verify_signed_artifact_envelope(
                    payload_bytes=base_payload,
                    envelope=_mutated_signature(base_envelope),
                    trust_bundle=bundle,
                )
            elif operation == "protected-header":
                envelope = copy.deepcopy(base_envelope)
                envelope["detachedJws"]["protectedHeader"]["typ"] = "substituted+jws"
                verify_signed_artifact_envelope(
                    payload_bytes=base_payload,
                    envelope=envelope,
                    trust_bundle=bundle,
                )
            elif operation in {
                "key-service",
                "key-environment",
                "key-purpose",
                "unknown-compromise",
            }:
                changed_bundle = copy.deepcopy(bundle)
                kid = base_envelope["detachedJws"]["protectedHeader"]["kid"]
                key = next(item for item in changed_bundle["keys"] if item["kid"] == kid)
                if operation == "key-service":
                    key["service"] = "cc.substituted"
                elif operation == "key-environment":
                    key["environment"] = "prototype-substituted"
                elif operation == "key-purpose":
                    key["purposes"] = ["unrelated-purpose"]
                else:
                    key["compromiseWindowUnknown"] = True
                verify_signed_artifact_envelope(
                    payload_bytes=base_payload,
                    envelope=base_envelope,
                    trust_bundle=changed_bundle,
                )
            elif operation == "missing-timestamp":
                envelope = copy.deepcopy(base_envelope)
                envelope["signingTimeEvidence"]["timestampTokenBase64"] = ""
                verify_signed_artifact_envelope(
                    payload_bytes=base_payload,
                    envelope=envelope,
                    trust_bundle=bundle,
                )
            elif operation == "wrong-tsa-policy":
                envelope = copy.deepcopy(base_envelope)
                envelope["signingTimeEvidence"]["tsaPolicyOid"] = (
                    "1.3.6.1.4.1.55555.9.9"
                )
                verify_signed_artifact_envelope(
                    payload_bytes=base_payload,
                    envelope=envelope,
                    trust_bundle=bundle,
                )
            elif operation == "wrong-tsa-chain":
                changed_authority = copy.deepcopy(authority)
                changed_authority["rootCertificatePem"] = fixture["wrongEku"][
                    "authority"
                ]["rootCertificatePem"]
                verify_rfc3161_token(
                    token_base64=base_envelope["signingTimeEvidence"][
                        "timestampTokenBase64"
                    ],
                    expected_message_imprint=base_envelope["signingTimeEvidence"][
                        "messageImprint"
                    ]["value"],
                    authority=changed_authority,
                )
            elif operation == "wrong-tsa-imprint":
                verify_rfc3161_token(
                    token_base64=base_envelope["signingTimeEvidence"][
                        "timestampTokenBase64"
                    ],
                    expected_message_imprint="sha256:" + "00" * 32,
                    authority=authority,
                )
            elif operation in {"human-stale-reauth", "human-held-key"}:
                record = copy.deepcopy(fixture["humanApproval"]["record"]["human_signature"])
                if operation == "human-stale-reauth":
                    record["reauthentication"]["auth_time"] = (
                        "2026-08-20T00:00:00.000Z"
                    )
                else:
                    record["privateKey"] = "prohibited"
                validate_human_electronic_signature(
                    record,
                    expected_record_hash=fixture["humanApproval"]["statementHash"],
                    approved_acr={"urn:accuratrials:prototype:high-assurance"},
                    approved_amr={"otp"},
                )
            elif operation == "evidence-tamper":
                value = copy.deepcopy(fixture["verificationEvidence"]["record"])
                value["result"] = "failed"
                verify_signed_artifact_envelope(
                    payload_bytes=canonical_json(value).encode(),
                    envelope=fixture["verificationEvidence"]["envelope"],
                    trust_bundle=bundle,
                )
            elif operation == "backdating":
                assert_signer_asserted_time(
                    "2026-01-01T00:00:00.000Z",
                    "2026-08-21T03:00:00.000Z",
                    60,
                )
            else:
                raise AssertionError(f"Unknown trust negative operation: {operation}")

        _expect_error(vector["code"], verify_vector)
    return len(fixture["negativeVectors"])


def verify_signing_trust_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Run the frozen Python side of the cross-language trust vectors."""
    if fixture.get("prototypeOnly") is not True or fixture.get(
        "privateKeysSerialized"
    ) is not False:
        _fail(
            "FIXTURE_CLASSIFICATION_INVALID",
            "Trust fixture must be synthetic and contain no private keys.",
        )
    cache = MonotonicTrustBundleCache(fixture["bootstrapAnchor"])
    accepted = [
        cache.accept(payload_bytes=_fixture_payload(item), envelope=item["envelope"])
        for item in fixture["trustBundles"]
    ]
    if [item["bundle"]["bundleVersion"] for item in accepted] != [1, 2]:
        _fail("TRUST_BUNDLE_CHAIN_INVALID", "Trust bundle rotation did not reach v2.")
    _expect_error(
        "TRUST_BUNDLE_ROLLBACK",
        lambda: cache.accept(
            payload_bytes=_fixture_payload(fixture["trustBundles"][0]),
            envelope=fixture["trustBundles"][0]["envelope"],
        ),
    )
    bundle = accepted[1]["bundle"]
    historical = verify_signed_artifact_envelope(
        payload_bytes=_fixture_payload(
            fixture["artifacts"]["historicalBeforeRevocation"]
        ),
        envelope=fixture["artifacts"]["historicalBeforeRevocation"]["envelope"],
        trust_bundle=bundle,
        expected_trust_bundle_hash=accepted[1]["hash"],
        minimum_trust_bundle_version=2,
    )
    rotated = verify_signed_artifact_envelope(
        payload_bytes=_fixture_payload(fixture["artifacts"]["rotatedKey"]),
        envelope=fixture["artifacts"]["rotatedKey"]["envelope"],
        trust_bundle=bundle,
    )
    revoked = fixture["artifacts"]["afterRevocation"]
    _expect_error(
        revoked["expectedCode"],
        lambda: verify_signed_artifact_envelope(
            payload_bytes=_fixture_payload(revoked),
            envelope=revoked["envelope"],
            trust_bundle=bundle,
        ),
    )
    human = fixture["humanApproval"]["record"]["human_signature"]
    validate_human_electronic_signature(
        human,
        expected_record_hash=fixture["humanApproval"]["statementHash"],
        approved_acr={"urn:accuratrials:prototype:high-assurance"},
        approved_amr={"otp"},
    )
    for item in (
        fixture["humanApproval"],
        fixture["verificationEvidence"],
        fixture["evidenceIndex"],
    ):
        verify_signed_artifact_envelope(
            payload_bytes=_fixture_payload(item),
            envelope=item["envelope"],
            trust_bundle=bundle,
        )
    wrong_eku = fixture["wrongEku"]
    _expect_error(
        wrong_eku["expectedCode"],
        lambda: verify_rfc3161_token(
            token_base64=wrong_eku["timestampTokenBase64"],
            expected_message_imprint=wrong_eku["messageImprint"],
            authority=wrong_eku["authority"],
        ),
    )
    negative_vector_count = _verify_fixture_negative_vectors(fixture, bundle)
    return {
        "schemaCount": 9,
        "signedEnvelopeCount": 8,
        "bundleVersions": [item["bundle"]["bundleVersion"] for item in accepted],
        "historicalKeyId": historical["kid"],
        "rotatedKeyId": rotated["kid"],
        "humanSubject": human["subject"],
        "negativeVectorCount": negative_vector_count,
        "wrongEkuRejected": True,
        "staleBundleRejected": True,
        "prototypePrivateKeysSerialized": False,
    }


__all__ = [name for name in globals() if not name.startswith("_")]
