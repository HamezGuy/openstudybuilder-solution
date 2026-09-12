"""
Tests for /concepts/pharmaceutical-products endpoints
"""

# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-arguments

import copy

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
from clinical_mdr_api.models.concepts.concept import LagTime, NumericValueWithUnit
from clinical_mdr_api.models.concepts.pharmaceutical_product import (
    PharmaceuticalProduct,
)
from clinical_mdr_api.models.controlled_terminologies.ct_term import CTTerm
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
pharmaceutical_products_all: list[PharmaceuticalProduct]
ct_term_roa: CTTerm
roa_codelist_uid: str
ct_term_dose_form: CTTerm
active_substances_all: list[ActiveSubstance]
dictionary_term_unii: DictionaryTerm
unii_codelist: DictionaryCodelist
strength: NumericValueWithUnit
lag_time: LagTime
half_life: NumericValueWithUnit
formulation_1: dict[Any, Any]


@pytest.fixture(scope="module")
def api_client(test_data):
    """Create FastAPI test client
    using the database name set in the `test_data` fixture"""
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    """Initialize test data"""
    db_name = "pharmaceutical-products.api"
    inject_and_clear_db(db_name)
    inject_base_data()

    global pharmaceutical_products_all
    global ct_term_dose_form
    global ct_term_roa
    global roa_codelist_uid
    global active_substances_all
    global dictionary_term_unii
    global unii_codelist
    global strength
    global lag_time
    global half_life
    global formulation_1

    catalogue_name = "SDTM CT"
    library_name = "Sponsor"
    # Create CT Terms
    ct_term_dose_form = TestUtils.create_ct_term(
        codelist_uid="C66726",
        submission_value="dosage_form_1",
        sponsor_preferred_name="dosage_form_1",
        order=1,
        catalogue_name=catalogue_name,
        library_name=library_name,
        approve=True,
    )
    roa_codelist_uid = "C66729"
    ct_term_roa = TestUtils.create_ct_term(
        codelist_uid=roa_codelist_uid,
        submission_value="route_of_administration_1",
        sponsor_preferred_name="route_of_administration_1",
        order=1,
        catalogue_name=catalogue_name,
        library_name=library_name,
        approve=True,
    )

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

    strength = TestUtils.create_numeric_value_with_unit(value=5, unit="mg/mL")
    half_life = TestUtils.create_numeric_value_with_unit(value=8, unit="hours")
    lag_time = TestUtils.create_lag_time(value=7, unit="days")

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

    # Create some pharmaceutical products
    ingredient_1 = {
        "external_id": "ingredient-prodex-id-a",
        "formulation_name": "formulation-name-a",
        "active_substance_uid": active_substances_all[0].uid,
        "strength_uid": strength.uid,
        "half_life_uid": half_life.uid,
        "lag_time_uids": [lag_time.uid],
    }
    ingredient_2 = {
        "external_id": "ingredient-prodex-id-b",
        "formulation_name": "formulation-name-b",
        "active_substance_uid": active_substances_all[1].uid,
        "strength_uid": strength.uid,
        "half_life_uid": half_life.uid,
        "lag_time_uids": [lag_time.uid],
    }

    formulation_1 = {
        "external_id": "formulation-prodex-id-a",
        "ingredients": [ingredient_1, ingredient_2],
    }

    pharmaceutical_products_all = []
    pharmaceutical_products_all.append(
        TestUtils.create_pharmaceutical_product(
            external_id="external_id_a",
            dosage_form_uids=[ct_term_dose_form.term_uid],
            route_of_administration_uids=[ct_term_roa.term_uid],
            formulations=[formulation_1],
        )
    )
    pharmaceutical_products_all.append(
        TestUtils.create_pharmaceutical_product(
            external_id="external_id_b",
            dosage_form_uids=[ct_term_dose_form.term_uid],
            route_of_administration_uids=[ct_term_roa.term_uid],
            formulations=[formulation_1],
        )
    )

    pharmaceutical_products_all.append(
        TestUtils.create_pharmaceutical_product(external_id="external_id_AAA")
    )
    pharmaceutical_products_all.append(
        TestUtils.create_pharmaceutical_product(external_id="external_id_BBB")
    )

    for index in range(5):
        pharmaceutical_product_a = TestUtils.create_pharmaceutical_product(
            external_id=f"external_id_AAA_{index}"
        )
        pharmaceutical_products_all.append(pharmaceutical_product_a)

        pharmaceutical_product_b = TestUtils.create_pharmaceutical_product(
            external_id=f"external_id_BBB_{index}"
        )
        pharmaceutical_products_all.append(pharmaceutical_product_b)

        pharmaceutical_product_c = TestUtils.create_pharmaceutical_product(
            external_id=f"external_id_XXX_{index}"
        )
        pharmaceutical_products_all.append(pharmaceutical_product_c)

        pharmaceutical_product_d = TestUtils.create_pharmaceutical_product(
            external_id=f"external_id_YYY_{index}"
        )
        pharmaceutical_products_all.append(pharmaceutical_product_d)

    yield


