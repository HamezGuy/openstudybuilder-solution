"""Current native heads through identity, candidate and package producers."""

from copy import deepcopy

import pytest

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
)
from clinical_mdr_api.generated.platform_contracts.native_identity_command_processor_v1 import (
    NativeIdentityCommandError,
)
from clinical_mdr_api.services.integrations import (
    candidate_set,
    native_identity,
    native_package_state_v2,
)

TENANT, PLATFORM_STUDY, NATIVE_STUDY = "tenant", "platform-study", "Study_synthetic"
START, LOCKED, REOPENED = (
    "2026-09-11T10:00:00Z",
    "2026-09-11T10:01:00Z",
    "2026-09-11T10:02:00Z",
)


class NativeHeads:
    def __init__(self, state="draft"):
        # Study lifecycle keeps the ended draft and the prior lock relationships.
        self.heads = [["LATEST_DRAFT", None, "DRAFT", START, None, True]]
        if state in {"locked", "reopened"}:
            self.heads[0][4] = LOCKED
            self.heads.append(["LATEST_LOCKED", "1", "LOCKED", LOCKED, None, True])
        if state == "reopened":
            self.heads[0][3:] = [REOPENED, None, True]
            self.heads[1][-1] = False
        self.exists = True
        self.receipts = []
        self.writes = []

    def cypher_query(self, query, params):
        if "PlatformNativeIdentityAudit" in query:
            return deepcopy(self.receipts), None
        if "RETURN next.binding_id" in query:
            self.writes.append(deepcopy(params))
            return [[params["binding_id"]]], None
        if "RETURN concept.managed_key" in query:
            return [], None
        assert "StudyRoot" in query
        if not self.exists:
            return [], None
        if "RETURN binding.binding_id,study.uid,type(head)" in query:
            return [
                ["native-binding", NATIVE_STUDY, *head[:3], "Title", "001", None, "P1", *head[3:]]
                for head in self.heads
            ], None
        if "ORDER BY preference" in query:
            # The old query asks Neo4j for the draft first, even when it is ended.
            head = self.heads[0]
            if "RETURN study.uid" in query:
                return [[NATIVE_STUDY, head[1], head[2]]], None
            return [[head[1], head[2]]], None
        assert "head.end_date" in query and "value=latest" in query
        return [[NATIVE_STUDY, *head] for head in self.heads], None

    def candidate(self, version, status):
        binding = {
            "bindingId": "native-binding",
            "nativeIdentity": NATIVE_STUDY,
            "nativeVersion": version,
        }
        receipt = {
            "contractVersion": "1.0.0",
            "receiptId": "synthetic-receipt",
            "tenantId": TENANT,
            "platformStudyId": PLATFORM_STUDY,
            "targetSystem": "osb",
            "namespace": "accuratrials-osb",
            "objectType": "study-draft-root",
            "nativeIdentity": NATIVE_STUDY,
            "nativeVersion": version,
            "targetStateHash": canonical_json_hash_ref(
                {
                    "nativeIdentity": NATIVE_STUDY,
                    "nativeVersion": version,
                    "status": status,
                    "domainBindingId": binding["bindingId"],
                },
                schema_version="OSBNativeStudyRootStateV1@1.0.0",
            ),
        }
        envelope = {"fixture": "retained identity envelope"}
        self.receipts = [[canonical_json(receipt), canonical_json(envelope)]]
        identity = {
            **binding,
            "bindingId": "platform-binding",
            "contractVersion": "1.1.0",
            "tenantId": TENANT,
            "platformStudyId": PLATFORM_STUDY,
            "system": "osb",
            "namespace": "accuratrials-osb",
            "objectType": "study-draft-root",
            "verificationStatus": "verified",
            "validTo": None,
            "evidence": {
                "receiptId": receipt["receiptId"],
                "receiptPayloadHash": canonical_json_hash_ref(
                    receipt, schema_version="NativeIdentityBindingReceiptV1@1.0.0"
                ),
                "signedEnvelopeHash": canonical_json_hash_ref(
                    envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"
                ),
            },
        }
        return {
            "tenantId": TENANT,
            "platformStudyId": PLATFORM_STUDY,
            "osbStudyIdentity": identity,
            "capabilityCheckpoint": {"nativeVersion": version, "osbOpenApiHash": "synthetic-openapi"},
            "expiresAt": "2099-01-01T00:00:00Z",
        }, binding


def attach(monkeypatch, store):
    for module in (native_identity, candidate_set, native_package_state_v2):
        monkeypatch.setattr(module, "db", store)


def revalidate(store, version, status):
    payload, binding = store.candidate(version, status)
    candidate_set.assert_candidate_set_current(
        payload, binding=binding, osb_openapi_hash="synthetic-openapi"
    )


