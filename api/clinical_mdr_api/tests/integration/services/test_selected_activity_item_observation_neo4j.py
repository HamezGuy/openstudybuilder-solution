"""Exact selected graph fixtures; no real draft creation or native approval claim."""

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.integrations.selected_activity_item_observation import (
    SelectedActivityItemRepository,
)
from clinical_mdr_api.services.integrations.native_item_observation import (
    NativeItemObservationError,
)
from clinical_mdr_api.services.integrations.selected_activity_item_observation import (
    SelectedActivityItemService,
)
from clinical_mdr_api.tests.integration.services.test_native_item_observation_neo4j import (
    native,
)
from clinical_mdr_api.tests.unit.services.test_selected_activity_item_observation import (
    selection_request,
)


@pytest.fixture()
def selected(native):
    f = native
    f.request = selection_request(f.request)
    f.params.update(f.request.model_dump())
    db.cypher_query(
        """
      MATCH (study:StudyRoot {uid:$nativeStudyId})-[:LATEST]->(studyValue:StudyValue)
      MATCH (study)-[head:LATEST_DRAFT]->(studyValue)
      CREATE (study)-[:HAS_VERSION {version:$studyValueVersion,status:head.status,
        start_date:head.start_date}]->(studyValue)
      CREATE (selection:StudyActivityInstance {fixture:$fixture,uid:$studyActivityInstanceUid})
      CREATE (studyValue)-[:HAS_STUDY_ACTIVITY_INSTANCE]->(selection)
      CREATE (activityRoot:ActivityInstanceRoot {fixture:$fixture,uid:$activityInstanceUid})
      CREATE (activity:ActivityInstanceValue {fixture:$fixture})
      CREATE (activityRoot)-[:LATEST]->(activity)
      CREATE (activityRoot)-[:HAS_VERSION {version:$activityInstanceVersion,status:'Final'}]->(activity)
      CREATE (selection)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(activity)
      CREATE (classRoot:ActivityItemClassRoot {fixture:$fixture,uid:$activityItemClassUid})
      CREATE (class:ActivityItemClassValue {fixture:$fixture})
      CREATE (classRoot)-[:LATEST]->(class)
      CREATE (classRoot)-[:HAS_VERSION {version:$activityItemClassVersion,status:'Final'}]->(class)
      CREATE (activityItem:ActivityItem {fixture:$fixture,text_value:'Native selected item',
        is_adam_param_specific:false,is_activity_instance_id_specific:true})
      CREATE (activity)-[:CONTAINS_ACTIVITY_ITEM]->(activityItem)
      CREATE (classRoot)-[:HAS_ACTIVITY_ITEM]->(activityItem)
      WITH activityItem
      MATCH (:OdmItemRoot {uid:$itemUid})-[:LATEST]->(item)
      CREATE (item)-[:LINKS_TO_ACTIVITY_ITEM {order:1,primary:true}]->(activityItem)
    """,
        f.params,
    )
    f.service = SelectedActivityItemService(
        auth_reader=lambda: f.auth, clock=lambda: f.now[0], monotonic=lambda: f.now[0]
    )
    return f


def test_actual_one_current_selected_full_path(selected):
    value = selected.service.observe(selected.request)
    assert value["selectedActivityReachabilityVerified"] is True
    assert value["selection"]["activityInstanceUid"] == "AI_1"
    assert value["selection"]["activityItem"]["textValue"] == "Native selected item"
    assert (
        value["semanticApprovalVerified"] is False
        and value["requirednessVerified"] is False
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "MATCH (:StudyRoot)-[v:HAS_VERSION]->() DELETE v",
        "MATCH (:StudyRoot)-[v:HAS_VERSION]->() SET v.version='older'",
        "MATCH (:StudyRoot)-[v:HAS_VERSION]->() SET v.status='LOCKED'",
        "MATCH (:StudyValue)-[r:HAS_STUDY_ACTIVITY_INSTANCE]->() DELETE r",
        "MATCH (s:StudyActivityInstance) SET s.uid='other-selection'",
        "MATCH (:ActivityInstanceRoot)-[v:HAS_VERSION]->() SET v.version='2.0'",
        "MATCH (:ActivityItemClassRoot)-[v:HAS_VERSION]->() SET v.version='2.0'",
        "MATCH (:ActivityItemClassRoot)-[v:HAS_VERSION]->() SET v.status='Retired'",
        "MATCH ()-[r:LINKS_TO_ACTIVITY_ITEM]->() DELETE r",
        "MATCH (a:ActivityItem) SET a.text_value=$oversized",
        "MATCH (a:ActivityItem) CREATE (a)-[:HAS_UNIT_DEFINITION]->(:UnitDefinitionRoot {fixture:$fixture,uid:'unit-1'})",
        "MATCH (sv:StudyValue)-[:HAS_STUDY_ACTIVITY_INSTANCE]->(s) CREATE (sv)-[:HAS_STUDY_ACTIVITY_INSTANCE]->(s)",
        "MATCH (s:StudyActivityInstance)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(a) CREATE (s)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(a)",
        "MATCH (a:ActivityInstanceValue)-[:CONTAINS_ACTIVITY_ITEM]->(i:ActivityItem), (c:ActivityItemClassRoot)-[:HAS_ACTIVITY_ITEM]->(i), (item:OdmItemValue)-[l:LINKS_TO_ACTIVITY_ITEM]->(i) CREATE (copy:ActivityItem) SET copy=properties(i) CREATE (a)-[:CONTAINS_ACTIVITY_ITEM]->(copy) CREATE (c)-[:HAS_ACTIVITY_ITEM]->(copy) CREATE (item)-[l2:LINKS_TO_ACTIVITY_ITEM]->(copy) SET l2=properties(l)",
        "MATCH (r:ActivityItemClassRoot)-[:LATEST]->(v) CREATE (r)-[:HAS_VERSION {version:'1.0',status:'Final'}]->(v)",
        "MATCH (s:DomainStudyScope) SET s.status='quarantined'",
    ],
)
def test_actual_missing_changed_duplicate_unversioned_or_unsupported_path_refuses(
    selected, mutation
):
    db.cypher_query(mutation, {**selected.params, "oversized": "x" * 4097})
    with pytest.raises(NativeItemObservationError):
        selected.service.observe(selected.request)


def test_duplicate_introduced_after_independent_count_is_still_refused(selected):
    class Duplicate(SelectedActivityItemRepository):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def path_count(self, p, timeout):
            rows = super().path_count(p, timeout)
            self.calls += 1
            if self.calls == 1:
                db.cypher_query(
                    "MATCH (s:StudyActivityInstance)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(a) CREATE (s)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(a)"
                )
            return rows

    selected.service.repository = Duplicate()
    with pytest.raises(NativeItemObservationError, match="PROJECTION_UNAVAILABLE"):
        selected.service.observe(selected.request)


def test_final_native_authority_withdrawal_after_path_projection_refuses(selected):
    class Withdraw(SelectedActivityItemRepository):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def path_projection(self, p, timeout):
            rows = super().path_projection(p, timeout)
            self.calls += 1
            if self.calls == 2:
                db.cypher_query("MATCH (s:DomainStudyScope) SET s.status='quarantined'")
            return rows

    selected.service.repository = Withdraw()
    with pytest.raises(NativeItemObservationError, match="SCOPE_UNAVAILABLE"):
        selected.service.observe(selected.request)
