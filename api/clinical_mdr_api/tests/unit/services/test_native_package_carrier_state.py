"""Actual carrier-state reader with synthetic native ports and pinned contracts.

Run with --noconftest. The identity bridge is an explicit mocked native I/O
boundary here; these fixtures do not constitute source acceptance or signatures.
"""

from __future__ import annotations

import copy
import importlib
from types import SimpleNamespace
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest

from clinical_mdr_api.tests.unit.services import test_native_package_v2 as legacy
from clinical_mdr_api.tests.unit.services.test_native_package_artifact_custody import (
    custody as network_custody,
)

TENANT, STUDY = legacy.TENANT, legacy.STUDY
PLATFORM_BINDING = "00000000-0000-4000-8000-000000000041"
DOMAIN_BINDING = "00000000-0000-4000-8000-000000000042"
OTHER = "00000000-0000-4000-8000-000000000043"
canonical = legacy.canonical
hash_ref = legacy.h


def artifact(payload, kind, contract, version, schema, media="application/json"):
    artifact_id = str(uuid5(NAMESPACE_URL, f"synthetic-carrier-state:{kind}"))
    return legacy.PACKAGE["_artifact_ref"](
        {
            "artifactId": artifact_id,
            "artifactVersionId": str(
                uuid5(NAMESPACE_URL, artifact_id + canonical(payload))
            ),
            "kind": kind,
            "stableLocator": f"artifact://synthetic-carrier-state/{kind}/{artifact_id}",
            "payloadHash": hash_ref(payload, schema, media),
            "byteSize": len(canonical(payload).encode("utf-8")),
            "classification": "regulated-non-phi",
            "tenantId": TENANT,
            "region": "us-central1",
            "producerService": "osb.clinical-mdr-api",
            "producerEnvironment": "prototype",
            "producerVersion": "synthetic-test",
            "payloadContract": contract,
            "payloadContractVersion": version,
            "purpose": "synthetic-carrier-state-test",
            "createdAt": "2026-10-13T00:00:00Z",
        }
    )


def carrier_reference(payload):
    return artifact(
        payload,
        "accepted-study-exchange-carrier",
        "accuratrials.csl.AcceptedStudyExchangeCarrierV1",
        "1.0.0",
        "AcceptedStudyExchangeCarrierV1@1.0.0",
    )


def bind_bundle(request):
    bundle = request["bundle"]
    request["carrier"]["bundleArtifact"] = artifact(
        bundle,
        "accepted-canonical-study-exchange",
        "accuratrials.edc.StudyExchangeBundleV2",
        "2.0.0",
        "StudyExchangeBundleV2@2.0.0",
        "application/vnd.accuratrial.studybundle+json",
    )
    for name, field, schema in (
        (
            "definitionDocumentHash",
            "document",
            "AuthoredStudyDefinitionDocumentV1@1.0.0",
        ),
        (
            "definitionSelectionHash",
            "selection",
            "AuthoredStudyDefinitionSelectionV1@1.0.0",
        ),
    ):
        request["carrier"][name] = hash_ref(bundle["definition"][field], schema)
    request["carrier_artifact"] = carrier_reference(request["carrier"])


