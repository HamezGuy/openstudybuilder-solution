# Trust bundle versions are exact JSON integers, never booleans or subclasses.
# pylint: disable=unidiomatic-typecheck

"""Read the actual locked OSB study for the additive carrier state profile.

Authored extensions do not manufacture native operation evidence. Historical
operations continue through the existing V1 custody and native read-back path.
"""

from __future__ import annotations

from typing import Any

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    hash_refs_equal,
)
from clinical_mdr_api.services.integrations.candidate_set import (
    OsbCandidateSetError,
    _assert_identity_binding,
    active_osb_binding,
)
from clinical_mdr_api.services.integrations.native_package_carrier_contracts import (
    validate_carrier_contract,
    validate_carrier_payload,
)
from clinical_mdr_api.services.integrations.native_package_state_v2 import (
    DEFINITION_SCHEMA,
    _current_study,
    load_checkpoint_native_state,
    read_pinned_canonical_definition,
    require_native_lock,
    require_source_projection,
)

CARRIER_STATE_SCHEMA = "OsbPackageNativeStateV1@1.2.0"
SOURCE_BASIS_SCHEMA = "AcceptedCanonicalSourceBasisV1@1.0.0"


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OsbCandidateSetError(
            code, "Carrier commitments or native state differ.", 409
        )


