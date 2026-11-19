from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from clinical_mdr_api.routers.integrations import edc_export
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.edc_draft_preview import DraftPreviewRequest
from common.exceptions import ForbiddenException, ValidationException


@pytest.fixture
def wire():
    group = {
        "refKey": "G",
        "name": "Measurements",
        "orderNumber": 1,
        "items": [
            {"refKey": "X", "name": "Value", "orderNumber": 1, "datatype": "text", "datatypeHint": "text", "vendorExtensions": {}, "codelistRef": "missing"},
        ],
    }
    return {
        "sourceDigest": "b" * 64,
        "dryRun": True,
        "definition": {
            "schemaVersion": "osb-draft-ecrf/1",
            "platformStudyId": "10000000-0000-0000-0000-000000000001",
            "tenantId": "20000000-0000-0000-0000-000000000002",
            "semanticStudyId": "30000000-0000-0000-0000-000000000003",
            "osbStudyUid": "Study_1",
            "buildHash": "sha256:" + "a" * 64,
            "odm": {"forms": [{"refKey": "F", "name": "Tests", "orderNumber": 1, "vendorExtensions": {}, "itemGroups": [group]}], "units": [], "codelists": []},
            "formVisitMatrix": [{"visitRef": "V", "formRef": "F", "doubleEntry": False, "_fieldVisitScope": [{"fieldRef": "X", "text": "scheduled"}]}],
        },
    }


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setattr(edc_export, "user", lambda: SimpleNamespace(tenant_id="authenticated-platform-tenant"))

    def binding(tenant, platform):
        assert tenant == "authenticated-platform-tenant"
        assert platform == "10000000-0000-0000-0000-000000000001"
        return {"nativeIdentity": "Study_1"}

    monkeypatch.setattr(edc_export, "active_osb_binding", binding)


def _preview(wire):
    return edc_export.preview_edc_draft("Study_1", DraftPreviewRequest.model_validate(wire))


def test_preview_covers_exact_source_membership_and_holds_every_native_row(auth, wire):
    expected = _preview(wire)
    assert expected["bindingStatus"] == "verified"
    assert expected["status"] == "held"
    assert expected["counts"]["created"] == expected["counts"]["reused"] == 0
    assert expected["counts"]["held"] == len(expected["objects"]) == 4
    assert all(o["uid"] is None and o["oid"] is None and o["nativeStatus"] == "held" for o in expected["objects"])
    assert {h["reason"] for h in expected["holds"]} == {"SIGNED_ECRF_SOURCE_STAGE_REQUIRED", "CODELIST_REFERENCE_UNRESOLVED", "NATIVE_ODM_ITEM_LENGTH_UNRESOLVED"}
    wire["dryRun"] = False
    assert _preview(wire) == {**expected, "dryRun": False}
    wire["definition"]["formVisitMatrix"][0]["doubleEntry"] = True
    changed = _preview(wire)
    assert changed["sourceDigest"] == expected["sourceDigest"]
    assert changed["definitionDigest"] != expected["definitionDigest"]
    assert changed["membershipDigest"] != expected["membershipDigest"]
    assert _preview(wire) == changed


def test_duplicate_rows_and_unresolved_assignments_are_visible(auth, wire):
    definition = wire["definition"]
    definition["odm"]["forms"].append(deepcopy(definition["odm"]["forms"][0]))
    definition["formVisitMatrix"][0]["_fieldVisitScope"][0]["fieldRef"] = "unknown"
    result = _preview(wire)
    assert result["counts"]["held"] == 7
    assert {h["reason"] for h in result["holds"]} >= {"SOURCE_REFERENCE_AMBIGUOUS", "FORM_REFERENCE_UNRESOLVED", "FIELD_VISIT_REFERENCE_UNRESOLVED"}


def test_native_path_and_binding_are_independently_checked(auth, wire, monkeypatch):
    with pytest.raises(ValidationException):
        edc_export.preview_edc_draft("Study_2", DraftPreviewRequest.model_validate(wire))
    monkeypatch.setattr(edc_export, "active_osb_binding", lambda *_: {"nativeIdentity": "Study_2"})
    with pytest.raises(ForbiddenException):
        _preview(wire)

    def missing(*_):
        raise OsbCandidateSetError("OSB_NATIVE_IDENTITY_BINDING_REQUIRED", "missing")

    monkeypatch.setattr(edc_export, "active_osb_binding", missing)
    assert _preview(wire)["bindingStatus"] == "EXACT_NATIVE_BINDING_REQUIRED"
    monkeypatch.setattr(edc_export, "user", lambda: SimpleNamespace(tenant_id=""))
    with pytest.raises(ForbiddenException):
        _preview(wire)


def test_only_collection_definition_envelope_is_allowed(auth, wire):
    wire["sourceBundle"] = {}
    with pytest.raises(ValidationError):
        _preview(wire)
    wire.pop("sourceBundle")
    wire["definition"]["odm"]["forms"][0]["vendorExtensions"]["data"] = '{"subjectData":[]}'
    with pytest.raises(ValidationException):
        _preview(wire)
