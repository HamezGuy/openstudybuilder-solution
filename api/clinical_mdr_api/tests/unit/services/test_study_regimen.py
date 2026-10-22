"""Offline checks for the single-transaction regimen add composition."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from clinical_mdr_api.models.study_selections.study_regimen import (
    StudyRegimenCreateInput,
)
from clinical_mdr_api.services.studies import study_regimen as service_module
from clinical_mdr_api.services.studies.study_regimen import StudyRegimenService
from clinical_mdr_api.tests.unit.services.test_authored_protocol_documents import (
    authored_source,
)
from common.exceptions import (
    AlreadyExistsException,
    BusinessLogicException,
    ValidationException,
)

STUDY_UID = "Study_000001"


def local(key):
    return {"local_key": key}


def regimen_payload():
    return {
        "expected_study_version": "None",
        "arms": [
            {"local_key": "active", "name": "Regimen H active", "short_name": "HA"},
            {"local_key": "placebo", "name": "Regimen H placebo", "short_name": "HP"},
        ],
        "branch_arms": [
            {
                "local_key": "active-branch",
                "name": "Regimen H active branch",
                "short_name": "HAB",
                "arm_uid": local("active"),
            }
        ],
        "cohorts": [
            {
                "local_key": "regimen",
                "name": "Regimen H",
                "short_name": "H",
                "arm_uids": [local("placebo"), "StudyArm_000001"],
                "branch_arm_uids": [local("active-branch")],
            }
        ],
        "elements": [
            {"local_key": "drug", "name": "Regimen H drug", "short_name": "HD"}
        ],
        "compounds": [
            {
                "local_key": "compound",
                "compound_alias_uid": "CompoundAlias_000001",
                "medicinal_product_uid": "MedicinalProduct_000001",
            }
        ],
        "compound_dosings": [
            {
                "local_key": "dose",
                "study_compound_uid": local("compound"),
                "study_element_uid": local("drug"),
            }
        ],
        "design_cells": [
            {
                "local_key": "active-treatment",
                "study_branch_arm_uid": local("active-branch"),
                "study_epoch_uid": "StudyEpoch_000002",
                "study_element_uid": local("drug"),
                "transition_rule": "Synthetic authored transition",
            },
            {
                "local_key": "placebo-treatment",
                "study_arm_uid": local("placebo"),
                "study_epoch_uid": "StudyEpoch_000002",
                "study_element_uid": "StudyElement_000009",
            },
        ],
        "lifecycle_events": [
            {
                "target_kind": "arm",
                "target": local("active"),
                "lifecycle_state": "Synthetic entering",
                "effective_date": "2026-10-21T12:00:00Z",
                "decision_reference": "urn:synthetic:decision:regimen-h",
                "change_reason": "Synthetic regimen onboarding",
            }
        ],
    }


class Recorder:
    """Stand in for every canonical service and record each call in order."""

    def __init__(self, monkeypatch, *, version="None", locked=False, fail=None):
        self.calls = []
        self.version = version
        self.fail = fail
        counter = iter(range(1, 1000))
        recorder = self

        def returning(kind, field):
            def call(_self, study_uid, payload, *args, **_kwargs):
                recorder.calls.append((kind, study_uid, payload, args))
                if recorder.fail == kind:
                    raise ValidationException(msg=f"synthetic {kind} failure")
                return SimpleNamespace(**{field: f"{kind}-{next(counter)}"})

            return call

        names = {
            "StudyArmSelectionService": ("arm", "arm_uid", "make_selection"),
            "StudyBranchArmSelectionService": (
                "branch",
                "branch_arm_uid",
                "make_selection",
            ),
            "StudyCohortSelectionService": ("cohort", "cohort_uid", "make_selection"),
            "StudyElementSelectionService": (
                "element",
                "element_uid",
                "make_selection",
            ),
            "StudyCompoundSelectionService": (
                "compound",
                "study_compound_uid",
                "make_selection",
            ),
            "StudyCompoundDosingSelectionService": (
                "dosing",
                "study_compound_dosing_uid",
                "make_selection",
            ),
            "StudyDesignCellService": ("cell", "design_cell_uid", "create"),
        }
        for class_name, (kind, field, method) in names.items():
            attributes = {
                "__init__": lambda _self: None,
                method: returning(kind, field),
            }
            if class_name == "StudyArmSelectionService":

                def lifecycle(_self, study_uid, target_kind, target_uid, payload):
                    recorder.calls.append(
                        ("lifecycle", study_uid, payload, (target_kind, target_uid))
                    )
                    return SimpleNamespace(uid=f"lifecycle-{next(counter)}")

                attributes["create_lifecycle_event"] = lifecycle
                attributes["get_all_selection"] = lambda _self, study_uid: (
                    SimpleNamespace(items=[SimpleNamespace(arm_uid="StudyArm_000001")])
                )
            monkeypatch.setattr(
                service_module, class_name, type(class_name, (), attributes)
            )

        class Study:
            def get_by_uid(self, study_uid):
                recorder.calls.append(("version", study_uid, None, ()))
                return SimpleNamespace(
                    current_metadata=SimpleNamespace(
                        version_metadata=SimpleNamespace(
                            version_number=None if version == "None" else version
                        )
                    )
                )

            def check_if_study_is_locked(self, study_uid):
                recorder.calls.append(("locked", study_uid, None, ()))
                return locked

        class Documents:
            def save(self, study_uid, request):
                recorder.calls.append(("documents", study_uid, request, ()))
                return {"content_hash": request.content.content_hash()}

        monkeypatch.setattr(service_module, "StudyService", Study)
        monkeypatch.setattr(service_module, "StudyDefinitionDocumentService", Documents)
        monkeypatch.setattr(
            service_module,
            "acquire_write_lock_study_value",
            lambda study_uid: recorder.calls.append(("lock", study_uid, None, ())),
        )

    def kinds(self):
        return [call[0] for call in self.calls]

    def payloads(self, kind):
        return [call[2] for call in self.calls if call[0] == kind]


def create(payload):
    # The undecorated method runs the composition without a database driver.
    return StudyRegimenService.create.__wrapped__(  # type: ignore[attr-defined]
        StudyRegimenService(), STUDY_UID, StudyRegimenCreateInput(**payload)
    )


def test_order_and_exact_reference_resolution(monkeypatch):
    recorder = Recorder(monkeypatch)
    result = create(regimen_payload())
    assert recorder.kinds() == [
        "lock",
        "locked",
        "version",
        "arm",
        "arm",
        "branch",
        "cohort",
        "element",
        "compound",
        "dosing",
        "cell",
        "cell",
        "lifecycle",
    ]
    arms = result.arms
    assert set(arms) == {"active", "placebo"}
    branch = recorder.payloads("branch")[0]
    assert branch.arm_uid == arms["active"]
    assert branch.name == "Regimen H active branch"
    cohort = recorder.payloads("cohort")[0]
    assert cohort.arm_uids == [arms["placebo"], "StudyArm_000001"]
    assert cohort.branch_arm_uids == [result.branch_arms["active-branch"]]
    dosing = recorder.payloads("dosing")[0]
    assert dosing.study_compound_uid == result.compounds["compound"]
    assert dosing.study_element_uid == result.elements["drug"]
    first, second = recorder.payloads("cell")
    assert first.study_arm_uid is None
    assert first.study_branch_arm_uid == result.branch_arms["active-branch"]
    assert first.study_element_uid == result.elements["drug"]
    assert first.transition_rule == "Synthetic authored transition"
    assert second.study_arm_uid == arms["placebo"]
    assert second.study_element_uid == "StudyElement_000009"
    event = recorder.payloads("lifecycle")[0]
    assert event.expected_study_version == "None"
    assert [call[3] for call in recorder.calls if call[0] == "lifecycle"] == [
        ("arm", arms["active"])
    ]
    assert (
        result.lifecycle_event_uids and result.protocol_documents_content_hash is None
    )
    assert result.study_version == "None"


def test_canonical_inputs_keep_only_client_supplied_fields(monkeypatch):
    recorder = Recorder(monkeypatch)
    create(regimen_payload())
    arm = recorder.payloads("arm")[0]
    assert type(arm).__name__ == "StudySelectionArmCreateInput"
    assert arm.model_fields_set == {"name", "short_name"}
    assert "local_key" not in type(arm).model_fields


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["arms"].append(dict(p["arms"][0])),
        lambda p: p["branch_arms"][0].update(arm_uid={"local_key": "missing"}),
        lambda p: p["branch_arms"][0].update(arm_uid="StudyArm_000001"),
        lambda p: p["cohorts"][0]["arm_uids"].append({"local_key": "drug"}),
        lambda p: p["compound_dosings"][0].update(
            study_element_uid={"local_key": "compound"}
        ),
        lambda p: p["design_cells"][0].update(study_arm_uid={"local_key": "active"}),
        lambda p: p["design_cells"][1].pop("study_arm_uid"),
        lambda p: p["design_cells"][1].update(study_arm_uid="StudyArm_000001"),
        lambda p: p["lifecycle_events"][0].update(target_kind="branch_arm"),
        lambda p: p["lifecycle_events"][0].update(effective_date="2026-10-21T12:00"),
        lambda p: p["arms"][0].update(local_key="bad key/1"),
        lambda p: p["arms"][0].update(unexpected="silently dropped"),
        lambda p: p.update(arms=[]),
        lambda p: p.update(unexpected=True),
    ],
)
def test_invalid_references_and_shapes_refuse_before_any_write(monkeypatch, change):
    recorder = Recorder(monkeypatch)
    payload = regimen_payload()
    change(payload)
    with pytest.raises(ValidationError):
        create(payload)
    assert not recorder.calls


@pytest.mark.parametrize(
    "version,locked,error",
    [("1", False, AlreadyExistsException), ("None", True, BusinessLogicException)],
)
def test_stale_or_locked_draft_refuses_before_canonical_writes(
    monkeypatch, version, locked, error
):
    recorder = Recorder(monkeypatch, version=version, locked=locked)
    with pytest.raises(error):
        create(regimen_payload())
    assert all(kind in {"lock", "locked", "version"} for kind in recorder.kinds())


@pytest.mark.parametrize("fail", ["branch", "cohort", "dosing", "cell"])
def test_late_canonical_failure_propagates_for_transaction_rollback(monkeypatch, fail):
    recorder = Recorder(monkeypatch, fail=fail)
    with pytest.raises(ValidationException, match=f"synthetic {fail} failure"):
        create(regimen_payload())
    assert recorder.kinds()[-1] == fail
    assert "documents" not in recorder.kinds()


def documents_payload(tokens=None):
    _, content = authored_source()
    content.bindings[0].arm_uids = ["StudyArm_000001", "token-active", "token-placebo"]
    content.bindings[1].arm_uids = ["StudyArm_000001"]
    content.bindings[2].arm_uids = ["token-active"]
    content.bindings[3].arm_uids = ["token-placebo"]
    return {
        "expected_content_hash": "a" * 64,
        "reason": "Synthetic regimen appendices",
        "arm_tokens": (
            tokens
            if tokens is not None
            else {"token-active": "active", "token-placebo": "placebo"}
        ),
        "content": content.model_dump(mode="json"),
    }


def test_documents_replace_only_declared_tokens_after_arms_exist(monkeypatch):
    recorder = Recorder(monkeypatch)
    payload = regimen_payload()
    payload["protocol_documents"] = documents_payload()
    result = create(payload)
    assert recorder.kinds()[-1] == "documents"
    request = recorder.payloads("documents")[0]
    assert request.expected_study_version == "None"
    assert request.expected_content_hash == "a" * 64
    assert request.reason == "Synthetic regimen appendices"
    bindings = [binding.arm_uids for binding in request.content.bindings]
    active, placebo = result.arms["active"], result.arms["placebo"]
    assert bindings == [
        ["StudyArm_000001", active, placebo],
        ["StudyArm_000001"],
        [active],
        [placebo],
    ]
    original = StudyRegimenCreateInput(**payload).protocol_documents.content
    unchanged = request.content.model_dump(mode="json")
    expected = original.model_dump(mode="json")
    for binding in expected["bindings"]:
        binding["arm_uids"] = None
    for binding in unchanged["bindings"]:
        binding["arm_uids"] = None
    assert unchanged == expected
    assert result.protocol_documents_content_hash == request.content.content_hash()


@pytest.mark.parametrize(
    "tokens",
    [
        {"token-active": "active", "token-placebo": "missing"},
        {"token-active": "active", "token-placebo": "placebo", "unused": "active"},
        {"token-active": "active", "token-placebo": "placebo", " ": "active"},
    ],
)
def test_undeclared_unused_or_blank_tokens_refuse(monkeypatch, tokens):
    recorder = Recorder(monkeypatch)
    payload = regimen_payload()
    payload["protocol_documents"] = documents_payload(tokens)
    with pytest.raises(ValidationError):
        create(payload)
    assert not recorder.calls


def test_token_equal_to_a_native_arm_uid_refuses(monkeypatch):
    recorder = Recorder(monkeypatch)
    payload = regimen_payload()
    documents = documents_payload(
        {"StudyArm_000001": "active", "token-placebo": "placebo"}
    )
    payload["protocol_documents"] = documents
    with pytest.raises(ValidationException, match="collide"):
        create(payload)
    assert "documents" not in recorder.kinds()
