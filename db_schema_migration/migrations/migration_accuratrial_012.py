"""Index authored control pools and adaptation references (plan item O7)."""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "study-control-pool-and-adaptation-reference-index"
QUERIES = (
    "CREATE INDEX index_StudyControlPool_uid IF NOT EXISTS "
    "FOR (n:StudyControlPool) ON (n.uid)",
    "CREATE INDEX index_StudyAdaptationReference_uid IF NOT EXISTS "
    "FOR (n:StudyAdaptationReference) ON (n.uid)",
)


def main():
    for query in QUERIES:
        logger.info(query)
        DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
