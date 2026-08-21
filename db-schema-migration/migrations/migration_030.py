"""Add one CSL mapping decision, managed study concepts, and native evidence."""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-study-mapping-decision-native-evidence-v1"


def main():
    queries = [
        "CREATE CONSTRAINT constraint_StudyMappingDecisionV1_id IF NOT EXISTS "
        "FOR (n:StudyMappingDecisionV1) REQUIRE (n.decision_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_StudyMappingDecisionV1_candidate_set IF NOT EXISTS "
        "FOR (n:StudyMappingDecisionV1) REQUIRE (n.tenant_id, n.candidate_set_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformManagedStudyConcept_key IF NOT EXISTS "
        "FOR (n:PlatformManagedStudyConcept) REQUIRE (n.managed_key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_NativeOperationEvidenceV1_id IF NOT EXISTS "
        "FOR (n:NativeOperationEvidenceV1) REQUIRE (n.evidence_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_NativeOperationEvidenceV1_operation IF NOT EXISTS "
        "FOR (n:NativeOperationEvidenceV1) REQUIRE (n.tenant_id, n.operation_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbNativeEvidenceSetV1_version IF NOT EXISTS "
        "FOR (n:OsbNativeEvidenceSetV1) REQUIRE (n.evidence_set_version_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_OsbNativeEvidenceSetV1_decision IF NOT EXISTS "
        "FOR (n:OsbNativeEvidenceSetV1) REQUIRE (n.tenant_id, n.decision_hash) IS NODE KEY",
    ]
    for query in queries:
        logger.info(query)
        DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
