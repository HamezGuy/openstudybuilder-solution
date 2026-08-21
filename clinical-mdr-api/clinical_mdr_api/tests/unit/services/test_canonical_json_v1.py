import json
import math
from pathlib import Path

import pytest

from clinical_mdr_api.services.integrations.canonical_json import (
    CANONICAL_JSON_VERSION,
    canonical_json,
)


FIXTURE = json.loads(
    (
        Path(__file__).parents[5]
        / "studybuilder-import"
        / "importers"
        / "tests"
        / "fixtures"
        / "canonical-json-v1.json"
    ).read_text(encoding="utf-8")
)


def test_api_matches_every_cross_language_vector():
    assert FIXTURE["canonicalizationVersion"] == CANONICAL_JSON_VERSION
    for vector in FIXTURE["vectors"]:
        assert canonical_json(vector["input"]) == vector["canonical"], vector["name"]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_api_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError, match="CANONICAL_JSON_NON_FINITE_NUMBER"):
        canonical_json(value)
