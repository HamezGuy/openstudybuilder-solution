"""Real Neo4j persistence test for governed Proposal V2 review records."""

import os
from copy import deepcopy

import pytest
from neomodel import db

from clinical_mdr_api.models.integrations.proposal_review import (
    ProposalExecutionAuthorizationInput,
    ProposalObjectDecisionInput,
    ProposalReviewIntake,
)
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.services.integrations.proposal_review import (
    Neo4jProposalReviewRepository,
    ProposalReviewPrincipal,
    ProposalReviewService,
)
from clinical_mdr_api.tests.fixtures.osb_proposal_v21 import (
    CONTEXT_HASH,
    OPENAPI_HASH,
    build_context,
    build_proposal,
)
from common.database import configure_database

TEST_DSN = os.environ.get("NEO4J_PROPOSAL_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not TEST_DSN,
    reason="disposable Proposal V2 Neo4j DSN is not configured",
)


def _principal(actor_id, signature_id):
    return ProposalReviewPrincipal(
        actor_id=actor_id,
        human_user_id=actor_id,
        token_id=signature_id,
        tenant_id="tenant-1",
        scoped_study_ids=frozenset(
            {"study-1", "Study_Proposal_Authorization_Test"}
        ),
        organization_ids=frozenset(),
        roles=frozenset({"Study.Read", "Study.Write"}),
        authentication_verified=True,
    )


def _values():
    return OPENAPI_HASH, CONTEXT_HASH, build_context(), build_proposal()


def _set_authority_mode(proposal, authority_mode):
    proposal = deepcopy(proposal)
    proposal["authorityMode"] = authority_mode
    proposal["sourceBuildHash"] = canonical_hash(
        {
            "tenantId": proposal["tenantId"],
            "studyId": proposal["studyId"],
            "projectId": proposal["projectId"],
            "authorityMode": authority_mode,
            "sourceRunIds": proposal["sourceRunIds"],
            "sourceDocuments": proposal["sourceDocuments"],
            "sourceFactRefs": proposal["sourceFactRefs"],
        }
    )
    objects = []
    for section, values in proposal["sections"].items():
        for item in values:
            item["proposalObjectId"] = canonical_hash(
                {
                    "sourceBuildHash": proposal["sourceBuildHash"],
                    "conceptId": item["conceptId"],
                    "targetKey": item["targetKey"],
                    "section": section,
                    "proposedResourceType": item["mapping"]["proposedResourceType"],
                }
            )
            objects.append(item)
    proposal["proposalId"] = canonical_hash(
        {
            "tenantId": proposal["tenantId"],
            "studyId": proposal["studyId"],
            "projectId": proposal["projectId"],
            "authorityMode": authority_mode,
            "sourceBuildHash": proposal["sourceBuildHash"],
            "osbOpenApiHash": proposal["osbOpenApiHash"],
            "osbMappingContextHash": proposal["osbMappingContextHash"],
            "proposalObjects": objects,
            "sourceDispositions": proposal["reconciliation"]["dispositions"],
        }
    )
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = canonical_hash(content)
    return proposal


def _cleanup(context_hash=None, proposal_hash=None):
    db.cypher_query(
        """
        MATCH (context:OsbMappingContextSnapshot {context_hash: $context_hash})
        OPTIONAL MATCH (proposal:OsbProposalReview {proposal_hash: $proposal_hash})
        OPTIONAL MATCH (proposal)-[:HAS_REVIEW_OBJECT]->(object)
        OPTIONAL MATCH (object)-[:HAS_DECISION]->(decision)
        OPTIONAL MATCH (proposal)-[:HAS_EXECUTION_AUTHORIZATION]->(authorization)
        DETACH DELETE decision, authorization, object, proposal, context
        """,
        {"context_hash": context_hash or "", "proposal_hash": proposal_hash or ""},
    )


def _cleanup_target(study_uid="Study_Proposal_Authorization_Test"):
    db.cypher_query(
        """
        MATCH (study:StudyRoot {uid: $study_uid})
        OPTIONAL MATCH (study)-[:LATEST_DRAFT]->(value:StudyValue)
        DETACH DELETE value, study
        """,
        {"study_uid": study_uid},
    )


