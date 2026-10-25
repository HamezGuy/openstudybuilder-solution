"""Offline checks of the governed osb.regimen.onboard translation (O4)."""

# Pytest resolves the fixture by parameter name.
# pylint: disable=redefined-outer-name

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinical_mdr_api.generated.platform_contracts.platform_command_v1 import (
    PlatformCommandError,
)
from clinical_mdr_api.models.study_selections.study_regimen import StudyRegimenCreated
from clinical_mdr_api.routers.integrations import native_identity
from clinical_mdr_api.services.integrations import regimen_onboard
from clinical_mdr_api.tests.unit.services.test_authored_protocol_documents import (
    authored_source,
)
from common.auth.dependencies import validate_token
from common.exceptions import (
    AlreadyExistsException,
    BusinessLogicException,
    InternalServerError,
)

# Command Center 8d002f4, contracts/platform-control-plane/v1, byte for byte.
COMMAND_CENTER_SHA256 = {
    "input": "4c406572b8f9b9844d1f853e88d39087c4a7d4a5a09c40fa27d17234c29bf861",
    "expected": "0d6ce7118bd1e4cf27d2156026022374eb371527549ce20f1092e0bed3292bbb",
    "effect": "b993a4c979f638b64bc4fcbcf453a51762c81be629aad47e42982270ab61ae42",
}
STUDY = "Study_000001"
PLATFORM = "11111111-1111-4111-8111-111111111111"
IDENTITY = {
    "namespace": "accuratrials-osb",
    "objectType": "study-draft-root",
    "nativeIdentity": STUDY,
}
HASH = "a" * 64


def regimen(documents=False):
    value = {
        "arms": [
            {"local_key": "a", "name": "Synthetic arm A", "short_name": "A"},
            {"local_key": "b", "name": "Synthetic arm B", "short_name": "B"},
        ],
        "branch_arms": [
            {
                "local_key": "a-branch",
                "name": "Synthetic branch",
                "short_name": "AB",
                "arm_uid": {"local_key": "a"},
            }
        ],
        "cohorts": [
            {
                "local_key": "pool",
                "name": "Synthetic cohort",
                "short_name": "C",
                "arm_uids": ["StudyArm_000001", {"local_key": "b"}],
            }
        ],
        "lifecycle_events": [
            {
                "target_kind": "arm",
                "target": {"local_key": "a"},
                "lifecycle_state": "Synthetic entering",
                "effective_date": "2046-03-04T05:06:07+00:00",
                "decision_reference": "Synthetic decision",
                "change_reason": "Synthetic onboarding",
            }
        ],
    }
    if documents:
        _, content = authored_source()
        content.bindings[0].arm_uids = ["StudyArm_000001", "token-a"]
        for binding in content.bindings[1:]:
            binding.arm_uids = ["StudyArm_000001"]
        value["protocol_documents"] = {
            "reason": "Synthetic appendix",
            "arm_tokens": {"token-a": "a"},
            "content": content.model_dump(mode="json"),
        }
    return value


def command(documents=False, **expected):
    return {
        "action": regimen_onboard.ACTION,
        "targetCapability": regimen_onboard.CAPABILITY,
        "platformStudyId": PLATFORM,
        "inputPayload": {
            "contractVersion": "OsbRegimenOnboardCommandInputV1@1.0.0",
            "osbStudyIdentity": dict(IDENTITY),
            "regimen": regimen(documents),
            "reason": "Synthetic regimen onboarding",
        },
        "expectedTargetState": {
            "contractVersion": "OsbRegimenOnboardExpectedStateV1@1.0.0",
            "osbStudyIdentity": dict(IDENTITY),
            "studyVersion": "None",
            "protocolDocumentsContentHash": HASH,
            **expected,
        },
    }


def created(documents_hash=None):
    return StudyRegimenCreated(
        study_uid=STUDY,
        study_version="None",
        arms={"a": "StudyArm_000002", "b": "StudyArm_000003"},
        branch_arms={"a-branch": "StudyBranchArm_000001"},
        cohorts={"pool": "StudyCohort_000001"},
        elements={},
        compounds={},
        compound_dosings={},
        design_cells={},
        lifecycle_event_uids=["StudyArmLifecycleEvent_000001"],
        protocol_documents_content_hash=documents_hash,
    )


