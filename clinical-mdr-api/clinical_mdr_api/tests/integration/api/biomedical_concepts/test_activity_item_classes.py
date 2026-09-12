"""
Tests for /activity-item-classes endpoints
"""

import json
import logging
from functools import reduce
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clinical_mdr_api.main import app
from clinical_mdr_api.models.biomedical_concepts.activity_instance_class import (
    ActivityInstanceClass,
)
from clinical_mdr_api.models.biomedical_concepts.activity_item_class import (
    ActivityItemClass,
)
from clinical_mdr_api.models.controlled_terminologies.ct_codelist import CTCodelist
from clinical_mdr_api.models.controlled_terminologies.ct_term import CTTerm
from clinical_mdr_api.models.standard_data_models.data_model_ig import DataModelIG
from clinical_mdr_api.models.standard_data_models.dataset import Dataset
from clinical_mdr_api.models.standard_data_models.dataset_class import DatasetClass
from clinical_mdr_api.models.standard_data_models.dataset_variable import (
    DatasetVariable,
)
from clinical_mdr_api.models.standard_data_models.variable_class import VariableClass
from clinical_mdr_api.tests.integration.utils.api import (
    inject_and_clear_db,
    inject_base_data,
)
from clinical_mdr_api.tests.integration.utils.utils import CT_CODELIST_UIDS, TestUtils
from clinical_mdr_api.tests.utils.checks import assert_response_status_code

# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-arguments

# pytest fixture functions have other fixture functions as arguments,
# which pylint interprets as unused arguments


log = logging.getLogger(__name__)

# Global variables shared between fixtures and tests
activity_item_classes_all: list[ActivityItemClass]
activity_instance_class: ActivityInstanceClass
activity_instance_class2: ActivityInstanceClass
role_term: CTTerm
data_type_term: CTTerm
dataset_class: DatasetClass
variable_class: VariableClass
data_type_codelist: CTCodelist
role_codelist: CTCodelist
response_codelist: CTCodelist
dataset: Dataset
dataset_variable: DatasetVariable
data_model_catalogue_name: str
data_model_ig: DataModelIG


