"""Focused stateful reconciliation tests for the 360i importer."""

# exact == [] / == {} comparisons are the assertion
# tests exercise private helpers directly
# pylint: disable=use-implicit-booleaness-not-comparison,protected-access

from ..mappings import payload_to_osb as mapping
from ..run_import_360i import CARRIER_EPOCH_DESCRIPTION, Import360i, ImportCensus


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


class _UnitApi:
    def __init__(self, rows, total=None):
        self.catalogue = {"items": rows, "total": len(rows) if total is None else total}

    def get_all_from_api_paged(self, path, items_only=True):
        assert path == "/concepts/unit-definitions"
        assert items_only is False
        return self.catalogue

    def simple_post_to_api(self, *_args, **_kwargs):
        raise AssertionError(
            "a carrier must not create or approve native unit identity"
        )


def test_units_require_unique_final_native_identity_without_case_folding_or_creation():
    api = _UnitApi(
        [
            {"uid": "count", "name": "G/L", "status": "Final"},
            {"uid": "mass", "name": "g/L", "status": "Final"},
            {"uid": "draft", "name": "bpm", "status": "Draft"},
            {
                "uid": "heart",
                "name": "beats/min",
                "status": "Final",
                "legacy_code": "bpm",
            },
            {"uid": "resp", "name": "breaths/min", "status": "Final"},
        ]
    )
    importer = _importer(api)
    payload = {
        "odm": {
            "units": ["G/L", "g/L", "bpm", "breaths/min"],
            "unitGovernanceVersion": "measured-unit/1",
            "unitGovernance": [
                {"key": "10^9/L", "spellings": ["G/L"]},
                {"key": "g/L", "spellings": ["g/L"]},
                {"key": "beats/min", "spellings": ["bpm"]},
                {"key": "breaths/min", "spellings": ["breaths/min"]},
            ],
        }
    }
    assert importer.ensure_units(payload) == {
        "G/L": "count",
        "g/L": "mass",
        "bpm": "heart",
        "breaths/min": "resp",
    }
    assert importer.census.created == []
    assert importer.census.stopped == []
    legacy = _importer(api)
    assert legacy.ensure_units({"odm": {"units": ["bpm", "g/l"]}}) == {"bpm": "heart"}
    assert len(legacy.census.stopped) == 1
    assert len(legacy.census.release_blockers) == 1
    assert legacy.census.created == []
    assert (
        _importer(_UnitApi(api.catalogue["items"], total=6)).ensure_units(payload) == {}
    )
    api.catalogue["items"].append(
        {"uid": "other-count", "name": "G/L", "status": "Final"}
    )
    api.catalogue["total"] += 1
    ambiguous = _importer(api)
    assert "G/L" not in ambiguous.ensure_units(payload)
    assert "AMBIGUOUS" in ambiguous.census.stopped[0]["reason"]


def test_aliases_with_two_approved_native_uids_require_review():
    importer = _importer(
        _UnitApi(
            [
                {"uid": "a", "name": "bpm", "status": "Final"},
                {"uid": "b", "name": "beats/min", "status": "Final"},
            ]
        )
    )
    assert (
        importer.ensure_units(
            {
                "odm": {
                    "units": ["bpm", "beats/min"],
                    "unitGovernanceVersion": "measured-unit/1",
                    "unitGovernance": [
                        {"key": "beats/min", "spellings": ["bpm", "beats/min"]}
                    ],
                }
            }
        )
        == {}
    )
    assert "AMBIGUOUS" in importer.census.stopped[0]["reason"]


def test_carrier_keys_and_catalogue_terms_cannot_authorize_native_unit_aliases():
    from copy import deepcopy

    source = {
        "odm": {
            "units": ["mg"],
            "unitGovernanceVersion": "measured-unit/1",
            "unitGovernance": [{"key": "g", "spellings": ["mg"], "catalogueTerm": "g"}],
        }
    }
    before = deepcopy(source)
    importer = _importer(_UnitApi([{"uid": "grams", "name": "g", "status": "Final"}]))
    assert importer.ensure_units(source) == {}
    assert importer.census.stopped[0]["reason"] == "OSB_UNIT_NATIVE_BINDING_REQUIRED"
    assert source == before


