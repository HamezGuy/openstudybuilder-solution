"""Actual native selections -> mapper -> public HTTP -> real M11 section DOM."""

from copy import deepcopy

import pytest
from lxml import html

from clinical_mdr_api.tests.fixtures.usdm_m11_source import (
    m11_source_graph,
    m11_source_report,
)
from clinical_mdr_api.tests.unit.services.test_m11_preview_http import client, get


@pytest.fixture(scope="module")
def mapped_source():
    return m11_source_report()


def page_for(monkeypatch, report):
    api, *_ = client(monkeypatch, report)
    response = get(api)
    assert response.status_code == 200, response.text
    return html.fromstring(response.text)


def test_native_population_criteria_sections_keep_taxonomy_order_repeats_units_and_unknowns(
    monkeypatch, mapped_source
):
    page = page_for(monkeypatch, deepcopy(mapped_source))
    population = page.get_element_by_id("m11-population-source")
    assert population.get("data-m11-section") == "5.1"
    assert "0 years [Unit_years]" in population.text_content()
    assert "0 days [Unit_days]" in population.text_content()
    assert "Unknown [C17998]" in population.text_content()
    assert "Counts retain their original scope" in population.text_content()
    cohort = population.xpath(".//tr[@data-source-uid='Cohort_1']")[0]
    assert cohort.xpath("./td[2]/text()") == ["0"]
    inclusion = page.get_element_by_id("m11-criteria-inclusion")
    assert inclusion.get("data-m11-section") == "5.2"
    assert inclusion.xpath(".//article/@data-source-uid") == [
        "Criterion_inc_first",
        "Criterion_inc_second",
        "Criterion_template",
    ]
    assert inclusion.text_content().count("Age ≥ 0 years; interval [0, 18)") == 2
    assert "Only a template is selected" in inclusion.text_content()
    assert "Age ≥ [parameter]" not in inclusion.text_content()
    assert "C25532" in inclusion.text_content()
    assert page.get_element_by_id("m11-criteria-exclusion").xpath(
        ".//article/@data-source-uid"
    ) == ["Criterion_exc"]
    assert page.get_element_by_id("m11-criteria-unclassified").xpath(
        ".//article/@data-source-uid"
    ) == ["Criterion_unknown"]
    assert "M11_SECTION_NARRATIVE_SOURCE_UNAVAILABLE" in page.text_content()
    assert "M11_SPONSOR_SOURCE_UNAVAILABLE" in page.text_content()


def test_native_three_arms_multiple_interventions_and_branch_keep_exact_dosing_scope(
    monkeypatch, mapped_source
):
    page = page_for(monkeypatch, deepcopy(mapped_source))
    section = page.get_element_by_id("m11-intervention-source")
    assert len(section.xpath("./article")) == 2
    expected = {
        "Dosing_1": ["Selected arm 1", "Native branch"],
        "Dosing_2": ["Selected arm 2"],
        "Dosing_3": ["Selected arm 3"],
        "Dosing_second": ["Selected arm 2"],
    }
    for uid, names in expected.items():
        administration = section.xpath(".//section[@data-source-uid=$uid]", uid=uid)[0]
        cells = administration.xpath(".//tbody/tr/td[1]")
        assert len(cells) == len(names)
        assert all(
            name in cells[index].text_content() for index, name in enumerate(names)
        )
    zero = section.xpath(
        ".//section[@data-source-uid='Dosing_2']//span[@class='source-dose']/text()"
    )[0]
    assert zero == "0.0 mg [Unit_mg @ 1.0]"
    assert (
        "Not available in the selected source"
        in section.xpath(
            ".//section[@data-source-uid='Dosing_3']//span[@class='source-dose']/text()"
        )[0]
    )
    assert (
        "0.0 mg" in section.text_content()
    )  # Native ingredient strength also keeps zero.
    assert "UNSELECTED" not in section.text_content()
    assert (
        "Native arms: 3; branches: 1"
        in page.get_element_by_id("m11-design-source").text_content()
    )


