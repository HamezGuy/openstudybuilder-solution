"""Actual CSL builders -> OSB custody/capture -> review -> package, no live I/O.

The committed complete CSL fixture is synthetic source material, never runtime
authority. Native read ports, registry bridge and database are test doubles;
approval signatures below are deliberately synthetic prerequisites.
"""

import copy
import importlib
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from clinical_mdr_api.tests.unit.services import test_native_package_v2 as legacy
from clinical_mdr_api.tests.unit.services.test_native_package_artifact_custody import (
    RetainedArtifacts,
)
from clinical_mdr_api.tests.unit.services.test_native_package_artifact_custody import (
    custody as network_custody,
)
from clinical_mdr_api.tests.unit.services.test_native_package_carrier_inspection import (
    InspectionStore,
)

FIXTURE = Path(__file__).with_name("data")
FIXTURE_HASHES = {
    "native-single": "b36d33ed8bebfcfc14234b6afd8c6a64a79d13ade8c974442d1bb305d44b1c86",
    "native-single-compiled": "385470983b954cafe69af885d6a82155cc173429aa586465b053f8e5f028cb4f",
    "": "c5e9d5f2b6f3071b968753ba5d87ea8eec04872cf56b2e8bb525af2da39d06cd",
}
canonical = legacy.canonical
h = legacy.h
STAMP = "2026-10-13T00:00:00Z"


def csl_build(value, phase):
    legacy.require_csl()
    module = (
        legacy.CSL / "packages/semantic-core/src/accepted-study-exchange-carrier-v1.ts"
    ).as_uri()
    hashes = (
        legacy.CSL / "packages/semantic-core/src/platform-hash-signing-v1.ts"
    ).as_uri()
    script = f"""
    import {{buildAuthoredStudyMemberCrosswalkV1,buildAcceptedStudyExchangeCarrierV1,buildTransformationCheckpointV2}} from {json.dumps(module)};
    import {{platformCanonicalHashRef,platformCanonicalJson,platformArtifactRefV1}} from {json.dumps(hashes)};
    let text=''; for await(const chunk of process.stdin) text+=chunk;
    const input=JSON.parse(text), f=input.fixture;
    const get=kind=>f.artifacts.find(item=>item.artifact.kind===kind).artifact;
    const rebind=(ref,payload)=>{{const {{contractVersion,descriptorHash,...fields}}=ref;
      return platformArtifactRefV1({{...fields,byteSize:Buffer.byteLength(platformCanonicalJson(payload)),
        payloadHash:platformCanonicalHashRef(payload,ref.payloadHash.schemaVersion,ref.payloadHash.mediaType)}});}};
    if(input.phase==='prepare'){{
      const osbBinding={{...f.crosswalk.osbBinding,nativeTenantId:f.scope.platformTenantId}};
      f.crosswalk=buildAuthoredStudyMemberCrosswalkV1({{bundle:f.bundle,scope:f.scope,
        sourceBasisHash:f.carrier.sourceBasisHash,osbBinding,rows:f.crosswalk.rows,
        authority:f.authority,authorityHash:f.sourceBasis.authorityArtifact.payloadHash}});
      const item=f.artifacts.find(item=>item.artifact.kind==='authored-study-member-crosswalk');
      item.payload=f.crosswalk; item.artifact=rebind(item.artifact,f.crosswalk);
    }}
    const arguments_={{projectionPackage:f.projectionPackage,scope:f.scope,sourceBasis:f.sourceBasis,
      authority:f.authority,bundle:f.bundle,bundleArtifact:get('accepted-canonical-study-exchange'),
      crosswalk:f.crosswalk,crosswalkArtifact:get('authored-study-member-crosswalk'),
      census:f.census,exportCensusArtifact:get('edc-study-bundle-export-census'),
      builder:{{id:f.carrier.builder.id,version:f.carrier.builder.version}}}};
    if(input.phase==='prepare'){{
      const built=buildAcceptedStudyExchangeCarrierV1(arguments_); Object.assign(f,built);
      const item=f.artifacts.find(item=>item.artifact.kind==='accepted-study-exchange-carrier');
      item.payload=f.carrier; item.artifact=rebind(item.artifact,f.carrier);
      console.log(JSON.stringify(f));
    }}else{{
      const built=buildTransformationCheckpointV2({{...arguments_,carrier:f.carrier,
        carrierArtifact:get('accepted-study-exchange-carrier'),conservation:f.conservation,
        nativeEvidenceBasis:f.nativeEvidenceBasis,nativeState:input.nativeState,
        nativeStateArtifact:input.nativeStateArtifact,canonicalDefinition:input.canonicalDefinition,
        checkpointId:f.checkpoint.checkpointId,checkpointVersionId:f.checkpoint.checkpointVersionId,
        createdAt:f.checkpoint.createdAt,createdBy:f.checkpoint.createdBy}});
      console.log(JSON.stringify({{...built,artifact:rebind(get('transformation-checkpoint'),built.checkpoint)}}));
    }}
    """
    output = subprocess.run(
        [
            os.environ.get("NODE", "node"),
            "--import",
            "tsx",
            "--input-type=module",
            "-e",
            script,
        ],
        input=json.dumps({**value, "phase": phase}),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=legacy.CSL,
        timeout=40,
        check=False,
    )
    assert output.returncode == 0, output.stderr
    return json.loads(output.stdout)


