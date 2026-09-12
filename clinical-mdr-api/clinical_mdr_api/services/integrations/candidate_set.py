"""Exact OsbCandidateRequestV1 intake and governed OsbCandidateSetV1 generation."""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import NAMESPACE_URL, uuid5

from fastapi.encoders import jsonable_encoder
from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
    hash_refs_equal,
    sha256_bytes,
)
from clinical_mdr_api.models.integrations.mapping_context import (
    MappingContextCandidateGroupRequest,
    MappingContextV2Request,
)
from clinical_mdr_api.services.integrations.mapping_context import MappingContextService
from clinical_mdr_api.services.integrations.native_study_head import read_current_study_head
from clinical_mdr_api.services.integrations.osb_candidate_request_versions import (
    ACCEPTED_REQUEST_CONTRACT_MINOR_VERSIONS,
    ACCEPTED_REQUEST_CONTRACT_VERSIONS,
    METADATA_REQUEST_CONTRACT_VERSIONS,
    SOURCE_CONTEXT_REQUEST_CONTRACT_VERSIONS,
)
from clinical_mdr_api.services.integrations.study_metadata_mapping import prepare_metadata_offers
from clinical_mdr_api.services.integrations.osb_family_map import (
    SUPPORTED_RESOURCE_FAMILIES,
    canonicalize_family,
)

CANDIDATE_REQUEST_MEDIA_TYPE = (
    "application/vnd.accuratrials.osb-candidate-request-v1+json"
)
CANDIDATE_SET_MEDIA_TYPE = (
    "application/vnd.accuratrials.osb-candidate-set-v1+json"
)

# The signed-envelope binding evidence was added in ExternalStudyIdentityV1
# 1.1. The identity coordinates and active native binding checks are unchanged.
ACCEPTED_EXTERNAL_IDENTITY_VERSIONS = ("1.0.0", "1.1.0")

# The six conservation dispositions, and how each tallies into census counts.
CENSUS_DISPOSITION_COUNT_KEYS = {
    "native": "native",
    "governed_extension": "governedExtension",
    "excluded_signed": "excludedSigned",
    "deferred_blocking": "deferredBlocking",
    "quarantined": "quarantined",
    "rejected": "rejected",
}
# Dispositions CSL's family router assigns to claims OSB cannot execute
# natively: census rows with target null and multiplicity.target == 0.
ROUTED_NON_NATIVE_DISPOSITIONS = frozenset({"governed_extension", "deferred_blocking"})


class OsbCandidateSetError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _record(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OsbCandidateSetError(code, "Expected an object.", 422)
    return value


def _list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise OsbCandidateSetError(code, "Expected an array.", 422)
    return value


def _iso(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError) as error:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TIME_INVALID", "Invalid request time.", 422) from error


def _artifact_ref(descriptor_fields: dict[str, Any]) -> dict[str, Any]:
    descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0", **descriptor_fields}
    return {
        "contractVersion": "ArtifactRefV1@1.0.0",
        **descriptor_fields,
        "descriptorHash": descriptor_hash(descriptor),
    }


def _same(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def _fact_key(value: dict[str, Any], id_field: str) -> str:
    fact_id = value.get(id_field)
    revision = value.get("revision")
    if not isinstance(fact_id, str) or not fact_id.strip() \
            or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_MEMBER_INVALID", "Invalid source member identity.", 422)
    return f"{fact_id}@{revision}"


def _request_contract_minor(payload: dict[str, Any]) -> str | None:
    """Return the supported declared version used to hash and sign the bytes."""
    declared = payload.get("contractVersion")
    if isinstance(declared, str) and declared in ACCEPTED_REQUEST_CONTRACT_VERSIONS:
        return declared.split("@", 1)[1]
    return None


def _assert_intent_additive_fields(intent: dict[str, Any]) -> None:
    """Loosely validate the additive intent fields CSL's parallel change may
    send: correct types when present, never a rejection of unknown keys."""
    operation = intent.get("nativeStudyOperation")
    if intent.get("resourceFamily") == "study_metadata":
        if not isinstance(operation, dict) or operation.get("contractVersion") != "OsbStudyMetadataPlanV1@1.0.0":
            raise OsbCandidateSetError("OSB_STUDY_METADATA_PLAN_REQUIRED", "Native metadata requires its source-pinned plan.", 422)
    elif operation is not None:
        raise OsbCandidateSetError("OSB_STUDY_METADATA_FAMILY_MISMATCH", "The metadata plan must use its native family.", 422)
    source = intent.get("source")
    if source is not None and not isinstance(source, dict):
        raise OsbCandidateSetError(
            "OSB_TYPED_SOURCE_INTENT_INVALID", "Intent source must be an object.", 422
        )
    if isinstance(source, dict) and source.get("classification") is not None \
            and not isinstance(source["classification"], dict):
        raise OsbCandidateSetError(
            "OSB_TYPED_SOURCE_INTENT_INVALID",
            "Intent source classification must be an object.",
            422,
        )
    for name in ("searchStringsOmitted", "searchCodesOmitted"):
        value = intent.get(name)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise OsbCandidateSetError(
                "OSB_TYPED_SOURCE_INTENT_INVALID",
                f"Intent {name} must be a non-negative integer.",
                422,
            )
    create_option = intent.get("createOption")
    if create_option is not None and not isinstance(create_option, dict):
        raise OsbCandidateSetError(
            "OSB_TYPED_SOURCE_INTENT_INVALID", "Intent createOption must be an object.", 422
        )
    if isinstance(create_option, dict):
        # requestedNativeType may now be null: 'no specific native type
        # requested'. It must never be coerced into a family search.
        requested = create_option.get("requestedNativeType")
        if requested is not None and not isinstance(requested, str):
            raise OsbCandidateSetError(
                "OSB_TYPED_SOURCE_INTENT_INVALID",
                "createOption.requestedNativeType must be a string or null.",
                422,
            )


def _assert_request_source_context(payload: dict[str, Any], intents: list[dict[str, Any]]) -> None:
    """Use the same version and shape rules before byte storage and generation."""
    for intent in intents:
        source = intent.get("source")
        if isinstance(source, dict) and "context" in source:
            if payload.get("contractVersion") not in SOURCE_CONTEXT_REQUEST_CONTRACT_VERSIONS:
                raise OsbCandidateSetError(
                    "OSB_SOURCE_CONTEXT_REQUEST_VERSION_REQUIRED",
                    "Source context requires request 1.3.0.",
                    422,
                )
            context = source["context"]
            if not isinstance(context, dict) or set(context) - {
                "encounters", "relationships", "semanticAssociations", "unresolvedRelationships",
            } or any(value is not None and not isinstance(value, list) for value in context.values()):
                raise OsbCandidateSetError(
                    "OSB_TYPED_SOURCE_CONTEXT_INVALID",
                    "Source context must contain only the retained array or null sections.",
                    422,
                )
        if payload.get("contractVersion") in SOURCE_CONTEXT_REQUEST_CONTRACT_VERSIONS \
                and (not isinstance(source, dict) or "evidence" not in intent):
            raise OsbCandidateSetError(
                "OSB_TYPED_SOURCE_INTENT_INVALID",
                "Current source intents must retain their source and evidence members.",
                422,
            )


def _assert_signed_request(
    payload: dict[str, Any], artifact: dict[str, Any], signed_envelope: dict[str, Any] | None,
    signature_verification: dict[str, Any] | None,
) -> None:
    envelope = _record(signed_envelope, "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED")
    verification = _record(signature_verification, "OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_REQUIRED")
    descriptor = {
        "contractVersion": "ArtifactDescriptorV1@1.0.0",
        **{key: value for key, value in artifact.items() if key not in {"contractVersion", "descriptorHash"}},
    }
    statement = _record(envelope.get("signingStatement"), "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")
    if envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0" \
            or not _same(envelope.get("artifactDescriptor"), descriptor) \
            or not _same(envelope.get("payloadHash"), artifact.get("payloadHash")) \
            or statement.get("signingPurpose") != "osb-candidate-request" \
            or statement.get("producerService") != "csl.attestation" \
            or statement.get("payloadContract") != "accuratrials.osb.OsbCandidateRequestV1" \
            or statement.get("payloadContractVersion") != _request_contract_minor(payload):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID", "Signed request envelope differs.", 422)
    envelope_hash = canonical_json_hash_ref(
        envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"
    )
    if verification.get("verified") is not True \
            or not _same(verification.get("payloadHash"), artifact.get("payloadHash")) \
            or not _same(verification.get("envelopeHash"), envelope_hash) \
            or not isinstance(verification.get("signerKeyId"), str) \
            or not isinstance(verification.get("trustedTime"), str):
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_UNVERIFIED",
            "Candidate request does not have trusted signature verification.",
            422,
        )


