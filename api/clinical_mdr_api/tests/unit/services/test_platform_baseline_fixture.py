import json
from pathlib import Path

import pytest

from clinical_mdr_api.models.integrations.study_authority import StudyAuthoritySnapshot
from clinical_mdr_api.services.integrations.study_authority import _canonical_hash


_HERE = Path(__file__).resolve()
FIXTURE_ROOT = (
    (_HERE.parents[6] if len(_HERE.parents) > 6 else Path("/nonexistent"))
    / "CommandCenter"
    / "fixtures"
    / "platform-control-plane"
    / "baseline-v1"
)
if not FIXTURE_ROOT.is_dir():
    # The Command Center baseline is a sibling checkout on the estate host only.
    pytest.skip(
        f"Command Center baseline not beside this checkout: {FIXTURE_ROOT}",
        allow_module_level=True,
    )


def load_snapshot() -> dict:
    return json.loads(
        (FIXTURE_ROOT / "osb-authority-snapshot-v1.json").read_text(encoding="utf-8")
    )


def test_platform_baseline_matches_current_osb_authority_contract_and_hash() -> None:
    payload = load_snapshot()
    snapshot = StudyAuthoritySnapshot(**payload)
    encoded = snapshot.model_dump(mode="json")
    content_hash = encoded.pop("content_hash")

    assert snapshot.schema_version == "osb-authority/1.2"
    assert snapshot.mapping_authority == "OpenStudyBuilder"
    assert snapshot.authority_mode == "enforced"
    assert snapshot.release_eligible is False
    assert [blocker.code for blocker in snapshot.blockers] == ["OSB_STUDY_NOT_RELEASED"]
    assert content_hash == _canonical_hash(encoded)


def test_platform_baseline_authority_counts_are_reproducible_and_nonzero() -> None:
    snapshot = StudyAuthoritySnapshot(**load_snapshot())

    assert snapshot.counts.objectives == len(snapshot.study_objectives) == 1
    assert snapshot.counts.endpoints == len(snapshot.study_endpoints) == 1
    assert snapshot.counts.criteria == len(snapshot.study_criteria) == 1
    assert snapshot.counts.arms == len(snapshot.study_arms) == 2
    assert snapshot.counts.visits == len(snapshot.study_visits) == 3
    assert snapshot.counts.odm_forms == len(snapshot.study_odm_metadata["forms"]) == 2
    assert snapshot.counts.odm_items == len(snapshot.study_odm_metadata["items"]) == 4


def test_platform_baseline_authority_mutation_changes_content_hash() -> None:
    payload = load_snapshot()
    expected = payload.pop("content_hash")
    payload["native_study"]["title"] = "Mutated fixture title"

    assert _canonical_hash(payload) != expected

