"""Proposal V2 planning tests: no V1 carrier or live OSB required."""

import hashlib

import pytest

from ..mappings.proposal_v2_to_osb import ProposalPlanError, proposal_object_plan
from ..utils.osb_proposal_db import _stable_hash


def proposal(objects, source_fact_ids=("fact-1",), dispositions=()):
    sections = {
        "studySetup": [],
        "standards": [],
        "objectives": [],
        "endpoints": [],
        "criteria": [],
        "productsDosing": [],
        "armsCohortsBranches": [],
        "epochsElementsCells": [],
        "visitsTiming": [],
        "activitiesItems": [],
        "soa": [],
        "odm": [],
        "extensions": [],
        "retainedNarrative": [],
        "unresolved": [],
    }
    for section, item in objects:
        sections[section].append(item)
    mapped = {fact_id for _, item in objects for fact_id in item["mapping"]["factIds"]}
    capability = {
        "StudySelectionActivity": "native_study_mutation",
        "OdmItem": "governed_library_reference",
    }
    kinds_by_fact = {}
    for _, entry in objects:
        kind = capability.get(entry["mapping"]["proposedResourceType"], "unresolved")
        for fact_id in entry["mapping"]["factIds"]:
            kinds_by_fact.setdefault(fact_id, []).append(kind)
    kinds = [kind for values in kinds_by_fact.values() for kind in values]
    return {
        "formatVersion": "osb-proposal/2.1",
        "canonicalizationVersion": "canonical-json/1.0",
        "proposalHash": "hash",
        "osbOpenApiHash": "openapi",
        "osbMappingContextHash": "context",
        "sections": sections,
        "sourceFactRefs": [
            {"factId": fact_id, "factContentHash": "fact-hash"}
            for fact_id in source_fact_ids
        ],
        "reconciliation": {
            "balanced": True,
            "sourceFacts": len(source_fact_ids),
            "proposedObjects": len(objects),
            "nativeStudyMutationTargets": kinds.count("native_study_mutation"),
            "governedLibraryReferenceTargets": kinds.count(
                "governed_library_reference"
            ),
            "governedExtensionTargets": kinds.count("governed_extension"),
            "retainedNarrativeTargets": kinds.count("retained_narrative"),
            "unresolvedTargets": kinds.count("unresolved"),
            "nativeTargetSourceFacts": sum(
                "native_study_mutation" in values for values in kinds_by_fact.values()
            ),
            "fullyNativeTargetSourceFacts": sum(
                "native_study_mutation" in values
                and all(
                    kind in {"native_study_mutation", "governed_library_reference"}
                    for kind in values
                )
                for values in kinds_by_fact.values()
            ),
            "mappedSourceFacts": len(mapped),
            "dispositions": list(dispositions),
        },
    }


def item(object_id, fact_id="fact-1", disposition="unresolved", candidates=()):
    selected = (
        candidates[0] if disposition == "exact" and len(candidates) == 1 else None
    )
    return {
        "proposalObjectId": object_id,
        "targetKey": object_id,
        "dependencyTargetKeys": [],
        "source": {
            "values": [
                {
                    "name": "assessment",
                    "sourcePath": "/assessment",
                    "valueType": "string",
                    "value": "Blood Pressure",
                }
            ]
        },
        "mapping": {
            "factIds": [fact_id],
            "proposedResourceType": "OdmItem",
            "candidates": list(candidates),
            "selectedCandidate": selected,
            "disposition": disposition,
        },
    }


def test_many_to_many_fact_targets_remain_two_itemized_actions():
    value = proposal(
        [
            ("activitiesItems", item("activity", disposition="create_request")),
            ("odm", item("item")),
        ]
    )

    plan = proposal_object_plan(value)

    assert [row["proposal_object_id"] for row in plan] == ["activity", "item"]
    assert [row["action"] for row in plan] == [
        "create_draft_request",
        "review_required",
    ]
    assert all(row["source_paths"] == ["/assessment"] for row in plan)


def test_exact_mapping_requires_one_selected_candidate_from_pinned_context():
    candidate = {
        "candidateKey": "candidate-key",
        "resourceFamily": "odm_items",
        "uid": "OdmItem_1",
        "resourceType": "OdmItem",
        "version": "1.0",
        "status": "Final",
        "libraryName": "Sponsor",
        "contextHash": "context",
    }
    plan = proposal_object_plan(
        proposal([("odm", item("item", disposition="exact", candidates=(candidate,)))])
    )
    assert plan[0]["action"] == "select_candidate"
    assert plan[0]["candidate_key"] == "candidate-key"


def test_stale_candidate_context_is_rejected_before_any_api_plan():
    candidate = {
        "candidateKey": "candidate-key",
        "resourceFamily": "odm_items",
        "uid": "OdmItem_1",
        "resourceType": "OdmItem",
        "version": "1.0",
        "status": "Final",
        "libraryName": "Sponsor",
        "contextHash": "old-context",
    }
    with pytest.raises(ProposalPlanError, match="STALE_CANDIDATE_CONTEXT"):
        proposal_object_plan(proposal([("odm", item("item", candidates=(candidate,)))]))


def test_fact_cannot_be_both_mapped_and_non_export_disposed():
    value = proposal(
        [("odm", item("item"))],
        dispositions=({"factId": "fact-1", "kind": "signed_exclusion"},),
    )
    with pytest.raises(ProposalPlanError, match="FACT_DOUBLE_DISPOSITION"):
        proposal_object_plan(value)


def test_object_without_exact_source_paths_is_rejected_before_execution_plan():
    broken = item("item")
    broken["source"]["values"][0].pop("sourcePath")
    with pytest.raises(ProposalPlanError, match="OBJECT_SOURCE_PATH_INVALID"):
        proposal_object_plan(proposal([("odm", broken)]))


def test_unknown_or_generic_resource_type_is_rejected():
    broken = item("item")
    broken["mapping"]["proposedResourceType"] = "GenericBucket"
    with pytest.raises(ProposalPlanError, match="RESOURCE_TYPE_UNSUPPORTED"):
        proposal_object_plan(proposal([("odm", broken)]))


def test_native_plan_names_existing_osb_route_and_missing_dependencies():
    activity = item("activity")
    activity["mapping"]["proposedResourceType"] = "StudySelectionActivity"
    activity["dependencyTargetKeys"] = ["flowchart-group"]

    plan = proposal_object_plan(proposal([("activitiesItems", activity)]))

    assert plan[0]["capability_kind"] == "native_study_mutation"
    assert plan[0]["api_path"] == "/studies/{study_uid}/study-activities"
    assert plan[0]["missing_dependency_target_keys"] == ["flowchart-group"]


def test_fact_hash_canonicalization_matches_typescript_shape_for_common_values():
    canonical = '{"a":1,"b":[true,null,"é"],"z":1.5}'
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert _stable_hash({"z": 1.5, "b": [True, None, "é"], "a": 1}) == expected


@pytest.mark.parametrize(
    ("value", "canonical"),
    [
        (1e-7, "1e-7"),
        (1e-6, "0.000001"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (9_007_199_254_740_993, "9007199254740992"),
        (-0.0, "0"),
    ],
)
def test_fact_hash_numeric_canonicalization_matches_javascript(value, canonical):
    expected = hashlib.sha256(f'{{"value":{canonical}}}'.encode("utf-8")).hexdigest()
    assert _stable_hash({"value": value}) == expected
