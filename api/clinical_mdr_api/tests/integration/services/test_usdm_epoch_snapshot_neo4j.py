"""Actual catalogue ancestry/epoch query and changing CT history on owned Neo4j.

Study and CT/package writers are native. The one epoch selection is an
explicitly authored native graph fixture, not an epoch-authoring UI receipt.
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_package_repository import CTPackageRepository
from clinical_mdr_api.domain_repositories.study_selections.study_epoch_repository import StudyEpochRepository
from clinical_mdr_api.models.controlled_terminologies.ct_term_name import CTTermNameEditInput
from clinical_mdr_api.services.controlled_terminologies.ct_term_name import CTTermNameService
from clinical_mdr_api.services.studies.study_epoch import StudyEpochService
from clinical_mdr_api.tests.fixtures.usdm_library_snapshot_neo4j import native_library_graph
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.auth.user import user


def epoch_case(graph):
    study_uid = graph.new_study()
    suffix = uuid4().hex[:12]
    with graph.principal():
        TestUtils.create_ct_catalogue(catalogue_name="SDTM CT")
        codelist = TestUtils.create_ct_codelist(
            catalogue_name="SDTM CT", name="Epoch source " + suffix,
            submission_value="EPOCH" + suffix, extensible=True, approve=True,
        )
        term = TestUtils.create_ct_term(
            catalogue_name="SDTM CT", codelist_uid=codelist.codelist_uid,
            submission_value="TREATMENT" + suffix, sponsor_preferred_name="Treatment " + suffix,
            sponsor_preferred_name_sentence_case="Treatment " + suffix,
            definition="Authored historical epoch", approve=True,
        )
        now = datetime.now(timezone.utc)
        published_uid = TestUtils.create_ct_package(
            catalogue="SDTM CT", name="opaque-published-" + suffix, number_of_codelists=0,
            import_date=now, effective_date=now,
        )
        sponsor = CTPackageRepository().create_sponsor_package(
            extends_package=published_uid, effective_date=now.date(),
            author_id=user().id(), library_name="Sponsor",
        )
        TestUtils.create_study_standard_version(study_uid, sponsor.uid)
        epoch_uid = "StudyEpoch_" + suffix
        db.cypher_query("""
            MATCH (study:StudyRoot {uid:$study})-[:LATEST]->(value:StudyValue),
                (term:CTTermRoot {uid:$term}), (list:CTCodelistRoot {uid:$codelist})
            CREATE (epoch:StudyEpoch {uid:$epoch, order:1, status:'LOCKED',
                start_rule:'Source entry narrative', end_rule:'Source stop narrative'})
            CREATE (value)-[:HAS_STUDY_EPOCH]->(epoch)
            CREATE (study)-[:AUDIT_TRAIL]->(:StudyAction:Create {date:$date, author_id:$author})-[:AFTER]->(epoch)
            CREATE (context:CTTermContext)-[:HAS_SELECTED_TERM]->(term)
            CREATE (context)-[:HAS_SELECTED_CODELIST]->(list)
            CREATE (epoch)-[:HAS_EPOCH]->(context)
            CREATE (epoch)-[:HAS_EPOCH_SUB_TYPE]->(context)
            CREATE (epoch)-[:HAS_EPOCH_TYPE]->(context)
        """, {"study": study_uid, "term": term.term_uid, "codelist": codelist.codelist_uid,
              "epoch": epoch_uid, "date": now, "author": user().id()})
    version, as_of = graph.lock(study_uid)
    return study_uid, version, as_of, epoch_uid, term.term_uid, sponsor.uid, published_uid


def test_actual_opaque_sponsor_epoch_uses_selected_date_after_later_term_change(native_library_graph):
    graph = native_library_graph
    study, version, as_of, epoch_uid, term_uid, selected_uid, published_uid = epoch_case(graph)
    with graph.principal():
        before = StudyEpochService.get_all_epochs(study, study_value_version=version).items[0]
        service = CTTermNameService()
        service.create_new_version(term_uid)
        service.edit_draft(term_uid, CTTermNameEditInput(
            sponsor_preferred_name="Later current epoch", sponsor_preferred_name_sentence_case="Later current epoch",
            change_description="Authored after the selected study snapshot",
        ))
        service.approve(term_uid)
        raw = StudyEpochRepository.find_all_epochs_by_study(study, study_value_version=version)
        after = StudyEpochService.get_all_epochs(study, study_value_version=version).items[0]
    graph.own()
    assert after.uid == epoch_uid and after.epoch_name == before.epoch_name
    assert raw[0].epoch.sponsor_preferred_name == before.epoch_name
    assert after.start_rule == "Source entry narrative" and after.end_rule == "Source stop narrative"
    witness = after.terminology_source
    assert witness["cutoff"] == as_of.isoformat()
    assert witness["package"]["selectedPackage"]["uid"] == selected_uid
    assert witness["package"]["publishedPackage"]["uid"] == published_uid
    assert not witness["issues"]
    assert witness["terms"]["epoch"]["name"]["valueIdentity"] == before.terminology_source["terms"]["epoch"]["name"]["valueIdentity"]
    print(json.dumps({"case": "actual-epoch-catalogue-history", "selectedSource": witness}))


def test_actual_epoch_with_missing_history_does_not_replay_latest(native_library_graph):
    graph = native_library_graph
    study, version, _, _, term_uid, _, _ = epoch_case(graph)
    with graph.principal():
        db.cypher_query("""
            MATCH (:CTTermRoot {uid:$uid})-[:HAS_NAME_ROOT]->(root:CTTermNameRoot)-[state:HAS_VERSION]->()
            DELETE state
        """, {"uid": term_uid})
        current = db.cypher_query("""
            MATCH (:CTTermRoot {uid:$uid})-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST_FINAL]->(value)
            RETURN count(value)
        """, {"uid": term_uid})[0]
        after = StudyEpochService.get_all_epochs(study, study_value_version=version).items[0]
    graph.own()
    assert current == [[1]]
    assert after.epoch_name is None and after.epoch_ctterm.date_conflict is True
    assert after.terminology_source["issues"]
