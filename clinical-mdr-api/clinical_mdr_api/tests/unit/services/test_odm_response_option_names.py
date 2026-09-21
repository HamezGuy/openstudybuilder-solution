"""Source-defined response options must survive a missing optional NCI name."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from clinical_mdr_api.services.odms.data_extractor import OdmDataExtractor


def test_sponsor_and_nci_options_are_retained_without_inventing_nci_names():
    source = [
        {"term_uid": "Term_3", "name": "Very good", "nci_preferred_name": None,
         "submission_value": "2", "codelist_uid": "Options"},
        {"term_uid": "Term_1", "name": "Excellent", "nci_preferred_name": None,
         "submission_value": "1", "codelist_uid": "Options"},
        {"term_uid": "C2", "name": "Native label", "nci_preferred_name": "Good",
         "submission_value": "3", "codelist_uid": "Options"},
        {"term_uid": "Term_4", "name": None, "nci_preferred_name": None,
         "submission_value": "4", "codelist_uid": "Options"},
    ]
    original = deepcopy(source)
    extractor = OdmDataExtractor.__new__(OdmDataExtractor)
    lookup = Mock(return_value=source)
    extractor.ct_term_attributes_service = SimpleNamespace(
        get_term_name_and_attributes_by_codelist_uids=lookup
    )
    extractor.set_terms_of_codelists([SimpleNamespace(codelist_uid="Options")])
    lookup.assert_called_once_with(["Options"])
    assert source == original
    assert {row["term_uid"]: row for row in extractor.ct_terms} == {
        row["term_uid"]: row for row in original
    }
    assert [row["term_uid"] for row in extractor.ct_terms] == [
        "Term_1", "C2", "Term_4", "Term_3"
    ]


def test_missing_nci_names_in_every_option_do_not_fail_the_form_export():
    source = [
        {"term_uid": "Term_no", "name": "No", "nci_preferred_name": None,
         "submission_value": "0", "codelist_uid": "YesNo"},
        {"term_uid": "Term_yes", "name": "Yes", "nci_preferred_name": None,
         "submission_value": "1", "codelist_uid": "YesNo"},
    ]
    extractor = OdmDataExtractor.__new__(OdmDataExtractor)
    extractor.ct_term_attributes_service = SimpleNamespace(
        get_term_name_and_attributes_by_codelist_uids=Mock(return_value=source)
    )
    extractor.set_terms_of_codelists([SimpleNamespace(codelist_uid="YesNo")])
    assert extractor.ct_terms == source
    assert all(row["nci_preferred_name"] is None for row in extractor.ct_terms)


def test_report_endpoint_renders_the_extracted_form_data(monkeypatch):
    from starlette.requests import Request

    from clinical_mdr_api.domains.odms.utils import TargetType
    from clinical_mdr_api.routers.odms import metadata

    extracted = SimpleNamespace(odm_forms=[], odm_item_groups=[], odm_items=[])
    constructor = Mock(return_value=extracted)
    monkeypatch.setattr(metadata, "OdmDataExtractor", constructor)
    request = Request({"type": "http", "method": "POST", "path": "/odms/metadata/report",
                       "headers": [], "query_string": b"", "scheme": "https",
                       "server": ("test", 443), "client": ("127.0.0.1", 1)})
    response = metadata.get_odm_report(request, TargetType.FORM, ["Form_1,0.1"])
    constructor.assert_called_once_with(TargetType.FORM, ["Form_1,0.1"])
    assert response.status_code == 200
    assert b"Case Report Form" in response.body


def test_empty_source_form_is_explicit_in_the_rendered_report(monkeypatch):
    from starlette.requests import Request

    from clinical_mdr_api.domains.odms.utils import TargetType
    from clinical_mdr_api.routers.odms import metadata

    form = SimpleNamespace(uid="Form_1", name="Source form shell", oid="FORM.SHELL",
                           version="0.1", translated_texts=[], aliases=[], item_groups=[])
    extracted = SimpleNamespace(odm_forms=[form], odm_item_groups=[], odm_items=[])
    monkeypatch.setattr(metadata, "OdmDataExtractor", Mock(return_value=extracted))
    request = Request({"type": "http", "method": "POST", "path": "/odms/metadata/report",
                       "headers": [], "query_string": b"", "scheme": "https",
                       "server": ("test", 443), "client": ("127.0.0.1", 1)})
    response = metadata.get_odm_report(request, TargetType.FORM, ["Form_1,0.1"])
    assert response.status_code == 200
    assert b"Source form shell" in response.body
    assert b"No questions are configured for this form version." in response.body
