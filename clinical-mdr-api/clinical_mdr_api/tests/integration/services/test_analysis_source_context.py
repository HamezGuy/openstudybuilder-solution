"""The analysis listings must preserve native source records before analysis coding."""

import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from neomodel import db

from clinical_mdr_api.services.listings.listings_adam import ADAMListingsService
from common.config import settings


@pytest.fixture
def source_study():
    assert os.environ.get("OSB_UNTIMED_FIXTURE") == "disposable-igs22"
    assert urlsplit(settings.neo4j_dsn).hostname == "igs22-graph"
    uid = "analysis-source-" + uuid4().hex
    db.cypher_query(
        """
        CREATE (root:StudyRoot {uid: $uid})-[:LATEST]->
               (study:StudyValue {study_id_prefix: 'SOURCE', study_number: '01'})
        CREATE (study)-[:HAS_STUDY_TIME_FIELD]->
               (:StudyTimeField {field_name: 'soa_preferred_time_unit'})
               -[:HAS_UNIT]->(:UnitDefinitionRoot)-[:LATEST_FINAL]->
               (:UnitDefinitionValue {name: 'day'})
        CREATE (type:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)
               -[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->
               (:CTTermNameValue {name: 'Follow-up'})
        CREATE (study)-[:HAS_STUDY_VISIT]->(visit:StudyVisit {
            uid: $uid + '-manual', unique_visit_number: '100',
            visit_name_label: 'Monthly follow-up', short_visit_label: 'MONTHLY',
            timing_mode: 'UNTIMED', is_global_anchor_visit: false,
            description: 'Every calendar month for 12 months from the actual last dose.'})
        CREATE (visit)-[:HAS_VISIT_TYPE]->(type)
        CREATE (visit)-[:HAS_VISIT_NAME]->(:VisitNameRoot)-[:LATEST]->
               (:VisitNameValue {name: 'MONTHLY'})
        CREATE (study)-[:HAS_STUDY_VISIT]->(timed:StudyVisit {
            uid: $uid + '-timed', unique_visit_number: '200', short_visit_label: 'V0',
            timing_mode: 'FIXED', is_global_anchor_visit: true})
        CREATE (timed)-[:HAS_VISIT_TYPE]->(type)
        CREATE (timed)-[:HAS_VISIT_NAME]->(:VisitNameRoot)-[:LATEST]->
               (:VisitNameValue {name: 'Visit zero'})
        CREATE (timed)-[:HAS_STUDY_DAY]->(:StudyDayRoot)-[:LATEST]->
               (:StudyDayValue {value: 0, name: 'Day 0'})
        CREATE (timed)-[:HAS_STUDY_WEEK]->(:StudyWeekRoot)-[:LATEST]->
               (:StudyWeekValue {value: 0, name: 'Week 0'})
        WITH root, study, visit, timed
        UNWIND ['Symptoms', 'Medication'] AS activity_name
        CREATE (study)-[:HAS_STUDY_ACTIVITY]->(activity:StudyActivity {
            uid: $uid + '-' + activity_name, order: 0})
        CREATE (activity)-[:HAS_SELECTED_ACTIVITY]->
               (:ActivityValue {name: activity_name})
        CREATE (study)-[:HAS_STUDY_ACTIVITY_SCHEDULE]->(schedule:StudyActivitySchedule {
            uid: $uid + '-schedule-' + activity_name})
        CREATE (activity)-[:STUDY_ACTIVITY_HAS_SCHEDULE]->(schedule)
        CREATE (visit)-[:STUDY_VISIT_HAS_SCHEDULE]->(schedule)
        """,
        {"uid": uid},
    )
    return uid


def test_untimed_visit_keeps_name_description_and_null_fixed_times(source_study):
    visits = ADAMListingsService().list_mdvisit(source_study)
    manual = next(item for item in visits if item.AVISITN == 100)
    assert manual.SOURCE_VISIT_UID == source_study + "-manual"
    assert manual.AVISIT == "Monthly follow-up"
    assert manual.TIMING_MODE == "UNTIMED"
    assert manual.VISIT_DESCRIPTION == (
        "Every calendar month for 12 months from the actual last dose."
    )
    assert manual.VISLABEL == "MONTHLY"
    assert manual.AVISIT1N is None
    assert manual.AVISIT1 is None
    assert manual.AVISIT2 is None
    assert manual.AVISIT2N is None