def request_for(port):
    root = port.root
    scope = {
        "platformTenantId": TENANT,
        "platformStudyId": STUDY,
        "nativeCslTenantId": TENANT,
        "nativeCslStudyId": STUDY,
    }
    source_basis = {
        "kind": "accepted-authored-canonical",
        "authorityArtifact": artifact(
            {"syntheticTransportReferenceOnly": True},
            "accepted-canonical-study-authority",
            "accuratrials.csl.AcceptedCanonicalStudyAuthorityV1",
            "1.0.0",
            "AcceptedCanonicalStudyAuthorityV1@1.0.0",
        ),
    }
    crosswalk = artifact(
        {"syntheticTransportReferenceOnly": True},
        "authored-study-member-crosswalk",
        "accuratrials.csl.AuthoredStudyMemberCrosswalkV1",
        "1.0.0",
        "AuthoredStudyMemberCrosswalkV1@1.0.0",
    )
    census = artifact(
        {"syntheticTransportReferenceOnly": True},
        "edc-study-bundle-export-census",
        "accuratrials.csl.EdcStudyBundleExportCensusV1",
        "1.0.0",
        "EdcStudyBundleExportCensusV1@1.0.0",
    )
    bundle = {
        "formatVersion": "2.0",
        "definition": {
            "document": copy.deepcopy(port.export["document"]),
            "selection": copy.deepcopy(port.selection),
        },
        "execution": {"extensions": {"literal.a[0]/~": None}},
        "source": {"syntheticTransportFixture": True},
    }
    identity = {
        "contractVersion": "1.1.0",
        "bindingId": PLATFORM_BINDING,
        "tenantId": TENANT,
        "platformStudyId": STUDY,
        "system": "osb",
        "namespace": "accuratrials-osb",
        "objectType": "study-draft-root",
        "nativeIdentity": root["nativeStudyId"],
        "nativeVersion": root["nativeVersion"],
        "verificationStatus": "verified",
        "verifiedBy": "synthetic-test",
        "verifiedAt": "2026-10-13T00:00:00Z",
        "validFrom": "2026-10-13T00:00:00Z",
        "createdAt": "2026-10-13T00:00:00Z",
        "createdBy": "synthetic-test",
        "validTo": None,
        "evidence": {
            "receiptId": OTHER,
            "receiptPayloadHash": hash_ref({}, "NativeIdentityBindingReceiptV1@1.0.0"),
            "signedEnvelopeHash": hash_ref({}, "SignedArtifactEnvelopeV1@1.0.0"),
            "trustBundleHash": hash_ref({}, "TrustBundleV1@1.0.0"),
            "trustBundleVersion": 1,
            "trustedSigningTime": "2026-10-13T00:00:00Z",
        },
    }
    request = {
        "tenant_id": TENANT,
        "platform_study_id": STUDY,
        "scope": scope,
        "source_basis": source_basis,
        "osb_binding": {
            "nativeTenantId": TENANT,
            "platformBindingId": PLATFORM_BINDING,
            "nativeStudyId": root["nativeStudyId"],
            "nativeVersion": root["nativeVersion"],
        },
        "osb_study_identity": identity,
        "native_evidence_basis": {
            "kind": "no-native-operations",
            "reason": "Authored execution is carried under its accepted canonical mapping and must be explicitly reviewed as extensions; no native-operation claim is made.",
        },
        "carrier": {
            "contractVersion": "AcceptedStudyExchangeCarrierV1@1.0.0",
            "scope": scope,
            "sourceBasisHash": hash_ref(
                source_basis, "AcceptedCanonicalSourceBasisV1@1.0.0"
            ),
            "edcBundleContentHash": {
                "profile": "edc-computeBundleContentHash/legacy",
                "value": "0" * 64,
            },
            "builder": {
                "id": "synthetic-state-reader",
                "version": "1",
                "sourceClosureHash": hash_ref(
                    {}, "AcceptedStudyExchangeSourceClosureV1@1.0.0"
                ),
            },
            "crosswalkArtifact": crosswalk,
            "exportCensusArtifact": census,
        },
        "bundle": bundle,
        "native_checkpoints": [],
    }
    bind_bundle(request)
    return request


