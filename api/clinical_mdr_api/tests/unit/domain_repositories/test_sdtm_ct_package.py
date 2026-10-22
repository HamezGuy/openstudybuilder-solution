"""SDTM CT membership is decided by the package's catalogue (fork difference)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_package_repository import (
    is_sdtm_ct_package,
)
from common.config import settings

SDTM = settings.sdtm_ct_catalogue_name


def repository(catalogue_name):
    calls = []

    def find_catalogue_name(package_uid):
        calls.append(package_uid)
        return catalogue_name

    return SimpleNamespace(find_catalogue_name=find_catalogue_name, calls=calls)


@pytest.mark.parametrize(
    "package_uid,catalogue_name,expected",
    [
        # The catalogue decides, whatever the package is called.
        ("Documents CT 3f2a", SDTM, True),
        (f"{SDTM} 2024-03-29", SDTM, True),
        (f"Sponsor {SDTM} 2026-10-22", SDTM, True),
        (f"{SDTM} lookalike", "DDF CT", False),
        ("DDF CT 2024-03-29", "DDF CT", False),
        # Unknown catalogue: upstream's name convention, unchanged.
        (f"{SDTM} 2024-03-29", None, True),
        (f"Sponsor {SDTM} 2026-10-22", None, True),
        ("Documents CT 3f2a", None, False),
        ("DDF CT 2024-03-29", None, False),
    ],
)
def test_the_package_catalogue_decides_and_the_name_is_only_a_fallback(
    package_uid, catalogue_name, expected
):
    lookup = repository(catalogue_name)
    assert is_sdtm_ct_package(package_uid, lookup) is expected
    assert lookup.calls == [package_uid]


def test_a_repository_double_without_a_catalogue_keeps_the_name_convention():
    """Mocked repositories in existing unit tests keep upstream's behaviour."""
    mocked = MagicMock()
    assert is_sdtm_ct_package(f"{SDTM} 2024-03-29", mocked) is True
    assert is_sdtm_ct_package("Documents CT 3f2a", mocked) is False


@pytest.mark.parametrize("package_uid", [None, ""])
def test_no_package_is_never_sdtm(package_uid):
    lookup = repository(SDTM)
    assert is_sdtm_ct_package(package_uid, lookup) is False
    assert not lookup.calls
