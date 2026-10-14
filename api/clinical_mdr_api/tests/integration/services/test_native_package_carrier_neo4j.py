"""Carrier custody and command atomicity on an owned, empty Community graph.

All Cypher, current locked-head selection, identity receipt joins, carrier
capture, inspection, review and package writes are the actual owner runtime.
The existing CSL/CC builders produce the synthetic source/release chain. Only
the legacy USDM projection and signing authority are synthetic prerequisites:
this does not qualify native projection, JWT crypto or a clinical release.
"""

# pytest fixtures are injected by name; explicit graph equality is intentional.
# pylint: disable=redefined-outer-name,use-implicit-booleaness-not-comparison

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from neomodel import db
from verify_signed_command_publication import make_command

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import canonical_json
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandError,
    PlatformCommandPrincipalV1,
    command_intent,
)
from clinical_mdr_api.services.integrations import (
    native_package_artifact_custody as custody,
)
from clinical_mdr_api.services.integrations import native_package_carrier_v2 as service
from clinical_mdr_api.services.integrations import native_package_state_v2 as native
from clinical_mdr_api.services.integrations.atomic_signed_command import (
    execute_osb_atomic_signed_command,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.native_package_carrier_inspection import (
    SECTIONS,
)
from clinical_mdr_api.services.integrations.platform_command import (
    Neo4jOsbPlatformCommandStore,
    ensure_platform_command_schema,
)
from clinical_mdr_api.tests.fixtures.null_adjudication_neo4j import verified_fixture
from clinical_mdr_api.tests.unit.services import (
    signed_command_test_fixtures,
)
from clinical_mdr_api.tests.unit.services import (
    test_native_package_carrier_flow as pure,
)

pytestmark = pytest.mark.estate_fixture
OWNED_LABELS = {
    "PlatformNativeStudyBinding",
    "DomainStudyScope",
    "StudyRoot",
    "StudyValue",
    "PlatformNativeIdentityAudit",
    "PlatformNativeIdentityEffect",
    "PlatformCommandLock",
    "PlatformCommandAuditLock",
    "PlatformCommandPreparation",
    "PlatformCommandEffect",
    "PlatformCommandAudit",
    "PlatformCommandOutbox",
    "PlatformCommandPublicationStream",
    "OsbInboundCarrierArtifact",
    "OsbCarrierNativeCapture",
    "OsbCarrierInspection",
    "OsbCarrierInspectionEntry",
    "OsbSpecialistReviewEvidenceV2",
    "OsbNativePackageV2",
}


def graph_state():
    """Compare actual persisted properties, including command journal/outbox."""
    nodes, relationships = (
        db.cypher_query(
            "MATCH (n) RETURN elementId(n),labels(n),properties(n) ORDER BY elementId(n)"
        )[0],
        db.cypher_query(
            "MATCH (a)-[r]->(b) RETURN elementId(a),type(r),elementId(b),properties(r) "
            "ORDER BY elementId(r)"
        )[0],
    )
    for row in nodes:
        row[1].sort()
    return nodes, relationships


def seed_native_identity(fixture, identity):
    """Synthetic retained identity authority; its real reader must resolve it."""
    root = fixture["nativeState"]["root"]
    scope = fixture["scope"]
    receipt = {
        "contractVersion": "1.0.0",
        "receiptId": identity["evidence"]["receiptId"],
        "tenantId": scope["platformTenantId"],
        "platformStudyId": scope["platformStudyId"],
        "targetSystem": "osb",
        "namespace": "accuratrials-osb",
        "objectType": "study-draft-root",
        "nativeIdentity": root["nativeStudyId"],
        "nativeVersion": root["nativeVersion"],
        "targetStateHash": pure.h(
            {
                "nativeIdentity": root["nativeStudyId"],
                "nativeVersion": root["nativeVersion"],
                "status": root["nativeStatus"].lower(),
                "domainBindingId": root["bindingId"],
            },
            "OSBNativeStudyRootStateV1@1.0.0",
        ),
    }
    envelope = {"syntheticIdentityReceipt": receipt["receiptId"]}
    identity["evidence"].update(
        receiptPayloadHash=pure.h(receipt, "NativeIdentityBindingReceiptV1@1.0.0"),
        signedEnvelopeHash=pure.h(envelope, "SignedArtifactEnvelopeV1@1.0.0"),
    )
    db.cypher_query(
        """CREATE (:PlatformNativeStudyBinding {tenant_id:$tenant,platform_study_id:$study,
          namespace:'accuratrials-osb',object_type:'study-draft-root',status:'active',
          binding_id:$binding,native_study_id:$native,native_version:$version})
        CREATE (:DomainStudyScope {tenant_id:$tenant,study_uid:$native,status:'active'})
        CREATE (study:StudyRoot {uid:$native})-[:LATEST]->(value:StudyValue {
          study_title:$title,study_number:$number,study_acronym:$acronym,project_number:$project})
        CREATE (study)-[:LATEST_LOCKED {version:$version,status:'Locked',start_date:datetime($stamp)}]->(value)
        CREATE (study)-[:LATEST_DRAFT {version:'1.9',status:'Draft',
          start_date:datetime('2026-09-08T00:00:00Z'),end_date:datetime($draft_end)}]->(value)
        CREATE (audit:PlatformNativeIdentityAudit {tenant_id:$tenant,platform_study_id:$study,
          receipt_id:$receipt_id,action:'PLATFORM_NATIVE_IDENTITY_PUBLISHED'})
        CREATE (audit)-[:AUDITS_EFFECT]->(:PlatformNativeIdentityEffect {
          tenant_id:$tenant,platform_study_id:$study,namespace:'accuratrials-osb',
          object_type:'study-draft-root',receipt_json:$receipt,signed_envelope_json:$envelope})""",
        {
            "tenant": scope["platformTenantId"],
            "study": scope["platformStudyId"],
            "binding": root["bindingId"],
            "native": root["nativeStudyId"],
            "version": root["nativeVersion"],
            "title": root["title"],
            "number": root["studyNumber"],
            "acronym": root["acronym"],
            "project": root["projectNumber"],
            "stamp": root["versionTimestamp"],
            "draft_end": root["draftEndedAt"],
            "receipt_id": receipt["receiptId"],
            "receipt": canonical_json(receipt),
            "envelope": canonical_json(envelope),
        },
    )


@pytest.fixture()
def carrier_graph(monkeypatch):
    dsn, owner = verified_fixture()
    db.set_connection(url=dsn)
    assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == [[0]]
    ensure_platform_command_schema()
    constraint_rows = db.cypher_query("SHOW CONSTRAINTS YIELD name RETURN name")[0]
    assert {
        "osb_carrier_artifact_identity",
        "osb_carrier_inspection_identity",
        "osb_carrier_inspection_entry_identity",
        "osb_specialist_review_v2_identity",
    } <= {row[0] for row in constraint_rows}
    fixture = copy.deepcopy(pure.prepared("native-single"))
    identity = pure.identity(fixture)
    seed_native_identity(fixture, identity)
    exported = {
        key: copy.deepcopy(fixture["canonicalDefinition"][key])
        for key in ("document", "mappingReport", "nativeRecords")
    }
    # Explicit boundary: do not claim this independently generated USDM source
    # was projected from the synthetic StudyRoot written above.
    monkeypatch.setattr(
        native,
        "USDMService",
        lambda: SimpleNamespace(
            get_by_uid_with_report=lambda *_args, **_kwargs: copy.deepcopy(exported)
        ),
    )
    scope = {
        "tenant_id": fixture["scope"]["platformTenantId"],
        "platform_study_id": fixture["scope"]["platformStudyId"],
    }

    def transfer(payload, reference):
        return custody.store_carrier_artifact_bytes(
            **scope,
            artifact_ref=reference,
            bytes_value=canonical_json(payload).encode("utf-8"),
            expected_hash=reference["payloadHash"]["value"],
        )

    with db.transaction:
        for item in fixture["artifacts"]:
            if item["artifact"]["kind"] not in {
                "osb-package-native-state",
                "osb-canonical-study-definition",
                "transformation-checkpoint",
            }:
                transfer(item["payload"], item["artifact"])
    carrier_refs = [
        item["artifact"]
        for item in fixture["artifacts"]
        if item["artifact"]["kind"] == "accepted-study-exchange-carrier"
    ]
    assert len(carrier_refs) == 1
    carrier_ref = carrier_refs[0]
    capture_input = {
        "contractVersion": service.CAPTURE_INPUT_SCHEMA,
        "sourceBasis": fixture["sourceBasis"],
        "carrierArtifact": carrier_ref,
        "osbBinding": fixture["crosswalk"]["osbBinding"],
        "nativeEvidenceBasis": fixture["nativeEvidenceBasis"],
        "osbStudyIdentity": identity,
    }
    actor = "synthetic:neo4j-carrier-owner"
    principal = PlatformCommandPrincipalV1(
        tenant_id=scope["tenant_id"],
        study_ids=(scope["platform_study_id"],),
        subject=actor,
        actor_chain=({"subject": actor, "type": "human"},),
        roles=("osb-specialist",),
        purpose="workflow-orchestration",
        capabilities=("package:release",),
    )
    monkeypatch.setattr(signed_command_test_fixtures, "TENANT", scope["tenant_id"])
    graph = SimpleNamespace(
        f=fixture,
        scope=scope,
        root=fixture["nativeState"]["root"],
        identity=identity,
        capture_input=capture_input,
        transfer=transfer,
        service=service,
        custody=custody,
        principal=principal,
        actor=actor,
        dsn=dsn,
        store=Neo4jOsbPlatformCommandStore(environment="prototype"),
        publisher=signed_command_test_fixtures.StubPublisher(),
    )
    try:
        yield graph
    finally:
        # Empty-at-entry verified fixture is exclusive to this gate. Refuse any
        # foreign-scoped node before removing this test's entire authored graph.
        scopes = db.cypher_query(
            "MATCH (n) WHERE n.tenant_id IS NOT NULL RETURN DISTINCT n.tenant_id"
        )[0]
        assert all(row == [scope["tenant_id"]] for row in scopes)
        assert all(
            set(row[0]) <= OWNED_LABELS
            for row in db.cypher_query("MATCH (n) RETURN labels(n)")[0]
        )
        db.cypher_query("MATCH (n) DETACH DELETE n")
        assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == [[0]]
        db.close_connection()
        print(
            json.dumps({"fixtureOwner": owner, "carrierGraphEmptyAfterCleanup": True})
        )


def capture_command(graph):
    command = make_command(
        graph.scope["tenant_id"], graph.scope["platform_study_id"], graph.actor
    )
    command.update(
        action="osb.carrier-native-state.capture", inputPayload=graph.capture_input
    )
    command["inputHash"] = pure.h(command["inputPayload"], "CommandInputV1@1.0.0")
    command["commandIntentHash"] = pure.h(
        command_intent(command), "CommandIntentV1@1.0.0"
    )
    return command


def execute_capture(graph, command, after_capture=lambda: None, store=None):
    def handler(_transaction):
        captured = service.capture_carrier_native_state(
            **graph.scope, input_payload=graph.capture_input
        )
        after_capture()
        return {
            "targetIdentity": captured["nativeStudyId"],
            "targetVersion": captured["nativeVersion"],
            "targetState": {"nativeStateHash": captured["nativeStateHash"]},
            "effectPayload": captured,
        }

    return execute_osb_atomic_signed_command(
        command,
        graph.principal,
        "osb",
        store or graph.store,
        handler,
        publisher=graph.publisher,
    )


def captured_checkpoint(graph, captured):
    state = custody.load_carrier_artifact(
        **graph.scope, artifact_ref=captured["nativeStateArtifact"]
    )
    definition = custody.load_carrier_artifact(
        **graph.scope, artifact_ref=captured["canonicalDefinitionArtifact"]
    )
    graph.built = pure.csl_build(
        {
            "fixture": graph.f,
            "nativeState": state,
            "nativeStateArtifact": captured["nativeStateArtifact"],
            "canonicalDefinition": definition,
        },
        "checkpoint",
    )
    graph.transfer(graph.built["checkpoint"], graph.built["artifact"])
    graph.request = {
        **graph.scope,
        "transformation_checkpoint_artifact": graph.built["artifact"],
        "osb_study_identity": graph.identity,
    }


def test_native_carrier_custody_reconnect_capture_replay_review_and_package(
    carrier_graph,
):
    graph = carrier_graph
    command = capture_command(graph)
    first = execute_capture(graph, command)
    captured = first["effectPayload"]
    retained = graph_state()
    assert first["publicationMode"] == "signed" and not first["replay"]
    db.close_connection()
    db.set_connection(url=graph.dsn)
    second = execute_capture(graph, command)
    assert second["replay"]
    assert second["signedReceiptEnvelope"] == first["signedReceiptEnvelope"]
    assert graph.publisher.calls == 1
    # A command replay advances only its existing lock revision; no custody,
    # inspection, signed receipt, journal or outbox record may be appended.
    assert len(graph_state()[0]) == len(retained[0])
    assert [row for row in graph_state()[0] if "PlatformCommandLock" not in row[1]] == [
        row for row in retained[0] if "PlatformCommandLock" not in row[1]
    ]
    assert graph_state()[1] == retained[1]
    with db.transaction:
        repeated_capture = service.capture_carrier_native_state(
            **graph.scope, input_payload=graph.capture_input
        )
    assert repeated_capture == captured
    captured_checkpoint(graph, captured)
    context = service.carrier_review_context(**graph.request)
    total_entries = 0
    for section in SECTIONS:
        offset = 0
        while True:
            page = service.carrier_review_page(
                **graph.request, section=section, offset=offset, limit=17
            )
            assert page["total"] == context["inspection"]["counts"][section]
            for item in page["items"]:
                entry = service.carrier_review_entry(
                    **graph.request, section=section, position=item["position"]
                )
                assert json.loads(entry["bytes"]) == item["value"]
                total_entries += 1
            if page["nextOffset"] is None:
                break
            assert page["nextOffset"] > offset
            offset = page["nextOffset"]
    assert total_entries == sum(context["inspection"]["counts"].values())
    with db.transaction:
        context, release_input = pure.release_prerequisites(graph)
    review_rows = db.cypher_query(
        "MATCH (n:OsbSpecialistReviewEvidenceV2) RETURN n.payload_json,n.artifact_ref_json"
    )[0]
    with db.transaction:
        pure.record_review(graph)
    assert (
        db.cypher_query(
            "MATCH (n:OsbSpecialistReviewEvidenceV2) RETURN n.payload_json,n.artifact_ref_json"
        )[0]
        == review_rows
    )
    arguments = {
        **graph.scope,
        "input_payload": release_input,
        "actor": "synthetic-package-owner",
        "expected_target_state": context["nativeStateHash"],
    }
    baseline = graph_state()
    with pytest.raises(RuntimeError, match="after actual package insertion"):
        with db.transaction:
            service.generate_carrier_native_package(**arguments)
            raise RuntimeError("after actual package insertion")
    assert graph_state() == baseline
    with db.transaction:
        package = service.generate_carrier_native_package(**arguments)
    db.close_connection()
    db.set_connection(url=graph.dsn)
    with db.transaction:
        replay = service.generate_carrier_native_package(**arguments)
    assert replay["replay"] and not package["replay"]
    assert replay["bytes"] == package["bytes"]
    assert replay["artifactRef"] == package["artifactRef"]
    assert db.cypher_query("MATCH (n:OsbNativePackageV2) RETURN count(n)")[0] == [[1]]
    print(
        json.dumps(
            {
                "entriesReadFromNeo4j": total_entries,
                "packageVersion": "2.3.0",
                "projectionBoundary": "synthetic USDM fixture",
            }
        )
    )


@pytest.mark.parametrize("failure", ["capture", "signer", "final-publication"])
def test_native_carrier_capture_and_journal_rollback_together(carrier_graph, failure):
    graph = carrier_graph
    baseline = graph_state()
    command = capture_command(graph)

    def fail_after_capture():
        assert db.cypher_query("MATCH (n:OsbCarrierNativeCapture) RETURN count(n)")[
            0
        ] == [[2]]
        assert (
            db.cypher_query("MATCH (n:OsbCarrierInspectionEntry) RETURN count(n)")[0][
                0
            ][0]
            > 0
        )
        if failure == "capture":
            raise RuntimeError("injected capture failure")

    class FailAfterPublicationStore:
        def serializable(self, value, callback):
            def fail_after_write(transaction):
                callback(transaction)
                assert db.cypher_query(
                    "MATCH (n:PlatformCommandOutbox) RETURN count(n)"
                )[0] == [[1]]
                raise RuntimeError("injected final-publication failure")

            return graph.store.serializable(value, fail_after_write)

    graph.publisher.fail = failure == "signer"
    expected_error = PlatformCommandError if failure == "signer" else RuntimeError
    with pytest.raises(expected_error) as caught:
        execute_capture(
            graph,
            command,
            fail_after_capture,
            FailAfterPublicationStore() if failure == "final-publication" else None,
        )
    if failure == "signer":
        assert caught.value.code == "SIGNED_PUBLICATION_DEPENDENCY_UNAVAILABLE"
    else:
        assert str(caught.value) == f"injected {failure} failure"
    assert graph.publisher.calls == (0 if failure == "capture" else 1)
    assert graph_state() == baseline
    graph.publisher.fail = False
    result = execute_capture(graph, command)
    assert result["publicationMode"] == "signed" and not result["replay"]


def test_native_carrier_rejects_foreign_scope_and_coherent_descriptor_relabel(
    carrier_graph,
):
    graph = carrier_graph
    command = capture_command(graph)
    captured = execute_capture(graph, command)["effectPayload"]
    captured_checkpoint(graph, captured)
    baseline = graph_state()
    with pytest.raises(OsbCandidateSetError):
        with db.transaction:
            db.cypher_query(
                "MATCH (n:OsbCarrierNativeCapture) REMOVE n:OsbCarrierNativeCapture"
            )
            service.carrier_review_context(**graph.request)
    assert graph_state() == baseline
    for field in ("tenant_id", "platform_study_id"):
        foreign = {**graph.scope, field: str(uuid4())}
        with pytest.raises(OsbCandidateSetError):
            custody.load_carrier_artifact(
                **foreign, artifact_ref=captured["nativeStateArtifact"]
            )
        with pytest.raises(OsbCandidateSetError):
            service.capture_carrier_native_state(
                **foreign, input_payload=graph.capture_input
            )
        with pytest.raises(OsbCandidateSetError):
            service.carrier_review_context(**{**graph.request, **foreign})
    original = captured["nativeStateArtifact"]
    payload = custody.load_carrier_artifact(**graph.scope, artifact_ref=original)
    fields = {
        key: value
        for key, value in original.items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    fields["producerVersion"] = "coherently relabeled"
    altered = pure.legacy.PACKAGE["_artifact_ref"](fields)
    with pytest.raises(OsbCandidateSetError) as error:
        graph.transfer(payload, altered)
    assert error.value.code == "OSB_CARRIER_ARTIFACT_TRANSFER_CONFLICT"
    assert graph_state() == baseline
