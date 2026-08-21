import json
import logging

import pytest
import requests

from ..mappings.proposal_v2_native_operations import native_operation_plan
from ..run_import_osb_proposal_v2 import ImportOsbProposalV2, WorkerScopeError
from ..utils.osb_proposal_db import _stable_hash


class FakeDb:
    def __init__(self, proposal):
        self.tenant_id = "tenant-synthetic"
        self.proposal = proposal
        self.results = []
        self.finishes = []
        self.generations = []
        self.claimed_studies = []
        self.native_successes = []

    def claim_next(self, owner, study_id=None):
        self.claimed_studies.append(("intake", study_id))
        return {
            "outbox_id": "job-1",
            "proposal_hash": self.proposal["proposalHash"],
            "lease_generation": 1,
            "proposal": self.proposal,
        }

    def claim_review_required(self, owner, study_id=None):
        self.claimed_studies.append(("review", study_id))
        return None

    def claim_review_complete(self, owner, study_id=None):
        self.claimed_studies.append(("native", study_id))
        return None

    def renew_lease(self, outbox_id, owner, generation, lease_seconds):
        self.generations.append(("renew", generation))
        return True

    def append_item_result(self, outbox_id, owner, generation, item):
        self.generations.append(("append", generation))
        self.results.append((outbox_id, owner, item))

    def finish(
        self,
        outbox_id,
        owner,
        generation,
        status,
        error=None,
        available_at=None,
    ):
        self.generations.append(("finish", generation))
        self.finishes.append((outbox_id, owner, status, error, available_at))

    def finish_native_execution(
        self,
        outbox_id,
        owner,
        generation,
        native_execution_evidence,
        reconciliation_evidence,
    ):
        self.generations.append(("native-success", generation))
        self.native_successes.append(
            (
                outbox_id,
                owner,
                native_execution_evidence,
                reconciliation_evidence,
            )
        )
        return (
            "succeeded"
            if native_execution_evidence.get("releaseReady") is True
            and not native_execution_evidence.get("releaseBlockers")
            and not native_execution_evidence.get("deferredObjects")
            else "native_partial"
        )


class FakeApi:
    api_base_url = "http://osb/api"
    api_headers = {}


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code
        self.text = content.decode() if isinstance(content, bytes) else str(content)

    def raise_for_status(self):
        return None

    def json(self):
        return json.loads(self.content)


def proposal(openapi_hash):
    return {
        "formatVersion": "osb-proposal/2.1",
        "canonicalizationVersion": "canonical-json/1.0",
        "proposalHash": "proposal-hash",
        "osbOpenApiHash": openapi_hash,
        "osbMappingContextHash": "context-hash",
        "sections": {
            "odm": [
                {
                    "proposalObjectId": "object-1",
                    "mapping": {
                        "factIds": ["fact-1"],
                        "proposedResourceType": "OdmItem",
                        "candidates": [],
                        "selectedCandidate": None,
                        "disposition": "unresolved",
                    },
                }
            ],
        },
        "sourceFactRefs": [{"factId": "fact-1", "factContentHash": "fact-hash"}],
        "reconciliation": {
            "balanced": True,
            "sourceFacts": 1,
            "mappedSourceFacts": 1,
            "proposedObjects": 1,
            "nativeStudyMutationTargets": 0,
            "governedLibraryReferenceTargets": 1,
            "governedExtensionTargets": 0,
            "retainedNarrativeTargets": 0,
            "unresolvedTargets": 0,
            "nativeTargetSourceFacts": 0,
            "fullyNativeTargetSourceFacts": 0,
            "dispositions": [],
        },
    }


def worker(db, api=None):
    value = object.__new__(ImportOsbProposalV2)
    value.db = db
    value.api = api or FakeApi()
    value.worker_id = "worker-1"
    value.study_id = "source-study-synthetic"
    value.target_study_uid = "Study_000999"
    value.target_study_version = "DRAFT"
    value.log = logging.getLogger("test-proposal-worker")
    return value


def test_validated_skeleton_never_marks_unimplemented_native_import_succeeded(
    monkeypatch,
):
    openapi = b'{"openapi":"3.1.0"}'
    db = FakeDb(proposal(_stable_hash(json.loads(openapi))))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(openapi),
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda *_args, **_kwargs: FakeResponse(
            json.dumps(
                {
                    "proposal_hash": "proposal-hash",
                    "decided_object_count": 0,
                }
            ).encode()
        ),
    )

    result = worker(db).run_once()

    assert result["status"] == "review_required"
    assert db.results[0][2]["proposal_object_id"] == "object-1"
    assert db.generations
    assert {generation for _, generation in db.generations} == {1}
    assert db.results[0][2]["code"] == "OSB_PROPOSAL_ACCEPTED_FOR_REVIEW"
    assert db.finishes[-1][2] == "review_required"
    assert db.finishes[-1][3] == "OSB_PROPOSAL_REVIEW_REQUIRED"
    assert all(finish[2] != "succeeded" for finish in db.finishes)


def test_openapi_drift_stops_before_native_planning(monkeypatch):
    db = FakeDb(proposal("expected-hash"))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(b'{"changed":true}'),
    )

    result = worker(db).run_once()

    assert result == {"status": "failed_terminal", "code": "OSB_OPENAPI_HASH_STALE"}
    assert db.results[0][2]["code"] == "OSB_OPENAPI_HASH_STALE"
    assert db.finishes[-1][2] == "failed_terminal"


def test_openapi_network_failure_is_retryable(monkeypatch):
    db = FakeDb(proposal("expected-hash"))

    def fail(*_args, **_kwargs):
        raise requests.ConnectionError("OSB unavailable")

    monkeypatch.setattr(requests, "get", fail)
    with pytest.raises(requests.ConnectionError):
        worker(db).run_once()

    assert db.finishes[-1][2] == "failed_retryable"
    assert db.finishes[-1][4] is not None


