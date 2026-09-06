from copy import deepcopy
import unittest

from clinical_mdr_api.services.integrations.native_observation import (
    NativeObservationError, canonical_hash, collect_native_observation, comparison_record,
)


class NativeObservationTests(unittest.TestCase):
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
