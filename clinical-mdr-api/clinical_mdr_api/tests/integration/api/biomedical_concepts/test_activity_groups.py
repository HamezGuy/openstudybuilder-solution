"""
Tests for /concepts/activities/activity-groups endpoints
"""

import logging
from operator import itemgetter

import pytest
from fastapi.testclient import TestClient

from clinical_mdr_api.main import app
from clinical_mdr_api.models.concepts.activities.activity_group import ActivityGroup
from clinical_mdr_api.tests.integration.utils.api import (
    inject_and_clear_db,
    inject_base_data,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from clinical_mdr_api.tests.utils.checks import assert_response_status_code

# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-arguments

# pytest fixture functions have other fixture functions as arguments,
# which pylint interprets as unused arguments


log = logging.getLogger(__name__)

# Global variables shared between fixtures and tests
activity_groups_all: list[ActivityGroup]


@pytest.fixture(scope="module")
def api_client(test_data):
    """Create FastAPI test client
    using the database name set in the `test_data` fixture"""
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    """Initialize test data"""
    db_name = "activitiesgroups.api"
    inject_and_clear_db(db_name)
    inject_base_data()

    global activity_groups_all
    activity_groups_all = [
        TestUtils.create_activity_group(name="name-AAA"),
        TestUtils.create_activity_group(
            name="name-BBB",
        ),
    ]

    for index in range(5):
        activity_groups_all.append(
            TestUtils.create_activity_group(
                name=f"ActivityGroup-{index}",
            )
        )

    yield


ACTIVITY_GROUP_FIELDS_ALL = [
    "uid",
    "name",
    "name_sentence_case",
    "nci_concept_id",
    "nci_concept_name",
    "definition",
    "abbreviation",
    "library_name",
    "start_date",
    "end_date",
    "status",
    "version",
    "change_description",
    "author_username",
    "possible_actions",
]

ACTIVITY_GROUP_FIELDS_NOT_NULL = ["uid", "name", "start_date"]


def test_get_activity_group(api_client):
    response = api_client.get(
        f"/concepts/activities/activity-groups/{activity_groups_all[0].uid}"
    )
    res = response.json()

    assert_response_status_code(response, 200)

    # Check fields included in the response
    assert set(res.keys()) == set(ACTIVITY_GROUP_FIELDS_ALL)
    for key in ACTIVITY_GROUP_FIELDS_NOT_NULL:
        assert res[key] is not None

    assert res["uid"] == activity_groups_all[0].uid
    assert res["name"] == "name-AAA"
    assert res["name_sentence_case"] == "name-AAA"
    assert res["library_name"] == "Sponsor"
    assert res["definition"] is None
    assert res["version"] == "1.0"
    assert res["status"] == "Final"
    assert res["possible_actions"] == ["inactivate", "new_version"]


def test_get_activity_group_version(api_client):
    response = api_client.get(
        f"/concepts/activities/activity-groups/{activity_groups_all[0].uid}/versions"
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert len(res) == 2

    # Check fields included in the response
    for item in res:
        assert set(list(item.keys())) == set(ACTIVITY_GROUP_FIELDS_ALL)
        for key in ACTIVITY_GROUP_FIELDS_NOT_NULL:
            assert item[key] is not None

    assert res[0]["uid"] == activity_groups_all[0].uid
    assert res[0]["name"] == "name-AAA"
    assert res[0]["name_sentence_case"] == "name-AAA"
    assert res[0]["library_name"] == "Sponsor"
    assert res[0]["definition"] is None
    assert res[0]["version"] == "1.0"
    assert res[0]["status"] == "Final"
    assert res[0]["possible_actions"] == ["inactivate", "new_version"]

    assert res[1]["uid"] == activity_groups_all[0].uid
    assert res[1]["name"] == "name-AAA"
    assert res[1]["name_sentence_case"] == "name-AAA"
    assert res[1]["library_name"] == "Sponsor"
    assert res[1]["definition"] is None
    assert res[1]["version"] == "0.1"
    assert res[1]["status"] == "Draft"
    assert res[1]["possible_actions"] == ["approve", "delete", "edit"]


def test_get_activity_groups_versions(api_client):
    # Create a new version of an activity group
    response = api_client.post(
        f"/concepts/activities/activity-groups/{activity_groups_all[0].uid}/versions"
    )
    assert_response_status_code(response, 201)

    # Get all versions of all activities
    response = api_client.get(
        "/concepts/activities/activity-groups/versions?page_size=100"
    )
    res = response.json()

    assert_response_status_code(response, 200)

    # Check fields included in the response
    assert set(res.keys()) == set(["items", "total", "page", "size"])

    assert len(res["items"]) == len(activity_groups_all) * 2 + 1
    for item in res["items"]:
        assert set(list(item.keys())) == set(ACTIVITY_GROUP_FIELDS_ALL)
        for key in ACTIVITY_GROUP_FIELDS_NOT_NULL:
            assert item[key] is not None

    # Check that the items are sorted by start_date descending
    sorted_items = sorted(res["items"], key=itemgetter("start_date"), reverse=True)
    assert sorted_items == res["items"]


@pytest.mark.parametrize(
    "filter_by, expected_matched_field, expected_result_prefix",
    [
        pytest.param('{"*": {"v": ["aaa"]}}', "name", "name-AAA"),
        pytest.param('{"*": {"v": ["bBb"]}}', "name", "name-BBB"),
        pytest.param('{"*": {"v": ["ccc"]}}', None, None),
        pytest.param('{"*": {"v": ["Final"]}}', "status", "Final"),
        pytest.param('{"*": {"v": ["1.0"]}}', "version", "1.0"),
    ],
)
def test_filtering_versions_wildcard(
    api_client, filter_by, expected_matched_field, expected_result_prefix
):
    url = f"/concepts/activities/activity-groups/versions?filters={filter_by}"
    response = api_client.get(url)
    res = response.json()

    assert_response_status_code(response, 200)
    if expected_result_prefix:
        assert len(res["items"]) > 0
        nested_path = None

        # if we expect a nested property to be equal to specified value
        if isinstance(expected_matched_field, str) and "." in expected_matched_field:
            nested_path = expected_matched_field.split(".")
            expected_matched_field = nested_path[-1]
            nested_path = nested_path[:-1]

        # Each returned row has a field that starts with the specified filter value
        for row in res["items"]:
            if nested_path:
                for prop in nested_path:
                    row = row[prop]
            if isinstance(row, list):
                any(
                    item[expected_matched_field].startswith(expected_result_prefix)
                    for item in row
                )
            else:
                assert row[expected_matched_field].startswith(expected_result_prefix)
    else:
        assert len(res["items"]) == 0


@pytest.mark.parametrize(
    "filter_by, expected_matched_field, expected_result",
    [
        pytest.param('{"name": {"v": ["name-AAA"]}}', "name", "name-AAA"),
        pytest.param('{"name": {"v": ["name-BBB"]}}', "name", "name-BBB"),
        pytest.param('{"name": {"v": ["cc"]}}', None, None),
        pytest.param(
            '{"name_sentence_case": {"v": ["name-AAA"]}}',
            "name_sentence_case",
            "name-AAA",
        ),
        pytest.param(
            '{"name_sentence_case": {"v": ["name-BBB"]}}',
            "name_sentence_case",
            "name-BBB",
        ),
        pytest.param('{"name_sentence_case": {"v": ["cc"]}}', None, None),
    ],
)
def test_filtering_versions_exact(
    api_client, filter_by, expected_matched_field, expected_result
):
    url = f"/concepts/activities/activity-groups/versions?filters={filter_by}"
    response = api_client.get(url)
    res = response.json()

    assert_response_status_code(response, 200)
    if expected_result:
        assert len(res["items"]) > 0

        # if we expect a nested property to be equal to specified value
        nested_path = None
        if isinstance(expected_matched_field, str) and "." in expected_matched_field:
            nested_path = expected_matched_field.split(".")
            expected_matched_field = nested_path[-1]
            nested_path = nested_path[:-1]

        # Each returned row has a field whose value is equal to the specified filter value
        for row in res["items"]:
            if nested_path:
                for prop in nested_path:
                    row = row[prop]
            if isinstance(expected_result, list):
                assert all(
                    item in row[expected_matched_field] for item in expected_result
                )
            else:
                if isinstance(row, list):
                    all(item[expected_matched_field] == expected_result for item in row)
                else:
                    assert row[expected_matched_field] == expected_result
    else:
        assert len(res["items"]) == 0


def test_cascade_edit_activity_groups(api_client):
    # ==== Create activity and activity instance ====
    activity_group = TestUtils.create_activity_group(name="Cascade Group")
    _second_activity_group = TestUtils.create_activity_group(name="Second Group")
    activity_subgroup = TestUtils.create_activity_subgroup(
        name="Cascade SubGroup",
    )
    activity = TestUtils.create_activity(
        name="Cascade Activity",
        activity_subgroups=[activity_subgroup.uid],
        activity_groups=[activity_group.uid],
        approve=True,
    )
    activity_instance_class = (
        TestUtils.create_activity_instance_class(
            name="Activity instance class",
            definition="def Activity instance class",
            is_domain_specific=True,
            level=1,
        ),
    )
    activity_instance = TestUtils.create_activity_instance(
        name="Cascade Activity Instance",
        activity_instance_class_uid=activity_instance_class[0].uid,
        name_sentence_case="cascade activity instance",
        nci_concept_id="C-1234",
        topic_code="cascade activity instance tc",
        activities=[activity.uid],
        activity_subgroups=[activity_subgroup.uid],
        activity_groups=[activity_group.uid],
        approve=True,
    )
    response = api_client.get(
        f"/concepts/activities/activity-sub-groups/{activity_subgroup.uid}"
    )

    res = response.json()
    assert_response_status_code(response, 200)
    assert res["name"] == activity_subgroup.name
    assert res["version"] == "1.0"
    assert res["status"] == "Final"

    # ==== Update activity group with cascade edit&approve, activity and instance should be updated also ====

    # Create new version of activity
    response = api_client.post(
        f"/concepts/activities/activity-groups/{activity_group.uid}/versions",
        json={},
    )
    assert_response_status_code(response, 201)

    # Update the activity group
    updated_activity_group_name = "Edited Cascade Activity Group"
    concept_id = "CONCEPT_ID"
    concept_name = "concept name"
    response = api_client.put(
        f"/concepts/activities/activity-groups/{activity_group.uid}",
        json={
            "name": updated_activity_group_name,
            "name_sentence_case": updated_activity_group_name.lower(),
            "nci_concept_id": concept_id,
            "nci_concept_name": concept_name,
            "change_description": "test cascade edit",
            "library_name": activity_group.library_name,
        },
    )
    assert_response_status_code(response, 200)

    # Approve the activity group with cascade_edit_and_approve set to True
    response = api_client.post(
        f"/concepts/activities/activity-groups/{activity_group.uid}/approvals",
        params={"cascade_edit_and_approve": True},
    )
    assert_response_status_code(response, 201)

    # Assert Activity Group was updated
    response = api_client.get(
        f"/concepts/activities/activity-groups/{activity_group.uid}"
    )
    assert_response_status_code(response, 200)
    res = response.json()
    assert res["name"] == updated_activity_group_name
    assert res["name_sentence_case"] == updated_activity_group_name.lower()
    assert res["nci_concept_id"] == concept_id
    assert res["nci_concept_name"] == concept_name
    assert res["status"] == "Final"
    assert res["version"] == "2.0"

    # Get the activity and assert that it was updated
    response = api_client.get(f"/concepts/activities/activities/{activity.uid}")
    assert_response_status_code(response, 200)
    res = response.json()
    assert res["version"] == "2.0"
    assert res["status"] == "Final"
    assert len(res["activity_groupings"]) == 1
    assert res["activity_groupings"][0]["activity_group_uid"] == activity_group.uid
    assert (
        res["activity_groupings"][0]["activity_group_name"]
        == updated_activity_group_name
    )
    assert (
        res["activity_groupings"][0]["activity_subgroup_uid"] == activity_subgroup.uid
    )
    assert (
        res["activity_groupings"][0]["activity_subgroup_name"] == activity_subgroup.name
    )

    # Get the activity instance and assert that it was updated
    response = api_client.get(
        f"/concepts/activities/activity-instances/{activity_instance.uid}"
    )
    assert_response_status_code(response, 200)
    res = response.json()
    assert res["groupings_version"] == "2.0"
    assert res["groupings_status"] == "Final"
    assert len(res["activity_groupings"]) == 1
    assert res["activity_groupings"][0]["activity_group"]["uid"] == activity_group.uid
    assert (
        res["activity_groupings"][0]["activity_group"]["name"]
        == updated_activity_group_name
    )
    assert (
        res["activity_groupings"][0]["activity_subgroup"]["uid"]
        == activity_subgroup.uid
    )
    assert (
        res["activity_groupings"][0]["activity_subgroup"]["name"]
        == activity_subgroup.name
    )
    assert res["activity_groupings"][0]["activity"]["uid"] == activity.uid
    assert res["activity_groupings"][0]["activity"]["name"] == activity.name

    # Get the activity versions and assert that one new version was created.
    # There should be a new final version 2.0 that links to activity group version 2.0
    # and activity subgroup version 1.0
    response = api_client.get(
        f"/concepts/activities/activities/{activity.uid}/versions"
    )
    assert_response_status_code(response, 200)
    res = response.json()
    unchanged_draft = TestUtils._get_version_from_list(res, "1.1")
    updated_draft = TestUtils._get_version_from_list(res, "1.2")
    new_final = TestUtils._get_version_from_list(res, "2.0")

    assert unchanged_draft is None
    assert updated_draft is None
    assert (
        new_final["activity_groupings"][0]["activity_group_name"]
        == updated_activity_group_name
    )

    # Get the activity instance versions and assert that one new version was created.
    # There should be a new final version 2.0 that links to activity version 2.0
    response = api_client.get(
        f"/concepts/activities/activity-instances/{activity_instance.uid}/groupings/versions"
    )
    assert_response_status_code(response, 200)
    res = response.json()
    unchanged_draft = TestUtils._get_version_from_list(res, "1.1")
    updated_draft = TestUtils._get_version_from_list(res, "1.2")
    new_final = TestUtils._get_version_from_list(res, "2.0")

    assert unchanged_draft is None
    assert updated_draft is None
    assert (
        new_final["activity_groupings"][0]["activity_group"]["name"]
        == updated_activity_group_name
    )

    # ==== Update activity group without cascade edit&approve, activity and instance should NOT be updated ====
    # Create new version of activity
    response = api_client.post(
        f"/concepts/activities/activity-groups/{activity_group.uid}/versions",
        json={},
    )
    assert_response_status_code(response, 201)

    # Put the activity group
    second_updated_activity_group_name = "Another update of activity group name"
    response = api_client.put(
        f"/concepts/activities/activity-groups/{activity_group.uid}",
        json={
            "name": second_updated_activity_group_name,
            "name_sentence_case": second_updated_activity_group_name.lower(),
            "change_description": "test cascade edit again",
            "library_name": activity.library_name,
        },
    )
    assert_response_status_code(response, 200)

    # Approve the activity group with cascade_edit_and_approve set to False
    response = api_client.post(
        f"/concepts/activities/activity-groups/{activity_group.uid}/approvals",
        params={"cascade_edit_and_approve": False},
    )
    assert_response_status_code(response, 201)

    # Get the activity and assert that it was not updated
    response = api_client.get(f"/concepts/activities/activities/{activity.uid}")
    assert_response_status_code(response, 200)
    res = response.json()
    assert res["version"] == "2.0"
    assert res["status"] == "Final"
    assert len(res["activity_groupings"]) == 1
    assert (
        res["activity_groupings"][0]["activity_subgroup_uid"] == activity_subgroup.uid
    )
    assert res["activity_groupings"][0]["activity_group_uid"] == activity_group.uid
    assert (
        res["activity_groupings"][0]["activity_group_name"]
        == updated_activity_group_name
    )

    # Get the activity instance and assert that it was not updated
    response = api_client.get(
        f"/concepts/activities/activity-instances/{activity_instance.uid}"
    )
    assert_response_status_code(response, 200)
    res = response.json()
    assert res["groupings_version"] == "2.0"
    assert res["groupings_status"] == "Final"
    assert len(res["activity_groupings"]) == 1
    assert (
        res["activity_groupings"][0]["activity_subgroup"]["uid"]
        == activity_subgroup.uid
    )
    assert res["activity_groupings"][0]["activity_group"]["uid"] == activity_group.uid
    assert (
        res["activity_groupings"][0]["activity_group"]["name"]
        == updated_activity_group_name
    )
