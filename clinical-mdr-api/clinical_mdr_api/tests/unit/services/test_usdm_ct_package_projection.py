"""Real producer codes use the published system/date, with exact native custody."""

from copy import deepcopy
from datetime import date, timezone
import json
from pathlib import Path

from neo4j.time import Date as Neo4jDate, DateTime as Neo4jDateTime
import pytest

from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    MappingContext, USDMMappingAuthorityRequired, native_json,
)
from clinical_mdr_api.services.integrations.edc_study_exchange import verify_source_exchange
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource, native_odm_graph
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION


AUTHORITY = json.loads(
    (Path(__file__).parents[2] / "fixtures/usdm_cdisc_code_projection_authority.json").read_text()
)


def mapper_for(source, monkeypatch, *, allow_incomplete=False):
    mapper = source.mapper()
    mapper._context = MappingContext(allow_incomplete=allow_incomplete)
    mapper._study_uid = STUDY_UID
    mapper._study_value_version = VERSION
    mapper._study_as_of = AS_OF
    mapper._load_selected_ct_packages(STUDY_UID)
    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", source.query)
    return mapper


def native_type_record(source):
    return next(row for row in source.ct_package_records
                if row["selectedCatalogue"] == "SDTM CT"
                and row["attributes"]["concept_id"] == "C98388")


def retained_type(mapper):
    return next(row["record"] for row in mapper._context.native_records
                if row["kind"] == "ctPackageTermDefinition"
                and row["record"]["attributes"]["concept_id"] == "C98388")


def test_actual_producer_uses_published_cdisc_identity_and_preserves_selected_package():
    source = NativeStudySource()
    odm = native_odm_graph()
    before = source.source_input(odm)
    bundle = source.export(odm)
    study_type = bundle["definition"]["document"]["study"]["versions"][0]["studyDesigns"][0]["studyType"]
    published = AUTHORITY["publishedExamples"][0]["code"]
    native = native_type_record(source)
    assert study_type["codeSystem"] == published["codeSystem"] == "http://www.cdisc.org"
    assert study_type["codeSystemVersion"] == native["publishedPackage"]["effective_date"] == "2025-09-26"
    assert study_type["code"] == AUTHORITY["publishedCtTerm"]["conceptId"] == "C98388"
    assert study_type["decode"] == AUTHORITY["publishedCtTerm"]["preferredTerm"]
    assert source.graph["study"].current_metadata.high_level_study_design.study_type_code.sponsor_preferred_name == "Renamed sponsor display label"
    records = bundle["extensions"]["_osbExport"]["native"]["usdmMappingRecords"]
    retained = next(row["record"] for row in records
                    if row["kind"] == "ctPackageTermDefinition"
                    and row["record"]["attributes"]["concept_id"] == "C98388")
    assert retained["selectedPackage"] == native["selectedPackage"]
    assert retained["selectedPackage"]["uid"] == "SDTM CT 2025-09-26"
    assert retained["attributes"] == native["attributes"]
    assert retained["termValueIdentity"] == native["termValueIdentity"]
    assert study_type["extensionAttributes"]
    assert before == source.source_input(odm)
    assert bundle["extensions"]["_osbExport"]["mappingReport"]["state"] == "incomplete"
    verify_source_exchange(bundle)


def test_exact_concept_attribute_can_resolve_a_native_uid_without_prefix_or_name_guess(monkeypatch):
    source = NativeStudySource()
    native_type_record(source)["termUid"] = "NativeTerm_actual_study_type"
    mapper = mapper_for(source, monkeypatch)
    code = mapper.get_ct_package_term_as_usdm_code("C98388")
    assert code.code == "C98388"
    assert retained_type(mapper)["termUid"] == "NativeTerm_actual_study_type"


