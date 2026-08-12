"""OSB-owned Proposal V2 intake and item-level decision semantics."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from clinical_mdr_api.models.integrations.proposal_review import (
    ProposalObjectDecisionInput,
    ProposalReviewIntake,
)
from clinical_mdr_api.services.integrations.proposal_review import (
    ProposalReviewService,
    _canonical_hash,
)
from clinical_mdr_api.services.integrations.canonical_json import canonical_json
from clinical_mdr_api.tests.fixtures.osb_proposal_v21 import (
    CONTEXT_HASH,
    OPENAPI_HASH,
    build_context,
    build_proposal,
)


def _proposal():
    return deepcopy(build_proposal())


def _activity_object(proposal):
    return proposal["sections"]["activitiesItems"][0]


def _item_object(proposal):
    return proposal["sections"]["odm"][0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1e-7, "1e-7"),
        (1e-6, "0.000001"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (9_007_199_254_740_993, "9007199254740992"),
        (-0.0, "0"),
    ],
)
def test_canonical_json_matches_javascript_number_format(value, expected):
    assert canonical_json(value) == expected


def test_review_decision_requires_a_signature_identity():
    with pytest.raises(ValidationError):
        ProposalObjectDecisionInput(action="selected_candidate", candidate_key="a" * 64)


def test_intake_preserves_structured_clinical_source_values():
    proposal = _proposal()
    source = _item_object(proposal)["source"]
    source["values"] = [
        {
            "name": "tests",
            "sourcePath": "/tests",
            "valueType": "json_list",
            "value": [
                {"testName": "Sodium", "loincCode": "2951-2", "unit": "mmol/L"},
                {"testName": "Potassium", "ranges": [{"low": 3.5, "high": 5.1}]},
            ],
        },
        {
            "name": "estimand",
            "sourcePath": "/estimand",
            "valueType": "object",
            "value": {
                "population": "Full analysis set",
                "intercurrentEvents": [
                    {
                        "event": "Treatment discontinuation",
                        "strategy": "Treatment policy",
                    }
                ],
            },
        },
    ]
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = _canonical_hash(content)

    intake = ProposalReviewIntake(proposal=proposal, worker_id="worker-1")

    assert (
        intake.proposal.sections.odm[0].source.values[0].value
        == source["values"][0]["value"]
    )
    assert (
        intake.proposal.sections.odm[0].source.values[1].value
        == source["values"][1]["value"]
    )


def test_structured_source_value_discriminator_and_paths_are_fail_closed():
    proposal = _proposal()
    source = _item_object(proposal)["source"]
    source["values"] = [
        {
            "name": "estimand",
            "sourcePath": "/estimand",
            "valueType": "object",
            "value": ["not-an-object"],
        },
    ]
    with pytest.raises(ValidationError, match="SOURCE_VALUE_TYPE_MISMATCH"):
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1")

    source["values"] = [
        {
            "name": "tests",
            "sourcePath": "/tests",
            "valueType": "json_list",
            "value": [],
        },
        {
            "name": "tests-copy",
            "sourcePath": "/tests",
            "valueType": "json_list",
            "value": [],
        },
    ]
    with pytest.raises(ValidationError, match="SOURCE_PATH_DUPLICATE"):
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1")


class MemoryRepository:
    def __init__(self):
        self.contexts = {CONTEXT_HASH: build_context()}
        self.proposals = {}
        self.decisions = []
        self.save_calls = 0

    def get_context(self, context_hash):
        return deepcopy(self.contexts.get(context_hash))

    def save_proposal(self, proposal, objects, worker_id):
        self.save_calls += 1
        existing = self.proposals.get(proposal["proposalHash"])
        if existing is None:
            self.proposals[proposal["proposalHash"]] = {
                "proposal": deepcopy(proposal),
                "accepted_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
                "worker_id": worker_id,
                "objects": deepcopy(objects),
            }

    def get_proposal(self, proposal_hash):
        value = self.proposals.get(proposal_hash)
        return deepcopy(value) if value else None

    def list_decisions(self, proposal_hash):
        return deepcopy(
            [
                value
                for value in self.decisions
                if value["proposal_hash"] == proposal_hash
            ]
        )

    def append_decision(self, proposal_hash, proposal_object_id, decision):
        self.decisions.append(
            {
                "proposal_hash": proposal_hash,
                "proposal_object_id": proposal_object_id,
                **decision,
            }
        )


def test_intake_is_idempotent_and_remains_review_required():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    intake = ProposalReviewIntake(proposal=_proposal(), worker_id="worker-1")

    first = service.intake(intake, OPENAPI_HASH)
    second = service.intake(intake, OPENAPI_HASH)

    assert first == second
    assert repository.save_calls == 2
    assert first.object_count == 2
    assert first.decided_object_count == 0
    assert first.review_complete is False
    assert first.native_execution_ready is False
    assert first.execution_blockers == ["OSB_PROPOSAL_REVIEW_INCOMPLETE"]
    assert first.source_run_ids == ["run-1"]
    assert first.source_document_version_ids == ["document-1"]
    assert first.source_fact_refs[0]["factId"] == "fact-1"
    assert first.objects[0].source["exactQuote"] == "Blood Pressure"
    assert first.objects[0].evidence[0]["box"]["space"] == "pdf_points"


def test_candidate_must_come_from_the_stored_osb_context():
    proposal = _proposal()
    proposal["sections"]["activitiesItems"][0]["mapping"]["candidates"][0][
        "uid"
    ] = "Invented_99"
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = _canonical_hash(content)

    with pytest.raises(ValueError, match="CANDIDATE_NOT_IN_CONTEXT"):
        ProposalReviewService(MemoryRepository()).intake(
            ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
            OPENAPI_HASH,
        )


def test_strict_intake_rejects_unknown_nested_properties_and_oversized_strings():
    proposal = _proposal()
    proposal["sections"]["activitiesItems"][0]["source"]["arbitraryNestedBlob"] = {
        "nested": {"secret": "not schema approved"}
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1")

    oversized = _proposal()
    oversized["sections"]["activitiesItems"][0]["source"]["exactQuote"] = "x" * 16_385
    with pytest.raises(ValidationError, match="at most 16384 characters"):
        ProposalReviewIntake(proposal=oversized, worker_id="worker-1")


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda proposal: proposal["sections"]["activitiesItems"][0]["mapping"][
                "evidence"
            ][0].__setitem__("textHash", "0" * 64),
            "EVIDENCE_IDENTITY_INVALID",
        ),
        (
            lambda proposal: proposal["sections"]["activitiesItems"][0].__setitem__(
                "proposalObjectId", "0" * 64
            ),
            "OBJECT_OR_CONCEPT_ID_INVALID",
        ),
        (
            lambda proposal: proposal["sections"]["activitiesItems"][0]["mapping"][
                "candidates"
            ][0].__setitem__("candidateKey", "0" * 64),
            "CANDIDATE_NOT_IN_CONTEXT",
        ),
    ],
)
def test_intake_recomputes_evidence_object_and_candidate_identity(mutate, expected):
    proposal = _proposal()
    mutate(proposal)
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = _canonical_hash(content)
    repository = MemoryRepository()

    with pytest.raises(ValueError, match=expected):
        ProposalReviewService(repository).intake(
            ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
            OPENAPI_HASH,
        )
    assert repository.save_calls == 0


def test_il_cannot_prepopulate_an_osb_reviewer_decision():
    proposal = _proposal()
    proposal["sections"]["activitiesItems"][0]["mapping"]["reviewerDecision"] = {
        "actorId": "il-user"
    }
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = _canonical_hash(content)

    with pytest.raises(ValidationError, match="Input should be None"):
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1")


def test_every_object_requires_an_osb_decision_before_native_execution():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    proposal = _proposal()
    service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"), OPENAPI_HASH
    )

    partial = service.decide(
        proposal["proposalHash"],
        _activity_object(proposal)["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="selected_candidate",
            candidate_key=_activity_object(proposal)["mapping"]["candidates"][0][
                "candidateKey"
            ],
            signature_id="signature-1",
        ),
        actor_id="reviewer-1",
    )
    assert partial.decided_object_count == 1
    assert partial.native_execution_ready is False

    complete = service.decide(
        proposal["proposalHash"],
        _item_object(proposal)["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="create_request",
            note="Create draft item",
            signature_id="signature-2",
        ),
        actor_id="reviewer-2",
    )
    assert complete.review_complete is True
    assert complete.native_execution_ready is False
    assert complete.execution_blockers[:2] == [
        "OSB_REVIEW_SIGNATURE_VERIFICATION_UNAVAILABLE",
        "OSB_STUDY_OWNERSHIP_VERSION_UNRESOLVED",
    ]
    assert (
        f"OSB_NATIVE_V2_CREATE_REQUEST_EXECUTOR_UNAVAILABLE:"
        f"{_item_object(proposal)['proposalObjectId']}" in complete.execution_blockers
    )
    assert {item.capability_kind for item in complete.objects} == {
        "native_study_mutation",
        "governed_library_reference",
    }
    assert all(
        item.latest_decision.signature_verified is False for item in complete.objects
    )
    assert all(
        len(item.latest_decision.decision_content_hash) == 64
        for item in complete.objects
    )
    assert {item.latest_decision.actor_id for item in complete.objects} == {
        "reviewer-1",
        "reviewer-2",
    }


def test_native_target_with_missing_dependency_is_explicitly_blocked():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    proposal = _proposal()
    activity = _activity_object(proposal)
    activity["dependencyTargetKeys"] = ["flowchart-group"]
    objects = [item for section in proposal["sections"].values() for item in section]
    proposal["proposalId"] = _canonical_hash(
        {
            "tenantId": proposal["tenantId"],
            "studyId": proposal["studyId"],
            "projectId": proposal["projectId"],
            "authorityMode": proposal["authorityMode"],
            "sourceBuildHash": proposal["sourceBuildHash"],
            "osbOpenApiHash": proposal["osbOpenApiHash"],
            "osbMappingContextHash": proposal["osbMappingContextHash"],
            "proposalObjects": objects,
            "sourceDispositions": proposal["reconciliation"]["dispositions"],
        }
    )
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = _canonical_hash(content)

    status = service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"), OPENAPI_HASH
    )

    activity_status = next(
        item for item in status.objects if item.target_key == "activity"
    )
    assert activity_status.capability_kind == "native_study_mutation"
    assert activity_status.missing_dependency_target_keys == ["flowchart-group"]
    assert status.native_execution_ready is False


def test_intake_recomputes_target_capability_counts():
    proposal = _proposal()
    proposal["reconciliation"]["nativeStudyMutationTargets"] = 2
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = _canonical_hash(content)
    with pytest.raises(ValueError, match="RECONCILIATION_COUNT_MISMATCH"):
        ProposalReviewService(MemoryRepository()).intake(
            ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
            OPENAPI_HASH,
        )


def test_existing_but_unselected_dependency_blocks_execution():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    proposal = _proposal()
    activity = _activity_object(proposal)
    item = _item_object(proposal)
    activity["dependencyTargetKeys"] = [item["targetKey"]]
    objects = [row for section in proposal["sections"].values() for row in section]
    proposal["proposalId"] = _canonical_hash(
        {
            "tenantId": proposal["tenantId"],
            "studyId": proposal["studyId"],
            "projectId": proposal["projectId"],
            "authorityMode": proposal["authorityMode"],
            "sourceBuildHash": proposal["sourceBuildHash"],
            "osbOpenApiHash": proposal["osbOpenApiHash"],
            "osbMappingContextHash": proposal["osbMappingContextHash"],
            "proposalObjects": objects,
            "sourceDispositions": proposal["reconciliation"]["dispositions"],
        }
    )
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = _canonical_hash(content)
    service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"), OPENAPI_HASH
    )
    service.decide(
        proposal["proposalHash"],
        activity["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="selected_candidate",
            candidate_key=activity["mapping"]["candidates"][0]["candidateKey"],
            signature_id="signature-1",
        ),
        actor_id="reviewer-1",
    )
    status = service.decide(
        proposal["proposalHash"],
        item["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="create_request",
            note="No existing item",
            signature_id="signature-2",
        ),
        actor_id="reviewer-2",
    )
    activity_status = next(
        row
        for row in status.objects
        if row.proposal_object_id == activity["proposalObjectId"]
    )
    assert activity_status.unselected_dependency_target_keys == [item["targetKey"]]
    assert any(
        blocker.startswith("OSB_NATIVE_V2_DEPENDENCY_NOT_SELECTED:")
        for blocker in status.execution_blockers
    )


def test_rejection_requires_a_reason_and_blocks_native_execution():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    proposal = _proposal()
    service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"), OPENAPI_HASH
    )

    with pytest.raises(ValueError, match="REASON_REQUIRED"):
        service.decide(
            proposal["proposalHash"],
            _item_object(proposal)["proposalObjectId"],
            ProposalObjectDecisionInput(action="rejected", signature_id="signature-1"),
            actor_id="reviewer-1",
        )

    service.decide(
        proposal["proposalHash"],
        _activity_object(proposal)["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="selected_candidate",
            candidate_key=_activity_object(proposal)["mapping"]["candidates"][0][
                "candidateKey"
            ],
            signature_id="signature-1",
        ),
        actor_id="reviewer-1",
    )
    rejected = service.decide(
        proposal["proposalHash"],
        _item_object(proposal)["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="rejected",
            note="Not part of the approved study definition",
            signature_id="signature-2",
        ),
        actor_id="reviewer-1",
    )
    assert rejected.review_complete is True
    assert rejected.rejected_object_count == 1
    assert rejected.native_execution_ready is False
