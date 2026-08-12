"""Focused stateful reconciliation tests for the 360i importer."""

from ..mappings import payload_to_osb as mapping
from ..run_import_360i import (
    CARRIER_EPOCH_DESCRIPTION,
    Import360i,
    ImportCensus,
)


class _EpochApi:
    def __init__(self, epochs, visits=None):
        self.epochs = epochs
        self.visits = visits or []
        self.posts = []
        self.patches = []

    def get_all_from_api(self, path, params=None):
        if path == "/studies/Study_1/study-epochs":
            return self.epochs
        if path == "/studies/Study_1/study-visits":
            return self.visits
        raise AssertionError(f"unexpected GET {path}")

    def simple_post_to_api(self, path, body, simple_path=None):
        self.posts.append((path, body))
        raise AssertionError("a mapped carrier epoch must not be recreated")

    def patch_to_api(self, body, path):
        self.patches.append((path, body))
        return body


def _importer(api):
    importer = object.__new__(Import360i)
    importer.api = api
    importer.census = ImportCensus()
    importer.uid_map = {
        "epochs": {mapping.CARRIER_EPOCH_NAME: "Epoch_keep"},
        "visits": {},
        "arms": {},
        "forms": {},
        "item_groups": {},
        "items": {},
        "codelists": {},
        "study_events": {},
        "objectives": {},
        "endpoints": {},
        "criteria": {},
        "timeframes": {},
        "native_soa_activities": {},
        "native_soa_schedules": {},
        "native_soa_owned_schedules": {},
    }
    importer._purpose_template_cache = {}
    importer._purpose_timeframe_cache = {}
    return importer


def test_carrier_epoch_uses_ledger_identity_and_retires_old_duplicates():
    epochs = [
        {
            "uid": "Epoch_old_1",
            "epoch_name": "Treatment 1",
            "order": 1,
            "description": CARRIER_EPOCH_DESCRIPTION,
        },
        {
            "uid": "Epoch_old_2",
            "epoch_name": "Treatment 2",
            "order": 2,
            "description": CARRIER_EPOCH_DESCRIPTION,
        },
        {
            "uid": "Epoch_keep",
            "epoch_name": "Treatment 3",
            "order": 1,
            "description": CARRIER_EPOCH_DESCRIPTION,
        },
    ]
    api = _EpochApi(epochs)
    importer = _importer(api)
    payload = {
        "epochs": [],
        "visits": [
            {"refKey": "V1"},
            {"refKey": "V2"},
        ],
    }

    by_visit, scaffolded, stale = importer.ensure_epochs(payload, "Study_1")

    assert scaffolded is True
    assert by_visit == {"V1": "Epoch_keep", "V2": "Epoch_keep"}
    assert {entry["uid"] for entry in stale} == {"Epoch_old_1", "Epoch_old_2"}
    assert api.posts == []
    assert api.patches == []


def test_carrier_epoch_prefers_the_identity_already_used_by_most_visits():
    epochs = [
        {
            "uid": "Epoch_original",
            "epoch_name": "Treatment 1",
            "order": 1,
            "description": CARRIER_EPOCH_DESCRIPTION,
        },
        {
            "uid": "Epoch_latest",
            "epoch_name": "Treatment 2",
            "order": 1,
            "description": CARRIER_EPOCH_DESCRIPTION,
        },
    ]
    visits = [
        {"uid": f"Visit_{index}", "study_epoch_uid": "Epoch_original"}
        for index in range(5)
    ] + [
        {"uid": "Visit_6", "study_epoch_uid": "Epoch_latest"},
        {"uid": "Visit_7", "study_epoch_uid": "Epoch_latest"},
    ]
    api = _EpochApi(epochs, visits)
    importer = _importer(api)
    importer.uid_map["epochs"][mapping.CARRIER_EPOCH_NAME] = "Epoch_latest"
    payload = {
        "epochs": [],
        "visits": [{"refKey": f"V{index}"} for index in range(1, 8)],
    }

    by_visit, _, stale = importer.ensure_epochs(payload, "Study_1")

    assert set(by_visit.values()) == {"Epoch_original"}
    assert stale == [
        {"ref": mapping.CARRIER_EPOCH_NAME, "uid": "Epoch_latest"}
    ]


class _DeferredVisitApi:
    def __init__(self):
        self.deleted = []

    @staticmethod
    def get_all_from_api(path, params=None):
        if path == "/studies/Study_1/study-visits":
            return [{"uid": "Visit_old"}]
        raise AssertionError(f"unexpected GET {path}")

    def simple_delete(self, path, simple_path=None):
        self.deleted.append(path)
        return True


def test_stale_visit_deletion_is_deferred_until_dependents_are_reconciled():
    api = _DeferredVisitApi()
    importer = _importer(api)
    importer.uid_map["visits"] = {"OLD": "Visit_old"}
    importer._lookup_ct_term = lambda *_args: "Term_1"
    importer._lookup_unit = lambda *_args: "Unit_day"

    stale = importer.ensure_visits({"visits": []}, "Study_1", {})

    assert stale == [{"ref": "OLD", "uid": "Visit_old"}]
    assert api.deleted == []
    assert importer.uid_map["visits"] == {"OLD": "Visit_old"}

    importer.remove_stale_visits("Study_1", stale)

    assert api.deleted == ["/studies/Study_1/study-visits/Visit_old"]
    assert importer.uid_map["visits"] == {}


