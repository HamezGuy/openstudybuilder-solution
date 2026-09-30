"""OpenStudyBuilder 2.10 ODM item datatypes (CODMDT terms) at the fork boundary."""

from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.integrations import native_capture_mapping
from clinical_mdr_api.services.integrations.native_capture_mapping import (
    NativeCapturePort,
)
from clinical_mdr_api.services.odms import datatypes
from clinical_mdr_api.services.odms.datatypes import (
    odm_datatype_term_uid,
    odm_datatype_term_uids,
    odm_datatype_value,
    odm_item_datatype_cypher,
)
from common.exceptions import BusinessLogicException

TERM = {
    "uid": "C170000",
    "name": "Integer",
    "codelist_uid": "CTCodelist_CODMDT",
    "submission_value": "integer",
}


def test_datatype_value_reads_terms_models_and_legacy_strings():
    assert odm_datatype_value(TERM) == "integer"
    assert odm_datatype_value(SimpleNamespace(**TERM)) == "integer"
    assert odm_datatype_value("text") == "text"
    assert odm_datatype_value(None) is None


def test_term_uids_come_from_the_configured_codmdt_codelist():
    requested = {}

    def find_all_terms_aggregated_result(codelist_submission_value):
        requested["codelist"] = codelist_submission_value
        return [
            SimpleNamespace(ct_codelist_term_vo=SimpleNamespace(submission_value="Integer", term_uid="T_INT")),
            SimpleNamespace(ct_codelist_term_vo=SimpleNamespace(submission_value="text", term_uid="T_TEXT")),
        ], 2

    repos = SimpleNamespace(
        ct_codelist_aggregated_repository=SimpleNamespace(
            find_all_terms_aggregated_result=find_all_terms_aggregated_result
        )
    )
    assert odm_datatype_term_uids(repos) == {"integer": "T_INT", "text": "T_TEXT"}
    assert requested["codelist"] == "CODMDT"


def test_term_uid_resolution_fails_closed():
    uids = {"integer": "T_INT"}
    assert odm_datatype_term_uid(uids, "INTEGER") == "T_INT"
    for missing in ("boolean", "", None):
        with pytest.raises(BusinessLogicException):
            odm_datatype_term_uid(uids, missing)


def test_cypher_reads_the_codelist_submission_value_of_the_named_value():
    expression = odm_item_datatype_cypher("odm_item")
    assert "MATCH (odm_item)-[:HAS_DATA_TYPE]->" in expression
    assert "HAS_SELECTED_CODELIST" in expression
    assert "RETURN datatype_codelist_term.submission_value" in expression
    assert expression.lstrip().startswith("head(COLLECT {")


def test_capture_port_writes_the_term_uid_and_keeps_the_plan_string(monkeypatch):
    lookups = []

    def term_uids(_repos):
        lookups.append(1)
        return {"integer": "T_INT"}

    monkeypatch.setattr(datatypes, "odm_datatype_term_uids", term_uids)
    port = NativeCapturePort()
    body = {"name": "Age", "oid": "I.AGE", "datatype": "integer"}
    native = port._native_body("odm_items", body)  # pylint: disable=protected-access
    assert native == {"name": "Age", "oid": "I.AGE", "datatype_uid": "T_INT"}
    assert body["datatype"] == "integer"
    port._native_body("odm_items", body)  # pylint: disable=protected-access
    assert len(lookups) == 1
    with pytest.raises(BusinessLogicException):
        port._native_body("odm_items", {"datatype": "boolean"})  # pylint: disable=protected-access
    form = {"name": "F", "oid": "F.1"}
    assert port._native_body("odm_forms", form) is form  # pylint: disable=protected-access


def test_capture_port_readback_keeps_the_evidence_string_shape():
    view = NativeCapturePort._native_view  # pylint: disable=protected-access
    assert view("odm_items", {"uid": "OdmItem_1", "datatype": dict(TERM)})["datatype"] == "integer"
    assert view("odm_items", {"uid": "OdmItem_1", "datatype": "text"})["datatype"] == "text"
    form = {"uid": "OdmForm_1", "datatype": dict(TERM)}
    assert view("odm_forms", form)["datatype"] == TERM


def test_capture_mapping_readers_use_the_shared_expression():
    assert native_capture_mapping.odm_item_datatype_cypher is odm_item_datatype_cypher
