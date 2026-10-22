"""Every platform-control refusal carries its machine code as a top-level `code`.

The routes are enumerated from the router's own OpenAPI paths, so a new route
is covered without editing this test. Each request runs twice: through the
main API's shared error handlers, and through FastAPI's default handler, whose
`detail` is exactly what the route raised. The shared body must keep that
status and message and add the same machine code at the top level.
"""

# Pytest resolves the fixture by parameter name.
# pylint: disable=redefined-outer-name

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware import Middleware
from starlette_context import context
from starlette_context.middleware import RawContextMiddleware

from clinical_mdr_api.routers.integrations import native_identity
from common.auth.dependencies import (
    dummy_access_token_claims,
    dummy_auth_object,
    validate_token,
)
from common.config import settings
from common.exception_handlers import register_exception_handlers
from common.models.error import MACHINE_CODE

PREFIX = "/integrations/platform-control"


def _client(shared: bool, principal) -> TestClient:
    app = FastAPI(middleware=[Middleware(RawContextMiddleware)])
    if shared:
        register_exception_handlers(app)
    app.include_router(native_identity.router, prefix=PREFIX)

    async def authenticated():
        context["auth"] = principal

    app.dependency_overrides[validate_token] = authenticated
    return TestClient(app, raise_server_exceptions=False)


def _reference_code(detail) -> str | None:
    if isinstance(detail, dict):
        detail = detail.get("code")
    return (
        detail if isinstance(detail, str) and MACHINE_CODE.fullmatch(detail) else None
    )


@pytest.fixture
def principal():
    """An authenticated caller with no platform purpose, capability or study."""
    return dummy_auth_object(dummy_access_token_claims(user_id="platform-probe"))


def _operations(client):
    for path, operations in sorted(client.app.openapi()["paths"].items()):
        assert path.startswith(PREFIX)
        for method in sorted(operations):
            yield method.upper(), path


def _sweep(principal):
    shared, reference = _client(True, principal), _client(False, principal)
    results = []
    for method, path in _operations(shared):
        concrete = re.sub(r"\{[^}]+\}", "synthetic", path)
        kwargs = {"headers": {"x-platform-study-id": "synthetic-platform-study"}}
        if method == "POST":
            kwargs["json"] = {}
        body = shared.request(method, concrete, **kwargs)
        expected = reference.request(method, concrete, **kwargs)
        detail = expected.json().get("detail")
        assert body.status_code == expected.status_code, (method, path)
        assert body.status_code >= 400, (method, path)
        code = _reference_code(detail)
        assert body.json().get("code") == code, (method, path, body.text)
        if code is not None:
            assert body.json()["message"] == str(detail), (method, path)
        results.append((method, path, body.status_code, code))
    return results


def test_every_platform_control_refusal_names_its_code_at_top_level(principal):
    results = _sweep(principal)
    # The router serves every platform-control route, the regimen command among them.
    assert ("POST", PREFIX + "/commands/regimen") in {
        (method, path) for method, path, _, _ in results
    }
    assert len(results) >= 27
    # Every route refuses this caller with a machine code, not a bare status.
    assert all(code for _, _, _, code in results), results


def test_with_commands_enabled_scope_and_action_refusals_also_carry_codes(
    principal, monkeypatch
):
    monkeypatch.setattr(settings, "platform_commands_prototype_enabled", True)
    monkeypatch.setattr(settings, "deployment_environment", "test")
    results = _sweep(principal)
    codes = {code for _, _, _, code in results}
    assert "OSB_PLATFORM_COMMAND_SCOPE_DENIED" in codes
    assert any(code and code.endswith("_UNSUPPORTED") for code in codes), codes
    statuses = {(path, status) for _, path, status, _ in results}
    assert (PREFIX + "/commands/regimen", 422) in statuses
