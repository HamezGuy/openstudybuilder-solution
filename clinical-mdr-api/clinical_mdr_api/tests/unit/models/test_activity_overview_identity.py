import pytest

from clinical_mdr_api.models.concepts.activities.activity import ActivityOverview


@pytest.mark.parametrize("version", ["0.1", "1.0", "2.0"])
def test_activity_overview_preserves_root_identity_and_versioned_source(version):
    source = {
        "activity_root": {"uid": "Activity_source"},
        "activity_value": {
            "name": "Source assessment",
            "name_sentence_case": "Source assessment",
            "definition": "Exact source definition ≤ 15 days",
            "is_data_collected": False,
        },
        "activity_library_name": "Requested",
        "has_version": {"version": version, "status": "Final"},
        "hierarchy": [],
        "activity_instances": [],
        "all_versions": ["0.1", "1.0", "2.0"],
    }
    result = ActivityOverview.from_repository_input(source)
    assert result.activity.uid == "Activity_source"
    assert result.activity.version == version
    assert result.activity.definition == source["activity_value"]["definition"]
    assert result.activity.is_data_collected is False
    assert result.activity_groupings == []