PHARMACEUTICAL_PRODUCT_FIELDS_ALL = [
    "uid",
    "derived_name",
    "library_name",
    "start_date",
    "end_date",
    "status",
    "version",
    "change_description",
    "author_username",
    "possible_actions",
    "external_id",
    "dosage_forms",
    "routes_of_administration",
    "formulations",
]

PHARMACEUTICAL_PRODUCT_FIELDS_NOT_NULL = [
    "uid",
    "derived_name",
    "start_date",
    "library_name",
    "dosage_forms",
    "routes_of_administration",
    "formulations",
]


def test_get_pharmaceutical_product(api_client):
    response = api_client.get(
        f"/concepts/pharmaceutical-products/{pharmaceutical_products_all[0].uid}"
    )
    res = response.json()

    assert_response_status_code(response, 200)

    # Check fields included in the response
    assert set(res.keys()) == set(PHARMACEUTICAL_PRODUCT_FIELDS_ALL)
    for key in PHARMACEUTICAL_PRODUCT_FIELDS_NOT_NULL:
        assert res[key] is not None

    assert res["uid"] == pharmaceutical_products_all[0].uid
    assert res["external_id"] == "external_id_a"
    # derived_name should contain ingredient names with formulation names and strengths
    assert "inn A formulation-name-a (5 mg/mL)" in res["derived_name"]
    assert "formulation-name-b (5 mg/mL)" in res["derived_name"]
    assert res["routes_of_administration"][0]["term_uid"] == ct_term_roa.term_uid
    assert (
        res["routes_of_administration"][0]["term_name"]
        == ct_term_roa.sponsor_preferred_name
    )
    assert res["dosage_forms"][0]["term_uid"] == ct_term_dose_form.term_uid
    assert (
        res["dosage_forms"][0]["term_name"] == ct_term_dose_form.sponsor_preferred_name
    )

    assert res["formulations"][0]["external_id"] == "formulation-prodex-id-a"

    ingr_1 = res["formulations"][0]["ingredients"][0]
    ingr_2 = res["formulations"][0]["ingredients"][1]

    assert ingr_1["external_id"] == "ingredient-prodex-id-a"
    assert ingr_1["active_substance"]["uid"] == active_substances_all[0].uid

    assert ingr_2["external_id"] == "ingredient-prodex-id-b"
    assert ingr_2["active_substance"]["uid"] == active_substances_all[1].uid

    for ingredient in res["formulations"][0]["ingredients"]:
        assert ingredient["strength"]["uid"] == strength.uid
        assert ingredient["half_life"]["uid"] == half_life.uid
        assert ingredient["lag_times"][0]["value"] == lag_time.value
        assert (
            ingredient["lag_times"][0]["unit_definition_uid"]
            == lag_time.unit_definition_uid
        )
        assert ingredient["lag_times"][0]["unit_label"] == lag_time.unit_label
        assert ingredient["lag_times"][0]["sdtm_domain_label"] == "Adverse Event Domain"

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)


def test_get_pharmaceutical_product_derived_name_without_formulations(api_client):
    """Products without formulations should have an empty derived_name."""
    response = api_client.get(
        f"/concepts/pharmaceutical-products/{pharmaceutical_products_all[2].uid}"
    )
    res = response.json()
    assert_response_status_code(response, 200)
    assert res["derived_name"] == ""


def test_get_pharmaceutical_product_derived_name_fallback(api_client):
    """derived_name should use the active substance inn when available."""
    as_with_known_inn = TestUtils.create_active_substance(
        inn="test-known-inn",
    )
    formulation = {
        "external_id": "formulation-derived-test",
        "ingredients": [
            {
                "active_substance_uid": as_with_known_inn.uid,
                "strength_uid": strength.uid,
            },
        ],
    }
    pp = TestUtils.create_pharmaceutical_product(formulations=[formulation])
    response = api_client.get(f"/concepts/pharmaceutical-products/{pp.uid}")
    res = response.json()
    assert_response_status_code(response, 200)
    assert "test-known-inn" in res["derived_name"]
    assert "(5 mg/mL)" in res["derived_name"]

    # Cleanup
    api_client.delete(f"/concepts/pharmaceutical-products/{pp.uid}")