def _assert_request_projection(payload: dict[str, Any], artifact: dict[str, Any]) -> None:
    source = _record(payload.get("sourceFactPackage"), "OSB_CANDIDATE_REQUEST_SOURCE_REQUIRED")
    snapshot = _record(payload.get("semanticSnapshot"), "OSB_CANDIDATE_REQUEST_SNAPSHOT_REQUIRED")
    identity = _record(payload.get("osbStudyIdentity"), "OSB_CANDIDATE_REQUEST_IDENTITY_REQUIRED")
    checkpoint = _record(payload.get("checkpointPreconditions"), "OSB_CANDIDATE_REQUEST_CHECKPOINT_REQUIRED")
    if identity.get("contractVersion") not in ACCEPTED_EXTERNAL_IDENTITY_VERSIONS \
            or identity.get("system") != "osb" \
            or identity.get("namespace") != "accuratrials-osb" \
            or identity.get("objectType") != "study-draft-root" \
            or identity.get("tenantId") != payload.get("tenantId") \
            or identity.get("platformStudyId") != payload.get("platformStudyId") \
            or identity.get("verificationStatus") != "verified" \
            or not all(isinstance(identity.get(name), str) and identity[name].strip()
                       for name in ("bindingId", "nativeIdentity", "nativeVersion")):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_IDENTITY_INVALID", "OSB identity binding is invalid.", 422)
    if not _same(checkpoint.get("semanticSnapshotHash"), snapshot.get("payloadHash")) \
            or checkpoint.get("osbNativeVersion") != identity.get("nativeVersion"):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CHECKPOINT_MISMATCH", "Request checkpoint differs.", 422)

    members = [_record(item, "OSB_CANDIDATE_REQUEST_MEMBER_INVALID") for item in
               _list(payload.get("activeClaimRevisions"), "OSB_CANDIDATE_REQUEST_MEMBERS_REQUIRED")]
    intents = [_record(item, "OSB_TYPED_SOURCE_INTENT_INVALID") for item in
               _list(payload.get("typedSourceIntents"), "OSB_TYPED_SOURCE_INTENTS_REQUIRED")]
    if any(item.get("resourceFamily") == "study_metadata" for item in intents) \
            and payload.get("contractVersion") not in METADATA_REQUEST_CONTRACT_VERSIONS:
        raise OsbCandidateSetError("OSB_STUDY_METADATA_REQUEST_VERSION_REQUIRED", "Native metadata requires request 1.2.0 or 1.3.0.", 422)
    _assert_request_source_context(payload, intents)
    member_keys = [_fact_key(member, "sourceFactId") for member in members]
    intent_keys = [_fact_key(intent, "factId") for intent in intents]
    if len(set(member_keys)) != len(member_keys) or len(set(intent_keys)) != len(intent_keys):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_DUPLICATE_MEMBER", "Duplicate candidate request member.", 422)
    # Members stay sorted and complete; intents are the NATIVE SUBSET of the
    # members (CSL's family router deliberately routes the rest to
    # governed_extension / deferred_blocking). The exact subset is checked
    # against the census dispositions below.
    #
    # SORTEDNESS IS CHECKED UNDER THE PRODUCER'S OWN RULE — codepoint order of
    # (factId, revision) as a TUPLE, which is what CSL's snapshot builder
    # states. This used to sort the concatenated "factId@revision" STRING,
    # a different relation whenever one fact id is a prefix of another:
    # '-' < '@', so 'act-vitals-phrase@1' < 'act-vitals@1' while the tuple
    # rule puts 'act-vitals' first. Every id IL emits is fixed-length, which
    # is the only reason the divergence never fired on a real protocol; the
    # first synthetic package with prefix-related ids refused here with
    # OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH against a correctly-built request.
    member_tuples = [(str(member.get("sourceFactId")), int(member.get("revision"))) for member in members]
    if member_tuples != sorted(member_tuples) or not set(intent_keys) <= set(member_keys):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH", "Candidate work order differs from snapshot.", 422)
    expected_member_hash = canonical_json_hash_ref(
        members, schema_version="SemanticSnapshotMemberSetV1@1.0.0"
    )
    if not _same(expected_member_hash, snapshot.get("memberSetHash")):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_MEMBER_HASH_MISMATCH", "Snapshot member hash differs.", 422)

    families: list[str] = []
    for intent in intents:
        family = canonicalize_family(str(intent.get("resourceFamily") or ""))
        if family not in SUPPORTED_RESOURCE_FAMILIES:
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_FAMILY_UNSUPPORTED", "Unsupported OSB resource family.", 422)
        if intent.get("targetKey") != "primary" or any(
            key in intent for key in ("selectedCandidate", "nativeIdentity", "nativeVersion", "candidateId")
        ):
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TARGET_SELECTION_FORBIDDEN", "CSL may not select an OSB target.", 422)
        _assert_intent_additive_fields(intent)
        families.append(family)
    expected_families = sorted(set(families))
    requested_families = sorted({
        canonicalize_family(str(family))
        for family in _list(payload.get("requestedObjectFamilies"), "OSB_CANDIDATE_REQUEST_FAMILY_MISMATCH")
    })
    if requested_families != expected_families:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_FAMILY_MISMATCH", "Requested families differ from intents.", 422)

    ruleset = _record(payload.get("projectionRuleset"), "OSB_CANDIDATE_REQUEST_RULESET_REQUIRED")
    expected_ruleset_hash = canonical_json_hash_ref(
        {"mapping": "fail-closed-family-router", "version": "1.0.0"},
        schema_version="ProjectionRulesetV1@1.0.0",
    )
    if ruleset.get("id") != "csl-to-osb-candidate-request" or ruleset.get("version") != "1.0.0" \
            or not _same(ruleset.get("hash"), expected_ruleset_hash):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_RULESET_UNSUPPORTED", "Projection ruleset is unsupported.", 422)

    request_id = payload.get("requestId")
    snapshot_version_id = snapshot.get("snapshotVersionId")
    if not isinstance(request_id, str) or not request_id.strip() \
            or not isinstance(snapshot_version_id, str) or not snapshot_version_id.strip():
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Candidate request census identity is missing.", 422)
    # Expectations are derived from the census ROWS themselves, not from an
    # assumed all-native shape: CSL's family router legitimately emits
    # governed_extension / deferred_blocking rows (target null,
    # multiplicity.target == 0, routing reason codes in evidenceRefs).
    census = _record(payload.get("inputConservation"), "OSB_CANDIDATE_REQUEST_CENSUS_REQUIRED")
    rows = [_record(row, "OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH") for row in
            _list(census.get("rows"), "OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH")]
    expected_census_hash = canonical_json_hash_ref(
        rows, schema_version="ConservationCensusRowsV1@1.0.0"
    )
    if census.get("contractVersion") != "ConservationCensusV1@1.0.0" \
            or not _same(census.get("rowSetHash"), expected_census_hash):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Candidate request census differs.", 422)
    member_key_set = set(member_keys)
    tally = {"rows": len(rows), "native": 0, "governedExtension": 0, "excludedSigned": 0,
             "deferredBlocking": 0, "quarantined": 0, "rejected": 0}
    disposition_by_key: dict[str, str] = {}
    for row in rows:
        disposition = str(row.get("disposition") or "")
        count_key = CENSUS_DISPOSITION_COUNT_KEYS.get(disposition)
        if count_key is None:
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Census disposition is unsupported.", 422)
        tally[count_key] += 1
        unit_id = row.get("unitId")
        row_key = unit_id.rsplit(":", 1)[0] if isinstance(unit_id, str) and ":" in unit_id else None
        if row_key is None or row_key in disposition_by_key or row_key not in member_key_set:
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Census row does not name a unique snapshot member.", 422)
        disposition_by_key[row_key] = disposition
        target = row.get("target")
        multiplicity = row.get("multiplicity") if isinstance(row.get("multiplicity"), dict) else {}
        if disposition == "native" and not isinstance(target, dict):
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Native census row must name a target.", 422)
        if disposition in ROUTED_NON_NATIVE_DISPOSITIONS \
                and (target is not None or multiplicity.get("target") != 0):
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Routed census row must carry no target.", 422)
    if census.get("counts") != tally:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Candidate request census differs.", 422)
    if len(disposition_by_key) != len(member_keys):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Census must cover every snapshot member.", 422)
    # The typed source intents must be exactly the native-disposition members,
    # in the same (sorted) order.
    native_member_keys = [key for key in member_keys if disposition_by_key[key] == "native"]
    if intent_keys != native_member_keys:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_MEMBER_MISMATCH", "Candidate work order differs from snapshot.", 422)
    upstream = census.get("upstreamExclusions")
    if upstream is not None:
        # Package-boundary exclusions referenced by hash — informational, NOT
        # rows in this census. Loose type validation only. The hash is the
        # STRING form ("sha256:<hex>") or null when the snapshot predates the
        # package census hash — null still carries the counts.
        upstream = _record(upstream, "OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH")
        upstream_hash = upstream.get("sourcePackageCensusHash")
        hash_ok = upstream_hash is None or (
            isinstance(upstream_hash, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", upstream_hash)
        )
        if not hash_ok or any(
            not isinstance(upstream.get(name), int) or isinstance(upstream.get(name), bool)
            or upstream.get(name) < 0
            for name in ("excludedSigned", "quarantined")
        ):
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_CENSUS_MISMATCH", "Upstream exclusion summary is invalid.", 422)

    evidence = _list(payload.get("evidenceArtifactRefs"), "OSB_CANDIDATE_REQUEST_EVIDENCE_REQUIRED")
    if len(evidence) != 1:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_EVIDENCE_MISMATCH", "Exactly one source artifact is required.", 422)
    source_artifact = _record(evidence[0], "OSB_CANDIDATE_REQUEST_EVIDENCE_MISMATCH")
    if source_artifact.get("artifactVersionId") != source.get("packageVersionId") \
            or not _same(source_artifact.get("payloadHash"), source.get("payloadHash")) \
            or source_artifact.get("tenantId") != payload.get("tenantId"):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_EVIDENCE_MISMATCH", "Source artifact reference differs.", 422)


def verify_candidate_request_artifact(
    payload: dict[str, Any],
    artifact: dict[str, Any],
    tenant_id: str,
    platform_study_id: str,
    signed_envelope: dict[str, Any] | None = None,
    signature_verification: dict[str, Any] | None = None,
) -> None:
    if (
        artifact.get("contractVersion") != "ArtifactRefV1@1.0.0"
        or artifact.get("kind") != "osb-candidate-request"
        or artifact.get("tenantId") != tenant_id
        or artifact.get("payloadContract") != "accuratrials.osb.OsbCandidateRequestV1"
        or artifact.get("payloadContractVersion") not in ACCEPTED_REQUEST_CONTRACT_MINOR_VERSIONS
        or artifact.get("producerService") != "csl.attestation"
        or artifact.get("purpose") != "osb-candidate-generation"
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_ARTIFACT_INVALID", "Candidate request artifact metadata differs.", 422)
    contract_minor = _request_contract_minor(payload)
    if (
        contract_minor is None
        or contract_minor != artifact.get("payloadContractVersion")
        or payload.get("tenantId") != tenant_id
        or payload.get("platformStudyId") != platform_study_id
        or payload.get("requestVersionId") != artifact.get("artifactVersionId")
        or payload.get("requestId") != artifact.get("artifactId")
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_SCOPE_INVALID", "Candidate request scope or identity differs.", 422)
    actual_payload_hash = canonical_json_hash_ref(
        payload,
        schema_version=str(payload["contractVersion"]),
        media_type=CANDIDATE_REQUEST_MEDIA_TYPE,
    )
    if not hash_refs_equal(actual_payload_hash, artifact.get("payloadHash")):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_HASH_MISMATCH", "Candidate request hash differs.", 422)
    fields = {
        key: value
        for key, value in artifact.items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    expected_descriptor = descriptor_hash(
        {"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}
    )
    if not hash_refs_equal(expected_descriptor, artifact.get("descriptorHash")):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_DESCRIPTOR_MISMATCH", "Descriptor hash differs.", 422)
    payload_bytes = canonical_json(payload).encode("utf-8")
    if artifact.get("byteSize") != len(payload_bytes) \
            or artifact.get("stableLocator") != f'artifact://csl/osb-candidate-request/{payload.get("requestVersionId")}':
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_BYTES_MISMATCH", "Candidate request byte descriptor differs.", 422)
    created_at = _iso(payload.get("createdAt"))
    expires_at = _iso(payload.get("expiresAt"))
    if expires_at <= datetime.now(UTC):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_EXPIRED", "Candidate request expired.")
    if expires_at <= created_at or (expires_at - created_at).total_seconds() > 3600:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TIME_INVALID", "Candidate request time window is invalid.", 422)
    _assert_request_projection(payload, artifact)
    _assert_signed_request(payload, artifact, signed_envelope, signature_verification)


def assert_candidate_request_transfer_envelope(
    payload: dict[str, Any], expected_hash: str, signed_envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    envelope = _record(signed_envelope, "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED")
    descriptor = _record(envelope.get("artifactDescriptor"), "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")
    statement = _record(envelope.get("signingStatement"), "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")
    payload_hash = _record(descriptor.get("payloadHash"), "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")
    if (
        envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0"
        or envelope.get("signatureProfile") != "jws-detached-rfc7797/1.0"
        or descriptor.get("kind") != "osb-candidate-request"
        or descriptor.get("payloadContract") != "accuratrials.osb.OsbCandidateRequestV1"
        or descriptor.get("payloadContractVersion") != _request_contract_minor(payload)
        or descriptor.get("producerService") != "csl.attestation"
        or descriptor.get("purpose") != "osb-candidate-generation"
        or descriptor.get("artifactId") != payload.get("requestId")
        or descriptor.get("artifactVersionId") != payload.get("requestVersionId")
        or descriptor.get("tenantId") != payload.get("tenantId")
        or payload_hash.get("value") != expected_hash
        or not _same(envelope.get("payloadHash"), descriptor.get("payloadHash"))
        or statement.get("signingPurpose") != "osb-candidate-request"
        or statement.get("producerService") != "csl.attestation"
        or statement.get("payloadContract") != "accuratrials.osb.OsbCandidateRequestV1"
        or not _same(statement.get("payloadHash"), descriptor.get("payloadHash"))
    ):
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID",
            "Transferred request is not bound by a matching signed envelope.",
            422,
        )
    return envelope


def decode_signed_artifact_envelope_header(value: str | None) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED",
            "Signed candidate-request envelope is required on transfer.",
            422,
        )
    try:
        decoded = json.loads(base64.b64decode(value, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID",
            "Signed envelope header is not valid base64 JSON.",
            422,
        ) from error
    return _record(decoded, "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID")


def require_exactly_one_active_osb_binding(rows: list[Any]) -> dict[str, str]:
    if len(rows) != 1:
        raise OsbCandidateSetError("OSB_NATIVE_IDENTITY_BINDING_REQUIRED", "Exactly one active OSB binding is required.")
    return {
        "bindingId": str(rows[0][0]),
        "nativeIdentity": str(rows[0][1]),
        "nativeVersion": str(rows[0][2]),
    }


def store_candidate_request_bytes(
    *, tenant_id: str, platform_study_id: str, bytes_value: bytes, expected_hash: str,
    signed_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if sha256_bytes(bytes_value) != expected_hash:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TRANSFER_HASH_MISMATCH", "Transferred bytes differ.", 422)
    try:
        payload = json.loads(
            bytes_value.decode("utf-8"),
            object_pairs_hook=lambda pairs: _unique_object(pairs),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_JSON_INVALID", "Candidate request is not valid UTF-8 JSON.", 422) from error
    if not isinstance(payload, dict) or canonical_json(payload).encode("utf-8") != bytes_value:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_NONCANONICAL", "Candidate request bytes are not canonical.", 422)
    if (
        payload.get("contractVersion") not in ACCEPTED_REQUEST_CONTRACT_VERSIONS
        or payload.get("tenantId") != tenant_id
        or payload.get("platformStudyId") != platform_study_id
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TRANSFER_SCOPE_MISMATCH", "Transferred request scope differs.", 422)
    envelope = assert_candidate_request_transfer_envelope(payload, expected_hash, signed_envelope)
    _assert_request_source_context(payload, [
        _record(item, "OSB_TYPED_SOURCE_INTENT_INVALID")
        for item in _list(payload.get("typedSourceIntents"), "OSB_TYPED_SOURCE_INTENTS_REQUIRED")
    ])
    envelope_json = canonical_json(envelope)
    rows, _ = db.cypher_query(
        """MERGE (artifact:OsbInboundArtifact {tenant_id: $tenant_id, payload_hash: $payload_hash})
           ON CREATE SET artifact.platform_study_id=$platform_study_id,
             artifact.artifact_version_id=$artifact_version_id,
             artifact.kind='osb-candidate-request', artifact.payload_json=$payload_json,
             artifact.byte_size=$byte_size, artifact.signed_envelope_json=$envelope_json,
             artifact.created_at=datetime()
           ON MATCH SET artifact.signed_envelope_json = CASE
             WHEN artifact.signed_envelope_json IS NULL THEN $envelope_json
             ELSE artifact.signed_envelope_json
           END
           RETURN artifact.platform_study_id,artifact.artifact_version_id,
                  artifact.payload_json,artifact.byte_size,artifact.signed_envelope_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "payload_hash": expected_hash,
            "artifact_version_id": payload["requestVersionId"],
            "payload_json": canonical_json(payload),
            "byte_size": len(bytes_value),
            "envelope_json": envelope_json,
        },
    )
    if not rows:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TRANSFER_FAILED", "Candidate request was not stored.", 500)
    row = rows[0]
    if (
        str(row[0]) != platform_study_id
        or str(row[1]) != payload["requestVersionId"]
        or str(row[2]) != canonical_json(payload)
        or int(row[3]) != len(bytes_value)
        or str(row[4]) != envelope_json
    ):
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_TRANSFER_CONFLICT", "Content hash already names different bytes.")
    return {
        "contractVersion": "ArtifactTransferReceiptV1@prototype",
        "kind": "osb-candidate-request",
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "contentHash": expected_hash,
        "byteSize": len(bytes_value),
        "signedEnvelopeBound": True,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_DUPLICATE_KEY", f"Duplicate JSON key {key}.", 422)
        result[key] = value
    return result


def load_candidate_request(payload_hash: str, tenant_id: str, platform_study_id: str) -> dict[str, Any]:
    rows, _ = db.cypher_query(
        """MATCH (artifact:OsbInboundArtifact {tenant_id: $tenant_id,
             platform_study_id: $platform_study_id, payload_hash: $payload_hash,
             kind: 'osb-candidate-request'})
           RETURN artifact.payload_json""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id, "payload_hash": payload_hash},
    )
    if not rows:
        raise OsbCandidateSetError("OSB_CANDIDATE_REQUEST_NOT_TRANSFERRED", "Exact candidate request bytes are unavailable.", 404)
    return _record(json.loads(str(rows[0][0])), "OSB_CANDIDATE_REQUEST_INVALID")


def active_osb_binding(tenant_id: str, platform_study_id: str) -> dict[str, str]:
    rows, _ = db.cypher_query(
        """MATCH (binding:PlatformNativeStudyBinding {tenant_id: $tenant_id,
             platform_study_id: $platform_study_id, namespace: 'accuratrials-osb',
             object_type: 'study-draft-root', status: 'active'})
           RETURN binding.binding_id,binding.native_study_id,binding.native_version""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id},
    )
    return require_exactly_one_active_osb_binding(rows)


def _assert_identity_binding(
    identity: dict[str, Any], binding: dict[str, str], *,
    tenant_id: str, platform_study_id: str, error_code: str,
) -> None:
    """Resolve a platform binding through OSB's own published receipt.

    The platform and native binding UUIDs belong to different registries.
    Version 1.1 carries the hashes of the exact receipt and envelope joining
    them. The signed target state also commits to the native binding UUID.
    """
    def reject() -> NoReturn:
        raise OsbCandidateSetError(error_code, "OSB binding evidence or native checkpoint changed.")

    if identity.get("nativeIdentity") != binding["nativeIdentity"] \
            or str(identity.get("nativeVersion") or "") != binding["nativeVersion"]:
        reject()
    if identity.get("contractVersion") == "1.0.0":
        if identity.get("bindingId") != binding["bindingId"]:
            reject()
        return
    if identity.get("contractVersion") != "1.1.0" \
            or identity.get("tenantId") != tenant_id \
            or identity.get("platformStudyId") != platform_study_id \
            or identity.get("system") != "osb" \
            or identity.get("namespace") != "accuratrials-osb" \
            or identity.get("objectType") != "study-draft-root" \
            or identity.get("verificationStatus") != "verified" \
            or identity.get("validTo") is not None:
        reject()
    evidence = identity.get("evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("receiptId"), str):
        reject()
    rows, _ = db.cypher_query(
        """MATCH (audit:PlatformNativeIdentityAudit {
             tenant_id: $tenant_id, platform_study_id: $platform_study_id,
             receipt_id: $receipt_id, action: 'PLATFORM_NATIVE_IDENTITY_PUBLISHED'})
             -[:AUDITS_EFFECT]->(effect:PlatformNativeIdentityEffect {
               tenant_id: $tenant_id, platform_study_id: $platform_study_id,
               namespace: 'accuratrials-osb', object_type: 'study-draft-root'})
           RETURN effect.receipt_json,effect.signed_envelope_json""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
         "receipt_id": evidence["receiptId"]},
    )
    if len(rows) != 1:
        reject()
    try:
        receipt = json.loads(str(rows[0][0]))
        envelope = json.loads(str(rows[0][1]))
    except (ValueError, TypeError, IndexError):
        reject()
    if not isinstance(receipt, dict) or not isinstance(envelope, dict):
        reject()
    expected_receipt = {
        "contractVersion": "1.0.0", "receiptId": evidence["receiptId"],
        "tenantId": tenant_id, "platformStudyId": platform_study_id,
        "targetSystem": "osb", "namespace": "accuratrials-osb",
        "objectType": "study-draft-root", "nativeIdentity": binding["nativeIdentity"],
        "nativeVersion": binding["nativeVersion"],
    }
    if any(receipt.get(key) != value for key, value in expected_receipt.items()) \
            or not hash_refs_equal(evidence.get("receiptPayloadHash"), canonical_json_hash_ref(
                receipt, schema_version="NativeIdentityBindingReceiptV1@1.0.0",
            )) \
            or not hash_refs_equal(evidence.get("signedEnvelopeHash"), canonical_json_hash_ref(
                envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0",
            )):
        reject()
    try:
        head = read_current_study_head(binding["nativeIdentity"], query=db.cypher_query)
    except ValueError:
        reject()
    if head is None:
        reject()
    native_status = head["nativeStatus"].lower()
    native_version = head["nativeVersion"]
    target_state = {
        "nativeIdentity": binding["nativeIdentity"], "nativeVersion": native_version,
        "status": native_status, "domainBindingId": binding["bindingId"],
    }
    if native_version != binding["nativeVersion"] or not hash_refs_equal(
        receipt.get("targetStateHash"),
        canonical_json_hash_ref(target_state, schema_version="OSBNativeStudyRootStateV1@1.0.0"),
    ):
        reject()


def _census_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "rows": len(rows), "native": 0, "governedExtension": 0, "excludedSigned": 0,
        "deferredBlocking": 0, "quarantined": 0, "rejected": 0,
    }
    for row in rows:
        key = CENSUS_DISPOSITION_COUNT_KEYS.get(str(row.get("disposition") or ""))
        if key is None:
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_CENSUS_MISMATCH", "Census disposition is unsupported.", 422)
        counts[key] += 1
    return counts


def _routed_census_row(
    *, unit_id: str, request_id: str, member_index: int, member_hash: Any,
    disposition: str, evidence_refs: list[str], index: int,
) -> dict[str, Any]:
    """A candidate-set census row for a member CSL routed away from native
    execution: the disposition and routing reason survive, no target exists."""
    return {
        "unitId": unit_id,
        "source": {
            "artifactId": request_id, "contract": "accuratrials.osb.OsbCandidateRequestV1",
            "type": "active-claim-revision", "path": f"#/activeClaimRevisions/{member_index}",
            "valueHash": member_hash,
        },
        "target": None,
        "multiplicity": {"source": 1, "target": 0},
        "splitMergeGroup": None, "splitMergeRule": None,
        "ordering": {"significant": False, "sourceIndex": index, "targetIndex": None},
        "disposition": disposition, "exclusionPolicy": None,
        "evidenceRefs": evidence_refs, "receiptRefs": [],
    }


def _census_row(
    *, unit_id: str, source_artifact_id: str, source_contract: str, source_type: str,
    source_path: str, source_hash: dict[str, Any], target_artifact_id: str,
    target_path: str, target_hash: dict[str, Any], index: int, disposition: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    return {
        "unitId": unit_id,
        "source": {
            "artifactId": source_artifact_id, "contract": source_contract, "type": source_type,
            "path": source_path, "valueHash": source_hash,
        },
        "target": {
            "artifactId": target_artifact_id, "contract": "accuratrials.osb.OsbCandidateSetV1",
            "type": "OsbCandidateRecordV1", "path": target_path, "valueHash": target_hash,
        },
        "multiplicity": {"source": 1, "target": 1},
        "splitMergeGroup": None, "splitMergeRule": None,
        "ordering": {"significant": True, "sourceIndex": index, "targetIndex": index},
        "disposition": disposition, "exclusionPolicy": None,
        "evidenceRefs": evidence_refs, "receiptRefs": [],
    }


def candidate_assignment_identity(
    *, tenant_id: str, platform_study_id: str, candidate_set_version_id: str,
) -> dict[str, str]:
    assignment_id = str(uuid5(
        NAMESPACE_URL,
        f"accuratrials:cc-candidate-assignment:v1:{tenant_id}:{platform_study_id}:{candidate_set_version_id}",
    ))
    return {
        "contractVersion": "CandidateAssignmentProjectionV1@1.0.0",
        "assignmentId": assignment_id,
        "kind": "mapping-adjudication",
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "candidateSetVersionId": candidate_set_version_id,
    }


def bind_candidate_set_envelope(artifact_ref: dict[str, Any]) -> dict[str, Any]:
    descriptor = {
        "contractVersion": "ArtifactDescriptorV1@1.0.0",
        **{key: value for key, value in artifact_ref.items() if key not in {"contractVersion", "descriptorHash"}},
    }
    if not hash_refs_equal(descriptor.get("payloadHash"), artifact_ref.get("payloadHash")):
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_SIGNATURE_INVALID", "Envelope payload hash differs.", 422)
    return {
        "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
        "artifactDescriptor": descriptor,
        "payloadHash": artifact_ref["payloadHash"],
        "signingStatement": {
            "signingPurpose": "osb-candidate-set",
            "producerService": "osb.package",
            "payloadContract": "accuratrials.osb.OsbCandidateSetV1",
            "payloadContractVersion": "1.0.0",
        },
    }


def _assert_candidate_native_checkpoint_current(
    payload: dict[str, Any], *, binding: dict[str, str], now: datetime | None = None,
) -> dict[str, Any]:
    identity = _record(payload.get("osbStudyIdentity"), "OSB_CANDIDATE_SET_IDENTITY_REQUIRED")
    checkpoint = _record(payload.get("capabilityCheckpoint"), "OSB_CANDIDATE_CHECKPOINT_REQUIRED")
    clock = now or datetime.now(UTC)
    if identity.get("contractVersion") == "1.1.0":
        _assert_identity_binding(
            identity, binding, tenant_id=str(payload.get("tenantId")),
            platform_study_id=str(payload.get("platformStudyId")), error_code="OSB_CANDIDATE_SET_STALE",
        )
    elif identity.get("nativeIdentity") != binding["nativeIdentity"] \
            or str(identity.get("nativeVersion") or "") != binding["nativeVersion"] \
            or identity.get("bindingId") != binding["bindingId"]:
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_STALE", "OSB binding or native version changed.")
    if str(checkpoint.get("nativeVersion") or "") != binding["nativeVersion"]:
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_STALE", "Capability or native checkpoint changed.")
    if _iso(payload.get("expiresAt")) <= clock:
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_EXPIRED", "Candidate set expired.")
    return checkpoint


def assert_candidate_set_current(
    payload: dict[str, Any], *, binding: dict[str, str], osb_openapi_hash: str, now: datetime | None = None,
) -> None:
    checkpoint = _assert_candidate_native_checkpoint_current(payload, binding=binding, now=now)
    if str(checkpoint.get("osbOpenApiHash") or "") != osb_openapi_hash:
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_STALE", "Capability or native checkpoint changed.")


def read_candidate_set_for_publication(
    *, request_payload: dict[str, Any], request_artifact: dict[str, Any],
    candidate_artifact: dict[str, Any], tenant_id: str, platform_study_id: str,
    osb_openapi_hash: str, signed_envelope: dict[str, Any],
    signature_verification: dict[str, Any],
) -> dict[str, Any]:
    """Read-only preparation/revalidation for a new signed publication receipt."""
    verify_candidate_request_artifact(
        request_payload, request_artifact, tenant_id, platform_study_id,
        signed_envelope, signature_verification,
    )
    version_id = candidate_artifact.get("artifactVersionId")
    rows, _ = db.cypher_query(
        """MATCH (candidate:OsbCandidateSetV1 {tenant_id: $tenant_id,
             platform_study_id: $platform_study_id,
             candidate_set_version_id: $candidate_set_version_id})
           RETURN candidate.payload_json,candidate.payload_hash,candidate.artifact_ref_json,
                  candidate.candidate_set_id,candidate.candidate_set_version_id,
                  candidate.native_study_id,candidate.native_version,candidate.signed_envelope_json""",
        {"tenant_id": tenant_id, "platform_study_id": platform_study_id,
         "candidate_set_version_id": version_id},
    )
    if len(rows) != 1:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_PUBLICATION_TARGET_REQUIRED", "One exact retained candidate version is required.", 409,
        )
    row = rows[0]
    payload = _record(json.loads(str(row[0])), "OSB_CANDIDATE_SET_STORED_INVALID")
    artifact = _record(json.loads(str(row[2])), "OSB_CANDIDATE_SET_STORED_INVALID")
    payload_hash = canonical_json_hash_ref(
        payload, schema_version="OsbCandidateSetV1@1.0.0", media_type=CANDIDATE_SET_MEDIA_TYPE,
    )
    retained_descriptor_hash = descriptor_hash({
        "contractVersion": "ArtifactDescriptorV1@1.0.0",
        **{key: value for key, value in artifact.items() if key not in {"contractVersion", "descriptorHash"}},
    })
    if payload_hash["value"] != str(row[1]) or not hash_refs_equal(payload_hash, artifact.get("payloadHash")) \
            or not hash_refs_equal(retained_descriptor_hash, artifact.get("descriptorHash")) \
            or artifact.get("kind") != "osb-candidate-set" \
            or artifact.get("producerService") != "osb.package" \
            or canonical_json(payload) != str(row[0]) \
            or len(str(row[0]).encode("utf-8")) != artifact.get("byteSize") \
            or artifact != candidate_artifact:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_PUBLICATION_ARTIFACT_MISMATCH", "Retained bytes or descriptor differ from the named candidate artifact.", 409,
        )
    if payload.get("tenantId") != tenant_id or payload.get("platformStudyId") != platform_study_id \
            or payload.get("candidateSetVersionId") != str(row[4]) or str(row[4]) != version_id \
            or payload.get("candidateSetId") != str(row[3]) \
            or payload.get("assignment") != candidate_assignment_identity(
                tenant_id=tenant_id, platform_study_id=platform_study_id, candidate_set_version_id=str(row[4]),
            ) \
            or payload.get("osbStudyIdentity") != request_payload.get("osbStudyIdentity") \
            or payload.get("request") != {
                "requestVersionId": request_payload.get("requestVersionId"),
                "payloadHash": request_artifact.get("payloadHash"),
            }:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_PUBLICATION_SOURCE_MISMATCH", "Candidate identity or source request differs.", 409,
        )
    binding = active_osb_binding(tenant_id, platform_study_id)
    assert_candidate_set_current(payload, binding=binding, osb_openapi_hash=osb_openapi_hash)
    if str(row[5]) != binding["nativeIdentity"] or str(row[6]) != binding["nativeVersion"]:
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_STALE", "Retained native checkpoint differs.")
    return {
        "payload": payload, "payloadHash": payload_hash, "artifactRef": artifact,
        "candidateSetId": str(row[3]), "candidateSetVersionId": str(row[4]),
        "nativeIdentity": str(row[5]), "nativeVersion": str(row[6]),
        "assignment": payload.get("assignment"),
        "signedEnvelope": json.loads(str(row[7])) if row[7] else bind_candidate_set_envelope(artifact),
    }


def _assert_readable_or_create(record: dict[str, Any]) -> str:
    native_candidates = _list(record.get("nativeCandidates"), "OSB_NATIVE_CANDIDATES_REQUIRED")
    create_option = record.get("createOption")
    create_allowed = isinstance(create_option, dict) and create_option.get("allowed") is True
    if native_candidates:
        for candidate in native_candidates:
            offered = _record(candidate, "OSB_NATIVE_CANDIDATE_INVALID")
            if not offered.get("uid") or not offered.get("version") or not offered.get("resourceType"):
                raise OsbCandidateSetError("OSB_CANDIDATE_SET_TARGET_UNREADABLE", "Native candidate identity is incomplete.", 422)
        return "native"
    # AN EMPTY RESULT ONLY MEANS "NO NATIVE OBJECT EXISTS" IF A SEARCH RAN.
    #
    # `governed_extension` is OSB asserting, of its own authority, that it holds
    # nothing for this concept and the platform may create one. That assertion
    # is only earned by a completed retrieval. When the group carries blockers -
    # a missing CT package, an unavailable family, an incomplete candidate
    # identity - the retrieval did not conclude, and booking the fact as a
    # governed extension launders an unanswered question into a decision.
    # Measured before this: NCT03167411's 415 intents were ALL booked
    # `governed_extension` against a study with no StudyStandardVersion, i.e.
    # against searches that never executed. `deferred_blocking` is the honest
    # disposition - the fact is conserved, named, and still awaiting an answer -
    # and it is already a disposition this census carries and the request's own
    # router uses for exactly this meaning.
    if record.get("blockers"):
        return "deferred_blocking"
    if create_allowed:
        return "native" if record.get("resourceFamily") == "study_metadata" else "governed_extension"
    raise OsbCandidateSetError(
        "OSB_CANDIDATE_SET_TARGET_UNREADABLE",
        "Candidate must name a readable native target or an explicit create request.",
        422,
    )


def generate_candidate_set(
    *, request_payload: dict[str, Any], artifact: dict[str, Any], tenant_id: str,
    platform_study_id: str, osb_openapi_hash: str, actor: str,
    signed_envelope: dict[str, Any] | None = None,
    signature_verification: dict[str, Any] | None = None,
    mapping_context_service: Any | None = None,
    artifact_signer: Any | None = None,
    supersedes_candidate_set_version_id: str | None = None,
) -> dict[str, Any]:
    verify_candidate_request_artifact(
        request_payload, artifact, tenant_id, platform_study_id,
        signed_envelope, signature_verification,
    )
    binding = active_osb_binding(tenant_id, platform_study_id)
    expected_identity = _record(request_payload.get("osbStudyIdentity"), "OSB_CANDIDATE_REQUEST_IDENTITY_REQUIRED")
    _assert_identity_binding(
        expected_identity, binding, tenant_id=tenant_id, platform_study_id=platform_study_id,
        error_code="OSB_CANDIDATE_REQUEST_NATIVE_PRECONDITION_FAILED",
    )
    if supersedes_candidate_set_version_id is not None and (
        not isinstance(supersedes_candidate_set_version_id, str)
        or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            supersedes_candidate_set_version_id,
        )
    ):
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REFRESH_PRECONDITION_INVALID",
            "An exact prior candidate-set version UUID is required for refresh.", 422,
        )
    request_hash = _record(artifact.get("payloadHash"), "OSB_CANDIDATE_REQUEST_HASH_REQUIRED")["value"]
    db.cypher_query(
        """MERGE (lock:OsbCandidateRequestLock {key: $key})
           ON CREATE SET lock.created_at=datetime(),lock.revision=0
           SET lock.revision=lock.revision+1,lock.touched_at=datetime()
           RETURN lock.revision""",
        {"key": f"{tenant_id}|{request_hash}"},
    )
    prior, _ = db.cypher_query(
        """MATCH (candidate:OsbCandidateSetV1 {tenant_id: $tenant_id, request_hash: $request_hash,
             platform_study_id: $platform_study_id})
           RETURN candidate.payload_json,candidate.payload_hash,candidate.artifact_ref_json,
                  candidate.candidate_set_id,candidate.candidate_set_version_id,
                  candidate.native_study_id,candidate.native_version,candidate.signed_envelope_json""",
        {"tenant_id": tenant_id, "request_hash": request_hash, "platform_study_id": platform_study_id},
    )
    if supersedes_candidate_set_version_id is not None and sum(
        str(row[4]) == supersedes_candidate_set_version_id for row in prior
    ) != 1:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_REFRESH_PRECONDITION_FAILED",
            "The named prior version must belong to this exact study and signed request.", 409,
        )
    current: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    for row in prior:
        prior_payload = _record(json.loads(str(row[0])), "OSB_CANDIDATE_SET_STORED_INVALID")
        expected_prior_hash = canonical_json_hash_ref(
            prior_payload, schema_version="OsbCandidateSetV1@1.0.0",
            media_type=CANDIDATE_SET_MEDIA_TYPE,
        )
        if expected_prior_hash["value"] != str(row[1]):
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_STORED_HASH_MISMATCH", "Stored candidate set is corrupt.", 500)
        if prior_payload.get("osbStudyIdentity") != expected_identity:
            raise OsbCandidateSetError(
                "OSB_CANDIDATE_SET_STORED_IDENTITY_MISMATCH", "Stored candidate identity differs from its exact request.", 500,
            )
        # Validate identity, native checkpoint and expiry unconditionally.
        # Only the separately compared capability hash is refreshable; a
        # concurrent native-binding failure must never be swallowed.
        checkpoint = _assert_candidate_native_checkpoint_current(prior_payload, binding=binding)
        if str(checkpoint.get("osbOpenApiHash") or "") != osb_openapi_hash:
            continue
        current.append((row, prior_payload, expected_prior_hash))
    if len(current) > 1:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_SET_CURRENT_AMBIGUOUS", "Multiple candidate versions claim the same current checkpoint.", 409,
        )
    if current:
        row, prior_payload, expected_prior_hash = current[0]
        envelope = json.loads(str(row[7])) if len(row) > 7 and row[7] else bind_candidate_set_envelope(
            json.loads(str(row[2]))
        )
        assignment = _record(prior_payload.get("assignment"), "OSB_CANDIDATE_SET_ASSIGNMENT_REQUIRED")
        return {
            "payload": prior_payload, "payloadHash": expected_prior_hash,
            "artifactRef": json.loads(str(row[2])), "candidateSetId": str(row[3]),
            "candidateSetVersionId": str(row[4]), "nativeIdentity": str(row[5]),
            "nativeVersion": str(row[6]), "assignment": assignment,
            "signedEnvelope": envelope, "replay": True,
        }
    if prior and supersedes_candidate_set_version_id is None:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_SET_STALE",
            "Capability or native checkpoint changed. Explicit versioned refresh is required.", 409,
        )
    intents = [_record(item, "OSB_TYPED_SOURCE_INTENT_INVALID") for item in _list(
        request_payload.get("typedSourceIntents"), "OSB_TYPED_SOURCE_INTENTS_REQUIRED"
    )]
    # Members CSL routed away from native execution (governed_extension /
    # deferred_blocking) have no typed source intent: they must produce NO
    # library-search work, only conserved census rows and deferredMembers
    # entries the adjudicating human can see.
    request_members = [_record(item, "OSB_CANDIDATE_REQUEST_MEMBER_INVALID") for item in _list(
        request_payload.get("activeClaimRevisions"), "OSB_CANDIDATE_REQUEST_MEMBERS_REQUIRED"
    )]
    member_index_by_key = {
        f'{member.get("sourceFactId")}@{member.get("revision")}': index
        for index, member in enumerate(request_members)
    }
    routed_request_rows = [
        row for row in (
            _record(item, "OSB_CANDIDATE_REQUEST_CENSUS_REQUIRED") for item in _list(
                _record(
                    request_payload.get("inputConservation"), "OSB_CANDIDATE_REQUEST_CENSUS_REQUIRED"
                ).get("rows"),
                "OSB_CANDIDATE_REQUEST_CENSUS_REQUIRED",
            )
        )
        if str(row.get("disposition") or "") in ROUTED_NON_NATIVE_DISPOSITIONS
    ]
    observed_keys: set[str] = set()
    groups: list[MappingContextCandidateGroupRequest] = []
    for index, intent in enumerate(intents):
        key = f'{intent.get("factId")}@{intent.get("revision")}:{intent.get("targetKey")}'
        if key in observed_keys:
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_DUPLICATE_MEMBER", "Duplicate candidate intent.", 422)
        observed_keys.add(key)
        family = canonicalize_family(str(intent.get("resourceFamily") or ""))
        search_strings = [str(value) for value in _list(intent.get("searchStrings", []), "OSB_SEARCH_STRINGS_INVALID")]
        group: dict[str, Any] = {
            "fact_id": str(intent["factId"]),
            "concept_id": str(intent["conceptId"]),
            "target_key": str(intent["targetKey"]),
            "semantic_role": str(intent["semanticRole"]),
            "resource_family": family,
            "search_strings": search_strings,
            "search_codes": [str(value) for value in _list(intent.get("searchCodes", []), "OSB_SEARCH_CODES_INVALID")],
        }
        if family == "controlled_terminology":
            group["parent_resource_type"] = "CTCodelist"
            group["parent_search_strings"] = search_strings[:10] or [str(intent["semanticRole"])[:256]]
        groups.append(MappingContextCandidateGroupRequest(**group))
    # THE BINDING'S CHECKPOINT IS NOT A STUDY VALUE VERSION. For a draft root,
    # `_native_checkpoint` returns `native_version or native_status.lower()` —
    # the literal string "draft" — and forwarding that here made the standard-
    # version repository filter `HAS_VERSION.version = 'draft'` against
    # relationships whose version is NULL on every draft in the database
    # (measured: 31/31). Packages therefore resolved to [] for every study,
    # the DDF CT prerequisite fired, and every group's library search was
    # skipped — even on a study whose StudyStandardVersions were correctly
    # selected. A numbered version (a LOCKED/RELEASED checkpoint) is a real
    # study value version and is forwarded; a status word means "the draft's
    # latest value", which is exactly the repository's None branch.
    native_version = str(binding["nativeVersion"] or "")
    study_value_version = native_version if native_version.replace(".", "", 1).isdigit() else None
    context = (mapping_context_service or MappingContextService()).get_context_v2(
        MappingContextV2Request(
            study_uid=binding["nativeIdentity"],
            study_value_version=study_value_version,
            candidate_groups=groups,
            maximum_candidates_per_group=10,
        ),
        osb_openapi_hash=osb_openapi_hash,
    )
    if len(context.candidate_groups) != len(intents):
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_MEMBER_MISMATCH", "Mapping context omitted or duplicated a source intent.", 422)
    context_value = _camelize(jsonable_encoder(context, by_alias=True, exclude_none=False))
    context_value["schemaVersion"] = "osb-mapping-context/2.0"
    context_value["mappingAuthority"] = "OpenStudyBuilder"
    context_value["contextHash"] = context.context_hash
    metadata_offers = prepare_metadata_offers(intents, binding["nativeIdentity"], context)
    metadata_blockers: list[str] = []
    candidates: list[dict[str, Any]] = []
    census_rows: list[dict[str, Any]] = []
    request_id = str(request_payload["requestId"])
    for index, (intent, group) in enumerate(zip(intents, context.candidate_groups, strict=True)):
        if str(group.fact_id) != str(intent["factId"]) or str(group.target_key) != str(intent["targetKey"]):
            raise OsbCandidateSetError("OSB_CANDIDATE_SET_MEMBER_MISMATCH", "Mapping context reordered source intents.", 422)
        candidate_records = _camelize(jsonable_encoder(group.candidates, by_alias=True, exclude_none=False))
        if intent["resourceFamily"] == "study_metadata" and candidate_records:
            raise OsbCandidateSetError("OSB_STUDY_METADATA_CANDIDATE_INVALID", "Study properties cannot select library candidates.", 422)
        record = {
            "factId": intent["factId"], "revision": intent["revision"],
            "conceptId": intent["conceptId"], "targetKey": intent["targetKey"],
            "semanticRole": intent["semanticRole"],
            # Both spellings survive: the family CSL asked for verbatim, and
            # the canonical family the search actually ran against.
            "resourceFamily": canonicalize_family(str(intent["resourceFamily"])),
            "requestedResourceFamily": intent["resourceFamily"],
            # The claim's content and provenance ride on the record so the
            # adjudicating human sees what they are mapping without
            # dereferencing the stored request payload.
            "source": intent.get("source"),
            "evidence": intent.get("evidence"),
            "nativeCandidates": candidate_records,
            "createOption": intent.get("createOption") or None,
            "complete": group.complete, "truncated": group.truncated,
            "blockers": list(group.release_blockers),
        }
        metadata_offer = metadata_offers.get(f'{intent["factId"]}@{intent["revision"]}:{intent["targetKey"]}')
        if metadata_offer is None:
            # Validate native create offers with the same planner as execution.
            # Keeping an unavailable create button would defer a known failure
            # until after a human signed their mapping choice.
            from clinical_mdr_api.services.integrations.native_capture_mapping import prepare_capture_create_offer

            metadata_offer = prepare_capture_create_offer(intent)
        if metadata_offer is not None:
            record["createOption"] = metadata_offer["createOption"]
            record["blockers"].extend(metadata_offer["blockers"])
            record["complete"] = record["complete"] and not metadata_offer["blockers"]
            metadata_blockers.extend(f'{code}:{intent["factId"]}:{intent["targetKey"]}'
                                     for code in metadata_offer["blockers"])
        disposition = _assert_readable_or_create(record)
        candidates.append(record)
        record_hash = canonical_json_hash_ref(record, schema_version="OsbCandidateRecordV1@1.0.0")
        census_rows.append(_census_row(
            unit_id=f'{intent["factId"]}@{intent["revision"]}:{intent["targetKey"]}',
            source_artifact_id=request_id,
            source_contract="accuratrials.osb.OsbCandidateRequestV1",
            source_type="OsbTypedSourceIntentV1",
            source_path=f"#/typedSourceIntents/{index}",
            source_hash=canonical_json_hash_ref(intent, schema_version="OsbTypedSourceIntentV1@1.0.0"),
            target_artifact_id="pending-candidate-set",
            target_path=f"#/candidateRecords/{index}",
            target_hash=record_hash,
            index=index,
            disposition=disposition,
            evidence_refs=[f"osb-context:{context.context_hash}"],
        ))
    deferred_members: list[dict[str, Any]] = []
    for offset, request_row in enumerate(routed_request_rows):
        member_key = str(request_row.get("unitId") or "").rsplit(":", 1)[0]
        member_index = member_index_by_key.get(member_key)
        if member_index is None:
            raise OsbCandidateSetError(
                "OSB_CANDIDATE_SET_CENSUS_MISMATCH",
                "Routed census row does not name a request member.",
                422,
            )
        member = request_members[member_index]
        disposition = str(request_row["disposition"])
        reason_codes = [str(ref) for ref in (request_row.get("evidenceRefs") or [])]
        deferred_members.append({
            "factId": member["sourceFactId"], "revision": member["revision"],
            "disposition": disposition, "reasonCodes": reason_codes,
        })
        census_rows.append(_routed_census_row(
            unit_id=str(request_row.get("unitId")),
            request_id=request_id,
            member_index=member_index,
            member_hash=member.get("valueHash"),
            disposition=disposition,
            evidence_refs=reason_codes,
            index=len(intents) + offset,
        ))
    candidate_set_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-candidate-set:v1:{request_hash}"))
    set_seed = canonical_json_hash_ref(
        {"requestHash": request_hash, "contextHash": context.context_hash,
         "osbOpenApiHash": osb_openapi_hash, "candidates": candidates},
        schema_version="OsbCandidateSetSeedV1@1.0.0",
    )["value"]
    candidate_set_version_id = str(uuid5(NAMESPACE_URL, f"{candidate_set_id}:{set_seed}"))
    for row in census_rows:
        if isinstance(row.get("target"), dict):
            row["target"]["artifactId"] = candidate_set_id
    census_hash = canonical_json_hash_ref(census_rows, schema_version="ConservationCensusRowsV1@1.0.0")
    created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assignment = candidate_assignment_identity(
        tenant_id=tenant_id, platform_study_id=platform_study_id,
        candidate_set_version_id=candidate_set_version_id,
    )
    payload = {
        "contractVersion": "OsbCandidateSetV1@1.0.0",
        "candidateSetId": candidate_set_id,
        "candidateSetVersionId": candidate_set_version_id,
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "request": {"requestVersionId": request_payload["requestVersionId"], "payloadHash": artifact["payloadHash"]},
        "semanticSnapshot": request_payload["semanticSnapshot"],
        "sourceFactPackage": request_payload["sourceFactPackage"],
        "osbStudyIdentity": expected_identity,
        "capabilityCheckpoint": {"osbOpenApiHash": osb_openapi_hash,
                                 "mappingContextHash": context.context_hash,
                                 "nativeVersion": binding["nativeVersion"], "governed": context.governed},
        "mappingContext": context_value,
        "candidateRecords": candidates,
        "deferredMembers": deferred_members,
        "conservation": {"contractVersion": "ConservationCensusV1@1.0.0", "rows": census_rows,
                         "rowSetHash": census_hash, "counts": _census_counts(census_rows)},
        "assignment": assignment,
        "blockers": sorted(set([*context.release_blockers, *metadata_blockers])),
        "expiresAt": request_payload["expiresAt"],
        "createdAt": created_at,
        "createdBy": actor,
    }
    payload_hash = canonical_json_hash_ref(
        payload, schema_version="OsbCandidateSetV1@1.0.0", media_type=CANDIDATE_SET_MEDIA_TYPE
    )
    artifact_ref = _artifact_ref({
        "artifactId": candidate_set_id, "artifactVersionId": candidate_set_version_id,
        "kind": "osb-candidate-set", "stableLocator": f"artifact://osb/candidate-set/{candidate_set_version_id}",
        "payloadHash": payload_hash, "byteSize": len(canonical_json(payload).encode("utf-8")),
        "classification": "regulated-non-phi", "tenantId": tenant_id,
        "region": "us-central1", "producerService": "osb.package",
        "producerEnvironment": "prototype", "producerVersion": "prototype",
        "payloadContract": "accuratrials.osb.OsbCandidateSetV1", "payloadContractVersion": "1.0.0",
        "purpose": "mapping-adjudication", "createdAt": created_at,
    })
    envelope = (artifact_signer or bind_candidate_set_envelope)(artifact_ref)
    if not isinstance(envelope, dict) or envelope.get("contractVersion") != "SignedArtifactEnvelopeV1@1.0.0" \
            or not hash_refs_equal(envelope.get("payloadHash"), payload_hash) \
            or _record(envelope.get("signingStatement"), "OSB_CANDIDATE_SET_SIGNATURE_INVALID").get("signingPurpose") != "osb-candidate-set":
        raise OsbCandidateSetError("OSB_CANDIDATE_SET_SIGNATURE_INVALID", "Candidate set envelope does not bind the payload.", 422)
    stored, _ = db.cypher_query(
        """MERGE (request:OsbCandidateRequestV1 {tenant_id: $tenant_id, request_hash: $request_hash})
           ON CREATE SET request.request_version_id=$request_version_id, request.request_id=$request_id,
             request.platform_study_id=$platform_study_id, request.payload_json=$request_json,
             request.received_at=datetime()
           MERGE (candidate:OsbCandidateSetV1 {tenant_id: $tenant_id, request_hash: $request_hash,
             candidate_set_version_id: $set_version_id})
           ON CREATE SET candidate.candidate_set_id=$set_id, candidate.platform_study_id=$platform_study_id,
             candidate.payload_hash=$payload_hash, candidate.context_hash=$context_hash,
             candidate.native_study_id=$native_study_id, candidate.native_version=$native_version,
             candidate.payload_json=$payload_json, candidate.artifact_ref_json=$artifact_ref_json,
             candidate.assignment_id=$assignment_id, candidate.signed_envelope_json=$signed_envelope_json,
             candidate.osb_openapi_hash=$osb_openapi_hash, candidate.created_at=datetime()
           MERGE (candidate)-[:GENERATED_FROM]->(request)
           RETURN candidate.candidate_set_version_id, candidate.payload_hash, candidate.assignment_id""",
        {"request_version_id": request_payload["requestVersionId"], "request_id": request_payload["requestId"],
         "tenant_id": tenant_id, "platform_study_id": platform_study_id, "request_hash": request_hash,
         "request_json": canonical_json(request_payload), "set_version_id": candidate_set_version_id,
         "set_id": candidate_set_id, "payload_hash": payload_hash["value"], "context_hash": context.context_hash,
         "native_study_id": binding["nativeIdentity"], "native_version": binding["nativeVersion"],
         "payload_json": canonical_json(payload), "artifact_ref_json": canonical_json(artifact_ref),
         "assignment_id": assignment["assignmentId"], "signed_envelope_json": canonical_json(envelope),
         "osb_openapi_hash": osb_openapi_hash},
    )
    if len(stored) != 1 or list(stored[0]) != [
        candidate_set_version_id, payload_hash["value"], assignment["assignmentId"],
    ]:
        raise OsbCandidateSetError(
            "OSB_CANDIDATE_SET_VERSION_COLLISION", "Candidate version already has different immutable bytes.", 409,
        )
    return {"payload": payload, "payloadHash": payload_hash, "artifactRef": artifact_ref,
            "candidateSetId": candidate_set_id, "candidateSetVersionId": candidate_set_version_id,
            "nativeIdentity": binding["nativeIdentity"], "nativeVersion": binding["nativeVersion"],
            "assignment": assignment, "signedEnvelope": envelope}


def _camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, item in value.items():
            parts = str(key).split("_")
            camel = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:] if part)
            rendered[camel] = _camelize(item)
        return rendered
    return value


__all__ = [
    "CANDIDATE_REQUEST_MEDIA_TYPE", "CANDIDATE_SET_MEDIA_TYPE", "OsbCandidateSetError",
    "active_osb_binding", "assert_candidate_request_transfer_envelope", "assert_candidate_set_current",
    "bind_candidate_set_envelope", "candidate_assignment_identity",
    "decode_signed_artifact_envelope_header",
    "generate_candidate_set", "load_candidate_request", "require_exactly_one_active_osb_binding",
    "store_candidate_request_bytes", "verify_candidate_request_artifact",
]