def test_requested_study_scopes_intake_and_review_claims():
    class EmptyDb(FakeDb):
        def claim_next(self, owner, study_id=None):
            self.claimed_studies.append(("intake", study_id))
            return None

        def claim_review_required(self, owner, study_id=None):
            self.claimed_studies.append(("review", study_id))
            return None

    db = EmptyDb(proposal("expected-hash"))
    scoped_worker = worker(db)
    scoped_worker.study_id = "study-a"

    assert scoped_worker.run_once() is None
    assert db.claimed_studies == [
        ("intake", "study-a"),
        ("review", "study-a"),
        ("native", "study-a"),
    ]


def test_review_contract_rejection_is_terminal(monkeypatch):
    openapi = b'{"openapi":"3.1.0"}'
    db = FakeDb(proposal(_stable_hash(json.loads(openapi))))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(openapi),
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda *_args, **_kwargs: FakeResponse(b'{"detail":"unknown context"}', 400),
    )

    with pytest.raises(ValueError, match="OSB_PROPOSAL_REVIEW_REJECTED:400"):
        worker(db).run_once()

    assert db.finishes[-1][2] == "failed_terminal"


class FakeReviewDb(FakeDb):
    def __init__(self, proposal, review_job=True):
        super().__init__(proposal)
        self.review_job = review_job

    def claim_next(self, owner, study_id=None):
        self.claimed_studies.append(("intake", study_id))
        return None

    def claim_review_required(self, owner, study_id=None):
        self.claimed_studies.append(("review", study_id))
        if not self.review_job:
            return None
        return {
            "outbox_id": "job-review-1",
            "proposal_hash": self.proposal["proposalHash"],
            "lease_generation": 1,
        }


def test_incomplete_osb_review_is_deferred_without_reposting(monkeypatch):
    db = FakeReviewDb(proposal("openapi"))
    post = pytest.fail
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(
            json.dumps(
                {
                    "proposal_hash": "proposal-hash",
                    "review_complete": False,
                    "decided_object_count": 1,
                }
            ).encode()
        ),
    )

    result = worker(db).run_once()

    assert result["status"] == "review_required"
    assert result["decided_objects"] == 1
    assert db.finishes[-1][2] == "review_required"
    assert db.finishes[-1][4] is not None


def test_completed_osb_review_advances_only_to_review_complete(monkeypatch):
    db = FakeReviewDb(proposal("openapi"))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(
            json.dumps(
                {
                    "proposal_hash": "proposal-hash",
                    "review_complete": True,
                    "decided_object_count": 2,
                    "rejected_object_count": 0,
                    "execution_blockers": ["OSB_NATIVE_V2_EXECUTOR_NOT_AVAILABLE"],
                }
            ).encode()
        ),
    )

    result = worker(db).run_once()

    assert result == {
        "status": "review_complete",
        "proposal_hash": "proposal-hash",
        "execution_blockers": ["OSB_NATIVE_V2_EXECUTOR_NOT_AVAILABLE"],
    }
    assert db.results[-1][2]["code"] == "OSB_PROPOSAL_REVIEW_COMPLETE"
    assert db.finishes[-1][2] == "review_complete"
    assert all(finish[2] != "succeeded" for finish in db.finishes)


def test_reviewer_rejection_is_terminal(monkeypatch):
    db = FakeReviewDb(proposal("openapi"))
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(
            json.dumps(
                {
                    "proposal_hash": "proposal-hash",
                    "review_complete": True,
                    "decided_object_count": 2,
                    "rejected_object_count": 1,
                }
            ).encode()
        ),
    )

    result = worker(db).run_once()

    assert result == {
        "status": "failed_terminal",
        "code": "OSB_PROPOSAL_REVIEW_REJECTED",
    }
    assert db.finishes[-1][2] == "failed_terminal"


def test_lost_heartbeat_fails_closed_before_http_mutation(monkeypatch):
    class LostLeaseDb(FakeDb):
        def renew_lease(self, outbox_id, owner, generation, lease_seconds):
            self.generations.append(("renew-lost", generation))
            return False

    db = LostLeaseDb(proposal("expected-hash"))
    called = []
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: called.append(True))

    with pytest.raises(RuntimeError, match="OSB_PROPOSAL_LEASE_OWNERSHIP_LOST"):
        worker(db).run_once()

    assert called == []
    assert db.generations[0] == ("renew-lost", 1)


def native_activity_proposal():
    activity = {
        "candidateKey": "activity-candidate",
        "resourceType": "Activity",
        "uid": "Activity_1",
    }
    flowchart = {
        "candidateKey": "flowchart-candidate",
        "resourceType": "CTTerm",
        "uid": "FlowchartGroup_1",
        "parentSubmissionValue": "Flowchart Group",
    }
    return {
        "proposalHash": "proposal-hash",
        "studyId": "source-study",
        "sections": {
            "study": [
                {
                    "proposalObjectId": "activity-object",
                    "targetKey": "activity",
                    "dependencyTargetKeys": ["flowchart-group"],
                    "mapping": {
                        "factIds": ["fact-1"],
                        "proposedResourceType": "StudySelectionActivity",
                        "candidates": [activity],
                    },
                },
                {
                    "proposalObjectId": "flowchart-object",
                    "targetKey": "flowchart-group",
                    "dependencyTargetKeys": [],
                    "mapping": {
                        "factIds": ["fact-1"],
                        "proposedResourceType": "CTTerm",
                        "candidates": [flowchart],
                    },
                },
            ]
        },
    }


