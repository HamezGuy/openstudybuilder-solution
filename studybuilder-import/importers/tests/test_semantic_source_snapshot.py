"""Complete source retention is independent of native clinical mapping holds."""
import base64
from copy import deepcopy
import gzip
import hashlib
import json
import random
import string
import unittest
from unittest.mock import Mock, patch

from importers.run_import_360i import Import360i, ImportCensus, SOURCE_SNAPSHOT_OID_PREFIX
from importers.mappings import payload_to_osb as mapping
from importers.utils.ecrf_platform_db import IMPORTER_VERSION


ATTRS = {name: f"attr-{name}" for name in ("refKey", "studyId", "buildHash", "ext", "bundleMeta")}


class SnapshotApi:
    def __init__(self):
        self.forms = {}
        self.posts = []
        self.patches = []
        self.corrupt_new_chunks = False

    def get_all_from_api(self, path, params=None):
        assert path == "/odms/forms"
        oid = json.loads(params["filters"])["oid"]["v"][0]
        return [deepcopy(self.forms[oid])] if oid in self.forms else []

    def simple_post_to_api(self, path, body):
        assert path == "/odms/forms"
        assert body["oid"].startswith(SOURCE_SNAPSHOT_OID_PREFIX)
        self.posts.append(deepcopy(body))
        record = {**deepcopy(body), "uid": f"Form_{len(self.posts)}", "status": "Draft", "item_groups": []}
        for attr in record["vendor_attributes"]:
            attr["name"] = next(key for key, uid in ATTRS.items() if uid == attr["uid"])
            if attr["name"] == "bundleMeta" and self.corrupt_new_chunks:
                attr["value"] = attr["value"][:-1]
        self.forms[body["oid"]] = record
        return deepcopy(record)

    def patch_to_api(self, body, path):
        assert path == "/odms/forms"
        self.patches.append(deepcopy(body))
        existing = self.forms[body["oid"]]
        self.forms[body["oid"]] = {**existing, **deepcopy(body)}
        return deepcopy(self.forms[body["oid"]])


def worker(api):
    importer = object.__new__(Import360i)
    importer.api = api
    importer.census = ImportCensus()
    importer.uid_map = {"forms": {}}
    importer.ensure_vendor_namespace = lambda: ATTRS
    return importer


def payload(large=False):
    text = "  Full semantic text\nwith qualifiers, 😀 and 0/false/null.  "
    if large:
        text += "".join(random.Random(42).choices(string.ascii_letters + string.digits, k=450000))
    return {"source": {"studyId": "semantic-study", "buildHash": "sha256:" + "a" * 64},
            "odm": {"forms": [{"refKey": "F_1", "itemGroups": [{"refKey": "G_1", "items": [
                {"refKey": "FIELD", "datatype": "text", "name": "Missing native maximum length"}]}]}]},
            "sourceBundle": {"study": {"name": "Semantic study"}, "visits": [{"refKey": "V_1", "metadata": False}],
                "forms": {"forms": [{"refKey": f"F_{index}", "fields": [
                    {"refKey": "FIELD", "label": "exact", "future": {"empty": "", "zero": 0, "nullable": None}}]} for index in range(14)]},
                "_sourceEvidence": {"fullText": text}},
            "sourceCustody": {"payload": {"original": text, "associations": [{"resolved": False}]}}}


def metadata(form):
    attrs = {attr.get("name") or next(key for key, uid in ATTRS.items() if uid == attr["uid"]): attr["value"]
             for attr in form["vendor_attributes"]}
    return attrs, json.loads(attrs["ext"])["semanticSourceSnapshot"]


def read_snapshot(api):
    _attrs, head = metadata(api.forms["F.SEMANTIC.SNAPSHOT.HEAD.Study_1"])
    chunks = []
    for index in range(1, head["chunkCount"] + 1):
        generation_hash = head.get("generationHash", head["snapshotHash"])
        attrs, descriptor = metadata(api.forms[f"F.SEMANTIC.SNAPSHOT.Study_1.{generation_hash}.{index:04d}"])
        assert descriptor["index"] == index
        chunks.append(attrs["bundleMeta"])
    encoded = "".join(chunks)
    raw = gzip.decompress(base64.b64decode(encoded[len("gzip+base64:"):])).decode() if encoded.startswith("gzip+base64:") else encoded
    return json.loads(raw)