@lru_cache(maxsize=3)
def prepared(profile="native-single"):
    suffix = f"-{profile}" if profile else ""
    raw = (FIXTURE / f"authored-carrier-csl{suffix}-fixture.json").read_bytes()
    assert legacy.HASHING["sha256_bytes"](raw) == "sha256:" + FIXTURE_HASHES[profile]
    return csl_build({"fixture": json.loads(raw)}, "prepare")


class Store:
    def __init__(self):
        self.artifacts, self.inspection = RetainedArtifacts(), InspectionStore()
        self.captured, self.conservation, self.reviews, self.packages = (
            set(),
            {},
            {},
            {},
        )
        self.writes = []

    def cypher_query(self, query, parameters):
        if "OsbCarrierInspection" in query:
            return self.inspection.cypher_query(query, parameters)
        if "OsbSpecialistReviewEvidenceV2" in query or "OsbNativePackageV2" in query:
            records = (
                self.reviews
                if "OsbSpecialistReviewEvidenceV2" in query
                else self.packages
            )
            key = (
                parameters["tenant_id"],
                parameters["platform_study_id"],
                parameters["version_id"],
            )
            if "CREATE (" in query:
                assert key not in records
                self.writes.append(query)
                records[key] = [
                    parameters["payload_json"],
                    parameters["artifact_ref_json"],
                ]
                return [], []
            return ([copy.deepcopy(records[key])] if key in records else []), []
        key = tuple(
            parameters[name]
            for name in ("tenant_id", "artifact_id", "artifact_version_id")
        )
        if "SET artifact:OsbCarrierNativeCapture" in query:
            assert key in self.artifacts.rows
            self.captured.add(key)
            return [], []
        if "OsbCarrierNativeCapture" in query:
            if key not in self.captured:
                return [], []
            if "carrier_conservation_json" in query:
                if "SET artifact" in query:
                    self.conservation.setdefault(key, parameters["conservation_json"])
                return [[self.conservation.get(key)]], []
            row = self.artifacts.rows[key]
            return ([[row[1]]] if row[0] == parameters["platform_study_id"] else []), []
        return self.artifacts.cypher_query(query, parameters)


