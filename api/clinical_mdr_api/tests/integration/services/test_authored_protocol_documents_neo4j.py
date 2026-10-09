"""Native document persistence on a separately owned, initially empty graph.

The external evidence harness starts/stops a loopback Community server. This
test never clears a database and refuses ordinary/shared fixture connections.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from neomodel import db
from starlette_context import request_cycle_context

from clinical_mdr_api.domain_repositories.study_selections.study_definition_document_repository import (
    StudyDefinitionDocumentRepository,
)
from clinical_mdr_api.tests.unit.services.test_authored_protocol_documents import (
    authored_source,
)
from common.auth.dependencies import dummy_access_token_claims, dummy_auth_object
from common.config import settings
from common.exceptions import AlreadyExistsException

pytestmark = pytest.mark.estate_fixture


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
            "MATCH (root:StudyRoot {uid:$uid})-[latest:LATEST]->(old:StudyValue)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document) DELETE latest CREATE (value:StudyValue), (root)-[:LATEST]->(value), (root)-[:HAS_VERSION {version:'1.0',status:'RELEASED'}]->(value), (value)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document)",
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