@pytest.mark.parametrize(
    "broken",
    [
        "element",
        "compound",
        "cell-arm",
        "wrong-existing-arm",
        "product",
        "wrong-existing-product",
        "intervention",
    ],
)
def test_orphan_or_wrong_scope_never_attaches_a_dose_to_another_arm(
    monkeypatch, mapped_source, broken
):
    report = deepcopy(mapped_source)
    version = report["document"]["study"]["versions"][0]
    design = version["studyDesigns"][0]
    intervention = version["studyInterventions"][0]
    dosing = next(
        row
        for row in report["nativeRecords"]
        if row["kind"] == "studyCompoundDosing" and row["uid"] == "Dosing_1"
    )
    if broken == "element":
        dosing["record"]["study_element"]["element_uid"] = "Element_2"
    elif broken == "compound":
        dosing["record"]["study_compound"]["study_compound_uid"] = "Foreign_compound"
    elif broken == "cell-arm":
        for cell in design["studyCells"]:
            cell["armId"] = "Missing_arm"
    elif broken == "wrong-existing-arm":
        for cell in design["studyCells"]:
            cell["armId"] = design["arms"][1]["id"]
    elif broken == "product":
        intervention["administrations"][0]["administrableProductId"] = "Missing_product"
    elif broken == "wrong-existing-product":
        other = deepcopy(version["administrableProducts"][0])
        other["id"] = "Wrong_existing_product"
        other["name"] = "Wrong existing product"
        # This existing object has no selected native identity and cannot inherit
        # assignment authority from merely being present in the same version.
        other["extensionAttributes"] = []
        version["administrableProducts"].append(other)
        intervention["administrations"][0]["administrableProductId"] = other["id"]
    else:
        design["studyInterventionIds"] = ["Missing_intervention"]
    page = page_for(monkeypatch, report)
    administration = page.xpath("//section[@data-source-uid='Dosing_1']")[0]
    if broken in ("product", "wrong-existing-product"):
        assert "M11_PRODUCT_REFERENCE_UNRESOLVED" in page.text_content()
        assert "Assigned product: Not available" in administration.text_content()
        assert "Wrong existing product" not in administration.text_content()
    else:
        assert not administration.xpath(".//tr[@data-arm-id]")
        assert "Unresolved applicability" in administration.text_content()
        assert "M11_INTERVENTION_SCOPE_UNRESOLVED" in page.text_content()
    if broken in ("element", "compound", "intervention"):
        assert "Assigned product: Not available" in administration.text_content()


def test_foreign_selected_native_record_fails_before_visual_readers(
    monkeypatch, mapped_source
):
    report = deepcopy(mapped_source)
    record = next(
        row for row in report["nativeRecords"] if row["kind"] == "studyCriteria"
    )
    record["scope"]["studyValueVersion"] = "99.0"
    api, _, flowchart, figure = client(monkeypatch, report)
    assert get(api).status_code == 422
    flowchart.assert_not_called()
    figure.assert_not_called()


def test_section_content_is_escaped_or_sanitized_and_retained_sources_do_not_change(
    monkeypatch, mapped_source
):
    report = deepcopy(mapped_source)
    version = report["document"]["study"]["versions"][0]
    version["studyInterventions"][0][
        "description"
    ] = '<strong>Selected description</strong><script id="clinical-source-script">alert(1)</script><p onclick="bad()">Source paragraph</p>'
    version["eligibilityCriterionItems"][0][
        "text"
    ] = '<script id="criterion-script">alert(1)</script>'
    before = deepcopy(report)
    page = page_for(monkeypatch, report)
    assert page.xpath("//strong[text()='Selected description']")
    assert not page.xpath(
        "//script[@id='clinical-source-script' or @id='criterion-script']"
    )
    assert not page.xpath("//*[@onclick='bad()']")
    assert report == before


