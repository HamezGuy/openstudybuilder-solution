"""Actual observer and original producer, with authored native query boundaries."""
import copy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from authlib.jose import JWTClaims

from common.auth.models import AccessTokenClaims, Auth
from clinical_mdr_api.domain_repositories.integrations.native_item_observation import NativeItemObservationRepository
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json, descriptor_hash
from clinical_mdr_api.models.integrations.native_item_observation import NativeItemObservationRequest
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.services.integrations.native_item_observation import (
    NativeItemObservationService, NativeItemObservationError, _hash,
)
from clinical_mdr_api.services.integrations.mapping_decision_v1 import apply_mapping_decision
from clinical_mdr_api.tests.unit.services.test_osb_mapping_decision_apply import (
    _decision_pair, FakeQuery, TENANT, STUDY, OPENAPI,
)
from clinical_mdr_api.tests.unit.services.test_native_capture_mapping import selected_library_fixture
from clinical_mdr_api.tests.unit.services.test_osb_native_head import NativeHeads


def fixture(*, context_enricher=None, openapi_hash=OPENAPI, request_version="1.0.0", native_version="0.1"):
    identity = {"resourceFamily": "odm_items", "resourceType": "OdmItem", "uid": "OdmItem_1", "version": "1.0"}
    capture_port, capture_source = None, None
    if request_version == "1.3.0":
        capture_items, capture_port = selected_library_fixture()
        capture_item = next(item for item in capture_items if item["intent"]["factId"] == "number")
        identity = capture_item["selection"]["candidateIdentity"]
        capture_source = capture_item["intent"]["source"]
    source, candidate, decision, artifact = _decision_pair("select", "odm_items", identity)
    native = {**candidate["osbStudyIdentity"], "contractVersion": "1.0.0", "system": "osb", "tenantId": TENANT,
              "platformStudyId": STUDY, "namespace": "accuratrials-osb",
              "objectType": "study-draft-root", "verificationStatus": "verified", "nativeVersion": native_version}
    context = {"schemaVersion": "osb-mapping-context/2.0", "studyUid": native["nativeIdentity"],
               "studyValueVersion": native_version, "osbOpenApiHash": openapi_hash, "governed": True,
               "selectedPackages": [], "selectedDataModels": [], "candidateGroups": []}
    if context_enricher is not None:
        context = context_enricher(copy.deepcopy(context))
    context_hash = canonical_hash(context)
    from clinical_mdr_api.tests.unit.services.test_native_item_observation_wire_fixture import complete_original_wire
    complete_original_wire(source, candidate, decision, native, context, context_hash)
    if capture_source is not None:
        intent = source["typedSourceIntents"][0]
        intent["source"] = copy.deepcopy(capture_source)
        record = candidate["candidateRecords"][0]
        record.update(source=copy.deepcopy(intent["source"]), evidence=copy.deepcopy(intent["evidence"]))
        source_rows = source["inputConservation"]["rows"]
        source_rows[0]["target"]["valueHash"] = _hash(intent, "OsbTypedSourceIntentV1@1.0.0")
        source["inputConservation"]["rowSetHash"] = _hash(source_rows, "ConservationCensusRowsV1@1.0.0")
        candidate_rows = candidate["conservation"]["rows"]
        candidate_rows[0]["source"]["valueHash"] = _hash(intent, "OsbTypedSourceIntentV1@1.0.0")
        candidate_rows[0]["target"]["valueHash"] = _hash(record, "OsbCandidateRecordV1@1.0.0")
        candidate["conservation"]["rowSetHash"] = _hash(candidate_rows, "ConservationCensusRowsV1@1.0.0")
    source["contractVersion"] = f"OsbCandidateRequestV1@{request_version}"
    source_hash = _hash(source, source["contractVersion"], "application/vnd.accuratrials.osb-candidate-request-v1+json")
    candidate["osbStudyIdentity"] = native
    candidate["request"] = {"requestVersionId": source["requestVersionId"], "payloadHash": source_hash}
    candidate["capabilityCheckpoint"]["mappingContextHash"] = context_hash
    candidate["capabilityCheckpoint"]["osbOpenApiHash"] = openapi_hash
    candidate["capabilityCheckpoint"]["nativeVersion"] = native_version
    statement = decision["statement"]
    statement.update({"osbStudyIdentity": native, "mappingContextHash": context_hash,
                      "candidateRequestHash": source_hash,
                      "candidateSetHash": _hash(candidate, "OsbCandidateSetV1@1.0.0",
                       "application/vnd.accuratrials.osb-candidate-set-v1+json")})
    decision["humanSignature"]["recordHash"] = _hash(statement, "StudyMappingDecisionStatementV1@1.0.0")
    decision["serviceAttestation"]["compositeHash"] = _hash({k: v for k, v in decision.items() if k != "serviceAttestation"}, "StudyMappingDecisionCompositeV1@1.0.0")
    artifact["payloadHash"] = _hash(decision, "StudyMappingDecisionV1@1.0.0")
    artifact["byteSize"] = len(canonical_json(decision).encode())
    artifact["descriptorHash"] = descriptor_hash({"contractVersion": "ArtifactDescriptorV1@1.0.0",
        **{k: v for k, v in artifact.items() if k not in {"contractVersion", "descriptorHash"}}})
    producer = FakeQuery(openapi_hash=openapi_hash)
    producer.binding = ("bind-1", native["nativeIdentity"], native_version)
    producer.request_json, producer.candidate_json, producer.decision_json = map(canonical_json, [source, candidate, decision])
    with (
        patch("clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query", producer.cypher_query),
        patch("clinical_mdr_api.services.integrations.native_capture_mapping.NativeCapturePort", lambda: capture_port)
        if capture_port is not None else nullcontext(),
    ):
        applied = apply_mapping_decision(tenant_id=TENANT, platform_study_id=STUDY,
                    decision_artifact=artifact, actor="authored", osb_openapi_hash=openapi_hash)
    request = NativeItemObservationRequest.model_validate({
        "contractVersion": "OsbNativeItemObservationRequestV1@1.0.0", "scope": "library-item",
        "platformStudyId": STUDY, "nativeStudyId": native["nativeIdentity"], "nativeStudyVersion": native_version,
        "bindingId": "bind-1", "decisionId": statement["decisionId"], "decisionHash": artifact["payloadHash"]["value"],
        "candidateSetVersionId": candidate["candidateSetVersionId"], "candidateSetHash": statement["candidateSetHash"]["value"],
        "mappingContextHash": context_hash, "evidenceSetVersionId": applied["evidenceSetVersionId"],
        "evidenceSetHash": applied["payloadHash"]["value"], "evidenceId": applied["payload"]["evidenceRecords"][0]["evidence"]["evidenceId"],
        "factId": "fact-1", "revision": 1, "targetKey": "primary", "itemUid": identity["uid"], "itemVersion": identity["version"]})
    now = [1_800_000_000.0]
    claims = AccessTokenClaims(iss="https://authored.invalid", sub="authored-service", aud=["osb"],
        exp=int(now[0] + 300), iat=int(now[0] - 1), tenantId=TENANT, studyIds=[STUDY, request.nativeStudyId],
        roles={"Study.Read", "Library.Read"}, purpose="workflow-orchestration", capabilities=["candidate:read", "study:read"])
    auth = Auth(JWTClaims({}, {}), claims, authentication_verified=True)
    blobs = [decision, candidate, source, applied["payload"], context, applied["artifactRef"],
             copy.deepcopy(applied["payload"]["evidenceRecords"][0]["evidence"])]
    fields = {"name": "Native observed name", "oid": "I.NATIVE", "prompt": "Native prompt",
              "datatype": "integer", "length": 8, "significantDigits": None,
               "sasFieldName": None, "sdsVarName": None, "origin": None, "comment": None}
    if capture_port is not None:
        capture_native = capture_port.read("odm_items", identity["uid"])
        names = {"significantDigits": "significant_digits", "sasFieldName": "sas_field_name", "sdsVarName": "sds_var_name"}
        fields = {name: capture_native.get(names.get(name, name)) for name in fields}
    repo = MemoryRepository(request, blobs, fields)
    service = NativeItemObservationService(repo, lambda: auth, lambda: now[0], lambda: now[0])
    result = SimpleNamespace(request=request, service=service, repository=repo, blobs=blobs,
                             now=now, auth=auth, fields=fields, applied=applied)
    from clinical_mdr_api.tests.unit.services.test_native_item_observation_wire_fixture import validate_original_wire
    validate_original_wire(result)
    return result


