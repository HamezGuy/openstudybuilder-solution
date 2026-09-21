"""Actual native writers, selected-path Cypher, dated OGM reads and USDM draft.

No storage/query mocks, live graph, clinical approval or complete-study claim.
The owned Community fixture and real native principal are explicit prerequisites.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from neomodel import db

from clinical_mdr_api.models.biomedical_concepts.activity_item_class import (
    ActivityInstanceClassRelInput, ActivityItemClassEditInput,
)
from clinical_mdr_api.models.concepts.activities.activity_item import ActivityItemCreateInput
from clinical_mdr_api.models.controlled_terminologies.ct_term_name import CTTermNameEditInput
from clinical_mdr_api.services.biomedical_concepts.activity_item_class import ActivityItemClassService
from clinical_mdr_api.services.controlled_terminologies.ct_term_name import CTTermNameService
from clinical_mdr_api.services.ddf.usdm_acquisition_mapping import project_acquisition
from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper
from clinical_mdr_api.services.ddf.usdm_mapping_context import MappingContext
from clinical_mdr_api.services.studies.study_activity_instance_snapshot import (
    read_study_activity_instance_definition, resolve_candidate_class_history,
)
from clinical_mdr_api.services.studies.study_compound_snapshot import StudyCompoundSourceError
from clinical_mdr_api.tests.fixtures.usdm_library_snapshot_neo4j import native_library_graph
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.config import settings


@pytest.fixture(scope="module")
def selected_activity(native_library_graph):
    graph = native_library_graph
    study_uid = graph.new_study()
    suffix = uuid4().hex[:12]
    with graph.principal():
        terms = {}
        for key, submission, term_value in (
            ("datatype", settings.stdm_odm_data_type_cl_submval, "float"),
            ("role", settings.stdm_role_cl_submval, "Result"),
            ("soa", settings.flowchart_group_cl_submval, "Assessments"),
        ):
            codelist = TestUtils.create_ct_codelist(
                name=f"{key} {suffix}", submission_value=submission, extensible=True, approve=True,
            )
            terms[key] = TestUtils.create_ct_term(
                codelist_uid=codelist.codelist_uid, submission_value=term_value,
                sponsor_preferred_name=term_value, sponsor_preferred_name_sentence_case=term_value,
                definition=f"Authored {key} definition", approve=True,
            )
        instance_class = TestUtils.create_activity_instance_class(
            name=f"Measurement {suffix}", definition="Authored class", order=1, level=1,
        )
        link = ActivityInstanceClassRelInput(
            uid=instance_class.uid, mandatory=False, is_adam_param_specific_enabled=False,
            is_additional_optional=False, is_default_linked=False,
        )
        item_class = TestUtils.create_activity_item_class(
            name=f"Native result {suffix}", order=1, definition="Original result definition",
            role_uid=terms["role"].term_uid, data_type_uid=terms["datatype"].term_uid,
            activity_instance_classes=[link],
        )
        unit = TestUtils.create_unit_definition(
            name=f"Native measurement unit {suffix}", definition="Authored unit", conversion_factor_to_master=1,
        )
        group = TestUtils.create_activity_group(name=f"Group {suffix}")
        subgroup = TestUtils.create_activity_subgroup(name=f"Subgroup {suffix}")
        activity = TestUtils.create_activity(
            name=f"Activity {suffix}", activity_groups=[group.uid], activity_subgroups=[subgroup.uid],
        )
        instance = TestUtils.create_activity_instance(
            name=f"Instance {suffix}", name_sentence_case=f"Instance {suffix}",
            activity_instance_class_uid=instance_class.uid,
            activities=[activity.uid], activity_groups=[group.uid], activity_subgroups=[subgroup.uid],
            activity_items=[ActivityItemCreateInput(
                activity_item_class_uid=item_class.uid, ct_terms=[], unit_definition_uids=[unit.uid],
                text_value=None, is_adam_param_specific=False, is_activity_instance_id_specific=False,
            )],
        )
        TestUtils.create_study_activity(
            study_uid=study_uid, soa_group_term_uid=terms["soa"].term_uid,
            activity_uid=activity.uid, activity_group_uid=group.uid, activity_subgroup_uid=subgroup.uid,
            activity_instance_uid=instance.uid,
        )
        rows, _ = db.cypher_query("""
            MATCH (:StudyRoot {uid:$study})-[:LATEST]->(:StudyValue)-[:HAS_STUDY_ACTIVITY_INSTANCE]->
                (selection:StudyActivityInstance)-[:HAS_SELECTED_ACTIVITY_INSTANCE]->(value:ActivityInstanceValue)
                <-[state:HAS_VERSION]-(:ActivityInstanceRoot {uid:$instance})
            WHERE state.status='Final'
            RETURN DISTINCT selection.uid, state.version
        """, {"study": study_uid, "instance": instance.uid})
        assert len(rows) == 1
    version, as_of = graph.lock(study_uid)
    case = SimpleNamespace(
        graph=graph, study_uid=study_uid, version=version, as_of=as_of,
        instance_uid=instance.uid, instance_version=rows[0][1], selection_uid=rows[0][0],
        item_class_uid=item_class.uid, class_link=link, terms=terms, unit_uid=unit.uid,
    )
    with graph.principal():
        case.before = read(case)
    assert case.before["nativeSnapshot"]["issues"] == []
    return case


def read(case, **changes):
    return read_study_activity_instance_definition(
        case.instance_uid, case.instance_version,
        **{"study_uid": case.study_uid, "study_value_version": case.version,
           "study_activity_instance_uid": case.selection_uid, "as_of": case.as_of, **changes},
    )


def test_actual_selected_activity_preserves_old_class_and_ct_after_publication(selected_activity):
    case = selected_activity
    with case.graph.principal():
        service = ActivityItemClassService()
        service.create_new_version(case.item_class_uid)
        service.edit_draft(case.item_class_uid, ActivityItemClassEditInput(
            name="Later changed class", definition="Later changed definition",
            activity_instance_classes=[case.class_link], change_description="Authored later publication",
        ))
        service.approve(case.item_class_uid)
        term_service = CTTermNameService()
        term_uid = case.terms["datatype"].term_uid
        term_service.create_new_version(term_uid)
        term_service.edit_draft(term_uid, CTTermNameEditInput(
            sponsor_preferred_name="Later display datatype", sponsor_preferred_name_sentence_case="Later display datatype",
            change_description="Authored later display publication",
        ))
        term_service.approve(term_uid)
        after = read(case)
        candidate = resolve_candidate_class_history(
            [{"activity_item": {"activityItemClassUid": case.item_class_uid}}],
            case.study_uid, case.version,
        )[0]["activity_item"]
    case.graph.own()
    old_item, item = case.before["activity_items"][0], after["activity_items"][0]
    assert item["nativeIdentity"] == old_item["nativeIdentity"]
    assert item["activity_item_class"]["source"]["properties"] == old_item["activity_item_class"]["source"]["properties"]
    assert item["activity_item_class"]["source"]["valueIdentity"] == old_item["activity_item_class"]["source"]["valueIdentity"]
    assert item["activity_item_class"]["dataType"]["term"]["name"]["properties"]["name"] == "float"
    assert item["unit_definitions"][0]["uid"] == case.unit_uid
    assert candidate["activityItemClassSource"]["valueIdentity"] == item["activity_item_class"]["source"]["valueIdentity"]
    assert after["nativeSnapshot"]["issues"] == []
    print(json.dumps({
        "case": "actual-native-activity-changing-class-and-ct", "studyUid": case.study_uid,
        "studyValueVersion": case.version, "selectedSnapshot": after["nativeSnapshot"],
    }))


@pytest.mark.parametrize("change", [
    {"study_activity_instance_uid": "UnselectedActivity"},
    {"study_value_version": "9999.0"},
])
def test_actual_selected_path_refuses_other_identity_or_study_version(selected_activity, change):
    with selected_activity.graph.principal(), pytest.raises(StudyCompoundSourceError, match="SELECTED_PATH_NOT_UNIQUE"):
        read(selected_activity, **change)


def test_actual_native_acquisition_reaches_typed_review_without_fake_requiredness(selected_activity):
    case = selected_activity
    with case.graph.principal():
        definition = read(case)
    mapper = USDMMapper(*(lambda *_args, **_kwargs: pytest.fail("Unrelated source domain") for _ in range(8)))
    mapper._study_uid, mapper._study_value_version, mapper._study_as_of = case.study_uid, case.version, case.as_of
    mapper._context = MappingContext(allow_incomplete=True)
    version = SimpleNamespace(id="NativeVersion", biomedicalConcepts=[])
    project_acquisition(
        mapper, SimpleNamespace(study_activity_instance_uid=case.selection_uid),
        definition, version, SimpleNamespace(id="NativeDesign"),
    )
    prop = version.biomedicalConcepts[0].properties[0].model_dump(mode="json", exclude_unset=True)
    assert prop["datatype"] == "float"
    assert prop["name"] == definition["activity_items"][0]["activity_item_class"]["source"]["properties"]["name"]
    assert "isEnabled" not in prop and "isRequired" not in prop and "code" not in prop
    review = next(issue["executionReview"] for issue in mapper._context.issues
                  if issue["code"] == "USDM_PROPERTY_ACQUISITION_REVIEW_REQUIRED")
    assert review["sourceScope"]["nativeAsOf"] == case.as_of.isoformat()
    assert review["canonicalScope"]["propertyId"] == prop["id"]
