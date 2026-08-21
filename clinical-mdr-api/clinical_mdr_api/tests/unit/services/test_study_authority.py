"""OSB authority boundary: native study + USDM must reconcile before release."""

from clinical_mdr_api.models.integrations.study_authority import (
    StudyAuthorityReconciliationRow,
)
from clinical_mdr_api.services.ddf.usdm_utils import IdManager
from clinical_mdr_api.services.integrations.edc_export import authority_disclosure
from clinical_mdr_api.services.integrations.study_authority import (
    _assemble_study_odm_metadata,
    _build_reconciliation,
    _build_usdm_extensions,
    _canonical_hash,
    _mapping_blockers,
)


def _native(status="LOCKED"):
    return {
        "current_metadata": {
            "version_metadata": {"study_status": status},
        }
    }


def _usdm(objectives=0, endpoints=0):
    return {
        "usdmVersion": "4.0.0",
        "study": {
            "versions": [
                {
                    "studyDesigns": [
                        {
                            "objectives": [
                                {"endpoints": [{} for _ in range(endpoints)]}
                                for _ in range(objectives)
                            ]
                        }
                    ]
                }
            ]
        },
    }


def _complete_endpoint(collection_disposition="direct"):
    return {
        "study_endpoint_uid": "E1",
        "endpoint": {"uid": "END", "name_plain": "Endpoint"},
        "study_objective": {"study_objective_uid": "O1"},
        "endpoint_level": {"term_uid": "C-level"},
        "endpoint_sublevel": {"term_uid": "C-sublevel"},
        "timeframe": {"uid": "TF"},
        "endpoint_units": {"units": [{"uid": "UNIT"}]},
        "collection_disposition": collection_disposition,
    }


def _standards():
    return [
        {
            "uid": f"SSV-{catalogue}",
            "automatically_created": False,
            "ct_package": {
                "uid": f"{catalogue}-2025-09-26",
                "catalogue_name": catalogue,
                "effective_date": "2025-09-26",
            },
        }
        for catalogue in ("DDF CT", "SDTM CT", "CDASH CT")
    ]


def test_release_blockers_name_native_to_usdm_losses():
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native("DRAFT"),
        usdm=_usdm(),
        standards=[],
        objectives=[{"study_objective_uid": "O1", "objective": {"uid": "OBJ"}}],
        endpoints=[
            {
                "study_endpoint_uid": "E1",
                "endpoint": {"uid": "END"},
                "endpoint_level": None,
                "timeframe": None,
                "endpoint_units": None,
            }
        ],
        criteria=[{"study_criteria_uid": "C1", "criteria": {"uid": "CRIT"}}],
        integrity={"all_passed": False},
    )
    codes = {blocker.code for blocker in blockers}
    assert {
        "OSB_STUDY_NOT_RELEASED",
        "OSB_STANDARD_VERSION_MISSING",
        "ENDPOINT_LEVEL_MISSING",
        "ENDPOINT_TIMEFRAME_MISSING",
        "ENDPOINT_UNITS_MISSING",
        "ENDPOINT_SUBLEVEL_MISSING",
        "USDM_OBJECTIVE_COVERAGE_GAP",
        "USDM_ENDPOINT_COVERAGE_GAP",
        "USDM_CRITERIA_EXTENSION_COVERAGE_GAP",
        "OSB_INTEGRITY_CHECK_FAILED",
    }.issubset(codes)


def test_complete_native_study_and_usdm_has_no_blockers():
    endpoints = [_complete_endpoint()]
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(objectives=1, endpoints=1),
        standards=_standards(),
        objectives=[{"study_objective_uid": "O1", "objective": {"uid": "OBJ"}}],
        endpoints=endpoints,
        criteria=[],
        integrity={"all_passed": True},
        usdm_extensions=_build_usdm_extensions(endpoints, []),
    )
    assert blockers == []


