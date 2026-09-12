"""Retain complete native study selections independently of CRF/calendar holds."""
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
import inspect
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
    singleton: bool = False


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
    Collection("studySoAGroup", "study_soa_group_uid", "study_soa_group", "StudySoAGroupService"),
    Collection("studyActivityInstance", "study_activity_instance_uid", "study_activity_instance_selection", "StudyActivityInstanceSelectionService"),
    Collection("studyActivityInstruction", "study_activity_instruction_uid", "study_activity_instruction", "StudyActivityInstructionService", "get_all_instructions", paginated=False),
    Collection("studySoAFootnote", "uid", "study_soa_footnote", "StudySoAFootnoteService", "get_all_by_study_uid"),
    Collection("studyDiseaseMilestone", "uid", "study_disease_milestone", "StudyDiseaseMilestoneService", "get_all_disease_milestones"),
    Collection("studyDataSupplier", "study_data_supplier_uid", "study_data_supplier", "StudyDataSupplierSelectionService", "get_all_selections"),
    Collection("studyDesignClass", "study_uid", "study_design_class", "StudyDesignClassService", "get_existing_study_design_class", paginated=False, singleton=True),
    Collection("studySourceVariable", "study_uid", "study_source_variable", "StudySourceVariableService", "get_study_source_variable", paginated=False, singleton=True),
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
    total = result.get("total") if isinstance(result, dict) else getattr(result, "total", None)
    if isinstance(total, int) and total > len(items):
        raise NativeStudyRecordError("Native study collection returned a truncated item list")
    return items


def collect_study_native_records(
    study_uid: str,
    *,
    study_value_version: str | None = None,
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
    if study_value_version is not None and (
        not isinstance(study_value_version, str) or not study_value_version.strip()
    ):
        raise NativeStudyRecordError("An explicit native study version must be a nonempty string")
    known = {collection.kind for collection in COLLECTIONS}
    if readers is not None and set(readers) - known:
        raise NativeStudyRecordError("Unknown native study collection reader")
    records: list[dict[str, Any]] = []
    census: list[dict[str, Any]] = []
    identities: dict[tuple[str, str, str | None], set[str]] = {}
    operational_scopes: dict[str, tuple[Any, Any]] = {}
    for collection in COLLECTIONS:
        if readers is not None and collection.kind not in readers:
            continue
        read = readers[collection.kind] if readers is not None else _reader(collection)
        kwargs: dict[str, Any] = {"study_uid": study_uid}
        if study_value_version is not None:
            parameters = inspect.signature(read).parameters
            if "study_value_version" not in parameters and not any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            ):
                raise NativeStudyRecordError(
                    f"Native collection reader cannot honor the selected study version: {collection.kind}"
                )
            kwargs["study_value_version"] = study_value_version
        if collection.paginated:
            kwargs["page_size"] = 0
        if collection.no_brackets:
            kwargs["no_brackets"] = False
        if collection.operational is not None:
            kwargs["operational"] = collection.operational
        result = read(**kwargs)
        selected = (
            ([result] if result is not None else [])
            if collection.singleton else _rows(result)
        )
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
            if (
                study_value_version is not None
                and record.get("study_version") is not None
                and record["study_version"] != study_value_version
            ):
                raise NativeStudyRecordError(f"Native study version mismatch: {collection.kind}")
            instance_uid = None
            scope_conflict = False
            if collection.kind == "studyOperationalActivitySchedule":
                instance_uid = record.get("study_activity_instance_uid")
                if instance_uid is not None and (
                    not isinstance(instance_uid, str) or not instance_uid.strip()
                ):
                    raise NativeStudyRecordError("Native operational schedule instance identity is invalid")
                selected_scope = (record.get("study_activity_uid"), record.get("study_visit_uid"))
                previous_scope = operational_scopes.setdefault(uid, selected_scope)
                scope_conflict = previous_scope != selected_scope
            # Operational reads expand the same native schedule UID by exact
            # selected instance. That qualifier distinguishes valid rows, not
            # contradictory readings of the same schedule/instance identity.
            key = (collection.kind, uid, instance_uid)
            reading = json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
            previous = identities.setdefault(key, set())
            if reading in previous:
                continue
            if previous or scope_conflict:
                census.append({"kind": "ambiguous_native_reading", "ref": f"{collection.kind}/{uid}",
                               "detail": "Distinct readings of one native UID retained in full; no reading was selected by position"})
            previous.add(reading)
            entry = {"kind": collection.kind, "uid": uid, "record": record}
            if collection.query_scoped_only or study_value_version is not None:
                entry["scope"] = {"studyUid": study_uid, "source": collection.service + "." + collection.method}
                if study_value_version is not None:
                    entry["scope"]["studyValueVersion"] = study_value_version
                if instance_uid is not None:
                    entry["scope"]["studyActivityInstanceUid"] = instance_uid
            records.append(entry)
            retained += 1
        census.append({"kind": "native_study_collection", "ref": collection.kind,
                       "studyUid": study_uid, "readCount": len(selected), "retainedReadings": retained,
                       "detail": "Complete scoped native metadata; no clinical mapping or release credit"})
    return records, census
