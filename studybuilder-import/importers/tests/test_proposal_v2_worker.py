import json
import logging

import pytest
import requests

from ..run_import_osb_proposal_v2 import ImportOsbProposalV2
from ..utils.osb_proposal_db import _stable_hash


class FakeDb:
    def __init__(self, proposal):
        self.proposal = proposal
        self.results = []
        self.finishes = []
        self.generations = []

    def claim_next(self, owner):
        return {
            "outbox_id": "job-1",
            "proposal_hash": self.proposal["proposalHash"],
            "lease_generation": 1,
            "proposal": self.proposal,
        }

    def claim_review_required(self, owner):
        return None

    def renew_lease(self, outbox_id, owner, generation, lease_seconds):
        self.generations.append(("renew", generation))
        return True

    def append_item_result(self, outbox_id, owner, generation, item):
        self.generations.append(("append", generation))
        self.results.append((outbox_id, owner, item))

    def finish(
        self,
        outbox_id,
        owner,
        generation,
        status,
        error=None,
        available_at=None,
    ):
        self.generations.append(("finish", generation))
        self.finishes.append((outbox_id, owner, status, error, available_at))


class FakeApi:
    api_base_url = "http://osb/api"
    api_headers = {}


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code
        self.text = content.decode() if isinstance(content, bytes) else str(content)

    def raise_for_status(self):
        return None

    def json(self):
        return json.loads(self.content)


def proposal(openapi_hash):
    return {
        "formatVersion": "osb-proposal/2.1",
        "canonicalizationVersion": "canonical-json/1.0",
        "proposalHash": "proposal-hash",
        "osbOpenApiHash": openapi_hash,
        "osbMappingContextHash": "context-hash",
        "sections": {
            "odm": [
                {
                    "proposalObjectId": "object-1",
                    "mapping": {
                        "factIds": ["fact-1"],
                        "proposedResourceType": "OdmItem",
                        "candidates": [],
                        "selectedCandidate": None,
                        "disposition": "unresolved",
                    },
                }
            ],
        },
        "sourceFactRefs": [{"factId": "fact-1", "factContentHash": "fact-hash"}],
        "reconciliation": {
            "balanced": True,
            "sourceFacts": 1,
            "mappedSourceFacts": 1,
            "proposedObjects": 1,
            "nativeStudyMutationTargets": 0,
            "governedLibraryReferenceTargets": 1,
            "governedExtensionTargets": 0,
            "retainedNarrativeTargets": 0,
            "unresolvedTargets": 0,
            "nativeTargetSourceFacts": 0,
            "fullyNativeTargetSourceFacts": 0,
            "dispositions": [],
        },
    }


def worker(db):
    value = object.__new__(ImportOsbProposalV2)
    value.db = db
    value.api = FakeApi()
    value.worker_id = "worker-1"
    value.log = logging.getLogger("test-proposal-worker")
    return value


def test_validated_skeleton_never_marks_unimplemented_native_import_succeeded(
    monkeypatch,
):
    openapi = b'{"openapi":"3.1.0"}'
    db = FakeDb(proposal(_stable_hash(json.loads(openapi))))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(openapi),
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda *_args, **_kwargs: FakeResponse(
            json.dumps(
                {
                    "proposal_hash": "proposal-hash",
                    "decided_object_count": 0,
                }
            ).encode()
        ),
    )

    result = worker(db).run_once()

    assert result["status"] == "review_required"
    assert db.results[0][2]["proposal_object_id"] == "object-1"
    assert db.generations
    assert {generation for _, generation in db.generations} == {1}
    assert db.results[0][2]["code"] == "OSB_PROPOSAL_ACCEPTED_FOR_REVIEW"
    assert db.finishes[-1][2] == "review_required"
    assert db.finishes[-1][3] == "OSB_PROPOSAL_REVIEW_REQUIRED"
    assert all(finish[2] != "succeeded" for finish in db.finishes)


def test_openapi_drift_stops_before_native_planning(monkeypatch):
    db = FakeDb(proposal("expected-hash"))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(b'{"changed":true}'),
    )

    result = worker(db).run_once()

    assert result == {"status": "failed_terminal", "code": "OSB_OPENAPI_HASH_STALE"}
    assert db.results[0][2]["code"] == "OSB_OPENAPI_HASH_STALE"
    assert db.finishes[-1][2] == "failed_terminal"


