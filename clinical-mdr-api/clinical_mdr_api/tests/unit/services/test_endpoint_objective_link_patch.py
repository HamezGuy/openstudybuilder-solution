"""An objective-only edit preserves endpoint content and its level ordering."""

from types import SimpleNamespace
from unittest.mock import Mock

from clinical_mdr_api.domains.study_selections.study_selection_endpoint import (
    StudySelectionEndpointVO,
)
from clinical_mdr_api.models.study_selections.study_selection import StudySelectionEndpointInput
from clinical_mdr_api.services.studies.study_endpoint_selection import StudyEndpointSelectionService
from common.config import settings


def test_linking_an_objective_preserves_the_endpoint_definition():
    service = StudyEndpointSelectionService.__new__(StudyEndpointSelectionService)
    service.author = "technical-import"
    order_reader = Mock(return_value=2)
    service._repos = SimpleNamespace(
        endpoint_repository=SimpleNamespace(find_by_uid=Mock(return_value=SimpleNamespace(
            item_metadata=SimpleNamespace(version="1.0")))),
        timeframe_repository=SimpleNamespace(find_by_uid=Mock(return_value=SimpleNamespace(
            item_metadata=SimpleNamespace(version="2.0")))),
        ct_term_name_repository=SimpleNamespace(
            term_specific_order_by_uid_and_cl_submval=order_reader,
            term_specific_order_by_uid=Mock(side_effect=AssertionError("unscoped lookup")),
        ),
    )
    current = StudySelectionEndpointVO.from_input_values(
        endpoint_uid="source-endpoint", endpoint_version="1.0",
        endpoint_level_uid="source-secondary", endpoint_sublevel_uid="source-sublevel",
        endpoint_units=({"uid": "source-unit"},), unit_separator="/",
        timeframe_uid="source-timeframe", timeframe_version="2.0",
        study_objective_uid=None, study_selection_uid="study-endpoint",
        endpoint_level_order=2, author_id="source-author",
    )
    updated = service._patch_prepare_new_study_endpoint(
        StudySelectionEndpointInput(study_objective_uid="study-objective"), current
    )
    assert updated.study_objective_uid == "study-objective"
    for field in (
        "endpoint_uid", "endpoint_version", "endpoint_level_uid", "endpoint_sublevel_uid",
        "endpoint_units", "unit_separator", "timeframe_uid", "timeframe_version",
        "study_selection_uid", "endpoint_level_order",
    ):
        assert getattr(updated, field) == getattr(current, field)
    order_reader.assert_called_once_with(
        uid="source-secondary", cl_submval=settings.study_endpoint_level_cl_submval
    )
    assert current.study_objective_uid is None
