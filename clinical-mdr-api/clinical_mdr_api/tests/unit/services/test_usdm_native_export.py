"""Actual isolated OSB producer, not a handwritten USDM/export fixture."""

from copy import deepcopy
import json

import pytest

from clinical_mdr_api.models.odms.vendor_attribute import OdmVendorAttributeRelationModel
from clinical_mdr_api.services.integrations.edc_study_exchange import (
    StudyExchangeError, verify_source_exchange,
)
from clinical_mdr_api.services.integrations.edc_export import EdcExportError
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeOdmReader, NativeStudySource, native_odm_graph
from clinical_mdr_api.tests.fixtures.usdm_native_study import STUDY_UID, VERSION


def test_actual_export_uses_actual_mapper_document_and_keeps_unresolved_source_report():
    source = NativeStudySource()
    odm = native_odm_graph()
    before = source.source_input(odm)
    bundle = source.export(odm)
    with source.isolated():
        actual_mapping = source.mapper().map_with_report(source.graph["study"], VERSION)
    assert bundle["definition"]["document"] == actual_mapping["document"]
    assert source.source_input(odm) == before
    assert bundle["formatVersion"] == "2.0"
    assert bundle["profile"]["mode"] == "draft"
    observed = bundle["extensions"]["_osbExport"]
    assert observed["mappingReport"] == actual_mapping["mappingReport"]
    assert observed["mappingReport"]["state"] == "incomplete"
    assert observed["mappingReport"]["studyUid"] == STUDY_UID
    assert observed["mappingReport"]["studyValueVersion"] == VERSION
    assert observed["native"]["usdmMappingRecords"] == actual_mapping["nativeRecords"]
    assert bundle["definition"]["document"]["study"]["versions"][0]["versionIdentifier"] == VERSION
    assert bundle["definition"]["entityBindings"] == []
    verify_source_exchange(bundle)


def test_native_odm_form_values_and_exact_candidate_identity_survive_without_name_assignment():
    source = NativeStudySource()
    odm = native_odm_graph()
    bundle = source.export(odm)
    form = bundle["execution"]["forms"]["forms"][0]
    assert form["_nativeCandidate"]["nativeOid"] == "NATIVE_LAB"
    assert form["_nativeCandidate"]["nativeUid"] == "OdmForm_lab"
    assert form["_nativeCandidate"]["nativeVersion"] == "1.0"
    assert form["_nativeCandidate"]["requiresVisitAssignmentReview"] is True
    assert [field["type"] for field in form["fields"]] == ["decimal", "date", "text"]
    assert [field["refKey"] for field in form["fields"]] == ["PLATELETS", "COLLECTION_DATE", "SAMPLE_COMMENT"]
    assert form["fields"][0]["unit"] == "10^9/L"
    assert form["fields"][2]["length"] == 512
    assert form["fields"][2]["required"] is False
    assert bundle["execution"]["visitFormAssignments"] == []
    export = bundle["extensions"]["_osbExport"]
    assert not any(row["kind"] == "ambiguous_join" for row in export["census"]["rows"])
    pending = next(row for row in export["census"]["rows"] if row.get("nativeUid") == "OdmForm_lab")
    assert pending["ref"] == form["refKey"]
    assert pending["requiredReview"] == ["form-version", "visit-assignment"]
    assert export["census"]["counts"]["lossy"] > 0
    retained = next(row for row in export["native"]["records"]
                    if row["kind"] == "item" and row["uid"] == "OdmItem_comment")
    assert retained["record"] == odm["items"][2].model_dump(mode="json")
    assert retained["record"]["comment"].endswith("  ")
    assert retained["record"]["aliases"][0]["name"] == "NATIVE_COMMENT"
    # No source/import carrier is fabricated to make the name join authoritative.
    assert export["native"]["sourceSnapshotMetadata"] == {}
    assert not any(row["kind"] == "sourceSnapshotCarrier" for row in export["native"]["records"])


def test_all_exact_reachable_form_versions_survive_without_selecting_current_library_row():
    source = NativeStudySource()
    odm = native_odm_graph()
    second = odm["forms"][0].model_copy(deep=True)
    second.version = "2.0"
    second.name = "Changed native form version"
    odm["forms"].append(second)
    bundle = source.export(odm)
    forms = bundle["execution"]["forms"]["forms"]
    assert len(forms) == 2
    assert {form["_nativeCandidate"]["nativeVersion"] for form in forms} == {"1.0", "2.0"}
    assert len({form["refKey"] for form in forms}) == 2
    assert all(form["_nativeCandidate"]["nativeUid"] == "OdmForm_lab" for form in forms)
    assert ("form_service", "OdmForm_lab", "1.0") in source.calls
    assert ("form_service", "OdmForm_lab", "2.0") in source.calls
    assert all(call[-1] is not None for call in source.calls if call[0] == "form_service")
    assert bundle["execution"]["visitFormAssignments"] == []
    verify_source_exchange(bundle)


