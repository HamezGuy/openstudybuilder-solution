# Authored control pool and adaptation reference indexes

Run with the existing migration environment against the intended database:

```
python -m migrations.migration_accuratrial_012
```

This creates the `index_StudyControlPool_uid` and
`index_StudyAdaptationReference_uid` lookup indexes. Fresh databases receive the
same indexes through `db/db_schema.py`. Running the migration again is safe. It
does not modify clinical data or create any pool or reference.

A control pool records authored donor and focal arm membership on an exact
StudyValue snapshot. An adaptation reference records an authored adaptation,
its affected arms and epochs, and the exact pins of one published CSL
biometrics publication entry (publication entity, publication hash, analysis
model hash, collection, entry and entry hash). Neither copies an alpha,
boundary, borrowing, concurrency window, denominator or allocation rule; those
stay in the referenced CSL and Workbench facts. OSB does not verify the
referenced fact, and a reference grants no statistical, allocation or
execution authority. Production mapping authority remains enforced.