def test_legacy_mode_cannot_establish_mapping_authority():
    blockers = _mapping_blockers(
        authority_mode="legacy",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
    )
    assert [blocker.code for blocker in blockers] == ["LEGACY_AUTHORITY_MODE"]


def test_authority_snapshot_hash_is_key_order_independent():
    assert _canonical_hash({"b": 2, "a": {"y": 1, "x": 0}}) == _canonical_hash(
        {"a": {"x": 0, "y": 1}, "b": 2}
    )


def test_usdm_id_manager_is_document_local_and_entity_scoped():
    manager = IdManager()
    assert manager.get_id("Objective", "same-osb-uid") == "Objective_1"
    assert manager.get_id("Endpoint", "same-osb-uid") == "Endpoint_1"
    assert manager.get_id("Objective", "same-osb-uid") == "Objective_1"
    manager.clear_all_ids()
    assert manager.get_id("Objective", "same-osb-uid") == "Objective_1"


def test_v1_edc_projection_never_claims_osb_authority():
    shadow = authority_disclosure("shadow", source_overlay_active=True)
    assert shadow["authoritative"] is False
    assert shadow["mappingAuthority"] == "legacy-source-overlay"
    assert shadow["studyDefinitionStandard"] == "CDISC USDM 4"


def test_empty_endpoint_units_are_release_blocking():
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(objectives=1, endpoints=1),
        standards=_standards(),
        objectives=[{"study_objective_uid": "O1", "objective": {"uid": "OBJ"}}],
        endpoints=[
            {
                "study_endpoint_uid": "E1",
                "endpoint": {"uid": "END"},
                "endpoint_level": {"term_uid": "LEVEL"},
                "timeframe": {"uid": "TF"},
                "endpoint_units": {"units": []},
            }
        ],
        criteria=[],
        integrity={"all_passed": True},
    )
    assert "ENDPOINT_UNITS_MISSING" in {blocker.code for blocker in blockers}


def test_auto_selected_or_unidentified_standard_packages_block_release():
    standards = _standards()
    standards[0]["automatically_created"] = True
    standards[1]["ct_package"]["effective_date"] = None
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=standards,
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
    )
    codes = {blocker.code for blocker in blockers}
    assert "OSB_STANDARD_VERSION_AUTO_SELECTED" in codes
    assert "OSB_STANDARD_PACKAGE_IDENTITY_INCOMPLETE" in codes


def test_criteria_are_covered_by_the_versioned_governed_extension():
    criteria = [
        {
            "study_criteria_uid": "C1",
            "criteria": {
                "uid": "CRIT1",
                "name": "<p>Age at least 18 years</p>",
                "name_plain": "Age at least 18 years",
                "version": "1.0",
            },
            "criteria_type": {"term_uid": "C25532"},
            "key_criteria": True,
        }
    ]
    extension = _build_usdm_extensions([], criteria)
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=criteria,
        integrity={"all_passed": True},
        usdm_extensions=extension,
    )
    assert extension["schemaVersion"] == "osb-usdm-extension/1.0"
    assert extension["eligibilityCriteria"][0]["studyCriteriaUid"] == "C1"
    assert "USDM_CRITERIA_EXTENSION_COVERAGE_GAP" not in {
        blocker.code for blocker in blockers
    }


def test_endpoint_collection_disposition_never_defaults_to_a_crf_field():
    endpoints = [_complete_endpoint(collection_disposition=None)]
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(objectives=1, endpoints=1),
        standards=_standards(),
        objectives=[{"study_objective_uid": "O1", "objective": {"uid": "OBJ"}}],
        endpoints=endpoints,
        criteria=[],
        integrity={"all_passed": True},
        usdm_extensions=_build_usdm_extensions(endpoints, []),
    )
    assert "ENDPOINT_COLLECTION_DISPOSITION_MISSING" in {
        blocker.code for blocker in blockers
    }


