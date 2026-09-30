"""Version-specific groupings distinguish an empty definition from missing/broken data."""

# pylint: disable=redefined-outer-name

import pytest
from fastapi.testclient import TestClient
from neomodel import db


@pytest.fixture(scope="module")
def groupings_client(temp_database):
    # Real Neo4j relationships, including an unbound historical version whose
    # current version has a grouping. No clinical bindings are synthesized by GET.
    db.cypher_query(
        """
        CREATE (gr:ActivityGroupRoot {uid: 'Group_fixture'})
            -[:HAS_VERSION {version: '1.0', status: 'Final', start_date: datetime('2026-09-01T00:00:00Z')}]->
            (gv:ActivityGroupValue {name: 'Source group'})
        CREATE (sgr:ActivitySubGroupRoot {uid: 'Subgroup_fixture'})
            -[:HAS_VERSION {version: '1.0', status: 'Final', start_date: datetime('2026-09-01T00:00:00Z')}]->
            (sgv:ActivitySubGroupValue {name: 'Source subgroup'})
        CREATE (grouping:ActivityGrouping {uid: 'Grouping_fixture'})
            -[:HAS_SELECTED_GROUP]->(gv)
        CREATE (grouping)-[:HAS_SELECTED_SUBGROUP]->(sgv)
        CREATE (empty:ActivityRoot {uid: 'Activity_empty'})
            -[:HAS_VERSION {version: '1.0', status: 'Final', start_date: datetime('2026-09-01T00:00:00Z')}]->
            (empty_value:ActivityValue {name: 'Unbound source activity'})
        CREATE (empty)-[:LATEST]->(empty_value)
        CREATE (bound:ActivityRoot {uid: 'Activity_bound'})
            -[:HAS_VERSION {version: '1.0', status: 'Final', start_date: datetime('2026-09-01T00:00:00Z')}]->
            (bound_value:ActivityValue {name: 'Bound source activity'})
        CREATE (bound)-[:LATEST]->(bound_value)
        CREATE (bound_value)-[:HAS_GROUPING]->(grouping)
        CREATE (historical:ActivityRoot {uid: 'Activity_historical'})
            -[:HAS_VERSION {version: '1.0', status: 'Final',
                start_date: datetime('2026-09-01T00:00:00Z'), end_date: datetime('2026-09-02T00:00:00Z')}]->
            (:ActivityValue {name: 'Original unbound definition'})
        CREATE (historical)-[:HAS_VERSION {version: '2.0', status: 'Final', start_date: datetime('2026-09-02T00:00:00Z')}]->
            (current_value:ActivityValue {name: 'Later bound definition'})
        CREATE (historical)-[:LATEST]->(current_value)
        CREATE (current_value)-[:HAS_GROUPING]->(grouping)
        CREATE (broken:ActivityRoot {uid: 'Activity_broken'})
            -[:HAS_VERSION {version: '1.0', status: 'Final', start_date: datetime('2026-09-01T00:00:00Z')}]->
            (broken_value:ActivityValue {name: 'Malformed existing grouping'})
        CREATE (broken)-[:LATEST]->(broken_value)
        CREATE (broken_value)-[:HAS_GROUPING]->(:ActivityGrouping {uid: 'Grouping_broken'})
            -[:HAS_SELECTED_GROUP]->(gv)
        CREATE (wrong_label:ActivityRoot {uid: 'Activity_wrong_label'})
            -[:HAS_VERSION {version: '1.0', status: 'Final', start_date: datetime('2026-09-01T00:00:00Z')}]->
            (wrong_value:ActivityValue {name: 'Malformed grouping target'})
        CREATE (wrong_label)-[:LATEST]->(wrong_value)
        CREATE (wrong_value)-[:HAS_GROUPING]->(:MalformedGroupingTarget {uid: 'Wrong_target'})
        """
    )
    from clinical_mdr_api.main import app

    return TestClient(app)


def grouping_url(uid, version="1.0"):
    return f"/concepts/activities/activities/{uid}/versions/{version}/groupings"


@pytest.mark.parametrize("page_number,page_size", [(1, 10), (2, 10), (1, 0)])
def test_existing_activity_version_without_groups_returns_an_empty_page(
    groupings_client, page_number, page_size
):
    before, _ = db.cypher_query("MATCH ()-[r:HAS_GROUPING]->() RETURN count(r)")
    response = groupings_client.get(
        grouping_url("Activity_empty"),
        params={"page_number": page_number, "page_size": page_size, "total_count": True},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["items"] == [
        {
            "activity_uid": "Activity_empty",
            "activity_version": "1.0",
            "activity_groupings": [],
            "activity_instances": [],
        }
    ]
    assert result["total"] == 0
    after, _ = db.cypher_query("MATCH ()-[r:HAS_GROUPING]->() RETURN count(r)")
    assert after == before


@pytest.mark.parametrize(
    "uid,version", [("Activity_missing", "1.0"), ("Activity_empty", "9.9")]
)
def test_missing_activity_or_version_still_returns_not_found(
    groupings_client, uid, version
):
    response = groupings_client.get(grouping_url(uid, version))
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("uid", ["Activity_broken", "Activity_wrong_label"])
def test_malformed_existing_grouping_is_not_hidden_as_an_empty_definition(
    groupings_client, uid
):
    response = groupings_client.get(grouping_url(uid))
    assert response.status_code == 400, response.text
    assert f"No data found for activity {uid} version 1.0" in response.text


def test_existing_group_with_no_instances_is_preserved(groupings_client):
    response = groupings_client.get(
        grouping_url("Activity_bound"), params={"total_count": True}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 1
    grouping = result["items"][0]["activity_groupings"]
    assert grouping == [
        {
            "valid_group_uid": "Grouping_fixture",
            "group": {
                "uid": "Group_fixture",
                "name": "Source group",
                "version": "1.0",
                "status": "Final",
            },
            "subgroup": {
                "uid": "Subgroup_fixture",
                "name": "Source subgroup",
                "version": "1.0",
                "status": "Final",
            },
            "activity_instances": [],
        }
    ]


def test_empty_historical_version_does_not_inherit_current_groupings(groupings_client):
    historical = groupings_client.get(grouping_url("Activity_historical", "1.0"))
    current = groupings_client.get(grouping_url("Activity_historical", "2.0"))
    assert historical.status_code == 200, historical.text
    assert current.status_code == 200, current.text
    assert historical.json()["items"][0]["activity_groupings"] == []
    assert historical.json()["total"] == 0
    assert current.json()["items"][0]["activity_groupings"][0]["valid_group_uid"] == (
        "Grouping_fixture"
    )