def test_each_group_spelling_needs_an_approved_native_alias_witness():
    source = {
        "odm": {
            "units": ["bpm", "BEATS/MIN", "beats/min"],
            "unitGovernanceVersion": "measured-unit/1",
            "unitGovernance": [
                {"key": "beats/min", "spellings": ["bpm", "BEATS/MIN", "beats/min"]}
            ],
        }
    }
    native = {
        "uid": "heart",
        "name": "beats/min",
        "status": "Final",
        "ucum": {"name": "bpm"},
        "ct_units": [{"submission_value": "BEATS/MIN"}],
    }
    assert _importer(_UnitApi([native])).ensure_units(source) == {
        "bpm": "heart",
        "BEATS/MIN": "heart",
        "beats/min": "heart",
    }
    native.pop("ct_units")
    assert (
        _importer(_UnitApi([native])).ensure_units(source) == {}
    ), "one missing alias holds the group"
    native["status"] = "Draft"
    assert (
        _importer(_UnitApi([native])).ensure_units(source) == {}
    ), "source groups cannot approve native drafts"


def test_missing_native_units_stop_before_programme_or_study_writes(monkeypatch):
    import logging
    from types import SimpleNamespace

    from .. import run_import_360i as module

    monkeypatch.setattr(module, "assert_unsafe_legacy_mutation_allowed", lambda _: None)
    importer = _importer(_UnitApi([]))
    importer.log = logging.getLogger(__name__)
    importer.db = SimpleNamespace(
        read_latest_payload=lambda _: {
            "payload": {"odm": {"units": ["not-approved"]}},
            "census": {"unmapped": 0},
            "payload_hash": "p" * 64,
            "build_hash": "b" * 64,
        },
        read_current_crosswalk=lambda _: None,
    )
    importer._finish = lambda *_: {"status": importer.census.status}
    # No programme/study writer is installed on this deliberately minimal worker.
    assert importer.run("study")["status"] == "partial"
    assert importer.census.stopped[0]["reason"] == "OSB_UNIT_NATIVE_BINDING_REQUIRED"


class _ArmProofApi:
    def __init__(self, arms):
        self.arms = arms
        self.elements = []
        self.cells = []
        self.patches = []

    def get_all_from_api(self, path, params=None):
        if path == "/studies":
            return [{"uid": "Study_1"}]
        if path == "/studies/Study_1/study-arms":
            return self.arms
        if path == "/studies/Study_1/study-elements":
            return self.elements
        if path == "/studies/Study_1/study-design-cells":
            return self.cells
        raise AssertionError(f"unexpected GET {path}")

    def get_all_from_api_paged(self, path, items_only=True):
        assert items_only is False
        rows = self.get_all_from_api(path)
        return {"items": rows, "total": len(rows)}

    def patch_to_api(self, body, path):
        assert path == "/studies/Study_1/study-elements"
        self.patches.append(dict(body))
        return dict(body)


def _legacy_arm_worker(names, native_name=None):
    import logging
    from types import SimpleNamespace

    old_ref = names[0][:200]
    uid = "Arm_original"
    payload_hash = "a" * 64
    crosswalk = {
        "osb_study_uid": "Study_1",
        "payload_hash": payload_hash,
        "uid_map": {"arms": {old_ref: uid}},
        "importer_version": "1.17",
        "status": "succeeded",
        "import_id": "prior-import",
    }
    payload = {"arms": [{"name": name} for name in names], "odm": {}}
    importer = _importer(
        _ArmProofApi(
            [
                {
                    "arm_uid": uid,
                    "name": native_name or old_ref,
                    "short_name": names[0][:20],
                    "description": None,
                }
            ]
        )
    )
    importer.log = logging.getLogger(__name__)
    importer.db = SimpleNamespace(
        read_payload=lambda digest, study: {"payload_hash": digest, "payload": payload},
        read_latest_payload=lambda _: {
            "payload_hash": payload_hash,
            "payload": payload,
            "build_hash": "b" * 64,
            "census": {"unmapped": 0},
        },
        read_current_crosswalk=lambda _: crosswalk,
    )
    importer.uid_map["arms"] = dict(crosswalk["uid_map"]["arms"])
    return importer, payload, crosswalk


