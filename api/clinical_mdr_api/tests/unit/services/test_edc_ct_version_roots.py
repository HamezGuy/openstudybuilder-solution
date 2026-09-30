"""The full CT readers must honor selected historical attributes/name versions."""

from types import SimpleNamespace

import pytest

from clinical_mdr_api.domain_repositories.models.controlled_terminology import (
    CTCodelistAttributesRoot,
    CTCodelistNameRoot,
    CTTermAttributesRoot,
    CTTermNameRoot,
)


@pytest.mark.parametrize("root_type", [CTCodelistAttributesRoot, CTCodelistNameRoot, CTTermAttributesRoot, CTTermNameRoot])
def test_ct_version_roots_resolve_the_requested_value_and_its_own_relation(root_type):
    shared_value = object()
    selected = SimpleNamespace(version="1.0", end_date=10, start_date=1)
    latest = SimpleNamespace(version="2.0", end_date=None, start_date=20)

    class Relations:
        def match(self, *, version):
            return [shared_value] if version in {"1.0", "2.0"} else []

        def all_relationships(self, value):
            assert value is shared_value
            return [latest, selected]

    root = SimpleNamespace(has_version=Relations())
    root.get_value_for_version = lambda version: root_type.get_value_for_version(root, version)
    assert root_type.get_value_for_version(root, "1.0") is shared_value
    assert root_type.get_relation_for_version(root, "1.0") is selected
    assert root_type.get_relation_for_version(root, "2.0") is latest
    assert root_type.get_value_for_version(root, "missing") is None