def test_sponsor_package_keeps_its_identity_but_uses_unchanged_published_base_release(monkeypatch):
    source = NativeStudySource()
    selection = next(row for row in source.graph["standards"] if row.ct_package.catalogue_name == "SDTM CT")
    old_uid = selection.ct_package.uid
    published_readings = deepcopy(source.ct_package_records)
    sponsor = source.select_sponsor_package("SDTM CT", date(2026, 3, 1))
    assert source.ct_package_records == published_readings
    assert [row["type"] for row in source.ct_package_factory_writes] == [
        "package", "EXTENDS_PACKAGE", "CONTAINS_PACKAGE",
    ]
    assert source.ct_package_extensions[sponsor.uid] == old_uid
    assert sponsor.extends_package == old_uid
    assert selection.ct_package.uid == sponsor.uid
    mapper = mapper_for(source, monkeypatch)
    code = mapper.get_ct_package_term_as_usdm_code("C98388")
    assert code.codeSystemVersion == "2025-09-26"
    retained = retained_type(mapper)
    assert retained["selectedPackage"]["uid"] == sponsor.uid
    assert retained["selectedPackage"]["effective_date"] == "2026-03-01"
    assert retained["publishedPackage"]["uid"] == old_uid
    assert retained["selectionMetadata"] == native_json(sponsor)
    assert [node["uid"] for node in retained["publishedPackagePath"]] == [sponsor.uid, old_uid]


def test_actual_export_from_native_sponsor_package_keeps_inherited_catalogue_and_both_dates():
    source = NativeStudySource()
    odm = native_odm_graph()
    sponsor = source.select_sponsor_package("SDTM CT", date(2026, 3, 1))
    before = source.source_input(odm)
    bundle = source.export(odm)
    code = bundle["definition"]["document"]["study"]["versions"][0]["studyDesigns"][0]["studyType"]
    records = bundle["extensions"]["_osbExport"]["native"]["usdmMappingRecords"]
    retained = next(row["record"] for row in records
                    if row["kind"] == "ctPackageTermDefinition"
                    and row["record"]["attributes"]["concept_id"] == "C98388")
    assert code["code"] == "C98388"
    assert code["codeSystem"] == "http://www.cdisc.org"
    assert code["codeSystemVersion"] == "2025-09-26"
    assert retained["selectedCatalogue"] == retained["publishedCatalogue"] == "SDTM CT"
    assert retained["selectedPackage"]["uid"] == sponsor.uid
    assert retained["selectedPackage"]["effective_date"] == "2026-03-01"
    assert retained["publishedPackage"]["uid"] == sponsor.extends_package
    assert retained["publishedPackage"]["effective_date"] == "2025-09-26"
    assert source.source_input(odm) == before
    assert bundle["extensions"]["_osbExport"]["mappingReport"]["state"] == "incomplete"
    verify_source_exchange(bundle)


def test_native_nested_sponsor_packages_retain_every_exact_ancestor(monkeypatch):
    source = NativeStudySource()
    first = source.select_sponsor_package("SDTM CT", date(2026, 3, 1))
    second = source.select_sponsor_package("SDTM CT", date(2026, 3, 2))
    mapper = mapper_for(source, monkeypatch)
    code = mapper.get_ct_package_term_as_usdm_code("C98388")
    assert code.codeSystemVersion == "2025-09-26"
    retained = retained_type(mapper)
    assert [node["uid"] for node in retained["publishedPackagePath"]] == [
        second.uid, first.uid, first.extends_package,
    ]


def break_sdtm_sponsor_ancestry(source, broken):
    sponsor = source.select_sponsor_package("SDTM CT", date(2026, 3, 1))
    if broken == "absent-link":
        del source.ct_package_extensions[sponsor.uid]
    elif broken == "unavailable-parent":
        source.ct_package_extensions[sponsor.uid] = "Unselected package"
    else:
        source.ct_package_extensions[sponsor.uid] = sponsor.uid
    return sponsor


@pytest.mark.parametrize("broken", ["absent-link", "unavailable-parent", "cycle"])
def test_unresolved_native_ancestry_cannot_fall_back_to_a_current_package(broken):
    source = NativeStudySource()
    # Both pinned catalogues contain this concept. Keep the PROTOCOL library
    # available but unselected, so no other selected package can authorize it.
    assert {row["selectedCatalogue"] for row in source.ct_package_records
            if row["attributes"]["concept_id"] == "C98388"} == {"SDTM CT", "PROTOCOL CT"}
    source.graph["standards"] = [
        row for row in source.graph["standards"]
        if row.ct_package.catalogue_name != "PROTOCOL CT"
    ]
    assert {row.ct_package.catalogue_name for row in source.graph["standards"]} == {
        "DDF CT", "SDTM CT",
    }
    break_sdtm_sponsor_ancestry(source, broken)
    odm = native_odm_graph()
    before = source.source_input(odm)
    bundle = source.export(odm)
    code = bundle["definition"]["document"]["study"]["versions"][0]["studyDesigns"][0].get("studyType")
    assert not code or code.get("code") is None
    report = bundle["extensions"]["_osbExport"]["mappingReport"]
    assert report["state"] == "incomplete"
    assert any(issue["code"] == "USDM_CODE_AUTHORITY_REQUIRED" for issue in report["issues"])
    records = bundle["extensions"]["_osbExport"]["native"]["usdmMappingRecords"]
    assert not [row for row in records
                if row["kind"] == "ctPackageTermDefinition"
                and row["record"]["attributes"].get("concept_id") == "C98388"]
    assert source.source_input(odm) == before
    verify_source_exchange(bundle)


