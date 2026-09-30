"""Exact requests emitted by the CSL CLI through the registered native route."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from clinical_mdr_api.routers.studies import studies
from clinical_mdr_api.tests.fixtures.null_adjudication import STUDY_UID, native_harness
from clinical_mdr_api.tests.unit.services.test_null_adjudication_http import client

WIRE = json.loads(
    (
        Path(__file__).resolve().parents[2] / "fixtures/null_adjudication_wire.json"
    ).read_text(encoding="utf-8")
)
SECTIONS = [
    "identification_metadata",
    "version_metadata",
    "high_level_study_design",
    "study_population",
    "study_intervention",
]


@pytest.mark.parametrize("entry", WIRE["cases"], ids=lambda entry: entry["name"])
def test_native_consumes_exact_cli_request_and_replays_its_receipt_and_metadata(entry):
    with native_harness() as harness, patch.object(
        studies, "StudyService", return_value=harness.service
    ):
        api, _ = client(harness)
        before = api.get(
            f"/{STUDY_UID}",
            params=[("include_sections", section) for section in SECTIONS],
        )
        assert before.status_code == 200
        for section, fields in entry["initialMetadata"].items():
            for name, value in fields.items():
                assert before.json()["current_metadata"][section][name] == value

        response = api.patch(f"/{STUDY_UID}/null-adjudications", json=entry["request"])
        assert response.status_code == 200, response.text
        assert response.json() == entry["nativeReceipt"]
        assert response.json()["request_hash"] == entry["requestHash"]
        after = api.get(
            f"/{STUDY_UID}",
            params=[("include_sections", section) for section in SECTIONS],
        )
        assert after.status_code == 200, after.text
        actual = after.json()
        expected = deepcopy(entry["nativeReadback"])
        # Each real service edit has a new timestamp. All clinical metadata,
        # identity, parent links and returned term presentation remain compared.
        actual["current_metadata"].pop("version_metadata")
        expected["current_metadata"].pop("version_metadata")
        assert actual == expected
        assert (
            len([call for call in harness.repository.calls if call[0] == "save"]) == 1
        )
        api.close()