@pytest.fixture
def reader(network_custody, monkeypatch):
    # network_custody installs a socket prohibition before any runtime import.
    del network_custody
    module = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_carrier_state"
    )
    native = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_state_v2"
    )
    port = SimpleNamespace(
        root={
            "bindingId": DOMAIN_BINDING,
            "nativeStudyId": "Study_Synthetic",
            "relationship": "LATEST_LOCKED",
            "nativeVersion": "1",
            "nativeStatus": "Locked",
            "versionTimestamp": "2026-10-13T00:00:00Z",
            "draftEndedAt": "2026-10-13T00:00:00Z",
            "title": None,
            "studyNumber": None,
            "acronym": None,
            "projectNumber": None,
        },
        export={
            "document": {
                "usdmVersion": "4.0.0",
                "study": {
                    "id": "Study_Synthetic",
                    "versions": [
                        {"id": "version-1", "studyDesigns": [{"id": "design-1"}]}
                    ],
                },
            },
            "mappingReport": {
                "state": "incomplete",
                "studyUid": "Study_Synthetic",
                "studyValueVersion": "1",
                "issues": [
                    {"code": "SYNTHETIC_MAPPING_ISSUE", "pointer": "/study/versions/0"}
                ],
            },
            "nativeRecords": [{"syntheticPortRow": True, "literal.a[0]/~": None}],
        },
        selection={"versionId": "version-1", "designId": "design-1"},
        reads=0,
        bridge_calls=[],
        definition_calls=[],
        binding_reads=0,
        change_on_reread=False,
        change_binding=False,
        reject_bridge=False,
    )

    def current_study(tenant_id, study_id, identity):
        assert (tenant_id, study_id) == (TENANT, STUDY)
        assert identity == {
            "nativeIdentity": port.root["nativeStudyId"],
            "nativeVersion": port.root["nativeVersion"],
        }
        port.reads += 1
        root = copy.deepcopy(port.root)
        if port.change_on_reread and port.reads > 1:
            root["title"] = "Concurrent native edit"
        return root, []

    def active_binding(tenant_id, study_id):
        assert (tenant_id, study_id) == (TENANT, STUDY)
        port.binding_reads += 1
        return {
            "bindingId": port.root["bindingId"],
            "nativeIdentity": port.root["nativeStudyId"],
            "nativeVersion": (
                "changed"
                if port.change_binding and port.binding_reads > 1
                else port.root["nativeVersion"]
            ),
        }

    def bridge(identity, binding, **scope):
        port.bridge_calls.append(copy.deepcopy((identity, binding, scope)))
        if port.reject_bridge:
            raise module.OsbCandidateSetError(
                scope["error_code"], "Synthetic receipt bridge refusal", 409
            )

    def read_definition(uid, study_value_version=None):
        port.definition_calls.append((uid, study_value_version))
        return copy.deepcopy(port.export)

    monkeypatch.setattr(module, "_current_study", current_study)
    monkeypatch.setattr(module, "active_osb_binding", active_binding)
    monkeypatch.setattr(module, "_assert_identity_binding", bridge)
    monkeypatch.setattr(
        native,
        "USDMService",
        lambda: SimpleNamespace(get_by_uid_with_report=read_definition),
    )
    return module, port


def test_pure_authored_state_has_no_fabricated_native_operations(reader):
    module, port = reader
    request = request_for(port)
    result = module.observe_carrier_native_state(**request)
    assert result["records"] == result["contentIndex"] == result["lineages"] == []
    assert (
        result["nativeState"]["nativeEvidenceBasis"] == request["native_evidence_basis"]
    )
    assert result["stateHash"] == hash_ref(
        result["nativeState"], "OsbPackageNativeStateV1@1.2.0"
    )
    assert (
        result["nativeState"]["carrierHash"]
        == request["carrier_artifact"]["payloadHash"]
    )
    assert (
        result["canonicalDefinition"]["mappingReport"] == port.export["mappingReport"]
    )
    assert (
        result["canonicalDefinition"]["nativeRecords"] == port.export["nativeRecords"]
    )
    assert result["canonicalDefinitionHash"] == hash_ref(
        result["canonicalDefinition"], "OsbCanonicalStudyDefinitionV1@1.0.0"
    )
    assert port.definition_calls == [("Study_Synthetic", "1")]
    assert port.reads == 2


def test_platform_binding_is_resolved_by_bridge_not_compared_to_domain_uuid(reader):
    module, port = reader
    result = module.observe_carrier_native_state(**request_for(port))
    assert result["root"]["bindingId"] == DOMAIN_BINDING != PLATFORM_BINDING
    assert len(port.bridge_calls) == 2
    for identity, binding, scope in port.bridge_calls:
        assert identity["bindingId"] == PLATFORM_BINDING
        assert binding["bindingId"] == DOMAIN_BINDING
        assert scope == {
            "tenant_id": TENANT,
            "platform_study_id": STUDY,
            "error_code": "OSB_CARRIER_IDENTITY_CONTEXT_MISMATCH",
        }