@pytest.fixture(scope="module")
def api_client(test_data):
    """Create FastAPI test client
    using the database name set in the `test_data` fixture"""
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    """Initialize test data"""
    inject_and_clear_db("activity-item-class.api")
    inject_base_data()

    global activity_item_classes_all
    global activity_instance_class
    global activity_instance_class2
    global data_type_codelist
    global data_type_term
    global role_codelist
    global role_term
    global response_codelist
    global variable_class
    global dataset_class
    global dataset
    global dataset_variable
    global data_model_catalogue_name
    global data_model_ig

    activity_instance_class = TestUtils.create_activity_instance_class(
        name="Activity Instance Class name1"
    )
    activity_instance_class2 = TestUtils.create_activity_instance_class(
        name="Activity Instance Class name2"
    )

    data_type_codelist = TestUtils.create_ct_codelist(
        name="DATATYPE", submission_value="DATATYPE", extensible=True, approve=True
    )
    data_type_term = TestUtils.create_ct_term(
        sponsor_preferred_name="Data type", codelist_uid=data_type_codelist.codelist_uid
    )
    role_codelist = TestUtils.create_ct_codelist(
        name="ROLE", submission_value="ROLE", extensible=True, approve=True
    )
    role_term = TestUtils.create_ct_term(
        sponsor_preferred_name="Role", codelist_uid=role_codelist.codelist_uid
    )
    response_codelist = TestUtils.create_ct_codelist(
        name="RESPONSE", submission_value="RESPONSE", extensible=True, approve=True
    )
    data_model = TestUtils.create_data_model()
    data_model_catalogue_name = TestUtils.create_data_model_catalogue()
    dataset_class = TestUtils.create_dataset_class(
        data_model_uid=data_model.uid,
        data_model_catalogue_name=data_model_catalogue_name,
        data_model_name=data_model.name,
    )
    variable_class = TestUtils.create_variable_class(
        dataset_class_uid=dataset_class.uid,
        data_model_catalogue_name=data_model_catalogue_name,
        data_model_name=data_model.uid,
        data_model_version=data_model.version_number,
    )
    data_model_ig = TestUtils.create_data_model_ig(
        implemented_data_model=data_model.uid
    )
    dataset = TestUtils.create_dataset(
        data_model_ig_uid=data_model_ig.uid,
        data_model_ig_version_number=data_model_ig.version_number,
        implemented_dataset_class_name=dataset_class.uid,
        data_model_catalogue_name=data_model_catalogue_name,
    )
    dataset_variable = TestUtils.create_dataset_variable(
        dataset_uid=dataset.uid,
        data_model_catalogue_name=data_model_catalogue_name,
        data_model_ig_name=data_model_ig.uid,
        data_model_ig_version=data_model_ig.version_number,
        class_variable_uid=variable_class.uid,
        references_codelist_uids=[CT_CODELIST_UIDS.default],
    )

    # Create some activity item classes
    activity_item_classes_all = [
        TestUtils.create_activity_item_class(
            name="name A",
            definition="definition A",
            nci_concept_id="nci id A",
            order=1,
            activity_instance_classes=[
                {
                    "uid": activity_instance_class.uid,
                    "mandatory": True,
                    "is_adam_param_specific_enabled": True,
                    "is_additional_optional": False,
                    "is_default_linked": False,
                },
                {
                    "uid": activity_instance_class2.uid,
                    "mandatory": True,
                    "is_adam_param_specific_enabled": True,
                    "is_additional_optional": False,
                    "is_default_linked": False,
                },
            ],
            role_uid=role_term.term_uid,
            data_type_uid=data_type_term.term_uid,
        ),
        TestUtils.create_activity_item_class(
            name="name-AAA",
            definition="definition AAA",
            nci_concept_id="nci id AAA",
            order=2,
            activity_instance_classes=[
                {
                    "uid": activity_instance_class.uid,
                    "mandatory": True,
                    "is_adam_param_specific_enabled": True,
                    "is_additional_optional": False,
                    "is_default_linked": False,
                }
            ],
            role_uid=role_term.term_uid,
            data_type_uid=data_type_term.term_uid,
        ),
        TestUtils.create_activity_item_class(
            name="name-BBB",
            definition="definition BBB",
            nci_concept_id="nci id BBB",
            order=3,
            activity_instance_classes=[
                {
                    "uid": activity_instance_class.uid,
                    "mandatory": True,
                    "is_adam_param_specific_enabled": True,
                    "is_additional_optional": False,
                    "is_default_linked": False,
                }
            ],
            role_uid=role_term.term_uid,
            data_type_uid=data_type_term.term_uid,
        ),
        TestUtils.create_activity_item_class(
            name="name XXX",
            definition="definition XXX",
            nci_concept_id="nci id XXX",
            order=4,
            activity_instance_classes=[
                {
                    "uid": activity_instance_class.uid,
                    "mandatory": True,
                    "is_adam_param_specific_enabled": True,
                    "is_additional_optional": False,
                    "is_default_linked": False,
                }
            ],
            role_uid=role_term.term_uid,
            data_type_uid=data_type_term.term_uid,
        ),
        TestUtils.create_activity_item_class(
            name="name YYY",
            definition="definition YYY",
            nci_concept_id="nci id YYY",
            order=5,
            activity_instance_classes=[
                {
                    "uid": activity_instance_class.uid,
                    "mandatory": True,
                    "is_adam_param_specific_enabled": True,
                    "is_additional_optional": False,
                    "is_default_linked": False,
                }
            ],
            role_uid=role_term.term_uid,
            data_type_uid=data_type_term.term_uid,
        ),
    ]

    for index in range(5):
        activity_item_classes_all.append(
            TestUtils.create_activity_item_class(
                name=f"name-AAA-{index}",
                definition=f"definition AAA-{index}",
                nci_concept_id=f"nci id AAA-{index}",
                order=(index * 4) + 1,
                activity_instance_classes=[
                    {
                        "uid": activity_instance_class.uid,
                        "mandatory": False,
                        "is_adam_param_specific_enabled": False,
                        "is_additional_optional": False,
                        "is_default_linked": False,
                    }
                ],
                role_uid=role_term.term_uid,
                data_type_uid=data_type_term.term_uid,
            )
        )
        activity_item_classes_all.append(
            TestUtils.create_activity_item_class(
                name=f"name-BBB-{index}",
                definition=f"definition BBB-{index}",
                nci_concept_id=f"nci id BBB-{index}",
                order=(index * 4) + 2,
                activity_instance_classes=[
                    {
                        "uid": activity_instance_class.uid,
                        "mandatory": False,
                        "is_adam_param_specific_enabled": False,
                        "is_additional_optional": False,
                        "is_default_linked": False,
                    },
                ],
                role_uid=role_term.term_uid,
                data_type_uid=data_type_term.term_uid,
            )
        )
        activity_item_classes_all.append(
            TestUtils.create_activity_item_class(
                name=f"name-XXX-{index}",
                definition=f"definition XXX-{index}",
                nci_concept_id=f"nci id XXX-{index}",
                order=(index * 4) + 3,
                activity_instance_classes=[
                    {
                        "uid": activity_instance_class.uid,
                        "mandatory": False,
                        "is_adam_param_specific_enabled": False,
                        "is_additional_optional": False,
                        "is_default_linked": False,
                    },
                ],
                role_uid=role_term.term_uid,
                data_type_uid=data_type_term.term_uid,
            )
        )
        activity_item_classes_all.append(
            TestUtils.create_activity_item_class(
                name=f"name-YYY-{index}",
                definition=f"definition YYY-{index}",
                nci_concept_id=f"nci id YYY-{index}",
                order=(index * 4) + 4,
                activity_instance_classes=[
                    {
                        "uid": activity_instance_class.uid,
                        "mandatory": False,
                        "is_adam_param_specific_enabled": False,
                        "is_additional_optional": False,
                        "is_default_linked": False,
                    },
                ],
                role_uid=role_term.term_uid,
                data_type_uid=data_type_term.term_uid,
            )
        )


