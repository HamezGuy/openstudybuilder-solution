"""
Tests for CT codelist attributes operations
"""

# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-arguments

# pytest fixture functions have other fixture functions as arguments,
# which pylint interprets as unused arguments

import logging

import pytest
from fastapi.testclient import TestClient

from clinical_mdr_api.domains.controlled_terminologies.ct_codelist_attributes import (
    DEFAULT_CODELIST_TYPE,
)
from clinical_mdr_api.main import app
from clinical_mdr_api.tests.integration.utils.api import (
    inject_and_clear_db,
    inject_base_data,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from clinical_mdr_api.tests.utils.checks import assert_response_status_code

log = logging.getLogger(__name__)

# Global variables shared between fixtures and tests
BASE_CODELIST = None

CATALOGUE_NAME = "SDTM CT"
SPONSOR_LIBRARY = "Sponsor"
URL = "/ct/codelists"


@pytest.fixture(scope="module")
def api_client(test_data):
    """Create FastAPI test client"""
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    """Initialize test data"""
    db_name = "ct-codelist-attributes.api"
    inject_and_clear_db(db_name)
    inject_base_data(inject_unit_subset=False)

    # Create a base codelist that can be reused across tests
    global BASE_CODELIST
    BASE_CODELIST = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Base Codelist",
        submission_value="BASE_CL",
        library_name=SPONSOR_LIBRARY,
        approve=True,
        extensible=True,
    )

    yield


def test_post_create_codelist(api_client):
    """Test creating a new codelist with terms"""
    # Create a term first using the base codelist
    term1 = TestUtils.create_ct_term(
        catalogue_name=CATALOGUE_NAME,
        codelist_uid=BASE_CODELIST.codelist_uid,
        sponsor_preferred_name="term1 preferred",
        submission_value="TERM1_CREATE_CL",
        library_name=SPONSOR_LIBRARY,
        approve=True,
    )

    data = {
        "catalogue_names": [CATALOGUE_NAME],
        "name": "name",
        "parent_codelist_uid": None,
        "submission_value": "CREATE_CL_SUBVAL",
        "nci_preferred_name": "Nci preferred name",
        "definition": "definition",
        "extensible": True,
        "is_ordinal": False,
        "codelist_type": "Response",
        "sponsor_preferred_name": "Sponsor preferred name",
        "template_parameter": True,
        "library_name": SPONSOR_LIBRARY,
        "terms": [
            {
                "term_uid": term1.term_uid,
                "order": 999999,
                "submission_value": "TERM1_CREATE_CL",
            }
        ],
    }
    response = api_client.post(URL, json=data)

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] is not None
    assert res["parent_codelist_uid"] is None
    assert res["child_codelist_uids"] == []
    assert res["name"] == "name"
    assert res["submission_value"] == "CREATE_CL_SUBVAL"
    assert res["nci_preferred_name"] == "Nci preferred name"
    assert res["definition"] == "definition"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["codelist_type"] == "Response"
    assert res["sponsor_preferred_name"] == "Sponsor preferred name"
    assert res["template_parameter"] is True
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["possible_actions"] == ["new_version"]


def test_post_create_codelist_with_parent_codelist(api_client):
    """Test creating a codelist with a parent codelist"""
    # Create parent codelist
    parent_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Parent Codelist",
        submission_value="PARENT_CL",
        library_name=SPONSOR_LIBRARY,
        approve=False,  # Keep as Draft
        extensible=True,
    )

    data = {
        "catalogue_names": [CATALOGUE_NAME],
        "name": "name with parent",
        "parent_codelist_uid": parent_codelist.codelist_uid,
        "submission_value": "Submission value with parent",
        "nci_preferred_name": "Nci preferred name with parent",
        "definition": "definition",
        "extensible": True,
        "is_ordinal": False,
        "sponsor_preferred_name": "Sponsor preferred name with parent",
        "template_parameter": True,
        "library_name": SPONSOR_LIBRARY,
        "terms": [],
    }
    response = api_client.post(URL, json=data)

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] is not None
    assert res["parent_codelist_uid"] == parent_codelist.codelist_uid
    assert res["child_codelist_uids"] == []
    assert res["name"] == "name with parent"
    assert res["submission_value"] == "Submission value with parent"
    assert res["nci_preferred_name"] == "Nci preferred name with parent"
    assert res["definition"] == "definition"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["codelist_type"] == DEFAULT_CODELIST_TYPE
    assert res["sponsor_preferred_name"] == "Sponsor preferred name with parent"
    assert res["template_parameter"] is True
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["possible_actions"] == ["approve", "edit"]


