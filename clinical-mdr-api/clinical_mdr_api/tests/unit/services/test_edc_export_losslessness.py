"""Losslessness checks for the OSB -> EDC projection."""

import base64
import gzip
import json

import pytest

from clinical_mdr_api.services.integrations.edc_export import (
    EdcExportError,
    EdcExportService,
    _carrier_json,
    _source_study_id,
)


def _service():
    service = object.__new__(EdcExportService)
    service.census = []
    service.source_bundle_meta = {}
    return service


def test_compressed_carrier_restores_exact_json():
    source = {"refKey": "AE.TERM", "metadata": ["exact"] * 1000}
    raw = json.dumps(source, separators=(",", ":")).encode()
    carrier = "gzip+base64:" + base64.b64encode(
        gzip.compress(raw, mtime=0)
    ).decode()

    assert _carrier_json(carrier) == source


def test_imported_study_description_recovers_exact_source_identity():
    assert (
        _source_study_id(
            {
                "description": "Imported from 360i study proj-05bb9341-study "
                "(build 0123456789ab)"
            }
        )
        == "proj-05bb9341-study"
    )
    assert _source_study_id({"description": "Native OSB study"}) is None


def test_same_visit_ref_from_another_study_is_excluded_from_forms_and_matrix():
    service = _service()
    service.study_event_service = type(
        "Events",
        (),
        {
            "get_all_odms": lambda self, page_size: [
                {
                    "oid": "SE.360I.study-a.V_SCREEN",
                    "name": "Screening",
                    "forms": [
                        {
                            "uid": "Form_A",
                            "order_number": 1,
                            "mandatory": "yes",
                        }
                    ],
                },
                {
                    "oid": "SE.360I.study-b.V_SCREEN",
                    "name": "Screening",
                    "forms": [
                        {
                            "uid": "Form_B",
                            "order_number": 1,
                            "mandatory": "yes",
                        }
                    ],
                },
            ]
        },
    )()
    visits = [{"refKey": "V_SCREEN", "name": "Screening"}]
    form_uids, source_ids = service._study_event_form_uids(
        {"screening": "V_SCREEN"}, visits, "study-a"
    )
    assert form_uids == {"Form_A"}
    assert source_ids == {"study-a"}

    assignments = service._assignments(
        "Study_1",
        {"screening": "V_SCREEN"},
        {"Form_A": "F_A", "Form_B": "F_B"},
        {},
        visits,
        source_ids,
    )
    assert assignments == [
        {
            "visitRef": "V_SCREEN",
            "formRef": "F_A",
            "required": True,
            "ordinal": 1,
        }
    ]


def test_native_form_fallback_does_not_absorb_other_studies_x360i_forms():
    service = _service()
    service.form_service = type(
        "Forms",
        (),
        {
            "get_all_odms": lambda self, page_size: [
                {
                    "uid": "Native_Form",
                    "oid": "NATIVE",
                    "name": "Native",
                    "item_groups": [],
                    "vendor_attributes": [],
                },
                {
                    "uid": "Imported_Form",
                    "oid": "IMPORTED",
                    "name": "Imported",
                    "item_groups": [],
                    "vendor_attributes": [
                        {"name": "studyId", "value": "other-study"},
                        {"name": "refKey", "value": "F_OTHER"},
                    ],
                },
            ]
        },
    )()
    forms, _, _ = service._forms(set(), set())
    assert [form["name"] for form in forms] == ["Native"]