def test_real_neo4j_review_persistence_is_idempotent_and_superseding():
    db.set_connection(driver=configure_database(TEST_DSN))
    constraints = (
        ("OsbMappingContextSnapshot", "context_hash"),
        ("OsbProposalReview", "proposal_hash"),
        ("OsbProposalReviewObject", "object_key"),
        ("OsbProposalReviewDecision", "decision_id"),
        ("OsbProposalExecutionAuthorization", "authorization_id"),
    )
    for label, prop in constraints:
        db.cypher_query(
            f"CREATE CONSTRAINT constraint_{label}_{prop} IF NOT EXISTS "
            f"FOR (node:{label}) REQUIRE (node.{prop}) IS NODE KEY"
        )

    openapi_hash, context_hash, context, proposal = _values()
    _cleanup(context_hash, proposal["proposalHash"])
    repository = Neo4jProposalReviewRepository()
    service = ProposalReviewService(repository)
    activity_object = proposal["sections"]["activitiesItems"][0]
    item_object = proposal["sections"]["odm"][0]
    try:
        repository.save_context(context_hash, context)
        repository.save_context(context_hash, context)
        first = service.intake(
            ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
            openapi_hash,
            principal=_principal("reviewer-1", "intake-token"),
        )
        second = service.intake(
            ProposalReviewIntake(proposal=proposal, worker_id="worker-2"),
            openapi_hash,
            principal=_principal("reviewer-1", "intake-token"),
        )
        assert first == second
        assert first.object_count == 2

        service.decide(
            proposal["proposalHash"],
            activity_object["proposalObjectId"],
            ProposalObjectDecisionInput(
                action="selected_candidate",
                candidate_key=activity_object["mapping"]["candidates"][0][
                    "candidateKey"
                ],
                signature_id="signature-1",
            ),
            principal=_principal("reviewer-1", "signature-1"),
        )
        complete = service.decide(
            proposal["proposalHash"],
            item_object["proposalObjectId"],
            ProposalObjectDecisionInput(
                action="create_request",
                note="Create a Sponsor draft ODM item",
                signature_id="signature-2",
            ),
            principal=_principal("reviewer-2", "signature-2"),
        )
        assert complete.review_complete is True
        assert complete.native_execution_ready is False

        revised = service.decide(
            proposal["proposalHash"],
            activity_object["proposalObjectId"],
            ProposalObjectDecisionInput(
                action="selected_candidate",
                candidate_key=activity_object["mapping"]["candidates"][0][
                    "candidateKey"
                ],
                note="Confirmed after second review",
                signature_id="signature-3",
            ),
            principal=_principal("reviewer-3", "signature-3"),
        )
        activity = next(
            item
            for item in revised.objects
            if item.proposal_object_id == activity_object["proposalObjectId"]
        )
        assert activity.latest_decision.actor_id == "reviewer-3"
        assert activity.latest_decision.signature_verified is True
        assert len(activity.latest_decision.decision_content_hash) == 64

        counts, _ = db.cypher_query("""
            MATCH (proposal:OsbProposalReview)
            OPTIONAL MATCH (proposal)-[:HAS_REVIEW_OBJECT]->(object)
            OPTIONAL MATCH (object)-[:HAS_DECISION]->(decision)
            WITH count(DISTINCT proposal) AS proposals,
                 count(DISTINCT object) AS objects,
                 count(DISTINCT decision) AS decisions
            MATCH ()-[latest:LATEST_DECISION]->()
            WITH proposals, objects, decisions, count(latest) AS latest_edges
            MATCH ()-[supersedes:SUPERSEDES]->()
            RETURN proposals, objects, decisions, latest_edges,
                   count(supersedes) AS supersedes_edges
            """)
        assert counts[0] == [1, 2, 3, 2, 1]
    finally:
        _cleanup(context_hash, proposal["proposalHash"])


