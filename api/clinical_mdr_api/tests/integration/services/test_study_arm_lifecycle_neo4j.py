"""Synthetic lifecycle evidence through HTTP on the owned Community fixture."""

# Pytest resolves the imported fixture functions by parameter name.
# pylint: disable=redefined-outer-name

from datetime import datetime, timezone

import pytest
from neomodel import db

from clinical_mdr_api.routers.studies import study
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from clinical_mdr_api.tests.fixtures.null_adjudication_neo4j import graph_fingerprint
from clinical_mdr_api.tests.integration.services.test_authored_protocol_documents_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _create_protocol_study,
    native_visit_graph,
    protocol_client,
    protocol_graph,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.config import settings
from common.exceptions import ValidationException

pytestmark = [
    pytest.mark.estate_fixture,
    pytest.mark.parametrize(
        "native_visit_graph", ["study-only"], indirect=True, scope="module"
    ),
]

_TARGET_COLLECTIONS = {"arm": "study-arms", "branch_arm": "study-branch-arms"}
_EVENT_FIELDS = {
    "uid",
    "study_uid",
    "target_kind",
    "target_uid",
    "lifecycle_state",
    "effective_date",
    "decision_reference",
    "change_reason",
    "recorded_at",
    "author_id",
}


@pytest.fixture
def lifecycle_client(protocol_client):
    # Keep the existing authentication, exception handlers, and connection owner.
    protocol_client.app.include_router(study.router)
    return protocol_client


@pytest.fixture
def lifecycle_case(protocol_graph, lifecycle_client):
    fixture, _ = protocol_graph
    study_uid = _create_protocol_study(
        fixture, lifecycle_client, title="Synthetic lifecycle platform"
    )
    with fixture.principal():
        arms = [
            TestUtils.create_study_arm(
                study_uid, name=f"Synthetic regimen {index}", short_name=f"R{index}"
            )
            for index in range(2)
        ]
        branches = [
            TestUtils.create_study_branch_arm(
                study_uid,
                name=f"Synthetic branch {index}",
                short_name=f"B{index}",
                study_arm_uid=arms[0].arm_uid,
            )
            for index in range(2)
        ]
    return {
        "study_uid": study_uid,
        "arm": [arm.arm_uid for arm in arms],
        "branch_arm": [branch.branch_arm_uid for branch in branches],
    }


def _target_path(case, target_kind, index=0):
    return (
        f"/studies/{case['study_uid']}/{_TARGET_COLLECTIONS[target_kind]}"
        f"/{case[target_kind][index]}"
    )


def _current_version(client, study_uid):
    response = client.get(f"/studies/{study_uid}/protocol-documents")
    assert response.status_code == 200, response.text
    return response.json()["study_value_version"]


def _event_payload(client, study_uid, **changes):
    return {
        "expected_study_version": _current_version(client, study_uid),
        "lifecycle_state": "pending external review",
        "effective_date": "2045-01-02T03:04:05+05:30",
        "decision_reference": "Synthetic committee decision 001",
        "change_reason": "Synthetic lifecycle regression",
        **changes,
    }


