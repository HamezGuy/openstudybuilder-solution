"""Selecting a term must retain its codelist identity after a native import."""

import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_codelist_attributes_repository import (
    CTCodelistAttributesRepository,
)
from clinical_mdr_api.domain_repositories.models.controlled_terminology import CTTermRoot
from common.config import settings
from common.exceptions import ValidationException


@pytest.fixture
def terminology():
    assert os.environ.get("OSB_UNTIMED_FIXTURE") == "disposable-igs22"
    assert urlsplit(settings.neo4j_dsn).hostname == "igs22-graph"
    prefix = "term-identity-" + uuid4().hex
    term = prefix + "-term"
    codelist = prefix + "-imported"
    catalogue = prefix + "-catalogue"
    # Create the unrelated same-code list first, as in a populated destination.
    db.cypher_query(
        """
        CREATE (cat:CTCatalogue {name: $catalogue})
        CREATE (other:CTCodelistRoot {uid: $other})
        CREATE (other)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
               -[:LATEST_FINAL]->(:CTCodelistAttributesValue {submission_value: $code})
        CREATE (cat)-[:HAS_CODELIST]->(other)
        CREATE (cl:CTCodelistRoot {uid: $codelist})
        CREATE (cl)-[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
               -[:LATEST_FINAL]->(:CTCodelistAttributesValue {submission_value: $code})
        CREATE (cat)-[:HAS_CODELIST]->(cl)
        CREATE (cl)-[:HAS_TERM {order: 2}]->(:CTCodelistTerm {submission_value: 'SECONDARY'})
               -[:HAS_TERM_ROOT]->(:CTTermRoot {uid: $term})
        """,
        {"catalogue": catalogue, "other": prefix + "-other", "code": prefix,
         "codelist": codelist, "term": term},
    )
    return {
        "term": CTTermRoot.nodes.get(uid=term), "codelist": codelist,
        "other": prefix + "-other", "catalogue": catalogue, "code": prefix,
    }


def select(case, **changes):
    values = {"codelist_submission_value": case["code"],
              "catalogue_name": case["catalogue"]}
    values.update(changes)
    return CTCodelistAttributesRepository().get_or_create_selected_term(case["term"], **values)


def context_count():
    return db.cypher_query("MATCH (n:CTTermContext) RETURN count(n)")[0][0][0]


def test_same_code_resolves_the_exact_membership_and_reuses_context(terminology):
    selected = select(terminology)
    assert selected.has_selected_codelist.single().uid == terminology["codelist"]
    assert selected.has_selected_term.single().uid == terminology["term"].uid
    before = context_count()
    assert select(terminology).element_id == selected.element_id
    assert context_count() == before


def test_wrong_catalogue_cannot_create_a_context(terminology):
    before = context_count()
    with pytest.raises(ValidationException, match="was not found in the codelist"):
        select(terminology, catalogue_name=terminology["catalogue"] + "-wrong")
    assert context_count() == before


def test_removed_membership_is_rejected_unless_explicitly_allowed(terminology):
    db.cypher_query(
        "MATCH (:CTCodelistRoot {uid:$uid})-[r:HAS_TERM]->() SET r.end_date=datetime()",
        {"uid": terminology["codelist"]},
    )
    before = context_count()
    with pytest.raises(ValidationException, match="was not found in the codelist"):
        select(terminology)
    assert context_count() == before
    selected = select(terminology, allow_removed_terms=True)
    assert selected.has_selected_codelist.single().uid == terminology["codelist"]


def test_ambiguous_memberships_require_an_explicit_codelist(terminology):
    db.cypher_query(
        """
        MATCH (cl:CTCodelistRoot {uid:$other}), (t:CTTermRoot {uid:$term})
        CREATE (cl)-[:HAS_TERM]->(:CTCodelistTerm)-[:HAS_TERM_ROOT]->(t)
        """,
        {"other": terminology["other"], "term": terminology["term"].uid},
    )
    before = context_count()
    with pytest.raises(ValidationException, match="multiple codelists"):
        select(terminology)
    assert context_count() == before
    selected = select(terminology, codelist_uid=terminology["codelist"])
    assert selected.has_selected_codelist.single().uid == terminology["codelist"]


def test_explicit_uid_still_validates_membership(terminology):
    before = context_count()
    with pytest.raises(ValidationException, match="was not found in the codelist"):
        select(terminology, codelist_submission_value=None, codelist_uid=terminology["other"])
    assert context_count() == before
    selected = select(terminology, codelist_submission_value=None,
                      codelist_uid=terminology["codelist"])
    assert selected.has_selected_codelist.single().uid == terminology["codelist"]
