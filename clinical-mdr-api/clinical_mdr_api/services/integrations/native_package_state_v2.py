"""Read-only, checkpoint-bound inventory for Package V2.

Native V1 observes library identity/name, not study properties or associations.
Keep those exact observations and their source/evidence; never upgrade them to
field projection or lock evidence. New materialization profiles must add their
own explicit read-back verifier here.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
    hash_refs_equal,
)
from clinical_mdr_api.services.integrations.candidate_set import (
    OsbCandidateSetError,
    _assert_identity_binding,
    active_osb_binding,
)
from clinical_mdr_api.services.integrations.mapping_decision_v1 import (
    _read_native_target,
)
from clinical_mdr_api.services.integrations.native_capture_mapping import (
    assert_selected_capture_identity,
    read_capture_target,
)
from clinical_mdr_api.services.integrations.native_capture_projection import (
    CAPTURE_READBACK_SCHEMA, capture_field_receipts,
)
from clinical_mdr_api.services.integrations.native_study_head import select_current_study_head
from clinical_mdr_api.services.integrations.osb_candidate_request_versions import (
    ACCEPTED_REQUEST_CONTRACT_VERSIONS,
    METADATA_REQUEST_CONTRACT_VERSIONS,
    SELECTED_CAPTURE_REQUEST_CONTRACT_VERSIONS,
)
from clinical_mdr_api.services.integrations.osb_family_map import canonicalize_family
from clinical_mdr_api.services.integrations.study_metadata_mapping import METADATA_PATHS
from clinical_mdr_api.services.integrations.study_metadata_mapping import (
    _normalized as normalize_metadata_value,
)
from clinical_mdr_api.services.integrations.study_metadata_mapping import (
    read_metadata_target,
    verify_metadata_reference_bindings,
)

NATIVE_SCHEMA = "OsbNativeTargetReadBackV1@1.0.0"
METADATA_SCHEMA = "OsbStudyMetadataReadBackV1@1.0.0"
MANAGED_SCHEMA = "OsbManagedStudyConceptV1@1.0.0"
STATE_SCHEMA = "OsbPackageNativeStateV1@1.0.0"
EVIDENCE_MEDIA = "application/vnd.accuratrials.osb-native-evidence-set-v1+json"
REQUEST_MEDIA = "application/vnd.accuratrials.osb-candidate-request-v1+json"
CANDIDATE_MEDIA = "application/vnd.accuratrials.osb-candidate-set-v1+json"


def _require(
    condition: bool, code: str = "OSB_PACKAGE_CHECKPOINT_CONTENT_MISMATCH"
) -> None:
    if not condition:
        raise OsbCandidateSetError(
            code, "Native package evidence, scope or content differs.", 409
        )


def _record(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict))
    return value


def _list(value: Any) -> list[Any]:
    _require(isinstance(value, list))
    return value


def _text(value: Any) -> str:
    _require(isinstance(value, str) and bool(value))
    return value


def _json(value: Any) -> dict[str, Any]:
    _require(isinstance(value, str))
    try:
        parsed = json.loads(value)
        _require(isinstance(parsed, dict) and canonical_json(parsed) == value)
    except (ValueError, UnicodeError) as error:
        raise OsbCandidateSetError(
            "OSB_PACKAGE_STORED_BYTES_INVALID", "Retained JSON is not canonical.", 422
        ) from error
    return parsed


def _hash(value: Any, schema: str, media: str = "application/json") -> dict[str, Any]:
    return canonical_json_hash_ref(value, schema_version=schema, media_type=media)


def _same(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def _one(rows: list[Any]) -> Any:
    _require(len(rows) == 1, "OSB_PACKAGE_CUSTODY_UNAVAILABLE")
    return rows[0]


def _scope(value: dict[str, Any], tenant_id: str, platform_study_id: str) -> None:
    _require(
        value.get("tenantId") == tenant_id
        and value.get("platformStudyId") == platform_study_id,
        "OSB_PACKAGE_STUDY_MISMATCH",
    )


def _key(value: dict[str, Any]) -> str:
    _require(
        isinstance(value.get("revision"), int)
        and not isinstance(value["revision"], bool)
        and value["revision"] >= 1
    )
    return f"{_text(value.get('factId'))}@{value['revision']}:{_text(value.get('targetKey'))}"


def _members(values: Any) -> dict[str, dict[str, Any]]:
    entries = [_record(value) for value in _list(values)]
    result = {_key(value): value for value in entries}
    _require(len(result) == len(entries), "OSB_PACKAGE_EVIDENCE_MEMBERSHIP_MISMATCH")
    return result


def _custody(
    tenant_id: str, platform_study_id: str, checkpoint: dict[str, Any]
) -> dict[str, Any]:
    _scope(checkpoint, tenant_id, platform_study_id)
    _require(checkpoint.get("contractVersion") == "TransformationCheckpointV1@1.0.0")
    expected = _record(checkpoint.get("nativeEvidenceSetHash"))
    rows, _ = db.cypher_query(
        """MATCH (evidence:OsbNativeEvidenceSetV1 {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,payload_hash:$payload_hash})
             -[:EXECUTED_DECISION]->(decision:StudyMappingDecisionV1 {
               tenant_id:$tenant_id,platform_study_id:$platform_study_id})
           RETURN evidence.payload_json,evidence.artifact_ref_json,decision.payload_json,
                  evidence.decision_hash,decision.decision_hash,evidence.evidence_set_version_id""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "payload_hash": _text(expected.get("value")),
        },
    )
    row = _one(rows)
    evidence, artifact, decision = map(_json, row[:3])
    _scope(evidence, tenant_id, platform_study_id)
    _require(
        evidence.get("contractVersion") == "OsbNativeEvidenceSetV1@1.0.0"
        and hash_refs_equal(
            _hash(evidence, "OsbNativeEvidenceSetV1@1.0.0", EVIDENCE_MEDIA), expected
        )
    )
    fields = {
        key: value
        for key, value in artifact.items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    _require(
        artifact.get("contractVersion") == "ArtifactRefV1@1.0.0"
        and artifact.get("kind") == "osb-native-evidence-set"
        and artifact.get("tenantId") == tenant_id
        and artifact.get("artifactId") == evidence.get("evidenceSetId")
        and artifact.get("artifactVersionId")
        == evidence.get("evidenceSetVersionId")
        == row[5]
        and artifact.get("payloadContract") == "accuratrials.osb.OsbNativeEvidenceSetV1"
        and artifact.get("payloadContractVersion") == "1.0.0"
        and artifact.get("byteSize") == len(canonical_json(evidence).encode("utf-8"))
        and hash_refs_equal(artifact.get("payloadHash"), expected)
        and hash_refs_equal(
            artifact.get("descriptorHash"),
            descriptor_hash(
                {"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}
            ),
        )
    )
    statement = _record(decision.get("statement"))
    _scope(statement, tenant_id, platform_study_id)
    decision_hash = _hash(decision, "StudyMappingDecisionV1@1.0.0")
    _require(
        decision.get("contractVersion") == "StudyMappingDecisionV1@1.0.0"
        and statement.get("contractVersion") == "StudyMappingDecisionStatementV1@1.0.0"
        and row[3] == row[4] == decision_hash["value"]
        and hash_refs_equal(evidence.get("decisionHash"), decision_hash)
    )
    candidate_hash = _record(statement.get("candidateSetHash"))
    rows, _ = db.cypher_query(
        """MATCH (candidate:OsbCandidateSetV1 {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,payload_hash:$payload_hash})
             -[:GENERATED_FROM]->(request:OsbCandidateRequestV1 {
               tenant_id:$tenant_id,platform_study_id:$platform_study_id})
           RETURN candidate.payload_json,request.payload_json,candidate.candidate_set_version_id""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "payload_hash": candidate_hash.get("value"),
        },
    )
    row = _one(rows)
    candidate, request = map(_json, row[:2])
    _scope(candidate, tenant_id, platform_study_id)
    _scope(request, tenant_id, platform_study_id)
    _require(
        request.get("contractVersion")
        in ACCEPTED_REQUEST_CONTRACT_VERSIONS
    )
    request_hash = _hash(request, request["contractVersion"], REQUEST_MEDIA)
    _require(
        candidate.get("contractVersion") == "OsbCandidateSetV1@1.0.0"
        and candidate.get("candidateSetVersionId") == row[2]
        and hash_refs_equal(
            _hash(candidate, "OsbCandidateSetV1@1.0.0", CANDIDATE_MEDIA), candidate_hash
        )
        and hash_refs_equal(evidence.get("candidateSetHash"), candidate_hash)
        and hash_refs_equal(statement.get("candidateRequestHash"), request_hash)
        and _record(candidate.get("request")).get("requestVersionId")
        == request.get("requestVersionId")
        and hash_refs_equal(candidate["request"].get("payloadHash"), request_hash)
    )
    native = _record(checkpoint.get("osbStudyIdentity"))
    identity = _record(statement.get("osbStudyIdentity"))
    _require(
        identity == candidate.get("osbStudyIdentity") == request.get("osbStudyIdentity")
        and native == evidence.get("nativeStudyIdentity")
        and native
        == {
            "nativeIdentity": identity.get("nativeIdentity"),
            "nativeVersion": identity.get("nativeVersion"),
        },
        "OSB_PACKAGE_STUDY_MISMATCH",
    )
    _require(
        hash_refs_equal(
            checkpoint.get("semanticSnapshotHash"),
            statement.get("semanticSnapshotHash"),
        )
        and hash_refs_equal(
            checkpoint.get("semanticSnapshotHash"),
            _record(request.get("semanticSnapshot")).get("payloadHash"),
        )
        and hash_refs_equal(
            checkpoint.get("decisionSetHash"), statement.get("decisionSetHash")
        )
        and hash_refs_equal(
            statement.get("decisionSetHash"),
            _hash(statement.get("selections"), "StudyMappingSelectionSetV1@1.0.0"),
        )
    )
    authority = _record(checkpoint.get("osbAuthority"))
    managed = _record(authority.get("managedTargetCheckpoint"))
    managed_hash = _hash(managed, "OsbManagedTargetCheckpointV1@1.0.0")
    _require(
        managed == evidence.get("managedTargetCheckpoint")
        and hash_refs_equal(managed_hash, authority.get("managedTargetCheckpointHash"))
        and hash_refs_equal(managed_hash, evidence.get("managedTargetCheckpointHash"))
        and managed.get("nativeStudyId") == native.get("nativeIdentity")
        and managed.get("nativeVersion") == native.get("nativeVersion")
    )
    binding = active_osb_binding(tenant_id, platform_study_id)
    _assert_identity_binding(
        identity,
        binding,
        tenant_id=tenant_id,
        platform_study_id=platform_study_id,
        error_code="OSB_PACKAGE_STUDY_MISMATCH",
    )
    return {
        "evidence": evidence,
        "decision": decision,
        "candidate": candidate,
        "request": request,
        "binding": binding,
        "requestHash": request_hash,
        "decisionHash": decision_hash,
    }


def _current_study(
    tenant_id: str, platform_study_id: str, native: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows, _ = db.cypher_query(
        """MATCH (binding:PlatformNativeStudyBinding {tenant_id:$tenant_id,
             platform_study_id:$platform_study_id,namespace:'accuratrials-osb',
             object_type:'study-draft-root',status:'active',native_study_id:$study_uid})
           MATCH (:DomainStudyScope {tenant_id:$tenant_id,status:'active',study_uid:$study_uid})
           MATCH (study:StudyRoot {uid:$study_uid})-[:LATEST]->(latest:StudyValue)
           MATCH (study)-[head:LATEST_DRAFT|LATEST_LOCKED|LATEST_RELEASED]->(value:StudyValue)
           RETURN binding.binding_id,study.uid,type(head),head.version,head.status,
                  value.study_title,value.study_number,value.study_acronym,value.project_number,
                  toString(head.start_date),toString(head.end_date),value=latest""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "study_uid": _text(native.get("nativeIdentity")),
        },
    )
    _require(bool(rows), "OSB_PACKAGE_STUDY_MISMATCH")
    try:
        head = select_current_study_head([
            {
                "relationship": item[2], "version": item[3], "status": item[4],
                "startDate": item[9], "endDate": item[10], "isLatest": item[11],
                "row": item,
            }
            for item in rows
        ])
    except ValueError as error:
        raise OsbCandidateSetError(
            "OSB_PACKAGE_STUDY_MISMATCH", "The native study current head differs.", 409
        ) from error
    row = head["row"]
    status, version = head["nativeStatus"], head["nativeVersion"]
    _require(version == native.get("nativeVersion"), "OSB_PACKAGE_STUDY_MISMATCH")
    root = {
        "bindingId": row[0],
        "nativeStudyId": row[1],
        "relationship": row[2],
        "nativeVersion": version,
        "nativeStatus": status,
        "versionTimestamp": row[9],
        "draftEndedAt": head["draftEndedAt"],
        "title": row[5],
        "studyNumber": row[6],
        "acronym": row[7],
        "projectNumber": row[8],
    }
    rows, _ = db.cypher_query(
        """MATCH (:StudyRoot {uid:$study_uid})-[:HAS_PLATFORM_MANAGED_CONCEPT]->(concept:PlatformManagedStudyConcept {
             tenant_id:$tenant_id,platform_study_id:$platform_study_id})
           RETURN concept.managed_key,concept.resource_family,concept.payload_json,
                  concept.content_hash,concept.version,concept.fact_id,concept.revision,concept.target_key
           ORDER BY concept.managed_key""",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "study_uid": native["nativeIdentity"],
        },
    )
    concepts = [
        {
            "managedKey": item[0],
            "resourceFamily": item[1],
            "payload": _json(item[2]),
            "contentHash": item[3],
            "nativeVersion": str(item[4]),
            "factId": item[5],
            "revision": item[6],
            "targetKey": item[7],
        }
        for item in rows
    ]
    return root, concepts