def native_activity_review(
    *, ready=True, blockers=None, signed=True, proposal_value=None
):
    proposal_value = proposal_value or native_activity_proposal()
    objects = []
    for item in proposal_value["sections"]["study"]:
        candidates = item["mapping"]["candidates"]
        candidate_value = candidates[0] if candidates else None
        objects.append(
            {
                "proposal_object_id": item["proposalObjectId"],
                "candidates": candidates,
                "latest_decision": {
                    "action": (
                        "selected_candidate" if candidate_value else "create_request"
                    ),
                    "candidate_key": (
                        candidate_value["candidateKey"] if candidate_value else None
                    ),
                    "signature_verified": signed,
                },
            }
        )
    decision_set_hash = "d" * 64
    return {
        "proposal_hash": proposal_value["proposalHash"],
        "source_study_id": proposal_value["studyId"],
        "review_complete": True,
        "rejected_object_count": 0,
        "native_execution_ready": ready,
        "execution_blockers": blockers or [],
        "release_ready": True,
        "release_blockers": [],
        "decision_set_hash": decision_set_hash,
        "target_study_uid": "Study_1",
        "target_study_version": "DRAFT",
        "target_study_status": "DRAFT",
        "target_study_owner_id": "reviewer-1",
        "target_ownership_verified": True,
        "execution_authorization": {
            "proposal_hash": proposal_value["proposalHash"],
            "target_study_uid": "Study_1",
            "target_study_version": "DRAFT",
            "target_study_status": "DRAFT",
            "decision_set_hash": decision_set_hash,
            "signature_verified": True,
            "actor_id": "reviewer-1",
            "authorization_content_hash": "a" * 64,
            "target_version_start_date": "2026-08-10T12:00:00+00:00",
        },
        "objects": objects,
    }


def native_epoch_visit_proposal():
    epoch_subtype = {
        "candidateKey": "epoch-subtype-candidate",
        "resourceType": "CTTerm",
        "uid": "EpochSubtype_Treatment",
        "parentSubmissionValue": "Epoch Sub Type",
    }
    visit_type = {
        "candidateKey": "visit-type-candidate",
        "resourceType": "CTTerm",
        "uid": "VisitType_Treatment",
        "parentSubmissionValue": "VisitType",
    }
    contact = {
        "candidateKey": "contact-candidate",
        "resourceType": "CTTerm",
        "uid": "VisitContact_OnSite",
        "parentSubmissionValue": "Visit Contact Mode",
    }
    time_reference = {
        "candidateKey": "time-reference-candidate",
        "resourceType": "CTTerm",
        "uid": "TimeReference_GlobalAnchor",
        "parentSubmissionValue": "Time Point Reference",
    }
    day = {
        "candidateKey": "day-unit-candidate",
        "resourceType": "UnitDefinition",
        "uid": "UnitDefinition_day",
    }
    return {
        "proposalHash": "epoch-visit-proposal-hash",
        "studyId": "source-study",
        "sections": {"study": [
            {
                "proposalObjectId": "epoch-object",
                "targetKey": "study-epoch",
                "dependencyTargetKeys": ["epoch-subtype"],
                "source": {"values": [
                    {"name": "epochId", "value": "epoch-treatment"},
                    {"name": "name", "value": "Treatment Period"},
                    {"name": "order", "value": 1},
                ]},
                "mapping": {
                    "factIds": ["epoch-fact"],
                    "proposedResourceType": "StudyEpoch",
                    "candidates": [],
                },
            },
            {
                "proposalObjectId": "epoch-subtype-object",
                "targetKey": "epoch-subtype",
                "dependencyTargetKeys": [],
                "mapping": {
                    "factIds": ["epoch-fact"],
                    "proposedResourceType": "CTTerm",
                    "candidates": [epoch_subtype],
                },
            },
            {
                "proposalObjectId": "visit-object",
                "targetKey": "study-visit",
                "dependencyTargetKeys": [
                    "visit-type", "visit-contact-mode", "visit-time-reference",
                    "visit-time-unit",
                ],
                "source": {"values": [
                    {"name": "visitId", "value": "visit-day-1"},
                    {"name": "epochId", "value": "epoch-treatment"},
                    {"name": "name", "value": "Day 1"},
                    {"name": "visitClass", "value": "MANUALLY_DEFINED_VISIT"},
                    {"name": "showVisit", "value": True},
                    {"name": "isGlobalAnchorVisit", "value": True},
                    {"name": "sequenceOrder", "value": 1},
                    {"name": "visitName", "value": "Day 1"},
                    {"name": "visitShortName", "value": "Day 1"},
                    {"name": "visitNumber", "value": 1},
                    {"name": "uniqueVisitNumber", "value": 100},
                    {"name": "timeValue", "value": 0},
                    {"name": "nativeTimingReady", "value": True},
                ]},
                "mapping": {
                    "factIds": ["visit-fact"],
                    "proposedResourceType": "StudyVisit",
                    "candidates": [],
                },
            },
            *[
                {
                    "proposalObjectId": f"{target_key}-object",
                    "targetKey": target_key,
                    "dependencyTargetKeys": [],
                    "mapping": {
                        "factIds": ["visit-fact"],
                        "proposedResourceType": resource_type,
                        "candidates": [candidate_value],
                    },
                }
                for target_key, resource_type, candidate_value in [
                    ("visit-type", "CTTerm", visit_type),
                    ("visit-contact-mode", "CTTerm", contact),
                    ("visit-time-reference", "CTTerm", time_reference),
                    ("visit-time-unit", "UnitDefinition", day),
                ]
            ],
        ]},
    }


def native_soa_schedule_proposal():
    value = native_epoch_visit_proposal()
    value["proposalHash"] = "soa-schedule-proposal-hash"
    activity = {
        "candidateKey": "activity-candidate",
        "resourceType": "Activity",
        "uid": "Activity_1",
    }
    flowchart = {
        "candidateKey": "flowchart-candidate",
        "resourceType": "CTTerm",
        "uid": "FlowchartGroup_Safety",
        "parentSubmissionValue": "Flowchart Group",
    }
    value["sections"]["study"].extend([
        {
            "proposalObjectId": "activity-object",
            "targetKey": "soa-activity",
            "dependencyTargetKeys": ["soa-activity-flowchart-group"],
            "source": {"values": [
                {"name": "activityId", "value": "soa-activity-bp"},
                {"name": "name", "value": "Blood pressure"},
            ]},
            "mapping": {
                "factIds": ["activity-fact"],
                "proposedResourceType": "StudySelectionActivity",
                "candidates": [activity],
            },
        },
        {
            "proposalObjectId": "flowchart-object",
            "targetKey": "soa-activity-flowchart-group",
            "dependencyTargetKeys": [],
            "mapping": {
                "factIds": ["activity-fact"],
                "proposedResourceType": "CTTerm",
                "candidates": [flowchart],
            },
        },
        {
            "proposalObjectId": "schedule-object",
            "targetKey": "activity-schedule",
            "dependencyTargetKeys": [],
            "source": {"values": [
                {"name": "scheduleId", "value": "sf-1"},
                {"name": "activityId", "value": "soa-activity-bp"},
                {"name": "visitId", "value": "visit-day-1"},
            ]},
            "mapping": {
                "factIds": ["schedule-fact"],
                "proposedResourceType": "StudyActivitySchedule",
                "candidates": [],
            },
        },
    ])
    return value


