"""Approved native units -> real HTTP pages -> the actual importer lookup.

Uses the existing collector-owned disposable graph. The HTTP principal is the
ordinary fixture principal: this proves catalogue/consumer custody, not RBAC or
approval of a live catalogue. Importer construction/CT-package preflight and a
whole study import are deliberately outside this read-only lookup proof.
"""

# pytest fixtures are injected by parameter name
# pylint: disable=redefined-outer-name,unused-import,import-outside-toplevel

import json
import os
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from neomodel import config as neomodel_config
from neomodel import db
from opencensus.trace import execution_context
from opencensus.trace.tracer import Tracer
from starlette_context import request_cycle_context
from starlette_context.middleware import RawContextMiddleware

from clinical_mdr_api.routers.concepts.unit_definitions.unit_definitions import router
from clinical_mdr_api.tests.integration.services.test_native_item_observation_neo4j import (
    native,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.auth.dependencies import (
    dummy_access_token_claims,
    dummy_auth_object,
    dummy_user_test_auth,
    security,
)

pytestmark = pytest.mark.estate_fixture

# Both sides use their declared Python environment; the API fixture must not
# borrow or replace libraries from the importer's independent environment.
_IMPORTER_LOOKUP = """
import copy, json, logging, sys
from contextlib import redirect_stdout
from functools import partial
from importers.run_import_360i import Import360i, ImportCensus
from importers.utils.api_bindings import ApiBinding
from importers.utils.metrics import Metrics
request = json.load(sys.stdin)
api = object.__new__(ApiBinding)
api.api_base_url = request['origin']
api.api_headers = {'Accept': 'application/json'}
api.metrics = Metrics()
api.log = logging.getLogger('native-unit-catalogue-fixture')
api.get_all_from_api_paged = partial(api.get_all_from_api_paged, page_size=1)
worker = object.__new__(Import360i)
worker.api = api
worker.census = ImportCensus()
before = copy.deepcopy(request['payload'])
try:
    with redirect_stdout(sys.stderr):
        bindings = worker.ensure_units(request['payload'])
    result = {'bindings': bindings, 'census': worker.census.as_dict()}
except ValueError as error:
    result = {'error': str(error)}
assert request['payload'] == before
assert isinstance(worker.census.created, list) and not worker.census.created
print(json.dumps(result))
"""


def _resolve(python, origin, payload):
    environment = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")
        if key in os.environ
    }
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(
        Path(__file__).resolve().parents[5] / "import_sponsor_data"
    )
    result = subprocess.run(
        [python, "-c", _IMPORTER_LOOKUP],
        input=json.dumps({"origin": origin, "payload": payload}),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=25,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) <= 1024 * 1024
    return json.loads(result.stdout)


@contextmanager
def _loopback(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", lifespan="off", loop="asyncio")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    try:
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Owned unit HTTP fixture did not start")
            time.sleep(0.02)
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "Owned unit HTTP fixture did not stop"


def _payload(spellings):
    return {
        "odm": {
            "units": spellings,
            "unitGovernanceVersion": "measured-unit/2",
            "unitGovernanceKernel": "governed-unit/3",
            "unitGovernance": [{"key": "mg/kg", "spellings": spellings}],
        }
    }


@pytest.mark.usefixtures("native")
def test_full_name_approved_native_catalogue_through_actual_importer_pages(monkeypatch):
    python = os.environ.get("OSB_UNIT_IMPORTER_PYTHON")
    assert (
        python and Path(python).is_file()
    ), "Supply the declared importer Python runtime"

    original_ids = [
        row[0] for row in db.cypher_query("MATCH (n) RETURN elementId(n)")[0]
    ]
    try:
        with request_cycle_context(
            {
                "auth": dummy_auth_object(
                    dummy_access_token_claims(user_id="unit-catalogue-fixture")
                )
            }
        ):
            TestUtils.create_dummy_user("unit-catalogue-fixture")
            TestUtils.create_dummy_user("unknown-user")
            TestUtils.create_library()
            TestUtils.create_library(name="UCUM", is_editable=True)
            codelist = TestUtils.create_dictionary_codelist(
                name="Unit catalogue UCUM", library_name="UCUM"
            )
            term = TestUtils.create_dictionary_term(
                codelist_uid=codelist.codelist_uid,
                dictionary_id="mg/kg",
                name="mg/kg",
                library_name="UCUM",
            )
            approved = TestUtils.create_unit_definition(
                name="milligrams per kilogram", ucum=term.term_uid
            )
            draft = TestUtils.create_unit_definition(
                name="pending milligrams per kilogram", approve=False
            )
            TestUtils.create_unit_definition(name="Unrelated catalogue page")

        # Neomodel's per-thread connections use this exact owned driver as well.
        monkeypatch.setattr(neomodel_config, "DRIVER", db.driver)
        requests = []
        app = FastAPI()
        app.add_middleware(RawContextMiddleware)
        app.dependency_overrides[security.dependency] = dummy_user_test_auth

        @app.middleware("http")
        async def observe(request, call_next):
            execution_context.set_opencensus_tracer(Tracer())
            requests.append(
                (request.method, request.url.path, dict(request.query_params))
            )
            if request.method != "GET":
                return JSONResponse(
                    {"error": "Fixture lookup is read-only"}, status_code=405
                )
            return await call_next(request)

        app.include_router(router, prefix="/concepts/unit-definitions")
        nodes_before = db.cypher_query("MATCH (n) RETURN count(n)")[0]
        relationships_before = db.cypher_query("MATCH ()-[r]->() RETURN count(r)")[0]
        with _loopback(app) as origin:
            payload = _payload(["mg/kg", "milligrams per kilogram"])
            result = _resolve(python, origin, payload)
            assert result["bindings"] == {
                "mg/kg": approved.uid,
                "milligrams per kilogram": approved.uid,
            }
            assert (
                result["census"]["stopped"]
                == result["census"]["release_blockers"]
                == []
            )
            first_read = list(requests)
            assert len(first_read) == 4
            assert [query["page_number"] for _, _, query in first_read] == [
                "1",
                "2",
                "3",
                "4",
            ]
            assert first_read[0][2]["total_count"] == "True"
            assert all(
                query["total_count"] == "False" for _, _, query in first_read[1:]
            )
            for missing in [
                "Milligrams per kilogram",
                "missing milligrams per kilogram",
                draft.name,
            ]:
                result = _resolve(python, origin, _payload([missing]))
                assert isinstance(result["bindings"], dict) and not result["bindings"]
                assert (
                    result["census"]["stopped"] and result["census"]["release_blockers"]
                )
            before_mismatch = len(requests)
            payload["odm"]["unitGovernanceKernel"] = "governed-unit/2"
            assert (
                "OSB_UNIT_GOVERNANCE_VERSION_UNSUPPORTED"
                in _resolve(python, origin, payload)["error"]
            )
            assert len(requests) == before_mismatch

        assert all(
            method == "GET" and path == "/concepts/unit-definitions"
            for method, path, _ in requests
        )
        assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == nodes_before
        assert (
            db.cypher_query("MATCH ()-[r]->() RETURN count(r)")[0]
            == relationships_before
        )
    finally:
        # The parent fixture verifies its original nodes afterward. Remove only
        # the exact additions in this explicitly empty, owned disposable graph.
        extra_ids = [
            row[0]
            for row in db.cypher_query(
                "MATCH (n) WHERE NOT elementId(n) IN $original RETURN elementId(n)",
                {"original": original_ids},
            )[0]
        ]
        db.cypher_query(
            "MATCH (n) WHERE elementId(n) IN $owned DETACH DELETE n",
            {"owned": extra_ids},
        )