def test_patch_draft_codelist(api_client):
    """Test patching a draft codelist attributes"""
    # Create a draft codelist
    codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Original Name",
        submission_value="ORIGINAL_SUB",
        nci_preferred_name="Original NCI",
        definition="Original definition",
        extensible=False,
        library_name=SPONSOR_LIBRARY,
        approve=False,  # Keep as Draft
    )

    data = {
        "name": "codelist new name",
        "submission_value": "new codelist submission value",
        "nci_preferred_name": "new codelist preferred term",
        "definition": "new codelist definition",
        "extensible": True,
        "codelist_type": "Response",
        "change_description": "changing codelist name",
    }
    response = api_client.patch(f"{URL}/{codelist.codelist_uid}/attributes", json=data)

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] == codelist.codelist_uid
    assert res["parent_codelist_uid"] is None
    assert res["name"] == "codelist new name"
    assert res["submission_value"] == "new codelist submission value"
    assert res["nci_preferred_name"] == "new codelist preferred term"
    assert res["definition"] == "new codelist definition"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["codelist_type"] == "Response"
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.2"
    assert res["change_description"] == "changing codelist name"
    assert res["author_username"] == "unknown-user@example.com"
    assert res["possible_actions"] == ["approve", "edit"]


def test_patch_draft_codelist_that_is_not_tp1(api_client):
    """Test patching a draft codelist that is not template parameter"""
    # Create a draft codelist
    codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Another Original Name",
        submission_value="ANOTHER_ORIGINAL",
        nci_preferred_name="Another Original NCI",
        definition="Another Original definition",
        extensible=False,
        template_parameter=False,
        library_name=SPONSOR_LIBRARY,
        approve=False,  # Keep as Draft
    )

    # First patch
    data = {
        "name": "not tp codelist new name",
        "submission_value": "new not tp codelist submission value",
        "nci_preferred_name": "new not tp codelist preferred term",
        "definition": "new not tp codelist definition",
        "extensible": True,
        "change_description": "changing codelist name",
    }
    response = api_client.patch(f"{URL}/{codelist.codelist_uid}/attributes", json=data)
    assert_response_status_code(response, 200)

    # Second patch
    data = {
        "name": "not tp codelist another new name",
        "submission_value": "new not tp codelist submission value",
        "nci_preferred_name": "new not tp codelist preferred term",
        "definition": "new not tp codelist definition",
        "extensible": True,
        "change_description": "changing codelist name",
    }
    response = api_client.patch(f"{URL}/{codelist.codelist_uid}/attributes", json=data)

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] == codelist.codelist_uid
    assert res["parent_codelist_uid"] is None
    assert res["name"] == "not tp codelist another new name"
    assert res["submission_value"] == "new not tp codelist submission value"
    assert res["nci_preferred_name"] == "new not tp codelist preferred term"
    assert res["definition"] == "new not tp codelist definition"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.3"
    assert res["change_description"] == "changing codelist name"
    assert res["author_username"] == "unknown-user@example.com"
    assert res["possible_actions"] == ["approve", "edit"]


def test_post_versions_codelist(api_client):
    """Test creating a new version of an approved codelist"""
    # Create and approve a codelist
    codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="codelist attributes value1",
        submission_value="codelist submission value1",
        nci_preferred_name="codelist preferred term",
        definition="codelist definition",
        extensible=False,
        library_name=SPONSOR_LIBRARY,
        approve=True,  # Approve it
    )

    response = api_client.post(f"{URL}/{codelist.codelist_uid}/attributes/versions")

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] == codelist.codelist_uid
    assert res["parent_codelist_uid"] is None
    assert res["child_codelist_uids"] == []
    assert res["name"] == "codelist attributes value1"
    assert res["submission_value"] == "codelist submission value1"
    assert res["nci_preferred_name"] == "codelist preferred term"
    assert res["definition"] == "codelist definition"
    assert res["extensible"] is False
    assert res["is_ordinal"] is False
    assert res["codelist_type"] == DEFAULT_CODELIST_TYPE
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "1.1"
    assert res["change_description"] == "New draft created"
    assert res["author_username"] == "unknown-user@example.com"
    assert res["possible_actions"] == ["approve", "edit"]


