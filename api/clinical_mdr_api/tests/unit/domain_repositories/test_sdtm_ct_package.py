"""SDTM CT membership is decided by the package's catalogue (fork difference)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_package_repository import (
    is_sdtm_ct_package,
)
from clinical_mdr_api.services.studies.study_standard_version_selection import (
    StudyStandardVersionService,
)
from common.config import settings
from common.exceptions import AlreadyExistsException

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


@pytest.mark.parametrize("operation", ["create", "edit"])
def test_another_selection_in_the_same_native_catalogue_is_rejected(operation):
    service = StudyStandardVersionService.__new__(StudyStandardVersionService)
    service._repos = MagicMock()
    service.repo = service._repos.study_standard_version_repository
    service._repos.ct_package_repository.find_by_uid.return_value.catalogue_name = SDTM
    service._repos.ct_package_repository.find_catalogue_name.return_value = SDTM
    current = SimpleNamespace(uid="selection_a", ct_package_uid="opaque-base")
    other = SimpleNamespace(uid="selection_b", ct_package_uid="opaque-second")
    service.repo.find_standard_versions_in_study.return_value = [current, other]
    service.repo.find_by_uid.return_value = current
    selection = SimpleNamespace(ct_package_uid="opaque-replacement", description=None)
    with pytest.raises(AlreadyExistsException, match="selection_b" if operation == "edit" else "selection_a"):
        if operation == "edit":
            service.edit.__wrapped__(service, "study", current.uid, selection)
        else:
            service.create.__wrapped__(service, "study", selection)
    service.repo.save.assert_not_called()


@pytest.mark.parametrize("package_uid", [None, ""])
def test_no_package_is_never_sdtm(package_uid):
    lookup = repository(SDTM)
    assert is_sdtm_ct_package(package_uid, lookup) is False
    assert not lookup.calls
