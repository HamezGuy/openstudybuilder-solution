"""Put the sponsor ODM datatype `text` in CODMDT before upstream migration_024.

OpenStudyBuilder 2.10 types an ODM item by a CODMDT term. Upstream migration_024
links each item whose `datatype` string matches a CODMDT term and then removes the
string from every item, matched or not. CDISC's CODMDT has no `text` term, but the
fork writes `text` (CSL maps EDC free-text widgets to it), so those items would
lose their datatype.

Upstream's 2.10 sponsor library already defines a sponsor `text` datatype term (the
parent of `string` and `comment`) and places it in CODMDT20, SEMTCDT and NSVXMLDT.
The fork's datatype.csv also places it in CODMDT, which covers new databases. This
migration covers existing ones: it adds that same term to CODMDT, writing what
`POST /ct/codelists/{uid}/terms` writes (the term's existing `text` CTCodelistTerm
and a current HAS_TERM relationship).

Run it after the 2.10 sponsor library import and before migration_024:
`python -m migrations.migration_accuratrial_010`. It is idempotent, and it refuses
instead of guessing when CODMDT or the sponsor `text` term is missing or ambiguous.
"""

import os

from migrations.utils.utils import (
    SchemaMigrationNode,
    get_db_driver,
    get_logger,
    print_counters_table,
    run_cypher_query,
)

logger = get_logger(os.path.basename(__file__))
DB_DRIVER = get_db_driver()
MIGRATION_DESC = "codmdt-sponsor-text-datatype"

CODMDT_SUBMVAL = "CODMDT"
DATATYPE_SUBMVAL = "DATATYPE"
TEXT_SUBMVAL = "text"
# datatype.csv gives `text` order 3 in every codelist it joins; a migrated database
# and a fresh 2.10 import then agree.
TEXT_ORDER = 3
AUTHOR_ID = "schema-migration"

CODELISTS = """
    MATCH (clr:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
          -[:LATEST]->(clav:CTCodelistAttributesValue)
    WHERE clav.submission_value = $submval
    RETURN clr.uid AS uid,
           EXISTS { MATCH (clr)-[:HAS_NAME_ROOT]->()-[:LATEST]->(:TemplateParameter) }
             AS template_parameter
"""

CURRENT_TEXT_MEMBERS = """
    MATCH (clr:CTCodelistRoot {uid: $codelist_uid})-[ht:HAS_TERM]->(clt:CTCodelistTerm)
          -[:HAS_TERM_ROOT]->(term:CTTermRoot)
    WHERE ht.end_date IS NULL AND toLower(clt.submission_value) = $text
    RETURN term.uid AS term_uid, clt.submission_value AS submission_value
"""

SPONSOR_TEXT_TERMS = """
    MATCH (dt:CTCodelistRoot {uid: $datatype_uid})-[ht:HAS_TERM]->
          (clt:CTCodelistTerm {submission_value: $text})-[:HAS_TERM_ROOT]->(term:CTTermRoot)
    WHERE ht.end_date IS NULL
    RETURN DISTINCT term.uid AS term_uid
"""

# The API refuses a term that is already a member, or a second member with the same
# name; so does this migration.
TERM_ALREADY_MEMBER = """
    MATCH (:CTCodelistRoot {uid: $codelist_uid})-[ht:HAS_TERM]->(clt:CTCodelistTerm)
          -[:HAS_TERM_ROOT]->(:CTTermRoot {uid: $term_uid})
    WHERE ht.end_date IS NULL
    RETURN clt.submission_value AS submission_value
"""

SAME_NAME_MEMBERS = """
    MATCH (term:CTTermRoot {uid: $term_uid})-[:HAS_NAME_ROOT]->()-[:LATEST]->(name_value)
    MATCH (:CTCodelistRoot {uid: $codelist_uid})-[ht:HAS_TERM]->(clt:CTCodelistTerm)
          -[:HAS_TERM_ROOT]->(other:CTTermRoot)
    WHERE ht.end_date IS NULL AND other.uid <> $term_uid
    MATCH (other)-[:HAS_NAME_ROOT]->()-[:LATEST]->(other_name)
    WHERE other_name.name = name_value.name
    RETURN other.uid AS term_uid, clt.submission_value AS submission_value
"""