@pytest.mark.parametrize(
    "state,version,status,relationship,timestamp",
    [
        ("draft", "draft", "draft", "LATEST_DRAFT", START),
        ("locked", "1", "locked", "LATEST_LOCKED", LOCKED),
        ("reopened", "draft", "draft", "LATEST_DRAFT", REOPENED),
    ],
)
def test_current_head_agrees_across_actual_identity_candidate_and_package(
    monkeypatch, state, version, status, relationship, timestamp
):
    store = NativeHeads(state)
    attach(monkeypatch, store)
    root, concepts = native_package_state_v2._current_study(
        TENANT, PLATFORM_STUDY, {"nativeIdentity": NATIVE_STUDY, "nativeVersion": version}
    )
    assert (root["relationship"], root["nativeVersion"], root["versionTimestamp"]) == (
        relationship, version, timestamp
    )
    assert concepts == []
    assert native_identity.Neo4jOsbNativeIdentityTransactionV1._native_checkpoint(
        NATIVE_STUDY
    ) == (version, status)
    revalidate(store, version, status)
    assert store.writes == []


def test_closed_draft_cannot_keep_a_draft_candidate_current(monkeypatch):
    store = NativeHeads("locked")
    attach(monkeypatch, store)
    with pytest.raises(candidate_set.OsbCandidateSetError) as error:
        revalidate(store, "draft", "draft")
    assert error.value.code == "OSB_CANDIDATE_SET_STALE"
    assert store.writes == []


def test_reopening_invalidates_locked_candidate_and_restores_draft_identity(monkeypatch):
    store = NativeHeads("reopened")
    attach(monkeypatch, store)
    with pytest.raises(candidate_set.OsbCandidateSetError) as error:
        revalidate(store, "1", "locked")
    assert error.value.code == "OSB_CANDIDATE_SET_STALE"
    revalidate(store, "draft", "draft")
    assert store.writes == []


@pytest.mark.parametrize("mutation", ["duplicate", "ended-lock", "wrong-status", "not-latest", "no-draft"])
def test_invalid_current_head_is_rejected_at_all_three_boundaries(monkeypatch, mutation):
    store = NativeHeads("locked")
    if mutation == "duplicate":
        store.heads.append(deepcopy(store.heads[0]))
    elif mutation == "ended-lock":
        store.heads[1][4] = REOPENED
    elif mutation == "wrong-status":
        store.heads[1][2] = "DRAFT"
    elif mutation == "not-latest":
        store.heads[1][-1] = False
    else:
        store.heads.pop(0)
    attach(monkeypatch, store)
    with pytest.raises(NativeIdentityCommandError) as error:
        native_identity.Neo4jOsbNativeIdentityTransactionV1._native_checkpoint(NATIVE_STUDY)
    assert error.value.code == "IDENTITY_NATIVE_VERSION_UNAVAILABLE"
    with pytest.raises(candidate_set.OsbCandidateSetError):
        revalidate(store, "1", "locked")
    with pytest.raises(candidate_set.OsbCandidateSetError):
        native_package_state_v2._current_study(
            TENANT, PLATFORM_STUDY, {"nativeIdentity": NATIVE_STUDY, "nativeVersion": "1"}
        )
    assert store.writes == []


def test_actual_rollover_uses_current_lock_and_leaves_the_old_binding_as_history(monkeypatch):
    store = NativeHeads("locked")
    attach(monkeypatch, store)
    transaction = native_identity.Neo4jOsbNativeIdentityTransactionV1(TENANT, PLATFORM_STUDY)
    previous = {"bindingId": "previous-binding", "nativeIdentity": NATIVE_STUDY, "nativeVersion": "draft"}
    result = transaction._rollover(
        {"expectedAbsence": False},
        {"nativeIdentity": NATIVE_STUDY, "nativeVersion": "1", "previousBindingId": previous["bindingId"]},
        previous,
    )
    assert result["nativeVersion"] == "1"
    assert result["targetState"]["status"] == "locked"
    assert result["previousBindingId"] == "previous-binding"
    assert len(store.writes) == 1
    assert store.writes[0]["previous"] == "previous-binding"
    assert previous["nativeVersion"] == "draft"


def test_missing_root_remains_a_distinct_identity_not_found_error(monkeypatch):
    store = NativeHeads()
    store.exists = False
    attach(monkeypatch, store)
    with pytest.raises(NativeIdentityCommandError) as error:
        native_identity.Neo4jOsbNativeIdentityTransactionV1._native_checkpoint(NATIVE_STUDY)
    assert error.value.code == "IDENTITY_NATIVE_ROOT_NOT_FOUND"