def _check_census(checkpoint: dict[str, Any], evidence: dict[str, Any]) -> None:
    wrappers = _list(evidence.get("evidenceRecords"))
    members = [_record(item) for item in _list(checkpoint.get("receiptMembership"))]
    member_map = {_text(item.get("operationId")): item for item in members}
    census = _record(checkpoint.get("conservation"))
    rows = [_record(item) for item in _list(census.get("rows"))]
    counts = _record(census.get("counts"))
    _require(
        len(members) == len(member_map) == len(wrappers) == len(rows)
        and hash_refs_equal(
            checkpoint.get("receiptSetHash"),
            _hash(members, "TransformationReceiptSetV1@1.0.0"),
        )
        and hash_refs_equal(
            census.get("rowSetHash"), _hash(rows, "ConservationCensusRowsV1@1.0.0")
        )
        and all(
            isinstance(counts.get(field), int) and not isinstance(counts[field], bool)
            for field in ("source", "target", "dropped")
        )
        and counts.get("source") == counts.get("target") == len(wrappers)
        and counts.get("dropped") == 0,
        "OSB_PACKAGE_EVIDENCE_MEMBERSHIP_MISMATCH",
    )
    source_map = {
        _record(wrapper.get("evidence")).get("operationId"): (index, wrapper)
        for index, wrapper in enumerate(wrappers)
    }
    _require(
        len(source_map) == len(wrappers) and set(member_map) == set(source_map),
        "OSB_PACKAGE_EVIDENCE_MEMBERSHIP_MISMATCH",
    )
    seen = set()
    disposition_counts: dict[str, int] = {}
    for index, row in enumerate(rows):
        operation_id = row.get("unitId")
        _require(operation_id in member_map and operation_id not in seen)
        seen.add(operation_id)
        source_index, wrapper = source_map[operation_id]
        member = members[index]
        disposition = wrapper["evidence"]["disposition"]
        _require(
            member.get("operationId") == operation_id
            and row.get("disposition") == disposition
            and row.get("sourcePath") == f"/evidenceRecords/{source_index}"
            and row.get("targetPath") == f"/transformationReceipts/{index}"
            and _same(row.get("multiplicity"), {"source": 1, "target": 1})
            and _same(
                row.get("ordering"),
                {
                    "significant": True,
                    "sourceIndex": source_index,
                    "targetIndex": index,
                },
            )
            and hash_refs_equal(row.get("sourceValueHash"), wrapper.get("payloadHash"))
            and hash_refs_equal(row.get("targetValueHash"), member.get("payloadHash"))
        )
        disposition_counts[disposition] = disposition_counts.get(disposition, 0) + 1
    _require(_same(counts.get("byDisposition"), disposition_counts))


