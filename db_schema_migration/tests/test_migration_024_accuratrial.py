"""AccuraTrial fork data through upstream migration_024 (OpenStudyBuilder 2.9 -> 2.10).

Upstream's seed carries only the ODM datatypes its own data uses. The fork's governed
native capture stores every value of the DATATYPES map in
api/clinical_mdr_api/services/integrations/native_capture_projection.py, including
"text", which CDISC CODMDT does not contain. migration_024 links an item only when its
datatype matches a CODMDT term, then removes the string property from every item, so
migration_accuratrial_010 first adds the 2.10 sponsor "text" datatype term to CODMDT.
"""

import os

import pytest

from migrations import migration_024, migration_accuratrial_010
from migrations.migration_accuratrial_010 import (
    CodmdtTextTermError,
    add_sponsor_text_to_codmdt,
)
from migrations.utils.utils import (
    execute_statements,
    get_db_driver,
    get_logger,
    run_cypher_query,
)
from tests.data.db_before_migration_024 import TEST_DATA
from tests.utils.utils import clear_db

# pylint: disable=redefined-outer-name

DB_DRIVER = get_db_driver()
logger = get_logger(os.path.basename(__file__))

FORK_DATATYPES = (
    "text",
    "string",
    "integer",
    "float",
    "double",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "time",
    "partialDate",
    "partialDatetime",
    "incompleteDate",
    "incompleteDatetime",
)

SPONSOR_TEXT_TERM = "CTTerm_SPONSOR_TEXT"

# Read-only: the same check is the production preflight before migration_024 runs.
DATATYPES_WITHOUT_CODMDT_TERM = """
    MATCH (oiv:OdmItemValue)
    WHERE oiv.datatype IS NOT NULL
      AND NOT EXISTS {
        MATCH (clr:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
              -[:LATEST]->(clav:CTCodelistAttributesValue)
        WHERE clav.submission_value = $codelist_submval
        MATCH (clr)-[:HAS_TERM]->(clt:CTCodelistTerm)
        WHERE toLower(clt.submission_value) = toLower(oiv.datatype)
      }
    RETURN oiv.datatype AS datatype, count(oiv) AS items
    ORDER BY datatype
"""

# The part of the 2.10 sponsor library import (datatype.csv) that the 2.9 seed lacks:
# the sponsor "text" term, added to the existing DATATYPE codelist but not to CODMDT
# (what an import from upstream's unmodified datatype.csv leaves behind).
SEED_SPONSOR_TEXT_TERM = """
    MATCH (dt:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
          -[:LATEST]->(:CTCodelistAttributesValue {submission_value: 'DATATYPE'})
    UNWIND $term_uids AS term_uid
    CREATE (dt)-[:HAS_TERM {start_date: datetime(), author_id: 'sponsor-import', order: 3}]
           ->(:CTCodelistTerm {submission_value: 'text'})
           -[:HAS_TERM_ROOT]->(:CTTermRoot {uid: term_uid})
"""

CODMDT_TEXT_MEMBERS = """
    MATCH (clr:CTCodelistRoot)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
          -[:LATEST]->(clav:CTCodelistAttributesValue {submission_value: 'CODMDT'})
    MATCH (clr)-[ht:HAS_TERM]->(clt:CTCodelistTerm)-[:HAS_TERM_ROOT]->(term:CTTermRoot)
    WHERE toLower(clt.submission_value) = 'text'
    RETURN term.uid AS term_uid, clt.submission_value AS submission_value,
           ht.author_id AS author_id, ht.order AS order,
           ht.start_date IS NOT NULL AS started, ht.end_date AS end_date
"""


def seed_sponsor_text_term(term_uids=(SPONSOR_TEXT_TERM,)):
    _, summary = run_cypher_query(
        DB_DRIVER, SEED_SPONSOR_TEXT_TERM, params={"term_uids": list(term_uids)}
    )
    assert summary.counters.nodes_created == 2 * len(term_uids), "no DATATYPE codelist"


def preflight():
    records, _ = run_cypher_query(
        DB_DRIVER,
        DATATYPES_WITHOUT_CODMDT_TERM,
        params={"codelist_submval": migration_024.DATATYPE_CODELIST_SUBMVAL},
    )
    return {r["datatype"]: r["items"] for r in records}


