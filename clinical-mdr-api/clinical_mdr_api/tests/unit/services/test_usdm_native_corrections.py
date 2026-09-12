"""Acceptance regressions for source facts that the original producer missed."""

from types import SimpleNamespace

import pytest

from clinical_mdr_api.domains.study_selections.study_compound_dosing import StudyCompoundDosingVO
from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionCompound, StudySelectionCriteria, StudySelectionEndpoint,
    StudySelectionObjective,
)
from clinical_mdr_api.models.syntax_instances.criteria import Criteria
from clinical_mdr_api.models.syntax_instances.endpoint import Endpoint
from clinical_mdr_api.models.syntax_instances.objective import Objective
from clinical_mdr_api.models.syntax_templates.criteria_template import CriteriaTemplate
from clinical_mdr_api.models.syntax_templates.objective_template import ObjectiveTemplate
from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json
from clinical_mdr_api.services.studies.study_compound_dosing_selection import StudyCompoundDosingSelectionService
from clinical_mdr_api.services.studies.study_design_class import StudyDesignClassService
from clinical_mdr_api.services.ddf.usdm_service import read_native_design_class, read_native_source_variable
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource, native_odm_graph
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION


def mapped(graph):
    before = native_json(graph)
    source = NativeStudySource(graph)
    with source.isolated():
        report = source.mapper().map_with_report(graph["study"], VERSION)
    assert native_json(graph) == before
    return report


def design(report):
    return report["document"]["study"]["versions"][0]["studyDesigns"][0]


@pytest.mark.parametrize("minimum,maximum", [(18, None), (None, 75), (0, None), (18, 75), (None, None)])
def test_partial_native_age_is_preserved_and_missing_required_bound_is_reported(minimum, maximum):
    graph = NativeStudySource().graph
    population = graph["study"].current_metadata.study_population
    if minimum is None:
        population.planned_minimum_age_of_subjects = None
    else:
        population.planned_minimum_age_of_subjects.duration_value = minimum
    if maximum is None:
        population.planned_maximum_age_of_subjects = None
    else:
        population.planned_maximum_age_of_subjects.duration_value = maximum
    report = mapped(graph)
    age = design(report)["population"]["plannedAge"]
    if minimum is maximum is None:
        assert age is None
    else:
        assert "isApproximate" not in age
        assert any(issue["targetPath"] == "Range/isApproximate"
                   for issue in report["mappingReport"]["issues"])
        for field, value in (("minValue", minimum), ("maxValue", maximum)):
            if value is None:
                assert field not in age
                assert any(issue["targetPath"] == f"Range/{field}"
                           for issue in report["mappingReport"]["issues"])
                assert report["mappingReport"]["state"] == "incomplete"
            else:
                assert age[field]["value"] == value
                assert age[field]["unit"]["standardCode"]["code"]


def test_native_age_unit_without_a_magnitude_remains_a_partial_quantity():
    graph = NativeStudySource().graph
    graph["study"].current_metadata.study_population.planned_minimum_age_of_subjects.duration_value = None
    report = mapped(graph)
    age = design(report)["population"]["plannedAge"]
    assert "value" not in age["minValue"]
    assert age["minValue"]["unit"]["standardCode"]["code"]
    assert age["maxValue"]["value"] == 75
    assert any(issue["targetPath"] == "Quantity/value" for issue in report["mappingReport"]["issues"])


@pytest.mark.parametrize("lower,upper,canonical_lower,canonical_upper,issue", [
    (-9999, 9999, None, None, None),
    (0, 0, None, None, None),
    (-2, 3, "P2D", "P3D", None),
    (-9999, 3, None, "P3D", "USDM_VISIT_WINDOW_INCOMPLETE"),
    (-2, 9999, "P2D", None, "USDM_VISIT_WINDOW_INCOMPLETE"),
])
def test_exported_timing_does_not_turn_no_window_sentinels_into_durations(
    lower, upper, canonical_lower, canonical_upper, issue,
):
    source = NativeStudySource()
    source.graph["visits"][1].value.min_visit_window_value = lower
    source.graph["visits"][1].value.max_visit_window_value = upper
    bundle = source.export(native_odm_graph())
    timing = bundle["definition"]["document"]["study"]["versions"][0]["studyDesigns"][0]["scheduleTimelines"][0]["timings"][1]
    assert timing.get("windowLower") == canonical_lower
    assert timing.get("windowUpper") == canonical_upper
    assert "9999" not in (timing.get("windowLower") or "") + (timing.get("windowUpper") or "")
    issues = bundle["extensions"]["_osbExport"]["mappingReport"]["issues"]
    window_issues = [row for row in issues if row["code"].startswith("USDM_VISIT_WINDOW")]
    assert [row["code"] for row in window_issues] == ([issue] if issue else [])
    portable = bundle["execution"]["visits"][1]
    if lower == -9999:
        assert "minDay" not in portable
    if upper == 9999:
        assert "maxDay" not in portable


