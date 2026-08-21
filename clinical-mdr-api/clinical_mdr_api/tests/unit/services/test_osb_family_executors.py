import pytest

from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.mapping_decision_v1 import executor_kind_for_family


@pytest.mark.parametrize("family,kind", [
    ("criteria_templates", "study"),
    ("objective_templates", "study"),
    ("units", "study"),
    ("controlled_terminology", "study"),
    ("odm_forms", "capture"),
    ("odm_item_groups", "capture"),
    ("odm_items", "capture"),
])
def test_declared_families_execute_or_route_explicitly(family: str, kind: str) -> None:
    assert executor_kind_for_family(family) == kind


def test_unknown_family_blocks_explicitly() -> None:
    with pytest.raises(OsbCandidateSetError) as error:
        executor_kind_for_family("not-a-family")
    assert error.value.code == "OSB_FAMILY_EXECUTOR_UNSUPPORTED"
