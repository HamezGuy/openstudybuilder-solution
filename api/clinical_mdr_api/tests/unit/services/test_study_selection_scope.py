"""Offline checks for authored endpoint applicability to arms, cohorts and epochs."""

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from clinical_mdr_api.domain_repositories.study_selections import (
    study_selection_scope_repository as scope_repository_module,
)
from clinical_mdr_api.domain_repositories.study_selections.study_selection_scope_repository import (
    StudySelectionScopeRepository,
)
from clinical_mdr_api.domains.study_selections.study_selection_criteria import (
    StudySelectionCriteriaAR,
    StudySelectionCriteriaVO,
)
from clinical_mdr_api.domains.study_selections.study_selection_endpoint import (
    StudySelectionEndpointsAR,
    StudySelectionEndpointVO,
)
from clinical_mdr_api.domains.study_selections.study_selection_objective import (
    StudySelectionObjectivesAR,
    StudySelectionObjectiveVO,
)
from clinical_mdr_api.domains.study_selections.study_selection_scope import (
    StudySelectionScopeVO,
    canonical_scope,
)
from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionCriteria,
    StudySelectionCriteriaApplicabilityInput,
    StudySelectionCriteriaInput,
    StudySelectionEndpoint,
    StudySelectionEndpointCreateInput,
    StudySelectionEndpointInput,
    StudySelectionEndpointTemplateSelectInput,
    StudySelectionObjectiveInput,
)
from clinical_mdr_api.models.syntax_instances.criteria import Criteria
from clinical_mdr_api.services.ddf.m11_preview import _authored_scope_text
from clinical_mdr_api.services.studies import (
    study_endpoint_selection as endpoint_service_module,
)
from clinical_mdr_api.services.studies.study_endpoint_selection import (
    StudyEndpointSelectionService,
)
from clinical_mdr_api.services.user_info import UserInfoService
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import AS_OF, STUDY_UID, VERSION
from common.exceptions import (
    AlreadyExistsException,
    BusinessLogicException,
    ValidationException,
)


@pytest.fixture(autouse=True)
def offline_author_names(monkeypatch):
    """VO factories resolve author names; these checks never open a database."""
    monkeypatch.setattr(
        UserInfoService, "get_author_username_from_id", classmethod(lambda _, uid: uid)
    )


SCOPE = StudySelectionScopeVO.from_values(
    applies_to_arm_uids=["StudyArm_2", "StudyArm_1"],
    applies_to_cohort_uids=["StudyCohort_1"],
    applies_to_epoch_uids=[],
)


def endpoint(uid="StudyEndpoint_1", scope=SCOPE, **changes):
    values = {
        "endpoint_uid": "Endpoint_1",
        "endpoint_version": "1.0",
        "endpoint_level_uid": None,
        "endpoint_sublevel_uid": None,
        "endpoint_units": None,
        "unit_separator": None,
        "timeframe_uid": None,
        "timeframe_version": None,
        "study_objective_uid": "StudyObjective_1",
        "study_selection_uid": uid,
        "endpoint_level_order": None,
        "author_id": "synthetic-author",
        "study_uid": "Study_1",
        "start_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "scope": scope,
    }
    values.update(changes)
    return StudySelectionEndpointVO.from_input_values(**values)


def test_scope_sets_are_sorted_unique_and_explicit():
    assert SCOPE.applies_to_arm_uids == ("StudyArm_1", "StudyArm_2")
    assert not canonical_scope(None)
    for invalid in (["StudyArm_1", "StudyArm_1"], [""], [" "], [5]):
        with pytest.raises(ValueError):
            canonical_scope(invalid)
    legacy = StudySelectionScopeVO.from_record({"applies_to_arm_uids": None})
    assert legacy.is_unscoped()
    assert StudySelectionScopeVO.from_record(SimpleNamespace()).is_unscoped()
    assert SCOPE.as_lists() == {
        "applies_to_arm_uids": ["StudyArm_1", "StudyArm_2"],
        "applies_to_cohort_uids": ["StudyCohort_1"],
        "applies_to_epoch_uids": [],
    }


def test_objective_unlink_keeps_scope_template_identity_and_acceptance():
    current = replace(
        endpoint(is_instance=False), accepted_version=True, author_username="author"
    )
    aggregate = StudySelectionEndpointsAR(
        _study_uid="Study_1", _study_endpoints_selection=(current,)
    )
    aggregate.deleting_study_objective("StudyObjective_1")
    (updated,) = aggregate.study_endpoints_selection
    assert updated == replace(current, study_objective_uid=None)


