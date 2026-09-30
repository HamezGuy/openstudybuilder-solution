"""Add immutable candidate-request intake and OSB candidate-set records."""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-candidate-request-and-set-v1"


def main():
    queries = [
        "CREATE CONSTRAINT constraint_OsbCandidateRequestLock_key IF NOT EXISTS "
        "FOR (n:OsbCandidateRequestLock) REQUIRE (n.key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbInboundArtifact_identity IF NOT EXISTS "
        "FOR (n:OsbInboundArtifact) REQUIRE (n.tenant_id, n.payload_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbInboundArtifact_version IF NOT EXISTS "
        "FOR (n:OsbInboundArtifact) REQUIRE (n.tenant_id, n.artifact_version_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbCandidateRequest_version IF NOT EXISTS "
        "FOR (n:OsbCandidateRequestV1) REQUIRE (n.request_version_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbCandidateRequest_hash IF NOT EXISTS "
        "FOR (n:OsbCandidateRequestV1) REQUIRE (n.tenant_id, n.request_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbCandidateSet_version IF NOT EXISTS "
        "FOR (n:OsbCandidateSetV1) REQUIRE (n.candidate_set_version_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbCandidateSet_request IF NOT EXISTS "
        "FOR (n:OsbCandidateSetV1) REQUIRE (n.tenant_id, n.request_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbCandidateSet_hash IF NOT EXISTS "
        "FOR (n:OsbCandidateSetV1) REQUIRE (n.tenant_id, n.payload_hash) IS NODE KEY",
    ]
    for query in queries:
        logger.info(query)
        DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