def test_get_pharmaceutical_products_versions(api_client):
    response = api_client.get(
        "/concepts/pharmaceutical-products/versions?total_count=true"
    )
    res = response.json()

    assert_response_status_code(response, 200)
    assert len(res["items"]) == 10
    assert res["total"] >= len(pharmaceutical_products_all)

    for item in res["items"]:
        assert set(list(item.keys())) == set(PHARMACEUTICAL_PRODUCT_FIELDS_ALL)
        for key in PHARMACEUTICAL_PRODUCT_FIELDS_NOT_NULL:
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
def test_get_pharmaceutical_products_versions_csv_xml_excel(api_client, export_format):
    url = "/concepts/pharmaceutical-products/versions"
    TestUtils.verify_exported_data_format(api_client, export_format, url)


def test_update_pharmaceutical_product_property(api_client):
    # First try a dummy patch with no new property values in the payload
    payload: dict[Any, Any]
    payload = {
        "change_description": "dummy update",
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation_1],
    }
    response = api_client.patch(
        f"/concepts/pharmaceutical-products/{pharmaceutical_products_all[0].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)
    assert res["uid"] == pharmaceutical_products_all[0].uid
    assert res["external_id"] == pharmaceutical_products_all[0].external_id
    assert res["routes_of_administration"][0]["term_uid"] == ct_term_roa.term_uid
    assert (
        res["routes_of_administration"][0]["term_name"]
        == ct_term_roa.sponsor_preferred_name
    )
    assert res["dosage_forms"][0]["term_uid"] == ct_term_dose_form.term_uid
    assert (
        res["dosage_forms"][0]["term_name"] == ct_term_dose_form.sponsor_preferred_name
    )

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    # Another dummy patch with no new property values in the payload
    payload = {
        "change_description": "dummy update",
    }
    response = api_client.patch(
        f"/concepts/pharmaceutical-products/{pharmaceutical_products_all[0].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)
    assert res["uid"] == pharmaceutical_products_all[0].uid
    assert res["external_id"] == pharmaceutical_products_all[0].external_id
    assert res["routes_of_administration"][0]["term_uid"] == ct_term_roa.term_uid
    assert (
        res["routes_of_administration"][0]["term_name"]
        == ct_term_roa.sponsor_preferred_name
    )
    assert res["dosage_forms"][0]["term_uid"] == ct_term_dose_form.term_uid
    assert (
        res["dosage_forms"][0]["term_name"] == ct_term_dose_form.sponsor_preferred_name
    )

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    # Update external_id
    external_id_new = f"{pharmaceutical_products_all[0].external_id}-updated"
    payload = {
        "external_id": external_id_new,
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation_1],
        "change_description": "external_id updated",
    }
    response = api_client.patch(
        f"/concepts/pharmaceutical-products/{pharmaceutical_products_all[0].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert res["uid"] == pharmaceutical_products_all[0].uid
    assert res["external_id"] == external_id_new
    assert res["routes_of_administration"][0]["term_uid"] == ct_term_roa.term_uid
    assert (
        res["routes_of_administration"][0]["term_name"]
        == ct_term_roa.sponsor_preferred_name
    )
    assert res["dosage_forms"][0]["term_uid"] == ct_term_dose_form.term_uid
    assert (
        res["dosage_forms"][0]["term_name"] == ct_term_dose_form.sponsor_preferred_name
    )

    assert res["version"] == "0.2"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)

    # Nullify external_id
    payload = {
        "external_id": None,
        "change_description": "external_id set to null",
    }
    response = api_client.patch(
        f"/concepts/pharmaceutical-products/{pharmaceutical_products_all[0].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert res["uid"] == pharmaceutical_products_all[0].uid
    assert res["external_id"] is None
    assert res["routes_of_administration"][0]["term_uid"] == ct_term_roa.term_uid
    assert (
        res["routes_of_administration"][0]["term_name"]
        == ct_term_roa.sponsor_preferred_name
    )
    assert res["dosage_forms"][0]["term_uid"] == ct_term_dose_form.term_uid
    assert (
        res["dosage_forms"][0]["term_name"] == ct_term_dose_form.sponsor_preferred_name
    )

    assert res["version"] == "0.3"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)


def test_update_pharmaceutical_product_roa(api_client):
    ct_term_roa_new = TestUtils.create_ct_term(
        codelist_uid=roa_codelist_uid,
        submission_value="route_of_administration_2",
        sponsor_preferred_name="route_of_administration_2",
        order=2,
        catalogue_name="SDTM CT",
        library_name="Sponsor",
        approve=True,
    )

    # Change roa
    payload = {
        "route_of_administration_uids": [ct_term_roa_new.term_uid],
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "formulations": [formulation_1],
        "change_description": "roa updated",
    }
    response = api_client.patch(
        f"/concepts/pharmaceutical-products/{pharmaceutical_products_all[1].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert res["uid"] == pharmaceutical_products_all[1].uid
    assert res["external_id"] == "external_id_b"
    assert res["routes_of_administration"][0]["term_uid"] == ct_term_roa_new.term_uid
    assert (
        res["routes_of_administration"][0]["term_name"]
        == ct_term_roa_new.sponsor_preferred_name
    )
    assert res["dosage_forms"][0]["term_uid"] == ct_term_dose_form.term_uid
    assert (
        res["dosage_forms"][0]["term_name"] == ct_term_dose_form.sponsor_preferred_name
    )

    assert res["version"] == "0.2"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)

    # Nullify roa and dosage forms values
    payload = {
        "route_of_administration_uids": [],
        "dosage_form_uids": [],
        "formulations": [formulation_1],
        "change_description": "roa and dosage forms updated",
    }
    response = api_client.patch(
        f"/concepts/pharmaceutical-products/{pharmaceutical_products_all[1].uid}",
        data=json.dumps(payload),
        headers=HEADERS,
    )
    res = response.json()

    assert_response_status_code(response, 200)

    assert res["uid"] == pharmaceutical_products_all[1].uid
    assert res["routes_of_administration"] == []
    assert res["dosage_forms"] == []

    assert res["version"] == "0.3"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]