def test_duplicate_identity_includes_each_scope_dimension():
    same = endpoint("StudyEndpoint_2", scope=SCOPE)
    other = endpoint(
        "StudyEndpoint_3",
        scope=StudySelectionScopeVO.from_values(applies_to_arm_uids=["StudyArm_1"]),
    )
    StudySelectionEndpointsAR(
        _study_uid="Study_1", _study_endpoints_selection=(endpoint(), other)
    ).validate()
    with pytest.raises(AlreadyExistsException):
        StudySelectionEndpointsAR(
            _study_uid="Study_1", _study_endpoints_selection=(endpoint(), same)
        ).validate()


@pytest.mark.parametrize(
    "model",
    [
        StudySelectionEndpointInput,
        StudySelectionEndpointCreateInput,
        StudySelectionEndpointTemplateSelectInput,
    ],
)
def test_inputs_default_to_unscoped_and_refuse_null_or_blank(model):
    required = {
        StudySelectionEndpointInput: {},
        StudySelectionEndpointCreateInput: {
            "endpoint_data": {
                "endpoint_template_uid": "EndpointTemplate_1",
                "parameter_terms": [],
            }
        },
        StudySelectionEndpointTemplateSelectInput: {
            "endpoint_template_uid": "EndpointTemplate_1"
        },
    }[model]
    omitted = model(**required)
    assert omitted.applies_to_arm_uids == []
    assert "applies_to_arm_uids" not in omitted.model_fields_set
    explicit = model(**required, applies_to_epoch_uids=[])
    assert "applies_to_epoch_uids" in explicit.model_fields_set
    for value in (None, [""], "StudyArm_1"):
        with pytest.raises(ValidationError):
            model(**required, applies_to_arm_uids=value)


def patch_service(monkeypatch):
    service = StudyEndpointSelectionService.__new__(StudyEndpointSelectionService)
    service.author = "synthetic-author"
    version = SimpleNamespace(item_metadata=SimpleNamespace(version="1.0"))
    service._repos = SimpleNamespace(
        endpoint_repository=SimpleNamespace(find_by_uid=Mock(return_value=version)),
        timeframe_repository=SimpleNamespace(find_by_uid=Mock(return_value=None)),
        ct_term_name_repository=SimpleNamespace(),
    )
    validated = []
    monkeypatch.setattr(
        endpoint_service_module.StudySelectionScopeRepository,
        "validate_targets",
        staticmethod(lambda study_uid, scope: validated.append((study_uid, scope))),
    )
    return service, validated


def test_patch_preserves_omitted_dimensions_and_clears_only_explicit_empty(
    monkeypatch,
):
    service, validated = patch_service(monkeypatch)
    current = endpoint()
    unchanged = service._patch_prepare_new_study_endpoint(
        StudySelectionEndpointInput(study_objective_uid="StudyObjective_2"),
        current,
        study_uid="Study_1",
    )
    assert unchanged.scope == SCOPE
    cleared = service._patch_prepare_new_study_endpoint(
        StudySelectionEndpointInput(applies_to_arm_uids=[]), current, "Study_1"
    )
    assert cleared.scope == replace(SCOPE, applies_to_arm_uids=())
    replaced = service._patch_prepare_new_study_endpoint(
        StudySelectionEndpointInput(applies_to_epoch_uids=["StudyEpoch_9"]),
        current,
        "Study_1",
    )
    assert replaced.scope == replace(SCOPE, applies_to_epoch_uids=("StudyEpoch_9",))
    assert [study_uid for study_uid, _ in validated] == ["Study_1"] * 3


def test_response_sets_scope_explicitly_for_exclude_unset_routes():
    project = SimpleNamespace(name="Synthetic project", project_number="123")
    response = StudySelectionEndpoint.from_study_selection_endpoint(
        study_selection=endpoint(endpoint_uid=None, study_objective_uid=None),
        study_uid="Study_1",
        order=1,
        get_endpoint_by_uid_and_version=Mock(),
        get_latest_endpoint_by_uid=Mock(),
        get_timeframe_by_uid_and_version=Mock(),
        get_latest_timeframe=Mock(),
        find_codelist_term_by_uid_and_submval=Mock(),
        get_study_objective_by_uid=Mock(),
        find_project_by_study_uid=Mock(return_value=project),
        terms_at_specific_datetime=None,
        study_value_version="1",
    )
    dumped = response.model_dump(exclude_unset=True)
    assert dumped["applies_to_arm_uids"] == ["StudyArm_1", "StudyArm_2"]
    assert dumped["applies_to_cohort_uids"] == ["StudyCohort_1"]
    assert dumped["applies_to_epoch_uids"] == []


