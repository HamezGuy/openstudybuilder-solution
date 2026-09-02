"""Durable OSB-owned review boundary for source-neutral Proposal V2 envelopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from neomodel import db

from clinical_mdr_api.models.integrations.proposal_review import (
    ProposalExecutionAuthorization,
    ProposalExecutionAuthorizationInput,
    ProposalObjectDecision,
    ProposalObjectDecisionInput,
    ProposalReviewIntake,
    ProposalReviewObject,
    ProposalReviewStatus,
)
from clinical_mdr_api.services.integrations.canonical_json import (
    canonical_hash as _canonical_hash,
)
from clinical_mdr_api.services.integrations.canonical_json import (
    canonical_json as _canonical_json,
)
from clinical_mdr_api.services.integrations.proposal_target_capabilities import (
    NATIVE_CREATE_REQUEST_RESOURCE_TYPES,
    NATIVE_DECLINABLE_RESOURCE_TYPES,
    NATIVE_DUAL_MODE_RESOURCE_TYPES,
    NATIVE_EXECUTOR_RESOURCE_TYPES,
    NATIVE_SELECTION_RESOURCE_TYPES,
    target_capability,
)
from common.utils import convert_to_datetime

SECTION_ORDER = (
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
MAX_PROPOSAL_BYTES = 128 * 1024 * 1024
REVIEWER_ROLE = "Study.Write"
READER_ROLE = "Study.Read"


@dataclass(frozen=True)
class ProposalReviewPrincipal:
    """Identity facts derived only from an already validated access token."""

    actor_id: str
    human_user_id: str
    token_id: str
    tenant_id: str
    scoped_study_ids: frozenset[str]
    organization_ids: frozenset[str]
    roles: frozenset[str]
    authentication_verified: bool
    purpose: str = ""
    capabilities: frozenset[str] = frozenset()
    enforce_delegated_scope: bool = False
    development_access: bool = False

    def _assert_access_identity(self) -> None:
        if self.development_access:
            return
        if not self.authentication_verified:
            raise ValueError("OSB_PROPOSAL_REVIEW_AUTHENTICATION_NOT_VERIFIED")
        if not self.actor_id:
            raise ValueError("OSB_PROPOSAL_REVIEW_ACTOR_REQUIRED")

    def _assert_authenticated_human(self) -> None:
        if not self.authentication_verified:
            raise ValueError("OSB_PROPOSAL_REVIEW_AUTHENTICATION_NOT_VERIFIED")
        if not self.actor_id or self.actor_id != self.human_user_id:
            raise ValueError("OSB_PROPOSAL_REVIEW_HUMAN_IDENTITY_REQUIRED")

    def assert_proposal_access(
        self,
        expected_tenant_id: str,
        expected_study_id: str,
        role: str,
    ) -> None:
        """Authorize human or service access to one proposal source scope.

        Authentication-disabled local development already bypasses the API's
        security/RBAC dependencies.  Preserve that preview behavior for
        intake/read, but never treat it as a verified signature identity.
        """
        self._assert_access_identity()
        if self.development_access:
            return
        if role not in self.roles:
            raise ValueError("OSB_PROPOSAL_REVIEW_ROLE_REQUIRED")
        if self.enforce_delegated_scope:
            if self.purpose not in {
                "interactive-domain-access",
                "workflow-orchestration",
            }:
                raise ValueError("OSB_PROPOSAL_REVIEW_PURPOSE_REQUIRED")
            capability = "study:write" if role == REVIEWER_ROLE else "study:read"
            if capability not in self.capabilities:
                raise ValueError("OSB_PROPOSAL_REVIEW_CAPABILITY_REQUIRED")
        if not self.tenant_id:
            raise ValueError("OSB_PROPOSAL_REVIEW_TENANT_ID_REQUIRED")
        if not expected_tenant_id or self.tenant_id != expected_tenant_id:
            raise ValueError("OSB_PROPOSAL_REVIEW_TENANT_SCOPE_MISMATCH")
        self.assert_study_access(expected_study_id)

    def assert_study_access(self, expected_study_id: str) -> None:
        self._assert_access_identity()
        if self.development_access:
            return
        if not expected_study_id or expected_study_id not in self.scoped_study_ids:
            raise ValueError("OSB_PROPOSAL_REVIEW_STUDY_SCOPE_MISMATCH")

    def assert_can_sign(self, signature_id: str) -> None:
        self._assert_authenticated_human()
        if REVIEWER_ROLE not in self.roles:
            raise ValueError("OSB_PROPOSAL_REVIEW_ROLE_REQUIRED")
        if not self.tenant_id:
            raise ValueError("OSB_PROPOSAL_REVIEW_TENANT_ID_REQUIRED")
        if not self.token_id or signature_id != self.token_id:
            raise ValueError("OSB_PROPOSAL_REVIEW_SIGNATURE_TOKEN_MISMATCH")


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key not in {"candidateKey", "label"}
    }


def _context_candidate(candidate: dict[str, Any], context_hash: str) -> dict[str, Any]:
    """Convert stored Mapping Context V2 metadata to the Proposal V2.1 key shape."""
    identity = {
        "resourceFamily": candidate.get("resource_family"),
        "resourceType": candidate.get("resource_type"),
        "uid": candidate.get("uid"),
        "version": candidate.get("version"),
        "packageUid": candidate.get("package_uid"),
        "catalogueName": candidate.get("catalogue_name"),
        "packageVersion": candidate.get("package_version"),
        "packageEffectiveDate": candidate.get("package_effective_date"),
        "libraryName": candidate.get("library_name"),
        "parentResourceType": candidate.get("parent_resource_type"),
        "parentUid": candidate.get("parent_uid"),
        "parentVersion": candidate.get("parent_version"),
        "parentSubmissionValue": candidate.get("parent_submission_value"),
        "modelUid": candidate.get("model_uid"),
        "modelVersion": candidate.get("model_version"),
        "implementationGuideUid": candidate.get("implementation_guide_uid"),
        "implementationGuideVersion": candidate.get("implementation_guide_version"),
        "mappingTargetUid": candidate.get("mapping_target_uid"),
        "mappingTargetVersion": candidate.get("mapping_target_version"),
        "status": candidate.get("status"),
        "validFrom": candidate.get("valid_from"),
        "validTo": candidate.get("valid_to"),
        "submissionValue": candidate.get("submission_value"),
        "ucumExpression": candidate.get("ucum_expression"),
        "extensible": candidate.get("extensible"),
        "dimension": candidate.get("dimension"),
        "conversionFactorToMaster": candidate.get("conversion_factor_to_master"),
        "stableOid": candidate.get("stable_oid"),
        "criteriaTypeUid": candidate.get("criteria_type_uid"),
        "parameterCount": candidate.get("parameter_count"),
        "contextHash": context_hash,
        "code": candidate.get("code"),
    }
    return {
        "candidateKey": _canonical_hash(identity),
        **identity,
        "label": candidate.get("label"),
    }


class Neo4jProposalReviewRepository:
    """Parameterized Cypher persistence; proposals/contexts/decisions are append-only."""

    def save_context(self, context_hash: str, content: dict[str, Any]) -> None:
        content_json = _canonical_json(content)
        if _canonical_hash(content) != context_hash:
            raise ValueError("OSB_MAPPING_CONTEXT_HASH_MISMATCH")
        existing = self.get_context(context_hash)
        if existing is not None:
            if _canonical_json(existing) != content_json:
                raise ValueError("OSB_MAPPING_CONTEXT_HASH_CONTENT_CONFLICT")
            return
        query = """
            MERGE (context:OsbMappingContextSnapshot {context_hash: $context_hash})
            ON CREATE SET context.content_json = $content_json,
                          context.osb_openapi_hash = $osb_openapi_hash,
                          context.governed = $governed,
                          context.created_at = datetime()
            RETURN context.content_json
        """
        result, _ = db.cypher_query(
            query,
            {
                "context_hash": context_hash,
                "content_json": content_json,
                "osb_openapi_hash": content.get("osbOpenApiHash"),
                "governed": bool(content.get("governed")),
            },
        )
        if not result or result[0][0] != content_json:
            raise ValueError("OSB_MAPPING_CONTEXT_HASH_CONTENT_CONFLICT")

    @staticmethod
    def get_context(context_hash: str) -> dict[str, Any] | None:
        result, _ = db.cypher_query(
            """
            MATCH (context:OsbMappingContextSnapshot {context_hash: $context_hash})
            RETURN context.content_json
            LIMIT 1
            """,
            {"context_hash": context_hash},
        )
        return json.loads(result[0][0]) if result else None

    def save_proposal(
        self,
        proposal: dict[str, Any],
        objects: list[dict[str, Any]],
        worker_id: str,
    ) -> None:
        proposal_hash = proposal["proposalHash"]
        proposal_json = _canonical_json(proposal)
        existing = self.get_proposal(proposal_hash)
        if existing is not None:
            if _canonical_json(existing["proposal"]) != proposal_json:
                raise ValueError("OSB_PROPOSAL_HASH_CONTENT_CONFLICT")
            return
        result, _ = db.cypher_query(
            """
            MATCH (context:OsbMappingContextSnapshot {context_hash: $context_hash})
            MERGE (proposal:OsbProposalReview {proposal_hash: $proposal_hash})
            ON CREATE SET proposal.proposal_id = $proposal_id,
                          proposal.source_study_id = $source_study_id,
                          proposal.osb_openapi_hash = $osb_openapi_hash,
                          proposal.context_hash = $context_hash,
                          proposal.proposal_json = $proposal_json,
                          proposal.accepted_by_worker = $worker_id,
                          proposal.accepted_at = datetime()
            MERGE (proposal)-[:USES_MAPPING_CONTEXT]->(context)
            WITH proposal
            CALL {
              WITH proposal
              UNWIND $objects AS item
              MERGE (object:OsbProposalReviewObject {object_key: item.object_key})
              ON CREATE SET object.proposal_object_id = item.proposal_object_id,
                            object.section = item.section,
                            object.object_json = item.object_json,
                            object.created_at = datetime()
              MERGE (proposal)-[:HAS_REVIEW_OBJECT]->(object)
              RETURN count(object) AS saved_objects
            }
            RETURN proposal.proposal_json, saved_objects
            """,
            {
                "proposal_hash": proposal_hash,
                "proposal_id": proposal["proposalId"],
                "source_study_id": proposal["studyId"],
                "osb_openapi_hash": proposal["osbOpenApiHash"],
                "context_hash": proposal["osbMappingContextHash"],
                "proposal_json": proposal_json,
                "worker_id": worker_id,
                "objects": [
                    {
                        "object_key": f"{proposal_hash}:{item['proposalObjectId']}",
                        "proposal_object_id": item["proposalObjectId"],
                        "section": item["section"],
                        "object_json": _canonical_json(item),
                    }
                    for item in objects
                ],
            },
        )
        if not result or result[0][0] != proposal_json:
            raise ValueError("OSB_PROPOSAL_HASH_CONTENT_CONFLICT")
        if result[0][1] != len(objects):
            raise ValueError("OSB_PROPOSAL_REVIEW_OBJECT_PERSISTENCE_MISMATCH")

    @staticmethod
    def get_proposal(proposal_hash: str) -> dict[str, Any] | None:
        result, _ = db.cypher_query(
            """
            MATCH (proposal:OsbProposalReview {proposal_hash: $proposal_hash})
            OPTIONAL MATCH (proposal)-[:HAS_REVIEW_OBJECT]->(object:OsbProposalReviewObject)
            RETURN proposal.proposal_json, proposal.accepted_at,
                   proposal.accepted_by_worker,
                   collect(object.object_json)
            LIMIT 1
            """,
            {"proposal_hash": proposal_hash},
        )
        if not result:
            return None
        proposal_json, accepted_at, worker_id, object_json = result[0]
        return {
            "proposal": json.loads(proposal_json),
            "accepted_at": accepted_at,
            "worker_id": worker_id,
            "objects": [json.loads(value) for value in object_json if value],
        }

    @staticmethod
    def list_decisions(proposal_hash: str) -> list[dict[str, Any]]:
        result, _ = db.cypher_query(
            """
            MATCH (:OsbProposalReview {proposal_hash: $proposal_hash})
                  -[:HAS_REVIEW_OBJECT]->(object:OsbProposalReviewObject)
                  -[:LATEST_DECISION]->(decision:OsbProposalReviewDecision)
            RETURN object.proposal_object_id, decision.decision_id,
                   decision.action, decision.candidate_key, decision.note,
                   decision.signature_id, decision.signature_verified,
                   decision.decision_content_hash,
                   decision.actor_id, decision.decided_at
            ORDER BY decision.decided_at, decision.decision_id
            """,
            {"proposal_hash": proposal_hash},
        )
        return [
            {
                "proposal_object_id": row[0],
                "decision_id": row[1],
                "action": row[2],
                "candidate_key": row[3],
                "note": row[4],
                "signature_id": row[5],
                "signature_verified": bool(row[6]),
                "decision_content_hash": row[7],
                "actor_id": row[8],
                "decided_at": convert_to_datetime(row[9]),
            }
            for row in result
        ]

    @staticmethod
    def append_decision(
        proposal_hash: str,
        proposal_object_id: str,
        decision: dict[str, Any],
    ) -> None:
        result, _ = db.cypher_query(
            """
            MATCH (proposal:OsbProposalReview {proposal_hash: $proposal_hash})
                  -[:HAS_REVIEW_OBJECT]->(object:OsbProposalReviewObject {
                    proposal_object_id: $proposal_object_id
                  })
            SET object.decision_sequence = coalesce(object.decision_sequence, 0) + 1
            WITH object, object.decision_sequence AS decision_sequence
            OPTIONAL MATCH (object)-[old_latest:LATEST_DECISION]->(previous)
            DELETE old_latest
            CREATE (decision:OsbProposalReviewDecision {
                decision_id: $decision_id,
                decision_sequence: decision_sequence,
                action: $action,
                candidate_key: $candidate_key,
                note: $note,
                signature_id: $signature_id,
                signature_verified: $signature_verified,
                decision_content_hash: $decision_content_hash,
                actor_id: $actor_id,
                decided_at: datetime($decided_at)
            })
            CREATE (object)-[:HAS_DECISION]->(decision)
            CREATE (object)-[:LATEST_DECISION]->(decision)
            FOREACH (_ IN CASE WHEN previous IS NULL THEN [] ELSE [1] END |
              CREATE (decision)-[:SUPERSEDES]->(previous)
            )
            RETURN decision.decision_id
            """,
            {
                "proposal_hash": proposal_hash,
                "proposal_object_id": proposal_object_id,
                **decision,
            },
        )
        if not result:
            raise ValueError("OSB_PROPOSAL_REVIEW_OBJECT_NOT_FOUND")

    @staticmethod
    def get_draft_target(study_uid: str) -> dict[str, Any] | None:
        """Return the live draft and its stable, original owner.

        `LATEST_DRAFT.author_id` is the most recent editor and changes on a
        metadata PATCH.  Ownership must instead come from the first
        `HAS_VERSION` relationship or an importer could take ownership merely
        by writing to the study.  Drafts intentionally have no numeric version;
        node id and start date are the optimistic-concurrency tokens.
        """
        result, _ = db.cypher_query(
            """
            MATCH (study:StudyRoot {uid: $study_uid})
                  -[draft:LATEST_DRAFT]->(value:StudyValue)
            WHERE draft.end_date IS NULL
              AND draft.status = 'DRAFT'
              AND draft.start_date IS NOT NULL
            MATCH (study)-[created:HAS_VERSION]->(:StudyValue)
            WHERE created.author_id IS NOT NULL
              AND created.start_date IS NOT NULL
            WITH study, draft, value, created
            ORDER BY created.start_date ASC, elementId(created) ASC
            WITH study, draft, value, head(collect(created)) AS initial
            RETURN study.uid, 'DRAFT', draft.status, initial.author_id,
                   draft.start_date, elementId(value)
            LIMIT 2
            """,
            {"study_uid": study_uid},
        )
        if len(result) > 1:
            raise ValueError("OSB_PROPOSAL_TARGET_DRAFT_AMBIGUOUS")
        if not result:
            return None
        row = result[0]
        return {
            "study_uid": row[0],
            "version": row[1],
            "status": row[2],
            "owner_id": row[3],
            "version_start_date": convert_to_datetime(row[4]),
            "study_value_node_id": row[5],
            "ownership_basis": "initial_version_author",
        }

    @staticmethod
    def append_execution_authorization(
        proposal_hash: str,
        authorization: dict[str, Any],
    ) -> None:
        """Atomically bind an authorization to the still-current owned draft."""
        result, _ = db.cypher_query(
            """
            MATCH (proposal:OsbProposalReview {proposal_hash: $proposal_hash})
            MATCH (reviewer:User {user_id: $actor_id})
            MATCH (study:StudyRoot {uid: $target_study_uid})
                  -[draft:LATEST_DRAFT]->(value:StudyValue)
            WHERE draft.end_date IS NULL
              AND draft.status = 'DRAFT'
              AND draft.start_date = datetime($target_version_start_date)
              AND elementId(value) = $target_study_value_node_id
            MATCH (study)-[created:HAS_VERSION]->(:StudyValue)
            WHERE created.author_id IS NOT NULL
              AND created.start_date IS NOT NULL
            WITH proposal, reviewer, study, draft, value, created
            ORDER BY created.start_date ASC, elementId(created) ASC
            WITH proposal, reviewer, study, draft, value,
                 head(collect(created)) AS initial
            WHERE $target_study_version = 'DRAFT'
              AND initial.author_id = $target_study_owner_id
              AND initial.author_id = $actor_id
              AND $target_ownership_basis = 'initial_version_author'
            OPTIONAL MATCH
              (proposal)-[old_latest:LATEST_EXECUTION_AUTHORIZATION]->(previous)
            DELETE old_latest
            CREATE (authorization:OsbProposalExecutionAuthorization {
                authorization_id: $authorization_id,
                proposal_hash: $proposal_hash,
                target_study_uid: $target_study_uid,
                target_study_version: $target_study_version,
                target_study_status: 'DRAFT',
                target_study_value_node_id: $target_study_value_node_id,
                target_study_owner_id: $target_study_owner_id,
                target_ownership_basis: $target_ownership_basis,
                target_version_start_date: datetime($target_version_start_date),
                decision_set_hash: $decision_set_hash,
                signature_id: $signature_id,
                signature_verified: $signature_verified,
                actor_id: $actor_id,
                authorized_at: datetime($authorized_at),
                authorization_content_hash: $authorization_content_hash
            })
            CREATE (proposal)-[:HAS_EXECUTION_AUTHORIZATION]->(authorization)
            CREATE (proposal)-[:LATEST_EXECUTION_AUTHORIZATION]->(authorization)
            CREATE (authorization)-[:TARGETS_STUDY]->(study)
            CREATE (authorization)-[:AUTHORIZED_BY]->(reviewer)
            FOREACH (_ IN CASE WHEN previous IS NULL THEN [] ELSE [1] END |
              CREATE (authorization)-[:SUPERSEDES]->(previous)
            )
            RETURN authorization.authorization_id
            """,
            {
                "proposal_hash": proposal_hash,
                **authorization,
            },
        )
        if not result:
            raise ValueError("OSB_PROPOSAL_TARGET_DRAFT_OWNERSHIP_STALE")

    @staticmethod
    def get_execution_authorization(
        proposal_hash: str,
    ) -> dict[str, Any] | None:
        result, _ = db.cypher_query(
            """
            MATCH (:OsbProposalReview {proposal_hash: $proposal_hash})
                  -[:LATEST_EXECUTION_AUTHORIZATION]->
                  (authorization:OsbProposalExecutionAuthorization)
            RETURN authorization.authorization_id,
                   authorization.proposal_hash,
                   authorization.target_study_uid,
                   authorization.target_study_version,
                   authorization.target_study_status,
                   authorization.target_study_value_node_id,
                   authorization.target_study_owner_id,
                   authorization.target_ownership_basis,
                   authorization.target_version_start_date,
                   authorization.decision_set_hash,
                   authorization.signature_id,
                   authorization.signature_verified,
                   authorization.actor_id,
                   authorization.authorized_at,
                   authorization.authorization_content_hash
            LIMIT 1
            """,
            {"proposal_hash": proposal_hash},
        )
        if not result:
            return None
        row = result[0]
        return {
            "authorization_id": row[0],
            "proposal_hash": row[1],
            "target_study_uid": row[2],
            "target_study_version": row[3],
            "target_study_status": row[4],
            "target_study_value_node_id": row[5],
            "target_study_owner_id": row[6],
            "target_ownership_basis": row[7],
            "target_version_start_date": convert_to_datetime(row[8]),
            "decision_set_hash": row[9],
            "signature_id": row[10],
            "signature_verified": bool(row[11]),
            "actor_id": row[12],
            "authorized_at": convert_to_datetime(row[13]),
            "authorization_content_hash": row[14],
        }


def _declined_optional(item) -> bool:
    """A signed not_applicable decision on a declinable family (GAP-8)."""
    return (
        item.proposed_resource_type in NATIVE_DECLINABLE_RESOURCE_TYPES
        and item.latest_decision is not None
        and item.latest_decision.action == "not_applicable"
    )


class ProposalReviewService:
    def __init__(self, repository: Neo4jProposalReviewRepository | None = None):
        self.repository = repository or Neo4jProposalReviewRepository()

    def intake(
        self,
        intake: ProposalReviewIntake,
        live_openapi_hash: str,
        principal: ProposalReviewPrincipal,
    ) -> ProposalReviewStatus:
        # exclude_unset keeps the dump wire-exact: `proposalHash` covers the
        # proposal AS RECEIVED, where an omitted optional (e.g. sourceAuthority)
        # and an explicit null hash differently. Plain model_dump would invent
        # nulls for every optional the producer omitted and refuse valid intakes.
        proposal = intake.proposal.model_dump(by_alias=True, exclude_unset=True)
        principal.assert_proposal_access(
            proposal.get("tenantId") or "",
            proposal.get("studyId") or "",
            REVIEWER_ROLE,
        )
        if len(_canonical_json(proposal).encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ValueError("OSB_PROPOSAL_BYTE_LIMIT_EXCEEDED")
        objects = self._validate_proposal(proposal, live_openapi_hash)
        self.repository.save_proposal(proposal, objects, intake.worker_id)
        return self.get_status(proposal["proposalHash"])

    def _validate_proposal(
        self,
        proposal: dict[str, Any],
        live_openapi_hash: str,
    ) -> list[dict[str, Any]]:
        if (
            proposal.get("formatVersion") != "osb-proposal/2.1"
            or proposal.get("canonicalizationVersion") != "canonical-json/1.0"
        ):
            raise ValueError("OSB_PROPOSAL_FORMAT_UNSUPPORTED")
        if proposal.get("authorityMode") not in {"shadow", "enforced"}:
            raise ValueError("OSB_PROPOSAL_AUTHORITY_MODE_INVALID")
        proposal_hash = proposal.get("proposalHash")
        content = {
            key: value for key, value in proposal.items() if key != "proposalHash"
        }
        if not proposal_hash or _canonical_hash(content) != proposal_hash:
            raise ValueError("OSB_PROPOSAL_HASH_MISMATCH")
        if proposal.get("osbOpenApiHash") != live_openapi_hash:
            raise ValueError("OSB_PROPOSAL_OPENAPI_HASH_STALE")
        context_hash = proposal.get("osbMappingContextHash")
        context = self.repository.get_context(context_hash)
        if context is None:
            raise ValueError("OSB_PROPOSAL_MAPPING_CONTEXT_UNKNOWN")
        if not context.get("governed"):
            raise ValueError("OSB_PROPOSAL_MAPPING_CONTEXT_UNGOVERNED")
        if context.get("osbOpenApiHash") != live_openapi_hash:
            raise ValueError("OSB_PROPOSAL_MAPPING_CONTEXT_OPENAPI_STALE")

        v2_group_candidates = [
            candidate
            for group in (context.get("candidateGroups") or [])
            for candidate in (group.get("candidates") or [])
        ]
        v1_candidates = [
            candidate
            for candidates in (context.get("candidates") or {}).values()
            for candidate in candidates
        ]
        candidate_catalogue = {
            candidate["candidateKey"]: candidate
            for raw in [*v2_group_candidates, *v1_candidates]
            for candidate in [
                raw if "candidateKey" in raw else _context_candidate(raw, context_hash)
            ]
            if raw.get("identity_schema_version") == "osb-candidate-key/2.0"
            or raw.get("identitySchemaVersion") == "osb-candidate-key/2.0"
        }
        source_refs = proposal.get("sourceFactRefs") or []
        source_ref_by_id = {item.get("factId"): item for item in source_refs}
        if None in source_ref_by_id or len(source_ref_by_id) != len(source_refs):
            raise ValueError("OSB_PROPOSAL_SOURCE_FACT_IDENTITY_INVALID")
        source_documents = proposal.get("sourceDocuments") or []
        source_authority = proposal.get("sourceAuthority")
        if source_authority and (
            source_authority.get("tenantId") != proposal.get("tenantId")
            or source_authority.get("studyId") != proposal.get("studyId")
        ):
            raise ValueError("OSB_PROPOSAL_SOURCE_AUTHORITY_INVALID")
        source_document_ids = {
            item.get("documentVersionId"): item.get("contentHash")
            for item in source_documents
        }
        if (
            None in source_document_ids
            or len(source_document_ids) != len(source_documents)
            or proposal.get("sourceDocumentVersionIds")
            != [item.get("documentVersionId") for item in source_documents]
        ):
            raise ValueError("OSB_PROPOSAL_SOURCE_DOCUMENT_IDENTITY_INVALID")
        source_build_content = {
                "tenantId": proposal.get("tenantId"),
                "studyId": proposal.get("studyId"),
                "projectId": proposal.get("projectId"),
                "authorityMode": proposal.get("authorityMode"),
                "sourceRunIds": proposal.get("sourceRunIds") or [],
                "sourceDocuments": source_documents,
                "sourceFactRefs": source_refs,
            }
        if source_authority:
            source_build_content["sourceAuthority"] = source_authority
        expected_source_build_hash = _canonical_hash(source_build_content)
        if proposal.get("sourceBuildHash") != expected_source_build_hash:
            raise ValueError("OSB_PROPOSAL_SOURCE_BUILD_HASH_MISMATCH")
        sections = proposal.get("sections") or {}
        if set(sections) - set(SECTION_ORDER):
            raise ValueError("OSB_PROPOSAL_UNKNOWN_SECTION")
        objects: list[dict[str, Any]] = []
        object_ids: set[str] = set()
        mapped_fact_ids: set[str] = set()
        for section in SECTION_ORDER:
            for raw in sections.get(section) or []:
                item = {**raw, "section": section}
                object_id = item.get("proposalObjectId")
                mapping = item.get("mapping") or {}
                resource_type = mapping.get("proposedResourceType")
                if target_capability(resource_type) is None:
                    raise ValueError(
                        f"OSB_PROPOSAL_RESOURCE_TYPE_UNSUPPORTED:{resource_type}"
                    )
                fact_ids = mapping.get("factIds") or []
                if len(fact_ids) != 1 or fact_ids[0] not in source_ref_by_id:
                    raise ValueError("OSB_PROPOSAL_OBJECT_ID_OR_FACTS_INVALID")
                fact_id = fact_ids[0]
                fact_ref = source_ref_by_id[fact_id]
                concept_id = _canonical_hash(
                    {
                        "factId": fact_id,
                        "revision": fact_ref.get("revision"),
                        "factContentHash": fact_ref.get("factContentHash"),
                        "targetKey": item.get("targetKey"),
                    }
                )
                expected_object_id = _canonical_hash(
                    {
                        "sourceBuildHash": proposal.get("sourceBuildHash"),
                        "conceptId": concept_id,
                        "targetKey": item.get("targetKey"),
                        "section": section,
                        "proposedResourceType": mapping.get("proposedResourceType"),
                    }
                )
                if (
                    not object_id
                    or object_id in object_ids
                    or item.get("conceptId") != concept_id
                    or object_id != expected_object_id
                    or item.get("section") != section
                ):
                    raise ValueError("OSB_PROPOSAL_OBJECT_OR_CONCEPT_ID_INVALID")
                if mapping.get("reviewerDecision") is not None:
                    raise ValueError("OSB_PROPOSAL_IL_CANNOT_SUPPLY_REVIEW_DECISION")
                candidates = mapping.get("candidates") or []
                for candidate in candidates:
                    if candidate.get("contextHash") != context_hash:
                        raise ValueError("OSB_PROPOSAL_CANDIDATE_CONTEXT_STALE")
                    candidate_key = candidate.get("candidateKey")
                    if (
                        candidate_key != _canonical_hash(_candidate_identity(candidate))
                        or candidate_catalogue.get(candidate_key) != candidate
                    ):
                        raise ValueError("OSB_PROPOSAL_CANDIDATE_NOT_IN_CONTEXT")
                selected = mapping.get("selectedCandidate")
                if selected is not None and selected.get("candidateKey") not in {
                    candidate.get("candidateKey") for candidate in candidates
                }:
                    raise ValueError("OSB_PROPOSAL_SELECTED_CANDIDATE_INVALID")
                for evidence in mapping.get("evidence") or []:
                    expected_text_hash = (
                        None
                        if evidence.get("verbatimText") is None
                        else _text_hash(evidence["verbatimText"])
                    )
                    if (
                        evidence.get("textHash") != expected_text_hash
                        or evidence.get("documentVersionId") not in source_document_ids
                        or (
                            evidence.get("documentContentHash") is not None
                            and evidence.get("documentContentHash")
                            != source_document_ids[evidence["documentVersionId"]]
                        )
                    ):
                        raise ValueError("OSB_PROPOSAL_EVIDENCE_IDENTITY_INVALID")
                object_ids.add(object_id)
                mapped_fact_ids.update(fact_ids)
                objects.append(item)

        reconciliation = proposal.get("reconciliation") or {}
        source_fact_ids = set(source_ref_by_id)
        disposition_fact_ids = {
            item.get("factId") for item in reconciliation.get("dispositions") or []
        }
        if (
            None in source_fact_ids
            or None in disposition_fact_ids
            or mapped_fact_ids & disposition_fact_ids
            or mapped_fact_ids | disposition_fact_ids != source_fact_ids
            or reconciliation.get("balanced") is not True
            or reconciliation.get("sourceFacts") != len(source_fact_ids)
            or reconciliation.get("mappedSourceFacts") != len(mapped_fact_ids)
            or reconciliation.get("proposedObjects") != len(objects)
        ):
            raise ValueError("OSB_PROPOSAL_RECONCILIATION_UNBALANCED")
        expected_counts = {
            "sourceFacts": len(source_refs),
            "eligibleApprovedFacts": sum(
                bool(item.get("eligibleApproved")) for item in source_refs
            ),
            "proposedObjects": len(objects),
            "nativeStudyMutationTargets": sum(
                target_capability(
                    (item.get("mapping") or {}).get("proposedResourceType")
                )
                == "native_study_mutation"
                for item in objects
            ),
            "governedLibraryReferenceTargets": sum(
                target_capability(
                    (item.get("mapping") or {}).get("proposedResourceType")
                )
                == "governed_library_reference"
                for item in objects
            ),
            "governedExtensionTargets": sum(
                target_capability(
                    (item.get("mapping") or {}).get("proposedResourceType")
                )
                == "governed_extension"
                for item in objects
            ),
            "retainedNarrativeTargets": sum(
                target_capability(
                    (item.get("mapping") or {}).get("proposedResourceType")
                )
                == "retained_narrative"
                for item in objects
            ),
            "unresolvedTargets": sum(
                target_capability(
                    (item.get("mapping") or {}).get("proposedResourceType")
                )
                == "unresolved"
                for item in objects
            ),
            "mappedSourceFacts": len(mapped_fact_ids),
            "exact": sum(
                (item.get("mapping") or {}).get("disposition") == "exact"
                for item in objects
            ),
            "review": sum(
                (item.get("mapping") or {}).get("disposition") == "review"
                for item in objects
            ),
            "createRequests": sum(
                (item.get("mapping") or {}).get("disposition") == "create_request"
                for item in objects
            ),
            "unresolved": sum(
                (item.get("mapping") or {}).get("disposition") == "unresolved"
                for item in objects
            ),
            "notApplicable": sum(
                item.get("kind") == "not_applicable"
                for item in reconciliation.get("dispositions") or []
            ),
            "excluded": sum(
                item.get("kind") == "signed_exclusion"
                for item in reconciliation.get("dispositions") or []
            ),
            "quarantined": sum(
                item.get("kind") == "quarantined"
                for item in reconciliation.get("dispositions") or []
            ),
            "rejected": sum(
                item.get("kind") == "rejected"
                for item in reconciliation.get("dispositions") or []
            ),
            "notBuildFeeding": sum(
                item.get("kind") == "not_build_feeding"
                for item in reconciliation.get("dispositions") or []
            ),
            "archived": sum(
                item.get("kind") == "archived"
                for item in reconciliation.get("dispositions") or []
            ),
            "unreviewed": sum(
                item.get("kind") == "unreviewed"
                for item in reconciliation.get("dispositions") or []
            ),
            "duplicateLinks": sum(
                item.get("kind") == "duplicate"
                for item in reconciliation.get("dispositions") or []
            ),
            "supersessionLinks": sum(
                item.get("kind") == "superseded"
                for item in reconciliation.get("dispositions") or []
            ),
        }
        expected_counts["signedExclusions"] = (
            expected_counts["notApplicable"] + expected_counts["excluded"]
        )
        kinds_by_fact: dict[str, list[str]] = {}
        for item in objects:
            resource_type = (item.get("mapping") or {}).get("proposedResourceType")
            kind = target_capability(resource_type)
            for fact_id in (item.get("mapping") or {}).get("factIds") or []:
                kinds_by_fact.setdefault(fact_id, []).append(kind)
        expected_counts["nativeTargetSourceFacts"] = sum(
            "native_study_mutation" in kinds for kinds in kinds_by_fact.values()
        )
        expected_counts["fullyNativeTargetSourceFacts"] = sum(
            "native_study_mutation" in kinds
            and all(
                kind in {"native_study_mutation", "governed_library_reference"}
                for kind in kinds
            )
            for kinds in kinds_by_fact.values()
        )
        if any(
            reconciliation.get(key) != value for key, value in expected_counts.items()
        ):
            raise ValueError("OSB_PROPOSAL_RECONCILIATION_COUNT_MISMATCH")
        expected_proposal_id = _canonical_hash(
            {
                "tenantId": proposal.get("tenantId"),
                "studyId": proposal.get("studyId"),
                "projectId": proposal.get("projectId"),
                "authorityMode": proposal.get("authorityMode"),
                "sourceBuildHash": proposal.get("sourceBuildHash"),
                "osbOpenApiHash": proposal.get("osbOpenApiHash"),
                "osbMappingContextHash": proposal.get("osbMappingContextHash"),
                "proposalObjects": objects,
                "sourceDispositions": reconciliation.get("dispositions") or [],
            }
        )
        if proposal.get("proposalId") != expected_proposal_id:
            raise ValueError("OSB_PROPOSAL_ID_MISMATCH")
        return objects

    def decide(
        self,
        proposal_hash: str,
        proposal_object_id: str,
        decision: ProposalObjectDecisionInput,
        principal: ProposalReviewPrincipal,
    ) -> ProposalReviewStatus:
        principal.assert_can_sign(decision.signature_id)
        actor_id = principal.actor_id
        if not actor_id:
            raise ValueError("OSB_PROPOSAL_REVIEW_ACTOR_REQUIRED")
        stored = self.repository.get_proposal(proposal_hash)
        if stored is None:
            raise ValueError("OSB_PROPOSAL_REVIEW_NOT_FOUND")
        principal.assert_proposal_access(
            stored["proposal"].get("tenantId") or "",
            stored["proposal"].get("studyId") or "",
            REVIEWER_ROLE,
        )
        item = next(
            (
                value
                for value in stored["objects"]
                if value.get("proposalObjectId") == proposal_object_id
            ),
            None,
        )
        if item is None:
            raise ValueError("OSB_PROPOSAL_REVIEW_OBJECT_NOT_FOUND")
        candidates = (item.get("mapping") or {}).get("candidates") or []
        if decision.action == "selected_candidate":
            if not decision.candidate_key or decision.candidate_key not in {
                candidate.get("candidateKey") for candidate in candidates
            }:
                raise ValueError("OSB_PROPOSAL_REVIEW_CANDIDATE_INVALID")
        elif decision.candidate_key is not None:
            raise ValueError("OSB_PROPOSAL_REVIEW_CANDIDATE_NOT_ALLOWED")
        if decision.action in {"create_request", "not_applicable", "rejected"} and not (
            decision.note and decision.note.strip()
        ):
            raise ValueError("OSB_PROPOSAL_REVIEW_REASON_REQUIRED")
        decided_at = datetime.now(timezone.utc).isoformat()
        decision_record = {
            "decision_id": str(uuid4()),
            "action": decision.action,
            "candidate_key": decision.candidate_key,
            "note": decision.note,
            "signature_id": decision.signature_id,
            "signature_verified": True,
            "actor_id": actor_id,
            "decided_at": decided_at,
        }
        decision_record["decision_content_hash"] = _canonical_hash(
            {
                "proposalHash": proposal_hash,
                "proposalObjectId": proposal_object_id,
                **decision_record,
            }
        )
        self.repository.append_decision(
            proposal_hash,
            proposal_object_id,
            decision_record,
        )
        return self.get_status(proposal_hash)

    @staticmethod
    def _decision_set_hash(
        proposal_hash: str,
        decisions: list[ProposalObjectDecision],
    ) -> str:
        return _canonical_hash(
            {
                "proposalHash": proposal_hash,
                "decisions": [
                    {
                        "proposalObjectId": item.proposal_object_id,
                        "decisionContentHash": item.decision_content_hash,
                    }
                    for item in sorted(
                        decisions,
                        key=lambda value: value.proposal_object_id,
                    )
                ],
            }
        )

    @staticmethod
    def _authorization_hash_input(
        authorization: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in authorization.items()
            if key != "authorization_content_hash"
        }

    def authorize_execution(
        self,
        proposal_hash: str,
        request: ProposalExecutionAuthorizationInput,
        principal: ProposalReviewPrincipal,
    ) -> ProposalReviewStatus:
        """Authorize one immutable decision set for one current owned draft."""
        stored = self.repository.get_proposal(proposal_hash)
        if stored is None:
            raise ValueError("OSB_PROPOSAL_REVIEW_NOT_FOUND")
        principal.assert_proposal_access(
            stored["proposal"].get("tenantId") or "",
            stored["proposal"].get("studyId") or "",
            REVIEWER_ROLE,
        )
        principal.assert_study_access(request.target_study_uid)
        principal.assert_can_sign(request.signature_id)
        status = self.get_status(proposal_hash)
        if not status.review_complete:
            raise ValueError("OSB_PROPOSAL_REVIEW_INCOMPLETE")
        if status.rejected_object_count:
            raise ValueError("OSB_PROPOSAL_REVIEW_REJECTED")
        if any(
            not item.latest_decision or not item.latest_decision.signature_verified
            for item in status.objects
        ):
            raise ValueError("OSB_PROPOSAL_REVIEW_SIGNATURE_NOT_VERIFIED")
        if request.expected_decision_set_hash != status.decision_set_hash:
            raise ValueError("OSB_PROPOSAL_REVIEW_DECISION_SET_STALE")

        if stored["proposal"].get("authorityMode") != "enforced":
            raise ValueError("OSB_PROPOSAL_AUTHORITY_MODE_NOT_ENFORCED")
        disallowed_blockers = [
            blocker
            for blocker in status.execution_blockers
            if blocker
            not in {
                "OSB_EXECUTION_AUTHORIZATION_REQUIRED",
                "OSB_STUDY_OWNERSHIP_UNVERIFIED",
            }
        ]
        if disallowed_blockers:
            raise ValueError(
                "OSB_PROPOSAL_EXECUTION_BLOCKED:" + ",".join(disallowed_blockers)
            )

        target = self.repository.get_draft_target(request.target_study_uid)
        if target is None:
            raise ValueError("OSB_PROPOSAL_TARGET_DRAFT_NOT_FOUND")
        if (
            target["study_uid"] != request.target_study_uid
            or target["version"] != request.target_study_version
            or target["status"] != "DRAFT"
        ):
            raise ValueError("OSB_PROPOSAL_TARGET_DRAFT_VERSION_STALE")
        if target["owner_id"] != principal.actor_id:
            raise ValueError("OSB_PROPOSAL_TARGET_DRAFT_NOT_OWNED_BY_REVIEWER")

        authorized_at = datetime.now(timezone.utc)
        authorization = {
            "authorization_id": str(uuid4()),
            "proposal_hash": proposal_hash,
            "target_study_uid": target["study_uid"],
            "target_study_version": target["version"],
            "target_study_status": "DRAFT",
            "target_study_value_node_id": target["study_value_node_id"],
            "target_study_owner_id": target["owner_id"],
            "target_ownership_basis": target["ownership_basis"],
            "target_version_start_date": target["version_start_date"].isoformat(),
            "decision_set_hash": status.decision_set_hash,
            "signature_id": request.signature_id,
            "signature_verified": True,
            "actor_id": principal.actor_id,
            "authorized_at": authorized_at.isoformat(),
        }
        authorization["authorization_content_hash"] = _canonical_hash(
            self._authorization_hash_input(authorization)
        )
        self.repository.append_execution_authorization(
            proposal_hash,
            authorization,
        )
        return self.get_status(proposal_hash)

    def get_status(
        self,
        proposal_hash: str,
        principal: ProposalReviewPrincipal | None = None,
    ) -> ProposalReviewStatus:
        stored = self.repository.get_proposal(proposal_hash)
        if stored is None:
            raise ValueError("OSB_PROPOSAL_REVIEW_NOT_FOUND")
        if principal is not None:
            principal.assert_proposal_access(
                stored["proposal"].get("tenantId") or "",
                stored["proposal"].get("studyId") or "",
                READER_ROLE,
            )
        latest = {}
        invalid_decision_hashes = []
        for value in self.repository.list_decisions(proposal_hash):
            expected_hash = _canonical_hash(
                {
                    "proposalHash": proposal_hash,
                    "proposalObjectId": value["proposal_object_id"],
                    **{
                        key: (
                            item if not isinstance(item, datetime) else item.isoformat()
                        )
                        for key, item in value.items()
                        if key
                        not in {
                            "proposal_hash",
                            "proposal_object_id",
                            "decision_content_hash",
                        }
                    },
                }
            )
            if value.get("decision_content_hash") != expected_hash:
                invalid_decision_hashes.append(value["proposal_object_id"])
                value["signature_verified"] = False
            latest[value["proposal_object_id"]] = ProposalObjectDecision(**value)
        review_objects = []
        object_target_keys_by_fact = {
            fact_id: {
                item.get("targetKey")
                for item in stored["objects"]
                if fact_id in ((item.get("mapping") or {}).get("factIds") or [])
            }
            for fact_id in {
                fact_id
                for item in stored["objects"]
                for fact_id in ((item.get("mapping") or {}).get("factIds") or [])
            }
        }
        object_by_fact_target = {
            (fact_id, item.get("targetKey")): item
            for item in stored["objects"]
            for fact_id in ((item.get("mapping") or {}).get("factIds") or [])
        }
        section_rank = {name: index for index, name in enumerate(SECTION_ORDER)}
        ordered_objects = sorted(
            stored["objects"],
            key=lambda item: (
                section_rank.get(item.get("section"), len(SECTION_ORDER)),
                item.get("proposalObjectId") or "",
            ),
        )
        for item in ordered_objects:
            mapping = item.get("mapping") or {}
            object_id = item["proposalObjectId"]
            fact_ids = mapping.get("factIds") or []
            dependency_target_keys = item.get("dependencyTargetKeys") or []
            available = (
                object_target_keys_by_fact.get(fact_ids[0], set())
                if fact_ids
                else set()
            )
            missing_dependencies = sorted(set(dependency_target_keys) - available)
            unselected_dependencies = (
                sorted(
                    target_key
                    for target_key in dependency_target_keys
                    if target_key in available
                    and (
                        latest.get(
                            object_by_fact_target[(fact_ids[0], target_key)][
                                "proposalObjectId"
                            ]
                        )
                        is None
                        or latest[
                            object_by_fact_target[(fact_ids[0], target_key)][
                                "proposalObjectId"
                            ]
                        ].action
                        != "selected_candidate"
                    )
                )
                if fact_ids
                else []
            )
            capability_kind = target_capability(mapping.get("proposedResourceType"))
            review_objects.append(
                ProposalReviewObject(
                    proposal_object_id=object_id,
                    concept_id=item["conceptId"],
                    target_key=item["targetKey"],
                    section=item["section"],
                    proposed_resource_type=mapping.get("proposedResourceType") or "",
                    fact_ids=fact_ids,
                    capability_kind=capability_kind or "unsupported",
                    dependency_target_keys=dependency_target_keys,
                    missing_dependency_target_keys=missing_dependencies,
                    unselected_dependency_target_keys=unselected_dependencies,
                    source=item.get("source") or {},
                    evidence=mapping.get("evidence") or [],
                    candidates=mapping.get("candidates") or [],
                    match_method=mapping.get("matchMethod") or "none",
                    extraction_confidence=mapping.get("extractionConfidence"),
                    mapping_confidence=mapping.get("mappingConfidence"),
                    proposed_disposition=mapping.get("disposition") or "unresolved",
                    latest_decision=latest.get(object_id),
                )
            )
        decisions = [
            item.latest_decision for item in review_objects if item.latest_decision
        ]
        rejected = sum(item.action == "rejected" for item in decisions)
        review_complete = len(decisions) == len(review_objects)
        decision_set_hash = self._decision_set_hash(proposal_hash, decisions)
        proposal = stored["proposal"]
        mapping_context = self.repository.get_context(proposal["osbMappingContextHash"])
        mapping_context_release_blockers = [
            f"OSB_RELEASE_MAPPING_CONTEXT_BLOCKER:{code}"
            for code in (mapping_context or {}).get("releaseBlockers", [])
        ]
        authorization_raw = self.repository.get_execution_authorization(proposal_hash)
        authorization = (
            ProposalExecutionAuthorization(**authorization_raw)
            if authorization_raw
            else None
        )
        target = (
            self.repository.get_draft_target(authorization.target_study_uid)
            if authorization
            else None
        )
        authorization_hash_valid = bool(
            authorization_raw
            and _canonical_hash(self._authorization_hash_input(authorization_raw))
            == authorization_raw.get("authorization_content_hash")
        )
        target_ownership_verified = bool(
            authorization
            and target
            and authorization_hash_valid
            and target.get("study_uid") == authorization.target_study_uid
            and target.get("version") == authorization.target_study_version
            and target.get("status") == "DRAFT"
            and target.get("owner_id") == authorization.target_study_owner_id
            and target.get("owner_id") == authorization.actor_id
            and target.get("ownership_basis") == authorization.target_ownership_basis
        )
        target_snapshot_verified = bool(
            target_ownership_verified
            and target
            and authorization
            and target.get("study_value_node_id")
            == authorization.target_study_value_node_id
            and target.get("version_start_date")
            == authorization.target_version_start_date
        )
        if not review_objects:
            execution_blockers = ["OSB_PROPOSAL_REVIEW_EMPTY"]
        elif not review_complete:
            execution_blockers = ["OSB_PROPOSAL_REVIEW_INCOMPLETE"]
        elif rejected:
            execution_blockers = ["OSB_PROPOSAL_REVIEW_REJECTED"]
        else:
            # Review completion is not execution readiness. Name each missing
            # authority/executor dependency instead of one blanket blocker so the
            # worker cannot mistake support for one family as support for all.
            execution_blockers = [
                *(
                    ["OSB_PROPOSAL_AUTHORITY_MODE_NOT_ENFORCED"]
                    if proposal.get("authorityMode") != "enforced"
                    else []
                ),
                *(
                    [
                        "OSB_REVIEW_DECISION_CONTENT_HASH_INVALID:"
                        + ",".join(sorted(invalid_decision_hashes))
                    ]
                    if invalid_decision_hashes
                    else []
                ),
                *(
                    [
                        "OSB_REVIEW_SIGNATURE_NOT_VERIFIED:"
                        + ",".join(
                            item.proposal_object_id
                            for item in review_objects
                            if not item.latest_decision
                            or not item.latest_decision.signature_verified
                        )
                    ]
                    if any(
                        not item.latest_decision
                        or not item.latest_decision.signature_verified
                        for item in review_objects
                    )
                    else []
                ),
                *(
                    ["OSB_EXECUTION_AUTHORIZATION_REQUIRED"]
                    if authorization is None
                    else []
                ),
                *(
                    ["OSB_EXECUTION_AUTHORIZATION_CONTENT_HASH_INVALID"]
                    if authorization and not authorization_hash_valid
                    else []
                ),
                *(
                    ["OSB_EXECUTION_AUTHORIZATION_PROPOSAL_MISMATCH"]
                    if authorization and authorization.proposal_hash != proposal_hash
                    else []
                ),
                *(
                    ["OSB_EXECUTION_AUTHORIZATION_DECISION_SET_STALE"]
                    if authorization
                    and authorization.decision_set_hash != decision_set_hash
                    else []
                ),
                *(
                    ["OSB_EXECUTION_AUTHORIZATION_SIGNATURE_NOT_VERIFIED"]
                    if authorization and not authorization.signature_verified
                    else []
                ),
                *(
                    ["OSB_STUDY_OWNERSHIP_UNVERIFIED"]
                    if not target_ownership_verified
                    else []
                ),
                *(
                    ["OSB_STUDY_DRAFT_SNAPSHOT_STALE"]
                    if target_ownership_verified and not target_snapshot_verified
                    else []
                ),
                *[
                    f"OSB_NATIVE_V2_DEPENDENCY_MISSING:{item.proposal_object_id}:"
                    f"{','.join(item.missing_dependency_target_keys)}"
                    for item in review_objects
                    if item.missing_dependency_target_keys
                ],
                *[
                    f"OSB_NATIVE_V2_DEPENDENCY_NOT_SELECTED:{item.proposal_object_id}:"
                    f"{','.join(item.unselected_dependency_target_keys)}"
                    for item in review_objects
                    if item.unselected_dependency_target_keys
                ],
                *[
                    f"OSB_NATIVE_V2_SELECTION_REQUIRED:{item.proposal_object_id}"
                    for item in review_objects
                    if item.capability_kind == "native_study_mutation"
                    and item.proposed_resource_type in NATIVE_SELECTION_RESOURCE_TYPES
                    and item.proposed_resource_type
                    not in NATIVE_DUAL_MODE_RESOURCE_TYPES
                    and item.latest_decision
                    and item.latest_decision.action != "selected_candidate"
                    and not _declined_optional(item)
                ],
                *[
                    f"OSB_NATIVE_V2_CREATE_REQUEST_REQUIRED:{item.proposal_object_id}"
                    for item in review_objects
                    if item.proposed_resource_type
                    in NATIVE_CREATE_REQUEST_RESOURCE_TYPES
                    and item.proposed_resource_type
                    not in NATIVE_DUAL_MODE_RESOURCE_TYPES
                    and item.latest_decision
                    and item.latest_decision.action != "create_request"
                    and not _declined_optional(item)
                ],
                *[
                    f"OSB_NATIVE_V2_SELECTION_OR_CREATE_REQUEST_REQUIRED:"
                    f"{item.proposal_object_id}"
                    for item in review_objects
                    if item.proposed_resource_type in NATIVE_DUAL_MODE_RESOURCE_TYPES
                    and item.latest_decision
                    and item.latest_decision.action
                    not in {"selected_candidate", "create_request"}
                ],
                *(
                    ["OSB_NATIVE_V2_NO_EXECUTABLE_OBJECTS"]
                    if not any(
                        item.proposed_resource_type in NATIVE_EXECUTOR_RESOURCE_TYPES
                        for item in review_objects
                    )
                    else []
                ),
            ]
        execution_blockers = list(dict.fromkeys(execution_blockers))
        dependency_object_ids = {
            object_by_fact_target[(item.fact_ids[0], target_key)]["proposalObjectId"]
            for item in review_objects
            if item.fact_ids
            for target_key in item.dependency_target_keys
            if (item.fact_ids[0], target_key) in object_by_fact_target
        }
        release_blockers = list(
            dict.fromkeys(
                [
                    *execution_blockers,
                    *mapping_context_release_blockers,
                    *[
                        f"OSB_RELEASE_NATIVE_FAMILY_EXECUTOR_UNAVAILABLE:"
                        f"{item.proposal_object_id}:{item.proposed_resource_type}"
                        for item in review_objects
                        if item.capability_kind == "native_study_mutation"
                        and item.proposed_resource_type
                        not in NATIVE_EXECUTOR_RESOURCE_TYPES
                    ],
                    *[
                        f"OSB_RELEASE_CREATE_REQUEST_EXECUTOR_UNAVAILABLE:"
                        f"{item.proposal_object_id}:{item.proposed_resource_type}"
                        for item in review_objects
                        if item.latest_decision
                        and item.latest_decision.action == "create_request"
                        and item.proposed_resource_type
                        not in NATIVE_CREATE_REQUEST_RESOURCE_TYPES
                    ],
                    *[
                        f"OSB_RELEASE_GOVERNED_REFERENCE_NOT_CONSUMED:"
                        f"{item.proposal_object_id}:{item.proposed_resource_type}"
                        for item in review_objects
                        if item.capability_kind == "governed_library_reference"
                        and item.proposal_object_id not in dependency_object_ids
                    ],
                    *[
                        f"OSB_RELEASE_NON_NATIVE_TARGET:"
                        f"{item.proposal_object_id}:{item.proposed_resource_type}"
                        for item in review_objects
                        if item.capability_kind
                        in {"governed_extension", "retained_narrative", "unresolved"}
                    ],
                    *[
                        f"OSB_RELEASE_RESOURCE_UNSUPPORTED:"
                        f"{item.proposal_object_id}:{item.proposed_resource_type}"
                        for item in review_objects
                        if item.capability_kind == "unsupported"
                    ],
                ]
            )
        )
        accepted_at = stored["accepted_at"]
        return ProposalReviewStatus(
            proposal_hash=proposal_hash,
            proposal_id=proposal["proposalId"],
            source_build_hash=proposal["sourceBuildHash"],
            source_study_id=proposal["studyId"],
            context_hash=proposal["osbMappingContextHash"],
            osb_openapi_hash=proposal["osbOpenApiHash"],
            accepted_at=convert_to_datetime(accepted_at),
            accepted_by_worker=stored["worker_id"],
            source_run_ids=proposal.get("sourceRunIds") or [],
            source_document_version_ids=proposal.get("sourceDocumentVersionIds") or [],
            source_fact_refs=proposal.get("sourceFactRefs") or [],
            object_count=len(review_objects),
            decided_object_count=len(decisions),
            rejected_object_count=rejected,
            review_complete=review_complete,
            decision_set_hash=decision_set_hash,
            target_study_uid=(
                authorization.target_study_uid if authorization else None
            ),
            target_study_version=(
                authorization.target_study_version if authorization else None
            ),
            target_study_status=(
                authorization.target_study_status if authorization else None
            ),
            target_study_value_node_id=(
                authorization.target_study_value_node_id if authorization else None
            ),
            target_study_owner_id=(
                authorization.target_study_owner_id if authorization else None
            ),
            target_ownership_basis=(
                authorization.target_ownership_basis if authorization else None
            ),
            target_ownership_verified=target_ownership_verified,
            target_snapshot_verified=target_snapshot_verified,
            execution_authorization=authorization,
            native_execution_ready=not execution_blockers,
            execution_blockers=execution_blockers,
            release_ready=not release_blockers,
            release_blockers=release_blockers,
            objects=review_objects,
        )