@pytest.fixture
def native(monkeypatch):
    calls = []
    state = {"binding": STUDY, "result": created(), "error": None}

    def create(_self, study_uid, request, *, expected_documents_hash):
        calls.append((study_uid, request, expected_documents_hash))
        if state["error"] is not None:
            # Each test sets an exception instance before calling.
            raise state["error"]  # pylint: disable=raising-bad-type
        return state["result"]

    monkeypatch.setattr(
        regimen_onboard,
        "active_osb_binding",
        lambda tenant, platform: {
            "bindingId": "binding",
            "nativeIdentity": state["binding"],
            "nativeVersion": "synthetic",
        },
    )
    monkeypatch.setattr(regimen_onboard.StudyRegimenService, "create", create)
    return state, calls


def run(value, study_ids=(PLATFORM, STUDY)):
    return regimen_onboard.onboard_regimen(
        value, tenant_id="tenant", study_ids=set(study_ids)
    )


def refusal(value, code, status, **kwargs):
    with pytest.raises(PlatformCommandError) as error:
        run(value, **kwargs)
    assert (error.value.code, error.value.status_code) == (code, status)


def test_vendored_contracts_are_command_center_bytes():
    for kind, name in regimen_onboard.SCHEMAS.items():
        data = (regimen_onboard.SCHEMA_DIRECTORY / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == COMMAND_CENTER_SHA256[kind], name


def test_native_request_carries_both_reviewed_preconditions():
    plain = regimen_onboard.native_request(
        command()["inputPayload"], command()["expectedTargetState"]
    )
    assert plain.expected_study_version == "None"
    assert plain.protocol_documents is None
    assert plain.keys("arms") == ["a", "b"]
    # The command's reason is recorded on each new arm's Create action.
    assert plain.change_reason == "Synthetic regimen onboarding"
    value = command(documents=True)
    with_documents = regimen_onboard.native_request(
        value["inputPayload"], value["expectedTargetState"]
    )
    assert with_documents.protocol_documents.expected_content_hash == HASH
    assert with_documents.protocol_documents.arm_tokens == {"token-a": "a"}
    assert (
        "expected_content_hash"
        not in value["inputPayload"]["regimen"]["protocol_documents"]
    )


@pytest.mark.parametrize(
    "path,value,code",
    [
        (("regimen", "expected_study_version"), "None", "OSB_REGIMEN_INPUT_INVALID"),
        (("regimen", "arms", 0, "local_key"), "-bad", "OSB_REGIMEN_INPUT_INVALID"),
        (("osbStudyIdentity", "namespace"), "other", "OSB_REGIMEN_INPUT_INVALID"),
        (("reason",), "    ", "OSB_REGIMEN_INPUT_INVALID"),
        (
            ("regimen", "lifecycle_events", 0, "effective_date"),
            "2046-03-04T05:06:07",
            "OSB_REGIMEN_INPUT_INVALID",
        ),
    ],
)
def test_input_contract_refuses_before_any_native_call(native, path, value, code):
    _, calls = native
    candidate = command()
    target = candidate["inputPayload"]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    refusal(candidate, code, 422)
    assert not calls


def test_document_precondition_cannot_travel_inside_the_regimen(native):
    _, calls = native
    candidate = command(documents=True)
    candidate["inputPayload"]["regimen"]["protocol_documents"][
        "expected_content_hash"
    ] = HASH
    refusal(candidate, "OSB_REGIMEN_INPUT_INVALID", 422)
    assert not calls


def test_expected_state_must_exist_validate_and_name_the_same_study(native):
    _, calls = native
    missing = command()
    missing["expectedTargetState"] = None
    refusal(missing, "OSB_REGIMEN_EXPECTED_STATE_REQUIRED", 422)
    refusal(
        command(protocolDocumentsContentHash="ABC"),
        "OSB_REGIMEN_EXPECTED_STATE_INVALID",
        422,
    )
    other = command()
    other["expectedTargetState"]["osbStudyIdentity"]["nativeIdentity"] = "Study_9"
    refusal(other, "OSB_REGIMEN_IDENTITY_MISMATCH", 422)
    assert not calls


def test_local_key_graph_is_refused_by_the_native_input(native):
    _, calls = native
    candidate = command()
    candidate["inputPayload"]["regimen"]["cohorts"][0]["arm_uids"][1] = {
        "local_key": "undeclared"
    }
    refusal(candidate, "OSB_REGIMEN_INPUT_INVALID", 422)
    assert not calls


def test_study_must_be_the_active_binding_in_token_scope(native):
    state, calls = native
    refusal(command(), "OSB_REGIMEN_STUDY_SCOPE_DENIED", 403, study_ids=(PLATFORM,))
    state["binding"] = "Study_000009"
    refusal(command(), "OSB_REGIMEN_STUDY_SCOPE_DENIED", 403)
    assert not calls


@pytest.mark.parametrize(
    "error,code,status",
    [
        (
            AlreadyExistsException(msg="Study version changed"),
            "OSB_REGIMEN_EXPECTED_STATE_MISMATCH",
            409,
        ),
        (
            BusinessLogicException(msg="Study is locked."),
            "OSB_REGIMEN_NATIVE_REFUSED",
            422,
        ),
    ],
)
def test_native_refusals_are_terminal_and_keep_their_message(
    native, error, code, status
):
    state, _ = native
    state["error"] = error
    with pytest.raises(PlatformCommandError) as raised:
        run(command())
    assert (raised.value.code, raised.value.status_code) == (code, status)
    assert str(raised.value) == error.msg


def test_native_server_errors_stay_retryable(native):
    state, _ = native
    state["error"] = InternalServerError("synthetic outage")
    with pytest.raises(InternalServerError):
        run(command())


def test_effect_without_documents_reports_the_checked_hash(native):
    state, calls = native
    effect = run(command())
    ((study_uid, request, expected_hash),) = calls
    assert (study_uid, expected_hash) == (STUDY, HASH)
    assert request.expected_study_version == "None"
    payload = effect["effectPayload"]
    regimen_onboard.check_contract("effect", payload, "TEST")
    assert payload["protocolDocumentsContentHash"] == HASH
    assert payload["created"]["arms"] == {
        "a": "StudyArm_000002",
        "b": "StudyArm_000003",
    }
    assert payload["created"]["elements"] == {}
    assert payload["lifecycleEventUids"] == ["StudyArmLifecycleEvent_000001"]
    assert effect["targetIdentity"] == STUDY
    assert effect["targetState"] == {
        "studyVersion": "None",
        "protocolDocumentsContentHash": HASH,
    }
    assert effect["conservationCounts"]["arms"] == 2
    assert state["result"].protocol_documents_content_hash is None


def test_effect_with_documents_reports_the_saved_hash(native):
    state, _ = native
    state["result"] = created(documents_hash="b" * 64)
    effect = run(command(documents=True))
    assert effect["effectPayload"]["protocolDocumentsContentHash"] == "b" * 64


def test_null_hash_means_the_study_has_no_bundle(native):
    _, calls = native
    effect = run(command(protocolDocumentsContentHash=None))
    assert calls[0][2] is None
    assert effect["effectPayload"]["protocolDocumentsContentHash"] is None


def test_other_actions_are_refused(native):
    _, calls = native
    candidate = deepcopy(command())
    candidate["action"] = "osb.package-v2.release"
    refusal(candidate, "OSB_REGIMEN_COMMAND_UNSUPPORTED", 422)
    assert not calls


@pytest.mark.parametrize(
    "enabled,action,capabilities,status,detail",
    [
        (
            False,
            regimen_onboard.ACTION,
            {"regimen:onboard"},
            503,
            "OSB_PLATFORM_COMMANDS_DISABLED",
        ),
        (
            True,
            "osb.package-v2.release",
            {"regimen:onboard"},
            422,
            "OSB_REGIMEN_COMMAND_UNSUPPORTED",
        ),
        (
            True,
            regimen_onboard.ACTION,
            {"package:release"},
            403,
            "OSB_PLATFORM_COMMAND_SCOPE_DENIED",
        ),
    ],
)
def test_route_guards_refuse_before_the_command_executor(
    monkeypatch, enabled, action, capabilities, status, detail
):
    principal = SimpleNamespace(
        purpose="workflow-orchestration",
        capabilities=capabilities,
        tenant_id="tenant",
        study_ids={PLATFORM, STUDY},
    )
    monkeypatch.setattr(native_identity, "user", lambda: principal)
    monkeypatch.setattr(
        native_identity,
        "settings",
        SimpleNamespace(
            platform_commands_prototype_enabled=enabled, deployment_environment="test"
        ),
    )
    executed = []
    monkeypatch.setattr(
        native_identity,
        "execute_osb_atomic_signed_command",
        lambda *args, **kwargs: executed.append(args),
    )
    app = FastAPI()
    app.include_router(native_identity.router)

    async def authenticated():
        return None

    app.dependency_overrides[validate_token] = authenticated
    body = {**command(), "action": action}
    response = TestClient(app).post("/commands/regimen", json=body)
    assert (response.status_code, response.json()["detail"]) == (status, detail)
    assert not executed