@pytest.mark.parametrize("field", ["document", "selection"])
def test_even_self_consistent_carrier_must_equal_actual_definition(reader, field):
    module, port = reader
    request = request_for(port)
    request["bundle"]["definition"][field] = {"changed": "different locked definition"}
    bind_bundle(request)
    with pytest.raises(module.OsbCandidateSetError) as error:
        module.observe_carrier_native_state(**request)
    assert error.value.code == "OSB_CARRIER_CANONICAL_DEFINITION_MISMATCH"


@pytest.mark.parametrize(
    "field", ["receiptPayloadHash", "signedEnvelopeHash", "trustBundleHash"]
)
@pytest.mark.parametrize("change", ["missing", "digest-only", "wrong-shape"])
def test_context_requires_complete_identity_custody_hashes(reader, field, change):
    module, port = reader
    request = request_for(port)
    evidence = request["osb_study_identity"]["evidence"]
    if change == "missing":
        evidence.pop(field)
    elif change == "digest-only":
        evidence[field] = {"value": evidence[field]["value"]}
    else:
        evidence[field] = {**evidence[field], "excludedPaths": ""}
    with pytest.raises(module.OsbCandidateSetError):
        module.observe_carrier_native_state(**request)
    assert not port.bridge_calls and not port.definition_calls


@pytest.mark.parametrize(
    "field", ["receiptId", "trustBundleVersion", "trustedSigningTime"]
)
def test_context_requires_identity_trust_metadata(reader, field):
    module, port = reader
    request = request_for(port)
    request["osb_study_identity"]["evidence"][field] = " "
    with pytest.raises(module.OsbCandidateSetError) as error:
        module.observe_carrier_native_state(**request)
    assert error.value.code == "OSB_CARRIER_CONTRACT_INVALID"
    assert not port.bridge_calls


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("scope", "platformTenantId", OTHER),
        ("osb_binding", "nativeTenantId", OTHER),
        ("osb_binding", "platformBindingId", DOMAIN_BINDING),
        ("osb_study_identity", "tenantId", OTHER),
        ("osb_study_identity", "platformStudyId", OTHER),
        ("osb_study_identity", "contractVersion", "1.0.0"),
    ],
)
def test_context_scope_and_identity_cannot_be_substituted(reader, target, field, value):
    module, port = reader
    request = request_for(port)
    request[target][field] = value
    with pytest.raises(module.OsbCandidateSetError):
        module.observe_carrier_native_state(**request)
    assert not port.bridge_calls


def test_identity_bridge_refusal_is_not_replaced_by_identifier_equality(reader):
    module, port = reader
    port.reject_bridge = True
    with pytest.raises(module.OsbCandidateSetError) as error:
        module.observe_carrier_native_state(**request_for(port))
    assert error.value.code == "OSB_CARRIER_IDENTITY_CONTEXT_MISMATCH"
    assert not port.definition_calls


@pytest.mark.parametrize(
    "change,code",
    [
        ("change_on_reread", "OSB_POST_CHECKPOINT_NATIVE_EDIT"),
        ("change_binding", "OSB_CARRIER_IDENTITY_CONTEXT_MISMATCH"),
    ],
)
def test_native_and_binding_reread_changes_refuse_state(reader, change, code):
    module, port = reader
    setattr(port, change, True)
    with pytest.raises(module.OsbCandidateSetError) as error:
        module.observe_carrier_native_state(**request_for(port))
    assert error.value.code == code


def test_current_draft_is_not_accepted_as_a_lock(reader):
    module, port = reader
    port.root.update(
        relationship="LATEST_DRAFT", nativeStatus="Draft", draftEndedAt=None
    )
    with pytest.raises(module.OsbCandidateSetError) as error:
        module.observe_carrier_native_state(**request_for(port))
    assert error.value.code == "OSB_NATIVE_STUDY_LOCK_REQUIRED"


