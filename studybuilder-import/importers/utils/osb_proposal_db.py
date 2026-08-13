"""Tenant-scoped Proposal V2/outbox bridge for the OSB worker.

This module is deliberately separate from ``ecrf_platform_db.py``: V1 reads an
EDC-derived carrier, while V2 reads immutable Fact-based proposals and owns a
leased transactional outbox. It never mutates proposal content.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
from decimal import Decimal
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from ..functions.utils import load_env

PROPOSAL_FORMAT_VERSION = "osb-proposal/2.1"
CANONICAL_JSON_VERSION = "canonical-json/1.0"
MAX_PROPOSAL_BYTES = 32 * 1024 * 1024
MAX_PROPOSAL_DEPTH = 20
MAX_PROPOSAL_NODES = 600_000
DELIVERY_STATUSES = {
    "queued",
    "running",
    "review_required",
    "review_complete",
    "succeeded",
    "failed_retryable",
    "failed_terminal",
}
TERMINAL_STATUSES = {"succeeded", "failed_terminal"}


def _utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def _canonical_json(value):
    """Match Proposal V2's TypeScript ``canonicalJson`` for Fact hashes."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        if abs(value) <= 9_007_199_254_740_991:
            return str(value)
        value = float(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("CANONICAL_JSON_NON_FINITE_NUMBER")
        if value == 0:
            return "0"
        absolute = abs(value)
        raw = repr(value).lower()
        if 1e-6 <= absolute < 1e21:
            if "e" in raw:
                raw = format(Decimal(raw), "f")
            if "." in raw:
                raw = raw.rstrip("0").rstrip(".")
            return raw
        if "e" not in raw:
            raw = format(value, ".15e")
        mantissa, exponent = raw.split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        exponent_value = int(exponent)
        sign = "+" if exponent_value >= 0 else "-"
        return f"{mantissa}e{sign}{abs(exponent_value)}"
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("CANONICAL_JSON_OBJECT_KEY_NOT_STRING")
        return (
            "{"
            + ",".join(
                f"{_canonical_json(key)}:{_canonical_json(value[key])}"
                for key in sorted(value, key=_utf16_sort_key)
            )
            + "}"
        )
    raise TypeError(f"CANONICAL_JSON_UNSUPPORTED_TYPE:{type(value).__name__}")


def _stable_hash(value):
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class OsbProposalIntegrityError(ValueError):
    """The immutable proposal/outbox record contradicts its own identity."""


def _decompress_limited(value: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(value), mode="rb") as stream:
        result = stream.read(MAX_PROPOSAL_BYTES + 1)
    if len(result) > MAX_PROPOSAL_BYTES:
        raise OsbProposalIntegrityError("OSB_PROPOSAL_DECOMPRESSED_BYTE_LIMIT_EXCEEDED")
    return result


def _assert_bounded(value):
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_PROPOSAL_NODES:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_NODE_LIMIT_EXCEEDED")
        if depth > MAX_PROPOSAL_DEPTH:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_DEPTH_LIMIT_EXCEEDED")
        if isinstance(item, str):
            if len(item) > 16_384:
                raise OsbProposalIntegrityError("OSB_PROPOSAL_STRING_LIMIT_EXCEEDED")
            return
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, (int, float)):
            if isinstance(item, float) and not math.isfinite(item):
                raise OsbProposalIntegrityError("OSB_PROPOSAL_NON_FINITE_NUMBER")
            return
        if isinstance(item, list):
            if len(item) > 10_000:
                raise OsbProposalIntegrityError("OSB_PROPOSAL_ARRAY_LIMIT_EXCEEDED")
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            if len(item) > 1_000 or any(not isinstance(key, str) for key in item):
                raise OsbProposalIntegrityError("OSB_PROPOSAL_OBJECT_LIMIT_EXCEEDED")
            for key, child in item.items():
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        raise OsbProposalIntegrityError(
            f"OSB_PROPOSAL_UNSUPPORTED_VALUE:{type(item).__name__}"
        )

    visit(value, 0)