def test_get_pharmaceutical_product_versioning(api_client):
    uid = pharmaceutical_products_all[3].uid

    response = api_client.get(f"/concepts/pharmaceutical-products/{uid}/versions")
    res = response.json()

    assert_response_status_code(response, 200)

    # Check fields included in the response
    for item in res:
        assert set(list(item.keys())) == set(PHARMACEUTICAL_PRODUCT_FIELDS_ALL)
        for key in PHARMACEUTICAL_PRODUCT_FIELDS_NOT_NULL:
            assert item[key] is not None

        assert item["uid"] == uid

    # Approve draft version
    response = api_client.post(f"/concepts/pharmaceutical-products/{uid}/approvals")
    res = response.json()
    assert_response_status_code(response, 201)
    assert res["version"] == "1.0"
    assert res["status"] == "Final"

    # Create new version
    response = api_client.post(f"/concepts/pharmaceutical-products/{uid}/versions")
    res = response.json()
    assert_response_status_code(response, 201)
    assert res["version"] == "1.1"
    assert res["status"] == "Draft"
    assert res["possible_actions"] == ["approve", "edit"]

    # Approve draft version
    response = api_client.post(f"/concepts/pharmaceutical-products/{uid}/approvals")
    res = response.json()
    assert_response_status_code(response, 201)
    assert res["version"] == "2.0"
    assert res["status"] == "Final"
    assert res["possible_actions"] == ["inactivate", "new_version"]

    # Inactivate final version
    response = api_client.delete(f"/concepts/pharmaceutical-products/{uid}/activations")
    res = response.json()
    assert_response_status_code(response, 200)
    assert res["version"] == "2.0"
    assert res["status"] == "Retired"
    assert res["possible_actions"] == ["reactivate"]

    # Reactivate retired version
    response = api_client.post(f"/concepts/pharmaceutical-products/{uid}/activations")
    res = response.json()
    assert_response_status_code(response, 200)
    assert res["version"] == "2.0"
    assert res["status"] == "Final"
    assert res["possible_actions"] == ["inactivate", "new_version"]

    # Get all versions, assert they are sorted by version number (newest on top)
    response = api_client.get(f"/concepts/pharmaceutical-products/{uid}/versions")
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


