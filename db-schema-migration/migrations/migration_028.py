"""Add atomic OSB CommandEnvelopeV1 idempotency and audit constraints."""

import os

from migrations.utils.utils import get_db_connection, get_logger

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-platform-command-publication"


def main():
    queries = [
        "CREATE CONSTRAINT constraint_PlatformCommandLock_key IF NOT EXISTS FOR (n:PlatformCommandLock) REQUIRE (n.key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformCommandEffect_effect_id IF NOT EXISTS FOR (n:PlatformCommandEffect) REQUIRE (n.target_effect_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformCommandEffect_command_scope IF NOT EXISTS FOR (n:PlatformCommandEffect) REQUIRE (n.tenant_id, n.command_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformCommandEffect_idempotency_scope IF NOT EXISTS FOR (n:PlatformCommandEffect) REQUIRE (n.tenant_id, n.target_capability, n.action, n.idempotency_key) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformCommandAudit_audit_id IF NOT EXISTS FOR (n:PlatformCommandAudit) REQUIRE (n.audit_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformCommandAudit_chain IF NOT EXISTS FOR (n:PlatformCommandAudit) REQUIRE (n.tenant_id, n.chain_sequence) IS NODE KEY",
        "CREATE CONSTRAINT constraint_PlatformCommandAudit_hash IF NOT EXISTS FOR (n:PlatformCommandAudit) REQUIRE (n.tenant_id, n.event_hash) IS NODE KEY",
    ]
    for query in queries:
        logger.info(query)
        DB_CONNECTION.cypher_query(query)


if __name__ == "__main__":
    main()
