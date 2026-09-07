from copy import deepcopy
import unittest
from common.exceptions import NotFoundException

from clinical_mdr_api.services.integrations.native_observation import (
    NativeObservationError, canonical_hash, collect_native_observation, comparison_record,
)


class NativeObservationTests(unittest.TestCase):
    def test_profile_11_only_excludes_exact_generated_endpoint_objective_label(self):
        label = "LATEST on 2026-09-06T00:00:00Z"
        source = {"study_uid": "S1", "study_endpoint_uid": "E1", "study_version": label,
                  "study_objective": {"study_version": label, "study_objective_uid": "O1",
                                      "nested": {"study_version": label}}}
        original = deepcopy(source)
        compared, paths = comparison_record(source, "study_endpoints")
        self.assertEqual(paths, ["/study_version", "/study_objective/study_version"])
        self.assertNotIn("study_version", compared["study_objective"])
        self.assertEqual(compared["study_objective"]["nested"]["study_version"], label)
        self.assertEqual(comparison_record(source, "study_arms")[0]["study_objective"]["study_version"], label)
        self.assertEqual(comparison_record(source)[0]["study_objective"]["study_version"], label)
        source["study_objective"]["study_version"] = "2.0"
        self.assertEqual(comparison_record(source, "study_endpoints")[0]["study_objective"]["study_version"], "2.0")
        result = collect_native_observation("S1", native_study={"uid": "S1"},
            readers={"study_endpoints": (lambda **_: [original], lambda **_: [])})
        self.assertEqual(result["comparisonProfile"], "osb-native-read/1.1")
        retained = result["records"][1]
        self.assertEqual(retained["record"], original)
        self.assertEqual(retained["comparisonHash"], canonical_hash(compared))
        self.assertEqual(retained["comparisonExcludedPaths"], paths)

    def test_generated_read_labels_do_not_reorder_retained_audit_revisions(self):
        expected = None
        for minute in range(10):
            label = f"LATEST on 2026-09-06T00:{minute:02d}:00Z"
            audits = [{"study_uid": "S1", "arm_uid": "A1", "start_date": f"2026-09-03T01:42:0{revision}Z",
                       "change_type": "Edit", "revision": revision, "study_version": label,
                       "nested": {"study_version": "original source", "false": False}} for revision in range(3)]
            result = collect_native_observation("S1", native_study={"uid": "S1"},
                readers={"study_arms": (lambda **_: [], lambda **_: audits)})
            observed = [entry["record"]["revision"] for entry in result["auditRecords"]]
            if expected is None:
                expected = observed
            self.assertEqual(observed, expected)
            self.assertEqual(sorted(observed), [0, 1, 2])
            self.assertTrue(all(entry["record"]["study_version"] == label for entry in result["auditRecords"]))
            self.assertTrue(all(entry["record"]["nested"]["study_version"] == "original source" for entry in result["auditRecords"]))

    def test_missing_historical_projection_retains_all_raw_rows_and_reports_unresolved(self):
        def unavailable(**_):
            raise NotFoundException(msg="Historical Study Objective 'StudyObjective_000073' is unavailable")
        raw = [{"study_selection_uid": "E1", "study_objective_uid": "StudyObjective_000073",
                "author_id": "exact-editor", "endpoint_version": "1.2", "value": {"zero": 0, "null": None}},
               {"study_selection_uid": "E2", "study_objective_uid": "other-objective", "author_id": "second-editor"}]
        result = collect_native_observation("S1", native_study={"uid": "S1"},
            readers={"study_endpoints": (lambda **_: [], unavailable)},
            raw_history_readers={"study_endpoints": lambda **_: raw}, raw_actions=[])
        self.assertEqual([row["record"] for row in result["auditRecords"]], raw)
        self.assertTrue(all(row["projectionStatus"] == "unresolved" for row in result["auditRecords"]))
        coverage = next(row for row in result["coverage"]["collections"] if row["collection"] == "study_endpoints")
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["auditStatus"], "raw-retained")
        self.assertEqual(coverage["auditProjection"]["status"], "unresolved")
        self.assertEqual(coverage["auditProjection"]["rawRecordCount"], 2)
        self.assertIn("StudyObjective_000073", coverage["auditProjection"]["reason"])

    def test_lookup_fallback_never_hides_transport_or_current_inventory_failure(self):
        for failure in [RuntimeError("database unavailable"), NativeObservationError("scope mismatch")]:
            def fail(**_):
                raise failure
            with self.assertRaises(type(failure)):
                collect_native_observation("S1", native_study={"uid": "S1"},
                    readers={"study_endpoints": (lambda **_: [], fail)},
                    raw_history_readers={"study_endpoints": lambda **_: [{"study_selection_uid": "E1"}]})
        def missing(**_):
            raise NotFoundException(msg="missing current reference")
        with self.assertRaises(NotFoundException):
            collect_native_observation("S1", native_study={"uid": "S1"}, readers={"study_endpoints": (missing, None)})
        with self.assertRaisesRegex(NativeObservationError, "RAW_AUDIT_MISSING"):
            collect_native_observation("S1", native_study={"uid": "S1"},
                readers={"study_endpoints": (lambda **_: [], missing)}, raw_history_readers={"study_endpoints": lambda **_: []})

    def test_full_rows_named_editor_delete_history_and_empty_values_are_retained(self):
        row = {"study_uid": "S1", "arm_uid": "A1", "study_version": "LATEST on 2026-09-06T00:00:00Z",
               "description": "文🧪" * 4000, "number_of_subjects": 0, "flag": False,
               "future": {"__proto__": {"source": True}, "empty": "", "nil": None, "study_version": "clinical"}}
        audit = {**row, "change_type": "Edit", "start_date": "2026-09-06T00:00:00Z", "author_username": "editor@example.test", "author_id": "native-user-7"}
        deleted = {**audit, "arm_uid": "deleted-A", "change_type": "Delete"}
        originals = deepcopy([row, audit, deleted])
        result = collect_native_observation("S1", native_study={"uid": "S1"}, study_audit=[],
            readers={"study_arms": (lambda **_: [row], lambda **_: [audit, deleted])})
        self.assertEqual(result["records"][1]["record"], originals[0])
        self.assertEqual([entry["record"] for entry in result["auditRecords"]], originals[1:])
        self.assertEqual(result["records"][1]["comparisonExcludedPaths"], ["/study_version"])
        self.assertEqual([row, audit, deleted], originals)
        content = {key: value for key, value in result.items() if key != "contentHash"}
        self.assertEqual(result["contentHash"], canonical_hash(content))
        self.assertFalse(result["coverage"]["releaseAuthority"])

    def test_only_top_level_generated_read_label_is_excluded_from_comparison(self):
        row = {"study_version": "LATEST on 2026-09-06T00:00:00Z", "nested": {"study_version": "LATEST on 2026-09-06T00:00:00Z"}, "zero": 0.0}
        first, _ = comparison_record(row)
        second, _ = comparison_record({**row, "study_version": "LATEST on 2026-09-06T00:10:00Z"})
        self.assertEqual(canonical_hash(first), canonical_hash(second))
        second["nested"]["study_version"] = "different clinical metadata"
        self.assertNotEqual(canonical_hash(first), canonical_hash(second))
        self.assertEqual(comparison_record({"study_version": "2.0"}), ({"study_version": "2.0"}, []))
        self.assertEqual(canonical_hash({"zero": 0.0}), canonical_hash({"zero": 0}))

    def test_incomplete_duplicate_or_foreign_scope_cannot_report_deletions(self):
        valid = {"study_uid": "S1", "arm_uid": "A1"}
        for rows in [[{**valid, "study_uid": "foreign"}], [valid, valid], {"items": [valid], "total": 3}]:
            with self.subTest(rows=rows), self.assertRaises(NativeObservationError):
                collect_native_observation("S1", native_study={"uid": "S1"}, readers={"study_arms": (lambda **_: rows, lambda **_: [])})
        with self.assertRaises(NativeObservationError):
            collect_native_observation("S1", native_study={"uid": "S1"}, readers={"study_arms": (lambda **_: [], lambda **_: [{**valid, "study_uid": "foreign"}])})

    def test_missing_audit_reader_is_explicit_and_does_not_invent_an_editor(self):
        row = {"study_uid": "S1", "study_activity_instruction_uid": "I1", "name": "source"}
        result = collect_native_observation("S1", native_study={"uid": "S1"}, readers={"study_activity_instructions": (lambda **_: [row], None)})
        self.assertEqual(result["coverage"]["collections"][1]["auditStatus"], "unavailable")
        self.assertEqual(result["auditRecords"], [])

    def test_scoped_raw_actions_keep_exact_subject_and_both_values_and_reject_shared_ownership(self):
        raw = {"study_uid": "S1", "owners": ["S1"], "actionId": "opaque-native-action", "author_id": "native-editor-b",
               "action": {"labels": ["Delete", "StudyAction"], "properties": {"author_id": "native-editor-b", "date": "2026-09-06T00:00:00.123456789Z"}},
               "before": [{"properties": {"text": "文🧪" * 4000, "flag": False, "zero": 0}}], "after": []}
        result = collect_native_observation("S1", native_study={"uid": "S1"}, readers={}, raw_actions=[raw])
        self.assertEqual(result["auditRecords"][0]["record"], raw)
        self.assertEqual(result["coverage"]["collections"][-1]["auditCount"], 1)
        for invalid in [{**raw, "owners": ["S1", "foreign"]}, {**raw, "study_uid": "foreign"}]:
            with self.assertRaises(NativeObservationError):
                collect_native_observation("S1", native_study={"uid": "S1"}, readers={}, raw_actions=[invalid])


if __name__ == "__main__":
    unittest.main()
