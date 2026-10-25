"""
Tests for the optional change reason on study arm writes (FX-OSB-09) and
OSB_ARM_WRITE_REASON_REQUIRED.
"""

# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

# pytest fixture functions have other fixture functions as arguments,
# which pylint interprets as unused arguments

from typing import Any

import pytest
from fastapi.testclient import TestClient

from clinical_mdr_api.main import app
from clinical_mdr_api.tests.integration.utils.api import (
    inject_and_clear_db,
    inject_base_data,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from clinical_mdr_api.tests.utils.checks import assert_response_status_code
from common.config import settings

REASON_REQUIRED = "STUDY_ARM_CHANGE_REASON_REQUIRED"


@pytest.fixture(scope="module")
def api_client():
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    inject_and_clear_db("studyarmchangereasonapi")
    inject_base_data()


@pytest.fixture
def reason_required(monkeypatch):
    monkeypatch.setattr(settings, "arm_write_reason_required", True)


def _audit_trail(api_client, study_uid: str, arm_uid: str) -> list[dict[str, Any]]:
    response = api_client.get(f"/studies/{study_uid}/study-arms/{arm_uid}/audit-trail")
    assert_response_status_code(response, 200)
    return response.json()


def _action(trail: list[dict[str, Any]], change_type: str) -> dict[str, Any]:
    matching = [row for row in trail if row["change_type"] == change_type]
    assert len(matching) == 1, trail
    return matching[0]


def _post_arm(api_client, study_uid: str, **content: Any):
    return api_client.post(f"/studies/{study_uid}/study-arms", json=content)


def test_batch_post_reason_is_shown_in_the_arm_audit_trail(api_client, test_data):
    study = TestUtils.create_study()
    response = api_client.post(
        f"/studies/{study.uid}/study-arms/batch",
        json=[
            {
                "method": "POST",
                "content": {
                    "name": "Reason arm C",
                    "short_name": "RC",
                    "change_reason": "Add arm C after the synthetic amendment",
                },
            },
            {
                "method": "POST",
                "content": {"name": "Reason arm D", "short_name": "RD"},
            },
        ],
    )
    assert_response_status_code(response, 207)
    with_reason, without_reason = (
        item["content"]["arm_uid"] for item in response.json()
    )

    created = _action(_audit_trail(api_client, study.uid, with_reason), "Create")
    assert created["change_reason"] == "Add arm C after the synthetic amendment"
    # Only the arm whose item gave a reason gets one.
    created = _action(_audit_trail(api_client, study.uid, without_reason), "Create")
    assert "change_reason" not in created
    assert "change_reason" not in created["changes"]


def test_create_patch_and_delete_reasons_land_on_their_own_actions(
    api_client, test_data
):
    study = TestUtils.create_study()
    response = _post_arm(
        api_client,
        study.uid,
        name="Reason arm E",
        short_name="RE",
        change_reason="Create arm E",
    )
    assert_response_status_code(response, 201)
    arm_uid = response.json()["arm_uid"]
    other = _post_arm(api_client, study.uid, name="Reason arm F", short_name="RF")
    assert_response_status_code(other, 201)

    response = api_client.patch(
        f"/studies/{study.uid}/study-arms/{arm_uid}",
        json={"description": "Edited", "change_reason": "Describe arm E"},
    )
    assert_response_status_code(response, 200)
    response = api_client.delete(
        f"/studies/{study.uid}/study-arms/{arm_uid}",
        params={"change_reason": "Drop arm E"},
    )
    assert_response_status_code(response, 204)

    trail = _audit_trail(api_client, study.uid, arm_uid)
    assert _action(trail, "Create")["change_reason"] == "Create arm E"
    assert _action(trail, "Edit")["change_reason"] == "Describe arm E"
    assert _action(trail, "Delete")["change_reason"] == "Drop arm E"
    # Arm F only moved up one place: its reorder Edit takes no reason.
    for row in _audit_trail(api_client, study.uid, other.json()["arm_uid"]):
        assert "change_reason" not in row


def test_copy_reason_is_stored_on_every_copied_arm(api_client, test_data):
    source = TestUtils.create_study()
    target = TestUtils.create_study()
    for name, short_name in (("Copy reason A", "CRA"), ("Copy reason B", "CRB")):
        assert_response_status_code(
            _post_arm(api_client, source.uid, name=name, short_name=short_name), 201
        )
    response = api_client.post(
        f"/studies/{target.uid}/study-arms/copy-from-study/{source.uid}",
        params={"change_reason": "Carry the synthetic design over"},
    )
    assert_response_status_code(response, 207)
    for item in response.json():
        created = _action(
            _audit_trail(api_client, target.uid, item["content"]["arm_uid"]),
            "Create",
        )
        assert created["change_reason"] == "Carry the synthetic design over"


def test_regimen_reason_is_stored_on_its_new_arms(api_client, test_data):
    study = TestUtils.create_study()
    response = api_client.post(
        f"/studies/{study.uid}/regimens",
        json={
            "expected_study_version": "None",
            "arms": [
                {"local_key": "c", "name": "Regimen reason C", "short_name": "RRC"},
                {
                    "local_key": "d",
                    "name": "Regimen reason D",
                    "short_name": "RRD",
                    "change_reason": "Arm D has its own reason",
                },
            ],
            "change_reason": "Add the synthetic regimen",
        },
    )
    assert_response_status_code(response, 201)
    arms = response.json()["arms"]
    created = _action(_audit_trail(api_client, study.uid, arms["c"]), "Create")
    assert created["change_reason"] == "Add the synthetic regimen"
    created = _action(_audit_trail(api_client, study.uid, arms["d"]), "Create")
    assert created["change_reason"] == "Arm D has its own reason"


def test_reason_longer_than_4000_characters_is_refused(api_client, test_data):
    study = TestUtils.create_study()
    response = _post_arm(
        api_client, study.uid, name="Long", short_name="L", change_reason="x" * 4001
    )
    # The fork answers request validation errors with 400.
    assert_response_status_code(response, 400)
    assert response.json()["details"][0]["field"] == ["body", "change_reason"]


def test_flag_off_writes_without_a_reason_as_before(api_client, test_data):
    assert settings.arm_write_reason_required is False
    study = TestUtils.create_study()
    response = _post_arm(api_client, study.uid, name="No reason", short_name="NR")
    assert_response_status_code(response, 201)
    arm_uid = response.json()["arm_uid"]
    response = api_client.post(
        f"/studies/{study.uid}/study-arms/batch",
        json=[
            {"method": "POST", "content": {"name": "No reason B", "short_name": "NB"}}
        ],
    )
    assert_response_status_code(response, 207)
    response = api_client.delete(f"/studies/{study.uid}/study-arms/{arm_uid}")
    assert_response_status_code(response, 204)
    for row in _audit_trail(api_client, study.uid, arm_uid):
        assert "change_reason" not in row


def test_flag_on_refuses_a_batch_post_without_a_reason(
    api_client, test_data, reason_required
):
    study = TestUtils.create_study()
    response = api_client.post(
        f"/studies/{study.uid}/study-arms/batch",
        json=[
            {
                "method": "POST",
                "content": {
                    "name": "Flag A",
                    "short_name": "FA",
                    "change_reason": "Has one",
                },
            },
            {"method": "POST", "content": {"name": "Flag B", "short_name": "FB"}},
        ],
    )
    assert_response_status_code(response, 400)
    assert REASON_REQUIRED in response.json()["message"]
    listed = api_client.get(f"/studies/{study.uid}/study-arms")
    assert_response_status_code(listed, 200)
    assert listed.json()["items"] == []


def test_flag_on_refuses_every_arm_write_without_a_reason(
    api_client, test_data, reason_required
):
    study = TestUtils.create_study()
    source = TestUtils.create_study()
    response = _post_arm(api_client, study.uid, name="Flag C", short_name="FC")
    assert_response_status_code(response, 400)
    assert REASON_REQUIRED in response.json()["message"]

    response = _post_arm(
        api_client,
        study.uid,
        name="Flag C",
        short_name="FC",
        change_reason="Create arm C",
    )
    assert_response_status_code(response, 201)
    arm_uid = response.json()["arm_uid"]

    refused = [
        api_client.patch(
            f"/studies/{study.uid}/study-arms/{arm_uid}", json={"description": "x"}
        ),
        api_client.post(
            f"/studies/{study.uid}/study-arms/batch",
            json=[
                {
                    "method": "PATCH",
                    "content": {"arm_uid": arm_uid, "description": "x"},
                }
            ],
        ),
        api_client.delete(f"/studies/{study.uid}/study-arms/{arm_uid}"),
        api_client.post(
            f"/studies/{study.uid}/study-arms/copy-from-study/{source.uid}"
        ),
        api_client.post(
            f"/studies/{study.uid}/regimens",
            json={
                "expected_study_version": "None",
                "arms": [{"local_key": "g", "name": "Flag G", "short_name": "FG"}],
            },
        ),
    ]
    for response in refused:
        assert_response_status_code(response, 400)
        assert REASON_REQUIRED in response.json()["message"]

    trail = _audit_trail(api_client, study.uid, arm_uid)
    assert [row["change_type"] for row in trail] == ["Create"]

    response = api_client.delete(
        f"/studies/{study.uid}/study-arms/{arm_uid}",
        params={"change_reason": "Remove arm C"},
    )
    assert_response_status_code(response, 204)