def _append_event(client, target_path, payload):
    response = client.post(target_path + "/lifecycle-events", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _history(client, target_path, version=None):
    params = {} if version is None else {"study_value_version": version}
    response = client.get(target_path + "/lifecycle-events", params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert isinstance(result, list), result
    return result


def _fingerprint(fixture):
    db.set_connection(url=fixture.dsn)
    return graph_fingerprint()


def _lock(client, study_uid, reasons):
    response = client.post(
        f"/studies/{study_uid}/locks",
        json={
            "change_description": "Synthetic lifecycle lock",
            "reason_for_change_uid": reasons["reason_for_lock_terms"][0].term_uid,
        },
    )
    assert response.status_code == 201, response.text
    return _current_version(client, study_uid)


def _unlock(client, study_uid, reasons):
    response = client.post(
        f"/studies/{study_uid}/unlocks",
        json={
            "reason_for_change_uid": reasons["reason_for_unlock_terms"][0].term_uid,
            "other_reason_for_unlocking": "Synthetic lifecycle amendment",
        },
    )
    assert response.status_code == 201, response.text


def _assert_selected_event_targets(fixture, case, events, version=None):
    """Each event navigates to exactly one revision within the chosen snapshot."""
    db.set_connection(url=fixture.dsn)
    selection = (
        "MATCH (:StudyRoot {uid:$study_uid})-[:LATEST]->(value:StudyValue) "
        if version is None
        else "MATCH (:StudyRoot {uid:$study_uid})"
        "-[:HAS_VERSION {version:$version}]->(value:StudyValue) "
    )
    rows, _ = db.cypher_query(
        selection + "WITH DISTINCT value "
        "MATCH (value)-[:HAS_STUDY_ARM_LIFECYCLE_EVENT]->(event:StudyArmLifecycleEvent) "
        "WHERE event.uid IN $event_uids "
        "OPTIONAL MATCH (value)-[selected]->(target)-[navigation]->(event) "
        "WHERE (event.target_kind='arm' AND target:StudyArm "
        "AND type(selected)='HAS_STUDY_ARM' "
        "AND type(navigation)='STUDY_ARM_HAS_LIFECYCLE_EVENT') "
        "OR (event.target_kind='branch_arm' AND target:StudyBranchArm "
        "AND type(selected)='HAS_STUDY_BRANCH_ARM' "
        "AND type(navigation)='STUDY_BRANCH_ARM_HAS_LIFECYCLE_EVENT') "
        "RETURN event.uid, count(navigation), collect(target.uid) ORDER BY event.uid",
        {
            "study_uid": case["study_uid"],
            "version": version,
            "event_uids": [event["uid"] for event in events],
        },
    )
    assert rows == sorted([event["uid"], 1, [event["target_uid"]]] for event in events)


@pytest.mark.parametrize("target_kind", ["arm", "branch_arm"])
def test_http_lifecycle_records_authored_dates_and_unknown_history(
    protocol_graph, lifecycle_client, lifecycle_case, target_kind
):
    fixture, _ = protocol_graph
    path = _target_path(lifecycle_case, target_kind)
    # No authored event means no supplied lifecycle state, including for a sibling.
    assert _history(lifecycle_client, path) == []
    events = []
    for index, (state, effective_date) in enumerate(
        [
            ("future closure proposal", "2045-01-02T03:04:05+05:30"),
            ("enrollment opened", "2001-01-02T03:04:05Z"),
            ("pending external review", "2001-01-02T03:04:05Z"),
        ]
    ):
        payload = _event_payload(
            lifecycle_client,
            lifecycle_case["study_uid"],
            lifecycle_state=state,
            effective_date=effective_date,
            decision_reference=f"Synthetic committee decision {index}",
        )
        before = datetime.now(timezone.utc)
        event = _append_event(lifecycle_client, path, payload)
        after = datetime.now(timezone.utc)
        assert set(event) == _EVENT_FIELDS
        assert event["uid"]
        assert event["study_uid"] == lifecycle_case["study_uid"]
        assert event["target_kind"] == target_kind
        assert event["target_uid"] == lifecycle_case[target_kind][0]
        for field in ("lifecycle_state", "decision_reference", "change_reason"):
            assert event[field] == payload[field]
        assert datetime.fromisoformat(
            event["effective_date"]
        ) == datetime.fromisoformat(effective_date)
        assert before <= datetime.fromisoformat(event["recorded_at"]) <= after
        assert event["author_id"] == fixture.auth.user.id()
        events.append(event)
    expected = sorted(
        events,
        key=lambda event: (
            datetime.fromisoformat(event["effective_date"]),
            datetime.fromisoformat(event["recorded_at"]),
            event["uid"],
        ),
    )
    assert _history(lifecycle_client, path) == expected
    assert _history(lifecycle_client, path) == expected
    assert (
        _history(lifecycle_client, _target_path(lifecycle_case, target_kind, 1)) == []
    )
    other_kind = "branch_arm" if target_kind == "arm" else "arm"
    assert _history(lifecycle_client, _target_path(lifecycle_case, other_kind)) == []
    _assert_selected_event_targets(fixture, lifecycle_case, events)


@pytest.mark.parametrize("target_kind", ["arm", "branch_arm"])
def test_http_lifecycle_rejects_invalid_authored_payload_without_writes(
    protocol_graph, lifecycle_client, lifecycle_case, target_kind
):
    fixture, _ = protocol_graph
    path = _target_path(lifecycle_case, target_kind)
    payload = _event_payload(lifecycle_client, lifecycle_case["study_uid"])
    invalid_changes = [
        {"effective_date": "2026-10-21T12:00:00"},
        {"lifecycle_state": " \t "},
        {"decision_reference": " \t "},
        {"change_reason": " \t "},
        {"expected_study_version": " \t "},
        {"effective_date": None},
        {"operational_status": "active"},
    ]
    baseline = _fingerprint(fixture)
    for changes in invalid_changes:
        response = lifecycle_client.post(
            path + "/lifecycle-events", json={**payload, **changes}
        )
        assert response.status_code == 400, (changes, response.text)
        assert _fingerprint(fixture) == baseline, changes
    missing_reference = {
        key: value for key, value in payload.items() if key != "decision_reference"
    }
    response = lifecycle_client.post(path + "/lifecycle-events", json=missing_reference)
    assert response.status_code == 400, response.text
    assert _fingerprint(fixture) == baseline
    assert _history(lifecycle_client, path) == []


@pytest.mark.parametrize("target_kind", ["arm", "branch_arm"])
def test_http_lifecycle_rejects_foreign_missing_and_wrong_kind_targets(
    protocol_graph, lifecycle_client, lifecycle_case, target_kind
):
    fixture, _ = protocol_graph
    other_study = _create_protocol_study(
        fixture, lifecycle_client, title="Synthetic other lifecycle platform"
    )
    payload = _event_payload(lifecycle_client, lifecycle_case["study_uid"])
    kind_path = _TARGET_COLLECTIONS[target_kind]
    target_uid = lifecycle_case[target_kind][0]
    other_kind = "branch_arm" if target_kind == "arm" else "arm"
    paths = [
        f"/studies/{other_study}/{kind_path}/{target_uid}",
        f"/studies/{lifecycle_case['study_uid']}/{kind_path}/{lifecycle_case[other_kind][0]}",
        f"/studies/{lifecycle_case['study_uid']}/{kind_path}/SyntheticMissingTarget",
        f"/studies/SyntheticMissingStudy/{kind_path}/{target_uid}",
    ]
    baseline = _fingerprint(fixture)
    for path in paths:
        response = lifecycle_client.get(path + "/lifecycle-events")
        assert response.status_code == 404, (path, response.text)
        response = lifecycle_client.post(path + "/lifecycle-events", json=payload)
        assert response.status_code == 404, (path, response.text)
        assert _fingerprint(fixture) == baseline, path
    for version, status in [("999999", 404), ("SyntheticMissingVersion", 400)]:
        response = lifecycle_client.get(
            _target_path(lifecycle_case, target_kind) + "/lifecycle-events",
            params={"study_value_version": version},
        )
        assert response.status_code == status, (version, response.text)
        assert _fingerprint(fixture) == baseline


@pytest.mark.parametrize("target_kind", ["arm", "branch_arm"])
def test_http_lifecycle_rejects_stale_and_locked_writes(
    protocol_graph, lifecycle_client, lifecycle_case, target_kind
):
    fixture, reasons = protocol_graph
    path = _target_path(lifecycle_case, target_kind)
    payload = _event_payload(lifecycle_client, lifecycle_case["study_uid"])
    event = _append_event(lifecycle_client, path, payload)
    baseline = _fingerprint(fixture)
    response = lifecycle_client.post(
        path + "/lifecycle-events",
        json={**payload, "expected_study_version": "SyntheticStaleVersion"},
    )
    assert response.status_code == 409, response.text
    assert _fingerprint(fixture) == baseline
    locked_version = _lock(lifecycle_client, lifecycle_case["study_uid"], reasons)
    baseline = _fingerprint(fixture)
    response = lifecycle_client.post(
        path + "/lifecycle-events",
        json={**payload, "expected_study_version": locked_version},
    )
    assert response.status_code == 400, response.text
    assert _fingerprint(fixture) == baseline
    assert _history(lifecycle_client, path) == [event]
    assert _history(lifecycle_client, path, locked_version) == [event]


@pytest.mark.parametrize("target_kind", ["arm", "branch_arm"])
def test_http_lifecycle_unlock_append_edit_and_reorder_preserve_locked_history(
    protocol_graph, lifecycle_client, lifecycle_case, target_kind
):
    fixture, reasons = protocol_graph
    path = _target_path(lifecycle_case, target_kind)
    first = _append_event(
        lifecycle_client,
        path,
        _event_payload(lifecycle_client, lifecycle_case["study_uid"]),
    )
    locked_version = _lock(lifecycle_client, lifecycle_case["study_uid"], reasons)
    response = lifecycle_client.get(
        path, params={"study_value_version": locked_version}
    )
    assert response.status_code == 200, response.text
    locked_target = response.json()
    _unlock(lifecycle_client, lifecycle_case["study_uid"], reasons)
    assert _history(lifecycle_client, path) == [first]
    # The target still shares its locked revision here. The new event must be
    # attached only to the draft before an ordinary arm/branch edit happens.
    second = _append_event(
        lifecycle_client,
        path,
        _event_payload(
            lifecycle_client,
            lifecycle_case["study_uid"],
            lifecycle_state="closed by synthetic committee",
            effective_date="2046-01-01T00:00:00Z",
            decision_reference="Synthetic committee decision 002",
        ),
    )
    assert _history(lifecycle_client, path, locked_version) == [first]
    _assert_selected_event_targets(fixture, lifecycle_case, [first, second])
    for suffix, changes in [
        (
            "",
            {"name": "Amended synthetic target", "description": "Synthetic amendment"},
        ),
        ("/order", {"new_order": 2}),
        ("", {"description": "Second synthetic amendment"}),
    ]:
        response = lifecycle_client.patch(path + suffix, json=changes)
        assert response.status_code == 200, response.text
        assert _history(lifecycle_client, path) == [first, second]
        assert _history(lifecycle_client, path, locked_version) == [first]
        _assert_selected_event_targets(fixture, lifecycle_case, [first, second])
        _assert_selected_event_targets(fixture, lifecycle_case, [first], locked_version)
    response = lifecycle_client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["order"] == 2
    assert response.json()["name"] == "Amended synthetic target"
    response = lifecycle_client.get(
        path, params={"study_value_version": locked_version}
    )
    assert response.status_code == 200, response.text
    for field in ("name", "short_name", "description", "order"):
        assert response.json()[field] == locked_target[field]


@pytest.mark.parametrize("deletion", ["arm", "branch_arm", "parent_arm", "cohort"])
def test_http_lifecycle_history_refuses_deletion_and_cascades_atomically(
    protocol_graph, lifecycle_client, lifecycle_case, deletion
):
    fixture, _ = protocol_graph
    target_kind = "arm" if deletion == "arm" else "branch_arm"
    target_index = 1 if deletion in ("parent_arm", "cohort") else 0
    path = _target_path(lifecycle_case, target_kind, target_index)
    delete_path = path
    params = {}
    if deletion == "parent_arm":
        # The first branch can be removed before the second branch's guard fails.
        delete_path = _target_path(lifecycle_case, "arm")
    elif deletion == "cohort":
        with fixture.principal():
            cohort = TestUtils.create_study_cohort(
                lifecycle_case["study_uid"],
                name="Synthetic linked cohort",
                short_name="SC",
                branch_arm_uids=lifecycle_case["branch_arm"],
            )
        delete_path = (
            f"/studies/{lifecycle_case['study_uid']}/study-cohorts/{cohort.cohort_uid}"
        )
        params = {"delete_linked_branches": True}
    event = _append_event(
        lifecycle_client,
        path,
        _event_payload(lifecycle_client, lifecycle_case["study_uid"]),
    )
    baseline = _fingerprint(fixture)
    response = lifecycle_client.delete(delete_path, params=params)
    assert response.status_code == 400, response.text
    assert _fingerprint(fixture) == baseline
    assert _history(lifecycle_client, path) == [event]
    # The rejected parent/cohort operation must also keep unprotected siblings.
    for kind in ("arm", "branch_arm"):
        for index in range(2):
            response = lifecycle_client.get(_target_path(lifecycle_case, kind, index))
            assert response.status_code == 200, response.text
    if deletion == "cohort":
        response = lifecycle_client.get(delete_path)
        assert response.status_code == 200, response.text
    _assert_selected_event_targets(fixture, lifecycle_case, [event])


@pytest.mark.parametrize("target_kind", ["arm", "branch_arm"])
def test_http_lifecycle_enforces_roles_and_delegated_study_visibility(
    protocol_graph, lifecycle_client, lifecycle_case, target_kind, monkeypatch
):
    fixture, _ = protocol_graph
    path = _target_path(lifecycle_case, target_kind)
    payload = _event_payload(lifecycle_client, lifecycle_case["study_uid"])
    event = _append_event(lifecycle_client, path, payload)
    assert (
        settings.oauth_rbac_enabled
    ), "This fixture requires the real RBAC dependencies"
    baseline = _fingerprint(fixture)
    with monkeypatch.context() as scoped:
        scoped.setattr(fixture.auth.user, "roles", {"Study.Read"})
        assert _history(lifecycle_client, path) == [event]
        response = lifecycle_client.post(path + "/lifecycle-events", json=payload)
        assert response.status_code == 403, response.text
        scoped.setattr(fixture.auth.user, "roles", {"Study.Write"})
        response = lifecycle_client.get(path + "/lifecycle-events")
        assert response.status_code == 403, response.text
    assert _fingerprint(fixture) == baseline
    # Exercise production visibility against the actual native tenant binding.
    # Authentication uses the fixture principal, without JWT transport or mocks
    # of the authorization dependencies being tested.
    with monkeypatch.context() as strict:
        strict.setattr(settings, "delegated_claims_required", True)
        strict.setattr(fixture.auth.user, "purpose", "interactive-domain-access")
        strict.setattr(fixture.auth.user, "capabilities", {"study:read", "study:write"})
        strict.setattr(fixture.auth.user, "roles", {"Study.Read", "Study.Write"})
        assert _history(lifecycle_client, path) == [event]
        for field, value, status in [
            ("study_ids", set(), 404),
            ("tenant_id", "synthetic-unrelated-tenant", 404),
            ("capabilities", set(), 403),
            ("purpose", "", 403),
        ]:
            with monkeypatch.context() as denied:
                denied.setattr(fixture.auth.user, field, value)
                response = lifecycle_client.get(path + "/lifecycle-events")
                assert response.status_code == status, (field, response.text)
                response = lifecycle_client.post(
                    path + "/lifecycle-events", json=payload
                )
                assert response.status_code == status, (field, response.text)
                assert _fingerprint(fixture) == baseline, field
    assert _history(lifecycle_client, path) == [event]


@pytest.mark.parametrize(
    "corruption",
    [
        "orphan-target",
        "duplicate-event-uid",
        "foreign-audit-owner",
        "duplicate-audit-owner",
        "other-target-orphan",
    ],
)
def test_http_lifecycle_history_restored_after_native_reader_corruption_guard(
    protocol_graph, lifecycle_client, lifecycle_case, corruption
):
    """Use HTTP before/after rollback and the native service during corruption.

    The read guard runs in the corruption transaction on the same connection.
    A separate HTTP connection could not observe these uncommitted mutations.
    """
    fixture, _ = protocol_graph
    path = _target_path(lifecycle_case, "arm")
    other_path = _target_path(lifecycle_case, "branch_arm")
    payload = _event_payload(lifecycle_client, lifecycle_case["study_uid"])
    first = _append_event(lifecycle_client, path, payload)
    other = _append_event(lifecycle_client, other_path, payload)
    assert _history(lifecycle_client, path) == [first]
    assert _history(lifecycle_client, other_path) == [other]
    baseline = _fingerprint(fixture)
    event_uid = other["uid"] if corruption == "other-target-orphan" else first["uid"]
    match = (
        "MATCH (root:StudyRoot {uid:$study_uid})-[:LATEST]->(value:StudyValue)"
        "-[:HAS_STUDY_ARM_LIFECYCLE_EVENT]->(event:StudyArmLifecycleEvent {uid:$event_uid}) "
    )
    if corruption in ("orphan-target", "other-target-orphan"):
        mutation = (
            "MATCH ()-[navigation:STUDY_ARM_HAS_LIFECYCLE_EVENT|STUDY_BRANCH_ARM_HAS_LIFECYCLE_EVENT]->(event) "
            "DELETE navigation RETURN count(navigation)"
        )
        message = "Lifecycle event target or study version ownership is inconsistent."
    elif corruption == "duplicate-event-uid":
        mutation = (
            "MATCH (root)-[:AUDIT_TRAIL]->(action:Create)-[:AFTER]->(event), "
            "(target:StudyArm)-[:STUDY_ARM_HAS_LIFECYCLE_EVENT]->(event) "
            "CREATE (duplicate:StudyArmLifecycleEvent:StudySelection) "
            "SET duplicate = properties(event) "
            "CREATE (value)-[:HAS_STUDY_ARM_LIFECYCLE_EVENT]->(duplicate), "
            "(target)-[:STUDY_ARM_HAS_LIFECYCLE_EVENT]->(duplicate), "
            "(action)-[:AFTER]->(duplicate) RETURN count(duplicate)"
        )
        message = (
            "The selected study value contains duplicate lifecycle event identities."
        )
    else:
        mutation = "MATCH (root)-[:AUDIT_TRAIL]->(action:Create)-[:AFTER]->(event) "
        if corruption == "foreign-audit-owner":
            mutation += (
                "CREATE (foreign:StudyRoot {uid:$foreign_uid})"
                "-[:AUDIT_TRAIL]->(action) RETURN count(foreign)"
            )
        else:
            mutation += "CREATE (root)-[duplicate:AUDIT_TRAIL]->(action) RETURN count(duplicate)"
        message = "Lifecycle event must retain exactly one native Create audit record."
    with fixture.principal():
        with pytest.raises(RuntimeError, match="rollback lifecycle corruption"):
            with db.transaction:
                rows, _ = db.cypher_query(
                    match + mutation,
                    {
                        "study_uid": lifecycle_case["study_uid"],
                        "event_uid": event_uid,
                        "foreign_uid": lifecycle_case["study_uid"] + "-foreign-audit",
                    },
                )
                assert rows == [[1]]
                corrupted = graph_fingerprint()
                assert corrupted != baseline
                with pytest.raises(ValidationException) as rejection:
                    StudyArmSelectionService().get_lifecycle_events(
                        study_uid=lifecycle_case["study_uid"],
                        target_kind="arm",
                        target_uid=lifecycle_case["arm"][0],
                    )
                assert rejection.type is ValidationException
                assert rejection.value.msg == message
                assert graph_fingerprint() == corrupted
                raise RuntimeError("rollback lifecycle corruption")
    assert _fingerprint(fixture) == baseline
    assert _history(lifecycle_client, path) == [first]
    assert _history(lifecycle_client, other_path) == [other]
