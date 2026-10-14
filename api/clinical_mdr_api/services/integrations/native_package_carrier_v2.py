"""Native observation and immutable carrier custody for package 2.3.

Capturing a native state produces evidence only. Acceptance, specialist review,
human release approval and package publication are separate operations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    hash_refs_equal,
    raw_bytes_hash_ref,
    sha256_bytes,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.native_package_artifact_custody import (
    load_carrier_artifact,
    store_carrier_artifact_bytes,
)
from clinical_mdr_api.services.integrations.native_package_carrier_contracts import (
    validate_carrier_contract,
    validate_carrier_payload,
)
from clinical_mdr_api.services.integrations.native_package_carrier_coverage import (
    verify_carrier_terminal_accounting,
)
from clinical_mdr_api.services.integrations.native_package_carrier_inspection import (
    PAGE_BYTE_LIMIT,
    prepare_carrier_inspection,
    read_carrier_inspection_descriptor,
    read_carrier_inspection_entry,
    read_carrier_inspection_page,
)
from clinical_mdr_api.services.integrations.native_package_carrier_state import (
    CARRIER_STATE_SCHEMA,
    SOURCE_BASIS_SCHEMA,
    observe_carrier_native_state,
)
from clinical_mdr_api.services.integrations.native_package_state_v2 import (
    require_native_lock,
)
from clinical_mdr_api.services.integrations.native_package_v2 import (
    _artifact_ref,
    _package_content,
    _parse_canonical,
    _require_expected_target_state,
)

CAPTURE_INPUT_SCHEMA = "OsbCarrierNativeStateCaptureInputV1@1.0.0"
REVIEW_SCOPE = "native-evidence-and-accepted-canonical-execution-extensions"
SPECIALIST_V2_MEDIA_TYPE = (
    "application/vnd.accuratrials.osb-specialist-review-evidence-v2+json"
)
PACKAGE_MEDIA_TYPE = "application/vnd.accuratrials.osb-native-package-v2+json"


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OsbCandidateSetError(code, "Carrier observation commitments differ.", 409)


def _same(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def load_carrier_observation_inputs(
    *,
    tenant_id: str,
    platform_study_id: str,
    source_basis: dict[str, Any],
    carrier_artifact: dict[str, Any],
    osb_binding: dict[str, Any],
    native_evidence_basis: dict[str, Any],
) -> dict[str, Any]:
    """Resolve full original descriptors through scoped custody, never locators."""
    validate_carrier_contract(source_basis, "SourceBasis")
    validate_carrier_contract(osb_binding, "OsbBindingPin")
    validate_carrier_contract(native_evidence_basis, "NativeEvidenceBasis")

    def load(reference):
        return load_carrier_artifact(
            artifact_ref=reference,
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
        )

    carrier = load(carrier_artifact)
    validate_carrier_contract(carrier, "BundleCarrier")
    bundle = load(carrier["bundleArtifact"])
    crosswalk = load(carrier["crosswalkArtifact"])
    census = load(carrier["exportCensusArtifact"])
    authority = load(source_basis["authorityArtifact"])
    projection_package = load(authority["snapshot"]["projectionPackageArtifact"])
    source_hash = canonical_json_hash_ref(
        source_basis, schema_version=SOURCE_BASIS_SCHEMA
    )
    _require(
        _same(carrier["scope"], authority["scope"])
        and _same(carrier["scope"], crosswalk["scope"])
        and _same(crosswalk["osbBinding"], osb_binding)
        and hash_refs_equal(carrier["sourceBasisHash"], source_hash)
        and hash_refs_equal(crosswalk["sourceBasisHash"], source_hash)
        and hash_refs_equal(
            crosswalk["bundlePayloadHash"], carrier["bundleArtifact"]["payloadHash"]
        )
        and hash_refs_equal(
            crosswalk["rowsHash"],
            canonical_json_hash_ref(
                crosswalk["rows"],
                schema_version="AuthoredStudyMemberCrosswalkRowsV1@1.0.0",
            ),
        ),
        "OSB_CARRIER_CLOSURE_MISMATCH",
    )
    _require(
        _same(
            census,
            bundle.get("extensions", {}).get("cslStudyBuild", {}).get("exportCensus"),
        )
        and census.get("mode") == "release"
        and census.get("reconciled") is True
        and census.get("unaccountedUnitIds") == []
        and projection_package.get("snapshot", {}).get("status") == "accepted"
        and projection_package.get("snapshot", {}).get("snapshotId")
        == authority["snapshot"]["snapshotId"]
        and projection_package.get("study", {}).get("studyId")
        == carrier["scope"]["nativeCslStudyId"],
        "OSB_CARRIER_SOURCE_CLOSURE_MISMATCH",
    )
    if source_basis["kind"] == "source-fact-and-accepted-canonical":
        load(source_basis["sourceFactCheckpointArtifact"])
    native_checkpoints = []
    if native_evidence_basis["kind"] == "verified-native-operations":
        native_checkpoints = [
            (reference, load(reference))
            for reference in native_evidence_basis["checkpointArtifacts"]
        ]
        for reference in native_evidence_basis["nativeEvidenceSetArtifacts"]:
            load(reference)
    return {
        "scope": carrier["scope"],
        "source_basis": source_basis,
        "osb_binding": osb_binding,
        "native_evidence_basis": native_evidence_basis,
        "carrier": carrier,
        "carrier_artifact": carrier_artifact,
        "bundle": bundle,
        "native_checkpoints": native_checkpoints,
    }


def _publish_native_observation(
    *,
    tenant_id: str,
    platform_study_id: str,
    payload: dict[str, Any],
    kind: str,
    payload_contract: str,
    version: str,
) -> dict[str, Any]:
    payload_hash = canonical_json_hash_ref(
        payload, schema_version=payload["contractVersion"]
    )
    artifact_id = str(
        uuid5(
            NAMESPACE_URL,
            f"accuratrials:osb-carrier-observation:{kind}:{tenant_id}:{platform_study_id}",
        )
    )
    version_id = str(uuid5(NAMESPACE_URL, f"{artifact_id}:{payload_hash['value']}"))
    # Only owner-captured artifacts are replay authority. Intake alone cannot add
    # this label or upgrade an uploaded descriptor into a native observation.
    rows, _ = db.cypher_query(
        """MATCH (artifact:OsbInboundCarrierArtifact:OsbCarrierNativeCapture {
             tenant_id:$tenant_id,platform_study_id:$platform_study_id,
             artifact_id:$artifact_id,artifact_version_id:$artifact_version_id})
           RETURN artifact.artifact_ref_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "artifact_id": artifact_id,
            "artifact_version_id": version_id,
        },
    )
    if rows:
        _require(len(rows) == 1, "OSB_CARRIER_NATIVE_CAPTURE_CONFLICT")
        reference = _parse_canonical(
            str(rows[0][0]).encode("utf-8"), "OSB_CARRIER_NATIVE_CAPTURE_INVALID"
        )
        retained = load_carrier_artifact(
            artifact_ref=reference,
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
        )
        _require(_same(retained, payload), "OSB_CARRIER_NATIVE_CAPTURE_CONFLICT")
        return reference
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    encoded = canonical_json(payload).encode("utf-8")
    reference = _artifact_ref(
        {
            "artifactId": artifact_id,
            "artifactVersionId": version_id,
            "kind": kind,
            "stableLocator": f"artifact://osb/carrier/{artifact_id}/{version_id}",
            "payloadHash": payload_hash,
            "byteSize": len(encoded),
            "classification": "regulated-non-phi",
            "tenantId": tenant_id,
            "region": "us-central1",
            "producerService": "osb.clinical-mdr-api",
            "producerEnvironment": "prototype",
            "producerVersion": "prototype",
            "payloadContract": payload_contract,
            "payloadContractVersion": version,
            "purpose": "osb-carrier-native-observation",
            "createdAt": now,
        }
    )
    store_carrier_artifact_bytes(
        artifact_ref=reference,
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        bytes_value=encoded,
        expected_hash=payload_hash["value"],
    )
    db.cypher_query(
        """MATCH (artifact:OsbInboundCarrierArtifact {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,artifact_id:$artifact_id,
             artifact_version_id:$artifact_version_id})
           SET artifact:OsbCarrierNativeCapture""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "artifact_id": artifact_id,
            "artifact_version_id": version_id,
        },
    )
    return reference


def capture_carrier_native_state(
    *,
    tenant_id: str,
    platform_study_id: str,
    input_payload: dict[str, Any],
    expected_target_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run inside the existing atomic signed command transaction."""
    required = {
        "contractVersion",
        "sourceBasis",
        "carrierArtifact",
        "osbBinding",
        "nativeEvidenceBasis",
        "osbStudyIdentity",
    }
    if (
        not isinstance(input_payload, dict)
        or set(input_payload) != required
        or input_payload.get("contractVersion") != CAPTURE_INPUT_SCHEMA
    ):
        raise OsbCandidateSetError(
            "OSB_CARRIER_CAPTURE_INPUT_INVALID", "Exact capture input required.", 422
        )
    inputs = load_carrier_observation_inputs(
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        source_basis=input_payload["sourceBasis"],
        carrier_artifact=input_payload["carrierArtifact"],
        osb_binding=input_payload["osbBinding"],
        native_evidence_basis=input_payload["nativeEvidenceBasis"],
    )
    arguments = {
        "tenant_id": tenant_id,
        "platform_study_id": platform_study_id,
        "osb_study_identity": input_payload["osbStudyIdentity"],
        **inputs,
    }
    observed = observe_carrier_native_state(**arguments)
    if expected_target_state is not None:
        _require_expected_target_state(expected_target_state, observed["stateHash"])

    def load(reference):
        return load_carrier_artifact(
            artifact_ref=reference,
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
        )

    crosswalk = load(inputs["carrier"]["crosswalkArtifact"])
    conservation = verify_carrier_terminal_accounting(
        bundle=inputs["bundle"],
        crosswalk=crosswalk,
        census=load(inputs["carrier"]["exportCensusArtifact"]),
        carrier=inputs["carrier"],
        authority=load(input_payload["sourceBasis"]["authorityArtifact"]),
        records=observed["records"],
        lineages=observed["lineages"],
    )
    definition_ref = _publish_native_observation(
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        payload=observed["canonicalDefinition"],
        kind="osb-canonical-study-definition",
        payload_contract="accuratrials.osb.OsbCanonicalStudyDefinitionV1",
        version="1.0.0",
    )
    state_ref = _publish_native_observation(
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        payload=observed["nativeState"],
        kind="osb-package-native-state",
        payload_contract="accuratrials.osb.OsbPackageNativeStateV1",
        version="1.2.0",
    )
    retained, _ = db.cypher_query(
        """MATCH (artifact:OsbInboundCarrierArtifact:OsbCarrierNativeCapture {
        tenant_id:$tenant_id,platform_study_id:$platform_study_id,
        artifact_id:$artifact_id,artifact_version_id:$artifact_version_id})
        SET artifact.carrier_conservation_json=coalesce(artifact.carrier_conservation_json,$conservation_json)
        RETURN artifact.carrier_conservation_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "artifact_id": state_ref["artifactId"],
            "artifact_version_id": state_ref["artifactVersionId"],
            "conservation_json": canonical_json(conservation),
        },
    )
    _require(
        retained == [[canonical_json(conservation)]],
        "OSB_CARRIER_CONSERVATION_CONFLICT",
    )
    prepare_carrier_inspection(
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        carrier_artifact=input_payload["carrierArtifact"],
        native_state_artifact=state_ref,
        canonical_definition_artifact=definition_ref,
        bundle_artifact=inputs["carrier"]["bundleArtifact"],
        crosswalk_artifact=inputs["carrier"]["crosswalkArtifact"],
        crosswalk=crosswalk,
        bundle=inputs["bundle"],
        records=observed["records"],
        canonical_definition=observed["canonicalDefinition"],
    )
    fresh = observe_carrier_native_state(**arguments)
    _require(
        hash_refs_equal(observed["stateHash"], fresh["stateHash"]),
        "OSB_POST_REVIEW_NATIVE_EDIT",
    )
    return {
        "nativeStateArtifact": state_ref,
        "canonicalDefinitionArtifact": definition_ref,
        "nativeStateHash": observed["stateHash"],
        "nativeStudyId": observed["root"]["nativeStudyId"],
        "nativeVersion": observed["root"]["nativeVersion"],
    }


def _captured_reference(tenant_id, platform_study_id, kind, payload_hash):
    artifact_id = str(
        uuid5(
            NAMESPACE_URL,
            f"accuratrials:osb-carrier-observation:{kind}:{tenant_id}:{platform_study_id}",
        )
    )
    version_id = str(uuid5(NAMESPACE_URL, f"{artifact_id}:{payload_hash['value']}"))
    rows, _ = db.cypher_query(
        """MATCH (artifact:OsbInboundCarrierArtifact:OsbCarrierNativeCapture {
        tenant_id:$tenant_id,platform_study_id:$platform_study_id,
        artifact_id:$artifact_id,artifact_version_id:$artifact_version_id})
        RETURN artifact.artifact_ref_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "artifact_id": artifact_id,
            "artifact_version_id": version_id,
        },
    )
    _require(len(rows) == 1, "OSB_CARRIER_OWNER_CAPTURE_REQUIRED")
    reference = _parse_canonical(
        str(rows[0][0]).encode("utf-8"), "OSB_CARRIER_NATIVE_CAPTURE_INVALID"
    )
    _require(
        hash_refs_equal(reference.get("payloadHash"), payload_hash),
        "OSB_CARRIER_NATIVE_CAPTURE_INVALID",
    )
    return reference