def test_exact_source_field_restores_codes_extensions_and_original_refkey():
    service = _service()
    source = {
        "refKey": "AE.TERM",
        "name": "Adverse event term",
        "type": "select",
        "ordinal": 4,
        "options": [{"label": "Yes", "value": "Y", "order": 1}],
        "sdtmMappingSource": "protocol",
        "validationRules": [{"kind": "required"}],
    }
    item = {
        "oid": "AE_TERM__X360I_PL_123",
        "name": source["name"],
        "datatype": "text",
        "prompt": "Current prompt",
        "length": 200,  # importer-only default; source stated no length
        "comment": None,
        "codelist": {"allows_multi_choice": False},
        "terms": [{"display_text": "Yes", "order": 1}],
        "unit_definitions": [],
        "vendor_attributes": [
            {"name": "fieldType", "value": "select"},
            {"name": "source", "value": json.dumps(source)},
            {
                "name": "ext",
                "value": json.dumps({"futureNullableProperty": "json:null"}),
            },
        ],
    }
    field = service._field(
        form_ref="F_AE",
        section_name="Adverse Events",
        item=item,
        item_ref={"order_number": 4, "mandatory": "yes"},
        order=4,
    )
    assert field["refKey"] == "AE.TERM"
    assert field["options"] == source["options"]
    assert field["sdtmMappingSource"] == "protocol"
    assert field["validationRules"] == source["validationRules"]
    assert field["ordinal"] == 4
    assert field["required"] is True
    assert field["futureNullableProperty"] is None
    assert "length" not in field


def test_source_assignment_metadata_survives_current_osb_relationship_overlay():
    service = _service()
    service.source_bundle_meta = {
        "visitFormAssignments": [
            {
                "visitRef": "V.1",
                "formRef": "F.AE",
                "required": False,
                "ordinal": 2,
                "_derivedFrom": ["protocol-table"],
            }
        ]
    }
    restored = service._restore_source_assignments(
        [{"visitRef": "V_1", "formRef": "F_AE", "required": True, "ordinal": 3}]
    )
    assert restored == [
        {
            "visitRef": "V.1",
            "formRef": "F.AE",
            "required": True,
            "ordinal": 3,
            "_derivedFrom": ["protocol-table"],
        }
    ]


def test_importer_visit_scaffolding_does_not_replace_source_schedule():
    service = _service()
    source = {
        "refKey": "V.1",
        "name": "Screening",
        "ordinal": 1,
        "type": "scheduled",
        "category": "Study Event",
        "scheduleDay": -21,
    }
    service.source_bundle_meta = {"visits": [source]}

    restored = service._restore_source_visits(
        [
            {
                "refKey": "V_1",
                "name": "Screening",
                "ordinal": 1,
                "type": "scheduled",
                "repeating": False,
                "category": "Treatment 1",
                "scheduleDay": 1,
                "minDay": 1,
                "maxDay": 1,
            }
        ]
    )

    assert restored == [source]


def test_non_arm_group_classes_are_not_dropped_when_arms_are_refreshed():
    service = _service()
    service.source_bundle_meta = {
        "studyGroupClasses": [
            {
                "name": "Sites",
                "groupClassTypeName": "Other",
                "groups": [{"name": "Site A"}],
            },
            {
                "name": "Old arms",
                "groupClassTypeName": "Arm",
                "groups": [{"name": "Old"}],
            },
        ]
    }
    current_arms = [
        {
            "name": "Arms",
            "groupClassTypeName": "Arm",
            "groups": [{"name": "Current"}],
        }
    ]
    restored = service._restore_source_group_classes(current_arms)
    assert [x["groupClassTypeName"] for x in restored["studyGroupClasses"]] == [
        "Other",
        "Arm",
    ]
    assert restored["studyGroupClasses"][1]["groups"][0]["name"] == "Current"


@pytest.mark.parametrize("mode", ["shadow", "enforced"])
def test_v1_send_is_blocked_outside_explicit_legacy_recovery(monkeypatch, mode):
    from clinical_mdr_api.services.integrations import edc_export

    monkeypatch.setattr(edc_export.config.settings, "mapping_authority_mode", mode)
    service = _service()
    with pytest.raises(EdcExportError, match=f"MAPPING_AUTHORITY_{mode.upper()}"):
        service.send_to_edc("Study_1", dry_run=True)


def test_v1_legacy_send_requires_nonproduction_explicit_opt_in(monkeypatch):
    from clinical_mdr_api.services.integrations import edc_export

    monkeypatch.setattr(edc_export.config.settings, "mapping_authority_mode", "legacy")
    monkeypatch.setattr(edc_export.config.settings, "deployment_environment", "development")
    monkeypatch.setattr(edc_export.config.settings, "allow_unsafe_legacy_edc_send", False)
    service = _service()
    with pytest.raises(EdcExportError, match="LEGACY_EDC_SEND_EXPLICIT_OPT_IN_REQUIRED"):
        service.send_to_edc("Study_1", dry_run=True)


