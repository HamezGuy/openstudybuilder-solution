import pytest

from ..utils.osb_proposal_db import OsbProposalDb, _native_delivery_status, _stable_hash


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
