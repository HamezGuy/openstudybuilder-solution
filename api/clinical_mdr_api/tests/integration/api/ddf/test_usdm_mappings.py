"""
Tests for DDF adapter endpoints
"""

# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name
# pylint: disable=too-many-arguments

# pytest fixture functions have other fixture functions as arguments,
# which pylint interprets as unused arguments

import pytest
from fastapi.testclient import TestClient

from clinical_mdr_api.main import app
from clinical_mdr_api.models.study_selections.study import Study
from clinical_mdr_api.tests.integration.utils.api import (
    inject_and_clear_db,
    inject_base_data,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from clinical_mdr_api.tests.utils.checks import assert_response_status_code

# Global variables shared between fixtures and tests
study: Study


@pytest.fixture(scope="module")
def api_client(test_data):
    """Create FastAPI test client
    using the database name set in the `test_data` fixture"""
    yield TestClient(app)


@pytest.fixture(scope="module")
def test_data():
    """Initialize test data"""
    db_name = "ddf.adapter.integration"
    inject_and_clear_db(db_name)
    inject_base_data()

    global study
    study = TestUtils.create_study()

    yield


def test_ddf_study(api_client):
    response = api_client.get(
        f"/usdm/v4/studyDefinitions/{study.uid}",
    )
    # AccuraTrial fork: USDM is exported only from governed native values. This
    # bare study has no title, so the export names the missing source value
    # instead of emitting a placeholder. The positive export is covered by the
    # native source suite (tests/unit/services/test_usdm_*).
    assert_response_status_code(response, 422)
    body = response.json()
    assert body["type"] == "USDMMappingAuthorityRequired"
    assert body["message"].startswith(
        "USDM_REQUIRED_SOURCE_VALUE_MISSING: current_metadata.study_description.study_title"
    )
