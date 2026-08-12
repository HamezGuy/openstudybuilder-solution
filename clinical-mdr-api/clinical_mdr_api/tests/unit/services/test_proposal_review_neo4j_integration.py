"""Real Neo4j persistence test for governed Proposal V2 review records."""

import os

import pytest
from neomodel import db

from clinical_mdr_api.models.integrations.proposal_review import (
    ProposalObjectDecisionInput,
    ProposalReviewIntake,
)
from clinical_mdr_api.services.integrations.canonical_json import canonical_hash
from clinical_mdr_api.services.integrations.proposal_review import (
    Neo4jProposalReviewRepository,
    ProposalReviewService,
)
from common.database import configure_database

TEST_DSN = os.environ.get("NEO4J_PROPOSAL_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not TEST_DSN,
    reason="disposable Proposal V2 Neo4j DSN is not configured",
)


def _values():
    openapi_hash = "o" * 64
    context = {
        "schemaVersion": "osb-mapping-context/1.0",
        "mappingAuthority": "OpenStudyBuilder",
        "studyUid": None,
        "studyValueVersion": None,
        "osbOpenApiHash": openapi_hash,
        "governed": True,
        "selectedPackages": [],
        "selectedDataModels": [],
        "requestedFamilies": ["activities"],
        "searchStrings": ["blood pressure"],
        "searchCodes": [],
        "maximumCandidatesPerFamily": 10,
        "candidates": {
            "activities": [
                {
                    "resource_family": "activities",
                    "resource_type": "Activity",
                    "uid": "Activity_Review_Test",
                    "version": "1.0",
                    "status": "Final",
                    "library_name": "Sponsor",
                    "label": "Blood Pressure",
                    "code": None,
                    "submission_value": None,
                    "package_uid": None,
                    "package_effective_date": None,
                    "library_name": "Sponsor",
                    "extensible": None,
                    "ucum_code": None,
                }
            ]
        },
        "releaseBlockers": [],
        "warnings": [],
    }
    context_hash = canonical_hash(context)
    candidate = {
        "resourceType": "Activity",
        "uid": "Activity_Review_Test",
        "version": "1.0",
        "packageUid": None,
        "contextHash": context_hash,
        "label": "Blood Pressure",
        "code": None,
    }
    content = {
        "formatVersion": "osb-proposal/2.0",
        "proposalId": "proposal-neo4j-integration",
        "tenantId": "tenant-neo4j-integration",
        "studyId": "study-neo4j-integration",
        "projectId": None,
        "sourceRunIds": ["run-1"],
        "sourceDocumentVersionIds": ["document-1"],
        "previousProposalHash": None,
        "osbOpenApiHash": openapi_hash,
        "osbMappingContextHash": context_hash,
        "authorityMode": "shadow",
        "sections": {
            "activitiesItems": [
                {
                    "proposalObjectId": "object-activity",
                    "source": {
                        "assertionType": "ASSESSMENT",
                        "clinicalDomain": "vital_signs",
                        "exactQuote": "Blood Pressure",
                        "fields": {"assessment": "Blood Pressure"},
                        "label": "Blood Pressure",
                    },
                    "mapping": {
                        "factIds": ["fact-1"],
                        "evidence": [],
                        "proposedResourceType": "StudyActivity",
                        "candidates": [candidate],
                        "selectedCandidate": None,
                        "matchMethod": "exact_text",
                        "confidence": 0.9,
                        "disposition": "review",
                        "reviewerDecision": None,
                    },
                }
            ],
            "odm": [
                {
                    "proposalObjectId": "object-item",
                    "source": {
                        "assertionType": "ASSESSMENT",
                        "clinicalDomain": "vital_signs",
                        "exactQuote": "Blood Pressure",
                        "fields": {"assessment": "Blood Pressure"},
                        "label": "Blood Pressure",
                    },
                    "mapping": {
                        "factIds": ["fact-1"],
                        "evidence": [],
                        "proposedResourceType": "OdmItem",
                        "candidates": [],
                        "selectedCandidate": None,
                        "matchMethod": "none",
                        "confidence": 0.9,
                        "disposition": "unresolved",
                        "reviewerDecision": None,
                    },
                }
            ],
        },
        "reconciliation": {
            "balanced": True,
            "sourceFacts": 1,
            "mappedSourceFacts": 1,
            "proposedObjects": 2,
            "dispositions": [],
        },
        "sourceFactRefs": [{"factId": "fact-1", "factContentHash": "f" * 64}],
    }
    proposal = {**content, "proposalHash": canonical_hash(content)}
    return openapi_hash, context_hash, context, proposal


def _cleanup(context_hash=None, proposal_hash=None):
    db.cypher_query(
        """
        MATCH (context:OsbMappingContextSnapshot {context_hash: $context_hash})
        OPTIONAL MATCH (proposal:OsbProposalReview {proposal_hash: $proposal_hash})
        OPTIONAL MATCH (proposal)-[:HAS_REVIEW_OBJECT]->(object)
        OPTIONAL MATCH (object)-[:HAS_DECISION]->(decision)
        DETACH DELETE decision, object, proposal, context
        """,
        {"context_hash": context_hash or "", "proposal_hash": proposal_hash or ""},
    )


def test_real_neo4j_review_persistence_is_idempotent_and_superseding():
    db.set_connection(driver=configure_database(TEST_DSN))
    constraints = (
        ("OsbMappingContextSnapshot", "context_hash"),
        ("OsbProposalReview", "proposal_hash"),
        ("OsbProposalReviewObject", "object_key"),
        ("OsbProposalReviewDecision", "decision_id"),
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
    try:
        repository.save_context(context_hash, context)
        repository.save_context(context_hash, context)
        first = service.intake(
            ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
            openapi_hash,
        )
        second = service.intake(
            ProposalReviewIntake(proposal=proposal, worker_id="worker-2"),
            openapi_hash,
        )
        assert first == second
        assert first.object_count == 2

        service.decide(
            proposal["proposalHash"],
            "object-activity",
            ProposalObjectDecisionInput(
                action="selected_candidate",
                candidate_uid="Activity_Review_Test",
                signature_id="signature-1",
            ),
            actor_id="reviewer-1",
        )
        complete = service.decide(
            proposal["proposalHash"],
            "object-item",
            ProposalObjectDecisionInput(
                action="create_request",
                note="Create a Sponsor draft ODM item",
                signature_id="signature-2",
            ),
            actor_id="reviewer-2",
        )
        assert complete.review_complete is True
        assert complete.native_execution_ready is False

        revised = service.decide(
            proposal["proposalHash"],
            "object-activity",
            ProposalObjectDecisionInput(
                action="selected_candidate",
                candidate_uid="Activity_Review_Test",
                note="Confirmed after second review",
                signature_id="signature-3",
            ),
            actor_id="reviewer-3",
        )
        activity = next(
            item
            for item in revised.objects
            if item.proposal_object_id == "object-activity"
        )
        assert activity.latest_decision.actor_id == "reviewer-3"
        assert activity.latest_decision.signature_verified is False
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
