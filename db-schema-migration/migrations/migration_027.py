"""Add atomic, versioned OSB native study identity publication records.

Active platform and native keys are separate constrained nodes. Historical
binding nodes remain immutable while rollover moves only the active pointers.
"""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-native-study-identity-publication"


def main():
    queries = [
        "CREATE CONSTRAINT constraint_PlatformNativeIdentityLock_key "
        "IF NOT EXISTS FOR (n:PlatformNativeIdentityLock) REQUIRE (n.key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformNativeStudyPlatformKey_key "
        "IF NOT EXISTS FOR (n:PlatformNativeStudyPlatformKey) REQUIRE (n.key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformNativeStudyNativeKey_key "
        "IF NOT EXISTS FOR (n:PlatformNativeStudyNativeKey) REQUIRE (n.key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformNativeStudyBinding_binding_id "
        "IF NOT EXISTS FOR (n:PlatformNativeStudyBinding) REQUIRE (n.binding_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformNativeIdentityEffect_intent_id "
        "IF NOT EXISTS FOR (n:PlatformNativeIdentityEffect) REQUIRE (n.intent_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformNativeIdentityEffect_idempotency_scope "
        "IF NOT EXISTS FOR (n:PlatformNativeIdentityEffect) "
        "REQUIRE (n.tenant_id, n.namespace, n.object_type, n.idempotency_key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformNativeIdentityEffect_creation_scope "
        "IF NOT EXISTS FOR (n:PlatformNativeIdentityEffect) "
        "REQUIRE (n.tenant_id, n.creation_effect_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformNativeIdentityAudit_audit_id "
        "IF NOT EXISTS FOR (n:PlatformNativeIdentityAudit) REQUIRE (n.audit_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformNativeIdentityAudit_event_hash "
        "IF NOT EXISTS FOR (n:PlatformNativeIdentityAudit) REQUIRE (n.event_hash) IS UNIQUE",
    ]
    for query in queries:
        logger.info(query)
        DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