@pytest.fixture(scope="module")
def rehearsal():
    """Seed upstream's 2.9 data, retype items to every fork datatype, migrate."""
    clear_db()
    execute_statements(TEST_DATA)
    records, _ = run_cypher_query(
        DB_DRIVER,
        """
        MATCH (oiv:OdmItemValue)
        WHERE oiv.datatype IS NOT NULL
        RETURN elementId(oiv) AS id
        ORDER BY oiv.oid, oiv.name, id
        LIMIT $count
        """,
        params={"count": len(FORK_DATATYPES)},
    )
    assert len(records) == len(FORK_DATATYPES), "seed has too few ODM items"
    expected = {record["id"]: dt for record, dt in zip(records, FORK_DATATYPES)}
    run_cypher_query(
        DB_DRIVER,
        """
        UNWIND $items AS item
        MATCH (oiv:OdmItemValue) WHERE elementId(oiv) = item.id
        SET oiv.datatype = item.datatype
        """,
        params={"items": [{"id": i, "datatype": d} for i, d in expected.items()]},
    )
    seed_sponsor_text_term()
    before = preflight()
    migration_accuratrial_010.main()
    after_fork_migration = preflight()
    migration_024.main()
    migrated, _ = run_cypher_query(
        DB_DRIVER,
        """
        UNWIND keys($expected) AS id
        MATCH (oiv:OdmItemValue) WHERE elementId(oiv) = id
        OPTIONAL MATCH (oiv)-[:HAS_DATA_TYPE]->(ctx:CTTermContext)
                       -[:HAS_SELECTED_TERM]->(ctr:CTTermRoot)
        OPTIONAL MATCH (ctx)-[:HAS_SELECTED_CODELIST]->(:CTCodelistRoot)
                       -[:HAS_TERM]->(clt:CTCodelistTerm)-[:HAS_TERM_ROOT]->(ctr)
        RETURN id, head(collect(clt.submission_value)) AS datatype,
               head(collect(ctr.uid)) AS term_uid
        """,
        params={"expected": expected},
    )
    return {
        "preflight": before,
        "preflight_after_fork_migration": after_fork_migration,
        "after": {expected[r["id"]]: r["datatype"] for r in migrated},
        "term_uids": {expected[r["id"]]: r["term_uid"] for r in migrated},
    }


def test_preflight_names_only_text(rehearsal):
    logger.info("Datatypes without a CODMDT term: %s", rehearsal["preflight"])
    assert set(rehearsal["preflight"]) == {"text"}


def test_fork_migration_puts_every_fork_datatype_in_codmdt(rehearsal):
    assert rehearsal["preflight_after_fork_migration"] == {}


@pytest.mark.parametrize("datatype", FORK_DATATYPES)
def test_fork_datatype_survives_migration(rehearsal, datatype):
    after = rehearsal["after"][datatype]
    assert after is not None, f"{datatype!r} item lost its datatype"
    assert after.lower() == datatype.lower()


def test_text_items_point_at_the_sponsor_text_term(rehearsal):
    assert rehearsal["after"]["text"] == "text"
    assert rehearsal["term_uids"]["text"] == SPONSOR_TEXT_TERM


@pytest.mark.usefixtures("rehearsal")
def test_codmdt_text_membership_is_what_the_api_writes():
    members, _ = run_cypher_query(DB_DRIVER, CODMDT_TEXT_MEMBERS)
    assert [dict(m) for m in members] == [
        {
            "term_uid": SPONSOR_TEXT_TERM,
            "submission_value": "text",
            "author_id": migration_accuratrial_010.AUTHOR_ID,
            "order": migration_accuratrial_010.TEXT_ORDER,
            "started": True,
            "end_date": None,
        }
    ]
    # The membership reuses the term's own `text` CTCodelistTerm, as the API does.
    shared, _ = run_cypher_query(
        DB_DRIVER,
        """
        MATCH (:CTTermRoot {uid: $term_uid})<-[:HAS_TERM_ROOT]-(clt:CTCodelistTerm)
        RETURN count(clt) AS codelist_terms
        """,
        params={"term_uid": SPONSOR_TEXT_TERM},
    )
    assert shared[0]["codelist_terms"] == 1


@pytest.mark.usefixtures("rehearsal")
def test_fork_migration_is_idempotent():
    assert add_sponsor_text_to_codmdt() is False
    members, _ = run_cypher_query(DB_DRIVER, CODMDT_TEXT_MEMBERS)
    assert len(members) == 1


# The cases below start from a clean database each, after the rehearsal tests.


@pytest.fixture
def codmdt_only():
    """Upstream's 2.9 seed (CODMDT without `text`) and nothing from the 2.10 import."""
    clear_db()
    execute_statements(TEST_DATA)


@pytest.mark.usefixtures("codmdt_only")
def test_refuses_without_the_sponsor_text_term():
    with pytest.raises(CodmdtTextTermError, match="found 0"):
        add_sponsor_text_to_codmdt()


@pytest.mark.usefixtures("codmdt_only")
def test_refuses_an_ambiguous_sponsor_text_term():
    seed_sponsor_text_term((SPONSOR_TEXT_TERM, "CTTerm_SECOND_TEXT"))
    with pytest.raises(CodmdtTextTermError, match="found 2"):
        add_sponsor_text_to_codmdt()
    members, _ = run_cypher_query(DB_DRIVER, CODMDT_TEXT_MEMBERS)
    assert not members


@pytest.mark.usefixtures("codmdt_only")
def test_leaves_an_existing_codmdt_text_member_alone():
    seed_sponsor_text_term()
    assert add_sponsor_text_to_codmdt() is True
    assert add_sponsor_text_to_codmdt() is False
    members, _ = run_cypher_query(DB_DRIVER, CODMDT_TEXT_MEMBERS)
    assert [m["term_uid"] for m in members] == [SPONSOR_TEXT_TERM]