def test_identity_reconciliation_catches_equal_count_swapped_arms():
    usdm = _usdm()
    design = usdm["study"]["versions"][0]["studyDesigns"][0]
    design["arms"] = [
        {"id": "StudyArm_1", "name": "Arm B", "label": "Arm B", "description": None},
        {"id": "StudyArm_2", "name": "Arm A", "label": "Arm A", "description": None},
    ]
    reconciliation = _build_reconciliation(
        usdm=usdm,
        objectives=[],
        endpoints=[],
        criteria=[],
        arms=[
            {"arm_uid": "A", "name": "Arm A", "description": None},
            {"arm_uid": "B", "name": "Arm B", "description": None},
        ],
        epochs=[],
        elements=[],
        design_cells=[],
        visits=[],
        activities=[],
        schedules=[],
        usdm_extensions=_build_usdm_extensions([], []),
    )
    arm_rows = [row for row in reconciliation if row.resource_class == "Arm"]
    assert len(arm_rows) == 2
    assert {row.status for row in arm_rows} == {"changed"}


def test_nonmatched_identity_row_blocks_release():
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
        reconciliation=[
            StudyAuthorityReconciliationRow(
                resource_class="Encounter",
                native_uid="Visit_1",
                usdm_id="Encounter_1",
                status="changed",
                blocker_code="USDM_ENCOUNTER_IDENTITY_MISMATCH",
            )
        ],
    )
    assert "USDM_ENCOUNTER_IDENTITY_MISMATCH" in {blocker.code for blocker in blockers}


def test_historical_integrity_unavailability_remains_explicit():
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": None, "version_consistent": False},
        structure_statistics={"version_consistent": True},
        study_value_version="1.0",
    )
    codes = {blocker.code for blocker in blockers}
    assert "OSB_HISTORICAL_INTEGRITY_UNAVAILABLE" in codes
    assert "OSB_INTEGRITY_CHECK_UNAVAILABLE" in codes


def test_zero_timing_without_anchor_is_not_reclassified_as_missing():
    visit = {
        "uid": "Visit_1",
        "time_value": 0,
        "time_unit_name": "day",
        "visit_type": {"term_uid": "C1"},
        "visit_contact_mode": {"term_uid": "C2"},
        "min_visit_window_value": None,
        "max_visit_window_value": None,
    }
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
        visits=[visit],
    )
    assert "USDM_RELATIVE_TIMING_WITHOUT_ANCHOR" not in {
        blocker.code for blocker in blockers
    }


def test_nonzero_timing_without_anchor_blocks_instead_of_minting_one():
    visit = {
        "uid": "Visit_1",
        "time_value": 7,
        "time_unit_name": "day",
        "visit_type": {"term_uid": "C1"},
        "visit_contact_mode": {"term_uid": "C2"},
    }
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
        visits=[visit],
    )
    assert "USDM_RELATIVE_TIMING_WITHOUT_ANCHOR" in {
        blocker.code for blocker in blockers
    }


def test_multiple_global_anchors_block_single_timeline_projection():
    visits = [
        {"uid": "V1", "is_global_anchor_visit": True},
        {"uid": "V2", "is_global_anchor_visit": True},
    ]
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
        visits=visits,
    )
    assert "USDM_MULTIPLE_GLOBAL_ANCHORS_UNSUPPORTED" in {
        blocker.code for blocker in blockers
    }


def test_activity_semantics_cannot_be_satisfied_by_hardcoded_defaults():
    activities = [
        {
            "study_activity_uid": "SA1",
            "activity": {"uid": "A1", "name": "Blood pressure"},
        }
    ]
    extension = _build_usdm_extensions([], [], activities=activities)
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
        activities=activities,
        usdm_extensions=extension,
    )
    codes = {blocker.code for blocker in blockers}
    assert {
        "ACTIVITY_CONDITIONALITY_MISSING",
        "ACTIVITY_PROCEDURE_TYPE_MISSING",
        "ACTIVITY_PROCEDURE_CODE_MISSING",
    }.issubset(codes)


