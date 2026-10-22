"""Synthetic control pools and adaptation references through HTTP (plan item O7)."""

# Pytest resolves the imported fixture functions by parameter name.
# pylint: disable=redefined-outer-name

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from neomodel import db

from clinical_mdr_api.tests.integration.services.test_authored_protocol_documents_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _create_protocol_study,
    native_visit_graph,
    protocol_client,
    protocol_graph,
)
from clinical_mdr_api.tests.integration.services.test_study_arm_lifecycle_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _current_version,
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

pytestmark = [
    pytest.mark.estate_fixture,
    pytest.mark.parametrize(
        "native_visit_graph", ["study-only"], indirect=True, scope="module"
    ),
]
DIGEST = "sha256:" + "a" * 64
MIGRATION_ROOT = Path(__file__).resolve().parents[5] / "db_schema_migration"
O7_INDEXES = [
    ["index_StudyAdaptationReference_uid", ["StudyAdaptationReference"], ["uid"]],
    ["index_StudyControlPool_uid", ["StudyControlPool"], ["uid"]],
]


def reference(collection="controlPools", entry="ControlPool_000001"):
    """Synthetic pins shaped like a CSL C6 publication entry; no fact is copied."""
    return {
        "publication_entity_id": "synthetic-csl-publication-0001",
        "publication_hash": DIGEST,
        "analysis_model_hash": "sha256:" + "b" * 64,
        "collection": collection,
        "entry_id": entry,
        "entry_hash": "sha256:" + "c" * 64,
    }


@pytest.fixture(scope="module")
def epoch_codelists(protocol_graph):
    fixture, _ = protocol_graph
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        create_study_epoch_codelists_ret_cat_and_lib(use_test_utils=True)
    fixture.own_created_nodes()


@pytest.fixture
def pool_case(protocol_graph, lifecycle_client, epoch_codelists):
    assert epoch_codelists is None
    fixture, reasons = protocol_graph
    study_uid = _create_protocol_study(
        fixture, lifecycle_client, title="Synthetic shared placebo platform"
    )
    with fixture.principal():
        arms = {
            name: TestUtils.create_study_arm(
                study_uid, name=f"Synthetic {name}", short_name=name
            ).arm_uid
            for name in ("PBO", "RA", "RB")
        }
        epoch = create_study_epoch("EpochSubType_0001", study_uid=study_uid).uid
    return {
        "fixture": fixture,
        "reasons": reasons,
        "study_uid": study_uid,
        "arms": arms,
        "epoch": epoch,
    }


def _path(case, collection, uid=None):
    path = f"/studies/{case['study_uid']}/{collection}"
    return path if uid is None else f"{path}/{uid}"


def _pool(client, case, **changes):
    return {
        "expected_study_version": _current_version(client, case["study_uid"]),
        "name": "Synthetic shared placebo pool",
        "donor_arm_uids": [case["arms"]["PBO"]],
        "focal_arm_uids": [case["arms"]["RA"]],
        "csl_reference": reference(),
        **changes,
    }


