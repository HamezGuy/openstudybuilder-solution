"""The OSB vocabulary registry, loaded once and verified (2026-09-21, plan item P1 of the ontology layer completion).

`schemas/platform/osb-vocabulary-registry-v1.json` is a byte copy of the ClinicalSemanticLayer-owned registry
(`packages/mappings/osb-vocabulary-registry-v1.json`). It states, once, how an OSB proposal resource type, its
capability kind, its native-executor flags, its candidate family, the family's section, plane, read model, blocker
code and aliases, and its CSL counterpart correspond. `proposal_target_capabilities.py`, `osb_family_map.py`,
`mapping_context.py` and `native_capture_projection.py` derive their tables from it; before this module each of them
kept its own copy and nothing proved the copies agreed.

The registry hash is recomputed here over the families and the rows with the platform's canonical JSON (keys sorted,
no whitespace, UTF-8), so a copy edited by hand without restamping refuses to load.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REGISTRY_CONTRACT_VERSION = "OsbVocabularyRegistryV1@1.1.0"
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "schemas" / "platform" / "osb-vocabulary-registry-v1.json"


class OsbVocabularyRegistryError(RuntimeError):
    """The registry copy is not the expected contract or its hash does not cover its content."""


def canonical_registry_hash(families: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps({"families": families, "rows": rows}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("contractVersion") != REGISTRY_CONTRACT_VERSION or not isinstance(registry.get("rows"), list) \
            or not isinstance(registry.get("families"), dict):
        raise OsbVocabularyRegistryError(f"OSB_VOCABULARY_REGISTRY_INVALID: {path}")
    names = [row["osbResourceType"] for row in registry["rows"]]
    if len(set(names)) != len(names):
        raise OsbVocabularyRegistryError("OSB_VOCABULARY_REGISTRY_INVALID: a resource type is named twice")
    expected = canonical_registry_hash(registry["families"], registry["rows"])
    if registry.get("registryHash") != expected:
        raise OsbVocabularyRegistryError(
            f"OSB_VOCABULARY_REGISTRY_HASH_MISMATCH: {registry.get('registryHash')} on disk, {expected} over its content"
        )
    return registry


REGISTRY: dict[str, Any] = load_registry()
ROWS: dict[str, dict[str, Any]] = {row["osbResourceType"]: row for row in REGISTRY["rows"]}
FAMILIES: dict[str, dict[str, Any]] = REGISTRY["families"]
FORMER_NAMES: dict[str, str] = {
    former: row["osbResourceType"] for row in REGISTRY["rows"] for former in row["formerNames"]
}


def canonical_resource_type(name: str) -> str:
    """The canonical OSB resource type for a name any ruleset has used (CtTerm -> CTTerm)."""
    return FORMER_NAMES.get(name, name)


def resource_types_where(flag: str) -> frozenset[str]:
    return frozenset(name for name, row in ROWS.items() if row.get(flag) is True)
