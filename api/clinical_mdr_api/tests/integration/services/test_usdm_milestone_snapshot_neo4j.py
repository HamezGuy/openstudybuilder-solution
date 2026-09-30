"""Actual changing-term graph witness on the verified, owned empty fixture.

Study/CT creation, CT publication, native aggregate lock/persistence, selection
query, service transaction, DTO and milestone mapper run unmocked. This is a
bounded milestone witness, not a whole-study release or UI qualification.
"""

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from neomodel import db

from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import StudyComponentEnum
from clinical_mdr_api.models.controlled_terminologies.ct_term_attributes import CTTermAttributesEditInput
from clinical_mdr_api.models.controlled_terminologies.ct_term_name import CTTermNameEditInput
from clinical_mdr_api.models.study_selections.study_disease_milestone import StudyDiseaseMilestoneCreateInput
from clinical_mdr_api.services._meta_repository import MetaRepository
from clinical_mdr_api.services.controlled_terminologies.ct_term_attributes import CTTermAttributesService
from clinical_mdr_api.services.controlled_terminologies.ct_term_name import CTTermNameService
from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper
from clinical_mdr_api.services.ddf.usdm_mapping_context import MappingContext
from clinical_mdr_api.services.ddf.usdm_native_mapping import NativeStudyMapping
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.services.studies.study_disease_milestone import StudyDiseaseMilestoneService
from clinical_mdr_api.tests.fixtures.null_adjudication_neo4j import (
    native_null_graph,
    own_created_nodes,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.auth.user import user
from common.config import settings


@pytest.fixture(scope="module")
def milestone_catalogue(native_null_graph):
    graph = native_null_graph
    with graph.principal():
        codelist = TestUtils.create_ct_codelist(
            name=settings.study_disease_milestone_type_name,
            submission_value=settings.disease_milestone_cl_submval,
            definition="Authored disease-milestone history fixture",
            extensible=True, approve=True,
        )
    own_created_nodes(graph.nonce)
    return graph, codelist.codelist_uid


def selected_milestone(fixture):
    graph, codelist_uid = fixture
    label = "Diagnosis A " + uuid4().hex[:12]
    authored_definition = "Historical milestone definition A; preserve 0 and trailing space. "
    with graph.principal():
        term = TestUtils.create_ct_term(
            codelist_uid=codelist_uid, submission_value=label,
            sponsor_preferred_name=label, sponsor_preferred_name_sentence_case=label,
            nci_preferred_name=label, definition=authored_definition,
            concept_id="SYNTHETIC-" + uuid4().hex, approve=True,
        )
        # Native input validation strips authored strings before persistence.
        # Historical expectations use a fresh read of that persisted definition.
        definition = CTTermAttributesService().get_by_uid(term.term_uid).definition
        assert definition == authored_definition.strip()
        study_uid = graph.new_study()
        graph.ordinary(study_uid, {
            "study_description": {"study_title": "Authored native milestone history witness"},
        })
        milestone = StudyDiseaseMilestoneService().create(
            study_uid, StudyDiseaseMilestoneCreateInput(
                study_uid=study_uid, disease_milestone_type=term.term_uid,
                repetition_indicator=False, order=1,
            ),
        )
        # Use the same native aggregate lock and repository save called by
        # StudyService.lock, without unrelated SoA/package authoring workflows.
        with db.transaction:
            repository = MetaRepository().study_definition_repository
            study = repository.find_by_uid(study_uid, for_update=True)
            study.lock(
                author_id=user().id(),
                version_description="Authored exact milestone snapshot",
            )
            repository.save(study)
            version = str(study.latest_released_or_locked_metadata.ver_metadata.version_number)
        selected = StudyService().get_by_uid(
            study_uid, study_value_version=version,
            include_sections=[StudyComponentEnum.VERSION_METADATA],
        )
        before = StudyDiseaseMilestoneService().get_all_disease_milestones(
            study_uid, study_value_version=version, page_size=0,
        ).items
    own_created_nodes(graph.nonce)
    assert len(before) == 1 and before[0].uid == milestone.uid
    assert before[0].terminology_source.state == "resolved"
    assert before[0].disease_milestone_type_name == label
    assert before[0].disease_milestone_type_definition == definition
    assert before[0].terminology_source.as_of == selected.current_metadata.version_metadata.version_timestamp
    return graph, study_uid, version, term.term_uid, label, definition, before[0]


def publish_change(term_uid, field, value):
    if field == "name":
        service = CTTermNameService()
        service.create_new_version(term_uid)
        service.edit_draft(term_uid, CTTermNameEditInput(
            sponsor_preferred_name=value, sponsor_preferred_name_sentence_case=value,
            change_description="Authored later native term name",
        ))
    else:
        service = CTTermAttributesService()
        service.create_new_version(term_uid)
        service.edit_draft(term_uid, CTTermAttributesEditInput(
            definition=value, change_description="Authored later native term definition",
        ))
    return service.approve(term_uid)


def map_actual_milestone(row):
    def unrelated(*_args, **_kwargs):
        raise AssertionError("This witness must not read an unrelated source domain")

    mapper = USDMMapper(*(unrelated for _ in range(8)))
    mapper._study_uid = row.study_uid
    mapper._study_value_version = row.study_version
    mapper._study_as_of = row.terminology_source.as_of
    mapper._context = MappingContext(allow_incomplete=True)
    mapper._native_rows = {"studyDiseaseMilestone": [row]}
    mapping = NativeStudyMapping(mapper)
    mapping._activity_targets = {}
    design = SimpleNamespace(activities=[])
    mapping._milestones(design)
    assert len(design.activities) == 1
    return design.activities[0].model_dump(mode="json", exclude_unset=True), mapper._context.issues


@pytest.mark.parametrize("changed", ["name", "definition", "both"])
def test_actual_native_history_keeps_selected_milestone_after_changing_term(milestone_catalogue, changed):
    graph, study_uid, version, term_uid, label, definition, before = selected_milestone(milestone_catalogue)
    with graph.principal():
        if changed in {"name", "both"}:
            publish_change(term_uid, "name", label + " CHANGED")
        if changed in {"definition", "both"}:
            publish_change(term_uid, "definition", "Definition B after study lock")
        current, _ = db.cypher_query("""
            MATCH (:CTTermRoot {uid:$term_uid})-[:HAS_NAME_ROOT]->
                  (:CTTermNameRoot)-[:LATEST_FINAL]->(name:CTTermNameValue)
            MATCH (:CTTermRoot {uid:$term_uid})-[:HAS_ATTRIBUTES_ROOT]->
                  (:CTTermAttributesRoot)-[:LATEST_FINAL]->(attributes:CTTermAttributesValue)
            RETURN name.name, attributes.definition
        """, {"term_uid": term_uid})
        assert current == [[
            label + " CHANGED" if changed in {"name", "both"} else label,
            "Definition B after study lock" if changed in {"definition", "both"} else definition,
        ]]
        after = StudyDiseaseMilestoneService().get_all_disease_milestones(
            study_uid, study_value_version=version, page_size=0,
        ).items[0]
    own_created_nodes(graph.nonce)
    assert after.uid == before.uid
    assert after.disease_milestone_type_name == label
    assert after.disease_milestone_type_definition == definition
    assert after.repetition_indicator is False
    assert after.terminology_source.state == "resolved"
    for field in ("name", "attributes"):
        old, retained = getattr(before.terminology_source, field), getattr(after.terminology_source, field)
        assert (retained.value_identity, retained.version, retained.value) == (
            old.value_identity, old.version, old.value,
        )
        assert retained.start_date <= after.terminology_source.as_of
        if retained.end_date is not None:
            assert after.terminology_source.as_of < retained.end_date
    activity, issues = map_actual_milestone(after)
    assert activity["name"] == label and activity["description"] == definition
    assert issues == []
    schema_path = Path(__file__).parents[2] / "fixtures/usdm_native_model4_schema.json"
    assert sha256(schema_path.read_bytes()).hexdigest() == "dc4303bca26256c56e5cb83222e898a09a5244472f7fd092b425cc2b7568fe19"
    schema = json.loads(schema_path.read_bytes())
    errors = list(Draft202012Validator({
        "$ref": "#/components/schemas/Activity-Input", "components": schema["components"],
    }).iter_errors(activity))
    assert errors == [], [error.message for error in errors]
    print(json.dumps({
        "case": "actual-changing-native-milestone-term", "changed": changed,
        "studyUid": study_uid, "studyValueVersion": version,
        "nativeCurrentTermValues": current,
        "selectedSource": after.terminology_source.model_dump(mode="json"),
        "canonicalActivity": activity,
    }))


def test_retired_term_keeps_historical_selection_without_latest_final(milestone_catalogue):
    graph, study_uid, version, term_uid, label, definition, _ = selected_milestone(milestone_catalogue)
    with graph.principal():
        CTTermNameService().inactivate_final(term_uid)
        CTTermAttributesService().inactivate_final(term_uid)
        latest, _ = db.cypher_query("""
            MATCH (:CTTermRoot {uid:$term_uid})-[:HAS_NAME_ROOT]->(root:CTTermNameRoot)
            OPTIONAL MATCH (root)-[:LATEST_FINAL]->(value)
            RETURN count(value)
        """, {"term_uid": term_uid})
        assert latest == [[0]]
        rows = StudyDiseaseMilestoneService().get_all_disease_milestones(
            study_uid, study_value_version=version,
        ).items
    own_created_nodes(graph.nonce)
    assert len(rows) == 1
    assert (rows[0].disease_milestone_type_name, rows[0].disease_milestone_type_definition) == (label, definition)
    assert rows[0].terminology_source.state == "resolved"
    activity, issues = map_actual_milestone(rows[0])
    assert activity["name"] == label and issues == []


@pytest.mark.parametrize("corruption", ["missing", "ambiguous"])
def test_actual_missing_or_ambiguous_history_cannot_reuse_current_term(milestone_catalogue, corruption):
    graph, study_uid, version, term_uid, label, definition, before = selected_milestone(milestone_catalogue)
    with graph.principal():
        publish_change(term_uid, "name", label + " CHANGED")
        if corruption == "missing":
            # Deliberate damage only to this authored fixture term's history.
            # Leave LATEST_FINAL intact to reproduce the old incorrect fallback.
            db.cypher_query("""
                MATCH (:CTTermRoot {uid:$term_uid})-[:HAS_NAME_ROOT]->
                      (:CTTermNameRoot)-[history:HAS_VERSION]->()
                DELETE history
            """, {"term_uid": term_uid})
        else:
            db.cypher_query("""
                MATCH (:CTTermRoot {uid:$term_uid})-[:HAS_NAME_ROOT]->
                      (root:CTTermNameRoot)-[history:HAS_VERSION]->()
                WHERE history.status = 'Final'
                SET history.start_date=datetime($as_of), history.end_date=null
            """, {"term_uid": term_uid, "as_of": before.terminology_source.as_of.isoformat()})
        rows = StudyDiseaseMilestoneService().get_all_disease_milestones(
            study_uid, study_value_version=version,
        ).items
    own_created_nodes(graph.nonce)
    assert len(rows) == 1 and rows[0].disease_milestone_type_name is None
    assert rows[0].disease_milestone_type_definition == definition
    assert rows[0].terminology_source.name is None
    assert rows[0].terminology_source.state == "unresolved"
    assert rows[0].terminology_source.issues
    activity, issues = map_actual_milestone(rows[0])
    assert "name" not in activity
    assert activity["description"] == definition
    assert any(issue["code"] == "USDM_MILESTONE_TERM_HISTORY_REQUIRED" for issue in issues)
    assert "CHANGED" not in json.dumps(activity)