def identity(fixture):
    scope, binding = fixture["scope"], fixture["crosswalk"]["osbBinding"]
    return {
        "contractVersion": "1.1.0",
        "bindingId": binding["platformBindingId"],
        "tenantId": scope["platformTenantId"],
        "platformStudyId": scope["platformStudyId"],
        "system": "osb",
        "namespace": "accuratrials-osb",
        "objectType": "study-draft-root",
        "nativeIdentity": binding["nativeStudyId"],
        "nativeVersion": binding["nativeVersion"],
        "verificationStatus": "verified",
        "verifiedBy": "synthetic-test",
        "verifiedAt": STAMP,
        "validFrom": STAMP,
        "validTo": None,
        "createdAt": STAMP,
        "createdBy": "synthetic-test",
        "evidence": {
            "receiptId": fixture["checkpoint"]["checkpointId"],
            "receiptPayloadHash": h({}, "NativeIdentityBindingReceiptV1@1.0.0"),
            "signedEnvelopeHash": h({}, "SignedArtifactEnvelopeV1@1.0.0"),
            "trustBundleVersion": 1,
            "trustBundleHash": h({}, "SigningTrustBundleV1@1.0.0"),
            "trustedSigningTime": STAMP,
        },
    }


def arrange_flow(monkeypatch, profile="native-single"):
    f, store = copy.deepcopy(prepared(profile)), Store()
    service = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_carrier_v2"
    )
    custody = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_artifact_custody"
    )
    inspection = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_carrier_inspection"
    )
    observer = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_carrier_state"
    )
    native = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_state_v2"
    )
    for module in (service, custody, inspection):
        monkeypatch.setattr(module, "db", store)
    root = copy.deepcopy(f["nativeState"]["root"])
    bridge_calls = []

    def current(tenant, study, _identity):
        assert (tenant, study) == (
            f["scope"]["platformTenantId"],
            f["scope"]["platformStudyId"],
        )
        return copy.deepcopy(root), []

    monkeypatch.setattr(observer, "_current_study", current)
    monkeypatch.setattr(
        observer,
        "active_osb_binding",
        lambda *_args: {
            "bindingId": root["bindingId"],
            "nativeIdentity": root["nativeStudyId"],
            "nativeVersion": root["nativeVersion"],
        },
    )
    monkeypatch.setattr(
        observer,
        "_assert_identity_binding",
        lambda *args, **kwargs: bridge_calls.append((args, kwargs)),
    )
    exported = {
        key: copy.deepcopy(f["canonicalDefinition"][key])
        for key in ("document", "mappingReport", "nativeRecords")
    }
    monkeypatch.setattr(
        native,
        "USDMService",
        lambda: SimpleNamespace(
            get_by_uid_with_report=lambda *_args, **_kwargs: copy.deepcopy(exported)
        ),
    )
    scope = {
        "tenant_id": f["scope"]["platformTenantId"],
        "platform_study_id": f["scope"]["platformStudyId"],
    }

    def transfer(payload, reference):
        return custody.store_carrier_artifact_bytes(
            **scope,
            artifact_ref=reference,
            bytes_value=canonical(payload).encode("utf-8"),
            expected_hash=reference["payloadHash"]["value"],
        )

    for item in f["artifacts"]:
        if item["artifact"]["kind"] not in {
            "osb-package-native-state",
            "osb-canonical-study-definition",
            "transformation-checkpoint",
        }:
            transfer(item["payload"], item["artifact"])
    carrier_ref = next(
        item["artifact"]
        for item in f["artifacts"]
        if item["artifact"]["kind"] == "accepted-study-exchange-carrier"
    )
    capture_input = {
        "contractVersion": service.CAPTURE_INPUT_SCHEMA,
        "sourceBasis": f["sourceBasis"],
        "carrierArtifact": carrier_ref,
        "osbBinding": f["crosswalk"]["osbBinding"],
        "nativeEvidenceBasis": f["nativeEvidenceBasis"],
        "osbStudyIdentity": identity(f),
    }
    return SimpleNamespace(
        f=f,
        store=store,
        service=service,
        custody=custody,
        scope=scope,
        capture_input=capture_input,
        root=root,
        transfer=transfer,
        bridge_calls=bridge_calls,
    )