def test_v1_real_send_is_always_prohibited(monkeypatch):
    from clinical_mdr_api.services.integrations import edc_export

    monkeypatch.setattr(edc_export.config.settings, "mapping_authority_mode", "legacy")
    monkeypatch.setattr(edc_export.config.settings, "deployment_environment", "development")
    monkeypatch.setattr(edc_export.config.settings, "allow_unsafe_legacy_edc_send", True)
    service = _service()
    with pytest.raises(EdcExportError, match="LEGACY_EDC_ACTIVATION_PROHIBITED"):
        service.send_to_edc("Study_1", dry_run=False)


def test_export_census_counts_gate_on_actual_loss_only():
    service = _service()
    service.census = [
        {"kind": "mapping_authority", "ref": "Study_1", "detail": "warning"},
        {"kind": "carrier_preserved", "ref": "study.sponsor", "detail": "kept"},
        {"kind": "field_type_downgrade", "ref": "AE.TERM", "detail": "select->text"},
        {"kind": "field_type_downgrade", "ref": "CM.DOSE", "detail": "number->text"},
        {"kind": "ambiguous_join", "ref": "V_1", "detail": "two candidate visits"},
    ]
    counts = service._export_census_counts()
    assert counts == {
        "total": 5,
        "downgrades": 2,
        "ambiguous_joins": 1,
        "lossy": 3,
    }


def _sendable_service(monkeypatch, bundle, body):
    from clinical_mdr_api.services.integrations import edc_export

    monkeypatch.setattr(edc_export.config.settings, "mapping_authority_mode", "legacy")
    monkeypatch.setattr(edc_export.config.settings, "deployment_environment", "development")
    monkeypatch.setattr(edc_export.config.settings, "allow_unsafe_legacy_edc_send", True)
    monkeypatch.setattr(edc_export.config.settings, "edc_base_url", "http://edc.test")
    monkeypatch.setattr(
        edc_export.config.settings, "edc_api_key", type(
            "Key", (), {"get_secret_value": lambda self: "secret"}
        )()
    )
    service = _service()
    service.build_bundle = lambda study_uid: json.loads(json.dumps(bundle))
    captured = {}

    class _Response:
        status_code = 200
        is_success = True

        @staticmethod
        def json():
            return body

    def _post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _Response()

    monkeypatch.setattr(edc_export.httpx, "post", _post)
    return service, captured


def test_send_keeps_export_census_on_the_wire(monkeypatch):
    census = {"rows": [{"kind": "mapping_authority", "ref": "s", "detail": "d"}],
              "counts": {"total": 1, "downgrades": 0, "ambiguous_joins": 0, "lossy": 0}}
    bundle = {"study": {"name": "S"}, "_exportCensus": census, "_mappingAuthority": {"mode": "legacy"}}
    service, captured = _sendable_service(monkeypatch, bundle, {"success": True})
    result = service.send_to_edc("Study_1", dry_run=True)
    assert captured["json"]["bundle"]["_exportCensus"] == census
    assert result["exportCensus"] == census


@pytest.mark.parametrize("body,expect", [
    ({"success": True, "partial": True}, "partial=True"),
    ({"success": True, "census": {"counts": {"unknown": 2}}}, r"census\.counts\.unknown=2"),
    ({"success": True, "assignmentsSkipped": 3}, "assignmentsSkipped=3"),
    ({"success": True, "census": {"counts": {"unknown": 0}, "studyTasksSkipped": [{"ref": "T1"}]}},
     "studyTasksSkipped="),
])
def test_send_rejects_narrowed_edc_acceptance(monkeypatch, body, expect):
    bundle = {"study": {"name": "S"}, "_exportCensus": {"rows": [], "counts": {}}}
    service, _ = _sendable_service(monkeypatch, bundle, body)
    with pytest.raises(EdcExportError, match="EDC_IMPORT_NARROWED") as caught:
        service.send_to_edc("Study_1", dry_run=True)
    import re as _re

    assert _re.search(expect, str(caught.value))
