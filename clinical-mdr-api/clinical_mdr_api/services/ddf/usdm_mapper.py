"""Deterministic, version-consistent OSB to CDISC USDM v4 mapping.

Native OpenStudyBuilder data is the editable authority. This mapper projects one
explicit OSB study version and never restores values from an Intelligence Layer
or EDC carrier.
"""

import inspect
import re
from datetime import date, datetime, timezone
from itertools import chain
from typing import Any, Callable

from neomodel import db
from usdm_info import __model_version__ as usdm_package_version
from usdm_model import Activity as USDMActivity
from usdm_model import AliasCode as USDMAliasCode
from usdm_model import Code as USDMCode
from usdm_model import Encounter as USDMEncounter
from usdm_model import Endpoint as USDMEndpoint
from usdm_model import Indication as USDMIndication
from usdm_model import Objective as USDMObjective
from usdm_model import Organization as USDMOrganization
from usdm_model import Quantity as USDMQuantity
from usdm_model import Range as USDMRange
from usdm_model import ScheduledActivityInstance
from usdm_model import ScheduleTimeline as USDMScheduleTimeline
from usdm_model import Study as USDMStudy
from usdm_model import StudyArm
from usdm_model import StudyCell as USDMStudyCell
from usdm_model import StudyDefinitionDocument as USDMStudyDefinitionDocument
from usdm_model import (
    StudyDefinitionDocumentVersion as USDMStudyDefinitionDocumentVersion,
)
from usdm_model import StudyDesign as USDMStudyDesign
from usdm_model import StudyDesignPopulation as USDMStudyDesignPopulation
from usdm_model import StudyElement as USDMStudyElement
from usdm_model import StudyEpoch as USDMStudyEpoch
from usdm_model import StudyIdentifier as USDMStudyIdentifier
from usdm_model import StudyIntervention as USDMStudyIntervention
from usdm_model import StudyTitle as USDMStudyTitle
from usdm_model import StudyVersion as USDMStudyVersion
from usdm_model import Timing as USDMTiming
from usdm_model import TransitionRule as USDMTransitionRule

from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyStatus,
)
from clinical_mdr_api.models.study_selections.study import Study as OSBStudy
from clinical_mdr_api.services.ddf.usdm_utils import IdManager
from common.telemetry import trace_calls

DDF_ORGANIZATION_TYPE_STUDY_REGISTRY = "C93453"
DDF_ORGANIZATION_TYPE_REGULATORY_AGENCY = "C188863"
DDF_STUDY_ARM_DATA_ORIGIN_TYPE_GENERATED_WITHIN_STUDY = "C188866"
DDF_STUDY_POPULATION_DURATION_UNIT_DAYS = "C25301"
DDF_STUDY_POPULATION_DURATION_UNIT_WEEKS = "C29844"
DDF_STUDY_POPULATION_DURATION_UNIT_MONTHS = "C29846"
DDF_STUDY_POPULATION_DURATION_UNIT_YEARS = "C29848"
DDF_STUDY_POPULATION_ENROLLMENT_NUMBER_UNIT = "C44278"
DDF_STUDY_PROTOCOL_STATUS_DRAFT = "C85255"
DDF_STUDY_PROTOCOL_STATUS_FINAL = "C25508"
DDF_STUDY_POPULATION_SEX_BOTH = "C49636"
DDF_STUDY_POPULATION_SEX_FEMALE = "C16576"
DDF_STUDY_POPULATION_SEX_MALE = "C20197"
DDF_STUDY_OFFICIAL_TITLE = "C207616"
DDF_TIMING_TYPE_AFTER = "C201356"
DDF_TIMING_TYPE_BEFORE = "C201357"
DDF_TIMING_TYPE_FIXED = "C201358"
DDF_TIME_RELATIVE_TO_FROM_START_TO_START = "C201355"


def get_ddf_timing_iso_duration_value(time_value: int, time_unit_name: str) -> str:
    unit = time_unit_name.strip().lower()
    magnitude = abs(time_value)
    if unit in {"week", "weeks"}:
        return f"P{magnitude}W"
    if unit in {"day", "days"}:
        return f"P{magnitude}D"
    if unit in {"hour", "hours"}:
        return f"PT{magnitude}H"
    raise ValueError(f"Unsupported time unit {time_unit_name}")


def extract_c_code_from_simple_term(term_uid: str | None) -> str | None:
    if not term_uid:
        return None
    match = re.search(r"(^C\d+)_?", term_uid)
    return match.group(1) if match else None


def _stable_selection_order(items: list[Any], uid_attribute: str) -> list[Any]:
    """Keep explicit native order and make unordered ties deterministic."""
    return sorted(
        items,
        key=lambda item: (
            getattr(item, "order", None) is None,
            getattr(item, "order", 0) or 0,
            str(getattr(item, uid_attribute, "") or ""),
        ),
    )


def _items(value: Any) -> list[Any]:
    return list(getattr(value, "items", value or []))


def _accepts_keyword(callback: Callable, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or parameter.name == keyword
        for parameter in parameters
    )


