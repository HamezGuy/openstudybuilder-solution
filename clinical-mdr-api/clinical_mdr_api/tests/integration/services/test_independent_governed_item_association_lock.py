"""Independent actual lock race; only an explicit disposable Community graph."""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from neo4j import GraphDatabase, Query
from neomodel import db

from clinical_mdr_api.tests.integration.services.test_governed_item_association_neo4j import associated_native
from clinical_mdr_api.domain_repositories.integrations.governed_item_association import GovernedItemAssociationRepository
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError


@pytest.mark.parametrize("withdraw", [None, "scope", "binding"])
def test_actual_append_rechecks_native_scope_after_waiting_for_original_review_lock(associated_native, withdraw):
    f = associated_native
    dsn = os.environ["OSB_ITEM_OBSERVATION_FIXTURE_DSN"]
    parsed = urlsplit(dsn)
    assert parsed.hostname == "127.0.0.1" and os.environ["OSB_ITEM_OBSERVATION_FIXTURE_CONTAINER"].startswith("codex-osb-item-community-")
    entered = threading.Event()
    class Entered(GovernedItemAssociationRepository):
        def append_association(self, params, timeout):
            entered.set()
            return super().append_association(params, timeout)
    f.service.repository = Entered()
    # Installed neomodel holds driver/session state in ContextVars. Configure
    # only this worker context; monitoring/locking uses a separate driver.
    def append():
        db.set_connection(url=dsn)
        try:
            try:
                return {"result": f.service.observe(f.review)}
            except Exception as error:
                return {"error": type(error).__name__, "message": str(error)}
        finally:
            db.close_connection()
    evidence = {"withdrawal": withdraw}
    with GraphDatabase.driver(f"bolt://{parsed.hostname}:{parsed.port}", auth=(parsed.username, parsed.password)) as driver:
        with driver.session(database="neo4j") as lock_session, ThreadPoolExecutor(max_workers=1) as pool:
            blocker = lock_session.begin_transaction(timeout=15)
            pending = None
            try:
                blocker.run("""MATCH (object:OsbProposalReviewObject {proposal_object_id:$objectId})
                    SET object.independent_test_lock_epoch=coalesce(object.independent_test_lock_epoch,0)+1
                    RETURN object.proposal_object_id""", objectId=f.item["proposalObjectId"]).consume()
                pending = pool.submit(append)
                assert entered.wait(8), "Actual service did not reach append while original object lock held"
                blocked = []
                until = time.monotonic() + 3
                while time.monotonic() < until:
                    with driver.session(database="neo4j") as monitor:
                        blocked = [dict(row) for row in monitor.run("""SHOW TRANSACTIONS YIELD currentQuery,status,resourceInformation
                            WHERE currentQuery CONTAINS 'CREATE (association:OsbGovernedItemAssociation'
                            RETURN status,resourceInformation""") if "Blocked" in row["status"]]
                    if blocked: break
                    time.sleep(0.02)
                assert len(blocked) == 1, f"Expected one observed blocked actual append, got {blocked}"
                evidence["blockedAppend"] = blocked
                if withdraw:
                    label = "DomainStudyScope" if withdraw == "scope" else "PlatformNativeStudyBinding"
                    with driver.session(database="neo4j") as writer:
                        # The independent withdrawal must commit before releasing
                        # the original review object lock, otherwise no race is proved.
                        count = writer.run(Query(f"MATCH (n:{label}) SET n.status='retired' RETURN count(n) AS count", timeout=1)).single()["count"]
                        assert count == 1
                    evidence["withdrawalCommittedBeforeObjectUnlock"] = True
                blocker.commit()
                evidence["outcome"] = pending.result(timeout=8)
                with driver.session(database="neo4j") as reader:
                    evidence["persistedAssociations"] = reader.run("MATCH (n:OsbGovernedItemAssociation) RETURN count(n) AS count").single()["count"]
            finally:
                if not blocker.closed(): blocker.rollback()
                if pending is not None: pending.result(timeout=8)
    print(json.dumps(evidence, default=str))
    if withdraw:
        assert evidence["outcome"].get("error") == "NativeItemObservationError", evidence
        assert evidence["persistedAssociations"] == 0, evidence
    else:
        assert "result" in evidence["outcome"], evidence
        assert evidence["persistedAssociations"] == 1, evidence


