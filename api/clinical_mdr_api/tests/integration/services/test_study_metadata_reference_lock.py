"""Synthetic two-transaction checks against an explicitly isolated Neo4j database."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from neo4j import GraphDatabase, Query

from clinical_mdr_api.services.integrations import (
    study_metadata_reference_lock as locks,
)

URI = os.environ.get("OSB_METADATA_CONCURRENCY_URI", "")
pytestmark = pytest.mark.skipif(
    not URI, reason="Requires the isolated metadata concurrency database"
)


class TransactionAdapter:
    def __init__(self, transaction):
        self.transaction = transaction

    def cypher_query(self, query, parameters):
        result = self.transaction.run(query, parameters)
        columns = result.keys()
        return [list(row.values()) for row in result], columns


@pytest.fixture(name="graph")
def isolated_graph():
    # This suite creates and deletes only its synthetic graph. Never accept the
    # normal OSB DSN or a client database as an accidental test target.
    assert URI == "bolt://codex-osb-metadata-db-20260910:7687"
    driver = GraphDatabase.driver(URI, auth=None, connection_timeout=5)
    fixture_id = uuid4().hex
    with driver.session(database="neo4j") as session:
        session.run(
            """
            CREATE (library:Library:MetadataLockFixture {fixture_id:$id,name:$id}),
              (term:CTTermRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-term'}),
              (term_name:CTTermNameRoot:MetadataLockFixture {fixture_id:$id}),
              (term_value:CTTermNameValue:MetadataLockFixture {fixture_id:$id,key:'term-name',name:'Original'}),
              (attrs:CTTermAttributesRoot:MetadataLockFixture {fixture_id:$id}),
              (attrs_value:CTTermAttributesValue:MetadataLockFixture {fixture_id:$id,key:'term-attributes'}),
              (list:CTCodelistRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-list',key:'codelist'}),
              (membership:CTCodelistTerm:MetadataLockFixture {fixture_id:$id,key:'membership'}),
              (sibling_membership:CTCodelistTerm:MetadataLockFixture {fixture_id:$id}),
              (sibling:CTTermRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-sibling'}),
              (sibling_name:CTTermNameRoot:MetadataLockFixture {fixture_id:$id}),
              (sibling_value:CTTermNameValue:MetadataLockFixture {fixture_id:$id,key:'sibling-name'}),
              (dictionary:DictionaryTermRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-dictionary'}),
              (dictionary_value:DictionaryTermValue:MetadataLockFixture {fixture_id:$id,key:'dictionary-name'}),
              (dictionary_list:DictionaryCodelistRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-dictionary-list'}),
              (source:UnitDefinitionRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-source'}),
              (source_value:UnitDefinitionValue:MetadataLockFixture {fixture_id:$id,key:'source-unit'}),
              (canonical:UnitDefinitionRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-canonical'}),
              (canonical_value:UnitDefinitionValue:MetadataLockFixture {fixture_id:$id,key:'canonical-unit'}),
              (ucum:DictionaryTermRoot:UCUMTermRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-ucum'}),
              (ucum_value:DictionaryTermValue:MetadataLockFixture {fixture_id:$id,key:'ucum'}),
              (subset_context:CTTermContext:MetadataLockFixture {fixture_id:$id,key:'subset-context'}),
              (subset:CTTermRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-subset',key:'subset'}),
              (dimension_context:CTTermContext:MetadataLockFixture {fixture_id:$id}),
              (dimension:CTTermRoot:MetadataLockFixture {fixture_id:$id,uid:$id+'-dimension',key:'dimension'}),
              (term)-[:HAS_NAME_ROOT]->(term_name)-[:LATEST_FINAL]->(term_value),
              (term)-[:HAS_ATTRIBUTES_ROOT]->(attrs)-[:LATEST_FINAL]->(attrs_value),
              (list)-[:HAS_TERM]->(membership)-[:HAS_TERM_ROOT]->(term),
              (list)-[:HAS_TERM]->(sibling_membership)-[:HAS_TERM_ROOT]->(sibling),
              (sibling)-[:HAS_NAME_ROOT]->(sibling_name)-[:LATEST_FINAL]->(sibling_value),
              (dictionary_list)-[:HAS_TERM]->(dictionary)-[:LATEST_FINAL]->(dictionary_value),
              (source)-[:LATEST_FINAL]->(source_value),
              (canonical)-[:LATEST_FINAL]->(canonical_value),
              (source_value)-[:HAS_UCUM_TERM]->(ucum)-[:LATEST_FINAL]->(ucum_value),
              (source_value)-[:HAS_UNIT_SUBSET]->(subset_context)-[:HAS_SELECTED_TERM]->(subset),
              (source_value)-[:HAS_CT_DIMENSION]->(dimension_context)-[:HAS_SELECTED_TERM]->(dimension),
              (library)-[:CONTAINS_TERM]->(term),
              (library)-[:CONTAINS_CODELIST]->(list),
              (library)-[:CONTAINS_DICTIONARY_TERM]->(dictionary),
              (library)-[:CONTAINS_CONCEPT]->(source),
              (library)-[:CONTAINS_CONCEPT]->(canonical)
            """,
            id=fixture_id,
        ).consume()
    try:
        yield driver, fixture_id
    finally:
        with driver.session(database="neo4j") as session:
            count = session.run(
                "MATCH (n:MetadataLockFixture {fixture_id:$id}) RETURN count(n) AS count",
                id=fixture_id,
            ).single()["count"]
            assert 0 < count < 100
            session.run(
                "MATCH (n:MetadataLockFixture {fixture_id:$id}) DETACH DELETE n",
                id=fixture_id,
            ).consume()
        driver.close()


@pytest.mark.parametrize(
    "key",
    [
        "term-name",
        "term-attributes",
        "codelist",
        "membership",
        "sibling-name",
        "dictionary-name",
        "source-unit",
        "canonical-unit",
        "ucum",
        "subset-context",
        "subset",
        "dimension",
    ],
)
def test_reference_dependencies_stay_locked_until_the_mapping_transaction_commits(
    graph, monkeypatch, key
):
    driver, fixture_id = graph
    bindings = [
        {
            "kind": "termRef",
            "identity": {"uid": fixture_id + "-term", "version": "1.0"},
        },
        {
            "kind": "dictionaryRef",
            "identity": {"uid": fixture_id + "-dictionary", "version": "1.0"},
        },
        {
            "kind": "unitRef",
            "identity": {
                "uid": fixture_id + "-canonical",
                "version": "1.0",
                "sourceUnitIdentity": {"uid": fixture_id + "-source", "version": "1.0"},
            },
        },
    ]
    query_marker = "metadata-reference-writer-" + uuid4().hex
    started = threading.Event()

    def edit_reference():
        with driver.session(database="neo4j") as session:
            started.set()
            session.run(
                Query(
                    f"/* {query_marker} */ MATCH (n:MetadataLockFixture {{fixture_id:$id,key:$key}}) "
                    "SET n.concurrent_revision=2 RETURN n.concurrent_revision",
                    timeout=10,
                ),
                id=fixture_id,
                key=key,
            ).consume()

    with ThreadPoolExecutor(max_workers=1) as pool, driver.session(
        database="neo4j"
    ) as session:
        with session.begin_transaction(timeout=10) as transaction:
            monkeypatch.setattr(locks, "db", TransactionAdapter(transaction))
            locks.lock_study_metadata_references(bindings)
            future = pool.submit(edit_reference)
            assert started.wait(2)
            blocked = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with driver.session(database="neo4j") as monitor:
                    active = monitor.run(
                        "SHOW TRANSACTIONS YIELD currentQuery,status "
                        "WHERE currentQuery CONTAINS $marker RETURN status",
                        marker=query_marker,
                    ).data()
                if any(str(row["status"]).startswith("Blocked") for row in active):
                    blocked = True
                    break
                if future.done():
                    break
                time.sleep(0.02)
            assert (
                blocked
            ), f"The concurrent edit of {key} did not wait for the mapping transaction"
            assert (
                transaction.run(
                    "MATCH (n:MetadataLockFixture {fixture_id:$id,key:$key}) RETURN n.concurrent_revision AS revision",
                    id=fixture_id,
                    key=key,
                ).single()["revision"]
                is None
            )
            transaction.commit()
        future.result(timeout=10)
        assert (
            session.run(
                "MATCH (n:MetadataLockFixture {fixture_id:$id,key:$key}) RETURN n.concurrent_revision AS revision",
                id=fixture_id,
                key=key,
            ).single()["revision"]
            == 2
        )


def test_missing_reference_is_rejected_without_a_success_receipt(graph, monkeypatch):
    from clinical_mdr_api.services.integrations.candidate_set import (
        OsbCandidateSetError,
    )

    driver, fixture_id = graph
    with driver.session(
        database="neo4j"
    ) as session, session.begin_transaction() as transaction:
        monkeypatch.setattr(locks, "db", TransactionAdapter(transaction))
        with pytest.raises(OsbCandidateSetError) as error:
            locks.lock_study_metadata_references(
                [
                    {"kind": "termRef", "identity": {"uid": fixture_id + "-term"}},
                    {"kind": "termRef", "identity": {"uid": fixture_id + "-missing"}},
                ]
            )
        assert error.value.code == "OSB_STUDY_METADATA_REFERENCE_CHANGED"
