from copy import deepcopy
from typing import Any

from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyComponentEnum,
)
from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.services.studies.study_activity_schedule import (
    StudyActivityScheduleService,
)
from clinical_mdr_api.services.studies.study_activity_selection import (
    StudyActivitySelectionService,
)
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from clinical_mdr_api.services.studies.study_compound_dosing_selection import (
    StudyCompoundDosingSelectionService,
)
from clinical_mdr_api.services.studies.study_compound_selection import (
    StudyCompoundSelectionService,
)
from clinical_mdr_api.services.studies.study_criteria_selection import (
    StudyCriteriaSelectionService,
)
from clinical_mdr_api.services.studies.study_design_cell import StudyDesignCellService
from clinical_mdr_api.services.studies.study_element_selection import (
    StudyElementSelectionService,
)
from clinical_mdr_api.services.studies.study_endpoint_selection import (
    StudyEndpointSelectionService,
)
from clinical_mdr_api.services.studies.study_epoch import StudyEpochService
from clinical_mdr_api.services.studies.study_objective_selection import (
    StudyObjectiveSelectionService,
)
from clinical_mdr_api.services.studies.study_standard_version_selection import (
    StudyStandardVersionService,
)
from clinical_mdr_api.services.studies.study_visit import StudyVisitService
from clinical_mdr_api.models.study_selections.study_visit import StudyVisit
from clinical_mdr_api.services.studies.study_cohort_selection import StudyCohortSelectionService
from clinical_mdr_api.services.studies.study_branch_arm_selection import StudyBranchArmSelectionService
from clinical_mdr_api.services.studies.study_activity_instance_selection import StudyActivityInstanceSelectionService
from clinical_mdr_api.services.studies.study_activity_instruction import StudyActivityInstructionService
from clinical_mdr_api.services.studies.study_activity_group import StudyActivityGroupService
from clinical_mdr_api.services.studies.study_activity_subgroup import StudyActivitySubGroupService
from clinical_mdr_api.services.studies.study_soa_footnote import StudySoAFootnoteService
from clinical_mdr_api.services.studies.study_soa_group import StudySoAGroupService
from clinical_mdr_api.services.studies.study_disease_milestone import StudyDiseaseMilestoneService
from clinical_mdr_api.services.studies.study_data_supplier import StudyDataSupplierSelectionService
from clinical_mdr_api.services.studies.study_design_class import StudyDesignClassService
from clinical_mdr_api.services.studies.study_source_variable import StudySourceVariableService
from clinical_mdr_api.services.studies.study_activity_instance_snapshot import (
    read_study_activity_instance_definition,
)
from common.telemetry import trace_calls


class NativeVisitSnapshot:
    """One native timeline read, including the anchor resolved by OSB itself."""

    def __init__(self, value: StudyVisit, anchor_uid: str | None):
        self.value = value
        self.resolved_anchor_visit_uid = anchor_uid

    def __getattr__(self, name):
        return getattr(self.value, name)

    def __deepcopy__(self, memo: dict[int, Any]) -> "NativeVisitSnapshot":
        """Copy the wrapper and its native anchor evidence with the same memo."""
        snapshot = type(self).__new__(type(self))
        memo[id(self)] = snapshot
        snapshot.__dict__ = deepcopy(self.__dict__, memo)
        return snapshot

    def model_dump(self, **kwargs):
        return {
            **self.value.model_dump(**kwargs),
            "resolved_anchor_visit_uid": self.resolved_anchor_visit_uid,
        }


def read_native_visits(
    study_uid: str, study_value_version: str | None = None, page_size: int = 0
) -> list[NativeVisitSnapshot]:
    if page_size != 0:
        raise ValueError("USDM native visit snapshots require the complete collection")
    StudyService.check_if_study_uid_and_version_exists(study_uid, study_value_version)
    visits = StudyVisitService._get_all_visits(study_uid, study_value_version=study_value_version)
    return [
        NativeVisitSnapshot(
            StudyVisit.transform_to_response_model(visit, study_value_version=study_value_version),
            getattr(visit.anchor_visit, "uid", None),
        )
        for visit in visits
    ]