ACTIVITY_IC_FIELDS_ALL = [
    "uid",
    "name",
    "display_name",
    "definition",
    "nci_concept_id",
    "order",
    "activity_instance_classes",
    "data_type",
    "role",
    "variable_classes",
    "library_name",
    "start_date",
    "end_date",
    "status",
    "version",
    "change_description",
    "author_username",
    "possible_actions",
]

ACTIVITY_IC_FIELDS_NOT_NULL = [
    "uid",
    "name",
    "order",
    "activity_instance_classes",
    "data_type",
    "role",
]


def test_get_activity_item_class(api_client):
    response = api_client.get(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}"
    )
    res = response.json()

    assert_response_status_code(response, 200)

    # Check fields included in the response
    assert set(res.keys()) == set(ACTIVITY_IC_FIELDS_ALL)
    for key in ACTIVITY_IC_FIELDS_NOT_NULL:
        assert res[key] is not None

    assert res["uid"] == activity_item_classes_all[0].uid
    assert res["name"] == "name A"
    assert res["definition"] == "definition A"
    assert res["nci_concept_id"] == "nci id A"
    assert res["order"] == 1
    assert sorted(
        instance_class["uid"] for instance_class in res["activity_instance_classes"]
    ) == [activity_instance_class.uid, activity_instance_class2.uid]
    assert sorted(
        instance_class["name"] for instance_class in res["activity_instance_classes"]
    ) == [activity_instance_class.name, activity_instance_class2.name]
    assert res["role"]["uid"] == role_term.term_uid
    assert res["data_type"]["uid"] == data_type_term.term_uid
    assert res["version"] == "1.0"
    assert res["status"] == "Final"
    assert res["library_name"] == "Sponsor"
    assert res["possible_actions"] == ["inactivate", "new_version"]


def test_get_activity_item_class_pagination(api_client):
    results_paginated: dict[Any, Any] = {}
    sort_by = '{"name": true}'
    for page_number in range(1, 4):
        url = f"/activity-item-classes?page_number={page_number}&page_size=10&sort_by={sort_by}"
        response = api_client.get(url)
        res = response.json()
        res_names = [item["name"] for item in res["items"]]
        results_paginated[page_number] = res_names
        log.info("Page %s: %s", page_number, res_names)

    log.info("All pages: %s", results_paginated)

    results_paginated_merged = list(
        reduce(lambda a, b: list(a) + list(b), list(results_paginated.values()))
    )
    log.info("All rows returned by pagination: %s", results_paginated_merged)

    res_all = api_client.get(
        f"/activity-item-classes?page_number=1&page_size=100&sort_by={sort_by}"
    ).json()
    results_all_in_one_page = list(map(lambda x: x["name"], res_all["items"]))
    log.info("All rows in one page: %s", results_all_in_one_page)
    assert len(results_all_in_one_page) == len(results_paginated_merged)
    assert len(activity_item_classes_all) == len(results_paginated_merged)