# Re-checks inside the write, so a concurrent or repeated run adds nothing twice.
ADD_TEXT_MEMBER = """
    MATCH (codmdt:CTCodelistRoot {uid: $codelist_uid})
    MATCH (term:CTTermRoot {uid: $term_uid})
    MATCH (dt:CTCodelistRoot {uid: $datatype_uid})-[dt_ht:HAS_TERM]->
          (clt:CTCodelistTerm {submission_value: $text})-[:HAS_TERM_ROOT]->(term)
    WHERE dt_ht.end_date IS NULL
      AND NOT EXISTS {
        MATCH (codmdt)-[ht:HAS_TERM]->(existing:CTCodelistTerm)
        WHERE ht.end_date IS NULL AND toLower(existing.submission_value) = $text
      }
      AND NOT EXISTS {
        MATCH (codmdt)-[ht:HAS_TERM]->(:CTCodelistTerm)-[:HAS_TERM_ROOT]->(term)
        WHERE ht.end_date IS NULL
      }
    WITH codmdt, clt ORDER BY elementId(clt) LIMIT 1
    CREATE (codmdt)-[:HAS_TERM {
        start_date: datetime(), author_id: $author_id, order: $order
    }]->(clt)
    RETURN count(*) AS added
"""


class CodmdtTextTermError(RuntimeError):
    """The database does not allow an unambiguous CODMDT `text` membership."""


def _single_codelist(submval: str) -> dict:
    rows, _ = run_cypher_query(DB_DRIVER, CODELISTS, {"submval": submval})
    if len(rows) != 1:
        raise CodmdtTextTermError(
            f"Expected exactly one {submval} codelist, found {len(rows)}."
        )
    return dict(rows[0])


def add_sponsor_text_to_codmdt() -> bool:
    """Add the sponsor `text` datatype term to CODMDT. Returns True when it wrote."""
    codmdt = _single_codelist(CODMDT_SUBMVAL)
    if codmdt["template_parameter"]:
        raise CodmdtTextTermError(
            "CODMDT is a template parameter codelist; add the `text` term through "
            "POST /ct/codelists/{uid}/terms so its parameter terms are created too."
        )

    present, _ = run_cypher_query(
        DB_DRIVER,
        CURRENT_TEXT_MEMBERS,
        {"codelist_uid": codmdt["uid"], "text": TEXT_SUBMVAL},
    )
    if present:
        logger.info(
            "CODMDT already has a current 'text' member (%s); nothing to do",
            ", ".join(f"{r['term_uid']}={r['submission_value']}" for r in present),
        )
        return False

    datatype = _single_codelist(DATATYPE_SUBMVAL)
    terms, _ = run_cypher_query(
        DB_DRIVER,
        SPONSOR_TEXT_TERMS,
        {"datatype_uid": datatype["uid"], "text": TEXT_SUBMVAL},
    )
    if len(terms) != 1:
        raise CodmdtTextTermError(
            f"Expected exactly one current DATATYPE term with submission value "
            f"'{TEXT_SUBMVAL}', found {len(terms)}. Run the 2.10 sponsor library "
            "import (sponsor_codelist_definitions.csv, datatype.csv) first."
        )
    term_uid = terms[0]["term_uid"]

    member, _ = run_cypher_query(
        DB_DRIVER,
        TERM_ALREADY_MEMBER,
        {"term_uid": term_uid, "codelist_uid": codmdt["uid"]},
    )
    if member:
        raise CodmdtTextTermError(
            f"{term_uid} is already in CODMDT as "
            + ", ".join(repr(r["submission_value"]) for r in member)
            + f", not as '{TEXT_SUBMVAL}'."
        )

    clashes, _ = run_cypher_query(
        DB_DRIVER,
        SAME_NAME_MEMBERS,
        {"term_uid": term_uid, "codelist_uid": codmdt["uid"]},
    )
    if clashes:
        raise CodmdtTextTermError(
            f"CODMDT already has a term named like {term_uid}: "
            + ", ".join(f"{r['term_uid']}={r['submission_value']}" for r in clashes)
        )

    records, summary = run_cypher_query(
        DB_DRIVER,
        ADD_TEXT_MEMBER,
        {
            "codelist_uid": codmdt["uid"],
            "term_uid": term_uid,
            "datatype_uid": datatype["uid"],
            "text": TEXT_SUBMVAL,
            "author_id": AUTHOR_ID,
            "order": TEXT_ORDER,
        },
    )
    added = records[0]["added"] if records else 0
    logger.info(
        "Added sponsor term %s to CODMDT %s as '%s' (%d relationship)",
        term_uid,
        codmdt["uid"],
        TEXT_SUBMVAL,
        added,
    )
    print_counters_table(summary.counters)
    return summary.counters.contains_updates


def main():
    logger.info("Running %s (%s)", os.path.basename(__file__), MIGRATION_DESC)
    with SchemaMigrationNode(filename=__file__, driver=DB_DRIVER):
        add_sponsor_text_to_codmdt()


if __name__ == "__main__":
    main()
