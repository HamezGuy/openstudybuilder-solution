import hashlib
import json

import pytest

from ..utils import osb_proposal_db
from ..utils.osb_proposal_db import (
    OsbProposalDb,
    OsbProposalIntegrityError,
    _native_delivery_status,
    _stable_hash,
)

TENANT = "00000000-0000-4000-8000-0000000000aa"
STUDY = "00000000-0000-4000-8000-0000000000bb"
FACT = "00000000-0000-4000-8000-0000000000cc"


def semantic_source(study_id=STUDY):
    """A synthetic semantic claim projection and the proposal that cites it."""
    carried = {"factId": FACT, "revision": 1, "provenance": {"citations": []}}
    content = {
        "tenantId": TENANT,
        "studyId": STUDY,
        "claimCount": 1,
        "claims": [{"claimId": FACT, "extensions": {"edcprotocoltoecrf/fact": carried}}],
    }
    package = {**content, "packageHash": f"sha256:{_stable_hash(content)}"}
    package_bytes = json.dumps(package).encode()
    fact = {**carried, "tenantId": TENANT, "studyId": STUDY}
    proposal = {
        "tenantId": TENANT,
        "studyId": study_id,
        "sourceFactRefs": [
            {"factId": FACT, "revision": 1, "factContentHash": _stable_hash(fact)}
        ],
        "sourceAuthority": {
            "authorityType": "semantic_claim_projection",
            "system": "ClinicalSemanticLayer",
            "contractVersion": "1.0",
            "tenantId": TENANT,
            "studyId": study_id,
            "mediaType": "application/vnd.accuratrials.semantic-claim-projection+json;version=1.0",
            "contentHash": f"sha256:{hashlib.sha256(package_bytes).hexdigest()}",
            "packageHash": package["packageHash"],
        },
    }
    return proposal, package_bytes


class _Response:
    status_code = 200
    ok = True

    def __init__(self, content):
        self.content = content


@pytest.fixture
def semantic_layer(monkeypatch):
    monkeypatch.setenv("SEMANTIC_API_URL", "https://semantic.invalid/")
    monkeypatch.setenv("SEMANTIC_ADAPTER_TOKEN", "synthetic-adapter-token")
    monkeypatch.setenv("SEMANTIC_ADAPTER_ID", "synthetic-osb-adapter")
    calls = []

    def serve(package_bytes):
        def get(url, headers, timeout):
            calls.append({"url": url, "headers": headers})
            return _Response(package_bytes)

        monkeypatch.setattr(osb_proposal_db.requests, "get", get)

    return calls, serve


def test_semantic_source_read_names_the_proposal_study(semantic_layer):
    """The object read carries x-semantic-study-id; without it CSL answers 400 STUDY_ID_REQUIRED."""
    calls, serve = semantic_layer
    proposal, package_bytes = semantic_source()
    serve(package_bytes)

    OsbProposalDb._verify_semantic_fact_refs(proposal, proposal["sourceAuthority"])

    assert len(calls) == 1
    digest = hashlib.sha256(package_bytes).hexdigest()
    assert calls[0]["url"] == f"https://semantic.invalid/v1/objects/sha256/{digest}"
    assert calls[0]["headers"]["x-semantic-study-id"] == STUDY
    assert calls[0]["headers"]["x-semantic-tenant-id"] == TENANT
    assert calls[0]["headers"]["x-semantic-adapter-id"] == "synthetic-osb-adapter"


@pytest.mark.parametrize("study_id", [None, "", "  "])
def test_semantic_source_without_a_study_is_refused_before_any_read(
    semantic_layer, study_id
):
    calls, serve = semantic_layer
    proposal, package_bytes = semantic_source(study_id)
    serve(package_bytes)

    with pytest.raises(
        OsbProposalIntegrityError, match="OSB_PROPOSAL_SOURCE_AUTHORITY_INVALID"
    ):
        OsbProposalDb._verify_semantic_fact_refs(proposal, proposal["sourceAuthority"])
    assert calls == []