@pytest.mark.parametrize("value", [None, "", "Confirm completion of the preceding assessment"])
def test_optional_transition_rules_preserve_unknown_and_explicit_text(value):
    graph = NativeStudySource().graph
    graph["visits"][1].value.start_rule = value
    graph["visits"][1].value.end_rule = value
    encounter = design(mapped(graph))["encounters"][1]
    for field in ("transitionStartRule", "transitionEndRule"):
        if value is None:
            assert encounter.get(field) is None
        else:
            assert encounter[field]["text"] == value


def test_template_selections_are_visible_as_incomplete_canonical_objects():
    graph = NativeStudySource().graph
    graph["objectives"] = [StudySelectionObjective(
        study_uid=STUDY_UID, study_version=VERSION, order=1,
        study_objective_uid="Objective_template_selection", start_date=AS_OF,
        template=ObjectiveTemplate(
            uid="Objective_template_1", name="Assess [parameter]", name_plain="Assess [parameter]",
            start_date=AS_OF, end_date=None, version="1.0", status="Final",
            is_confirmatory_testing=False,
        ),
    )]
    graph["criteria"] = [StudySelectionCriteria(
        study_uid=STUDY_UID, study_version=VERSION, order=1,
        study_criteria_uid="Criteria_template_selection", start_date=AS_OF,
        template=CriteriaTemplate(
            uid="Criteria_template_1", name="Age at least [age]", name_plain="Age at least [age]",
            start_date=AS_OF, end_date=None, version="1.0", status="Final",
        ),
        key_criteria=None,
    )]
    report = mapped(graph)
    version = report["document"]["study"]["versions"][0]
    objective = design(report)["objectives"][0]
    criterion = design(report)["eligibilityCriteria"][0]
    item = next(row for row in version["eligibilityCriterionItems"] if row["id"] == criterion["criterionItemId"])
    assert objective["name"] == "Assess [parameter]" and "text" not in objective
    assert item["name"] == "Age at least [age]" and "text" not in item
    targets = {row["targetPath"] for row in report["mappingReport"]["issues"]}
    assert {"Objective/text", "EligibilityCriterionItem/text"} <= targets
    assert report["mappingReport"]["state"] == "incomplete"
    assert {"studyObjective", "studyCriteria"} <= {row["kind"] for row in report["nativeRecords"]}


@pytest.mark.parametrize("flag", [None, False, True])
def test_unknown_key_criterion_is_not_projected_as_false(flag):
    graph = NativeStudySource().graph
    graph["criteria"] = [StudySelectionCriteria(
        study_uid=STUDY_UID, study_version=VERSION, order=1,
        study_criteria_uid="Criteria_selection", start_date=AS_OF, key_criteria=flag,
        criteria=Criteria(
            uid="Criteria_1", name="Age at least 18", name_plain="Age at least 18",
            version="1.0", status="Final",
        ),
    )]
    report = mapped(graph)
    flags = [row["valueBoolean"] for row in design(report)["eligibilityCriteria"][0]["extensionAttributes"]
             if row["url"].endswith("/key-criterion")]
    assert flags == ([] if flag is None else [flag])
    retained = next(row for row in report["nativeRecords"] if row["kind"] == "studyCriteria")
    assert retained["record"]["key_criteria"] is flag


def endpoint_graph():
    graph = NativeStudySource().graph
    graph["endpoints"] = [StudySelectionEndpoint(
        study_uid=STUDY_UID, study_version=VERSION, order=1,
        study_endpoint_uid="Endpoint_selection", start_date=AS_OF, study_objective=None,
        endpoint=Endpoint(
            uid="Endpoint_1", name="Change from baseline", name_plain="Change from baseline",
            version="1.0", status="Final",
        ),
    )]
    return graph


def test_orphan_endpoint_requires_exact_objective_association_review():
    graph = endpoint_graph()
    report = mapped(graph)
    assert design(report)["objectives"] == []
    assert any(row["code"] == "USDM_ENDPOINT_OBJECTIVE_UNRESOLVED"
               and "Endpoint_selection" in row["sourcePath"]
               for row in report["mappingReport"]["issues"])
    retained = next(row for row in report["nativeRecords"] if row["kind"] == "studyEndpoint")
    assert retained["record"]["endpoint"]["name_plain"] == "Change from baseline"


def test_missing_endpoint_purpose_remains_absent_with_a_required_value_issue():
    graph = endpoint_graph()
    objective = StudySelectionObjective(
        study_uid=STUDY_UID, study_version=VERSION, order=1,
        study_objective_uid="Objective_selection", start_date=AS_OF,
        objective=Objective(uid="Objective_1", name="Assess change", name_plain="Assess change",
                            version="1.0", status="Final"),
    )
    graph["objectives"] = [objective]
    graph["endpoints"][0].study_objective = objective
    report = mapped(graph)
    endpoint = design(report)["objectives"][0]["endpoints"][0]
    assert endpoint["text"] == "Change from baseline"
    assert "purpose" not in endpoint
    assert any(row["targetPath"] == "Endpoint/purpose"
               and "Endpoint_selection" in row["sourcePath"]
               for row in report["mappingReport"]["issues"])