def test_proven_legacy_arm_rebind_keeps_native_uid_and_patches_full_names():
    full_name = "Arm " + "x" * 220
    worker, payload, crosswalk = _legacy_arm_worker([full_name])
    assert worker._preflight_arm_identity(payload, crosswalk, "study") is True
    assert worker.uid_map["arms"] == {full_name: "Arm_original"}
    assert crosswalk["uid_map"]["arms"] == {full_name[:200]: "Arm_original"}
    diff = mapping.arm_diff(payload, worker._current_arms_by_ref("Study_1"))
    assert diff["create"] == [] and diff["delete"] == []
    assert diff["patch"][0]["uid"] == "Arm_original"
    assert diff["patch"][0]["plan"]["name"] == full_name
    assert diff["patch"][0]["plan"]["short_name"] == full_name
    assert worker.census.carried[0]["source_payload_hash"] == crosswalk["payload_hash"]


def test_ambiguous_or_altered_legacy_arm_stops_run_before_any_native_write(monkeypatch):
    from .. import run_import_360i as module

    monkeypatch.setattr(module, "assert_unsafe_legacy_mutation_allowed", lambda _: None)
    prefix = "Arm " + "x" * 220
    for names, changed_native in [
        ([prefix + " A", prefix + " B"], None),
        ([prefix], "Reviewed native arm"),
    ]:
        worker, _payload, crosswalk = _legacy_arm_worker(names, changed_native)
        worker._finish = lambda *_, current=worker: {"status": current.census.status}
        worker.ensure_programme_and_project = lambda *_: (_ for _ in ()).throw(
            AssertionError("native writes must not start before identity admission")
        )
        assert worker.run("study")["status"] == "partial"
        assert worker.uid_map["arms"] == crosswalk["uid_map"]["arms"]
        assert worker.census.stopped[0]["kind"] == "arm_identity"
        assert worker.census.release_blockers


def test_legacy_arm_rebind_requires_original_payload_hash_and_all_native_rows():
    from types import SimpleNamespace

    full_name = "Arm " + "x" * 220
    for corrupt in ("absent", "hash", "incomplete", "duplicate-uid", "removed"):
        worker, payload, crosswalk = _legacy_arm_worker([full_name])
        if corrupt == "absent":
            worker.db = SimpleNamespace(read_payload=lambda *_: None)
        elif corrupt == "hash":
            worker.db = SimpleNamespace(
                read_payload=lambda *_, retained=payload: {
                    "payload_hash": "bad",
                    "payload": retained,
                }
            )
        elif corrupt == "incomplete":
            worker.api.get_all_from_api_paged = lambda *_args, **_kwargs: {
                "items": [],
                "total": 1,
            }
        elif corrupt == "duplicate-uid":
            worker.api.arms.append(dict(worker.api.arms[0]))
        else:
            payload = {"arms": [], "odm": {}}
        assert worker._preflight_arm_identity(payload, crosswalk, "study") is False
        assert worker.uid_map["arms"] == crosswalk["uid_map"]["arms"]
        assert worker.census.stopped and worker.census.release_blockers


def test_arm_name_collision_stops_run_before_native_reads_or_writes(monkeypatch):
    import logging
    from types import SimpleNamespace

    import pytest

    from .. import run_import_360i as module

    monkeypatch.setattr(module, "assert_unsafe_legacy_mutation_allowed", lambda _: None)
    worker = _importer(None)
    worker.log = logging.getLogger(__name__)
    worker.db = SimpleNamespace(
        read_latest_payload=lambda _: {
            "payload": {"arms": [{"name": "Arm"}, {"name": " Arm "}], "odm": {}}
        }
    )
    with pytest.raises(ValueError, match="OSB_ARM_NATIVE_NAME_COLLISION"):
        worker.run("study")


def test_existing_native_arm_name_or_short_name_collision_stops_before_writes(
    monkeypatch,
):
    from .. import run_import_360i as module

    monkeypatch.setattr(module, "assert_unsafe_legacy_mutation_allowed", lambda _: None)
    for property_name in ("name", "short_name"):
        worker, payload, crosswalk = _legacy_arm_worker(["Arm"])
        worker.api.arms.append({"arm_uid": "Arm_manual", property_name: "Arm"})
        worker._finish = lambda *_, current=worker: {"status": current.census.status}
        worker.ensure_programme_and_project = lambda *_: (_ for _ in ()).throw(
            AssertionError("native writes must not start before uniqueness admission")
        )
        assert worker.run("study")["status"] == "partial"
        assert worker.uid_map["arms"] == crosswalk["uid_map"]["arms"]
        assert worker.census.stopped[0]["reason"] == "OSB_ARM_NATIVE_NAME_ALREADY_BOUND"
        assert payload["arms"] == [{"name": "Arm"}]