class MemoryRepository:
    def __init__(self, request, blobs, fields):
        self.request, self.blobs, self.fields = request, blobs, fields
        self.calls, self.hook, self.duplicate, self.missing, self.related = [], None, None, None, False
        self.item_version, self.status = "1.0", "Final"
        self.heads = NativeHeads().heads
        self.heads[0][1] = request.nativeStudyVersion

    def read(self, name, rows, timeout):
        assert 0 < timeout <= 5
        self.calls.append(name)
        if self.hook:
            self.hook(name)
        if self.missing == name:
            return []
        return copy.deepcopy(rows * (2 if self.duplicate == name else 1))

    def scope(self, p, timeout):
        return self.read("scope", [["bind-1", self.request.nativeStudyId, self.request.nativeStudyVersion]], timeout)

    def study_heads(self, p, timeout):
        return self.read("study_heads", self.heads, timeout)

    def custody(self, p, timeout):
        return self.read("custody", [[[canonical_json(v) for v in self.blobs],
            _hash(self.blobs[6], "NativeOperationEvidenceV1@1.0.0")["value"]]], timeout)

    def item(self, p, timeout):
        return self.read("item", [[self.request.itemUid, self.item_version, self.status, self.fields, self.related]], timeout)


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])
def test_actual_native_producer_and_current_scalar_observation(version):
    f = fixture(request_version=version)
    result = f.service.observe(f.request)
    assert result["item"]["fields"] == f.fields
    assert result["item"]["fields"]["datatype"] == "integer"
    assert result["itemHash"] == _hash(result["item"], "OsbNativeLibraryItemScalarsV1@1.0.0")
    assert result["studySelectionVerified"] is False and result["semanticApprovalVerified"] is False
    assert result["decisionAssurance"] == "retained-prototype-attestation"
    assert "rationale" not in canonical_json(result) and "mandatory" not in canonical_json(result)
    assert f.repository.calls.count("scope") == 3


