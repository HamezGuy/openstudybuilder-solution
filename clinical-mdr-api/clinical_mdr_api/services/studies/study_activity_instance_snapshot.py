"""Exact selected activity definition and its independently dated descendants."""

from copy import deepcopy

from neomodel import db

from clinical_mdr_api.domain_repositories.models.activities import ActivityInstanceRoot
from clinical_mdr_api.domain_repositories.models.biomedical_concepts import ActivityItemClassRoot
from clinical_mdr_api.services.ddf.usdm_ct_package_mapping import _source_json
from clinical_mdr_api.services.studies.study_compound_snapshot import StudyCompoundSourceError
from clinical_mdr_api.services.studies.study_native_library_snapshot import (
    StudyNativeLibrarySnapshot, one, related,
)


SELECTED_ACTIVITY_QUERY = """
    MATCH (study:StudyRoot {uid:$study_uid})-[study_version:HAS_VERSION]->(study_value:StudyValue)
    WHERE study_version.version=$study_value_version
      AND study_version.status IN ['LOCKED','RELEASED']
      AND study_version.start_date=$as_of
    MATCH (study_value)-[:HAS_STUDY_ACTIVITY_INSTANCE]->
          (selection:StudyActivityInstance {uid:$selection_uid})
          -[:HAS_SELECTED_ACTIVITY_INSTANCE]->(value:ActivityInstanceValue)
    MATCH (root:ActivityInstanceRoot {uid:$uid})-[version:HAS_VERSION]->(value)
    WHERE version.version=$version
    RETURN DISTINCT elementId(study_value), elementId(selection),
                    elementId(value), properties(selection)
"""


def _selected_value(snapshot, value, kind):
    if value is None:
        snapshot.missing(kind, None, "STUDY_LIBRARY_SELECTED_CHILD_MISSING")
        return None
    # The same immutable value can have Draft/Final/Retired history edges to
    # one root. Those state edges do not create several parent identities.
    roots = {
        snapshot.reader._value_id(root): root for root in related(value, "has_version")
    }
    if len(roots) != 1:
        snapshot.missing(kind, snapshot.reader._value_id(value), "STUDY_LIBRARY_SELECTED_PARENT_NOT_UNIQUE")
        return None
    root = next(iter(roots.values()))
    _, evidence = snapshot.value(
        root, kind, value_identity=snapshot.reader._value_id(value),
    )
    return deepcopy(evidence)


def project_activity_definition(snapshot, root, selected_value_identity, version, selection):
    """Materialize graph facts, never a mutable ActivityInstance service DTO."""
    value, evidence = snapshot.value(root, "activityInstance", value_identity=selected_value_identity)
    if value is None or evidence["version"] != version:
        raise StudyCompoundSourceError("STUDY_ACTIVITY_SELECTED_VERSION_MISMATCH")
    class_root = one(value, "activity_instance_class")
    _, instance_class = snapshot.value(class_root, "activityInstanceClass")
    items = []
    for item in related(value, "contains_activity_item"):
        item_identity = snapshot.reader._value_id(item)
        item_class_root = one(item, "has_activity_item_class")
        item_class, item_class_source = snapshot.value(item_class_root, "activityItemClass")
        datatype = snapshot.context(one(item_class, "has_data_type")) if item_class is not None else None
        role = snapshot.context(one(item_class, "has_role")) if item_class is not None else None
        item_record = {
            "nativeIdentity": item_identity,
            "properties": _source_json(dict(item.__properties__)),
            "activity_item_class": {
                "uid": getattr(item_class_root, "uid", None),
                "source": deepcopy(item_class_source),
                "dataType": datatype, "role": role,
            },
            "codelists": [snapshot.codelist(node.uid) for node in related(item, "has_codelist")],
            "ct_terms": [snapshot.context(node) for node in related(item, "has_ct_term")],
            "unit_definitions": [snapshot.unit(node) for node in related(item, "has_unit_definition")],
        }
        # Source scalar fields stay available in their native spellings as well
        # as the complete stored row. They are not clinical response defaults.
        item_record.update(item_record["properties"])
        items.append(item_record)
    items.sort(key=lambda item: item["nativeIdentity"])
    grouping_root = one(root, "has_grouping_root")
    grouping_value, grouping_source = snapshot.value(grouping_root, "activityInstanceGrouping")
    groupings = []
    if grouping_value is not None:
        for grouping in related(grouping_value, "has_activity"):
            groupings.append({
                "nativeIdentity": snapshot.reader._value_id(grouping),
                "properties": _source_json(dict(grouping.__properties__)),
                "activity": _selected_value(snapshot, one(grouping, "has_grouping"), "activity"),
                "group": _selected_value(snapshot, one(grouping, "has_selected_group"), "activityGroup"),
                "subgroup": _selected_value(snapshot, one(grouping, "has_selected_subgroup"), "activitySubGroup"),
            })
    result = {
        **deepcopy(evidence["properties"]),
        "uid": root.uid, "version": version,
        "activity_instance_class": deepcopy(instance_class),
        "activity_items": items, "activity_groupings": groupings,
        "nativeSnapshot": {
            "contract": "osb-selected-activity-definition/1",
            "studyUid": snapshot.reader.study_uid,
            "studyValueVersion": snapshot.reader.study_value_version,
            "asOf": snapshot.as_of.isoformat(),
            "selection": deepcopy(selection),
            "activityInstance": deepcopy(evidence),
            "grouping": deepcopy(grouping_source),
            "records": deepcopy(snapshot.records), "issues": deepcopy(snapshot.issues),
        },
    }
    return result


