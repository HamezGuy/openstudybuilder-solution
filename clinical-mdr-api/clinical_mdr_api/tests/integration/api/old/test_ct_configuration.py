# pylint: disable=unused-argument, redefined-outer-name, too-many-arguments, line-too-long, too-many-statements

# pytest fixture functions have other fixture functions as arguments,
# which pylint interprets as unused arguments

import pytest
from fastapi.testclient import TestClient
from neomodel import db

from clinical_mdr_api.main import app
from clinical_mdr_api.tests.integration.utils.api import drop_db, inject_and_clear_db
from clinical_mdr_api.tests.integration.utils.data_library import (
    STARTUP_PARAMETERS_CYPHER,
)
from clinical_mdr_api.tests.utils.checks import assert_response_status_code


@pytest.fixture(scope="module")
def api_client(test_data):
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    inject_and_clear_db("old.json.test.ct.configuration")
    db.cypher_query(STARTUP_PARAMETERS_CYPHER)

    yield

    drop_db("old.json.test.ct.configuration")


def test_empty_list_1(api_client):
    response = api_client.get("/configurations")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res == []


def test_adding(api_client):
    data = {
        "author_username": "unknown-user@example.com",
        "study_field_name": "unit1",
        "study_field_data_type": "text",
        "study_field_name_api": "unit1",
        "study_field_grouping": "sbtab",
        "is_dictionary_term": False,
    }
    response = api_client.post("/configurations", json=data)

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["uid"] == "CTConfig_000001"
    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.1"
    assert res["change_description"] == "Initial version"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_all_1(api_client):
    response = api_client.get("/configurations")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res[0]["uid"] == "CTConfig_000001"
    assert res[0]["author_username"] == "unknown-user@example.com"
    assert res[0]["study_field_name"] == "unit1"
    assert res[0]["study_field_grouping"] == "sbtab"
    assert res[0]["end_date"] is None
    assert res[0]["status"] == "Draft"
    assert res[0]["version"] == "0.1"
    assert res[0]["change_description"] == "Initial version"
    assert res[0]["study_field_data_type"] == "text"
    assert res[0]["study_field_null_value_code"] is None
    assert res[0]["configured_codelist_uid"] is None
    assert res[0]["configured_term_uid"] is None
    assert res[0]["study_field_name_api"] == "unit1"
    assert res[0]["is_dictionary_term"] is False


def test_delete_success(api_client):
    response = api_client.delete("/configurations/CTConfig_000001")

    assert_response_status_code(response, 204)


def test_empty_list1(api_client):
    response = api_client.get("/configurations")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res == []