def test_http_control_pool_versions_refusals_guards_and_export(
    lifecycle_client, pool_case
):
    client, case = lifecycle_client, pool_case
    arms = case["arms"]
    pools = _path(case, "study-control-pools")
    baseline = _fingerprint(case["fixture"])
    for changes, status in (
        ({"focal_arm_uids": [arms["PBO"]]}, 400),
        ({"donor_arm_uids": ["StudyArm_999999"]}, 400),
        ({"focal_arm_uids": []}, 400),
        ({"csl_reference": reference(collection="interimAnalyses")}, 400),
        ({"csl_reference": {**reference(), "entry_hash": "abc"}}, 400),
        ({"alpha": 0.025}, 400),
        ({"expected_study_version": "1"}, 409),
    ):
        response = client.post(pools, json=_pool(client, case, **changes))
        assert response.status_code == status, (changes, response.text)
        assert _fingerprint(case["fixture"]) == baseline, changes
    response = client.post(pools, json=_pool(client, case, csl_reference=None))
    assert response.status_code == 201, response.text
    pool = response.json()
    assert pool["csl_reference"] is None and pool["reference_verified_by_osb"] is False
    response = client.put(
        _path(case, "study-control-pools", pool["uid"]),
        json=_pool(client, case),
    )
    assert response.status_code == 200, response.text
    assert response.json()["csl_reference"] == reference()

    _lock(client, case["study_uid"], case["reasons"])
    _unlock(client, case["study_uid"], case["reasons"])
    response = client.put(
        _path(case, "study-control-pools", pool["uid"]),
        json=_pool(client, case, focal_arm_uids=[arms["RB"], arms["RA"]]),
    )
    assert response.status_code == 200, response.text
    baseline = _fingerprint(case["fixture"])
    response = client.delete(f"/studies/{case['study_uid']}/study-arms/{arms['PBO']}")
    assert response.status_code == 400, response.text
    assert pool["uid"] in response.text
    assert _fingerprint(case["fixture"]) == baseline
    _lock(client, case["study_uid"], case["reasons"])
    first = client.get(pools, params={"study_value_version": "1"}).json()
    second = client.get(pools, params={"study_value_version": "2"}).json()
    assert [item["focal_arm_uids"] for item in first] == [[arms["RA"]]]
    assert [item["focal_arm_uids"] for item in second] == [
        sorted([arms["RA"], arms["RB"]])
    ]
    diff = client.get(f"/studies/{case['study_uid']}/versions/1/diff/2").json()
    assert "focal_arm_uids" in json.dumps(diff["changes"])
    # The USDM projection is checked offline: this minimal fixture cannot map
    # a complete USDM document (no authored study type, one CT package each).

    _unlock(client, case["study_uid"], case["reasons"])
    response = client.delete(
        _path(case, "study-control-pools", pool["uid"]),
        params={"expected_study_version": _current_version(client, case["study_uid"])},
    )
    assert response.status_code == 204, response.text
    assert client.get(pools).json() == []
    assert len(client.get(pools, params={"study_value_version": "2"}).json()) == 1


def test_http_adaptation_reference_pins_a_fact_and_guards_its_targets(
    lifecycle_client, pool_case
):
    client, case = lifecycle_client, pool_case
    path = _path(case, "study-adaptation-references")
    body = {
        "expected_study_version": _current_version(client, case["study_uid"]),
        "adaptation_type": "Synthetic regimen futility review",
        "csl_reference": reference("interimAnalyses", "InterimAnalysis_000001"),
        "affected_arm_uids": [case["arms"]["RA"]],
        "affected_epoch_uids": [case["epoch"]],
        "trigger_reference": "Synthetic interim 1",
    }
    baseline = _fingerprint(case["fixture"])
    for changes in (
        {"csl_reference": None},
        {"affected_epoch_uids": ["StudyEpoch_999999"]},
        {"adaptation_type": " "},
    ):
        response = client.post(path, json={**body, **changes})
        assert response.status_code in {400, 422}, (changes, response.text)
        assert _fingerprint(case["fixture"]) == baseline
    response = client.post(path, json=body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["csl_reference"]["collection"] == "interimAnalyses"
    assert created["reference_verified_by_osb"] is False
    assert client.get(path).json() == [created]
    for target in (
        f"/studies/{case['study_uid']}/study-arms/{case['arms']['RA']}",
        f"/studies/{case['study_uid']}/study-epochs/{case['epoch']}",
    ):
        baseline = _fingerprint(case["fixture"])
        response = client.delete(target)
        assert response.status_code == 400, response.text
        assert created["uid"] in response.text
        assert _fingerprint(case["fixture"]) == baseline


def _o7_indexes():
    rows, _ = db.cypher_query(
        "SHOW INDEXES YIELD name, labelsOrTypes, properties "
        "WHERE name IN $names RETURN name, labelsOrTypes, properties ORDER BY name",
        {"names": [row[0] for row in O7_INDEXES]},
    )
    return rows


def test_migration_accuratrial_012_creates_both_indexes_and_is_idempotent(
    protocol_graph,
):
    fixture, _ = protocol_graph
    db.set_connection(url=fixture.dsn)
    for name, _, _ in O7_INDEXES:
        db.cypher_query(f"DROP INDEX {name} IF EXISTS")
    assert not _o7_indexes()
    dsn = urlsplit(fixture.dsn)
    environment = {
        **os.environ,
        "DATABASE_URL": f"{dsn.scheme}://{dsn.netloc}",
        "DATABASE_NAME": dsn.path.strip("/"),
        "PYTHONPATH": str(MIGRATION_ROOT),
    }
    for _ in range(2):
        subprocess.run(
            [sys.executable, "-m", "migrations.migration_accuratrial_012"],
            cwd=MIGRATION_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            timeout=300,
        )
        db.set_connection(url=fixture.dsn)
        assert _o7_indexes() == O7_INDEXES
