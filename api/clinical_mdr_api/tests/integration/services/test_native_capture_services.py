"""Real ODM/CT services against an explicitly disposable, empty Neo4j graph."""

import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from neomodel import db
from starlette_context import request_cycle_context

from clinical_mdr_api.services.integrations.native_capture_mapping import (
    NativeCapturePort, apply_native_capture_selections, read_capture_target,
)
from clinical_mdr_api.services.integrations.native_capture_projection import capture_field_receipts
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.tests.unit.services.test_native_capture_mapping import fixture as capture_fixture
from common.auth.dependencies import dummy_access_token_claims, dummy_auth_object
from common.config import settings

DSN = os.environ.get("OSB_CAPTURE_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="Requires the disposable native capture service graph")
TENANT = "11111111-1111-4111-8111-111111111111"
STUDY = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(scope="module")
def native_study():
    assert DSN == "bolt://neo4j:synthetic-only@codex-osb-capture-db-20260911:7687/neo4j"
    assert os.environ["NEO4J_DSN"] == DSN
    db.set_connection(url=DSN)
    rows, _ = db.cypher_query("MATCH (node) RETURN count(node)")
    assert rows == [[0]], "Refuse any existing or client graph"
    claims = dummy_access_token_claims(user_id="capture-service-test")
    claims.sub = "capture-service-test"
    claims.subject_type = "service"
    claims.tenant_id = TENANT
    claims.roles = {"Admin.Write", "Study.Write", "Library.Write", "Library.Read"}
    with request_cycle_context({"auth": dummy_auth_object(claims)}):
        from clinical_mdr_api.tests.integration.utils.utils import TestUtils

        TestUtils.create_dummy_user("capture-service-test")
        TestUtils.create_library()
        # Native study creation needs the normal, synthetic field configuration.
        # None of the capture drafts exercised below are approved by these seeds.
        TestUtils.create_ct_catalogue()
        TestUtils.create_ct_codelist()
        TestUtils.create_study_fields_configuration()
        TestUtils.create_unit_definition(name=settings.day_unit_name)
        TestUtils.create_unit_definition(name=settings.week_unit_name)
        programme = TestUtils.create_clinical_programme(name="Synthetic capture programme")
        TestUtils.create_project(project_number="CAPTURE_SYNTHETIC", clinical_programme_uid=programme.uid)
        created = TestUtils.create_study(number="9971", acronym="SYNTHETIC", project_number="CAPTURE_SYNTHETIC")
        db.cypher_query(
            """CREATE (:PlatformNativeStudyBinding {tenant_id:$tenant,platform_study_id:$platform,
                 namespace:'accuratrials-osb',object_type:'study-draft-root',status:'active',
                 binding_id:'synthetic-capture-binding',native_study_id:$native,native_version:'draft'})
               MERGE (:DomainStudyScope {tenant_id:$tenant,study_uid:$native,status:'active'})""",
            {"tenant": TENANT, "platform": STUDY, "native": created.uid})
        yield created.uid
    db.close_connection()


def test_real_native_services_preserve_capture_properties_edges_and_atomicity(native_study):
    source = capture_fixture()
    with db.transaction:
        result = apply_native_capture_selections(
            source, tenant_id=TENANT, platform_study_id=STUDY, native_study_id=native_study)
        assert len(result) == 6
        for item in source:
            intent = item["intent"]
            key = f'{intent["factId"]}@{intent["revision"]}:{intent["targetKey"]}'
            receipt, blockers = capture_field_receipts(intent, result[key])
            assert receipt
            assert blockers == [{"code": "NATIVE_CAPTURE_LIBRARY_REVIEW_REQUIRED"}]
        assert result["group@1:primary"]["nativeValues"]["order"] == 7
        assert result["choice@1:primary"]["nativeValues"]["order"] == 3
        assert result["number@1:primary"]["nativeValues"]["order"] == 9
        assert result["choice@1:primary"]["nativeValues"]["required"] is False
    port = NativeCapturePort()
    before, _ = db.cypher_query("MATCH (node) RETURN count(node)")
    with db.transaction:
        repeated = apply_native_capture_selections(
            source, tenant_id=TENANT, platform_study_id=STUDY, native_study_id=native_study)
        assert repeated == result
    after, _ = db.cypher_query("MATCH (node) RETURN count(node)")
    assert before == after
    item = result["choice@1:primary"]
    observed = read_capture_target({
        "bindingKey": f"{TENANT}|{STUDY}|choice@1:primary",
        "resourceFamily": "odm_items", "uid": item["uid"], "version": item["version"]}, port=port)
    assert observed == item
    second_source = deepcopy(source[0])
    second_source["intent"]["factId"] = "rollback"
    for value in second_source["intent"]["source"]["values"]:
        if value["name"] == "name":
            value["value"] = "Rollback form"
        if value["name"] == "formRef":
            value["value"] = "F.ROLLBACK"
    with pytest.raises(RuntimeError, match="synthetic transaction failure"):
        with db.transaction:
            apply_native_capture_selections(
                [second_source], tenant_id=TENANT, platform_study_id=STUDY, native_study_id=native_study)
            raise RuntimeError("synthetic transaction failure")
    rolled_back, _ = db.cypher_query("MATCH (node) RETURN count(node)")
    assert rolled_back == before
    assert port.binding(f"{TENANT}|{STUDY}|rollback@1:primary") is None


def test_real_stage_expiry_rolls_back_native_objects_and_receipt_then_retries_and_replays(native_study, monkeypatch):
    from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json, canonical_json_hash_ref
    from clinical_mdr_api.services.integrations import source_draft_stage as stage
    from clinical_mdr_api.tests.unit.services.test_candidate_set_v1 import _refresh
    from clinical_mdr_api.tests.unit.services.test_source_draft_stage import fixture, signature, stage_ref

    payload, _, candidate = fixture()
    request = candidate[0]
    identity = request["osbStudyIdentity"]
    binding, _ = db.cypher_query(
        "MATCH (binding:PlatformNativeStudyBinding {tenant_id:$tenant,platform_study_id:$study}) "
        "RETURN binding.binding_id,binding.native_version",
        {"tenant": TENANT, "study": STUDY})
    identity.update(bindingId=binding[0][0], nativeIdentity=native_study, nativeVersion=binding[0][1])
    request["checkpointPreconditions"]["osbNativeVersion"] = identity["nativeVersion"]
    for index, intent in enumerate(request["typedSourceIntents"]):
        for value in intent["source"]["values"]:
            if value["name"] in {"name", "formRef", "itemGroupRef"}:
                value["value"] += "-expiry"
        request["inputConservation"]["rows"][index]["target"]["valueHash"] = canonical_json_hash_ref(
            intent, schema_version="OsbTypedSourceIntentV1@1.0.0")
    request["inputConservation"]["rowSetHash"] = canonical_json_hash_ref(
        request["inputConservation"]["rows"], schema_version="ConservationCensusRowsV1@1.0.0")
    _refresh(candidate)
    payload["osbStudyIdentity"] = deepcopy(identity)
    payload["candidateRequestArtifact"] = deepcopy(candidate[1])
    artifact = stage_ref(payload)
    envelope, verification = signature(artifact)
    clock = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)

    class ExpiringStore(stage.NativeSourceDraftStageStore):
        expire_on_save = True
        wrote_receipt = False

        def save_receipt(self, receipt, reference):
            super().save_receipt(receipt, reference)
            self.wrote_receipt = True
            if self.expire_on_save:
                clock[0] = datetime.fromisoformat(payload["expiresAt"].replace("Z", "+00:00")) + timedelta(seconds=1)

    store = ExpiringStore()
    # These in-memory verifier proofs are synthetic test authority. Cryptographic
    # signer/receiver compatibility is covered by test_source_draft_stage_signing.
    def verify(value, signed):
        if value == payload and signed == envelope:
            return verification
        assert value == request and signed == candidate[2]
        return candidate[3]

    with db.transaction:
        db.cypher_query(
            """CREATE (:OsbInboundArtifact {tenant_id:$tenant,platform_study_id:$study,
                 payload_hash:$hash,kind:'osb-candidate-request',
                 payload_json:$payload,signed_envelope_json:$envelope})""",
            {"tenant": TENANT, "study": STUDY, "hash": candidate[1]["payloadHash"]["value"],
             "payload": canonical_json(request), "envelope": canonical_json(candidate[2])})
        stage.store_source_draft_stage_bytes(
            tenant_id=TENANT, platform_study_id=STUDY, bytes_value=canonical_json(payload).encode(),
            expected_hash=artifact["payloadHash"]["value"], signed_envelope=envelope,
            verify_signature=verify, store=store)
    before, _ = db.cypher_query("MATCH (node) RETURN count(node)")
    monkeypatch.setattr(stage, "datetime", Clock)

    def execute():
        return stage.stage_native_source_draft(
            tenant_id=TENANT, platform_study_id=STUDY, stage_artifact=artifact,
            verify_signature=verify, store=store)

    with pytest.raises(OsbCandidateSetError) as error:
        with db.transaction:
            execute()
    assert error.value.code == "OSB_SOURCE_DRAFT_STAGE_EXPIRED"
    assert store.wrote_receipt, "Expiry must exercise actual native writes and receipt insertion"
    assert db.cypher_query("MATCH (node) RETURN count(node)")[0] == before
    assert store.receipt(TENANT, STUDY, stage_version=payload["stageVersionId"]) is None
    for source_key in payload["selectedSourceKeys"]:
        assert NativeCapturePort().binding(f"{TENANT}|{STUDY}|{source_key}") is None
    store.expire_on_save = False
    clock[0] = datetime.now(UTC)
    with db.transaction:
        completed = execute()
    assert completed["payload"]["summary"] == {
        "selected": 2, "created": 2, "reused": 0, "blocked": 0, "withBlockers": 2}
    assert completed["payload"]["clinicalApproval"] is False
    assert completed["payload"]["releaseEligible"] is False
    committed, _ = db.cypher_query("MATCH (node) RETURN count(node)")
    with db.transaction:
        replayed = execute()
    assert replayed["replay"] is True and replayed["payload"] == completed["payload"]
    assert replayed["artifactRef"] == completed["artifactRef"]
    assert db.cypher_query("MATCH (node) RETURN count(node)")[0] == committed
