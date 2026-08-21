"""Add immutable specialist review evidence and OSB-native Package V2 records."""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-native-package-v2-prototype"


def main():
    queries = [
        "CREATE CONSTRAINT constraint_OsbSpecialistReviewEvidenceV1_version IF NOT EXISTS "
        "FOR (n:OsbSpecialistReviewEvidenceV1) REQUIRE (n.review_version_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbSpecialistReviewEvidenceV1_hash IF NOT EXISTS "
        "FOR (n:OsbSpecialistReviewEvidenceV1) REQUIRE (n.tenant_id, n.payload_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbNativePackageV2_version IF NOT EXISTS "
        "FOR (n:OsbNativePackageV2) REQUIRE (n.package_version_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbNativePackageV2_hash IF NOT EXISTS "
        "FOR (n:OsbNativePackageV2) REQUIRE (n.tenant_id, n.payload_hash) IS NODE KEY",
    ]
    for query in queries:
        logger.info(query)
        DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
