import json
import math
from pathlib import Path

import pytest

from ..utils.osb_proposal_db import (
    CANONICAL_JSON_VERSION,
    _canonical_json as worker_canonical_json,
)


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "canonical-json-v1.json").read_text(
        encoding="utf-8"
    )
)


def test_worker_matches_every_cross_language_vector():
    assert FIXTURE["canonicalizationVersion"] == CANONICAL_JSON_VERSION
    for vector in FIXTURE["vectors"]:
        assert worker_canonical_json(vector["input"]) == vector["canonical"], vector["name"]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_worker_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError, match="CANONICAL_JSON_NON_FINITE_NUMBER"):
        worker_canonical_json(value)


def test_worker_rejects_non_json_types_and_non_string_keys():
    with pytest.raises(TypeError, match="CANONICAL_JSON_UNSUPPORTED_TYPE"):
        worker_canonical_json({1, 2})
    with pytest.raises(TypeError, match="CANONICAL_JSON_OBJECT_KEY_NOT_STRING"):
        worker_canonical_json({1: "not-json-object-identity"})