def _read_checkpoint_target(family: str, selected: dict[str, Any]) -> dict[str, Any]:
    if canonicalize_family(family) == "study_metadata":
        return read_metadata_target(selected)
    if "bindingKey" in selected:
        return read_capture_target(selected)
    return _read_native_target(family, selected)


def _metadata_offer(
    intent: dict[str, Any], candidate: dict[str, Any], native_study: dict[str, Any]
) -> dict[str, Any]:
    plan = _record(intent.get("nativeStudyOperation"))
    option = _record(candidate.get("createOption"))
    offer = _record(option.get("nativeStudyOperation"))
    before = _record(offer.get("nativePreconditionHash"))
    digest = _text(before.get("value"))
    hash_shape = {
        **_hash(None, "OsbStudyMetadataPreconditionV1@1.0.0"),
        "value": digest,
    }
    _require(
        plan.get("contractVersion") == "OsbStudyMetadataPlanV1@1.0.0"
        and plan.get("kind") == "native-metadata"
        and plan.get("osbResourceType") == "StudyMetadata"
        and plan.get("method") == "PATCH"
        and plan.get("route") == "/studies/{study_uid}"
        and plan.get("metadataPath") in METADATA_PATHS
        and "metadataValue" in plan
        and option.get("allowed") is True
        and option.get("requestedNativeType") == "StudyMetadata"
        and offer.get("contractVersion") == "OsbStudyMetadataOfferV1@1.0.0"
        and offer.get("nativeStudyId") == native_study.get("nativeIdentity")
        and offer.get("metadataPath") == plan.get("metadataPath")
        and "metadataValue" in offer
        and offer.get("joinedText") is (plan.get("metadataJoinedText") is True)
        and offer.get("multiValued") is (plan.get("metadataMultiValued") is True)
        and isinstance(offer.get("referenceBindings"), list)
        and hash_refs_equal(
            offer.get("sourcePlanHash"), _hash(plan, "OsbStudyMetadataPlanV1@1.0.0")
        )
        and hash_refs_equal(
            option.get("nativeStudyOperationHash"),
            _hash(offer, "OsbStudyMetadataOfferV1@1.0.0"),
        )
        and digest.startswith("sha256:")
        and len(digest) == 71
        and all(char in "0123456789abcdef" for char in digest[7:])
        and hash_refs_equal(before, hash_shape),
        "OSB_PACKAGE_METADATA_OFFER_MISMATCH",
    )
    return offer


