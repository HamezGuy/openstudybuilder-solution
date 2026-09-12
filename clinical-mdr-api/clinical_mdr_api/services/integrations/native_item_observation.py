"""Current library Item observation, distinct from semantic decision approval.

Two bounded native read observations surround validation. This is not a graph
snapshot lease, fresh IdP logout observation, or study-selection verification.
"""
from __future__ import annotations

import json
import time
from threading import Event
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from clinical_mdr_api.domain_repositories.integrations.native_item_observation import (
    NativeItemObservationRepository,
)
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json, canonical_json_hash_ref, descriptor_hash,
)
from clinical_mdr_api.models.integrations.native_item_observation import NativeItemObservationRequest, NativeItemObservationResponse
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.services.integrations.native_capture_mapping import assert_selected_capture_identity
from clinical_mdr_api.services.integrations.native_capture_projection import CAPTURE_READBACK_SCHEMA, capture_field_receipts
from clinical_mdr_api.services.integrations.native_study_head import select_current_study_head
from clinical_mdr_api.services.integrations.osb_candidate_request_versions import (
    ACCEPTED_REQUEST_CONTRACT_VERSIONS,
    SELECTED_CAPTURE_REQUEST_CONTRACT_VERSIONS,
)
from common.auth.user import auth


class NativeItemObservationError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def _require(condition: bool, code: str = "OSB_ITEM_CUSTODY_MISMATCH") -> None:
    if not condition:
        raise NativeItemObservationError(code, 403 if code in {
            "OSB_ITEM_AUTH_REQUIRED", "OSB_ITEM_AUTH_EXPIRED", "OSB_ITEM_SCOPE_DENIED"} else 409)


def _one(rows: list, code: str) -> Any:
    _require(len(rows) == 1, code)
    return rows[0]


def _hash(payload: Any, schema: str, media: str = "application/json") -> dict:
    return canonical_json_hash_ref(payload, schema_version=schema, media_type=media)