def test_no_native_operations_rejects_inherited_checkpoint_arguments(reader):
    module, port = reader
    request = request_for(port)
    request["native_checkpoints"] = [({}, {})]
    with pytest.raises(module.OsbCandidateSetError) as error:
        module.observe_carrier_native_state(**request)
    assert error.value.code == "OSB_CARRIER_NATIVE_BASIS_MISMATCH"


def inherited_request(port):
    original_pair = legacy.SOURCE["_decision_pair"]
    original_query = legacy.SOURCE["FakeQuery"]

    def decision_pair(*args, **kwargs):
        values = original_pair(*args, **kwargs)
        values[1]["osbStudyIdentity"]["bindingId"] = DOMAIN_BINDING
        return values

    class NativeFixtureQuery(original_query):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.binding = (DOMAIN_BINDING, *self.binding[1:])

    # Set a schema-valid synthetic domain binding before the actual producers
    # hash the request/decision/checkpoint; never rewrite committed output.
    with (
        patch.dict(
            legacy.SOURCE,
            {"_decision_pair": decision_pair, "FakeQuery": NativeFixtureQuery},
        ),
        patch.object(legacy, "wire", legacy.wire.__wrapped__),
    ):
        retained = legacy.setup()
    # MemoryStore.set_checkpoint creates a shortened legacy-only descriptor.
    # Preserve the complete actual producer descriptor for the V3 boundary.
    retained.checkpoint_artifact = copy.deepcopy(retained.built["artifactRef"])
    observed = legacy.state(retained)
    port.root = copy.deepcopy(observed["root"])
    definition = observed["canonicalDefinition"]
    port.export = {
        key: copy.deepcopy(definition[key])
        for key in ("document", "mappingReport", "nativeRecords")
    }
    port.selection = copy.deepcopy(definition["selection"])
    request = request_for(port)
    request["native_evidence_basis"] = {
        "kind": "verified-native-operations",
        "checkpointArtifacts": [retained.checkpoint_artifact],
        "nativeEvidenceSetArtifacts": [retained.applied["artifactRef"]],
    }
    request["native_checkpoints"] = [
        (retained.checkpoint_artifact, retained.checkpoint)
    ]
    return request, retained, observed


def test_real_legacy_custody_loader_preserves_inherited_native_records(
    reader, monkeypatch
):
    module, port = reader
    request, retained, observed = inherited_request(port)
    calls = []

    def legacy_read(**arguments):
        calls.append(copy.deepcopy(arguments))
        # The established legacy harness executes its actual source definitions
        # with only native I/O ports replaced. Never return a fabricated census.
        return legacy.STATE["load_checkpoint_native_state"](**arguments)

    monkeypatch.setattr(module, "load_checkpoint_native_state", legacy_read)
    result = module.observe_carrier_native_state(**request)
    assert len(calls) == 1
    assert result["records"] == observed["records"]
    assert result["contentIndex"] == observed["contentIndex"]
    assert (
        result["lineages"][0]["state"]["nativeEvidenceSetArtifact"]
        == retained.applied["artifactRef"]
    )


def test_inherited_tuple_membership_cannot_repeat_one_checkpoint_and_skip_another(
    reader, monkeypatch
):
    module, port = reader
    request, retained, _ = inherited_request(port)
    second_payload = {**retained.checkpoint, "checkpointVersionId": OTHER}
    fields = {
        key: value
        for key, value in retained.checkpoint_artifact.items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    second_ref = legacy.PACKAGE["_artifact_ref"](
        {
            **fields,
            "artifactVersionId": OTHER,
            "payloadHash": hash_ref(second_payload, "TransformationCheckpointV1@1.0.0"),
            "byteSize": len(canonical(second_payload).encode("utf-8")),
        }
    )
    request["native_evidence_basis"]["checkpointArtifacts"].append(second_ref)
    request["native_checkpoints"].append(
        copy.deepcopy(request["native_checkpoints"][0])
    )
    monkeypatch.setattr(
        module,
        "load_checkpoint_native_state",
        legacy.STATE["load_checkpoint_native_state"],
    )
    with pytest.raises(module.OsbCandidateSetError) as error:
        module.observe_carrier_native_state(**request)
    assert error.value.code == "OSB_CARRIER_NATIVE_BASIS_MISMATCH"
