"""Real PostgreSQL lease, RLS, and Fact-integrity tests for Proposal V2.

Set ECRF_TEST_OWNER_PG_DSN and ECRF_TEST_PG_DSN to an isolated database that has
migrations 001-039 applied. The worker DSN must use the non-owner osb_importer
role; fixture setup is the only code that uses owner credentials.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from ..utils.osb_proposal_db import OsbProposalDb, _stable_hash

OWNER_DSN = os.environ.get("ECRF_TEST_OWNER_PG_DSN")
WORKER_DSN = os.environ.get("ECRF_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(
    not OWNER_DSN or not WORKER_DSN,
    reason="disposable Proposal V2 PostgreSQL DSNs are not configured",
)


def _fixture():
    suffix = uuid.uuid4().hex
    tenant_id = f"t-proposal-worker-{suffix}"
    study_id = f"study-proposal-worker-{suffix}"
    document_id = f"document-proposal-worker-{suffix}"
    fact_id = str(uuid.uuid4())
    outbox_id = str(uuid.uuid4())
    fact = {
        "factId": fact_id,
        "tenantId": tenant_id,
        "studyId": study_id,
        "revision": 1,
        "lifecycle": "accepted",
        "lastOperation": "created",
        "exactQuote": "Blood Pressure",
        "classification": {
            "assertionType": "ASSESSMENT",
            "clinicalDomain": "vital_signs",
            "cdashDomain": "VS",
            "feedsBuild": True,
        },
        "quality": {"quarantined": False, "confidence": 0.9},
        "review": {"decision": "accepted", "humanEdited": False},
        "provenance": {"citations": [{"page": 1}]},
    }
    fact_hash = _stable_hash(fact)
    content = {
        "formatVersion": "osb-proposal/2.0",
        "proposalId": f"proposal-{suffix}",
        "tenantId": tenant_id,
        "studyId": study_id,
        "projectId": None,
        "sourceRunIds": ["run-1"],
        "sourceDocumentVersionIds": [document_id],
        "previousProposalHash": None,
        "osbOpenApiHash": "openapi-hash",
        "osbMappingContextHash": "context-hash",
        "authorityMode": "shadow",
        "sections": {
            "unresolved": [
                {
                    "proposalObjectId": f"object-{suffix}",
                    "mapping": {
                        "factIds": [fact_id],
                        "proposedResourceType": "OdmItem",
                        "candidates": [],
                        "selectedCandidate": None,
                        "disposition": "unresolved",
                    },
                }
            ]
        },
        "reconciliation": {
            "balanced": True,
            "sourceFacts": 1,
            "proposedObjects": 1,
            "mappedSourceFacts": 1,
            "dispositions": [],
        },
        "sourceFactRefs": [
            {
                "factId": fact_id,
                "revision": 1,
                "factContentHash": fact_hash,
            }
        ],
    }
    proposal_hash = _stable_hash(content)
    proposal = {**content, "proposalHash": proposal_hash}
    return {
        "tenant_id": tenant_id,
        "study_id": study_id,
        "document_id": document_id,
        "fact_id": fact_id,
        "outbox_id": outbox_id,
        "fact": fact,
        "proposal": proposal,
        "proposal_hash": proposal_hash,
    }


def _seed(owner, value):
    with owner.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO documents (
              document_id, tenant_id, study_id, filename, mime_type,
              content_hash, byte_size, status, registered_by,
              document_category, feeds_build
            ) VALUES (%s,%s,%s,'protocol.pdf','application/pdf',%s,1,
                      'REGISTERED','proposal-worker-test','PROTOCOL',TRUE)
            """,
            (
                value["document_id"],
                value["tenant_id"],
                value["study_id"],
                uuid.uuid4().hex,
            ),
        )
        cursor.execute(
            """
            INSERT INTO fact (
              fact_id, tenant_id, study_id, document_id, assertion_type,
              normalized_quote, first_seen_run_id
            ) VALUES (%s,%s,%s,%s,'ASSESSMENT','blood pressure','run-1')
            """,
            (
                value["fact_id"],
                value["tenant_id"],
                value["study_id"],
                value["document_id"],
            ),
        )
        cursor.execute(
            """
            INSERT INTO fact_revision (
              revision_id, tenant_id, fact_id, study_id, revision, operation,
              actor_id, actor_type, occurred_at, after_json
            ) VALUES (%s,%s,%s,%s,1,'created','proposal-worker-test','system',%s,
                      %s::jsonb)
            """,
            (
                str(uuid.uuid4()),
                value["tenant_id"],
                value["fact_id"],
                value["study_id"],
                datetime.now(timezone.utc),
                json.dumps(value["fact"]),
            ),
        )
        proposal = value["proposal"]
        cursor.execute(
            """
            INSERT INTO osb_study_proposals_v2 (
              tenant_id, proposal_hash, proposal_id, study_id, project_id,
              format_version, authority_mode, osb_openapi_hash,
              osb_mapping_context_hash, previous_proposal_hash,
              source_fact_count, eligible_approved_fact_count,
              proposed_object_count, mapped_source_fact_count,
              source_disposition_count, unresolved_count,
              reconciliation_balanced, proposal_jsonb, byte_size
            ) VALUES (%s,%s,%s,%s,NULL,'osb-proposal/2.0','shadow',
                      'openapi-hash','context-hash',NULL,1,1,1,1,0,1,TRUE,
                      %s::jsonb,%s)
            """,
            (
                value["tenant_id"],
                value["proposal_hash"],
                proposal["proposalId"],
                value["study_id"],
                json.dumps(proposal),
                len(json.dumps(proposal).encode("utf-8")),
            ),
        )
        cursor.execute(
            """
            INSERT INTO osb_proposal_outbox (
              tenant_id, outbox_id, proposal_hash, study_id
            ) VALUES (%s,%s,%s,%s)
            """,
            (
                value["tenant_id"],
                value["outbox_id"],
                value["proposal_hash"],
                value["study_id"],
            ),
        )
    owner.commit()