def test_full_selected_capture_evidence_observation_preserves_source_without_granting_semantic_approval():
    f = fixture(request_version="1.3.0")
    before = canonical_json(f.blobs)
    operation = f.blobs[6]
    assert operation["normalizedReadBackHash"]["schemaVersion"] == "OsbNativeCaptureReadBackV1@1.0.0"
    assert operation["normalizedReadBack"]["sourceBinding"]["source"] == f.blobs[2]["typedSourceIntents"][0]["source"]
    result = f.service.observe(f.request)
    assert result["item"]["fields"] == f.fields
    assert result["semanticApprovalVerified"] is False and result["studySelectionVerified"] is False
    assert canonical_json(f.blobs) == before
    assert f.repository.calls.count("item") == 2


def test_full_capture_original_readback_cannot_hide_a_changed_native_scalar():
    f = fixture(request_version="1.3.0")
    f.fields["prompt"] = "Changed after original readback"
    with pytest.raises(NativeItemObservationError, match="OSB_ITEM_OBSERVATION_CHANGED"):
        f.service.observe(f.request)


@pytest.mark.parametrize("request_version", ["1.0.0", "1.3.0"])
def test_observer_uses_current_lock_after_the_draft_ends(request_version):
    f = fixture(request_version=request_version, native_version="1")
    f.repository.heads = NativeHeads("locked").heads
    f.repository.heads[0][1] = "0.1"
    before = canonical_json(f.blobs)
    result = f.service.observe(f.request)
    assert result["pins"]["nativeStudyVersion"] == "1"
    assert result["item"]["fields"] == f.fields
    assert result["semanticApprovalVerified"] is False
    assert f.repository.calls.count("study_heads") == f.repository.calls.count("item") == 2
    assert canonical_json(f.blobs) == before


