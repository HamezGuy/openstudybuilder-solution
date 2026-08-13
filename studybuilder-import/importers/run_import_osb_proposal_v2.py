"""Claim and validate one Fact-based OSB Proposal V2 delivery job.

The worker proves leasing, exact Fact revision checks, OpenAPI pinning and
deterministic planning, then hands the immutable envelope to OSB's durable
item-level review inbox. It does not call V1 mapping code and deliberately cannot
report ``succeeded`` until reviewed native execution and reconciliation complete.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import requests

from .mappings.proposal_v2_to_osb import ProposalPlanError, proposal_object_plan
from .utils.importer import BaseImporter
from .utils.metrics import Metrics
from .utils.osb_proposal_db import (
    OsbProposalDb,
    OsbProposalIntegrityError,
    _stable_hash,
)


class ImportOsbProposalV2(BaseImporter):
    logging_name = "import_osb_proposal_v2"
    lease_seconds = 300
    heartbeat_seconds = 60

    def __init__(
        self,
        api=None,
        metrics_inst=None,
        db=None,
        worker_id=None,
        study_id=None,
    ):
        super().__init__(api=api, metrics_inst=metrics_inst)
        self.db = db or OsbProposalDb(log=self.log)
        self.worker_id = worker_id or f"{socket.gethostname()}:{id(self)}"
        self.study_id = study_id

    def _append(self, job, item):
        self.db.append_item_result(
            job["outbox_id"],
            self.worker_id,
            job["lease_generation"],
            item,
        )

    def _finish(self, job, status, error=None, available_at=None):
        self.db.finish(
            job["outbox_id"],
            self.worker_id,
            job["lease_generation"],
            status,
            error,
            available_at=available_at,
        )

    @contextmanager
    def _heartbeat(self, job):
        """Fence long HTTP work with immediate and periodic lease renewal."""
        if not hasattr(self.db, "renew_lease"):
            yield
            return
        if not self.db.renew_lease(
            job["outbox_id"],
            self.worker_id,
            job["lease_generation"],
            self.lease_seconds,
        ):
            raise RuntimeError("OSB_PROPOSAL_LEASE_OWNERSHIP_LOST")
        stopped = threading.Event()
        lost = []

        def renew():
            while not stopped.wait(self.heartbeat_seconds):
                if not self.db.renew_lease(
                    job["outbox_id"],
                    self.worker_id,
                    job["lease_generation"],
                    self.lease_seconds,
                ):
                    lost.append(True)
                    stopped.set()

        thread = threading.Thread(
            target=renew, name="osb-proposal-heartbeat", daemon=True
        )
        thread.start()
        try:
            yield
            if lost:
                raise RuntimeError("OSB_PROPOSAL_LEASE_OWNERSHIP_LOST")
        finally:
            stopped.set()
            thread.join(timeout=1)

    def _live_openapi_hash(self):
        response = requests.get(
            f"{self.api.api_base_url.rstrip('/')}/openapi.json",
            headers=self.api.api_headers,
            timeout=30,
        )
        response.raise_for_status()
        return _stable_hash(response.json())

    def _handoff_to_review(self, proposal):
        response = requests.post(
            f"{self.api.api_base_url.rstrip('/')}/integrations/proposal-reviews",
            headers={**self.api.api_headers, "Content-Type": "application/json"},
            json={"proposal": proposal, "worker_id": self.worker_id},
            timeout=30,
        )
        if 400 <= response.status_code < 500:
            raise OsbProposalIntegrityError(
                f"OSB_PROPOSAL_REVIEW_REJECTED:{response.status_code}:"
                f"{response.text[:500]}"
            )
        response.raise_for_status()
        review = response.json()
        if review.get("proposal_hash") != proposal["proposalHash"]:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_REVIEW_RECEIPT_MISMATCH")
        return review

    def _review_status(self, proposal_hash):
        response = requests.get(
            f"{self.api.api_base_url.rstrip('/')}/integrations/proposal-reviews/{proposal_hash}",
            headers=self.api.api_headers,
            timeout=30,
        )
        if 400 <= response.status_code < 500:
            raise OsbProposalIntegrityError(
                f"OSB_PROPOSAL_REVIEW_STATUS_REJECTED:{response.status_code}:"
                f"{response.text[:500]}"
            )
        response.raise_for_status()
        review = response.json()
        if review.get("proposal_hash") != proposal_hash:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_REVIEW_RECEIPT_MISMATCH")
        return review

    def _poll_review(self, job):
        outbox_id = job["outbox_id"]
        proposal_hash = job["proposal_hash"]
        with self._heartbeat(job):
            review = self._review_status(proposal_hash)
        if not review.get("review_complete"):
            self._finish(
                job,
                "review_required",
                "OSB_PROPOSAL_REVIEW_REQUIRED",
                available_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            return {
                "status": "review_required",
                "proposal_hash": proposal_hash,
                "decided_objects": review.get("decided_object_count", 0),
            }
        if review.get("rejected_object_count", 0) > 0:
            self._append(
                job,
                {
                    "kind": "proposal",
                    "status": "failed_terminal",
                    "code": "OSB_PROPOSAL_REVIEW_REJECTED",
                    "rejected_objects": review["rejected_object_count"],
                },
            )
            self._finish(
                job,
                "failed_terminal",
                "OSB_PROPOSAL_REVIEW_REJECTED",
            )
            return {
                "status": "failed_terminal",
                "code": "OSB_PROPOSAL_REVIEW_REJECTED",
            }
        self._append(
            job,
            {
                "kind": "proposal",
                "status": "review_complete",
                "code": "OSB_PROPOSAL_REVIEW_COMPLETE",
                "decided_objects": review.get("decided_object_count", 0),
                "execution_blockers": review.get("execution_blockers", []),
            },
        )
        self._finish(
            job,
            "review_complete",
            "OSB_NATIVE_V2_EXECUTION_PENDING",
        )
        return {
            "status": "review_complete",
            "proposal_hash": proposal_hash,
            "execution_blockers": review.get("execution_blockers", []),
        }

    def run_once(self):
        job = self.db.claim_next(self.worker_id, study_id=self.study_id)
        if job is None:
            review_job = self.db.claim_review_required(
                self.worker_id,
                study_id=self.study_id,
            )
            if review_job is None:
                self.log.info(
                    "No queued, reclaimable, or review-pending OSB Proposal V2 job"
                )
                return None
            try:
                return self._poll_review(review_job)
            except OsbProposalIntegrityError as error:
                self._finish(
                    review_job,
                    "failed_terminal",
                    str(error),
                )
                raise
            except requests.RequestException as error:
                self._finish(
                    review_job,
                    "review_required",
                    f"OSB_REVIEW_STATUS_UNAVAILABLE:{error}",
                    available_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                )
                raise
            except Exception as error:
                self._finish(
                    review_job,
                    "review_required",
                    f"OSB_REVIEW_STATUS_ERROR:{error}",
                    available_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                )
                raise

        outbox_id = job["outbox_id"]
        proposal = job["proposal"]
        try:
            with self._heartbeat(job):
                live_openapi_hash = self._live_openapi_hash()
            if live_openapi_hash != proposal["osbOpenApiHash"]:
                self._append(
                    job,
                    {
                        "kind": "proposal",
                        "status": "failed_terminal",
                        "code": "OSB_OPENAPI_HASH_STALE",
                        "expected": proposal["osbOpenApiHash"],
                        "actual": live_openapi_hash,
                    },
                )
                self._finish(
                    job,
                    "failed_terminal",
                    "OSB_OPENAPI_HASH_STALE",
                )
                return {"status": "failed_terminal", "code": "OSB_OPENAPI_HASH_STALE"}

            plan = proposal_object_plan(proposal)
            with self._heartbeat(job):
                review = self._handoff_to_review(proposal)
            for item in plan:
                self._append(
                    job,
                    {
                        **item,
                        "status": "review_required",
                        "code": "OSB_PROPOSAL_ACCEPTED_FOR_REVIEW",
                    },
                )

            # OSB now owns the review queue, but a review receipt is not a native,
            # reconciled study and must never be reported as succeeded.
            self._finish(
                job,
                "review_required",
                "OSB_PROPOSAL_REVIEW_REQUIRED",
                available_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            return {
                "status": "review_required",
                "proposal_hash": proposal["proposalHash"],
                "planned_objects": len(plan),
                "decided_objects": review.get("decided_object_count", 0),
            }
        except (OsbProposalIntegrityError, ProposalPlanError) as error:
            self._finish(
                job,
                "failed_terminal",
                str(error),
            )
            raise
        except requests.RequestException as error:
            self._finish(
                job,
                "failed_retryable",
                f"OSB_PROPOSAL_OSB_UNAVAILABLE:{error}",
                available_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            raise
        except Exception as error:
            self._finish(
                job,
                "failed_retryable",
                f"OSB_PROPOSAL_WORKER_ERROR:{error}",
                available_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            raise


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="run_import_osb_proposal_v2.py")
    parser.add_argument(
        "--study",
        default=os.environ.get("ECRF_STUDY_ID"),
        help="Limit this worker invocation to one study id",
    )
    args = parser.parse_args()

    metrics = Metrics()
    importer = ImportOsbProposalV2(metrics_inst=metrics, study_id=args.study)
    try:
        result = importer.run_once()
    finally:
        importer.db.close()
    metrics.print()
    return (
        0
        if result is None
        or result.get("status") in {"review_required", "review_complete"}
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