class SemanticSourceSnapshotTests(unittest.TestCase):
    def test_complete_large_source_survives_all_clinical_form_holds(self):
        api, source = SnapshotApi(), payload(large=True)
        importer = worker(api)
        self.assertTrue(importer.ensure_source_snapshot(source, "Study_1"))
        self.assertEqual(read_snapshot(api), {**source["sourceBundle"], "semanticSourceCustody": source["sourceCustody"]})
        self.assertEqual(importer.uid_map["forms"], {})
        self.assertGreater(len(api.forms), 2)
        self.assertTrue(all(form["status"] == "Draft" and form["item_groups"] == [] for form in api.forms.values()))
        with self.assertRaisesRegex(ValueError, "OSB_CAPTURE_TEXT_LENGTH_AUTHORITY_REQUIRED"):
            mapping.odm_item_body(source["odm"]["forms"][0]["itemGroups"][0]["items"][0], {}, {})

    def test_replay_is_idempotent_and_head_commits_only_after_all_chunks_verify(self):
        api, source = SnapshotApi(), payload()
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        writes = len(api.posts)
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        self.assertEqual(len(api.posts), writes)
        self.assertEqual(api.patches, [])
        source["sourceBundle"]["study"]["name"] = "New semantic reading"
        api.corrupt_new_chunks = True
        self.assertFalse(worker(api).ensure_source_snapshot(source, "Study_1"))
        self.assertEqual(read_snapshot(api)["study"]["name"], "Semantic study")
        self.assertEqual(api.patches, [])

    def test_new_verified_generation_replaces_head_and_keeps_previous_full_snapshot(self):
        api, source = SnapshotApi(), payload()
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        old_forms = set(api.forms)
        source["sourceBundle"]["study"]["name"] = "Revised"
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        self.assertEqual(len(api.patches), 1)
        self.assertTrue(old_forms.issubset(api.forms))
        self.assertEqual(read_snapshot(api)["study"]["name"], "Revised")

    def test_conflicting_custody_or_unavailable_attributes_never_commit(self):
        api, source = SnapshotApi(), payload()
        source["sourceBundle"]["semanticSourceCustody"] = {"different": True}
        self.assertFalse(worker(api).ensure_source_snapshot(source, "Study_1"))
        self.assertEqual(api.forms, {})
        importer = worker(api)
        importer.ensure_vendor_namespace = lambda: {}
        self.assertFalse(importer.ensure_source_snapshot(payload(), "Study_1"))
        self.assertEqual(api.forms, {})

    def test_existing_head_cannot_be_adopted_by_another_semantic_study(self):
        api, source = SnapshotApi(), payload()
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        count = len(api.posts)
        source["source"]["studyId"] = "other-semantic-study"
        self.assertFalse(worker(api).ensure_source_snapshot(source, "Study_1"))
        self.assertEqual(len(api.posts), count)
        self.assertEqual(api.patches, [])

    def test_conflicting_existing_head_stamps_refuse_before_any_new_carrier_write(self):
        for name in ("studyId", "buildHash"):
            with self.subTest(name=name):
                api, source = SnapshotApi(), payload()
                self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
                head = api.forms["F.SEMANTIC.SNAPSHOT.HEAD.Study_1"]
                next(attr for attr in head["vendor_attributes"] if attr["name"] == name)["value"] = "foreign-reading"
                before, posts = deepcopy(api.forms), len(api.posts)
                source["sourceBundle"]["study"]["name"] = "Next reading"
                importer = worker(api)
                self.assertFalse(importer.ensure_source_snapshot(source, "Study_1"))
                self.assertEqual(api.forms, before)
                self.assertEqual(len(api.posts), posts)
                self.assertEqual(api.patches, [])

    def test_nonfinite_source_is_refused_before_storage_without_rewriting_clinical_values(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                api, source = SnapshotApi(), payload()
                source["sourceBundle"]["fields"] = {"dose": value}
                self.assertFalse(worker(api).ensure_source_snapshot(source, "Study_1"))
                self.assertEqual(api.forms, {})

    def test_advancing_head_keeps_independent_native_vendor_metadata(self):
        api, source = SnapshotApi(), payload()
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        head = api.forms["F.SEMANTIC.SNAPSHOT.HEAD.Study_1"]
        head["vendor_attributes"].append({"uid": "independent-note", "name": "auditNote", "value": "Full native note"})
        head["vendor_elements"] = [{"uid": "independent-element", "name": "Audit", "value": "Complete association"}]
        head["vendor_element_attributes"] = [{"uid": "independent-child", "name": "Detail", "value": "All qualifiers"}]
        source["sourceBundle"]["study"]["name"] = "Next reading"
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        patched = api.forms["F.SEMANTIC.SNAPSHOT.HEAD.Study_1"]
        self.assertIn({"uid": "independent-note", "value": "Full native note"}, patched["vendor_attributes"])
        self.assertEqual(patched["vendor_elements"], [{"uid": "independent-element", "value": "Complete association"}])
        self.assertEqual(patched["vendor_element_attributes"], [{"uid": "independent-child", "value": "All qualifiers"}])

    def test_invalid_or_duplicate_manifest_identity_refuses_before_new_native_writes(self):
        for mutation in ("nonobject", "duplicate"):
            with self.subTest(mutation=mutation):
                api, source = SnapshotApi(), payload()
                self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
                head = api.forms["F.SEMANTIC.SNAPSHOT.HEAD.Study_1"]
                ext = next(attr for attr in head["vendor_attributes"] if attr["name"] == "ext")
                ext["value"] = "[]" if mutation == "nonobject" else ext["value"].replace(
                    '"sourceStudyId":"semantic-study"', '"sourceStudyId":"foreign","sourceStudyId":"semantic-study"')
                before = deepcopy(api.forms)
                source["sourceBundle"]["study"]["name"] = "Revised"
                self.assertFalse(worker(api).ensure_source_snapshot(source, "Study_1"))
                self.assertEqual(api.forms, before)
                self.assertEqual(api.patches, [])

    def test_duplicate_head_lookup_never_selects_one_native_identity(self):
        api, source = SnapshotApi(), payload()
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        original_read = api.get_all_from_api
        api.get_all_from_api = lambda path, params=None: original_read(path, params) * 2
        before = deepcopy(api.forms)
        self.assertFalse(worker(api).ensure_source_snapshot(source, "Study_1"))
        self.assertEqual(api.forms, before)
        self.assertEqual(api.patches, [])

    def test_identical_source_in_new_payload_build_has_distinct_immutable_generation(self):
        api, source = SnapshotApi(), payload()
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        old_forms = deepcopy(api.forms)
        _attrs, old_head = metadata(api.forms["F.SEMANTIC.SNAPSHOT.HEAD.Study_1"])
        source["source"]["buildHash"] = "sha256:" + "b" * 64
        self.assertTrue(worker(api).ensure_source_snapshot(source, "Study_1"))
        _attrs, head = metadata(api.forms["F.SEMANTIC.SNAPSHOT.HEAD.Study_1"])
        self.assertEqual(head["snapshotHash"], old_head["snapshotHash"])
        self.assertNotEqual(head["generationHash"], old_head["generationHash"])
        self.assertEqual(head["generationHash"], hashlib.sha256(
            (head["snapshotHash"] + "\n" + source["source"]["buildHash"]).encode()).hexdigest())
        for oid, original in old_forms.items():
            if oid != "F.SEMANTIC.SNAPSHOT.HEAD.Study_1":
                self.assertEqual(api.forms[oid], original)
        restored = read_snapshot(api)
        self.assertEqual(head["byteLength"], len(json.dumps(restored, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")))
        self.assertEqual(restored, {**source["sourceBundle"], "semanticSourceCustody": source["sourceCustody"]})

    def test_oversized_source_is_refused_before_any_native_carrier_write(self):
        api = SnapshotApi()
        importer = worker(api)
        with patch("importers.run_import_360i.SOURCE_SNAPSHOT_MAX_BYTES", 64):
            self.assertFalse(importer.ensure_source_snapshot(payload(), "Study_1"))
        self.assertEqual(api.posts, [])
        self.assertEqual(importer.census.stopped[0]["reason"], "SEMANTIC_SOURCE_SNAPSHOT_SIZE_EXCEEDED")

    def test_literal_reference_capacity_is_retained_without_deriving_answer_limits(self):
        item = {"refKey": "R", "name": "Literal reference", "datatype": "text", "length": 7,
                "lengthBasis": "literalReferenceCapacity", "vendorExtensions": {"readonly": True}}
        self.assertEqual(mapping.odm_item_body(item, {}, {})["length"], 7)
        self.assertEqual(json.loads(mapping.vendor_ext_value(item))["lengthBasis"], "literalReferenceCapacity")
        self.assertEqual(item["vendorExtensions"], {"readonly": True})
        del item["length"]
        with self.assertRaisesRegex(ValueError, "OSB_CAPTURE_TEXT_LENGTH_AUTHORITY_REQUIRED"):
            mapping.odm_item_body(item, {}, {})

    def test_pre_strict_retention_success_revalidates_identical_payload_before_clinical_work(self):
        api, source = SnapshotApi(), payload()
        importer = worker(api)
        previous = {"payload_hash": "identical", "status": "succeeded", "import_id": "old-import",
                    "importer_version": "360i-importer/1.16", "osb_study_uid": "Study_1", "uid_map": {}}
        self.assertEqual(IMPORTER_VERSION, "360i-importer/1.17")
        self.assertNotEqual(IMPORTER_VERSION, previous["importer_version"])
        importer.db = Mock()
        importer.db.read_latest_payload.return_value = {"payload": source, "payload_hash": "identical",
                                                       "build_hash": "same-build", "census": {"unmapped": 0}}
        importer.db.read_current_crosswalk.return_value = previous
        original_get = api.get_all_from_api
        api.get_all_from_api = lambda path, params=None: ([{"uid": "Study_1"}] if path == "/studies"
                                                        else original_get(path, params))
        importer.log = Mock()
        importer.ensure_programme_and_project = Mock(return_value="project")
        importer.ensure_units = Mock(return_value={})
        importer.ensure_codelists = Mock(return_value={})
        importer.ensure_study = Mock(return_value="Study_1")
        importer.ensure_epochs = Mock(side_effect=RuntimeError("clinical phase reached after source backfill"))
        with patch("importers.run_import_360i.assert_unsafe_legacy_mutation_allowed"):
            with self.assertRaisesRegex(RuntimeError, "clinical phase reached after source backfill"):
                importer.run("semantic-study")
        self.assertEqual(read_snapshot(api), {**source["sourceBundle"], "semanticSourceCustody": source["sourceCustody"]})
        self.assertTrue(importer.same_payload_replay)


if __name__ == "__main__":
    unittest.main()
