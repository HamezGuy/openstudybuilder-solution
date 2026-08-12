"""Durable OSB-owned review boundary for source-neutral Proposal V2 envelopes."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from neomodel import db

from clinical_mdr_api.models.integrations.proposal_review import (
    ProposalObjectDecision,
    ProposalObjectDecisionInput,
    ProposalReviewIntake,
    ProposalReviewObject,
    ProposalReviewStatus,
)
from clinical_mdr_api.services.integrations.canonical_json import (
    canonical_hash as _canonical_hash,
)
from clinical_mdr_api.services.integrations.proposal_target_capabilities import (
    NATIVE_EXECUTOR_RESOURCE_TYPES,
    target_capability,
)
from clinical_mdr_api.services.integrations.canonical_json import (
    canonical_json as _canonical_json,
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
MAX_PROPOSAL_BYTES = 32 * 1024 * 1024


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
                signature_verified: false,
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


class ProposalReviewService:
    def __init__(self, repository: Neo4jProposalReviewRepository | None = None):
        self.repository = repository or Neo4jProposalReviewRepository()

    def intake(
        self,
        intake: ProposalReviewIntake,
        live_openapi_hash: str,
    ) -> ProposalReviewStatus:
        proposal = intake.proposal.model_dump(by_alias=True)
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
        expected_source_build_hash = _canonical_hash(
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
        actor_id: str,
    ) -> ProposalReviewStatus:
        if not actor_id:
            raise ValueError("OSB_PROPOSAL_REVIEW_ACTOR_REQUIRED")
        stored = self.repository.get_proposal(proposal_hash)
        if stored is None:
            raise ValueError("OSB_PROPOSAL_REVIEW_NOT_FOUND")
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
            "signature_verified": False,
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

    def get_status(self, proposal_hash: str) -> ProposalReviewStatus:
        stored = self.repository.get_proposal(proposal_hash)
        if stored is None:
            raise ValueError("OSB_PROPOSAL_REVIEW_NOT_FOUND")
        latest = {}
        for value in self.repository.list_decisions(proposal_hash):
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
                "OSB_REVIEW_SIGNATURE_VERIFICATION_UNAVAILABLE",
                "OSB_STUDY_OWNERSHIP_VERSION_UNRESOLVED",
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
                    f"OSB_NATIVE_V2_FAMILY_EXECUTOR_UNAVAILABLE:"
                    f"{item.proposed_resource_type}"
                    for item in review_objects
                    if item.capability_kind == "native_study_mutation"
                    and item.proposed_resource_type
                    not in NATIVE_EXECUTOR_RESOURCE_TYPES
                ],
                *[
                    f"OSB_NATIVE_V2_CREATE_REQUEST_EXECUTOR_UNAVAILABLE:"
                    f"{item.proposal_object_id}"
                    for item in review_objects
                    if item.latest_decision
                    and item.latest_decision.action == "create_request"
                ],
                *[
                    f"OSB_NATIVE_V2_RESOURCE_UNSUPPORTED:{resource_type}"
                    for resource_type in sorted(
                        {
                            item.proposed_resource_type
                            for item in review_objects
                            if item.capability_kind
                            not in {
                                "native_study_mutation",
                                "governed_library_reference",
                            }
                            and item.proposed_resource_type
                        }
                    )
                ],
            ]
        proposal = stored["proposal"]
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
            native_execution_ready=False,
            execution_blockers=execution_blockers,
            objects=review_objects,
        )