class OsbProposalDb:
    """One tenant-scoped ecrf_platform connection used by a V2 worker."""

    def __init__(self, dsn=None, tenant_id=None, log=None):
        self.dsn = dsn or load_env("ECRF_PG_DSN")
        self.tenant_id = tenant_id or load_env("ECRF_TENANT_ID")
        self.log = log
        self.conn = psycopg.connect(self.dsn, row_factory=dict_row)
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.tenant_id', %s, false)",
                (self.tenant_id,),
            )
            cursor.execute("""
                SELECT current_user AS role_name, role.rolsuper, role.rolbypassrls,
                       EXISTS (
                         SELECT 1 FROM pg_class table_row
                         WHERE table_row.relname IN (
                           'osb_study_proposals_v2', 'osb_proposal_outbox',
                           'osb_proposal_heads', 'fact_state'
                         )
                           AND table_row.relowner = role.oid
                       ) AS owns_protected_table
                  FROM pg_roles role
                 WHERE role.rolname = current_user
                """)
            posture = cursor.fetchone()
            if (
                not posture
                or posture["role_name"] != "osb_importer"
                or posture["rolsuper"]
                or posture["rolbypassrls"]
                or posture["owns_protected_table"]
            ):
                raise RuntimeError("OSB_IMPORTER_DATABASE_ROLE_POSTURE_INVALID")
            cursor.execute("""
                SELECT has_table_privilege(current_user, 'osb_study_proposals_v2', 'INSERT,UPDATE,DELETE')
                       OR has_table_privilege(current_user, 'osb_proposal_heads', 'INSERT,UPDATE,DELETE')
                       OR has_table_privilege(current_user, 'osb_proposal_outbox', 'INSERT,DELETE')
                       AS excess_privilege
                """)
            if cursor.fetchone()["excess_privilege"]:
                raise RuntimeError("OSB_IMPORTER_DATABASE_GRANTS_EXCESSIVE")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def claim_next(
        self,
        lease_owner: str,
        lease_seconds: int = 300,
        study_id: str | None = None,
    ):
        """Claim one queued/retryable/expired job with ``SKIP LOCKED``.

        The proposal and outbox are selected in the same transaction. A running
        row is reclaimable only after its lease expires; migration 039 enforces
        the owner/attempt transition again at the database boundary.
        """
        if not lease_owner:
            raise ValueError("lease_owner is required")
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT outbox_id, proposal_hash
                      FROM osb_proposal_outbox
                     WHERE available_at <= NOW()
                       AND workflow_stage = 'intake'
                       AND (CAST(%s AS text) IS NULL OR study_id = %s)
                       AND (
                         status IN ('queued', 'failed_retryable')
                         OR (status = 'running' AND lease_expires_at < clock_timestamp())
                       )
                     ORDER BY available_at, created_at, outbox_id
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                    """,
                    (study_id, study_id),
                )
                candidate = cursor.fetchone()
                if candidate is None:
                    self.conn.commit()
                    return None
                cursor.execute(
                    """
                    UPDATE osb_proposal_outbox
                       SET status = 'running',
                           attempt = attempt + 1,
                           lease_generation = lease_generation + 1,
                           workflow_stage = 'intake',
                           lease_owner = %s,
                           lease_expires_at = %s,
                           last_error = NULL
                     WHERE outbox_id = %s
                     RETURNING outbox_id, proposal_hash, study_id, status,
                               workflow_stage, attempt, lease_generation,
                               lease_owner, lease_expires_at
                    """,
                    (lease_owner, lease_expires_at, candidate["outbox_id"]),
                )
                claimed = cursor.fetchone()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        try:
            proposal = self.read_proposal(claimed["proposal_hash"])
        except OsbProposalIntegrityError as error:
            self.finish(
                claimed["outbox_id"],
                lease_owner,
                claimed["lease_generation"],
                "failed_terminal",
                str(error),
            )
            raise
        if proposal is None:
            self.finish(
                claimed["outbox_id"],
                lease_owner,
                claimed["lease_generation"],
                "failed_terminal",
                "OUTBOX_PROPOSAL_MISSING",
            )
            raise OsbProposalIntegrityError("OUTBOX_PROPOSAL_MISSING")
        return {**claimed, "proposal": proposal}

    def claim_review_required(
        self,
        lease_owner: str,
        lease_seconds: int = 300,
        study_id: str | None = None,
    ):
        """Lease one proposal awaiting OSB review without re-running intake."""
        if not lease_owner:
            raise ValueError("lease_owner is required")
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT outbox_id, proposal_hash
                      FROM osb_proposal_outbox
                     WHERE workflow_stage = 'review_polling'
                       AND available_at <= NOW()
                       AND (CAST(%s AS text) IS NULL OR study_id = %s)
                       AND (
                         status = 'review_required'
                         OR (status = 'running' AND lease_expires_at < clock_timestamp())
                       )
                     ORDER BY available_at, updated_at, outbox_id
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                    """,
                    (study_id, study_id),
                )
                candidate = cursor.fetchone()
                if candidate is None:
                    self.conn.commit()
                    return None
                cursor.execute(
                    """
                    UPDATE osb_proposal_outbox
                       SET status = 'running',
                           attempt = attempt + 1,
                           lease_generation = lease_generation + 1,
                           workflow_stage = 'review_polling',
                           lease_owner = %s,
                           lease_expires_at = %s,
                           last_error = NULL
                     WHERE outbox_id = %s
                     RETURNING outbox_id, proposal_hash, study_id, status,
                               workflow_stage, attempt, lease_generation,
                               lease_owner, lease_expires_at
                    """,
                    (lease_owner, lease_expires_at, candidate["outbox_id"]),
                )
                claimed = cursor.fetchone()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return claimed

    def claim_review_complete(self, lease_owner: str, lease_seconds: int = 300):
        """Lease one fully reviewed proposal for the separate native-execution stage.

        The proposal and every exact Fact revision/hash are re-verified at this
        boundary. Review completion never exempts a stale proposal from source
        integrity checks.
        """
        if not lease_owner:
            raise ValueError("lease_owner is required")
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    SELECT outbox_id, proposal_hash
                      FROM osb_proposal_outbox
                     WHERE workflow_stage = 'native_execution'
                       AND available_at <= NOW()
                       AND (
                         status = 'review_complete'
                         OR (status = 'running' AND lease_expires_at < clock_timestamp())
                       )
                     ORDER BY available_at, updated_at, outbox_id
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                    """)
                candidate = cursor.fetchone()
                if candidate is None:
                    self.conn.commit()
                    return None
                cursor.execute(
                    """
                    UPDATE osb_proposal_outbox
                       SET status = 'running',
                           attempt = attempt + 1,
                           lease_generation = lease_generation + 1,
                           workflow_stage = 'native_execution',
                           lease_owner = %s,
                           lease_expires_at = %s,
                           last_error = NULL
                     WHERE outbox_id = %s
                     RETURNING outbox_id, proposal_hash, study_id, status,
                               workflow_stage, attempt, lease_generation,
                               lease_owner, lease_expires_at
                    """,
                    (lease_owner, lease_expires_at, candidate["outbox_id"]),
                )
                claimed = cursor.fetchone()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        try:
            proposal = self.read_proposal(claimed["proposal_hash"])
        except OsbProposalIntegrityError as error:
            self.finish(
                claimed["outbox_id"],
                lease_owner,
                claimed["lease_generation"],
                "failed_terminal",
                str(error),
            )
            raise
        if proposal is None:
            self.finish(
                claimed["outbox_id"],
                lease_owner,
                claimed["lease_generation"],
                "failed_terminal",
                "OUTBOX_PROPOSAL_MISSING",
            )
            raise OsbProposalIntegrityError("OUTBOX_PROPOSAL_MISSING")
        return {**claimed, "proposal": proposal}

    def read_proposal(self, proposal_hash: str):
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT proposal_hash, proposal_id, source_build_hash, study_id,
                       format_version, canonicalization_version,
                       authority_mode, osb_openapi_hash,
                       osb_mapping_context_hash, source_fact_count,
                       proposed_object_count, mapped_source_fact_count,
                       source_disposition_count, reconciliation_balanced,
                       proposal_jsonb, proposal_gzip
                  FROM osb_study_proposals_v2
                 WHERE proposal_hash = %s
                """,
                (proposal_hash,),
            )
            row = cursor.fetchone()
        self.conn.commit()
        if row is None:
            return None
        if row["proposal_gzip"] is not None:
            proposal = json.loads(_decompress_limited(bytes(row["proposal_gzip"])))
        elif row["proposal_jsonb"] is not None:
            proposal = row["proposal_jsonb"]
        else:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_REPRESENTATION_MISSING")
        self._assert_proposal(row, proposal)
        self.verify_fact_refs(proposal)
        return proposal

    def verify_fact_refs(self, proposal):
        """Reject stale or substituted Fact revisions before OSB processing."""
        refs = proposal.get("sourceFactRefs") or []
        fact_ids = [ref.get("factId") for ref in refs]
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT fact_id, revision, fact_json
                  FROM fact_state
                 WHERE study_id = %s AND fact_id = ANY(%s::uuid[])
                """,
                (proposal.get("studyId"), fact_ids),
            )
            current = {str(row["fact_id"]): row for row in cursor.fetchall()}
        self.conn.commit()
        for ref in refs:
            row = current.get(ref.get("factId"))
            if row is None:
                raise OsbProposalIntegrityError(
                    f"OSB_PROPOSAL_FACT_MISSING:{ref.get('factId')}"
                )
            if row["revision"] != ref.get("revision"):
                raise OsbProposalIntegrityError(
                    f"OSB_PROPOSAL_FACT_REVISION_STALE:{ref.get('factId')}"
                )
            if _stable_hash(row["fact_json"]) != ref.get("factContentHash"):
                raise OsbProposalIntegrityError(
                    f"OSB_PROPOSAL_FACT_HASH_MISMATCH:{ref.get('factId')}"
                )

    @staticmethod
    def _assert_proposal(row, proposal):
        _assert_bounded(proposal)
        canonical = _canonical_json(proposal)
        if len(canonical.encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_BYTE_LIMIT_EXCEEDED")
        reconciliation = proposal.get("reconciliation") or {}
        source_refs = proposal.get("sourceFactRefs") or []
        content = {
            key: value for key, value in proposal.items() if key != "proposalHash"
        }
        if _stable_hash(content) != proposal.get("proposalHash"):
            raise OsbProposalIntegrityError("OSB_PROPOSAL_HASH_MISMATCH")
        if proposal.get("proposalHash") != row["proposal_hash"]:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_HASH_IDENTITY_MISMATCH")
        if proposal.get("proposalId") != row["proposal_id"]:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_IDENTITY_MISMATCH")
        if proposal.get("studyId") != row["study_id"]:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_STUDY_SCOPE_MISMATCH")
        if proposal.get("formatVersion") != PROPOSAL_FORMAT_VERSION:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_FORMAT_UNSUPPORTED")
        if proposal.get("formatVersion") != row["format_version"]:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_FORMAT_COLUMN_MISMATCH")
        if (
            proposal.get("canonicalizationVersion") != CANONICAL_JSON_VERSION
            or proposal.get("canonicalizationVersion")
            != row["canonicalization_version"]
        ):
            raise OsbProposalIntegrityError(
                "OSB_PROPOSAL_CANONICALIZATION_VERSION_MISMATCH"
            )
        if proposal.get("sourceBuildHash") != row["source_build_hash"]:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_SOURCE_BUILD_COLUMN_MISMATCH")
        if not proposal.get("osbOpenApiHash") or not proposal.get(
            "osbMappingContextHash"
        ):
            raise OsbProposalIntegrityError("OSB_PROPOSAL_CONTEXT_REQUIRED")
        if proposal.get("osbOpenApiHash") != row["osb_openapi_hash"]:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_OPENAPI_HASH_MISMATCH")
        if proposal.get("osbMappingContextHash") != row["osb_mapping_context_hash"]:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_CONTEXT_HASH_MISMATCH")
        if (
            reconciliation.get("balanced") is not True
            or not row["reconciliation_balanced"]
        ):
            raise OsbProposalIntegrityError("OSB_PROPOSAL_RECONCILIATION_UNBALANCED")

        if len(source_refs) > 5_000:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_FACT_LIMIT_EXCEEDED")
        source_ref_by_id = {ref.get("factId"): ref for ref in source_refs}
        if None in source_ref_by_id or len(source_ref_by_id) != len(source_refs):
            raise OsbProposalIntegrityError("OSB_PROPOSAL_SOURCE_FACT_IDENTITY_INVALID")
        source_documents = proposal.get("sourceDocuments") or []
        document_by_id = {
            item.get("documentVersionId"): item.get("contentHash")
            for item in source_documents
        }
        if (
            not source_documents
            or None in document_by_id
            or len(document_by_id) != len(source_documents)
            or proposal.get("sourceDocumentVersionIds")
            != [item.get("documentVersionId") for item in source_documents]
            or any(
                not isinstance(content_hash, str) or len(content_hash) != 64
                for content_hash in document_by_id.values()
            )
        ):
            raise OsbProposalIntegrityError(
                "OSB_PROPOSAL_SOURCE_DOCUMENT_IDENTITY_INVALID"
            )
        expected_source_build_hash = _stable_hash(
            {
                "tenantId": proposal.get("tenantId"),
                "studyId": proposal.get("studyId"),
                "projectId": proposal.get("projectId"),
                "authorityMode": proposal.get("authorityMode"),
                "sourceRunIds": proposal.get("sourceRunIds") or [],
                "sourceDocuments": source_documents,
                "sourceFactRefs": source_refs,
            }
        )
        if proposal.get("sourceBuildHash") != expected_source_build_hash:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_SOURCE_BUILD_HASH_MISMATCH")

        section_order = (
            "studySetup",
            "standards",
            "objectives",
            "endpoints",
            "criteria",
            "productsDosing",
            "armsCohortsBranches",
            "epochsElementsCells",
            "visitsTiming",
            "activitiesItems",
            "soa",
            "odm",
            "extensions",
            "retainedNarrative",
            "unresolved",
        )
        sections = proposal.get("sections") or {}
        if set(sections) - set(section_order):
            raise OsbProposalIntegrityError("OSB_PROPOSAL_UNKNOWN_SECTION")
        objects = [
            item for section in section_order for item in sections.get(section) or []
        ]
        if len(objects) > 15_000:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_OBJECT_LIMIT_EXCEEDED")
        object_ids = set()
        concept_ids = set()
        fact_targets = set()
        mapped_fact_ids = set()
        for section in section_order:
            for item in sections.get(section) or []:
                mapping = item.get("mapping") or {}
                fact_ids = mapping.get("factIds") or []
                if len(fact_ids) != 1 or fact_ids[0] not in source_ref_by_id:
                    raise OsbProposalIntegrityError("OSB_PROPOSAL_OBJECT_FACT_INVALID")
                fact_id = fact_ids[0]
                fact_ref = source_ref_by_id[fact_id]
                concept_id = _stable_hash(
                    {
                        "factId": fact_id,
                        "revision": fact_ref.get("revision"),
                        "factContentHash": fact_ref.get("factContentHash"),
                        "targetKey": item.get("targetKey"),
                    }
                )
                object_id = _stable_hash(
                    {
                        "sourceBuildHash": proposal.get("sourceBuildHash"),
                        "conceptId": concept_id,
                        "targetKey": item.get("targetKey"),
                        "section": section,
                        "proposedResourceType": mapping.get("proposedResourceType"),
                    }
                )
                fact_target = (fact_id, item.get("targetKey"))
                if (
                    item.get("section") != section
                    or item.get("conceptId") != concept_id
                    or item.get("proposalObjectId") != object_id
                    or object_id in object_ids
                    or concept_id in concept_ids
                    or fact_target in fact_targets
                ):
                    raise OsbProposalIntegrityError(
                        "OSB_PROPOSAL_OBJECT_OR_CONCEPT_ID_INVALID"
                    )
                candidates = mapping.get("candidates") or []
                if len(candidates) > 25:
                    raise OsbProposalIntegrityError(
                        "OSB_PROPOSAL_CANDIDATE_LIMIT_EXCEEDED"
                    )
                candidate_keys = set()
                for candidate in candidates:
                    identity = {
                        key: value
                        for key, value in candidate.items()
                        if key not in {"candidateKey", "label"}
                    }
                    candidate_key = candidate.get("candidateKey")
                    if (
                        candidate.get("contextHash")
                        != proposal.get("osbMappingContextHash")
                        or candidate_key != _stable_hash(identity)
                        or candidate_key in candidate_keys
                    ):
                        raise OsbProposalIntegrityError(
                            "OSB_PROPOSAL_CANDIDATE_IDENTITY_INVALID"
                        )
                    candidate_keys.add(candidate_key)
                selected = mapping.get("selectedCandidate")
                if selected and selected.get("candidateKey") not in candidate_keys:
                    raise OsbProposalIntegrityError(
                        "OSB_PROPOSAL_SELECTED_CANDIDATE_INVALID"
                    )
                evidence = mapping.get("evidence") or []
                if len(evidence) > 100:
                    raise OsbProposalIntegrityError(
                        "OSB_PROPOSAL_EVIDENCE_LIMIT_EXCEEDED"
                    )
                for evidence_row in evidence:
                    text = evidence_row.get("verbatimText")
                    expected_text_hash = (
                        None
                        if text is None
                        else hashlib.sha256(text.encode("utf-8")).hexdigest()
                    )
                    document_id = evidence_row.get("documentVersionId")
                    if (
                        evidence_row.get("textHash") != expected_text_hash
                        or document_id not in document_by_id
                        or (
                            evidence_row.get("documentContentHash") is not None
                            and evidence_row.get("documentContentHash")
                            != document_by_id[document_id]
                        )
                    ):
                        raise OsbProposalIntegrityError(
                            "OSB_PROPOSAL_EVIDENCE_IDENTITY_INVALID"
                        )
                object_ids.add(object_id)
                concept_ids.add(concept_id)
                fact_targets.add(fact_target)
                mapped_fact_ids.add(fact_id)

        counts = {
            "sourceFacts": len(source_refs),
            "proposedObjects": sum(
                len(items) for items in (proposal.get("sections") or {}).values()
            ),
            "mappedSourceFacts": len(
                {
                    fact_id
                    for items in (proposal.get("sections") or {}).values()
                    for item in items
                    for fact_id in (item.get("mapping") or {}).get("factIds", [])
                }
            ),
            "sourceDispositions": len(reconciliation.get("dispositions") or []),
        }
        expected = {
            "sourceFacts": row["source_fact_count"],
            "proposedObjects": row["proposed_object_count"],
            "mappedSourceFacts": row["mapped_source_fact_count"],
            "sourceDispositions": row["source_disposition_count"],
        }
        if counts != expected:
            raise OsbProposalIntegrityError(
                f"OSB_PROPOSAL_COUNT_MISMATCH:{counts!r}!={expected!r}"
            )
        if counts["sourceFacts"] != (
            counts["mappedSourceFacts"] + counts["sourceDispositions"]
        ):
            raise OsbProposalIntegrityError("OSB_PROPOSAL_FACT_BALANCE_MISMATCH")
        if any(not ref.get("factContentHash") for ref in source_refs):
            raise OsbProposalIntegrityError("OSB_PROPOSAL_FACT_HASH_MISSING")
        disposition_rows = reconciliation.get("dispositions") or []
        disposition_ids = [item.get("factId") for item in disposition_rows]
        if (
            None in disposition_ids
            or len(set(disposition_ids)) != len(disposition_ids)
            or mapped_fact_ids & set(disposition_ids)
            or mapped_fact_ids | set(disposition_ids) != set(source_ref_by_id)
        ):
            raise OsbProposalIntegrityError("OSB_PROPOSAL_FACT_BALANCE_MISMATCH")
        expected_proposal_id = _stable_hash(
            {
                "tenantId": proposal.get("tenantId"),
                "studyId": proposal.get("studyId"),
                "projectId": proposal.get("projectId"),
                "authorityMode": proposal.get("authorityMode"),
                "sourceBuildHash": proposal.get("sourceBuildHash"),
                "osbOpenApiHash": proposal.get("osbOpenApiHash"),
                "osbMappingContextHash": proposal.get("osbMappingContextHash"),
                "proposalObjects": objects,
                "sourceDispositions": disposition_rows,
            }
        )
        if proposal.get("proposalId") != expected_proposal_id:
            raise OsbProposalIntegrityError("OSB_PROPOSAL_ID_MISMATCH")

    def renew_lease(
        self,
        outbox_id,
        lease_owner: str,
        lease_generation: int,
        lease_seconds: int = 300,
    ) -> bool:
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE osb_proposal_outbox
                   SET lease_expires_at = %s
                 WHERE outbox_id = %s
                   AND status = 'running'
                   AND lease_owner = %s
                   AND lease_generation = %s
                   AND lease_expires_at >= clock_timestamp()
                """,
                (lease_expires_at, outbox_id, lease_owner, lease_generation),
            )
            renewed = cursor.rowcount == 1
        self.conn.commit()
        return renewed

    def append_item_result(
        self,
        outbox_id,
        lease_owner: str,
        lease_generation: int,
        item_result: dict,
    ):
        encoded = json.dumps(item_result, sort_keys=True, separators=(",", ":"))
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE osb_proposal_outbox
                   SET item_results = item_results || %s::jsonb
                 WHERE outbox_id = %s
                   AND status = 'running'
                   AND lease_owner = %s
                   AND lease_generation = %s
                   AND lease_expires_at >= clock_timestamp()
                """,
                (
                    json.dumps([json.loads(encoded)]),
                    outbox_id,
                    lease_owner,
                    lease_generation,
                ),
            )
            if cursor.rowcount != 1:
                self.conn.rollback()
                raise RuntimeError("OSB_PROPOSAL_LEASE_OWNERSHIP_LOST")
        self.conn.commit()

    def finish(
        self,
        outbox_id,
        lease_owner: str,
        lease_generation: int,
        status: str,
        error: str | None = None,
        available_at: datetime | None = None,
    ):
        if status not in DELIVERY_STATUSES - {"queued", "running"}:
            raise ValueError(f"invalid finish status {status}")
        retry_at = available_at or datetime.now(timezone.utc)
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE osb_proposal_outbox
                   SET status = %s,
                       workflow_stage = CASE
                         WHEN %s = 'review_required' THEN 'review_polling'
                         WHEN %s = 'review_complete' THEN 'native_execution'
                         ELSE workflow_stage
                       END,
                       available_at = CASE
                         WHEN %s IN ('failed_retryable', 'review_required') THEN %s
                         ELSE available_at
                       END,
                       last_error = %s
                 WHERE outbox_id = %s
                   AND status = 'running'
                   AND lease_owner = %s
                   AND lease_generation = %s
                   AND lease_expires_at >= clock_timestamp()
                """,
                (
                    status,
                    status,
                    status,
                    status,
                    retry_at,
                    error,
                    outbox_id,
                    lease_owner,
                    lease_generation,
                ),
            )
            if cursor.rowcount != 1:
                self.conn.rollback()
                raise RuntimeError("OSB_PROPOSAL_LEASE_OWNERSHIP_LOST")
        self.conn.commit()