def test_adding1(api_client):
    data = {
        "author_username": "unknown-user@example.com",
        "study_field_name": "unit1",
        "study_field_data_type": "text",
        "study_field_name_api": "unit1",
        "study_field_grouping": "sbtab",
        "is_dictionary_term": False,
    }
    response = api_client.post("/configurations", json=data)

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.1"
    assert res["change_description"] == "Initial version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_retire_draft_fail(api_client):
    response = api_client.delete("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Cannot retire draft version."


def test_reactivate_draft_fail(api_client):
    response = api_client.post("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Only RETIRED version can be reactivated."


def test_post_versions_draft_fail(api_client):
    response = api_client.post("/configurations/CTConfig_000002/versions")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "New draft version can be created only for FINAL versions."


def test_get_all1(api_client):
    response = api_client.get("/configurations")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res[0]["author_username"] == "unknown-user@example.com"
    assert res[0]["study_field_name"] == "unit1"
    assert res[0]["study_field_grouping"] == "sbtab"
    assert res[0]["end_date"] is None
    assert res[0]["status"] == "Draft"
    assert res[0]["version"] == "0.1"
    assert res[0]["change_description"] == "Initial version"
    assert res[0]["uid"] == "CTConfig_000002"
    assert res[0]["study_field_data_type"] == "text"
    assert res[0]["study_field_null_value_code"] is None
    assert res[0]["configured_codelist_uid"] is None
    assert res[0]["configured_term_uid"] is None
    assert res[0]["study_field_name_api"] == "unit1"
    assert res[0]["is_dictionary_term"] is False


def test_get_specific_latest(api_client):
    response = api_client.get("/configurations/CTConfig_000002")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.1"
    assert res["change_description"] == "Initial version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_status_draft(api_client):
    response = api_client.get("/configurations/CTConfig_000002?status=Draft")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.1"
    assert res["change_description"] == "Initial version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_version_0_1(api_client):
    response = api_client.get("/configurations/CTConfig_000002?version=0.1")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.1"
    assert res["change_description"] == "Initial version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_at_specified_date_start_date_ofv0_1_1(api_client):
    response = api_client.get(
        "/configurations/CTConfig_000002?at_specified_date={start_date_ofV0_1}"
    )

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.1"
    assert res["change_description"] == "Initial version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_status_final(api_client):
    response = api_client.get("/configurations/CTConfig_000002?status=Final")

    assert_response_status_code(response, 404)

    res = response.json()

    assert res["type"] == "NotFoundException"
    assert res["message"] == "CT Config with UID 'CTConfig_000002' doesn't exist."


def test_get_specific_version_0_2_1(api_client):
    response = api_client.get("/configurations/CTConfig_000002?version=0.2")

    assert_response_status_code(response, 404)

    res = response.json()

    assert res["type"] == "NotFoundException"
    assert res["message"] == "CT Config with UID 'CTConfig_000002' doesn't exist."


def test_get_specific_status_retired(api_client):
    response = api_client.get("/configurations/CTConfig_000002?status=Retired")

    assert_response_status_code(response, 404)

    res = response.json()

    assert res["type"] == "NotFoundException"
    assert res["message"] == "CT Config with UID 'CTConfig_000002' doesn't exist."


def test_patch(api_client):
    data = {
        "change_description": "Patched version",
        "study_field_name": "unit1-patched",
        "study_field_data_type": "text",
        "study_field_name_api": "unit1",
        "is_dictionary_term": False,
    }
    response = api_client.patch("/configurations/CTConfig_000002", json=data)

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.2"
    assert res["change_description"] == "Patched version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_all2(api_client):
    response = api_client.get("/configurations")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res[0]["author_username"] == "unknown-user@example.com"
    assert res[0]["study_field_name"] == "unit1-patched"
    assert res[0]["study_field_grouping"] == "sbtab"
    assert res[0]["end_date"] is None
    assert res[0]["status"] == "Draft"
    assert res[0]["version"] == "0.2"
    assert res[0]["change_description"] == "Patched version"
    assert res[0]["uid"] == "CTConfig_000002"
    assert res[0]["study_field_data_type"] == "text"
    assert res[0]["study_field_null_value_code"] is None
    assert res[0]["configured_codelist_uid"] is None
    assert res[0]["configured_term_uid"] is None
    assert res[0]["study_field_name_api"] == "unit1"
    assert res[0]["is_dictionary_term"] is False


def test_get_specific_latest1(api_client):
    response = api_client.get("/configurations/CTConfig_000002")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.2"
    assert res["change_description"] == "Patched version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_status_draft1(api_client):
    response = api_client.get("/configurations/CTConfig_000002?status=Draft")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.2"
    assert res["change_description"] == "Patched version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_version_0_2_2(api_client):
    response = api_client.get("/configurations/CTConfig_000002?version=0.2")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.2"
    assert res["change_description"] == "Patched version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_at_specified_date_start_date_ofv0_2(api_client):
    response = api_client.get(
        "/configurations/CTConfig_000002?at_specified_date={start_date_ofV0_2}"
    )

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.2"
    assert res["change_description"] == "Patched version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_version_0_11(api_client):
    response = api_client.get("/configurations/CTConfig_000002?version=0.1")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.1"
    assert res["change_description"] == "Initial version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_status_final1(api_client):
    response = api_client.get("/configurations/CTConfig_000002?status=Final")

    assert_response_status_code(response, 404)

    res = response.json()

    assert res["type"] == "NotFoundException"
    assert res["message"] == "CT Config with UID 'CTConfig_000002' doesn't exist."


def test_get_specific_status_retired1(api_client):
    response = api_client.get("/configurations/CTConfig_000002?status=Retired")

    assert_response_status_code(response, 404)

    res = response.json()

    assert res["type"] == "NotFoundException"
    assert res["message"] == "CT Config with UID 'CTConfig_000002' doesn't exist."


def test_approve(api_client):
    response = api_client.post("/configurations/CTConfig_000002/approvals")

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Final"
    assert res["version"] == "1.0"
    assert res["change_description"] == "Approved version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_all_after_approval(api_client):
    response = api_client.get("/configurations")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res[0]["author_username"] == "unknown-user@example.com"
    assert res[0]["study_field_name"] == "unit1-patched"
    assert res[0]["study_field_grouping"] == "sbtab"
    assert res[0]["end_date"] is None
    assert res[0]["status"] == "Final"
    assert res[0]["version"] == "1.0"
    assert res[0]["change_description"] == "Approved version"
    assert res[0]["uid"] == "CTConfig_000002"
    assert res[0]["study_field_data_type"] == "text"
    assert res[0]["study_field_null_value_code"] is None
    assert res[0]["configured_codelist_uid"] is None
    assert res[0]["configured_term_uid"] is None
    assert res[0]["study_field_name_api"] == "unit1"
    assert res[0]["is_dictionary_term"] is False


def test_get_by_uid_after_approval(api_client):
    response = api_client.get("/configurations/CTConfig_000002")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Final"
    assert res["version"] == "1.0"
    assert res["change_description"] == "Approved version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_by_uid_after_approval_status_final(api_client):
    response = api_client.get("/configurations/CTConfig_000002?status=Final")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Final"
    assert res["version"] == "1.0"
    assert res["change_description"] == "Approved version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_by_uid_after_approval_version_1_0(api_client):
    response = api_client.get("/configurations/CTConfig_000002?version=1.0")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Final"
    assert res["version"] == "1.0"
    assert res["change_description"] == "Approved version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_approve_final_fail(api_client):
    response = api_client.post("/configurations/CTConfig_000002/approvals")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "The object isn't in draft status."


def test_reactivate_final_fail(api_client):
    response = api_client.post("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Only RETIRED version can be reactivated."


def test_patch_final_fail(api_client):
    data = {
        "change_description": "Patched version 2 (failed)",
        "study_field_name": "unit1-patched-fail",
        "unit_dimension": "unit1-dimension-patched-fail",
        "legacy_code": "unit1-legacy-code-patched-fail",
        "study_field_data_type": "text",
        "study_field_name_api": "unit1",
        "is_dictionary_term": False,
    }
    response = api_client.patch("/configurations/CTConfig_000002", json=data)

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "The object isn't in draft status."


def test_delete_final_fail(api_client):
    response = api_client.delete("/configurations/CTConfig_000002")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Object has been accepted"


def test_inactivate(api_client):
    response = api_client.delete("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Retired"
    assert res["version"] == "1.0"
    assert res["change_description"] == "Inactivated version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_retire_retired_fail(api_client):
    response = api_client.delete("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Cannot retire draft version."


def test_post_versions_on_retired_fail(api_client):
    response = api_client.post("/configurations/CTConfig_000002/versions")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "New draft version can be created only for FINAL versions."


def test_approve_retired_fail(api_client):
    response = api_client.post("/configurations/CTConfig_000002/approvals")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "The object isn't in draft status."


def test_patch_retired_fail(api_client):
    data = {
        "change_description": "Patched version 2 (failed)",
        "study_field_name": "unit1-patched-fail",
        "unit_dimension": "unit1-dimension-patched-fail",
        "legacy_code": "unit1-legacy-code-patched-fail",
        "study_field_data_type": "text",
        "study_field_name_api": "unit1",
        "is_dictionary_term": False,
    }
    response = api_client.patch("/configurations/CTConfig_000002", json=data)

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "The object isn't in draft status."


def test_delete_retired_fail(api_client):
    response = api_client.delete("/configurations/CTConfig_000002")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Object has been accepted"


def test_reactivate(api_client):
    response = api_client.post("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Final"
    assert res["version"] == "1.0"
    assert res["change_description"] == "Reactivated version"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_approve_final_fail1(api_client):
    response = api_client.post("/configurations/CTConfig_000002/approvals")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "The object isn't in draft status."


def test_reactivate_final_fail1(api_client):
    response = api_client.post("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Only RETIRED version can be reactivated."


def test_patch_final_fail1(api_client):
    data = {
        "change_description": "Patched version 2 (failed)",
        "study_field_name": "unit1-patched-fail",
        "unit_dimension": "unit1-dimension-patched-fail",
        "legacy_code": "unit1-legacy-code-patched-fail",
        "study_field_data_type": "text",
        "study_field_name_api": "unit1",
        "is_dictionary_term": False,
    }
    response = api_client.patch("/configurations/CTConfig_000002", json=data)

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "The object isn't in draft status."


def test_delete_final_fail1(api_client):
    response = api_client.delete("/configurations/CTConfig_000002")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Object has been accepted"


def test_post_versions(api_client):
    response = api_client.post("/configurations/CTConfig_000002/versions")

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["author_username"] == "unknown-user@example.com"
    assert res["study_field_name"] == "unit1-patched"
    assert res["study_field_grouping"] == "sbtab"
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "1.1"
    assert res["change_description"] == "New draft created"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_retire_draft_fail1(api_client):
    response = api_client.delete("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Cannot retire draft version."


def test_reactivate_draft_fail1(api_client):
    response = api_client.post("/configurations/CTConfig_000002/activations")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Only RETIRED version can be reactivated."


def test_post_versions_draft_fail1(api_client):
    response = api_client.post("/configurations/CTConfig_000002/versions")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "New draft version can be created only for FINAL versions."


def test_delete_draft_that_had_been_accepted_fail(api_client):
    response = api_client.delete("/configurations/CTConfig_000002")

    assert_response_status_code(response, 400)

    res = response.json()

    assert res["type"] == "BusinessLogicException"
    assert res["message"] == "Object has been accepted"


def test_patch_next_major_version(api_client):
    data = {
        "change_description": "Patched version 2",
        "study_field_name": "unit1-patched-2",
        "study_field_data_type": "text",
        "study_field_name_api": "unit1",
        "is_dictionary_term": False,
    }
    response = api_client.patch("/configurations/CTConfig_000002", json=data)

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["end_date"] is None
    assert res["author_username"] == "unknown-user@example.com"
    assert res["change_description"] == "Patched version 2"
    assert res["study_field_grouping"] == "sbtab"
    assert res["status"] == "Draft"
    assert res["version"] == "1.2"
    assert res["study_field_name"] == "unit1-patched-2"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_latest2(api_client):
    response = api_client.get("/configurations/CTConfig_000002")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["end_date"] is None
    assert res["author_username"] == "unknown-user@example.com"
    assert res["change_description"] == "Patched version 2"
    assert res["study_field_grouping"] == "sbtab"
    assert res["status"] == "Draft"
    assert res["version"] == "1.2"
    assert res["study_field_name"] == "unit1-patched-2"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_specific_status_final2(api_client):
    response = api_client.get("/configurations/CTConfig_000002?status=Final")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["end_date"] is None
    assert res["author_username"] == "unknown-user@example.com"
    assert res["change_description"] == "Reactivated version"
    assert res["study_field_grouping"] == "sbtab"
    assert res["status"] == "Final"
    assert res["version"] == "1.0"
    assert res["study_field_name"] == "unit1-patched"
    assert res["uid"] == "CTConfig_000002"
    assert res["study_field_data_type"] == "text"
    assert res["study_field_null_value_code"] is None
    assert res["configured_codelist_uid"] is None
    assert res["configured_term_uid"] is None
    assert res["study_field_name_api"] == "unit1"
    assert res["is_dictionary_term"] is False


def test_get_versions1(api_client):
    response = api_client.get("/configurations/CTConfig_000002/versions")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res[0]["author_username"] == "unknown-user@example.com"
    assert res[0]["study_field_name"] == "unit1-patched-2"
    assert res[0]["study_field_grouping"] == "sbtab"
    assert res[0]["end_date"] is None
    assert res[0]["status"] == "Draft"
    assert res[0]["version"] == "1.2"
    assert res[0]["change_description"] == "Patched version 2"
    assert res[0]["uid"] == "CTConfig_000002"
    assert res[0]["study_field_data_type"] == "text"
    assert res[0]["study_field_null_value_code"] is None
    assert res[0]["configured_codelist_uid"] is None
    assert res[0]["configured_term_uid"] is None
    assert res[0]["study_field_name_api"] == "unit1"
    assert res[0]["is_dictionary_term"] is False
    assert res[1]["author_username"] == "unknown-user@example.com"
    assert res[1]["study_field_name"] == "unit1-patched"
    assert res[1]["study_field_grouping"] == "sbtab"
    assert res[1]["end_date"] is None
    assert res[1]["status"] == "Draft"
    assert res[1]["version"] == "1.1"
    assert res[1]["change_description"] == "New draft created"
    assert res[1]["uid"] == "CTConfig_000002"
    assert res[1]["study_field_data_type"] == "text"
    assert res[1]["study_field_null_value_code"] is None
    assert res[1]["configured_codelist_uid"] is None
    assert res[1]["configured_term_uid"] is None
    assert res[1]["study_field_name_api"] == "unit1"
    assert res[1]["is_dictionary_term"] is False
    assert res[2]["author_username"] == "unknown-user@example.com"
    assert res[2]["study_field_name"] == "unit1-patched"
    assert res[2]["study_field_grouping"] == "sbtab"
    assert res[2]["end_date"] is None
    assert res[2]["status"] == "Final"
    assert res[2]["version"] == "1.0"
    assert res[2]["change_description"] == "Reactivated version"
    assert res[2]["uid"] == "CTConfig_000002"
    assert res[2]["study_field_data_type"] == "text"
    assert res[2]["study_field_null_value_code"] is None
    assert res[2]["configured_codelist_uid"] is None
    assert res[2]["configured_term_uid"] is None
    assert res[2]["study_field_name_api"] == "unit1"
    assert res[2]["is_dictionary_term"] is False
    assert res[3]["author_username"] == "unknown-user@example.com"
    assert res[3]["study_field_name"] == "unit1-patched"
    assert res[3]["study_field_grouping"] == "sbtab"
    assert res[3]["end_date"] is None
    assert res[3]["status"] == "Retired"
    assert res[3]["version"] == "1.0"
    assert res[3]["change_description"] == "Inactivated version"
    assert res[3]["uid"] == "CTConfig_000002"
    assert res[3]["study_field_data_type"] == "text"
    assert res[3]["study_field_null_value_code"] is None
    assert res[3]["configured_codelist_uid"] is None
    assert res[3]["configured_term_uid"] is None
    assert res[3]["study_field_name_api"] == "unit1"
    assert res[3]["is_dictionary_term"] is False
    assert res[4]["author_username"] == "unknown-user@example.com"
    assert res[4]["study_field_name"] == "unit1-patched"
    assert res[4]["study_field_grouping"] == "sbtab"
    assert res[4]["end_date"] is None
    assert res[4]["status"] == "Final"
    assert res[4]["version"] == "1.0"
    assert res[4]["change_description"] == "Approved version"
    assert res[4]["uid"] == "CTConfig_000002"
    assert res[4]["study_field_data_type"] == "text"
    assert res[4]["study_field_null_value_code"] is None
    assert res[4]["configured_codelist_uid"] is None
    assert res[4]["configured_term_uid"] is None
    assert res[4]["study_field_name_api"] == "unit1"
    assert res[4]["is_dictionary_term"] is False
    assert res[5]["author_username"] == "unknown-user@example.com"
    assert res[5]["study_field_name"] == "unit1-patched"
    assert res[5]["study_field_grouping"] == "sbtab"
    assert res[5]["end_date"] is None
    assert res[5]["status"] == "Draft"
    assert res[5]["version"] == "0.2"
    assert res[5]["change_description"] == "Patched version"
    assert res[5]["uid"] == "CTConfig_000002"
    assert res[5]["study_field_data_type"] == "text"
    assert res[5]["study_field_null_value_code"] is None
    assert res[5]["configured_codelist_uid"] is None
    assert res[5]["configured_term_uid"] is None
    assert res[5]["study_field_name_api"] == "unit1"
    assert res[5]["is_dictionary_term"] is False
    assert res[6]["author_username"] == "unknown-user@example.com"
    assert res[6]["study_field_name"] == "unit1"
    assert res[6]["study_field_grouping"] == "sbtab"
    assert res[6]["end_date"] is None
    assert res[6]["status"] == "Draft"
    assert res[6]["version"] == "0.1"
    assert res[6]["change_description"] == "Initial version"
    assert res[6]["uid"] == "CTConfig_000002"
    assert res[6]["study_field_data_type"] == "text"
    assert res[6]["study_field_null_value_code"] is None
    assert res[6]["configured_codelist_uid"] is None
    assert res[6]["configured_term_uid"] is None
    assert res[6]["study_field_name_api"] == "unit1"
    assert res[6]["is_dictionary_term"] is False