@pytest.mark.parametrize("request_version", ["1.0.0", "1.3.0"])
def test_observer_rejects_ended_draft_and_reopened_lock_pins(request_version):
    stale_draft = fixture(request_version=request_version)
    stale_draft.repository.heads = NativeHeads("locked").heads
    stale_draft.repository.heads[0][1] = "0.1"
    with pytest.raises(NativeItemObservationError, match="OSB_ITEM_NATIVE_VERSION_CHANGED"):
        stale_draft.service.observe(stale_draft.request)
    assert "custody" not in stale_draft.repository.calls

    stale_lock = fixture(request_version=request_version, native_version="1")
    stale_lock.repository.heads = NativeHeads("reopened").heads
    stale_lock.repository.heads[0][1] = "0.1"
    with pytest.raises(NativeItemObservationError, match="OSB_ITEM_NATIVE_VERSION_CHANGED"):
        stale_lock.service.observe(stale_lock.request)
    assert "custody" not in stale_lock.repository.calls

    stale_draft.repository.heads = copy.deepcopy(stale_lock.repository.heads)
    assert stale_draft.service.observe(stale_draft.request)["pins"]["nativeStudyVersion"] == "0.1"


@pytest.mark.parametrize("mutation", ["duplicate", "ended-lock", "wrong-status", "not-latest", "no-draft", "row-limit", "missing-marker"])
def test_observer_rejects_ambiguous_or_noncurrent_native_heads(mutation):
    f = fixture(request_version="1.3.0", native_version="1")
    f.repository.heads = NativeHeads("locked").heads
    if mutation == "duplicate":
        f.repository.heads.append(copy.deepcopy(f.repository.heads[0]))
    elif mutation == "ended-lock":
        f.repository.heads[1][4] = "2026-09-11T11:00:00Z"
    elif mutation == "wrong-status":
        f.repository.heads[1][2] = "Draft"
    elif mutation == "not-latest":
        f.repository.heads[1][-1] = False
    elif mutation == "no-draft":
        f.repository.heads.pop(0)
    elif mutation == "row-limit":
        f.repository.heads.extend(copy.deepcopy(f.repository.heads[0]) for _ in range(3))
    else:
        f.repository.heads[0].pop()
    with pytest.raises(NativeItemObservationError, match="OSB_ITEM_NATIVE_VERSION_UNAVAILABLE"):
        f.service.observe(f.request)
    assert "custody" not in f.repository.calls


def test_native_head_change_between_bounded_observations_withholds_response():
    f = fixture(request_version="1.3.0")

    def hook(name):
        if name == "study_heads" and f.repository.calls.count(name) == 2:
            f.repository.heads[0][3] = "2026-09-11T11:00:00Z"

    f.repository.hook = hook
    with pytest.raises(NativeItemObservationError, match="OSB_ITEM_OBSERVATION_CHANGED"):
        f.service.observe(f.request)