def _cleanup(owner, value):
    with owner.cursor() as cursor:
        for table in ("osb_study_proposals_v2", "fact_revision", "fact", "documents"):
            cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        try:
            cursor.execute(
                "DELETE FROM osb_proposal_outbox WHERE tenant_id = %s",
                (value["tenant_id"],),
            )
            cursor.execute(
                "DELETE FROM osb_study_proposals_v2 WHERE tenant_id = %s",
                (value["tenant_id"],),
            )
            cursor.execute(
                "DELETE FROM fact_state WHERE tenant_id = %s",
                (value["tenant_id"],),
            )
            cursor.execute(
                "DELETE FROM fact_revision WHERE tenant_id = %s",
                (value["tenant_id"],),
            )
            cursor.execute(
                "DELETE FROM fact WHERE tenant_id = %s",
                (value["tenant_id"],),
            )
            cursor.execute(
                "DELETE FROM documents WHERE tenant_id = %s",
                (value["tenant_id"],),
            )
        finally:
            for table in (
                "documents",
                "fact",
                "fact_revision",
                "osb_study_proposals_v2",
            ):
                cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
    owner.commit()


def test_worker_role_claims_renews_reclaims_and_finishes_exactly_once():
    value = _fixture()
    with psycopg.connect(OWNER_DSN, row_factory=dict_row) as owner:
        _seed(owner, value)
        first = OsbProposalDb(WORKER_DSN, value["tenant_id"])
        second = OsbProposalDb(WORKER_DSN, value["tenant_id"])
        other_tenant = OsbProposalDb(WORKER_DSN, f"other-{value['tenant_id']}")
        try:
            with first.conn.cursor() as cursor:
                cursor.execute(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
                role = cursor.fetchone()
            first.conn.commit()
            assert role == {
                "current_user": "osb_importer",
                "rolsuper": False,
                "rolbypassrls": False,
            }

            assert (
                first.claim_next(
                    "wrong-study-worker",
                    lease_seconds=30,
                    study_id="not-this-study",
                )
                is None
            )
            claimed = first.claim_next(
                "worker-1",
                lease_seconds=30,
                study_id=value["study_id"],
            )
            assert claimed["attempt"] == 1
            assert claimed["proposal"] == value["proposal"]
            assert second.claim_next("worker-2", lease_seconds=30) is None
            assert other_tenant.claim_next("other-worker", lease_seconds=30) is None

            assert first.renew_lease(value["outbox_id"], "worker-2", 30) is False
            assert first.renew_lease(value["outbox_id"], "worker-1", 30) is True
            with pytest.raises(RuntimeError, match="OSB_PROPOSAL_LEASE_OWNERSHIP_LOST"):
                second.append_item_result(
                    value["outbox_id"], "worker-2", {"status": "wrong-owner"}
                )
            first.append_item_result(
                value["outbox_id"], "worker-1", {"status": "planned"}
            )

            with owner.cursor() as cursor:
                cursor.execute(
                    "UPDATE osb_proposal_outbox "
                    "SET lease_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE tenant_id = %s AND outbox_id = %s",
                    (value["tenant_id"], value["outbox_id"]),
                )
            owner.commit()

            reclaimed = second.claim_next("worker-2", lease_seconds=30)
            assert reclaimed["attempt"] == 2
            with pytest.raises(RuntimeError, match="OSB_PROPOSAL_LEASE_OWNERSHIP_LOST"):
                first.finish(value["outbox_id"], "worker-1", "succeeded")
            second.append_item_result(
                value["outbox_id"], "worker-2", {"status": "reconciled"}
            )
            second.finish(value["outbox_id"], "worker-2", "succeeded")

            with second.conn.cursor() as cursor:
                cursor.execute(
                    "SELECT status, attempt, lease_owner, lease_expires_at, "
                    "completed_at, item_results FROM osb_proposal_outbox "
                    "WHERE outbox_id = %s",
                    (value["outbox_id"],),
                )
                completed = cursor.fetchone()
            second.conn.commit()
            assert completed["status"] == "succeeded"
            assert completed["attempt"] == 2
            assert completed["lease_owner"] is None
            assert completed["lease_expires_at"] is None
            assert completed["completed_at"] is not None
            assert completed["item_results"] == [
                {"status": "planned"},
                {"status": "reconciled"},
            ]

            with pytest.raises(psycopg.errors.RaiseException, match="terminal"):
                with second.conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE osb_proposal_outbox SET status = 'queued' "
                        "WHERE outbox_id = %s",
                        (value["outbox_id"],),
                    )
            second.conn.rollback()
        finally:
            first.close()
            second.close()
            other_tenant.close()
            _cleanup(owner, value)