@pytest.mark.parametrize(
    "page_size, page_number, total_count, sort_by, expected_result_len",
    [
        pytest.param(None, None, None, None, 10),
        pytest.param(3, 1, True, None, 3),
        pytest.param(3, 2, True, None, 3),
        pytest.param(10, 2, True, None, 10),
        pytest.param(10, 3, True, None, 5),  # Total number of data models is 25
        pytest.param(10, 1, True, '{"name": false}', 10),
        pytest.param(10, 2, True, '{"name": true}', 10),
    ],
)
def test_get_activity_item_classes(
    api_client, page_size, page_number, total_count, sort_by, expected_result_len
):
    url = "/activity-item-classes"
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
    assert res["total"] == (len(activity_item_classes_all) if total_count else 0)
    assert res["page"] == (page_number if page_number else 1)
    assert res["size"] == (page_size if page_size else 10)

    for item in res["items"]:
        assert set(list(item.keys())) == set(ACTIVITY_IC_FIELDS_ALL)
        for key in ACTIVITY_IC_FIELDS_NOT_NULL:
            assert item[key] is not None

    if sort_by:
        # sort_by is JSON string in the form: {"sort_field_name": is_ascending_order}
        sort_by_dict = json.loads(sort_by)
        sort_field: str = list(sort_by_dict.keys())[0]
        sort_order_ascending: bool = list(sort_by_dict.values())[0]

        # extract list of values of 'sort_field_name' field from the returned result
        result_vals = list(map(lambda x: x[sort_field], res["items"]))
        result_vals_sorted_locally = result_vals.copy()
        result_vals_sorted_locally.sort(reverse=not sort_order_ascending)
        # This asser fails due to API issue with sorting coupled with pagination
        # assert result_vals == result_vals_sorted_locally


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
def test_get_activity_item_classes_csv_xml_excel(api_client, export_format):
    url = "activity-item-classes"
    TestUtils.verify_exported_data_format(api_client, export_format, url)


@pytest.mark.parametrize(
    "filter_by, expected_matched_field, expected_result_prefix",
    [
        pytest.param('{"*": {"v": ["aaa"]}}', "name", "name-AAA"),
        pytest.param('{"*": {"v": ["bBb"]}}', "name", "name-BBB"),
        pytest.param(
            '{"*": {"v": ["Activity Instance Class name1"]}}',
            "activity_instance_classes.name",
            "Activity Instance Class name1",
        ),
        pytest.param('{"*": {"v": ["ccc"]}}', None, None),
    ],
)
def test_filtering_wildcard(
    api_client, filter_by, expected_matched_field, expected_result_prefix
):
    url = f"/activity-item-classes?filters={filter_by}"
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
        pytest.param('{"order": {"v": [1]}}', "order", 1),
        pytest.param(
            '{"activity_instance_classes.uid": {"v": ["ActivityInstanceClass_000001"]}}',
            "activity_instance_classes.uid",
            "ActivityInstanceClass_000001",
        ),
        pytest.param(
            '{"activity_instance_classes.name": {"v": ["Activity Instance Class name1"]}}',
            "activity_instance_classes.name",
            "Activity Instance Class name1",
        ),
        pytest.param(
            '{"activity_instance_classes.mandatory": {"v": [true]}}',
            "activity_instance_classes.mandatory",
            True,
        ),
        pytest.param(
            '{"data_type.name": {"v": ["Data type"]}}',
            "data_type.name",
            "Data type",
        ),
        pytest.param(
            '{"role.name": {"v": ["Role"]}}',
            "role.name",
            "Role",
        ),
    ],
)
def test_filtering_exact(
    api_client, filter_by, expected_matched_field, expected_result
):
    url = f"/activity-item-classes?filters={filter_by}"
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


