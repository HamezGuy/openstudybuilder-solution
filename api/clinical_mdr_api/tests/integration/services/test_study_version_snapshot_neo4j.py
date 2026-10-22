"""Synthetic amendment and exact-version evidence on the owned Community graph."""

# Pytest discovers imported fixtures by their parameter names.
# pylint: disable=redefined-outer-name

import json

import pytest
from neomodel import db

from clinical_mdr_api.services.studies.study_version_snapshot import (
    StudyVersionSnapshotService,
)
from clinical_mdr_api.tests.fixtures.null_adjudication_neo4j import graph_fingerprint
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
    _unlock,
    lifecycle_client,
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


@pytest.fixture
def version_case(protocol_graph, lifecycle_client):
    fixture, _ = protocol_graph
    study_uid = _create_protocol_study(
        fixture, lifecycle_client, title="Synthetic amendment platform"
    )
    with fixture.principal():
        arms = [
            TestUtils.create_study_arm(
                study_uid,
                name=f"Synthetic version regimen {index}",
                short_name=f"V{index}",
                description=f"Original regimen {index}",
            )
            for index in range(2)
        ]
    return study_uid, [arm.arm_uid for arm in arms]


def _amendment(arm_uids, amendment_type="Synthetic protocol amendment"):
    return {
        "amendment_type": amendment_type,
        "affected_arm_uids": arm_uids,
        "authorizing_decision_ref": "Synthetic authored decision 003",
    }


def _lock_input(reasons, **extra):
    return {
        "change_description": "Synthetic amendment lock",
        "reason_for_change_uid": reasons["reason_for_lock_terms"][0].term_uid,
        "other_reason_for_locking_releasing": "Synthetic authored lock reason",
        **extra,
    }


def _lock(client, study_uid, reasons, **extra):
    response = client.post(
        f"/studies/{study_uid}/locks", json=_lock_input(reasons, **extra)
    )
    assert response.status_code == 201, response.text
    return _current_version(client, study_uid)