def test_void_usdm_codes_block_release():
    usdm = _usdm()
    usdm["study"]["versions"][0]["studyDesigns"][0]["studyType"] = {
        "instanceType": "Code",
        "code": "",
        "decode": "",
    }
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=usdm,
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
    )
    assert "USDM_VOID_CODE_PRESENT" in {blocker.code for blocker in blockers}


def test_study_reachable_odm_hierarchy_is_complete_and_deduplicated():
    mapped = {
        "activity_item": {
            "studyActivityInstanceUid": "SAI1",
            "activityInstanceUid": "AI1",
            "activityInstanceVersion": "1.0",
            "activityItemClassUid": "AIC1",
            "activityItemClassName": "Result",
            "textValue": None,
        },
        "odm_item": {
            "uid": "OdmItem_1",
            "version": "1.0",
            "name": "Visual acuity",
            "oid": "VAORRES",
        },
        "activity_item_link": {"order": 1, "primary": True},
        "odm_item_group": {
            "uid": "OdmItemGroup_1",
            "version": "1.0",
            "name": "Visual acuity assessment",
            "oid": "VA",
        },
        "item_ref": {"orderNumber": 1, "mandatory": True},
        "odm_form": {
            "uid": "OdmForm_1",
            "version": "1.0",
            "name": "Ophthalmology",
            "oid": "OPHTH",
        },
        "item_group_ref": {"orderNumber": 1, "mandatory": True},
        "odm_study_event": {
            "uid": "OdmStudyEvent_1",
            "version": "1.0",
            "name": "Month 12",
            "oid": "M12",
        },
        "form_ref": {"orderNumber": 1, "mandatory": True, "locked": False},
    }
    unmapped = {
        "activity_item": {
            "studyActivityInstanceUid": "SAI2",
            "activityInstanceUid": "AI2",
            "activityInstanceVersion": "1.0",
            "activityItemClassUid": "AIC2",
            "activityItemClassName": "Comment",
            "textValue": None,
        }
    }

    metadata = _assemble_study_odm_metadata([mapped, mapped, unmapped])

    assert metadata["scope"] == "study-reachable-native-odm"
    assert len(metadata["activityItems"]) == 2
    assert metadata["unmappedActivityItemCount"] == 1
    assert len(metadata["items"]) == 1
    assert metadata["items"][0]["activityItemLinks"][0]["primary"] is True
    assert metadata["itemGroups"][0]["itemRefs"][0]["uid"] == "OdmItem_1"
    assert metadata["forms"][0]["itemGroupRefs"][0]["uid"] == "OdmItemGroup_1"
    assert metadata["studyEvents"][0]["formRefs"][0]["uid"] == "OdmForm_1"


def test_native_compound_without_usdm_intervention_blocks_release():
    blockers = _mapping_blockers(
        authority_mode="shadow",
        native_study=_native(),
        usdm=_usdm(),
        standards=_standards(),
        objectives=[],
        endpoints=[],
        criteria=[],
        integrity={"all_passed": True},
        compounds=[
            {
                "study_compound_uid": "SC1",
                "compound": {"uid": "Compound_1", "name": "Ranibizumab"},
            }
        ],
    )
    assert "USDM_INTERVENTION_COVERAGE_GAP" in {blocker.code for blocker in blockers}


def test_odm_snapshot_keeps_distinct_identical_activity_items_and_source_properties():
    semantic_item = {
        "studyActivityInstanceUid": "SAI1",
        "activityInstanceUid": "AI1",
        "activityItemClassUid": "AIC1",
        "textValue": None,
        "sourceProperties": {"is_adam_param_specific": True},
    }
    rows = [
        {"activity_item_key": "neo4j-item-1", "activity_item": semantic_item},
        {"activity_item_key": "neo4j-item-2", "activity_item": semantic_item},
    ]

    metadata = _assemble_study_odm_metadata(rows)

    assert len(metadata["activityItems"]) == 2
    assert metadata["unmappedActivityItemCount"] == 2
    assert metadata["activityItems"][0]["sourceProperties"] == {
        "is_adam_param_specific": True
    }