def _update_ddf_encounter_scheduled_at(encounters, schedule_timelines) -> None:
    timings = list(chain.from_iterable(timeline.timings for timeline in schedule_timelines))
    timing_by_instance = {
        timing.relativeFromScheduledInstanceId: timing.id
        for timing in timings
        if timing.relativeFromScheduledInstanceId is not None
    }
    scheduled_at_by_encounter = {
        instance.encounterId: timing_by_instance.get(instance.id)
        for timeline in schedule_timelines
        for instance in timeline.instances
    }
    for encounter in encounters:
        encounter.scheduledAtId = scheduled_at_by_encounter.get(encounter.id)


class USDMMapper:
    @trace_calls
    def __init__(
        self,
        get_osb_study_design_cells: Callable,
        get_osb_study_arms: Callable,
        get_osb_study_epochs: Callable,
        get_osb_study_elements: Callable,
        get_osb_study_endpoints: Callable,
        get_osb_study_visits: Callable,
        get_osb_study_activities: Callable,
        get_osb_activity_schedules: Callable,
        get_osb_study_objectives: Callable | None = None,
        get_osb_study_standard_versions: Callable | None = None,
    ):
        self._get_osb_study_design_cells = get_osb_study_design_cells
        self._get_osb_study_arms = get_osb_study_arms
        self._get_osb_study_epochs = get_osb_study_epochs
        self._get_osb_study_elements = get_osb_study_elements
        self._get_osb_study_endpoints = get_osb_study_endpoints
        self._get_osb_study_visits = get_osb_study_visits
        self._get_osb_study_activities = get_osb_study_activities
        self._get_osb_activity_schedules = get_osb_activity_schedules
        self._get_osb_study_objectives = get_osb_study_objectives
        self._get_osb_study_standard_versions = get_osb_study_standard_versions
        self._id_manager = IdManager()
        self._ct_packages: dict[str, dict[str, str]] = {}
        self._registid_labels: dict[str, str] = {}
        self._study_value_version: str | None = None

    def _call(self, callback: Callable, *args, **kwargs):
        if self._study_value_version is not None and _accepts_keyword(
            callback, "study_value_version"
        ):
            kwargs["study_value_version"] = self._study_value_version
        return callback(*args, **kwargs)

    @staticmethod
    def _effective_date_to_str(effective_date: Any) -> str:
        return str(effective_date)[:10]

    @staticmethod
    def _effective_date_to_datetime(effective_date_str: str) -> datetime | None:
        if effective_date_str == "UNPINNED":
            return None
        parsed = date.fromisoformat(effective_date_str)
        return datetime(
            parsed.year,
            parsed.month,
            parsed.day,
            23,
            59,
            59,
            999999,
            tzinfo=timezone.utc,
        )

    def _load_selected_ct_packages(self, study_uid: str) -> None:
        """Cache only the packages explicitly selected on this study version."""
        self._ct_packages = {}
        if self._get_osb_study_standard_versions is None:
            return
        selected_versions = self._call(
            self._get_osb_study_standard_versions, study_uid=study_uid
        )
        for row in selected_versions:
            package = getattr(row, "ct_package", None)
            catalogue_name = getattr(package, "catalogue_name", None)
            package_uid = getattr(package, "uid", None)
            effective_date = getattr(package, "effective_date", None)
            if not catalogue_name or not package_uid or effective_date is None:
                continue
            candidate = {
                "uid": str(package_uid),
                "effective_date": self._effective_date_to_str(effective_date),
            }
            existing = self._ct_packages.get(catalogue_name)
            if existing is None or candidate["effective_date"] > existing["effective_date"]:
                self._ct_packages[catalogue_name] = candidate

    def _resolve_ct_package_effective_date(self, study_uid: str) -> str:
        self._load_selected_ct_packages(study_uid)
        for catalogue_name in ("DDF CT", "SDTM CT"):
            package = self._ct_packages.get(catalogue_name)
            if package is not None:
                return package["effective_date"]
        return "UNPINNED"

    @staticmethod
    def _load_registid_labels() -> dict[str, str]:
        query = """
            MATCH (codelist:CTCodelistRoot {uid: 'CTCodelist_000038'})
                  -[:HAS_TERM]->(:CTCodelistTerm)-[:HAS_TERM_ROOT]->(term:CTTermRoot)
            MATCH (term)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)
                  -[:LATEST]->(name:CTTermNameValue)
            RETURN term.uid AS term_uid, name.name AS term_name
        """
        result, _ = db.cypher_query(query)
        return {row[0]: row[1] for row in result}

    def get_void_usdm_code(self) -> USDMCode:
        return USDMCode(
            id=self._id_manager.get_id(USDMCode.__name__),
            code="", codeSystem="", codeSystemVersion="", decode="",
            instanceType="Code",
        )

    @trace_calls(args=[1], kwargs=["concept_id"])
    def get_ct_package_term_as_usdm_code(self, concept_id: str | None) -> USDMCode:
        if concept_id is None:
            return self.get_void_usdm_code()
        packages = [
            self._ct_packages[catalogue]
            for catalogue in ("DDF CT", "SDTM CT", "CDASH CT")
            if catalogue in self._ct_packages
        ]
        if not packages:
            return self.get_void_usdm_code()
        query = """
            MATCH (package:CTPackage {uid: $package_uid})
                  -[:CONTAINS_CODELIST]->(:CTPackageCodelist)
                  -[:CONTAINS_TERM]->(:CTPackageTerm)
                  -[:CONTAINS_ATTRIBUTES]->(:CTTermAttributesValue)
                  <-[:HAS_VERSION]-(:CTTermAttributesRoot)
                  <-[:HAS_ATTRIBUTES_ROOT]-(root:CTTermRoot)
            MATCH (library:Library)-[:CONTAINS_TERM]->(root)
            WHERE root.uid = $concept_id OR root.uid STARTS WITH $concept_id + '_'
            MATCH (root)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)
                  -[version:HAS_VERSION]->(value:CTTermNameValue)
            WHERE version.status IN ['Final', 'Retired']
              AND version.start_date <= $package_datetime
              AND (version.end_date IS NULL OR version.end_date > $package_datetime)
            RETURN library, value
            ORDER BY version.start_date DESC, root.uid
            LIMIT 1
        """
        for package in packages:
            result, _ = db.cypher_query(query, {
                "concept_id": concept_id,
                "package_uid": package["uid"],
                "package_datetime": self._effective_date_to_datetime(
                    package["effective_date"]
                ),
            })
            if result:
                library, value = result[0]
                return USDMCode(
                    id=self._id_manager.get_id(USDMCode.__name__, concept_id),
                    code=concept_id, codeSystem=library["name"],
                    # The package UID is the exact governed artifact identity.
                    # Retired is accepted only when this immutable selected
                    # package contains the term and the version interval covers
                    # the package date; it is never proposed as a new candidate.
                    codeSystemVersion=package["uid"],
                    decode=value["name"], instanceType="Code",
                )
        return self.get_void_usdm_code()

    @trace_calls
    def get_dictionary_term_as_usdm_code(self, term_uid: str | None) -> USDMCode:
        if term_uid is None:
            return self.get_void_usdm_code()
        query = """
            MATCH (library:Library)-[:CONTAINS_DICTIONARY_TERM]
                  ->(root:DictionaryTermRoot)-[:LATEST]->(value)
            WHERE root.uid STARTS WITH $term_uid
            RETURN library, value
            ORDER BY root.uid
            LIMIT 1
        """
        result, _ = db.cypher_query(query, {"term_uid": term_uid})
        if not result:
            return self.get_void_usdm_code()
        library, value = result[0]
        return USDMCode(
            id=self._id_manager.get_id(USDMCode.__name__, term_uid),
            code=term_uid, codeSystem=library["name"],
            codeSystemVersion="DICTIONARY_LATEST_UNPINNED",
            decode=value["name"], instanceType="Code",
        )

    def get_ddf_study_population_duration_unit_from_name_as_code(
        self, time_unit_name: str
    ) -> USDMCode:
        concept_by_unit = {
            "day": DDF_STUDY_POPULATION_DURATION_UNIT_DAYS,
            "days": DDF_STUDY_POPULATION_DURATION_UNIT_DAYS,
            "week": DDF_STUDY_POPULATION_DURATION_UNIT_WEEKS,
            "weeks": DDF_STUDY_POPULATION_DURATION_UNIT_WEEKS,
            "month": DDF_STUDY_POPULATION_DURATION_UNIT_MONTHS,
            "months": DDF_STUDY_POPULATION_DURATION_UNIT_MONTHS,
            "year": DDF_STUDY_POPULATION_DURATION_UNIT_YEARS,
            "years": DDF_STUDY_POPULATION_DURATION_UNIT_YEARS,
        }
        return self.get_ct_package_term_as_usdm_code(
            concept_by_unit.get(time_unit_name.lower())
        )

    def get_ddf_study_population_enrollment_number_unit(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_STUDY_POPULATION_ENROLLMENT_NUMBER_UNIT)

    def get_ddf_study_protocol_status_draft(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_STUDY_PROTOCOL_STATUS_DRAFT)

    def get_ddf_study_protocol_status_final(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_STUDY_PROTOCOL_STATUS_FINAL)

    def get_ddf_study_population_sex_both(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_STUDY_POPULATION_SEX_BOTH)

    def get_ddf_study_population_sex_female(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_STUDY_POPULATION_SEX_FEMALE)

    def get_ddf_study_population_sex_male(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_STUDY_POPULATION_SEX_MALE)

    def get_ddf_timing_type_code_after(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_TIMING_TYPE_AFTER)

    def get_ddf_timing_type_code_before(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_TIMING_TYPE_BEFORE)

    def get_ddf_timing_type_code_fixed(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_TIMING_TYPE_FIXED)

    def get_ddf_timing_relative_to_from(self) -> USDMCode:
        return self.get_ct_package_term_as_usdm_code(DDF_TIME_RELATIVE_TO_FROM_START_TO_START)

    @trace_calls
    def map(self, study: OSBStudy, study_value_version: str | None = None) -> dict[str, Any]:
        self._study_value_version = study_value_version
        self._id_manager.clear_all_ids()
        self._resolve_ct_package_effective_date(study.uid)
        self._registid_labels = self._load_registid_labels()

        usdm_study = USDMStudy(
            id=self._id_manager.get_id(USDMStudy.__name__, study.uid),
            name=self._get_study_name(study), label=self._get_study_label(study),
            description=self._get_study_description(study), instanceType="Study",
        )
        document = self._get_study_definition_document(study)
        usdm_study.documentedBy = [document]
        title = USDMStudyTitle(
            id=self._id_manager.get_id(USDMStudyTitle.__name__),
            text=self._get_study_title(study),
            type=self.get_ct_package_term_as_usdm_code(DDF_STUDY_OFFICIAL_TITLE),
            instanceType="StudyTitle",
        )
        identifiers, organizations = self._get_study_identifiers_and_organizations(study)
        version_metadata = getattr(study.current_metadata, "version_metadata", None)
        version = USDMStudyVersion(
            id=self._id_manager.get_id(USDMStudyVersion.__name__), titles=[title],
            studyIdentifiers=identifiers, organizations=organizations,
            versionIdentifier=self._get_study_version(study),
            rationale=getattr(version_metadata, "version_description", None) or "",
            instanceType="StudyVersion", documentVersionIds=[document.id],
        )
        version.studyInterventions = self._get_study_interventions(study)
        version.studyDesigns = self._get_study_designs(study)
        if version.studyDesigns:
            version.studyDesigns[0].studyInterventionIds = [
                intervention.id for intervention in version.studyInterventions
            ]
        usdm_study.versions = [version]
        return {"study": usdm_study, "usdmVersion": usdm_package_version,
                "systemName": None, "systemVersion": None}

    def _get_study_arms(self, study: OSBStudy) -> list[StudyArm]:
        rows = _stable_selection_order(_items(self._call(self._get_osb_study_arms, study.uid)), "arm_uid")
        return [StudyArm(
            id=self._id_manager.get_id(StudyArm.__name__, row.arm_uid),
            name=row.name, label=row.name, description=row.description,
            type=(self.get_ct_package_term_as_usdm_code(row.arm_type.term_uid)
                  if row.arm_type else self.get_void_usdm_code()),
            dataOriginDescription="",
            dataOriginType=self.get_ct_package_term_as_usdm_code(
                DDF_STUDY_ARM_DATA_ORIGIN_TYPE_GENERATED_WITHIN_STUDY),
        ) for row in rows]

    def _get_study_cells(self, study: OSBStudy) -> list[USDMStudyCell]:
        rows = _stable_selection_order(
            _items(self._call(self._get_osb_study_design_cells, study.uid)), "design_cell_uid")
        return [USDMStudyCell(
            id=self._id_manager.get_id(USDMStudyCell.__name__, row.design_cell_uid),
            armId=self._id_manager.get_id(StudyArm.__name__, row.study_arm_uid),
            epochId=self._id_manager.get_id(USDMStudyEpoch.__name__, row.study_epoch_uid),
            elementIds=[self._id_manager.get_id(USDMStudyElement.__name__, row.study_element_uid)],
        ) for row in rows if row.study_arm_uid is not None
          and row.study_epoch_uid is not None and row.study_element_uid is not None]

    def _get_study_designs(self, study: OSBStudy) -> list[USDMStudyDesign]:
        design_name = self._get_study_name(study)
        design = USDMStudyDesign(
            id=self._id_manager.get_id(USDMStudyDesign.__name__),
            name=f"{design_name} Study Design" if design_name else "Study Design",
            description=self._get_study_description(study) or "", rationale="",
            arms=self._get_study_arms(study), studyCells=self._get_study_cells(study),
            epochs=self._get_study_epochs(study), elements=self._get_study_elements(study),
            population=self._get_study_population(study), instanceType="StudyDesign",
        )
        design.studyType = self._get_study_type(study)
        design.studyPhase = self._get_study_phase(study)
        design.therapeuticAreas = self._get_therapeutic_areas(study)
        design.indications = self._get_study_indications(study)
        design.objectives = self._get_study_objectives(study)
        design.encounters = self._get_study_encounters(study)
        design.activities = self._get_study_activities(study)
        design.scheduleTimelines = self._get_study_schedule_timelines(study)
        _update_ddf_encounter_scheduled_at(design.encounters, design.scheduleTimelines)
        return [design]

    def _get_study_activities(self, study: OSBStudy) -> list[USDMActivity]:
        rows = _stable_selection_order(
            _items(self._call(self._get_osb_study_activities, study.uid)), "study_activity_uid")
        activities = []
        for row in rows:
            native = getattr(row, "activity", None)
            subgroup = getattr(row, "study_activity_subgroup", None)
            activities.append(USDMActivity(
                id=self._id_manager.get_id(USDMActivity.__name__, row.study_activity_uid),
                name=(getattr(native, "name", None)
                      or getattr(subgroup, "activity_subgroup_name", None)
                      or f"Unresolved activity {row.study_activity_uid}"),
                label=getattr(native, "name_sentence_case", None),
                description=getattr(native, "definition", None),
                definedProcedures=[],
                instanceType="Activity",
            ))
        return activities

    def _get_study_elements(self, study: OSBStudy) -> list[USDMStudyElement]:
        rows = _stable_selection_order(
            _items(self._call(self._get_osb_study_elements, study.uid)), "element_uid")
        return [USDMStudyElement(
            id=self._id_manager.get_id(USDMStudyElement.__name__, row.element_uid),
            name=self._id_manager.get_id(USDMStudyElement.__name__, row.element_uid),
            description=row.description, label=row.name, instanceType="StudyElement",
        ) for row in rows]

    def _get_study_epochs(self, study: OSBStudy) -> list[USDMStudyEpoch]:
        rows = _stable_selection_order(_items(self._call(self._get_osb_study_epochs, study.uid)), "uid")
        all_ordered = bool(rows) and all(getattr(row, "order", None) is not None for row in rows)
        return [USDMStudyEpoch(
            id=self._id_manager.get_id(USDMStudyEpoch.__name__, row.uid),
            name=row.epoch_name if row.epoch_name is not None else " ",
            label=row.epoch_name, description=row.description,
            type=(self.get_ct_package_term_as_usdm_code(row.epoch_type_ctterm.term_uid)
                  if row.epoch_type_ctterm is not None else self.get_void_usdm_code()),
            nextId=(self._id_manager.get_id(USDMStudyEpoch.__name__, rows[index + 1].uid)
                    if all_ordered and index + 1 < len(rows) else None),
            previousId=(self._id_manager.get_id(USDMStudyEpoch.__name__, rows[index - 1].uid)
                        if all_ordered and index > 0 else None),
        ) for index, row in enumerate(rows)]

    def _get_study_objectives(self, study: OSBStudy) -> list[USDMObjective]:
        endpoint_kwargs = {"no_brackets": True} if _accepts_keyword(
            self._get_osb_study_endpoints, "no_brackets") else {}
        endpoints = _stable_selection_order(_items(self._call(
            self._get_osb_study_endpoints, study.uid, **endpoint_kwargs)), "study_endpoint_uid")
        if self._get_osb_study_objectives is None:
            objectives, seen = [], set()
            for endpoint in endpoints:
                selection = getattr(endpoint, "study_objective", None)
                uid = getattr(selection, "study_objective_uid", None)
                if uid and uid not in seen:
                    seen.add(uid)
                    objectives.append(selection)
        else:
            objective_kwargs = {"no_brackets": True} if _accepts_keyword(
                self._get_osb_study_objectives, "no_brackets") else {}
            objectives = _stable_selection_order(_items(self._call(
                self._get_osb_study_objectives, study.uid, **objective_kwargs)), "study_objective_uid")
        endpoints_by_objective: dict[str, list[USDMEndpoint]] = {}
        for selection in endpoints:
            objective_selection = getattr(selection, "study_objective", None)
            endpoint = getattr(selection, "endpoint", None)
            selection_uid = getattr(objective_selection, "study_objective_uid", None)
            if not selection_uid or endpoint is None:
                continue
            endpoint_id = self._id_manager.get_id(USDMEndpoint.__name__, endpoint.uid)
            endpoints_by_objective.setdefault(selection_uid, []).append(USDMEndpoint(
                id=endpoint_id, name=endpoint_id, description=endpoint.name,
                instanceType="Endpoint", text=endpoint.name_plain or "",
                purpose=endpoint.name_plain or "", label=endpoint.name_plain or "",
                level=(self.get_ct_package_term_as_usdm_code(selection.endpoint_level.term_uid)
                       if getattr(selection, "endpoint_level", None) is not None
                       else self.get_void_usdm_code()),
            ))
        result = []
        for selection in objectives:
            objective = getattr(selection, "objective", None)
            if objective is None:
                continue
            objective_id = self._id_manager.get_id(USDMObjective.__name__, objective.uid)
            result.append(USDMObjective(
                id=objective_id, name=objective_id, instanceType="Objective",
                label=objective.name_plain, text=objective.name_plain, description=objective.name,
                level=(self.get_ct_package_term_as_usdm_code(selection.objective_level.term_uid)
                       if getattr(selection, "objective_level", None) is not None
                       else self.get_void_usdm_code()),
                endpoints=endpoints_by_objective.get(selection.study_objective_uid, []),
            ))
        return result

    def _get_study_schedule_timelines(self, study: OSBStudy) -> list[USDMScheduleTimeline]:
        schedules = _stable_selection_order(
            _items(self._call(self._get_osb_activity_schedules, study.uid)),
            "study_activity_schedule_uid")
        visits = _stable_selection_order(
            _items(self._call(self._get_osb_study_visits, study.uid)), "uid")
        timeline_id = self._id_manager.get_id(USDMScheduleTimeline.__name__)
        timeline = USDMScheduleTimeline(
            id=timeline_id, name="Main Timeline", mainTimeline=True,
            entryCondition="", entryId="", instances=[])
        anchor = next((visit for visit in visits if visit.is_global_anchor_visit is True), None)
        anchor_id = (self._id_manager.get_id(ScheduledActivityInstance.__name__, f"anchor:{anchor.uid}")
                     if anchor is not None else None)
        instances, timings = [], []
        for visit in visits:
            visit_schedules = [s for s in schedules if s.study_visit_uid == visit.uid]
            instance_id = (anchor_id if visit is anchor else self._id_manager.get_id(
                ScheduledActivityInstance.__name__, f"visit:{visit.uid}"))
            instance = ScheduledActivityInstance(
                id=instance_id, name="Activity Instance", timelineId=timeline_id,
                instanceType="ScheduledActivityInstance",
                encounterId=self._id_manager.get_id(USDMEncounter.__name__, visit.uid),
                activityIds=[self._id_manager.get_id(USDMActivity.__name__, s.study_activity_uid)
                             for s in visit_schedules if s.study_activity_uid is not None],
                epochId=(self._id_manager.get_id(USDMStudyEpoch.__name__, visit.study_epoch_uid)
                         if visit.study_epoch_uid is not None else None),
            )
            if visit.time_value is None or not visit.time_unit_name:
                instances.append(instance)
                continue
            if visit.time_value < 0:
                timing_type = self.get_ddf_timing_type_code_before()
            elif visit.time_value > 0:
                timing_type = self.get_ddf_timing_type_code_after()
            else:
                timing_type = self.get_ddf_timing_type_code_fixed()
            has_window = (visit.min_visit_window_value is not None
                          and visit.max_visit_window_value is not None
                          and bool(visit.visit_window_unit_name)
                          and (visit.min_visit_window_value != 0 or visit.max_visit_window_value != 0))
            epoch_label = getattr(getattr(visit, "study_epoch", None), "sponsor_preferred_name", None)
            timing_id = self._id_manager.get_id(USDMTiming.__name__, visit.uid)
            timing = USDMTiming(
                id=timing_id, name=timing_id, label=epoch_label, description=epoch_label,
                type=timing_type,
                relativeToFrom=(self.get_ddf_timing_relative_to_from()
                                if anchor_id is not None else self.get_void_usdm_code()),
                value=get_ddf_timing_iso_duration_value(
                    visit.time_value, visit.time_unit_name
                ),
                valueLabel=f"{abs(visit.time_value)} {visit.time_unit_name}",
                relativeFromScheduledInstanceId=instance_id,
                relativeToScheduledInstanceId=anchor_id,
                windowLower=(get_ddf_timing_iso_duration_value(
                    visit.min_visit_window_value, visit.visit_window_unit_name) if has_window else None),
                windowUpper=(get_ddf_timing_iso_duration_value(
                    visit.max_visit_window_value, visit.visit_window_unit_name) if has_window else None),
                window=(f"{visit.min_visit_window_value}..{visit.max_visit_window_value} "
                        f"{visit.visit_window_unit_name}" if has_window else None),
            )
            instances.append(instance)
            timings.append(timing)
        timeline.instances, timeline.timings = instances, timings
        return [timeline]

    def _get_study_encounters(self, study: OSBStudy) -> list[USDMEncounter]:
        visits = _stable_selection_order(_items(self._call(self._get_osb_study_visits, study.uid)), "uid")
        return [USDMEncounter(
            id=self._id_manager.get_id(USDMEncounter.__name__, visit.uid),
            name=visit.visit_short_name, label=visit.visit_name, description=visit.description,
            type=(self.get_ct_package_term_as_usdm_code(visit.visit_type.term_uid)
                  if visit.visit_type is not None else self.get_void_usdm_code()),
            transitionStartRule=USDMTransitionRule(
                id=self._id_manager.get_id(USDMTransitionRule.__name__, f"start:{visit.uid}"),
                name="Transition Start Rule", text=visit.start_rule or ""),
            transitionEndRule=USDMTransitionRule(
                id=self._id_manager.get_id(USDMTransitionRule.__name__, f"end:{visit.uid}"),
                name="Transition End Rule", text=visit.end_rule or ""),
            contactModes=([self.get_ct_package_term_as_usdm_code(visit.visit_contact_mode.term_uid)]
                          if visit.visit_contact_mode is not None else []),
            nextId=(self._id_manager.get_id(USDMEncounter.__name__, visits[index + 1].uid)
                    if index + 1 < len(visits) else None),
            previousId=(self._id_manager.get_id(USDMEncounter.__name__, visits[index - 1].uid)
                        if index > 0 else None),
        ) for index, visit in enumerate(visits)]

    def _get_study_identifiers_and_organizations(self, study: OSBStudy):
        identification = getattr(study.current_metadata, "identification_metadata", None)
        registry = getattr(identification, "registry_identifiers", None)
        identifiers, organizations = [], []
        for field_name, config in self.REGISTRY_ORGANIZATIONS.items():
            org_name, term_uid, scheme, org_type, fallback = config
            value = getattr(registry, field_name, None)
            if not value:
                continue
            organization_id = self._id_manager.get_id(USDMOrganization.__name__, field_name)
            organizations.append(USDMOrganization(
                id=organization_id, name=org_name,
                label=self._registid_labels.get(term_uid, fallback) if term_uid else fallback,
                type=self.get_ct_package_term_as_usdm_code(org_type),
                identifierScheme=scheme, identifier=org_name, instanceType="Organization"))
            identifiers.append(USDMStudyIdentifier(
                id=self._id_manager.get_id(USDMStudyIdentifier.__name__, field_name),
                text=value, scopeId=organization_id, instanceType="StudyIdentifier"))
        return identifiers, organizations

    def _get_study_indications(self, study: OSBStudy) -> list[USDMIndication]:
        population = getattr(study.current_metadata, "study_population", None)
        if population is None:
            return []
        rare = getattr(population, "rare_disease_indicator", None)
        return [USDMIndication(
            id=self._id_manager.get_id(USDMIndication.__name__, getattr(item, "term_uid", item.name)),
            name=item.name, label=item.name, isRareDisease=rare)
            for item in getattr(population, "disease_condition_or_indication_codes", [])
            if getattr(item, "name", None) is not None and rare is not None]

    def _get_study_interventions(self, study: OSBStudy) -> list[USDMStudyIntervention]:
        intervention = getattr(study.current_metadata, "study_intervention", None)
        if intervention is None:
            return []
        codes = [self.get_ct_package_term_as_usdm_code(value.term_uid)
                 for field in ("intervention_model_code", "control_type_code", "trial_blinding_schema_code")
                 if (value := getattr(intervention, field, None)) is not None]
        codes.extend(self.get_ct_package_term_as_usdm_code(value.term_uid)
                     for value in (getattr(intervention, "trial_intent_types_codes", None) or [])
                     if value is not None)
        model_code = getattr(intervention, "intervention_model_code", None)
        type_code = getattr(intervention, "intervention_type_code", None)
        return [USDMStudyIntervention(
            id=self._id_manager.get_id(USDMStudyIntervention.__name__, study.uid),
            name="Study Intervention",
            description=model_code.sponsor_preferred_name if model_code is not None else None,
            codes=codes, role=self.get_void_usdm_code(),
            type=(self.get_ct_package_term_as_usdm_code(type_code.term_uid)
                  if type_code is not None else self.get_void_usdm_code()),
            instanceType="StudyIntervention")]

    def _get_study_population(self, study: OSBStudy) -> USDMStudyDesignPopulation:
        population = study.current_metadata.study_population
        sex_name = getattr(getattr(population, "sex_of_participants_code", None),
                           "sponsor_preferred_name", "").upper()
        sex_factory = {
            "BOTH": self.get_ddf_study_population_sex_both,
            "FEMALE": self.get_ddf_study_population_sex_female,
            "MALE": self.get_ddf_study_population_sex_male,
        }.get(sex_name)
        planned_sex = [sex_factory()] if sex_factory is not None else []
        minimum = getattr(population, "planned_minimum_age_of_subjects", None)
        maximum = getattr(population, "planned_maximum_age_of_subjects", None)
        planned_age = None
        if (minimum is not None and maximum is not None
                and minimum.duration_value is not None and maximum.duration_value is not None):
            planned_age = USDMRange(
                id=self._id_manager.get_id(USDMRange.__name__),
                minValue=self._duration_quantity(minimum), maxValue=self._duration_quantity(maximum),
                isApproximate=False, instanceType="Range")
        enrollment = getattr(population, "number_of_expected_subjects", None)
        planned_enrollment = (USDMQuantity(
            id=self._id_manager.get_id(USDMQuantity.__name__), value=enrollment,
            unit=USDMAliasCode(id=self._id_manager.get_id(USDMAliasCode.__name__),
                               standardCode=self.get_ddf_study_population_enrollment_number_unit(),
                               instanceType="AliasCode"), instanceType="Quantity")
            if enrollment is not None else None)
        result = USDMStudyDesignPopulation(
            id=self._id_manager.get_id(USDMStudyDesignPopulation.__name__),
            name="Study Design Population", plannedSex=planned_sex,
            plannedEnrollmentNumber=planned_enrollment, plannedAge=planned_age,
            includesHealthySubjects=(population.healthy_subject_indicator
                                     if population.healthy_subject_indicator is not None else False))
        result.description = " | ".join(
            f"{attribute}: {getattr(population, attribute, None)}" for attribute in (
                "diagnosis_group_codes", "disease_condition_or_indication_codes",
                "healthy_subject_indicator", "number_of_expected_subjects",
                "pediatric_investigation_plan_indicator", "pediatric_postmarket_study_indicator",
                "pediatric_study_indicator", "planned_maximum_age_of_subjects",
                "planned_minimum_age_of_subjects", "rare_disease_indicator", "relapse_criteria",
                "sex_of_participants_code", "stable_disease_minimum_duration", "therapeutic_area_codes"))
        return result

    def _duration_quantity(self, duration) -> USDMQuantity:
        unit_name = getattr(getattr(duration, "duration_unit_code", None), "name", None)
        return USDMQuantity(
            id=self._id_manager.get_id(USDMQuantity.__name__), value=duration.duration_value,
            unit=USDMAliasCode(
                id=self._id_manager.get_id(USDMAliasCode.__name__),
                standardCode=(self.get_ddf_study_population_duration_unit_from_name_as_code(unit_name)
                              if unit_name else self.get_void_usdm_code()),
                instanceType="AliasCode"), instanceType="Quantity")

    def _get_study_definition_document(self, study: OSBStudy) -> USDMStudyDefinitionDocument:
        document = USDMStudyDefinitionDocument(
            id=self._id_manager.get_id(USDMStudyDefinitionDocument.__name__),
            name="Study Definition Document", language=self.get_void_usdm_code(),
            type=self.get_void_usdm_code(), templateName="",
            instanceType="StudyDefinitionDocument")
        metadata = getattr(study.current_metadata, "version_metadata", None)
        status, number = getattr(metadata, "study_status", None), getattr(metadata, "version_number", None)
        if status == StudyStatus.DRAFT.value:
            protocol_status = self.get_ddf_study_protocol_status_draft()
        elif status in {StudyStatus.LOCKED.value, StudyStatus.RELEASED.value}:
            protocol_status = self.get_ddf_study_protocol_status_final()
        else:
            protocol_status = self.get_void_usdm_code()
        document.versions = [USDMStudyDefinitionDocumentVersion(
            id=self._id_manager.get_id(USDMStudyDefinitionDocumentVersion.__name__),
            instanceType="StudyDefinitionDocumentVersion", status=protocol_status,
            version="DRAFT" if status == StudyStatus.DRAFT.value else str(number))]
        return document

    def _get_study_description(self, study: OSBStudy):
        return getattr(getattr(study.current_metadata, "study_description", None), "study_title", None)

    def _get_study_label(self, study: OSBStudy):
        return getattr(getattr(study.current_metadata, "study_description", None), "study_short_title", None)

    def _get_study_name(self, study: OSBStudy):
        return getattr(getattr(study.current_metadata, "identification_metadata", None), "study_id", "")

    def _get_study_title(self, study: OSBStudy):
        return self._get_study_description(study) or ""

    def _get_study_phase(self, study: OSBStudy) -> USDMAliasCode | None:
        design = getattr(study.current_metadata, "high_level_study_design", None)
        phase = getattr(design, "trial_phase_code", None)
        if phase is None:
            return None
        return USDMAliasCode(
            id=self._id_manager.get_id(USDMAliasCode.__name__),
            standardCode=self.get_ct_package_term_as_usdm_code(
                extract_c_code_from_simple_term(phase.term_uid)
            ),
            instanceType="AliasCode")

    def _get_study_type(self, study: OSBStudy) -> USDMCode | None:
        design = getattr(study.current_metadata, "high_level_study_design", None)
        study_type = getattr(design, "study_type_code", None)
        if study_type is None:
            return None
        return self.get_ct_package_term_as_usdm_code(
            extract_c_code_from_simple_term(study_type.term_uid)
        )

    def _get_study_version(self, study: OSBStudy) -> str:
        version = getattr(study.current_metadata, "version_metadata", None)
        if version is None:
            return ""
        value = str(version.study_status)
        if version.version_number is not None:
            value += f" v{version.version_number}"
        return value

    def _get_therapeutic_areas(self, study: OSBStudy) -> list[USDMCode]:
        population = getattr(study.current_metadata, "study_population", None)
        return [self.get_dictionary_term_as_usdm_code(term.term_uid)
                for term in (getattr(population, "therapeutic_area_codes", None) or [])]

    REGISTRY_ORGANIZATIONS: dict[str, tuple[str, str | None, str, str, str]] = {
        "ct_gov_id": ("CT-GOV", "CTTerm_000212", "USGOV", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "ClinicalTrials.gov ID"),
        "eudract_id": ("EUDRACT", "CTTerm_000215", "EU", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "EUDRACT ID"),
        "eu_trial_number": ("EU-CT", "CTTerm_000218", "EU", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "EU Trial Number"),
        "universal_trial_number_utn": ("WHO-UTN", "CTTerm_000214", "UTN", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "Universal Trial Number (UTN)"),
        "japanese_trial_registry_id_japic": ("JAPIC", "CTTerm_000213", "JAPIC", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "Japanese Trial Registry ID (JAPIC)"),
        "japanese_trial_registry_number_jrct": ("JRCT", "CTTerm_000221", "JRCT", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "Japanese Trial Registry Number (jRCT)"),
        "investigational_new_drug_application_number_ind": ("FDA-IND", "CTTerm_000217", "USGOV", DDF_ORGANIZATION_TYPE_REGULATORY_AGENCY, "Investigational New Drug Application (IND) Number"),
        "investigational_device_exemption_ide_number": ("FDA-IDE", "CTTerm_000224", "USGOV", DDF_ORGANIZATION_TYPE_REGULATORY_AGENCY, "Investigational Device Exemption (IDE) Number"),
        "civ_id_sin_number": ("CIV-SIN", "CTTerm_000216", "EU", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "CIV-ID/SIN Number"),
        "national_clinical_trial_number": ("NCT-REG", "CTTerm_000220", "NATIONAL", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "National Clinical Trial Number"),
        "national_medical_products_administration_nmpa_number": ("NMPA", "CTTerm_000222", "NMPA", DDF_ORGANIZATION_TYPE_REGULATORY_AGENCY, "National Medical Products Administration (NMPA) Number"),
        "eudamed_srn_number": ("EUDAMED", "CTTerm_000223", "EU", DDF_ORGANIZATION_TYPE_REGULATORY_AGENCY, "EUDAMED SRN Number"),
        "eu_pas_number": ("EU-PAS", None, "EU", DDF_ORGANIZATION_TYPE_STUDY_REGISTRY, "EU PAS Register"),
    }