def _snapshot_history(client, study_uid):
    response = client.get(
        f"/studies/{study_uid}/snapshot-history", params={"page_size": 0}
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    result = {item["metadata_version"]: item for item in items}
    assert len(result) == len(items), "Each metadata version has one history record"
    return result


def _diff_path(study_uid, before="1", after="2"):
    return f"/studies/{study_uid}/versions/{before}/diff/{after}"


def _diff(client, study_uid, before="1", after="2"):
    response = client.get(_diff_path(study_uid, before, after))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["study_uid"] == study_uid
    assert result["from_version"] == before
    assert result["to_version"] == after
    for key, version in (("before", before), ("after", after)):
        assert result[key]["study_uid"] == study_uid
        assert result[key]["version"] == version
        assert result[key]["structural_hash"]
    return result


def _node(snapshot, label, **properties):
    matches = [
        node
        for node in snapshot["content"]["nodes"].values()
        if label in node["labels"]
        and all(
            node["properties"].get(name) == {"type": "string", "value": value}
            for name, value in properties.items()
        )
    ]
    assert len(matches) == 1, (label, properties, matches)
    return matches[0]


def _clinical_result(diff):
    return {
        "before": diff["before"]["content"],
        "after": diff["after"]["content"],
        "before_hash": diff["before"]["structural_hash"],
        "after_hash": diff["after"]["structural_hash"],
        "changes": diff["changes"],
        "structurally_equal": diff["structurally_equal"],
        "semantic_comparison_complete": diff["semantic_comparison_complete"],
    }


def test_http_amendment_relock_can_reference_removed_arm_without_history_leak(
    protocol_graph, lifecycle_client, version_case
):
    fixture, reasons = protocol_graph
    study_uid, arms = version_case
    initial = _amendment(arms, "Synthetic initial protocol")
    assert _lock(lifecycle_client, study_uid, reasons, amendment=initial) == "1"
    first = _snapshot_history(lifecycle_client, study_uid)["1"]
    assert first["amendment"] == initial
    assert (
        first["reason_for_lock_name"]
        == reasons["reason_for_lock_terms"][0].sponsor_preferred_name
    )
    assert first["other_reason_for_locking_releasing"] == (
        "Synthetic authored lock reason"
    )
    assert first["modified_by"]
    assert first["modified_date"]

    _unlock(lifecycle_client, study_uid, reasons)
    history = _snapshot_history(lifecycle_client, study_uid)
    draft = next(item for item in history.values() if "DRAFT" in item["study_status"])
    assert draft["amendment"] is None
    assert draft["reason_for_lock_name"] is None
    assert draft["other_reason_for_locking_releasing"] is None
    assert (
        draft["reason_for_unlock_name"]
        == reasons["reason_for_unlock_terms"][0].sponsor_preferred_name
    )
    assert draft["other_reason_for_unlocking"] == "Synthetic lifecycle amendment"

    response = lifecycle_client.patch(
        f"/studies/{study_uid}",
        json={
            "current_metadata": {
                "identification_metadata": {
                    "description": "Synthetic StudyValue metadata successor"
                },
                "study_description": {"study_title": "Synthetic metadata successor"},
            }
        },
    )
    assert response.status_code == 200, response.text
    edited_draft = next(
        item
        for item in _snapshot_history(lifecycle_client, study_uid).values()
        if "DRAFT" in item["study_status"]
    )
    assert edited_draft["amendment"] is None
    assert edited_draft["reason_for_lock_name"] is None
    assert edited_draft["other_reason_for_locking_releasing"] is None
    assert edited_draft["reason_for_unlock_name"] == draft["reason_for_unlock_name"]
    assert (
        edited_draft["other_reason_for_unlocking"]
        == draft["other_reason_for_unlocking"]
    )

    response = lifecycle_client.delete(f"/studies/{study_uid}/study-arms/{arms[0]}")
    assert response.status_code == 204, response.text
    removed = _amendment([arms[0]], "Synthetic removal of a regimen")
    assert _lock(lifecycle_client, study_uid, reasons, amendment=removed) == "2"
    history = _snapshot_history(lifecycle_client, study_uid)
    assert history["1"]["amendment"] == initial
    assert history["2"]["amendment"] == removed
    assert (
        history["1"]["other_reason_for_locking_releasing"]
        == first["other_reason_for_locking_releasing"]
    )
    assert history["2"]["reason_for_unlock_name"] == draft["reason_for_unlock_name"]

    _unlock(lifecycle_client, study_uid, reasons)
    response = lifecycle_client.post(
        f"/studies/{study_uid}/release", json=_lock_input(reasons)
    )
    assert response.status_code == 201, response.text
    history = _snapshot_history(lifecycle_client, study_uid)
    assert history["2.1"]["amendment"] is None
    assert history["2"]["amendment"] == removed
    assert _lock(lifecycle_client, study_uid, reasons) == "3"
    history = _snapshot_history(lifecycle_client, study_uid)
    assert history["3"]["amendment"] is None
    assert history["1"]["amendment"] == initial
    assert history["2"]["amendment"] == removed
    # The read-only diff must not add an amendment or any other graph state.
    baseline = _fingerprint(fixture)
    _diff(lifecycle_client, study_uid, "1", "3")
    assert _fingerprint(fixture) == baseline


def test_http_amendment_rejects_incomplete_or_foreign_scope_atomically(
    protocol_graph, lifecycle_client, version_case
):
    fixture, reasons = protocol_graph
    study_uid, arms = version_case
    other_uid = _create_protocol_study(fixture, lifecycle_client)
    with fixture.principal():
        other = TestUtils.create_study_arm(other_uid, name="Foreign", short_name="F")
    valid = _amendment([arms[0]])
    invalid = [
        {},
        {key: value for key, value in valid.items() if key != "affected_arm_uids"},
        {key: value for key, value in valid.items() if key != "amendment_type"},
        {
            key: value
            for key, value in valid.items()
            if key != "authorizing_decision_ref"
        },
        {**valid, "amendment_type": "  "},
        {**valid, "authorizing_decision_ref": "  "},
        {**valid, "affected_arm_uids": [arms[0], arms[0]]},
        {**valid, "affected_arm_uids": [" "]},
        {**valid, "affected_arm_uids": None},
        {**valid, "affected_arm_uids": ["SyntheticMissingArm"]},
        {**valid, "affected_arm_uids": [other.arm_uid]},
    ]
    baseline = _fingerprint(fixture)
    for amendment in invalid:
        response = lifecycle_client.post(
            f"/studies/{study_uid}/locks",
            json=_lock_input(reasons, amendment=amendment),
        )
        assert response.status_code == 400, (amendment, response.text)
        assert _fingerprint(fixture) == baseline, amendment
    empty = _amendment([])
    _lock(lifecycle_client, study_uid, reasons, amendment=empty)
    assert _snapshot_history(lifecycle_client, study_uid)["1"]["amendment"] == empty
    _unlock(lifecycle_client, study_uid, reasons)
    _lock(lifecycle_client, study_uid, reasons, amendment=None)
    assert _snapshot_history(lifecycle_client, study_uid)["2"]["amendment"] is None


def test_http_exact_version_diff_preserves_metadata_arms_and_authored_documents(
    protocol_graph, lifecycle_client
):
    fixture, reasons = protocol_graph
    study_uid = _create_protocol_study(
        fixture, lifecycle_client, title="Original title"
    )
    content, first_hash = _author_first_protocol_documents(
        fixture, lifecycle_client, study_uid
    )
    arm_uid = content.bindings[0].arm_uids[0]
    _lock(lifecycle_client, study_uid, reasons)
    _unlock(lifecycle_client, study_uid, reasons)
    for path, payload in (
        (
            f"/studies/{study_uid}",
            {
                "current_metadata": {
                    "study_description": {"study_title": "Amended title"}
                }
            },
        ),
        (
            f"/studies/{study_uid}/study-arms/{arm_uid}",
            {"description": "Synthetic amended regimen description"},
        ),
    ):
        response = lifecycle_client.patch(path, json=payload)
        assert response.status_code == 200, response.text
    content.narrative_content_items[-1].text += " Synthetic exact appendix amendment."
    response = lifecycle_client.put(
        f"/studies/{study_uid}/protocol-documents",
        json={
            "expected_study_version": _current_version(lifecycle_client, study_uid),
            "expected_content_hash": first_hash,
            "reason": "Synthetic exact version difference",
            "content": content.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200, response.text
    second_hash = response.json()["content_hash"]
    assert second_hash != first_hash
    _lock(lifecycle_client, study_uid, reasons)
    result = _diff(lifecycle_client, study_uid)
    assert result["structurally_equal"] is False
    assert result["before"]["structural_hash"] != result["after"]["structural_hash"]
    assert _node(result["before"], "StudyTextField", field_name="study_title")[
        "properties"
    ]["value"] == {"type": "string", "value": "Original title"}
    assert _node(result["after"], "StudyTextField", field_name="study_title")[
        "properties"
    ]["value"] == {"type": "string", "value": "Amended title"}
    assert _node(result["after"], "StudyArm", uid=arm_uid)["properties"][
        "description"
    ] == {"type": "string", "value": "Synthetic amended regimen description"}
    for key, content_hash in (("before", first_hash), ("after", second_hash)):
        assert _node(result[key], "StudyDefinitionDocument")["properties"][
            "authored_documents_hash"
        ] == {"type": "string", "value": content_hash}
    change_text = json.dumps(result["changes"])
    for expected in (
        "Original title",
        "Amended title",
        "Synthetic amended regimen description",
        "Synthetic exact appendix amendment.",
    ):
        assert expected in change_text
    assert result["semantic_comparison_complete"] is False
    assert any(
        issue["code"] == "library_value_not_pinned"
        for snapshot in (result["before"], result["after"])
        for issue in snapshot["coverage_issues"]
    )
    _unlock(lifecycle_client, study_uid, reasons)
    # Closing a HAS_VERSION state edge changes provenance, not clinical content.
    after_unlock = _diff(lifecycle_client, study_uid)
    assert _clinical_result(after_unlock) == _clinical_result(result)
    response = lifecycle_client.patch(
        f"/studies/{study_uid}",
        json={
            "current_metadata": {"study_description": {"study_title": "Later draft"}}
        },
    )
    assert response.status_code == 200, response.text
    assert _diff(lifecycle_client, study_uid) == after_unlock
    identical = _diff(lifecycle_client, study_uid, "1", "1")
    assert identical["structurally_equal"] is True
    assert identical["changes"] == []
    assert identical["semantic_comparison_complete"] is False


def test_http_version_diff_reports_cohort_relationship_only_change(
    protocol_graph, lifecycle_client, version_case
):
    fixture, reasons = protocol_graph
    study_uid, arms = version_case
    with fixture.principal():
        cohort = TestUtils.create_study_cohort(
            study_uid, name="Synthetic cohort", short_name="C", arm_uids=[arms[0]]
        )
    _lock(lifecycle_client, study_uid, reasons)
    _unlock(lifecycle_client, study_uid, reasons)
    response = lifecycle_client.patch(
        f"/studies/{study_uid}/study-cohorts/{cohort.cohort_uid}",
        json={"arm_uids": [arms[1]]},
    )
    assert response.status_code == 200, response.text
    _lock(lifecycle_client, study_uid, reasons)
    result = _diff(lifecycle_client, study_uid)
    assert _node(result["before"], "StudyCohort", uid=cohort.cohort_uid) == _node(
        result["after"], "StudyCohort", uid=cohort.cohort_uid
    )
    relationships = [
        [
            edge
            for edge in result[side]["content"]["relationships"]
            if edge["type"] == "STUDY_ARM_HAS_COHORT"
        ]
        for side in ("before", "after")
    ]
    assert len(relationships[0]) == len(relationships[1]) == 1
    assert relationships[0][0]["source"] != relationships[1][0]["source"]
    assert relationships[0][0]["target"] == relationships[1][0]["target"]
    assert result["structurally_equal"] is False
    assert "STUDY_ARM_HAS_COHORT" in json.dumps(result["changes"])


def test_http_parent_cascade_retains_distinct_child_version_records(
    protocol_graph, lifecycle_client, version_case
):
    fixture, reasons = protocol_graph
    study_uid, arms = version_case
    children = []
    for index in range(2):
        response = lifecycle_client.post(
            "/studies",
            json={
                "study_parent_part_uid": study_uid,
                "study_subpart_acronym": f"SYN{index}",
                "description": f"Synthetic appendix {index}",
            },
        )
        assert response.status_code == 201, response.text
        child_uid = response.json()["uid"]
        fixture.auth.user.study_ids.add(child_uid)
        children.append(child_uid)
    authored = _amendment([arms[0]])
    _lock(lifecycle_client, study_uid, reasons, amendment=authored)
    for child_uid in children:
        child = _snapshot_history(lifecycle_client, child_uid)["1"]
        assert child["amendment"] is None
        assert (
            child["reason_for_lock_name"]
            == reasons["reason_for_lock_terms"][0].sponsor_preferred_name
        )
    _unlock(lifecycle_client, study_uid, reasons)
    parent_before = _diff(lifecycle_client, study_uid, "1", "1")
    response = lifecycle_client.post(
        f"/studies/{study_uid}/release", json=_lock_input(reasons)
    )
    assert response.status_code == 201, response.text
    _lock(lifecycle_client, study_uid, reasons, amendment=_amendment([]))
    for child_uid in children:
        history = _snapshot_history(lifecycle_client, child_uid)
        assert history["1"]["amendment"] is None
        assert history["2"]["amendment"] is None
        assert all(item["amendment"] is None for item in history.values())
        assert (
            history["1"]["reason_for_lock_name"] == history["2"]["reason_for_lock_name"]
        )
    assert _diff(lifecycle_client, study_uid, "1", "1") == parent_before
    db.set_connection(url=fixture.dsn)
    rows, _ = db.cypher_query(
        "MATCH (root:StudyRoot)-[locked:HAS_VERSION {status:'LOCKED'}]->(value:StudyValue) "
        "WHERE root.uid IN $uids "
        "WITH DISTINCT root, locked.version AS version, value "
        "OPTIONAL MATCH (value)-[membership:HAS_STUDY_VERSION]->(record:StudyVersion) "
        "RETURN root.uid, version, count(membership), count(DISTINCT record) "
        "ORDER BY root.uid, version",
        {"uids": [study_uid, *children]},
    )
    assert len(rows) == 6, rows
    assert all(row[2:] == [1, 1] for row in rows), rows


def test_http_diff_enforces_roles_visibility_and_exact_numeric_versions(
    protocol_graph, lifecycle_client, version_case, monkeypatch
):
    fixture, reasons = protocol_graph
    study_uid, _ = version_case
    draft_version = _current_version(lifecycle_client, study_uid)
    draft_fingerprint = _fingerprint(fixture)
    response = lifecycle_client.get(_diff_path(study_uid, draft_version, draft_version))
    assert response.status_code == 400, response.text
    assert _fingerprint(fixture) == draft_fingerprint
    _lock(lifecycle_client, study_uid, reasons)
    path = _diff_path(study_uid, "1", "1")
    result = _diff(lifecycle_client, study_uid, "1", "1")
    baseline = _fingerprint(fixture)
    assert settings.oauth_rbac_enabled
    with monkeypatch.context() as scoped:
        scoped.setattr(fixture.auth.user, "roles", {"Study.Write"})
        response = lifecycle_client.get(path)
        assert response.status_code == 403, response.text
        scoped.setattr(fixture.auth.user, "roles", {"Study.Read"})
        assert lifecycle_client.get(path).json() == result
    # JWT transport is replaced only by the owned principal fixture. These are
    # the actual RBAC and delegated native study visibility dependencies.
    with monkeypatch.context() as strict:
        strict.setattr(settings, "delegated_claims_required", True)
        strict.setattr(fixture.auth.user, "purpose", "interactive-domain-access")
        strict.setattr(fixture.auth.user, "capabilities", {"study:read"})
        strict.setattr(fixture.auth.user, "roles", {"Study.Read"})
        assert lifecycle_client.get(path).json() == result
        for field, value, status in (
            ("study_ids", set(), 404),
            ("tenant_id", "synthetic-unrelated-tenant", 404),
            ("capabilities", set(), 403),
            ("purpose", "", 403),
        ):
            with monkeypatch.context() as denied:
                denied.setattr(fixture.auth.user, field, value)
                response = lifecycle_client.get(path)
                assert response.status_code == status, (field, response.text)
                assert _fingerprint(fixture) == baseline
    for version, status in (("999999", 404), ("latest", 400), ("None", 400)):
        response = lifecycle_client.get(_diff_path(study_uid, "1", version))
        assert response.status_code == status, response.text
    response = lifecycle_client.get(_diff_path("SyntheticMissingStudy", "1", "1"))
    assert response.status_code == 404, response.text
    assert _fingerprint(fixture) == baseline


def test_http_snapshot_restored_after_native_relationship_property_probe(
    protocol_graph, lifecycle_client, version_case
):
    """Probe the native reader with model-shaped edges, not endpoint authoring.

    Sparse endpoint and conjunction nodes exist only in this rolled-back
    transaction. They test lossless raw structural reading, not clinical
    endpoint completeness or the public syntax authoring workflow. HTTP reads
    bracket the transaction; the service shares its uncommitted connection.
    """
    fixture, reasons = protocol_graph
    study_uid, arms = version_case
    _lock(lifecycle_client, study_uid, reasons)
    original = _diff(lifecycle_client, study_uid, "1", "1")
    baseline = _fingerprint(fixture)
    with fixture.principal():
        with pytest.raises(RuntimeError, match="rollback relationship probe"):
            with db.transaction:
                rows, _ = db.cypher_query(
                    "MATCH (:StudyRoot {uid:$study_uid})"
                    "-[:HAS_VERSION {version:'1'}]->(value:StudyValue) "
                    "WITH DISTINCT value "
                    "MATCH (value)-[:HAS_STUDY_ARM]->(arm:StudyArm {uid:$arm_uid}), "
                    "(unit:UnitDefinitionRoot {uid:$unit_uid}) "
                    "CREATE (endpoint:StudyEndpoint:StudySelection "
                    "{uid:$endpoint_uid, order:1, accepted_version:false}), "
                    "(conjunction:Conjunction {name:'Synthetic and'}), "
                    "(value)-[:HAS_STUDY_ENDPOINT]->(endpoint), "
                    "(endpoint)-[:HAS_UNIT {index:0}]->(unit), "
                    "(endpoint)-[:HAS_CONJUNCTION {position:1,set_number:0}]->(conjunction), "
                    "(value)-[:HAS_PROTOCOL_SOA_CELL "
                    "{row:0,column:1,span:2,is_propagated:false,order:0}]->(arm), "
                    "(value)-[:HAS_PROTOCOL_SOA_CELL "
                    "{row:0,column:1,span:2,is_propagated:false,order:0}]->(arm) "
                    "RETURN count(endpoint)",
                    {
                        "study_uid": study_uid,
                        "arm_uid": arms[0],
                        "unit_uid": fixture.day_uid,
                        "endpoint_uid": study_uid + "-reader-endpoint",
                    },
                )
                assert rows == [[1]]
                service = StudyVersionSnapshotService()
                first = service.get_snapshot(study_uid, "1").model_dump(mode="json")
                relationships = first["content"]["relationships"]
                layout = [
                    edge
                    for edge in relationships
                    if edge["type"] == "HAS_PROTOCOL_SOA_CELL"
                ]
                assert len(layout) == 2
                assert (
                    layout[0] == layout[1]
                ), "Equal semantic edges retain multiplicity"
                assert layout[0]["properties"] == {
                    "row": {"type": "integer", "value": "0"},
                    "column": {"type": "integer", "value": "1"},
                    "span": {"type": "integer", "value": "2"},
                    "is_propagated": {"type": "boolean", "value": False},
                    "order": {"type": "integer", "value": "0"},
                }
                units = [edge for edge in relationships if edge["type"] == "HAS_UNIT"]
                assert len(units) == 1
                assert units[0]["properties"]["index"] == {
                    "type": "integer",
                    "value": "0",
                }
                conjunctions = [
                    edge for edge in relationships if edge["type"] == "HAS_CONJUNCTION"
                ]
                assert len(conjunctions) == 1
                assert conjunctions[0]["properties"] == {
                    "position": {"type": "integer", "value": "1"},
                    "set_number": {"type": "integer", "value": "0"},
                }
                db.cypher_query(
                    "MATCH (:StudyEndpoint {uid:$endpoint_uid})-[unit:HAS_UNIT]->() "
                    "SET unit.index=1 RETURN count(unit)",
                    {"endpoint_uid": study_uid + "-reader-endpoint"},
                )
                second = service.get_snapshot(study_uid, "1").model_dump(mode="json")
                assert second["structural_hash"] != first["structural_hash"]
                assert second["content"]["nodes"] == first["content"]["nodes"]
                assert (
                    second["content"]["study_value"] == first["content"]["study_value"]
                )
                assert second["content"]["relationships"] != relationships
                corrupted = graph_fingerprint()
                assert (
                    service.get_snapshot(study_uid, "1").model_dump(mode="json")
                    == second
                )
                assert graph_fingerprint() == corrupted
                raise RuntimeError("rollback relationship probe")
    assert _fingerprint(fixture) == baseline
    assert _diff(lifecycle_client, study_uid, "1", "1") == original


@pytest.mark.parametrize(
    "corruption",
    [
        "ambiguous-version",
        "duplicate-membership",
        "duplicate-identity",
        "orphan-selection",
        "foreign-selection",
    ],
)
def test_http_snapshot_restored_after_native_corruption_guard(
    protocol_graph, lifecycle_client, version_case, corruption
):
    """Use the real service on the corruption connection and HTTP after rollback."""
    fixture, reasons = protocol_graph
    study_uid, arms = version_case
    foreign_branch_uid = None
    if corruption == "foreign-selection":
        foreign_uid = _create_protocol_study(fixture, lifecycle_client)
        with fixture.principal():
            foreign_arm = TestUtils.create_study_arm(
                foreign_uid, name="Synthetic foreign arm", short_name="F"
            )
            foreign_branch = TestUtils.create_study_branch_arm(
                foreign_uid,
                name="Synthetic foreign branch",
                short_name="FB",
                study_arm_uid=foreign_arm.arm_uid,
            )
            foreign_branch_uid = foreign_branch.branch_arm_uid
    _lock(lifecycle_client, study_uid, reasons)
    original = _diff(lifecycle_client, study_uid, "1", "1")
    baseline = _fingerprint(fixture)
    selection = (
        "MATCH (root:StudyRoot {uid:$study_uid})"
        "-[:HAS_VERSION {version:'1'}]->(value:StudyValue) "
        "WITH DISTINCT root, value "
        "MATCH (value)-[:HAS_STUDY_ARM]->(arm:StudyArm {uid:$arm_uid}) "
    )
    if corruption == "ambiguous-version":
        mutation = (
            "CREATE (duplicate:StudyValue) SET duplicate=properties(value) "
            "CREATE (root)-[:HAS_VERSION {version:'1',status:'RELEASED'}]->(duplicate) "
            "RETURN count(duplicate)"
        )
        error_fragment = "ambiguous version records"
    elif corruption == "duplicate-membership":
        mutation = (
            "CREATE (value)-[duplicate:HAS_STUDY_ARM]->(arm) RETURN count(duplicate)"
        )
        error_fragment = "STUDY_SNAPSHOT_DUPLICATE_MEMBERSHIP"
    elif corruption == "duplicate-identity":
        mutation = (
            "CREATE (duplicate:StudyArm:StudySelection) SET duplicate=properties(arm) "
            "CREATE (value)-[:HAS_STUDY_ARM]->(duplicate) RETURN count(duplicate)"
        )
        error_fragment = "STUDY_SNAPSHOT_DUPLICATE_IDENTITY"
    elif corruption == "orphan-selection":
        mutation = (
            "CREATE (orphan:StudyBranchArm:StudySelection {uid:$orphan_uid}), "
            "(arm)-[:STUDY_ARM_HAS_BRANCH_ARM]->(orphan) RETURN count(orphan)"
        )
        error_fragment = "STUDY_SNAPSHOT_TARGET_NOT_SELECTED"
    else:
        mutation = (
            "MATCH (foreign:StudyBranchArm {uid:$foreign_branch_uid}) "
            "CREATE (arm)-[link:STUDY_ARM_HAS_BRANCH_ARM]->(foreign) RETURN count(link)"
        )
        error_fragment = "STUDY_SNAPSHOT_TARGET_NOT_SELECTED"
    with fixture.principal():
        with pytest.raises(RuntimeError, match="rollback snapshot corruption"):
            with db.transaction:
                rows, _ = db.cypher_query(
                    selection + mutation,
                    {
                        "study_uid": study_uid,
                        "arm_uid": arms[0],
                        "orphan_uid": study_uid + "-orphan-branch",
                        "foreign_branch_uid": foreign_branch_uid,
                    },
                )
                assert rows == [[1]]
                corrupted = graph_fingerprint()
                assert corrupted != baseline
                with pytest.raises(ValidationException) as rejection:
                    StudyVersionSnapshotService().get_diff(study_uid, "1", "1")
                assert rejection.type is ValidationException
                assert error_fragment in rejection.value.msg
                assert graph_fingerprint() == corrupted
                raise RuntimeError("rollback snapshot corruption")
    assert _fingerprint(fixture) == baseline
    assert _diff(lifecycle_client, study_uid, "1", "1") == original
