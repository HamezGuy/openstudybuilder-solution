"""Actual native proposal intake/review/association in a uniquely owned graph."""
import copy
from unittest.mock import patch
from uuid import uuid4
import pytest
from neomodel import db
from clinical_mdr_api.tests.integration.services import test_native_item_observation_neo4j as base
from clinical_mdr_api.tests.integration.services.test_selected_activity_item_observation_neo4j import selected
from clinical_mdr_api.tests.unit.services.test_governed_item_association import association_fixture, current_request
from clinical_mdr_api.models.integrations.proposal_review import ProposalReviewIntake, ProposalObjectDecisionInput
from clinical_mdr_api.services.integrations.proposal_review import ProposalReviewService, Neo4jProposalReviewRepository
from clinical_mdr_api.domain_repositories.integrations.governed_item_association import GovernedItemAssociationRepository
from clinical_mdr_api.services.integrations.governed_item_association import GovernedItemAssociationService
from clinical_mdr_api.services.integrations.native_item_observation import NativeItemObservationError
from clinical_mdr_api.tests.fixtures.osb_proposal_v21 import OPENAPI_HASH


def select_original_review(f, action="selected_candidate"):
    f.native_review.decide(f.proposal["proposalHash"], f.item["proposalObjectId"],
        ProposalObjectDecisionInput(action=action, candidate_key=f.candidate["candidateKey"] if action=="selected_candidate" else None,
            note="Authored review change" if action != "selected_candidate" else None, signature_id="session-1"), f.principal)
    latest = next(d for d in Neo4jProposalReviewRepository.list_decisions(f.proposal["proposalHash"])
                  if d["proposal_object_id"] == f.item["proposalObjectId"])
    return latest


@pytest.fixture()
def associated_native():
    f = association_fixture()
    # Only substitute authored input factory into the existing isolated graph
    # fixture. Native production queries and original review methods execute.
    with patch.object(base, "fixture", return_value=f):
        generator = base.native.__wrapped__()
        f = next(generator)
    try:
        selected.__wrapped__(f)
        f.native_review = ProposalReviewService()
        f.native_review.intake(ProposalReviewIntake(proposal=f.proposal, worker_id="authored-native-fixture"),
            live_openapi_hash=OPENAPI_HASH, principal=f.principal)
        latest = select_original_review(f)
        f.review = f.review.model_copy(update={"selector":f.review.selector.model_copy(update={
            "reviewDecisionId":latest["decision_id"], "reviewDecisionHash":latest["decision_content_hash"]})})
        f.repository = GovernedItemAssociationRepository()
        f.service = GovernedItemAssociationService(f.repository, lambda:f.auth, lambda:f.now[0], lambda:f.now[0], review=True)
        yield f
    finally:
        # The service's real new native records have no test-only properties.
        # This entire named database was asserted empty before fixture seeding.
        # Verify the only additional labels are exactly this authored review
        # slice before marking them for the existing exact-fixture cleanup.
        rows = db.cypher_query("MATCH (n) WHERE n.fixture IS NULL RETURN labels(n) LIMIT 4097")[0]
        allowed = {"OsbProposalReview", "OsbProposalReviewObject", "OsbProposalReviewDecision", "OsbGovernedItemAssociation"}
        assert len(rows) < 4097 and all(set(row[0]) <= allowed for row in rows)
        db.cypher_query("MATCH (n) WHERE n.fixture IS NULL SET n.fixture=$fixture", f.params)
        try: next(generator)
        except StopIteration: pass


def create_current(f):
    result = f.service.observe(f.review)
    f.service.review = False
    return result, current_request(f,result)


def test_actual_intake_review_append_current_observation_and_exact_replay(associated_native):
    f = associated_native
    original = Neo4jProposalReviewRepository.list_decisions(f.proposal["proposalHash"])
    result = f.service.observe(f.review)
    repeated = f.service.observe(f.review)
    assert repeated["associationHash"] == result["associationHash"]
    assert db.cypher_query("MATCH (n:OsbGovernedItemAssociation) RETURN count(n)")[0] == [[1]]
    assert Neo4jProposalReviewRepository.list_decisions(f.proposal["proposalHash"]) == original
    f.service.review = False
    observed = f.service.observe(current_request(f,result))
    assert observed["assurance"] == "native-session-review"
    assert observed["cslPlanCustodyVerified"] is False and observed["clinicalApprovalVerified"] is False


def test_existing_supersedes_invalidates_old_association_then_explicit_new_review(associated_native):
    f = associated_native
    _, old = create_current(f)
    newer = select_original_review(f)
    assert db.cypher_query("MATCH (:OsbProposalReviewDecision)-[r:SUPERSEDES]->() RETURN count(r)")[0] == [[1]]
    with pytest.raises(NativeItemObservationError): f.service.observe(old)
    f.service.review = True
    fresh = f.review.model_copy(update={"associationId":str(uuid4()), "selector":f.review.selector.model_copy(update={
        "reviewDecisionId":newer["decision_id"], "reviewDecisionHash":newer["decision_content_hash"]})})
    assert f.service.observe(fresh)["associationId"] == fresh.associationId
    assert db.cypher_query("MATCH (n:OsbGovernedItemAssociation) RETURN count(n)")[0] == [[2]]


