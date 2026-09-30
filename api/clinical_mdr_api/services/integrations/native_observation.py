"""Read native study state and edit history without granting mapping authority.

Raw records are retained intact. Comparison excludes only explicitly declared
read-time study_version model labels, never arbitrary nested clinical metadata.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import hashlib
from importlib import import_module
import re
from typing import Any, Callable
from clinical_mdr_api.services.integrations.canonical_json import canonical_json
from common.exceptions import NotFoundException


class NativeObservationError(ValueError):
    pass


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def comparison_record(record: dict, collection: str | None = None) -> tuple[dict, list[str]]:
    compared = deepcopy(record)
    ignored = []
    if isinstance(compared.get("study_version"), str) and re.fullmatch(r"LATEST on \d{4}-\d{2}-\d{2}T.+", compared["study_version"]):
        del compared["study_version"]
        ignored.append("/study_version")
    # Profile 1.1 declares one additional generated model label: the objective
    # model embedded by the endpoint service. Other nested metadata is source.
    objective = compared.get("study_objective")
    if collection == "study_endpoints" and isinstance(objective, dict) and isinstance(objective.get("study_version"), str) and re.fullmatch(r"LATEST on \d{4}-\d{2}-\d{2}T.+", objective["study_version"]):
        del objective["study_version"]
        ignored.append("/study_objective/study_version")
    return compared, ignored


@dataclass(frozen=True)
class NativeCollection:
    collection: str
    resource_type: str
    uid_field: str
    module: str
    service: str
    current: str = "get_all_selection"
    audit: str | None = "get_all_selection_audit_trail"
    paginated: bool = True
    no_brackets: bool = False
    query_scope_only: bool = False


COLLECTIONS = (
    NativeCollection("study_arms", "StudySelectionArm", "arm_uid", "study_arm_selection", "StudyArmSelectionService"),
    NativeCollection("study_visits", "StudyVisit", "uid", "study_visit", "StudyVisitService", "get_all_visits", "audit_trail_all_visits"),
    NativeCollection("study_epochs", "StudyEpoch", "uid", "study_epoch", "StudyEpochService", "get_all_epochs", "audit_trail_all_epochs"),
    NativeCollection("study_objectives", "StudySelectionObjective", "study_objective_uid", "study_objective_selection", "StudyObjectiveSelectionService", no_brackets=True),
    NativeCollection("study_endpoints", "StudySelectionEndpoint", "study_endpoint_uid", "study_endpoint_selection", "StudyEndpointSelectionService", no_brackets=True),
    NativeCollection("study_criteria", "StudySelectionCriteria", "study_criteria_uid", "study_criteria_selection", "StudyCriteriaSelectionService", no_brackets=True),
    NativeCollection("study_elements", "StudySelectionElement", "element_uid", "study_element_selection", "StudyElementSelectionService"),
    NativeCollection("study_design_cells", "StudyDesignCell", "design_cell_uid", "study_design_cell", "StudyDesignCellService", "get_all_design_cells", "get_all_design_cells_audit_trail", paginated=False),
    NativeCollection("study_cohorts", "StudyCohort", "cohort_uid", "study_cohort_selection", "StudyCohortSelectionService"),
    NativeCollection("study_branch_arms", "StudyBranchArm", "branch_arm_uid", "study_branch_arm_selection", "StudyBranchArmSelectionService"),
    NativeCollection("study_activity_groups", "StudyActivityGroup", "study_activity_group_uid", "study_activity_group", "StudyActivityGroupService"),
    NativeCollection("study_activity_subgroups", "StudyActivitySubGroup", "study_activity_subgroup_uid", "study_activity_subgroup", "StudyActivitySubGroupService"),
    NativeCollection("study_activities", "StudySelectionActivity", "study_activity_uid", "study_activity_selection", "StudyActivitySelectionService"),
    NativeCollection("study_activity_instances", "StudyActivityInstance", "study_activity_instance_uid", "study_activity_instance_selection", "StudyActivityInstanceSelectionService"),
    NativeCollection("study_activity_instructions", "StudyActivityInstruction", "study_activity_instruction_uid", "study_activity_instruction", "StudyActivityInstructionService", "get_all_instructions", None, paginated=False),
    NativeCollection("study_activity_schedules", "StudyActivitySchedule", "study_activity_schedule_uid", "study_activity_schedule", "StudyActivityScheduleService", "get_all_schedules", "get_all_schedules_audit_trail", paginated=False, query_scope_only=True),
    NativeCollection("study_standard_versions", "StudyStandardVersion", "uid", "study_standard_version_selection", "StudyStandardVersionService", "get_standard_versions_in_study", "audit_trail_all_standard_versions", paginated=False),
    NativeCollection("study_compounds", "StudySelectionCompound", "study_compound_uid", "study_compound_selection", "StudyCompoundSelectionService"),
    NativeCollection("study_compound_dosings", "StudyCompoundDosing", "study_compound_dosing_uid", "study_compound_dosing_selection", "StudyCompoundDosingSelectionService", "get_all_compound_dosings"),
)


def _json(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        from fastapi.encoders import jsonable_encoder
        return jsonable_encoder(asdict(value))
    return deepcopy(value)


def _rows(value, collection="unknown"):
    if isinstance(value, list):
        return value
    items = value.get("items") if isinstance(value, dict) else getattr(value, "items", None)
    if not isinstance(items, list):
        raise NativeObservationError("NATIVE_OBSERVATION_COLLECTION_INCOMPLETE:" + collection + ":" + type(value).__name__)
    total = value.get("total") if isinstance(value, dict) else getattr(value, "total", None)
    # These services use total=0 when total_count was not requested.
    if total not in (None, 0, len(items)):
        raise NativeObservationError("NATIVE_OBSERVATION_COLLECTION_TRUNCATED")
    return items


def _raw_action_json(value):
    if isinstance(value, dict):
        return {key: _raw_action_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_raw_action_json(item) for item in value]
    # Neo4j temporal properties carry nanoseconds. Keep their source precision.
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def collect_study_actions(study_uid: str) -> list[dict]:
    """Retain the exact owned action, author subject, and directly linked values.

    No inferred username joins or library graph walk. Native action identifiers
    are opaque and scoped to this database/study observation stream.
    """
    from neomodel import db
    rows, columns = db.cypher_query("""
        MATCH (sr:StudyRoot {uid: $study_uid})-[:AUDIT_TRAIL]->(action:StudyAction)
        OPTIONAL MATCH (owner:StudyRoot)-[:AUDIT_TRAIL]->(action)
        WITH sr, action, collect(DISTINCT owner.uid) AS owners
        OPTIONAL MATCH (action)-[:BEFORE]->(before)
        WITH sr, action, owners, collect(DISTINCT CASE WHEN before IS NULL THEN null ELSE
            {nodeId: elementId(before), labels: labels(before), properties: properties(before)} END) AS before
        OPTIONAL MATCH (action)-[:AFTER]->(after)
        RETURN sr.uid AS study_uid, elementId(action) AS actionId, owners,
            {labels: labels(action), properties: properties(action)} AS action,
            before, collect(DISTINCT CASE WHEN after IS NULL THEN null ELSE
            {nodeId: elementId(after), labels: labels(after), properties: properties(after)} END) AS after
        ORDER BY actionId
    """, {"study_uid": study_uid})
    result = []
    for values in rows:
        row = _raw_action_json(dict(zip(columns, values)))
        if row["study_uid"] != study_uid or row["owners"] != [study_uid]:
            raise NativeObservationError("NATIVE_OBSERVATION_ACTION_SCOPE_AMBIGUOUS")
        # Cypher collection and label ordering has no semantic meaning. Sorting
        # gives repeatable source readings without changing any property value.
        row["action"]["labels"].sort()
        for side in ("before", "after"):
            row[side].sort(key=lambda node: node["nodeId"])
            for node in row[side]:
                node["labels"].sort()
        properties = row["action"]["properties"]
        actions = [kind for kind in ("Create", "Edit", "Delete") if kind in row["action"]["labels"]]
        row.update(start_date=properties.get("date"), author_id=properties.get("author_id"),
                   change_type=actions[0] if len(actions) == 1 else None)
        result.append(row)
    return result


USER_PROJECTION_FIELDS = ("username", "oid", "subjectType", "issuer", "humanSubject", "serviceActor")


def collect_user_projection(user_ids: set[str], usernames: set[str]) -> list[dict]:
    """The User projection rows for the editors an observation names.

    persist_user writes, for every verified token, the platform identity the
    Command Center carried: the issuer-qualified subject (oid), subject type,
    issuer, human subject and service actor. This reads those rows back exactly,
    by author id or by username, so the semantic layer can attribute a native
    edit to a platform subject without guessing from a display name.
    """
    if not user_ids and not usernames:
        return []
    from neomodel import db
    rows, columns = db.cypher_query("""
        MATCH (u:User) WHERE u.user_id IN $ids OR u.username IN $names
        RETURN u.user_id AS userId, u.username AS username, u.oid AS oid, u.subject_type AS subjectType,
               u.issuer AS issuer, u.human_subject AS humanSubject, u.service_actor AS serviceActor
        ORDER BY u.user_id
    """, {"ids": sorted(user_ids), "names": sorted(usernames)})
    return [dict(zip(columns, values)) for values in rows]


def _verified_user_projection(rows: list) -> list[dict]:
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("userId"), str) or not row["userId"] or row["userId"] in seen:
            raise NativeObservationError("NATIVE_OBSERVATION_USER_PROJECTION_INVALID")
        entry = {"userId": row["userId"]}
        for field in USER_PROJECTION_FIELDS:
            value = row.get(field)
            if value is not None and not isinstance(value, str):
                raise NativeObservationError("NATIVE_OBSERVATION_USER_PROJECTION_INVALID")
            entry[field] = value if value else None
        seen.add(entry["userId"])
        result.append(entry)
    return sorted(result, key=lambda entry: entry["userId"])


def collect_native_observation(study_uid: str, *, readers: dict[str, tuple[Callable, Callable | None]] | None = None, native_study: dict | None = None, study_audit: list | None = None, raw_actions: list | None = None, raw_history_readers: dict[str, Callable] | None = None, user_projection: list | None = None) -> dict:
    if not isinstance(study_uid, str) or not study_uid.strip():
        raise NativeObservationError("NATIVE_OBSERVATION_STUDY_REQUIRED")
    if readers is not None and set(readers) - {row.collection for row in COLLECTIONS}:
        raise NativeObservationError("NATIVE_OBSERVATION_UNKNOWN_COLLECTION")
    if native_study is None:
        from clinical_mdr_api.services.studies.study import StudyService
        from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import StudyComponentEnum
        study_service = StudyService()
        native_study = _json(study_service.get_by_uid(uid=study_uid, include_sections=list(StudyComponentEnum)))
        study_audit = [_json(row) for row in (study_service.get_fields_audit_trail_by_uid(uid=study_uid) or [])]
    if native_study.get("uid") != study_uid:
        raise NativeObservationError("NATIVE_OBSERVATION_STUDY_SCOPE_MISMATCH")
    records, history, coverage = [], [], []

    def add_record(collection, resource_type, uid, record):
        compared, ignored = comparison_record(record, collection)
        records.append({"collection": collection, "resourceType": resource_type, "nativeUid": uid,
                        "record": _json(record), "comparisonHash": canonical_hash(compared), "comparisonExcludedPaths": ignored})

    add_record("native_study", "StudyMetadata", study_uid, native_study)
    for row in study_audit or []:
        if row.get("study_uid") not in (None, study_uid):
            raise NativeObservationError("NATIVE_OBSERVATION_AUDIT_SCOPE_MISMATCH")
        history.append({"collection": "native_study", "nativeUid": study_uid, "record": _json(row)})
    coverage.append({"collection": "native_study", "complete": True, "recordCount": 1, "auditStatus": "available", "auditCount": len(study_audit or [])})
    for spec in COLLECTIONS:
        if readers is not None and spec.collection not in readers:
            continue
        raw_history_reader = (raw_history_readers or {}).get(spec.collection)
        if readers is None:
            factory = getattr(import_module("clinical_mdr_api.services.studies." + spec.module), spec.service)
            service = factory(study_uid=study_uid) if spec.collection in ("study_visits", "study_epochs") else factory()
            current_reader = getattr(service, spec.current)
            # Group/subgroup service history renderers are unimplemented. The
            # study-scoped repository history is complete and retains author_id.
            audit_reader = service.repository.find_selection_history if spec.collection in ("study_activity_groups", "study_activity_subgroups") else getattr(service, spec.audit) if spec.audit else None
            if spec.collection == "study_endpoints":
                raw_history_reader = service._repos.study_endpoint_repository.find_selection_history
        else:
            current_reader, audit_reader = readers[spec.collection]
        options = {"study_uid": study_uid}
        if spec.paginated:
            options["page_size"] = 0
        if spec.no_brackets:
            options["no_brackets"] = False
        current = [_json(row) for row in _rows(current_reader(**options), spec.collection)]
        seen = set()
        for row in current:
            if ("study_uid" in row and row["study_uid"] != study_uid) or ("study_uid" not in row and not spec.query_scope_only):
                raise NativeObservationError("NATIVE_OBSERVATION_RECORD_SCOPE_MISMATCH:" + spec.collection)
            uid = row.get(spec.uid_field)
            if not isinstance(uid, str) or not uid or uid in seen:
                raise NativeObservationError("NATIVE_OBSERVATION_RECORD_IDENTITY_AMBIGUOUS:" + spec.collection)
            seen.add(uid)
            add_record(spec.collection, spec.resource_type, uid, row)
        audit_options = {"study_uid": study_uid}
        if spec.collection == "study_criteria":
            audit_options["criteria_type_uid"] = None
        audit_projection_error = None
        try:
            audits = [_json(row) for row in _rows(audit_reader(**audit_options), spec.collection + ".audit")] if audit_reader else []
        except NotFoundException as error:
            # A genuinely absent historical lookup must not erase owned audit
            # rows. Only this known lookup failure has a scoped raw-history
            # alternative; transport, database and scope failures still abort.
            if raw_history_reader is None:
                raise
            audits = [_json(row) for row in _rows(raw_history_reader(study_uid=study_uid), spec.collection + ".raw-audit")]
            if not audits:
                raise NativeObservationError("NATIVE_OBSERVATION_RAW_AUDIT_MISSING:" + spec.collection) from error
            audit_projection_error = {"status": "unresolved", "reason": str(error),
                                      "rawBasis": "exact study-scoped repository selection history",
                                      "rawRecordCount": len(audits)}
        for row in audits:
            if row.get("study_uid") not in (None, study_uid):
                raise NativeObservationError("NATIVE_OBSERVATION_AUDIT_SCOPE_MISMATCH:" + spec.collection)
            uid = row.get(spec.uid_field)
            if uid is None and spec.collection == "study_design_cells":
                uid = row.get("study_design_cell_uid")
            if uid is None and (audit_projection_error or spec.collection in ("study_activity_groups", "study_activity_subgroups")):
                uid = row.get("study_selection_uid")
            if not isinstance(uid, str) or not uid:
                raise NativeObservationError("NATIVE_OBSERVATION_AUDIT_IDENTITY_MISSING:" + spec.collection)
            history.append({"collection": spec.collection, "nativeUid": uid, "record": row,
                            **({"projectionStatus": "unresolved", "recordBasis": "raw-native-history"} if audit_projection_error else {})})
        coverage.append({"collection": spec.collection, "complete": True, "recordCount": len(current),
                         "auditStatus": "raw-retained" if audit_projection_error else "available" if audit_reader else "unavailable", "auditCount": len(audits),
                         **({"auditProjection": audit_projection_error} if audit_projection_error else {}),
                         **({"auditReason": "No native audit reader is exposed for this collection"} if not audit_reader else {})})
    if readers is None and raw_actions is None:
        raw_actions = collect_study_actions(study_uid)
    if raw_actions is not None:
        seen_actions = set()
        for row in raw_actions:
            if row.get("study_uid") != study_uid or row.get("owners") != [study_uid]:
                raise NativeObservationError("NATIVE_OBSERVATION_ACTION_SCOPE_AMBIGUOUS")
            action_id = row.get("actionId")
            if not isinstance(action_id, str) or not action_id or action_id in seen_actions:
                raise NativeObservationError("NATIVE_OBSERVATION_ACTION_IDENTITY_AMBIGUOUS")
            seen_actions.add(action_id)
            history.append({"collection": "native_study_actions", "nativeUid": action_id, "record": _json(row)})
        coverage.append({"collection": "native_study_actions", "complete": True, "recordCount": 0,
                         "auditStatus": "available", "auditCount": len(raw_actions),
                         "scope": "StudyRoot AUDIT_TRAIL actions and directly linked BEFORE/AFTER values"})
    editor_ids, editor_names = set(), set()
    for entry in history:
        record = entry.get("record") if isinstance(entry.get("record"), dict) else {}
        for field in ("author_id", "user_id"):
            if isinstance(record.get(field), str) and record[field]:
                editor_ids.add(record[field])
        for field in ("author_username", "version_author"):
            if isinstance(record.get(field), str) and record[field]:
                editor_names.add(record[field])
    if readers is None and user_projection is None:
        user_projection = collect_user_projection(editor_ids, editor_names)
    users = _verified_user_projection(user_projection) if user_projection is not None else None
    records.sort(key=lambda row: (row["collection"], row["nativeUid"]))
    # Generated read labels must not reshuffle otherwise identical audit rows
    # between observations. The original label remains in each retained row.
    history.sort(key=lambda row: (row["collection"], row["nativeUid"], canonical_hash(comparison_record(row["record"], row["collection"])[0])))
    content = {"schemaVersion": "osb-native-observation/1.0", "nativeStudyId": study_uid, "capturedAt": datetime.now(timezone.utc).isoformat(),
               "comparisonProfile": "osb-native-read/1.1", "records": records, "auditRecords": history,
               **({"users": users} if users is not None else {}),
               "coverage": {"collections": coverage, "releaseAuthority": False,
                            "excludedSurfaces": ["USDM projection", "unlinked global library records", "clinical participant data",
                                "ODM and library definition edit histories outside StudyRoot AUDIT_TRAIL; retained source export remains a separate surface"]}}
    return {**content, "contentHash": canonical_hash(content)}
