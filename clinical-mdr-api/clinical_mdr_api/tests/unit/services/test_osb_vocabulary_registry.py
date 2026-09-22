"""The OSB vocabulary registry (owned by ClinicalSemanticLayer, copied here byte for byte) is the one source the
executors' tables are derived from: proposal_target_capabilities, osb_family_map, mapping_context's family node
models and native_capture_projection's read-back types.

One object used to be named five ways across the two repositories with nothing proving the names agreed (overlap
report F7, gap G-I7); until 2026-09-21 the registry only asserted the hand-kept tables, now it replaces them. These
tests pin the derivation: the hash the loader verifies, the shape of every derived table, and the facts the executors
rely on. Correspondence is not identity: a row says which kind of correspondence it is.
"""

import json
from pathlib import Path

import pytest

from clinical_mdr_api.services.integrations.mapping_context import (
    BLOCKER_ONLY_FAMILY_CODES as CONTEXT_BLOCKER_CODES,
)
from clinical_mdr_api.services.integrations.native_capture_projection import CAPTURE_FAMILY_TYPES
from clinical_mdr_api.services.integrations.osb_family_map import (
    BLOCKER_ONLY_FAMILIES,
    BLOCKER_ONLY_FAMILY_CODES,
    CAPTURE_SECTION_FAMILIES,
    COLLECTION_MODE_FAMILIES,
    FAMILY_ALIASES,
    FAMILY_NODE_MODELS,
    NATIVE_CREATE_FAMILIES,
    NATIVE_READ_MODELS,
    STUDY_SECTION_FAMILIES,
    SUPPORTED_RESOURCE_FAMILIES,
    canonicalize_family,
    executor_kind_for_canonical_family,
)
from clinical_mdr_api.services.integrations.osb_vocabulary_registry import (
    FAMILIES,
    REGISTRY,
    REGISTRY_PATH,
    ROWS,
    OsbVocabularyRegistryError,
    canonical_registry_hash,
    canonical_resource_type,
    load_registry,
)
from clinical_mdr_api.services.integrations.proposal_target_capabilities import (
    NATIVE_CREATE_REQUEST_RESOURCE_TYPES,
    NATIVE_DECLINABLE_RESOURCE_TYPES,
    NATIVE_DUAL_MODE_RESOURCE_TYPES,
    NATIVE_EXECUTOR_RESOURCE_TYPES,
    NATIVE_SELECTION_RESOURCE_TYPES,
    TARGET_CAPABILITIES,
    target_capability,
)


def test_registry_is_the_expected_contract_and_its_hash_covers_families_and_rows() -> None:
    assert REGISTRY["contractVersion"] == "OsbVocabularyRegistryV1@1.1.0"
    assert REGISTRY["owner"] == "ClinicalSemanticLayer"
    assert REGISTRY["registryHash"] == canonical_registry_hash(REGISTRY["families"], REGISTRY["rows"])
    assert len(ROWS) == len(REGISTRY["rows"]) == 37
    assert len(FAMILIES) == 21


def test_a_registry_edited_without_restamping_refuses_to_load(tmp_path: Path) -> None:
    tampered = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    tampered["rows"][0]["osbCapability"] = "unresolved"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(OsbVocabularyRegistryError, match="OSB_VOCABULARY_REGISTRY_HASH_MISMATCH"):
        load_registry(path)


def test_target_capabilities_and_native_sets_are_the_registry_rows() -> None:
    assert set(TARGET_CAPABILITIES) == set(ROWS)
    for name, row in ROWS.items():
        assert target_capability(name) == row["osbCapability"]
        assert (name in NATIVE_EXECUTOR_RESOURCE_TYPES) == row["osbNativeExecutor"], name
        assert (name in NATIVE_SELECTION_RESOURCE_TYPES) == row["osbSelection"], name
        assert (name in NATIVE_DUAL_MODE_RESOURCE_TYPES) == row["osbDualMode"], name
        assert (name in NATIVE_CREATE_REQUEST_RESOURCE_TYPES) == row["osbCreateRequest"], name
        assert (name in NATIVE_DECLINABLE_RESOURCE_TYPES) == row["osbDeclinable"], name
    # The facts the executors and the reviewer rely on, pinned so a registry edit cannot move them silently.
    assert TARGET_CAPABILITIES["StudySelectionArm"] == "native_study_mutation"
    assert TARGET_CAPABILITIES["CTTerm"] == "governed_library_reference"
    assert TARGET_CAPABILITIES["IntegrationExtension"] == "governed_extension"
    assert TARGET_CAPABILITIES["Unresolved"] == "unresolved"
    assert {"StudyMetadata", "StudyVisit", "StudyActivitySchedule", "OdmForm"} <= NATIVE_EXECUTOR_RESOURCE_TYPES
    assert NATIVE_SELECTION_RESOURCE_TYPES == {
        "StudySelectionObjective", "StudySelectionEndpoint", "StudySelectionCriteria", "StudySelectionActivity",
        "StudySelectionCompound", "StudyActivityInstruction", "StudySelectionActivityInstance",
    }
    assert NATIVE_DUAL_MODE_RESOURCE_TYPES == {"OdmForm", "OdmItemGroup", "OdmItem", "OdmMethod", "OdmCondition"}
    assert NATIVE_DECLINABLE_RESOURCE_TYPES == {
        "StudyStandardVersion", "StudySelectionCompound", "StudyCompoundDosing", "StudyActivityInstruction",
    }