def test_openapi_network_failure_is_retryable(monkeypatch):
    db = FakeDb(proposal("expected-hash"))

    def fail(*_args, **_kwargs):
        raise requests.ConnectionError("OSB unavailable")

    monkeypatch.setattr(requests, "get", fail)
    with pytest.raises(requests.ConnectionError):
        worker(db).run_once()

    assert db.finishes[-1][2] == "failed_retryable"
    assert db.finishes[-1][4] is not None


def test_review_contract_rejection_is_terminal(monkeypatch):
    openapi = b'{"openapi":"3.1.0"}'
    db = FakeDb(proposal(_stable_hash(json.loads(openapi))))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(openapi),
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda *_args, **_kwargs: FakeResponse(b'{"detail":"unknown context"}', 400),
    )

    with pytest.raises(ValueError, match="OSB_PROPOSAL_REVIEW_REJECTED:400"):
        worker(db).run_once()

    assert db.finishes[-1][2] == "failed_terminal"


class FakeReviewDb(FakeDb):
    def __init__(self, proposal, review_job=True):
        super().__init__(proposal)
        self.review_job = review_job

    def claim_next(self, owner):
        return None

    def claim_review_required(self, owner):
        if not self.review_job:
            return None
        return {
            "outbox_id": "job-review-1",
            "proposal_hash": self.proposal["proposalHash"],
            "lease_generation": 1,
        }


def test_incomplete_osb_review_is_deferred_without_reposting(monkeypatch):
    db = FakeReviewDb(proposal("openapi"))
    post = pytest.fail
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(
            json.dumps(
                {
                    "proposal_hash": "proposal-hash",
                    "review_complete": False,
                    "decided_object_count": 1,
                }
            ).encode()
        ),
    )

    result = worker(db).run_once()

    assert result["status"] == "review_required"
    assert result["decided_objects"] == 1
    assert db.finishes[-1][2] == "review_required"
    assert db.finishes[-1][4] is not None


def test_completed_osb_review_advances_only_to_review_complete(monkeypatch):
    db = FakeReviewDb(proposal("openapi"))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(
            json.dumps(
                {
                    "proposal_hash": "proposal-hash",
                    "review_complete": True,
                    "decided_object_count": 2,
                    "rejected_object_count": 0,
                    "execution_blockers": ["OSB_NATIVE_V2_EXECUTOR_NOT_AVAILABLE"],
                }
            ).encode()
        ),
    )

    result = worker(db).run_once()

    assert result == {
        "status": "review_complete",
        "proposal_hash": "proposal-hash",
        "execution_blockers": ["OSB_NATIVE_V2_EXECUTOR_NOT_AVAILABLE"],
    }
    assert db.results[-1][2]["code"] == "OSB_PROPOSAL_REVIEW_COMPLETE"
    assert db.finishes[-1][2] == "review_complete"
    assert all(finish[2] != "succeeded" for finish in db.finishes)


def test_reviewer_rejection_is_terminal(monkeypatch):
    db = FakeReviewDb(proposal("openapi"))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(
            json.dumps(
                {
                    "proposal_hash": "proposal-hash",
                    "review_complete": True,
                    "decided_object_count": 2,
                    "rejected_object_count": 1,
                }
            ).encode()
        ),
    )

    result = worker(db).run_once()

    assert result == {
        "status": "failed_terminal",
        "code": "OSB_PROPOSAL_REVIEW_REJECTED",
    }
    assert db.finishes[-1][2] == "failed_terminal"


def test_lost_heartbeat_fails_closed_before_http_mutation(monkeypatch):
    class LostLeaseDb(FakeDb):
        def renew_lease(self, outbox_id, owner, generation, lease_seconds):
            self.generations.append(("renew-lost", generation))
            return False

    db = LostLeaseDb(proposal("expected-hash"))
    called = []
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: called.append(True))

    with pytest.raises(RuntimeError, match="OSB_PROPOSAL_LEASE_OWNERSHIP_LOST"):
        worker(db).run_once()

    assert called == []
    assert db.generations[0] == ("renew-lost", 1)
