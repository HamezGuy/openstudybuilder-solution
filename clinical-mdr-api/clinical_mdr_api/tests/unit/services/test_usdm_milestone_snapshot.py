"""Dated native CT rows -> real milestone reader/DTO -> actual USDM mapper.

Graph storage is replaced here. The separate owned-Neo4j test exercises the
actual selection query and changing-term writers.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from starlette_context import request_cycle_context

from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_term_attributes_repository import (
    CTTermAttributesRepository,
)
from clinical_mdr_api.domain_repositories.study_selections.study_disease_milestone_repository import (
    StudyDiseaseMilestoneRepository,
)
from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
    native_json,
)
from clinical_mdr_api.services.studies.study_disease_milestone import StudyDiseaseMilestoneService
from clinical_mdr_api.services.studies.study_disease_milestone_snapshot import (
    StudyDiseaseMilestoneSourceError,
    read_disease_milestone_snapshot,
)
from clinical_mdr_api.tests.fixtures.usdm_native_compound import NativeCompoundSource, Relations
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION, native_study_graph
from common.auth.dependencies import dummy_access_token_claims, dummy_auth_object


class MilestoneSource:
    def __init__(self):
        self.graph = native_study_graph()
        self.library = NativeCompoundSource(STUDY_UID, VERSION, AS_OF)
        self.repos = self.library.repos
        self.name = self.library.root(
            "ctTermName", "DIAGNOSIS",
            {"name": "Historical diagnosis", "name_sentence_case": "Historical diagnosis"},
            later_properties={"name": "UNSELECTED current diagnosis"},
        )
        self.attributes = self.library.root(
            "ctTermAttributes", "DIAGNOSIS",
            {"definition": "Historical definition with 0 and trailing whitespace. "},
            later_properties={"definition": "UNSELECTED current definition"},
        )
        self.term = SimpleNamespace(
            uid="DIAGNOSIS", has_name_root=Relations(self.name),
            has_attributes_root=Relations(self.attributes),
        )
        self.repos.ct_term_attributes_repository = object.__new__(CTTermAttributesRepository)
        self.repos.study_disease_milestone_repository = StudyDiseaseMilestoneRepository("synthetic-author")
        self.rows = [{
            "study_value_identity": "synthetic-study-value",
            "study_version": {
                "version": VERSION, "status": "LOCKED", "start_date": AS_OF, "end_date": None,
            },
            "selection_identity": "synthetic-milestone-node",
            "selection": {
                "uid": "Milestone_1", "order": 1, "status": "DRAFT",
                "accepted_version": False, "repetition_indicator": False,
            },
            "action_identity": "synthetic-milestone-create",
            "action": {"date": AS_OF - timedelta(days=1), "author_id": "synthetic-author"},
            "context_identity": "synthetic-term-context",
            "term_uid": "DIAGNOSIS", "codelist_uid": "MilestoneTypes",
        }]
        self.queries = []

    @contextmanager
    def isolated(self, monkeypatch):
        def query(text, params):
            assert params == {"study_uid": STUDY_UID, "study_value_version": VERSION}
            assert "version.version = $study_value_version" in text
            assert "LATEST" not in text
            assert "OPTIONAL MATCH (value)-[:HAS_STUDY_DISEASE_MILESTONE]" in text
            assert "HAS_SELECTED_CODELIST" in text and "HAS_SELECTED_TERM" in text
            self.queries.append((text, deepcopy(params)))
            columns = list(self.rows[0]) if self.rows else []
            return [[row[key] for key in columns] for row in deepcopy(self.rows)], columns

        with monkeypatch.context() as patcher, request_cycle_context({
            "auth": dummy_auth_object(dummy_access_token_claims("synthetic-author")),
        }):
            patcher.setattr(
                "clinical_mdr_api.domain_repositories.study_selections.study_disease_milestone_repository.db.cypher_query",
                query,
            )
            patcher.setattr(
                "clinical_mdr_api.services.studies.study_disease_milestone_snapshot.CTTermRoot",
                SimpleNamespace(nodes=SimpleNamespace(
                    get_or_none=lambda uid: self.term if uid == self.term.uid else None,
                )),
            )
            with self.library.isolated(self.graph["study"]):
                yield

    def read(self, monkeypatch, **kwargs):
        # Storage unit test: run the service body without opening a transaction.
        # The native integration uses the real constructor and transaction.
        service = object.__new__(StudyDiseaseMilestoneService)
        service._repos = self.repos
        with self.isolated(monkeypatch):
            return StudyDiseaseMilestoneService.get_all_disease_milestones.__wrapped__(
                service, STUDY_UID, study_value_version=VERSION, **kwargs
            )

    def mapped(self, row):
        graph = deepcopy(self.graph)
        graph["milestones"] = [row]
        source = NativeStudySource(graph)
        with source.isolated():
            report = source.mapper().map_with_report(graph["study"], VERSION)
        activity = next(
            item for item in report["document"]["study"]["versions"][0]["studyDesigns"][0]["activities"]
            if any(
                extension["url"].endswith("/native/diseaseMilestone")
                for extension in item.get("extensionAttributes", [])
            )
        )
        return report, activity


def test_historical_reader_uses_actual_selected_query_and_independent_name_attribute_versions(monkeypatch):
    source = MilestoneSource()
    source.attributes.has_version.rows[0][1].version = "3.0"
    row = source.read(monkeypatch, page_size=0, total_count=True).items[0]
    assert row.disease_milestone_type_name == "Historical diagnosis"
    assert row.disease_milestone_type_definition == "Historical definition with 0 and trailing whitespace. "
    assert row.repetition_indicator is False and row.order == 1
    assert row.terminology_source.name.version == "1.0"
    assert row.terminology_source.attributes.version == "3.0"
    assert row.terminology_source.as_of == AS_OF
    assert row.terminology_source.study_value_version == VERSION
    assert row.terminology_source.native_selection["selection"]["accepted_version"] is False
    report, activity = source.mapped(row)
    assert activity["name"] == row.disease_milestone_type_name
    assert activity["description"] == row.disease_milestone_type_definition
    assert not any(issue["code"] == "USDM_MILESTONE_TERM_HISTORY_REQUIRED"
                   for issue in report["mappingReport"]["issues"])
    retained = next(item["record"] for item in report["nativeRecords"]
                    if item["kind"] == "studyDiseaseMilestone")
    assert retained == row.model_dump(mode="json")
    assert source.queries


@pytest.mark.parametrize("name,definition", [
    (" Historical diagnosis ", "\tHistorical definition with 0. \n"),
    ("Historical diagnosis", " "),
    ("Diagnosis\t", "Definition\n"),
])
def test_native_response_keeps_stored_text_exact_through_mapping(monkeypatch, name, definition):
    source = MilestoneSource()
    name_value = source.name.has_version.rows[0][0]
    attributes_value = source.attributes.has_version.rows[0][0]
    name_value.__properties__["name"] = name
    attributes_value.__properties__["definition"] = definition
    row = source.read(monkeypatch).items[0]
    assert row.disease_milestone_type_name == name
    assert row.disease_milestone_type_definition == definition
    assert row.terminology_source.name.value["name"] == name
    assert row.terminology_source.attributes.value["definition"] == definition
    _, activity = source.mapped(row)
    assert activity["name"] == name
    assert activity["description"] == definition


def test_authored_milestone_input_still_uses_native_input_normalization():
    from clinical_mdr_api.models.study_selections.study_disease_milestone import (
        StudyDiseaseMilestoneCreateInput,
    )

    value = StudyDiseaseMilestoneCreateInput(
        disease_milestone_type=" DIAGNOSIS ", study_uid=" Study_1 ",
        repetition_indicator=False, order=1,
    )
    assert value.disease_milestone_type == "DIAGNOSIS"
    assert value.study_uid == "Study_1"


@pytest.mark.parametrize("mutation", ["missing", "ambiguous", "future", "draft"])
def test_unknown_or_ambiguous_historical_name_stays_missing_with_a_draft_issue(monkeypatch, mutation):
    source = MilestoneSource()
    first, relation = source.name.has_version.rows[0]
    if mutation == "missing":
        source.name.has_version.rows = []
        source.name.has_version.nodes = []
    elif mutation == "ambiguous":
        source.name.has_version.rows[1][1].start_date = AS_OF - timedelta(hours=1)
        relation.end_date = None
    elif mutation == "future":
        relation.start_date = AS_OF + timedelta(hours=1)
    else:
        relation.status = "Draft"
    row = source.read(monkeypatch).items[0]
    assert row.disease_milestone_type_name is None
    assert row.disease_milestone_type_definition.endswith(" ")
    assert row.terminology_source.name is None
    assert row.terminology_source.state == "unresolved"
    report, activity = source.mapped(row)
    assert "name" not in activity
    assert activity["description"] == row.disease_milestone_type_definition
    assert report["mappingReport"]["state"] == "incomplete"
    assert any(issue["code"] == "USDM_MILESTONE_TERM_HISTORY_REQUIRED"
               for issue in report["mappingReport"]["issues"])
    assert "UNSELECTED" not in str(activity)
    assert first.name == "Historical diagnosis"


def test_name_filter_and_pagination_use_resolved_history_not_current_labels(monkeypatch):
    source = MilestoneSource()
    second = deepcopy(source.rows[0])
    second["selection_identity"] = "second-native-selection"
    second["selection"].update(uid="Milestone_2", order=2)
    source.rows.append(second)
    result = source.read(
        monkeypatch, filter_by={"disease_milestone_type_name": {"v": ["Historical diagnosis"]}},
        sort_by={"order": True}, total_count=True, page_size=1, page_number=2,
    )
    assert result.total == 2 and [row.uid for row in result.items] == ["Milestone_2"]
    absent = source.read(
        monkeypatch, filter_by={"disease_milestone_type_name": {"v": ["UNSELECTED current diagnosis"]}},
        total_count=True,
    )
    assert absent.total == 0 and absent.items == []


def test_same_timestamp_released_and_locked_states_use_the_native_locked_scope(monkeypatch):
    source = MilestoneSource()
    released = deepcopy(source.rows[0])
    released["study_version"]["status"] = "RELEASED"
    source.rows.insert(0, released)
    row = source.read(monkeypatch).items[0]
    assert row.terminology_source.native_selection["study_version"]["status"] == "LOCKED"
    assert row.disease_milestone_type_name == "Historical diagnosis"


def test_an_exact_snapshot_with_no_milestones_remains_an_empty_complete_collection(monkeypatch):
    source = MilestoneSource()
    for field in (
        "selection_identity", "selection", "action_identity", "action",
        "context_identity", "term_uid", "codelist_uid",
    ):
        source.rows[0][field] = None
    result = source.read(monkeypatch, page_size=0, total_count=True)
    assert result.items == [] and result.total == 0


@pytest.mark.parametrize("mutation,code", [
    ("second-study-value", "SNAPSHOT_AMBIGUOUS"),
    ("wrong-timestamp", "SNAPSHOT_DATE_MISMATCH"),
    ("same-uid-distinct-selection", "SELECTION_AMBIGUOUS"),
    ("missing-term", "SOURCE_IDENTITY_REQUIRED"),
    ("future-selection", "SELECTION_AFTER_SNAPSHOT"),
])
def test_broken_selected_snapshot_never_guesses_another_native_reading(monkeypatch, mutation, code):
    source = MilestoneSource()
    if mutation in {"second-study-value", "same-uid-distinct-selection"}:
        other = deepcopy(source.rows[0])
        field = "study_value_identity" if mutation == "second-study-value" else "selection_identity"
        other[field] += "-other"
        source.rows.insert(0, other)
    elif mutation == "wrong-timestamp":
        source.rows[0]["study_version"]["start_date"] -= timedelta(days=1)
    elif mutation == "missing-term":
        source.rows[0]["term_uid"] = None
    else:
        source.rows[0]["action"]["date"] = AS_OF + timedelta(seconds=1)
    with source.isolated(monkeypatch), pytest.raises(StudyDiseaseMilestoneSourceError, match=code):
        read_disease_milestone_snapshot(source.repos, STUDY_UID, VERSION)


@pytest.mark.parametrize("field,value", [
    ("study_uid", "OtherStudy"), ("study_value_version", "1.0"),
    ("term_uid", "OtherTerm"), ("as_of", AS_OF - timedelta(days=1)),
])
def test_mapper_rejects_a_witness_from_another_exact_scope(field, value):
    source = MilestoneSource()
    row = source.graph["milestones"][0]
    setattr(row.terminology_source, field, value)
    with pytest.raises(USDMMappingAuthorityRequired, match="TERM_SOURCE_SCOPE_MISMATCH"):
        source.mapped(row)


def test_historical_dto_without_term_witness_retains_raw_text_but_cannot_assert_it():
    source = MilestoneSource()
    row = source.graph["milestones"][0]
    row.terminology_source = None
    before = native_json(row)
    report, activity = source.mapped(row)
    assert native_json(row) == before
    assert "name" not in activity and activity.get("description") is None
    assert report["mappingReport"]["state"] == "incomplete"
    assert any(issue["code"] == "USDM_MILESTONE_TERM_HISTORY_REQUIRED"
               for issue in report["mappingReport"]["issues"])
    assert next(item["record"] for item in report["nativeRecords"]
                if item["kind"] == "studyDiseaseMilestone") == before


def test_conflicting_response_text_is_not_authorized_by_an_unchanged_term_witness():
    source = MilestoneSource()
    row = source.graph["milestones"][0]
    row.disease_milestone_type_name = "Current label injected into a historical DTO"
    with pytest.raises(USDMMappingAuthorityRequired, match="TERM_SOURCE_TEXT_MISMATCH"):
        source.mapped(row)