class _PurposeApi:
    codelists = {
        "Objective Level": "CL_OBJECTIVE",
        "Endpoint Level": "CL_ENDPOINT",
        "Criteria Type": "CL_CRITERIA",
    }
    terms = {
        "CL_OBJECTIVE": ("C85826", "Primary Objective"),
        "CL_ENDPOINT": ("C98772", "Primary Outcome Measure"),
        "CL_CRITERIA": ("C25532", "Inclusion Criteria"),
    }

    def __init__(self):
        self.objectives = []
        self.endpoints = []
        self.criteria = []
        self.posts = []
        self.next_template = 1

    def get_all_from_api(self, path, params=None):
        if path == "/ct/codelists/names":
            return [
                {"name": name, "codelist_uid": uid}
                for name, uid in self.codelists.items()
            ]
        if path == "/ct/terms":
            uid, name = self.terms[params["codelist_uid"]]
            return [
                {
                    "term_uid": uid,
                    "name": {"sponsor_preferred_name": name, "status": "Final"},
                    "attributes": {"status": "Final"},
                }
            ]
        if path in (
            "/objective-templates",
            "/endpoint-templates",
            "/criteria-templates",
        ):
            return []
        if path == "/studies/Study_1/study-objectives":
            return self.objectives
        if path == "/studies/Study_1/study-endpoints":
            return self.endpoints
        if path == "/studies/Study_1/study-criteria":
            return self.criteria
        raise AssertionError(f"unexpected GET {path}")

    def simple_post_to_api(self, path, body, simple_path=None, params=None):
        self.posts.append((path, body, params))
        if path.endswith("-templates"):
            uid = f"Template_{self.next_template}"
            self.next_template += 1
            return {"uid": uid}
        if path == "/studies/Study_1/study-objectives":
            row = {
                "study_objective_uid": "StudyObjective_1",
                "objective_level": {"term_uid": body["objective_level_uid"]},
                "objective": {"name_plain": "Compare treatment A with treatment B."},
            }
            self.objectives.append(row)
            return row
        if path == "/studies/Study_1/study-endpoints":
            row = {
                "study_endpoint_uid": "StudyEndpoint_1",
                "study_objective": {
                    "study_objective_uid": body["study_objective_uid"]
                },
                "endpoint_level": {"term_uid": body["endpoint_level_uid"]},
                "endpoint": {"name_plain": "Total insulin used."},
                "timeframe": None,
            }
            self.endpoints.append(row)
            return row
        if path == "/studies/Study_1/study-criteria":
            row = {
                "study_criteria_uid": "StudyCriteria_1",
                "criteria_type": {"term_uid": "C25532"},
                "criteria": {"name_plain": "Adults aged 18 years and older."},
            }
            self.criteria.append(row)
            return row
        raise AssertionError(f"unexpected POST {path}")

    @staticmethod
    def simple_approve(path):
        return True

    @staticmethod
    def simple_delete(path, simple_path=None):
        raise AssertionError(f"same payload replay must not delete {path}")


def _purpose_payload():
    return {
        "studyPurpose": {
            "objectives": [
                {
                    "refKey": "OBJ-P1",
                    "aliasRefKeys": [],
                    "text": "Compare treatment A with treatment B.",
                    "level": "PRIMARY",
                    "sourceAssertionIds": ["objective-a"],
                    "evidence": [],
                }
            ],
            "endpoints": [
                {
                    "refKey": "EP-P1",
                    "aliasRefKeys": [],
                    "objectiveRef": "OBJ-P1",
                    "text": "Total insulin used.",
                    "level": "PRIMARY",
                    "sourceAssertionIds": ["endpoint-a"],
                    "evidence": [],
                }
            ],
            "criteria": [
                {
                    "refKey": "I-01",
                    "aliasRefKeys": [],
                    "text": "Adults aged 18 years and older.",
                    "type": "INCLUSION",
                    "sourceAssertionIds": ["criterion-a"],
                    "evidence": [],
                }
            ],
            "blockers": [],
            "reconciliation": {
                "sourceAssertions": 3,
                "mappedAssertions": 3,
                "blockedAssertions": 0,
                "objectives": 1,
                "endpoints": 1,
                "criteria": 1,
                "balanced": True,
            },
        }
    }


def test_native_study_purpose_is_created_in_dependency_order_and_replays_cleanly():
    api = _PurposeApi()
    importer = _importer(api)

    importer.ensure_study_purpose(_purpose_payload(), "Study_1")

    selection_paths = [path for path, _, _ in api.posts if "/studies/" in path]
    assert selection_paths == [
        "/studies/Study_1/study-objectives",
        "/studies/Study_1/study-endpoints",
        "/studies/Study_1/study-criteria",
    ]
    assert importer.uid_map["objectives"] == {"OBJ-P1": "StudyObjective_1"}
    assert importer.uid_map["endpoints"] == {"EP-P1": "StudyEndpoint_1"}
    assert importer.uid_map["criteria"] == {"I-01": "StudyCriteria_1"}

    first_post_count = len(api.posts)
    importer.ensure_study_purpose(_purpose_payload(), "Study_1")

    assert len(api.posts) == first_post_count
    assert len(api.objectives) == len(api.endpoints) == len(api.criteria) == 1