def _metadata_values(
    selections: dict[str, Any],
    intents: dict[str, Any],
    candidates: dict[str, Any],
    native_study: dict[str, Any],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for key, selection in selections.items():
        if (
            intents[key].get("resourceFamily") == "study_metadata"
            and selection.get("action") == "create"
        ):
            offer = _metadata_offer(intents[key], candidates[key], native_study)
            grouped.setdefault(offer["metadataPath"], []).append(offer)
    expected = {}
    for path, offers in grouped.items():
        values = list(
            {
                canonical_json(offer["metadataValue"]): offer["metadataValue"]
                for offer in offers
            }.values()
        )
        if len(values) == 1:
            value = values[0]
        elif all(offer["joinedText"] for offer in offers) and all(
            isinstance(item, str) for item in values
        ):
            value = "\n".join(values)
        elif all(offer["multiValued"] for offer in offers) and all(
            isinstance(item, list) for item in values
        ):
            value = list(
                {
                    canonical_json(item): item for items in values for item in items
                }.values()
            )
        else:
            raise OsbCandidateSetError(
                "OSB_PACKAGE_METADATA_OFFER_MISMATCH",
                "Reviewed metadata contributors conflict.",
                409,
            )
        expected[path] = normalize_metadata_value(value)
    return expected


def _verify_native_projection(
    operation: dict[str, Any],
    selection: dict[str, Any],
    intent: dict[str, Any],
    candidate: dict[str, Any],
    native_study: dict[str, Any],
    *,
    request_contract_version: str | None = None,
) -> dict[str, Any]:
    """Extension point for explicitly versioned native study projections.

    Study metadata binds its reviewed offer and path; the caller also compares
    the complete observed value with all selected contributors for that path.
    Identity and metadata profiles do not grant complete source projection.
    Capture readbacks prove fields from native DTOs and retained source custody.
    The caller always independently re-reads and hashes the entire raw payload.
    No flags supplied in the operation can grant projection verification.
    """
    observed = _record(operation.get("normalizedReadBack"))
    schema = _record(operation.get("normalizedReadBackHash")).get("schemaVersion")
    if schema == CAPTURE_READBACK_SCHEMA:
        action = selection.get("action")
        _require((action == "create" or (action == "select"
                                        and request_contract_version in SELECTED_CAPTURE_REQUEST_CONTRACT_VERSIONS))
                 and operation.get("disposition") == "native"
                 and observed.get("version") == operation.get("postTargetVersion")
                 and hash_refs_equal(operation.get("sourceInputHash"),
                                      _hash(intent, "OsbTypedSourceIntentV1@1.0.0")))
        _, blockers = capture_field_receipts(
            intent, observed, binding_key=_text(operation.get("idempotencyKey")),
            native_study_id=_text(native_study.get("nativeIdentity")))
        identity = {field: _text(observed.get(field))
                    for field in ("resourceFamily", "resourceType", "uid", "version")}
        _require(operation.get("nativeTargetIdentity") == identity)
        target: dict[str, Any] = {**identity, "bindingKey": operation["idempotencyKey"]}
        if action == "select":
            selected = _record(selection.get("candidateIdentity"))
            _require(any(_same(selected, value) for value in _list(candidate.get("nativeCandidates"))))
            assert_selected_capture_identity(identity["resourceFamily"], selected, observed["native"])
            target["candidateIdentity"] = selected
        return {
            "targetIdentity": identity,
            "readTarget": target,
            "scope": "library-object",
            "sourceProjectionVerified": not blockers,
        }
    if schema == METADATA_SCHEMA:
        offer = _metadata_offer(intent, candidate, native_study)
        _require(
            selection.get("action") == "create"
            and operation.get("disposition") == "native"
            and intent.get("resourceFamily") == "study_metadata"
            and set(observed)
            == {
                "uid",
                "version",
                "label",
                "resourceType",
                "resourceFamily",
                "metadataPath",
                "metadataValue",
            }
            and observed.get("resourceType") == "StudyMetadata"
            and observed.get("resourceFamily") == "study_metadata"
            and observed.get("uid") == native_study.get("nativeIdentity")
            and observed.get("metadataPath")
            == offer["metadataPath"]
            == observed.get("label")
            and observed.get("version") == operation.get("postTargetVersion")
        )
        identity = {
            field: _text(observed.get(field))
            for field in (
                "resourceFamily",
                "resourceType",
                "uid",
                "version",
                "metadataPath",
            )
        }
        _require(operation.get("nativeTargetIdentity") == identity)
        return {
            "targetIdentity": identity,
            "readTarget": identity,
            "scope": "study-metadata",
            "sourceProjectionVerified": False,
        }
    _require(schema == NATIVE_SCHEMA, "OSB_PACKAGE_READBACK_PROFILE_UNSUPPORTED")
    family = canonicalize_family(_text(intent.get("resourceFamily")))
    _require(
        operation.get("disposition") == "native"
        and set(observed)
        == {"uid", "version", "label", "resourceType", "resourceFamily"}
        and isinstance(observed.get("label"), str)
        and observed.get("resourceFamily") == family
        and observed.get("version") == operation.get("postTargetVersion")
    )
    identity = {
        field: _text(observed.get(field))
        for field in ("resourceFamily", "resourceType", "uid", "version")
    }
    _require(operation.get("nativeTargetIdentity") in (identity, identity["uid"]))
    if selection.get("action") == "select":
        selected = _record(selection.get("candidateIdentity"))
        _require(
            all(
                identity[field] == selected.get(field)
                for field in ("uid", "version", "resourceType")
            )
            and family == canonicalize_family(_text(selected.get("resourceFamily")))
        )
    else:
        _require(
            identity["uid"]
            == str(
                uuid5(
                    NAMESPACE_URL,
                    f"accuratrials:osb-native-create:v1:{operation['idempotencyKey']}",
                )
            )
        )
    return {
        "targetIdentity": identity,
        "readTarget": identity,
        "scope": "library-object",
        "sourceProjectionVerified": False,
    }


def _verify_metadata_reference_state(selections, intents, candidates, native, context):
    for key, selection in selections.items():
        if (
            intents[key].get("resourceFamily") == "study_metadata"
            and selection.get("action") == "create"
        ):
            verify_metadata_reference_bindings(
                _metadata_offer(intents[key], candidates[key], native),
                intents[key]["nativeStudyOperation"],
                context,
            )


def load_checkpoint_native_state(
    *, tenant_id: str, platform_study_id: str, checkpoint: dict[str, Any]
) -> dict[str, Any]:
    """Retain and re-read the exact checkpoint inventory; this grants no release."""
    custody = _custody(tenant_id, platform_study_id, checkpoint)
    evidence = custody["evidence"]
    statement = custody["decision"]["statement"]
    selections = _members(statement.get("selections"))
    intents = _members(custody["request"].get("typedSourceIntents"))
    candidates = _members(custody["candidate"].get("candidateRecords"))
    wrappers = [_record(item) for item in _list(evidence.get("evidenceRecords"))]
    _require(
        set(selections) == set(intents) == set(candidates)
        and len(wrappers) == len(selections),
        "OSB_PACKAGE_EVIDENCE_MEMBERSHIP_MISMATCH",
    )
    _check_census(checkpoint, evidence)
    native = _record(checkpoint.get("osbStudyIdentity"))
    metadata_values = _metadata_values(selections, intents, candidates, native)
    _require(
        not metadata_values
        or custody["request"]["contractVersion"] in METADATA_REQUEST_CONTRACT_VERSIONS,
        "OSB_PACKAGE_METADATA_OFFER_MISMATCH",
    )
    _verify_metadata_reference_state(
        selections,
        intents,
        candidates,
        native,
        custody["candidate"].get("mappingContext"),
    )
    root, concepts = _current_study(tenant_id, platform_study_id, native)
    concept_map = {_text(item["managedKey"]): item for item in concepts}
    managed = evidence["managedTargetCheckpoint"]
    _require(
        len(concept_map) == len(concepts)
        and set(concept_map) == set(_list(managed.get("managedKeys")))
        and len(concepts) == len(managed["managedKeys"])
        and managed.get("operationCount") == len(wrappers),
        "OSB_POST_CHECKPOINT_NATIVE_EDIT",
    )
    records = []
    seen_keys = set()
    managed_keys = set()
    observed_native: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for wrapper in wrappers:
        operation = _record(wrapper.get("evidence"))
        _require(
            operation.get("contractVersion") == "NativeOperationEvidenceV1@1.0.0"
            and hash_refs_equal(
                wrapper.get("payloadHash"),
                _hash(operation, "NativeOperationEvidenceV1@1.0.0"),
            )
        )
        prefix = f"{tenant_id}|{platform_study_id}|"
        scoped_key = _text(operation.get("idempotencyKey"))
        key = scoped_key.removeprefix(prefix)
        _require(
            scoped_key.startswith(prefix)
            and key in selections
            and key not in seen_keys,
            "OSB_PACKAGE_EVIDENCE_MEMBERSHIP_MISMATCH",
        )
        seen_keys.add(key)
        selection, intent, candidate = selections[key], intents[key], candidates[key]
        operation_id = str(
            uuid5(
                NAMESPACE_URL,
                f"accuratrials:osb-operation:v1:{statement['decisionId']}:{key}",
            )
        )
        _require(
            operation.get("operationId") == operation.get("effectId") == operation_id
            and operation.get("evidenceId")
            == str(
                uuid5(
                    NAMESPACE_URL, f"accuratrials:osb-native-evidence:v1:{operation_id}"
                )
            )
            and operation.get("decisionId") == statement["decisionId"]
            and operation.get("expectedTargetPrecondition")
            == {
                "nativeStudyId": native["nativeIdentity"],
                "nativeVersion": native["nativeVersion"],
                "candidateSetVersionId": custody["candidate"]["candidateSetVersionId"],
            }
            and hash_refs_equal(
                operation.get("sourceInputHash"),
                _hash(intent, "OsbTypedSourceIntentV1@1.0.0"),
            )
        )
        action = selection.get("action")
        selected = selection.get("candidateIdentity")
        _require(
            (
                action == "select"
                and any(
                    _same(selected, item)
                    for item in _list(candidate.get("nativeCandidates"))
                )
            )
            or (
                action == "create"
                and selected is None
                and _record(candidate.get("createOption")).get("allowed") is True
            )
            or (action in {"defer", "reject"} and selected is None)
        )
        observed = operation.get("normalizedReadBack")
        if action in {"defer", "reject"}:
            _require(
                observed is None
                and operation.get("normalizedReadBackHash") is None
                and operation.get("disposition")
                == ("deferred_blocking" if action == "defer" else "excluded_signed")
            )
            continue
        observed = _record(observed)
        family = canonicalize_family(_text(intent.get("resourceFamily")))
        _require(family == canonicalize_family(_text(candidate.get("resourceFamily"))))
        readback_hash = _record(operation.get("normalizedReadBackHash"))
        schema = _text(readback_hash.get("schemaVersion"))
        proof = (
            None
            if schema == MANAGED_SCHEMA
            else _verify_native_projection(
                operation, selection, intent, candidate, native,
                request_contract_version=custody["request"]["contractVersion"],
            )
        )
        _require(hash_refs_equal(readback_hash, _hash(observed, schema)))
        if schema == METADATA_SCHEMA:
            _require(
                _same(
                    observed["metadataValue"],
                    metadata_values.get(observed["metadataPath"]),
                ),
                "OSB_PACKAGE_METADATA_OFFER_MISMATCH",
            )
        version = _text(operation.get("postTargetVersion"))
        if proof is not None:
            identity, target = proof["targetIdentity"], proof["readTarget"]
            cache_key = canonical_json([family, schema, target])
            if cache_key not in observed_native:
                current = _read_checkpoint_target(family, target)
                observed_native[cache_key] = (family, target, current)
            current = observed_native[cache_key][2]
            _require(
                _same(current, observed)
                and hash_refs_equal(_hash(current, schema), readback_hash),
                "OSB_POST_CHECKPOINT_NATIVE_EDIT",
            )
            kind, scope, projected = (
                "native",
                proof["scope"],
                proof["sourceProjectionVerified"],
            )
        else:
            _require(
                operation.get("disposition") == "governed_extension"
                and observed.get("managedKey") == scoped_key
                and observed.get("nativeStudyId") == native["nativeIdentity"]
                and _key(observed) == key
                and _same(observed.get("source"), intent.get("source"))
                and _same(observed.get("selectedNativeTarget"), selected)
                and observed.get("action") == action
                and observed.get("decisionId") == statement["decisionId"]
                and observed.get("rationale") == selection.get("rationale")
                and observed.get("semanticRole") == intent.get("semanticRole")
                and canonicalize_family(_text(observed.get("resourceFamily"))) == family
            )
            identity = {
                "resourceType": "PlatformManagedStudyConcept",
                "resourceFamily": observed["resourceFamily"],
                "managedKey": scoped_key,
                "nativeStudyId": native["nativeIdentity"],
                "version": version,
            }
            _require(operation.get("nativeTargetIdentity") in (identity, scoped_key))
            stored = concept_map.get(scoped_key)
            _require(
                stored is not None
                and stored["payload"] == observed
                and stored["nativeVersion"] == version
                and stored["contentHash"] == readback_hash["value"]
                and hash_refs_equal(
                    _hash(stored["payload"], MANAGED_SCHEMA), readback_hash
                )
                and _key(stored) == key
                and canonicalize_family(stored["resourceFamily"]) == family,
                "OSB_POST_CHECKPOINT_NATIVE_EDIT",
            )
            kind, scope, projected = "managed", "managed-study-concept", True
            managed_keys.add(scoped_key)
        entry = {
            "operationId": operation_id,
            "kind": kind,
            "scope": scope,
            "resourceFamily": family,
            "sourceProjectionVerified": projected,
            "targetIdentity": identity,
            "nativeVersion": version,
            "payload": observed,
            "readBackHash": readback_hash,
            "contentHash": readback_hash["value"],
            "sourceIntent": intent,
            "candidateRecord": candidate,
            "selection": selection,
            "nativeOperationEvidence": wrapper,
        }
        if kind == "managed":
            entry["managedKey"] = scoped_key
        records.append(entry)
    _require(
        managed_keys == set(concept_map), "OSB_PACKAGE_EVIDENCE_MEMBERSHIP_MISMATCH"
    )
    # Detect an edit during collection too; historical library versions are read
    # by the producer's exact pinned-version helper, never by a "latest" search.
    _require(
        _same(
            [root, concepts], list(_current_study(tenant_id, platform_study_id, native))
        ),
        "OSB_POST_CHECKPOINT_NATIVE_EDIT",
    )
    _require(
        root["bindingId"] == custody["binding"]["bindingId"],
        "OSB_PACKAGE_STUDY_MISMATCH",
    )
    for family, identity, observed in observed_native.values():
        _require(
            _same(_read_checkpoint_target(family, identity), observed),
            "OSB_POST_CHECKPOINT_NATIVE_EDIT",
        )
    _verify_metadata_reference_state(
        selections,
        intents,
        candidates,
        native,
        custody["candidate"].get("mappingContext"),
    )
    records.sort(key=lambda item: item["operationId"])
    index = [
        {
            "operationId": item["operationId"],
            "kind": item["kind"],
            "resourceFamily": item["resourceFamily"],
            "targetIdentity": item["targetIdentity"],
            "readBackHash": item["readBackHash"],
            "recordHash": _hash(item, "OsbPackageNativeRecordV1@1.0.0"),
        }
        for item in records
    ]
    state = {
        "contractVersion": STATE_SCHEMA,
        "tenantId": tenant_id,
        "platformStudyId": platform_study_id,
        "root": root,
        "nativeEvidenceSetHash": checkpoint["nativeEvidenceSetHash"],
        "contentIndex": index,
    }
    return {
        "root": root,
        "records": records,
        "contentIndex": index,
        "contentIndexHash": _hash(index, "OsbPackageContentIndexV1@1.0.0"),
        "stateHash": _hash(state, STATE_SCHEMA),
        "request": custody["request"],
        "requestHash": custody["requestHash"],
        "decisionHash": custody["decisionHash"],
        "candidateSetHash": evidence["candidateSetHash"],
    }


def require_native_lock(state: dict[str, Any]) -> dict[str, Any]:
    root = state["root"]
    status = str(root["nativeStatus"]).lower()
    _require(
        root["relationship"] == "LATEST_LOCKED"
        and status == "locked"
        and isinstance(root["versionTimestamp"], str)
        and bool(root["versionTimestamp"])
        and isinstance(root["draftEndedAt"], str)
        and bool(root["draftEndedAt"]),
        "OSB_NATIVE_STUDY_LOCK_REQUIRED",
    )
    return {
        field: root[field]
        for field in (
            "nativeStudyId",
            "nativeVersion",
            "relationship",
            "nativeStatus",
            "versionTimestamp",
            "draftEndedAt",
        )
    }


def require_source_projection(state: dict[str, Any]) -> None:
    _require(
        all(item["sourceProjectionVerified"] is True for item in state["records"]),
        "OSB_NATIVE_SOURCE_PROJECTION_UNAVAILABLE",
    )