@pytest.fixture
def flow(network_custody, monkeypatch, request):
    del network_custody
    setup = arrange_flow(monkeypatch, getattr(request, "param", "native-single"))
    f, store, service, custody, scope, capture_input, root, transfer, bridge_calls = (
        getattr(setup, name)
        for name in (
            "f",
            "store",
            "service",
            "custody",
            "scope",
            "capture_input",
            "root",
            "transfer",
            "bridge_calls",
        )
    )
    captured = service.capture_carrier_native_state(
        **scope, input_payload=capture_input
    )
    state = custody.load_carrier_artifact(
        **scope, artifact_ref=captured["nativeStateArtifact"]
    )
    definition = custody.load_carrier_artifact(
        **scope, artifact_ref=captured["canonicalDefinitionArtifact"]
    )
    built = csl_build(
        {
            "fixture": f,
            "nativeState": state,
            "nativeStateArtifact": captured["nativeStateArtifact"],
            "canonicalDefinition": definition,
        },
        "checkpoint",
    )
    transfer(built["checkpoint"], built["artifact"])
    request = {
        **scope,
        "transformation_checkpoint_artifact": built["artifact"],
        "osb_study_identity": identity(f),
    }
    return SimpleNamespace(
        f=f,
        service=service,
        custody=custody,
        store=store,
        scope=scope,
        root=root,
        capture_input=capture_input,
        captured=captured,
        request=request,
        built=built,
        transfer=transfer,
        bridge_calls=bridge_calls,
    )


def review_input(context, native_identity):
    return {
        "contractVersion": "OsbSpecialistReviewCommandInputV2@2.0.0",
        "osbStudyIdentity": native_identity,
        **{
            key: context[key]
            for key in (
                "transformationCheckpointArtifact",
                "nativeStateHash",
                "sourceBasisHash",
                "authoredExecution",
                "conservationHash",
                "reviewScope",
            )
        },
        "displayedStatement": "I reviewed these exact synthetic native and execution values.",
        "meaning": "prototype package review",
        "reason": "synthetic test",
    }


def record_review(flow):
    context = flow.service.carrier_review_context(**flow.request)
    result = flow.service.record_carrier_specialist_review(
        **flow.scope,
        input_payload=review_input(context, flow.request["osb_study_identity"]),
        actor="synthetic-specialist",
        expected_target_state=context["nativeStateHash"],
    )
    return context, result