def evidence(*, release_ready=False, blockers=None, deferred=None, reconciled=True):
    blockers = blockers or []
    deferred = deferred or []
    native = {
        "schemaVersion": "osb-native-execution/1.0",
        "proposalHash": "proposal-hash",
        "sourceStudyId": "source-study",
        "targetStudyUid": "Study_1",
        "targetStudyVersion": "DRAFT",
        "operationCount": 1,
        "releaseReady": release_ready,
        "releaseBlockers": blockers,
        "deferredObjects": deferred,
        "receipts": [{"idempotencyKey": "operation-1"}],
    }
    reconciliation = {
        "schemaVersion": "osb-native-reconciliation/1.0",
        "proposalHash": "proposal-hash",
        "targetStudyUid": "Study_1",
        "targetStudyVersion": "DRAFT",
        "operationCount": 1,
        "allReconciled": reconciled,
        "releaseReady": release_ready,
        "releaseBlockers": blockers,
        "rows": [{"idempotencyKey": "operation-1"}],
    }
    return (
        {**native, "contentHash": _stable_hash(native)},
        {**reconciliation, "contentHash": _stable_hash(reconciliation)},
    )


def test_native_delivery_status_distinguishes_release_ready_from_partial():
    native, reconciliation = evidence(release_ready=True)
    assert _native_delivery_status(native, reconciliation) == "succeeded"

    native, reconciliation = evidence(
        blockers=["OSB_RELEASE_EXTENSION_OBJECTS_PRESENT"]
    )
    assert _native_delivery_status(native, reconciliation) == "native_partial"

    native, reconciliation = evidence(
        release_ready=True,
        deferred=[{"proposalObjectId": "deferred-1"}],
    )
    assert _native_delivery_status(native, reconciliation) == "native_partial"


def test_native_delivery_status_rejects_unreconciled_or_mismatched_evidence():
    native, reconciliation = evidence(reconciled=False)
    with pytest.raises(ValueError, match="allReconciled true"):
        _native_delivery_status(native, reconciliation)

    native, reconciliation = evidence(release_ready=True)
    changed = {**reconciliation, "proposalHash": "wrong-proposal"}
    changed["contentHash"] = _stable_hash(
        {key: value for key, value in changed.items() if key != "contentHash"}
    )
    with pytest.raises(ValueError, match="proposalHash"):
        _native_delivery_status(native, changed)

    native, reconciliation = evidence()
    native_without_identity = {**native, "proposalHash": ""}
    native_without_identity["contentHash"] = _stable_hash(
        {
            key: value
            for key, value in native_without_identity.items()
            if key != "contentHash"
        }
    )
    reconciliation_without_identity = {**reconciliation, "proposalHash": ""}
    reconciliation_without_identity["contentHash"] = _stable_hash(
        {
            key: value
            for key, value in reconciliation_without_identity.items()
            if key != "contentHash"
        }
    )
    with pytest.raises(ValueError, match="proposalHash must be a non-empty string"):
        _native_delivery_status(
            native_without_identity,
            reconciliation_without_identity,
        )


def test_native_delivery_status_rejects_incomplete_operation_evidence():
    native, reconciliation = evidence()
    incomplete = {**native, "receipts": []}
    incomplete["contentHash"] = _stable_hash(
        {key: value for key, value in incomplete.items() if key != "contentHash"}
    )
    with pytest.raises(ValueError, match="receipt count"):
        _native_delivery_status(incomplete, reconciliation)


@pytest.mark.parametrize("status", ["native_partial", "succeeded"])
def test_generic_finish_cannot_bypass_native_evidence_gate(status):
    db = object.__new__(OsbProposalDb)

    with pytest.raises(ValueError, match="invalid finish status"):
        db.finish("outbox-1", "worker-1", 1, status)
