# Authored arm lifecycle event index

Run with the existing migration environment against the intended database:

```
python -m migrations.migration_accuratrial_011
```

This creates the `index_StudyArmLifecycleEvent_uid` lookup index. Fresh databases
receive the same index through `db/db_schema.py`. Running the migration again is
safe. It does not modify clinical data or backfill lifecycle events.

Arm and branch arm lifecycle events are immutable authored records attached to
an exact StudyValue snapshot. New draft snapshots retain their existing event
membership. Effective dates, decision references and audit identity remain on
the original event. An absent history means the lifecycle state is unknown.

Lifecycle state is an authored label. Governed terminology and operational
approval remain separate dependencies. These records and their descriptive
USDM and EDC exports do not authorize enrollment, allocation or statistical
decisions. Production mapping authority remains enforced.