def read_study_activity_instance_definition(
    uid, version, *, study_uid, study_value_version,
    study_activity_instance_uid, as_of=None,
):
    snapshot = StudyNativeLibrarySnapshot(study_uid, study_value_version, as_of=as_of)
    if study_value_version is None:
        # Current native draft selection is explicit. It is not a fallback from
        # a failed requested version.
        query = SELECTED_ACTIVITY_QUERY.replace(
            "[study_version:HAS_VERSION]", "[study_version:LATEST]"
        ).replace(
            "study_version.version=$study_value_version\n      AND study_version.status IN ['LOCKED','RELEASED']\n      AND study_version.start_date=$as_of",
            "true",
        )
    else:
        query = SELECTED_ACTIVITY_QUERY
    rows, _ = db.cypher_query(query, {
        "study_uid": study_uid, "study_value_version": study_value_version,
        "selection_uid": study_activity_instance_uid, "uid": uid, "version": version,
        "as_of": snapshot.as_of,
    })
    if len(rows) != 1 or len(rows[0]) != 4 or any(not value for value in rows[0][:3]):
        raise StudyCompoundSourceError("STUDY_ACTIVITY_SELECTED_PATH_NOT_UNIQUE")
    study_value, selection_id, value_id, properties = rows[0]
    if properties.get("uid") != study_activity_instance_uid:
        raise StudyCompoundSourceError("STUDY_ACTIVITY_SELECTION_IDENTITY_MISMATCH")
    root = ActivityInstanceRoot.nodes.get_or_none(uid=uid)
    if root is None:
        raise StudyCompoundSourceError("STUDY_ACTIVITY_ROOT_MISSING")
    return project_activity_definition(snapshot, root, value_id, version, {
        "studyValueIdentity": study_value, "selectionIdentity": selection_id,
        "properties": _source_json(properties),
    })


def resolve_candidate_class_history(rows, study_uid, study_value_version, *, snapshot=None):
    """Enrich exact ODM reachability rows without current class/value joins."""
    if not rows:
        return rows
    snapshot = snapshot or StudyNativeLibrarySnapshot(study_uid, study_value_version)
    for row in rows:
        item = row.get("activity_item")
        if not isinstance(item, dict):
            continue
        uid = item.get("activityItemClassUid")
        root = ActivityItemClassRoot.nodes.get_or_none(uid=uid) if uid else None
        _, evidence = snapshot.value(root, "activityItemClass")
        properties = evidence["properties"] if evidence else {}
        item.update({
            "activityItemClassName": properties.get("display_name") if properties.get("display_name") is not None else properties.get("name"),
            "activityItemClassDefinition": properties.get("definition"),
            "activityItemClassNciConceptId": properties.get("nci_concept_id"),
            "activityItemClassSource": deepcopy(evidence),
            "activityItemClassSourceIssues": deepcopy(snapshot.issues) if evidence is None else [],
        })
    return rows
