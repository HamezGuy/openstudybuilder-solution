"""Native document persistence on a separately owned, initially empty graph.

The external evidence harness starts/stops a loopback Community server. This
test never clears a database and refuses ordinary/shared fixture connections.
"""

# Pytest resolves these fixture names by their function parameters.
# pylint: disable=redefined-outer-name

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neomodel import db
from starlette.middleware import Middleware
from starlette_context import context, request_cycle_context
from starlette_context.middleware import RawContextMiddleware

from clinical_mdr_api.domain_repositories.study_selections.study_definition_document_repository import (
    StudyDefinitionDocumentRepository,
)
from clinical_mdr_api.routers.studies import studies
from clinical_mdr_api.tests.fixtures.study_visit_lock_neo4j import (  # noqa: F401  pylint: disable=unused-import
    native_visit_graph,
)
from clinical_mdr_api.tests.integration.utils.data_library import (
    create_reason_for_lock_unlock_terms,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from clinical_mdr_api.tests.unit.services.test_authored_protocol_documents import (
    authored_source,
)
from clinical_mdr_api.utils.db_integrity_checks import QUERIES, execute_check_for_study
from common.auth.dependencies import (
    dummy_access_token_claims,
    dummy_auth_object,
    security,
)
from common.config import settings
from common.exception_handlers import register_exception_handlers
from common.exceptions import AlreadyExistsException

pytestmark = pytest.mark.estate_fixture


@pytest.fixture(scope="module")
def protocol_graph(native_visit_graph):
    with native_visit_graph.principal():
        reasons = create_reason_for_lock_unlock_terms()
    return native_visit_graph, reasons


@pytest.fixture
def protocol_client(protocol_graph):
    fixture, _ = protocol_graph
    app = FastAPI(middleware=[Middleware(RawContextMiddleware)])
    register_exception_handlers(app)
    app.include_router(studies.router, prefix="/studies")

    async def fixture_authentication():
        db.set_connection(url=fixture.dsn)
        context["auth"] = fixture.auth
        try:
            yield
        finally:
            db.close_connection()

    app.dependency_overrides[security.dependency] = fixture_authentication
    with TestClient(app) as client:
        yield client


def _assert_protocol_header(client, path, expected_version, content, content_hash):
    response = client.get(path + "/protocol-header-versions")
    assert response.status_code == 200, response.text
    header = response.json()
    assert header["protocol_header_version"] == expected_version
    assert header["authored_documents"] == content
    assert header["authored_documents_hash"] == content_hash


def _snapshot_headers(client, path):
    response = client.get(path + "/snapshot-history", params={"page_size": 0})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    headers = {
        item["metadata_version"]: (
            item["protocol_header_major_version"],
            item["protocol_header_minor_version"],
        )
        for item in items
    }
    assert len(headers) == len(items), "Each metadata version has one snapshot"
    return headers


def _create_protocol_study(
    fixture, client, title="Synthetic platform", package_name=None
):
    fixture.created += 1
    with fixture.principal():
        study = TestUtils.create_study(
            number=str(1900 + fixture.created),
            acronym=f"DOCUMENTS{fixture.created}",
            project_number="123",
        )
        fixture.auth.user.study_ids.add(study.uid)
        TestUtils.set_study_standard_version(
            study_uid=study.uid,
            package_name=package_name or f"Documents CT {fixture.nonce}",
            effective_date=datetime.now(timezone.utc),
            create_codelists_and_terms_for_package=False,
        )
    response = client.patch(
        f"/studies/{study.uid}",
        json={"current_metadata": {"study_description": {"study_title": title}}},
    )
    assert response.status_code == 200, response.text
    return study.uid


def _author_first_protocol_documents(fixture, client, study_uid):
    with fixture.principal():
        arms = [
            TestUtils.create_study_arm(
                study_uid, name=f"Regimen {index}", short_name=f"R{index}"
            )
            for index in range(3)
        ]
    _, content = authored_source()
    content.bindings[0].arm_uids = [arm.arm_uid for arm in arms]
    for binding, arm in zip(content.bindings[1:], arms):
        binding.arm_uids = [arm.arm_uid]
    path = f"/studies/{study_uid}"
    response = client.get(path + "/protocol-documents")
    assert response.status_code == 200, response.text
    current = response.json()
    response = client.put(
        path + "/protocol-documents",
        json={
            "expected_study_version": current["study_value_version"],
            "expected_content_hash": None,
            "reason": "Synthetic master and regimen authoring",
            "content": content.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"] == content.model_dump(mode="json")
    return content, response.json()["content_hash"]


def _protocol_document_checks(study_uid):
    check_ids = {
        "only_one_study_definition_document_per_study_version",
        "study_version_exists_if_has_protocol_soa_cell_and_study_definition_document_exists",
        "study_version_exists_if_study_definition_document_exists",
    }
    results = {
        check_id: execute_check_for_study(check_id, description, query, study_uid)
        for check_id, description, query in QUERIES
        if check_id in check_ids
    }
    assert results.keys() == check_ids
    assert all(result.error is None for result in results.values()), results
    return results


def _assert_protocol_document_integrity(study_uid):
    results = _protocol_document_checks(study_uid)
    assert all(result.passed for result in results.values()), results


@pytest.mark.parametrize("with_header", [False, True])
@pytest.mark.parametrize(
    "native_visit_graph", ["study-only"], indirect=True, scope="module"
)
def test_http_lock_unlock_carries_master_and_appendices(
    protocol_graph, protocol_client, with_header
):
    """Real native version writes and HTTP reads on the owned Community graph."""
    fixture, reasons = protocol_graph
    client = protocol_client
    study_uid = _create_protocol_study(fixture, client)
    content, first_hash = _author_first_protocol_documents(fixture, client, study_uid)
    first_content = content.model_dump(mode="json")
    path = f"/studies/{study_uid}"
    _assert_protocol_header(client, path, None, first_content, first_hash)
    lock_input = {
        "change_description": "Synthetic protocol lock",
        "reason_for_change_uid": reasons["reason_for_lock_terms"][0].term_uid,
    }
    if with_header:
        lock_input.update(
            protocol_header_major_version=1, protocol_header_minor_version=0
        )
    response = client.post(path + "/locks", json=lock_input)
    assert response.status_code == 201, response.text
    locked = client.get(
        path + "/protocol-documents", params={"study_value_version": "1"}
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["content"] == first_content
    assert locked.json()["content_hash"] == first_hash
    _assert_protocol_header(
        client, path, "1.0" if with_header else None, first_content, first_hash
    )
    response = client.post(
        path + "/unlocks",
        json={
            "reason_for_change_uid": reasons["reason_for_unlock_terms"][0].term_uid,
            "other_reason_for_unlocking": "Synthetic appendix amendment",
        },
    )
    assert response.status_code == 201, response.text
    response = client.get(path + "/protocol-documents")
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["content"] == first_content
    assert draft["content_hash"] == first_hash
    _assert_protocol_header(
        client, path, "1.0" if with_header else None, first_content, first_hash
    )
    release_input = dict(lock_input)
    if with_header:
        release_input["protocol_header_minor_version"] = 1
    response = client.post(path + "/release", json=release_input)
    assert response.status_code == 201, response.text
    response = client.get(path + "/protocol-documents")
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["content"] == first_content
    assert draft["content_hash"] == first_hash
    _assert_protocol_header(
        client, path, "1.1" if with_header else None, first_content, first_hash
    )
    released = client.get(
        path + "/protocol-documents", params={"study_value_version": "1.1"}
    )
    assert released.status_code == 200, released.text
    released_content = released.json()
    assert released_content["content"] == first_content
    assert released_content["content_hash"] == first_hash
    headers = _snapshot_headers(client, path)
    assert headers["1"] == ((1, 0) if with_header else (None, None))
    assert headers["1.1"] == ((1, 1) if with_header else (None, None))
    appendix_section = next(
        item
        for item in content.narrative_content_items
        if item.id.startswith("authored-arm-1-")
    )
    appendix_section.text += " Synthetic amended appendix."
    second_content = content.model_dump(mode="json")
    request = {
        "expected_study_version": draft["study_value_version"],
        "expected_content_hash": first_hash,
        "reason": "Synthetic amendment with retained master",
        "content": second_content,
    }
    response = client.put(path + "/protocol-documents", json=request)
    assert response.status_code == 200, response.text
    second_hash = response.json()["content_hash"]
    assert response.json()["content"] == second_content
    assert second_hash != first_hash
    assert client.put(path + "/protocol-documents", json=request).status_code == 409
    _assert_protocol_header(
        client, path, "1.1" if with_header else None, second_content, second_hash
    )
    for version in ("1", "1.1"):
        response = client.get(
            path + "/protocol-documents", params={"study_value_version": version}
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"] == first_content
        assert response.json()["content_hash"] == first_hash
        if version == "1.1":
            assert response.json() == released_content
    if with_header:
        lock_input["protocol_header_major_version"] = 2
    response = client.post(path + "/locks", json=lock_input)
    assert response.status_code == 201, response.text
    for version, expected_content, expected_hash in (
        ("2", second_content, second_hash),
        ("1.1", first_content, first_hash),
        ("1", first_content, first_hash),
    ):
        response = client.get(
            path + "/protocol-documents", params={"study_value_version": version}
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"] == expected_content
        assert response.json()["content_hash"] == expected_hash
        if version == "1.1":
            assert response.json() == released_content
    _assert_protocol_header(
        client, path, "2.0" if with_header else None, second_content, second_hash
    )
    assert _snapshot_headers(client, path) == (
        {"2": (2, 0), "1.1": (1, 1), "1": (1, 0)}
        if with_header
        else {"2": (None, None), "1.1": (None, None), "1": (None, None)}
    )


@pytest.mark.parametrize(
    "native_visit_graph", ["study-only"], indirect=True, scope="module"
)
def test_http_header_only_release_keeps_draft_header_unset(
    protocol_graph, protocol_client
):
    """Header-only releases remain exact when later content starts its own history."""
    fixture, reasons = protocol_graph
    client = protocol_client
    study_uid = _create_protocol_study(
        fixture, client, title="Synthetic header-only protocol"
    )
    path = f"/studies/{study_uid}"
    _assert_protocol_header(client, path, None, None, None)
    lock_input = {
        "change_description": "Synthetic header-only lock",
        "reason_for_change_uid": reasons["reason_for_lock_terms"][0].term_uid,
    }
    response = client.post(path + "/locks", json=lock_input)
    assert response.status_code == 201, response.text
    response = client.post(
        path + "/unlocks",
        json={
            "reason_for_change_uid": reasons["reason_for_unlock_terms"][0].term_uid,
        },
    )
    assert response.status_code == 201, response.text
    assert _snapshot_headers(client, path) == {
        None: (None, None),
        "1": (None, None),
    }
    expected_headers = {None: (None, None), "1": (None, None)}
    for minor in (1, 2):
        response = client.post(
            path + "/release",
            json={
                **lock_input,
                "protocol_header_major_version": 0,
                "protocol_header_minor_version": minor,
            },
        )
        assert response.status_code == 201, response.text
        _assert_protocol_header(client, path, f"0.{minor}", None, None)
        expected_headers[f"1.{minor}"] = (0, minor)
        assert _snapshot_headers(client, path) == expected_headers
        for version in expected_headers:
            response = client.get(
                path + "/protocol-documents",
                params={"study_value_version": version} if version else {},
            )
            assert response.status_code == 200, response.text
            assert response.json()["content"] is None
            assert response.json()["content_hash"] is None

    content, authored_hash = _author_first_protocol_documents(
        fixture, client, study_uid
    )
    authored_content = content.model_dump(mode="json")
    _assert_protocol_header(client, path, "0.2", authored_content, authored_hash)
    response = client.post(
        path + "/locks",
        json={
            **lock_input,
            "protocol_header_major_version": 1,
            "protocol_header_minor_version": 0,
        },
    )
    assert response.status_code == 201, response.text
    _assert_protocol_header(client, path, "1.0", authored_content, authored_hash)
    assert _snapshot_headers(client, path) == {
        "2": (1, 0),
        "1.2": (0, 2),
        "1.1": (0, 1),
        "1": (None, None),
    }
    for version in (None, "2", "1.2", "1.1", "1"):
        response = client.get(
            path + "/protocol-documents",
            params={"study_value_version": version} if version else {},
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"] == (
            authored_content if version in (None, "2") else None
        )
        assert response.json()["content_hash"] == (
            authored_hash if version in (None, "2") else None
        )


@pytest.mark.parametrize(
    "native_visit_graph", ["study-only"], indirect=True, scope="module"
)
def test_http_first_authored_release_and_metadata_edit_preserve_integrity(
    protocol_graph, protocol_client
):
    """Unversioned authored drafts remain valid before and after first release."""
    fixture, reasons = protocol_graph
    client = protocol_client
    study_uid = _create_protocol_study(fixture, client)
    _assert_protocol_document_integrity(study_uid)
    content, content_hash = _author_first_protocol_documents(fixture, client, study_uid)
    expected_content = content.model_dump(mode="json")
    _assert_protocol_document_integrity(study_uid)
    path = f"/studies/{study_uid}"
    response = client.post(
        path + "/release",
        json={
            "change_description": "Synthetic first authored release",
            "reason_for_change_uid": reasons["reason_for_lock_terms"][0].term_uid,
            "protocol_header_major_version": 0,
            "protocol_header_minor_version": 1,
        },
    )
    assert response.status_code == 201, response.text
    released_response = client.get(
        path + "/protocol-documents", params={"study_value_version": "0.1"}
    )
    assert released_response.status_code == 200, released_response.text
    released = released_response.json()
    assert released["content"] == expected_content
    assert released["content_hash"] == content_hash
    _assert_protocol_document_integrity(study_uid)
    response = client.get(path + "/protocol-documents")
    assert response.status_code == 200, response.text
    assert response.json()["study_status"] == "DRAFT"
    assert response.json()["content"] == expected_content
    assert response.json()["content_hash"] == content_hash
    _assert_protocol_document_integrity(study_uid)
    response = client.patch(
        path,
        json={
            "current_metadata": {
                "study_description": {"study_title": "Synthetic draft metadata edit"}
            }
        },
    )
    assert response.status_code == 200, response.text
    response = client.get(path + "/protocol-documents")
    assert response.status_code == 200, response.text
    assert response.json()["content"] == expected_content
    assert response.json()["content_hash"] == content_hash
    _assert_protocol_document_integrity(study_uid)
    _assert_protocol_header(client, path, "0.1", expected_content, content_hash)
    response = client.get(
        path + "/protocol-documents", params={"study_value_version": "0.1"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == released


@pytest.mark.parametrize(
    "corruption",
    [
        "released-authored-without-version",
        "released-header-only-without-version",
        "draft-header-only-without-version",
        "duplicate-draft-documents",
    ],
)
@pytest.mark.parametrize(
    "native_visit_graph", ["study-only"], indirect=True, scope="module"
)
def test_protocol_document_integrity_detects_rolled_back_corruption(
    protocol_graph, protocol_client, corruption
):
    """Valid authored drafts do not exempt released, header-only or duplicate data."""
    fixture, reasons = protocol_graph
    client = protocol_client
    study_uid = _create_protocol_study(fixture, client)
    if corruption in (
        "released-authored-without-version",
        "duplicate-draft-documents",
    ):
        _author_first_protocol_documents(fixture, client, study_uid)
    if corruption != "duplicate-draft-documents":
        response = client.post(
            f"/studies/{study_uid}/release",
            json={
                "change_description": "Synthetic integrity release",
                "reason_for_change_uid": reasons["reason_for_lock_terms"][0].term_uid,
                "protocol_header_major_version": 0,
                "protocol_header_minor_version": 1,
            },
        )
        assert response.status_code == 201, response.text

    baseline = _protocol_document_checks(study_uid)
    with fixture.principal():
        with pytest.raises(RuntimeError, match="rollback integrity corruption"):
            with db.transaction:
                if corruption.startswith("released-"):
                    rows, _ = db.cypher_query(
                        "MATCH (:StudyRoot {uid:$study_uid})"
                        "-[:HAS_VERSION {status:'RELEASED',version:'0.1'}]->(value:StudyValue)"
                        "-[version:HAS_STUDY_VERSION]->(:StudyVersion) "
                        "DELETE version RETURN count(version)",
                        {"study_uid": study_uid},
                    )
                    assert rows == [[1]]
                    # The conditional SoA check must retain the same released-version
                    # requirement when a protocol cell is present.
                    db.cypher_query(
                        "MATCH (:StudyRoot {uid:$study_uid})"
                        "-[:HAS_VERSION {status:'RELEASED',version:'0.1'}]->(value:StudyValue) "
                        "CREATE (value)-[:HAS_PROTOCOL_SOA_CELL]->(:StudySelection)",
                        {"study_uid": study_uid},
                    )
                    expected_failures = (
                        "study_version_exists_if_study_definition_document_exists",
                        "study_version_exists_if_has_protocol_soa_cell_and_study_definition_document_exists",
                    )
                elif corruption == "draft-header-only-without-version":
                    # Every release gives its new draft exactly one reason
                    # record. Removing it builds the unversioned draft state.
                    rows, _ = db.cypher_query(
                        "MATCH (root:StudyRoot {uid:$study_uid})-[:LATEST]->(draft:StudyValue), "
                        "(root)-[:HAS_VERSION {status:'RELEASED',version:'0.1'}]"
                        "->(:StudyValue)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document:StudyDefinitionDocument) "
                        "WHERE document.authored_documents_json IS NULL "
                        "OPTIONAL MATCH (draft)-[membership:HAS_STUDY_VERSION]->(:StudyVersion) "
                        "WITH draft, document, collect(membership) AS memberships "
                        "FOREACH (membership IN memberships | DELETE membership) "
                        "CREATE (draft)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document) "
                        "RETURN count(document), size(memberships)",
                        {"study_uid": study_uid},
                    )
                    assert rows == [[1, 1]]
                    expected_failures = (
                        "study_version_exists_if_study_definition_document_exists",
                    )
                else:
                    rows, _ = db.cypher_query(
                        "MATCH (:StudyRoot {uid:$study_uid})-[:LATEST]->(draft:StudyValue)"
                        "-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document:StudyDefinitionDocument) "
                        "WHERE NOT (draft)-[:HAS_STUDY_VERSION]->(:StudyVersion) "
                        "CREATE (duplicate:StudyDefinitionDocument:StudySelection) "
                        "SET duplicate = properties(document), duplicate.uid = document.uid + '-duplicate' "
                        "CREATE (draft)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(duplicate) "
                        "RETURN count(duplicate)",
                        {"study_uid": study_uid},
                    )
                    assert rows == [[1]]
                    expected_failures = (
                        "only_one_study_definition_document_per_study_version",
                    )
                corrupted = _protocol_document_checks(study_uid)
                for check_id in expected_failures:
                    result = corrupted[check_id]
                    assert not result.passed, result
                    assert (
                        result.noncompliant_count
                        > baseline[check_id].noncompliant_count
                    ), (baseline[check_id], result)
                raise RuntimeError("rollback integrity corruption")
    assert _protocol_document_checks(study_uid) == baseline


def test_real_graph_document_history_header_copy_concurrency_and_rollback():
    marker_path = os.environ.get("OSB_AUTHORED_DOCUMENT_FIXTURE", "")
    assert marker_path, "Explicit isolated authored-document fixture is required"
    marker = json.loads(Path(marker_path).read_text(encoding="utf-8"))
    parsed = urlsplit(settings.neo4j_dsn)
    assert marker["owner"] == "ta3-b3-authored-protocol"
    assert parsed.hostname == "127.0.0.1" and parsed.port == marker["port"]
    assert parsed.path == "/neo4j"
    assert marker["data_directory"] == str(Path(marker_path).parent / "data")
    nonce = marker["nonce"]
    db.set_connection(url=settings.neo4j_dsn)
    rows, _ = db.cypher_query("MATCH (n) RETURN count(n)")
    assert rows == [[1]], "Fresh fixture must contain only its ownership marker"
    rows, _ = db.cypher_query(
        "MATCH (n:AuthoredFixtureOwner {nonce:$nonce}) RETURN count(n)",
        {"nonce": nonce},
    )
    assert rows == [[1]]
    uid = "Study_authored_" + nonce
    db.cypher_query(
        "CREATE (root:StudyRoot {uid:$uid}), (value:StudyValue) CREATE (root)-[:LATEST]->(value), (root)-[:HAS_VERSION {version:'0.1',status:'DRAFT'}]->(value)",
        {"uid": uid},
    )
    claims = dummy_access_token_claims(user_id="authored-fixture-writer")
    claims.sub = "authored-fixture-writer"
    auth = dummy_auth_object(claims)
    _, content = authored_source()
    first_json, first_hash = content.model_dump_json(), content.content_hash()
    with request_cycle_context({"auth": auth}):
        repository = StudyDefinitionDocumentRepository()
        first = repository.save_authored_documents(
            uid, first_json, first_hash, None, "Synthetic initial authoring"
        )
        assert repository.get_latest_protocol_header_version(uid) is None
        assert (
            repository.get_authored_documents(uid).authored_documents_json == first_json
        )
        assert first.authored_documents_author == "authored-fixture-writer"
        # A subsequent StudyValue points to the existing immutable selection,
        # as native version creation does; its later edits must not change 0.1.
        db.cypher_query(
            "MATCH (root:StudyRoot {uid:$uid})-[latest:LATEST]->(old:StudyValue)"
            "-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document) DELETE latest "
            "CREATE (value:StudyValue), (root)-[:LATEST]->(value), "
            "(root)-[:HAS_VERSION {version:'1.0',status:'RELEASED'}]->(value), "
            "(value)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document)",
            {"uid": uid},
        )
        repository.create_or_update_study_definition_document(uid, 1, 0, version="1.0")
        current = repository.get_authored_documents(uid, "1.0")
        assert current.authored_documents_hash == first_hash
        assert current.authored_documents_json == first_json
        assert current.protocol_header_major_version == 1
        assert (
            repository.get_authored_documents(uid, "0.1").authored_documents_json
            == first_json
        )
        content.documents[1].versions[0].version = "2.0"
        content.narrative_content_items[
            0
        ].text += " Synthetic amendment preserves 0 mg/day."
        second_json, second_hash = content.model_dump_json(), content.content_hash()
        repository.save_authored_documents(
            uid, second_json, second_hash, first_hash, "Synthetic amendment"
        )
        assert (
            repository.get_authored_documents(uid).authored_documents_json
            == second_json
        )
        assert (
            repository.get_authored_documents(uid, "0.1").authored_documents_json
            == first_json
        )
        with pytest.raises(AlreadyExistsException):
            repository.save_authored_documents(
                uid, first_json, first_hash, first_hash, "Stale update"
            )
        before_count = db.cypher_query(
            "MATCH (n:StudyDefinitionDocument) RETURN count(n)"
        )[0][0][0]
        with pytest.raises(RuntimeError, match="rollback qualification"):
            with db.transaction:
                repository.save_authored_documents(
                    uid, first_json, first_hash, second_hash, "Rolled-back edit"
                )
                raise RuntimeError("rollback qualification")
        assert (
            repository.get_authored_documents(uid).authored_documents_hash
            == second_hash
        )
        assert (
            db.cypher_query("MATCH (n:StudyDefinitionDocument) RETURN count(n)")[0][0][
                0
            ]
            == before_count
        )

    def writer(index):
        db.set_connection(url=settings.neo4j_dsn)
        try:
            with request_cycle_context({"auth": auth}):
                candidate = content.model_copy(deep=True)
                candidate.narrative_content_items[0].text += f" Candidate {index}."
                try:
                    StudyDefinitionDocumentRepository().save_authored_documents(
                        uid,
                        candidate.model_dump_json(),
                        candidate.content_hash(),
                        second_hash,
                        f"Concurrent synthetic writer {index}",
                    )
                    return "saved"
                except AlreadyExistsException:
                    return "conflict"
        finally:
            db.close_connection()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(writer, [1, 2])) == ["conflict", "saved"]
    db.set_connection(url=settings.neo4j_dsn)
    with request_cycle_context({"auth": auth}):
        assert (
            StudyDefinitionDocumentRepository()
            .get_authored_documents(uid, "0.1")
            .authored_documents_hash
            == first_hash
        )
    actions, _ = db.cypher_query(
        "MATCH (:StudyRoot {uid:$uid})-[:AUDIT_TRAIL]->(action:StudyAction) RETURN action.author_id, labels(action)",
        {"uid": uid},
    )
    assert len(actions) == 4
    assert all(row[0] == "authored-fixture-writer" for row in actions)
    assert sum("Create" in row[1] for row in actions) == 1
    db.close_connection()
