"""Synthetic single-transaction regimen add through HTTP on the owned graph."""

# Pytest resolves the imported fixture functions by parameter name.
# pylint: disable=redefined-outer-name

import pytest
from neomodel import db

from clinical_mdr_api.tests.integration.services.test_authored_protocol_documents_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _author_first_protocol_documents,
    _create_protocol_study,
    native_visit_graph,
    protocol_client,
    protocol_graph,
)
from clinical_mdr_api.tests.integration.services.test_study_arm_lifecycle_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _current_version,
    _fingerprint,
    _lock,
    lifecycle_client,
)
from clinical_mdr_api.tests.integration.utils.factory_epoch import (
    create_study_epoch,
    create_study_epoch_codelists_ret_cat_and_lib,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils

pytestmark = [
    pytest.mark.estate_fixture,
    pytest.mark.parametrize(
        "native_visit_graph", ["study-only"], indirect=True, scope="module"
    ),
]


@pytest.fixture(scope="module")
def regimen_library(protocol_graph):
    """Approved library compound used by every synthetic regimen in this module."""
    fixture, _ = protocol_graph
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        create_study_epoch_codelists_ret_cat_and_lib(use_test_utils=True)
        compound = TestUtils.create_compound(name="Synthetic regimen H", approve=True)
        alias = TestUtils.create_compound_alias(
            name="Synthetic regimen H alias",
            compound_uid=compound.uid,
            is_preferred_synonym=True,
            approve=True,
        )
        product = TestUtils.create_medicinal_product(
            name="Synthetic regimen H product",
            compound_uid=compound.uid,
            approve=True,
        )
    fixture.own_created_nodes()
    return {"compound_alias_uid": alias.uid, "medicinal_product_uid": product.uid}


@pytest.fixture
def platform_case(protocol_graph, lifecycle_client, regimen_library):
    """A platform draft with a master bundle, one existing arm and one epoch."""
    fixture, reasons = protocol_graph
    study_uid = _create_protocol_study(
        fixture, lifecycle_client, title="Synthetic regimen platform"
    )
    content, content_hash = _author_first_protocol_documents(
        fixture, lifecycle_client, study_uid
    )
    with fixture.principal():
        epoch = create_study_epoch("EpochSubType_0001", study_uid=study_uid)
        placebo = TestUtils.create_study_element(
            study_uid, name="Synthetic shared placebo", short_name="PBO"
        )
    return {
        "fixture": fixture,
        "reasons": reasons,
        "study_uid": study_uid,
        "content": content,
        "content_hash": content_hash,
        "existing_arm_uids": list(content.bindings[0].arm_uids),
        "epoch_uid": epoch.uid,
        "placebo_element_uid": placebo.element_uid,
        **regimen_library,
    }


def _local(key):
    return {"local_key": key}


def _regimen(client, case, **changes):
    content = case["content"].model_dump(mode="json")
    master = content["bindings"][0]
    master["arm_uids"] += ["token-h-active", "token-h-placebo"]
    appendix = dict(content["bindings"][1])
    appendix.update(document_id="authored-arm-h", arm_uids=["token-h-active"])
    content["bindings"].append(appendix)
    document = dict(content["documents"][1])
    document = {
        **document,
        "id": "authored-arm-h",
        "name": "Synthetic regimen H appendix",
        "versions": [
            {
                **version,
                "id": "authored-arm-h-version-1",
                "contents": [
                    {
                        **section,
                        "id": section["id"].replace("authored-arm-0", "authored-arm-h"),
                        "name": section["name"].replace(
                            "authored-arm-0", "authored-arm-h"
                        ),
                        "contentItemId": section["contentItemId"].replace(
                            "authored-arm-0", "authored-arm-h"
                        ),
                    }
                    for section in version["contents"]
                ],
            }
            for version in document["versions"]
        ],
        "language": {**document["language"], "id": "authored-arm-h-language"},
        "type": {**document["type"], "id": "authored-arm-h-type"},
    }
    document["versions"][0]["status"] = {
        **document["versions"][0]["status"],
        "id": "authored-arm-h-status",
    }
    content["documents"].append(document)
    content["documents"][0]["childIds"].append("authored-arm-h")
    for item in list(content["narrative_content_items"]):
        if item["id"].startswith("authored-arm-0-"):
            content["narrative_content_items"].append(
                {
                    **item,
                    "id": item["id"].replace("authored-arm-0", "authored-arm-h"),
                    "name": item["name"].replace("authored-arm-0", "authored-arm-h"),
                    "text": item["text"].replace("authored-arm-0", "authored-arm-h"),
                }
            )
    for disposition in list(content["section_dispositions"]):
        if disposition["content_id"].startswith("authored-arm-0-"):
            content["section_dispositions"].append(
                {
                    **disposition,
                    "content_id": disposition["content_id"].replace(
                        "authored-arm-0", "authored-arm-h"
                    ),
                }
            )
    payload = {
        "expected_study_version": _current_version(client, case["study_uid"]),
        "arms": [
            {"local_key": "h-active", "name": "Regimen H active", "short_name": "HA"},
            {"local_key": "h-placebo", "name": "Regimen H placebo", "short_name": "HP"},
        ],
        "branch_arms": [
            {
                "local_key": "h-active-branch",
                "name": "Regimen H active branch",
                "short_name": "HAB",
                "arm_uid": _local("h-active"),
            }
        ],
        "cohorts": [
            {
                "local_key": "h-regimen",
                "name": "Regimen H",
                "short_name": "HC",
                "arm_uids": [_local("h-placebo")],
                "branch_arm_uids": [_local("h-active-branch")],
            },
            {
                "local_key": "shared-placebo",
                "name": "Synthetic shared placebo pool",
                "short_name": "SPP",
                "arm_uids": [case["existing_arm_uids"][0], _local("h-placebo")],
            },
        ],
        "elements": [
            {"local_key": "h-drug", "name": "Regimen H drug", "short_name": "HD"}
        ],
        "compounds": [
            {
                "local_key": "h-compound",
                "compound_alias_uid": case["compound_alias_uid"],
                "medicinal_product_uid": case["medicinal_product_uid"],
            }
        ],
        "compound_dosings": [
            {
                "local_key": "h-dose",
                "study_compound_uid": _local("h-compound"),
                "study_element_uid": _local("h-drug"),
            }
        ],
        "design_cells": [
            {
                "local_key": "h-active-treatment",
                "study_branch_arm_uid": _local("h-active-branch"),
                "study_epoch_uid": case["epoch_uid"],
                "study_element_uid": _local("h-drug"),
                "transition_rule": "Synthetic authored regimen H transition",
            },
            {
                "local_key": "h-placebo-treatment",
                "study_arm_uid": _local("h-placebo"),
                "study_epoch_uid": case["epoch_uid"],
                "study_element_uid": case["placebo_element_uid"],
            },
        ],
        "lifecycle_events": [
            {
                "target_kind": "arm",
                "target": _local("h-active"),
                "lifecycle_state": "Synthetic entering",
                "effective_date": "2046-03-04T05:06:07+00:00",
                "decision_reference": "Synthetic committee decision H",
                "change_reason": "Synthetic regimen onboarding",
            }
        ],
        "protocol_documents": {
            "expected_content_hash": case["content_hash"],
            "reason": "Synthetic regimen H appendix",
            "arm_tokens": {
                "token-h-active": "h-active",
                "token-h-placebo": "h-placebo",
            },
            "content": content,
        },
    }
    payload.update(changes)
    return payload


def _post(client, study_uid, payload):
    return client.post(f"/studies/{study_uid}/regimens", json=payload)


def _value_rows(study_uid, query, **params):
    rows, _ = db.cypher_query(
        "MATCH (:StudyRoot {uid:$study_uid})-[:LATEST]->(value:StudyValue) " + query,
        {"study_uid": study_uid, **params},
    )
    return rows


def test_http_regimen_add_creates_every_native_record_once(
    lifecycle_client, platform_case
):
    case = platform_case
    study_uid = case["study_uid"]
    response = _post(lifecycle_client, study_uid, _regimen(lifecycle_client, case))
    assert response.status_code == 201, response.text
    created = response.json()
    arms, branches = created["arms"], created["branch_arms"]
    assert set(arms) == {"h-active", "h-placebo"}
    assert created["study_version"] == _current_version(lifecycle_client, study_uid)
    assert len(created["lifecycle_event_uids"]) == 1

    db.set_connection(url=case["fixture"].dsn)
    assert _value_rows(
        study_uid,
        "MATCH (value)-[:HAS_STUDY_ARM]->(arm:StudyArm) WHERE arm.uid IN $uids "
        "RETURN arm.uid, arm.name ORDER BY arm.name",
        uids=list(arms.values()),
    ) == [
        [arms["h-active"], "Regimen H active"],
        [arms["h-placebo"], "Regimen H placebo"],
    ]
    assert _value_rows(
        study_uid,
        "MATCH (value)-[:HAS_STUDY_ARM]->(arm:StudyArm)-[:STUDY_ARM_HAS_BRANCH_ARM]->"
        "(branch:StudyBranchArm)<-[:HAS_STUDY_BRANCH_ARM]-(value) "
        "RETURN arm.uid, branch.uid",
    ) == [[arms["h-active"], branches["h-active-branch"]]]
    cohort_rows = _value_rows(
        study_uid,
        "MATCH (value)-[:HAS_STUDY_COHORT]->(cohort:StudyCohort) "
        "OPTIONAL MATCH (value)-[:HAS_STUDY_ARM]->(arm:StudyArm)-[:STUDY_ARM_HAS_COHORT]->(cohort) "
        "OPTIONAL MATCH (value)-[:HAS_STUDY_BRANCH_ARM]->(branch:StudyBranchArm)"
        "-[:STUDY_BRANCH_ARM_HAS_COHORT]->(cohort) "
        "RETURN cohort.uid, collect(DISTINCT arm.uid), collect(DISTINCT branch.uid)",
    )
    cohorts = {
        uid: (sorted(arm_uids), branch_uids)
        for uid, arm_uids, branch_uids in cohort_rows
    }
    assert cohorts == {
        created["cohorts"]["h-regimen"]: (
            [arms["h-placebo"]],
            [branches["h-active-branch"]],
        ),
        created["cohorts"]["shared-placebo"]: (
            sorted([case["existing_arm_uids"][0], arms["h-placebo"]]),
            [],
        ),
    }
    assert _value_rows(
        study_uid,
        "MATCH (value)-[:HAS_STUDY_COMPOUND]->(compound:StudyCompound)"
        "-[:STUDY_COMPOUND_HAS_COMPOUND_DOSING]->(dosing:StudyCompoundDosing)"
        "<-[:STUDY_ELEMENT_HAS_COMPOUND_DOSING]-(element:StudyElement)"
        "<-[:HAS_STUDY_ELEMENT]-(value) "
        "WHERE (value)-[:HAS_STUDY_COMPOUND_DOSING]->(dosing) "
        "RETURN compound.uid, dosing.uid, element.uid",
    ) == [
        [
            created["compounds"]["h-compound"],
            created["compound_dosings"]["h-dose"],
            created["elements"]["h-drug"],
        ]
    ]
    response = lifecycle_client.get(f"/studies/{study_uid}/study-design-cells")
    assert response.status_code == 200, response.text
    cells = {cell["design_cell_uid"]: cell for cell in response.json()}
    active = cells[created["design_cells"]["h-active-treatment"]]
    assert active["study_branch_arm_uid"] == branches["h-active-branch"]
    assert active["study_epoch_uid"] == case["epoch_uid"]
    assert active["study_element_uid"] == created["elements"]["h-drug"]
    assert active["transition_rule"] == "Synthetic authored regimen H transition"
    placebo = cells[created["design_cells"]["h-placebo-treatment"]]
    assert placebo["study_arm_uid"] == arms["h-placebo"]
    assert placebo["study_element_uid"] == case["placebo_element_uid"]
    assert len(cells) == 2

    response = lifecycle_client.get(
        f"/studies/{study_uid}/study-arms/{arms['h-active']}/lifecycle-events"
    )
    assert response.status_code == 200, response.text
    assert [event["uid"] for event in response.json()] == created[
        "lifecycle_event_uids"
    ]
    response = lifecycle_client.get(f"/studies/{study_uid}/protocol-documents")
    assert response.status_code == 200, response.text
    documents = response.json()
    assert documents["content_hash"] == created["protocol_documents_content_hash"]
    bindings = {
        binding["document_id"]: binding["arm_uids"]
        for binding in documents["content"]["bindings"]
    }
    assert bindings["authored-master"] == [
        *case["existing_arm_uids"],
        arms["h-active"],
        arms["h-placebo"],
    ]
    assert bindings["authored-arm-h"] == [arms["h-active"]]
    assert "token-h-active" not in response.text

    # Every record is ordinary draft content: the next lock retains it all.
    _lock(lifecycle_client, study_uid, case["reasons"])
    db.set_connection(url=case["fixture"].dsn)
    assert _value_rows(
        study_uid,
        "MATCH (value)-[:HAS_STUDY_ARM]->(arm:StudyArm) WHERE arm.uid IN $uids "
        "RETURN count(arm)",
        uids=list(arms.values()),
    ) == [[2]]


@pytest.mark.parametrize(
    "failure", ["missing-epoch", "stale-document-hash", "foreign-existing-arm"]
)
def test_http_late_regimen_failure_rolls_back_every_write(
    lifecycle_client, platform_case, failure
):
    case = platform_case
    study_uid = case["study_uid"]
    payload = _regimen(lifecycle_client, case)
    if failure == "missing-epoch":
        payload["design_cells"][-1]["study_epoch_uid"] = "StudyEpoch_999999"
    elif failure == "stale-document-hash":
        payload["protocol_documents"]["expected_content_hash"] = "0" * 64
    else:
        payload["cohorts"][1]["arm_uids"][0] = "StudyArm_999999"
    baseline = _fingerprint(case["fixture"])
    response = _post(lifecycle_client, study_uid, payload)
    # The canonical service that refuses chooses the client error code.
    # Rollback of every earlier write is the property under test.
    assert response.status_code in {400, 404, 409}, response.text
    assert _fingerprint(case["fixture"]) == baseline, failure
    retry = _post(lifecycle_client, study_uid, _regimen(lifecycle_client, case))
    assert retry.status_code == 201, retry.text


def test_http_regimen_refuses_stale_locked_or_read_only_callers_without_writes(
    lifecycle_client, platform_case, monkeypatch
):
    case = platform_case
    study_uid = case["study_uid"]
    payload = _regimen(lifecycle_client, case)
    baseline = _fingerprint(case["fixture"])
    stale = {**payload, "expected_study_version": "1"}
    response = _post(lifecycle_client, study_uid, stale)
    assert response.status_code == 409, response.text
    with monkeypatch.context() as scoped:
        scoped.setattr(case["fixture"].auth.user, "roles", {"Study.Read"})
        response = _post(lifecycle_client, study_uid, payload)
        assert response.status_code == 403, response.text
    assert _fingerprint(case["fixture"]) == baseline
    _lock(lifecycle_client, study_uid, case["reasons"])
    locked = _fingerprint(case["fixture"])
    response = _post(lifecycle_client, study_uid, payload)
    assert response.status_code == 400, response.text
    assert _fingerprint(case["fixture"]) == locked