def test_get_pharmaceutical_products_pagination(api_client):
    results_paginated: dict[Any, Any] = {}
    sort_by = '{"external_id": true}'
    for page_number in range(1, 4):
        url = f"/concepts/pharmaceutical-products?page_number={page_number}&page_size=10&sort_by={sort_by}"
        response = api_client.get(url)
        res = response.json()
        res_external_ids = list(map(lambda x: x["external_id"], res["items"]))
        results_paginated[page_number] = res_external_ids
        log.info("Page %s: %s", page_number, res_external_ids)

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
        f"/concepts/pharmaceutical-products?page_number=1&page_size=100&sort_by={sort_by}"
    ).json()
    results_all_in_one_page = list(map(lambda x: x["external_id"], res_all["items"]))
    log.info("All rows in one page: %s", results_all_in_one_page)
    assert len(results_all_in_one_page) == len(results_paginated_merged)
    assert len(pharmaceutical_products_all) == len(results_paginated_merged)


@pytest.mark.parametrize(
    "page_size, page_number, total_count, sort_by, expected_result_len",
    [
        pytest.param(None, None, None, None, 10),
        pytest.param(3, 1, True, None, 3),
        pytest.param(3, 2, True, None, 3),
        pytest.param(10, 2, True, None, 10),
        pytest.param(
            10, 3, True, None, 4
        ),  # Total number of pharmaceutical products is 24, so the last page should have 4 items
        pytest.param(10, 1, True, '{"external_id": false}', 10),
        pytest.param(10, 2, True, '{"external_id": true}', 10),
    ],
)
def test_get_pharmaceutical_products(
    api_client, page_size, page_number, total_count, sort_by, expected_result_len
):
    url = "/concepts/pharmaceutical-products"
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
    assert res["total"] == (len(pharmaceutical_products_all) if total_count else 0)
    assert res["page"] == (page_number if page_number else 1)
    assert res["size"] == (page_size if page_size else 10)

    for item in res["items"]:
        assert set(list(item.keys())) == set(PHARMACEUTICAL_PRODUCT_FIELDS_ALL)
        for key in PHARMACEUTICAL_PRODUCT_FIELDS_NOT_NULL:
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
    "sort_by",
    [
        pytest.param('{"dosage_form": true}'),
        pytest.param('{"dosage_form": false}'),
        pytest.param('{"dosage_forms": true}'),
        pytest.param('{"route_of_administration": true}'),
        pytest.param('{"route_of_administration": false}'),
        pytest.param('{"routes_of_administration": true}'),
        pytest.param('{"derived_name": true}'),
        pytest.param('{"derived_name": false}'),
    ],
)
def test_get_pharmaceutical_products_sort_by_related_fields(api_client, sort_by):
    """Sorting by dosage_form, route_of_administration, and derived_name should not error."""
    url = f"/concepts/pharmaceutical-products?page_number=1&page_size=10&total_count=true&sort_by={sort_by}"
    response = api_client.get(url)
    res = response.json()

    assert_response_status_code(response, 200)
    assert len(res["items"]) == 10
    assert res["total"] == len(pharmaceutical_products_all)


@pytest.mark.parametrize(
    "sort_by, sort_field, response_key",
    [
        pytest.param('{"derived_name": true}', "derived_name", "derived_name"),
        pytest.param('{"derived_name": false}', "derived_name", "derived_name"),
        pytest.param('{"dosage_form": true}', "dosage_form", "dosage_forms"),
        pytest.param('{"dosage_form": false}', "dosage_form", "dosage_forms"),
        pytest.param(
            '{"route_of_administration": true}',
            "route_of_administration",
            "routes_of_administration",
        ),
        pytest.param(
            '{"route_of_administration": false}',
            "route_of_administration",
            "routes_of_administration",
        ),
    ],
)
def test_get_pharmaceutical_products_sort_by_related_field_order(
    api_client, sort_by, sort_field, response_key
):
    """Verify that sorting by related fields actually produces ordered results."""
    sort_by_dict = json.loads(sort_by)
    ascending = sort_by_dict[sort_field]

    response = api_client.get(
        f"/concepts/pharmaceutical-products?page_number=1&page_size=100&sort_by={sort_by}"
    )
    res = response.json()
    assert_response_status_code(response, 200)

    # Build comparable sort values from response data
    if response_key == "derived_name":
        sort_values = [item[response_key] for item in res["items"]]
    else:
        # For list-of-term fields (dosage_forms, routes_of_administration),
        # concatenate term names to mirror the _search_* Cypher alias.
        sort_values = [
            " ".join(term["term_name"] or "" for term in (item[response_key] or []))
            for item in res["items"]
        ]

    expected = sorted(
        sort_values, key=lambda x: (x is None, x.lower()), reverse=not ascending
    )
    assert sort_values == expected


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
def test_get_pharmaceutical_products_csv_xml_excel(api_client, export_format):
    url = "/concepts/pharmaceutical-products"
    TestUtils.verify_exported_data_format(api_client, export_format, url)