def test_family_tables_are_the_registry_families() -> None:
    assert set(STUDY_SECTION_FAMILIES) == {
        "metadata", "standards", "compounds", "dosing", "instructions", "criteria", "objectives", "endpoints",
        "timeframes", "activities",
    }
    assert set(CAPTURE_SECTION_FAMILIES) == {
        "forms", "sections_groups", "items", "checks", "conditions", "branching", "assignments", "collection_standards",
    }
    assert FAMILY_ALIASES == {
        "edit_checks": "odm_methods", "conditions": "odm_conditions", "branching": "odm_aliases", "assignments": "activity_schedules",
    }
    assert SUPPORTED_RESOURCE_FAMILIES == set(FAMILIES) | set(FAMILY_ALIASES)
    for family, entry in FAMILIES.items():
        assert executor_kind_for_canonical_family(family) == entry["plane"], family
        assert (family in BLOCKER_ONLY_FAMILIES) == entry["blockerOnly"], family
        assert NATIVE_READ_MODELS.get(family) == (
            (entry["readModel"]["root"], entry["readModel"]["value"]) if entry["readModel"] else None
        ), family
    assert BLOCKER_ONLY_FAMILY_CODES == {
        "compound_product_relationships": "MAPPING_CONTEXT_COMPOUND_PRODUCT_RELATIONSHIP_UNAVAILABLE",
        "study_compound_dosing_relationships": "MAPPING_CONTEXT_STUDY_COMPOUND_DOSING_RELATIONSHIP_UNAVAILABLE",
    }
    assert CONTEXT_BLOCKER_CODES is BLOCKER_ONLY_FAMILY_CODES
    assert COLLECTION_MODE_FAMILIES == {"cdash_variables"}
    assert set(FAMILY_NODE_MODELS) == {
        "objective_templates", "endpoint_templates", "criteria_templates", "activity_instruction_templates",
        "timeframe_templates", "timeframes", "activities", "odm_forms", "odm_item_groups", "odm_items",
        "odm_conditions", "odm_methods",
    }
    assert FAMILY_NODE_MODELS["timeframes"] == ("TimeframeRoot", "TimeframeValue", "Timeframe")
    assert "activity_instruction_templates" in NATIVE_CREATE_FAMILIES
    assert canonicalize_family("edit_checks") == "odm_methods"


def test_every_row_family_is_a_registered_family_with_the_same_read_model_and_blocker_flag() -> None:
    for resource_type, row in ROWS.items():
        family = row["osbFamily"]
        if family is None:
            assert row["osbReadModel"] is None, resource_type
            continue
        assert family in FAMILIES, f"{resource_type}: {family} is not a registered family"
        assert row["osbFamilyBlockerOnly"] == FAMILIES[family]["blockerOnly"], resource_type
        assert row["osbReadModel"] == FAMILIES[family]["readModel"], resource_type


def test_capture_readback_types_follow_the_rows_and_keep_the_hashed_spelling() -> None:
    assert CAPTURE_FAMILY_TYPES == {
        "odm_forms": "OdmForm", "odm_item_groups": "OdmItemGroup", "odm_items": "OdmItem",
        "controlled_terminology_codelists": "CtCodelist", "controlled_terminology": "CtTerm",
    }
    assert ROWS["CTTerm"]["captureReadbackResourceType"] == "CtTerm"


def test_former_ruleset_names_resolve_to_the_canonical_resource_type() -> None:
    assert ROWS["CTTerm"]["formerNames"] == ["CtTerm"]
    assert ROWS["CTCodelist"]["formerNames"] == ["CtCodelist"]
    assert ROWS["DatasetVariable"]["formerNames"] == ["CdashVariable"]
    assert canonical_resource_type("CtTerm") == "CTTerm"
    assert canonical_resource_type("StudyVisit") == "StudyVisit"
    assert ROWS["OdmMethod"]["semanticMismatch"] is True
    assert "EDIT_CHECK" in ROWS["OdmMethod"]["cslAssertionTypes"]
