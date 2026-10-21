"""Index immutable authored arm lifecycle events without inventing history."""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "study-arm-lifecycle-event-index"


def main():
    query = (
        "CREATE INDEX index_StudyArmLifecycleEvent_uid IF NOT EXISTS "
        "FOR (n:StudyArmLifecycleEvent) ON (n.uid)"
    )
    logger.info(query)
    DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
