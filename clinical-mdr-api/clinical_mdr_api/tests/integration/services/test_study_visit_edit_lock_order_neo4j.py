"""Actual ordinary visit edit/reorder transactions must retain sibling data."""

import hashlib
import inspect
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from neo4j import GraphDatabase
from neomodel import db

from clinical_mdr_api.domain_repositories.study_selections.study_visit_repository import (
    StudyVisitRepository,
)
from clinical_mdr_api.services.studies import study_visit as visit_module
from clinical_mdr_api.services.studies.study_visit import StudyVisitService
from clinical_mdr_api.tests.fixtures.study_visit_lock_neo4j import native_visit_graph

SIBLING_UPDATE = {
    "description": "Writer A exact V2 description; preserve during V1 reorder.",
    "min_visit_window_value": -2,
    "max_visit_window_value": 3,
    "show_visit": False,
    "is_soa_milestone": True,
    "start_rule": "Writer A start rule",
    "end_rule": "Writer A end rule",
}


class RollbackOrdinaryWriter(Exception):
    """End A's actual outer transaction without committing its visit edit."""


def physical_visits(session, study_uid):
    return [
        dict(row)
        for row in session.run(
            "MATCH (:StudyRoot {uid:$uid})-[:LATEST]->(:StudyValue)"
            "-[:HAS_STUDY_VISIT]->(visit:StudyVisit) "
            "RETURN elementId(visit) AS node, visit.uid AS uid, "
            "visit.description AS description, visit.visit_number AS visit_number, "
            "visit.unique_visit_number AS unique_visit_number, "
            "visit.visit_window_min AS min_visit_window_value, "
            "visit.visit_window_max AS max_visit_window_value, "
            "visit.show_visit AS show_visit, visit.is_soa_milestone AS is_soa_milestone, "
            "visit.start_rule AS start_rule, visit.end_rule AS end_rule "
            "ORDER BY visit.visit_number",
            uid=study_uid,
        )
    ]