@pytest.mark.parametrize(
    "filter_by, expected_matched_field, expected_result_prefix",
    [
        pytest.param(
            '{"*": {"v": ["external_id_aaa"]}}', "external_id", "external_id_AAA"
        ),
        pytest.param(
            '{"*": {"v": ["external_id_bbb"]}}', "external_id", "external_id_BBB"
        ),
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
    url = f"/concepts/pharmaceutical-products?filters={filter_by}"
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
            '{"external_id": {"v": ["external_id_AAA"]}}',
            "external_id",
            "external_id_AAA",
        ),
        pytest.param(
            '{"external_id": {"v": ["external_id_BBB"]}}',
            "external_id",
            "external_id_BBB",
        ),
        pytest.param('{"external_id": {"v": ["cc"]}}', None, None),
    ],
)
def test_filtering_exact(
    api_client, filter_by, expected_matched_field, expected_result
):
    url = f"/concepts/pharmaceutical-products?filters={filter_by}"
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
        pytest.param("external_id", ["external_id_AAA", "external_id_BBB"]),
    ],
)
def test_get_pharmaceutical_products_headers(
    api_client, field_name, expected_returned_values
):
    url = f"/concepts/pharmaceutical-products/headers?field_name={field_name}&page_size=100"
    response = api_client.get(url)
    res = response.json()

    assert_response_status_code(response, 200)
    assert len(res) >= len(expected_returned_values)
    for val in expected_returned_values:
        assert val in res


def test_create_and_delete_pharmaceutical_product(api_client):
    # Create new pharmaceutical product
    formulation = {
        "external_id": "formulation-prodex-id",
        "ingredients": [
            {
                "external_id": "ingredient-prodex-id-a",
                "formulation_name": "formulation-name-a",
                "active_substance_uid": active_substances_all[0].uid,
                "strength_uid": strength.uid,
                "half_life_uid": half_life.uid,
                "lag_time_uids": [lag_time.uid],
            },
            {
                "external_id": "ingredient-prodex-id-b",
                "formulation_name": "formulation-name-b",
                "active_substance_uid": active_substances_all[1].uid,
                "strength_uid": strength.uid,
                "half_life_uid": half_life.uid,
                "lag_time_uids": [lag_time.uid],
            },
        ],
    }
    payload = {
        "library_name": "Sponsor",
        "external_id": "external_id-NEW",
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation],
    }
    response = api_client.post(
        "/concepts/pharmaceutical-products", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 201)
    assert res["external_id"] == "external_id-NEW"
    assert res["routes_of_administration"][0]["term_uid"] == ct_term_roa.term_uid
    assert (
        res["routes_of_administration"][0]["term_name"]
        == ct_term_roa.sponsor_preferred_name
    )
    assert res["dosage_forms"][0]["term_uid"] == ct_term_dose_form.term_uid
    assert (
        res["dosage_forms"][0]["term_name"] == ct_term_dose_form.sponsor_preferred_name
    )

    assert res["formulations"][0]["external_id"] == "formulation-prodex-id"

    ingr_1 = res["formulations"][0]["ingredients"][0]
    ingr_2 = res["formulations"][0]["ingredients"][1]

    assert ingr_1["external_id"] == "ingredient-prodex-id-a"
    assert ingr_1["formulation_name"] == "formulation-name-a"
    assert ingr_1["active_substance"]["uid"] == active_substances_all[0].uid

    assert ingr_2["external_id"] == "ingredient-prodex-id-b"
    assert ingr_2["formulation_name"] == "formulation-name-b"
    assert ingr_2["active_substance"]["uid"] == active_substances_all[1].uid

    for ingredient in res["formulations"][0]["ingredients"]:
        assert ingredient["strength"]["uid"] == strength.uid
        assert ingredient["half_life"]["uid"] == half_life.uid
        assert ingredient["lag_times"][0]["value"] == lag_time.value
        assert (
            ingredient["lag_times"][0]["unit_definition_uid"]
            == lag_time.unit_definition_uid
        )
        assert ingredient["lag_times"][0]["unit_label"] == lag_time.unit_label
        assert ingredient["lag_times"][0]["sdtm_domain_label"] == "Adverse Event Domain"

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]
    # Verify derived_name for freshly created product
    assert "inn A formulation-name-a (5 mg/mL)" in res["derived_name"]
    assert "formulation-name-b (5 mg/mL)" in res["derived_name"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)

    # Delete pharmaceutical product
    response = api_client.delete(f"/concepts/pharmaceutical-products/{res['uid']}")
    assert_response_status_code(response, 204)

    # Check that the pharmaceutical product is deleted
    response = api_client.get(f"/concepts/pharmaceutical-products/{res['uid']}")
    assert_response_status_code(response, 404)


