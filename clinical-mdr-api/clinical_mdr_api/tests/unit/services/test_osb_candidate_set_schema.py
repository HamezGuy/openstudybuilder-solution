"""The real candidate producer must satisfy its published wire schema."""

import importlib
import json
from copy import deepcopy
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from clinical_mdr_api.services.integrations import candidate_set as module
from clinical_mdr_api.services.integrations.osb_family_map import (
    SUPPORTED_RESOURCE_FAMILIES,
    canonicalize_family,
)
from clinical_mdr_api.tests.unit.services.test_candidate_set_v1 import _refresh
from clinical_mdr_api.tests.unit.services.test_osb_candidate_request_projection import (
    _intent,
    _routed,
)
from clinical_mdr_api.tests.unit.services.test_osb_candidate_set_generation import (
    OPENAPI_HASH,
    STUDY,
    TENANT,
    FakeMapping,
    FakeQuery,
    _external_binding_bundle,
    _request_bundle,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "schemas/platform/osb-candidate-set-v1.schema.json").read_text()
)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def _produce(monkeypatch, identity_version="legacy", family="odm_items"):
    source_intent = _intent("source-field", family=family)
    if family == "study_metadata":
        from clinical_mdr_api.services.integrations import study_metadata_mapping
        from clinical_mdr_api.tests.unit.services.test_study_metadata_mapping import COUNT, NativePort, intent as metadata_intent

        source_intent["nativeStudyOperation"] = metadata_intent("source-field", COUNT, 30000)["nativeStudyOperation"]
        source_intent["source"]["assertionType"] = "STUDY_DESIGN_ATTRIBUTE"
        source_intent["source"]["values"] = [
            {"name": "attribute", "sourcePath": "/fields/attribute", "valueType": "string", "value": "TARGET_ENROLLMENT"},
            {"name": "numericValue", "sourcePath": "/fields/numericValue", "valueType": "integer", "value": 30000},
        ]
        native_port = NativePort("Study_990001")
        monkeypatch.setattr(study_metadata_mapping, "NativeStudyMetadataPort", lambda: native_port)
    values = list(_request_bundle(
        intents=[source_intent],
        routed=[_routed("retained-narrative", "governed_extension"), _routed("unresolved-design")],
    ))
    store = FakeQuery()
    payload = values[0]
    intent = payload["typedSourceIntents"][0]
    intent["source"]["classification"] = {
        "candidateSemanticsVersion": "candidate-semantics/1.0",
        "role": "data_field",
        "sourceDocumentKind": "CRF",
    }
    intent["createOption"]["requestedNativeType"] = None
    payload["contractVersion"] = "OsbCandidateRequestV1@1.1.0"
    values[1]["payloadContractVersion"] = "1.1.0"
    values[2]["signingStatement"]["payloadContractVersion"] = "1.1.0"
    if family == "study_metadata":
        payload["contractVersion"] = "OsbCandidateRequestV1@1.2.0"
        values[1]["payloadContractVersion"] = "1.2.0"
        values[2]["signingStatement"]["payloadContractVersion"] = "1.2.0"
    identity = payload["osbStudyIdentity"]
    if identity_version == "1.1.0":
        store, external_values = _external_binding_bundle()
        identity = deepcopy(external_values[0]["osbStudyIdentity"])
        payload["osbStudyIdentity"] = identity
        identity["evidence"].update({
            "trustBundleVersion": 1,
            "trustBundleHash": module.canonical_json_hash_ref(
                {"fixture": "trust"}, schema_version="SigningTrustBundleV1@1.0.0",
            ),
            "trustedSigningTime": payload["createdAt"],
        })
    elif identity_version == "1.0.0":
        identity["evidence"] = {
            "receiptId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "receiptPayloadHash": module.canonical_json_hash_ref(
                {"fixture": "receipt"}, schema_version="NativeIdentityBindingReceiptV1@1.0.0",
            ),
        }
    if identity_version != "legacy":
        identity.update({
            "verifiedBy": "service:platform-registry",
            "verifiedAt": payload["createdAt"],
            "validFrom": payload["createdAt"],
            "validTo": None,
            "createdAt": payload["createdAt"],
            "createdBy": "service:platform-registry",
            "supersedesBindingId": None,
        })
    # Source hashes are refreshed before the production verification boundary.
    for row in payload["inputConservation"]["rows"]:
        if row["target"] is not None:
            row["target"]["valueHash"] = module.canonical_json_hash_ref(
                intent, schema_version="OsbTypedSourceIntentV1@1.0.0",
            )
    payload["inputConservation"]["rowSetHash"] = module.canonical_json_hash_ref(
        payload["inputConservation"]["rows"], schema_version="ConservationCensusRowsV1@1.0.0",
    )
    _refresh(values)
    monkeypatch.setattr(module, "db", store)
    generated = module.generate_candidate_set(
        request_payload=values[0], artifact=values[1],
        tenant_id=TENANT, platform_study_id=STUDY,
        osb_openapi_hash=OPENAPI_HASH, actor="service:osb",
        signed_envelope=values[2], signature_verification=values[3],
        mapping_context_service=FakeMapping(native=family != "study_metadata"),
    )
    return generated["payload"], payload


