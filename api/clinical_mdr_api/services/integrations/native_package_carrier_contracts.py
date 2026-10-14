"""Pinned schema and byte validation for additive authored-execution artifacts.

This is transport validation, not acceptance, native observation or approval.
Those checks belong to the respective owner services and release preparation.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
    hash_refs_equal,
    raw_bytes_hash_ref,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.native_package_v2 import (
    _approval_schema_version,
    _manifest_schema_version,
    _verify_artifact_ref,
)

SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "generated/platform_contracts"
MAX_CARRIER_ARTIFACT_BYTES = 128 * 1024 * 1024


def _no_remote_schema(uri: str) -> Any:
    raise NoSuchResource(ref=uri)


@lru_cache(maxsize=10)
def _document(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIRECTORY / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=64)
def _validator(
    definition: str, successor: bool = False, interface: bool = False
) -> Draft202012Validator:
    formats = FormatChecker()
    if not {"uuid", "uri", "date-time"}.issubset(formats.checkers):
        raise OsbCandidateSetError(
            "OSB_CARRIER_SCHEMA_FORMATS_UNAVAILABLE",
            "Required schema format validators are unavailable.",
            503,
        )
    if definition == "PlatformArtifactRef":
        schema = _document("artifact-ref-v1.schema.json")
    elif definition == "NativeIdentityContext":
        command = _document("authored-review-successor-contracts.json")["commandInput"]
        schema = {
            "$schema": command["$schema"],
            "$defs": command["$defs"],
            "$ref": "#/$defs/ExternalStudyIdentityV11",
        }
    elif interface:
        schema = _document("authored-review-interface-contracts.json")[definition]
    elif successor:
        schema = _document("authored-review-successor-contracts.json")[definition]
    else:
        owner = _document("authored-study-execution-v1.schema.json")
        schema = {
            "$schema": owner["$schema"],
            "$defs": owner["$defs"],
            "$ref": f"#/$defs/{definition}",
        }
    Draft202012Validator.check_schema(schema)
    resources = [
        _document(name)
        for name in (
            "osb-native-package-v2.schema.json",
            "governance-attestation-v1.schema.json",
            "artifact-ref-v1.schema.json",
            "hash-ref-v1.schema.json",
        )
    ]
    registry = Registry(retrieve=_no_remote_schema).with_resources(
        (resource["$id"], Resource.from_contents(resource)) for resource in resources
    )
    return Draft202012Validator(
        schema,
        format_checker=formats,
        registry=registry,
    )


def validate_carrier_contract(
    payload: Any, definition: str, *, successor: bool = False, interface: bool = False
) -> None:
    """Validate a pinned owner definition without fetching remote references."""
    try:
        error = next(
            _validator(definition, successor, interface).iter_errors(payload), None
        )
        # JSON Schema's number type can contain non-finite Python values; the
        # platform canonicalizer is also the scalar/Unicode admissibility gate.
        canonical_json(payload)
    except (
        ValueError,
        TypeError,
        OverflowError,
        UnicodeError,
        RecursionError,
    ) as error:
        raise OsbCandidateSetError(
            "OSB_CARRIER_CONTRACT_INVALID",
            "Artifact is not admissible canonical JSON.",
            422,
        ) from error
    if error is not None:
        raise OsbCandidateSetError(
            "OSB_CARRIER_CONTRACT_INVALID",
            f"Artifact does not satisfy {definition}.",
            422,
        )


def validate_artifact_ref(artifact: Any) -> None:
    validate_carrier_contract(artifact, "PlatformArtifactRef")
    validate_carrier_contract(artifact, "ArtifactRef")
    fields = {
        key: value
        for key, value in artifact.items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    if not hash_refs_equal(
        artifact["descriptorHash"],
        descriptor_hash({"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}),
    ):
        raise OsbCandidateSetError(
            "OSB_CARRIER_DESCRIPTOR_MISMATCH", "Artifact descriptor differs.", 422
        )
    if artifact["byteSize"] > MAX_CARRIER_ARTIFACT_BYTES:
        raise OsbCandidateSetError(
            "OSB_CARRIER_ARTIFACT_TOO_LARGE",
            "Artifact exceeds the transfer limit.",
            413,
        )


@lru_cache(maxsize=1)
def _profiles() -> dict[tuple[str, str], dict[str, Any]]:
    profiles = {}
    for profile in _document("authored-execution-contract-pins.json")[
        "artifactProfiles"
    ]:
        if profile["record"] == "SnapshotProjectionPackage":
            for version in ("1.2", "1.3", "1.4"):
                profiles[(profile["kind"], f"{version}.0")] = {
                    **profile,
                    "version": f"{version}.0",
                    "payloadVersion": version,
                    "schemaVersion": f"SnapshotProjectionPackage@{version}.0",
                }
        else:
            if profile["record"] == "StudyExchangeBundle":
                profile = {
                    **profile,
                    "mediaType": "application/vnd.accuratrial.studybundle+json",
                }
            profiles[(profile["kind"], profile["version"])] = profile
    for kind, contract, schema, media in (
        (
            "transformation-checkpoint",
            "TransformationCheckpointV1",
            "TransformationCheckpointV1@1.0.0",
            "application/json",
        ),
        (
            "osb-native-evidence-set",
            "OsbNativeEvidenceSetV1",
            "OsbNativeEvidenceSetV1@1.0.0",
            "application/vnd.accuratrials.osb-native-evidence-set-v1+json",
        ),
    ):
        owner = "osb" if kind == "osb-native-evidence-set" else "csl"
        profiles[(kind, "1.0.0")] = {
            "record": "LegacyNativeEvidence",
            "kind": kind,
            "payloadContract": f"accuratrials.{owner}.{contract}",
            "version": "1.0.0",
            "schemaVersion": schema,
            "mediaType": media,
        }
    profiles[("osb-canonical-study-definition", "1.0.0")] = {
        "record": "OsbCanonicalStudyDefinition",
        "kind": "osb-canonical-study-definition",
        "payloadContract": "accuratrials.osb.OsbCanonicalStudyDefinitionV1",
        "version": "1.0.0",
        "schemaVersion": "OsbCanonicalStudyDefinitionV1@1.0.0",
        "mediaType": "application/json",
    }
    for kind, contract, media in (
        (
            "platform-manifest-v1",
            "PlatformManifestV1",
            "application/vnd.accuratrials.platform-manifest-v1+json",
        ),
        (
            "pre-release-approval-v1",
            "PreReleaseApprovalV1",
            "application/vnd.accuratrials.pre-release-approval-v1+json",
        ),
        (
            "osb-specialist-review-evidence",
            "OsbSpecialistReviewEvidenceV1",
            "application/vnd.accuratrials.osb-specialist-review-evidence-v1+json",
        ),
    ):
        for version in (
            ("1.0.0",)
            if kind == "osb-specialist-review-evidence"
            else ("1.0.0", "1.1.0")
        ):
            owner = "osb" if kind == "osb-specialist-review-evidence" else "cc"
            profiles[(kind, version)] = {
                "record": "LegacyRelease",
                "kind": kind,
                "payloadContract": f"accuratrials.{owner}.{contract}",
                "version": version,
                "schemaVersion": f"{contract}@{version}",
                "mediaType": media,
            }
    return profiles


def validate_carrier_payload(
    payload: Any, artifact: Any, tenant_id: str, platform_study_id: str
) -> None:
    """Verify original descriptor, exact supported profile, canonical bytes and scope.

    Payloads without a root study field (notably the full bundle) are scoped by
    authenticated retained custody here and by the complete owner closure later.
    A successful upload grants no source acceptance or release eligibility.
    """
    validate_artifact_ref(artifact)
    profile = _profiles().get((artifact["kind"], artifact["payloadContractVersion"]))
    if profile is None or artifact["payloadContract"] != profile["payloadContract"]:
        raise OsbCandidateSetError(
            "OSB_CARRIER_ARTIFACT_PROFILE_UNSUPPORTED",
            "Unsupported artifact profile.",
            422,
        )
    if not isinstance(payload, dict):
        raise OsbCandidateSetError(
            "OSB_CARRIER_PAYLOAD_INVALID", "Artifact must be an object.", 422
        )
    record = profile["record"]
    owner_definitions = _document("authored-study-execution-v1.schema.json")["$defs"]
    if record == "LegacyRelease":
        schema_version = profile["schemaVersion"]
        if artifact["kind"] == "platform-manifest-v1":
            schema_version = _manifest_schema_version(payload, artifact)
        elif artifact["kind"] == "pre-release-approval-v1":
            schema_version = _approval_schema_version(artifact)
        _verify_artifact_ref(
            payload,
            artifact,
            kind=artifact["kind"],
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
            schema_version=schema_version,
            media_type=profile["mediaType"],
        )
    elif record in owner_definitions:
        validate_carrier_contract(payload, record)
    elif record in ("specialist", "approval"):
        validate_carrier_contract(payload, record, successor=True)
    elif record == "StudyExchangeBundle":
        if payload.get("formatVersion") != "2.0" or any(
            not isinstance(payload.get(field), dict)
            for field in ("definition", "execution", "source")
        ):
            raise OsbCandidateSetError(
                "OSB_CARRIER_BUNDLE_INVALID", "Complete bundle required.", 422
            )
    elif record == "SnapshotProjectionPackage":
        if payload.get("contractVersion") != profile["payloadVersion"]:
            raise OsbCandidateSetError(
                "OSB_CARRIER_SNAPSHOT_VERSION_INVALID", "Snapshot version differs.", 422
            )
    elif payload.get("contractVersion") != profile["schemaVersion"]:
        raise OsbCandidateSetError(
            "OSB_CARRIER_PAYLOAD_VERSION_INVALID", "Artifact version differs.", 422
        )
    scope = payload.get("scope")
    mismatched_scope = isinstance(scope, dict) and (
        scope.get("platformTenantId"),
        scope.get("platformStudyId"),
    ) != (tenant_id, platform_study_id)
    if (
        artifact["tenantId"] != tenant_id
        or mismatched_scope
        or (
            "tenantId" in payload
            and record != "SnapshotProjectionPackage"
            and (payload.get("tenantId"), payload.get("platformStudyId"))
            != (tenant_id, platform_study_id)
        )
    ):
        raise OsbCandidateSetError(
            "OSB_CARRIER_SCOPE_MISMATCH", "Artifact scope differs.", 422
        )
    human = payload.get("human_signature")
    if isinstance(human, dict) and (
        human.get("tenant_id"),
        human.get("platform_study_id"),
    ) != (tenant_id, platform_study_id):
        raise OsbCandidateSetError(
            "OSB_CARRIER_SCOPE_MISMATCH", "Human approval scope differs.", 422
        )
    # OSB assigns these owner artifact IDs from the corresponding logical IDs.
    # CSL checkpoint artifact identity is independently assigned by its owner.
    identities = {
        "specialist": ("reviewId", "reviewVersionId"),
        "Package23": ("packageId", "packageVersionId"),
    }
    if record in identities and any(
        payload[field] != artifact[key]
        for field, key in zip(identities[record], ("artifactId", "artifactVersionId"))
    ):
        raise OsbCandidateSetError(
            "OSB_CARRIER_ARTIFACT_IDENTITY_MISMATCH",
            "Artifact identity differs from its payload.",
            422,
        )
    try:
        encoded = canonical_json(payload).encode("utf-8")
        expected = (
            raw_bytes_hash_ref(
                encoded,
                schema_version=profile["schemaVersion"],
                media_type=profile["mediaType"],
            )
            if profile.get("canonicalizationVersion") == "raw-bytes/1.0"
            else canonical_json_hash_ref(
                payload,
                schema_version=profile["schemaVersion"],
                media_type=profile["mediaType"],
            )
        )
    except (
        ValueError,
        TypeError,
        OverflowError,
        UnicodeError,
        RecursionError,
    ) as error:
        raise OsbCandidateSetError(
            "OSB_CARRIER_PAYLOAD_INVALID", "Artifact is not canonical JSON.", 422
        ) from error
    if len(encoded) > MAX_CARRIER_ARTIFACT_BYTES:
        raise OsbCandidateSetError(
            "OSB_CARRIER_ARTIFACT_TOO_LARGE",
            "Artifact exceeds the transfer limit.",
            413,
        )
    if artifact["byteSize"] != len(encoded) or not hash_refs_equal(
        artifact["payloadHash"], expected
    ):
        raise OsbCandidateSetError(
            "OSB_CARRIER_PAYLOAD_MISMATCH",
            "Artifact bytes or hash profile differ.",
            422,
        )
