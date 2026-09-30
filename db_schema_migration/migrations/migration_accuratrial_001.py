"""Schema constraints for governed Proposal V2 context and review records."""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-proposal-v2-review-constraints"


def main():
    logger.info("Adding Proposal V2 review constraints to DB '%s'", os.environ["DATABASE_NAME"])
    for query in (
        "CREATE CONSTRAINT constraint_OsbMappingContextSnapshot_context_hash IF NOT EXISTS "
        "FOR (n:OsbMappingContextSnapshot) REQUIRE (n.context_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbProposalReview_proposal_hash IF NOT EXISTS "
        "FOR (n:OsbProposalReview) REQUIRE (n.proposal_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbProposalReviewObject_object_key IF NOT EXISTS "
        "FOR (n:OsbProposalReviewObject) REQUIRE (n.object_key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbProposalReviewDecision_decision_id IF NOT EXISTS "
        "FOR (n:OsbProposalReviewDecision) REQUIRE (n.decision_id) IS NODE KEY",
    ):
        logger.info(query)
        DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()