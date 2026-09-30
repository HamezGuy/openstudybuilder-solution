"""Actual Community Study/CT producers, HTTP guards and a held native TX race."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from urllib.parse import urlsplit

import pytest
from neo4j import GraphDatabase
from neomodel import db

from clinical_mdr_api.domain_repositories.study_definitions import (
    study_definition_repository_impl,
)
from clinical_mdr_api.tests.fixtures.null_adjudication_neo4j import (
    COMPANION_PATH,
    VALUE_PATH,
    graph_fingerprint,
    guarded_request,
    native_null_graph,
)


def guard(fixture, uid, request):
    return fixture.client().patch(f"/studies/{uid}/null-adjudications", json=request)


@pytest.mark.parametrize("competing", ["null", "sibling"])
def test_guard_waits_for_ordinary_patch_transaction_and_rechecks_current_pair(
    native_null_graph, competing, monkeypatch
):
    fixture = native_null_graph
    uid = fixture.new_study()
    observed = fixture.readback(uid)["current_metadata"]
    request = guarded_request(observed, fixture.terms["UNK"])
    parsed = urlsplit(fixture.dsn)
    evidence = {"case": competing, "nativeStudyUid": uid}
    pending = None
    entered_lock = threading.Event()
    ordinary_thread = threading.get_ident()
    actual_lock = study_definition_repository_impl.acquire_write_lock_study_value

    def observe_actual_lock(study_uid):
        if threading.get_ident() != ordinary_thread:
            entered_lock.set()
        return actual_lock(study_uid)

    monkeypatch.setattr(
        study_definition_repository_impl,
        "acquire_write_lock_study_value",
        observe_actual_lock,
    )
    with GraphDatabase.driver(
        f"bolt://{parsed.hostname}:{parsed.port}",
        auth=(parsed.username, parsed.password),
    ) as monitor:
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                with fixture.principal(), db.transaction:
                    metadata = (
                        {
                            "is_extension_trial_null_value_code": {
                                "term_uid": fixture.terms["NA"]
                            }
                        }
                        if competing == "null"
                        else {
                            "study_stop_rules": "Concurrent sibling from actual ordinary PATCH",
                        }
                    )
                    fixture.ordinary(uid, {"high_level_study_design": metadata})
                    after_ordinary = graph_fingerprint()
                    pending = pool.submit(guard, fixture, uid, request)
                    reached_lock = entered_lock.wait(8)
                    deadline = time.monotonic() + 3
                    blocked = []
                    transactions = []
                    while time.monotonic() < deadline:
                        with monitor.session(database="neo4j") as session:
                            transactions = [
                                dict(row)
                                for row in session.run(
                                    "SHOW TRANSACTIONS YIELD transactionId,currentQuery,status,resourceInformation "
                                    "RETURN transactionId,currentQuery,status,resourceInformation"
                                )
                            ]
                            blocked = [
                                row
                                for row in transactions
                                if "REMOVE sr.__WRITE_LOCK__" in row["currentQuery"]
                                and "Blocked" in row["status"]
                            ]
                        if blocked:
                            break
                        assert (
                            not pending.done()
                        ), "Guarded request completed while ordinary writer still held its transaction"
                        time.sleep(0.02)
                    assert (
                        reached_lock
                    ), f"Guarded HTTP request did not reach the real lock call: {transactions}"
                    assert (
                        len(blocked) == 1
                    ), f"Guarded HTTP request did not visibly block on the actual StudyRoot write lock: {transactions}"
                    evidence["blockedOnNativeStudyRoot"] = blocked
                # The ordinary native transaction has committed here. No
                # third-party blocker or unlocked preview substitutes for it.
                response = pending.result(timeout=10)
                evidence["guardStatus"] = response.status_code
                current = fixture.readback(uid)["current_metadata"][
                    "high_level_study_design"
                ]
                if competing == "null":
                    assert response.status_code == 412, response.text
                    assert (
                        current["is_extension_trial_null_value_code"]["term_uid"]
                        == fixture.terms["NA"]
                    )
                    assert current["is_extension_trial"] is None
                    assert (
                        graph_fingerprint() == after_ordinary
                    ), "Rejected guard changed native data or history"
                else:
                    assert response.status_code == 200, response.text
                    assert response.json()["preconditions_verified"] is True
                    assert (
                        current["is_extension_trial_null_value_code"]["term_uid"]
                        == fixture.terms["UNK"]
                    )
                    assert current["study_stop_rules"] == metadata["study_stop_rules"]
            finally:
                if pending is not None:
                    pending.result(timeout=10)
    print(json.dumps(evidence, default=str))


def test_ordinary_patch_before_control_can_replace_a_concurrent_null_reason(
    native_null_graph,
):
    fixture = native_null_graph
    uid = fixture.new_study()
    for flavor in ["NA", "UNK"]:
        response = fixture.client().patch(
            f"/studies/{uid}",
            json={
                "current_metadata": {
                    "high_level_study_design": {
                        "is_extension_trial_null_value_code": {
                            "term_uid": fixture.terms[flavor]
                        },
                    }
                },
            },
        )
        assert response.status_code == 200, response.text
        assert (
            fixture.readback(uid)["current_metadata"]["high_level_study_design"][
                "is_extension_trial_null_value_code"
            ]["term_uid"]
            == fixture.terms[flavor]
        )


def test_actual_batch_conflict_changes_neither_eligible_nor_conflicting_target(
    native_null_graph,
):
    fixture = native_null_graph
    uid = fixture.new_study()
    metadata = fixture.readback(uid)["current_metadata"]
    request = guarded_request(metadata, fixture.terms["UNK"])
    request["adjudications"].extend(
        guarded_request(
            metadata,
            fixture.terms["UNK"],
            "high_level_study_design.is_adaptive_design",
            "high_level_study_design.is_adaptive_design_null_value_code",
        )["adjudications"]
    )
    fixture.ordinary(
        uid,
        {
            "high_level_study_design": {
                "is_adaptive_design_null_value_code": {"term_uid": fixture.terms["NA"]}
            }
        },
    )
    before = graph_fingerprint()
    response = guard(fixture, uid, request)
    assert response.status_code == 412, response.text
    assert graph_fingerprint() == before
    readback = fixture.readback(uid)["current_metadata"]["high_level_study_design"]
    assert readback["is_extension_trial_null_value_code"] is None
    assert (
        readback["is_adaptive_design_null_value_code"]["term_uid"]
        == fixture.terms["NA"]
    )


def test_actual_guard_preserves_subpart_parent_relationship_and_siblings(
    native_null_graph,
):
    fixture = native_null_graph
    parent = fixture.new_study()
    child = fixture.new_study(parent=parent)
    fixture.ordinary(
        child,
        {"high_level_study_design": {"study_stop_rules": "Retained subpart sibling"}},
        parent=parent,
    )
    before = fixture.readback(child)
    response = guard(
        fixture,
        child,
        guarded_request(before["current_metadata"], fixture.terms["UNK"]),
    )
    assert response.status_code == 200, response.text
    after = fixture.readback(child)
    assert after["study_parent_part"] == before["study_parent_part"]
    assert child in fixture.readback(parent)["study_subpart_uids"]
    design = after["current_metadata"]["high_level_study_design"]
    assert design["study_stop_rules"] == "Retained subpart sibling"
    assert (
        design["is_extension_trial_null_value_code"]["term_uid"] == fixture.terms["UNK"]
    )


@pytest.mark.parametrize(
    "section,name,live,incorrect_observation",
    [
        ("high_level_study_design", "is_extension_trial", False, 0),
        ("study_population", "number_of_expected_subjects", 0, False),
    ],
)
def test_actual_native_false_and_zero_remain_distinct_values(
    native_null_graph, section, name, live, incorrect_observation
):
    fixture = native_null_graph
    uid = fixture.new_study()
    fixture.ordinary(uid, {section: {name: live}})
    metadata = fixture.readback(uid)["current_metadata"]
    request = guarded_request(
        metadata,
        fixture.terms["UNK"],
        f"{section}.{name}",
        f"{section}.{name}_null_value_code",
    )
    request["adjudications"][0]["expected_value"]["value"] = incorrect_observation
    before = graph_fingerprint()
    response = guard(fixture, uid, request)
    assert response.status_code == 412, response.text
    assert graph_fingerprint() == before
    current = fixture.readback(uid)["current_metadata"][section][name]
    assert type(current) is type(live) and current == live


@pytest.mark.parametrize("missing", ["expected_value", "expected_null_companion"])
def test_actual_http_missing_observation_never_writes(native_null_graph, missing):
    fixture = native_null_graph
    uid = fixture.new_study()
    request = guarded_request(
        fixture.readback(uid)["current_metadata"], fixture.terms["UNK"]
    )
    del request["adjudications"][0][missing]
    before = graph_fingerprint()
    response = guard(fixture, uid, request)
    assert response.status_code == 400, response.text
    assert response.json()["type"] == "RequestValidationError"
    assert graph_fingerprint() == before


def test_actual_old_route_set_returns_404_without_ordinary_patch(native_null_graph):
    fixture = native_null_graph
    uid = fixture.new_study()
    request = guarded_request(
        fixture.readback(uid)["current_metadata"], fixture.terms["UNK"]
    )
    before = graph_fingerprint()
    legacy = fixture.client(legacy=True)
    assert legacy.get(f"/studies/{uid}/null-adjudications").status_code == 404
    assert (
        legacy.patch(f"/studies/{uid}/null-adjudications", json=request).status_code
        == 404
    )
    assert graph_fingerprint() == before


def test_native_capability_and_real_write_role_dependency(native_null_graph):
    fixture = native_null_graph
    uid = fixture.new_study()
    capability = fixture.client().get(f"/studies/{uid}/null-adjudications")
    assert capability.status_code == 200
    assert capability.json()["atomic_preconditions"] is True
    assert len(capability.json()["fields"]) == 44
    request = guarded_request(
        fixture.readback(uid)["current_metadata"], fixture.terms["UNK"]
    )
    before = graph_fingerprint()
    response = fixture.client(roles={"Study.Read"}).patch(
        f"/studies/{uid}/null-adjudications", json=request
    )
    assert response.status_code == 403, response.text
    assert graph_fingerprint() == before


def test_actual_plural_intent_carrier_and_two_row_success(native_null_graph):
    fixture = native_null_graph
    uid = fixture.new_study()
    metadata = fixture.readback(uid)["current_metadata"]
    request = guarded_request(metadata, fixture.terms["UNK"])
    request["adjudications"].extend(
        guarded_request(
            metadata,
            fixture.terms["UNK"],
            "study_intervention.trial_intent_types_codes",
            "study_intervention.trial_intent_types_null_value_code",
        )["adjudications"]
    )
    response = guard(fixture, uid, deepcopy(request))
    assert response.status_code == 200, response.text
    assert response.json()["checked_paths"] == [
        VALUE_PATH,
        COMPANION_PATH,
        "study_intervention.trial_intent_types_codes",
        "study_intervention.trial_intent_types_null_value_code",
    ]
    current = fixture.readback(uid)["current_metadata"]
    assert (
        current["high_level_study_design"]["is_extension_trial_null_value_code"][
            "term_uid"
        ]
        == fixture.terms["UNK"]
    )
    assert (
        current["study_intervention"]["trial_intent_types_null_value_code"]["term_uid"]
        == fixture.terms["UNK"]
    )