def release_prerequisites(flow):
    """Use CC's real V2 approval producer with its in-memory synthetic port."""
    context, review = record_review(flow)
    manifest = {
        "contractVersion": "PlatformManifestV1@1.0.0",
        "tenantId": flow.scope["tenant_id"],
        "platformStudyId": flow.scope["platform_study_id"],
        "manifestId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "manifestVersionId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "productionEligible": False,
    }
    fields = {
        "artifactId": manifest["manifestId"],
        "artifactVersionId": manifest["manifestVersionId"],
        "kind": "platform-manifest-v1",
        "stableLocator": "artifact://cc/manifest/synthetic",
        "payloadHash": h(
            manifest,
            "PlatformManifestV1@1.0.0",
            legacy.PACKAGE["PLATFORM_MANIFEST_MEDIA_TYPE"],
        ),
        "byteSize": len(canonical(manifest).encode("utf-8")),
        "classification": "regulated-non-phi",
        "tenantId": flow.scope["tenant_id"],
        "region": "us-central1",
        "producerService": "cc.release-control",
        "producerEnvironment": "prototype",
        "producerVersion": "synthetic",
        "purpose": "workflow-orchestration",
        "payloadContract": "accuratrials.cc.PlatformManifestV1",
        "payloadContractVersion": "1.0.0",
        "createdAt": STAMP,
    }
    manifest_ref = legacy.PACKAGE["_artifact_ref"](fields)
    authority = flow.f["authority"]
    basis = {
        "contractVersion": "AcceptedCanonicalPreReleaseBasisV1@1.0.0",
        "scope": flow.f["scope"],
        "sourceBasisKind": flow.f["sourceBasis"]["kind"],
        "acceptedSnapshotId": authority["snapshot"]["snapshotId"],
        "acceptedSnapshotManifestHash": authority["snapshot"]["manifestHash"],
        "acceptedProjectionPackageHash": authority["snapshot"]["projectionPackageHash"],
        "authorityHash": flow.f["sourceBasis"]["authorityArtifact"]["payloadHash"],
        "readinessHash": h(
            {"syntheticReadiness": True}, "AcceptedCanonicalReleaseReadinessV1@1.0.0"
        ),
        "sourceEpochHash": h(
            {"syntheticEpoch": True}, "AcceptedCanonicalReleaseEpochV1@1.0.0"
        ),
        "conservationHash": context["conservationHash"],
        "counts": context["conservation"]["counts"],
        "ready": True,
        "lossless": True,
        "fetchedAt": STAMP,
    }
    approval_input = {
        "approvalVersion": "PreReleaseApprovalV2",
        "transformationCheckpointHash": flow.built["artifact"]["payloadHash"]["value"],
        "osbAuthorityVersion": flow.root["nativeVersion"],
        "osbAuthorityHash": context["nativeStateHash"]["value"],
        "specialistReviewEvidenceHash": review["payloadHash"]["value"],
        **{
            key: context[key]
            for key in (
                "transformationCheckpointArtifact",
                "nativeStateHash",
                "sourceBasisHash",
                "authoredExecution",
                "conservationHash",
                "reviewScope",
            )
        },
        "sourceEpochHash": basis["sourceEpochHash"],
    }
    cc = legacy.OSB.parent / "CommandCenter"
    script = """
    const {createSyntheticApproval}=require('./test/fixtures/pre-release-approval');
    const {composeAcceptedCanonicalStatement}=require('./server/accepted-canonical-pre-release');
    (async()=>{let raw='';for await(const chunk of process.stdin)raw+=chunk;const f=JSON.parse(raw);
      const readiness={...f.approvalInput,tenantId:f.tenantId,platformStudyId:f.platformStudyId,
        statementBasis:f.basis,statement:composeAcceptedCanonicalStatement(f.basis)};
      const result=await createSyntheticApproval({...f,readiness,readAuthoritativeReadiness:async()=>structuredClone(readiness)});
      process.stdout.write(JSON.stringify(result));})().catch(error=>{console.error(error);process.exitCode=1;});
    """
    output = subprocess.run(
        [os.environ.get("NODE", "node"), "-e", script],
        cwd=cc,
        input=json.dumps(
            {
                "tenantId": flow.scope["tenant_id"],
                "platformStudyId": flow.scope["platform_study_id"],
                "manifestId": manifest["manifestId"],
                "manifestHash": manifest_ref["payloadHash"]["value"],
                "now": 1791849600000,
                "basis": basis,
                "approvalInput": approval_input,
            }
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert output.returncode == 0, output.stderr
    produced = json.loads(output.stdout)
    flow.transfer(manifest, manifest_ref)
    flow.transfer(produced["approval"], produced["artifactRef"])
    return context, {
        "contractVersion": "OsbNativePackageReleaseInputV2@2.0.0",
        "transformationCheckpointArtifact": flow.built["artifact"],
        "platformManifestArtifact": manifest_ref,
        "preReleaseApprovalArtifact": produced["artifactRef"],
        "specialistReviewArtifact": review["artifactRef"],
        "osbStudyIdentity": flow.request["osb_study_identity"],
    }


def test_real_cc_approval_produces_schema_valid_immutable_package(flow):
    context, payload = release_prerequisites(flow)
    arguments = {
        **flow.scope,
        "input_payload": payload,
        "actor": "synthetic-package-owner",
        "expected_target_state": context["nativeStateHash"],
    }
    first = flow.service.generate_carrier_native_package(**arguments)
    second = flow.service.generate_carrier_native_package(**arguments)
    assert not first["replay"] and second["replay"]
    assert first["bytes"] == second["bytes"]
    assert first["artifactRef"] == second["artifactRef"]
    assert (
        first["payload"]["captureDesign"]["authoredExecution"]
        == context["authoredExecution"]
    )
    assert first["payload"]["productionEligible"] is False
    assert first["payloadHash"]["canonicalizationVersion"] == "raw-bytes/1.0"
    assert len(flow.store.packages) == 1
    headers = flow.custody.retained_artifact_headers(
        payload_bytes=first["bytes"],
        artifact_ref_json=canonical(first["artifactRef"]),
        **flow.scope,
    )
    assert (
        flow.custody.decode_artifact_reference_header(headers["x-artifact-reference"])
        == first["artifactRef"]
    )


@pytest.mark.parametrize("change", ["guard", "identity", "approval", "review"])
def test_package_refuses_stale_or_missing_authority_before_write(flow, change):
    context, payload = release_prerequisites(flow)
    guard = context["nativeStateHash"]
    if change == "guard":
        guard = None
    elif change == "identity":
        payload["osbStudyIdentity"]["nativeVersion"] = "different-version"
    elif change == "review":
        flow.store.reviews.clear()
    else:
        payload["preReleaseApprovalArtifact"][
            "producerVersion"
        ] = "coherently altered metadata"
        fields = {
            key: value
            for key, value in payload["preReleaseApprovalArtifact"].items()
            if key not in {"contractVersion", "descriptorHash"}
        }
        payload["preReleaseApprovalArtifact"] = legacy.PACKAGE["_artifact_ref"](fields)
    with pytest.raises(flow.service.OsbCandidateSetError):
        flow.service.generate_carrier_native_package(
            **flow.scope,
            input_payload=payload,
            actor="synthetic-package-owner",
            expected_target_state=guard,
        )
    assert not flow.store.packages


@pytest.mark.parametrize(
    "flow", ["native-single", "native-single-compiled"], indirect=True
)
def test_actual_csl_capture_context_and_pages_preserve_all_terminal_values(flow):
    context = flow.service.carrier_review_context(**flow.request)
    assert context["conservation"] == flow.f["conservation"]
    assert context["nativeStateHash"] == flow.captured["nativeStateHash"]
    assert context["contentCounts"]["nativeObjects"] == 0
    count = flow.f["conservation"]["counts"]["atoms"]
    assert context["contentCounts"]["terminalAtoms"] == count
    assert "content" not in context and "nativeState" not in context
    assert flow.root["bindingId"] != flow.request["osb_study_identity"]["bindingId"]
    values, offset = [], 0
    while True:
        page = flow.service.carrier_review_page(
            **flow.request, section="terminalAtoms", offset=offset, limit=7
        )
        assert len(canonical({"ok": True, "data": page}).encode("utf-8")) <= 262144
        values.extend(page["items"])
        if page["nextOffset"] is None:
            break
        offset = page["nextOffset"]
    assert len(values) == count
    for item in values:
        exact = flow.service.carrier_review_entry(
            **flow.request, section="terminalAtoms", position=item["position"]
        )
        assert exact["bytes"] == canonical(item["value"]).encode("utf-8")
    assert len(flow.bridge_calls) > 4
    assert not flow.store.reviews and not flow.store.packages


def test_complete_multiversion_source_refuses_unproven_native_projection(
    network_custody, monkeypatch
):
    del network_custody
    setup = arrange_flow(monkeypatch, "")
    with pytest.raises(setup.service.OsbCandidateSetError) as error:
        setup.service.capture_carrier_native_state(
            **setup.scope, input_payload=setup.capture_input
        )
    assert error.value.code == "OSB_CARRIER_CANONICAL_DEFINITION_MISMATCH"
    assert not setup.store.captured and not setup.store.inspection.headers


def test_review_replay_retains_original_bytes_and_full_descriptor(flow):
    context, first = record_review(flow)
    repeated = flow.service.record_carrier_specialist_review(
        **flow.scope,
        input_payload=review_input(context, flow.request["osb_study_identity"]),
        actor="synthetic-specialist",
        expected_target_state=context["nativeStateHash"],
    )
    assert repeated["replay"] and not first["replay"]
    assert (
        repeated["artifactRef"] == first["artifactRef"]
        and repeated["payload"] == first["payload"]
    )
    assert len(flow.store.reviews) == 1


@pytest.mark.parametrize(
    "change",
    [
        "nativeStateHash",
        "sourceBasisHash",
        "conservationHash",
        "authoredExecution",
        "blank",
        "guard",
    ],
)
def test_review_rejects_changed_display_commitments_before_any_write(flow, change):
    context = flow.service.carrier_review_context(**flow.request)
    payload = review_input(context, flow.request["osb_study_identity"])
    guard = context["nativeStateHash"]
    if change == "guard":
        guard = None
    elif change == "blank":
        payload["reason"] = "   "
    elif change == "authoredExecution":
        payload[change]["carrierHash"]["value"] = "sha256:" + "0" * 64
    else:
        payload[change] = {**payload[change], "value": "sha256:" + "0" * 64}
    with pytest.raises(flow.service.OsbCandidateSetError):
        flow.service.record_carrier_specialist_review(
            **flow.scope,
            input_payload=payload,
            actor="synthetic-specialist",
            expected_target_state=guard,
        )
    assert not flow.store.reviews


def test_fresh_native_change_prevents_context_and_replay(flow):
    context, _ = record_review(flow)
    flow.root["title"] = "Changed after review"
    with pytest.raises(flow.service.OsbCandidateSetError):
        flow.service.carrier_review_context(**flow.request)
    with pytest.raises(flow.service.OsbCandidateSetError):
        flow.service.record_carrier_specialist_review(
            **flow.scope,
            input_payload=review_input(context, flow.request["osb_study_identity"]),
            actor="synthetic-specialist",
            expected_target_state=context["nativeStateHash"],
        )
    assert len(flow.store.reviews) == 1


def test_imported_state_never_grants_owner_observation(flow):
    flow.store.captured.clear()
    with pytest.raises(flow.service.OsbCandidateSetError) as error:
        flow.service.carrier_review_context(**flow.request)
    assert error.value.code == "OSB_CARRIER_OWNER_CAPTURE_REQUIRED"


def test_mutated_checkpoint_conservation_rejected_against_original_capture(flow):
    checkpoint = copy.deepcopy(flow.built["checkpoint"])
    checkpoint["conservation"]["terminalSetHash"]["value"] = "sha256:" + "1" * 64
    fields = {
        key: value
        for key, value in flow.built["artifact"].items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    fields["artifactVersionId"] = checkpoint["checkpointVersionId"] = (
        "11111111-2222-4333-8444-555555555555"
    )
    fields["payloadHash"] = h(checkpoint, "TransformationCheckpointV2@2.0.0")
    fields["byteSize"] = len(canonical(checkpoint).encode("utf-8"))
    reference = legacy.PACKAGE["_artifact_ref"](fields)
    flow.transfer(checkpoint, reference)
    with pytest.raises(flow.service.OsbCandidateSetError) as error:
        flow.service.carrier_review_context(
            **{**flow.request, "transformation_checkpoint_artifact": reference}
        )
    assert error.value.code == "OSB_CARRIER_CONSERVATION_CONFLICT"
