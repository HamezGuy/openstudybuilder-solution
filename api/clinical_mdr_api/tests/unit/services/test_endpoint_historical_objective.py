from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from common.exceptions import NotFoundException
from clinical_mdr_api.services.studies.study_endpoint_selection import (
    StudyEndpointSelectionService,
)


def instant(day):
    return datetime(2026, 9, day, tzinfo=timezone.utc)


class EndpointHistoricalObjectiveTests(TestCase):
    def service(self, rows):
        service = StudyEndpointSelectionService.__new__(StudyEndpointSelectionService)
        service._repos = SimpleNamespace(
            study_objective_repository=SimpleNamespace(
                find_selection_history=Mock(return_value=rows),
                find_by_study=Mock(
                    side_effect=AssertionError("Must not use current selections")
                ),
            ),
            ct_codelist_name_repository=SimpleNamespace(
                get_codelist_term_by_uid_and_submval=Mock()
            ),
        )
        return service

    def render(self, service, starts):
        histories = [SimpleNamespace(start_date=instant(day)) for day in starts]

        def endpoint(**values):
            return values["get_study_objective_by_uid"](
                "S1", "StudyObjective_000073", terms_at_specific_datetime=None
            )

        def objective(**values):
            return values["study_selection_history"].objective_version

        with patch(
            "clinical_mdr_api.services.studies.study_endpoint_selection.StudySelectionEndpoint.from_study_selection_history",
            side_effect=endpoint,
        ), patch(
            "clinical_mdr_api.services.studies.study_endpoint_selection.StudySelectionObjective.from_study_selection_history",
            side_effect=objective,
        ):
            return service._transform_history_to_response_model(
                histories, "S1", [None] * len(histories)
            )

    def test_deleted_objective_resolves_exact_revision_and_edit_boundary(self):
        rows = [
            SimpleNamespace(
                study_selection_uid="StudyObjective_000073",
                start_date=instant(1),
                end_date=instant(3),
                objective_version="1.0",
            ),
            SimpleNamespace(
                study_selection_uid="StudyObjective_000073",
                start_date=instant(3),
                end_date=instant(5),
                objective_version="2.0",
            ),
        ]
        service = self.service(rows)
        self.assertEqual(self.render(service, [2, 3, 4]), ["1.0", "2.0", "2.0"])
        service._repos.study_objective_repository.find_selection_history.assert_called_once_with(
            "S1"
        )
        service._repos.study_objective_repository.find_by_study.assert_not_called()

    def test_objective_render_cache_uses_revision_and_effective_date_per_read(self):
        rows = [
            SimpleNamespace(
                study_selection_uid="StudyObjective_000073",
                start_date=instant(1),
                end_date=instant(3),
                objective_version="1.0",
            ),
            SimpleNamespace(
                study_selection_uid="StudyObjective_000073",
                start_date=instant(3),
                end_date=None,
                objective_version="2.0",
            ),
        ]
        service = self.service(rows)
        histories = [
            SimpleNamespace(start_date=instant(day)) for day in [2, 2, 3, 4, 4]
        ]
        dates = [None, None, None, instant(5), instant(5)]

        def endpoint(**values):
            return values["get_study_objective_by_uid"]("S1", "StudyObjective_000073")

        def objective(**values):
            return (
                values["study_selection_history"].objective_version,
                values["effective_date"],
            )

        with patch(
            "clinical_mdr_api.services.studies.study_endpoint_selection.StudySelectionEndpoint.from_study_selection_history",
            side_effect=endpoint,
        ), patch(
            "clinical_mdr_api.services.studies.study_endpoint_selection.StudySelectionObjective.from_study_selection_history",
            side_effect=objective,
        ) as render:
            expected = [
                ("1.0", None),
                ("1.0", None),
                ("2.0", None),
                ("2.0", instant(5)),
                ("2.0", instant(5)),
            ]
            self.assertEqual(
                service._transform_history_to_response_model(histories, "S1", dates),
                expected,
            )
            self.assertEqual(render.call_count, 3)
            service._repos.study_objective_repository.find_selection_history.assert_called_once_with(
                "S1"
            )
            self.assertEqual(
                service._transform_history_to_response_model(
                    histories[:1], "S1", [None]
                ),
                expected[:1],
            )
            self.assertEqual(render.call_count, 4)
            self.assertEqual(
                service._repos.study_objective_repository.find_selection_history.call_count,
                2,
            )

    def test_missing_ambiguous_and_future_rows_do_not_guess_latest(self):
        row = SimpleNamespace(
            study_selection_uid="StudyObjective_000073",
            start_date=instant(1),
            end_date=instant(4),
            objective_version="1.0",
        )
        future = SimpleNamespace(
            study_selection_uid="StudyObjective_000073",
            start_date=instant(3),
            end_date=None,
            objective_version="2.0",
        )
        for rows, at in [([], 2), ([row, row], 2), ([row], 5), ([future], 2)]:
            with self.subTest(rows=len(rows), at=at), self.assertRaises(
                NotFoundException
            ):
                self.render(self.service(rows), [at])
        with self.assertRaises(NotFoundException):
            self.render(
                self.service(
                    [
                        SimpleNamespace(
                            study_selection_uid="foreign-objective",
                            start_date=instant(1),
                            end_date=None,
                        )
                    ]
                ),
                [2],
            )

    def test_missing_library_version_never_resolves_latest(self):
        for version in [None, "", " "]:
            row = SimpleNamespace(
                study_selection_uid="StudyObjective_000073",
                start_date=instant(1),
                end_date=None,
                objective_version=version,
            )
            with self.subTest(version=version), self.assertRaisesRegex(
                NotFoundException, "no exact library version"
            ):
                self.render(self.service([row]), [2])

    def test_versioned_and_ct_caches_preserve_keys_copies_and_reset_per_read(self):
        service = self.service([])
        service._transform_endpoint_model = Mock(
            side_effect=lambda uid, version: {
                "uid": uid,
                "version": version,
                "values": [0, False, ""],
            }
        )
        service._transform_timeframe_model = Mock(
            side_effect=lambda uid, version: {
                "uid": uid,
                "version": version,
                "values": [None],
            }
        )
        term_reader = (
            service._repos.ct_codelist_name_repository.get_codelist_term_by_uid_and_submval
        )
        term_reader.side_effect = lambda uid, codelist, at_specific_date_time: {
            "uid": uid,
            "codelist": codelist,
            "date": at_specific_date_time,
            "values": [0],
        }
        # Repeated rows, changed library versions, changed UID, and separate CT
        # codelists/effective dates must remain distinct despite shared lookups.
        inputs = [
            ("E1", "1.0", "CL1", None),
            ("E1", "1.0", "CL1", None),
            ("E1", "2.0", "CL1", None),
            ("E2", "1.0", "CL2", None),
            ("E1", "1.0", "CL1", instant(5)),
        ]
        histories = [SimpleNamespace(source=value) for value in inputs]

        def endpoint(**values):
            uid, version, codelist, date = values["study_selection_history"].source
            return {
                "endpoint": values["get_endpoint_by_uid"](uid, version),
                "timeframe": values["get_timeframe_by_uid"](uid, version),
                "term": values["find_codelist_term_by_uid_and_submval"](
                    "TERM", codelist, at_specific_date_time=date
                ),
            }

        with patch(
            "clinical_mdr_api.services.studies.study_endpoint_selection.StudySelectionEndpoint.from_study_selection_history",
            side_effect=endpoint,
        ):
            result = service._transform_history_to_response_model(
                histories, "S1", [None] * len(histories)
            )
            self.assertEqual(service._transform_endpoint_model.call_count, 3)
            self.assertEqual(service._transform_timeframe_model.call_count, 3)
            self.assertEqual(term_reader.call_count, 3)
            self.assertEqual(result[2]["endpoint"]["version"], "2.0")
            self.assertEqual(result[3]["endpoint"]["uid"], "E2")
            self.assertEqual(result[3]["term"]["codelist"], "CL2")
            self.assertEqual(result[4]["term"]["date"], instant(5))
            result[0]["endpoint"]["values"].append("changed")
            result[0]["timeframe"]["values"].append("changed")
            result[0]["term"]["values"].append("changed")
            self.assertEqual(result[1]["endpoint"]["values"], [0, False, ""])
            self.assertEqual(result[1]["timeframe"]["values"], [None])
            self.assertEqual(result[1]["term"]["values"], [0])
            again = service._transform_history_to_response_model(
                histories[:1], "S1", [None]
            )
            self.assertEqual(again[0], result[1])
            self.assertEqual(service._transform_endpoint_model.call_count, 4)
            self.assertEqual(service._transform_timeframe_model.call_count, 4)
            self.assertEqual(term_reader.call_count, 4)
            service._repos.study_objective_repository.find_selection_history.assert_not_called()
