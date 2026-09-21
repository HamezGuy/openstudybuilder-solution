"""The OSB vocabulary registry (owned by ClinicalSemanticLayer, copied here byte for byte) must agree with the tables
the executors read: proposal_target_capabilities.TARGET_CAPABILITIES and its native sets, and osb_family_map.

One object used to be named five ways across the two repositories with nothing proving the names agreed
(overlap report F7, gap G-I7). The registry states the correspondence once; this test fails the moment an OSB
table moves away from it, and the CSL test fails the moment the ruleset or the CSL copies do. Correspondence is
not identity: a row says which kind of correspondence it is.
"""

import hashlib
import json
from pathlib import Path

from clinical_mdr_api.services.integrations.osb_family_map import (
    BLOCKER_ONLY_FAMILIES,
    FAMILY_ALIASES,
    NATIVE_READ_MODELS,
    SUPPORTED_RESOURCE_FAMILIES,
)
from clinical_mdr_api.services.integrations.proposal_target_capabilities import (
    NATIVE_CREATE_REQUEST_RESOURCE_TYPES,
    NATIVE_DECLINABLE_RESOURCE_TYPES,
    NATIVE_DUAL_MODE_RESOURCE_TYPES,
    NATIVE_EXECUTOR_RESOURCE_TYPES,
    NATIVE_SELECTION_RESOURCE_TYPES,
    TARGET_CAPABILITIES,
)

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "schemas/platform/osb-vocabulary-registry-v1.json"
REGISTRY = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
ROWS = {row["osbResourceType"]: row for row in REGISTRY["rows"]}


def _canonical_hash(rows: list[dict]) -> str:
    # The platform's canonical JSON: keys sorted, no whitespace, non-ASCII kept; identical to CSL canonical-json.ts for
    # the string, boolean, null, array and object values a registry row holds.
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_registry_is_the_expected_contract_and_its_hash_covers_its_rows() -> None:
    assert REGISTRY["contractVersion"] == "OsbVocabularyRegistryV1@1.0.0"
    assert REGISTRY["owner"] == "ClinicalSemanticLayer"
    assert REGISTRY["registryHash"] == _canonical_hash(REGISTRY["rows"])
    assert len(ROWS) == len(REGISTRY["rows"]), "a resource type is named twice"


def test_every_target_capability_has_a_row_with_the_same_kind_and_native_sets() -> None:
    for resource_type, capability in TARGET_CAPABILITIES.items():
        row = ROWS.get(resource_type)
        assert row is not None, f"{resource_type} has no registry row"
        assert row["osbCapability"] == capability, resource_type
        assert row["osbNativeExecutor"] == (resource_type in NATIVE_EXECUTOR_RESOURCE_TYPES), resource_type
        assert row["osbSelection"] == (resource_type in NATIVE_SELECTION_RESOURCE_TYPES), resource_type
        assert row["osbDualMode"] == (resource_type in NATIVE_DUAL_MODE_RESOURCE_TYPES), resource_type
        assert row["osbCreateRequest"] == (resource_type in NATIVE_CREATE_REQUEST_RESOURCE_TYPES), resource_type
        assert row["osbDeclinable"] == (resource_type in NATIVE_DECLINABLE_RESOURCE_TYPES), resource_type
    for resource_type in ROWS:
        assert resource_type in TARGET_CAPABILITIES, f"registry row {resource_type} is not an OSB resource type"


def test_every_family_a_row_names_is_a_family_the_executors_know_with_the_same_read_model() -> None:
    for resource_type, row in ROWS.items():
        family = row["osbFamily"]
        if family is None:
            assert row["osbReadModel"] is None, resource_type
            continue
        assert family in SUPPORTED_RESOURCE_FAMILIES, f"{resource_type}: {family} is not a supported family"
        assert row["osbFamilyBlockerOnly"] == (family in BLOCKER_ONLY_FAMILIES), resource_type
        read_model = NATIVE_READ_MODELS.get(family)
        expected = {"root": read_model[0], "value": read_model[1]} if read_model else None
        assert row["osbReadModel"] == expected, f"{resource_type}: read model differs for {family}"


def test_family_aliases_are_recorded_and_the_edit_check_alias_is_marked_as_a_semantic_mismatch() -> None:
    assert REGISTRY["sources"]["osbFamilyAliases"] == FAMILY_ALIASES
    assert ROWS["OdmMethod"]["semanticMismatch"] is True
    assert "EDIT_CHECK" in ROWS["OdmMethod"]["cslAssertionTypes"]


def test_the_ruleset_names_that_differ_from_osb_are_registered_aliases() -> None:
    assert ROWS["CTTerm"]["rulesetAliases"] == ["CtTerm"]
    assert ROWS["CTCodelist"]["rulesetAliases"] == ["CtCodelist"]
    assert ROWS["DatasetVariable"]["rulesetAliases"] == ["CdashVariable"]
