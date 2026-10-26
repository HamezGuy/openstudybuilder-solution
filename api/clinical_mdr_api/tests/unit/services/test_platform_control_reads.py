"""Platform-control reads are refused where the commands that write them are (OP-23).

The artifact and receipt GETs answer 503 in production and with the prototype
off, exactly as the uploads and commands do, before any scope check or read.
The command receipt GET also requires the capability its command ran under, so
a workflow token that only lists the platform study cannot read it.
"""

# Pytest resolves the fixture by parameter name.
# pylint: disable=redefined-outer-name

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinical_mdr_api.routers.integrations import native_identity, source_draft_stage
from common.auth.dependencies import validate_token
from common.config import settings

TENANT = "synthetic-tenant"
PLATFORM_STUDY = "synthetic-platform-study"
HEADERS = {"x-platform-study-id": PLATFORM_STUDY}

READS = [
    "/commands/synthetic-command/receipt",
    "/artifacts/osb-candidate-sets/synthetic",
    "/artifacts/native-evidence-sets/synthetic",
    "/artifacts/specialist-reviews/synthetic",
    "/artifacts/native-packages-v2/synthetic",
    "/artifacts/source-draft-stage-receipts/synthetic",
]


class Refused(AssertionError):
    pass


def _refuse(*_args, **_kwargs):
    raise Refused("the read ran")


def _principal(capabilities):
    return SimpleNamespace(
        purpose="workflow-orchestration",
        capabilities=set(capabilities),
        tenant_id=TENANT,
        study_ids={PLATFORM_STUDY},
    )


def _client(monkeypatch, principal, store=None) -> TestClient:
    monkeypatch.setattr(native_identity, "user", lambda: principal)
    monkeypatch.setattr(native_identity, "db", SimpleNamespace(cypher_query=_refuse))
    monkeypatch.setattr(
        native_identity,
        "platform_command_store",
        store or SimpleNamespace(lookup_with_capability=_refuse),
    )
    monkeypatch.setattr(
        source_draft_stage,
        "NativeSourceDraftStageStore",
        lambda: SimpleNamespace(receipt=_refuse),
    )
    app = FastAPI()
    app.include_router(native_identity.router)

    async def authenticated():
        return None

    app.dependency_overrides[validate_token] = authenticated
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def full_principal():
    return _principal(
        {"candidate:read", "package:release", "draft:read", "draft:stage"}
    )


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize(
    "prototype, environment",
    [(True, "production"), (True, "prod"), (False, "test"), (False, "production")],
)
def test_reads_answer_503_where_commands_are_off(
    monkeypatch, full_principal, path, prototype, environment
):
    monkeypatch.setattr(settings, "platform_commands_prototype_enabled", prototype)
    monkeypatch.setattr(settings, "deployment_environment", environment)
    response = _client(monkeypatch, full_principal).get(path, headers=HEADERS)
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "OSB_PLATFORM_COMMANDS_DISABLED"


@pytest.mark.parametrize("path", READS[1:])
def test_artifact_reads_still_need_their_capability_when_on(monkeypatch, path):
    monkeypatch.setattr(settings, "platform_commands_prototype_enabled", True)
    monkeypatch.setattr(settings, "deployment_environment", "test")
    response = _client(monkeypatch, _principal(set())).get(path, headers=HEADERS)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "OSB_PLATFORM_COMMAND_SCOPE_DENIED"


def _receipt_store(capability):
    calls = []

    def lookup_with_capability(tenant_id, platform_study_id, command_id):
        calls.append((tenant_id, platform_study_id, command_id))
        if command_id != "synthetic-command":
            return None
        return capability, {"receipt": {"commandId": command_id}}

    return SimpleNamespace(lookup_with_capability=lookup_with_capability), calls


@pytest.fixture
def commands_on(monkeypatch):
    monkeypatch.setattr(settings, "platform_commands_prototype_enabled", True)
    monkeypatch.setattr(settings, "deployment_environment", "test")


@pytest.mark.usefixtures("commands_on")
def test_receipt_needs_the_commands_target_capability(monkeypatch):
    store, calls = _receipt_store("package:release")
    # An identity-intent token: the platform study, but not the command's capability.
    api = _client(monkeypatch, _principal({"native-identity:bind"}), store)
    response = api.get(READS[0], headers=HEADERS)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "OSB_PLATFORM_COMMAND_RECEIPT_SCOPE_DENIED"
    assert calls == [(TENANT, PLATFORM_STUDY, "synthetic-command")]


@pytest.mark.usefixtures("commands_on")
def test_receipt_with_the_capability_is_returned_unchanged(monkeypatch):
    store, _ = _receipt_store("package:release")
    api = _client(monkeypatch, _principal({"package:release"}), store)
    response = api.get(READS[0], headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "data": {"receipt": {"commandId": "synthetic-command"}},
    }


@pytest.mark.usefixtures("commands_on")
def test_receipt_with_no_stored_capability_is_refused(monkeypatch):
    store, _ = _receipt_store("")
    api = _client(monkeypatch, _principal({"package:release"}), store)
    response = api.get(READS[0], headers=HEADERS)
    assert response.status_code == 403, response.text


@pytest.mark.usefixtures("commands_on")
def test_unknown_receipt_is_404_and_scope_is_checked_first(monkeypatch):
    store, calls = _receipt_store("package:release")
    api = _client(monkeypatch, _principal({"package:release"}), store)
    response = api.get("/commands/other/receipt", headers=HEADERS)
    assert response.status_code == 404, response.text
    outside = api.get(READS[0], headers={"x-platform-study-id": "another-study"})
    assert outside.status_code == 403, outside.text
    assert calls == [(TENANT, PLATFORM_STUDY, "other")]


def test_lookup_keeps_its_published_shape(monkeypatch):
    rows = [
        [
            "effect-1",
            "intent-hash",
            '{"receiptId": "r-1"}',
            "{}",
            None,
            None,
            "signed",
            "package:release",
        ]
    ]
    store_module = native_identity.platform_command_store
    monkeypatch.setattr(
        "clinical_mdr_api.services.integrations.platform_command.db",
        SimpleNamespace(cypher_query=lambda *_args: (rows, [])),
    )
    capability, published = store_module.lookup_with_capability("t", "p", "c")
    assert capability == "package:release"
    assert published == store_module.lookup("t", "p", "c")
    assert published["receipt"] == {"receiptId": "r-1"}
    assert published["publicationMode"] == "signed"
    assert "targetCapability" not in published