def test_worker_refuses_missing_scope_before_claim():
    db = FakeDb(proposal("unused"))
    value = worker(db)
    value.study_id = None

    with pytest.raises(WorkerScopeError, match="SOURCE_STUDY_SCOPE_REQUIRED"):
        value.run_once()

    assert db.claimed_studies == []


def native_template_proposal(family):
    if family == "objective":
        selection_type = "StudySelectionObjective"
        selection_target = "objective-template"
        dependency_target = "objective-level"
        template = {
            "candidateKey": "objective-template-candidate",
            "resourceType": "ObjectiveTemplate",
            "uid": "ObjectiveTemplate_1",
            "parameterCount": 0,
            "libraryName": "Sponsor",
        }
        dependency = {
            "candidateKey": "objective-level-candidate",
            "resourceType": "CTTerm",
            "uid": "ObjectiveLevel_1",
            "parentSubmissionValue": "Objective Level",
        }
    elif family == "criteria":
        selection_type = "StudySelectionCriteria"
        selection_target = "inclusion-criterion"
        dependency_target = "criteria-type"
        template = {
            "candidateKey": "criteria-template-candidate",
            "resourceType": "CriteriaTemplate",
            "uid": "CriteriaTemplate_1",
            "parameterCount": 0,
            "libraryName": "Sponsor",
            "criteriaTypeUid": "CriteriaType_1",
        }
        dependency = {
            "candidateKey": "criteria-type-candidate",
            "resourceType": "CTTerm",
            "uid": "CriteriaType_1",
            "parentSubmissionValue": "Criteria Type",
        }
    elif family == "endpoint":
        selection_type = "StudySelectionEndpoint"
        selection_target = "endpoint-selection"
        dependency_target = "endpoint-level"
        template = {
            "candidateKey": "endpoint-template-candidate",
            "resourceType": "EndpointTemplate",
            "uid": "EndpointTemplate_1",
            "parameterCount": 0,
            "libraryName": "Sponsor",
        }
        dependency = {
            "candidateKey": "endpoint-level-candidate",
            "resourceType": "CTTerm",
            "uid": "EndpointLevel_1",
            "parentSubmissionValue": "Endpoint Level",
        }
    else:
        raise AssertionError(f"unsupported test family {family}")
    return {
        "proposalHash": f"{family}-proposal-hash",
        "studyId": "source-study",
        "sections": {
            "study": [
                {
                    "proposalObjectId": f"{family}-object",
                    "targetKey": selection_target,
                    "dependencyTargetKeys": [dependency_target],
                    "mapping": {
                        "factIds": ["fact-1"],
                        "proposedResourceType": selection_type,
                        "candidates": [template],
                    },
                },
                {
                    "proposalObjectId": f"{family}-dependency-object",
                    "targetKey": dependency_target,
                    "dependencyTargetKeys": [],
                    "mapping": {
                        "factIds": ["fact-1"],
                        "proposedResourceType": "CTTerm",
                        "candidates": [dependency],
                    },
                },
            ]
        },
    }


def native_design_graph_proposal():
    epoch_subtype = {
        "candidateKey": "epoch-subtype-candidate",
        "resourceType": "CTTerm",
        "uid": "EpochSubtype_Treatment",
        "parentSubmissionValue": "Epoch Sub Type",
    }
    return {
        "proposalHash": "design-graph-proposal-hash",
        "studyId": "source-study",
        "sections": {"study": [
            {
                "proposalObjectId": "arm-object",
                "targetKey": "study-arm",
                "dependencyTargetKeys": [],
                "source": {"values": [
                    {"name": "armId", "value": "arm-a"},
                    {"name": "name", "value": "Arm A"},
                ]},
                "mapping": {
                    "factIds": ["arm-fact"],
                    "proposedResourceType": "StudySelectionArm",
                    "candidates": [],
                },
            },
            {
                "proposalObjectId": "element-object",
                "targetKey": "study-element",
                "dependencyTargetKeys": [],
                "source": {"values": [
                    {"name": "elementId", "value": "element-active"},
                    {"name": "name", "value": "Active treatment"},
                ]},
                "mapping": {
                    "factIds": ["element-fact"],
                    "proposedResourceType": "StudySelectionElement",
                    "candidates": [],
                },
            },
            {
                "proposalObjectId": "epoch-object",
                "targetKey": "study-epoch",
                "dependencyTargetKeys": ["epoch-subtype"],
                "source": {"values": [
                    {"name": "epochId", "value": "epoch-treatment"},
                    {"name": "name", "value": "Treatment"},
                    {"name": "order", "value": 1},
                ]},
                "mapping": {
                    "factIds": ["epoch-fact"],
                    "proposedResourceType": "StudyEpoch",
                    "candidates": [],
                },
            },
            {
                "proposalObjectId": "epoch-subtype-object",
                "targetKey": "epoch-subtype",
                "dependencyTargetKeys": [],
                "mapping": {
                    "factIds": ["epoch-fact"],
                    "proposedResourceType": "CTTerm",
                    "candidates": [epoch_subtype],
                },
            },
            {
                "proposalObjectId": "cell-object",
                "targetKey": "study-design-cell",
                "dependencyTargetKeys": [],
                "source": {"values": [
                    {"name": "designCellId", "value": "cell-a-treatment"},
                    {"name": "armId", "value": "arm-a"},
                    {"name": "epochId", "value": "epoch-treatment"},
                    {"name": "elementId", "value": "element-active"},
                    {"name": "order", "value": 1},
                ]},
                "mapping": {
                    "factIds": ["cell-fact"],
                    "proposedResourceType": "StudyDesignCell",
                    "candidates": [],
                },
            },
        ]},
    }