def test_targets_must_be_exact_current_selections(monkeypatch):
    calls = []

    def query(cypher, params):
        calls.append((cypher, params))
        return [[uid, 2 if uid == "StudyArm_2" else 1] for uid in params["uids"]][
            :1
        ], None

    monkeypatch.setattr(scope_repository_module.db, "cypher_query", query)
    with pytest.raises(ValidationException, match="StudyArm_2"):
        StudySelectionScopeRepository.validate_targets("Study_1", SCOPE)
    assert "HAS_STUDY_ARM" in calls[0][0] and ":LATEST]" in calls[0][0]
    monkeypatch.setattr(
        scope_repository_module.db,
        "cypher_query",
        lambda cypher, params: ([[uid, 2] for uid in params["uids"]], None),
    )
    with pytest.raises(ValidationException, match="ambiguous"):
        StudySelectionScopeRepository.validate_targets("Study_1", SCOPE)
    monkeypatch.setattr(
        scope_repository_module.db,
        "cypher_query",
        lambda cypher, params: ([[uid, 1] for uid in params["uids"]], None),
    )
    StudySelectionScopeRepository.validate_targets("Study_1", SCOPE)


def test_referenced_target_refuses_deletion(monkeypatch):
    monkeypatch.setattr(
        scope_repository_module.db,
        "cypher_query",
        lambda cypher, params: ([["StudyEndpoint_1"]], None),
    )
    with pytest.raises(BusinessLogicException, match="StudyEndpoint_1"):
        StudySelectionScopeRepository.refuse_referenced_target(
            "Study_1", "applies_to_arm_uids", "StudyArm_1"
        )
    monkeypatch.setattr(
        scope_repository_module.db, "cypher_query", lambda cypher, params: ([], None)
    )
    StudySelectionScopeRepository.refuse_referenced_target(
        "Study_1", "applies_to_epoch_uids", "StudyEpoch_1"
    )
    with pytest.raises(ValueError):
        StudySelectionScopeRepository.current_references("Study_1", "arm", "x")


def objective(uid, scope=SCOPE, **changes):
    values = {
        "objective_uid": "ObjectiveTemplate_1",
        "objective_version": "1.0",
        "objective_level_uid": None,
        "objective_level_order": 1,
        "author_id": "synthetic-author",
        "study_uid": "Study_1",
        "study_selection_uid": uid,
        "is_instance": False,
        "accepted_version": True,
        "scope": scope,
    }
    values.update(changes)
    return StudySelectionObjectiveVO.from_input_values(**values)


def criterion(uid, scope=SCOPE, **changes):
    values = {
        "syntax_object_uid": "Criteria_1",
        "syntax_object_version": "1.0",
        "author_id": "synthetic-author",
        "criteria_type_uid": "Inclusion",
        "criteria_type_order": 1,
        "study_uid": "Study_1",
        "study_selection_uid": uid,
        "accepted_version": True,
        "key_criteria": True,
        "scope": scope,
    }
    values.update(changes)
    return StudySelectionCriteriaVO.from_input_values(**values)


def test_objective_reorder_keeps_template_identity_acceptance_and_scope():
    aggregate = StudySelectionObjectivesAR(
        _study_uid="Study_1",
        _study_objectives_selection=(
            objective("StudyObjective_1"),
            objective("StudyObjective_2", objective_uid="ObjectiveTemplate_2"),
        ),
    )
    aggregate.set_new_order_for_selection("StudyObjective_1", 2, "reordering-author")
    moved = next(
        item
        for item in aggregate.study_objectives_selection
        if item.study_selection_uid == "StudyObjective_1"
    )
    assert moved.author_id == "reordering-author"
    assert (moved.is_instance, moved.accepted_version, moved.scope) == (
        False,
        True,
        SCOPE,
    )