def test_actual_graph_oversized_authority_record_refuses_before_any_append(associated_native):
    f = associated_native
    scopes = [str(uuid4()) for _ in range(600)]
    f.auth.access_token_claims.study_ids.extend(scopes)
    f.auth.user.study_ids.update(scopes)
    with pytest.raises(NativeItemObservationError, match="RECORD_LIMIT"):
        f.service.observe(f.review)
    assert db.cypher_query("MATCH (n:OsbGovernedItemAssociation) RETURN count(n)")[0] == [[0]]
    # No append epoch or record mutation is used to discover the record limit.
    assert db.cypher_query("MATCH (s:StudyRoot) RETURN s.governed_item_association_epoch")[0] == [[None]]


@pytest.mark.parametrize("node,property_name,replacement", [
    ("DomainStudyScope", "status", "retired"),
    ("DomainStudyScope", "tenant_id", "foreign-tenant"),
    ("PlatformNativeStudyBinding", "status", "retired"),
    ("PlatformNativeStudyBinding", "native_version", "2.0"),
    ("PlatformNativeStudyBinding", "binding_id", "replacement-binding"),
    ("PlatformNativeStudyBinding", "namespace", "foreign-namespace"),
])
def test_current_authority_pins_are_reread_after_own_node_lock(associated_native, node, property_name, replacement):
    f = associated_native
    dsn = os.environ["OSB_ITEM_OBSERVATION_FIXTURE_DSN"]
    parsed = urlsplit(dsn)
    entered = threading.Event()
    class Entered(GovernedItemAssociationRepository):
        def append_association(self, params, timeout):
            entered.set()
            return super().append_association(params, timeout)
    f.service.repository = Entered()
    def append():
        db.set_connection(url=dsn)
        try:
            try: return {"result": f.service.observe(f.review)}
            except Exception as error: return {"error": type(error).__name__, "message": str(error)}
        finally: db.close_connection()
    evidence = {"lockedNode": node, "changedProperty": property_name}
    with GraphDatabase.driver(f"bolt://{parsed.hostname}:{parsed.port}", auth=(parsed.username, parsed.password)) as driver:
        with driver.session(database="neo4j") as lock_session, ThreadPoolExecutor(max_workers=1) as pool:
            blocker = lock_session.begin_transaction(timeout=15)
            pending = None
            try:
                blocker.run(f"MATCH (n:{node}) SET n.independent_test_lock_epoch=coalesce(n.independent_test_lock_epoch,0)+1 RETURN count(n)").consume()
                pending = pool.submit(append)
                assert entered.wait(8)
                until = time.monotonic() + 3
                blocked = []
                while time.monotonic() < until:
                    with driver.session(database="neo4j") as monitor:
                        blocked = [dict(row) for row in monitor.run("""SHOW TRANSACTIONS YIELD currentQuery,status,resourceInformation
                            WHERE currentQuery CONTAINS 'CREATE (association:OsbGovernedItemAssociation'
                            RETURN status,resourceInformation""") if "Blocked" in row["status"]]
                    if blocked: break
                    time.sleep(0.02)
                assert len(blocked) == 1
                evidence["blockedAppend"] = blocked
                blocker.run(f"MATCH (n:{node}) SET n.{property_name}=$replacement RETURN count(n)", replacement=replacement).consume()
                blocker.commit()
                evidence["outcome"] = pending.result(timeout=8)
                with driver.session(database="neo4j") as reader:
                    evidence["persistedAssociations"] = reader.run("MATCH (n:OsbGovernedItemAssociation) RETURN count(n) AS count").single()["count"]
            finally:
                if not blocker.closed(): blocker.rollback()
                if pending is not None: pending.result(timeout=8)
    print(json.dumps(evidence, default=str))
    assert evidence["outcome"].get("error") == "NativeItemObservationError", evidence
    assert evidence["persistedAssociations"] == 0, evidence


@pytest.mark.parametrize("node", ["DomainStudyScope", "PlatformNativeStudyBinding"])
def test_duplicate_current_authority_created_after_initial_read_refuses_before_record_write(associated_native, node):
    f = associated_native
    class Duplicate(GovernedItemAssociationRepository):
        def append_association(self, params, timeout):
            db.cypher_query(f"MATCH (n:{node}) CREATE (duplicate:{node}) SET duplicate=properties(n)")
            return super().append_association(params, timeout)
    f.service.repository = Duplicate()
    with pytest.raises(NativeItemObservationError, match="APPEND_CONFLICT"):
        f.service.observe(f.review)
    assert db.cypher_query("MATCH (n:OsbGovernedItemAssociation) RETURN count(n)")[0] == [[0]]