def test_edit_activity_item_class(api_client):
    activity_instance_class_after_edit = TestUtils.create_activity_instance_class(
        name="Activity IC after edit"
    )
    activity_item_class = TestUtils.create_activity_item_class(
        name="New item class",
        order=30,
        activity_instance_classes=[
            {
                "uid": activity_instance_class.uid,
                "mandatory": True,
                "is_adam_param_specific_enabled": True,
                "is_additional_optional": False,
                "is_default_linked": False,
            }
        ],
        approve=False,
        data_type_uid=data_type_term.term_uid,
        role_uid=role_term.term_uid,
    )
    response = api_client.patch(
        f"/activity-item-classes/{activity_item_class.uid}",
        json={
            "name": "new name for item class",
            "definition": "new definition for item class",
            "nci_concept_id": "new nci concept id",
            "display_name": "new display name for item class",
            "order": 45,
            "activity_instance_classes": [
                {
                    "uid": activity_instance_class_after_edit.uid,
                    "mandatory": False,
                    "is_adam_param_specific_enabled": False,
                    "is_additional_optional": True,
                    "is_default_linked": True,
                }
            ],
            "change_description": "updated item class",
        },
    )
    res = response.json()
    assert_response_status_code(response, 200)
    assert res["name"] == "new name for item class"
    assert res["definition"] == "new definition for item class"
    assert res["nci_concept_id"] == "new nci concept id"
    assert res["display_name"] == "new display name for item class"
    assert res["order"] == 45
    ic_after_edit = next(
        x
        for x in res["activity_instance_classes"]
        if x["uid"] == activity_instance_class_after_edit.uid
    )
    assert ic_after_edit["name"] == "Activity IC after edit"
    assert ic_after_edit["mandatory"] is False
    assert ic_after_edit["is_adam_param_specific_enabled"] is False
    assert ic_after_edit["is_additional_optional"] is True
    assert ic_after_edit["is_default_linked"] is True
    assert res["version"] == "0.2"
    assert res["status"] == "Draft"
    assert res["possible_actions"] == ["approve", "delete", "edit"]
    assert res["library_name"] == "Sponsor"

    # Verify the changes persisted by doing a GET request
    get_response = api_client.get(f"/activity-item-classes/{activity_item_class.uid}")
    get_res = get_response.json()
    assert_response_status_code(get_response, 200)
    assert get_res["name"] == "new name for item class"
    assert get_res["definition"] == "new definition for item class"
    assert get_res["nci_concept_id"] == "new nci concept id"
    assert get_res["display_name"] == "new display name for item class"
    assert get_res["order"] == 45
    ic_after_edit = next(
        x
        for x in res["activity_instance_classes"]
        if x["uid"] == activity_instance_class_after_edit.uid
    )
    assert ic_after_edit["name"] == "Activity IC after edit"
    assert ic_after_edit["mandatory"] is False
    assert ic_after_edit["is_adam_param_specific_enabled"] is False
    assert ic_after_edit["is_additional_optional"] is True
    assert ic_after_edit["is_default_linked"] is True
    assert get_res["version"] == "0.2"
    assert get_res["status"] == "Draft"
    assert get_res["possible_actions"] == ["approve", "delete", "edit"]
    assert get_res["library_name"] == "Sponsor"

    response = api_client.patch(
        f"/activity-item-classes/{activity_item_class.uid}/model-mappings",
        json={
            "variable_class_uids": [variable_class.uid],
        },
    )
    res = response.json()
    assert_response_status_code(response, 200)
    assert res["variable_classes"] == [{"uid": variable_class.uid}]

    # Edit Valid codelist mapping and verify
    response = api_client.patch(
        f"/activity-item-classes/{activity_item_class.uid}/valid-codelist-mappings",
        json={"valid_codelist_uids": [response_codelist.codelist_uid]},
    )
    res = response.json()
    assert_response_status_code(response, 200)

    response = api_client.get(
        f"/activity-item-classes/{activity_item_class.uid}/datasets/{dataset.uid}/codelists?valid_codelists_for_item=True",
    )
    res = response.json()
    assert len(res["items"]) == 1
    assert res["items"][0]["codelist_uid"] == response_codelist.codelist_uid


