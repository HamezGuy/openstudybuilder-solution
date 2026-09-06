"""Retain complete native study selections independently of CRF/calendar holds."""
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
import json
from typing import Any, Callable, Mapping


class NativeStudyRecordError(ValueError):
    """A native study collection cannot be retained within the requested scope."""


@dataclass(frozen=True)
class Collection:
    kind: str
    identity: str
    module: str
    service: str
    method: str = "get_all_selection"
    paginated: bool = True
    no_brackets: bool = False
    query_scoped_only: bool = False
    operational: bool | None = None


COLLECTIONS = (
    Collection("studyEpoch", "uid", "study_epoch", "StudyEpochService", "get_all_epochs"),
    Collection("studyObjective", "study_objective_uid", "study_objective_selection", "StudyObjectiveSelectionService", no_brackets=True),
    Collection("studyEndpoint", "study_endpoint_uid", "study_endpoint_selection", "StudyEndpointSelectionService", no_brackets=True),
    Collection("studyCriteria", "study_criteria_uid", "study_criteria_selection", "StudyCriteriaSelectionService", no_brackets=True),
    Collection("studyActivity", "study_activity_uid", "study_activity_selection", "StudyActivitySelectionService"),
    Collection("studyElement", "element_uid", "study_element_selection", "StudyElementSelectionService"),
    Collection("studyDesignCell", "design_cell_uid", "study_design_cell", "StudyDesignCellService", "get_all_design_cells", paginated=False),
    Collection("studyCohort", "cohort_uid", "study_cohort_selection", "StudyCohortSelectionService"),
    Collection("studyBranchArm", "branch_arm_uid", "study_branch_arm_selection", "StudyBranchArmSelectionService"),
    Collection("studyActivityGroup", "study_activity_group_uid", "study_activity_group", "StudyActivityGroupService"),
    Collection("studyActivitySubGroup", "study_activity_subgroup_uid", "study_activity_subgroup", "StudyActivitySubGroupService"),
    Collection("studyActivityInstance", "study_activity_instance_uid", "study_activity_instance_selection", "StudyActivityInstanceSelectionService"),
    Collection("studyActivityInstruction", "study_activity_instruction_uid", "study_activity_instruction", "StudyActivityInstructionService", "get_all_instructions", paginated=False),
    # This response DTO omits study_uid; its service performs an explicit
    # study-scoped repository query. Preserve that scope beside the raw record.
    Collection("studyActivitySchedule", "study_activity_schedule_uid", "study_activity_schedule", "StudyActivityScheduleService", "get_all_schedules", paginated=False, query_scoped_only=True, operational=False),
    Collection("studyOperationalActivitySchedule", "study_activity_schedule_uid", "study_activity_schedule", "StudyActivityScheduleService", "get_all_schedules", paginated=False, query_scoped_only=True, operational=True),
)


def _reader(collection: Collection) -> Callable[..., Any]:
    module = import_module("clinical_mdr_api.services.studies." + collection.module)
    service = getattr(module, collection.service)()
    return getattr(service, collection.method)


def _rows(result: Any) -> list[Any]:
    if isinstance(result, list):
        return result
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", None)
    if not isinstance(items, list):
        raise NativeStudyRecordError("Native study collection did not return a complete item list")
    return items


def collect_study_native_records(
    study_uid: str,
    *,
    readers: Mapping[str, Callable[..., Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return raw scoped records and an explicit collection/ambiguity census.

    Production readers use only exact study-scoped services, with no pagination
    cutoff or label joins. Injected readers select a subset for focused tests.
    Unknown record keys, nested definitions, false/null/empty values and
    distinct readings of the same native identity are preserved unchanged.
    No approval, mapping decision or clinical projection is performed here.
    """
    if not isinstance(study_uid, str) or not study_uid.strip():
        raise NativeStudyRecordError("An explicit native study UID is required")
    known = {collection.kind for collection in COLLECTIONS}
    if readers is not None and set(readers) - known:
        raise NativeStudyRecordError("Unknown native study collection reader")
    records: list[dict[str, Any]] = []
    census: list[dict[str, Any]] = []
    identities: dict[tuple[str, str], set[str]] = {}
    for collection in COLLECTIONS:
        if readers is not None and collection.kind not in readers:
            continue
        read = readers[collection.kind] if readers is not None else _reader(collection)
        kwargs: dict[str, Any] = {"study_uid": study_uid}
        if collection.paginated:
            kwargs["page_size"] = 0
        if collection.no_brackets:
            kwargs["no_brackets"] = False
        if collection.operational is not None:
            kwargs["operational"] = collection.operational
        selected = _rows(read(**kwargs))
        retained = 0
        for model in selected:
            # Use the model's API JSON representation (including datetime
            # formatting), so retaining a selection does not change its wire
            # metadata merely because it is nested in an export dictionary.
            record = deepcopy(model.model_dump(mode="json") if hasattr(model, "model_dump") else model)
            if not isinstance(record, dict):
                raise NativeStudyRecordError("Native study record must be an object")
            if ("study_uid" in record and record["study_uid"] != study_uid) or (
                "study_uid" not in record and not collection.query_scoped_only
            ):
                raise NativeStudyRecordError(f"Native study scope mismatch: {collection.kind}")
            uid = record.get(collection.identity)
            if not isinstance(uid, str) or not uid:
                raise NativeStudyRecordError(f"Native selection identity is missing: {collection.kind}")
            key = (collection.kind, uid)
            reading = json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
            previous = identities.setdefault(key, set())
            if reading in previous:
                continue
            if previous:
                census.append({"kind": "ambiguous_native_reading", "ref": f"{collection.kind}/{uid}",
                               "detail": "Distinct readings of one native UID retained in full; no reading was selected by position"})
            previous.add(reading)
            entry = {"kind": collection.kind, "uid": uid, "record": record}
            if collection.query_scoped_only:
                entry["scope"] = {"studyUid": study_uid, "source": collection.service + "." + collection.method}
            records.append(entry)
            retained += 1
        census.append({"kind": "native_study_collection", "ref": collection.kind,
                       "studyUid": study_uid, "readCount": len(selected), "retainedReadings": retained,
                       "detail": "Complete scoped native metadata; no clinical mapping or release credit"})
    return records, census