@pytest.mark.parametrize("broken", ["absent-link", "unavailable-parent", "cycle"])
def test_broken_ancestry_keeps_independently_selected_protocol_authority(broken):
    source = NativeStudySource()
    sponsor = break_sdtm_sponsor_ancestry(source, broken)
    selected = next(row.ct_package for row in source.graph["standards"]
                    if row.ct_package.catalogue_name == "PROTOCOL CT")
    native = next(row for row in source.ct_package_records
                  if row["selectedCatalogue"] == "PROTOCOL CT"
                  and row["attributes"]["concept_id"] == "C98388")
    odm = native_odm_graph()
    before = source.source_input(odm)
    bundle = source.export(odm)
    code = bundle["definition"]["document"]["study"]["versions"][0]["studyDesigns"][0]["studyType"]
    assert code["code"] == "C98388"
    assert code["codeSystem"] == "http://www.cdisc.org"
    assert code["codeSystemVersion"] == "2025-09-26"
    assert code["decode"] == native["attributes"]["preferred_term"]
    records = bundle["extensions"]["_osbExport"]["native"]["usdmMappingRecords"]
    retained = [row["record"] for row in records
                if row["kind"] == "ctPackageTermDefinition"
                and row["record"]["attributes"]["concept_id"] == "C98388"]
    assert len(retained) == 1
    evidence = retained[0]
    assert evidence["selectedCatalogue"] == evidence["publishedCatalogue"] == "PROTOCOL CT"
    assert evidence["selectedPackage"] == native["selectedPackage"]
    assert evidence["selectedPackage"]["uid"] == selected.uid == "PROTOCOL CT 2025-09-26"
    assert evidence["selectedPackage"]["uid"] != sponsor.uid
    assert evidence["selectionMetadata"] == native_json(selected)
    assert evidence["publishedPackage"] == native["publishedPackage"]
    assert evidence["publishedPackagePath"] == [native["selectedPackage"]]
    assert evidence["termUid"] == native["termUid"]
    assert evidence["termValueIdentity"] == native["termValueIdentity"]
    assert evidence["attributes"] == native["attributes"]
    assert source.source_input(odm) == before
    assert bundle["extensions"]["_osbExport"]["mappingReport"]["state"] == "incomplete"
    verify_source_exchange(bundle)


def test_native_ancestor_catalogue_cannot_change_the_selected_study_catalogue(monkeypatch):
    source = NativeStudySource()
    sponsor = source.select_sponsor_package("SDTM CT", date(2026, 3, 1))
    source.ct_sponsor_packages[sponsor.uid]["catalogue"] = "DDF CT"
    mapper = mapper_for(source, monkeypatch)
    with pytest.raises(USDMMappingAuthorityRequired, match="USDM_CT_PACKAGE_SOURCE_IDENTITY_MISMATCH"):
        mapper.get_ct_package_term_as_usdm_code("C98388")


@pytest.mark.parametrize("invalid", [
    "library", "no-published-base", "published-catalogue", "published-date",
    "source-date-mismatch", "future-published-date",
])
def test_unproven_published_release_never_uses_library_label_or_package_uid_as_authority(monkeypatch, invalid):
    source = NativeStudySource()
    row = native_type_record(source)
    if invalid == "library":
        row["library"]["name"] = "Sponsor vocabulary"
    elif invalid == "no-published-base":
        row["publishedPackage"] = None
    elif invalid == "published-catalogue":
        row["publishedCatalogue"] = "DDF CT"
    elif invalid == "published-date":
        row["publishedPackage"]["effective_date"] = None
    elif invalid == "source-date-mismatch":
        row["selectedPackage"]["effective_date"] = "2026-03-01"
    else:
        row["publishedPackage"]["effective_date"] = "2026-12-01"
    mapper = mapper_for(source, monkeypatch)
    with pytest.raises(USDMMappingAuthorityRequired, match="USDM_CT_PUBLISHED_PACKAGE_AUTHORITY_REQUIRED"):
        mapper.get_ct_package_term_as_usdm_code("C98388")
    assert retained_type(mapper)["selectedPackage"] == row["selectedPackage"]


