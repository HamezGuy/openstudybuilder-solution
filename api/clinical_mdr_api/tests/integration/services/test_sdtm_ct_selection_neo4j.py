"""SDTM CT selection detection by package catalogue, natively (fork difference).

Lock adds an automatic SDTM CT standard version only when the study selects
none. Upstream detected a selection by the package UID naming the catalogue;
the fork asks the package's catalogue, with the name as the fallback when the
catalogue is unknown (README, AccuraTrial fork). Synthetic engineering
evidence on the owned Community graph.
"""

# Pytest resolves the imported fixture functions by parameter name.
# pylint: disable=redefined-outer-name

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_package_repository import (
    CTPackageRepository,
)
from clinical_mdr_api.services.ddf.usdm_service import USDMService
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from clinical_mdr_api.services.studies.study_standard_version_selection import (
    StudyStandardVersionService,
)
from clinical_mdr_api.tests.integration.services.test_authored_protocol_documents_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _create_protocol_study,
    native_visit_graph,
    protocol_client,
    protocol_graph,
)
from clinical_mdr_api.tests.integration.services.test_study_arm_lifecycle_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _lock,
    _unlock,
    lifecycle_client,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.config import settings

pytestmark = [
    pytest.mark.estate_fixture,
    pytest.mark.parametrize(
        "native_visit_graph", ["study-only"], indirect=True, scope="module"
    ),
]
SDTM = settings.sdtm_ct_catalogue_name


def _selections(fixture, study_uid, version=None):
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        rows = StudyStandardVersionService().get_standard_versions_in_study(
            study_uid, study_value_version=version
        )
        return sorted(
            (row.ct_package.uid, bool(row.automatically_created)) for row in rows
        )


def _package_date(fixture, package_uid):
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        return CTPackageRepository().find_by_uid(package_uid).effective_date


def test_unconventional_sdtm_package_gets_no_second_selection_and_exports(
    protocol_graph, lifecycle_client
):
    fixture, reasons = protocol_graph
    client = lifecycle_client
    study_uid = _create_protocol_study(fixture, client, title="Synthetic CT naming")
    package_uid = f"Documents CT {fixture.nonce}"
    assert SDTM not in package_uid
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        assert CTPackageRepository().find_catalogue_name(package_uid) == SDTM
    assert _selections(fixture, study_uid) == [(package_uid, False)]

    version = _lock(client, study_uid, reasons)
    # No second, automatic SDTM CT selection: the study already selects one.
    assert _selections(fixture, study_uid, version) == [(package_uid, False)]
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        # The locked version's USDM export is no longer refused as ambiguous.
        report = USDMService().get_by_uid_with_report(
            study_uid, study_value_version=version
        )
        assert report["mappingReport"]["studyValueVersion"] == version
        # Both terms-at-date readers resolve the selected package's date.
        expected = _package_date(fixture, package_uid)
        study_date = StudyService()._extract_terms_at_date(study_uid, version)
        selection_date = (
            StudyArmSelectionService()._extract_study_standards_effective_date(
                study_uid, version
            )
        )
    assert study_date.date() == expected
    assert selection_date.date() == expected

    _unlock(client, study_uid, reasons)
    # A selection the author made is never removed as if it were automatic.
    assert _selections(fixture, study_uid) == [(package_uid, False)]


def test_conventional_names_behave_as_before(protocol_graph, lifecycle_client):
    fixture, reasons = protocol_graph
    client = lifecycle_client
    conventional = f"{SDTM} conventional {fixture.nonce}"
    study_uid = _create_protocol_study(
        fixture, client, title="Synthetic CT convention", package_name=conventional
    )
    version = _lock(client, study_uid, reasons)
    assert _selections(fixture, study_uid, version) == [(conventional, False)]

    # A study with no selection gets the automatic sponsor package at lock,
    # loses it at unlock and gets it again at the next lock, as upstream does.
    fixture.created += 1
    with fixture.principal():
        bare = TestUtils.create_study(
            number=str(1900 + fixture.created),
            acronym=f"CTBARE{fixture.created}",
            project_number="123",
        ).uid
        fixture.auth.user.study_ids.add(bare)
    response = client.patch(
        f"/studies/{bare}",
        json={"current_metadata": {"study_description": {"study_title": "Bare"}}},
    )
    assert response.status_code == 200, response.text
    assert not _selections(fixture, bare)
    first = _lock(client, bare, reasons)
    locked = _selections(fixture, bare, first)
    assert len(locked) == 1 and locked[0][1] is True
    assert locked[0][0].startswith("Sponsor ") and SDTM in locked[0][0]
    _unlock(client, bare, reasons)
    assert not _selections(fixture, bare)
    second = _lock(client, bare, reasons)
    assert _selections(fixture, bare, second) == locked