def test_real_neo4j_authorization_is_bound_to_owned_current_draft():
    db.set_connection(driver=configure_database(TEST_DSN))
    openapi_hash, context_hash, context, raw_proposal = _values()
    proposal = _set_authority_mode(raw_proposal, "enforced")
    target_uid = "Study_Proposal_Authorization_Test"
    _cleanup(context_hash, proposal["proposalHash"])
    _cleanup_target(target_uid)
    repository = Neo4jProposalReviewRepository()
    service = ProposalReviewService(repository)
    try:
        db.cypher_query(
            """
            MERGE (reviewer:User {user_id: 'reviewer-1'})
            CREATE (study:StudyRoot {uid: $study_uid})
            CREATE (value:StudyValue)
            CREATE (study)-[:LATEST_DRAFT {
                start_date: datetime('2026-08-10T12:00:00Z'),
                end_date: null,
                status: 'DRAFT',
                author_id: 'reviewer-1'
            }]->(value)
            CREATE (study)-[:HAS_VERSION {
                start_date: datetime('2026-08-10T12:00:00Z'),
                end_date: null,
                status: 'DRAFT',
                author_id: 'reviewer-1'
            }]->(value)
            """,
            {"study_uid": target_uid},
        )
        repository.save_context(context_hash, context)
        service.intake(
            ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
            openapi_hash,
            principal=_principal("reviewer-1", "intake-token"),
        )
        activity = proposal["sections"]["activitiesItems"][0]
        item = proposal["sections"]["odm"][0]
        service.decide(
            proposal["proposalHash"],
            activity["proposalObjectId"],
            ProposalObjectDecisionInput(
                action="selected_candidate",
                candidate_key=activity["mapping"]["candidates"][0]["candidateKey"],
                signature_id="decision-token-1",
            ),
            principal=_principal("reviewer-1", "decision-token-1"),
        )
        reviewed = service.decide(
            proposal["proposalHash"],
            item["proposalObjectId"],
            ProposalObjectDecisionInput(
                action="not_applicable",
                note="No ODM mutation is authorized",
                signature_id="decision-token-2",
            ),
            principal=_principal("reviewer-1", "decision-token-2"),
        )
        ready = service.authorize_execution(
            proposal["proposalHash"],
            ProposalExecutionAuthorizationInput(
                target_study_uid=target_uid,
                target_study_version="DRAFT",
                expected_decision_set_hash=reviewed.decision_set_hash,
                signature_id="authorization-token",
            ),
            principal=_principal("reviewer-1", "authorization-token"),
        )
        assert ready.native_execution_ready is True
        assert ready.target_ownership_verified is True
        counts, _ = db.cypher_query(
            """
            MATCH (:OsbProposalReview {proposal_hash: $proposal_hash})
                  -[:LATEST_EXECUTION_AUTHORIZATION]->(authorization)
                  -[:TARGETS_STUDY]->(:StudyRoot {uid: $study_uid})
            RETURN count(authorization),
                   authorization.authorization_content_hash
            """,
            {"proposal_hash": proposal["proposalHash"], "study_uid": target_uid},
        )
        assert counts[0][0] == 1
        assert len(counts[0][1]) == 64

        db.cypher_query(
            """
            MATCH (:StudyRoot {uid: $study_uid})-[draft:LATEST_DRAFT]->()
            SET draft.author_id = 'different-reviewer'
            """,
            {"study_uid": target_uid},
        )
        still_owned = service.get_status(proposal["proposalHash"])
        assert still_owned.native_execution_ready is True
        assert still_owned.target_ownership_verified is True

        db.cypher_query(
            """
            MATCH (:StudyRoot {uid: $study_uid})-[draft:LATEST_DRAFT]->()
            SET draft.start_date = datetime('2026-08-10T12:01:00Z')
            """,
            {"study_uid": target_uid},
        )
        stale = service.get_status(proposal["proposalHash"])
        assert stale.native_execution_ready is False
        assert stale.target_ownership_verified is True
        assert stale.target_snapshot_verified is False
        assert "OSB_STUDY_DRAFT_SNAPSHOT_STALE" in stale.execution_blockers
    finally:
        _cleanup(context_hash, proposal["proposalHash"])
        _cleanup_target(target_uid)