def test_post_approve_codelist(api_client):
    """Test approving a draft codelist"""
    # Create a draft codelist
    codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Codelist to Approve",
        submission_value="CL_TO_APPROVE",
        nci_preferred_name="NCI to approve",
        definition="definition to approve",
        extensible=True,
        library_name=SPONSOR_LIBRARY,
        approve=False,  # Keep as Draft
    )

    response = api_client.post(f"{URL}/{codelist.codelist_uid}/attributes/approvals")

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] == codelist.codelist_uid
    assert res["parent_codelist_uid"] is None
    assert res["name"] == "Codelist to Approve"
    assert res["submission_value"] == "CL_TO_APPROVE"
    assert res["nci_preferred_name"] == "NCI to approve"
    assert res["definition"] == "definition to approve"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["codelist_type"] == DEFAULT_CODELIST_TYPE
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["end_date"] is None
    assert res["status"] == "Final"
    assert res["version"] == "1.0"
    assert res["change_description"] == "Approved version"
    assert res["author_username"] == "unknown-user@example.com"
    assert res["possible_actions"] == ["new_version"]


def test_get_codelist_with_parent_codelist_uid(api_client):
    """Test getting a codelist that has a parent"""
    # Create parent codelist
    parent_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Get Parent Codelist",
        submission_value="GET_PARENT_CL",
        library_name=SPONSOR_LIBRARY,
        approve=False,
        extensible=True,
    )

    # Create child codelist
    child_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Get Child Codelist",
        submission_value="GET_CHILD_CL",
        nci_preferred_name="Child NCI",
        definition="Child definition",
        extensible=True,
        sponsor_preferred_name="Child Sponsor Name",
        template_parameter=True,
        parent_codelist_uid=parent_codelist.codelist_uid,
        library_name=SPONSOR_LIBRARY,
        approve=False,
    )

    response = api_client.get(f"{URL}/{child_codelist.codelist_uid}/attributes")

    assert_response_status_code(response, 200)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] == child_codelist.codelist_uid
    assert res["parent_codelist_uid"] == parent_codelist.codelist_uid
    assert res["child_codelist_uids"] == []
    assert res["name"] == "Get Child Codelist"
    assert res["submission_value"] == "GET_CHILD_CL"
    assert res["nci_preferred_name"] == "Child NCI"
    assert res["definition"] == "Child definition"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["codelist_type"] == DEFAULT_CODELIST_TYPE
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["end_date"] is None
    assert res["status"] == "Draft"
    assert res["version"] == "0.1"
    assert res["change_description"] == "Initial version"
    assert res["author_username"] == "unknown-user@example.com"
    assert res["possible_actions"] == ["approve", "edit"]


def test_post_add_term_to_codelist(api_client):
    """Test adding a term to an approved codelist"""
    # Create an approved codelist
    codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Codelist for Term",
        submission_value="CL_FOR_TERM",
        sponsor_preferred_name="codelist_name_value",
        library_name=SPONSOR_LIBRARY,
        approve=True,
        extensible=True,
    )

    # Create a term using the base codelist
    term = TestUtils.create_ct_term(
        catalogue_name=CATALOGUE_NAME,
        codelist_uid=BASE_CODELIST.codelist_uid,
        sponsor_preferred_name="Term to Add",
        submission_value="TERM_ADD_TO_CL",
        library_name=SPONSOR_LIBRARY,
        approve=True,
    )

    data = {
        "term_uid": term.term_uid,
        "order": 999999,
        "submission_value": "TERM_ADD_TO_CL",
    }
    response = api_client.post(f"{URL}/{codelist.codelist_uid}/terms", json=data)

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] == codelist.codelist_uid
    assert res["parent_codelist_uid"] is None
    assert res["name"] == "Codelist for Term"
    assert res["submission_value"] == "CL_FOR_TERM"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["sponsor_preferred_name"] == "codelist_name_value"
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["possible_actions"] == ["new_version"]