def test_criteria_reorder_keeps_acceptance_key_and_scope():
    aggregate = StudySelectionCriteriaAR(
        _study_uid="Study_1",
        _study_criteria_selection=(
            criterion("StudyCriteria_1"),
            criterion(
                "StudyCriteria_2",
                syntax_object_uid="Criteria_2",
                criteria_type_order=2,
            ),
        ),
    )
    aggregate.set_new_order_for_selection("StudyCriteria_1", 2, "reordering-author")
    moved = next(
        item
        for item in aggregate.study_criteria_selection
        if item.study_selection_uid == "StudyCriteria_1"
    )
    assert moved.author_id == "reordering-author"
    assert (moved.accepted_version, moved.key_criteria, moved.scope) == (
        True,
        True,
        SCOPE,
    )


def test_objective_and_criteria_duplicates_include_scope():
    other = StudySelectionScopeVO.from_values(applies_to_epoch_uids=["StudyEpoch_1"])
    StudySelectionObjectivesAR(
        _study_uid="Study_1",
        _study_objectives_selection=(objective("A"), objective("B", scope=other)),
    ).validate()
    with pytest.raises(AlreadyExistsException):
        StudySelectionObjectivesAR(
            _study_uid="Study_1",
            _study_objectives_selection=(objective("A"), objective("B")),
        ).validate()
    StudySelectionCriteriaAR(
        _study_uid="Study_1",
        _study_criteria_selection=(criterion("A"), criterion("B", scope=other)),
    ).validate()
    with pytest.raises(AlreadyExistsException):
        StudySelectionCriteriaAR(
            _study_uid="Study_1",
            _study_criteria_selection=(criterion("A"), criterion("B")),
        ).validate()


def test_criteria_applicability_input_keeps_omitted_and_refuses_null_or_extra():
    omitted = StudySelectionCriteriaApplicabilityInput(applies_to_arm_uids=[])
    assert omitted.model_fields_set == {"applies_to_arm_uids"}
    for payload in (
        {"applies_to_cohort_uids": None},
        {"applies_to_epoch_uids": [""]},
        {"key_criteria": True},
    ):
        with pytest.raises(ValidationError):
            StudySelectionCriteriaApplicabilityInput(**payload)
    for model in (StudySelectionCriteriaInput, StudySelectionObjectiveInput):
        assert model.model_fields.get("applies_to_arm_uids") is not None


def test_usdm_keeps_scoped_criteria_out_of_the_whole_study_population():
    graph = NativeStudySource().graph
    graph["criteria"] = [
        StudySelectionCriteria(
            study_uid=STUDY_UID,
            study_version=VERSION,
            order=index,
            study_criteria_uid=uid,
            start_date=AS_OF,
            key_criteria=False,
            criteria=Criteria(
                uid=f"Criteria_{index}",
                name=f"Synthetic criterion {index}",
                name_plain=f"Synthetic criterion {index}",
                version="1.0",
                status="Final",
            ),
            **scope,
        )
        for index, (uid, scope) in enumerate(
            (
                ("Criteria_unscoped", {}),
                ("Criteria_arm", {"applies_to_arm_uids": ["StudyArm_000001"]}),
            ),
            start=1,
        )
    ]
    source = NativeStudySource(graph)
    with source.isolated():
        report = source.mapper().map_with_report(graph["study"], VERSION)
    design = report["document"]["study"]["versions"][0]["studyDesigns"][0]
    by_label = {item["label"]: item for item in design["eligibilityCriteria"]}
    assert design["population"]["criterionIds"] == [by_label["Criteria_unscoped"]["id"]]
    scoped = json.dumps(by_label["Criteria_arm"]["extensionAttributes"])
    assert "studyCriteriaScope" in scoped and "not-stated" in scoped
    assert "StudyArm_000001" in scoped
    assert "studyCriteriaScope" not in json.dumps(
        by_label["Criteria_unscoped"]["extensionAttributes"]
    )
    issues = [
        issue
        for issue in report["mappingReport"]["issues"]
        if issue["code"] == "USDM_SELECTION_SCOPE_NOT_STANDARD"
    ]
    assert [issue["sourcePath"] for issue in issues] == [
        "studyCriteria/Criteria_arm/applies_to"
    ]


def test_m11_preview_lists_each_authored_dimension_without_combining_them():
    assert _authored_scope_text({}) == "none authored"
    assert (
        _authored_scope_text(
            {
                "applies_to_arm_uids": ["StudyArm_1", "StudyArm_2"],
                "applies_to_cohort_uids": [],
                "applies_to_epoch_uids": ["StudyEpoch_1"],
            }
        )
        == "arms StudyArm_1, StudyArm_2; epochs StudyEpoch_1"
    )