def read_native_design_class(
    study_uid: str, study_value_version: str | None = None, page_size: int = 0
):
    if page_size != 0:
        raise ValueError("USDM native snapshots require the complete collection")
    value = StudyDesignClassService().get_existing_study_design_class(
        study_uid, study_value_version=study_value_version
    )
    return [value] if value is not None else []


def read_native_source_variable(
    study_uid: str, study_value_version: str | None = None, page_size: int = 0
):
    if page_size != 0:
        raise ValueError("USDM native snapshots require the complete collection")
    value = StudySourceVariableService().get_study_source_variable(
        study_uid, study_value_version=study_value_version
    )
    return [value] if value is not None else []


class USDMService:
    _usdm_mapper: USDMMapper

    @trace_calls
    def __init__(self):
        self._usdm_mapper = USDMMapper(
            get_osb_study_design_cells=StudyDesignCellService().get_all_design_cells,
            get_osb_study_arms=StudyArmSelectionService().get_all_selection,
            get_osb_study_epochs=StudyEpochService.get_all_epochs,
            get_osb_study_elements=StudyElementSelectionService().get_all_selection,
            get_osb_study_objectives=StudyObjectiveSelectionService().get_all_selection,
            get_osb_study_endpoints=StudyEndpointSelectionService().get_all_selection,
            get_osb_study_standard_versions=StudyStandardVersionService().get_standard_versions_in_study,
            get_osb_study_compounds=StudyCompoundSelectionService().get_all_selection,
            get_osb_study_compound_dosings=StudyCompoundDosingSelectionService().get_all_compound_dosings,
            get_osb_study_criteria=StudyCriteriaSelectionService().get_all_selection,
            get_osb_study_visits=read_native_visits,
            get_osb_study_activities=StudyActivitySelectionService().get_all_selection,
            get_osb_activity_schedules=StudyActivityScheduleService().get_all_schedules,
            get_osb_study_cohorts=StudyCohortSelectionService().get_all_selection,
            get_osb_study_branch_arms=StudyBranchArmSelectionService().get_all_selection,
            get_osb_activity_instances=StudyActivityInstanceSelectionService().get_all_selection,
            get_osb_activity_instructions=StudyActivityInstructionService().get_all_instructions,
            get_osb_activity_groups=StudyActivityGroupService().get_all_selection,
            get_osb_activity_subgroups=StudyActivitySubGroupService().get_all_selection,
            get_osb_soa_groups=StudySoAGroupService().get_all_selection,
            get_osb_soa_footnotes=StudySoAFootnoteService().get_all_by_study_uid,
            get_osb_disease_milestones=StudyDiseaseMilestoneService().get_all_disease_milestones,
            get_osb_study_data_suppliers=StudyDataSupplierSelectionService().get_all_selections,
            get_osb_study_design_class=read_native_design_class,
            get_osb_study_source_variable=read_native_source_variable,
            get_osb_activity_instance_definition=read_study_activity_instance_definition,
            get_osb_protocol_header=StudyService().get_protocol_header_version,
            get_osb_snapshot_history=StudyService().get_study_snapshot_history,
        )

    @trace_calls(args=[1], kwargs=["uid"])
    def get_by_uid(
        self, uid: str, study_value_version: str | None = None
    ) -> dict[str, Any]:
        return self._usdm_mapper.map(
            self._read_study(uid, study_value_version), study_value_version=study_value_version
        )

    @trace_calls(args=[1], kwargs=["uid"])
    def get_by_uid_with_report(
        self, uid: str, study_value_version: str | None = None
    ) -> dict[str, Any]:
        """Return an honest draft and source issues; this grants no release authority."""
        return self._usdm_mapper.map_with_report(
            self._read_study(uid, study_value_version), study_value_version=study_value_version
        )

    @staticmethod
    def _read_study(uid: str, study_value_version: str | None):
        return StudyService().get_by_uid(
            uid,
            include_sections=[
                StudyComponentEnum.IDENTIFICATION_METADATA,
                StudyComponentEnum.REGISTRY_IDENTIFIERS,
                StudyComponentEnum.VERSION_METADATA,
                StudyComponentEnum.STUDY_DESCRIPTION,
                StudyComponentEnum.STUDY_DESIGN,
                StudyComponentEnum.STUDY_INTERVENTION,
                StudyComponentEnum.STUDY_POPULATION,
            ],
            study_value_version=study_value_version,
        )
