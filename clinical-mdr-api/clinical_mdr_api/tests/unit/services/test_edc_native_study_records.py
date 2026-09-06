from copy import deepcopy
from types import SimpleNamespace
import unittest

from clinical_mdr_api.services.integrations.edc_native_study_records import (
    collect_study_native_records,
    NativeStudyRecordError,
)


class NativeStudyRecordsTests(unittest.TestCase):
    def test_all_source_metadata_is_retained_without_native_visits_or_forms(self):
        original = {"study_uid": "Study_1", "study_endpoint_uid": "EndpointSelection_1",
                    "endpoint": {"uid": "Endpoint_1", "version": "1.0", "future": {"empty": "", "zero": 0, "null": None}},
                    "timeframe": {"uid": "Timeframe_1", "version": "2.0"}, "accepted_version": False}
        before, calls = deepcopy(original), []
        class Model:
            def model_dump(self, *, mode):
                assert mode == "json"
                return original
        def read(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(items=[Model()], total=1)
        records, census = collect_study_native_records("Study_1", readers={"studyEndpoint": read})
        self.assertEqual(calls, [{"study_uid": "Study_1", "page_size": 0, "no_brackets": False}])
        self.assertEqual(records, [{"kind": "studyEndpoint", "uid": "EndpointSelection_1", "record": before}])
        records[0]["record"]["endpoint"]["future"]["null"] = "changed copy"
        self.assertEqual(original, before)
        self.assertEqual(census[0]["readCount"], 1)

    def test_foreign_or_absent_study_scope_fails_closed(self):
        for source in ({"uid": "Epoch_1", "study_uid": "Other"}, {"uid": "Epoch_1"}):
            with self.subTest(source=source), self.assertRaises(NativeStudyRecordError):
                collect_study_native_records("Study_1", readers={"studyEpoch": lambda **_: [source]})
        with self.assertRaises(NativeStudyRecordError):
            collect_study_native_records("", readers={})

    def test_distinct_same_uid_readings_are_not_silently_selected_or_overwritten(self):
        first = {"study_uid": "Study_1", "study_activity_uid": "Selection_1", "activity": {"name": "first"}}
        second = {**first, "activity": {"name": "second"}}
        records, census = collect_study_native_records("Study_1", readers={"studyActivity": lambda **_: [first, first, second]})
        self.assertEqual([entry["record"] for entry in records], [first, second])
        self.assertEqual(census[0]["kind"], "ambiguous_native_reading")

    def test_noncalendar_schedule_scope_is_explicit_and_raw_body_is_unchanged(self):
        schedule = {"study_activity_schedule_uid": "Schedule_1", "study_activity_uid": "Activity_1", "study_visit_uid": "Visit_1"}
        calls = []
        def read(**kwargs):
            calls.append(kwargs)
            return [schedule]
        records, _ = collect_study_native_records("Study_1", readers={"studyActivitySchedule": read})
        self.assertEqual(calls, [{"study_uid": "Study_1", "operational": False}])
        self.assertEqual(records[0]["record"], schedule)
        self.assertEqual(records[0]["scope"]["studyUid"], "Study_1")
        with self.assertRaises(NativeStudyRecordError):
            collect_study_native_records("Study_1", readers={"studyActivitySchedule": lambda **_: [{**schedule, "study_uid": "Other"}]})

    def test_complete_response_required_and_empty_collection_is_counted(self):
        with self.assertRaises(NativeStudyRecordError):
            collect_study_native_records("Study_1", readers={"studyObjective": lambda **_: {"total": 10}})
        with self.assertRaises(NativeStudyRecordError):
            collect_study_native_records("Study_1", readers={"studyCriteria": lambda **_: [{"study_uid": "Study_1"}]})
        records, census = collect_study_native_records("Study_1", readers={"studyDesignCell": lambda **_: []})
        self.assertEqual(records, [])
        self.assertEqual(census[0]["readCount"], 0)


if __name__ == "__main__":
    unittest.main()
