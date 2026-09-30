"""Schema constraint for Proposal V2 execution authorization receipts."""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-proposal-v2-execution-authorization-constraint"


def main():
    logger.info(
        "Adding Proposal V2 authorization constraint to DB '%s'",
        os.environ["DATABASE_NAME"],
    )
    query = (
        "CREATE CONSTRAINT "
        "constraint_OsbProposalExecutionAuthorization_authorization_id "
        "IF NOT EXISTS FOR (n:OsbProposalExecutionAuthorization) "
        "REQUIRE (n.authorization_id) IS NODE KEY"
    )
    logger.info(query)
    DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