def _review_state(
    *,
    tenant_id,
    platform_study_id,
    transformation_checkpoint_artifact,
    osb_study_identity,
):
    def load(reference):
        return load_carrier_artifact(
            artifact_ref=reference,
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
        )

    checkpoint = load(transformation_checkpoint_artifact)
    validate_carrier_contract(checkpoint, "Checkpoint")
    state_ref = checkpoint["expectedNativeStateArtifact"]
    _require(
        _same(
            state_ref,
            _captured_reference(
                tenant_id,
                platform_study_id,
                "osb-package-native-state",
                state_ref["payloadHash"],
            ),
        ),
        "OSB_CARRIER_OWNER_CAPTURE_REQUIRED",
    )
    captured = load(state_ref)
    validate_carrier_contract(captured, "OsbNativeState12")
    definition_ref = _captured_reference(
        tenant_id,
        platform_study_id,
        "osb-canonical-study-definition",
        captured["canonicalDefinitionHash"],
    )
    definition = load(definition_ref)
    carrier = load(checkpoint["carrierArtifact"])
    validate_carrier_contract(carrier, "BundleCarrier")
    inspection = read_carrier_inspection_descriptor(
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        carrier_artifact=checkpoint["carrierArtifact"],
        native_state_artifact=state_ref,
        canonical_definition_artifact=definition_ref,
    )
    source_hash = canonical_json_hash_ref(
        checkpoint["sourceBasis"], schema_version=SOURCE_BASIS_SCHEMA
    )
    _require(
        hash_refs_equal(source_hash, checkpoint["sourceBasisHash"])
        and hash_refs_equal(source_hash, captured["sourceBasisHash"])
        and _same(checkpoint["scope"], carrier["scope"]),
        "OSB_CARRIER_SOURCE_BASIS_MISMATCH",
    )
    basis = checkpoint["nativeEvidenceBasis"]
    native_checkpoints = [
        (reference, load(reference))
        for reference in basis.get("checkpointArtifacts", [])
    ]
    arguments = {
        "tenant_id": tenant_id,
        "platform_study_id": platform_study_id,
        "scope": checkpoint["scope"],
        "source_basis": checkpoint["sourceBasis"],
        "osb_binding": checkpoint["osbBinding"],
        "osb_study_identity": osb_study_identity,
        "native_evidence_basis": basis,
        "carrier": carrier,
        "carrier_artifact": checkpoint["carrierArtifact"],
        "bundle": None,
        "native_checkpoints": native_checkpoints,
        "captured_state": captured,
    }
    state = observe_carrier_native_state(**arguments)
    _require(
        hash_refs_equal(state["stateHash"], state_ref["payloadHash"])
        and _same(definition, state["canonicalDefinition"]),
        "OSB_POST_REVIEW_NATIVE_EDIT",
    )
    conservation = checkpoint["conservation"]
    retained_conservation, _ = db.cypher_query(
        """MATCH (artifact:OsbInboundCarrierArtifact:OsbCarrierNativeCapture {
        tenant_id:$tenant_id,platform_study_id:$platform_study_id,
        artifact_id:$artifact_id,artifact_version_id:$artifact_version_id})
        RETURN artifact.carrier_conservation_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "artifact_id": state_ref["artifactId"],
            "artifact_version_id": state_ref["artifactVersionId"],
        },
    )
    _require(
        retained_conservation == [[canonical_json(conservation)]],
        "OSB_CARRIER_CONSERVATION_CONFLICT",
    )
    counts = conservation["counts"]
    _require(
        not checkpoint["blockers"]
        and all(
            counts[key] == 0
            for key in (
                "unmappedAtoms",
                "multiplyAssignedAtoms",
                "unsupportedExecutionAtoms",
            )
        )
        and counts["members"] == inspection["counts"]["crosswalkRows"]
        and counts["atoms"] == inspection["counts"]["terminalAtoms"]
        and inspection["counts"]["nativeRecords"] == len(state["records"])
        and (
            basis["kind"] != "no-native-operations"
            or counts["nativeObservedAtoms"] == 0
        )
        and hash_refs_equal(
            conservation["bundlePayloadHash"], carrier["bundleArtifact"]["payloadHash"]
        )
        and hash_refs_equal(
            conservation["exportCensusHash"],
            carrier["exportCensusArtifact"]["payloadHash"],
        ),
        "OSB_CARRIER_CHECKPOINT_RELEASE_BLOCKED",
    )
    execution = {
        "carrierArtifact": checkpoint["carrierArtifact"],
        "carrierHash": checkpoint["carrierArtifact"]["payloadHash"],
        "bundleArtifact": carrier["bundleArtifact"],
        "bundlePayloadHash": carrier["bundleArtifact"]["payloadHash"],
        "crosswalkArtifact": carrier["crosswalkArtifact"],
        "crosswalkHash": carrier["crosswalkArtifact"]["payloadHash"],
        "exportCensusArtifact": carrier["exportCensusArtifact"],
    }
    context = {
        "contractVersion": "OsbSpecialistReviewContextV2@2.0.0",
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "transformationCheckpointArtifact": transformation_checkpoint_artifact,
        "nativeStateArtifact": state_ref,
        "nativeStateHash": state["stateHash"],
        "nativeLockEvidence": require_native_lock(state),
        "canonicalDefinitionArtifact": definition_ref,
        "canonicalDefinitionHash": state["canonicalDefinitionHash"],
        "sourceBasisHash": source_hash,
        "authoredExecution": execution,
        "conservation": conservation,
        "conservationHash": canonical_json_hash_ref(
            conservation, schema_version="AuthoredExecutionConservationV1@1.0.0"
        ),
        "contentCounts": {
            "managedConcepts": sum(
                record["kind"] == "managed" for record in state["records"]
            ),
            "nativeObjects": sum(
                record["kind"] == "native" for record in state["records"]
            ),
            "crosswalkRows": counts["members"],
            "terminalAtoms": counts["atoms"],
        },
        "inspection": {
            key: inspection[key] for key in ("counts", "pageSizeLimit", "pageByteLimit")
        },
        "reviewScope": REVIEW_SCOPE,
        "productionEligible": False,
        "clinicalApproval": False,
    }
    validate_carrier_contract(context, "context", interface=True)
    return {
        "context": context,
        "checkpoint": checkpoint,
        "state": state,
        "arguments": arguments,
        "inspection": inspection,
    }


def _recheck_review_state(result):
    fresh = observe_carrier_native_state(**result["arguments"])
    _require(
        hash_refs_equal(fresh["stateHash"], result["state"]["stateHash"]),
        "OSB_POST_REVIEW_NATIVE_EDIT",
    )


def carrier_review_context(**arguments):
    result = _review_state(**arguments)
    _recheck_review_state(result)
    return result["context"]


def carrier_review_page(*, section, offset, limit, **arguments):
    result = _review_state(**arguments)
    page = read_carrier_inspection_page(
        tenant_id=arguments["tenant_id"],
        platform_study_id=arguments["platform_study_id"],
        index_key=result["inspection"]["indexKey"],
        section=section,
        offset=offset,
        limit=limit,
    )
    response = {
        **result["context"],
        **page,
        "contractVersion": "OsbSpecialistReviewPageV2@2.0.0",
    }
    # Account for the complete wire envelope, including all immutable pins.
    while (
        len(canonical_json({"ok": True, "data": response}).encode("utf-8"))
        > PAGE_BYTE_LIMIT
    ):
        if not response["items"]:
            raise OsbCandidateSetError(
                "OSB_CARRIER_INSPECTION_RESPONSE_LIMIT",
                "Inspection context exceeds the bounded response limit.",
                413,
            )
        if len(response["items"]) <= 1:
            entry = read_carrier_inspection_entry(
                tenant_id=arguments["tenant_id"],
                platform_study_id=arguments["platform_study_id"],
                index_key=result["inspection"]["indexKey"],
                section=section,
                position=offset,
            )
            error = OsbCandidateSetError(
                "OSB_CARRIER_INSPECTION_ENTRY_TOO_LARGE",
                "Exact indexed entry download required.",
                413,
            )
            error.details = {
                key: entry[key]
                for key in ("section", "position", "valueHash", "source")
            }
            raise error
        response["items"].pop()
        response["nextOffset"] = offset + len(response["items"])
    validate_carrier_contract(response, "page", interface=True)
    _recheck_review_state(result)
    return response


def carrier_review_entry(*, section, position, **arguments):
    result = _review_state(**arguments)
    entry = read_carrier_inspection_entry(
        tenant_id=arguments["tenant_id"],
        platform_study_id=arguments["platform_study_id"],
        index_key=result["inspection"]["indexKey"],
        section=section,
        position=position,
    )
    validate_carrier_contract(
        {key: entry[key] for key in ("section", "position", "valueHash", "source")},
        "entryProof",
        interface=True,
    )
    _recheck_review_state(result)
    return entry


def _release_artifact(
    payload,
    *,
    tenant_id,
    kind,
    contract,
    version,
    media_type,
    identity,
    version_id,
    purpose,
    timestamp,
):
    encoded = canonical_json(payload).encode("utf-8")
    payload_hash = (
        raw_bytes_hash_ref(
            encoded, schema_version=payload["contractVersion"], media_type=media_type
        )
        if kind == "osb-native-package-v2"
        else canonical_json_hash_ref(
            payload, schema_version=payload["contractVersion"], media_type=media_type
        )
    )
    return _artifact_ref(
        {
            "artifactId": identity,
            "artifactVersionId": version_id,
            "kind": kind,
            "stableLocator": f"artifact://osb/{kind}/{version_id}",
            "payloadHash": payload_hash,
            "byteSize": len(encoded),
            "classification": "regulated-non-phi",
            "tenantId": tenant_id,
            "region": "us-central1",
            "producerService": "osb.clinical-mdr-api",
            "producerEnvironment": "prototype",
            "producerVersion": "prototype",
            "payloadContract": contract,
            "payloadContractVersion": version,
            "purpose": purpose,
            "createdAt": timestamp,
        }
    )


def record_carrier_specialist_review(
    *, tenant_id, platform_study_id, input_payload, actor, expected_target_state
):
    validate_carrier_contract(input_payload, "commandInput", successor=True)
    result = _review_state(
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        transformation_checkpoint_artifact=input_payload[
            "transformationCheckpointArtifact"
        ],
        osb_study_identity=input_payload["osbStudyIdentity"],
    )
    context = result["context"]
    _require_expected_target_state(expected_target_state, context["nativeStateHash"])
    commitments = (
        "nativeStateHash",
        "sourceBasisHash",
        "authoredExecution",
        "conservationHash",
        "reviewScope",
    )
    _require(
        all(_same(input_payload[key], context[key]) for key in commitments),
        "OSB_SPECIALIST_REVIEW_CONTEXT_MISMATCH",
    )
    text_fields = {
        key: input_payload[key] for key in ("displayedStatement", "meaning", "reason")
    }
    _require(
        isinstance(actor, str)
        and bool(actor.strip())
        and all(value.strip() for value in text_fields.values()),
        "OSB_SPECIALIST_REVIEW_STATEMENT_REQUIRED",
    )
    seed = canonical_json_hash_ref(
        {
            "checkpointArtifact": input_payload["transformationCheckpointArtifact"],
            **{key: context[key] for key in commitments},
            "specialistSubject": actor,
            **text_fields,
        },
        schema_version="OsbSpecialistReviewSeedV2@2.0.0",
    )
    review_id = str(
        uuid5(NAMESPACE_URL, f"accuratrials:osb-specialist-review:v2:{seed['value']}")
    )
    version_id = str(
        uuid5(NAMESPACE_URL, f"{review_id}:{context['nativeStateHash']['value']}")
    )
    timestamp = (
        datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
    root = result["state"]["root"]
    review = {
        "contractVersion": "OsbSpecialistReviewEvidenceV2@2.0.0",
        "reviewId": review_id,
        "reviewVersionId": version_id,
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "osbStudyIdentity": {
            "nativeIdentity": root["nativeStudyId"],
            "nativeVersion": root["nativeVersion"],
        },
        "transformationCheckpointArtifact": context["transformationCheckpointArtifact"],
        "transformationCheckpointHash": context["transformationCheckpointArtifact"][
            "payloadHash"
        ],
        **{key: context[key] for key in commitments},
        "osbAuthorityHash": context["nativeStateHash"],
        "nativeLockEvidence": context["nativeLockEvidence"],
        "canonicalDefinitionHash": context["canonicalDefinitionHash"],
        "managedConceptCount": context["contentCounts"]["managedConcepts"],
        "nativeObjectCount": context["contentCounts"]["nativeObjects"],
        "specialistSubject": actor,
        **text_fields,
        "lockState": "checkpoint-locked",
        "productionEligible": False,
        "clinicalApproval": False,
        "reviewedAt": timestamp,
    }
    artifact = _release_artifact(
        review,
        tenant_id=tenant_id,
        kind="osb-specialist-review-evidence",
        contract="accuratrials.osb.OsbSpecialistReviewEvidenceV2",
        version="2.0.0",
        media_type=SPECIALIST_V2_MEDIA_TYPE,
        identity=review_id,
        version_id=version_id,
        purpose="osb-specialist-review-lock",
        timestamp=timestamp,
    )
    validate_carrier_payload(review, artifact, tenant_id, platform_study_id)
    rows, _ = db.cypher_query(
        """MATCH (review:OsbSpecialistReviewEvidenceV2 {tenant_id:$tenant_id,
        platform_study_id:$platform_study_id,review_version_id:$version_id})
        RETURN review.payload_json,review.artifact_ref_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "version_id": version_id,
        },
    )
    _recheck_review_state(result)
    if rows:
        _require(len(rows) == 1, "OSB_SPECIALIST_REVIEW_REPLAY_CONFLICT")
        retained = _parse_canonical(
            str(rows[0][0]).encode("utf-8"), "OSB_SPECIALIST_REVIEW_STORED_INVALID"
        )
        retained_ref = _parse_canonical(
            str(rows[0][1]).encode("utf-8"), "OSB_SPECIALIST_REVIEW_STORED_INVALID"
        )
        validate_carrier_payload(retained, retained_ref, tenant_id, platform_study_id)
        _require(
            _same(
                {key: value for key, value in retained.items() if key != "reviewedAt"},
                {key: value for key, value in review.items() if key != "reviewedAt"},
            ),
            "OSB_SPECIALIST_REVIEW_REPLAY_CONFLICT",
        )
        return {
            "payload": retained,
            "payloadHash": retained_ref["payloadHash"],
            "artifactRef": retained_ref,
            "replay": True,
        }
    db.cypher_query(
        """CREATE (review:OsbSpecialistReviewEvidenceV2 {review_id:$review_id,
        review_version_id:$version_id,tenant_id:$tenant_id,platform_study_id:$platform_study_id,
        payload_hash:$payload_hash,payload_json:$payload_json,artifact_ref_json:$artifact_ref_json,created_at:datetime()})""",
        {
            "review_id": review_id,
            "version_id": version_id,
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "payload_hash": artifact["payloadHash"]["value"],
            "payload_json": canonical_json(review),
            "artifact_ref_json": canonical_json(artifact),
        },
    )
    return {
        "payload": review,
        "payloadHash": artifact["payloadHash"],
        "artifactRef": artifact,
        "replay": False,
    }