@pytest.mark.parametrize(
    "commit_writer",
    [True, False],
    ids=["committed-sibling", "rolled-back-sibling-control"],
)
def test_ordinary_reorder_reads_siblings_after_the_actual_study_lock(
    native_visit_graph, commit_writer, monkeypatch
):
    fixture = native_visit_graph
    case = fixture.new_case()
    study_uid = case["study_uid"]
    first_uid, second_uid = case["first_uid"], case["second_uid"]
    before = fixture.readback(study_uid)
    assert [visit["uid"] for visit in before] == [first_uid, second_uid]
    assert [visit["order"] for visit in before] == [1, 2]
    assert all(visit["visit_class"] == "SINGLE_VISIT" for visit in before)
    assert all(visit["visit_subclass"] == "SINGLE_VISIT" for visit in before)

    first_input = case["first_input"].model_copy(
        update={
            "time_value": 10,
            "description": "Writer B reordered V1 after V2",
        }
    )
    second_input = case["second_input"].model_copy(update=SIBLING_UPDATE)
    attempted = threading.Event()
    acquired = threading.Event()
    worker_id = {}
    snapshots = []
    actual_lock = visit_module.acquire_write_lock_study_value
    actual_read = StudyVisitRepository.find_all_visits_by_study_uid

    def observe_lock(uid):
        is_reorder = threading.get_ident() == worker_id.get("value")
        if is_reorder:
            attempted.set()
        result = actual_lock(uid=uid)
        if is_reorder:
            acquired.set()
        return result

    def observe_read(_cls, study_uid, study_value_version=None):
        visits = actual_read(study_uid, study_value_version=study_value_version)
        if threading.get_ident() == worker_id.get("value"):
            snapshots.append(
                {
                    "afterLockAcquired": acquired.is_set(),
                    "visits": [
                        {
                            "uid": visit.uid,
                            "description": visit.description,
                            "show_visit": visit.show_visit,
                            "is_soa_milestone": visit.is_soa_milestone,
                            "min_visit_window_value": visit.visit_window_min,
                            "max_visit_window_value": visit.visit_window_max,
                        }
                        for visit in visits
                    ],
                }
            )
        return visits

    # Observe and delegate. Neither the lock, repository results, validation,
    # timeline nor persistence behavior is replaced with a successful stub.
    monkeypatch.setattr(visit_module, "acquire_write_lock_study_value", observe_lock)
    monkeypatch.setattr(
        StudyVisitRepository, "find_all_visits_by_study_uid", classmethod(observe_read)
    )

    def reorder():
        worker_id["value"] = threading.get_ident()
        with fixture.worker_connection():
            return StudyVisitService(study_uid).edit(study_uid, first_uid, first_input)

    parsed = urlsplit(fixture.dsn)
    evidence = {
        "case": "committed-sibling" if commit_writer else "rolled-back-sibling-control",
        "studyUid": study_uid,
        "source": str(Path(visit_module.__file__).resolve()),
        "serviceSha256": hashlib.sha256(
            Path(visit_module.__file__).read_bytes()
        ).hexdigest(),
        "editMethodSource": inspect.getsourcefile(
            inspect.unwrap(StudyVisitService.edit)
        ),
        "editMethodSha256": hashlib.sha256(
            inspect.getsource(inspect.unwrap(StudyVisitService.edit)).encode("utf-8")
        ).hexdigest(),
        "writerAInput": second_input.model_dump(mode="json"),
        "writerBInput": first_input.model_dump(mode="json"),
        "before": before,
    }
    pending = None
    with GraphDatabase.driver(
        f"bolt://{parsed.hostname}:{parsed.port}",
        auth=(parsed.username, parsed.password),
    ) as monitor, ThreadPoolExecutor(max_workers=1) as pool:
        try:
            try:
                with fixture.principal(), db.transaction:
                    first_result = StudyVisitService(study_uid).edit(
                        study_uid, second_uid, second_input
                    )
                    evidence["writerAUncommittedResponse"] = first_result.model_dump(
                        mode="json"
                    )
                    assert first_result.description == SIBLING_UPDATE["description"]
                    pending = pool.submit(reorder)
                    assert attempted.wait(
                        10
                    ), "Reorder did not reach the actual StudyRoot lock"
                    blocked = []
                    transactions = []
                    deadline = time.monotonic() + 5
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
                            if "SET sr.__WRITE_LOCK__" in row["currentQuery"]
                            and "REMOVE sr.__WRITE_LOCK__" in row["currentQuery"]
                            and "Blocked" in row["status"]
                        ]
                        if blocked:
                            break
                        assert (
                            not pending.done()
                        ), "Reorder completed while A still held the native transaction"
                        time.sleep(0.02)
                    assert (
                        len(blocked) == 1
                    ), f"No actual StudyRoot lock wait: {transactions}"
                    assert not acquired.is_set()
                    evidence["blockedOnActualStudyRoot"] = blocked
                    evidence["snapshotsWhileWriterHeldLock"] = list(snapshots)
                    if not commit_writer:
                        raise RollbackOrdinaryWriter()
            except RollbackOrdinaryWriter:
                pass

            # A has committed/rolled back. B resumes its original public call.
            reordered = pending.result(timeout=20)
            evidence["writerBResponse"] = reordered.model_dump(mode="json")
            assert acquired.is_set()
            with monitor.session(database="neo4j") as session:
                current_nodes = physical_visits(session, study_uid)
                sibling_history = [
                    dict(row)
                    for row in session.run(
                        "MATCH (visit:StudyVisit {uid:$uid}) "
                        "RETURN elementId(visit) AS node, properties(visit) AS properties "
                        "ORDER BY node",
                        uid=second_uid,
                    )
                ]

            # The service reader opens a separate connection and transaction.
            def fresh_readback():
                with fixture.worker_connection():
                    return fixture.readback(study_uid)

            current = pool.submit(fresh_readback).result(timeout=20)
            evidence.update(
                {
                    "current": current,
                    "currentPhysicalVisits": current_nodes,
                    "siblingHistory": sibling_history,
                    "repositorySnapshots": snapshots,
                }
            )
            expected_sibling = (
                SIBLING_UPDATE
                if commit_writer
                else {field: before[1][field] for field in SIBLING_UPDATE}
            )
            assert [visit["uid"] for visit in current] == [second_uid, first_uid]
            assert [visit["order"] for visit in current] == [1, 2]
            assert [visit["uid"] for visit in current_nodes] == [second_uid, first_uid]
            assert len({visit["node"] for visit in current_nodes}) == 2
            assert current[1]["time_value"] == 10
            assert current[1]["description"] == first_input.description
            actual_sibling = {field: current[0][field] for field in expected_sibling}
            actual_physical = {
                field: current_nodes[0][field] for field in expected_sibling
            }
            evidence["expectedSibling"] = expected_sibling
            evidence["actualSibling"] = actual_sibling
            evidence["siblingRetained"] = (
                actual_sibling == expected_sibling
                and actual_physical == expected_sibling
            )
            assert (
                actual_sibling == expected_sibling
            ), "Renumbering replaced A's committed sibling data"
            assert (
                actual_physical == expected_sibling
            ), "Current native StudyValue points to stale sibling data"
            if commit_writer:
                assert snapshots and all(
                    snapshot["afterLockAcquired"] for snapshot in snapshots
                )
                assert evidence["snapshotsWhileWriterHeldLock"] == []
            else:
                assert all(
                    visit["properties"].get("description")
                    != SIBLING_UPDATE["description"]
                    for visit in sibling_history
                ), "A's rolled-back edit leaked into visit history"
        finally:
            # The ordinary transaction has already exited before joining B.
            if pending is not None:
                pending.result(timeout=20)
            print(json.dumps(evidence, default=str, sort_keys=True))
