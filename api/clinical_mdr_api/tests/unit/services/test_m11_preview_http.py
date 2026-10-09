"""Registered M11 HTTP route and real template; native source reads are isolated."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from lxml import html

from clinical_mdr_api.routers.ddf import study_definitions
from clinical_mdr_api.routers.studies.study_access import enforce_visible_study
from clinical_mdr_api.services.utils.table_f import (
    SimpleFootnote,
    TableCell,
    TableRow,
    TableWithFootnotes,
    tables_to_html,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import STUDY_UID, VERSION
from common.auth import rbac
from common.auth.dependencies import security
from common.exception_handlers import register_exception_handlers


@pytest.fixture(scope="module")
def mapped_source():
    source = NativeStudySource()
    with source.isolated():
        return source.mapper().map_with_report(source.graph["study"], VERSION)


def client(monkeypatch, report, allowed=True):
    mapper = Mock(return_value=report)
    flowchart = Mock(
        return_value='<html><table class="soa"><tr><td>Selected SoA</td></tr></table></html>'
    )
    figure = Mock(
        return_value='<svg xmlns="http://www.w3.org/2000/svg"><text>Selected figure</text></svg>'
    )
    monkeypatch.setattr(
        study_definitions,
        "USDMService",
        lambda: SimpleNamespace(get_by_uid_with_report=mapper),
    )
    monkeypatch.setattr(
        study_definitions,
        "StudyFlowchartService",
        lambda: SimpleNamespace(get_study_flowchart_html=flowchart),
    )
    monkeypatch.setattr(
        study_definitions,
        "StudyDesignFigureService",
        lambda **_: SimpleNamespace(get_svg_document=figure),
    )
    app = FastAPI()
    app.include_router(study_definitions.router, prefix="/usdm/v4")
    register_exception_handlers(app, value_error=False)

    async def authenticated():
        return None

    async def visible():
        if not allowed:
            raise HTTPException(403, "Study is outside the authored test scope")

    app.dependency_overrides[security.dependency] = authenticated
    app.dependency_overrides[rbac.STUDY_READ.dependency] = authenticated
    app.dependency_overrides[enforce_visible_study] = visible
    return TestClient(app), mapper, flowchart, figure


def get(api, version=VERSION):
    return api.get(
        f"/usdm/v4/studyDefinitions/{STUDY_UID}/m11",
        params={} if version is None else {"study_value_version": version},
    )


def cell(response, label):
    page = html.fromstring(response.text)
    row = page.xpath("//tr[th[contains(normalize-space(.), $label)]]", label=label)
    assert len(row) == 1
    return " ".join(row[0].xpath("./td")[0].itertext())


def test_actual_mapper_dictionary_renders_selected_native_header_and_all_reader_versions(
    monkeypatch, mapped_source
):
    api, mapper, flowchart, figure = client(monkeypatch, deepcopy(mapped_source))
    response = get(api)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/html")
    assert "Draft protocol preview" in response.text
    assert "not an approved, submission-ready or immutable protocol" in response.text
    assert "LOCKED" in response.text
    assert "OSB-S" in cell(response, "Trial Acronym")
    assert "OSB-SEMANTIC" in cell(response, "Sponsor Protocol Identifier")
    assert "Synthetic native semantics study" in cell(response, "Full Title")
    assert "1.1" in cell(response, "Version Number")
    assert "Not available in the selected source" in cell(response, "Version Date")
    assert "M11_SPONSOR_SOURCE_UNAVAILABLE" in response.text
    assert mapped_source["mappingReport"]["issues"][0]["code"] in response.text
    assert "Selected SoA" in response.text and "Selected figure" in response.text
    mapper.assert_called_once_with(STUDY_UID, study_value_version=VERSION)
    flowchart.assert_called_once_with(
        study_uid=STUDY_UID,
        study_value_version=VERSION,
        layout=study_definitions.SoALayout.DETAILED,
    )
    figure.assert_called_once_with(STUDY_UID, study_value_version=VERSION)


def test_selected_version_and_referenced_protocol_are_not_first_item_fallbacks(
    monkeypatch, mapped_source
):
    report = deepcopy(mapped_source)
    study = report["document"]["study"]
    decoy = deepcopy(study["versions"][0])
    decoy["versionIdentifier"] = "99.0"
    decoy["studyDesigns"][0]["population"]["plannedEnrollmentNumber"]["value"] = 999
    study["versions"].insert(0, decoy)
    decoy_document = deepcopy(study["documentedBy"][0])
    decoy_document["versions"][0].update(
        {"id": "unrelated-document-version", "version": "WRONG-VERSION"}
    )
    study["documentedBy"].insert(0, decoy_document)
    api, *_ = client(monkeypatch, report)
    response = get(api)
    assert response.status_code == 200, response.text
    assert "1.1" in cell(response, "Version Number")
    assert "WRONG-VERSION" not in response.text
    assert "999 participants" not in response.text


def test_current_preview_is_explicit_and_uses_current_for_all_readers(
    monkeypatch, mapped_source
):
    report = deepcopy(mapped_source)
    report["mappingReport"]["studyValueVersion"] = None
    api, mapper, flowchart, figure = client(monkeypatch, report)
    response = get(api, None)
    assert response.status_code == 200
    assert "separate source reads can change" in response.text
    assert mapper.call_args.kwargs["study_value_version"] is None
    assert flowchart.call_args.kwargs["study_value_version"] is None
    assert figure.call_args.kwargs["study_value_version"] is None


def test_actual_flowchart_export_keeps_each_table_footnote_in_order(
    monkeypatch, mapped_source
):
    tables = [
        TableWithFootnotes(
            id=f"soa-{index}",
            rows=[
                TableRow(
                    cells=[TableCell(text=f"Selected visit {index}", footnotes=["a"])]
                )
            ],
            footnotes={
                "a": SimpleFootnote(
                    uid=f"footnote-{index}",
                    text_html="unused",
                    text_plain=f"Clinical instruction {index}: only after fasting <8 hours>",
                )
            },
        )
        for index in (1, 2)
    ]
    api, _, flowchart, _ = client(monkeypatch, deepcopy(mapped_source))
    flowchart.return_value = tables_to_html(tables)
    response = get(api)
    assert response.status_code == 200, response.text
    page = html.fromstring(response.text)
    for index in (1, 2):
        table = page.get_element_by_id(f"soa-{index}")
        footnote = table.getnext()
        assert footnote.tag == "p" and footnote.get("class") == "footnote"
        assert (
            "".join(footnote.itertext())
            == f"aClinical instruction {index}: only after fasting <8 hours>"
        )
        assert table.xpath(".//sup/b/text()") == ["a"]
    assert page.get_element_by_id("soa-1").getnext().getnext().get("id") == "soa-2"


def test_objective_and_endpoint_rich_text_is_sanitized_without_losing_formatting(
    monkeypatch, mapped_source
):
    report = deepcopy(mapped_source)
    rich_text = '<strong>Clinical meaning</strong><script id="unsafe-objective">alert(1)</script><p onclick="alert(2)">Safe paragraph</p>'
    objectives = report["document"]["study"]["versions"][0]["studyDesigns"][0][
        "objectives"
    ]
    objectives[:] = [
        {
            "name": "Safety",
            "description": rich_text,
            "level": {"decode": "Primary Objective"},
            "endpoints": [{"name": "Endpoint", "description": rich_text}],
        }
    ]
    api, *_ = client(monkeypatch, report)
    response = get(api)
    assert response.status_code == 200, response.text
    page = html.fromstring(response.text)
    assert len(page.xpath("//strong[text()='Clinical meaning']")) == 2
    assert len(page.xpath("//p[text()='Safe paragraph']")) == 2
    assert not page.xpath("//script[@id='unsafe-objective']")
    assert not page.xpath("//*[@onclick='alert(2)']")
    assert (
        objectives[0]["description"] == rich_text
    ), "presentation must not mutate retained source evidence"


@pytest.mark.parametrize(
    "changed",
    [
        "report-study",
        "report-version",
        "native-version",
        "mapped-version",
        "duplicate-version",
        "missing-native",
    ],
)
def test_foreign_or_ambiguous_source_is_refused_before_visual_readers(
    monkeypatch, mapped_source, changed
):
    report = deepcopy(mapped_source)
    if changed == "report-study":
        report["mappingReport"]["studyUid"] = "other"
    elif changed == "report-version":
        report["mappingReport"]["studyValueVersion"] = "99.0"
    elif changed == "native-version":
        next(row for row in report["nativeRecords"] if row["kind"] == "study")[
            "record"
        ]["current_metadata"]["version_metadata"]["version_number"] = "99.0"
    elif changed == "mapped-version":
        report["document"]["study"]["versions"][0]["versionIdentifier"] = "99.0"
    elif changed == "duplicate-version":
        report["document"]["study"]["versions"].append(
            deepcopy(report["document"]["study"]["versions"][0])
        )
    else:
        report["nativeRecords"] = []
    api, _, flowchart, figure = client(monkeypatch, report)
    response = get(api)
    assert response.status_code == 422, response.text
    assert response.json()["type"] == "USDMMappingAuthorityRequired"
    flowchart.assert_not_called()
    figure.assert_not_called()


@pytest.mark.parametrize("designs", [[], None, [{}, {}], [{}, None]])
def test_missing_or_ambiguous_design_and_protocol_stay_unknown(
    monkeypatch, mapped_source, designs
):
    report = deepcopy(mapped_source)
    version = report["document"]["study"]["versions"][0]
    version["studyDesigns"] = designs
    version["studyInterventions"] = []
    version["documentVersionIds"] = []
    api, _, flowchart, _ = client(monkeypatch, report)
    flowchart.return_value = "<html>No table available</html>"
    response = get(api)
    assert response.status_code == 200, response.text
    assert "M11_DESIGN_UNRESOLVED" in response.text
    assert "M11_PROTOCOL_VERSION_UNRESOLVED" in response.text
    assert "M11_FLOWCHART_UNAVAILABLE" in response.text
    assert "Not available in the selected source" in cell(response, "Version Number")


def test_registry_identity_is_not_a_sponsor_and_headers_are_escaped(
    monkeypatch, mapped_source
):
    report = deepcopy(mapped_source)
    native = next(row for row in report["nativeRecords"] if row["kind"] == "study")[
        "record"
    ]["current_metadata"]
    native["identification_metadata"]["registry_identifiers"] = {
        "ct_gov_id": "NCT01234567",
        "investigational_new_drug_application_number_ind": "IND-123",
    }
    native["study_description"][
        "study_title"
    ] = '<script id="source-script">alert(1)</script>'
    report["document"]["study"]["versions"][0]["organizations"] = [
        {"id": "fda", "name": "FDA", "legalAddress": {"text": "Registry address"}}
    ]
    api, *_ = client(monkeypatch, report)
    response = get(api)
    assert response.status_code == 200
    sponsor = cell(response, "Sponsor Name and Address")
    assert "Not available in the selected source" in sponsor
    assert "FDA" not in sponsor and "Registry address" not in sponsor
    assert "Novo Nordisk" not in response.text
    assert "ClinicalTrials.gov ID: NCT01234567" in response.text
    assert "IND Number: IND-123" in response.text
    assert "FDA IND Number: NCT01234567" not in response.text
    assert '<script id="source-script">' not in response.text
    assert "&lt;script" in response.text


def test_null_optional_quantities_and_zero_values_render_without_truncation(
    monkeypatch, mapped_source
):
    report = deepcopy(mapped_source)
    design = report["document"]["study"]["versions"][0]["studyDesigns"][0]
    design.update(
        {"studyPhase": None, "scheduleTimelines": [], "objectives": [], "arms": []}
    )
    design["population"] = {
        "includesHealthySubjects": None,
        "plannedAge": {"minValue": {"value": 0, "unit": None}, "maxValue": None},
        "plannedEnrollmentNumber": {"value": 2.5},
    }
    api, *_ = client(monkeypatch, report)
    response = get(api)
    assert response.status_code == 200, response.text
    assert "Minimum: 0 " in response.text
    assert "2.5 participants" in response.text


def test_native_visibility_and_query_validation_remain_on_public_route(
    monkeypatch, mapped_source
):
    api, mapper, *_ = client(monkeypatch, mapped_source, allowed=False)
    assert get(api).status_code == 403
    mapper.assert_not_called()
    api, mapper, *_ = client(monkeypatch, mapped_source)
    # This application's validation handler deliberately uses HTTP 400.
    assert get(api, " ").status_code == 400
    assert get(api, "").status_code == 400
    mapper.assert_not_called()
    operation = api.app.openapi()["paths"][
        f"/usdm/v4/studyDefinitions/{{study_uid}}/m11"
    ]["get"]
    assert any(
        parameter["name"] == "study_value_version"
        for parameter in operation["parameters"]
    )