def test_create_and_delete_pharmaceutical_product_with_missing_values(api_client):
    # Create new pharmaceutical product
    formulation = {
        "external_id": "formulation-prodex-id",
        "ingredients": [
            {
                "external_id": "ingredient-prodex-id-a",
                "active_substance_uid": active_substances_all[0].uid,
            },
            {
                "external_id": "ingredient-prodex-id-b",
                "active_substance_uid": active_substances_all[1].uid,
                "strength_uid": strength.uid,
                "half_life_uid": half_life.uid,
                "lag_time_uids": [lag_time.uid],
            },
        ],
    }
    payload = {
        "library_name": "Sponsor",
        "external_id": "external_id-NEW",
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation],
    }
    response = api_client.post(
        "/concepts/pharmaceutical-products", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 201)
    assert res["external_id"] == "external_id-NEW"
    assert res["routes_of_administration"][0]["term_uid"] == ct_term_roa.term_uid
    assert (
        res["routes_of_administration"][0]["term_name"]
        == ct_term_roa.sponsor_preferred_name
    )
    assert res["dosage_forms"][0]["term_uid"] == ct_term_dose_form.term_uid
    assert (
        res["dosage_forms"][0]["term_name"] == ct_term_dose_form.sponsor_preferred_name
    )

    assert res["formulations"][0]["external_id"] == "formulation-prodex-id"

    ingr_1 = res["formulations"][0]["ingredients"][0]
    ingr_2 = res["formulations"][0]["ingredients"][1]

    assert ingr_1["external_id"] == "ingredient-prodex-id-a"
    assert ingr_1["active_substance"]["uid"] == active_substances_all[0].uid

    assert ingr_2["external_id"] == "ingredient-prodex-id-b"
    assert ingr_2["active_substance"]["uid"] == active_substances_all[1].uid

    ingredients = res["formulations"][0]["ingredients"]
    assert ingredients[0]["strength"] is None
    assert ingredients[0]["half_life"] is None
    assert ingredients[0]["lag_times"] == []

    assert ingredients[1]["strength"]["uid"] == strength.uid
    assert ingredients[1]["half_life"]["uid"] == half_life.uid
    assert ingredients[1]["lag_times"][0]["value"] == lag_time.value
    assert (
        ingredients[1]["lag_times"][0]["unit_definition_uid"]
        == lag_time.unit_definition_uid
    )
    assert ingredients[1]["lag_times"][0]["unit_label"] == lag_time.unit_label
    assert ingredients[1]["lag_times"][0]["sdtm_domain_label"] == "Adverse Event Domain"

    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert list(res["possible_actions"]) == ["approve", "delete", "edit"]

    TestUtils.assert_timestamp_is_in_utc_zone(res["start_date"])
    TestUtils.assert_timestamp_is_newer_than(res["start_date"], 60)

    # Delete pharmaceutical product
    response = api_client.delete(f"/concepts/pharmaceutical-products/{res['uid']}")
    assert_response_status_code(response, 204)

    # Check that the pharmaceutical product is deleted
    response = api_client.get(f"/concepts/pharmaceutical-products/{res['uid']}")
    assert_response_status_code(response, 404)


