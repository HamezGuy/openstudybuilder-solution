from __future__ import annotations

import json
from copy import deepcopy

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
from clinical_mdr_api.services.integrations.native_capture_projection import (
    CAPTURE_FAMILY_TYPES,
    CAPTURE_READBACK_SCHEMA,
    capture_field_receipts,
)
from clinical_mdr_api.tests.unit.services.test_native_capture_mapping import (
    selected_dependency_fixture,
    selected_library_fixture,
)

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
        self.binding = ("bind-1", "Study_990001", "0.1")
        self.links = []
        self.evidence_nodes = []

    def cypher_query(self, query, params=None):
        params = params or {}
        if any(edge in query for edge in ("PLATFORM_OPERATION_EVIDENCE", "HAS_PLATFORM_MAPPING_DECISION")):
            self.links.append({"query": query, "params": params})
            return ([[1]], None)
        if "CREATE (evidence:NativeOperationEvidenceV1" in query:
            self.evidence_nodes.append(params)
            return ([], None)
        if "PlatformNativeStudyBinding" in query:
            return ([list(self.binding)], None)
        if "OsbInboundArtifact" in query and "study-mapping-decision" in query:
            if "MERGE" in query:
                self.decision_json = params["payload_json"]
                return ([[params["platform_study_id"], params["artifact_version_id"],
                          params["payload_json"], params["byte_size"]]], None)
            return ([[self.decision_json]], None)
        if "OsbCandidateSetV1" in query and "GENERATED_FROM" in query:
            return ([[self.candidate_json, self.request_json, self.binding[1], self.binding[2],
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
        if any(label in query for label in ("CriteriaTemplateRoot", "CTTermRoot", "CTCodelistRoot", "UnitDefinitionRoot")):
            if not self.native_readable:
                return ([], None)
            return ([[params.get("uid", "CriteriaTemplate_1"), params.get("version", "1.0"), "Age >= 18"]], None)
        if "PlatformManagedStudyConcept" in query:
            key = params["managed_key"]
            self.managed_params = params
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


def _decision_pair(action="select", family="criteria_templates", native=None, source=None, create_option=None):
    native = native or (
        NATIVE if family == "criteria_templates"
        else {"resourceFamily": family, "resourceType": family, "uid": f"{family}-1", "version": "1.0"}
    )
    source_intent = source or {
        "factId": "fact-1", "revision": 1, "targetKey": "primary",
        "resourceFamily": family, "semanticRole": "protocol concept",
        "source": {"label": "Age at least 18 years"},
    }
    candidate_record = {
        "factId": "fact-1", "revision": 1, "targetKey": "primary",
        "resourceFamily": family, "semanticRole": "protocol concept",
        "nativeCandidates": [native] if action == "select" else [],
        "createOption": create_option or {"allowed": True, "requestedNativeType": "governed-extension"},
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
    assert evidence["nativeTargetIdentity"] == NATIVE
    assert evidence["normalizedReadBack"]["uid"] == "CriteriaTemplate_1"
    assert evidence["adapterVersion"] == "native-study/1.0.0"
    assert store.managed == {}


def capture_decision_pair(items, request_version="1.3.0"):
    request, candidate, payload, artifact = _decision_pair()
    intents, records, selections = [], [], []
    for item in items:
        intent = deepcopy(item["intent"])
        intent["semanticRole"] = "capture definition"
        intent["evidence"] = {"citations": [{"exactQuote": "Synthetic capture source", "page": None}]}
        key = {field: intent[field] for field in ("factId", "revision", "targetKey")}
        selection = {**key, **deepcopy(item["selection"]), "rationale": "Exact native capture source"}
        intents.append(intent)
        selections.append(selection)
        records.append({**key, "resourceFamily": intent["resourceFamily"], "semanticRole": intent["semanticRole"],
                        "source": deepcopy(intent["source"]), "evidence": deepcopy(intent["evidence"]),
                        "nativeCandidates": [selection["candidateIdentity"]] if selection["action"] == "select" else [],
                        "createOption": {"allowed": True, "requestedNativeType": CAPTURE_FAMILY_TYPES[intent["resourceFamily"]]}})
    request.update(contractVersion=f"OsbCandidateRequestV1@{request_version}", typedSourceIntents=intents)
    candidate["candidateRecords"] = records
    statement = payload["statement"]
    statement["candidateSetHash"] = _hash(candidate, "OsbCandidateSetV1@1.0.0", CANDIDATE_SET_MEDIA_TYPE)
    statement["selections"] = selections
    statement["decisionSetHash"] = _hash(selections, "StudyMappingSelectionSetV1@1.0.0")
    payload["humanSignature"]["recordHash"] = _hash(statement, "StudyMappingDecisionStatementV1@1.0.0")
    payload["serviceAttestation"]["compositeHash"] = _hash(
        {key: value for key, value in payload.items() if key != "serviceAttestation"},
        "StudyMappingDecisionCompositeV1@1.0.0",
    )
    artifact["payloadHash"] = _hash(payload, "StudyMappingDecisionV1@1.0.0")
    artifact["byteSize"] = len(canonical_json(payload).encode("utf-8"))
    artifact["descriptorHash"] = descriptor_hash({
        **{key: value for key, value in artifact.items() if key != "descriptorHash"},
        "contractVersion": "ArtifactDescriptorV1@1.0.0",
    })
    return request, candidate, payload, artifact


@pytest.mark.parametrize("request_version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])
def test_selected_capture_decision_retains_full_native_source_readback_only_under_current_request(monkeypatch, request_version):
    items, port = selected_dependency_fixture(select_group=True)
    if request_version == "1.3.0":
        items[0]["intent"]["source"]["context"] = {
            "encounters": [], "relationships": [{"condition": None}], "semanticAssociations": None,
        }
    request, candidate, payload, artifact = capture_decision_pair(items, request_version)
    store = FakeQuery()
    store.request_json, store.candidate_json, store.decision_json = map(canonical_json, (request, candidate, payload))
    monkeypatch.setattr("clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query", store.cypher_query)
    monkeypatch.setattr("clinical_mdr_api.services.integrations.native_capture_mapping.NativeCapturePort", lambda: port)
    applied = apply_mapping_decision(
        tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
        actor="synthetic-reviewer", osb_openapi_hash=OPENAPI,
    )
    evidence = {wrapper["evidence"]["idempotencyKey"]: wrapper["evidence"] for wrapper in applied["payload"]["evidenceRecords"]}
    for intent, selection in zip(request["typedSourceIntents"], payload["statement"]["selections"], strict=True):
        key = f'{TENANT}|{STUDY}|{intent["factId"]}@1:primary'
        operation = evidence[key]
        if selection["action"] == "select" and request_version != "1.3.0":
            assert operation["normalizedReadBackHash"]["schemaVersion"] == "OsbNativeTargetReadBackV1@1.0.0"
            assert "sourceBinding" not in operation["normalizedReadBack"]
            continue
        assert operation["normalizedReadBackHash"]["schemaVersion"] == CAPTURE_READBACK_SCHEMA
        observed = operation["normalizedReadBack"]
        assert observed["sourceBinding"] == port.bindings[key]
        assert observed["sourceBinding"]["source"] == intent["source"]
        assert observed["native"] == port.read(intent["resourceFamily"], observed["uid"])
        assert operation["postTargetVersion"] == observed["version"]
        receipts, blockers = capture_field_receipts(intent, observed)
        assert all(receipt["disposition"] in {"native", "governed_extension"} for receipt in receipts)
        assert all(blocker["code"] == "NATIVE_CAPTURE_LIBRARY_REVIEW_REQUIRED" for blocker in blockers)
    if request_version == "1.3.0":
        codelist = evidence[f"{TENANT}|{STUDY}|codelist@1:primary"]["normalizedReadBack"]
        assert codelist["native"]["name"]["version"] == "2.0"
        assert codelist["version"] == codelist["native"]["attributes"]["version"] == "5.0"
    before = deepcopy((port.creates, port.associations, port.bindings))
    replay = apply_mapping_decision(
        tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
        actor="synthetic-reviewer", osb_openapi_hash=OPENAPI,
    )
    assert replay["replay"] is True and replay["payload"] == applied["payload"]
    assert (port.creates, port.associations, port.bindings) == before


def test_selected_only_native_capture_decision_observes_existing_definitions(monkeypatch):
    items, port = selected_library_fixture()
    request, candidate, payload, artifact = capture_decision_pair(items)
    store = FakeQuery()
    store.request_json, store.candidate_json, store.decision_json = map(canonical_json, (request, candidate, payload))
    monkeypatch.setattr("clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query", store.cypher_query)
    monkeypatch.setattr("clinical_mdr_api.services.integrations.native_capture_mapping.NativeCapturePort", lambda: port)
    result = apply_mapping_decision(
        tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
        actor="synthetic-reviewer", osb_openapi_hash=OPENAPI,
    )
    assert port.creates == port.associations == []
    assert len(result["payload"]["evidenceRecords"]) == len(items)
    for wrapper, intent in zip(result["payload"]["evidenceRecords"], request["typedSourceIntents"], strict=True):
        operation = wrapper["evidence"]
        assert operation["normalizedReadBackHash"]["schemaVersion"] == CAPTURE_READBACK_SCHEMA
        _, blockers = capture_field_receipts(intent, operation["normalizedReadBack"])
        assert blockers == []


def test_metadata_decision_applies_native_property_once_and_replays_the_original_evidence(monkeypatch):
    from clinical_mdr_api.services.integrations import study_metadata_mapping
    from clinical_mdr_api.tests.unit.services.test_study_metadata_mapping import COUNT, CONTEXT, NativePort, intent

    port = NativePort("Study_990001")
    source = intent("fact-1", COUNT, 30000)
    source.update({"semanticRole": "STUDY_DESIGN_ATTRIBUTE", "source": {
        "label": "Planned enrollment", "values": [
            {"name": "numericValue", "sourcePath": "/fields/numericValue", "valueType": "integer", "value": 30000},
        ],
    }})
    option = study_metadata_mapping.prepare_metadata_offers([source], port.uid, CONTEXT, port=port)["fact-1@1:primary"]["createOption"]
    request_payload, candidate_set, payload, artifact = _decision_pair(
        "create", family="study_metadata", source=source, create_option=option,
    )
    store = FakeQuery()
    store.request_json = canonical_json(request_payload)
    store.candidate_json = canonical_json(candidate_set)
    store.decision_json = canonical_json(payload)
    monkeypatch.setattr("clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query", store.cypher_query)
    monkeypatch.setattr(study_metadata_mapping, "NativeStudyMetadataPort", lambda: port)
    first = apply_mapping_decision(tenant_id=TENANT, platform_study_id=STUDY,
                                   decision_artifact=artifact, actor="synthetic-test", osb_openapi_hash=OPENAPI)
    second = apply_mapping_decision(tenant_id=TENANT, platform_study_id=STUDY,
                                    decision_artifact=artifact, actor="synthetic-test", osb_openapi_hash=OPENAPI)
    assert len(port.patches) == 1
    assert port.study["current_metadata"]["study_population"]["number_of_expected_subjects"] == 30000
    assert second["replay"] is True
    assert second["payload"] == first["payload"]
    evidence = first["payload"]["evidenceRecords"][0]["evidence"]
    assert evidence["disposition"] == "native"
    assert evidence["normalizedReadBack"]["metadataValue"] == 30000
    assert evidence["nativeTargetIdentity"]["uid"] == port.uid
    assert evidence["nativeTargetIdentity"]["metadataPath"] == COUNT
    assert evidence["normalizedReadBackHash"]["schemaVersion"] == "OsbStudyMetadataReadBackV1@1.0.0"
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


def test_native_binding_rollover_blocks_decision_before_any_native_write(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair("select")
    store = FakeQuery()
    store.binding = ("replacement-binding", "Study_990001", "0.1")
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
    assert error.value.code == "OSB_CANDIDATE_SET_STALE"
    assert store.managed == {}
    assert store.evidence is None


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


def test_unimplemented_create_cannot_manufacture_a_final_name_only_library_node(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair("create")
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
            actor="reviewer@example.com", osb_openapi_hash=OPENAPI,
        )
    assert error.value.code == "OSB_NATIVE_SOURCE_CREATE_UNSUPPORTED"
    assert store.evidence is None
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
    assert evidence["nativeTargetIdentity"] == {
        "resourceType": "PlatformManagedStudyConcept", "resourceFamily": "compound_product_relationships",
        "managedKey": f"{TENANT}|{STUDY}|fact-1@1:primary", "nativeStudyId": "Study_990001", "version": "1"}
    assert store.managed


def test_create_managed_concept_carries_the_csl_entity_reference(monkeypatch):
    """A5: a 1.4.0 typed intent names the CSL entity its fact materialised; the managed side-car stores it as
    node properties (outside payload_json, so the hashed managed read-back is unchanged)."""
    reference = {"entityId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "entityType": "StudyIntervention",
                 "revisionId": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1"}
    source = {
        "factId": "fact-1", "revision": 1, "targetKey": "primary",
        "resourceFamily": "compound_product_relationships", "semanticRole": "protocol concept",
        "source": {"label": "Age at least 18 years"}, "cslEntity": reference,
    }
    request_payload, candidate_set, payload, artifact = _decision_pair(
        "create", family="compound_product_relationships", source=source,
    )
    request_payload["contractVersion"] = "OsbCandidateRequestV1@1.4.0"
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
    assert applied["payload"]["evidenceRecords"][0]["evidence"]["disposition"] == "governed_extension"
    assert store.managed_params["csl_entity_id"] == reference["entityId"]
    assert store.managed_params["csl_entity_type"] == "StudyIntervention"
    assert store.managed_params["csl_revision_id"] == reference["revisionId"]
    # The managed read-back payload is unchanged by the reference: it is a node property, not hashed content.
    assert "cslEntity" not in json.loads(store.managed_params["payload_json"])


def test_managed_concept_without_a_csl_reference_stores_none(monkeypatch):
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
    apply_mapping_decision(
        tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
        actor="reviewer@example.com", osb_openapi_hash=OPENAPI,
    )
    assert store.managed_params["csl_entity_id"] is None
    assert store.managed_params["csl_entity_type"] is None


def test_original_scalar_evidence_replay_preserves_exact_retained_bytes(monkeypatch):
    request, candidate, decision, artifact = _decision_pair("select")
    store = FakeQuery()
    store.request_json, store.candidate_json, store.decision_json = map(canonical_json, [request, candidate, decision])
    monkeypatch.setattr("clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query", store.cypher_query)
    first = apply_mapping_decision(tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
                                   actor="authored", osb_openapi_hash=OPENAPI)
    payload = first["payload"]
    wrapped = payload["evidenceRecords"][0]
    wrapped["evidence"]["nativeTargetIdentity"] = "CriteriaTemplate_1"
    wrapped["payloadHash"] = _hash(wrapped["evidence"], "NativeOperationEvidenceV1@1.0.0")
    payload_hash = _hash(payload, "OsbNativeEvidenceSetV1@1.0.0", "application/vnd.accuratrials.osb-native-evidence-set-v1+json")
    retained_artifact = first["artifactRef"]
    retained_artifact.update({"payloadHash": payload_hash, "byteSize": len(canonical_json(payload).encode())})
    retained_artifact["descriptorHash"] = descriptor_hash({"contractVersion": "ArtifactDescriptorV1@1.0.0",
        **{k: v for k, v in retained_artifact.items() if k not in {"contractVersion", "descriptorHash"}}})
    store.evidence = [canonical_json(payload), payload_hash["value"], canonical_json(retained_artifact),
                      payload["evidenceSetId"], payload["evidenceSetVersionId"]]
    retained = tuple(store.evidence)
    store.native_readable = False
    replay = apply_mapping_decision(tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
                                    actor="authored", osb_openapi_hash=OPENAPI)
    assert canonical_json(replay["payload"]) == retained[0]
    assert canonical_json(replay["artifactRef"]) == retained[2]
    assert tuple(store.evidence) == retained


def test_applied_evidence_is_linked_from_the_study_and_the_native_target_and_states_both_versions(monkeypatch):
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
    # A selection writes nothing, so the version before equals the version read back.
    assert evidence["preTargetVersion"] == "1.0"
    assert evidence["postTargetVersion"] == "1.0"
    node = store.evidence_nodes[0]
    assert node["native_resource_type"] == "CriteriaTemplate"
    assert node["native_uid"] == "CriteriaTemplate_1"
    assert node["native_version_before"] == "1.0"
    assert node["native_version_after"] == "1.0"
    assert node["disposition"] == "native"
    assert node["selection_action"] == "select"
    assert node["selection_rationale"] == "exact template"
    assert node["decision_reason"] == "vertical slice"
    assert node["signature_meaning"] == "study-mapping-decision"
    assert node["signed_at"] == "2026-08-21T08:45:00Z"
    assert node["requesting_actor"] == "reviewer@example.com"
    assert (node["fact_id"], node["fact_revision"], node["target_key"]) == ("fact-1", 1, "primary")
    study_links = [link for link in store.links if "HAS_PLATFORM_OPERATION_EVIDENCE" in link["query"]]
    target_links = [link for link in store.links if "MERGE (target)-[:PLATFORM_OPERATION_EVIDENCE]" in link["query"]]
    decision_links = [link for link in store.links if "HAS_PLATFORM_MAPPING_DECISION" in link["query"]]
    assert [link["params"]["study_uid"] for link in study_links] == ["Study_990001"]
    assert len(target_links) == 1
    assert "MATCH (target:CriteriaTemplateRoot)" in target_links[0]["query"]
    assert target_links[0]["params"]["uid"] == "CriteriaTemplate_1"
    assert target_links[0]["params"]["evidence_id"] == evidence["evidenceId"]
    assert [link["params"]["decision_id"] for link in decision_links] == [payload["statement"]["decisionId"]]


def test_an_unreadable_native_target_fails_the_decision_instead_of_leaving_evidence_unlinked(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair("select")
    store = FakeQuery()
    store.request_json = canonical_json(request_payload)
    store.candidate_json = canonical_json(candidate_set)
    store.decision_json = canonical_json(payload)
    original = store.cypher_query

    def unlinked(query, params=None):
        if "MERGE (target)-[:PLATFORM_OPERATION_EVIDENCE]" in query:
            return ([[0]], None)
        return original(query, params)

    monkeypatch.setattr("clinical_mdr_api.services.integrations.mapping_decision_v1.db.cypher_query", unlinked)
    with pytest.raises(OsbCandidateSetError) as failure:
        apply_mapping_decision(tenant_id=TENANT, platform_study_id=STUDY, decision_artifact=artifact,
                               actor="reviewer@example.com", osb_openapi_hash=OPENAPI)
    assert failure.value.code == "OSB_NATIVE_EVIDENCE_TARGET_UNLINKED"
    assert store.evidence is None


def test_managed_concept_creation_has_no_prior_version_and_links_from_the_side_car(monkeypatch):
    request_payload, candidate_set, payload, artifact = _decision_pair("create", family="compound_product_relationships")
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
    assert evidence["preTargetVersion"] is None
    assert evidence["postTargetVersion"] == "1"
    target_links = [link for link in store.links if "MERGE (target)-[:PLATFORM_OPERATION_EVIDENCE]" in link["query"]]
    assert len(target_links) == 1
    assert "PlatformManagedStudyConcept {managed_key:$key}" in target_links[0]["query"]
    assert target_links[0]["params"]["key"] == evidence["nativeTargetIdentity"]["managedKey"]
    assert store.evidence_nodes[0]["native_version_before"] is None
    assert store.evidence_nodes[0]["native_uid"] == evidence["nativeTargetIdentity"]["managedKey"]