def _recorded_carrier_review(tenant_id, platform_study_id, reference):
    rows, _ = db.cypher_query(
        """MATCH (review:OsbSpecialistReviewEvidenceV2 {tenant_id:$tenant_id,
        platform_study_id:$platform_study_id,review_version_id:$version_id})
        RETURN review.payload_json,review.artifact_ref_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "version_id": reference["artifactVersionId"],
        },
    )
    _require(len(rows) == 1, "OSB_SPECIALIST_REVIEW_NOT_FOUND")
    review = _parse_canonical(
        str(rows[0][0]).encode("utf-8"), "OSB_SPECIALIST_REVIEW_STORED_INVALID"
    )
    _require(
        canonical_json(reference) == str(rows[0][1]),
        "OSB_SPECIALIST_REVIEW_BINDING_MISMATCH",
    )
    validate_carrier_payload(review, reference, tenant_id, platform_study_id)
    return review


def generate_carrier_native_package(
    *, tenant_id, platform_study_id, input_payload, actor, expected_target_state
):
    validate_carrier_contract(input_payload, "releaseInput", interface=True)
    result = _review_state(
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        transformation_checkpoint_artifact=input_payload[
            "transformationCheckpointArtifact"
        ],
        osb_study_identity=input_payload["osbStudyIdentity"],
    )
    context, state, checkpoint = (
        result["context"],
        result["state"],
        result["checkpoint"],
    )
    _require_expected_target_state(expected_target_state, context["nativeStateHash"])

    def load(reference):
        return load_carrier_artifact(
            artifact_ref=reference,
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
        )

    manifest_ref, approval_ref, review_ref = (
        input_payload[key]
        for key in (
            "platformManifestArtifact",
            "preReleaseApprovalArtifact",
            "specialistReviewArtifact",
        )
    )
    manifest, approval = load(manifest_ref), load(approval_ref)
    _require(
        manifest_ref["kind"] == "platform-manifest-v1"
        and approval_ref["kind"] == "pre-release-approval-v2"
        and review_ref["payloadContract"]
        == "accuratrials.osb.OsbSpecialistReviewEvidenceV2",
        "OSB_CARRIER_RELEASE_PROFILE_MISMATCH",
    )
    validate_carrier_contract(approval, "approval", successor=True)
    review = _recorded_carrier_review(tenant_id, platform_study_id, review_ref)
    _require(
        all(
            _same(review[key], context[key])
            for key in (
                "transformationCheckpointArtifact",
                "nativeStateHash",
                "sourceBasisHash",
                "authoredExecution",
                "conservationHash",
                "reviewScope",
                "nativeLockEvidence",
                "canonicalDefinitionHash",
            )
        ),
        "OSB_SPECIALIST_REVIEW_BINDING_MISMATCH",
    )
    expected_approval = {
        "transformation_checkpoint_hash": context["transformationCheckpointArtifact"][
            "payloadHash"
        ]["value"],
        "transformation_checkpoint_artifact": context[
            "transformationCheckpointArtifact"
        ],
        "osb_authority_hash": state["stateHash"]["value"],
        "osb_authority_version": state["root"]["nativeVersion"],
        "specialist_review_evidence_hash": review_ref["payloadHash"]["value"],
        "platform_manifest_hash": manifest_ref["payloadHash"]["value"],
        "native_state_hash": context["nativeStateHash"],
        "source_basis_hash": context["sourceBasisHash"],
        "authored_execution": context["authoredExecution"],
        "conservation_hash": context["conservationHash"],
        "review_scope": REVIEW_SCOPE,
    }
    _require(
        all(_same(approval[key], value) for key, value in expected_approval.items()),
        "OSB_PRE_RELEASE_APPROVAL_BINDING_MISMATCH",
    )
    authority = load(checkpoint["sourceBasis"]["authorityArtifact"])
    basis = approval["readiness_basis"]
    _require(
        _same(basis["scope"], checkpoint["scope"])
        and basis["sourceBasisKind"] == checkpoint["sourceBasis"]["kind"]
        and basis["acceptedSnapshotId"] == authority["snapshot"]["snapshotId"]
        and _same(
            basis["acceptedSnapshotManifestHash"], authority["snapshot"]["manifestHash"]
        )
        and _same(
            basis["acceptedProjectionPackageHash"],
            authority["snapshot"]["projectionPackageHash"],
        )
        and _same(
            basis["authorityHash"],
            checkpoint["sourceBasis"]["authorityArtifact"]["payloadHash"],
        )
        and _same(basis["conservationHash"], context["conservationHash"])
        and _same(basis["counts"], context["conservation"]["counts"])
        and _same(basis["sourceEpochHash"], approval["source_epoch_hash"]),
        "OSB_PRE_RELEASE_APPROVAL_READINESS_INVALID",
    )
    human = approval["human_signature"]
    _require(
        human["record_hash"]
        == sha256_bytes(
            canonical_json(
                {
                    key: value
                    for key, value in approval.items()
                    if key != "human_signature"
                }
            )
        )
        and human["displayed_statement"] == approval["displayed_readiness_statement"]
        and all(
            human[key] == approval[key] and approval[key].strip()
            for key in ("meaning", "reason")
        ),
        "OSB_PRE_RELEASE_HUMAN_SIGNATURE_INVALID",
    )
    content = _package_content({**state, "request": {}})
    content["captureDesign"]["authoredExecution"] = context["authoredExecution"]
    content["capabilityManifest"]["executionMode"] = "accepted-canonical-carrier/1.0.0"
    lineage = state["lineages"][0] if len(state["lineages"]) == 1 else None
    prior_state = lineage["state"] if lineage else {}
    prior_checkpoint = lineage["checkpoint"] if lineage else {}
    prior_request = prior_state.get("request") or {}
    seed = canonical_json_hash_ref(
        {
            "prerequisites": {
                key: input_payload[key]
                for key in (
                    "transformationCheckpointArtifact",
                    "platformManifestArtifact",
                    "preReleaseApprovalArtifact",
                    "specialistReviewArtifact",
                )
            },
            "nativeStateHash": state["stateHash"],
            "actor": actor,
        },
        schema_version="OsbNativePackageV2Seed@2.3.0",
    )
    package_id = str(
        uuid5(NAMESPACE_URL, f"accuratrials:osb-native-package-v2:2.3:{seed['value']}")
    )
    version_id = str(
        uuid5(NAMESPACE_URL, f"{package_id}:{state['stateHash']['value']}")
    )
    timestamp = (
        datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
    package = {
        "contractVersion": "OsbNativePackageV2@2.3.0",
        "packageId": package_id,
        "packageVersionId": version_id,
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "osbStudyIdentity": {
            "nativeIdentity": state["root"]["nativeStudyId"],
            "nativeVersion": state["root"]["nativeVersion"],
        },
        "releasedVersion": state["root"]["nativeVersion"],
        "authorityHash": state["stateHash"],
        "sourceBasis": checkpoint["sourceBasis"],
        "sourceBasisHash": context["sourceBasisHash"],
        "transformationCheckpointArtifact": context["transformationCheckpointArtifact"],
        "transformationCheckpointHash": context["transformationCheckpointArtifact"][
            "payloadHash"
        ],
        "specialistReviewLockReceipt": review_ref,
        "preReleaseApproval": approval_ref,
        "platformManifest": manifest_ref,
        **(
            {"studyStandards": manifest["studyStandards"]}
            if "studyStandards" in manifest
            else {}
        ),
        "profiles": {
            "projectionRuleset": prior_request.get("projectionRuleset"),
            "exclusionPolicy": prior_checkpoint.get("exclusionPolicy"),
            "nativeState": CARRIER_STATE_SCHEMA,
        },
        **content,
        "conservation": context["conservation"],
        "provenancePins": {
            "candidateRequestId": prior_request.get("requestId"),
            "candidateRequestHash": prior_state.get("requestHash"),
            "candidateSetHash": prior_state.get("candidateSetHash"),
            "decisionHash": prior_state.get("decisionHash"),
            "nativeStateHash": state["stateHash"],
            "canonicalDefinitionHash": state["canonicalDefinitionHash"],
            "sourceFactPackageHash": (prior_request.get("sourceFactPackage") or {}).get(
                "payloadHash"
            ),
            "nativeEvidenceSetHash": prior_checkpoint.get("nativeEvidenceSetHash"),
            "sourceBasisHash": context["sourceBasisHash"],
            **{
                key: context["authoredExecution"][key]
                for key in ("carrierHash", "bundlePayloadHash", "crosswalkHash")
            },
        },
        "productionEligible": False,
        "createdAt": timestamp,
        "createdBy": actor,
    }
    artifact = _release_artifact(
        package,
        tenant_id=tenant_id,
        kind="osb-native-package-v2",
        contract="accuratrials.osb.OsbNativePackageV2",
        version="2.3.0",
        media_type=PACKAGE_MEDIA_TYPE,
        identity=package_id,
        version_id=version_id,
        purpose="edc-deployment",
        timestamp=timestamp,
    )
    validate_carrier_payload(package, artifact, tenant_id, platform_study_id)
    prior, _ = db.cypher_query(
        """MATCH (package:OsbNativePackageV2 {tenant_id:$tenant_id,
        platform_study_id:$platform_study_id,package_version_id:$version_id})
        RETURN package.payload_json,package.artifact_ref_json""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "version_id": version_id,
        },
    )
    _recheck_review_state(result)
    if prior:
        _require(len(prior) == 1, "OSB_PACKAGE_REPLAY_CONFLICT")
        retained = _parse_canonical(
            str(prior[0][0]).encode("utf-8"), "OSB_PACKAGE_REPLAY_INVALID"
        )
        retained_ref = _parse_canonical(
            str(prior[0][1]).encode("utf-8"), "OSB_PACKAGE_REPLAY_INVALID"
        )
        validate_carrier_payload(retained, retained_ref, tenant_id, platform_study_id)
        _require(
            _same(
                {key: value for key, value in retained.items() if key != "createdAt"},
                {key: value for key, value in package.items() if key != "createdAt"},
            ),
            "OSB_PACKAGE_REPLAY_CONFLICT",
        )
        return {
            "payload": retained,
            "bytes": canonical_json(retained).encode("utf-8"),
            "payloadHash": retained_ref["payloadHash"],
            "artifactRef": retained_ref,
            "packageVersionId": version_id,
            "replay": True,
        }
    encoded = canonical_json(package).encode("utf-8")
    db.cypher_query(
        """CREATE (package:OsbNativePackageV2 {package_id:$package_id,
        package_version_id:$version_id,tenant_id:$tenant_id,platform_study_id:$platform_study_id,
        payload_hash:$payload_hash,payload_json:$payload_json,byte_size:$byte_size,
        artifact_ref_json:$artifact_ref_json,created_at:datetime()})""",
        {
            "package_id": package_id,
            "version_id": version_id,
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "payload_hash": artifact["payloadHash"]["value"],
            "payload_json": encoded.decode("utf-8"),
            "byte_size": len(encoded),
            "artifact_ref_json": canonical_json(artifact),
        },
    )
    return {
        "payload": package,
        "bytes": encoded,
        "payloadHash": artifact["payloadHash"],
        "artifactRef": artifact,
        "packageVersionId": version_id,
        "replay": False,
    }
