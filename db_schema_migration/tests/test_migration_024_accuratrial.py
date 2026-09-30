"""AccuraTrial fork data through upstream migration_024 (OpenStudyBuilder 2.9 -> 2.10).

Upstream's seed carries only the ODM datatypes its own data uses. The fork's governed
native capture stores every value of the DATATYPES map in
api/clinical_mdr_api/services/integrations/native_capture_projection.py, including
"text", which CDISC CODMDT does not contain. migration_024 links an item only when its
datatype matches a CODMDT term, then removes the string property from every item, so an
unmatched datatype is lost rather than carried.
"""

import os

import pytest

from migrations import migration_024
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

TEXT_DECISION_PENDING = pytest.mark.xfail(
    strict=True,
    reason=(
        "Owner decision pending: CDISC CODMDT has no 'text' term, so migration_024 "
        "drops the datatype of fork items typed 'text'. A fork migration that runs "
        "before migration_024 must either map 'text' to 'string' or add a sponsor "
        "'text' term to CODMDT."
    ),
)

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
    preflight, _ = run_cypher_query(
        DB_DRIVER,
        DATATYPES_WITHOUT_CODMDT_TERM,
        params={"codelist_submval": migration_024.DATATYPE_CODELIST_SUBMVAL},
    )
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
        RETURN id, head(collect(clt.submission_value)) AS datatype
        """,
        params={"expected": expected},
    )
    return {
        "preflight": {r["datatype"]: r["items"] for r in preflight},
        "after": {expected[r["id"]]: r["datatype"] for r in migrated},
    }


@TEXT_DECISION_PENDING
def test_preflight_finds_every_fork_datatype_in_codmdt(rehearsal):
    logger.info("Datatypes without a CODMDT term: %s", rehearsal["preflight"])
    assert rehearsal["preflight"] == {}


def test_preflight_names_only_text(rehearsal):
    assert set(rehearsal["preflight"]) <= {"text"}


@pytest.mark.parametrize(
    "datatype",
    [
        pytest.param(dt, marks=TEXT_DECISION_PENDING) if dt == "text" else dt
        for dt in FORK_DATATYPES
    ],
)
def test_fork_datatype_survives_migration(rehearsal, datatype):
    after = rehearsal["after"][datatype]
    assert after is not None, f"{datatype!r} item lost its datatype"
    assert after.lower() == datatype.lower()