def _legacy_element(worker, full_name):
    from ..run_import_360i import SCAFFOLDING_ELEMENT_DESCRIPTION

    worker.api.elements = [
        {
            "element_uid": "Element_original",
            "name": full_name[:200],
            "short_name": full_name[:20],
            "description": SCAFFOLDING_ELEMENT_DESCRIPTION,
            "start_rule": "Native rule that must not be overwritten",
        }
    ]
    worker.api.cells = [
        {
            "study_arm_uid": "Arm_original",
            "study_epoch_uid": "Epoch_original",
            "study_element_uid": "Element_original",
        }
    ]


def test_legacy_element_proof_patches_same_uid_without_replacing_cells():
    full_name = "Arm " + "x" * 220
    for previously_rebound in (False, True):
        worker, payload, crosswalk = _legacy_arm_worker([full_name])
        _legacy_element(worker, full_name)
        if previously_rebound:
            crosswalk["uid_map"]["arms"] = {full_name: "Arm_original"}
            worker.uid_map["arms"] = dict(crosswalk["uid_map"]["arms"])
            worker.api.arms[0].update(name=full_name, short_name=full_name)
        assert worker._preflight_arm_identity(payload, crosswalk, "study") is True
        worker._lookup_final_ct_term = lambda *_: ({"term_uid": "Subtype"}, None)
        worker.ensure_design_structure(payload, "Study_1", ["Epoch_original"])
        assert worker.api.patches == [
            {
                "uid": "Element_original",
                "name": full_name,
                "short_name": full_name,
            }
        ]
        assert worker.api.cells == [
            {
                "study_arm_uid": "Arm_original",
                "study_epoch_uid": "Epoch_original",
                "study_element_uid": "Element_original",
            }
        ]
        assert (
            worker.api.elements[0]["start_rule"]
            == "Native rule that must not be overwritten"
        )
        assert not worker.census.stopped
        assert (
            worker.census.updated[0]["source_payload_hash"] == crosswalk["payload_hash"]
        )


def test_legacy_element_missing_or_ambiguous_proof_refuses_before_writes(monkeypatch):
    from .. import run_import_360i as module

    monkeypatch.setattr(module, "assert_unsafe_legacy_mutation_allowed", lambda _: None)
    full_name = "Arm " + "x" * 220
    for corruption in (
        "duplicate",
        "unowned",
        "missing-cell",
        "wrong-arm",
        "wrong-element",
    ):
        worker, _payload, crosswalk = _legacy_arm_worker([full_name])
        _legacy_element(worker, full_name)
        if corruption == "duplicate":
            worker.api.elements.append(
                {**worker.api.elements[0], "element_uid": "Other"}
            )
        elif corruption == "unowned":
            worker.api.elements[0]["description"] = "Human authored element"
        elif corruption == "missing-cell":
            worker.api.cells = []
        elif corruption == "wrong-arm":
            worker.api.cells[0]["study_arm_uid"] = "Other"
        else:
            worker.api.cells[0]["study_element_uid"] = "Other"
        worker._finish = lambda *_, current=worker: {"status": current.census.status}
        worker.ensure_programme_and_project = lambda *_: (_ for _ in ()).throw(
            AssertionError("no native writes before all arm/element proof succeeds")
        )
        assert worker.run("study")["status"] == "partial"
        assert worker.uid_map["arms"] == crosswalk["uid_map"]["arms"]
        assert worker.api.patches == []
        assert worker.census.release_blockers


def test_proved_element_rename_does_not_require_current_epochs_or_subtype():
    full_name = "Arm " + "x" * 220
    for epochs in ([], ["Epoch_original"]):
        worker, payload, crosswalk = _legacy_arm_worker([full_name])
        _legacy_element(worker, full_name)
        assert worker._preflight_arm_identity(payload, crosswalk, "study") is True
        worker._lookup_final_ct_term = lambda *_: (_ for _ in ()).throw(
            AssertionError("name-only repair must not resolve new subtype authority")
        )
        worker.ensure_design_structure(payload, "Study_1", epochs)
        assert worker.api.patches == [
            {
                "uid": "Element_original",
                "name": full_name,
                "short_name": full_name,
            }
        ]
        assert not worker.census.stopped


