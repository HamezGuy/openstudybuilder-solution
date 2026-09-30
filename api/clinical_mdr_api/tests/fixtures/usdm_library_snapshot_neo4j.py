"""Native library producers inside the verified, reserved Community fixture.

This extends only the test-owned label inventory. The original empty-graph,
container/relay/owner checks and native authentication fixture remain in force.
"""

import json

import pytest
from neomodel import db

from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import StudyComponentEnum
from clinical_mdr_api.services._meta_repository import MetaRepository
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.tests.fixtures.null_adjudication_neo4j import native_null_graph
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.auth.user import user


ACTIVITY_LABELS = {
    "ActivityRoot", "ActivityValue", "ActivityGrouping",
    "ActivityGroupRoot", "ActivityGroupValue", "ActivitySubGroupRoot", "ActivitySubGroupValue",
    "ActivityInstanceRoot", "ActivityInstanceValue",
    "ActivityInstanceGroupingRoot", "ActivityInstanceGroupingValue", "ActivityItem",
    "ActivityItemClassRoot", "ActivityItemClassValue",
    "ActivityInstanceClassRoot", "ActivityInstanceClassValue",
}


class NativeLibraryGraph:
    def __init__(self, graph):
        self.graph = graph
        self.nonce = graph.nonce
        self.created = 0

    def principal(self):
        return self.graph.principal()

    def own(self):
        rows, _ = db.cypher_query("MATCH (n) RETURN labels(n), n.null_adjudication_fixture")
        base = {"Counter", "Library", "ClinicalProgramme", "Project", "DomainStudyScope", "User"}
        for labels, mark in rows:
            assert mark in (None, self.nonce), "Another fixture wrote into the reserved graph"
            assert any(label in base | ACTIVITY_LABELS or label.startswith(("CT", "Study", "UnitDefinition"))
                       for label in labels), labels
        db.cypher_query(
            "MATCH (n) WHERE n.null_adjudication_fixture IS NULL SET n.null_adjudication_fixture=$fixture",
            {"fixture": self.nonce},
        )

    def new_study(self):
        self.created += 1
        with self.principal():
            study = TestUtils.create_study(
                number=str(2700 + self.created), acronym=f"USDMHIST{self.created}",
                project_number="123", description="Authored native library history witness",
            )
        self.graph.auth.user.study_ids.add(study.uid)
        self.own()
        return study.uid

    def lock(self, study_uid):
        with self.principal():
            with db.transaction:
                repository = MetaRepository().study_definition_repository
                study = repository.find_by_uid(study_uid, for_update=True)
                study.lock(author_id=user().id(), version_description="Authored native history snapshot")
                repository.save(study)
                version = str(study.latest_released_or_locked_metadata.ver_metadata.version_number)
            selected = StudyService().get_by_uid(
                study_uid, study_value_version=version,
                include_sections=[StudyComponentEnum.VERSION_METADATA],
            )
        self.own()
        return version, selected.current_metadata.version_metadata.version_timestamp


@pytest.fixture(scope="module")
def native_library_graph(native_null_graph):
    fixture = NativeLibraryGraph(native_null_graph)
    try:
        yield fixture
    finally:
        fixture.own()
        # Only these additional, explicitly owned native activity nodes are
        # removed here. The parent fixture verifies and cleans all other nodes.
        db.cypher_query("""
            MATCH (n {null_adjudication_fixture:$fixture})
            WHERE any(label IN labels(n) WHERE label IN $labels)
            DETACH DELETE n
        """, {"fixture": fixture.nonce, "labels": sorted(ACTIVITY_LABELS)})
        assert db.cypher_query("""
            MATCH (n) WHERE any(label IN labels(n) WHERE label IN $labels)
            RETURN count(n)
        """, {"labels": sorted(ACTIVITY_LABELS)})[0] == [[0]]
        print(json.dumps({"fixture": "native-library-snapshot", "additionalActivityNodesRemoved": True}))
