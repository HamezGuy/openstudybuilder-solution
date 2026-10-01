# Sponsor ODM datatype `text` in CODMDT

Run after the 2.10 sponsor library import and **before** upstream `migration_024`:

```
python -m migrations.migration_accuratrial_010
```

## Why

OpenStudyBuilder 2.10 types an ODM item by a term of the CODMDT codelist.
Upstream `migration_024` links each item whose `datatype` string matches a CODMDT
term, then removes the string from every item. CDISC's CODMDT has no `text` term,
but the fork writes `text`: CSL maps EDC free-text widgets (`text`, `textarea`,
`radio`, `select`, `checkbox`, `combobox`) to it. Without this migration those
items would come out of `migration_024` with no datatype, and new governed writes
of a `text` item would be refused.

Upstream's 2.10 sponsor library already defines a sponsor `text` datatype term
(the parent of `string` and `comment`) in CODMDT20, SEMTCDT and NSVXMLDT. The fork
adds the same term to CODMDT:

- new databases: the fork's `import_sponsor_data/datafiles/sponsor_library/datatype.csv`
  lists CODMDT for `text`;
- existing databases: this migration.

## What it writes

Exactly what `POST /ct/codelists/{CODMDT uid}/terms` writes for the term: a current
`HAS_TERM {start_date, author_id: 'schema-migration', order: 3}` from CODMDT to the
term's existing `text` CTCodelistTerm (order 3, as datatype.csv gives it).

## When it refuses

It changes nothing and fails when:

- there is not exactly one CODMDT codelist, or exactly one DATATYPE codelist;
- CODMDT is a template parameter codelist (use the API instead);
- there is not exactly one current DATATYPE term with submission value `text`
  (run the 2.10 sponsor library import first);
- that term is already in CODMDT under another submission value, or another CODMDT
  term has the same name.

It does nothing when CODMDT already has a current `text` member, so it is safe to
run again.

## Check

Before `migration_024`, this preflight must return no rows:

```cypher
MATCH (oiv:OdmItemValue) WHERE oiv.datatype IS NOT NULL
  AND NOT EXISTS {
    MATCH (clr:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
          -[:LATEST]->(clav) WHERE clav.submission_value = 'CODMDT'
    MATCH (clr)-[:HAS_TERM]->(clt:CTCodelistTerm)
    WHERE toLower(clt.submission_value) = toLower(oiv.datatype) }
RETURN oiv.datatype, count(*);
```

`tests/test_migration_024_accuratrial.py` rehearses the sequence on upstream's 2.9
seed with every datatype the fork writes.
