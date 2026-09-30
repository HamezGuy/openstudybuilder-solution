import json
import math
from pathlib import Path

import pytest

from clinical_mdr_api.services.integrations.canonical_json import (
    CANONICAL_JSON_VERSION,
    canonical_json,
)

FIXTURE_PATH = (
    Path(__file__).parents[5]
    / "import_sponsor_data"
    / "importers"
    / "tests"
    / "fixtures"
    / "canonical-json-v1.json"
)
if not FIXTURE_PATH.is_file():
    # The API test container mounts api/ only; the shared vectors live beside it.
    pytest.skip(
        f"cross-language vectors not in this checkout: {FIXTURE_PATH}",
        allow_module_level=True,
    )
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_api_matches_every_cross_language_vector():
    assert FIXTURE["canonicalizationVersion"] == CANONICAL_JSON_VERSION
    for vector in FIXTURE["vectors"]:
        assert canonical_json(vector["input"]) == vector["canonical"], vector["name"]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_api_rejects_non_finite_numbers(value):
    # The platform contract's PlatformHashError is a ValueError carrying the code.
    with pytest.raises(ValueError) as error:
        canonical_json(value)
    assert error.value.code == "CANONICAL_JSON_NON_FINITE_NUMBER"