def test_review_complete_is_claimed_only_by_the_native_execution_stage():
    value = _fixture()
    with psycopg.connect(OWNER_DSN, row_factory=dict_row) as owner:
        _seed(owner, value)
        db = OsbProposalDb(WORKER_DSN, value["tenant_id"])
        try:
            initial = db.claim_next("intake-worker", lease_seconds=30)
            db.finish(
                initial["outbox_id"],
                "intake-worker",
                "review_required",
                "OSB_PROPOSAL_REVIEW_REQUIRED",
                available_at=datetime.now(timezone.utc),
            )

            assert (
                db.claim_review_required(
                    "wrong-study-poller",
                    lease_seconds=30,
                    study_id="not-this-study",
                )
                is None
            )
            review = db.claim_review_required(
                "review-poller",
                lease_seconds=30,
                study_id=value["study_id"],
            )
            assert review["attempt"] == 2
            db.finish(
                review["outbox_id"],
                "review-poller",
                "review_complete",
                "OSB_NATIVE_V2_EXECUTION_PENDING",
            )

            # Intake must not reprocess an already-reviewed proposal.
            assert db.claim_next("intake-worker", lease_seconds=30) is None
            execution = db.claim_review_complete("native-worker", lease_seconds=30)
            assert execution["attempt"] == 3
            assert execution["proposal"] == value["proposal"]
            db.finish(
                execution["outbox_id"],
                "native-worker",
                "failed_terminal",
                "TEST_NATIVE_STAGE_END",
            )
        finally:
            db.close()
            _cleanup(owner, value)
