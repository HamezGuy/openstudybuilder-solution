"""Actual Study/CT producers on an explicitly owned, empty Community graph."""

import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from neomodel import db
from starlette.middleware import Middleware
from starlette_context import context, request_cycle_context
from starlette_context.middleware import RawContextMiddleware

from clinical_mdr_api.models.study_selections.study import StudyPatchRequestJsonModel
from clinical_mdr_api.routers.studies import studies
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.tests.fixtures.null_adjudication_http import (
    install_native_exception_handlers,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.auth.dependencies import (
    dummy_access_token_claims,
    dummy_auth_object,
    security,
)
from common.config import settings

SECTIONS = [
    "identification_metadata",
    "version_metadata",
    "high_level_study_design",
    "study_population",
    "study_intervention",
]
VALUE_PATH = "high_level_study_design.is_extension_trial"
COMPANION_PATH = "high_level_study_design.is_extension_trial_null_value_code"


def verified_fixture():
    """No database creation/replacement, nonloopback target, or generic fixture."""
    dsn = os.environ.get("OSB_NULL_ADJUDICATION_FIXTURE_DSN", "")
    container_id = os.environ.get("OSB_NULL_ADJUDICATION_FIXTURE_CONTAINER_ID", "")
    owner = os.environ.get("OSB_NULL_ADJUDICATION_FIXTURE_OWNER", "")
    if not dsn or not container_id or not owner.startswith("null-adjudication-"):
        pytest.fail("Explicit owned null-adjudication Community fixture is required")
    parsed = urlsplit(dsn)
    assert (
        parsed.scheme == "bolt"
        and parsed.hostname == "127.0.0.1"
        and parsed.path == "/neo4j"
    )
    inspected = json.loads(
        subprocess.check_output(
            ["docker", "inspect", container_id], text=True, timeout=10
        )
    )[0]
    assert inspected["Id"] == container_id
    assert inspected["Name"].startswith("/codex-osb-null-community-")
    assert inspected["Config"]["Labels"].get("codex.audit") == owner
    assert inspected["State"]["Running"]
    proxy_id = os.environ.get("OSB_NULL_ADJUDICATION_FIXTURE_PROXY_ID", "")
    assert proxy_id, "The internal graph requires the explicitly owned fixed TCP relay"
    proxy = json.loads(
        subprocess.check_output(["docker", "inspect", proxy_id], text=True, timeout=10)
    )[0]
    assert (
        proxy["Id"] == proxy_id
        and proxy["Config"]["Labels"].get("codex.audit") == owner
    )
    assert proxy["NetworkSettings"]["Ports"]["7687/tcp"] == [
        {"HostIp": "127.0.0.1", "HostPort": str(parsed.port)}
    ]
    assert len(inspected["NetworkSettings"]["Networks"]) == 1
    network_name, endpoint = next(
        iter(inspected["NetworkSettings"]["Networks"].items())
    )
    network = json.loads(
        subprocess.check_output(
            ["docker", "network", "inspect", endpoint["NetworkID"]],
            text=True,
            timeout=10,
        )
    )[0]
    assert network["Internal"] and network["Labels"].get("codex.audit") == owner
    assert network_name in proxy["NetworkSettings"]["Networks"]
    assert proxy["Config"]["Entrypoint"] == ["socat"]
    assert proxy["Config"]["Cmd"] == [
        "TCP-LISTEN:7687,fork,reuseaddr",
        f"TCP:{endpoint['IPAddress']}:7687",
    ]
    assert (
        settings.neo4j_dsn == dsn
    ), "Configuration must be fixed before importing the native application"
    return dsn, owner


def own_created_nodes(nonce):
    # The container is reserved, and the graph was empty before native seeding.
    # Refuse unexpected node kinds/other fixture marks before assigning custody.
    rows, _ = db.cypher_query("MATCH (n) RETURN labels(n), n.null_adjudication_fixture")
    simple = {
        "Counter",
        "Library",
        "ClinicalProgramme",
        "Project",
        "DomainStudyScope",
        "User",
    }
    for labels, mark in rows:
        assert mark in (None, nonce), "Another fixture wrote into the reserved graph"
        assert any(
            label in simple or label.startswith(("CT", "Study", "UnitDefinition"))
            for label in labels
        ), labels
    db.cypher_query(
        "MATCH (n) WHERE n.null_adjudication_fixture IS NULL SET n.null_adjudication_fixture=$fixture",
        {"fixture": nonce},
    )


def graph_fingerprint():
    """All node/relationship bytes, including history; omit only fixture tags."""
    nodes, _ = db.cypher_query(
        "MATCH (n) RETURN elementId(n), labels(n), properties(n) ORDER BY elementId(n)"
    )
    relationships, _ = db.cypher_query(
        "MATCH (a)-[r]->(b) RETURN elementId(r), elementId(a), type(r), elementId(b), properties(r) ORDER BY elementId(r)"
    )
    for row in nodes:
        row[1].sort()
        row[2].pop("null_adjudication_fixture", None)
    return hashlib.sha256(
        json.dumps([nodes, relationships], sort_keys=True, default=str).encode()
    ).hexdigest()


def observation(metadata, path):
    cursor = metadata
    for key in path.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            return {"present": False}
        cursor = cursor[key]
    return {"present": True, "value": deepcopy(cursor)}


def guarded_request(
    metadata, null_term_uid, value_path=VALUE_PATH, companion_path=COMPANION_PATH
):
    return {
        "contract_version": "StudyNullAdjudicationV1@1.0.0",
        "adjudications": [
            {
                "value_path": value_path,
                "companion_path": companion_path,
                "expected_value": observation(metadata, value_path),
                "expected_null_companion": observation(metadata, companion_path),
                "null_term_uid": null_term_uid,
            }
        ],
    }


class NativeNullGraph:
    def __init__(self, dsn, nonce):
        self.dsn = dsn
        self.nonce = nonce
        self.created = 0
        claims = dummy_access_token_claims(user_id=f"null-fixture-{nonce}")
        claims.sub = f"null-fixture-{nonce}"
        claims.tenant_id = f"null-fixture-{nonce}"
        self.auth = dummy_auth_object(claims)
        self.terms = {}

    @contextmanager
    def principal(self):
        with request_cycle_context({"auth": self.auth}):
            yield

    def client(self, *, legacy=False, roles=None):
        app = FastAPI(middleware=[Middleware(RawContextMiddleware)])
        install_native_exception_handlers(app)
        selected = APIRouter()
        paths = (
            {"/{study_uid}"}
            if legacy
            else {"/{study_uid}", "/{study_uid}/null-adjudications"}
        )
        selected.routes.extend(
            route
            for route in studies.router.routes
            if route.path in paths and route.methods <= {"GET", "PATCH"}
        )
        app.include_router(selected, prefix="/studies")
        authored_auth = deepcopy(self.auth)
        if roles is not None:
            authored_auth.user.roles = set(roles)

        async def authored_authentication():
            # Actual visibility and STUDY_READ/WRITE dependencies still execute.
            # Only the JWT transport is an authored local test principal.
            db.set_connection(url=self.dsn)
            context["auth"] = authored_auth
            try:
                yield
            finally:
                db.close_connection()

        app.dependency_overrides[security.dependency] = authored_authentication
        return TestClient(app)

    def new_study(self, parent=None):
        self.created += 1
        with self.principal():
            study = TestUtils.create_study(
                number=str(1700 + self.created),
                acronym=f"NULLAUDIT{self.created}",
                project_number="123",
                description=f"Authored Community null-guard fixture {self.created}",
                study_parent_part_uid=parent,
                subpart_acronym=f"PART{self.created}" if parent else None,
            )
        self.auth.user.study_ids.add(study.uid)
        own_created_nodes(self.nonce)
        return study.uid

    def readback(self, uid):
        response = self.client().get(
            f"/studies/{uid}",
            params=[("include_sections", section) for section in SECTIONS],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["uid"] == uid
        assert set(SECTIONS) <= set(body["current_metadata"])
        return body

    def ordinary(self, uid, metadata, parent=None):
        with self.principal():
            return StudyService().patch(
                uid,
                False,
                StudyPatchRequestJsonModel(
                    current_metadata=metadata, study_parent_part_uid=parent
                ),
            )


@pytest.fixture(scope="module")
def native_null_graph():
    dsn, owner = verified_fixture()
    db.set_connection(url=dsn)
    assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == [
        [0]
    ], "Only an empty reserved graph may be seeded"
    assert db.cypher_query("CALL dbms.components() YIELD edition RETURN edition")[
        0
    ] == [["community"]]
    assert db.cypher_query("RETURN apoc.version()")[0][0][
        0
    ], "Actual native producers require bundled APOC"
    nonce = str(uuid4())
    fixture = NativeNullGraph(dsn, nonce)
    try:
        # Community-compatible uniqueness only; do not use enterprise NODE KEY
        # or the older CREATE OR REPLACE DATABASE test setup.
        db.cypher_query(
            "CREATE CONSTRAINT null_adjudication_counter IF NOT EXISTS FOR (c:Counter) REQUIRE c.counterId IS UNIQUE"
        )
        with fixture.principal():
            TestUtils.create_library(name="Sponsor", is_editable=True)
            TestUtils.create_ct_catalogue()
            TestUtils.create_ct_codelist()
            TestUtils.create_study_fields_configuration()
            TestUtils.create_unit_definition(name=settings.day_unit_name)
            TestUtils.create_unit_definition(name=settings.week_unit_name)
            yes_no = TestUtils.create_ct_codelist(
                name="Authored Yes No",
                codelist_uid=settings.ct_uid_boolean_codelist,
                extensible=True,
                approve=True,
            )
            for term_uid, value, label in [
                (settings.ct_uid_boolean_yes, "Y", "Yes"),
                (settings.ct_uid_boolean_no, "N", "No"),
            ]:
                TestUtils.create_ct_term(
                    codelist_uid=yes_no.codelist_uid,
                    term_uid=term_uid,
                    submission_value=value,
                    sponsor_preferred_name=label,
                    sponsor_preferred_name_sentence_case=label,
                    approve=True,
                )
            nulls = TestUtils.create_ct_codelist(
                name="Authored Null Flavor",
                submission_value=settings.null_flavor_cl_submval,
                extensible=True,
                approve=True,
            )
            for flavor, label in [("NA", "Not applicable"), ("UNK", "Unknown")]:
                term = TestUtils.create_ct_term(
                    codelist_uid=nulls.codelist_uid,
                    submission_value=flavor,
                    sponsor_preferred_name=label,
                    sponsor_preferred_name_sentence_case=label,
                )
                fixture.terms[flavor] = term.term_uid
            programme = TestUtils.create_clinical_programme(
                name=f"Null guard Community {nonce}"
            )
            TestUtils.create_project(
                project_number="123", clinical_programme_uid=programme.uid
            )
        own_created_nodes(nonce)
        print(
            json.dumps(
                {
                    "fixtureOwner": owner,
                    "seedMode": "actual TestUtils Study/CT services",
                    "edition": "community",
                }
            )
        )
        yield fixture
    finally:
        # Establish that every remaining node is an authored native fixture
        # before the only delete in this integration setup.
        own_created_nodes(nonce)
        assert db.cypher_query(
            "MATCH (n) WHERE n.null_adjudication_fixture IS NULL OR n.null_adjudication_fixture<>$fixture RETURN count(n)",
            {"fixture": nonce},
        )[0] == [[0]]
        db.cypher_query(
            "MATCH (n {null_adjudication_fixture:$fixture}) DETACH DELETE n",
            {"fixture": nonce},
        )
        assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == [[0]]
        db.cypher_query("DROP CONSTRAINT null_adjudication_counter IF EXISTS")
        db.close_connection()
        print(
            json.dumps(
                {
                    "fixtureOwner": owner,
                    "cleanup": "authored nodes removed; graph empty",
                }
            )
        )