def test_add_term_to_unapproved_codelist(api_client):
    """Test adding a term to an unapproved codelist"""
    # Create an approved codelist
    codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Unapproved codelist",
        submission_value="UNAPPROVED_CL",
        sponsor_preferred_name="unapproved_codelist_name_value",
        library_name=SPONSOR_LIBRARY,
        approve=False,
        extensible=True,
    )

    # Create a term using the base codelist
    term = TestUtils.create_ct_term(
        catalogue_name=CATALOGUE_NAME,
        codelist_uid=BASE_CODELIST.codelist_uid,
        sponsor_preferred_name="Term to Add to Unapproved codelist",
        submission_value="TERM_ADD_TO_UNAPPROVED_CL",
        library_name=SPONSOR_LIBRARY,
        approve=True,
    )

    data = {
        "term_uid": term.term_uid,
        "order": 999999,
        "submission_value": "TERM_ADD_TO_UNAPPROVED_CL",
    }
    response = api_client.post(f"{URL}/{codelist.codelist_uid}/terms", json=data)
    assert_response_status_code(response, 400)

    response = api_client.post(f"{URL}/{codelist.codelist_uid}/attributes/approvals")
    assert_response_status_code(response, 201)


def test_post_approve_child_codelist(api_client):
    """Test approving a child codelist"""
    # Create parent codelist (approved)
    parent_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Approve Child Parent",
        submission_value="APPROVE_CHILD_PARENT",
        library_name=SPONSOR_LIBRARY,
        approve=True,
        extensible=True,
    )

    # Create child codelist (draft)
    child_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="name with parent to approve",
        submission_value="CHILD_TO_APPROVE_SUBVAL",
        nci_preferred_name="Child NCI preferred name",
        definition="Child definition for approval test",
        extensible=True,
        sponsor_preferred_name="Child sponsor preferred name",
        template_parameter=True,
        parent_codelist_uid=parent_codelist.codelist_uid,
        library_name=SPONSOR_LIBRARY,
        approve=False,
    )

    response = api_client.post(
        f"{URL}/{child_codelist.codelist_uid}/attributes/approvals"
    )

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] == child_codelist.codelist_uid
    assert res["parent_codelist_uid"] == parent_codelist.codelist_uid
    assert res["child_codelist_uids"] == []
    assert res["name"] == "name with parent to approve"
    assert res["submission_value"] == "CHILD_TO_APPROVE_SUBVAL"
    assert res["nci_preferred_name"] == "Child NCI preferred name"
    assert res["definition"] == "Child definition for approval test"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["codelist_type"] == DEFAULT_CODELIST_TYPE
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["end_date"] is None
    assert res["status"] == "Final"
    assert res["version"] == "1.0"
    assert res["change_description"] == "Approved version"
    assert res["author_username"] == "unknown-user@example.com"
    assert res["possible_actions"] == ["new_version"]


def test_post_add_term_to_child_codelist(api_client):
    """Test adding a term to a child codelist"""
    # Create parent codelist (approved)
    parent_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Add Term Child Parent",
        submission_value="ADD_TERM_CHILD_PARENT",
        library_name=SPONSOR_LIBRARY,
        approve=True,
        extensible=True,
    )

    # Create child codelist (approved)
    child_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Child for term addition",
        submission_value="CHILD_FOR_TERM_ADD_SUBVAL",
        nci_preferred_name="Child for term addition NCI",
        definition="Child definition for term addition",
        extensible=True,
        sponsor_preferred_name="Child for term sponsor name",
        template_parameter=True,
        parent_codelist_uid=parent_codelist.codelist_uid,
        library_name=SPONSOR_LIBRARY,
        approve=True,
    )

    # Create a term using the parent codelist
    term = TestUtils.create_ct_term(
        catalogue_name=CATALOGUE_NAME,
        codelist_uid=parent_codelist.codelist_uid,
        sponsor_preferred_name="Term for child",
        submission_value="TERM_FOR_CHILD_CL",
        library_name=SPONSOR_LIBRARY,
        approve=True,
    )

    data = {
        "term_uid": term.term_uid,
        "submission_value": "TERM_FOR_CHILD_CL",
        "order": 999999,
    }
    response = api_client.post(f"{URL}/{child_codelist.codelist_uid}/terms", json=data)

    assert_response_status_code(response, 201)

    res = response.json()

    assert res["catalogue_names"] == [CATALOGUE_NAME]
    assert res["codelist_uid"] == child_codelist.codelist_uid
    assert res["parent_codelist_uid"] == parent_codelist.codelist_uid
    assert res["child_codelist_uids"] == []
    assert res["name"] == "Child for term addition"
    assert res["submission_value"] == "CHILD_FOR_TERM_ADD_SUBVAL"
    assert res["nci_preferred_name"] == "Child for term addition NCI"
    assert res["definition"] == "Child definition for term addition"
    assert res["extensible"] is True
    assert res["is_ordinal"] is False
    assert res["sponsor_preferred_name"] == "Child for term sponsor name"
    assert res["template_parameter"] is True
    assert res["library_name"] == SPONSOR_LIBRARY
    assert res["possible_actions"] == ["new_version"]