def test_element_rename_rechecks_native_uid_and_names_before_patch():
    full_name = "Arm " + "x" * 220
    for field in ("element_uid", "name", "short_name"):
        worker, payload, crosswalk = _legacy_arm_worker([full_name])
        _legacy_element(worker, full_name)
        assert worker._preflight_arm_identity(payload, crosswalk, "study") is True
        worker.api.elements[0][field] = "Changed after preflight"
        worker.ensure_design_structure(payload, "Study_1", [])
        assert worker.api.patches == []
        assert (
            worker.census.stopped[0]["reason"]
            == "OSB_ELEMENT_NATIVE_CHANGED_AFTER_PREFLIGHT"
        )
        assert worker.census.release_blockers


def test_unit_lookup_reads_all_http_pages_and_detects_late_ambiguity():
    import json
    import logging
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlparse

    import pytest

    from ..utils.api_bindings import ApiBinding

    rows = [
        {"uid": f"u{i}", "name": f"unit-{i}", "status": "Final"} for i in range(1001)
    ]
    rows[0]["name"] = rows[-1]["name"] = "G/L"
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # pylint: disable=invalid-name
            path = urlparse(self.path)
            assert path.path == "/concepts/unit-definitions"
            query = parse_qs(path.query)
            page, size = int(query["page_number"][0]), int(query["page_size"][0])
            requests.append((page, size))
            body = json.dumps(
                {
                    "items": rows[(page - 1) * size : page * size],
                    "total": len(rows),
                    "page": page,
                    "size": size,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api = object.__new__(ApiBinding)
    api.api_base_url = f"http://127.0.0.1:{server.server_port}"
    api.api_headers = {}
    api.log = logging.getLogger(__name__)
    api.metrics = SimpleNamespace(icrement=lambda *_: None)
    try:
        with pytest.raises(ValueError, match="OSB_UNIT_NATIVE_BINDING_AMBIGUOUS"):
            _importer(api)._lookup_unit("G/L")
        assert requests == [(1, 1000), (2, 1000)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_group_metadata_and_empty_descriptions_reach_native_import_bodies():
    class CreateApi:
        def __init__(self):
            self.posts = []

        def get_all_from_api(self, _path, params=None):
            return []

        def odm_item_request_body(self, _path, body):
            return body

        def simple_post_to_api(self, path, body, params=None):
            self.posts.append((path, body))
            return {
                "uid": "NativeGroup" if path == "/odms/item-groups" else "NativeForm"
            }

        def simple_approve(self, _path):
            return True

    api = CreateApi()
    worker = _importer(api)
    worker.same_payload_replay = False
    worker.ensure_vendor_namespace = lambda: {"ext": "NativeExtAttribute"}
    extensions = {"subtitle": "", "instructions": "Repeat for each specimen."}
    worker.ensure_odm(
        {
            "source": {"studyId": "source", "buildHash": "hash"},
            "sourceBundle": {},
            "odm": {
                "forms": [
                    {
                        "refKey": "F",
                        "name": "Form",
                        "description": "",
                        "itemGroups": [
                            {
                                "refKey": "G",
                                "name": "Group",
                                "description": "",
                                "orderNumber": 1,
                                "repeating": True,
                                "vendorExtensions": extensions,
                                "items": [],
                            },
                        ],
                    }
                ]
            },
            "visits": [],
            "formVisitMatrix": [],
        },
        "Study_1",
        {},
        {},
    )
    group = next(body for path, body in api.posts if path == "/odms/item-groups")
    form = next(body for path, body in api.posts if path == "/odms/forms")
    assert group["vendor_attributes"] == [
        {
            "uid": "NativeExtAttribute",
            "value": mapping.vendor_ext_value({"vendorExtensions": extensions}),
        }
    ]
    assert group["repeating"] == "yes"
    assert (
        group["translated_texts"][0]["text"]
        == form["translated_texts"][0]["text"]
        == ""
    )
    assert not worker.census.stopped


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

    assert scaffolded is False
    assert by_visit == {}
    assert stale == []
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

    assert by_visit == {}
    assert stale == []


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
                "study_objective": {"study_objective_uid": body["study_objective_uid"]},
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
