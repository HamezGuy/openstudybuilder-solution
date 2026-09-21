"""Synthetic native-model graph. No database, imported carrier or USDM echo."""

from datetime import datetime, timezone
from unittest.mock import patch

from clinical_mdr_api.models.concepts.activities.activity_instance import (
    ActivityInstanceAttributes,
)
from clinical_mdr_api.models.concepts.unit_definitions.unit_definition import (
    UnitDefinitionModel,
)
from clinical_mdr_api.models.study_selections.study import (
    Study,
    StudyProtocolHeaderVersion,
    StudyVersionHistory,
)
from clinical_mdr_api.models.study_selections.study_disease_milestone import (
    StudyDiseaseMilestone,
)
from clinical_mdr_api.models.study_selections.study_epoch import StudyEpoch
from clinical_mdr_api.models.study_selections.study_selection import (
    StudyActivityGroup,
    StudyActivityInstruction,
    StudyActivitySchedule,
    StudyActivitySubGroup,
    StudyDesignCell,
    StudyDesignClass,
    StudySelectionActivity,
    StudySelectionActivityInstance,
    StudySelectionArm,
    StudySelectionBranchArm,
    StudySelectionCohort,
    StudySelectionDataSupplier,
    StudySelectionElement,
    StudySoAGroup,
    StudySourceVariable,
)
from clinical_mdr_api.models.study_selections.study_soa_footnote import StudySoAFootnote
from clinical_mdr_api.models.study_selections.study_standard_version import (
    StudyStandardVersion,
)
from clinical_mdr_api.models.study_selections.study_visit import StudyVisit
from clinical_mdr_api.services.ddf.usdm_service import NativeVisitSnapshot
from common.utils import VisitClass

STUDY_UID = "Study_semantic_fixture"
VERSION = "2.0"
AS_OF = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)


def term(code, label):
    return {"term_uid": code, "sponsor_preferred_name": label}


