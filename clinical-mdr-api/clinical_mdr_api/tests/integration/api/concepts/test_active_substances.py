"""
Tests for /concepts/active-substances endpoints
"""

# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-arguments

# pytest fixture functions have other fixture functions as arguments,
# which pylint interprets as unused arguments
import json
import logging
from functools import reduce
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clinical_mdr_api.main import app
from clinical_mdr_api.models.concepts.active_substance import ActiveSubstance
from clinical_mdr_api.models.dictionaries.dictionary_codelist import DictionaryCodelist
from clinical_mdr_api.models.dictionaries.dictionary_term import DictionaryTerm
from clinical_mdr_api.tests.integration.utils.api import (
    inject_and_clear_db,
    inject_base_data,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from clinical_mdr_api.tests.utils.checks import assert_response_status_code

log = logging.getLogger(__name__)

HEADERS = {"content-type": "application/json"}

# Global variables shared between fixtures and tests
active_substances_all: list[ActiveSubstance]
dictionary_term_unii: DictionaryTerm
unii_codelist: DictionaryCodelist


@pytest.fixture(scope="module")
def api_client(test_data):
    """Create FastAPI test client
    using the database name set in the `test_data` fixture"""
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    """Initialize test data"""
    db_name = "active-substances.api"
    inject_and_clear_db(db_name)
    inject_base_data()

    global active_substances_all
    global dictionary_term_unii
    global unii_codelist

    TestUtils.create_library("UNII")
    unii_codelist = TestUtils.create_dictionary_codelist(
        name="UNII", library_name="UNII"
    )
    dictionary_term_unii = TestUtils.create_dictionary_term(
        codelist_uid=unii_codelist.codelist_uid,
        library_name=unii_codelist.library_name,
        dictionary_id="P7YU3ED05N",
        name="INSULIN ICODEC",
    )

    # Create some active substances
    active_substances_all = []
    active_substances_all.append(
        TestUtils.create_active_substance(
            unii_term_uid=dictionary_term_unii.term_uid,
            external_id="external_id_a",
            analyte_number="analyte A",
            short_number="short number A",
            long_number="long number A",
            inn="inn A",
        )
    )

    active_substances_all.append(
        TestUtils.create_active_substance(analyte_number="analyte_number-AAA")
    )
    active_substances_all.append(
        TestUtils.create_active_substance(analyte_number="analyte_number-BBB")
    )
    active_substances_all.append(
        TestUtils.create_active_substance(short_number="short_number-XXX")
    )
    active_substances_all.append(
        TestUtils.create_active_substance(short_number="short_number-YYY")
    )

    for index in range(5):
        active_substance_a = TestUtils.create_active_substance(
            analyte_number=f"analyte_number-AAA-{index}"
        )
        active_substances_all.append(active_substance_a)

        active_substance_b = TestUtils.create_active_substance(
            analyte_number=f"analyte_number-BBB-{index}"
        )
        active_substances_all.append(active_substance_b)

        active_substance_c = TestUtils.create_active_substance(
            short_number=f"short_number-XXX-{index}"
        )
        active_substances_all.append(active_substance_c)

        active_substance_d = TestUtils.create_active_substance(
            short_number=f"short_number-YYY-{index}"
        )
        active_substances_all.append(active_substance_d)

    yield


ACTIVE_SUBSTANCE_FIELDS_ALL = [
    "uid",
    "library_name",
    "start_date",
    "end_date",
    "status",
    "version",
    "change_description",
    "author_username",
    "possible_actions",
    "analyte_number",
    "short_number",
    "long_number",
    "inn",
    "external_id",
    "unii",
]

ACTIVE_SUBSTANCE_FIELDS_NOT_NULL = ["uid", "analyte_number", "start_date"]


def test_get_active_substance(api_client):
    response = api_client.get(
        f"/concepts/active-substances/{active_substances_all[0].uid}"
    )
    res = response.json()

    assert_response_status_code(response, 200)

    # Check fields included in the response
    assert set(res.keys()) == set(ACTIVE_SUBSTANCE_FIELDS_ALL)
    for key in ACTIVE_SUBSTANCE_FIELDS_NOT_NULL:
        assert res[key] is not None

    assert res["uid"] == active_substances_all[0].uid
    assert res["analyte_number"] == "analyte A"
    assert res["short_number"] == "short number A"
    assert res["long_number"] == "long number A"
    assert res["inn"] == "inn A"
    assert res["external_id"] == "external_id_a"
    assert res["unii"]["substance_term_uid"] == dictionary_term_unii.term_uid
    assert res["unii"]["substance_name"] == dictionary_term_unii.name
    assert res["unii"]["substance_unii"] == dictionary_term_unii.dictionary_id

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)


