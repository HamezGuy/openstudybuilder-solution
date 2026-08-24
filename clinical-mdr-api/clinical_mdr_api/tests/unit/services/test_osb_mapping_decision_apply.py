from __future__ import annotations

import pytest

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    descriptor_hash,
)
from clinical_mdr_api.services.integrations.candidate_set import (
    CANDIDATE_SET_MEDIA_TYPE,
    OsbCandidateSetError,
)
from clinical_mdr_api.services.integrations.mapping_decision_v1 import apply_mapping_decision

TENANT = "11111111-1111-4111-8111-111111111111"
STUDY = "22222222-2222-4222-8222-222222222222"
OPENAPI = "sha256:" + "ab" * 32
NATIVE = {
    "resourceFamily": "criteria_templates",
    "resourceType": "CriteriaTemplate",
    "uid": "CriteriaTemplate_1",
    "version": "1.0",
}


class FakeQuery:
    def __init__(self, *, native_readable=True, openapi_hash=OPENAPI):
        self.native_readable = native_readable
        self.candidate_json = None
        self.request_json = None
        self.decision_json = None
        self.evidence = None
        self.managed = {}
        self.openapi_hash = openapi_hash

    def cypher_query(self, query, params=None):
        params = params or {}
        if "OsbInboundArtifact" in query and "study-mapping-decision" in query:
            if "MERGE" in query:
                self.decision_json = params["payload_json"]
                return ([[params["platform_study_id"], params["artifact_version_id"],
                          params["payload_json"], params["byte_size"]]], None)
            return ([[self.decision_json]], None)
        if "OsbCandidateSetV1" in query and "GENERATED_FROM" in query:
            return ([[self.candidate_json, self.request_json, "Study_990001", "0.1",
                      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]], None)
        if "OsbCandidateRequestLock" in query:
            return ([[1]], None)
        if "OsbNativeEvidenceSetV1" in query and "RETURN evidence.payload_json" in query:
            return ([self.evidence], None) if self.evidence else ([], None)
        if "HAS_VERSION" in query and "PlatformManagedStudyConcept" not in query:
            if not self.native_readable and "MERGE" not in query:
                return ([], None)
            uid = str(params.get("uid") or "CriteriaTemplate_1")
            version = str(params.get("version") or "1.0")
            name = str(params.get("name") or "Age >= 18")
            return ([[uid, version, name]], None)
        if "OdmAlias" in query:
            if not self.native_readable and "MERGE" not in query:
                return ([], None)
            uid = str(params.get("uid") or params.get("name") or "alias-1")
            return ([[uid, str(params.get("version") or "0.1"), params.get("name") or uid]], None)
        if "CriteriaTemplateRoot" in query or "CTTermRoot" in query or "UnitDefinitionRoot" in query:
            if not self.native_readable:
                return ([], None)
            return ([["CriteriaTemplate_1", "1.0", "Age >= 18"]], None)
        if "PlatformManagedStudyConcept" in query:
            key = params["managed_key"]
            stored = self.managed.setdefault(key, [params["payload_json"], params["content_hash"], 1])
            return ([stored], None)
        if "NativeOperationEvidenceV1" in query:
            return ([], None)
        if "CREATE (decision:StudyMappingDecisionV1" in query:
            self.evidence = [
                params["payload_json"], params["payload_hash"], params["artifact_ref_json"],
                params["evidence_set_id"], params["evidence_set_version_id"],
            ]
            return ([], None)
        return ([], None)


def _hash(value, schema, media=None):
    kwargs = {"schema_version": schema}
    if media:
        kwargs["media_type"] = media
    return canonical_json_hash_ref(value, **kwargs)