def native_study_graph():
    study = Study(
        uid=STUDY_UID,
        study_parent_part=None,
        study_subpart_uids=[],
        possible_actions=[],
        current_metadata={
            "identification_metadata": {
                "study_id": "OSB-SEMANTIC",
                "study_acronym": "OSB-S",
                "description": "",
                "registry_identifiers": {},
            },
            "study_description": {
                "study_title": "Synthetic native semantics study",
                "study_short_title": "Native semantics",
            },
            "version_metadata": {
                "study_status": "LOCKED",
                "version_number": VERSION,
                "version_timestamp": AS_OF,
                "version_author": "synthetic-author",
                "version_description": "Native snapshot two",
            },
            "high_level_study_design": {
                "study_type_code": term("C98388", "Renamed sponsor display label"),
                "is_adaptive_design": False,
                "is_extension_trial": True,
            },
            "study_intervention": {
                "intervention_model_code": term("C82639", "Parallel"),
                "trial_blinding_schema_code": term("C49659", "Open label"),
                "trial_intent_types_codes": [term("C49656", "Treatment")],
                "is_trial_randomised": False,
            },
            "study_population": {
                "healthy_subject_indicator": False,
                "rare_disease_indicator": False,
                "sex_of_participants_code": term("C49636", "Renamed BOTH display"),
                "number_of_expected_subjects": 30,
                "planned_minimum_age_of_subjects": {
                    "duration_value": 18,
                    "duration_unit_code": {"uid": "Unit_years", "name": "years"},
                },
                "planned_maximum_age_of_subjects": {
                    "duration_value": 75,
                    "duration_unit_code": {"uid": "Unit_years", "name": "years"},
                },
                "disease_condition_or_indication_codes": [
                    {"term_uid": "Dictionary_1", "name": "Synthetic disease"}
                ],
            },
        },
    )
    common = {"study_uid": STUDY_UID, "study_version": VERSION, "order": 1}
    arm = StudySelectionArm(
        **common,
        arm_uid="Arm_1",
        name="Native arm",
        short_name="A",
        start_date=AS_OF,
        description="",
        number_of_subjects=30,
        arm_type={"term_uid": "C174266", "term_name": "Experimental Arm"},
        # Explicit fixture authorship; these are not participant observations.
        data_origin_type_uid="C176263",
        data_origin_description="Synthetic arm data authored for isolated mapper and export tests.",
    )
    branch = StudySelectionBranchArm(
        **common,
        branch_arm_uid="Branch_1",
        name="Native branch",
        short_name="B",
        start_date=AS_OF,
        description=None,
        number_of_subjects=0,
        arm_root=arm,
    )
    cohorts = [
        StudySelectionCohort(
            **common,
            cohort_uid="Cohort_1",
            name="Cohort one",
            short_name="C1",
            start_date=AS_OF,
            description="",
            number_of_subjects=0,
            arm_roots=[arm],
            branch_arm_roots=[branch],
        ),
        StudySelectionCohort(
            **{**common, "order": 2},
            cohort_uid="Cohort_2",
            name="Cohort two",
            short_name="C2",
            start_date=AS_OF,
            description=None,
            number_of_subjects=20,
            arm_roots=[arm],
        ),
    ]
    element = StudySelectionElement(
        **common,
        element_uid="Element_1",
        name="Treatment element",
        short_name="Tx",
        code="T",
        description="Source element",
        start_date=AS_OF,
        start_rule="Begin assigned treatment",
        end_rule="Treatment completed",
    )
    epoch = StudyEpoch(
        **common,
        uid="Epoch_1",
        epoch_name="Treatment",
        epoch_type_name="Treatment",
        epoch_ctterm=term("C101526", "Treatment"),
        epoch_subtype_ctterm=term("C101526", "Treatment"),
        epoch_type_ctterm=term("C101526", "Treatment"),
        start_date=AS_OF.isoformat(),
        status="LOCKED",
        possible_actions=[],
        study_visit_count=3,
    )
    cell = StudyDesignCell(
        **common,
        design_cell_uid="Cell_1",
        study_arm_uid="Arm_1",
        study_branch_arm_uid="Branch_1",
        study_epoch_uid="Epoch_1",
        study_epoch_name="Treatment",
        study_element_uid="Element_1",
        study_element_name="Treatment element",
        start_date=AS_OF,
        transition_rule="Only participants assigned to this branch",
    )
    activity = StudySelectionActivity(
        **common,
        study_activity_uid="Activity_1",
        study_activity_subgroup=None,
        study_activity_group=None,
        study_soa_group={
            "study_soa_group_uid": "SoAGroup_1",
            "soa_group_term_uid": "SoATerm_1",
            "soa_group_term_name": "Study assessments",
        },
        activity={
            "uid": "LibraryActivity_1",
            "name": "Haematology",
            "is_data_collected": True,
            "version": "1.0",
            "library_name": "Synthetic library",
        },
    )
    selected = StudySelectionActivityInstance(
        study_uid=STUDY_UID,
        study_version=VERSION,
        study_activity_instance_uid="InstanceSelection_1",
        study_activity_uid="Activity_1",
        study_data_supplier_uid="SupplierSelection_1",
        show_activity_instance_in_protocol_flowchart=False,
        activity=activity.activity.model_dump(),
        activity_instance={
            "uid": "LibraryInstance_1",
            "name": "Platelets at selected version",
            "version": "1.0",
            "activity_instance_class": {"uid": "Class_1", "name": "Laboratory"},
            "topic_code": "PLAT",
            "standard_unit": "10^9/L",
        },
        latest_activity_instance={
            "uid": "LibraryInstance_1",
            "name": "UNSELECTED LATEST",
            "version": "2.0",
            "activity_instance_class": {"uid": "Class_1", "name": "Laboratory"},
        },
    )
    definition = ActivityInstanceAttributes(
        uid="LibraryInstance_1",
        library_name="Synthetic library",
        version="1.0",
        name="Platelets at selected version",
        molecular_weight=0,
        activity_instance_class={"uid": "Class_1", "name": "Laboratory"},
        activity_items=[
            {
                "activity_item_class": {"uid": "ItemClass_1", "name": "Result"},
                "ct_terms": [],
                "unit_definitions": [
                    {"uid": "Unit_1", "name": "10^9/L", "dimension_name": None}
                ],
                "is_adam_param_specific": False,
                "is_activity_instance_id_specific": None,
                "text_value": "",
            }
        ],
    )
    instruction = StudyActivityInstruction(
        study_uid=STUDY_UID,
        study_version=VERSION,
        study_activity_instruction_uid="InstructionSelection_1",
        study_activity_uid="Activity_1",
        activity_instruction_uid="Instruction_1",
        activity_instruction_name="Collect before dosing; preserve <b>fasting</b> text.",
        start_date=AS_OF,
    )
    visits = []
    for index, (uid, anchor, offset) in enumerate(
        [
            ("Visit_1", "Visit_1", 0),
            ("Visit_2", "Visit_1", 14),
            ("Visit_3", "Visit_2", 2),
        ],
        start=1,
    ):
        value = StudyVisit(
            uid=uid,
            study_uid=STUDY_UID,
            study_version=VERSION,
            show_visit=True,
            study_epoch_uid="Epoch_1",
            study_epoch=term("C101526", "Treatment"),
            visit_number=index,
            unique_visit_number=index,
            visit_short_name=f"V{index}",
            visit_name=f"Visit {index}",
            visit_class=VisitClass.SINGLE_VISIT,
            is_global_anchor_visit=index == 1,
            is_soa_milestone=False,
            visit_type=term("C25716", "Visit"),
            visit_contact_mode=None,
            visit_subnumber=0,
            order=index,
            visit_subname=f"Visit {index}",
            status="LOCKED",
            start_date=AS_OF,
            possible_actions=[],
            time_value=offset,
            time_unit_name="days",
            time_unit_uid="Unit_days",
            min_visit_window_value=0 if index == 1 else -2,
            max_visit_window_value=0 if index == 1 else 3,
            visit_window_unit_name="days",
            start_rule="",
        )
        visits.append(NativeVisitSnapshot(value, anchor))
    planning = [
        StudyActivitySchedule(
            study_activity_schedule_uid="Plan_1",
            study_activity_uid="Activity_1",
            study_visit_uid="Visit_2",
        )
    ]
    operational = [
        StudyActivitySchedule(
            study_activity_schedule_uid="Operation_1",
            study_activity_uid="Activity_1",
            study_visit_uid="Visit_2",
            study_activity_instance_uid="InstanceSelection_1",
        )
    ]
    footnote = StudySoAFootnote(
        uid="FootnoteSelection_1",
        study_uid=STUDY_UID,
        study_version=VERSION,
        order=1,
        footnote={
            "uid": "Footnote_1",
            "name": "Use <i>local</i> sample handling.",
            "version": "1.0",
        },
        latest_footnote={
            "uid": "Footnote_1",
            "name": "UNSELECTED FOOTNOTE",
            "version": "2.0",
        },
        referenced_items=[
            {
                "item_type": "StudyActivityInstance",
                "item_uid": "InstanceSelection_1",
                "visible_in_protocol_soa": False,
            },
            {"item_type": "StudyVisit", "item_uid": "Visit_2"},
            {"item_type": "StudyActivitySchedule", "item_uid": "Operation_1"},
        ],
    )
    milestone = StudyDiseaseMilestone(
        uid="Milestone_1",
        study_uid=STUDY_UID,
        study_version=VERSION,
        disease_milestone_type="DIAGNOSIS",
        disease_milestone_type_name="Diagnosis",
        disease_milestone_type_definition="Diagnosis of the study condition",
        repetition_indicator=False,
        order=1,
        start_date=AS_OF,
        status="LOCKED",
        # Authored synthetic native history, not a real graph qualification.
        # The owned-Neo4j milestone regression separately exercises these reads.
        terminology_source={
            "state": "resolved",
            "mode": "study-snapshot-as-of",
            "study_uid": STUDY_UID,
            "study_value_version": VERSION,
            "study_value_identity": "synthetic-native-study-value",
            "as_of": AS_OF,
            "selection_identity": "synthetic-native-milestone-selection",
            "context_identity": "synthetic-native-milestone-term-context",
            "term_uid": "DIAGNOSIS",
            "codelist_uid": "synthetic-milestone-types",
            "name": {
                "value_identity": "synthetic-milestone-name-value",
                "version": "1.0",
                "status": "Final",
                "start_date": AS_OF,
                "end_date": None,
                "author_id": "synthetic-author",
                "change_description": "Explicit synthetic source fixture",
                "value": {"name": "Diagnosis", "name_sentence_case": "Diagnosis"},
            },
            "attributes": {
                "value_identity": "synthetic-milestone-attributes-value",
                "version": "1.0",
                "status": "Final",
                "start_date": AS_OF,
                "end_date": None,
                "author_id": "synthetic-author",
                "change_description": "Explicit synthetic source fixture",
                "value": {"definition": "Diagnosis of the study condition"},
            },
            "issues": [],
            "native_selection": {
                "fixture": "authored native DTO input; not a graph execution receipt",
                "uid": "Milestone_1",
                "disease_milestone_type": "DIAGNOSIS",
            },
        },
    )
    standards = [
        StudyStandardVersion(
            uid="Standard_" + name,
            study_uid=STUDY_UID,
            study_version=VERSION,
            ct_package={
                "uid": name + " CT 2025-09-26",
                "catalogue_name": name + " CT",
                "name": name + " CT 2025-09-26",
                "description": None,
                "registration_status": "Final",
                "extends_package": None,
                "import_date": AS_OF,
                "effective_date": "2025-09-26",
            },
            automatically_created=False,
            start_date=AS_OF,
            study_status="LOCKED",
            description=None,
        )
        for name in ("DDF", "SDTM", "PROTOCOL")
    ]
    history = [
        StudyVersionHistory(
            study_status=["LOCKED"],
            metadata_version=version,
            protocol_header_major_version=1,
            protocol_header_minor_version=index,
            description=f"Source history {version}",
            modified_date=modified,
            modified_by="synthetic-author",
        )
        for index, (version, modified) in enumerate(
            [
                ("1.0", datetime(2026, 8, 1, tzinfo=timezone.utc)),
                (VERSION, AS_OF),
                ("3.0", datetime(2026, 9, 9, tzinfo=timezone.utc)),
            ]
        )
    ]
    # These are actual native response models. Only the username resolver is an
    # isolated storage boundary; the study facts and model validators stay real.
    with patch(
        "clinical_mdr_api.models.study_selections.study_selection."
        "UserInfoService.get_author_username_from_id",
        side_effect=lambda uid: (
            "Synthetic Author" if uid == "synthetic-author" else uid
        ),
    ):
        design_class = StudyDesignClass(
            study_uid=STUDY_UID,
            value="Study with cohorts, branches and subpopulations",
            start_date=AS_OF,
            author_username="synthetic-author",
        )
        source_variable = StudySourceVariable(
            study_uid=STUDY_UID,
            source_variable="Cohort",
            source_variable_description="Explicit synthetic cohort assignment variable.",
            start_date=AS_OF,
            author_username="synthetic-author",
        )
    return {
        "study": study,
        "arms": [arm],
        "branches": [branch],
        "cohorts": cohorts,
        "elements": [element],
        "epochs": [epoch],
        "cells": [cell],
        "activities": [activity],
        "instances": [selected],
        "instructions": [instruction],
        "visits": visits,
        "planning": planning,
        "operational": operational,
        "footnotes": [footnote],
        "milestones": [milestone],
        "standards": standards,
        "data_suppliers": [
            StudySelectionDataSupplier(
                study_uid=STUDY_UID,
                study_version=VERSION,
                study_data_supplier_uid="SupplierSelection_1",
                study_data_supplier_order=1,
                data_supplier_uid="DataSupplier_1",
                name="Synthetic laboratory",
                description="",
                order=0,
                api_base_url="https://data.synthetic.invalid/api",
                ui_base_url=None,
                start_date=AS_OF,
                author_username="Synthetic Author",
                status=None,
                study_data_supplier_type=None,
            )
        ],
        "design_classes": [design_class],
        "source_variables": [source_variable],
        "groups": [
            StudyActivityGroup(
                study_uid=STUDY_UID,
                study_activity_group_uid="Group_1",
                activity_group_uid="LibraryGroup_1",
                activity_group_name="Assessments",
                study_activity_subgroup_uids=["Subgroup_1"],
                study_soa_group_uid="SoAGroup_1",
                show_activity_group_in_protocol_flowchart=False,
            )
        ],
        "subgroups": [
            StudyActivitySubGroup(
                study_uid=STUDY_UID,
                study_activity_subgroup_uid="Subgroup_1",
                activity_subgroup_uid="LibrarySubgroup_1",
                activity_subgroup_name="Laboratory",
                study_activity_group_uid="Group_1",
                study_activity_uids=["Activity_1"],
                show_activity_subgroup_in_protocol_flowchart=False,
            )
        ],
        "soa_groups": [
            StudySoAGroup(
                study_uid=STUDY_UID,
                study_soa_group_uid="SoAGroup_1",
                soa_group_term_uid="SoATerm_1",
                soa_group_term_name="Study assessments",
                study_activity_group_uids=["Group_1"],
                show_soa_group_in_protocol_flowchart=False,
            )
        ],
        "definition": definition,
        "history": history,
        "unit_definitions": [
            UnitDefinitionModel(
                uid="Unit_1",
                name="10^9/L",
                library_name="Synthetic native units",
                version="1.0",
                start_date=AS_OF,
                status="Final",
                change_description="Explicit synthetic unit definition",
                definition="Billions of entities per litre.",
                convertible_unit=False,
                display_unit=True,
                master_unit=False,
                si_unit=False,
                us_conventional_unit=False,
                use_complex_unit_conversion=False,
                ct_units=[],
                unit_subsets=[],
                ucum=None,
                template_parameter=False,
                use_molecular_weight=False,
                conversion_factor_to_master=None,
                comment="",
                order=0,
            )
        ],
        "header": StudyProtocolHeaderVersion(
            protocol_header_version="1.1",
            has_final_protocol_locked_version=True,
        ),
    }
