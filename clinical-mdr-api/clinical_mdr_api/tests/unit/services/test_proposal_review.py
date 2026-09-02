"""OSB-owned Proposal V2 intake and item-level decision semantics."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from clinical_mdr_api.models.integrations.proposal_review import (
    ProposalExecutionAuthorizationInput,
    ProposalObjectDecisionInput,
    ProposalReviewIntake,
)
from clinical_mdr_api.services.integrations.canonical_json import canonical_json
from clinical_mdr_api.services.integrations.proposal_review import (
    ProposalReviewPrincipal,
    ProposalReviewService,
    _canonical_hash,
)
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


def _principal(
    actor_id: str,
    signature_id: str,
    scoped_study_ids: frozenset[str] | None = None,
) -> ProposalReviewPrincipal:
    return ProposalReviewPrincipal(
        actor_id=actor_id,
        human_user_id=actor_id,
        token_id=signature_id,
        tenant_id="tenant-1",
        scoped_study_ids=(
            scoped_study_ids or frozenset({"study-1", "Study_1"})
        ),
        organization_ids=frozenset(),
        roles=frozenset({"Study.Read", "Study.Write"}),
        authentication_verified=True,
    )


def _service_principal() -> ProposalReviewPrincipal:
    return ProposalReviewPrincipal(
        actor_id="proposal-worker",
        human_user_id="",
        token_id="",
        tenant_id="tenant-1",
        scoped_study_ids=frozenset({"study-1"}),
        organization_ids=frozenset(),
        roles=frozenset({"Study.Read", "Study.Write"}),
        authentication_verified=True,
    )


def _set_authority_mode(proposal: dict, authority_mode: str) -> dict:
    proposal = deepcopy(proposal)
    proposal["authorityMode"] = authority_mode
    proposal["sourceBuildHash"] = _canonical_hash(
        {
            "tenantId": proposal["tenantId"],
            "studyId": proposal["studyId"],
            "projectId": proposal["projectId"],
            "authorityMode": authority_mode,
            "sourceRunIds": proposal["sourceRunIds"],
            "sourceDocuments": proposal["sourceDocuments"],
            "sourceFactRefs": proposal["sourceFactRefs"],
        }
    )
    objects = []
    for section, values in proposal["sections"].items():
        for item in values:
            item["proposalObjectId"] = _canonical_hash(
                {
                    "sourceBuildHash": proposal["sourceBuildHash"],
                    "conceptId": item["conceptId"],
                    "targetKey": item["targetKey"],
                    "section": section,
                    "proposedResourceType": item["mapping"]["proposedResourceType"],
                }
            )
            objects.append(item)
    proposal["proposalId"] = _canonical_hash(
        {
            "tenantId": proposal["tenantId"],
            "studyId": proposal["studyId"],
            "projectId": proposal["projectId"],
            "authorityMode": authority_mode,
            "sourceBuildHash": proposal["sourceBuildHash"],
            "osbOpenApiHash": proposal["osbOpenApiHash"],
            "osbMappingContextHash": proposal["osbMappingContextHash"],
            "proposalObjects": objects,
            "sourceDispositions": proposal["reconciliation"]["dispositions"],
        }
    )
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    proposal["proposalHash"] = _canonical_hash(content)
    return proposal


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

    source["values"] = [
        {
            "name": "name",
            "sourcePath": "/first/name",
            "valueType": "string",
            "value": "Blood pressure",
        },
        {
            "name": "name",
            "sourcePath": "/second/name",
            "valueType": "string",
            "value": "Heart rate",
        },
    ]
    with pytest.raises(ValidationError, match="SOURCE_NAME_DUPLICATE"):
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1")


class MemoryRepository:
    def __init__(self):
        self.contexts = {CONTEXT_HASH: build_context()}
        self.proposals = {}
        self.decisions = []
        self.authorizations = {}
        self.targets = {}
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

    def get_execution_authorization(self, proposal_hash):
        return deepcopy(self.authorizations.get(proposal_hash))

    def get_draft_target(self, study_uid):
        return deepcopy(self.targets.get(study_uid))

    def append_execution_authorization(self, proposal_hash, authorization):
        target = self.targets.get(authorization["target_study_uid"])
        if (
            not target
            or target["version"] != authorization["target_study_version"]
            or target["owner_id"] != authorization["actor_id"]
            or target["study_value_node_id"]
            != authorization["target_study_value_node_id"]
            or target["ownership_basis"] != authorization["target_ownership_basis"]
        ):
            raise ValueError("OSB_PROPOSAL_TARGET_DRAFT_OWNERSHIP_STALE")
        self.authorizations[proposal_hash] = deepcopy(authorization)


def test_intake_is_idempotent_and_remains_review_required():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    intake = ProposalReviewIntake(proposal=_proposal(), worker_id="worker-1")

    first = service.intake(intake, OPENAPI_HASH, principal=_service_principal())
    second = service.intake(intake, OPENAPI_HASH, principal=_service_principal())

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


def test_service_access_is_scoped_but_does_not_require_a_human_identity():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    proposal = _proposal()
    intake = ProposalReviewIntake(proposal=proposal, worker_id="worker-1")
    worker = _service_principal()

    accepted = service.intake(intake, OPENAPI_HASH, principal=worker)
    assert service.get_status(proposal["proposalHash"], principal=worker) == accepted
    with pytest.raises(ValueError, match="HUMAN_IDENTITY_REQUIRED"):
        worker.assert_can_sign("worker-token")

    wrong_tenant = ProposalReviewPrincipal(
        **{**worker.__dict__, "tenant_id": "tenant-2"}
    )
    with pytest.raises(ValueError, match="TENANT_SCOPE_MISMATCH"):
        service.get_status(proposal["proposalHash"], principal=wrong_tenant)

    wrong_study = ProposalReviewPrincipal(
        **{**worker.__dict__, "scoped_study_ids": frozenset({"study-2"})}
    )
    with pytest.raises(ValueError, match="STUDY_SCOPE_MISMATCH"):
        service.get_status(proposal["proposalHash"], principal=wrong_study)


def test_auth_disabled_development_can_intake_but_cannot_sign():
    principal = ProposalReviewPrincipal(
        actor_id="unknown-user",
        human_user_id="unknown-user",
        token_id="",
        tenant_id="",
        scoped_study_ids=frozenset(),
        organization_ids=frozenset(),
        roles=frozenset(),
        authentication_verified=False,
        development_access=True,
    )
    proposal = _proposal()
    service = ProposalReviewService(MemoryRepository())

    accepted = service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="local-worker"),
        OPENAPI_HASH,
        principal=principal,
    )

    assert accepted.proposal_hash == proposal["proposalHash"]
    with pytest.raises(ValueError, match="AUTHENTICATION_NOT_VERIFIED"):
        principal.assert_can_sign("local-signature")


def test_unverified_non_development_access_is_denied():
    principal = ProposalReviewPrincipal(
        actor_id="unknown-user",
        human_user_id="unknown-user",
        token_id="",
        tenant_id="",
        scoped_study_ids=frozenset(),
        organization_ids=frozenset(),
        roles=frozenset({"Study.Read", "Study.Write"}),
        authentication_verified=False,
    )
    with pytest.raises(ValueError, match="AUTHENTICATION_NOT_VERIFIED"):
        principal.assert_proposal_access("tenant-1", "study-1", "Study.Read")


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
            principal=_service_principal(),
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
            principal=_service_principal(),
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
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
        OPENAPI_HASH,
        principal=_service_principal(),
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
        principal=_principal("reviewer-1", "signature-1"),
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
        principal=_principal("reviewer-2", "signature-2"),
    )
    assert complete.review_complete is True
    assert complete.native_execution_ready is False
    assert complete.execution_blockers[:3] == [
        "OSB_PROPOSAL_AUTHORITY_MODE_NOT_ENFORCED",
        "OSB_EXECUTION_AUTHORIZATION_REQUIRED",
        "OSB_STUDY_OWNERSHIP_UNVERIFIED",
    ]
    assert (
        f"OSB_RELEASE_CREATE_REQUEST_EXECUTOR_UNAVAILABLE:"
        f"{_item_object(proposal)['proposalObjectId']}:OdmItem"
        in complete.release_blockers
    )
    assert {item.capability_kind for item in complete.objects} == {
        "native_study_mutation",
        "governed_library_reference",
    }
    assert all(
        item.latest_decision.signature_verified is True for item in complete.objects
    )
    assert all(
        len(item.latest_decision.decision_content_hash) == 64
        for item in complete.objects
    )
    assert {item.latest_decision.actor_id for item in complete.objects} == {
        "reviewer-1",
        "reviewer-2",
    }


def test_verified_reviewer_can_authorize_exact_owned_draft_and_receipt_goes_stale():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    proposal = _set_authority_mode(_proposal(), "enforced")
    service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
        OPENAPI_HASH,
        principal=_service_principal(),
    )
    activity = _activity_object(proposal)
    item = _item_object(proposal)
    service.decide(
        proposal["proposalHash"],
        activity["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="selected_candidate",
            candidate_key=activity["mapping"]["candidates"][0]["candidateKey"],
            signature_id="decision-token-1",
        ),
        principal=_principal("reviewer-1", "decision-token-1"),
    )
    reviewed = service.decide(
        proposal["proposalHash"],
        item["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="not_applicable",
            note="No ODM mutation is authorized by this native execution",
            signature_id="decision-token-2",
        ),
        principal=_principal("reviewer-1", "decision-token-2"),
    )
    assert reviewed.execution_blockers == [
        "OSB_EXECUTION_AUTHORIZATION_REQUIRED",
        "OSB_STUDY_OWNERSHIP_UNVERIFIED",
    ]
    repository.targets["Study_1"] = {
        "study_uid": "Study_1",
        "version": "DRAFT",
        "status": "DRAFT",
        "owner_id": "reviewer-1",
        "study_value_node_id": "study-value-1",
        "ownership_basis": "initial_version_author",
        "version_start_date": datetime(2026, 8, 10, tzinfo=timezone.utc),
    }

    ready = service.authorize_execution(
        proposal["proposalHash"],
        ProposalExecutionAuthorizationInput(
            target_study_uid="Study_1",
            target_study_version="DRAFT",
            expected_decision_set_hash=reviewed.decision_set_hash,
            signature_id="authorization-token",
        ),
        principal=_principal("reviewer-1", "authorization-token"),
    )

    assert ready.native_execution_ready is True
    assert ready.execution_blockers == []
    assert ready.release_ready is False
    assert any(
        blocker.startswith("OSB_RELEASE_GOVERNED_REFERENCE_NOT_CONSUMED:")
        for blocker in ready.release_blockers
    )
    assert ready.target_study_uid == "Study_1"
    assert ready.target_study_version == "DRAFT"
    assert ready.target_study_value_node_id == "study-value-1"
    assert ready.target_ownership_basis == "initial_version_author"
    assert ready.target_ownership_verified is True
    assert ready.target_snapshot_verified is True
    assert ready.execution_authorization is not None
    assert ready.execution_authorization.signature_verified is True
    assert len(ready.execution_authorization.authorization_content_hash) == 64

    changed = service.decide(
        proposal["proposalHash"],
        activity["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="selected_candidate",
            candidate_key=activity["mapping"]["candidates"][0]["candidateKey"],
            note="Superseding decision invalidates the authorization receipt",
            signature_id="decision-token-3",
        ),
        principal=_principal("reviewer-1", "decision-token-3"),
    )
    assert changed.native_execution_ready is False
    assert "OSB_EXECUTION_AUTHORIZATION_DECISION_SET_STALE" in (
        changed.execution_blockers
    )


def test_signature_and_target_ownership_checks_fail_closed():
    with pytest.raises(ValueError, match="SIGNATURE_TOKEN_MISMATCH"):
        _principal("reviewer-1", "real-token").assert_can_sign("claimed-token")

    repository = MemoryRepository()
    repository.targets["Study_1"] = {
        "study_uid": "Study_1",
        "version": "DRAFT",
        "status": "DRAFT",
        "owner_id": "different-owner",
        "study_value_node_id": "study-value-1",
        "ownership_basis": "initial_version_author",
        "version_start_date": datetime(2026, 8, 10, tzinfo=timezone.utc),
    }
    service = ProposalReviewService(repository)
    proposal = _set_authority_mode(_proposal(), "enforced")
    service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
        OPENAPI_HASH,
        principal=_service_principal(),
    )
    activity = _activity_object(proposal)
    item = _item_object(proposal)
    service.decide(
        proposal["proposalHash"],
        activity["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="selected_candidate",
            candidate_key=activity["mapping"]["candidates"][0]["candidateKey"],
            signature_id="decision-token-1",
        ),
        principal=_principal("reviewer-1", "decision-token-1"),
    )
    reviewed = service.decide(
        proposal["proposalHash"],
        item["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="not_applicable",
            note="No ODM mutation",
            signature_id="decision-token-2",
        ),
        principal=_principal("reviewer-1", "decision-token-2"),
    )
    with pytest.raises(ValueError, match="literal_error"):
        service.authorize_execution(
            proposal["proposalHash"],
            ProposalExecutionAuthorizationInput(
                target_study_uid="Study_1",
                target_study_version="0.1",
                expected_decision_set_hash=reviewed.decision_set_hash,
                signature_id="authorization-token",
            ),
            principal=_principal("reviewer-1", "authorization-token"),
        )
    with pytest.raises(ValueError, match="STUDY_SCOPE_MISMATCH"):
        service.authorize_execution(
            proposal["proposalHash"],
            ProposalExecutionAuthorizationInput(
                target_study_uid="Study_1",
                target_study_version="DRAFT",
                expected_decision_set_hash=reviewed.decision_set_hash,
                signature_id="authorization-token",
            ),
            principal=_principal(
                "reviewer-1",
                "authorization-token",
                scoped_study_ids=frozenset({"study-1"}),
            ),
        )
    with pytest.raises(ValueError, match="NOT_OWNED_BY_REVIEWER"):
        service.authorize_execution(
            proposal["proposalHash"],
            ProposalExecutionAuthorizationInput(
                target_study_uid="Study_1",
                target_study_version="DRAFT",
                expected_decision_set_hash=reviewed.decision_set_hash,
                signature_id="authorization-token",
            ),
            principal=_principal("reviewer-1", "authorization-token"),
        )


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
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
        OPENAPI_HASH,
        principal=_service_principal(),
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
            principal=_service_principal(),
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
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
        OPENAPI_HASH,
        principal=_service_principal(),
    )
    service.decide(
        proposal["proposalHash"],
        activity["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="selected_candidate",
            candidate_key=activity["mapping"]["candidates"][0]["candidateKey"],
            signature_id="signature-1",
        ),
        principal=_principal("reviewer-1", "signature-1"),
    )
    status = service.decide(
        proposal["proposalHash"],
        item["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="create_request",
            note="No existing item",
            signature_id="signature-2",
        ),
        principal=_principal("reviewer-2", "signature-2"),
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
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
        OPENAPI_HASH,
        principal=_service_principal(),
    )

    with pytest.raises(ValueError, match="REASON_REQUIRED"):
        service.decide(
            proposal["proposalHash"],
            _item_object(proposal)["proposalObjectId"],
            ProposalObjectDecisionInput(action="rejected", signature_id="signature-1"),
            principal=_principal("reviewer-1", "signature-1"),
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
        principal=_principal("reviewer-1", "signature-1"),
    )
    rejected = service.decide(
        proposal["proposalHash"],
        _item_object(proposal)["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="rejected",
            note="Not part of the approved study definition",
            signature_id="signature-2",
        ),
        principal=_principal("reviewer-1", "signature-2"),
    )
    assert rejected.review_complete is True
    assert rejected.rejected_object_count == 1
    assert rejected.native_execution_ready is False


def _with_declinable_object(proposal, resource_type, target_key, section):
    """Add one object of a declinable family and re-derive the proposal ids."""
    from clinical_mdr_api.tests.fixtures.osb_proposal_v21 import _object

    added = _object(proposal["sourceBuildHash"], target_key, section, resource_type)
    proposal["sections"][section] = [*proposal["sections"][section], added]
    reconciliation = proposal["reconciliation"]
    reconciliation["proposedObjects"] += 1
    reconciliation["nativeStudyMutationTargets"] += 1
    reconciliation["unresolved"] += 1
    objects = [item for items in proposal["sections"].values() for item in items]
    content = {key: value for key, value in proposal.items() if key != "proposalHash"}
    content["proposalId"] = _canonical_hash(
        {
            "tenantId": content["tenantId"],
            "studyId": content["studyId"],
            "projectId": content["projectId"],
            "authorityMode": content["authorityMode"],
            "sourceBuildHash": content["sourceBuildHash"],
            "osbOpenApiHash": content["osbOpenApiHash"],
            "osbMappingContextHash": content["osbMappingContextHash"],
            "proposalObjects": objects,
            "sourceDispositions": reconciliation.get("dispositions") or [],
        }
    )
    return {**content, "proposalHash": _canonical_hash(content)}, added


def test_a_declined_optional_family_is_a_recorded_deferral_not_a_blocker():
    """IL register GAP-8: StudyCompoundDosing (and the other three attribute
    families) now have an executor, so an undecided one is an executor gap no
    longer; a signed not_applicable decision on one is a deferral on the
    record, while the study's spine keeps its all-or-nothing rule."""
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    proposal, dosing = _with_declinable_object(
        _proposal(), "StudyCompoundDosing", "dosing-regimen", "productsDosing"
    )
    service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
        OPENAPI_HASH,
        principal=_service_principal(),
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
        principal=_principal("reviewer-1", "signature-1"),
    )
    service.decide(
        proposal["proposalHash"],
        _item_object(proposal)["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="create_request", note="Create draft item", signature_id="signature-2"
        ),
        principal=_principal("reviewer-2", "signature-2"),
    )
    complete = service.decide(
        proposal["proposalHash"],
        dosing["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="not_applicable",
            note="Dose stated per arm; no library value to bind yet",
            signature_id="signature-3",
        ),
        principal=_principal("reviewer-3", "signature-3"),
    )
    assert complete.review_complete is True
    dosing_id = dosing["proposalObjectId"]
    assert f"OSB_NATIVE_V2_CREATE_REQUEST_REQUIRED:{dosing_id}" not in complete.execution_blockers
    assert f"OSB_NATIVE_V2_SELECTION_REQUIRED:{dosing_id}" not in complete.execution_blockers
    assert not any(
        blocker.startswith("OSB_RELEASE_NATIVE_FAMILY_EXECUTOR_UNAVAILABLE:")
        and blocker.endswith(":StudyCompoundDosing")
        for blocker in complete.release_blockers
    )


def test_the_spine_keeps_its_all_or_nothing_rule_under_not_applicable():
    repository = MemoryRepository()
    service = ProposalReviewService(repository)
    proposal = _proposal()
    service.intake(
        ProposalReviewIntake(proposal=proposal, worker_id="worker-1"),
        OPENAPI_HASH,
        principal=_service_principal(),
    )
    declined_activity = service.decide(
        proposal["proposalHash"],
        _activity_object(proposal)["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="not_applicable", note="declined", signature_id="signature-1"
        ),
        principal=_principal("reviewer-1", "signature-1"),
    )
    complete = service.decide(
        proposal["proposalHash"],
        _item_object(proposal)["proposalObjectId"],
        ProposalObjectDecisionInput(
            action="create_request", note="Create draft item", signature_id="signature-2"
        ),
        principal=_principal("reviewer-2", "signature-2"),
    )
    assert declined_activity.review_complete is False
    activity_id = _activity_object(proposal)["proposalObjectId"]
    assert f"OSB_NATIVE_V2_SELECTION_REQUIRED:{activity_id}" in complete.execution_blockers
