"""A sponsor term's sentence-case name must be a case variant of its sponsor name.

The API refuses a name whose sentence case differs by more than case ("isn't an
independent case version"). The importer then leaves the draft name version it
opened behind. The 2.10 datatype file named intervalDatetime 'Intervale Date Time'
beside the sentence case 'interval date time', which left exactly such a draft on
an upgraded 2.9 database.
"""

import csv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _sponsor_name_files():
    files = []
    for folder in ("datafiles", "e2e_datafiles"):
        for path in sorted((ROOT / folder).rglob("*.csv")):
            with open(path, encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle), [])
            if "SPONSOR_NAME" in header and "SPONSOR_NAME_SENTENCE_CASE" in header:
                files.append(path)
    return files


SPONSOR_NAME_FILES = _sponsor_name_files()


def test_sponsor_name_files_are_found():
    assert len(SPONSOR_NAME_FILES) > 100


@pytest.mark.parametrize(
    "path", SPONSOR_NAME_FILES, ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_sentence_case_is_a_case_variant_of_the_sponsor_name(path):
    with open(path, encoding="utf-8", newline="") as handle:
        mismatches = [
            (line, row["SPONSOR_NAME"], row["SPONSOR_NAME_SENTENCE_CASE"])
            for line, row in enumerate(csv.DictReader(handle), start=2)
            if row["SPONSOR_NAME"]
            and row["SPONSOR_NAME_SENTENCE_CASE"]
            and row["SPONSOR_NAME"].lower() != row["SPONSOR_NAME_SENTENCE_CASE"].lower()
        ]
    assert not mismatches, mismatches