def test_matching_native_event_name_cannot_claim_an_unrelated_form():
    source = NativeStudySource()
    odm = native_odm_graph()
    unrelated = odm["forms"][0].model_copy(deep=True)
    unrelated.uid = "OdmForm_foreign"
    unrelated.oid = "FOREIGN"
    unrelated.item_groups = []
    odm["forms"].append(unrelated)
    event = odm["events"][0].model_copy(deep=True)
    event.uid = "OdmStudyEvent_foreign"
    event.forms[0].uid = unrelated.uid
    odm["events"].append(event)
    forms = source.export(odm)["execution"]["forms"]["forms"]
    assert [form["_nativeCandidate"]["nativeUid"] for form in forms] == ["OdmForm_lab"]


def test_missing_exact_candidate_child_version_is_not_replaced_by_latest():
    source = NativeStudySource()
    odm = native_odm_graph()
    odm["groups"][0].items[1].version = None
    with pytest.raises(EdcExportError, match="OSB_NATIVE_ODM_EXACT_VERSION_REQUIRED:item/OdmItem_collection_date"):
        source.export(odm)


def test_historical_vendor_carriers_cannot_supply_native_candidate_field_authority():
    source = NativeStudySource()
    odm = native_odm_graph()
    stale = {
        "refKey": "FOREIGN_FIELD", "unit": "mg", "min": 100,
        "sourceVersion": "999.0", "required": False,
    }
    values = {
        "source": json.dumps(stale), "refKey": "STALE_REF", "fieldType": "text",
        "ext": json.dumps({"calculation": "100", "showWhen": {"field": "FOREIGN_FIELD"}}),
    }
    odm["items"][0].vendor_attributes = [
        OdmVendorAttributeRelationModel(uid="Vendor_" + name, name=name, value=value)
        for name, value in values.items()
    ]
    odm["forms"][0].vendor_attributes = [
        OdmVendorAttributeRelationModel(
            uid="Vendor_form_source", name="source",
            value=json.dumps({"refKey": "FOREIGN_FORM", "fields": [stale]}),
        ),
    ]
    before = source.source_input(odm)
    bundle = source.export(odm)
    form = bundle["execution"]["forms"]["forms"][0]
    field = form["fields"][0]
    assert form["_nativeCandidate"]["nativeVersion"] == "1.0"
    assert form["refKey"] != "FOREIGN_FORM"
    assert (field["refKey"], field["type"], field["unit"], field["required"]) == (
        "PLATELETS", "decimal", "10^9/L", True,
    )
    assert not {"min", "sourceVersion", "calculation", "showWhen"} & field.keys()
    retained = next(row["record"] for row in bundle["extensions"]["_osbExport"]["native"]["records"]
                    if row["kind"] == "item" and row["uid"] == "OdmItem_platelets")
    assert retained["vendor_attributes"] == odm["items"][0].model_dump(mode="json")["vendor_attributes"]
    assert source.source_input(odm) == before
    verify_source_exchange(bundle)


def test_each_candidate_reads_its_exact_group_and_item_version_without_current_mixing():
    source = NativeStudySource()
    odm = native_odm_graph()
    item_v2 = odm["items"][0].model_copy(deep=True)
    item_v2.version = "2.0"
    item_v2.datatype = "text"
    item_v2.unit_definitions = []
    item_v2.length = 200
    odm["items"].append(item_v2)
    group_v2 = odm["groups"][0].model_copy(deep=True)
    group_v2.version = "2.0"
    group_v2.items[0].version = "2.0"
    odm["groups"].append(group_v2)
    form_v2 = odm["forms"][0].model_copy(deep=True)
    form_v2.version = "2.0"
    form_v2.item_groups[0].version = "2.0"
    odm["forms"].append(form_v2)
    odm["activityItemLinks"].append({
        **deepcopy(odm["activityItemLinks"][0]), "odmItemVersion": "2.0",
    })
    bundle = source.export(odm)
    forms = {form["_nativeCandidate"]["nativeVersion"]: form
             for form in bundle["execution"]["forms"]["forms"]}
    assert set(forms) == {"1.0", "2.0"}
    assert forms["1.0"]["fields"][0]["type"] == "decimal"
    assert forms["1.0"]["fields"][0]["unit"] == "10^9/L"
    assert forms["2.0"]["fields"][0]["type"] == "text"
    assert forms["2.0"]["fields"][0]["length"] == 200
    assert "unit" not in forms["2.0"]["fields"][0]
    for version in ("1.0", "2.0"):
        assert ("item_group_service", "OdmItemGroup_lab", version) in source.calls
        assert ("item_service", "OdmItem_platelets", version) in source.calls
    assert bundle["execution"]["visitFormAssignments"] == []
    verify_source_exchange(bundle)