@pytest.mark.parametrize("query", [
    "MATCH (o:OsbProposalReviewObject)-[:LATEST_DECISION]->(d) CREATE (o)-[:LATEST_DECISION]->(d)",
    "MATCH (o:OsbProposalReviewObject)-[r:LATEST_DECISION]->() DELETE r",
    "MATCH (d:OsbProposalReviewDecision) SET d.actor_id='borrowed'",
    "MATCH (p:OsbProposalReview) SET p.proposal_json='changed'",
    "MATCH (p:OsbProposalReview) SET p.proposal_json=$oversized",
    "MATCH (c:OsbMappingContextSnapshot) SET c.content_json='changed'",
    "MATCH (s:DomainStudyScope) SET s.status='quarantined'",
    "MATCH (i:OdmItemValue) SET i.datatype='string'",
    "MATCH (i:ActivityItem) SET i.text_value='changed'",
    "MATCH (a:OsbGovernedItemAssociation) SET a.payload_json='changed'",
    "MATCH (d)-[:HAS_GOVERNED_ITEM_ASSOCIATION]->(a:OsbGovernedItemAssociation) CREATE (b:OsbGovernedItemAssociation) SET b=properties(a) CREATE (d)-[:HAS_GOVERNED_ITEM_ASSOCIATION]->(b)",
    "MATCH (o:OsbProposalReviewObject)-[:LATEST_DECISION]->(d)-[r:HAS_GOVERNED_ITEM_ASSOCIATION]->(a) MATCH (other:OsbProposalReviewObject) WHERE other<>o DELETE r CREATE (other)-[:LATEST_DECISION]->(d) CREATE (d)-[:HAS_GOVERNED_ITEM_ASSOCIATION]->(a)",
])
def test_actual_current_custody_changes_and_ambiguity_refuse(associated_native, query):
    f = associated_native
    _, current = create_current(f)
    db.cypher_query(query, {"oversized":"x"*65537})
    with pytest.raises(NativeItemObservationError): f.service.observe(current)


def test_competing_current_association_cannot_overwrite_existing_record(associated_native):
    f = associated_native
    original = f.service.observe(f.review)
    request = f.review.model_copy(update={"associationId":str(uuid4())})
    with pytest.raises(NativeItemObservationError, match="CONFLICT"): f.service.observe(request)
    assert db.cypher_query("MATCH (a:OsbGovernedItemAssociation) RETURN a.payload_hash")[0] == [[original["associationHash"]]]


def test_actual_current_head_is_rechecked_inside_append_after_read(associated_native):
    f = associated_native
    class Change(GovernedItemAssociationRepository):
        def append_association(self,p,timeout):
            select_original_review(f, "rejected")
            return super().append_association(p,timeout)
    f.service.repository = Change()
    with pytest.raises(NativeItemObservationError, match="APPEND_CONFLICT"): f.service.observe(f.review)
    assert db.cypher_query("MATCH (a:OsbGovernedItemAssociation) RETURN count(a)")[0] == [[0]]


def test_final_native_scope_withdrawal_withholds_success_but_preserves_custody(associated_native):
    f = associated_native
    class Withdraw(GovernedItemAssociationRepository):
        def append_association(self,p,timeout):
            rows = super().append_association(p,timeout)
            db.cypher_query("MATCH (s:DomainStudyScope) SET s.status='quarantined'")
            return rows
    f.service.repository = Withdraw()
    with pytest.raises(NativeItemObservationError): f.service.observe(f.review)
    assert db.cypher_query("MATCH (a:OsbGovernedItemAssociation) RETURN count(a)")[0] == [[1]]


@pytest.mark.parametrize("mode", ["review", "read"])
def test_actual_final_association_read_scope_withdrawal_is_checked_again(associated_native, mode):
    f = associated_native
    request = f.review
    if mode == "read":
        _, request = create_current(f)
    class WithdrawLast(GovernedItemAssociationRepository):
        def __init__(self): super().__init__(); self.reads = 0
        def associations(self,p,timeout):
            rows = super().associations(p,timeout)
            self.reads += 1
            if self.reads == 2:
                db.cypher_query("MATCH (s:DomainStudyScope) SET s.status='quarantined'")
            return rows
    f.service.repository = WithdrawLast()
    with pytest.raises(NativeItemObservationError, match="SCOPE_WITHDRAWN"): f.service.observe(request)
    assert db.cypher_query("MATCH (a:OsbGovernedItemAssociation) RETURN count(a)")[0] == [[1]]


def test_actual_append_query_checks_deadline_after_current_head_lock(associated_native):
    f = associated_native
    class Expire(GovernedItemAssociationRepository):
        def append_association(self,p,timeout):
            return super().append_association({**p,"credentialExpiryMillis":0},timeout)
    f.service.repository = Expire()
    with pytest.raises(NativeItemObservationError, match="APPEND_CONFLICT"): f.service.observe(f.review)
    assert db.cypher_query("MATCH (a:OsbGovernedItemAssociation) RETURN count(a)")[0] == [[0]]
