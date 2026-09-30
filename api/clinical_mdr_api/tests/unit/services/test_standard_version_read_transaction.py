"""Epoch and metadata readers already own a native transaction."""
from types import SimpleNamespace

from clinical_mdr_api.services.studies import study_standard_version_selection as module


def test_standard_read_reuses_the_callers_native_transaction(monkeypatch):
    existing = object()
    monkeypatch.setattr(module.db, "_active_transaction", existing)
    calls = []
    service = object.__new__(module.StudyStandardVersionService)
    service.repo = SimpleNamespace(find_all_standard_version=lambda **kwargs: (
        calls.append(kwargs) or [], 0
    ))
    assert service.get_standard_versions_in_study(
        "Study_1", study_value_version="0.1", page_size=0
    ) == []
    assert module.db._active_transaction is existing
    assert calls == [{
        "study_uid": "Study_1", "sort_by": None, "page_number": 1, "page_size": 0,
        "filter_by": None, "filter_operator": module.FilterOperator.AND,
        "total_count": False, "study_value_version": "0.1",
    }]