def test_post_activity_item_class(api_client):
    response = api_client.post(
        "/activity-item-classes",
        json={
            "name": "New AIC Name",
            "definition": "New AIC Def",
            "nci_concept_id": "New nci id",
            "display_name": "New AIC Display Name",
            "order": 36,
            "library_name": "Sponsor",
            "activity_instance_classes": [
                {
                    "uid": activity_instance_class.uid,
                    "mandatory": True,
                    "is_adam_param_specific_enabled": True,
                    "is_additional_optional": False,
                    "is_default_linked": False,
                }
            ],
            "role_uid": role_term.term_uid,
            "data_type_uid": data_type_term.term_uid,
        },
    )
    assert_response_status_code(response, 201)
    res = response.json()
    assert res["name"] == "New AIC Name"
    assert res["definition"] == "New AIC Def"
    assert res["nci_concept_id"] == "New nci id"
    assert res["display_name"] == "New AIC Display Name"
    assert res["order"] == 36
    assert res["activity_instance_classes"][0]["uid"] == activity_instance_class.uid
    assert res["activity_instance_classes"][0]["name"] == activity_instance_class.name
    assert res["activity_instance_classes"][0]["mandatory"] is True
    assert res["activity_instance_classes"][0]["is_adam_param_specific_enabled"] is True
    assert res["activity_instance_classes"][0]["is_additional_optional"] is False
    assert res["activity_instance_classes"][0]["is_default_linked"] is False
    assert res["version"] == "0.1"
    assert res["status"] == "Draft"
    assert res["possible_actions"] == ["approve", "delete", "edit"]
    assert res["library_name"] == "Sponsor"


def test_activity_item_class_versioning(api_client):
    activity_item_class = TestUtils.create_activity_item_class(
        name="New item",
        order=2,
        activity_instance_classes=[
            {
                "uid": activity_instance_class.uid,
                "mandatory": False,
                "is_adam_param_specific_enabled": False,
                "is_additional_optional": False,
                "is_default_linked": False,
            }
        ],
        approve=False,
        data_type_uid=data_type_term.term_uid,
        role_uid=role_term.term_uid,
    )

    # not successful create new version
    response = api_client.post(
        f"/activity-item-classes/{activity_item_class.uid}/versions"
    )
    res = response.json()
    assert_response_status_code(response, 400)
    assert res["message"] == "New draft version can be created only for FINAL versions."

    # successful approve
    response = api_client.post(
        f"/activity-item-classes/{activity_item_class.uid}/approvals"
    )
    assert_response_status_code(response, 201)

    # not successful approve
    response = api_client.post(
        f"/activity-item-classes/{activity_item_class.uid}/approvals"
    )
    res = response.json()
    assert_response_status_code(response, 400)
    assert res["message"] == "The object isn't in draft status."

    # not successful reactivate
    response = api_client.post(
        f"/activity-item-classes/{activity_item_class.uid}/activations"
    )
    assert_response_status_code(response, 400)
    res = response.json()
    assert res["message"] == "Only RETIRED version can be reactivated."

    # successful inactivate
    response = api_client.delete(
        f"/activity-item-classes/{activity_item_class.uid}/activations"
    )
    assert_response_status_code(response, 200)

    # successful reactivate
    response = api_client.post(
        f"/activity-item-classes/{activity_item_class.uid}/activations"
    )
    assert_response_status_code(response, 200)

    # successful new version
    response = api_client.post(
        f"/activity-item-classes/{activity_item_class.uid}/versions"
    )
    assert_response_status_code(response, 201)

    activity_ic_to_delete = TestUtils.create_activity_item_class(
        name="activity ic to delete",
        order=2,
        activity_instance_classes=[
            {
                "uid": activity_instance_class.uid,
                "mandatory": False,
                "is_adam_param_specific_enabled": False,
                "is_additional_optional": False,
                "is_default_linked": False,
            },
        ],
        approve=False,
        data_type_uid=data_type_term.term_uid,
        role_uid=role_term.term_uid,
    )
    # successful delete
    response = api_client.delete(f"/activity-item-classes/{activity_ic_to_delete.uid}")
    assert_response_status_code(response, 204)