class FakeNativeDb(FakeDb):
    def __init__(self, proposal_value, item_results=None):
        super().__init__(proposal_value)
        self.item_results = list(item_results or [])
        self.native_claimed = False

    def claim_next(self, owner, study_id=None):
        self.claimed_studies.append(("intake", study_id))
        return None

    def claim_review_required(self, owner, study_id=None):
        self.claimed_studies.append(("review", study_id))
        return None

    def claim_review_complete(self, owner, study_id=None):
        self.claimed_studies.append(("native", study_id))
        if self.native_claimed:
            return None
        self.native_claimed = True
        return {
            "outbox_id": "native-job-1",
            "proposal_hash": self.proposal["proposalHash"],
            "study_id": self.proposal["studyId"],
            "lease_generation": 3,
            "proposal": self.proposal,
            "item_results": list(self.item_results),
        }

    def append_item_result(self, outbox_id, owner, generation, item):
        super().append_item_result(outbox_id, owner, generation, item)
        self.item_results.append(item)


class FakeNativeApi:
    api_base_url = "http://osb/api"
    api_headers = {}

    def __init__(
        self,
        activities=None,
        objectives=None,
        endpoints=None,
        criteria=None,
        arms=None,
        elements=None,
        epochs=None,
        design_cells=None,
        visits=None,
        schedules=None,
        retain_post=True,
    ):
        self.activities = list(activities or [])
        self.objectives = list(objectives or [])
        self.endpoints = list(endpoints or [])
        self.criteria = list(criteria or [])
        self.arms = list(arms or [])
        self.elements = list(elements or [])
        self.epochs = list(epochs or [])
        self.design_cells = list(design_cells or [])
        self.visits = list(visits or [])
        self.schedules = list(schedules or [])
        self.retain_post = retain_post
        self.study = {
            "uid": "Study_1",
            "current_metadata": {
                "version_metadata": {
                    "study_status": "DRAFT",
                    "version_number": None,
                    "version_timestamp": "2026-08-10T12:00:00+00:00",
                }
            },
        }
        self.get_calls = []
        self.post_calls = []
        self.patch_calls = []

    def proposal_v2_get(self, path, params=None):
        self.get_calls.append((path, params))
        if path == "/studies/Study_1":
            return self.study
        if path == "/studies/Study_1/study-activities":
            return {"items": list(self.activities)}
        if path == "/studies/Study_1/study-objectives":
            return {"items": list(self.objectives)}
        if path == "/studies/Study_1/study-endpoints":
            return {"items": list(self.endpoints)}
        if path == "/studies/Study_1/study-criteria":
            return {"items": list(self.criteria)}
        if path == "/studies/Study_1/study-arms":
            return {"items": list(self.arms)}
        if path == "/studies/Study_1/study-elements":
            return {"items": list(self.elements)}
        if path == "/studies/Study_1/study-epochs":
            return {"items": list(self.epochs)}
        if path == "/studies/Study_1/study-design-cells":
            return {"items": list(self.design_cells)}
        if path == "/studies/Study_1/study-visits":
            return {"items": list(self.visits)}
        if path == "/studies/Study_1/study-activity-schedules":
            return {"items": list(self.schedules)}
        raise AssertionError(f"unexpected GET {path}")

    def proposal_v2_post(
        self,
        path,
        body,
        params=None,
        *,
        idempotency_key,
        proposal_object_id,
    ):
        self.post_calls.append(
            (path, body, params, idempotency_key, proposal_object_id)
        )
        if path.endswith("/study-activities"):
            record = {
                "study_activity_uid": "StudyActivity_1",
                "activity": {"uid": body["activity_uid"]},
                "study_soa_group": {"soa_group_term_uid": body["soa_group_term_uid"]},
            }
            collection = self.activities
        elif path.endswith("/study-objectives"):
            record = {
                "study_objective_uid": "StudyObjective_1",
                "objective": {
                    "template": {
                        "uid": body["objective_data"]["objective_template_uid"]
                    }
                },
                "objective_level": {"term_uid": body["objective_level_uid"]},
            }
            collection = self.objectives
        elif path.endswith("/study-endpoints"):
            record = {
                "study_endpoint_uid": "StudyEndpoint_1",
                "endpoint": {
                    "template": {
                        "uid": body["endpoint_data"]["endpoint_template_uid"]
                    }
                },
                "endpoint_level": {"term_uid": body["endpoint_level_uid"]},
            }
            if body.get("study_objective_uid"):
                record["study_objective"] = {
                    "study_objective_uid": body["study_objective_uid"]
                }
            if body.get("timeframe_uid"):
                record["timeframe"] = {"uid": body["timeframe_uid"]}
            collection = self.endpoints
        elif path.endswith("/study-criteria"):
            record = {
                "study_criteria_uid": "StudyCriteria_1",
                "criteria": {
                    "template": {"uid": body["criteria_data"]["criteria_template_uid"]}
                },
                "criteria_type": {"term_uid": "CriteriaType_1"},
            }
            collection = self.criteria
        elif path.endswith("/study-arms"):
            record = {
                "arm_uid": "StudyArm_1",
                **body,
            }
            if body.get("arm_type_uid"):
                record["arm_type"] = {"term_uid": body["arm_type_uid"]}
            collection = self.arms
        elif path.endswith("/study-elements"):
            record = {
                "element_uid": "StudyElement_1",
                **body,
            }
            if body.get("element_subtype_uid"):
                record["element_subtype"] = {
                    "term_uid": body["element_subtype_uid"]
                }
            collection = self.elements
        elif path.endswith("/study-epochs"):
            record = {
                "uid": "StudyEpoch_1",
                "study_uid": body["study_uid"],
                "epoch_subtype_ctterm": {"term_uid": body["epoch_subtype"]},
                "order": body.get("order"),
                "description": body["description"],
            }
            for key in ("start_rule", "end_rule", "duration", "duration_unit"):
                if key in body:
                    record[key] = body[key]
            collection = self.epochs
        elif path.endswith("/study-design-cells"):
            record = {
                "design_cell_uid": "StudyDesignCell_1",
                **body,
            }
            collection = self.design_cells
        elif path.endswith("/study-visits"):
            record = {
                "uid": "StudyVisit_1",
                "study_epoch_uid": body["study_epoch_uid"],
                "visit_type": body["visit_type"],
                "show_visit": body["show_visit"],
                "description": body["description"],
                "visit_contact_mode": body["visit_contact_mode"],
                "visit_class": body["visit_class"],
                "is_global_anchor_visit": body["is_global_anchor_visit"],
                "time_reference": body.get("time_reference"),
                "time_value": body.get("time_value"),
                "time_unit_uid": body.get("time_unit_uid"),
                "visit_name": body.get("visit_name"),
                "visit_short_name": body.get("visit_short_name"),
                "visit_number": body.get("visit_number"),
                "unique_visit_number": body.get("unique_visit_number"),
            }
            collection = self.visits
        elif path.endswith("/study-activity-schedules"):
            record = {
                "study_activity_schedule_uid": "StudyActivitySchedule_1",
                "study_activity_uid": body["study_activity_uid"],
                "study_visit_uid": body["study_visit_uid"],
            }
            collection = self.schedules
        else:
            raise AssertionError(f"unexpected POST {path}")
        if self.retain_post:
            collection.append(record)
        return record

    def proposal_v2_patch(
        self,
        path,
        body,
        params=None,
        *,
        idempotency_key,
        proposal_object_id,
    ):
        assert path == "/studies/Study_1"
        self.patch_calls.append(
            (path, body, params, idempotency_key, proposal_object_id)
        )

        def merge(target, update):
            for key, value in update.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = value

        merge(self.study, body)
        return self.study