def test_fixed_zero_day_and_week_remain_real_values(source_study):
    timed = next(
        item
        for item in ADAMListingsService().list_mdvisit(source_study)
        if item.AVISITN == 200
    )
    assert timed.AVISIT == "Visit zero (day 0)"
    assert timed.AVISIT1N == 0
    assert timed.AVISIT1 == "Day 0"
    assert timed.AVISIT2N == "0"
    assert timed.AVISIT2 == "Week 0"


def test_uncoded_activities_with_same_order_do_not_collapse(source_study):
    flow = ADAMListingsService().list_mdflow(source_study)
    assert len(flow) == 2
    assert {item.SOURCE_ACTIVITY_NAME for item in flow} == {"Symptoms", "Medication"}
    assert {item.SOURCE_SCHEDULE_UID for item in flow} == {
        source_study + "-schedule-Symptoms",
        source_study + "-schedule-Medication",
    }
    assert {item.SOURCE_ACTIVITY_UID for item in flow} == {
        source_study + "-Symptoms",
        source_study + "-Medication",
    }
    for item in flow:
        assert item.AVISIT == "Monthly follow-up"
        assert item.SOURCE_VISIT_UID == source_study + "-manual"
        assert item.PARAMN == "0"
        assert item.PARAMCD is None
        assert item.PARAM is None
        assert item.TOPICCD is None
        assert item.TIMING_MODE == "UNTIMED"
        assert item.VISIT_DESCRIPTION


def test_unconfigured_preferred_unit_does_not_erase_known_visit_name(source_study):
    db.cypher_query(
        """
        MATCH (:StudyRoot {uid: $uid})-[:LATEST]->(study)
              -->(field:StudyTimeField)
        DETACH DELETE field
        """,
        {"uid": source_study},
    )
    visits = ADAMListingsService().list_mdvisit(source_study)
    assert next(item for item in visits if item.AVISITN == 200).AVISIT == "Visit zero"


def test_released_version_and_other_study_are_isolated(source_study):
    db.cypher_query(
        """
        MATCH (root:StudyRoot {uid: $uid})
        CREATE (root)-[:HAS_VERSION {status: 'RELEASED', version: '1'}]->
               (old:StudyValue {study_id_prefix: 'SOURCE', study_number: '01'})
        CREATE (old)-[:HAS_STUDY_VISIT]->(visit:StudyVisit {
            uid: $uid + '-old', unique_visit_number: '300', timing_mode: 'UNTIMED',
            visit_name_label: 'Original visit', description: 'Original protocol wording'})
        CREATE (visit)-[:HAS_VISIT_TYPE]->(:CTTermContext)-[:HAS_SELECTED_TERM]->
               (:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST]->
               (:CTTermNameValue {name: 'Original type'})
        CREATE (old)-[:HAS_STUDY_ACTIVITY]->(activity:StudyActivity {
            uid: $uid + '-old-activity', order: 1})
        CREATE (activity)-[:HAS_SELECTED_ACTIVITY]->(:ActivityValue {name: 'Original activity'})
        CREATE (old)-[:HAS_STUDY_ACTIVITY_SCHEDULE]->
               (schedule:StudyActivitySchedule {uid: $uid + '-old-schedule'})
        CREATE (visit)-[:STUDY_VISIT_HAS_SCHEDULE]->(schedule)
        CREATE (activity)-[:STUDY_ACTIVITY_HAS_SCHEDULE]->(schedule)
        CREATE (:StudyRoot {uid: $uid + '-other'})-[:LATEST]->
               (:StudyValue {study_id_prefix: 'OTHER', study_number: '99'})
        """,
        {"uid": source_study},
    )
    service = ADAMListingsService()
    old_visits = service.list_mdvisit(source_study, study_value_version="1")
    assert [item.AVISIT for item in old_visits] == ["Original visit"]
    old_flow = service.list_mdflow(source_study, study_value_version="1")
    assert [item.SOURCE_ACTIVITY_NAME for item in old_flow] == ["Original activity"]
    assert len(service.list_mdflow(source_study)) == 2
    assert service.list_mdflow(source_study + "-other") == []
    assert service.list_mdvisit(source_study + "-other") == []
