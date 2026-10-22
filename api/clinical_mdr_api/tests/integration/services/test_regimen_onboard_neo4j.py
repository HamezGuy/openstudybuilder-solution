"""The governed osb.regimen.onboard command through the worker route on the owned graph.

Command Center's worker posts a CommandEnvelopeV1 with the three regimen
contracts to POST /integrations/platform-control/commands/regimen. Everything
after authentication is the real runtime: command validation, the platform
command journal, the binding and scope checks, the native regimen service and
its writer lock. Synthetic prerequisites, labelled here: the worker principal
(the platform token is not minted), the active OSB binding node (create-or-bind
needs the external identity signer) and the receipt signer (the existing test
stub). Synthetic engineering evidence only.
"""

# Pytest resolves the imported fixture functions by parameter name.
# pylint: disable=redefined-outer-name

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neomodel import db
from starlette.middleware import Middleware
from starlette_context import context
from starlette_context.middleware import RawContextMiddleware
from verify_signed_command_publication import make_command

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json_hash_ref,
)
from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    command_intent,
)
from clinical_mdr_api.routers.integrations import native_identity
from clinical_mdr_api.services.integrations.platform_command import (
    ensure_platform_command_schema,
)
from clinical_mdr_api.services.integrations.regimen_onboard import (
    CAPABILITY,
    check_contract,
)
from clinical_mdr_api.tests.integration.services.test_authored_protocol_documents_neo4j import (  # noqa: F401  pylint: disable=unused-import
    native_visit_graph,
    protocol_client,
    protocol_graph,
)
from clinical_mdr_api.tests.integration.services.test_study_arm_lifecycle_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _fingerprint,
    _lock,
    lifecycle_client,
)
from clinical_mdr_api.tests.integration.services.test_study_regimen_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _regimen,
    _value_rows,
    platform_case,
    regimen_library,
)
from clinical_mdr_api.tests.unit.services import signed_command_test_fixtures
from common.auth.dependencies import (
    dummy_access_token_claims,
    dummy_auth_object,
    validate_token,
)
from common.exception_handlers import register_exception_handlers

pytestmark = [
    pytest.mark.estate_fixture,
    pytest.mark.parametrize(
        "native_visit_graph", ["study-only"], indirect=True, scope="module"
    ),
]
PATH = "/commands/regimen"
IDENTITY_KEYS = {"namespace": "accuratrials-osb", "objectType": "study-draft-root"}


@pytest.fixture
def worker(protocol_graph, platform_case, monkeypatch):
    """The worker route, a worker principal and the study's active OSB binding."""
    fixture, _ = protocol_graph
    study_uid = platform_case["study_uid"]
    platform_study_id = str(uuid4())
    tenant_id = fixture.auth.user.tenant_id
    actor = f"synthetic-regimen-worker-{fixture.nonce}"
    claims = dummy_access_token_claims(user_id=actor)
    claims.sub = actor
    claims.tenant_id = tenant_id
    auth = dummy_auth_object(claims)
    auth.user.purpose = "workflow-orchestration"
    auth.user.capabilities = {CAPABILITY}
    auth.user.roles = {"service"}
    auth.user.study_ids = {platform_study_id, study_uid}
    db.set_connection(url=fixture.dsn)
    ensure_platform_command_schema()
    db.cypher_query(
        "CREATE (:PlatformNativeStudyBinding {tenant_id:$tenant_id,"
        "platform_study_id:$platform_study_id,namespace:'accuratrials-osb',"
        "object_type:'study-draft-root',status:'active',binding_id:$binding_id,"
        "native_study_id:$study_uid,native_version:'synthetic'})",
        {
            "tenant_id": tenant_id,
            "platform_study_id": platform_study_id,
            "binding_id": str(uuid4()),
            "study_uid": study_uid,
        },
    )
    # The existing synthetic signer names the command tenant in its envelope.
    monkeypatch.setattr(signed_command_test_fixtures, "TENANT", tenant_id)
    publisher = signed_command_test_fixtures.StubPublisher()
    original = native_identity.execute_osb_atomic_signed_command
    monkeypatch.setattr(
        native_identity,
        "execute_osb_atomic_signed_command",
        lambda *args, **kwargs: original(*args, **kwargs, publisher=publisher),
    )
    monkeypatch.setattr(
        native_identity,
        "settings",
        SimpleNamespace(
            platform_commands_prototype_enabled=True, deployment_environment="test"
        ),
    )
    app = FastAPI(middleware=[Middleware(RawContextMiddleware)])
    register_exception_handlers(app)
    app.include_router(native_identity.router)

    async def worker_authentication():
        db.set_connection(url=fixture.dsn)
        context["auth"] = auth
        try:
            yield
        finally:
            db.close_connection()

    app.dependency_overrides[validate_token] = worker_authentication
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            auth=auth,
            actor=actor,
            tenant_id=tenant_id,
            platform_study_id=platform_study_id,
            study_uid=study_uid,
            publisher=publisher,
            fixture=fixture,
        )


