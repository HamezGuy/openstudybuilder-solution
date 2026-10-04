"""A sponsor term is re-versioned only when its sponsor name really changed.

find_term_in_codelists() reads existing terms from /ct/codelists/{uid}/terms, whose
items carry sponsor_preferred_name flat. patch_term_if_required() read only the
nested form (name.sponsor_preferred_name), saw None for every existing term and
created a new name version on every run: re-running the 2.10 datatype import on a
2.9 database re-versioned each unchanged term once per pass.
"""

import asyncio

from importers.utils.importer import BaseImporter


class RecordingApi:
    def __init__(self):
        self.calls = []

    async def new_version_to_api_async(self, path, session):
        self.calls.append(("version", path))
        return 201, {}

    async def patch_to_api_async(self, path, body, session):
        self.calls.append(("patch", path, body))
        return 200, {}

    async def approve_async(self, path, session):
        self.calls.append(("approve", path))
        return 201, {}


def patch_calls(existing, new):
    api = RecordingApi()
    importer = BaseImporter(api=api)
    asyncio.run(importer.patch_term_if_required(existing, new, session=None))
    return api.calls


INTEGER = {
    "sponsor_preferred_name": "Integer",
    "sponsor_preferred_name_sentence_case": "integer",
}


def test_flat_existing_term_with_the_same_name_is_left_alone():
    existing = {"term_uid": "CTTerm_000179", "sponsor_preferred_name": "Integer"}
    assert not patch_calls(existing, INTEGER)


def test_nested_existing_name_is_still_read():
    existing = {
        "term_uid": "CTTerm_000179",
        "name": {"sponsor_preferred_name": "Integer"},
    }
    assert not patch_calls(existing, INTEGER)


def test_changed_name_gets_one_new_version_patch_and_approval():
    existing = {
        "term_uid": "CTTerm_000197",
        "sponsor_preferred_name": "Intervale Date Time",
    }
    calls = patch_calls(
        existing,
        {
            "sponsor_preferred_name": "Interval Date Time",
            "sponsor_preferred_name_sentence_case": "interval date time",
        },
    )
    assert [call[0] for call in calls] == ["version", "patch", "approve"]
    assert calls[0][1] == "/ct/terms/CTTerm_000197/names/versions"
    assert calls[1][2]["sponsor_preferred_name"] == "Interval Date Time"
    assert calls[1][2]["sponsor_preferred_name_sentence_case"] == "interval date time"