def native_worker(db, api):
    value = worker(db, api=api)
    value.study_id = "source-study"
    value.target_study_uid = "Study_1"
    value.target_study_version = "DRAFT"
    return value


def install_review_response(monkeypatch, review):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(json.dumps(review).encode()),
    )


def test_reviewed_native_activity_is_written_receipted_and_reconciled(monkeypatch):
    proposal_value = native_activity_proposal()
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, native_activity_review())

    result = native_worker(db, api).run_once()

    assert result == {
        "status": "succeeded",
        "proposal_hash": "proposal-hash",
        "target_study_uid": "Study_1",
        "planned_operations": 1,
        "reconciled_operations": 1,
    }
    assert len(api.post_calls) == 1
    assert api.post_calls[0][0] == "/studies/Study_1/study-activities"
    receipts = [item for _, _, item in db.results if item["kind"] == "native_operation"]
    assert len(receipts) == 1
    assert receipts[0]["status"] == "reconciled"
    assert receipts[0]["native_uid"] == "StudyActivity_1"
    assert len(db.native_successes) == 1
    native_evidence = db.native_successes[0][2]
    reconciliation_evidence = db.native_successes[0][3]
    assert native_evidence["contentHash"]
    assert reconciliation_evidence["contentHash"]
    assert reconciliation_evidence["allReconciled"] is True


def test_reconciled_native_subset_is_reported_partial_when_release_is_blocked(
    monkeypatch,
):
    proposal_value = native_activity_proposal()
    review = native_activity_review()
    review["release_ready"] = False
    review["release_blockers"] = ["OSB_RELEASE_EXTENSION_OBJECTS_PRESENT"]
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "native_partial"
    assert result["planned_operations"] == 1
    assert db.native_successes[0][2]["releaseReady"] is False
    assert db.native_successes[0][2]["releaseBlockers"] == [
        "OSB_RELEASE_EXTENSION_OBJECTS_PRESENT"
    ]


@pytest.mark.parametrize(
    ("family", "expected_path", "expected_receipt_family"),
    [
        (
            "objective",
            "/studies/Study_1/study-objectives",
            "StudySelectionObjective",
        ),
        (
            "criteria",
            "/studies/Study_1/study-criteria",
            "StudySelectionCriteria",
        ),
    ],
)
def test_reviewed_template_selection_families_use_typed_api_and_reconcile(
    monkeypatch, family, expected_path, expected_receipt_family
):
    proposal_value = native_template_proposal(family)
    review = native_activity_review(proposal_value=proposal_value)
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "succeeded"
    assert result["planned_operations"] == 1
    assert api.post_calls[0][0] == expected_path
    receipts = [item for _, _, item in db.results if item["kind"] == "native_operation"]
    assert receipts[0]["family"] == expected_receipt_family
    assert receipts[0]["status"] == "reconciled"
    assert db.native_successes[0][3]["allReconciled"] is True


def test_endpoint_resolves_objective_uid_from_prior_native_receipt(monkeypatch):
    proposal_value = native_template_proposal("objective")
    proposal_value["proposalHash"] = "objective-endpoint-proposal-hash"
    objective = proposal_value["sections"]["study"][0]
    objective["source"] = {
        "values": [{"name": "objectiveId", "value": "OBJ-1"}]
    }
    endpoint_template = {
        "candidateKey": "endpoint-template-candidate",
        "resourceType": "EndpointTemplate",
        "uid": "EndpointTemplate_1",
        "parameterCount": 0,
        "libraryName": "Sponsor",
    }
    endpoint_level = {
        "candidateKey": "endpoint-level-candidate",
        "resourceType": "CTTerm",
        "uid": "EndpointLevel_1",
        "parentSubmissionValue": "Endpoint Level",
    }
    proposal_value["sections"]["study"].extend(
        [
            {
                "proposalObjectId": "endpoint-object",
                "targetKey": "endpoint-selection",
                "dependencyTargetKeys": ["endpoint-level"],
                "source": {
                    "values": [{"name": "objectiveId", "value": "OBJ-1"}]
                },
                "mapping": {
                    "factIds": ["endpoint-fact"],
                    "proposedResourceType": "StudySelectionEndpoint",
                    "candidates": [endpoint_template],
                },
            },
            {
                "proposalObjectId": "endpoint-level-object",
                "targetKey": "endpoint-level",
                "dependencyTargetKeys": [],
                "mapping": {
                    "factIds": ["endpoint-fact"],
                    "proposedResourceType": "CTTerm",
                    "candidates": [endpoint_level],
                },
            },
        ]
    )
    review = native_activity_review(proposal_value=proposal_value)
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "succeeded"
    assert result["planned_operations"] == 2
    endpoint_call = next(call for call in api.post_calls if call[0].endswith("study-endpoints"))
    assert endpoint_call[1]["study_objective_uid"] == "StudyObjective_1"
    assert api.endpoints[0]["study_objective"] == {
        "study_objective_uid": "StudyObjective_1"
    }
    assert db.native_successes[0][3]["allReconciled"] is True