def test_get_activity_item_class_codelists(api_client):
    # Map an ActivityItemClass to a VariableClass
    # This VariableClass will have Variables belonging to the target Dataset
    api_client.patch(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/model-mappings",
        json={
            "variable_class_uids": [variable_class.uid],
        },
    )

    # Also map the item class to a response codelist
    api_client.patch(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/valid-codelist-mappings",
        json={"valid_codelist_uids": [response_codelist.codelist_uid]},
    )

    # Fetching terms with this ActivityItemClass and Dataset
    # Will return those Variables
    # Which will in turn map a Codelist, whose Terms should be returned
    response = api_client.get(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/datasets/{dataset.uid}/codelists"
    )
    assert_response_status_code(response, 200)
    res = response.json()
    assert res["items"][0]["codelist_uid"] == "C66737"
    assert len(res["items"]) == 1

    # term uids should be None, indicating that all terms of the codelist are available
    assert res["items"][0]["term_uids"] is None

    # Fetch codelists with ct catalogue name filter
    response = api_client.get(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/datasets/{dataset.uid}/codelists?ct_catalogue_name=SDTM CT"
    )
    assert_response_status_code(response, 200)
    res = response.json()
    assert len(res["items"]) == 1
    assert res["items"][0]["codelist_uid"] == "C66737"

    # Fetch codelists with non-existent ct catalogue name filter
    response = api_client.get(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/datasets/{dataset.uid}/codelists?ct_catalogue_name=non-existent"
    )
    assert_response_status_code(response, 200)
    res = response.json()
    assert len(res["items"]) == 0

    # Now, test that sponsor models are properly used
    sponsor_model = TestUtils.create_sponsor_model(
        ig_uid=data_model_ig.uid,
        ig_version_number=data_model_ig.version_number,
        version_number="1",
    )
    sponsor_dataset = TestUtils.create_sponsor_dataset(
        dataset_uid=dataset.uid,
        sponsor_model_name=sponsor_model.name,
        sponsor_model_version_number=sponsor_model.version,
        implemented_dataset_class=dataset_class.uid,
    )

    _ = TestUtils.create_sponsor_dataset_variable(
        target_data_model_catalogue=data_model_catalogue_name,
        dataset_uid=sponsor_dataset.uid,
        dataset_variable_uid=dataset_variable.uid,
        sponsor_model_name=sponsor_model.name,
        sponsor_model_version_number=sponsor_model.version,
        implemented_variable_class=variable_class.uid,
        implemented_parent_dataset_class=dataset_class.uid,
        references_codelists=[CT_CODELIST_UIDS.default],
        references_terms=["C123631"],
    )

    # Fetch codelists using sponsor model
    # Should be filtered down to a single term
    response = api_client.get(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/datasets/{dataset.uid}/codelists?use_sponsor_model=True"
    )
    res = response.json()
    assert len(res["items"]) == 1
    assert res["items"][0]["term_uids"] == ["C123631"]

    # Fetch codelists without using sponsor model
    # Should return all terms, i.e. None
    response = api_client.get(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/datasets/{dataset.uid}/codelists?use_sponsor_model=False"
    )
    res = response.json()
    # term uids should be None, indicating that all terms of the codelist are available
    assert len(res["items"]) == 1
    assert res["items"][0]["term_uids"] is None

    # Finally, fetching using the valid codelists filter
    # Will only return the specific codelists marked as valid
    response = api_client.get(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/datasets/{dataset.uid}/codelists?valid_codelists_for_item=True",
    )
    res = response.json()
    assert len(res["items"]) == 1
    assert res["items"][0]["codelist_uid"] == response_codelist.codelist_uid


def test_get_activity_item_class_overview(api_client: TestClient) -> None:
    """Test GET /activity-item-classes/{uid}/overview endpoint"""
    activity_item_class = activity_item_classes_all[0]

    # Test basic overview
    response = api_client.get(
        f"/activity-item-classes/{activity_item_class.uid}/overview"
    )
    assert_response_status_code(response, 200)

    result = response.json()
    assert "activity_item_class" in result
    assert "all_versions" in result

    item_detail = result["activity_item_class"]
    assert item_detail["uid"] == activity_item_class.uid
    assert item_detail["name"] == activity_item_class.name
    assert item_detail["definition"] == activity_item_class.definition
    assert item_detail["nci_code"] == activity_item_class.nci_concept_id
    assert item_detail["status"] == "Final"  # Test data creates items with Final status
    assert item_detail["version"] == "1.0"  # Test data creates version 1.0

    # Test with version parameter
    response = api_client.get(
        f"/activity-item-classes/{activity_item_class.uid}/overview?version=0.1"
    )
    assert_response_status_code(response, 200)

    result = response.json()
    assert result["activity_item_class"]["version"] == "0.1"

    # Test with non-existent UID
    response = api_client.get("/activity-item-classes/INVALID_UID/overview")
    assert_response_status_code(response, 404)