def _command(worker, native_payload, **expected_changes):
    """Split a native regimen request into the three reviewed command parts."""
    regimen = deepcopy(native_payload)
    version = regimen.pop("expected_study_version")
    documents_hash = expected_changes.pop("documents_hash", None)
    documents = regimen.get("protocol_documents")
    if documents is not None:
        documents_hash = documents.pop("expected_content_hash")
    identity = {**IDENTITY_KEYS, "nativeIdentity": worker.study_uid}
    command = make_command(worker.tenant_id, worker.platform_study_id, worker.actor)
    command.update(
        action="osb.regimen.onboard",
        targetCapability=CAPABILITY,
        inputPayload={
            "contractVersion": "OsbRegimenOnboardCommandInputV1@1.0.0",
            "osbStudyIdentity": identity,
            "regimen": regimen,
            "reason": "Synthetic regimen onboarding through Command Center",
        },
        expectedTargetState={
            "contractVersion": "OsbRegimenOnboardExpectedStateV1@1.0.0",
            "osbStudyIdentity": identity,
            "studyVersion": version,
            "protocolDocumentsContentHash": documents_hash,
            **expected_changes,
        },
    )
    command["inputHash"] = canonical_json_hash_ref(
        command["inputPayload"], schema_version="CommandInputV1@1.0.0"
    )
    command["commandIntentHash"] = canonical_json_hash_ref(
        command_intent(command), schema_version="CommandIntentV1@1.0.0"
    )
    return command


def _post(worker, command):
    return worker.client.post(
        PATH, json=command, headers={"x-platform-study-id": worker.platform_study_id}
    )


def _code(response):
    """The machine code in the API's shared ErrorResponse message."""
    message = response.json()["message"]
    codes = [
        code
        for code in (
            "OSB_REGIMEN_EXPECTED_STATE_MISMATCH",
            "OSB_REGIMEN_NATIVE_REFUSED",
            "OSB_REGIMEN_STUDY_SCOPE_DENIED",
            "OSB_PLATFORM_COMMAND_SCOPE_DENIED",
        )
        if code in message
    ]
    assert len(codes) == 1, message
    return codes[0]


def _content_fingerprint(fixture):
    """The whole graph except the command lock, whose revision every call touches."""
    db.set_connection(url=fixture.dsn)
    nodes, _ = db.cypher_query(
        "MATCH (n) WHERE NOT n:PlatformCommandLock "
        "RETURN elementId(n), labels(n), properties(n) ORDER BY elementId(n)"
    )
    relationships, _ = db.cypher_query(
        "MATCH (a)-[r]->(b) RETURN elementId(r), type(r), properties(r) "
        "ORDER BY elementId(r)"
    )
    return repr((nodes, relationships))


def _arm_count(study_uid):
    return _value_rows(
        study_uid, "MATCH (value)-[:HAS_STUDY_ARM]->(arm:StudyArm) RETURN count(arm)"
    )[0][0]


def test_worker_regimen_command_executes_natively_and_replays_its_receipt(
    worker, platform_case, lifecycle_client
):
    case = platform_case
    payload = _regimen(lifecycle_client, case)
    command = _command(worker, payload)
    db.set_connection(url=case["fixture"].dsn)
    arms_before = _arm_count(worker.study_uid)

    response = _post(worker, command)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    receipt, effect = result["receipt"], result["effectPayload"]
    assert result["publicationMode"] == "signed" and result["replay"] is False
    assert receipt["status"] == "succeeded"
    assert receipt["commandId"] == command["commandId"]
    assert receipt["targetIdentity"] == worker.study_uid
    check_contract("effect", effect, "TEST_EFFECT_INVALID")
    assert effect["osbStudyIdentity"] == command["inputPayload"]["osbStudyIdentity"]
    for kind in effect["created"]:
        assert sorted(effect["created"][kind]) == sorted(
            entry["local_key"] for entry in payload.get(kind, [])
        ), kind
    assert len(effect["lifecycleEventUids"]) == 1

    # Native readback through the interactive routes.
    db.set_connection(url=case["fixture"].dsn)
    arms = effect["created"]["arms"]
    assert _arm_count(worker.study_uid) == arms_before + 2
    assert _value_rows(
        worker.study_uid,
        "MATCH (value)-[:HAS_STUDY_ARM]->(arm:StudyArm) WHERE arm.uid IN $uids "
        "RETURN arm.name ORDER BY arm.name",
        uids=list(arms.values()),
    ) == [["Regimen H active"], ["Regimen H placebo"]]
    documents = lifecycle_client.get(f"/studies/{worker.study_uid}/protocol-documents")
    assert documents.status_code == 200, documents.text
    assert documents.json()["content_hash"] == effect["protocolDocumentsContentHash"]
    assert documents.json()["study_value_version"] == effect["studyVersion"]
    bindings = {
        item["document_id"]: item["arm_uids"]
        for item in documents.json()["content"]["bindings"]
    }
    assert bindings["authored-arm-h"] == [arms["h-active"]]
    events = lifecycle_client.get(
        f"/studies/{worker.study_uid}/study-arms/{arms['h-active']}/lifecycle-events"
    )
    assert [event["uid"] for event in events.json()] == effect["lifecycleEventUids"]

    # The same command replays its original signed receipt and writes nothing
    # beyond touching the existing command lock.
    baseline = _content_fingerprint(case["fixture"])
    repeated = _post(worker, command)
    assert repeated.status_code == 200, repeated.text
    replay = repeated.json()["data"]
    assert replay["replay"] is True
    assert replay["signedReceiptEnvelope"] == result["signedReceiptEnvelope"]
    assert replay["effectPayload"] == effect
    assert worker.publisher.calls == 1
    assert _content_fingerprint(case["fixture"]) == baseline


