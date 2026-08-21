"""Constrain and transactionally protect OSB platform audit history."""

import os

from migrations.utils.utils import (
    DATABASE_NAME,
    get_db_connection,
    get_db_driver,
    get_logger,
)

logger = get_logger(os.path.basename(__file__))
DB_CONNECTION = get_db_connection()
MIGRATION_DESC = "osb-platform-domain-audit-integrity"

PROTECTED_LABELS = [
    "PlatformCommandEffect",
    "PlatformCommandAudit",
    "PlatformCommandOutbox",
    "DomainAuditEvent",
    "DomainAuditRootCheckpoint",
    "DomainAuditExport",
]

IMMUTABILITY_TRIGGER = """
CALL {
  WITH $removedLabels AS changes
  UNWIND keys(changes) AS label
  WITH label,changes
  WHERE label IN ['PlatformCommandEffect','PlatformCommandAudit','PlatformCommandOutbox',
    'DomainAuditEvent','DomainAuditRootCheckpoint','DomainAuditExport']
    AND size(changes[label])>0
  CALL apoc.util.validate(true,'OSB_PLATFORM_AUDIT_HISTORY_IMMUTABLE',[])
  RETURN count(*) AS removed_label_count
}
CALL {
  WITH $assignedNodeProperties AS changes,$createdNodes AS created
  UNWIND keys(changes) AS key
  UNWIND changes[key] AS change
  WITH DISTINCT change.node AS node,created,$deletedNodes AS deleted
  WHERE NOT (node IN created) AND NOT (node IN deleted)
    AND any(label IN labels(node)
      WHERE label IN ['PlatformCommandEffect','PlatformCommandAudit','PlatformCommandOutbox',
        'DomainAuditEvent','DomainAuditRootCheckpoint','DomainAuditExport'])
  CALL apoc.util.validate(true,'OSB_PLATFORM_AUDIT_HISTORY_IMMUTABLE',[])
  RETURN count(*) AS assigned_count
}
CALL {
  WITH $removedNodeProperties AS changes
  UNWIND keys(changes) AS key
  UNWIND changes[key] AS change
  WITH DISTINCT change.node AS node,$deletedNodes AS deleted
  WHERE NOT (node IN deleted)
    AND any(label IN labels(node)
      WHERE label IN ['PlatformCommandEffect','PlatformCommandAudit','PlatformCommandOutbox',
        'DomainAuditEvent','DomainAuditRootCheckpoint','DomainAuditExport'])
  CALL apoc.util.validate(true,'OSB_PLATFORM_AUDIT_HISTORY_IMMUTABLE',[])
  RETURN count(*) AS removed_property_count
}
RETURN removed_label_count,assigned_count,removed_property_count
"""


def install_immutability_trigger() -> None:
    """Install the database-scoped APOC trigger through Neo4j's system DB."""

    trigger_name = "protect-platform-audit-history"
    driver = get_db_driver()
    try:
        with driver.session(database="system") as session:
            installed = list(session.run(
                "CALL apoc.trigger.show($database_name) YIELD name RETURN name",
                database_name=DATABASE_NAME,
            ))
            if not any(record["name"] == trigger_name for record in installed):
                logger.info("Installing transactional platform audit immutability trigger")
                session.run(
                    "CALL apoc.trigger.install($database_name,$name,$statement,"
                    "{phase:'before'},{})",
                    database_name=DATABASE_NAME,
                    name=trigger_name,
                    statement=IMMUTABILITY_TRIGGER,
                ).consume()
    finally:
        driver.close()


def main():
    constraints = [
        "CREATE CONSTRAINT constraint_DomainAuditEvent_id IF NOT EXISTS "
        "FOR (n:DomainAuditEvent) REQUIRE (n.audit_event_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_DomainAuditEvent_scope_sequence IF NOT EXISTS "
        "FOR (n:DomainAuditEvent) REQUIRE "
        "(n.tenant_id,n.platform_study_id,n.stream_id,n.sequence) IS NODE KEY",
        "CREATE CONSTRAINT constraint_DomainAuditEvent_scope_hash IF NOT EXISTS "
        "FOR (n:DomainAuditEvent) REQUIRE "
        "(n.tenant_id,n.platform_study_id,n.stream_id,n.event_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_DomainAuditRootCheckpoint_id IF NOT EXISTS "
        "FOR (n:DomainAuditRootCheckpoint) REQUIRE (n.checkpoint_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_DomainAuditRootCheckpoint_scope_end IF NOT EXISTS "
        "FOR (n:DomainAuditRootCheckpoint) REQUIRE "
        "(n.tenant_id,n.platform_study_id,n.stream_id,n.sequence_end) IS NODE KEY",
        "CREATE CONSTRAINT constraint_DomainAuditRootCheckpoint_scope_hash IF NOT EXISTS "
        "FOR (n:DomainAuditRootCheckpoint) REQUIRE "
        "(n.tenant_id,n.platform_study_id,n.payload_hash) IS NODE KEY",
        "CREATE CONSTRAINT constraint_DomainAuditRootCheckpoint_no_fork IF NOT EXISTS "
        "FOR (n:DomainAuditRootCheckpoint) REQUIRE "
        "(n.tenant_id,n.platform_study_id,n.previous_checkpoint_hash) IS UNIQUE",
        "CREATE CONSTRAINT constraint_DomainAuditExport_id IF NOT EXISTS "
        "FOR (n:DomainAuditExport) REQUIRE (n.export_id) IS NODE KEY",
        "CREATE CONSTRAINT constraint_DomainAuditExport_scope IF NOT EXISTS "
        "FOR (n:DomainAuditExport) REQUIRE (n.export_scope_key) IS NODE KEY",
    ]
    for query in constraints:
        logger.info(query)
        DB_CONNECTION.cypher_query(query)

    incomplete, _ = DB_CONNECTION.cypher_query(
        """MATCH (node)
           WHERE any(label IN labels(node) WHERE label IN $protected_labels)
             AND ((node:DomainAuditEvent AND (node.event_json IS NULL OR node.event_hash_json IS NULL))
               OR (node:DomainAuditRootCheckpoint AND
                 (node.checkpoint_json IS NULL OR node.payload_hash_json IS NULL
                  OR node.signed_envelope_json IS NULL OR node.signature_verification_json IS NULL))
               OR (node:DomainAuditExport AND
                 (node.checkpoint_id IS NULL OR node.payload_hash IS NULL OR node.export_hash IS NULL)))
           RETURN count(node)""",
        {"protected_labels": PROTECTED_LABELS},
    )
    if incomplete and int(incomplete[0][0]) != 0:
        raise RuntimeError("OSB_PLATFORM_AUDIT_HISTORY_INCOMPLETE")

    install_immutability_trigger()


if __name__ == "__main__":
    main()