def test_visit_resolves_epoch_uid_from_prior_native_receipt(monkeypatch):
    proposal_value = native_epoch_visit_proposal()
    review = native_activity_review(proposal_value=proposal_value)
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "succeeded"
    assert result["planned_operations"] == 2
    assert [call[0] for call in api.post_calls] == [
        "/studies/Study_1/study-epochs",
        "/studies/Study_1/study-visits",
    ]
    assert api.post_calls[1][1]["study_epoch_uid"] == "StudyEpoch_1"
    assert api.visits[0]["study_epoch_uid"] == "StudyEpoch_1"
    receipts = [
        item for _, _, item in db.results if item["kind"] == "native_operation"
    ]
    assert [(item["family"], item["native_uid"]) for item in receipts] == [
        ("StudyEpoch", "StudyEpoch_1"),
        ("StudyVisit", "StudyVisit_1"),
    ]
    assert {item["native_record_hash_scope"] for item in receipts} == {"match"}
    assert db.native_successes[0][3]["allReconciled"] is True


def test_soa_schedule_resolves_activity_and_visit_uids_and_persists_relationship(
    monkeypatch,
):
    proposal_value = native_soa_schedule_proposal()
    review = native_activity_review(proposal_value=proposal_value)
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "succeeded"
    assert result["planned_operations"] == 4
    assert [call[0] for call in api.post_calls] == [
        "/studies/Study_1/study-epochs",
        "/studies/Study_1/study-activities",
        "/studies/Study_1/study-visits",
        "/studies/Study_1/study-activity-schedules",
    ]
    assert api.post_calls[-1][1] == {
        "study_activity_uid": "StudyActivity_1",
        "study_visit_uid": "StudyVisit_1",
    }
    assert api.schedules == [{
        "study_activity_schedule_uid": "StudyActivitySchedule_1",
        "study_activity_uid": "StudyActivity_1",
        "study_visit_uid": "StudyVisit_1",
    }]
    receipts = [
        item for _, _, item in db.results if item["kind"] == "native_operation"
    ]
    assert [(item["family"], item["native_uid"]) for item in receipts] == [
        ("StudyEpoch", "StudyEpoch_1"),
        ("StudySelectionActivity", "StudyActivity_1"),
        ("StudyVisit", "StudyVisit_1"),
        ("StudyActivitySchedule", "StudyActivitySchedule_1"),
    ]
    assert receipts[-1]["native_record_hash_scope"] == "match"
    assert db.native_successes[0][3]["allReconciled"] is True


def test_design_cell_resolves_arm_epoch_and_element_uids_from_native_receipts(
    monkeypatch,
):
    proposal_value = native_design_graph_proposal()
    review = native_activity_review(proposal_value=proposal_value)
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "succeeded"
    assert result["planned_operations"] == 4
    assert [call[0] for call in api.post_calls] == [
        "/studies/Study_1/study-arms",
        "/studies/Study_1/study-elements",
        "/studies/Study_1/study-epochs",
        "/studies/Study_1/study-design-cells",
    ]
    assert api.post_calls[-1][1] == {
        "order": 1,
        "study_arm_uid": "StudyArm_1",
        "study_epoch_uid": "StudyEpoch_1",
        "study_element_uid": "StudyElement_1",
    }
    assert api.design_cells[0]["study_arm_uid"] == "StudyArm_1"
    assert api.design_cells[0]["study_epoch_uid"] == "StudyEpoch_1"
    assert api.design_cells[0]["study_element_uid"] == "StudyElement_1"
    receipts = [
        item for _, _, item in db.results if item["kind"] == "native_operation"
    ]
    assert [(item["family"], item["native_uid"]) for item in receipts] == [
        ("StudySelectionArm", "StudyArm_1"),
        ("StudySelectionElement", "StudyElement_1"),
        ("StudyEpoch", "StudyEpoch_1"),
        ("StudyDesignCell", "StudyDesignCell_1"),
    ]
    assert db.native_successes[0][3]["allReconciled"] is True


def test_reviewed_metadata_create_request_patches_and_reconciles_single_study(
    monkeypatch,
):
    placeholder = {
        "candidateKey": "placeholder",
        "resourceType": "Unused",
        "uid": "Unused_1",
    }
    proposal_value = {
        "proposalHash": "metadata-proposal-hash",
        "studyId": "source-study",
        "sections": {
            "study": [
                {
                    "proposalObjectId": "metadata-object",
                    "targetKey": "study-metadata:study_population.number_of_expected_subjects",
                    "dependencyTargetKeys": [],
                    "source": {
                        "values": [
                            {
                                "name": "numericValue",
                                "sourcePath": "/numericValue",
                                "valueType": "number",
                                "value": 120,
                            }
                        ]
                    },
                    "mapping": {
                        "factIds": ["fact-1"],
                        "proposedResourceType": "StudyMetadata",
                        "candidates": [placeholder],
                    },
                }
            ]
        },
    }
    review = native_activity_review(proposal_value=proposal_value)
    proposal_value["sections"]["study"][0]["mapping"]["candidates"] = []
    review["objects"][0]["candidates"] = []
    review["objects"][0]["latest_decision"] = {
        "action": "create_request",
        "candidate_key": None,
        "signature_verified": True,
    }
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "succeeded"
    assert result["planned_operations"] == 1
    assert api.post_calls == []
    assert len(api.patch_calls) == 1
    assert api.study["current_metadata"]["study_population"] == {
        "number_of_expected_subjects": 120
    }
    receipts = [item for _, _, item in db.results if item["kind"] == "native_operation"]
    assert receipts[0]["family"] == "StudyMetadata"
    assert receipts[0]["native_uid"] == "Study_1"
    assert receipts[0]["status"] == "reconciled"