def test_negative_create_pharmaceutical_product_wrong_links(api_client):
    formulation = {
        "external_id": "formulation-prodex-id",
        "name": "formulation-name",
        "ingredients": [
            {
                "external_id": "ingredient-prodex-id-a",
                "active_substance_uid": active_substances_all[0].uid,
                "strength_uid": strength.uid,
                "half_life_uid": half_life.uid,
                "lag_time_uids": [lag_time.uid],
            },
            {
                "external_id": "ingredient-prodex-id-b",
                "active_substance_uid": active_substances_all[1].uid,
                "strength_uid": strength.uid,
                "half_life_uid": half_life.uid,
                "lag_time_uids": [lag_time.uid],
            },
        ],
    }

    # Try to create new pharmaceutical product with non-existing dosage form
    payload = {
        "library_name": "Sponsor",
        "external_id": "external_id-NEW",
        "dosage_form_uids": ["NON_EXISTING_UID"],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation],
    }
    response = api_client.post(
        "/concepts/pharmaceutical-products", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 400)
    assert (
        res["message"]
        == "PharmaceuticalProductVO tried to connect to non-existent or non-final Dosage Form with UID 'NON_EXISTING_UID'."
    )

    # Try to create new pharmaceutical product with non-existing route of administration
    payload = {
        "library_name": "Sponsor",
        "external_id": "external_id-NEW",
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": ["NON_EXISTING_UID"],
        "formulations": [formulation],
    }
    response = api_client.post(
        "/concepts/pharmaceutical-products", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 400)
    assert (
        res["message"]
        == "PharmaceuticalProductVO tried to connect to non-existent or non-final Route of Administration with UID 'NON_EXISTING_UID'."
    )

    # Try to create new pharmaceutical product with non-existing active substance
    formulation_wrong_uid = copy.deepcopy(formulation)
    formulation_wrong_uid["ingredients"][0]["active_substance_uid"] = "NON_EXISTING_UID"
    payload = {
        "library_name": "Sponsor",
        "external_id": "external_id-NEW",
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation_wrong_uid],
    }
    response = api_client.post(
        "/concepts/pharmaceutical-products", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 400)
    assert (
        res["message"]
        == "IngredientVO tried to connect to non-existent Active Substance with UID 'NON_EXISTING_UID'."
    )

    # Try to create new pharmaceutical product with non-existing ingredient strength
    formulation_wrong_uid = copy.deepcopy(formulation)
    formulation_wrong_uid["ingredients"][0]["strength_uid"] = "NON_EXISTING_UID"
    payload = {
        "library_name": "Sponsor",
        "external_id": "external_id-NEW",
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation_wrong_uid],
    }
    response = api_client.post(
        "/concepts/pharmaceutical-products", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 400)
    assert (
        res["message"]
        == "IngredientVO tried to connect to non-existent Strength with UID 'NON_EXISTING_UID'."
    )

    # Try to create new pharmaceutical product with non-existing ingredient half-life
    formulation_wrong_uid = copy.deepcopy(formulation)
    formulation_wrong_uid["ingredients"][0]["half_life_uid"] = "NON_EXISTING_UID"
    payload = {
        "library_name": "Sponsor",
        "external_id": "external_id-NEW",
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation_wrong_uid],
    }
    response = api_client.post(
        "/concepts/pharmaceutical-products", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 400)
    assert (
        res["message"]
        == "IngredientVO tried to connect to non-existent Half-Life with UID 'NON_EXISTING_UID'."
    )

    # Try to create new pharmaceutical product with non-existing ingredient lag-time
    formulation_wrong_uid = copy.deepcopy(formulation)
    formulation_wrong_uid["ingredients"][0]["lag_time_uids"][0] = "NON_EXISTING_UID"
    payload = {
        "library_name": "Sponsor",
        "external_id": "external_id-NEW",
        "dosage_form_uids": [ct_term_dose_form.term_uid],
        "route_of_administration_uids": [ct_term_roa.term_uid],
        "formulations": [formulation_wrong_uid],
    }
    response = api_client.post(
        "/concepts/pharmaceutical-products", data=json.dumps(payload), headers=HEADERS
    )
    res = response.json()

    assert_response_status_code(response, 400)
    assert (
        res["message"]
        == "IngredientVO tried to connect to non-existent Lag-Time with UID 'NON_EXISTING_UID'."
    )


def test_negative_delete_approved_pharmaceutical_product(api_client):
    item = TestUtils.create_pharmaceutical_product(approve=True)

    # Try to delete approved pharmaceutical product
    response = api_client.delete(f"/concepts/pharmaceutical-products/{item.uid}")
    assert_response_status_code(response, 400)
    assert response.json()["message"] == "Object has been accepted"

    # Check that the pharmaceutical product is not deleted
    response = api_client.get(f"/concepts/pharmaceutical-products/{item.uid}")
    assert_response_status_code(response, 200)