def test_get_active_substances_versions(api_client):
    response = api_client.get("/concepts/active-substances/versions?total_count=true")
    res = response.json()

    assert_response_status_code(response, 200)
    assert len(res["items"]) == 10
    assert res["total"] == len(active_substances_all)

    for item in res["items"]:
        assert set(list(item.keys())) == set(ACTIVE_SUBSTANCE_FIELDS_ALL)
        for key in ACTIVE_SUBSTANCE_FIELDS_NOT_NULL:
            assert item[key] is not None
        TestUtils.assert_timestamp_is_in_utc_zone(item["start_date"])
        TestUtils.assert_timestamp_is_newer_than(item["start_date"], 60)


@pytest.mark.parametrize(
    "export_format",
    [
        pytest.param("text/csv"),
        pytest.param("text/xml"),
        pytest.param(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    ],
)
def test_get_active_substances_versions_csv_xml_excel(api_client, export_format):
    url = "/concepts/active-substances/versions"
    TestUtils.verify_exported_data_format(api_client, export_format, url)


def test_update_active_substance_property(api_client):
    payload: dict[Any, Any]
    # First try a dummy patch with no new property values in the payload
    payload = {
        "unii_term_uid": dictionary_term_unii.term_uid,
        "change_description": "dummy updated",
    }
    response = api_client.patch(
        f"/concepts/active-substances/{active_substances_all[0].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)
    assert res["uid"] == active_substances_all[0].uid
    assert res["analyte_number"] == active_substances_all[0].analyte_number
    assert res["short_number"] == active_substances_all[0].short_number
    assert res["long_number"] == active_substances_all[0].long_number
    assert res["inn"] == active_substances_all[0].inn
    assert res["external_id"] == active_substances_all[0].external_id
    assert res["unii"]["substance_term_uid"] == dictionary_term_unii.term_uid
    assert res["unii"]["substance_name"] == dictionary_term_unii.name
    assert res["unii"]["substance_unii"] == dictionary_term_unii.dictionary_id

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    # Another dummy patch with no new property values in the payload
    payload = {
        "change_description": "dummy update",
    }
    response = api_client.patch(
        f"/concepts/active-substances/{active_substances_all[0].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)
    assert res["uid"] == active_substances_all[0].uid
    assert res["analyte_number"] == active_substances_all[0].analyte_number
    assert res["short_number"] == active_substances_all[0].short_number
    assert res["long_number"] == active_substances_all[0].long_number
    assert res["inn"] == active_substances_all[0].inn
    assert res["external_id"] == active_substances_all[0].external_id
    assert res["unii"]["substance_term_uid"] == dictionary_term_unii.term_uid
    assert res["unii"]["substance_name"] == dictionary_term_unii.name
    assert res["unii"]["substance_unii"] == dictionary_term_unii.dictionary_id

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    # Update analyte number
    analyte_number_new = f"{active_substances_all[0].analyte_number}-updated"
    payload = {
        "analyte_number": analyte_number_new,
        "unii_term_uid": dictionary_term_unii.term_uid,
        "change_description": "analyte number updated",
    }
    response = api_client.patch(
        f"/concepts/active-substances/{active_substances_all[0].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert res["uid"] == active_substances_all[0].uid
    assert res["analyte_number"] == analyte_number_new
    assert res["short_number"] == active_substances_all[0].short_number
    assert res["long_number"] == active_substances_all[0].long_number
    assert res["inn"] == active_substances_all[0].inn
    assert res["external_id"] == active_substances_all[0].external_id
    assert res["unii"]["substance_term_uid"] == dictionary_term_unii.term_uid
    assert res["unii"]["substance_name"] == dictionary_term_unii.name
    assert res["unii"]["substance_unii"] == dictionary_term_unii.dictionary_id

    assert res["version"] == "0.2"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)

    # Nullify inn
    payload = {
        "inn": None,
        "change_description": "inn set to null",
    }
    response = api_client.patch(
        f"/concepts/active-substances/{active_substances_all[0].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert res["uid"] == active_substances_all[0].uid
    assert res["analyte_number"] == analyte_number_new
    assert res["short_number"] == active_substances_all[0].short_number
    assert res["long_number"] == active_substances_all[0].long_number
    assert res["inn"] is None
    assert res["external_id"] == active_substances_all[0].external_id
    assert res["unii"]["substance_term_uid"] == dictionary_term_unii.term_uid
    assert res["unii"]["substance_name"] == dictionary_term_unii.name
    assert res["unii"]["substance_unii"] == dictionary_term_unii.dictionary_id

    assert res["version"] == "0.3"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)


def test_update_active_substance_unii(api_client):
    unii_term_new = TestUtils.create_dictionary_term(
        codelist_uid=unii_codelist.codelist_uid,
        library_name=unii_codelist.library_name,
        dictionary_id="UNII123",
        name="Substance 123",
    )

    payload: dict[Any, Any]
    # Change unii value
    payload = {
        "unii_term_uid": unii_term_new.term_uid,
        "change_description": "unii updated",
    }
    response = api_client.patch(
        f"/concepts/active-substances/{active_substances_all[1].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert res["uid"] == active_substances_all[1].uid
    assert res["short_number"] == active_substances_all[1].short_number
    assert res["long_number"] == active_substances_all[1].long_number
    assert res["inn"] == active_substances_all[1].inn
    assert res["external_id"] == active_substances_all[1].external_id
    assert res["unii"]["substance_term_uid"] == unii_term_new.term_uid
    assert res["unii"]["substance_name"] == unii_term_new.name
    assert res["unii"]["substance_unii"] == unii_term_new.dictionary_id

    assert res["version"] == "0.2"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)

    # Nullify unii value
    payload = {
        "unii_term_uid": None,
        "change_description": "unii updated",
    }
    response = api_client.patch(
        f"/concepts/active-substances/{active_substances_all[1].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert res["uid"] == active_substances_all[1].uid
    assert res["unii"] is None

    assert res["version"] == "0.3"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]


def test_get_active_substance_versioning(api_client):
    uid = active_substances_all[3].uid

    response = api_client.get(f"/concepts/active-substances/{uid}/versions")
    res = response.json()

    assert_response_status_code(response, 200)

    # Check fields included in the response
    for item in res:
        assert set(list(item.keys())) == set(ACTIVE_SUBSTANCE_FIELDS_ALL)
        for key in ACTIVE_SUBSTANCE_FIELDS_NOT_NULL:
            assert item[key] is not None

        assert item["uid"] == uid

    # Approve draft version
    response = api_client.post(f"/concepts/active-substances/{uid}/approvals")
    res = response.json()
    assert_response_status_code(response, 201)
    assert res["version"] == "1.0"
    assert res["status"] == "Final"

    # Create new version
    response = api_client.post(f"/concepts/active-substances/{uid}/versions")
    res = response.json()
    assert_response_status_code(response, 201)
    assert res["version"] == "1.1"
    assert res["status"] == "Draft"
    assert res["possible_actions"] == ["approve", "edit"]

    # Approve draft version
    response = api_client.post(f"/concepts/active-substances/{uid}/approvals")
    res = response.json()
    assert_response_status_code(response, 201)
    assert res["version"] == "2.0"
    assert res["status"] == "Final"
    assert res["possible_actions"] == ["inactivate", "new_version"]

    # Inactivate final version
    response = api_client.delete(f"/concepts/active-substances/{uid}/activations")
    res = response.json()
    assert_response_status_code(response, 200)
    assert res["version"] == "2.0"
    assert res["status"] == "Retired"
    assert res["possible_actions"] == ["reactivate"]

    # Reactivate retired version
    response = api_client.post(f"/concepts/active-substances/{uid}/activations")
    res = response.json()
    assert_response_status_code(response, 200)
    assert res["version"] == "2.0"
    assert res["status"] == "Final"
    assert res["possible_actions"] == ["inactivate", "new_version"]

    # Get all versions, assert they are sorted by version number (newest on top)
    response = api_client.get(f"/concepts/active-substances/{uid}/versions")
    res = response.json()
    assert_response_status_code(response, 200)

    assert len(res) == 6

    assert res[0]["version"] == "2.0"
    assert res[0]["status"] == "Final"
    assert res[0]["possible_actions"] == ["inactivate", "new_version"]

    assert res[1]["version"] == "2.0"
    assert res[1]["status"] == "Retired"
    assert res[1]["possible_actions"] == ["reactivate"]

    assert res[2]["version"] == "2.0"
    assert res[2]["status"] == "Final"
    assert res[2]["possible_actions"] == ["inactivate", "new_version"]

    assert res[3]["version"] == "1.1"
    assert res[3]["status"] == "Draft"
    assert res[3]["possible_actions"] == ["approve", "edit"]

    assert res[4]["version"] == "1.0"
    assert res[4]["status"] == "Final"
    assert res[4]["possible_actions"] == ["inactivate", "new_version"]

    assert res[5]["version"] == "0.1"
    assert res[5]["status"] == "Draft"
    assert res[5]["possible_actions"] == ["approve", "delete", "edit"]


def test_get_active_substances_pagination(api_client):
    results_paginated: dict[Any, Any] = {}
    sort_by = '{"analyte_number": true}'
    for page_number in range(1, 4):
        url = f"/concepts/active-substances?page_number={page_number}&page_size=10&sort_by={sort_by}"
        response = api_client.get(url)
        res = response.json()
        res_analyte_numbers = [item["analyte_number"] for item in res["items"]]
        results_paginated[page_number] = res_analyte_numbers
        log.info("Page %s: %s", page_number, res_analyte_numbers)

    log.info("All pages: %s", results_paginated)

    results_paginated_merged = list(
        set(
            list(
                reduce(lambda a, b: list(a) + list(b), list(results_paginated.values()))
            )
        )
    )
    log.info("All unique rows returned by pagination: %s", results_paginated_merged)

    res_all = api_client.get(
        f"/concepts/active-substances?page_number=1&page_size=100&sort_by={sort_by}"
    ).json()
    results_all_in_one_page = list(map(lambda x: x["analyte_number"], res_all["items"]))
    log.info("All rows in one page: %s", results_all_in_one_page)
    assert len(results_all_in_one_page) == len(results_paginated_merged)
    assert len(active_substances_all) == len(results_paginated_merged)


@pytest.mark.parametrize(
    "page_size, page_number, total_count, sort_by, expected_result_len",
    [
        pytest.param(None, None, None, None, 10),
        pytest.param(3, 1, True, None, 3),
        pytest.param(3, 2, True, None, 3),
        pytest.param(10, 2, True, None, 10),
        pytest.param(10, 3, True, None, 5),  # Total number of active substances is 25
        pytest.param(10, 1, True, '{"analyte_number": false}', 10),
        pytest.param(10, 2, True, '{"analyte_number": true}', 10),
    ],
)
def test_get_active_substances(
    api_client, page_size, page_number, total_count, sort_by, expected_result_len
):
    url = "/concepts/active-substances"
    query_params = []
    if page_size:
        query_params.append(f"page_size={page_size}")
    if page_number:
        query_params.append(f"page_number={page_number}")
    if total_count:
        query_params.append(f"total_count={total_count}")
    if sort_by:
        query_params.append(f"sort_by={sort_by}")

    if query_params:
        url = f"{url}?{'&'.join(query_params)}"

    log.info("GET %s", url)
    response = api_client.get(url)
    res = response.json()

    assert_response_status_code(response, 200)

    # Check fields included in the response
    assert list(res.keys()) == ["items", "total", "page", "size"]
    assert len(res["items"]) == expected_result_len
    assert res["total"] == (len(active_substances_all) if total_count else 0)
    assert res["page"] == (page_number if page_number else 1)
    assert res["size"] == (page_size if page_size else 10)

    for item in res["items"]:
        assert set(list(item.keys())) == set(ACTIVE_SUBSTANCE_FIELDS_ALL)
        for key in ACTIVE_SUBSTANCE_FIELDS_NOT_NULL:
            assert item[key] is not None
        TestUtils.assert_timestamp_is_in_utc_zone(item["start_date"])
        TestUtils.assert_timestamp_is_newer_than(item["start_date"], 60)

    if sort_by:
        # sort_by is JSON string in the form: {"sort_field_name": is_ascending_order}
        sort_by_dict = json.loads(sort_by)
        sort_field: str = list(sort_by_dict.keys())[0]
        sort_order_ascending: bool = list(sort_by_dict.values())[0]

        # extract list of values of 'sort_field_name' field from the returned result
        result_vals = list(map(lambda x: x[sort_field], res["items"]))
        result_vals_sorted_locally = result_vals.copy()
        result_vals_sorted_locally = sorted(
            result_vals_sorted_locally,
            key=lambda x: (x is None, x),
            reverse=not sort_order_ascending,
        )
        assert result_vals == result_vals_sorted_locally


@pytest.mark.parametrize(
    "export_format",
    [
        pytest.param("text/csv"),
        pytest.param("text/xml"),
        pytest.param(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    ],
)
def test_get_active_substances_csv_xml_excel(api_client, export_format):
    url = "/concepts/active-substances"
    TestUtils.verify_exported_data_format(api_client, export_format, url)


@pytest.mark.parametrize(
    "filter_by, expected_matched_field, expected_result_prefix",
    [
        pytest.param('{"*": {"v": ["aaa"]}}', "analyte_number", "analyte_number-AAA"),
        pytest.param('{"*": {"v": ["bBb"]}}', "analyte_number", "analyte_number-BBB"),
        pytest.param(
            '{"*": {"v": ["unknown-user"], "op": "co"}}',
            "author_username",
            "unknown-user@example.com",
        ),
        pytest.param('{"*": {"v": ["Draft"]}}', "status", "Draft"),
        pytest.param('{"*": {"v": ["0.1"]}}', "version", "0.1"),
        pytest.param('{"*": {"v": ["ccc"]}}', None, None),
    ],
)
def test_filtering_wildcard(
    api_client, filter_by, expected_matched_field, expected_result_prefix
):
    url = f"/concepts/active-substances?filters={filter_by}"
    response = api_client.get(url)
    res = response.json()

    assert_response_status_code(response, 200)
    if expected_result_prefix:
        assert len(res["items"]) > 0
        # Each returned row has a field that starts with the specified filter value
        for row in res["items"]:
            assert row[expected_matched_field].startswith(expected_result_prefix)
    else:
        assert len(res["items"]) == 0


@pytest.mark.parametrize(
    "filter_by, expected_matched_field, expected_result",
    [
        pytest.param(
            '{"analyte_number": {"v": ["analyte_number-AAA"]}}',
            "analyte_number",
            "analyte_number-AAA",
        ),
        pytest.param(
            '{"analyte_number": {"v": ["analyte_number-BBB"]}}',
            "analyte_number",
            "analyte_number-BBB",
        ),
        pytest.param('{"analyte_number": {"v": ["cc"]}}', None, None),
        pytest.param(
            '{"short_number": {"v": ["short_number-XXX"]}}',
            "short_number",
            "short_number-XXX",
        ),
        pytest.param(
            '{"short_number": {"v": ["short_number-YYY"]}}',
            "short_number",
            "short_number-YYY",
        ),
        pytest.param('{"short_number": {"v": ["cc"]}}', None, None),
    ],
)
def test_filtering_exact(
    api_client, filter_by, expected_matched_field, expected_result
):
    url = f"/concepts/active-substances?filters={filter_by}"
    response = api_client.get(url)
    res = response.json()

    assert_response_status_code(response, 200)
    if expected_result:
        assert len(res["items"]) > 0
        # Each returned row has a field whose value is equal to the specified filter value
        for row in res["items"]:
            assert row[expected_matched_field] == expected_result
    else:
        assert len(res["items"]) == 0


@pytest.mark.parametrize(
    "field_name, expected_returned_values",
    [
        pytest.param("analyte_number", ["analyte_number-AAA", "analyte_number-BBB"]),
        pytest.param("short_number", ["short_number-XXX", "short_number-YYY"]),
        pytest.param("long_number", ["long number A"]),
    ],
)
def test_get_active_substances_headers(
    api_client, field_name, expected_returned_values
):
    url = f"/concepts/active-substances/headers?field_name={field_name}&page_size=100"
    response = api_client.get(url)
    res = response.json()

    assert_response_status_code(response, 200)
    assert len(res) >= len(expected_returned_values)
    for val in expected_returned_values:
        assert val in res


def test_create_and_delete_active_substance(api_client):
    # Create new active substance
    payload = {
        "library_name": "Sponsor",
        "analyte_number": "analyte_number-NEW",
        "short_number": "short_number-NEW",
        "long_number": "long_number-NEW",
        "inn": "inn-NEW",
        "external_id": "external_id-NEW",
        "unii_term_uid": dictionary_term_unii.term_uid,
    }
    response = api_client.post(
        "/concepts/active-substances", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 201)
    assert res["analyte_number"] == "analyte_number-NEW"
    assert res["short_number"] == "short_number-NEW"
    assert res["long_number"] == "long_number-NEW"
    assert res["inn"] == "inn-NEW"
    assert res["external_id"] == "external_id-NEW"
    assert res["unii"]["substance_term_uid"] == dictionary_term_unii.term_uid
    assert res["unii"]["substance_name"] == dictionary_term_unii.name
    assert res["unii"]["substance_unii"] == dictionary_term_unii.dictionary_id

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)

    # Delete active substance
    response = api_client.delete(f"/concepts/active-substances/{res['uid']}")
    assert_response_status_code(response, 204)

    # Check that the active substance is deleted
    response = api_client.get(f"/concepts/active-substances/{res['uid']}")
    assert_response_status_code(response, 404)


def test_negative_delete_approved_active_substance(api_client):
    item = TestUtils.create_active_substance(approve=True)

    # Try to delete approved active substance
    response = api_client.delete(f"/concepts/active-substances/{item.uid}")
    assert_response_status_code(response, 400)
    assert response.json()["message"] == "Object has been accepted"

    # Check that the active substance is not deleted
    response = api_client.get(f"/concepts/active-substances/{item.uid}")
    assert_response_status_code(response, 200)


def test_negative_create_active_substance_wrong_links(api_client):
    # Try to create new active substance with non-existing UNII uid
    payload = {
        "analyte_number": "analyte_number_new",
        "unii_term_uid": "NON_EXISTING_UID",
        "library_name": "Sponsor",
    }
    response = api_client.post(
        "/concepts/active-substances", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 400)
    assert (
        res["message"]
        == "ActiveSubstanceVO tried to connect to non existing UNII Term with UID 'NON_EXISTING_UID'."
    )
