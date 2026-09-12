"""Public draft-stage behavior with synthetic IO and real contract verifiers."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json, canonical_json_hash_ref, sha256_bytes,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError, _artifact_ref
from clinical_mdr_api.services.integrations import source_draft_stage as stage
from clinical_mdr_api.tests.unit.services.test_candidate_set_v1 import (
    STUDY_ID, TENANT_ID, _fixture as candidate_fixture, _refresh,
)
from clinical_mdr_api.tests.unit.services.test_native_capture_mapping import NativePort, fixture as capture_fixture


def signature(artifact, *, purpose="source-draft-stage", service="csl.semantic-api"):
    envelope = {
        "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
        "artifactDescriptor": {"contractVersion": "ArtifactDescriptorV1@1.0.0", **{
            key: value for key, value in artifact.items() if key not in {"contractVersion", "descriptorHash"}}},
        "payloadHash": artifact["payloadHash"],
        "signingStatement": {
            "signingPurpose": purpose, "producerService": service,
            "payloadContract": artifact["payloadContract"],
            "payloadContractVersion": artifact["payloadContractVersion"],
        },
    }
    proof = {"verified": True, "payloadHash": artifact["payloadHash"],
             "envelopeHash": canonical_json_hash_ref(envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"),
             "signerKeyId": "synthetic-key", "trustedTime": artifact["createdAt"]}
    return envelope, proof


def stage_ref(payload):
    return _artifact_ref({
        "artifactId": payload["stageId"], "artifactVersionId": payload["stageVersionId"],
        "kind": "source-draft-stage", "stableLocator": f'artifact://csl/source-draft-stage/{payload["stageVersionId"]}',
        "payloadHash": stage._hash(payload), "byteSize": len(canonical_json(payload).encode()),
        "classification": "regulated-non-phi", "tenantId": TENANT_ID,
        "region": "us-central-1", "producerService": "csl.semantic-api",
        "producerEnvironment": "prototype", "producerVersion": "test",
        "payloadContract": "accuratrials.csl.SourceDraftStageV1", "payloadContractVersion": "1.0.0",
        "purpose": "source-draft-review", "createdAt": payload["createdAt"],
    })


def fixture(*, missing_required=False, version="1.0.0"):
    candidate = list(candidate_fixture())
    request = candidate[0]
    request["contractVersion"] = f"OsbCandidateRequestV1@{version}"
    candidate[1]["payloadContractVersion"] = version
    candidate[2]["signingStatement"]["payloadContractVersion"] = version
    captures = capture_fixture()[:2]
    if missing_required:
        captures[1]["intent"]["source"]["values"] = [
            value for value in captures[1]["intent"]["source"]["values"] if value["name"] != "required"]
    for index, item in enumerate(captures):
        intent = request["typedSourceIntents"][index]
        intent["resourceFamily"], intent["source"] = item["intent"]["resourceFamily"], item["intent"]["source"]
        target = request["inputConservation"]["rows"][index]["target"]
        target["contract"] = f"accuratrials.osb.OsbCandidateRequestV1@{version}"
        target["valueHash"] = canonical_json_hash_ref(intent, schema_version="OsbTypedSourceIntentV1@1.0.0")
    request["requestedObjectFamilies"] = ["odm_forms", "odm_item_groups"]
    request["inputConservation"]["rowSetHash"] = canonical_json_hash_ref(
        request["inputConservation"]["rows"], schema_version="ConservationCensusRowsV1@1.0.0")
    _refresh(candidate)
    now = datetime.now(UTC)
    payload = {
        "contractVersion": stage.STAGE_CONTRACT,
        "stageId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "stageVersionId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "tenantId": TENANT_ID, "platformStudyId": STUDY_ID,
        "candidateRequestArtifact": candidate[1],
        "semanticSnapshotHash": request["semanticSnapshot"]["payloadHash"],
        "sourceFactPackageHash": request["sourceFactPackage"]["payloadHash"],
        "osbStudyIdentity": request["osbStudyIdentity"],
        "selectedSourceKeys": [stage._key(value) for value in request["typedSourceIntents"]],
        "createdAt": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(minutes=10)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "createdBy": {"actorType": "service", "issuerQualifiedSubject": "csl.semantic-api"},
        "clinicalApproval": False, "purpose": "source-draft-review",
    }
    payload = deepcopy(payload)
    return payload, stage_ref(payload), candidate


class Store:
    def __init__(self, payload, artifact, candidate):
        self.payload, self.artifact, self.candidate = deepcopy((payload, artifact, candidate))
        self.envelope, self.proof = signature(artifact)
        self.saved = None
        self.locks = 0
        self.transfers = []

    def load(self, tenant, study, value_hash, kind):
        assert (tenant, study) == (TENANT_ID, STUDY_ID)
        if kind == "source-draft-stage":
            assert value_hash == self.artifact["payloadHash"]["value"]
            return deepcopy(self.payload), deepcopy(self.envelope)
        assert kind == "osb-candidate-request"
        assert value_hash == self.candidate[1]["payloadHash"]["value"]
        return deepcopy(self.candidate[0]), deepcopy(self.candidate[2])

    def verify(self, payload, envelope):
        if payload["contractVersion"] == stage.STAGE_CONTRACT:
            assert envelope == self.envelope
            return self.proof
        assert payload == self.candidate[0] and envelope == self.candidate[2]
        return self.candidate[3]

    def lock_scope(self, payload):
        assert payload == self.payload
        self.locks += 1
        return {"nativeStudyId": "Study_990041", "nativeVersion": "0.1", "nativeStatus": "DRAFT"}

    def receipt(self, *args, **kwargs):
        return deepcopy(self.saved)

    def save_receipt(self, payload, artifact):
        assert self.saved is None
        self.saved = deepcopy({"payload": payload, "artifactRef": artifact})

    def store(self, *args):
        self.transfers.append(args)


def execute(store, port):
    return stage.stage_native_source_draft(
        tenant_id=TENANT_ID, platform_study_id=STUDY_ID, stage_artifact=store.artifact,
        verify_signature=store.verify, store=store, capture_port=port)


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])
def test_signed_semantic_request_creates_real_dto_drafts_and_an_immutable_review_receipt(version):
    store, port = Store(*fixture(version=version)), NativePort()
    result = execute(store, port)
    receipt = result["payload"]
    assert receipt["clinicalApproval"] is False and receipt["releaseEligible"] is False
    assert receipt["summary"] == {"selected": 2, "created": 2, "reused": 0, "blocked": 0, "withBlockers": 2}
    assert all(member["readBack"]["native"]["status"] == "Draft" for member in receipt["members"])
    assert [row["sourceKey"] for row in receipt["members"]] == store.payload["selectedSourceKeys"]
    assert all(member["fieldReceipts"] for member in receipt["members"])
    creates = deepcopy(port.creates)
    replay = execute(store, port)
    assert replay["replay"] is True and replay["payload"] == receipt and port.creates == creates
    assert result["artifactRef"]["payloadHash"] == stage._hash(receipt, stage.RECEIPT_CONTRACT, stage.RECEIPT_MEDIA_TYPE)


def test_missing_group_requirement_keeps_native_draft_but_does_not_invent_an_association():
    store, port = Store(*fixture(missing_required=True)), NativePort()
    receipt = execute(store, port)["payload"]
    group = receipt["members"][1]
    assert group["outcome"] == "created"
    assert group["readBack"]["native"]["parents"] == []
    assert any(value["code"] == "OSB_CAPTURE_RELATIONSHIP_REQUIRED_UNKNOWN" for value in group["blockers"])
    assert not port.associations
    assert "required" not in group["readBack"]["nativeValues"]


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(clinicalApproval=True),
    lambda value: value["createdBy"].update(actorType="human"),
    lambda value: value.update(localBundle={"forms": []}),
    lambda value: value["selectedSourceKeys"].append(value["selectedSourceKeys"][0]),
    lambda value: value["selectedSourceKeys"].append("unselected@1:primary"),
    lambda value: value.update(platformStudyId="dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
    lambda value: value["semanticSnapshotHash"].update(value="sha256:" + "fa" * 32),
    lambda value: value["sourceFactPackageHash"].update(value="sha256:" + "fa" * 32),
    lambda value: value["osbStudyIdentity"].update(nativeIdentity="Study_foreign"),
    lambda value: value.update(expiresAt="2020-01-01T00:00:00.000Z"),
])
def test_scope_source_selection_and_machine_authority_fail_before_native_creation(mutation):
    payload, _, candidate = fixture()
    mutation(payload)
    store, port = Store(payload, stage_ref(payload), candidate), NativePort()
    with pytest.raises(OsbCandidateSetError):
        execute(store, port)
    assert not port.creates and store.saved is None and store.locks == 0


@pytest.mark.parametrize("purpose,service", [
    ("study-mapping-decision", "csl.semantic-api"), ("source-draft-stage", "csl.attestation"),
    ("command-receipt", "csl.semantic-api"),
])
def test_a_verified_signature_for_another_purpose_is_not_draft_authority(purpose, service):
    store, port = Store(*fixture()), NativePort()
    store.envelope, store.proof = signature(store.artifact, purpose=purpose, service=service)
    with pytest.raises(OsbCandidateSetError, match="custody differs"):
        execute(store, port)
    assert not port.creates


def test_transfer_verifies_exact_signed_bytes_and_cannot_accept_locally_generated_payload_fields():
    store = Store(*fixture())
    content = canonical_json(store.payload).encode()
    receipt = stage.store_source_draft_stage_bytes(
        tenant_id=TENANT_ID, platform_study_id=STUDY_ID, bytes_value=content,
        expected_hash=sha256_bytes(content), signed_envelope=store.envelope,
        verify_signature=store.verify, store=store)
    assert receipt["signedEnvelopeBound"] is True and len(store.transfers) == 1
    with pytest.raises(OsbCandidateSetError):
        stage.store_source_draft_stage_bytes(
            tenant_id=TENANT_ID, platform_study_id=STUDY_ID, bytes_value=content + b"\n",
            expected_hash=sha256_bytes(content + b"\n"), signed_envelope=store.envelope,
            verify_signature=store.verify, store=store)
    assert len(store.transfers) == 1


@pytest.mark.parametrize("boundary", ["lock", "capture", "receipt"])
def test_expiry_during_work_cannot_be_published_as_success(monkeypatch, boundary):
    store, port = Store(*fixture()), NativePort()
    time = {"now": datetime.now(UTC)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return time["now"]

    monkeypatch.setattr(stage, "datetime", Clock)

    def expire():
        time["now"] = datetime.fromisoformat(store.payload["expiresAt"].replace("Z", "+00:00")) + timedelta(milliseconds=1)

    if boundary == "lock":
        previous = store.lock_scope

        def delayed_lock(payload):
            result = previous(payload)
            expire()
            return result

        store.lock_scope = delayed_lock
    elif boundary == "capture":
        previous = port.create

        def delayed_create(*args):
            result = previous(*args)
            expire()
            return result

        port.create = delayed_create
    else:
        previous = store.save_receipt

        def delayed_receipt(*args):
            previous(*args)
            expire()

        store.save_receipt = delayed_receipt
    with pytest.raises(OsbCandidateSetError) as caught:
        execute(store, port)
    assert caught.value.code == "OSB_SOURCE_DRAFT_STAGE_EXPIRED"
    if boundary == "lock":
        assert not port.creates
    if boundary != "receipt":
        assert store.saved is None
