"""Selected native graph -> real repository factory -> DTO -> USDM regressions."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_codelist_name_repository import CTCodelistNameRepository
from clinical_mdr_api.domain_repositories.study_selections.study_compound_repository import StudySelectionCompoundRepository
from clinical_mdr_api.services.ddf.usdm_mapping_context import USDMMappingAuthorityRequired
from clinical_mdr_api.services.studies.study_compound_dosing_selection import StudyCompoundDosingSelectionService
from clinical_mdr_api.services.studies.study_compound_snapshot import StudyCompoundSnapshotReader, StudyCompoundSourceError
from clinical_mdr_api.tests.fixtures.usdm_native_compound import NativeCompoundSource, Relations
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource, native_odm_graph
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION, native_study_graph
from common.exceptions import NotFoundException, ValidationException


def source_and_graph():
    graph = native_study_graph()
    source = NativeCompoundSource(STUDY_UID, VERSION, AS_OF)
    return source, graph


def graph_with_compounds():
    source, graph = source_and_graph()
    graph["compounds"], graph["dosings"] = source.response_models(graph["study"], graph["elements"][0])
    return source, graph


def map_graph(graph):
    source = NativeStudySource(graph)
    with source.isolated():
        return source.mapper().map_with_report(graph["study"], VERSION)


def test_actual_service_factories_keep_selected_values_and_full_historical_children():
    source, graph = graph_with_compounds()
    compound = graph["compounds"][0]
    assert compound.study_version == VERSION
    assert compound.compound.name == "Synthetic compound"
    assert compound.compound_alias.name == "Selected alias"
    assert compound.compound_alias.version == "1.0"
    assert compound.medicinal_product.name == "Selected medicinal product"
    assert compound.medicinal_product.version == "1.0"
    assert compound.medicinal_product.dose_values[0].value == 5.5
    product = compound.pharmaceutical_products[0]
    assert (product.uid, product.version, product.external_id) == ("PharmaceuticalProduct_1", "1.0", "SYN-PHARM")
    ingredient = product.formulations[0].ingredients[0]
    assert ingredient.formulation_name == ""
    assert ingredient.strength.value == 0
    assert ingredient.strength.unit_label == "mg"
    assert ingredient.half_life.value == 8
    assert ingredient.lag_times[0].value == 0
    assert ingredient.lag_times[0].sdtm_domain_label == "Synthetic exposure domain"
    assert ingredient.active_substance.unii.substance_unii == "SYN-UNII"
    assert ingredient.active_substance.unii.pclass_id == "SYN-PC"
    dosing = graph["dosings"][0]
    assert dosing.study_version == VERSION
    assert dosing.study_compound.compound_alias.version == "1.0"
    assert dosing.dose_value.value == 5.5 and dosing.dose_value.unit_label == "mg"
    bindings = compound.native_library_bindings
    assert all(row["version"] == "1.0" for row in bindings if "version" in row)
    assert {row["kind"] for row in bindings} >= {
        "compound", "compoundAlias", "medicinalProduct", "pharmaceuticalProduct",
        "activeSubstance", "numericValueWithUnit", "unitDefinition", "lagTime",
        "dictionarySubstance", "dictionaryTerm", "ctTermName",
    }
    unit = next(row for row in bindings if row["kind"] == "unitDefinition" and row["uid"] == "Unit_mg")
    assert unit["definition"]["use_molecular_weight"] is False
    assert unit["definition"]["conversion_factor_to_master"] is None
    assert unit["definition"]["order"] == 0 and unit["definition"]["comment"] == ""
    serialized = json.dumps(compound.model_dump(mode="json"))
    assert "UNSELECTED" not in serialized
    assert source.calls and all(row[2] == VERSION for row in source.calls)


def test_direct_value_selection_is_distinct_from_the_value_effective_at_the_snapshot():
    source, graph = source_and_graph()
    with source.isolated(graph["study"]):
        current_at_date = StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION, as_of=AS_OF)
        assert current_at_date.read("compoundAlias", "Alias_1").item_metadata.version == "2.0"
        selected = StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION, as_of=AS_OF)
        _, alias, _, _ = selected.selection_models(source.selection)
        assert alias.version == "1.0"
        assert next(row for row in selected.bindings if row["kind"] == "compoundAlias")["mode"] == "selected-value"


@pytest.mark.parametrize("mutation", ["missing", "ambiguous", "future", "draft"])
def test_unresolved_native_library_scope_never_falls_back_to_latest(mutation):
    source, graph = source_and_graph()
    root = source.roots["unitDefinition", "Unit_mg"]
    value, relation = root.has_version.rows[0]
    if mutation == "missing":
        root.has_version.rows = []
        root.has_version.nodes = []
    elif mutation == "ambiguous":
        root.has_version.rows[1][1].start_date = AS_OF - timedelta(days=1)
        relation.end_date = None
    elif mutation == "future":
        relation.start_date = AS_OF + timedelta(days=1)
    else:
        relation.status = "Draft"
    with source.isolated(graph["study"]), pytest.raises(StudyCompoundSourceError, match="SOURCE_UNRESOLVED"):
        StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION, as_of=AS_OF).read("unitDefinition", "Unit_mg")


def test_selected_value_version_ambiguity_is_reported_instead_of_selecting_highest():
    source, graph = source_and_graph()
    root = source.roots["compoundAlias", "Alias_1"]
    value, relation = root.has_version.rows[0]
    root.has_version.rows.append((value, SimpleNamespace(
        **{**vars(relation), "version": "3.0", "start_date": AS_OF - timedelta(hours=1)}
    )))
    with source.isolated(graph["study"]), pytest.raises(StudyCompoundSourceError, match="SOURCE_UNRESOLVED"):
        StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION, as_of=AS_OF).selection_models(source.selection)


def test_reader_validates_the_exact_requested_study_version_and_selection_scope():
    source, graph = source_and_graph()
    graph["study"].current_metadata.version_metadata.version_number = Decimal("9.0")
    with source.isolated(graph["study"]), pytest.raises(StudyCompoundSourceError, match="VERSION_MISMATCH"):
        StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION)
    reader = StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION, as_of=AS_OF)
    with pytest.raises(StudyCompoundSourceError, match="SELECTION_STUDY_MISMATCH"):
        reader.selection_models(replace(source.selection, study_uid="OtherStudy"))


def test_reader_accepts_the_actual_native_decimal_version_without_changing_query_scope():
    source, graph = source_and_graph()
    metadata = graph["study"].current_metadata.version_metadata
    assert isinstance(metadata.version_number, Decimal)
    assert metadata.version_number == Decimal(VERSION)
    with source.isolated(graph["study"]):
        reader = StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION)
        reader.selection_models(source.selection)
    assert reader.study_value_version == VERSION
    assert source.calls == [("selection", STUDY_UID, VERSION, None, None)]


@pytest.mark.parametrize("version", ["NaN", "Infinity", "not-a-native-version"])
def test_invalid_native_version_fails_before_any_source_query(version):
    with pytest.raises(StudyCompoundSourceError, match="STUDY_VERSION_INVALID"):
        StudyCompoundSnapshotReader(None, STUDY_UID, version)


def test_reader_rejects_a_foreign_study_with_the_same_version_number():
    source, graph = source_and_graph()
    graph["study"].uid = "OtherStudy"
    with source.isolated(graph["study"]), pytest.raises(StudyCompoundSourceError, match="STUDY_IDENTITY_MISMATCH"):
        StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION)


@pytest.mark.parametrize("study_uid,version", [("", VERSION), (STUDY_UID, ""), (STUDY_UID, " ")])
def test_reader_rejects_empty_scope_before_any_native_query(study_uid, version):
    with pytest.raises(StudyCompoundSourceError, match="REQUIRED"):
        StudyCompoundSnapshotReader(None, study_uid, version)


def test_selected_revision_dosing_failure_cannot_use_a_deleted_current_fallback():
    service = object.__new__(StudyCompoundDosingSelectionService)
    def missing(**kwargs):
        assert kwargs["study_value_version"] == VERSION
        raise NotFoundException(msg="No compound in this exact study version")
    service._repos = SimpleNamespace(study_compound_repository=SimpleNamespace(
        find_by_uid=missing,
        find_by_uid_and_dosing_uid=lambda **_: pytest.fail("Selected snapshots cannot use history as fallback."),
    ))
    with pytest.raises(NotFoundException, match="exact study version"):
        service._transform_study_compound_model(
            STUDY_UID, "CompoundSelection_1", "Compound_1", "Alias_1", "MedicinalProduct_1",
            "DosingSelection_1", AS_OF, VERSION,
        )


def test_historical_dosing_uses_its_own_audit_scope_even_if_a_current_selection_exists():
    source, graph = source_and_graph()
    source.repos.study_compound_repository.find_by_uid = lambda **_: pytest.fail("History cannot read the current selection.")
    service = object.__new__(StudyCompoundDosingSelectionService)
    service._repos = source.repos
    with source.isolated(graph["study"]):
        result = service._transform_study_compound_model(
            STUDY_UID, "CompoundSelection_1", "Compound_1", "Alias_1", "MedicinalProduct_1",
            "DosingSelection_1", None, history_date=source.selection_date,
        )
    assert result.compound_alias.name == "Selected alias"
    assert source.calls == [("selection", STUDY_UID, None, "DosingSelection_1", source.selection_date)]
    assert {row["asOf"] for row in result.native_library_bindings} == {source.selection_date.isoformat()}


def test_unit_term_labels_use_scoped_native_name_history_inside_the_real_unit_factory():
    source, graph = source_and_graph()
    value = source.roots["unitDefinition", "Unit_mg"].has_version.rows[0][0]
    value.has_ct_unit = Relations(SimpleNamespace(has_selected_term=Relations(source.public_terms["Domain_1"])))
    with source.isolated(graph["study"]):
        reader = StudyCompoundSnapshotReader(source.repos, STUDY_UID, VERSION, as_of=AS_OF)
        unit = reader.read("unitDefinition", "Unit_mg")
    assert unit.concept_vo.ct_units[0].name == "Synthetic exposure domain"
    assert any(row["kind"] == "ctTermName" and row["version"] == "1.0" for row in reader.bindings)


def test_full_native_intervention_reaches_actual_export_as_a_truthful_partial_canonical_product():
    _, graph = graph_with_compounds()
    source = NativeStudySource(graph)
    bundle = source.export(native_odm_graph())
    version = bundle["definition"]["document"]["study"]["versions"][0]
    product = version["administrableProducts"][0]
    ingredient = product["ingredients"][0]
    assert "productDesignation" not in product and "administrableDoseForm" not in product
    assert "role" not in ingredient
    assert ingredient["substance"]["codes"][0]["code"] == "SYN-UNII"
    strength = ingredient["substance"]["strengths"][0]["numerator"]
    assert strength["value"] == 0
    assert strength["unit"]["standardCode"]["codeSystemVersion"] == "1.0"
    administration = version["studyInterventions"][0]["administrations"][0]
    assert administration["administrableProductId"] == product["id"]
    assert administration["dose"]["value"] == 5.5
    assert administration["route"] is None
    report = bundle["extensions"]["_osbExport"]["mappingReport"]
    assert report["state"] == "incomplete"
    assert {"AdministrableProduct/productDesignation", "AdministrableProduct/administrableDoseForm", "Ingredient/role"} <= {
        row["targetPath"] for row in report["issues"]
    }
    retained = bundle["extensions"]["_osbExport"]["native"]["usdmMappingRecords"]
    native = next(row["record"] for row in retained if row["kind"] == "pharmaceuticalProductDefinition")
    assert native == graph["compounds"][0].pharmaceutical_products[0].model_dump(mode="json")


def test_catalog_product_without_a_direct_selection_is_not_assigned_to_administration():
    _, graph = graph_with_compounds()
    graph["compounds"][0].native_library_bindings = [
        row for row in graph["compounds"][0].native_library_bindings if row["kind"] != "pharmaceuticalProduct"
    ]
    report = map_graph(graph)
    version = report["document"]["study"]["versions"][0]
    assert len(version["administrableProducts"]) == 1
    assert version["studyInterventions"][0]["administrations"][0]["administrableProductId"] is None
    assert any(row["code"] == "USDM_ADMINISTRABLE_PRODUCT_ASSIGNMENT_UNRESOLVED"
               for row in report["mappingReport"]["issues"])


def test_multiple_exact_products_remain_candidates_without_choosing_first_or_matching_name():
    _, graph = graph_with_compounds()
    selection = graph["compounds"][0]
    second = selection.pharmaceutical_products[0].model_copy(deep=True)
    second.uid = "PharmaceuticalProduct_2"
    selection.pharmaceutical_products.append(second)
    binding = next(row for row in selection.native_library_bindings if row["kind"] == "pharmaceuticalProduct")
    selection.native_library_bindings.append({**binding, "uid": second.uid, "valueIdentity": "Pharma2:selected:value"})
    report = map_graph(graph)
    version = report["document"]["study"]["versions"][0]
    assert len(version["administrableProducts"]) == 2
    assert version["studyInterventions"][0]["administrations"][0]["administrableProductId"] is None
    assert any(row["code"] == "USDM_ADMINISTRABLE_PRODUCT_ASSIGNMENT_UNRESOLVED"
               for row in report["mappingReport"]["issues"])


def test_one_resolved_and_one_missing_selected_product_does_not_partially_assign_a_dose():
    _, graph = graph_with_compounds()
    selection = graph["compounds"][0]
    binding = next(row for row in selection.native_library_bindings if row["kind"] == "pharmaceuticalProduct")
    selection.native_library_bindings.append({
        **binding, "uid": "MissingProduct", "valueIdentity": "MissingProduct:selected:value",
    })
    report = map_graph(graph)
    version = report["document"]["study"]["versions"][0]
    assert len(version["administrableProducts"]) == 1
    assert version["studyInterventions"][0]["administrations"][0]["administrableProductId"] is None
    assert {"USDM_PRODUCT_SELECTED_DEFINITION_MISSING", "USDM_ADMINISTRABLE_PRODUCT_ASSIGNMENT_UNRESOLVED"} <= {
        row["code"] for row in report["mappingReport"]["issues"]
    }


def test_conflicting_full_product_values_with_one_identity_fail_closed():
    _, graph = graph_with_compounds()
    conflict = graph["compounds"][0].pharmaceutical_products[0].model_copy(deep=True)
    conflict.formulations[0].ingredients[0].strength.value = 99
    graph["compounds"][0].pharmaceutical_products.append(conflict)
    with pytest.raises(USDMMappingAuthorityRequired, match="USDM_PRODUCT_SOURCE_CONFLICT"):
        map_graph(graph)


def test_compact_reference_is_retained_but_never_claimed_as_full_product_mapping():
    _, graph = graph_with_compounds()
    graph["compounds"][0].pharmaceutical_products = []
    report = map_graph(graph)
    assert report["document"]["study"]["versions"][0]["administrableProducts"] == []
    assert any(row["code"] == "USDM_PRODUCT_DEFINITION_UNRESOLVED"
               for row in report["mappingReport"]["issues"])


def test_scoped_repository_query_preserves_distinct_selected_value_identities(monkeypatch):
    calls = []
    row = {
        "scope_identity": "StudyValue:2.0", "selection_identity": "CompoundSelection:1",
        "aliases": [{"kind": "compoundAlias", "uid": "Alias_1", "valueIdentity": "AliasValue:1"}] * 2,
        "products": [],
        "pharmaceuticals": [
            {"kind": "pharmaceuticalProduct", "uid": "Pharma_1", "valueIdentity": "PharmaValue:1"},
            {"kind": "pharmaceuticalProduct", "uid": "Pharma_1", "valueIdentity": "PharmaValue:2"},
        ],
    }
    def query(text, parameters):
        assert "LATEST" not in text
        assert "HAS_VERSION {version: $study_value_version}" in text
        calls.append(parameters)
        return [list(row.values())], list(row)
    monkeypatch.setattr("clinical_mdr_api.domain_repositories.study_selections.study_compound_repository.db.cypher_query", query)
    result = StudySelectionCompoundRepository().get_selected_library_references(
        STUDY_UID, "CompoundSelection_1", VERSION,
    )
    assert len(result) == 3
    assert {item["valueIdentity"] for item in result} == {"AliasValue:1", "PharmaValue:1", "PharmaValue:2"}
    assert calls[0]["study_value_version"] == VERSION and calls[0]["study_uid"] == STUDY_UID


@pytest.mark.parametrize("mode", ["missing", "duplicate", "missing-name"])
def test_strict_dated_codelist_read_cannot_fall_back_or_select_first(monkeypatch, mode):
    calls = []
    row = dict(
        term_uid="Term_1", term_name="Selected name", preferred_term="Term",
        submission_value="SOURCE", order=0, codelist_name="Selected codelist",
        codelist_uid="Codelist_1", codelist_submission_value="SOURCE-CL",
        membership_identity="membership:1", codelist_attributes_identity="cl-attrs:1",
        codelist_name_identity="cl-name:1", term_name_identity="name:1", term_attributes_identity="attrs:1",
    )
    if mode == "missing-name":
        row["term_name"] = None
    rows = [] if mode == "missing" else [row]
    if mode == "duplicate":
        rows.append({**row, "membership_identity": "membership:2"})
    def query(text, parameters):
        calls.append(parameters)
        assert "LATEST" not in text
        assert "term_attributes_hv.start_date <= datetime($at_specific_date)" in text
        return [list(item.values()) for item in rows], list(row)
    monkeypatch.setattr("clinical_mdr_api.domain_repositories.controlled_terminologies.ct_codelist_generic_repository.db.cypher_query", query)
    repository = object.__new__(CTCodelistNameRepository)
    repository.cache_store_term_by_uid_and_submval.clear()
    with pytest.raises(ValidationException, match="STUDY_LIBRARY_CT_SNAPSHOT"):
        repository.get_codelist_term_by_uid_and_submval(
            "Term_1", "SOURCE-CL", AS_OF, strict_snapshot=True,
        )
    assert len(calls) == 1
