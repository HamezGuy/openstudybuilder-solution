"""Exercise the actual repository decorators used by guarded ODM reads.

Only native persistence is substituted here. These tests do not claim database
locking proof; the separate disposable Neo4j check owns that requirement.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories._utils.native_read_cache import (
    uncached_native_reads,
)
from clinical_mdr_api.domain_repositories.controlled_terminologies import (
    ct_term_generic_repository,
)
from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_codelist_name_repository import (
    CTCodelistNameRepository,
)
from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_term_attributes_repository import (
    CTTermAttributesRepository,
)
from clinical_mdr_api.domain_repositories.library_item_repository import (
    LibraryItemRepositoryImplBase,
)
from clinical_mdr_api.domain_repositories.odms.form_repository import FormRepository
from clinical_mdr_api.domain_repositories.odms.item_group_repository import (
    ItemGroupRepository,
)
from clinical_mdr_api.domain_repositories.odms.item_repository import ItemRepository
from clinical_mdr_api.services.odms.generic_service import OdmGenericService


@pytest.fixture(autouse=True)
def clear_repository_caches():
    caches = (
        LibraryItemRepositoryImplBase.cache_store_item_by_uid,
        LibraryItemRepositoryImplBase.cache_store_term_by_uid_and_submval,
    )
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()


def native_reader(monkeypatch, repository_type):
    repository = object.__new__(repository_type)
    state = {"source": {"exact": [None, False, 0, "before"]}, "reads": 0, "locks": []}
    root, library, relation = SimpleNamespace(), SimpleNamespace(), SimpleNamespace()

    def read_root(uid):
        assert uid == "Pinned-root"
        state["reads"] += 1
        return root, library

    def value_relations(_root):
        assert _root is root
        latest = SimpleNamespace(single=lambda: deepcopy(state["source"]))
        return None, latest, None, None, None

    def aggregate(**values):
        return SimpleNamespace(snapshot=deepcopy(values["value"]))

    monkeypatch.setattr(repository, "_get_root_and_library", read_root)
    monkeypatch.setattr(repository, "_get_version_relation_keys", value_relations)
    monkeypatch.setattr(repository, "_get_latest_version", lambda *_: relation)
    monkeypatch.setattr(
        repository,
        "_create_aggregate_root_instance_based_on_return_counts",
        aggregate,
    )
    monkeypatch.setattr(repository, "_lock_object", state["locks"].append)
    return repository, state


def service_read(repository, *, for_update=False):
    service = SimpleNamespace(repository=repository, aggregate_class=SimpleNamespace)
    return OdmGenericService._find_by_uid_or_raise_not_found(
        service, "Pinned-root", for_update=for_update
    )


@pytest.mark.parametrize("repository_type", [FormRepository, ItemGroupRepository, ItemRepository])
def test_guard_scope_reaches_actual_odm_repository_cache(monkeypatch, repository_type):
    repository, state = native_reader(monkeypatch, repository_type)
    original = service_read(repository)
    state["source"] = {"exact": [None, False, 0, "changed without a version increment"]}
    assert service_read(repository) is original
    assert state["reads"] == 1

    with uncached_native_reads():
        observed = service_read(repository)
        assert observed.snapshot == state["source"]
        assert observed.snapshot != original.snapshot
        assert state["reads"] == 2

    # An authoritative read neither consults nor populates the ordinary cache.
    assert service_read(repository) is original
    assert state["reads"] == 2


@pytest.mark.parametrize("repository_type", [FormRepository, ItemGroupRepository, ItemRepository])
def test_update_read_always_reenters_actual_lock_and_storage(monkeypatch, repository_type):
    repository, state = native_reader(monkeypatch, repository_type)
    first = service_read(repository, for_update=True)
    state["source"] = {"exact": [None, False, 0, "second native reading"]}
    second = service_read(repository, for_update=True)
    assert first.snapshot != second.snapshot
    assert second.snapshot == state["source"]
    assert state["locks"] == ["Pinned-root", "Pinned-root"]
    assert state["reads"] == 2
    assert second.repository_closure_data[3].snapshot == second.snapshot


def test_exception_restores_nested_scope_without_replacing_normal_cache(monkeypatch):
    repository, state = native_reader(monkeypatch, ItemRepository)
    original = service_read(repository)
    state["source"] = {"exact": ["later"]}
    with pytest.raises(RuntimeError, match="native failure"):
        with uncached_native_reads():
            with uncached_native_reads():
                assert service_read(repository).snapshot == state["source"]
            assert service_read(repository).snapshot == state["source"]
            raise RuntimeError("native failure")
    assert service_read(repository) is original
    assert state["reads"] == 3


@pytest.mark.parametrize("for_update", [False, True])
def test_ct_term_outer_cache_and_nested_library_cache_both_observe_scope(monkeypatch, for_update):
    repository, state = native_reader(monkeypatch, CTTermAttributesRepository)
    version_root = SimpleNamespace(element_id="Pinned-root")
    term_root = SimpleNamespace(
        **{repository.relationship_from_root: SimpleNamespace(single=lambda: version_root)}
    )
    node_reads = []

    def find_term(**values):
        assert values == {"uid": "Exact-term"}
        node_reads.append(values)
        return term_root

    monkeypatch.setattr(
        ct_term_generic_repository,
        "CTTermRoot",
        SimpleNamespace(nodes=SimpleNamespace(get_or_none=find_term)),
    )

    # for_update is intentionally positional at this existing public boundary.
    def read():
        return repository.find_by_uid("Exact-term", None, None, None, for_update)

    first = read()
    state["source"] = {"exact": ["new selected term reading"]}
    if not for_update:
        assert read() is first
        with uncached_native_reads():
            second = read()
        assert read() is first
    else:
        second = read()
        assert state["locks"] == ["Pinned-root", "Pinned-root"]
    assert second.snapshot == state["source"]
    assert second.snapshot != first.snapshot
    assert len(node_reads) == 2
    assert state["reads"] == 2


def test_codelist_membership_cache_is_bypassed_without_importing_other_wip(monkeypatch):
    repository = object.__new__(CTCodelistNameRepository)
    state = {"term_name": "Original selected term", "reads": 0}
    names = (
        "term_uid", "term_name", "preferred_term", "submission_value", "order",
        "codelist_name", "codelist_uid", "codelist_submission_value",
    )

    def query(_query, params):
        assert params == {"cl_submval": "EXACT-LIST", "term_uid": "Exact-term"}
        state["reads"] += 1
        return [[
            "Exact-term", state["term_name"], "Preferred", "Exact-value", 7,
            "Selected list", "Exact-codelist", "EXACT-LIST",
        ]], list(names)

    monkeypatch.setattr(db, "cypher_query", query)
    first = repository.get_codelist_term_by_uid_and_submval("Exact-term", "EXACT-LIST")
    state["term_name"] = "Changed selected term"
    assert repository.get_codelist_term_by_uid_and_submval("Exact-term", "EXACT-LIST") is first
    with uncached_native_reads():
        second = repository.get_codelist_term_by_uid_and_submval("Exact-term", "EXACT-LIST")
    assert second != first
    assert second.ct_simple_codelist_term_vo.term_name == state["term_name"]
    assert repository.get_codelist_term_by_uid_and_submval("Exact-term", "EXACT-LIST") is first
    assert state["reads"] == 2