@pytest.mark.parametrize("identity_version", ["legacy", "1.0.0", "1.1.0"])
def test_real_producer_retains_all_identity_source_and_routed_members(monkeypatch, identity_version):
    candidate_set, request = _produce(monkeypatch, identity_version)
    VALIDATOR.validate(candidate_set)
    assert candidate_set["osbStudyIdentity"] == request["osbStudyIdentity"]
    assert candidate_set["candidateRecords"][0]["source"] == request["typedSourceIntents"][0]["source"]
    assert candidate_set["candidateRecords"][0]["evidence"] == request["typedSourceIntents"][0]["evidence"]
    assert request["typedSourceIntents"][0]["createOption"]["requestedNativeType"] is None
    # This source-retention fixture does not declare a typed STUDY_ITEM.
    # Its nullable request survives; the native planner must refuse creation.
    assert candidate_set["candidateRecords"][0]["createOption"] is None
    assert "OSB_CAPTURE_SOURCE_TYPE_UNSUPPORTED" in candidate_set["candidateRecords"][0]["blockers"]
    assert len(candidate_set["candidateRecords"]) == 1
    assert len(candidate_set["deferredMembers"]) == 2
    assert candidate_set["conservation"]["counts"]["rows"] == 3


@pytest.mark.parametrize("family", sorted(SUPPORTED_RESOURCE_FAMILIES))
def test_every_supported_native_family_and_request_alias_conforms(monkeypatch, family):
    candidate_set, _ = _produce(monkeypatch, family=family)
    VALIDATOR.validate(candidate_set)
    record = candidate_set["candidateRecords"][0]
    assert record["requestedResourceFamily"] == family
    assert record["resourceFamily"] == canonicalize_family(family)


def test_unknown_candidate_fields_and_incomplete_identity_evidence_remain_invalid(monkeypatch):
    candidate_set, _ = _produce(monkeypatch, "1.1.0")
    candidate_set["candidateRecords"][0]["inventedNativeApproval"] = True
    assert not VALIDATOR.is_valid(candidate_set)
    del candidate_set["candidateRecords"][0]["inventedNativeApproval"]
    del candidate_set["osbStudyIdentity"]["evidence"]["trustBundleHash"]
    assert not VALIDATOR.is_valid(candidate_set)


@pytest.mark.parametrize(("name", "container"), [
    ("osb_candidate_request_v1", "OsbCandidateRequestV1"),
    ("osb_candidate_set_v1", "OsbCandidateSetV1"),
    ("study_mapping_decision_statement_v1", "StudyMappingDecisionStatementV1"),
    ("study_mapping_decision_v1", "StudyMappingDecisionStatementV1"),
])
def test_generated_python_contracts_import_and_resolve_both_identity_shapes(name, container):
    contract = importlib.import_module(f"clinical_mdr_api.generated.platform_contracts.{name}")
    identity = get_type_hints(getattr(contract, container))["osbStudyIdentity"]
    assert {value.__name__ for value in get_args(identity)} == {
        "OsbLegacyStudyIdentityV1", "OsbExternalStudyIdentityV1",
    }