def _json(text: str) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            _require(key not in result, "OSB_ITEM_STORED_JSON_INVALID")
            result[key] = value
        return result

    _require(isinstance(text, str) and len(text.encode("utf-8")) <= 262_144,
             "OSB_ITEM_CUSTODY_LIMIT")
    try:
        result = json.loads(text, object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        _require(isinstance(result, dict), "OSB_ITEM_STORED_JSON_INVALID")
        # Canonicalization also rejects unsupported numbers and Unicode.
        canonical_json(result)
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise NativeItemObservationError("OSB_ITEM_STORED_JSON_INVALID") from error


class NativeItemObservationService:
    def __init__(self, repository=None, auth_reader: Callable = auth,
                 clock: Callable = time.time, monotonic: Callable = time.monotonic,
                 allowed_purposes: frozenset[str] = frozenset({"workflow-orchestration"})):
        self.repository = repository or NativeItemObservationRepository()
        self.auth_reader, self.clock, self.monotonic = auth_reader, clock, monotonic
        self.allowed_purposes = frozenset(allowed_purposes)

    def request_budget(self) -> float:
        """Nonrenewable credential ceiling for the route's bounded await."""
        original = self.auth_reader()
        _require(original is not None and original.authentication_verified is True,
                 "OSB_ITEM_AUTH_REQUIRED")
        expiry = original.access_token_claims.exp
        _require(type(expiry) is int, "OSB_ITEM_AUTH_REQUIRED")
        budget = min(15.0, expiry - self.clock())
        _require(budget > 0, "OSB_ITEM_AUTH_EXPIRED")
        return budget

    def observe(self, request: NativeItemObservationRequest, cancellation: Event | None = None) -> dict:
        started, wall_started = self.monotonic(), self.clock()
        original = self.auth_reader()
        authority_pin = None
        last_item_observed = None

        def authority():
            nonlocal authority_pin
            _require(cancellation is None or not cancellation.is_set(), "OSB_ITEM_READ_CANCELLED")
            current = self.auth_reader()
            _require(current is original and current is not None
                     and current.authentication_verified is True, "OSB_ITEM_AUTH_REQUIRED")
            claims, caller = current.access_token_claims, current.user
            now = self.clock()
            _require(type(claims.exp) is int and now < claims.exp and claims.iat <= now
                     and (claims.nbf is None or claims.nbf <= now)
                     and now >= wall_started and self.monotonic() - started < 15,
                     "OSB_ITEM_AUTH_EXPIRED")
            _require(bool(caller.sub and caller.issuer and caller.tenant_id)
                     and caller.purpose in self.allowed_purposes
                     and {request.platformStudyId, request.nativeStudyId} <= caller.study_ids
                     and {"candidate:read", "study:read"} <= caller.capabilities
                     and bool({"Study.Read", "Admin.Read"} & caller.roles)
                     and bool({"Library.Read", "Admin.Read"} & caller.roles),
                     "OSB_ITEM_SCOPE_DENIED")
            # Compare the provider-built caller with the original verified
            # claims as well as conserving it across native I/O.
            pin = (caller.sub, caller.issuer, caller.tenant_id, caller.purpose,
                   tuple(sorted(caller.study_ids)), tuple(sorted(caller.capabilities)),
                   tuple(sorted(caller.roles)), claims.exp, claims.iat, claims.nbf)
            expected = (claims.sub, claims.iss, claims.tenant_id, claims.purpose,
                        tuple(sorted(str(v) for v in claims.study_ids)),
                        tuple(sorted(claims.capabilities)), tuple(sorted(claims.roles or [])),
                        claims.exp, claims.iat, claims.nbf)
            _require(pin == expected and (authority_pin is None or authority_pin == pin),
                     "OSB_ITEM_SCOPE_DENIED")
            authority_pin = pin
            return caller

        caller = authority()
        if request.scope != "library-item":
            raise NativeItemObservationError("OSB_ITEM_STUDY_SELECTION_UNSUPPORTED", 422)
        p = {**request.model_dump(), "tenantId": caller.tenant_id}

        def read(method):
            nonlocal last_item_observed
            authority()
            remaining = min(5.0, 15 - (self.monotonic() - started),
                            original.access_token_claims.exp - self.clock())
            _require(remaining > 0, "OSB_ITEM_AUTH_EXPIRED")
            value = getattr(self.repository, method)(p, remaining)
            if method == "item":
                last_item_observed = self.clock()
            authority()
            return value

        def snapshot():
            scope = _one(read("scope"), "OSB_ITEM_SCOPE_UNAVAILABLE")
            _require(scope == [request.bindingId, request.nativeStudyId, request.nativeStudyVersion],
                     "OSB_ITEM_BINDING_CHANGED")
            heads = read("study_heads")
            _require(0 < len(heads) < 5, "OSB_ITEM_NATIVE_VERSION_UNAVAILABLE")
            _require(all(isinstance(row, (list, tuple)) and len(row) == 6
                         and row[0] in {"LATEST_DRAFT", "LATEST_LOCKED", "LATEST_RELEASED"} for row in heads),
                     "OSB_ITEM_NATIVE_VERSION_UNAVAILABLE")
            try:
                head = select_current_study_head([
                    {"relationship": row[0], "version": row[1], "status": row[2],
                     "startDate": row[3], "endDate": row[4], "isLatest": row[5]}
                    for row in heads
                ])
            except ValueError as error:
                raise NativeItemObservationError("OSB_ITEM_NATIVE_VERSION_UNAVAILABLE") from error
            _require(head["nativeVersion"] == request.nativeStudyVersion,
                     "OSB_ITEM_NATIVE_VERSION_CHANGED")
            custody = _one(read("custody"), "OSB_ITEM_CUSTODY_UNAVAILABLE")
            stored = custody[0]
            _require(isinstance(stored, list) and len(stored) == 7, "OSB_ITEM_CUSTODY_LIMIT")
            blobs = [_json(text) for text in stored]
            item = _one(read("item"), "OSB_ITEM_CURRENT_VERSION_UNAVAILABLE")
            _require(item[0] == request.itemUid and item[1] == request.itemVersion
                     and item[2] == "Final", "OSB_ITEM_CURRENT_VERSION_CHANGED")
            _require(isinstance(item[3], dict), "OSB_ITEM_TYPED_FIELDS_LIMIT")
            _require(item[4] is False, "OSB_ITEM_TYPED_RELATIONSHIPS_UNSUPPORTED")
            fields = item[3]
            _require(set(fields) == {"name", "oid", "prompt", "datatype", "length",
                      "significantDigits", "sasFieldName", "sdsVarName", "origin", "comment"}
                     and isinstance(fields["datatype"], str) and bool(fields["datatype"]),
                     "OSB_ITEM_TYPED_FIELDS_UNAVAILABLE")
            for key, value in fields.items():
                if key in {"length", "significantDigits"}:
                    _require(value is None or (type(value) is int and 0 <= value <= 2_147_483_647),
                             "OSB_ITEM_TYPED_FIELDS_UNAVAILABLE")
                else:
                    _require(value is None or (isinstance(value, str) and len(value) <= 4096),
                             "OSB_ITEM_TYPED_FIELDS_LIMIT")
            return scope, head, blobs, item, custody[1]

        before = snapshot()
        decision, candidate, source, evidence_set, context, artifact, native_operation = before[2]
        statement = decision.get("statement", {})
        native = {"contractVersion": "1.0.0", "system": "osb", "tenantId": p["tenantId"], "platformStudyId": request.platformStudyId,
                  "namespace": "accuratrials-osb", "objectType": "study-draft-root",
                  "verificationStatus": "verified", "bindingId": request.bindingId,
                  "nativeIdentity": request.nativeStudyId, "nativeVersion": request.nativeStudyVersion}
        decision_hash = _hash(decision, "StudyMappingDecisionV1@1.0.0")
        candidate_hash = _hash(candidate, "OsbCandidateSetV1@1.0.0",
                               "application/vnd.accuratrials.osb-candidate-set-v1+json")
        evidence_hash = _hash(evidence_set, "OsbNativeEvidenceSetV1@1.0.0",
                              "application/vnd.accuratrials.osb-native-evidence-set-v1+json")
        _require(source.get("contractVersion") in ACCEPTED_REQUEST_CONTRACT_VERSIONS,
                 "OSB_ITEM_SOURCE_VERSION_UNSUPPORTED")
        source_hash = _hash(source, source["contractVersion"],
                            "application/vnd.accuratrials.osb-candidate-request-v1+json")
        _require(decision_hash["value"] == request.decisionHash
                 and candidate_hash["value"] == request.candidateSetHash
                 and evidence_hash["value"] == request.evidenceSetHash)
        _require(decision.get("contractVersion") == "StudyMappingDecisionV1@1.0.0"
                 and statement.get("decisionId") == request.decisionId
                 and statement.get("tenantId") == p["tenantId"]
                 and statement.get("platformStudyId") == request.platformStudyId
                 and statement.get("osbStudyIdentity") == native
                 and statement.get("candidateRequestHash") == source_hash
                 and statement.get("candidateSetHash") == candidate_hash
                 and statement.get("mappingContextHash") == request.mappingContextHash)
        attestation = decision.get("serviceAttestation", {})
        _require(attestation.get("mode") == "prototype-session-attested"
                 and attestation.get("productionEligible") is False,
                 "OSB_ITEM_DECISION_ASSURANCE_UNSUPPORTED")
        _require(decision.get("humanSignature", {}).get("recordHash") ==
                 _hash(statement, "StudyMappingDecisionStatementV1@1.0.0")
                 and attestation.get("compositeHash") == _hash({k: v for k, v in decision.items()
                 if k != "serviceAttestation"}, "StudyMappingDecisionCompositeV1@1.0.0"))
        _require(candidate.get("tenantId") == p["tenantId"]
                 and candidate.get("platformStudyId") == request.platformStudyId
                 and candidate.get("candidateSetVersionId") == request.candidateSetVersionId
                 and candidate.get("osbStudyIdentity") == native
                 and candidate.get("request", {}).get("payloadHash") == source_hash
                 and candidate.get("request", {}).get("requestVersionId") == source.get("requestVersionId")
                 and candidate.get("capabilityCheckpoint", {}).get("mappingContextHash") == request.mappingContextHash
                 and canonical_hash(context) == request.mappingContextHash
                 and context.get("studyUid") == request.nativeStudyId)
        _require(source.get("tenantId") == p["tenantId"]
                 and source.get("platformStudyId") == request.platformStudyId)
        _require(evidence_set.get("tenantId") == p["tenantId"]
                 and evidence_set.get("platformStudyId") == request.platformStudyId
                 and evidence_set.get("evidenceSetVersionId") == request.evidenceSetVersionId
                 and evidence_set.get("decisionHash") == decision_hash
                 and evidence_set.get("candidateSetHash") == candidate_hash
                 and evidence_set.get("nativeStudyIdentity") == {"nativeIdentity": request.nativeStudyId,
                                                               "nativeVersion": request.nativeStudyVersion})
        descriptor = {"contractVersion": "ArtifactDescriptorV1@1.0.0",
                      **{k: v for k, v in artifact.items() if k not in {"contractVersion", "descriptorHash"}}}
        _require(artifact.get("contractVersion") == "ArtifactRefV1@1.0.0"
                 and artifact.get("descriptorHash") == descriptor_hash(descriptor)
                 and artifact.get("payloadHash") == evidence_hash
                 and artifact.get("artifactVersionId") == request.evidenceSetVersionId
                 and artifact.get("tenantId") == p["tenantId"]
                 and artifact.get("kind") == "osb-native-evidence-set"
                 and artifact.get("artifactId") == evidence_set.get("evidenceSetId")
                 and artifact.get("stableLocator") == f"artifact://osb/native-evidence-set/{request.evidenceSetVersionId}"
                 and artifact.get("producerService") == "osb.clinical-mdr-api"
                 and artifact.get("purpose") == "transformation-verification"
                 and artifact.get("payloadContract") == "accuratrials.osb.OsbNativeEvidenceSetV1"
                 and artifact.get("payloadContractVersion") == "1.0.0"
                 and artifact.get("byteSize") == len(canonical_json(evidence_set).encode("utf-8")))
        key = lambda row: (row.get("factId"), row.get("revision"), row.get("targetKey"))
        selected_key = (request.factId, request.revision, request.targetKey)
        selections = statement.get("selections", [])
        _require(statement.get("decisionSetHash") == _hash(selections, "StudyMappingSelectionSetV1@1.0.0"))
        selection = _one([row for row in selections if key(row) == selected_key], "OSB_ITEM_SELECTION_UNAVAILABLE")
        record = _one([row for row in candidate.get("candidateRecords", []) if key(row) == selected_key], "OSB_ITEM_CANDIDATE_UNAVAILABLE")
        intent = _one([row for row in source.get("typedSourceIntents", []) if key(row) == selected_key], "OSB_ITEM_SOURCE_UNAVAILABLE")
        identity = selection.get("candidateIdentity", {})
        _require(selection.get("action") == "select"
                 and identity.get("uid") == request.itemUid and identity.get("version") == request.itemVersion
                 and identity.get("resourceFamily") == "odm_items"
                 and record.get("resourceFamily") == "odm_items" and intent.get("resourceFamily") == "odm_items"
                 and sum(value == identity for value in record.get("nativeCandidates", [])) == 1,
                 "OSB_ITEM_SELECTION_UNSUPPORTED")
        wrapped = _one([row for row in evidence_set.get("evidenceRecords", [])
                        if row.get("evidence", {}).get("evidenceId") == request.evidenceId],
                       "OSB_ITEM_OPERATION_UNAVAILABLE")
        operation = wrapped["evidence"]
        operation_id = str(uuid5(NAMESPACE_URL, f"accuratrials:osb-operation:v1:{request.decisionId}:{request.factId}@{request.revision}:{request.targetKey}"))
        readback = {"uid": request.itemUid, "version": request.itemVersion,
                    "label": operation.get("normalizedReadBack", {}).get("label"),
                    "resourceType": identity.get("resourceType"), "resourceFamily": "odm_items"}
        readback_schema = "OsbNativeTargetReadBackV1@1.0.0"
        target_identity = identity
        binding_key = f"{p['tenantId']}|{request.platformStudyId}|{request.factId}@{request.revision}:{request.targetKey}"
        if isinstance(operation.get("normalizedReadBackHash"), dict) \
                and operation["normalizedReadBackHash"].get("schemaVersion") == CAPTURE_READBACK_SCHEMA:
            _require(source["contractVersion"] in SELECTED_CAPTURE_REQUEST_CONTRACT_VERSIONS,
                     "OSB_ITEM_SOURCE_VERSION_UNSUPPORTED")
            readback = operation.get("normalizedReadBack")
            try:
                _, blockers = capture_field_receipts(
                    intent, readback, binding_key=binding_key, native_study_id=request.nativeStudyId,
                )
                _require(not blockers)
                assert_selected_capture_identity("odm_items", identity, readback["native"])
            except (OsbCandidateSetError, KeyError, TypeError, ValueError, AttributeError) as error:
                raise NativeItemObservationError("OSB_ITEM_CUSTODY_MISMATCH") from error
            target_identity = {name: readback[name] for name in ("resourceFamily", "resourceType", "uid", "version")}
            readback_schema = CAPTURE_READBACK_SCHEMA
            # Use the existing bounded scalar reads to confirm the retained
            # native DTO. Source annotations cannot prove current properties.
            names = {"significantDigits": "significant_digits", "sasFieldName": "sas_field_name", "sdsVarName": "sds_var_name"}
            retained_fields = {name: readback["native"].get(names.get(name, name)) for name in before[3][3]}
            _require(readback["native"].get("status") == before[3][2]
                     and canonical_json(retained_fields) == canonical_json(before[3][3]),
                     "OSB_ITEM_OBSERVATION_CHANGED")
        _require(wrapped.get("payloadHash") == _hash(operation, "NativeOperationEvidenceV1@1.0.0")
                 and wrapped["payloadHash"]["value"] == before[4] and native_operation == operation
                 and operation.get("operationId") == operation_id
                 and operation.get("evidenceId") == str(uuid5(NAMESPACE_URL, f"accuratrials:osb-native-evidence:v1:{operation_id}"))
                 and operation.get("effectId") == operation_id
                 and operation.get("idempotencyKey") == binding_key
                 and operation.get("expectedTargetPrecondition") == {
                     "nativeStudyId": request.nativeStudyId, "nativeVersion": request.nativeStudyVersion,
                     "candidateSetVersionId": request.candidateSetVersionId}
                 and operation.get("decisionId") == request.decisionId
                 and operation.get("nativeTargetIdentity") == target_identity
                 and operation.get("postTargetVersion") == request.itemVersion
                 and operation.get("disposition") == "native"
                 and operation.get("sourceInputHash") == _hash(intent, "OsbTypedSourceIntentV1@1.0.0")
                 and operation.get("normalizedReadBack") == readback
                 and operation.get("normalizedReadBackHash") == _hash(readback, readback_schema))
        after = snapshot()
        _require(before == after, "OSB_ITEM_OBSERVATION_CHANGED")
        _require(_one(read("scope"), "OSB_ITEM_SCOPE_UNAVAILABLE") == before[0], "OSB_ITEM_BINDING_CHANGED")
        authority()
        item = {"uid": after[3][0], "version": after[3][1], "status": after[3][2],
                "fields": after[3][3]}
        expires = min(wall_started + 15, original.access_token_claims.exp)
        result = {"contractVersion": "OsbNativeItemObservationV1@1.1.0",
                  "scope": "library-item", "pins": request.model_dump(),
                  "tenantId": p["tenantId"], "item": item,
                  "itemHash": _hash(item, "OsbNativeLibraryItemScalarsV1@1.0.0"),
                  "decisionHash": decision_hash, "candidateSetHash": candidate_hash,
                  "evidenceSetHash": evidence_hash, "operationHash": wrapped["payloadHash"],
                  "decisionAssurance": "retained-prototype-attestation",
                  "contextAssurance": "retained-exact-context",
                  "studySelectionVerified": False, "semanticApprovalVerified": False,
                  "observedAt": datetime.fromtimestamp(last_item_observed, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                  "authorityCheckedAt": datetime.fromtimestamp(self.clock(), UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                  "expiresAt": datetime.fromtimestamp(expires, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
        _require(len(canonical_json(result).encode("utf-8")) <= 65_536, "OSB_ITEM_RESPONSE_LIMIT")
        result = NativeItemObservationResponse.model_validate(result).model_dump()
        authority()
        _require(self.clock() * 1000 < int(expires * 1000), "OSB_ITEM_AUTH_EXPIRED")
        return result
