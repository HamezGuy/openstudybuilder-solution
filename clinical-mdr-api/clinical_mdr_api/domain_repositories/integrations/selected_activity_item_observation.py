"""Independent cardinality check and bounded projection of one full native path."""
from clinical_mdr_api.domain_repositories.integrations.native_item_observation import NativeItemObservationRepository


# No incoming global item-group/form/event edge establishes study applicability.
# No elementId is returned or used as a synthetic public ActivityItem identity.
PATH = """
 MATCH (study:StudyRoot {uid:$nativeStudyId})
   -[head:LATEST_DRAFT|LATEST|LATEST_LOCKED|LATEST_RELEASED]->(studyValue:StudyValue)
 WHERE head.version=$studyValueVersion AND NOT EXISTS {
   MATCH (study)-[other:LATEST_DRAFT|LATEST|LATEST_LOCKED|LATEST_RELEASED]->()
   WHERE (CASE type(other) WHEN 'LATEST_DRAFT' THEN 0 WHEN 'LATEST' THEN 1
            WHEN 'LATEST_LOCKED' THEN 2 ELSE 3 END)
       < (CASE type(head) WHEN 'LATEST_DRAFT' THEN 0 WHEN 'LATEST' THEN 1
            WHEN 'LATEST_LOCKED' THEN 2 ELSE 3 END)
 }
 MATCH (study)-[studyVersion:HAS_VERSION {version:$studyValueVersion}]->(studyValue)
 WHERE studyVersion.end_date IS NULL AND studyVersion.status=head.status
 MATCH (studyValue)-[:HAS_STUDY_ACTIVITY_INSTANCE]->
   (selection:StudyActivityInstance {uid:$studyActivityInstanceUid})
   -[:HAS_SELECTED_ACTIVITY_INSTANCE]->(activity:ActivityInstanceValue)
 MATCH (activityRoot:ActivityInstanceRoot {uid:$activityInstanceUid})-[:LATEST]->(activity)
 MATCH (activityRoot)-[activityVersion:HAS_VERSION {version:$activityInstanceVersion,status:'Final'}]->(activity)
 WHERE activityVersion.end_date IS NULL
 MATCH (activity)-[:CONTAINS_ACTIVITY_ITEM]->(activityItem:ActivityItem)
 MATCH (classRoot:ActivityItemClassRoot {uid:$activityItemClassUid})-[:HAS_ACTIVITY_ITEM]->(activityItem)
 MATCH (classRoot)-[:LATEST]->(classValue:ActivityItemClassValue)
 MATCH (classRoot)-[classVersion:HAS_VERSION {version:$activityItemClassVersion,status:'Final'}]->(classValue)
 WHERE classVersion.end_date IS NULL
 MATCH (itemRoot:OdmItemRoot {uid:$itemUid})-[:LATEST]->(item:OdmItemValue)
 MATCH (itemRoot)-[itemVersion:HAS_VERSION {version:$itemVersion,status:'Final'}]->(item)
 WHERE itemVersion.end_date IS NULL
 MATCH (item)-[itemLink:LINKS_TO_ACTIVITY_ITEM]->(activityItem)
"""


class SelectedActivityItemRepository(NativeItemObservationRepository):
    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint

    def query(self, text, params, timeout):
        if self.checkpoint:
            timeout = min(timeout, self.checkpoint())
        result = super().query(text, params, timeout)
        if self.checkpoint:
            self.checkpoint()
        return result

    def path_count(self, p, timeout):
        # Stop after two matched paths, before any data-bearing projection.
        return self.query("CALL { " + PATH + " RETURN 1 AS found LIMIT 2 } RETURN count(found)", p, timeout)

    def path_projection(self, p, timeout):
        return self.query(PATH + """
          WITH study,studyVersion,selection,activityRoot,activityVersion,classRoot,classVersion,
            itemRoot,itemVersion,activityItem,itemLink,
            [activityItem.text_value,itemLink.preset_response_value,itemLink.value_condition,
             itemLink.value_dependent_map] AS strings
          RETURN CASE WHEN all(v IN strings WHERE v IS NULL OR size(v)<=4096) THEN {
            nativeStudyId:study.uid,studyValueVersion:studyVersion.version,studyVersionStatus:studyVersion.status,
            studyActivityInstanceUid:selection.uid,activityInstanceUid:activityRoot.uid,
            activityInstanceVersion:activityVersion.version,activityInstanceStatus:activityVersion.status,
            activityItemClassUid:classRoot.uid,activityItemClassVersion:classVersion.version,
            activityItemClassStatus:classVersion.status,itemUid:itemRoot.uid,itemVersion:itemVersion.version,
            itemStatus:itemVersion.status,activityItem:{textValue:activityItem.text_value,
              isAdamParamSpecific:activityItem.is_adam_param_specific,
              isActivityInstanceIdSpecific:activityItem.is_activity_instance_id_specific},
            itemLink:{order:itemLink.order,primary:itemLink.primary,
              presetResponseValue:itemLink.preset_response_value,valueCondition:itemLink.value_condition,
              valueDependentMap:itemLink.value_dependent_map}
          } ELSE null END,
          EXISTS { (activityItem)-[:HAS_CODELIST|HAS_CT_TERM|HAS_UNIT_DEFINITION]->() }
          LIMIT 2
        """, p, timeout)
