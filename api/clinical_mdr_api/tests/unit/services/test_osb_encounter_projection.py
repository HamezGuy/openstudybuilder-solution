"""Build visit bindings retain their own identity across the native intake."""

import json
from copy import deepcopy
from pathlib import Path
from typing import get_type_hints

import pytest
from jsonschema import Draft202012Validator

from clinical_mdr_api.generated.platform_contracts import osb_candidate_request_v1 as generated
from clinical_mdr_api.services.integrations import candidate_set as module
from clinical_mdr_api.tests.unit.services.test_candidate_set_v1 import (
    STUDY_ID, TENANT_ID, _code, _fixture, _refresh_intent_census, _set_contract_version,
)
from clinical_mdr_api.tests.unit.services.test_osb_candidate_request_projection import _intent
from clinical_mdr_api.tests.unit.services.test_osb_candidate_set_generation import (
    OPENAPI_HASH, STUDY, TENANT, FakeMapping, FakeQuery, _request_bundle,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "schemas/platform/osb-candidate-request-v1.schema.json").read_text()
)
CONTEXT_SCHEMA = SCHEMA["$defs"]["typedSourceIntent"]["properties"]["source"]["properties"]["context"]
VALIDATOR = Draft202012Validator({**CONTEXT_SCHEMA, "$defs": SCHEMA["$defs"]})


def _projection():
    return {
        "contract": "study-build-encounter-projection/1",
        "buildHash": "a" * 64,
        "encounters": [{
            "visitId": "visit-monthly", "visitLabel": "Monthly follow-up",
            "timepoint": None, "required": False, "derivedFrom": "soa_matrix",
            "evidenceRef": "table:row-4:col-2", "formObjectId": "form-1",
            "objectMetadata": {"formScope": ["F1", "F2"]},
            "properties": {"window": {"value": 0, "unit": "day"}, "condition": None},
            "occurrenceEvidence": [{"page": 14}, {"page": 15}],
        }],
        "retainedMetadata": {"producer": "fixture", "value": None},
    }


def _values(context, version="1.3.0"):
    values = list(_fixture())
    _set_contract_version(values, version)
    values[0]["typedSourceIntents"][0]["source"]["context"] = context
    _refresh_intent_census(values)
    return values


@pytest.mark.parametrize("encounters", [[], _projection()["encounters"]])
def test_complete_build_projection_survives_verification_generation_storage_and_replay(monkeypatch, encounters):
    projection = {**_projection(), "encounters": encounters}
    context = {"encounters": None, "encounterProjection": projection, "relationships": []}
    VALIDATOR.validate(context)
    values = _values(context)
    before = deepcopy(values)
    module.verify_candidate_request_artifact(*values[:2], TENANT_ID, STUDY_ID, *values[2:])
    assert values == before

    intent = _intent("source-with-build-encounters", family="criteria_templates")
    intent["source"]["context"] = deepcopy(context)
    generation = list(_request_bundle(intents=[intent]))
    _set_contract_version(generation, "1.3.0")
    store = FakeQuery()
    monkeypatch.setattr(module, "db", store)
    arguments = dict(
        request_payload=generation[0], artifact=generation[1], tenant_id=TENANT, platform_study_id=STUDY,
        osb_openapi_hash=OPENAPI_HASH, actor="service:osb", signed_envelope=generation[2],
        signature_verification=generation[3], mapping_context_service=FakeMapping(),
    )
    result = module.generate_candidate_set(**arguments)
    assert result["payload"]["candidateRecords"][0]["source"] == intent["source"]
    stored = json.loads(next(iter(store.sets.values()))[0])
    assert stored["candidateRecords"] == result["payload"]["candidateRecords"]
    replay = module.generate_candidate_set(**arguments)
    assert replay["replay"] is True
    assert replay["payload"] == result["payload"]


@pytest.mark.parametrize("projection", [
    None, [], {}, {**_projection(), "contract": "unknown/1"},
    {**_projection(), "buildHash": "A" * 64},
    {**_projection(), "buildHash": "a" * 63},
    {**_projection(), "buildHash": "a" * 64 + "\n"},
    {**_projection(), "encounters": None},
    {**_projection(), "encounters": [None]},
    {**_projection(), "encounters": [{"visitId": "visit-only"}]},
    *[{**_projection(), "encounters": [{**_projection()["encounters"][0], key: value}]}
      for key, value in [
          ("visitId", ""), ("evidenceRef", ""), ("visitLabel", 0), ("required", 1),
          ("timepoint", False), ("derivedFrom", []), ("formObjectId", None),
          ("objectMetadata", []), ("properties", None),
      ]],
])
def test_schema_and_native_intake_refuse_malformed_projection(projection):
    context = {"encounterProjection": projection}
    assert not VALIDATOR.is_valid(context)
    assert _code(tuple(_values(context))) == "OSB_TYPED_SOURCE_CONTEXT_INVALID"


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0"])
def test_legacy_request_cannot_claim_build_projection(version):
    assert _code(tuple(_values({"encounterProjection": _projection()}, version))) \
        == "OSB_SOURCE_CONTEXT_REQUEST_VERSION_REQUIRED"


def test_generated_model_exposes_projection_separately_from_legacy_source_encounters():
    context_fields = get_type_hints(generated.OsbTypedSourceContextV1)
    assert context_fields["encounterProjection"] is generated.OsbSourceEncounterProjectionV1
    assert get_type_hints(generated.OsbSourceEncounterProjectionV1)["encounters"] \
        == list[generated.OsbSourceEncounterRefV1]
    assert "encounters" in context_fields