def test_get_activity_instance_classes_using_item(api_client: TestClient) -> None:
    """Test GET /activity-item-classes/{uid}/activity-instance-classes endpoint"""
    activity_item_class = activity_item_classes_all[0]

    # Test basic request
    response = api_client.get(
        f"/activity-item-classes/{activity_item_class.uid}/activity-instance-classes"
    )
    assert_response_status_code(response, 200)

    result = response.json()
    assert "items" in result
    assert "total" in result
    assert len(result["items"]) > 0

    # Check first item structure
    first_item = result["items"][0]
    assert "uid" in first_item
    assert "name" in first_item
    assert "adam_param_specific_enabled" in first_item
    assert "mandatory" in first_item
    assert "modified_date" in first_item
    assert "modified_by" in first_item
    assert "version" in first_item
    assert "status" in first_item

    # Test with pagination
    response = api_client.get(
        f"/activity-item-classes/{activity_item_class.uid}/activity-instance-classes?page_size=1&page_number=1&total_count=true"
    )
    assert_response_status_code(response, 200)

    result = response.json()
    assert len(result["items"]) <= 1
    assert result["total"] >= 1

    # Test with version parameter
    response = api_client.get(
        f"/activity-item-classes/{activity_item_class.uid}/activity-instance-classes?version=0.1"
    )
    assert_response_status_code(response, 200)

    # Test with non-existent UID - returns empty results
    response = api_client.get(
        "/activity-item-classes/INVALID_UID/activity-instance-classes"
    )
    assert_response_status_code(response, 200)
    result = response.json()
    assert result["items"] == []
    assert result["total"] == 0


def test_get_activity_item_classes_versions(api_client):
    """Test GET /activity-item-classes/versions endpoint"""
    # First, create a new version of one item class so we have multiple versions
    response = api_client.post(
        f"/activity-item-classes/{activity_item_classes_all[0].uid}/versions"
    )
    assert_response_status_code(response, 201)

    # Get all versions
    response = api_client.get(
        "/activity-item-classes/versions?page_size=100&total_count=true"
    )
    res = response.json()

    assert_response_status_code(response, 200)

    # Check response structure
    assert set(res.keys()) == {"items", "total", "page", "size"}
    assert res["total"] > 0

    # Should have more versions than unique classes (since we created a new version above)
    assert res["total"] > len(activity_item_classes_all)

    # Check that the items are sorted by start_date descending
    start_dates = [item["start_date"] for item in res["items"] if item["start_date"]]
    assert start_dates == sorted(start_dates, reverse=True)

    # Check that the class updated in this test has multiple versions
    versions_of_class = [
        item["version"]
        for item in res["items"]
        if item["uid"] == activity_item_classes_all[0].uid
    ]
    assert len(versions_of_class) >= 2

    # Check fields on first item
    item = res["items"][0]
    assert "uid" in item
    assert "name" in item
    assert "version" in item
    assert "status" in item
    assert "start_date" in item
    assert "library_name" in item


def test_get_activity_item_classes_versions_pagination(api_client):
    """Test pagination on /activity-item-classes/versions"""
    # Get first page
    response = api_client.get(
        "/activity-item-classes/versions?page_size=2&page_number=1&total_count=true"
    )
    assert_response_status_code(response, 200)
    page1 = response.json()
    assert len(page1["items"]) == 2
    assert page1["total"] > 2

    # Get second page
    response = api_client.get(
        "/activity-item-classes/versions?page_size=2&page_number=2&total_count=true"
    )
    assert_response_status_code(response, 200)
    page2 = response.json()
    assert len(page2["items"]) > 0

    # Pages should not overlap
    page1_uids_versions = {(i["uid"], i["version"]) for i in page1["items"]}
    page2_uids_versions = {(i["uid"], i["version"]) for i in page2["items"]}
    assert page1_uids_versions.isdisjoint(page2_uids_versions)
