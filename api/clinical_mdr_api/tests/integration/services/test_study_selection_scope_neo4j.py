"""Synthetic endpoint applicability through HTTP on the owned Community graph."""

# Pytest resolves the imported fixture functions by parameter name.
# pylint: disable=redefined-outer-name

import json

import pytest
from neomodel import db

from clinical_mdr_api.tests.integration.services.test_authored_protocol_documents_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _create_protocol_study,
    native_visit_graph,
    protocol_client,
    protocol_graph,
)
from clinical_mdr_api.tests.integration.services.test_study_arm_lifecycle_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _fingerprint,
    _lock,
    _unlock,
    lifecycle_client,
)
from clinical_mdr_api.tests.integration.utils.factory_epoch import (
    create_study_epoch,
    create_study_epoch_codelists_ret_cat_and_lib,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.config import settings

pytestmark = [
    pytest.mark.estate_fixture,
    pytest.mark.parametrize(
        "native_visit_graph", ["study-only"], indirect=True, scope="module"
    ),
]
SCOPE_FIELDS = (
    "applies_to_arm_uids",
    "applies_to_cohort_uids",
    "applies_to_epoch_uids",
)


@pytest.fixture(scope="module")
def scope_library(protocol_graph):
    """One approved plain endpoint template shared by the module."""
    fixture, _ = protocol_graph
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        create_study_epoch_codelists_ret_cat_and_lib(use_test_utils=True)
        TestUtils.create_template_parameter(settings.study_endpoint_tp_name)
        template = TestUtils.create_endpoint_template(
            name="Synthetic scoped endpoint without parameters"
        )
    fixture.own_created_nodes()
    return template.uid


@pytest.fixture
def scope_case(protocol_graph, lifecycle_client, scope_library):
    fixture, reasons = protocol_graph
    study_uid = _create_protocol_study(
        fixture, lifecycle_client, title="Synthetic scoped platform"
    )
    with fixture.principal():
        arms = [
            TestUtils.create_study_arm(
                study_uid,
                name=f"Synthetic scoped regimen {index}",
                short_name=f"S{index}",
            ).arm_uid
            for index in range(2)
        ]
        cohort = TestUtils.create_study_cohort(
            study_uid, name="Synthetic scoped cohort", short_name="SC", arm_uids=arms
        ).cohort_uid
        epochs = [
            create_study_epoch("EpochSubType_0001", study_uid=study_uid).uid
            for _ in range(2)
        ]
    return {
        "fixture": fixture,
        "reasons": reasons,
        "study_uid": study_uid,
        "template_uid": scope_library,
        "arms": arms,
        "cohort": cohort,
        "epochs": epochs,
    }


def _scope(item):
    return {field: item[field] for field in SCOPE_FIELDS}


def _create(client, case, **scope):
    return client.post(
        f"/studies/{case['study_uid']}/study-endpoints",
        params={"create_endpoint": True},
        json={
            "endpoint_data": {
                "endpoint_template_uid": case["template_uid"],
                "parameter_terms": [],
                "library_name": settings.sponsor_library_name,
            },
            **scope,
        },
    )


def _endpoint_path(case, uid):
    return f"/studies/{case['study_uid']}/study-endpoints/{uid}"


def _read(client, path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_http_scope_create_patch_lock_history_and_diff(lifecycle_client, scope_case):
    case = scope_case
    arms, cohort, epochs = case["arms"], case["cohort"], case["epochs"]
    response = _create(
        lifecycle_client,
        case,
        applies_to_arm_uids=[arms[1], arms[0]],
        applies_to_cohort_uids=[cohort],
    )
    assert response.status_code == 201, response.text
    created = response.json()
    authored = {
        "applies_to_arm_uids": sorted(arms),
        "applies_to_cohort_uids": [cohort],
        "applies_to_epoch_uids": [],
    }
    assert _scope(created) == authored
    path = _endpoint_path(case, created["study_endpoint_uid"])
    assert _scope(_read(lifecycle_client, path)) == authored

    # Omitted dimensions are preserved; [] clears only its own dimension.
    response = lifecycle_client.patch(path, json={"applies_to_epoch_uids": [epochs[0]]})
    assert response.status_code == 200, response.text
    authored["applies_to_epoch_uids"] = [epochs[0]]
    assert _scope(response.json()) == authored
    response = lifecycle_client.patch(path, json={"applies_to_arm_uids": []})
    assert response.status_code == 200, response.text
    authored["applies_to_arm_uids"] = []
    assert _scope(response.json()) == authored
    baseline = _fingerprint(case["fixture"])
    response = lifecycle_client.patch(path, json={"applies_to_cohort_uids": None})
    assert response.status_code in {400, 422}, response.text
    assert _fingerprint(case["fixture"]) == baseline
    assert _scope(_read(lifecycle_client, path)) == authored

    _lock(lifecycle_client, case["study_uid"], case["reasons"])
    _unlock(lifecycle_client, case["study_uid"], case["reasons"])
    response = lifecycle_client.patch(
        path, json={"applies_to_arm_uids": [arms[0]], "applies_to_epoch_uids": epochs}
    )
    assert response.status_code == 200, response.text
    _lock(lifecycle_client, case["study_uid"], case["reasons"])
    first = _read(lifecycle_client, path, study_value_version="1")
    assert _scope(first) == authored
    second = _read(lifecycle_client, path, study_value_version="2")
    assert _scope(second) == {
        "applies_to_arm_uids": [arms[0]],
        "applies_to_cohort_uids": [cohort],
        "applies_to_epoch_uids": sorted(epochs),
    }
    history = _read(lifecycle_client, path + "/audit-trail")
    assert [_scope(item) for item in history][-1]["applies_to_arm_uids"] == sorted(
        arms
    ), "The first revision keeps its own authored scope"
    assert any(_scope(item) == _scope(second) for item in history)

    diff = _read(lifecycle_client, f"/studies/{case['study_uid']}/versions/1/diff/2")
    changes = json.dumps(diff["changes"])
    assert "applies_to_arm_uids" in changes and "applies_to_epoch_uids" in changes


def test_http_scope_targets_must_be_current_selections_atomically(
    protocol_graph, lifecycle_client, scope_case
):
    case = scope_case
    fixture, _ = protocol_graph
    other_uid = _create_protocol_study(fixture, lifecycle_client)
    with fixture.principal():
        foreign_arm = TestUtils.create_study_arm(
            other_uid, name="Synthetic foreign scoped arm", short_name="F"
        ).arm_uid
    baseline = _fingerprint(case["fixture"])
    for scope in (
        {"applies_to_arm_uids": [foreign_arm]},
        {"applies_to_epoch_uids": ["StudyEpoch_999999"]},
        {"applies_to_cohort_uids": [case["arms"][0]]},
        {"applies_to_arm_uids": [case["arms"][0], case["arms"][0]]},
        {"applies_to_arm_uids": [" "]},
        {"applies_to_arm_uids": None},
    ):
        response = _create(lifecycle_client, case, **scope)
        assert response.status_code in {400, 422}, (scope, response.text)
        assert _fingerprint(case["fixture"]) == baseline, scope
    # The same library endpoint with a different scope is a distinct selection;
    # the same scope sets in another order are a duplicate.
    assert (
        _create(lifecycle_client, case, applies_to_arm_uids=case["arms"]).status_code
        == 201
    )
    assert (
        _create(
            lifecycle_client, case, applies_to_arm_uids=[case["arms"][0]]
        ).status_code
        == 201
    )
    duplicate = _create(lifecycle_client, case, applies_to_arm_uids=case["arms"][::-1])
    assert duplicate.status_code in {400, 409}, duplicate.text


def test_http_scoped_target_deletion_refuses_until_scope_is_cleared(
    lifecycle_client, scope_case
):
    case = scope_case
    arms, cohort, epochs = case["arms"], case["cohort"], case["epochs"]
    response = _create(
        lifecycle_client,
        case,
        applies_to_arm_uids=[arms[0]],
        applies_to_cohort_uids=[cohort],
        applies_to_epoch_uids=[epochs[0]],
    )
    assert response.status_code == 201, response.text
    path = _endpoint_path(case, response.json()["study_endpoint_uid"])
    study = f"/studies/{case['study_uid']}"
    targets = (
        ("applies_to_arm_uids", f"{study}/study-arms/{arms[0]}"),
        ("applies_to_cohort_uids", f"{study}/study-cohorts/{cohort}"),
        ("applies_to_epoch_uids", f"{study}/study-epochs/{epochs[0]}"),
    )
    for _, target in targets:
        baseline = _fingerprint(case["fixture"])
        response = lifecycle_client.delete(target)
        assert response.status_code == 400, response.text
        assert "authored scope" in response.text
        assert _fingerprint(case["fixture"]) == baseline
    for field, target in targets:
        response = lifecycle_client.patch(path, json={field: []})
        assert response.status_code == 200, response.text
        response = lifecycle_client.delete(target)
        assert response.status_code == 204, response.text
    assert _scope(_read(lifecycle_client, path)) == {
        field: [] for field in SCOPE_FIELDS
    }


@pytest.fixture(scope="module")
def family_library(protocol_graph, scope_library):
    """Approved plain objective and criteria templates for the other families."""
    # The endpoint library fixture seeds the shared epoch codelists first.
    assert scope_library
    fixture, _ = protocol_graph
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        objective = TestUtils.create_objective_template(
            name="Synthetic scoped objective without parameters"
        )
        codelist = TestUtils.create_ct_codelist(
            name="Synthetic criteria type",
            sponsor_preferred_name="Synthetic criteria type",
            catalogue_name="SDTM CT",
            submission_value="CRITRTP",
            extensible=True,
            approve=True,
        )
        inclusion = TestUtils.create_ct_term(
            sponsor_preferred_name="INCLUSION CRITERIA",
            nci_preferred_name="Inclusion Criteria",
            catalogue_name="SDTM CT",
            codelist_uid=codelist.codelist_uid,
            submission_value="Inclusion",
        )
        criteria = TestUtils.create_criteria_template(
            name="Synthetic scoped criterion without parameters",
            type_uid=inclusion.term_uid,
        )
    fixture.own_created_nodes()
    return {"objective": objective.uid, "criteria": criteria.uid}


FAMILIES = {
    "objective": {
        "collection": "study-objectives",
        "query": "create_objective",
        "data": "objective_data",
        "template": "objective_template_uid",
        "uid": "study_objective_uid",
        "patch": "",
    },
    "criteria": {
        "collection": "study-criteria",
        "query": "create_criteria",
        "data": "criteria_data",
        "template": "criteria_template_uid",
        "uid": "study_criteria_uid",
        "patch": "/applicability",
    },
}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_http_objective_and_criteria_scope_follow_the_same_rules(
    lifecycle_client, scope_case, family_library, family
):
    case, spec = scope_case, FAMILIES[family]
    arms, cohort, epochs = case["arms"], case["cohort"], case["epochs"]
    collection = f"/studies/{case['study_uid']}/{spec['collection']}"

    def create(**scope):
        return lifecycle_client.post(
            collection,
            params={spec["query"]: True},
            json={
                spec["data"]: {
                    spec["template"]: family_library[family],
                    "parameter_terms": [],
                    "library_name": settings.sponsor_library_name,
                },
                **scope,
            },
        )

    baseline = _fingerprint(case["fixture"])
    response = create(applies_to_arm_uids=["StudyArm_999999"])
    assert response.status_code == 400, response.text
    assert _fingerprint(case["fixture"]) == baseline, "No library item or audit"
    response = create(applies_to_arm_uids=[arms[1], arms[0]])
    assert response.status_code == 201, response.text
    created = response.json()
    authored = {
        "applies_to_arm_uids": sorted(arms),
        "applies_to_cohort_uids": [],
        "applies_to_epoch_uids": [],
    }
    assert _scope(created) == authored
    path = f"{collection}/{created[spec['uid']]}"
    response = lifecycle_client.patch(
        path + spec["patch"], json={"applies_to_cohort_uids": [cohort]}
    )
    assert response.status_code == 200, response.text
    authored["applies_to_cohort_uids"] = [cohort]
    assert _scope(response.json()) == authored
    baseline = _fingerprint(case["fixture"])
    response = lifecycle_client.patch(
        path + spec["patch"], json={"applies_to_epoch_uids": None}
    )
    assert response.status_code in {400, 422}, response.text
    assert _fingerprint(case["fixture"]) == baseline
    # The same library item with the same sets in another order is a duplicate;
    # dropping the cohort dimension makes a distinct scope.
    duplicate = create(applies_to_arm_uids=arms[::-1], applies_to_cohort_uids=[cohort])
    assert duplicate.status_code in {400, 409}, duplicate.text
    assert create(applies_to_arm_uids=[arms[0]]).status_code == 201

    _lock(lifecycle_client, case["study_uid"], case["reasons"])
    _unlock(lifecycle_client, case["study_uid"], case["reasons"])
    response = lifecycle_client.patch(
        path + spec["patch"],
        json={"applies_to_arm_uids": [], "applies_to_epoch_uids": [epochs[1]]},
    )
    assert response.status_code == 200, response.text
    _lock(lifecycle_client, case["study_uid"], case["reasons"])
    assert _scope(_read(lifecycle_client, path, study_value_version="1")) == authored
    assert _scope(_read(lifecycle_client, path, study_value_version="2")) == {
        "applies_to_arm_uids": [],
        "applies_to_cohort_uids": [cohort],
        "applies_to_epoch_uids": [epochs[1]],
    }
    diff = _read(lifecycle_client, f"/studies/{case['study_uid']}/versions/1/diff/2")
    assert "applies_to_epoch_uids" in json.dumps(diff["changes"])
    _unlock(lifecycle_client, case["study_uid"], case["reasons"])
    baseline = _fingerprint(case["fixture"])
    response = lifecycle_client.delete(
        f"/studies/{case['study_uid']}/study-cohorts/{cohort}"
    )
    assert response.status_code == 400, response.text
    assert _fingerprint(case["fixture"]) == baseline
