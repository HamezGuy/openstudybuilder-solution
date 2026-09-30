"""Pure Proposal V2 validation and deterministic saga planning.

This is intentionally not a V1 payload adapter. Proposal objects name OSB
resource families and candidate IDs from a pinned mapping context; unknown or
unresolved objects become review items rather than invented native values.
"""

from __future__ import annotations

from .proposal_v2_capabilities import require_target_capability

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

ACTIONABLE_DISPOSITIONS = {"exact", "review", "create_request", "unresolved"}


class ProposalPlanError(ValueError):
    pass


def proposal_object_plan(proposal):
    """Return objects in dependency order after enforcing conservation.

    The plan carries decisions; it does not convert source text into final OSB
    IDs. Only an ``exact`` object with one selected, context-pinned candidate can
    be auto-selected. Everything else remains review-required.
    """
    if (
        proposal.get("formatVersion") != "osb-proposal/2.1"
        or proposal.get("canonicalizationVersion") != "canonical-json/1.0"
    ):
        raise ProposalPlanError("OSB_PROPOSAL_FORMAT_UNSUPPORTED")
    context_hash = proposal.get("osbMappingContextHash")
    openapi_hash = proposal.get("osbOpenApiHash")
    if not context_hash or not openapi_hash:
        raise ProposalPlanError("OSB_PROPOSAL_CONTEXT_REQUIRED")
    reconciliation = proposal.get("reconciliation") or {}
    if reconciliation.get("balanced") is not True:
        raise ProposalPlanError("OSB_PROPOSAL_RECONCILIATION_UNBALANCED")

    sections = proposal.get("sections") or {}
    unknown_sections = sorted(set(sections) - set(SECTION_ORDER))
    if unknown_sections:
        raise ProposalPlanError(
            f"OSB_PROPOSAL_UNKNOWN_SECTION:{','.join(unknown_sections)}"
        )

    plan = []
    object_ids = set()
    mapped_fact_ids = set()
    target_keys_by_fact = {}
    raw_items = []
    for section in SECTION_ORDER:
        for item in sections.get(section) or []:
            mapping = item.get("mapping") or {}
            raw_items.append((section, item))
            for fact_id in mapping.get("factIds") or []:
                target_keys_by_fact.setdefault(fact_id, set()).add(
                    item.get("targetKey")
                )
    for section, item in raw_items:
        object_id = item.get("proposalObjectId")
        mapping = item.get("mapping") or {}
        disposition = mapping.get("disposition")
        fact_ids = mapping.get("factIds") or []
        candidates = mapping.get("candidates") or []
        selected = mapping.get("selectedCandidate")
        resource_type = mapping.get("proposedResourceType")
        try:
            capability_kind, api_path = require_target_capability(resource_type)
        except ValueError as error:
            raise ProposalPlanError(str(error)) from error
        source_values = (item.get("source") or {}).get("values") or []
        dependency_target_keys = item.get("dependencyTargetKeys") or []
        if not object_id or object_id in object_ids:
            raise ProposalPlanError("OSB_PROPOSAL_OBJECT_ID_DUPLICATE_OR_MISSING")
        if not fact_ids or len(fact_ids) != len(set(fact_ids)):
            raise ProposalPlanError(f"OSB_PROPOSAL_OBJECT_FACT_ID_INVALID:{object_id}")
        if disposition not in ACTIONABLE_DISPOSITIONS:
            raise ProposalPlanError(
                f"OSB_PROPOSAL_OBJECT_DISPOSITION_INVALID:{object_id}"
            )
        source_paths = [value.get("sourcePath") for value in source_values]
        if any(
            not isinstance(path, str) or not path.startswith("/") or path == "/"
            for path in source_paths
        ) or len(source_paths) != len(set(source_paths)):
            raise ProposalPlanError(
                f"OSB_PROPOSAL_OBJECT_SOURCE_PATH_INVALID:{object_id}"
            )
        for candidate in candidates:
            if candidate.get("contextHash") != context_hash:
                raise ProposalPlanError(
                    f"OSB_PROPOSAL_STALE_CANDIDATE_CONTEXT:{object_id}"
                )
            if (
                not candidate.get("candidateKey")
                or not candidate.get("uid")
                or not candidate.get("resourceFamily")
                or not candidate.get("resourceType")
                or not candidate.get("version")
                or not candidate.get("status")
                or "libraryName" not in candidate
            ):
                raise ProposalPlanError(
                    f"OSB_PROPOSAL_CANDIDATE_IDENTITY_INVALID:{object_id}"
                )
        if disposition == "exact":
            if len(candidates) != 1 or selected != candidates[0]:
                raise ProposalPlanError(
                    f"OSB_PROPOSAL_EXACT_SELECTION_INVALID:{object_id}"
                )
            action = "select_candidate"
        elif disposition == "create_request":
            action = "create_draft_request"
        else:
            action = "review_required"
        available_dependencies = target_keys_by_fact.get(fact_ids[0], set())
        missing_dependencies = sorted(
            set(dependency_target_keys) - available_dependencies
        )
        object_ids.add(object_id)
        mapped_fact_ids.update(fact_ids)
        plan.append(
            {
                "proposal_object_id": object_id,
                "concept_id": item.get("conceptId"),
                "target_key": item.get("targetKey"),
                "section": section,
                "resource_type": mapping.get("proposedResourceType"),
                "capability_kind": capability_kind,
                "api_path": api_path,
                "dependency_target_keys": list(dependency_target_keys),
                "missing_dependency_target_keys": missing_dependencies,
                "fact_ids": list(fact_ids),
                "source_paths": source_paths,
                "action": action,
                "candidate_key": selected.get("candidateKey") if selected else None,
            }
        )

    source_fact_ids = {
        ref.get("factId") for ref in proposal.get("sourceFactRefs") or []
    }
    disposition_fact_ids = {
        row.get("factId") for row in reconciliation.get("dispositions") or []
    }
    if None in source_fact_ids or None in disposition_fact_ids:
        raise ProposalPlanError("OSB_PROPOSAL_FACT_ID_MISSING")
    if mapped_fact_ids & disposition_fact_ids:
        raise ProposalPlanError("OSB_PROPOSAL_FACT_DOUBLE_DISPOSITION")
    if mapped_fact_ids | disposition_fact_ids != source_fact_ids:
        raise ProposalPlanError("OSB_PROPOSAL_FACT_BALANCE_MISMATCH")
    if reconciliation.get("mappedSourceFacts") != len(mapped_fact_ids):
        raise ProposalPlanError("OSB_PROPOSAL_MAPPED_FACT_COUNT_MISMATCH")
    if reconciliation.get("proposedObjects") != len(plan):
        raise ProposalPlanError("OSB_PROPOSAL_OBJECT_COUNT_MISMATCH")
    capability_counts = {
        "nativeStudyMutationTargets": sum(
            item["capability_kind"] == "native_study_mutation" for item in plan
        ),
        "governedLibraryReferenceTargets": sum(
            item["capability_kind"] == "governed_library_reference" for item in plan
        ),
        "governedExtensionTargets": sum(
            item["capability_kind"] == "governed_extension" for item in plan
        ),
        "retainedNarrativeTargets": sum(
            item["capability_kind"] == "retained_narrative" for item in plan
        ),
        "unresolvedTargets": sum(
            item["capability_kind"] == "unresolved" for item in plan
        ),
    }
    kinds_by_fact = {}
    for item in plan:
        for fact_id in item["fact_ids"]:
            kinds_by_fact.setdefault(fact_id, []).append(item["capability_kind"])
    capability_counts["nativeTargetSourceFacts"] = sum(
        "native_study_mutation" in kinds for kinds in kinds_by_fact.values()
    )
    capability_counts["fullyNativeTargetSourceFacts"] = sum(
        "native_study_mutation" in kinds
        and all(
            kind in {"native_study_mutation", "governed_library_reference"}
            for kind in kinds
        )
        for kinds in kinds_by_fact.values()
    )
    if any(
        reconciliation.get(key) != value for key, value in capability_counts.items()
    ):
        raise ProposalPlanError("OSB_PROPOSAL_TARGET_CAPABILITY_COUNT_MISMATCH")
    return plan