def test_actual_dosing_dto_conversion_preserves_selected_study_version():
    graph = NativeStudySource().graph
    compound = StudySelectionCompound(
        study_uid=STUDY_UID, study_version=VERSION, order=1,
        study_compound_uid="Compound_selection", start_date=AS_OF, medicinal_product=None,
    )
    vo = StudyCompoundDosingVO(
        study_uid=STUDY_UID, study_selection_uid="Dosing_selection",
        study_compound_uid=compound.study_compound_uid,
        study_element_uid=graph["elements"][0].element_uid, start_date=AS_OF,
        author_id="synthetic", compound_uid="Compound_1", compound_alias_uid="Alias_1",
        medicinal_product_uid="Product_1", dose_value_uid=None,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("A null dose does not authorize any library lookup.")

    service = object.__new__(StudyCompoundDosingSelectionService)
    service._transform_study_compound_model = lambda *args, **kwargs: compound
    service._transform_study_element_model = lambda *args, **kwargs: graph["elements"][0]
    service._repos = SimpleNamespace(
        numeric_value_with_unit_repository=SimpleNamespace(find_by_uid_2=forbidden),
        unit_definition_repository=SimpleNamespace(find_by_uid_2=forbidden),
    )
    result = service._transform_to_response_model(STUDY_UID, vo, 1, AS_OF, study_value_version=VERSION)
    assert result.study_version == VERSION
    graph["dosings"] = [result]
    report = mapped(graph)
    row = next(row for row in report["nativeRecords"] if row["kind"] == "studyCompoundDosing")
    assert row["record"]["study_version"] == VERSION


def test_missing_design_class_is_read_without_creating_the_ui_default():
    calls = []
    service = object.__new__(StudyDesignClassService)
    service.check_if_study_exists = lambda **kwargs: calls.append(("exists", kwargs))
    service._repos = SimpleNamespace(study_design_class_repository=SimpleNamespace(
        get_study_design_class=lambda **kwargs: calls.append(("read", kwargs)),
    ))
    service.create = lambda **_: pytest.fail("A read must not author a Manual design.")
    assert service.get_existing_study_design_class(STUDY_UID, VERSION) is None
    assert calls == [
        ("exists", {"study_uid": STUDY_UID}),
        ("read", {"study_uid": STUDY_UID, "study_value_version": VERSION}),
    ]


def test_singleton_readers_pass_exact_version_and_preserve_absence(monkeypatch):
    calls = []

    class DesignService:
        def get_existing_study_design_class(self, study_uid, *, study_value_version):
            calls.append(("design", study_uid, study_value_version))
            return None

    class VariableService:
        def get_study_source_variable(self, study_uid, *, study_value_version):
            calls.append(("variable", study_uid, study_value_version))
            return None

    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_service.StudyDesignClassService", DesignService)
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_service.StudySourceVariableService", VariableService)
    assert read_native_design_class(STUDY_UID, VERSION) == []
    assert read_native_source_variable(STUDY_UID, VERSION) == []
    assert calls == [("design", STUDY_UID, VERSION), ("variable", STUDY_UID, VERSION)]
    with pytest.raises(ValueError, match="complete collection"):
        read_native_design_class(STUDY_UID, VERSION, page_size=1)


def test_supplier_design_class_and_source_variable_are_captured_with_exact_instance_link():
    source = NativeStudySource()
    report = mapped(source.graph)
    expected = {
        "studyDataSupplier": source.graph["data_suppliers"][0],
        "studyDesignClass": source.graph["design_classes"][0],
        "studySourceVariable": source.graph["source_variables"][0],
    }
    for kind, original in expected.items():
        record = next(row for row in report["nativeRecords"] if row["kind"] == kind)
        assert record["record"] == native_json(original)
        assert any(row["url"].endswith("/" + kind)
                   for row in design(report)["extensionAttributes"])
    instance = next(row for row in design(report)["activities"] if row["biomedicalConceptIds"])
    assert any(row["url"].endswith("/activity-data-supplier")
               for row in instance["extensionAttributes"])


def test_absent_selected_supplier_is_reported_and_never_matched_by_name():
    graph = NativeStudySource().graph
    graph["instances"][0].study_data_supplier_uid = "MissingSupplierSelection"
    report = mapped(graph)
    assert any(row["code"] == "USDM_ACTIVITY_DATA_SUPPLIER_UNRESOLVED"
               for row in report["mappingReport"]["issues"])
    instance = next(row for row in design(report)["activities"] if row["biomedicalConceptIds"])
    assert not any(row["url"].endswith("/activity-data-supplier")
                   for row in instance["extensionAttributes"])
