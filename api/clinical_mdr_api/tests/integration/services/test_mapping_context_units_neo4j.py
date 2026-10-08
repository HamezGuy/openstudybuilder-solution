"""Native unit lifecycle -> HTTP mapping context -> retained review identity.

Uses the existing collector-owned disposable graph fixture. Authentication is
the ordinary local test principal; this is data-custody evidence, not RBAC proof.
"""

# pytest fixtures are injected by parameter name
# pylint: disable=redefined-outer-name,unused-import

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neomodel import db
from starlette_context import request_cycle_context
from starlette_context.middleware import RawContextMiddleware

from clinical_mdr_api.models.dictionaries.dictionary_term import DictionaryTermEditInput
from clinical_mdr_api.routers.integrations.mapping_context import router
from clinical_mdr_api.services.concepts.unit_definitions.unit_definition import (
    UnitDefinitionService,
)
from clinical_mdr_api.services.dictionaries.dictionary_term_generic_service import (
    DictionaryTermGenericService,
)
from clinical_mdr_api.services.integrations.mapping_context import MappingContextService
from clinical_mdr_api.services.integrations.proposal_review import (
    Neo4jProposalReviewRepository,
    _context_candidate,
)
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


@pytest.mark.usefixtures("native")
def test_native_unit_metadata_history_http_and_retained_identity():
    # The reused fixture has verified an empty, explicitly owned graph before
    # creating its own nodes. Remove only this test's exact additional node IDs.
    original_ids = [
        row[0] for row in db.cypher_query("MATCH (n) RETURN elementId(n)")[0]
    ]
    try:
        with request_cycle_context(
            {
                "auth": dummy_auth_object(
                    dummy_access_token_claims(user_id="unit-custody-fixture")
                )
            }
        ):
            TestUtils.create_dummy_user("unit-custody-fixture")
            TestUtils.create_library()
            TestUtils.create_library(name="UCUM", is_editable=True)
            plain = TestUtils.create_unit_definition(
                name="Clinical score", conversion_factor_to_master=None
            )
            plain_readback = UnitDefinitionService().get_by_uid(plain.uid)
            assert plain_readback.version == "1.0"
            assert plain_readback.ucum is None
            codelist = TestUtils.create_dictionary_codelist(
                name="UCUM custody", library_name="UCUM"
            )
            term = TestUtils.create_dictionary_term(
                codelist_uid=codelist.codelist_uid,
                dictionary_id="mg",
                name="mg",
                library_name="UCUM",
            )
            unit = TestUtils.create_unit_definition(
                name="Temporal milligram",
                ucum=term.term_uid,
                conversion_factor_to_master=0.001,
            )
            cutoff = datetime.now(timezone.utc)
            dictionary = DictionaryTermGenericService()
            dictionary.create_new_version(term.term_uid)
            draft_cutoff = datetime.now(timezone.utc)
            dictionary.edit_draft(
                term.term_uid,
                DictionaryTermEditInput(
                    name="mg-updated",
                    name_sentence_case="mg-updated",
                    change_description="Owned historical-custody test",
                ),
            )
            dictionary.approve(term.term_uid)

        historical, incomplete = MappingContextService._units_v2(
            ["temporal milligram"], [], 10, cutoff
        )
        current, current_incomplete = MappingContextService._units_v2(
            ["temporal milligram"], [], 10, None
        )
        assert incomplete == current_incomplete == 0
        assert [
            (c.uid, c.version, c.ucum_expression, c.conversion_factor_to_master)
            for c in historical
        ] == [(unit.uid, "1.0", "mg", 0.001)]
        assert [
            (c.uid, c.version, c.ucum_expression, c.conversion_factor_to_master)
            for c in current
        ] == [(unit.uid, "1.0", "mg-updated", 0.001)]
        assert MappingContextService._units_v2(
            ["temporal milligram"], [], 10, draft_cutoff
        ) == ([], 1)

        # Authored package identity is explicitly pinned; no fake clinical terms.
        db.cypher_query(
            "CREATE (:CTCatalogue {name:'DDF CT'})-[:CONTAINS_PACKAGE]->(:CTPackage {uid:'DDF-owned',effective_date:date('2026-01-01')})"
        )
        packages = [
            {
                "catalogue_name": "DDF CT",
                "package_uid": "DDF-owned",
                "effective_date": "2026-01-01",
            }
        ]
        app = FastAPI()
        app.add_middleware(RawContextMiddleware)
        app.dependency_overrides[security.dependency] = dummy_user_test_auth
        app.include_router(router, prefix="/mapping")
        with TestClient(app) as client:
            v1 = client.post(
                "/mapping/contexts",
                json={
                    "requested_packages": packages,
                    "resource_families": ["units"],
                    "search_strings": ["Clinical score"],
                },
            )
            assert v1.status_code == 200, v1.text
            candidate = v1.json()["candidates"]["units"][0]
            assert (
                candidate["uid"],
                candidate["version"],
                candidate["ucum_expression"],
                candidate["dimension"],
                candidate["conversion_factor_to_master"],
            ) == (plain.uid, "1.0", None, None, None)
            v2 = client.post(
                "/mapping/contexts/v2",
                json={
                    "requested_packages": packages,
                    "as_of": cutoff.isoformat(),
                    "candidate_groups": [
                        {
                            "fact_id": "unit-fact",
                            "concept_id": "unit-concept",
                            "target_key": "primary",
                            "semantic_role": "unit",
                            "resource_family": "units",
                            "search_strings": ["Temporal milligram"],
                        }
                    ],
                },
            )
            assert v2.status_code == 200, v2.text
        response = v2.json()
        assert response["candidate_groups"][0]["complete"] is True
        retained = Neo4jProposalReviewRepository.get_context(response["context_hash"])
        assert retained["candidateGroups"] == response["candidate_groups"]
        candidate = _context_candidate(
            retained["candidateGroups"][0]["candidates"][0], response["context_hash"]
        )
        assert (
            candidate["uid"],
            candidate["version"],
            candidate["ucumExpression"],
            candidate["dimension"],
            candidate["conversionFactorToMaster"],
        ) == (unit.uid, "1.0", "mg", None, 0.001)
        assert candidate["contextHash"] == response["context_hash"]
        assert len(candidate["candidateKey"]) == 64
    finally:
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