def test_conflicting_selected_attribute_values_cannot_be_resolved_by_first_row(monkeypatch):
    source = NativeStudySource()
    conflict = deepcopy(native_type_record(source))
    conflict["termValueIdentity"] += ":conflict"
    conflict["attributes"]["preferred_term"] = "Conflicting governed definition"
    source.ct_package_records.append(conflict)
    mapper = mapper_for(source, monkeypatch)
    with pytest.raises(USDMMappingAuthorityRequired, match="USDM_CT_PACKAGE_TERM_AMBIGUOUS"):
        mapper.get_ct_package_term_as_usdm_code("C98388")


def test_a_cdisc_spelled_root_cannot_silently_change_its_published_concept(monkeypatch):
    source = NativeStudySource()
    native_type_record(source)["attributes"]["concept_id"] = "C16084"
    mapper = mapper_for(source, monkeypatch)
    with pytest.raises(USDMMappingAuthorityRequired, match="USDM_CT_PACKAGE_SOURCE_IDENTITY_MISMATCH"):
        mapper.get_ct_package_term_as_usdm_code("C98388")


@pytest.mark.parametrize("field, target", [("concept_id", "code"), ("preferred_term", "decode")])
def test_missing_published_term_fact_is_a_partial_code_with_an_explicit_draft_issue(monkeypatch, field, target):
    source = NativeStudySource()
    native_type_record(source)["attributes"][field] = None
    mapper = mapper_for(source, monkeypatch, allow_incomplete=True)
    value = native_json(mapper.get_ct_package_term_as_usdm_code("C98388"))
    assert value["codeSystem"] == "http://www.cdisc.org"
    assert value["codeSystemVersion"] == "2025-09-26"
    assert target not in value
    assert any(issue["targetPath"] == "Code/" + target for issue in mapper._context.issues)


def test_native_neo4j_date_and_nanosecond_history_are_retained_without_rounding(monkeypatch):
    source = NativeStudySource()
    mapper = mapper_for(source, monkeypatch)
    stamp = Neo4jDateTime(2025, 9, 26, 0, 0, 0, 123456789, tzinfo=timezone.utc)

    def query(text, parameters):
        rows, columns = source.query(text, parameters)
        for row in rows:
            row[4]["effective_date"] = Neo4jDate(2025, 9, 26)
            row[6]["effective_date"] = Neo4jDate(2025, 9, 26)
            row[8][0]["start_date"] = stamp
        return rows, columns

    monkeypatch.setattr("clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", query)
    code = mapper.get_ct_package_term_as_usdm_code("C98388")
    assert code.codeSystemVersion == "2025-09-26"
    assert retained_type(mapper)["approvedNativeVersions"][0]["start_date"] == str(stamp)
    assert ".123456789" in retained_type(mapper)["approvedNativeVersions"][0]["start_date"]


@pytest.mark.parametrize("state", ["Draft", "future"])
def test_unapproved_or_future_native_term_values_remain_unresolved_in_actual_export(state):
    source = NativeStudySource()
    for row in source.ct_package_records:
        if row["attributes"]["concept_id"] == "C98388":
            if state == "Draft":
                row["approvedNativeVersions"][0]["status"] = "Draft"
            else:
                row["approvedNativeVersions"][0]["start_date"] = "2027-01-01T00:00:00+00:00"
    bundle = source.export(native_odm_graph())
    study_type = bundle["definition"]["document"]["study"]["versions"][0]["studyDesigns"][0]["studyType"]
    assert "code" not in study_type and "codeSystemVersion" not in study_type
    issues = bundle["extensions"]["_osbExport"]["mappingReport"]["issues"]
    assert any(issue["code"] == "USDM_CODE_AUTHORITY_REQUIRED"
               and "/studyType/code" in issue["targetPath"] for issue in issues)