@pytest.mark.parametrize(
    "ambiguous", ["native-arm", "canonical-arm", "criterion", "intervention"]
)
def test_duplicate_source_identities_are_explicit_and_cannot_authorize_scope(
    monkeypatch, mapped_source, ambiguous
):
    report = deepcopy(mapped_source)
    version = report["document"]["study"]["versions"][0]
    design = version["studyDesigns"][0]
    if ambiguous == "native-arm":
        report["nativeRecords"].append(
            deepcopy(
                next(
                    row for row in report["nativeRecords"] if row["kind"] == "studyArm"
                )
            )
        )
    elif ambiguous == "canonical-arm":
        design["arms"].append(deepcopy(design["arms"][0]))
    elif ambiguous == "criterion":
        version["eligibilityCriterionItems"].append(
            deepcopy(version["eligibilityCriterionItems"][0])
        )
    else:
        version["studyInterventions"].append(deepcopy(version["studyInterventions"][0]))
    page = page_for(monkeypatch, report)
    assert "M11_SOURCE_IDENTITY_UNRESOLVED" in page.text_content()
    if ambiguous == "native-arm":
        assert (
            "Native arms: Not available"
            in page.get_element_by_id("m11-design-source").text_content()
        )
    if ambiguous in ("native-arm", "canonical-arm", "intervention"):
        for section in page.xpath("//section[@data-source-uid='Dosing_1']"):
            assert not section.xpath(".//tr[td[contains(., 'Selected arm 1')]]")


def test_strength_ratio_preserves_both_quantities_and_zero_denominator(
    monkeypatch, mapped_source
):
    report = deepcopy(mapped_source)
    version = report["document"]["study"]["versions"][0]
    strength = version["administrableProducts"][0]["ingredients"][0]["substance"][
        "strengths"
    ][0]
    # The current native mapper supplies scalar numerator strengths. A canonical
    # denominator, if supplied, must never disappear in the presentation.
    strength["denominator"] = {
        "value": 0,
        "unit": {
            "standardCode": {
                "code": "mL",
                "decode": "milliliter",
                "codeSystemVersion": "1",
            }
        },
    }
    page = page_for(monkeypatch, report)
    assert (
        "(0.0 mg [Unit_mg @ 1.0]) / (0 milliliter [mL @ 1])"
        in page.get_element_by_id("m11-intervention-source").text_content()
    )


def test_multiple_native_products_remain_visible_without_guessing_assignment(
    monkeypatch,
):
    graph = m11_source_graph()
    selection = graph["compounds"][0]
    second = selection.pharmaceutical_products[0].model_copy(deep=True)
    second.uid = "PharmaceuticalProduct_2"
    selection.pharmaceutical_products.append(second)
    binding = next(
        row
        for row in selection.native_library_bindings
        if row["kind"] == "pharmaceuticalProduct"
    )
    selection.native_library_bindings.append(
        {**binding, "uid": second.uid, "valueIdentity": "Pharma2:selected:value"}
    )
    page = page_for(monkeypatch, m11_source_report(graph))
    intervention = page.xpath("//article[@data-source-uid='CompoundSelection_1']")[0]
    assert intervention.xpath(
        ".//section[@class='source-product']/@data-source-uid"
    ) == ["PharmaceuticalProduct_1", "PharmaceuticalProduct_2"]
    assert "Their presence does not assign a product" in intervention.text_content()
    assert "USDM_ADMINISTRABLE_PRODUCT_ASSIGNMENT_UNRESOLVED" in page.text_content()
    for administration in intervention.xpath(
        ".//section[@class='source-administration']"
    ):
        assert "Assigned product: Not available" in administration.text_content()
        assert "Route: Not available" in administration.text_content()


def test_product_selected_by_other_compound_cannot_be_assigned_by_a_wrong_reference(
    monkeypatch,
):
    graph = m11_source_graph()
    second = graph["compounds"][1]
    second.pharmaceutical_products[0].uid = "OtherCompoundProduct"
    for binding in second.native_library_bindings:
        if binding["kind"] == "pharmaceuticalProduct":
            binding.update(
                uid="OtherCompoundProduct",
                valueIdentity="OtherCompoundProduct:selected:value",
            )
    report = m11_source_report(graph)
    version = report["document"]["study"]["versions"][0]
    first, second = version["studyInterventions"]
    other_product_id = second["administrations"][0]["administrableProductId"]
    assert (
        other_product_id
        and other_product_id != first["administrations"][0]["administrableProductId"]
    )
    first["administrations"][0]["administrableProductId"] = other_product_id
    page = page_for(monkeypatch, report)
    first_dose = page.xpath("//section[@data-source-uid='Dosing_1']")[0]
    assert "Assigned product: Not available" in first_dose.text_content()
    assert "M11_PRODUCT_REFERENCE_UNRESOLVED" in page.text_content()
    second_dose = page.xpath("//section[@data-source-uid='Dosing_second']")[0]
    assert "Assigned product: Not available" not in second_dose.text_content()
