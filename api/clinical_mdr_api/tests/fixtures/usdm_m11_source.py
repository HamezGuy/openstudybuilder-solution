"""Explicit synthetic native selections for M11; production mapper builds USDM."""

from starlette_context import request_cycle_context

from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionCriteria,
)
from clinical_mdr_api.models.syntax_instances.criteria import Criteria
from clinical_mdr_api.models.syntax_templates.criteria_template import CriteriaTemplate
from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json
from clinical_mdr_api.tests.fixtures.usdm_native_compound import NativeCompoundSource
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import (
    AS_OF,
    STUDY_UID,
    VERSION,
    native_study_graph,
)
from common.auth.dependencies import dummy_access_token_claims, dummy_auth_object


def m11_source_graph():
    graph = native_study_graph()
    arm, element, cell = graph["arms"][0], graph["elements"][0], graph["cells"][0]
    graph["arms"] = [
        arm.model_copy(
            deep=True,
            update={
                "arm_uid": f"Arm_{index}",
                "name": f"Selected arm {index}",
                "order": index,
            },
        )
        for index in (1, 2, 3)
    ]
    graph["elements"] = [
        element.model_copy(
            deep=True,
            update={
                "element_uid": f"Element_{index}",
                "name": f"Treatment element {index}",
                "order": index,
            },
        )
        for index in (1, 2, 3)
    ]
    graph["cells"] = [
        cell.model_copy(
            deep=True,
            update={
                "design_cell_uid": f"Cell_{index}",
                "study_arm_uid": f"Arm_{index}",
                "study_branch_arm_uid": None,
                "study_element_uid": f"Element_{index}",
                "study_element_name": f"Treatment element {index}",
                "order": index,
            },
        )
        for index in (1, 2, 3)
    ]
    graph["branches"][0].arm_root = graph["arms"][0]
    graph["cells"].append(
        cell.model_copy(
            deep=True, update={"design_cell_uid": "BranchCell_1", "order": 4}
        )
    )

    compound_source = NativeCompoundSource(STUDY_UID, VERSION, AS_OF)
    with request_cycle_context(
        {"auth": dummy_auth_object(dummy_access_token_claims())}
    ):
        compounds, dosings = compound_source.response_models(
            graph["study"], graph["elements"][0]
        )
    graph["compounds"] = compounds
    graph["dosings"] = []
    for index, value in ((1, 5.5), (2, 0), (3, None)):
        dose = dosings[0].model_copy(
            deep=True,
            update={
                "study_compound_dosing_uid": f"Dosing_{index}",
                "order": index,
                "study_element": graph["elements"][index - 1],
            },
        )
        if value is None:
            dose.dose_value = None
        else:
            dose.dose_value = dose.dose_value.model_copy(
                deep=True, update={"uid": f"SyntheticDose_{index}", "value": value}
            )
        graph["dosings"].append(dose)
    # A second compound selection can reference the same selected product. Its
    # source identity and bindings remain distinct from the first selection.
    second = compounds[0].model_copy(
        deep=True, update={"study_compound_uid": "CompoundSelection_2", "order": 2}
    )
    for binding in second.native_library_bindings:
        if "studyCompoundUid" in binding:
            binding["studyCompoundUid"] = second.study_compound_uid
    second.other_info = "Second selection has its own dosing relationship"
    graph["compounds"].append(second)
    graph["dosings"].append(
        graph["dosings"][0].model_copy(
            deep=True,
            update={
                "study_compound_dosing_uid": "Dosing_second",
                "order": 4,
                "study_compound": second,
                "study_element": graph["elements"][1],
            },
        )
    )

    population = graph["study"].current_metadata.study_population
    values = population.model_dump(mode="python")
    values.update(
        {
            "planned_minimum_age_of_subjects": {
                "duration_value": 0,
                "duration_unit_code": {"uid": "Unit_years", "name": "years"},
            },
            "planned_maximum_age_of_subjects": None,
            "stable_disease_minimum_duration": {
                "duration_value": 0,
                "duration_unit_code": {"uid": "Unit_days", "name": "days"},
            },
            "pediatric_study_indicator": False,
            "relapse_criteria": "No relapse during the selected interval",
            "planned_maximum_age_of_subjects_null_value_code": {
                "term_uid": "C17998",
                "sponsor_preferred_name": "Unknown",
            },
        }
    )
    graph["study"].current_metadata.study_population = type(population)(**values)
    graph["criteria"] = []
    for uid, order, category, text in (
        ("Criterion_inc_second", 2, "C25532", "Age ≥ 0 years; interval [0, 18)"),
        (
            "Criterion_exc",
            1,
            "C25370",
            "Excluded only when the selected condition applies",
        ),
        ("Criterion_inc_first", 1, "C25532", "Age ≥ 0 years; interval [0, 18)"),
        ("Criterion_unknown", 3, None, "Review category before use"),
    ):
        graph["criteria"].append(
            StudySelectionCriteria(
                study_uid=STUDY_UID,
                study_version=VERSION,
                study_criteria_uid=uid,
                order=order,
                start_date=AS_OF,
                key_criteria=False,
                criteria_type=(
                    {
                        "term_uid": category,
                        "term_name": (
                            "EXCLUSION" if category == "C25532" else "INCLUSION"
                        ),
                    }
                    if category
                    else None
                ),
                criteria=Criteria(
                    uid=uid + "_definition",
                    name=text,
                    name_plain=text,
                    version="1.0",
                    status="Final",
                ),
            )
        )
    graph["criteria"].append(
        StudySelectionCriteria(
            study_uid=STUDY_UID,
            study_version=VERSION,
            study_criteria_uid="Criterion_template",
            order=4,
            start_date=AS_OF,
            criteria_type={"term_uid": "C25532", "term_name": "INCLUSION"},
            key_criteria=None,
            template=CriteriaTemplate(
                uid="Uninstantiated_template",
                name="Age ≥ [parameter]",
                name_plain="Age ≥ [parameter]",
                start_date=AS_OF,
                end_date=None,
                version="1.0",
                status="Final",
            ),
        )
    )
    return graph


def m11_source_report(graph=None):
    source = NativeStudySource(m11_source_graph() if graph is None else graph)
    before = native_json(source.graph)
    with source.isolated():
        report = source.mapper().map_with_report(source.graph["study"], VERSION)
    assert native_json(source.graph) == before
    return report
