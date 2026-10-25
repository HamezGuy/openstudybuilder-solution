"""
Tests for study arm batch POST conflict resolution and copy-from-study endpoint.
"""

# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

# pytest fixture functions have other fixture functions as arguments,
# which pylint interprets as unused arguments

import logging
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from neomodel import db

from clinical_mdr_api.domains.study_selections.study_arm_origin import (
    STUDY_ARM_ORIGIN_CATALOGUE,
    STUDY_ARM_ORIGIN_CODELIST_UID,
)
from clinical_mdr_api.main import app
from clinical_mdr_api.models.controlled_terminologies import ct_term
from clinical_mdr_api.models.study_selections.study import Study
from clinical_mdr_api.tests.integration.utils.api import (
    inject_and_clear_db,
    inject_base_data,
)
from clinical_mdr_api.tests.integration.utils.factory_controlled_terminology import (
    create_codelist,
    get_catalogue_name_library_name,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from clinical_mdr_api.tests.utils.checks import assert_response_status_code
from common.config import settings

log = logging.getLogger(__name__)

study: Study
investigational_arm: ct_term.CTTerm
placebo_arm_type: ct_term.CTTerm


@pytest.fixture(scope="module")
def api_client():
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    db_name = "studyarmbatchconflictapi"
    inject_and_clear_db(db_name)

    global study, investigational_arm, placebo_arm_type
    study, _ = inject_base_data()

    _catalogue_name, library_name = get_catalogue_name_library_name(use_test_utils=True)
    catalogue_name = "SDTM CT"
    ct_term_codelist = create_codelist(
        "Arm Type",
        "CTCodelist_ArmType_BatchConflict",
        catalogue_name,
        library_name,
        submission_value="ARMTTP",
    )

    investigational_arm = TestUtils.create_ct_term(
        codelist_uid=ct_term_codelist.codelist_uid,
        submission_value="Investigational Arm",
        sponsor_preferred_name="Investigational Arm",
        order=1,
        catalogue_name=catalogue_name,
        library_name=library_name,
        effective_date=datetime(2020, 3, 25, tzinfo=timezone.utc),
        approve=True,
    )
    placebo_arm_type = TestUtils.create_ct_term(
        codelist_uid=ct_term_codelist.codelist_uid,
        submission_value="Placebo Arm",
        sponsor_preferred_name="Placebo Arm",
        order=2,
        catalogue_name=catalogue_name,
        library_name=library_name,
        effective_date=datetime(2020, 3, 25, tzinfo=timezone.utc),
        approve=True,
    )

    for term in (investigational_arm, placebo_arm_type):
        db.cypher_query(
            """
            MATCH (n)-[:HAS_NAME_ROOT]-(ct_name:CTTermNameRoot)-[has_version:HAS_VERSION]-(val)
            WHERE n.uid = $uid AND EXISTS((ct_name)-[:LATEST]-(val))
              AND has_version.status = 'Final'
            SET has_version.start_date = $date
            """,
            params={
                "uid": term.term_uid,
                "date": datetime(2020, 3, 26, tzinfo=timezone.utc),
            },
        )

    db.cypher_query(
        """
        MATCH (n)-[:HAS_ATTRIBUTES_ROOT]-(attr:CTCodelistAttributesRoot)-[has_version:HAS_VERSION]-(val)
        WHERE n.uid = $uid AND EXISTS((attr)-[:LATEST]-(val))
          AND has_version.status = 'Final'
        SET has_version.start_date = $date
        """,
        params={
            "uid": ct_term_codelist.codelist_uid,
            "date": datetime(2020, 3, 26, tzinfo=timezone.utc),
        },
    )


@pytest.fixture(scope="module")
def arm_types(test_data):
    yield {
        "investigational": investigational_arm,
        "placebo": placebo_arm_type,
    }


def _post_batch(api_client, study_uid: str, payload: list[dict[str, Any]]):
    return api_client.post(f"/studies/{study_uid}/study-arms/batch", json=payload)


def test_batch_post_skip_by_name_and_arm_type(api_client, arm_types):
    target_study = TestUtils.create_study()
    TestUtils.create_study_arm(
        study_uid=target_study.uid,
        name="MCPBatchArmPlacebo",
        short_name="MCPBPBO1",
        arm_type_uid=arm_types["investigational"].term_uid,
    )

    response = _post_batch(
        api_client,
        target_study.uid,
        [
            {
                "method": "POST",
                "content": {
                    "name": "MCPBatchArmPlacebo",
                    "short_name": "MCPBPBO2",
                    "arm_type_uid": arm_types["investigational"].term_uid,
                },
            }
        ],
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert res[0]["response_code"] == 200
    assert "creation skipped" in res[0]["notification"]
    assert res[0]["content"]["name"] == "MCPBatchArmPlacebo"


def test_batch_post_suffix_name_on_different_arm_type(api_client, arm_types):
    target_study = TestUtils.create_study()
    TestUtils.create_study_arm(
        study_uid=target_study.uid,
        name="MCPBatchArmSharedName",
        short_name="MCPBSH1",
        arm_type_uid=arm_types["investigational"].term_uid,
    )

    response = _post_batch(
        api_client,
        target_study.uid,
        [
            {
                "method": "POST",
                "content": {
                    "name": "MCPBatchArmSharedName",
                    "short_name": "MCPBSH2",
                    "arm_type_uid": arm_types["placebo"].term_uid,
                },
            }
        ],
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert res[0]["response_code"] == 201
    assert "Arm name adjusted to" in res[0]["notification"]
    assert res[0]["content"]["name"] == "MCPBatchArmSharedName - Placebo Arm"


def test_batch_post_suffix_code_on_conflict(api_client):
    target_study = TestUtils.create_study()
    TestUtils.create_study_arm(
        study_uid=target_study.uid,
        name="MCPBatchArmCode1",
        short_name="MCPBC1",
        code="MCP_CODE",
    )

    response = _post_batch(
        api_client,
        target_study.uid,
        [
            {
                "method": "POST",
                "content": {
                    "name": "MCPBatchArmCode2",
                    "short_name": "MCPBC2",
                    "code": "MCP_CODE",
                },
            }
        ],
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert res[0]["response_code"] == 201
    assert "Arm code adjusted to" in res[0]["notification"]
    assert res[0]["content"]["code"].endswith(res[0]["content"]["arm_uid"])


def test_copy_from_study_creates_all_arms(api_client):
    source_study = TestUtils.create_study()
    target_study = TestUtils.create_study()
    TestUtils.create_study_arm(
        study_uid=source_study.uid,
        name="MCPSourceArm1",
        short_name="MCPSA1",
        code="MCPSAC1",
    )
    TestUtils.create_study_arm(
        study_uid=source_study.uid,
        name="MCPSourceArm2",
        short_name="MCPSA2",
        code="MCPSAC2",
    )

    response = api_client.post(
        f"/studies/{target_study.uid}/study-arms/copy-from-study/{source_study.uid}"
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert len(res) == 2
    assert all(item["response_code"] == 201 for item in res)

    list_response = api_client.get(f"/studies/{target_study.uid}/study-arms")
    assert_response_status_code(list_response, 200)
    names = [item["name"] for item in list_response.json()["items"]]
    assert "MCPSourceArm1" in names
    assert "MCPSourceArm2" in names


def test_copy_from_study_skips_conflicting_arm_on_target(api_client, arm_types):
    source_study = TestUtils.create_study()
    target_study = TestUtils.create_study()
    arm_type_uid = arm_types["investigational"].term_uid
    TestUtils.create_study_arm(
        study_uid=source_study.uid,
        name="MCPCopyConflictArm",
        short_name="MCPCCA1",
        code="MCPCCAC1",
        arm_type_uid=arm_type_uid,
    )
    TestUtils.create_study_arm(
        study_uid=source_study.uid,
        name="MCPCopyUniqueArm",
        short_name="MCPCCA2",
        code="MCPCCAC2",
        arm_type_uid=arm_type_uid,
    )
    TestUtils.create_study_arm(
        study_uid=target_study.uid,
        name="MCPCopyConflictArm",
        short_name="MCPCCAT1",
        code="MCPCCATC1",
        arm_type_uid=arm_type_uid,
    )

    response = api_client.post(
        f"/studies/{target_study.uid}/study-arms/copy-from-study/{source_study.uid}"
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert len(res) == 2
    assert res[0]["response_code"] == 200
    assert "creation skipped" in res[0]["notification"]
    assert res[0]["content"]["name"] == "MCPCopyConflictArm"
    assert res[1]["response_code"] == 201
    assert res[1]["content"]["name"] == "MCPCopyUniqueArm"

    list_response = api_client.get(f"/studies/{target_study.uid}/study-arms")
    assert_response_status_code(list_response, 200)
    assert len(list_response.json()["items"]) == 2


def test_copy_from_study_target_not_found(api_client):
    source_study = TestUtils.create_study()
    response = api_client.post(
        f"/studies/Study_DoesNotExist/study-arms/copy-from-study/{source_study.uid}"
    )
    assert_response_status_code(response, 404)


def test_batch_mixed_patch_and_post(api_client):
    target_study = TestUtils.create_study()
    existing_arm = TestUtils.create_study_arm(
        study_uid=target_study.uid,
        name="MCPMixedPatchArm",
        short_name="MCPMPA1",
        label="Original label",
    )

    response = _post_batch(
        api_client,
        target_study.uid,
        [
            {
                "method": "PATCH",
                "content": {
                    "arm_uid": existing_arm.arm_uid,
                    "label": "Updated label",
                },
            },
            {
                "method": "POST",
                "content": {
                    "name": "MCPMixedPostArm",
                    "short_name": "MCPMPA2",
                    "label": "New arm label",
                },
            },
        ],
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert len(res) == 2
    assert res[0]["response_code"] == 200
    assert res[0]["content"]["label"] == "Updated label"
    assert res[1]["response_code"] == 201
    assert res[1]["content"]["name"] == "MCPMixedPostArm"

    list_response = api_client.get(f"/studies/{target_study.uid}/study-arms")
    assert_response_status_code(list_response, 200)
    assert len(list_response.json()["items"]) == 2


def test_copy_from_study_rejects_same_study(api_client):
    target_study = TestUtils.create_study()
    response = api_client.post(
        f"/studies/{target_study.uid}/study-arms/copy-from-study/{target_study.uid}"
    )
    assert_response_status_code(response, 400)
    assert "must differ" in response.json()["message"]


def test_copy_from_study_not_found(api_client):
    target_study = TestUtils.create_study()
    response = api_client.post(
        f"/studies/{target_study.uid}/study-arms/copy-from-study/Study_DoesNotExist"
    )
    assert_response_status_code(response, 404)


def test_copy_from_study_empty_source(api_client):
    source_study = TestUtils.create_study()
    target_study = TestUtils.create_study()
    response = api_client.post(
        f"/studies/{target_study.uid}/study-arms/copy-from-study/{source_study.uid}"
    )
    assert_response_status_code(response, 207)
    assert response.json() == []


# FX-OSB-02: the data origin pair survives batch POST and copy in both modes,
# and OSB_ARM_BATCH_CONFLICTS=refuse refuses identity collisions with 409.

ORIGIN_DESCRIPTION = "Synthetic arm authored for the batch conflict tests."


@pytest.fixture(scope="module")
def arm_origin_uid(test_data):
    TestUtils.create_ct_catalogue(catalogue_name=STUDY_ARM_ORIGIN_CATALOGUE)
    TestUtils.create_ct_codelist(
        catalogue_name=STUDY_ARM_ORIGIN_CATALOGUE,
        codelist_uid=STUDY_ARM_ORIGIN_CODELIST_UID,
        name="Synthetic study arm data origin type",
        submission_value="ARMORIGIN",
        extensible=True,
        approve=True,
    )
    TestUtils.create_ct_term(
        catalogue_name=STUDY_ARM_ORIGIN_CATALOGUE,
        codelist_uid=STUDY_ARM_ORIGIN_CODELIST_UID,
        term_uid="C176263",
        concept_id="C176263",
        submission_value="SYNTHETIC",
        sponsor_preferred_name="Synthetic data",
        sponsor_preferred_name_sentence_case="synthetic data",
    )
    yield "C176263"


@pytest.fixture
def refuse_conflicts(monkeypatch):
    monkeypatch.setattr(settings, "arm_batch_conflicts", "refuse")


def _arm_names(api_client, study_uid: str) -> list[str]:
    response = api_client.get(f"/studies/{study_uid}/study-arms")
    assert_response_status_code(response, 200)
    return [item["name"] for item in response.json()["items"]]


@pytest.mark.parametrize("mode", ["adjust", "refuse"])
def test_batch_post_keeps_data_origin(api_client, arm_origin_uid, monkeypatch, mode):
    monkeypatch.setattr(settings, "arm_batch_conflicts", mode)
    target_study = TestUtils.create_study()

    response = _post_batch(
        api_client,
        target_study.uid,
        [
            {
                "method": "POST",
                "content": {
                    "name": f"MCPOriginBatchArm {mode}",
                    "short_name": f"MCPOBA {mode}",
                    "data_origin_type_uid": arm_origin_uid,
                    "data_origin_description": ORIGIN_DESCRIPTION,
                },
            }
        ],
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert res[0]["response_code"] == 201
    assert res[0]["content"]["data_origin_type_uid"] == arm_origin_uid
    assert res[0]["content"]["data_origin_description"] == ORIGIN_DESCRIPTION

    arm_uid = res[0]["content"]["arm_uid"]
    stored = api_client.get(f"/studies/{target_study.uid}/study-arms/{arm_uid}")
    assert_response_status_code(stored, 200)
    assert stored.json()["data_origin_type_uid"] == arm_origin_uid
    assert stored.json()["data_origin_description"] == ORIGIN_DESCRIPTION


@pytest.mark.parametrize("mode", ["adjust", "refuse"])
def test_copy_from_study_keeps_data_origin_and_records_source(
    api_client, arm_origin_uid, monkeypatch, mode
):
    monkeypatch.setattr(settings, "arm_batch_conflicts", mode)
    source_study = TestUtils.create_study()
    target_study = TestUtils.create_study()
    source = api_client.post(
        f"/studies/{source_study.uid}/study-arms",
        json={
            "name": f"MCPOriginSourceArm {mode}",
            "short_name": f"MCPOSA {mode}",
            "data_origin_type_uid": arm_origin_uid,
            "data_origin_description": ORIGIN_DESCRIPTION,
        },
    )
    assert_response_status_code(source, 201)
    source_arm_uid = source.json()["arm_uid"]

    response = api_client.post(
        f"/studies/{target_study.uid}/study-arms/copy-from-study/{source_study.uid}"
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert len(res) == 1
    assert res[0]["response_code"] == 201
    assert res[0]["content"]["data_origin_type_uid"] == arm_origin_uid
    assert res[0]["content"]["data_origin_description"] == ORIGIN_DESCRIPTION

    rows, _ = db.cypher_query(
        """
        MATCH (:StudyRoot {uid: $study_uid})-[:AUDIT_TRAIL]->(action:Create)
              -[:AFTER]->(:StudyArm {uid: $arm_uid})
        RETURN action.source_study_uid, action.source_selection_uid
        """,
        params={
            "study_uid": target_study.uid,
            "arm_uid": res[0]["content"]["arm_uid"],
        },
    )
    assert rows == [[source_study.uid, source_arm_uid]]


@pytest.mark.parametrize(
    "existing, submitted, field_name, value",
    [
        (
            {"name": "MCPRefuseCode1", "short_name": "MCPRC1", "code": "MCPRCODE"},
            {"name": "MCPRefuseCode2", "short_name": "MCPRC2", "code": "MCPRCODE"},
            "code",
            "MCPRCODE",
        ),
        (
            {
                "name": "MCPRefuseRand1",
                "short_name": "MCPRR1",
                "randomization_group": "MCPRGROUP",
            },
            {
                "name": "MCPRefuseRand2",
                "short_name": "MCPRR2",
                "randomization_group": "MCPRGROUP",
            },
            "randomization_group",
            "MCPRGROUP",
        ),
        (
            {
                "name": "MCPRefuseName",
                "short_name": "MCPRN1",
                "type": "investigational",
            },
            {"name": "MCPRefuseName", "short_name": "MCPRN2", "type": "placebo"},
            "name",
            "MCPRefuseName",
        ),
        (
            {
                "name": "MCPRefuseSame",
                "short_name": "MCPRS1",
                "type": "investigational",
            },
            {
                "name": "MCPRefuseSame",
                "short_name": "MCPRS2",
                "type": "investigational",
            },
            "name",
            "MCPRefuseSame",
        ),
    ],
)
def test_batch_post_refuses_identity_collision(
    api_client, arm_types, refuse_conflicts, existing, submitted, field_name, value
):
    target_study = TestUtils.create_study()
    existing = dict(existing)
    existing_type = existing.pop("type", None)
    existing_arm = TestUtils.create_study_arm(
        study_uid=target_study.uid,
        arm_type_uid=arm_types[existing_type].term_uid if existing_type else None,
        **existing,
    )
    submitted = dict(submitted)
    submitted_type = submitted.pop("type", None)
    if submitted_type:
        submitted["arm_type_uid"] = arm_types[submitted_type].term_uid

    response = _post_batch(
        api_client,
        target_study.uid,
        [
            {
                "method": "POST",
                "content": {"name": "MCPRefuseUnique", "short_name": "MCPRU"},
            },
            {"method": "POST", "content": submitted},
        ],
    )
    assert_response_status_code(response, 409)
    message = response.json()["message"]
    assert message.startswith("STUDY_ARM_BATCH_CONFLICT:")
    assert f"field {field_name} value '{value}'" in message
    assert existing_arm.arm_uid in message
    # The whole request is refused: the unique arm before it is not written.
    assert _arm_names(api_client, target_study.uid) == [existing_arm.name]


def test_batch_post_refuse_returns_identical_arm(
    api_client, arm_types, refuse_conflicts
):
    target_study = TestUtils.create_study()
    arm_type_uid = arm_types["investigational"].term_uid
    existing_arm = TestUtils.create_study_arm(
        study_uid=target_study.uid,
        name="MCPRefuseIdentical",
        short_name="MCPRI1",
        code="MCPRICODE",
        arm_type_uid=arm_type_uid,
    )

    response = _post_batch(
        api_client,
        target_study.uid,
        [
            {
                "method": "POST",
                "content": {
                    "name": "MCPRefuseIdentical",
                    "short_name": "MCPRI1",
                    "code": "MCPRICODE",
                    "arm_type_uid": arm_type_uid,
                },
            }
        ],
    )
    assert_response_status_code(response, 207)
    res = response.json()
    assert res[0]["response_code"] == 200
    assert res[0]["content"]["arm_uid"] == existing_arm.arm_uid
    assert "creation skipped" in res[0]["notification"]
    assert _arm_names(api_client, target_study.uid) == ["MCPRefuseIdentical"]


def test_copy_from_study_refuses_conflicting_arm(
    api_client, arm_types, refuse_conflicts
):
    source_study = TestUtils.create_study()
    target_study = TestUtils.create_study()
    arm_type_uid = arm_types["investigational"].term_uid
    TestUtils.create_study_arm(
        study_uid=source_study.uid,
        name="MCPRefuseCopyUnique",
        short_name="MCPRCU1",
        arm_type_uid=arm_type_uid,
    )
    TestUtils.create_study_arm(
        study_uid=source_study.uid,
        name="MCPRefuseCopyConflict",
        short_name="MCPRCC1",
        code="MCPRCCS",
        arm_type_uid=arm_type_uid,
    )
    target_arm = TestUtils.create_study_arm(
        study_uid=target_study.uid,
        name="MCPRefuseCopyConflict",
        short_name="MCPRCCT1",
        code="MCPRCCT",
        arm_type_uid=arm_type_uid,
    )

    response = api_client.post(
        f"/studies/{target_study.uid}/study-arms/copy-from-study/{source_study.uid}"
    )
    assert_response_status_code(response, 409)
    message = response.json()["message"]
    assert "field name value 'MCPRefuseCopyConflict'" in message
    assert target_arm.arm_uid in message
    assert _arm_names(api_client, target_study.uid) == ["MCPRefuseCopyConflict"]