def test_native_head_repository_reads_lifecycle_markers_under_the_query_deadline(monkeypatch):
    heads = NativeHeads("locked").heads
    calls = []

    def query(statement, parameters):
        calls.append((statement, parameters))
        assert statement.timeout == 2.5
        assert parameters == {"nativeStudyId": "Study_synthetic"}
        text = str(statement)
        assert "head.end_date" in text and "value=latest" in text
        assert "LIMIT 5" in text
        return copy.deepcopy(heads), None

    monkeypatch.setattr("clinical_mdr_api.domain_repositories.integrations.native_item_observation.db.cypher_query", query)
    result = NativeItemObservationRepository().study_heads({"nativeStudyId": "Study_synthetic"}, 2.5)
    assert result == heads
    assert len(calls) == 1


@pytest.mark.parametrize("part", ["scope", "study_heads", "custody", "item"])
def test_missing_or_duplicate_exact_native_rows_refuse(part):
    for kind in ("missing", "duplicate"):
        f = fixture()
        setattr(f.repository, kind, part)
        with pytest.raises(NativeItemObservationError):
            f.service.observe(f.request)


@pytest.mark.parametrize("change", ["version", "draft", "related", "expiry", "native-scope", "platform-scope", "role", "capability", "unverified", "study-selection"])
def test_version_authority_and_scope_fail_closed(change):
    f = fixture()
    if change == "version": f.repository.item_version = "2.0"
    if change == "draft": f.repository.status = "Draft"
    if change == "related": f.repository.related = True
    if change == "expiry": f.now[0] += 301
    if change == "native-scope": f.auth.user.study_ids.remove(f.request.nativeStudyId)
    if change == "platform-scope": f.auth.user.study_ids.remove(STUDY)
    if change == "role": f.auth.user.roles.remove("Library.Read")
    if change == "capability": f.auth.user.capabilities.remove("candidate:read")
    if change == "unverified": f.auth.authentication_verified = False
    if change == "study-selection": f.request = f.request.model_copy(update={"scope": "study-selection"})
    with pytest.raises(NativeItemObservationError):
        f.service.observe(f.request)


@pytest.mark.parametrize("change", ["context-withdrawn", "typed-mutated", "scope-withdrawn", "token-expired", "total-deadline"])
def test_changes_during_native_io_withhold_response(change):
    f = fixture()
    def hook(name):
        if name == "item" and f.repository.calls.count("item") == (2 if change == "typed-mutated" else 1):
            if change == "context-withdrawn": f.repository.missing = "custody"
            if change == "typed-mutated": f.fields["datatype"] = "string"
            if change == "scope-withdrawn": f.repository.missing = "scope"
            if change == "token-expired": f.now[0] += 301
            if change == "total-deadline": f.now[0] += 16
    f.repository.hook = hook
    with pytest.raises(NativeItemObservationError):
        f.service.observe(f.request)


@pytest.mark.parametrize("index", range(7))
def test_changed_original_custody_never_reseals_automatically(index):
    f = fixture()
    f.blobs[index]["tampered"] = True
    with pytest.raises(NativeItemObservationError):
        f.service.observe(f.request)


def test_limits_and_closed_request():
    f = fixture()
    f.fields["name"] = "x" * 4097
    with pytest.raises(NativeItemObservationError): f.service.observe(f.request)
    from pydantic import ValidationError
    for extra in ({"revision": True}, {"unexpected": "private"}, {"itemUid": " name alias "}):
        with pytest.raises(ValidationError):
            NativeItemObservationRequest.model_validate({**f.request.model_dump(), **extra})


def test_data_observation_time_is_distinct_from_final_authority_time():
    f = fixture()
    def hook(name):
        if name == "scope" and f.repository.calls.count("scope") == 3:
            f.now[0] += 0.25
    f.repository.hook = hook
    result = f.service.observe(f.request)
    assert result["contractVersion"] == "OsbNativeItemObservationV1@1.1.0"
    from datetime import datetime
    data = datetime.fromisoformat(result["observedAt"])
    authority = datetime.fromisoformat(result["authorityCheckedAt"])
    assert (authority - data).total_seconds() == 0.25