@pytest.mark.parametrize("changed_kind", ["form_service", "item_group_service"])
def test_candidate_full_read_must_still_contain_the_witnessed_native_path(monkeypatch, changed_kind):
    read = NativeOdmReader.get_by_uid

    def changed_read(self, uid, version=None):
        row = read(self, uid, version)
        if self.name == changed_kind:
            row = row.model_copy(deep=True)
            if changed_kind == "form_service":
                row.item_groups = []
            else:
                row.items = row.items[1:]
        return row

    monkeypatch.setattr(NativeOdmReader, "get_by_uid", changed_read)
    with pytest.raises(EdcExportError, match="OSB_NATIVE_ODM_CLOSURE_MISMATCH"):
        NativeStudySource().export(native_odm_graph())


def test_candidate_version_reader_cannot_return_a_different_version(monkeypatch):
    read = NativeOdmReader.get_by_uid

    def changed_read(self, uid, version=None):
        row = read(self, uid, version)
        if self.name == "item_service":
            row = row.model_copy(deep=True)
            row.version = "99.0"
        return row

    monkeypatch.setattr(NativeOdmReader, "get_by_uid", changed_read)
    with pytest.raises(EdcExportError, match="OSB_NATIVE_ODM_VERSION_MISMATCH"):
        NativeStudySource().export(native_odm_graph())


def test_native_candidate_with_multiple_units_keeps_all_choices_for_review():
    source = NativeStudySource()
    alternative = source.graph["unit_definitions"][0].model_copy(deep=True)
    alternative.uid = "Unit_alternative"
    alternative.name = "10^3/uL"
    source.graph["unit_definitions"].append(alternative)
    odm = native_odm_graph()
    reference = odm["items"][0].unit_definitions[0].model_copy(deep=True)
    reference.uid = alternative.uid
    reference.name = alternative.name
    odm["items"][0].unit_definitions.append(reference)
    bundle = source.export(odm)
    form = bundle["execution"]["forms"]["forms"][0]
    assert len(form["fields"]) == 3
    assert "unit" not in form["fields"][0]
    export = bundle["extensions"]["_osbExport"]
    pending = next(row for row in export["census"]["rows"]
                   if row.get("ref") == f"{form['refKey']}/PLATELETS/unit")
    assert [unit["uid"] for unit in pending["nativeValues"]] == ["Unit_1", "Unit_alternative"]
    assert {row["uid"] for row in export["native"]["records"] if row["kind"] == "unitDefinition"} == {
        "Unit_1", "Unit_alternative",
    }
    verify_source_exchange(bundle)


def test_pinned_primary_terms_and_exact_selected_instance_are_used_in_producer():
    source = NativeStudySource()
    source.graph["instances"][0].latest_activity_instance.name = "CHANGED UNSELECTED LATEST"
    source.graph["footnotes"][0].latest_footnote.name = "CHANGED UNSELECTED FOOTNOTE"
    bundle = source.export(native_odm_graph())
    version = bundle["definition"]["document"]["study"]["versions"][0]
    title = next(row for row in version["titles"] if row["type"]["code"] == "C207646")
    assert title["text"] == "OSB-S"
    assert title["type"]["decode"] == "Study Acronym"
    assert version["studyDesigns"][0]["arms"][0]["type"]["code"] == "C174266"
    assert version["studyDesigns"][0]["arms"][0]["type"]["codeSystem"] == "http://www.cdisc.org"
    assert version["studyDesigns"][0]["arms"][0]["type"]["codeSystemVersion"] == "2025-09-26"
    source_terms = bundle["extensions"]["_osbExport"]["native"]["usdmMappingRecords"]
    arm_term = next(row["record"] for row in source_terms
                    if row["kind"] == "ctPackageTermDefinition"
                    and row["record"]["attributes"]["concept_id"] == "C174266")
    assert arm_term["selectedPackage"]["uid"] == "PROTOCOL CT 2025-09-26"
    assert version["biomedicalConcepts"][0]["name"] == "Platelets at selected version"
    assert len(version["biomedicalConcepts"][0]["properties"]) == 1
    assert version["notes"][0]["text"] == "Use <i>local</i> sample handling."
    assert ("definition", "LibraryInstance_1", "1.0") in source.calls


def test_fixed_export_clock_and_fresh_native_readers_produce_identical_custody_bytes():
    first = NativeStudySource().export(native_odm_graph())
    second = NativeStudySource().export(native_odm_graph())
    assert first == second
    changed = deepcopy(second)
    changed["definition"]["document"]["study"]["label"] = "Unreviewed changed label"
    with pytest.raises(StudyExchangeError, match="EDC_SOURCE_MAPPED_VALUE:/study/label"):
        verify_source_exchange(changed)
