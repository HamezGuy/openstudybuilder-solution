"""Imported CSV datafiles must not start with a UTF-8 byte order mark.

The importers open CSVs as plain utf-8 (importers/utils/importer.py), so a BOM
becomes part of the first header: response_codelists.csv once read its first
column as "\\ufefflibrary_name", and every CDISC row lost its library name.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Reference sheets that no importer reads.
NOT_IMPORTED = {"ReferenceTSParams.csv"}
CSV_FILES = sorted(
    path
    for folder in ("datafiles", "e2e_datafiles")
    for path in (ROOT / folder).rglob("*.csv")
    if path.name not in NOT_IMPORTED
)


def test_datafiles_are_found():
    assert len(CSV_FILES) > 100


@pytest.mark.parametrize(
    "path", CSV_FILES, ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_csv_has_no_byte_order_mark(path):
    with path.open("rb") as handle:
        assert handle.read(3) != b"\xef\xbb\xbf"
