"""Every feature flag row has all five columns and an explicit enabled value.

The 2.10 file shipped nsvs_library without its enabled column, so its description was read
as "enabled". map_boolean() fell back to False and the flag would have been created switched
off with no description. The importer now refuses such a row instead.
"""

import csv
from pathlib import Path

import pytest

from importers.functions.parsers import map_boolean

ROOT = Path(__file__).resolve().parents[2]
FLAG_FILES = sorted(
    path
    for folder in ("datafiles", "e2e_datafiles")
    for path in (ROOT / folder).rglob("feature_flags.csv")
)
COLUMNS = ["section", "feature", "name", "enabled", "description"]


def test_feature_flag_files_are_found():
    assert len(FLAG_FILES) == 2


@pytest.mark.parametrize(
    "path", FLAG_FILES, ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_every_row_has_every_column_and_a_boolean_enabled(path):
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == COLUMNS
    for line, row in enumerate(rows[1:], start=2):
        assert len(row) == len(COLUMNS), (line, row)
        flag = dict(zip(COLUMNS, row))
        map_boolean(flag["enabled"], raise_exception=True)
        assert flag["name"] and flag["description"], (line, row)


def test_a_shifted_row_is_refused():
    with pytest.raises(ValueError):
        map_boolean("This flag toggles on/off the whole subpage", raise_exception=True)
