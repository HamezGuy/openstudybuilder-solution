"""Canonical Phase 4 OSB family map. Study and capture sections share one vocabulary.

Derived from the OSB vocabulary registry (osb_vocabulary_registry.py) since 2026-09-21. The tables kept by hand here
before, plus the family node models, blocker codes and collection-mode families that mapping_context.py kept as a
second copy, all come from the registry's `families` block now.
"""

from __future__ import annotations

from clinical_mdr_api.services.integrations.osb_vocabulary_registry import FAMILIES

FAMILY_ALIASES: dict[str, str] = {
    alias: family for family, entry in FAMILIES.items() for alias in entry["aliases"]
}


def _section_families(plane: str) -> dict[str, tuple[str, ...]]:
    sections: dict[str, list[str]] = {}
    for family, entry in FAMILIES.items():
        if entry["plane"] == plane:
            sections.setdefault(entry["section"], []).append(family)
    return {section: tuple(families) for section, families in sections.items()}


STUDY_SECTION_FAMILIES: dict[str, tuple[str, ...]] = _section_families("study")

CAPTURE_SECTION_FAMILIES: dict[str, tuple[str, ...]] = _section_families("capture")

BLOCKER_ONLY_FAMILIES = frozenset(family for family, entry in FAMILIES.items() if entry["blockerOnly"])

# The release blocker a blocker-only family raises in the mapping context.
BLOCKER_ONLY_FAMILY_CODES: dict[str, str] = {
    family: entry["blockerCode"] for family, entry in FAMILIES.items() if entry["blockerCode"]
}

# Families whose retrieval consults the SDTM/CDASH data models and IGs. Their
# prerequisites are prerequisites of THESE searches, not of the whole request.
COLLECTION_MODE_FAMILIES = frozenset(
    family for family, entry in FAMILIES.items() if entry["collectionModelPrerequisite"]
)

STUDY_FAMILIES = frozenset(
    family for families in STUDY_SECTION_FAMILIES.values() for family in families
)
CAPTURE_FAMILIES = frozenset(
    family for families in CAPTURE_SECTION_FAMILIES.values() for family in families
)
SUPPORTED_RESOURCE_FAMILIES = STUDY_FAMILIES | CAPTURE_FAMILIES | frozenset(FAMILY_ALIASES)

NATIVE_READ_MODELS: dict[str, tuple[str, str | None]] = {
    family: (entry["readModel"]["root"], entry["readModel"]["value"])
    for family, entry in FAMILIES.items() if entry["readModel"]
}

# Root label, value label and the model class the versioned library search reports as resourceType.
FAMILY_NODE_MODELS: dict[str, tuple[str, str, str]] = {
    family: (entry["readModel"]["root"], entry["readModel"]["value"], entry["model"])
    for family, entry in FAMILIES.items() if entry["model"]
}

NATIVE_CREATE_FAMILIES = frozenset(
    family for family, model in NATIVE_READ_MODELS.items() if model[1] is not None
)


def canonicalize_family(family: str) -> str:
    return FAMILY_ALIASES.get(family, family)


def executor_kind_for_canonical_family(family: str) -> str | None:
    canonical = canonicalize_family(family)
    if canonical in STUDY_FAMILIES:
        return "study"
    if canonical in CAPTURE_FAMILIES:
        return "capture"
    return None
