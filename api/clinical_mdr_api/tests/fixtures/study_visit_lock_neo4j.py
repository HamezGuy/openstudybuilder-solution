"""Ordinary native visit workflows on an explicitly owned Community graph."""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from neomodel import db
from starlette_context import request_cycle_context

from clinical_mdr_api.models.study_selections.study_visit import (
    StudyVisitCreateInput,
    StudyVisitEditInput,
)
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.services.studies.study_visit import StudyVisitService
from clinical_mdr_api.tests.fixtures.null_adjudication_neo4j import verified_fixture
from clinical_mdr_api.tests.integration.utils.factory_epoch import create_study_epoch
from clinical_mdr_api.tests.integration.utils.factory_visit import (
    DAY,
    WEEK,
    create_study_visit_codelists,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.auth.dependencies import dummy_access_token_claims, dummy_auth_object
from common.config import settings


class NativeVisitGraph:
    def __init__(self, dsn, owner, nonce):
        self.dsn = dsn
        self.owner = owner
        self.nonce = nonce
        self.created = 0
        self.day_uid = None
        claims = dummy_access_token_claims(user_id=f"visit-lock-{nonce}")
        claims.sub = f"visit-lock-{nonce}"
        claims.tenant_id = f"visit-lock-{nonce}"
        self.auth = dummy_auth_object(claims)

    @contextmanager
    def principal(self):
        with request_cycle_context({"auth": self.auth}):
            yield

    @contextmanager
    def worker_connection(self):
        # Neomodel transaction state and connections are local to this worker.
        db.set_connection(url=self.dsn)
        try:
            with self.principal():
                yield
        finally:
            db.close_connection()

    def own_created_nodes(self):
        rows, _ = db.cypher_query(
            "MATCH (n) RETURN labels(n), n.study_visit_lock_fixture"
        )
        simple = {
            "Counter",
            "Library",
            "ClinicalProgramme",
            "Project",
            "DomainStudyScope",
            "User",
        }
        prefixes = (
            "CT",
            "Study",
            "UnitDefinition",
            "NumericValue",
            "TimePoint",
            "VisitName",
        )
        for labels, mark in rows:
            assert mark in (None, self.nonce), "Another fixture wrote into this graph"
            assert any(
                label in simple or label.startswith(prefixes) for label in labels
            ), labels
        db.cypher_query(
            "MATCH (n) WHERE n.study_visit_lock_fixture IS NULL "
            "SET n.study_visit_lock_fixture=$fixture",
            {"fixture": self.nonce},
        )

    def new_case(self):
        """Create both visits through the ordinary public native services."""
        self.created += 1
        with self.principal():
            study = TestUtils.create_study(
                number=str(1800 + self.created),
                acronym=f"VISITLOCK{self.created}",
                project_number="123",
                description="Authored visit lock-order regression",
            )
            self.auth.user.study_ids.add(study.uid)
            TestUtils.set_study_standard_version(
                study_uid=study.uid,
                package_name=f"Visit lock CT {self.nonce}",
                effective_date=datetime.now(timezone.utc),
                create_codelists_and_terms_for_package=False,
            )
            # Ordinary study creation already creates its preferred day unit.
            preferred_unit = StudyService().get_study_preferred_time_unit(study.uid)
            assert preferred_unit.study_uid == study.uid
            assert preferred_unit.time_unit_uid == self.day_uid
            epoch = create_study_epoch("EpochSubType_0001", study_uid=study.uid)
            common = {
                "study_epoch_uid": epoch.uid,
                "visit_type": {"term_uid": "VisitType_0003"},
                "visit_contact_mode": {"term_uid": "VisitContactMode_0001"},
                "time_reference": {"term_uid": "VisitSubType_0005"},
                "time_unit_uid": self.day_uid,
                "visit_window_unit_uid": self.day_uid,
                "min_visit_window_value": -1,
                "max_visit_window_value": 1,
                "show_visit": True,
                "is_soa_milestone": False,
                "start_rule": "Original start rule",
                "end_rule": "Original end rule",
                "visit_class": "SINGLE_VISIT",
                "visit_subclass": "SINGLE_VISIT",
            }
            # Create the day-zero anchor first. The second creation inserts V1
            # before it, giving two ordinary nonmanual visits ordered V1, V2.
            second_input = {
                **common,
                "time_value": 0,
                "is_global_anchor_visit": True,
                "description": "Original V2 description",
            }
            service = StudyVisitService(study_uid=study.uid)
            second = service.create(study.uid, StudyVisitCreateInput(**second_input))
            first_input = {
                **common,
                "time_value": -10,
                "is_global_anchor_visit": False,
                "description": "Original V1 description",
            }
            first = service.create(study.uid, StudyVisitCreateInput(**first_input))
        self.own_created_nodes()
        return {
            "study_uid": study.uid,
            "first_uid": first.uid,
            "second_uid": second.uid,
            "first_input": StudyVisitEditInput(uid=first.uid, **first_input),
            "second_input": StudyVisitEditInput(uid=second.uid, **second_input),
        }

    def readback(self, study_uid):
        with self.principal():
            rows = StudyVisitService.get_all_visits(
                study_uid=study_uid, derive_props_based_on_timeline=False
            ).items
        return [
            row.model_dump(mode="json")
            for row in sorted(rows, key=lambda row: row.order)
        ]


@pytest.fixture(scope="module")
def native_visit_graph():
    # Reuse only the existing read-only container/network identity check. This
    # fixture has its own seeding, node custody and cleanup; it does not invoke
    # or modify the null-adjudication fixture's lifecycle.
    dsn, owner = verified_fixture()
    db.set_connection(url=dsn)
    assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == [
        [0]
    ], "Only an empty reserved graph may be seeded"
    assert db.cypher_query("CALL dbms.components() YIELD edition RETURN edition")[
        0
    ] == [["community"]]
    assert db.cypher_query("RETURN apoc.version()")[0][0][0]
    fixture = NativeVisitGraph(dsn, owner, str(uuid4()))
    try:
        db.cypher_query(
            "CREATE CONSTRAINT study_visit_lock_counter IF NOT EXISTS "
            "FOR (c:Counter) REQUIRE c.counterId IS UNIQUE"
        )
        with fixture.principal():
            TestUtils.create_library(
                name=settings.sponsor_library_name, is_editable=True
            )
            TestUtils.create_ct_catalogue()
            TestUtils.create_ct_codelist()
            create_study_visit_codelists(
                create_unit_definitions=False, use_test_utils=True
            )
            fixture.day_uid = TestUtils.create_unit_definition(**DAY).uid
            TestUtils.create_unit_definition(**WEEK)
            TestUtils.create_study_fields_configuration()
            yes_no = TestUtils.create_ct_codelist(
                name="Visit lock Yes No",
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
                )
            programme = TestUtils.create_clinical_programme(
                name=f"Visit lock Community {fixture.nonce}"
            )
            TestUtils.create_project(
                project_number="123", clinical_programme_uid=programme.uid
            )
        fixture.own_created_nodes()
        print(
            json.dumps(
                {
                    "fixtureOwner": owner,
                    "seedMode": "actual TestUtils Study/CT and ordinary Epoch/Visit services",
                    "edition": "community",
                }
            )
        )
        yield fixture
    finally:
        db.set_connection(url=dsn)
        fixture.own_created_nodes()
        assert db.cypher_query(
            "MATCH (n) WHERE n.study_visit_lock_fixture IS NULL "
            "OR n.study_visit_lock_fixture<>$fixture RETURN count(n)",
            {"fixture": fixture.nonce},
        )[0] == [[0]]
        db.cypher_query(
            "MATCH (n {study_visit_lock_fixture:$fixture}) DETACH DELETE n",
            {"fixture": fixture.nonce},
        )
        assert db.cypher_query("MATCH (n) RETURN count(n)")[0] == [[0]]
        db.cypher_query("DROP CONSTRAINT study_visit_lock_counter IF EXISTS")
        db.close_connection()
        print(
            json.dumps(
                {
                    "fixtureOwner": owner,
                    "cleanup": "authored visit fixture nodes removed; graph empty",
                }
            )
        )