def test_worker_regimen_without_documents_pins_the_current_document_hash(
    worker, platform_case, lifecycle_client
):
    case = platform_case
    payload = _regimen(lifecycle_client, case)
    payload.pop("protocol_documents")
    stale = _command(worker, payload, documents_hash="0" * 64)
    baseline = _fingerprint(case["fixture"])
    response = _post(worker, stale)
    assert response.status_code == 409, response.text
    assert _code(response) == "OSB_REGIMEN_EXPECTED_STATE_MISMATCH"
    assert _fingerprint(case["fixture"]) == baseline

    current = _command(worker, payload, documents_hash=case["content_hash"])
    response = _post(worker, current)
    assert response.status_code == 200, response.text
    effect = response.json()["data"]["effectPayload"]
    check_contract("effect", effect, "TEST_EFFECT_INVALID")
    # Command Center's receipt rule: no documents, the reviewed hash unchanged.
    assert effect["protocolDocumentsContentHash"] == case["content_hash"]
    documents = lifecycle_client.get(f"/studies/{worker.study_uid}/protocol-documents")
    assert documents.json()["content_hash"] == case["content_hash"]


@pytest.mark.parametrize(
    "case_name", ["stale-version", "stale-documents", "foreign-binding", "locked"]
)
def test_worker_regimen_refuses_without_any_write(
    worker, platform_case, lifecycle_client, case_name
):
    case = platform_case
    payload = _regimen(lifecycle_client, case)
    if case_name == "stale-version":
        command = _command(worker, payload, studyVersion="1")
        expected = (409, "OSB_REGIMEN_EXPECTED_STATE_MISMATCH")
    elif case_name == "stale-documents":
        payload["protocol_documents"]["expected_content_hash"] = "0" * 64
        command = _command(worker, payload)
        expected = (409, "OSB_REGIMEN_EXPECTED_STATE_MISMATCH")
    elif case_name == "foreign-binding":
        db.set_connection(url=case["fixture"].dsn)
        db.cypher_query(
            "MATCH (binding:PlatformNativeStudyBinding {platform_study_id:$id}) "
            "SET binding.native_study_id = 'Study_999999'",
            {"id": worker.platform_study_id},
        )
        command = _command(worker, payload)
        expected = (403, "OSB_REGIMEN_STUDY_SCOPE_DENIED")
    else:
        _lock(lifecycle_client, worker.study_uid, case["reasons"])
        command = _command(worker, payload)
        expected = (422, "OSB_REGIMEN_NATIVE_REFUSED")
    baseline = _fingerprint(case["fixture"])
    response = _post(worker, command)
    assert response.status_code == expected[0], response.text
    assert _code(response) == expected[1]
    assert _fingerprint(case["fixture"]) == baseline, case_name
    assert worker.publisher.calls == 0


def test_worker_regimen_requires_the_regimen_capability_and_study_scope(
    worker, platform_case, lifecycle_client
):
    case = platform_case
    command = _command(worker, _regimen(lifecycle_client, case))
    baseline = _fingerprint(case["fixture"])
    worker.auth.user.capabilities = {"package:release"}
    response = _post(worker, command)
    assert response.status_code == 403, response.text
    worker.auth.user.capabilities = {CAPABILITY}
    worker.auth.user.study_ids = {worker.platform_study_id}
    response = _post(worker, command)
    assert response.status_code == 403, response.text
    assert _code(response) == "OSB_REGIMEN_STUDY_SCOPE_DENIED"
    assert _fingerprint(case["fixture"]) == baseline
