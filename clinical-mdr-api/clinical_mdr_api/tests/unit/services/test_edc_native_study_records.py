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

    def test_selected_version_is_required_at_reader_and_record_boundaries(self):
        record = {"study_uid": "Study_1", "uid": "Epoch_1", "study_version": "2.0"}
        calls = []

        def read(**kwargs):
            calls.append(kwargs)
            return [record]

        records, _ = collect_study_native_records(
            "Study_1", study_value_version="2.0", readers={"studyEpoch": read}
        )
        self.assertEqual(calls, [{
            "study_uid": "Study_1", "study_value_version": "2.0", "page_size": 0,
        }])
        self.assertEqual(records[0]["scope"]["studyValueVersion"], "2.0")
        with self.assertRaisesRegex(NativeStudyRecordError, "version mismatch"):
            collect_study_native_records(
                "Study_1", study_value_version="1.0", readers={"studyEpoch": read}
            )
        with self.assertRaisesRegex(NativeStudyRecordError, "cannot honor"):
            collect_study_native_records(
                "Study_1", study_value_version="2.0",
                readers={"studyEpoch": lambda study_uid, page_size: [record]},
            )

    def test_reported_partial_page_is_rejected_even_when_page_size_zero_was_requested(self):
        def read(**kwargs):
            self.assertEqual(kwargs["page_size"], 0)
            return {"items": [{"study_uid": "Study_1", "uid": "Epoch_1"}], "total": 2}

        with self.assertRaisesRegex(NativeStudyRecordError, "truncated"):
            collect_study_native_records("Study_1", readers={"studyEpoch": read})

    def test_optional_singleton_domains_keep_exact_scope_without_creating_defaults(self):
        design = {"study_uid": "Study_1", "study_design_class": "Cohort", "study_version": "2.0"}
        calls = []

        def read_design(**kwargs):
            calls.append(("design", kwargs))
            return design

        def read_absent_source(**kwargs):
            calls.append(("source", kwargs))
            return None

        records, census = collect_study_native_records(
            "Study_1", study_value_version="2.0",
            readers={"studyDesignClass": read_design, "studySourceVariable": read_absent_source},
        )
        self.assertEqual(calls, [
            ("design", {"study_uid": "Study_1", "study_value_version": "2.0"}),
            ("source", {"study_uid": "Study_1", "study_value_version": "2.0"}),
        ])
        self.assertEqual(records[0]["record"], design)
        self.assertEqual(records[0]["scope"]["studyValueVersion"], "2.0")
        self.assertEqual(len(records), 1)
        self.assertEqual([row["readCount"] for row in census], [1, 0])

    def test_data_suppliers_request_the_whole_exact_selected_collection(self):
        supplier = {
            "study_uid": "Study_1", "study_version": "2.0",
            "study_data_supplier_uid": "SupplierSelection_1",
            "order": 0, "data_supplier": {"name": "", "api_base_url": None},
        }
        calls = []

        def read(**kwargs):
            calls.append(kwargs)
            return {"items": [supplier], "total": 1}

        records, _ = collect_study_native_records(
            "Study_1", study_value_version="2.0", readers={"studyDataSupplier": read},
        )
        self.assertEqual(calls, [{"study_uid": "Study_1", "study_value_version": "2.0", "page_size": 0}])
        self.assertEqual(records[0]["record"], supplier)


if __name__ == "__main__":
    unittest.main()
