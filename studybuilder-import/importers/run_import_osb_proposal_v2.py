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
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import requests

from .mappings.proposal_v2_native_operations import (
    NativeOperationPlanError,
    native_operation_plan,
)
from .mappings.proposal_v2_to_osb import ProposalPlanError, proposal_object_plan
from .utils.importer import BaseImporter
from .utils.metrics import Metrics
from .utils.osb_proposal_db import (
    OsbProposalDb,
    OsbProposalIntegrityError,
    _stable_hash,
)


class NativeOperationBlocked(RuntimeError):
    """A reviewed proposal cannot safely execute without operator correction."""

    def __init__(self, blockers):
        self.blockers = blockers
        super().__init__(";".join(item["code"] for item in blockers))


class NativeOperationReconciliationError(RuntimeError):
    """A native write did not produce one unambiguous read-back match."""


class WorkerScopeError(RuntimeError):
    """The worker was not bound to one tenant, source study, and OSB target."""


def _hashed_evidence(payload):
    return {**payload, "contentHash": _stable_hash(payload)}


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
        target_study_uid=None,
        target_study_version=None,
    ):
        super().__init__(api=api, metrics_inst=metrics_inst)
        self.db = db or OsbProposalDb(log=self.log)
        self.worker_id = worker_id or f"{socket.gethostname()}:{id(self)}"
        self.study_id = study_id
        self.target_study_uid = target_study_uid or os.environ.get(
            "OSB_TARGET_STUDY_UID"
        )
        self.target_study_version = (
            target_study_version
            or os.environ.get("OSB_TARGET_STUDY_VERSION")
            or "DRAFT"
        )

    def _append(self, job, item):
        self.db.append_item_result(
            job["outbox_id"],
            self.worker_id,
            job["lease_generation"],
            item,
        )

    def _assert_worker_scope(self):
        tenant_id = str(getattr(self.db, "tenant_id", "") or "").strip()
        source_study_id = str(self.study_id or "").strip()
        target_study_uid = str(self.target_study_uid or "").strip()
        if not tenant_id:
            raise WorkerScopeError("OSB_WORKER_TENANT_SCOPE_REQUIRED")
        if not source_study_id or "*" in source_study_id:
            raise WorkerScopeError("OSB_WORKER_SOURCE_STUDY_SCOPE_REQUIRED")
        if not target_study_uid or "*" in target_study_uid:
            raise WorkerScopeError("OSB_WORKER_TARGET_STUDY_SCOPE_REQUIRED")

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

    @staticmethod
    def _path_value(value, path):
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @classmethod
    def _matching_records(cls, payload, expected, collection=True):
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            records = payload["items"]
        elif isinstance(payload, list):
            records = payload
        elif not collection and isinstance(payload, dict):
            records = [payload]
        else:
            raise NativeOperationReconciliationError(
                "OSB_NATIVE_V2_READ_BACK_COLLECTION_INVALID"
            )
        return [
            record
            for record in records
            if isinstance(record, dict)
            and all(
                cls._path_value(record, key) == value for key, value in expected.items()
            )
        ]

    @staticmethod
    def _versions_equal(actual, expected):
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except (InvalidOperation, TypeError, ValueError):
            return str(actual).strip() == str(expected).strip()

    def _assert_source_binding(self, job, proposal, review):
        source_study_id = str(self.study_id or "").strip()
        if not source_study_id:
            raise NativeOperationPlanError("OSB_NATIVE_V2_SOURCE_STUDY_SCOPE_REQUIRED")
        identities = {
            "claimed": job.get("study_id"),
            "proposal": proposal.get("studyId"),
            "review": review.get("source_study_id"),
        }
        if any(value != source_study_id for value in identities.values()):
            raise OsbProposalIntegrityError(
                "OSB_NATIVE_V2_SOURCE_STUDY_MISMATCH:"
                + json.dumps(identities, sort_keys=True)
            )

    @staticmethod
    def _timestamps_equal(actual, expected):
        if actual is None or expected is None:
            return False
        try:
            left = datetime.fromisoformat(str(actual).replace("Z", "+00:00"))
            right = datetime.fromisoformat(str(expected).replace("Z", "+00:00"))
            return left == right
        except ValueError:
            return str(actual).strip() == str(expected).strip()

    def _assert_target_study_preconditions(
        self,
        authorization=None,
        snapshot_consumed=False,
    ):
        target_uid = str(self.target_study_uid or "").strip()
        target_version = str(self.target_study_version or "").strip()
        if not target_uid:
            raise NativeOperationPlanError("OSB_NATIVE_V2_TARGET_STUDY_REQUIRED")
        if not target_version:
            raise NativeOperationPlanError(
                "OSB_NATIVE_V2_TARGET_STUDY_VERSION_REQUIRED"
            )
        study = self.api.proposal_v2_get(f"/studies/{target_uid}")
        metadata = (study.get("current_metadata") or {}).get("version_metadata") or {}
        actual_uid = study.get("uid")
        actual_status = metadata.get("study_status")
        actual_version = (
            "DRAFT" if actual_status == "DRAFT" else metadata.get("version_number")
        )
        blockers = []
        if actual_uid != target_uid:
            blockers.append(
                {
                    "code": "OSB_NATIVE_V2_TARGET_STUDY_MISMATCH",
                    "details": [str(actual_uid), target_uid],
                }
            )
        if actual_status != "DRAFT":
            blockers.append(
                {
                    "code": "OSB_NATIVE_V2_TARGET_STUDY_NOT_DRAFT",
                    "details": [str(actual_status)],
                }
            )
        if not self._versions_equal(actual_version, target_version):
            blockers.append(
                {
                    "code": "OSB_NATIVE_V2_TARGET_STUDY_VERSION_STALE",
                    "details": [str(actual_version), target_version],
                }
            )
        if (
            authorization
            and not snapshot_consumed
            and not self._timestamps_equal(
                metadata.get("version_timestamp"),
                authorization.get("target_version_start_date"),
            )
        ):
            blockers.append(
                {
                    "code": "OSB_NATIVE_V2_TARGET_STUDY_SNAPSHOT_STALE",
                    "details": [
                        str(metadata.get("version_timestamp")),
                        str(authorization.get("target_version_start_date")),
                    ],
                }
            )
        if blockers:
            raise NativeOperationBlocked(blockers)
        return study

    def _read_operation_matches(self, operation):
        read = operation["read_after_write"]
        payload = self.api.proposal_v2_get(read["path"], params=read.get("params"))
        return self._matching_records(
            payload,
            read["match"],
            collection=read.get("collection", True),
        )

    def _native_record_hash(self, operation, record):
        scope = operation.get("record_hash_scope", "record")
        if scope == "record":
            return _stable_hash(record)
        if scope == "match":
            match = operation["read_after_write"]["match"]
            projection = {
                path: self._path_value(record, path) for path in sorted(match)
            }
            return _stable_hash(projection)
        raise NativeOperationReconciliationError(
            "OSB_NATIVE_V2_RECORD_HASH_SCOPE_UNSUPPORTED:" + str(scope)
        )

    @staticmethod
    def _set_nested(target, path, value):
        # A batch route's body is a list of envelopes ("0.content.x"): a
        # numeric segment indexes a list, every other segment a mapping.
        parts = path.split(".")
        cursor = target
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            if isinstance(cursor, list):
                position = int(part)
                if last:
                    cursor[position] = value
                else:
                    cursor = cursor[position]
            elif last:
                cursor[part] = value
            else:
                cursor = cursor.setdefault(part, {})

    def _resolve_operation_references(self, operation, receipts):
        if not operation.get("body_references"):
            return operation
        resolved = deepcopy(operation)
        receipt_by_operation = {
            (receipt.get("proposal_object_id"), receipt.get("family")): receipt
            for receipt in receipts
            if receipt.get("proposal_object_id") and receipt.get("family")
        }
        for reference in resolved["body_references"]:
            receipt = receipt_by_operation.get(
                (reference.get("proposal_object_id"), reference.get("family"))
            )
            native_uid = receipt.get("native_uid") if receipt else None
            if not native_uid:
                raise NativeOperationReconciliationError(
                    "OSB_NATIVE_V2_REFERENCE_RECEIPT_MISSING:"
                    + operation["proposal_object_id"]
                )
            self._set_nested(resolved["body"], reference["body_path"], native_uid)
            # Reconciliation predicates intentionally use flat dotted-path
            # keys; `_matching_records` resolves each key against the native
            # response.  Only request bodies are nested DTO structures.
            resolved["read_after_write"]["match"][
                reference["read_match_path"]
            ] = native_uid
        return resolved

    @staticmethod
    def _native_uid(operation, record):
        key = {
            "StudyMetadata": "uid",
            "StudySelectionArm": "arm_uid",
            "StudySelectionElement": "element_uid",
            "StudyEpoch": "uid",
            "StudyDesignCell": "design_cell_uid",
            "StudyVisit": "uid",
            "StudySelectionObjective": "study_objective_uid",
            "StudySelectionEndpoint": "study_endpoint_uid",
            "StudySelectionCriteria": "study_criteria_uid",
            "StudySelectionActivity": "study_activity_uid",
            "StudyActivitySchedule": "study_activity_schedule_uid",
            "StudyStandardVersion": "uid",
            "StudySelectionCompound": "study_compound_uid",
            "StudyCompoundDosing": "study_compound_dosing_uid",
            "StudyActivityInstruction": "study_activity_instruction_uid",
        }[operation["family"]]
        return record.get(key)

    @staticmethod
    def _receipt_index(item_results):
        receipts = {}
        for item in item_results or []:
            if (
                item.get("kind") == "native_operation"
                and item.get("status") == "reconciled"
                and item.get("idempotency_key")
            ):
                receipts[item["idempotency_key"]] = item
        return receipts

    def _validate_prior_receipt(
        self,
        proposal,
        operation,
        receipt,
        authorization_content_hash,
    ):
        expected = {
            "proposal_hash": proposal["proposalHash"],
            "proposal_object_id": operation["proposal_object_id"],
            "family": operation["family"],
            "target_study_uid": self.target_study_uid,
            "target_study_version": self.target_study_version,
            "idempotency_key": operation["idempotency_key"],
            "authorization_content_hash": authorization_content_hash,
            "native_record_hash_scope": operation.get("record_hash_scope", "record"),
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise OsbProposalIntegrityError(
                "OSB_NATIVE_V2_PERSISTED_RECEIPT_BINDING_MISMATCH:"
                + operation["proposal_object_id"]
            )

    def _execute_native_operation(
        self,
        job,
        proposal,
        operation,
        prior_receipt,
        authorization_content_hash,
    ):
        before = self._read_operation_matches(operation)
        if len(before) > 1:
            raise NativeOperationReconciliationError(
                "OSB_NATIVE_V2_READ_BACK_AMBIGUOUS:" + operation["proposal_object_id"]
            )
        if prior_receipt is not None:
            self._validate_prior_receipt(
                proposal,
                operation,
                prior_receipt,
                authorization_content_hash,
            )
            if len(before) != 1:
                raise OsbProposalIntegrityError(
                    "OSB_NATIVE_V2_PERSISTED_RECEIPT_DIVERGED:"
                    + operation["proposal_object_id"]
                )
            if self._native_record_hash(operation, before[0]) != prior_receipt.get(
                "native_record_hash"
            ):
                raise OsbProposalIntegrityError(
                    "OSB_NATIVE_V2_PERSISTED_RECEIPT_RECORD_DIVERGED:"
                    + operation["proposal_object_id"]
                )
            return prior_receipt

        write_response = None
        if before:
            write_disposition = "already_present"
        else:
            if operation["method"] == "POST":
                writer = self.api.proposal_v2_post
            elif operation["method"] == "PATCH":
                writer = self.api.proposal_v2_patch
            else:
                raise NativeOperationReconciliationError(
                    "OSB_NATIVE_V2_METHOD_UNSUPPORTED:" + operation["method"]
                )
            write_response = writer(
                operation["path"],
                operation["body"],
                params=operation.get("params"),
                idempotency_key=operation["idempotency_key"],
                proposal_object_id=operation["proposal_object_id"],
            )
            write_disposition = "created"

        after = self._read_operation_matches(operation)
        if len(after) != 1:
            suffix = "MISSING" if not after else "AMBIGUOUS"
            raise NativeOperationReconciliationError(
                f"OSB_NATIVE_V2_READ_BACK_{suffix}:" + operation["proposal_object_id"]
            )
        record = after[0]
        receipt = {
            "kind": "native_operation",
            "status": "reconciled",
            "code": "OSB_NATIVE_V2_OPERATION_RECONCILED",
            "proposal_hash": proposal["proposalHash"],
            "proposal_object_id": operation["proposal_object_id"],
            "family": operation["family"],
            "idempotency_key": operation["idempotency_key"],
            "target_study_uid": self.target_study_uid,
            "target_study_version": self.target_study_version,
            "authorization_content_hash": authorization_content_hash,
            "write_disposition": write_disposition,
            "native_uid": self._native_uid(operation, record),
            "native_record_hash_scope": operation.get("record_hash_scope", "record"),
            "native_record_hash": self._native_record_hash(operation, record),
            "write_response_hash": (
                _stable_hash(write_response) if write_response is not None else None
            ),
            "match": operation["read_after_write"]["match"],
        }
        self._append(job, receipt)
        return receipt

    def _execute_reviewed_native_job(self, job):
        proposal = job["proposal"]
        with self._heartbeat(job):
            review = self._review_status(proposal["proposalHash"])
            self._assert_source_binding(job, proposal, review)
            prior_receipts = self._receipt_index(job.get("item_results"))
            snapshot_consumed = any(
                receipt.get("family") == "StudyMetadata"
                for receipt in prior_receipts.values()
            )
            plan = native_operation_plan(
                proposal,
                review,
                self.target_study_uid,
                self.target_study_version,
                allow_stale_target_snapshot=snapshot_consumed,
            )
            if plan["blockers"]:
                raise NativeOperationBlocked(plan["blockers"])

            authorization = review.get("execution_authorization") or {}
            authorization_content_hash = authorization.get("authorization_content_hash")
            self._assert_target_study_preconditions(
                authorization,
                snapshot_consumed=snapshot_consumed,
            )
            receipts = []
            for operation in plan["operations"]:
                try:
                    resolved_operation = self._resolve_operation_references(
                        operation,
                        [*prior_receipts.values(), *receipts],
                    )
                    receipt = self._execute_native_operation(
                        job,
                        proposal,
                        resolved_operation,
                        prior_receipts.get(operation["idempotency_key"]),
                        authorization_content_hash,
                    )
                except Exception as error:
                    self._append(
                        job,
                        {
                            "kind": "native_operation",
                            "status": "failed",
                            "code": "OSB_NATIVE_V2_OPERATION_FAILED",
                            "proposal_hash": proposal["proposalHash"],
                            "proposal_object_id": operation["proposal_object_id"],
                            "family": operation["family"],
                            "idempotency_key": operation["idempotency_key"],
                            "target_study_uid": self.target_study_uid,
                            "target_study_version": self.target_study_version,
                            "error": str(error)[:1000],
                        },
                    )
                    raise
                receipts.append(receipt)

            reconciliation_rows = []
            for operation in plan["operations"]:
                resolved_operation = self._resolve_operation_references(
                    operation,
                    receipts,
                )
                matches = self._read_operation_matches(resolved_operation)
                if len(matches) != 1:
                    raise NativeOperationReconciliationError(
                        "OSB_NATIVE_V2_FINAL_RECONCILIATION_FAILED:"
                        + operation["proposal_object_id"]
                    )
                receipt = next(
                    item
                    for item in receipts
                    if item["idempotency_key"] == operation["idempotency_key"]
                )
                if self._native_record_hash(
                    resolved_operation, matches[0]
                ) != receipt.get("native_record_hash"):
                    raise NativeOperationReconciliationError(
                        "OSB_NATIVE_V2_FINAL_RECORD_DIVERGED:"
                        + operation["proposal_object_id"]
                    )
                reconciliation_rows.append(
                    {
                        "proposalObjectId": operation["proposal_object_id"],
                        "idempotencyKey": operation["idempotency_key"],
                        "match": resolved_operation["read_after_write"]["match"],
                        "nativeRecordHashScope": resolved_operation.get(
                            "record_hash_scope", "record"
                        ),
                        "nativeRecordHash": self._native_record_hash(
                            resolved_operation, matches[0]
                        ),
                    }
                )
            self._assert_target_study_preconditions(
                authorization,
                snapshot_consumed=any(
                    receipt.get("family") == "StudyMetadata" for receipt in receipts
                ),
            )

        native_evidence = _hashed_evidence(
            {
                "schemaVersion": "osb-native-execution/1.0",
                "proposalHash": proposal["proposalHash"],
                "sourceStudyId": job["study_id"],
                "targetStudyUid": self.target_study_uid,
                "targetStudyVersion": self.target_study_version,
                "operationCount": len(plan["operations"]),
                "releaseReady": bool(review.get("release_ready")),
                "releaseBlockers": review.get("release_blockers") or [],
                "deferredObjects": plan.get("deferred_objects") or [],
                "receipts": receipts,
            }
        )
        reconciliation_evidence = _hashed_evidence(
            {
                "schemaVersion": "osb-native-reconciliation/1.0",
                "proposalHash": proposal["proposalHash"],
                "targetStudyUid": self.target_study_uid,
                "targetStudyVersion": self.target_study_version,
                "operationCount": len(plan["operations"]),
                "allReconciled": len(reconciliation_rows) == len(plan["operations"]),
                "releaseReady": bool(review.get("release_ready")),
                "releaseBlockers": review.get("release_blockers") or [],
                "rows": reconciliation_rows,
            }
        )
        delivery_status = self.db.finish_native_execution(
            job["outbox_id"],
            self.worker_id,
            job["lease_generation"],
            native_evidence,
            reconciliation_evidence,
        )
        return {
            "status": delivery_status,
            "proposal_hash": proposal["proposalHash"],
            "target_study_uid": self.target_study_uid,
            "planned_operations": len(plan["operations"]),
            "reconciled_operations": len(reconciliation_rows),
        }

    def _poll_review(self, job):
        proposal_hash = job["proposal_hash"]
        with self._heartbeat(job):
            review = self._review_status(proposal_hash)
        if int(review.get("object_count") or 0) == 0:
            # An empty proposal can be a valid, fully verified projection when no
            # source facts have crossed the human-approval gate. Keep it visible
            # as blocked review work without waking the worker every minute or
            # advancing it into the native execution queue.
            self._append(
                job,
                {
                    "kind": "proposal",
                    "status": "review_required",
                    "code": "OSB_PROPOSAL_REVIEW_EMPTY",
                    "object_count": 0,
                },
            )
            self._finish(
                job,
                "review_required",
                "OSB_PROPOSAL_REVIEW_EMPTY",
                available_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            return {
                "status": "review_required",
                "proposal_hash": proposal_hash,
                "planned_objects": 0,
                "decided_objects": 0,
                "blocker": "OSB_PROPOSAL_REVIEW_EMPTY",
            }
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

    def _defer_native_execution(self, job, blockers):
        codes = []
        for blocker in blockers:
            code = blocker.get("code") or "OSB_NATIVE_V2_BLOCKED"
            codes.append(code)
            self._append(
                job,
                {
                    "kind": "native_operation_plan",
                    "status": "blocked",
                    "proposal_object_id": blocker.get("proposal_object_id"),
                    "code": code,
                    "details": blocker.get("details") or [],
                    "target_study_uid": self.target_study_uid,
                    "target_study_version": self.target_study_version,
                },
            )
        error = "OSB_NATIVE_V2_EXECUTION_BLOCKED:" + ",".join(codes)
        self._finish(
            job,
            "review_complete",
            error,
            available_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        return {
            "status": "review_complete",
            "proposal_hash": job["proposal_hash"],
            "execution_blockers": blockers,
        }

    def _append_native_failure(self, job, code, error):
        self._append(
            job,
            {
                "kind": "native_execution",
                "status": "failed",
                "code": code,
                "error": str(error)[:1000],
                "target_study_uid": self.target_study_uid,
                "target_study_version": self.target_study_version,
            },
        )

    def _run_native_stage(self, job):
        try:
            return self._execute_reviewed_native_job(job)
        except NativeOperationPlanError as error:
            return self._defer_native_execution(
                job,
                [{"code": str(error), "details": []}],
            )
        except NativeOperationBlocked as error:
            return self._defer_native_execution(job, error.blockers)
        except OsbProposalIntegrityError as error:
            self._append_native_failure(job, "OSB_NATIVE_V2_INTEGRITY_FAILURE", error)
            self._finish(job, "failed_terminal", str(error))
            raise
        except (requests.RequestException, NativeOperationReconciliationError) as error:
            self._append_native_failure(job, "OSB_NATIVE_V2_EXECUTION_RETRYABLE", error)
            self._finish(
                job,
                "failed_retryable",
                f"OSB_NATIVE_V2_EXECUTION_RETRYABLE:{error}",
                available_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            raise
        except Exception as error:
            self._append_native_failure(job, "OSB_NATIVE_V2_EXECUTION_ERROR", error)
            self._finish(
                job,
                "failed_retryable",
                f"OSB_NATIVE_V2_EXECUTION_ERROR:{error}",
                available_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            raise

    def run_once(self):
        # Refuse before claiming a lease. An absent scope must never turn the
        # worker's optional SQL predicate into a tenant-wide job scan.
        self._assert_worker_scope()
        job = self.db.claim_next(self.worker_id, study_id=self.study_id)
        if job is None:
            review_job = self.db.claim_review_required(
                self.worker_id,
                study_id=self.study_id,
            )
            if review_job is None:
                native_job = self.db.claim_review_complete(
                    self.worker_id,
                    study_id=self.study_id,
                )
                if native_job is None:
                    self.log.info(
                        "No queued, review-pending, or native-execution "
                        "OSB Proposal V2 job"
                    )
                    return None
                return self._run_native_stage(native_job)
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
            # reconciled study and must never be reported as succeeded. An empty
            # plan means the source contains no human-approved facts yet; poll it
            # daily rather than creating a hot one-minute retry loop.
            empty_plan = len(plan) == 0
            review_code = (
                "OSB_PROPOSAL_REVIEW_EMPTY"
                if empty_plan
                else "OSB_PROPOSAL_REVIEW_REQUIRED"
            )
            if empty_plan:
                self._append(
                    job,
                    {
                        "kind": "proposal",
                        "status": "review_required",
                        "code": review_code,
                        "object_count": 0,
                    },
                )
            self._finish(
                job,
                "review_required",
                review_code,
                available_at=datetime.now(timezone.utc)
                + (timedelta(days=1) if empty_plan else timedelta(minutes=1)),
            )
            return {
                "status": "review_required",
                "proposal_hash": proposal["proposalHash"],
                "planned_objects": len(plan),
                "decided_objects": review.get("decided_object_count", 0),
                **({"blocker": review_code} if empty_plan else {}),
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
        help="Required source-study scope for native execution",
    )
    parser.add_argument(
        "--target-study-uid",
        default=os.environ.get("OSB_TARGET_STUDY_UID"),
        help="Explicit OSB Study UID that reviewed native operations may mutate",
    )
    parser.add_argument(
        "--target-study-version",
        default=os.environ.get("OSB_TARGET_STUDY_VERSION") or "DRAFT",
        help="Logical target version; OSB live drafts use the literal DRAFT",
    )
    args = parser.parse_args()

    metrics = Metrics()
    importer = ImportOsbProposalV2(
        metrics_inst=metrics,
        study_id=args.study,
        target_study_uid=args.target_study_uid,
        target_study_version=args.target_study_version,
    )
    try:
        result = importer.run_once()
    finally:
        importer.db.close()
    metrics.print()
    return (
        0
        if result is None
        or result.get("status") in {"review_required", "review_complete", "succeeded"}
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