def test_multiple_metadata_patches_reconcile_by_field_projection(monkeypatch):
    placeholder = {
        "candidateKey": "placeholder",
        "resourceType": "Unused",
        "uid": "Unused_1",
    }
    metadata_objects = [
        {
            "proposalObjectId": "metadata-subject-count",
            "targetKey": "study-metadata:study_population.number_of_expected_subjects",
            "dependencyTargetKeys": [],
            "source": {
                "values": [
                    {
                        "name": "numericValue",
                        "sourcePath": "/numericValue",
                        "valueType": "number",
                        "value": 120,
                    }
                ]
            },
            "mapping": {
                "factIds": ["fact-count"],
                "proposedResourceType": "StudyMetadata",
                "candidates": [placeholder],
            },
        },
        {
            "proposalObjectId": "metadata-title",
            "targetKey": "study-metadata:study_description.study_title",
            "dependencyTargetKeys": [],
            "source": {
                "values": [
                    {
                        "name": "value",
                        "sourcePath": "/title",
                        "valueType": "string",
                        "value": "A governed study title",
                    }
                ]
            },
            "mapping": {
                "factIds": ["fact-title"],
                "proposedResourceType": "StudyMetadata",
                "candidates": [placeholder],
            },
        },
    ]
    proposal_value = {
        "proposalHash": "multi-metadata-proposal-hash",
        "studyId": "source-study",
        "sections": {"study": metadata_objects},
    }
    review = native_activity_review(proposal_value=proposal_value)
    for proposal_object, review_object in zip(metadata_objects, review["objects"]):
        proposal_object["mapping"]["candidates"] = []
        review_object["candidates"] = []
        review_object["latest_decision"] = {
            "action": "create_request",
            "candidate_key": None,
            "signature_verified": True,
        }
    db = FakeNativeDb(proposal_value)
    api = FakeNativeApi()
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "succeeded"
    assert result["planned_operations"] == 2
    assert len(api.patch_calls) == 2
    assert api.study["current_metadata"]["study_population"] == {
        "number_of_expected_subjects": 120
    }
    assert api.study["current_metadata"]["study_description"] == {
        "study_title": "A governed study title"
    }
    receipts = [item for _, _, item in db.results if item["kind"] == "native_operation"]
    assert len(receipts) == 2
    assert {receipt["native_record_hash_scope"] for receipt in receipts} == {"match"}
    assert db.native_successes[0][3]["allReconciled"] is True


def test_native_write_without_read_back_cannot_report_succeeded(monkeypatch):
    db = FakeNativeDb(native_activity_proposal())
    api = FakeNativeApi(retain_post=False)
    install_review_response(monkeypatch, native_activity_review())

    with pytest.raises(RuntimeError, match="OSB_NATIVE_V2_READ_BACK_MISSING"):
        native_worker(db, api).run_once()

    assert len(api.post_calls) == 1
    assert db.native_successes == []
    assert db.finishes[-1][2] == "failed_retryable"
    assert all(finish[2] != "succeeded" for finish in db.finishes)
    object_failures = [
        item
        for _, _, item in db.results
        if item.get("kind") == "native_operation" and item.get("status") == "failed"
    ]
    assert object_failures[0]["proposal_object_id"] == "activity-object"
    assert object_failures[0]["idempotency_key"]
    assert db.results[-1][2]["code"] == "OSB_NATIVE_V2_EXECUTION_RETRYABLE"


def test_retry_with_persisted_receipt_reconciles_without_duplicate_post(monkeypatch):
    proposal_value = native_activity_proposal()
    review = native_activity_review()
    operation = native_operation_plan(proposal_value, review, "Study_1", "DRAFT")[
        "operations"
    ][0]
    record = {
        "study_activity_uid": "StudyActivity_1",
        "activity": {"uid": "Activity_1"},
        "study_soa_group": {"soa_group_term_uid": "FlowchartGroup_1"},
    }
    prior_receipt = {
        "kind": "native_operation",
        "status": "reconciled",
        "code": "OSB_NATIVE_V2_OPERATION_RECONCILED",
        "proposal_hash": "proposal-hash",
        "proposal_object_id": "activity-object",
        "family": "StudySelectionActivity",
        "idempotency_key": operation["idempotency_key"],
        "target_study_uid": "Study_1",
        "target_study_version": "DRAFT",
        "authorization_content_hash": "a" * 64,
        "native_record_hash_scope": "record",
        "native_record_hash": _stable_hash(record),
    }
    db = FakeNativeDb(proposal_value, item_results=[prior_receipt])
    api = FakeNativeApi(activities=[record])
    install_review_response(monkeypatch, review)

    result = native_worker(db, api).run_once()

    assert result["status"] == "succeeded"
    assert api.post_calls == []
    assert len(api.activities) == 1
    assert db.native_successes[0][2]["receipts"] == [prior_receipt]


def test_authority_blockers_leave_job_review_complete_without_api_mutation(
    monkeypatch,
):
    db = FakeNativeDb(native_activity_proposal())
    api = FakeNativeApi()
    install_review_response(
        monkeypatch,
        native_activity_review(
            ready=False,
            blockers=["OSB_REVIEW_SIGNATURE_VERIFICATION_UNAVAILABLE"],
        ),
    )

    result = native_worker(db, api).run_once()

    assert result["status"] == "review_complete"
    assert "AUTHORITY_NOT_READY" in result["execution_blockers"][0]["code"]
    assert api.post_calls == []
    assert db.native_successes == []
    assert db.finishes[-1][2] == "review_complete"


def test_ready_flag_cannot_bypass_unverified_object_signature(monkeypatch):
    db = FakeNativeDb(native_activity_proposal())
    api = FakeNativeApi()
    install_review_response(
        monkeypatch,
        native_activity_review(ready=True, signed=False),
    )

    result = native_worker(db, api).run_once()

    assert result["status"] == "review_complete"
    assert "SIGNATURE_NOT_VERIFIED" in result["execution_blockers"][0]["code"]
    assert api.post_calls == []
    assert db.native_successes == []
