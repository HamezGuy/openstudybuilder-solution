import asyncio

import pytest

from clinical_mdr_api.routers import system
from clinical_mdr_api.tests.utils.checks import assert_response_status_code


def test_get_system_information(api_client):
    response = api_client.get("/system/information")
    assert_response_status_code(response, 200)
    payload = response.json()
    assert payload.get(
        "api_version"
    ), "missing api_version property of system information"
    assert payload.get(
        "db_version"
    ), "missing db_version property of system information"
    assert payload.get("build_id"), "missing build_id property of system information"


def test_get_system_healthcheck(api_client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        system.db,
        "cypher_query",
        lambda query: observed.append(query) or ([[1]], None),
    )
    response = api_client.get("/system/healthcheck")
    assert_response_status_code(response, 200)
    assert observed == ["RETURN 1 AS ready"]


def test_system_healthcheck_fails_when_database_is_unreachable(monkeypatch):
    def unavailable(_query):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(system.db, "cypher_query", unavailable)

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(system.healthcheck())


def test_get_system_information_build_id(api_client):
    response = api_client.get("/system/information/build-id")
    assert_response_status_code(response, 200)
    assert response.text
