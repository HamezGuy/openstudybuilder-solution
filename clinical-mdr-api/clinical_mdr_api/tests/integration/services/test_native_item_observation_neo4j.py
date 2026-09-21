"""Actual Community Cypher over a collector-owned, empty disposable graph.

No deployment/RBAC, JWT crypto, human signature, or study-selection claim.
"""

import os
from uuid import uuid4

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.integrations.native_item_observation import (
    NativeItemObservationRepository,
)
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.services.integrations.native_item_observation import (
    NativeItemObservationError,
    NativeItemObservationService,
)
from clinical_mdr_api.tests.unit.services.test_native_item_observation import (
    STUDY,
    TENANT,
    fixture,
)


@pytest.fixture()
def native():
    dsn = os.environ.get("OSB_ITEM_OBSERVATION_FIXTURE_DSN", "")
    if not dsn or not os.environ.get(
        "OSB_ITEM_OBSERVATION_FIXTURE_CONTAINER", ""
    ).startswith("codex-osb-item-community-"):
        pytest.fail("Explicit collector-owned disposable Neo4j fixture required")
    assert "@127.0.0.1:" in dsn and dsn.endswith("/neo4j")
    db.set_connection(url=dsn)
    assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == [
        [0]
    ], "Fixture graph must be empty"
    f = fixture()
    nonce = str(uuid4())
    p = {
        **f.request.model_dump(),
        "tenantId": TENANT,
        "fixture": nonce,
        "decision": canonical_json(f.blobs[0]),
        "candidate": canonical_json(f.blobs[1]),
        "source": canonical_json(f.blobs[2]),
        "evidence": canonical_json(f.blobs[3]),
        "context": canonical_json(f.blobs[4]),
        "artifact": canonical_json(f.blobs[5]),
        "operation": canonical_json(f.blobs[6]),
        "operationHash": f.applied["payload"]["evidenceRecords"][0]["payloadHash"][
            "value"
        ],
    }
    db.cypher_query(
        """
      CREATE (scope:DomainStudyScope {fixture:$fixture,study_uid:$nativeStudyId,tenant_id:$tenantId,status:'active'})
      CREATE (binding:PlatformNativeStudyBinding {fixture:$fixture,tenant_id:$tenantId,
        platform_study_id:$platformStudyId,namespace:'accuratrials-osb',object_type:'study-draft-root',status:'active',
        binding_id:$bindingId,native_study_id:$nativeStudyId,native_version:$nativeStudyVersion})
      CREATE (study:StudyRoot {fixture:$fixture,uid:$nativeStudyId})
        -[:LATEST]->(studyValue:StudyValue {fixture:$fixture})
      CREATE (study)-[:LATEST_DRAFT {version:$nativeStudyVersion,status:'Draft',
        start_date:datetime('2026-09-11T10:00:00Z')}]->(studyValue)
      CREATE (root:OdmItemRoot {fixture:$fixture,uid:$itemUid})
      CREATE (value:OdmItemValue {fixture:$fixture,name:'Native observed name',oid:'I.NATIVE',
        prompt:'Native prompt',datatype:'integer',length:8})
      CREATE (root)-[:LATEST]->(value)
      CREATE (root)-[:HAS_VERSION {version:$itemVersion,status:'Final'}]->(value)
      CREATE (decision:StudyMappingDecisionV1 {fixture:$fixture,tenant_id:$tenantId,
        platform_study_id:$platformStudyId,decision_id:$decisionId,decision_hash:$decisionHash,payload_json:$decision})
      CREATE (evidence:OsbNativeEvidenceSetV1 {fixture:$fixture,tenant_id:$tenantId,
        platform_study_id:$platformStudyId,evidence_set_version_id:$evidenceSetVersionId,
        payload_hash:$evidenceSetHash,decision_hash:$decisionHash,payload_json:$evidence,artifact_ref_json:$artifact})
        -[:EXECUTED_DECISION]->(decision)
      CREATE (candidate:OsbCandidateSetV1 {fixture:$fixture,tenant_id:$tenantId,platform_study_id:$platformStudyId,
        candidate_set_version_id:$candidateSetVersionId,payload_hash:$candidateSetHash,
        context_hash:$mappingContextHash,payload_json:$candidate})
        -[:GENERATED_FROM]->(:OsbCandidateRequestV1 {fixture:$fixture,tenant_id:$tenantId,
          platform_study_id:$platformStudyId,payload_json:$source})
      CREATE (:OsbMappingContextSnapshot {fixture:$fixture,context_hash:$mappingContextHash,content_json:$context})
      CREATE (:NativeOperationEvidenceV1 {fixture:$fixture,tenant_id:$tenantId,platform_study_id:$platformStudyId,
        evidence_id:$evidenceId,decision_id:$decisionId,payload_json:$operation,payload_hash:$operationHash})
    """,
        p,
    )
    f.service = NativeItemObservationService(
        auth_reader=lambda: f.auth, clock=lambda: f.now[0], monotonic=lambda: f.now[0]
    )
    f.params = p
    try:
        yield f
    finally:
        # Delete only this authored fixture's nodes after verifying membership.
        assert db.cypher_query(
            "MATCH (n) WHERE n.fixture IS NULL OR n.fixture<>$fixture RETURN count(n)",
            p,
        )[0] == [[0]]
        db.cypher_query("MATCH (n {fixture:$fixture}) DETACH DELETE n", p)
        assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == [[0]]