def _same(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def _hash(value: Any, schema: str) -> dict[str, Any]:
    return canonical_json_hash_ref(value, schema_version=schema)


def _verified_identity(
    identity: dict[str, Any],
    pin: dict[str, Any],
    tenant_id: str,
    platform_study_id: str,
) -> dict[str, str]:
    """Join the contextual registry identity to OSB's retained receipt bridge."""
    validate_carrier_contract(pin, "OsbBindingPin")
    validate_carrier_contract(identity, "NativeIdentityContext")
    evidence = identity.get("evidence") if isinstance(identity, dict) else None
    _require(
        isinstance(evidence, dict)
        and identity.get("contractVersion") == "1.1.0"
        and identity.get("bindingId") == pin["platformBindingId"]
        and identity.get("nativeIdentity") == pin["nativeStudyId"]
        and identity.get("nativeVersion") == pin["nativeVersion"]
        and pin["nativeTenantId"] == tenant_id
        and identity.get("tenantId") == tenant_id
        and identity.get("platformStudyId") == platform_study_id,
        "OSB_CARRIER_IDENTITY_CONTEXT_REQUIRED",
    )
    for key in ("receiptPayloadHash", "signedEnvelopeHash", "trustBundleHash"):
        validate_carrier_contract(evidence.get(key), "HashRef")
    _require(
        all(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ("receiptId", "trustedSigningTime")
        )
        and type(evidence.get("trustBundleVersion")) is int
        and evidence["trustBundleVersion"] >= 1,
        "OSB_CARRIER_IDENTITY_CONTEXT_REQUIRED",
    )
    binding = active_osb_binding(tenant_id, platform_study_id)
    _assert_identity_binding(
        identity,
        binding,
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        error_code="OSB_CARRIER_IDENTITY_CONTEXT_MISMATCH",
    )
    return binding


def observe_carrier_native_state(
    *,
    tenant_id: str,
    platform_study_id: str,
    scope: dict[str, Any],
    source_basis: dict[str, Any],
    osb_binding: dict[str, Any],
    osb_study_identity: dict[str, Any],
    native_evidence_basis: dict[str, Any],
    carrier: dict[str, Any],
    carrier_artifact: dict[str, Any],
    bundle: dict[str, Any] | None,
    native_checkpoints: list[tuple[dict[str, Any], dict[str, Any]]],
    captured_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe a state preimage before checkpoint creation or review.

    ``native_checkpoints`` contains exact (ArtifactRef, payload) pairs obtained
    from authorized custody. Source acceptance and crosswalk conservation are
    independently validated before this observation is used for release.
    """
    validate_carrier_contract(scope, "Scope")
    validate_carrier_contract(source_basis, "SourceBasis")
    validate_carrier_contract(native_evidence_basis, "NativeEvidenceBasis")
    _require(
        (scope["platformTenantId"], scope["platformStudyId"])
        == (tenant_id, platform_study_id),
        "OSB_CARRIER_SCOPE_MISMATCH",
    )
    validate_carrier_payload(carrier, carrier_artifact, tenant_id, platform_study_id)
    _require(_same(carrier["scope"], scope), "OSB_CARRIER_SCOPE_MISMATCH")
    source_hash = _hash(source_basis, SOURCE_BASIS_SCHEMA)
    _require(
        hash_refs_equal(source_hash, carrier["sourceBasisHash"]),
        "OSB_CARRIER_SOURCE_BASIS_MISMATCH",
    )
    if bundle is not None:
        validate_carrier_payload(
            bundle, carrier["bundleArtifact"], tenant_id, platform_study_id
        )
    else:
        # Context reads use only an owner-captured preimage; the caller verifies
        # its immutable owner capture/index association before taking this path.
        validate_carrier_contract(captured_state, "OsbNativeState12")
    binding = _verified_identity(
        osb_study_identity, osb_binding, tenant_id, platform_study_id
    )
    native = {
        "nativeIdentity": osb_binding["nativeStudyId"],
        "nativeVersion": osb_binding["nativeVersion"],
    }
    root, managed = _current_study(tenant_id, platform_study_id, native)
    require_native_lock({"root": root})
    _require(
        root["bindingId"] == binding["bindingId"],
        "OSB_CARRIER_IDENTITY_CONTEXT_MISMATCH",
    )

    records: dict[str, dict[str, Any]] = {}
    lineages = []
    if native_evidence_basis["kind"] == "no-native-operations":
        _require(not native_checkpoints, "OSB_CARRIER_NATIVE_BASIS_MISMATCH")
    else:
        refs = native_evidence_basis["checkpointArtifacts"]
        expected_evidence = native_evidence_basis["nativeEvidenceSetArtifacts"]
        supplied_refs = [
            canonical_json(reference) for reference, _ in native_checkpoints
        ]
        _require(
            len(refs) == len(native_checkpoints)
            and len({canonical_json(ref) for ref in refs}) == len(refs)
            and len(set(supplied_refs)) == len(supplied_refs)
            and set(supplied_refs) == {canonical_json(ref) for ref in refs}
            and len({canonical_json(ref) for ref in expected_evidence})
            == len(expected_evidence),
            "OSB_CARRIER_NATIVE_BASIS_MISMATCH",
        )
        actual_evidence = {}
        for checkpoint_ref, checkpoint in native_checkpoints:
            _require(
                sum(_same(checkpoint_ref, ref) for ref in refs) == 1,
                "OSB_CARRIER_NATIVE_BASIS_MISMATCH",
            )
            validate_carrier_payload(
                checkpoint, checkpoint_ref, tenant_id, platform_study_id
            )
            _require(
                checkpoint.get("contractVersion") == "TransformationCheckpointV1@1.0.0",
                "OSB_CARRIER_NATIVE_CHECKPOINT_VERSION_UNSUPPORTED",
            )
            observed = load_checkpoint_native_state(
                tenant_id=tenant_id,
                platform_study_id=platform_study_id,
                checkpoint=checkpoint,
            )
            require_source_projection(observed)
            require_native_lock(observed)
            _require(_same(observed["root"], root), "OSB_CARRIER_NATIVE_ROOT_MISMATCH")
            evidence_ref = observed["nativeEvidenceSetArtifact"]
            actual_evidence[canonical_json(evidence_ref)] = evidence_ref
            lineages.append({"checkpoint": checkpoint, "state": observed})
            for record in observed["records"]:
                prior = records.get(record["operationId"])
                _require(
                    prior is None or _same(prior, record),
                    "OSB_CARRIER_NATIVE_OPERATION_CONFLICT",
                )
                records[record["operationId"]] = record
        _require(
            set(actual_evidence) == {canonical_json(ref) for ref in expected_evidence},
            "OSB_CARRIER_NATIVE_BASIS_MISMATCH",
        )

    definition = read_pinned_canonical_definition(root)
    definition_hash = _hash(definition, DEFINITION_SCHEMA)
    _require(
        (
            bundle is None
            or (
                _same(bundle["definition"].get("document"), definition["document"])
                and _same(
                    bundle["definition"].get("selection"), definition["selection"]
                )
            )
        )
        and hash_refs_equal(
            carrier["definitionDocumentHash"],
            _hash(definition["document"], "AuthoredStudyDefinitionDocumentV1@1.0.0"),
        )
        and hash_refs_equal(
            carrier["definitionSelectionHash"],
            _hash(definition["selection"], "AuthoredStudyDefinitionSelectionV1@1.0.0"),
        ),
        "OSB_CARRIER_CANONICAL_DEFINITION_MISMATCH",
    )
    ordered_records = sorted(
        records.values(), key=lambda record: record["operationId"].encode("utf-16-be")
    )
    index = [
        {
            "operationId": record["operationId"],
            "kind": record["kind"],
            "resourceFamily": record["resourceFamily"],
            "targetIdentity": record["targetIdentity"],
            "readBackHash": record["readBackHash"],
            "recordHash": _hash(record, "OsbPackageNativeRecordV1@1.0.0"),
        }
        for record in ordered_records
    ]
    state = {
        "contractVersion": CARRIER_STATE_SCHEMA,
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "root": root,
        "canonicalDefinitionHash": definition_hash,
        "contentIndex": index,
        "nativeEvidenceBasis": native_evidence_basis,
        "sourceBasisHash": source_hash,
        "carrierHash": carrier_artifact["payloadHash"],
        "bundlePayloadHash": carrier["bundleArtifact"]["payloadHash"],
        "crosswalkHash": carrier["crosswalkArtifact"]["payloadHash"],
    }
    validate_carrier_contract(state, "OsbNativeState12")
    if captured_state is not None:
        _require(_same(state, captured_state), "OSB_POST_REVIEW_NATIVE_EDIT")
    _require(
        _same(
            [root, managed], list(_current_study(tenant_id, platform_study_id, native))
        ),
        "OSB_POST_CHECKPOINT_NATIVE_EDIT",
    )
    _require(
        _same(
            binding,
            _verified_identity(
                osb_study_identity, osb_binding, tenant_id, platform_study_id
            ),
        ),
        "OSB_CARRIER_IDENTITY_CONTEXT_MISMATCH",
    )
    return {
        "nativeState": state,
        "stateHash": _hash(state, CARRIER_STATE_SCHEMA),
        "root": root,
        "records": ordered_records,
        "contentIndex": index,
        "contentIndexHash": _hash(index, "OsbPackageContentIndexV1@1.0.0"),
        "canonicalDefinition": definition,
        "canonicalDefinitionHash": definition_hash,
        "lineages": lineages,
    }