def _decision_pair(action="select", family="criteria_templates", native=None):
    native = native or (
        NATIVE if family == "criteria_templates"
        else {"resourceFamily": family, "resourceType": family, "uid": f"{family}-1", "version": "1.0"}
    )
    source_intent = {
        "factId": "fact-1", "revision": 1, "targetKey": "primary",
        "resourceFamily": family, "semanticRole": "protocol concept",
        "source": {"label": "Age at least 18 years"},
    }
    candidate_record = {
        "factId": "fact-1", "revision": 1, "targetKey": "primary",
        "resourceFamily": family, "semanticRole": "protocol concept",
        "nativeCandidates": [native] if action == "select" else [],
        "createOption": {"allowed": True, "requestedNativeType": "governed-extension"},
    }
    request_payload = {"typedSourceIntents": [source_intent]}
    candidate_set = {
        "contractVersion": "OsbCandidateSetV1@1.0.0",
        "candidateSetId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "candidateSetVersionId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "tenantId": TENANT, "platformStudyId": STUDY,
        "osbStudyIdentity": {"bindingId": "bind-1", "nativeIdentity": "Study_990001", "nativeVersion": "0.1"},
        "capabilityCheckpoint": {"osbOpenApiHash": OPENAPI, "mappingContextHash": "context-1", "nativeVersion": "0.1"},
        "candidateRecords": [candidate_record],
        "expiresAt": "2099-01-01T00:00:00Z",
    }
    candidate_set_hash = _hash(candidate_set, "OsbCandidateSetV1@1.0.0", CANDIDATE_SET_MEDIA_TYPE)
    selection = {
        "factId": "fact-1", "revision": 1, "targetKey": "primary", "action": action,
        "candidateIdentity": native if action == "select" else None,
        "rationale": "exact template",
    }
    statement = {
        "contractVersion": "StudyMappingDecisionStatementV1@1.0.0",
        "decisionId": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        "tenantId": TENANT, "platformStudyId": STUDY,
        "candidateSetHash": candidate_set_hash,
        "mappingContextHash": "context-1",
        "osbStudyIdentity": candidate_set["osbStudyIdentity"],
        "selections": [selection],
        "displayedStatement": "I reviewed the exact candidate set.",
        "signatureMeaning": "study-mapping-decision",
        "reason": "vertical slice",
        "supersedesDecisionId": None,
    }
    statement["decisionSetHash"] = _hash(statement["selections"], "StudyMappingSelectionSetV1@1.0.0")
    statement_hash = _hash(statement, "StudyMappingDecisionStatementV1@1.0.0")
    human_signature = {
        "contractVersion": "HumanElectronicSignatureV1@1.0.0",
        "recordHash": statement_hash,
        "displayedStatement": statement["displayedStatement"],
        "signatureMeaning": statement["signatureMeaning"],
        "reason": statement["reason"],
        "signedAt": "2026-08-21T08:45:00Z",
    }
    unsigned = {
        "contractVersion": "StudyMappingDecisionV1@1.0.0",
        "statement": statement,
        "humanSignature": human_signature,
    }
    payload = {
        **unsigned,
        "serviceAttestation": {
            "mode": "prototype-session-attested",
            "productionEligible": False,
            "compositeHash": _hash(unsigned, "StudyMappingDecisionCompositeV1@1.0.0"),
            "service": "csl.semantic-api",
            "environment": "prototype",
            "attestedAt": "2026-08-21T08:45:00Z",
        },
    }
    payload_hash = _hash(payload, "StudyMappingDecisionV1@1.0.0")
    fields = {
        "artifactId": statement["decisionId"],
        "artifactVersionId": statement["decisionId"],
        "kind": "study-mapping-decision",
        "stableLocator": f"artifact://csl/study-mapping-decision/{statement['decisionId']}",
        "payloadHash": payload_hash,
        "byteSize": len(canonical_json(payload).encode("utf-8")),
        "classification": "regulated-non-phi",
        "tenantId": TENANT,
        "region": "us-central1",
        "producerService": "csl.semantic-api",
        "producerEnvironment": "prototype",
        "producerVersion": "test",
        "payloadContract": "accuratrials.csl.StudyMappingDecisionV1",
        "payloadContractVersion": "1.0.0",
        "purpose": "mapping",
        "createdAt": "2026-08-21T08:45:00Z",
    }
    artifact = {
        "contractVersion": "ArtifactRefV1@1.0.0",
        **fields,
        "descriptorHash": descriptor_hash({"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}),
    }
    return request_payload, candidate_set, payload, artifact


def test_select_reads_native_target_not_managed_concept(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair("select")
    store = FakeQuery()
    store.request_json = canonical_json(request_payload)
    store.candidate_json = canonical_json(candidate_set)
    store.decision_json = canonical_json(payload)
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query",
        store.cypher_query,
    )
    applied = apply_mapping_decision(
        tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
        actor="reviewer@example.com", osb_openapi_hash=OPENAPI,
    )
    evidence = applied["payload"]["evidenceRecords"][0]["evidence"]
    assert evidence["disposition"] == "native"
    assert evidence["nativeTargetIdentity"] == "CriteriaTemplate_1"
    assert evidence["normalizedReadBack"]["uid"] == "CriteriaTemplate_1"
    assert evidence["adapterVersion"] == "native-study/1.0.0"
    assert store.managed == {}


def test_current_openapi_hash_mismatch_fails_closed(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair("select")
    store = FakeQuery()
    store.request_json = canonical_json(request_payload)
    store.candidate_json = canonical_json(candidate_set)
    store.decision_json = canonical_json(payload)
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query",
        store.cypher_query,
    )
    with pytest.raises(OsbCandidateSetError) as error:
        apply_mapping_decision(
            tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
            actor="reviewer@example.com", osb_openapi_hash="sha256:" + "cd" * 32,
        )
    assert error.value.code == "OSB_CANDIDATE_SET_STALE"


def test_unreadable_native_select_fails_closed(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair("select")
    store = FakeQuery(native_readable=False)
    store.request_json = canonical_json(request_payload)
    store.candidate_json = canonical_json(candidate_set)
    store.decision_json = canonical_json(payload)
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query",
        store.cypher_query,
    )
    with pytest.raises(OsbCandidateSetError) as error:
        apply_mapping_decision(
            tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
            actor="reviewer@example.com", osb_openapi_hash=OPENAPI,
        )
    assert error.value.code == "OSB_NATIVE_TARGET_UNREADABLE"


def test_create_writes_native_library_node(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair("create")
    store = FakeQuery()
    store.request_json = canonical_json(request_payload)
    store.candidate_json = canonical_json(candidate_set)
    store.decision_json = canonical_json(payload)
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query",
        store.cypher_query,
    )
    applied = apply_mapping_decision(
        tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
        actor="reviewer@example.com", osb_openapi_hash=OPENAPI,
    )
    evidence = applied["payload"]["evidenceRecords"][0]["evidence"]
    assert evidence["disposition"] == "native"
    assert evidence["adapterVersion"] == "native-study/1.0.0"
    assert evidence["nativeTargetIdentity"]
    assert store.managed == {}


def test_create_compound_relationship_is_governed_extension(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair(
        "create", family="compound_product_relationships",
    )
    store = FakeQuery()
    store.request_json = canonical_json(request_payload)
    store.candidate_json = canonical_json(candidate_set)
    store.decision_json = canonical_json(payload)
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query",
        store.cypher_query,
    )
    applied = apply_mapping_decision(
        tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
        actor="reviewer@example.com", osb_openapi_hash=OPENAPI,
    )
    evidence = applied["payload"]["evidenceRecords"][0]["evidence"]
    assert evidence["disposition"] == "governed_extension"
    assert evidence["adapterVersion"] == "platform-managed-study/1.0.0"
    assert store.managed