def test_actual_native_queries_return_current_typed_bytes(native):
    result = native.service.observe(native.request)
    assert result["item"]["fields"]["datatype"] == "integer"
    assert result["item"]["fields"]["name"] == "Native observed name"
    assert result["studySelectionVerified"] is False
    assert (
        result["operationHash"]
        == native.applied["payload"]["evidenceRecords"][0]["payloadHash"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "MATCH (b:PlatformNativeStudyBinding) SET b.status='retired'",
        "MATCH (s:DomainStudyScope) SET s.tenant_id='foreign-tenant'",
        "MATCH (:StudyRoot)-[h:LATEST_DRAFT]->() SET h.version='0.2'",
        "MATCH (:StudyRoot)-[h:LATEST_DRAFT]->() SET h.end_date=datetime('2026-09-11T10:01:00Z')",
        "MATCH (:StudyRoot)-[h:LATEST_DRAFT]->() SET h.status='LOCKED'",
        "MATCH (:StudyRoot)-[h:LATEST]->() DELETE h",
        "MATCH (:OdmItemRoot)-[v:HAS_VERSION]->() SET v.version='2.0'",
        "MATCH (:OdmItemRoot)-[v:HAS_VERSION]->() SET v.status='Retired'",
        "MATCH (c:OsbMappingContextSnapshot) DELETE c",
        "MATCH (o:NativeOperationEvidenceV1) DELETE o",
        "MATCH (c:OsbCandidateSetV1) SET c.payload_json=repeat('x',65537)",
        "MATCH (v:OdmItemValue) SET v.prompt=repeat('x',4097)",
        "MATCH (v:OdmItemValue) CREATE (v)-[:HAS_CODELIST]->(:CTCodelistRoot {fixture:$fixture,uid:'CL.1'})",
        "MATCH (b:PlatformNativeStudyBinding) CREATE (copy:PlatformNativeStudyBinding) SET copy=properties(b)",
        "MATCH (c:OsbMappingContextSnapshot) CREATE (copy:OsbMappingContextSnapshot) SET copy=properties(c)",
        "MATCH (r:OdmItemRoot)-[:LATEST]->(v) CREATE (r)-[:HAS_VERSION {version:'1.0',status:'Final'}]->(v)",
    ],
)
def test_actual_current_version_authority_context_and_bounds_refuse(native, mutation):
    # Neo4j core has no repeat() function; supply retained oversized bytes as parameters.
    mutation = mutation.replace("repeat('x',65537)", "$largeJson").replace(
        "repeat('x',4097)", "$largeField"
    )
    db.cypher_query(
        mutation, {**native.params, "largeJson": "x" * 65537, "largeField": "x" * 4097}
    )
    with pytest.raises(NativeItemObservationError):
        native.service.observe(native.request)


def test_actual_context_withdrawal_between_observations(native):
    class Withdrawal(NativeItemObservationRepository):
        def __init__(self):
            self.count = 0

        def item(self, p, timeout):
            result = super().item(p, timeout)
            self.count += 1
            if self.count == 1:
                db.cypher_query(
                    "MATCH (c:OsbMappingContextSnapshot {context_hash:$mappingContextHash}) DELETE c",
                    p,
                )
            return result

    native.service.repository = Withdrawal()
    with pytest.raises(NativeItemObservationError, match="CUSTODY_UNAVAILABLE"):
        native.service.observe(native.request)


def test_actual_scope_withdrawal_during_last_item_read(native):
    class Withdrawal(NativeItemObservationRepository):
        def __init__(self):
            self.count = 0

        def item(self, p, timeout):
            result = super().item(p, timeout)
            self.count += 1
            if self.count == 2:
                db.cypher_query(
                    "MATCH (s:DomainStudyScope {study_uid:$nativeStudyId}) SET s.status='quarantined'",
                    p,
                )
            return result

    native.service.repository = Withdrawal()
    with pytest.raises(NativeItemObservationError, match="SCOPE_UNAVAILABLE"):
        native.service.observe(native.request)
