"""Native metadata must survive the narrower editable EDC projection."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.integrations.edc_export import (
    EdcExportError,
    EdcExportService,
    _source_study_id,
)


def test_real_study_response_resolves_source_identity_from_identification_metadata():
    assert (
        _source_study_id(
            {
                "current_metadata": {
                    "identification_metadata": {
                        "description": "Imported from 360i study actt-source (build sha256:ffefd)",
                    }
                }
            }
        )
        == "actt-source"
    )


def test_native_study_does_not_absorb_an_unrelated_source_bundle_carrier():
    exporter = service()
    exporter.form_service = SimpleNamespace(
        get_all_odms=lambda page_size: [
            {
                "uid": "ForeignForm",
                "oid": "FOREIGN",
                "item_groups": [],
                "vendor_attributes": [
                    {"name": "studyId", "value": "foreign-study"},
                    {"name": "bundleMeta", "value": '{"study":{"name":"FOREIGN"}}'},
                ],
            }
        ]
    )
    assert exporter._forms(set(), set())[0] == []
    assert exporter.source_bundle_meta == {}


def test_shared_visit_keys_cannot_choose_between_two_unidentified_source_studies():
    exporter = service()
    exporter.study_event_service = SimpleNamespace(
        get_all_odms=lambda page_size: [
            {"oid": "SE.360I.actt.V1", "forms": [{"uid": "ACTT_FORM"}]},
            {"oid": "SE.360I.surpass.V1", "forms": [{"uid": "SURPASS_FORM"}]},
        ]
    )
    with pytest.raises(EdcExportError, match="AMBIGUOUS_SOURCE_STUDY"):
        exporter._study_event_form_uids({}, [{"refKey": "V1"}])


def test_explicit_source_scope_never_uses_unstamped_name_only_events():
    exporter = service()
    exporter.study_event_service = SimpleNamespace(
        get_all_odms=lambda page_size: [
            {
                "oid": "UNSTAMPED",
                "name": "Baseline",
                "forms": [{"uid": "FOREIGN_FORM"}],
            },
            {"oid": "SE.360I.actt.V1", "forms": [{"uid": "ACTT_FORM"}]},
        ]
    )
    assert exporter._study_event_form_uids(
        {"baseline": "V1"}, [{"refKey": "V1"}], "actt"
    ) == ({"ACTT_FORM"}, {"actt"})


def test_one_foreign_stamped_event_does_not_establish_unbound_study_ownership():
    exporter = service()
    exporter.study_event_service = SimpleNamespace(
        get_all_odms=lambda **kwargs: [
            {"oid": "SE.360I.foreign.V_SCREEN", "forms": [{"uid": "Foreign_Form"}]}
        ]
    )
    with pytest.raises(EdcExportError, match="AMBIGUOUS_SOURCE_STUDY"):
        exporter._study_event_form_uids(
            {"screening": "V_SCREEN"}, [{"refKey": "V_SCREEN"}]
        )


def test_owned_event_definition_survives_a_held_native_visit():
    exporter = service()
    owned = {
        "uid": "E1",
        "oid": "SE.360I.actt.V1",
        "forms": [{"uid": "F1"}],
        "metadata": {"required": False, "unknown": None},
    }
    exporter.study_event_service = SimpleNamespace(
        get_all_odms=lambda **kwargs: [
            owned,
            {"oid": "SE.360I.foreign.V1", "forms": [{"uid": "Foreign"}]},
            {"oid": "UNSTAMPED", "name": "V1", "forms": [{"uid": "Unowned"}]},
        ]
    )
    assert exporter._study_event_form_uids({}, [], "actt") == ({"F1"}, {"actt"})
    assert exporter._native_records == [
        {"kind": "studyEvent", "uid": "E1", "record": owned}
    ]


def test_owned_unattached_definitions_survive_without_claiming_foreign_or_similar_ids():
    exporter = service()
    group = {"uid": "G1", "oid": "IG.360I.actt.F1", "items": [{"uid": "I1"}]}
    item = {"uid": "I1", "oid": "IT.360I.actt.ITEM", "future": [None, False, 0]}
    calls = []

    def read(kind, rows, **kwargs):
        calls.append((kind, kwargs))
        return SimpleNamespace(items=rows)

    exporter.item_group_service = SimpleNamespace(
        get_all_odms=lambda **kw: read("itemGroup", [group], **kw)
    )
    exporter.item_service = SimpleNamespace(
        get_all_odms=lambda **kw: read(
            "item",
            [
                item,
                {"uid": "Foreign", "oid": "IT.360I.actt-other.ITEM"},
                {"uid": "Embedded", "oid": "FOREIGN.IT.360I.actt.ITEM"},
            ],
            **kw
        )
    )
    exporter._retain_source_owned_odm_definitions("actt")
    assert [row["record"] for row in exporter._native_records] == [group, item]
    assert len(exporter.census) == 2
    assert calls[0][1]["filter_by"]["oid"]["v"] == ["IG.360I.actt."]
    exporter._retain_source_owned_odm_definitions("actt")
    assert len(exporter._native_records) == 2
    assert len(exporter.census) == 2


def test_conflicting_unattached_definition_identity_is_not_silently_exported():
    exporter = service()
    exporter.item_group_service = SimpleNamespace(
        get_all_odms=lambda **kwargs: [
            {
                "uid": "G1",
                "oid": "IG.360I.actt.F1",
                "vendor_attributes": [{"name": "studyId", "value": "foreign"}],
            }
        ]
    )
    with pytest.raises(EdcExportError, match="AMBIGUOUS_SOURCE_REFERENCE"):
        exporter._retain_source_owned_odm_definitions("actt")


def test_unbound_event_reference_does_not_claim_a_foreign_stamped_form():
    exporter = service()
    exporter.form_service = SimpleNamespace(
        get_all_odms=lambda **kwargs: [
            {
                "uid": "Foreign_Form",
                "oid": "F1",
                "vendor_attributes": [{"name": "studyId", "value": "foreign"}],
            }
        ]
    )
    assert exporter._forms({"Foreign_Form"}, set())[0] == []


def test_missing_and_duplicate_native_assignments_are_explicit_without_source_loss():
    exporter = service()
    source = [{"visitRef": "V1", "formRef": "F1", "required": False}]
    exporter.source_bundle_meta = {
        "_provenance": {"builtBy": "csl.bundle-builder/preview"},
        "visitFormAssignments": source,
    }
    assert exporter._restore_source_assignments([]) == source
    assert exporter.census[-1]["nativeValue"] == 0
    assert exporter._restore_source_assignments([*source, *source]) == source
    assert exporter.census[-1]["nativeValue"] == 2


def test_conflicting_form_stamp_cannot_enter_through_an_event_reference():
    exporter = service()
    exporter.form_service = SimpleNamespace(
        get_all_odms=lambda page_size: [
            {
                "uid": "ForeignForm",
                "oid": "FOREIGN",
                "item_groups": [],
                "vendor_attributes": [{"name": "studyId", "value": "surpass"}],
            }
        ]
    )
    assert exporter._forms({"ForeignForm"}, {"actt"})[0] == []


def test_generated_section_identifier_does_not_replace_source_group_label():
    exporter = service()
    original = {"refKey": "AE1", "section": "AE_LOG", "group": "Adverse Event Details"}
    fields = exporter._restore_source_fields(
        [{"refKey": "AE1", "section": "AE_LOG", "group": "AE_LOG"}],
        {"fields": [original]},
    )
    assert fields[0]["group"] == "Adverse Event Details"
    fields = exporter._restore_source_fields(
        [{"refKey": "AE1", "section": "Updated section", "group": "Updated group"}],
        {"fields": [original]},
    )
    assert fields[0]["group"] == "Updated group"


def test_empty_native_parameter_projection_keeps_all_source_design_values(monkeypatch):
    from clinical_mdr_api.services.integrations import edc_export

    monkeypatch.setattr(edc_export.config.settings, "mapping_authority_mode", "shadow")
    exporter = service()
    exporter.study_service = SimpleNamespace(
        get_by_uid=lambda *args, **kwargs: {"uid": "Study_1"}
    )
    exporter._visits = lambda study_uid: ([], {}, {})
    exporter._study_event_form_uids = lambda *args: (set(), set())
    parameters = {
        "design.RATIO": "2:1",
        "design.ADAPTIVE": False,
        "design.AGE": {"value": 18, "qualifier": ">="},
    }

    def forms(*args):
        exporter.source_bundle_meta = {"study": {"studyParameters": parameters}}
        return ([{"name": "F", "fields": []}], {}, {})

    exporter._forms = forms
    exporter._assignments = lambda *args: []
    exporter._group_classes = lambda *args: []
    exporter._retain_native_associated_records = lambda *args: None
    bundle = exporter.build_bundle("Study_1")
    assert bundle["study"]["studyParameters"] == parameters
    assert bundle["_osbNative"]["records"][0]["record"] == {"uid": "Study_1"}


def service():
    value = object.__new__(EdcExportService)
    value.census = []
    value.source_bundle_meta = {}
    return value


def test_semantic_source_values_and_shapes_survive_native_field_and_visit_changes():
    exporter = service()
    exporter.source_bundle_meta = {
        "_provenance": {"builtBy": "csl.bundle-builder/1.0.0/preview"},
        "visits": [
            {
                "refKey": "V1",
                "ordinal": 15,
                "name": "Source visit",
                "metadata": {"empty": None},
            }
        ],
        "visitFormAssignments": [
            {
                "visitRef": "V1",
                "formRef": "F1",
                "required": False,
                "conditions": [{"when": "source"}],
            }
        ],
    }
    native_visits = [{"refKey": "V1", "ordinal": 12, "name": "Native changed visit"}]
    assert (
        exporter._restore_source_visits(native_visits)
        == exporter.source_bundle_meta["visits"]
    )
    assert (
        exporter._restore_source_assignments(
            [{"visitRef": "V1", "formRef": "F1", "required": True}]
        )
        == exporter.source_bundle_meta["visitFormAssignments"]
    )
    source_form = {
        "refKey": "F1",
        "fields": [
            {
                "refKey": "Q1",
                "label": "Complete semantic question",
                "type": "decimal",
                "metadata": {"values": [0, False, None]},
            },
            {"refKey": "Q2", "label": "Source field absent from native projection"},
        ],
    }
    assert (
        exporter._restore_source_fields(
            [{"refKey": "Q1", "label": "Native shortened", "type": "text"}], source_form
        )
        == source_form["fields"]
    )
    assert all(row["kind"] == "semantic_native_difference" for row in exporter.census)
    assert any(
        row["sourceValue"] == "Complete semantic question" for row in exporter.census
    )


def test_semantic_study_identifiers_are_not_replaced_with_osb_internal_identifiers(
    monkeypatch,
):
    from clinical_mdr_api.services.integrations import edc_export

    monkeypatch.setattr(edc_export.config.settings, "mapping_authority_mode", "shadow")
    exporter = service()
    exporter.study_service = SimpleNamespace(
        get_by_uid=lambda *a, **k: {
            "uid": "Study_1",
            "current_metadata": {
                "identification_metadata": {
                    "study_id": "OSB-123",
                    "study_acronym": "NATIVE",
                }
            },
        }
    )
    exporter._visits = lambda *a: ([], {}, {})
    exporter._study_event_form_uids = lambda *a: (set(), set())
    source_study = {
        "name": "Canonical semantic name",
        "uniqueIdentifier": "SEMANTIC-123",
    }

    def forms(*args):
        exporter.source_bundle_meta = {
            "study": source_study,
            "_provenance": {"builtBy": "csl.bundle-builder/2.0/preview"},
            "_exportCensus": {
                "contractVersion": "source-v1",
                "units": [{"id": "claim-1", "unknown": [0, False, None]}],
                "counts": {"mapped": 0},
            },
            "_mappingAuthority": {
                "mode": "preview",
                "semanticMetadata": {"accepted": False},
            },
        }
        return ([{"name": "F", "fields": []}], {}, {})

    exporter._forms = forms
    exporter._assignments = lambda *a: []
    exporter._group_classes = lambda *a: []
    exporter._retain_native_associated_records = lambda *a: None
    bundle = exporter.build_bundle("Study_1")
    assert bundle["study"]["name"] == source_study["name"]
    assert bundle["study"]["uniqueIdentifier"] == source_study["uniqueIdentifier"]
    assert bundle["_mappingAuthority"]["sourceTruthSystem"] == "ClinicalSemanticLayer"
    assert bundle["_mappingAuthority"]["deploymentAllowed"] is False
    assert (
        bundle["_mappingAuthority"]["sourceAuthority"]
        == exporter.source_bundle_meta["_mappingAuthority"]
    )
    assert (
        bundle["_exportCensus"]["sourceCensus"]
        == exporter.source_bundle_meta["_exportCensus"]
    )
    assert any(
        row["nativeValue"] == "OSB-123"
        for row in exporter.census
        if row["kind"] == "semantic_native_difference"
    )


def test_explicitly_empty_semantic_collections_do_not_import_unreviewed_native_rows():
    exporter = service()
    exporter.source_bundle_meta = {
        "_provenance": {"builtBy": "csl.bundle-builder/1.0/preview"},
        "visits": [],
        "visitFormAssignments": [],
        "studyGroupClasses": [],
    }
    assert exporter._restore_source_visits([{"refKey": "NATIVE"}]) == []
    assert (
        exporter._restore_source_assignments(
            [{"visitRef": "NATIVE", "formRef": "NATIVE"}]
        )
        == []
    )
    assert exporter._restore_source_group_classes([{"name": "Native arm"}]) == {
        "studyGroupClasses": []
    }


def test_native_item_keeps_all_units_translations_links_and_unknown_metadata():
    exporter = service()
    item = {
        "uid": "OdmItem_1",
        "oid": "LAB.RESULT",
        "name": "Result",
        "datatype": "float",
        "unit_definitions": [
            {"uid": "U1", "name": "mg/dL", "conversion_factor": 1},
            {"uid": "U2", "name": "mmol/L", "conversion_factor": 0.0555},
        ],
        "translated_texts": [
            {"language": "fr", "text_type": "Question", "text": "Résultat"}
        ],
        "activity_instances": [{"activity_instance_uid": "AI1", "primary": False}],
        "aliases": [{"context": "SDTM", "name": "LBORRES"}],
        "future": {"nullable": None, "enabled": False, "values": [0, ""]},
    }
    expected = deepcopy(item)
    field = exporter._field("LAB", "Chemistry", item, {"order_number": 2}, 1)
    assert field["unit"] == "mg/dL"
    assert exporter._native_records == [
        {"kind": "item", "uid": "OdmItem_1", "record": expected}
    ]
    item["future"]["values"].append("later mutation")
    assert exporter._native_records[0]["record"] == expected


def test_same_native_identity_keeps_distinct_readings_but_deduplicates_exact_repeats():
    exporter = service()
    first = {"uid": "I1", "metadata": {"value": None}}
    second = {"uid": "I1", "metadata": {"value": False}}
    exporter._retain_native("item", first)
    exporter._retain_native("item", deepcopy(first))
    exporter._retain_native("item", second)
    assert [r["record"] for r in exporter._native_records] == [first, second]


def test_selection_identity_is_preserved_without_rewriting_its_native_model():
    exporter = service()
    record = {"study_objective_uid": "SO1", "study_uid": "Study1", "metadata": None}
    exporter._retain_native("studyObjective", record, uid="SO1")
    assert exporter._native_records == [
        {"kind": "studyObjective", "uid": "SO1", "record": record}
    ]
    assert "uid" not in exporter._native_records[0]["record"]


def test_associated_collectors_keep_raw_scope_identity_and_unresolved_evidence(
    monkeypatch,
):
    from clinical_mdr_api.services.integrations import (
        edc_native_library_definitions,
        edc_native_study_records,
    )

    exporter = service()
    schedule = {"study_activity_schedule_uid": "Schedule1", "details": [False, None]}
    scope = {"studyUid": "Study1", "source": "scoped-query"}
    association = {
        "sourceUid": "Schedule1",
        "targetUid": "Missing",
        "status": "unresolved",
    }
    census = {
        "kind": "unresolved_native_reference",
        "ref": "Missing",
        "association": association,
    }
    monkeypatch.setattr(
        edc_native_study_records,
        "collect_study_native_records",
        lambda uid: (
            [
                {
                    "kind": "studyActivitySchedule",
                    "uid": "Schedule1",
                    "record": schedule,
                    "scope": scope,
                }
            ],
            [],
        ),
    )
    seen = []

    def definitions(records):
        seen.extend(deepcopy(records))
        return [], [association], [census]

    monkeypatch.setattr(
        edc_native_library_definitions,
        "collect_native_library_definitions",
        definitions,
    )
    exporter._retain_native_associated_records("Study1")
    assert (
        seen
        == exporter._native_records
        == [
            {
                "kind": "studyActivitySchedule",
                "uid": "Schedule1",
                "record": schedule,
                "scope": scope,
            }
        ]
    )
    assert exporter._native_associations == [association]
    assert exporter.census == [census]
    assert exporter._export_census_counts()["lossy"] == 1


def test_native_codelist_submission_values_are_not_replaced_with_display_labels():
    exporter = service()
    item = {
        "uid": "I1",
        "name": "Response",
        "datatype": "text",
        "codelist": {"allows_multi_choice": False},
        "terms": [
            {"term_uid": "T1", "name": "Yes", "submission_value": "Y", "order": 1},
            {"term_uid": "T2", "name": "None", "submission_value": 0, "order": 2},
        ],
    }
    field = exporter._field("F1", "Responses", item, {}, 1)
    assert [option["value"] for option in field["options"]] == ["Y", "0"]


def test_normalized_field_key_collision_cannot_hybridize_two_source_definitions():
    exporter = service()
    with pytest.raises(EdcExportError, match="AMBIGUOUS_SOURCE_REFERENCE"):
        exporter._restore_source_fields(
            [{"refKey": "A_B"}],
            {
                "fields": [
                    {"refKey": "A-B", "metadata": {"value": "first"}},
                    {"refKey": "A_B", "metadata": {"value": "second"}},
                ]
            },
        )


def test_referenced_method_and_condition_definitions_and_unresolved_oids_retained():
    exporter = service()
    group = {
        "uid": "G1",
        "items": [
            {
                "uid": "I1",
                "method_oid": "M1",
                "collection_exception_condition_oid": "C1",
                "vendor": {"attributes": [{"name": "evidence", "value": "source"}]},
            },
            {"uid": "I2", "method_oid": "MISSING"},
        ],
    }
    method = {
        "uid": "Method1",
        "oid": "M1",
        "formal_expressions": [{"context": "SAS", "expression": "x=y+1;"}],
    }
    condition = {
        "uid": "Condition1",
        "oid": "C1",
        "formal_expressions": [{"context": "SAS", "expression": "age>=18"}],
    }
    exporter._retain_native("itemGroup", group)
    calls = []

    def read(kind, records, **kwargs):
        calls.append((kind, kwargs))
        return SimpleNamespace(items=records)

    exporter.method_service = SimpleNamespace(
        get_all_odms=lambda **kw: read("method", [method], **kw)
    )
    exporter.condition_service = SimpleNamespace(
        get_all_odms=lambda **kw: read("condition", [condition], **kw)
    )
    exporter._retain_linked_definitions()
    assert [row["record"] for row in exporter._native_records] == [
        group,
        method,
        condition,
    ]
    assert calls[0][1]["filter_by"]["oid"]["v"] == ["M1", "MISSING"]
    assert exporter.census[0]["ref"] == "method/MISSING"
    assert exporter._export_census_counts()["lossy"] == 1


@pytest.mark.parametrize(
    "kind",
    [
        "dangling_form_ref",
        "bundle_meta_unparseable",
        "source_form_unparseable",
        "source_field_unparseable",
        "ext_unparseable",
        "ext_value_unparseable",
    ],
)
def test_incomplete_metadata_no_longer_reports_zero_loss(kind):
    exporter = service()
    exporter.census = [
        {"kind": kind, "ref": "native-record", "detail": "retention failed"}
    ]
    assert exporter._export_census_counts()["lossy"] == 1
