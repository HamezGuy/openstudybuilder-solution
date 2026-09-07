"""Per-read CT package reuse must preserve historical interval semantics."""
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, call

from clinical_mdr_api.services.studies.study_selection_base import StudySelectionMixin


def version(uid, start, end=None, change_type="Edit"):
    return SimpleNamespace(
        ct_package_uid=uid,
        start_date=start,
        end_date=end,
        change_type=change_type,
    )


def service(versions, packages):
    instance = StudySelectionMixin()
    instance._repos = SimpleNamespace(
        study_standard_version_repository=SimpleNamespace(
            get_all_study_version_versions=Mock(return_value=versions)
        ),
        ct_package_repository=SimpleNamespace(
            find_by_uid=Mock(side_effect=lambda uid: packages[uid])
        ),
    )
    return instance


class StudyStandardsHistoryDateTests(unittest.TestCase):
    def test_6007_same_package_rows_fetch_once_and_the_next_call_reads_fresh_data(self):
        packages = {"CT-A": SimpleNamespace(effective_date=date(2024, 1, 15))}
        instance = service([version("CT-A", datetime(2024, 1, 1))], packages)
        starts = [datetime(2024, 3, 1)] * 6007
        actual = instance._extract_multiple_version_study_standards_effective_date("S1", starts)
        self.assertEqual(actual, [datetime(2024, 1, 15, 23, 59, 59, 999999)] * 6007)
        self.assertEqual(instance._repos.ct_package_repository.find_by_uid.call_count, 1)
        instance._repos.ct_package_repository.find_by_uid.assert_called_once_with("CT-A")
        instance._repos.study_standard_version_repository.get_all_study_version_versions.assert_called_once_with(study_uid="S1")

        packages["CT-A"] = SimpleNamespace(effective_date=date(2024, 2, 29))
        refreshed = instance._extract_multiple_version_study_standards_effective_date("S1", starts)
        self.assertEqual(refreshed, [datetime(2024, 2, 29, 23, 59, 59, 999999)] * 6007)
        self.assertEqual(instance._repos.ct_package_repository.find_by_uid.call_args_list, [call("CT-A"), call("CT-A")])

    def test_multiple_packages_gaps_delete_filter_and_first_match_order_remain_exact(self):
        versions = [
            version("missing-start", None),
            version("deleted", datetime(2023, 1, 1), change_type="Delete"),
            version("CT-A", datetime(2024, 1, 1), datetime(2024, 2, 1)),
            # Preserve the original first-match order for overlapping versions.
            version("overlap", datetime(2024, 1, 10), datetime(2024, 2, 1)),
            version("CT-B", datetime(2024, 3, 1), datetime(2024, 4, 1)),
            version("CT-A", datetime(2024, 5, 1)),
        ]
        instance = service(versions, {
            "CT-A": SimpleNamespace(effective_date=date(2023, 12, 15)),
            "CT-B": SimpleNamespace(effective_date=date(2024, 2, 15)),
        })
        starts = [datetime(2024, 5, 1), datetime(2023, 12, 31), datetime(2024, 1, 1),
                  datetime(2024, 1, 15), datetime(2024, 2, 1), datetime(2024, 3, 1),
                  datetime(2024, 4, 1), datetime(2024, 3, 15)]
        actual = instance._extract_multiple_version_study_standards_effective_date("S1", starts)
        a = datetime(2023, 12, 15, 23, 59, 59, 999999)
        b = datetime(2024, 2, 15, 23, 59, 59, 999999)
        self.assertEqual(actual, [a, None, a, a, None, b, None, b])
        self.assertEqual(instance._repos.ct_package_repository.find_by_uid.call_args_list, [call("CT-A"), call("CT-B")])
        self.assertEqual(starts[0], datetime(2024, 5, 1))

    def test_empty_or_unmatched_dates_do_not_fetch_any_package(self):
        instance = service([], {})
        self.assertEqual(instance._extract_multiple_version_study_standards_effective_date("S1", []), [])
        self.assertEqual(instance._extract_multiple_version_study_standards_effective_date("S1", [datetime(2024, 1, 1)]), [None])
        instance._repos.ct_package_repository.find_by_uid.assert_not_called()

    def test_lookup_errors_and_invalid_dates_are_not_hidden_or_cached_across_calls(self):
        instance = service([version("CT-A", datetime(2024, 1, 1))], {})
        lookup = instance._repos.ct_package_repository.find_by_uid
        error = RuntimeError("package lookup unavailable")
        lookup.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            instance._extract_multiple_version_study_standards_effective_date("S1", [datetime(2024, 2, 1)] * 2)
        self.assertIs(caught.exception, error)
        lookup.assert_called_once_with("CT-A")
        lookup.side_effect = None
        lookup.return_value = SimpleNamespace(effective_date=None)
        with self.assertRaises(AttributeError):
            instance._extract_multiple_version_study_standards_effective_date("S1", [datetime(2024, 2, 1)])
        lookup.return_value = SimpleNamespace(effective_date=date(2024, 1, 15))
        self.assertEqual(instance._extract_multiple_version_study_standards_effective_date("S1", [datetime(2024, 2, 1)]), [datetime(2024, 1, 15, 23, 59, 59, 999999)])
        self.assertEqual(lookup.call_count, 3)


if __name__ == "__main__":
    unittest.main()