def test_get_all_sub_codelists_that_have_the_provided_terms(api_client):
    """Test getting sub-codelists that contain specific terms"""
    # Create parent codelist (approved)
    parent_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Sub Codelists Parent",
        submission_value="SUB_CODELISTS_PARENT",
        library_name=SPONSOR_LIBRARY,
        approve=True,
        extensible=True,
    )

    # Create child codelist (draft initially, then approved)
    child_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        name="Sub child codelist",
        submission_value="Sub child codelist submission value",
        nci_preferred_name="Sub child NCI",
        definition="Sub child definition",
        extensible=True,
        sponsor_preferred_name="Sub child sponsor preferred name",
        template_parameter=True,
        parent_codelist_uid=parent_codelist.codelist_uid,
        library_name=SPONSOR_LIBRARY,
        approve=True,
    )

    # Create a term and add to child codelist
    term = TestUtils.create_ct_term(
        catalogue_name=CATALOGUE_NAME,
        codelist_uid=child_codelist.codelist_uid,
        sponsor_preferred_name="Shared Term",
        submission_value="SHARED_TERM_SUB",
        library_name=SPONSOR_LIBRARY,
        approve=True,
    )

    # Add term to parent codelist as well (this creates new version)
    data = {
        "term_uid": term.term_uid,
        "submission_value": "SHARED_TERM_SUB",
        "order": 999999,
    }
    api_client.post(f"{URL}/{parent_codelist.codelist_uid}/terms", json=data)

    # Now query for sub-codelists with this term
    response = api_client.get(
        f"{URL}/{parent_codelist.codelist_uid}/sub-codelists",
        params={"term_uids": term.term_uid},
    )

    assert_response_status_code(response, 200)

    res = response.json()

    assert len(res["items"]) >= 1
    # Find the child codelist in the results
    child_result = next(
        (
            item
            for item in res["items"]
            if item["codelist_uid"] == child_codelist.codelist_uid
        ),
        None,
    )
    assert child_result is not None
    assert child_result["catalogue_names"] == [CATALOGUE_NAME]
    assert child_result["codelist_uid"] == child_codelist.codelist_uid
    assert child_result["parent_codelist_uid"] == parent_codelist.codelist_uid
    assert child_result["child_codelist_uids"] == []
    assert child_result["library_name"] == SPONSOR_LIBRARY
    assert child_result["name"]["name"] == "Sub child sponsor preferred name"
    assert child_result["name"]["template_parameter"] is True
    assert child_result["attributes"]["name"] == "Sub child codelist"
    assert (
        child_result["attributes"]["submission_value"]
        == "Sub child codelist submission value"
    )
    assert child_result["attributes"]["nci_preferred_name"] == "Sub child NCI"
    assert child_result["attributes"]["definition"] == "Sub child definition"
    assert child_result["attributes"]["extensible"] is True


def test_ordinal_codelist(api_client):
    """Test ordinal codelist behaviour"""
    ordinal_codelist = TestUtils.create_ct_codelist(
        catalogue_name=CATALOGUE_NAME,
        library_name=SPONSOR_LIBRARY,
        is_ordinal=True,
        approve=True,
    )

    # Create term in the base codelist
    term = TestUtils.create_ct_term(
        catalogue_name=CATALOGUE_NAME,
        codelist_uid=BASE_CODELIST.codelist_uid,
        sponsor_preferred_name="Not-ordinal term",
        submission_value="NOT_ORDINAL_TERM",
        library_name=SPONSOR_LIBRARY,
        approve=True,
    )

    # Add term to ordinal codelist, without specifying ordinal
    data = {
        "term_uid": term.term_uid,
        "submission_value": "NOT_ORDINAL_TERM",
        "order": 999999,
    }
    response = api_client.post(
        f"{URL}/{ordinal_codelist.codelist_uid}/terms", json=data
    )
    assert_response_status_code(response, 400)

    data["ordinal"] = 1.0
    response = api_client.post(
        f"{URL}/{ordinal_codelist.codelist_uid}/terms", json=data
    )
    assert_response_status_code(response, 201)
