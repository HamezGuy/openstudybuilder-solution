"""Create fail-closed native OSB tenant-scope records.

Existing studies cannot be attributed safely from labels, authors, or registry
numbers, so their new scope records are quarantined for explicit backfill.
"""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-native-study-tenant-scope"


def main():
    queries = [
        (
            "CREATE CONSTRAINT constraint_DomainStudyScope_study_uid "
            "IF NOT EXISTS FOR (n:DomainStudyScope) REQUIRE (n.study_uid) IS NODE KEY"
        ),
        """
        MATCH (study:StudyRoot)
        WHERE NOT EXISTS {
          MATCH (:DomainStudyScope {study_uid: study.uid})
        }
        CREATE (:DomainStudyScope {
          study_uid: study.uid,
          tenant_id: null,
          status: 'quarantined',
          reason: 'legacy-tenant-attribution-ambiguous',
          created_at: datetime()
        })
        """,
    ]
    for query in queries:
        logger.info(query)
        DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
