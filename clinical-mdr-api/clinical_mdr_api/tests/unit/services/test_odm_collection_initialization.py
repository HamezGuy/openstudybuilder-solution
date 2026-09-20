"""Real initializer, native link writers and decorator with staged persistence.

The transaction port commits or rolls back its staged graph. This is not a
Neo4j concurrency test; native root-lock behavior needs the isolated DB check.
"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from neomodel import db
from pydantic import ValidationError

from clinical_mdr_api.domain_repositories._utils.native_read_cache import native_read_cached
from clinical_mdr_api.domain_repositories.models.odm import OdmItemGroupRefRelation, OdmItemRefRelation
from clinical_mdr_api.domain_repositories.odms import generic_repository
from clinical_mdr_api.domains.enums import LibraryItemStatus
from clinical_mdr_api.domains.odms.utils import RelationType
from clinical_mdr_api.models.odms.collection_initialization import OdmCollectionInitializationInput
from clinical_mdr_api.models.odms.item import OdmItemParentGroup
from clinical_mdr_api.models.odms.item_group import OdmItemGroupItemPostInput
from clinical_mdr_api.services import _utils
from clinical_mdr_api.services.odms.collection_initialization import (
    OdmCollectionInitializationConflict, initialize_odm_collection,
)
from clinical_mdr_api.services.odms.forms import OdmFormService
from clinical_mdr_api.services.odms.item_groups import OdmItemGroupService
from clinical_mdr_api.services.odms.items import OdmItemService
from common.exceptions import BusinessLogicException


class StagedStore:
    def __init__(self):
        self.records = {}
        self.raw_records = {}
        self.staged = None
        self.staged_raw = None
        self.locks = []
        self.writes = []
        self.failure_at = None
        self.corrupt_after_write = False
        self.change_child_after_write = None
        self.transactions = []

    @property
    def state(self):
        return self.records if self.staged is None else self.staged

    @property
    def raw_state(self):
        return self.raw_records if self.staged_raw is None else self.staged_raw

    def transaction(self, database):
        store = self

        class Transaction:
            def __enter__(self):
                assert store.staged is None
                store.staged = deepcopy(store.records)
                store.staged_raw = deepcopy(store.raw_records)
                database._active_transaction = self
                return self

            def __exit__(self, kind, error, traceback):
                if kind is None:
                    store.records = store.staged
                    store.raw_records = store.staged_raw
                store.transactions.append("commit" if kind is None else "rollback")
                store.staged = None
                store.staged_raw = None
                database._active_transaction = None
                return False

        return Transaction()


class RelationRepository:
    def __init__(self, store, family):
        self.store, self.family = store, family
        self.root_class = SimpleNamespace(__label__=family + "Root")

    def lock_for_relationship_update(self, uid):
        assert self.store.staged is not None and db._active_transaction is not None
        self.store.locks.append((self.family, uid))

    def read_capture_collection(self, uid, collection):
        assert (self.family, uid) in self.store.locks
        if uid in self.store.raw_state:
            return deepcopy(self.store.raw_state[uid])
        rows = []
        for record in self.store.state[uid][collection]:
            properties = {
                key: deepcopy(value) for key, value in record.items()
                if key not in {"uid", "name", "oid", "version"} and value is not None
            }
            properties["mandatory"] = record["mandatory"] == "Yes"
            properties["vendor"] = json.dumps({
                "attributes": [
                    {key: attribute[key] for key in ("uid", "value")}
                    for attribute in record["vendor"]["attributes"]
                ],
            })
            rows.append({
                "properties": properties, "owner_uids": [record["uid"]],
                "current_owner_uids": [record["uid"]],
            })
        return rows

    def validate_capture_item_custody(self, uid, item_uid):
        assert self.family == "OdmItemGroup"
        self.lock_for_relationship_update(uid)
        # Model the existing native zero-or-one ITEM_REF ownership query. A
        # child's projected backlink alone cannot enumerate all incoming edges.
        for owner_uid, owner in self.store.state.items():
            if owner_uid != uid and any(row["uid"] == item_uid for row in owner.get("items", [])):
                raise BusinessLogicException(
                    msg=f"OdmItem with UID '{item_uid}' is already connected to another OdmItemGroup."
                )

    def add_relation(self, uid, relation_uid, relationship_type, parameters=None, **kwargs):
        assert (self.family, uid) in self.store.locks
        assert self.store.staged is not None
        self.store.writes.append((uid, relation_uid, deepcopy(parameters)))
        if self.store.failure_at == len(self.store.writes):
            raise RuntimeError("Second native child write failed")
        field = "item_groups" if self.family == "OdmForm" else "items"
        raw = self.store.raw_state.setdefault(uid, self.read_capture_collection(uid, field))
        # Use the same native relationship model/deflater as RelationshipManager.connect.
        # In particular strtobool supplies integers, while Neo4j stores BooleanProperty.
        model = OdmItemGroupRefRelation if field == "item_groups" else OdmItemRefRelation
        assert not (set(parameters) - set(model.defined_properties(aliases=False, rels=False)))
        native_relation = model(**deepcopy(parameters))
        properties = {
            key: value for key, value in model.deflate(native_relation.__properties__).items()
            if value is not None
        }
        raw.append({"properties": properties, "owner_uids": [relation_uid],
                    "current_owner_uids": [relation_uid]})
        child = self.store.state[relation_uid]
        relation = {**deepcopy(parameters), "uid": relation_uid, "oid": child["oid"],
                    "name": child["name"], "version": child["version"]}
        relation["mandatory"] = "Yes" if parameters["mandatory"] else "No"
        for attribute in relation["vendor"]["attributes"]:
            attribute.update(name="Native attribute", data_type="string", value_regex=".*", vendor_namespace_uid="Namespace")
        self.store.state[uid][field].append(relation)
        if field == "items":
            parent = self.store.state[uid]
            child["odm_item_group"] = OdmItemParentGroup(
                uid=parent["uid"], oid=parent["oid"], name=parent["name"],
            ).model_dump(mode="json")
        if self.store.change_child_after_write is not None:
            self.store.change_child_after_write(child)
        if self.store.corrupt_after_write:
            relation["order_number"] += 1

    def remove_relation(self, *args, **kwargs):
        raise AssertionError("Initialization must never remove existing relations")


class MemoryService:
    family = None

    def __init__(self, store):
        self.store = store
        self._repository = RelationRepository(store, self.family)
        self._repos = SimpleNamespace(**{
            "odm_form_repository": self._repository,
            "odm_item_group_repository": self._repository,
        })

    def __del__(self):
        pass

    @property
    def repository(self):
        return self._repository

    def _find_by_uid_or_raise_not_found(self, uid, **kwargs):
        record = self.store.state[uid]
        return SimpleNamespace(
            uid=uid, item_metadata=SimpleNamespace(status=LibraryItemStatus(record["status"])),
            odm_vo=SimpleNamespace(item_group_uids=[row["uid"] for row in record.get("item_groups", [])],
                                   item_uids=[row["uid"] for row in record.get("items", [])]),
            snapshot=deepcopy(record),
        )

    def _transform_aggregate_root_to_pydantic_model(self, aggregate):
        return deepcopy(aggregate.snapshot)

    def get_regex_patterns_of_attributes(self, uids):
        return {uid: ".*" for uid in uids}

    def are_attributes_vendor_compatible(self, attributes, kind):
        assert all(row.uid == "Attribute" for row in attributes)

    def can_connect_vendor_attributes(self, attributes):
        assert all(row.uid == "Attribute" for row in attributes)

    def attribute_values_matches_their_regex(self, attributes, patterns):
        assert all(row.uid in patterns for row in attributes)


class MemoryForm(MemoryService, OdmFormService):
    family = "OdmForm"


class MemoryGroup(MemoryService, OdmItemGroupService):
    family = "OdmItemGroup"


class MemoryItem(MemoryService, OdmItemService):
    family = "OdmItem"


@pytest.fixture
def native(monkeypatch):
    store = StagedStore()
    monkeypatch.setattr(db, "_active_transaction", None)
    monkeypatch.setattr(_utils, "TransactionProxy", lambda database: store.transaction(database))
    return store


def case(store, collection, count=2):
    parent_type, child_type = (MemoryForm, MemoryGroup) if collection == "item_groups" else (MemoryGroup, MemoryItem)
    store.records["Parent"] = {
        "uid": "Parent", "oid": "P.EXACT", "name": "Parent", "version": "0.1", "status": "Draft",
        "library_name": "Sponsor", collection: [], "independent_metadata": {"unknown": [None, False, 0, ""]},
    }
    children = []
    for index in range(count):
        uid = f"Child-{index}"
        store.records[uid] = {"uid": uid, "oid": f"C.{index}", "name": f"Child {index}",
                              "version": "0.1", "status": "Draft", "retained": {"source": [1, 2]}}
        relation = {
            "uid": uid, "order_number": 7 + index * 4, "mandatory": "No",
            "collection_exception_condition_oid": "Condition.source",
            "vendor": {"attributes": [{"uid": "Attribute", "value": "source value"}]},
        }
        if collection == "items":
            store.records[uid]["odm_item_group"] = None
            relation.update(key_sequence="2", method_oid="Method.source", imputation_method_oid=None,
                            role="Source role", role_codelist_oid=None)
        children.append(relation)
    request = OdmCollectionInitializationInput(
        expected_parent=deepcopy(store.records["Parent"]),
        expected_children={row["uid"]: deepcopy(store.records[row["uid"]]) for row in children},
        children=children,
    )
    return parent_type(store), child_type(store), request


def call(parent, child, request, collection):
    return initialize_odm_collection(parent, child, "Parent", request, collection=collection)


@pytest.mark.parametrize("collection", ["items", "item_groups"])
def test_actual_native_writer_preserves_full_bindings_and_exact_replay_is_noop(native, collection):
    parent, child, request = case(native, collection)
    original_request = deepcopy(request.model_dump(mode="json"))
    expected_children = deepcopy(request.expected_children)
    if collection == "items":
        for value in expected_children.values():
            value["odm_item_group"] = OdmItemParentGroup(
                uid="Parent", oid="P.EXACT", name="Parent",
            ).model_dump(mode="json")
    result = call(parent, child, request, collection)
    assert native.transactions == ["commit"]
    assert [row["order_number"] for row in result[collection]] == [7, 11]
    assert all(row["collection_exception_condition_oid"] == "Condition.source" for row in result[collection])
    assert all(row["vendor"]["attributes"][0]["value"] == "source value" for row in result[collection])
    if collection == "items":
        assert all(row["key_sequence"] == "2" and row["method_oid"] == "Method.source"
                   and row["role"] == "Source role" for row in result[collection])
    assert result["independent_metadata"] == request.expected_parent["independent_metadata"]
    assert {uid: native.records[uid] for uid in expected_children} == expected_children
    assert request.model_dump(mode="json") == original_request
    assert len(native.writes) == 2
    assert {uid for _, uid in native.locks} == {"Parent", "Child-0", "Child-1"}
    before = deepcopy(native.records)
    # Repeat the ORIGINAL empty-parent request after a potentially lost response.
    assert call(parent, child, request, collection) == result
    assert native.records == before
    assert len(native.writes) == 2
    assert native.transactions == ["commit", "commit"]


def test_original_prelink_snapshot_replay_accepts_only_the_committed_native_image(native):
    parent, child, request = case(native, "items")
    original_request = deepcopy(request.model_dump(mode="json"))
    # Establish the state through the real ordinary service, not the initializer
    # being tested, so old-code replay can be reproduced independently of its
    # first-write postcondition failure.
    with native.transaction(db):
        parent.repository.lock_for_relationship_update("Parent")
        parent.add_items(
            "Parent", [OdmItemGroupItemPostInput(**row) for row in request.children],
            override=False, preserve_order=True,
        )
    before, raw_before, writes = deepcopy(native.records), deepcopy(native.raw_records), len(native.writes)
    assert before["Child-0"]["odm_item_group"] == {"uid": "Parent", "oid": "P.EXACT", "name": "Parent"}
    assert call(parent, child, request, "items") == before["Parent"]
    assert native.records == before and native.raw_records == raw_before
    assert len(native.writes) == writes
    assert request.model_dump(mode="json") == original_request


def ownership_query_port(monkeypatch, rows):
    origin = object()
    child = SimpleNamespace(__label__="OdmItemValue")
    child_root = SimpleNamespace(has_latest_value=SimpleNamespace(single=lambda: child))
    child_type = SimpleNamespace(nodes=SimpleNamespace(get_or_none=lambda **_: child_root))
    parent_value = SimpleNamespace(item_ref=origin)
    parent_root = SimpleNamespace(
        __label__="OdmItemGroupRoot",
        has_latest_value=SimpleNamespace(single=lambda: parent_value),
    )
    parent_type = SimpleNamespace(
        __label__="OdmItemGroupRoot",
        nodes=SimpleNamespace(get_or_none=lambda **_: parent_root),
    )
    monkeypatch.setattr(generic_repository, "OdmItemRoot", child_type)
    observed = []

    def query(statement=None, params=None, **kwargs):
        statement = statement if statement is not None else kwargs["query"]
        observed.append((statement, deepcopy(params)))
        if "apoc.lock.nodes" in statement:
            assert params == {"uid": "Parent"}
            return [["Parent"]], ["uid"]
        assert "RETURN COUNT(*) > 0" in statement
        assert params == {"source_uid": "Child", "target_uid": "Parent"}
        return deepcopy(rows), ["COUNT(*) > 0"]

    monkeypatch.setattr(db, "cypher_query", query)

    class Repository(generic_repository.OdmGenericRepository):
        root_class = parent_type
        value_class = SimpleNamespace(__label__="OdmItemGroupValue")

    return Repository, origin, child, observed


@pytest.mark.parametrize("rows", [
    [], [[]], [[False, False]], [[False], [False]], [[0]], [[1]],
    [[None]], [["false"]], None, {"value": False},
], ids=[
    "empty", "zero_columns", "multiple_columns", "multiple_rows", "integer_zero",
    "integer_one", "null_value", "string_value", "null_result", "mapping_result",
])
def test_ownership_query_requires_one_boolean_aggregate(monkeypatch, rows):
    repository, _, _, observed = ownership_query_port(monkeypatch, rows)
    with pytest.raises(BusinessLogicException, match="ODM_RELATIONSHIP_OWNERSHIP_QUERY_UNPROVEN"):
        repository._get_origin_and_relation_node(
            "Parent", "Child", RelationType.ITEM, zero_or_one_relation=True,
        )
    assert len(observed) == 2


@pytest.mark.parametrize("foreign_owner", [False, True])
def test_ownership_query_preserves_actual_boolean_behavior(monkeypatch, foreign_owner):
    repository, origin, child, observed = ownership_query_port(monkeypatch, [[foreign_owner]])
    if foreign_owner:
        with pytest.raises(BusinessLogicException, match="already connected to another"):
            repository._get_origin_and_relation_node(
                "Parent", "Child", RelationType.ITEM, zero_or_one_relation=True,
            )
    else:
        result = repository._get_origin_and_relation_node(
            "Parent", "Child", RelationType.ITEM, zero_or_one_relation=True,
        )
        assert result == (origin, child)
    assert len(observed) == 2


@pytest.mark.parametrize("change", [
    "uid", "version", "attribute", "unknown", "backlink_parent", "backlink_oid",
    "backlink_name", "backlink_unknown", "backlink_missing", "backlink_null",
])
def test_original_request_replay_refuses_unrelated_child_changes(native, change):
    parent, child, request = case(native, "items")
    call(parent, child, request, "items")
    value = native.records["Child-0"]
    if change == "uid":
        value["uid"] = "Different-child"
    elif change == "version":
        value["version"] = "0.2"
    elif change == "attribute":
        value["name"] = "Changed clinical name"
    elif change == "unknown":
        value["retained"]["source"].append({"new": [None, False, 0, ""]})
    elif change == "backlink_parent":
        value["odm_item_group"]["uid"] = "Foreign-parent"
    elif change == "backlink_oid":
        value["odm_item_group"]["oid"] = "Different.oid"
    elif change == "backlink_name":
        value["odm_item_group"]["name"] = "Different parent"
    elif change == "backlink_unknown":
        value["odm_item_group"]["unrequested"] = {"keep": [None, False, ""]}
    elif change == "backlink_missing":
        del value["odm_item_group"]
    else:
        value["odm_item_group"] = None
    before, raw_before, writes = deepcopy(native.records), deepcopy(native.raw_records), len(native.writes)
    with pytest.raises(OdmCollectionInitializationConflict, match="CHILD_SNAPSHOT_CHANGED"):
        call(parent, child, request, "items")
    assert native.records == before and native.raw_records == raw_before
    assert len(native.writes) == writes
    assert native.transactions[-1] == "rollback"


@pytest.mark.parametrize("backlink", [
    {"uid": "Foreign-parent", "oid": "F.EXACT", "name": "Foreign"},
    {"uid": "Parent", "oid": "P.EXACT", "name": "Different name"},
    {"uid": "Parent", "oid": "P.EXACT", "name": "Parent", "unknown": None},
])
def test_complete_foreign_or_unproved_backlink_snapshot_cannot_be_rebound(native, backlink):
    parent, child, request = case(native, "items")
    native.records["Child-0"]["odm_item_group"] = deepcopy(backlink)
    request = request.model_copy(update={"expected_children": {
        uid: deepcopy(native.records[uid]) for uid in request.expected_children
    }})
    before = deepcopy(native.records)
    with pytest.raises(OdmCollectionInitializationConflict, match="CHILD_BACKLINK_CUSTODY_UNPROVEN"):
        call(parent, child, request, "items")
    assert native.records == before and native.raw_records == {}
    assert native.writes == [] and native.transactions == ["rollback"]


def test_missing_native_backlink_member_is_not_defaulted(native):
    parent, child, request = case(native, "items")
    del native.records["Child-0"]["odm_item_group"]
    request = request.model_copy(update={"expected_children": {
        uid: deepcopy(native.records[uid]) for uid in request.expected_children
    }})
    before = deepcopy(native.records)
    with pytest.raises(OdmCollectionInitializationConflict, match="CHILD_BACKLINK_CUSTODY_UNPROVEN"):
        call(parent, child, request, "items")
    assert native.records == before and native.raw_records == {}
    assert native.writes == [] and native.transactions == ["rollback"]


def test_exact_projected_backlink_does_not_hide_an_additional_foreign_owner(native):
    parent, child, request = case(native, "items")
    call(parent, child, request, "items")
    foreign = deepcopy(native.records["Parent"])
    foreign.update(uid="Foreign-parent", oid="F.EXACT", name="Foreign")
    native.records["Foreign-parent"] = foreign
    # The native child DTO selects one parent; keep its own-parent projection.
    # The complete native ownership query must still reject the other owner.
    assert native.records["Child-0"]["odm_item_group"]["uid"] == "Parent"
    before, raw_before, writes = deepcopy(native.records), deepcopy(native.raw_records), len(native.writes)
    with pytest.raises(BusinessLogicException, match="already connected to another"):
        call(parent, child, request, "items")
    assert native.records == before and native.raw_records == raw_before
    assert len(native.writes) == writes and native.transactions[-1] == "rollback"


@pytest.mark.parametrize("change", ["uid", "version", "attribute", "unknown", "foreign_backlink"])
def test_native_child_write_may_only_add_the_exact_parent_backlink(native, change):
    parent, child, request = case(native, "items")
    before = deepcopy(native.records)

    def change_child(value):
        if change == "uid":
            value["uid"] = "Wrong identity"
        elif change == "version":
            value["version"] = "0.2"
        elif change == "attribute":
            value["name"] = "Changed clinical name"
        elif change == "unknown":
            value["retained"]["source"].append({"unrequested": None})
        else:
            value["odm_item_group"]["uid"] = "Foreign-parent"

    native.change_child_after_write = change_child
    with pytest.raises(OdmCollectionInitializationConflict, match="CHILD_WRITE_DIVERGED"):
        call(parent, child, request, "items")
    assert native.records == before and native.raw_records == {}
    assert native.transactions == ["rollback"]


@pytest.mark.parametrize("collection", ["items", "item_groups"])
def test_same_version_concurrent_collection_edit_is_not_overwritten_or_appended(native, collection):
    parent, child, request = case(native, collection, count=1)
    existing = {**deepcopy(request.children[0]), "uid": "Independent-child",
                "name": "Independent child", "oid": "I.KEEP", "version": "3.0"}
    native.records["Parent"][collection] = [existing]
    before = deepcopy(native.records)
    assert before["Parent"]["version"] == request.expected_parent["version"]
    with pytest.raises(OdmCollectionInitializationConflict, match="EXISTING_CHILDREN_CONFLICT"):
        call(parent, child, request, collection)
    assert native.records == before
    assert native.writes == []
    assert native.transactions == ["rollback"]


@pytest.mark.parametrize("collection", ["items", "item_groups"])
@pytest.mark.parametrize("which", ["parent", "child"])
def test_full_snapshot_pin_detects_same_version_metadata_edit(native, collection, which):
    parent, child, request = case(native, collection, count=1)
    uid = "Parent" if which == "parent" else "Child-0"
    native.records[uid]["unrequested_metadata"] = {"new": False}
    before = deepcopy(native.records)
    with pytest.raises(OdmCollectionInitializationConflict, match="SNAPSHOT_CHANGED"):
        call(parent, child, request, collection)
    assert native.records == before
    assert native.writes == []


@pytest.mark.parametrize("which", ["parent", "child"])
def test_locked_guard_bypasses_a_real_native_read_cache(native, which):
    parent, child, request = case(native, "items", count=1)
    service, uid = (parent, "Parent") if which == "parent" else (child, "Child-0")
    service._find_by_uid_or_raise_not_found = native_read_cached(cache={})(
        service._find_by_uid_or_raise_not_found
    )
    service._find_by_uid_or_raise_not_found(uid)
    native.records[uid]["source_edit"] = {"retained": False}
    before = deepcopy(native.records)
    with pytest.raises(OdmCollectionInitializationConflict, match="SNAPSHOT_CHANGED"):
        call(parent, child, request, "items")
    assert native.records == before
    assert native.writes == []


@pytest.mark.parametrize("collection", ["items", "item_groups"])
@pytest.mark.parametrize("change", ["unknown_property", "parallel_edge", "unowned_target",
                                   "old_target_version", "vendor_duplicate_json_key"])
def test_full_persisted_inventory_cannot_be_hidden_by_native_dto_projection(native, collection, change):
    parent, child, request = case(native, collection, count=1)
    result = call(parent, child, request, collection)
    writes = len(native.writes)
    stored = native.raw_records["Parent"][0]
    if change == "unknown_property":
        stored["properties"]["unknown_qualifier"] = "Exact source-owned value"
    elif change == "parallel_edge":
        native.raw_records["Parent"].append(deepcopy(stored))
    elif change == "unowned_target":
        stored["owner_uids"] = []
    elif change == "old_target_version":
        stored["current_owner_uids"] = []
    else:
        stored["properties"]["vendor"] = '{"attributes":[],"attributes":[{"uid":"Attribute","value":"source value"}]}'
    before, raw_before = deepcopy(native.records), deepcopy(native.raw_records)
    request = request.model_copy(update={"expected_parent": result})
    with pytest.raises(OdmCollectionInitializationConflict):
        call(parent, child, request, collection)
    assert native.records == before
    assert native.raw_records == raw_before
    assert len(native.writes) == writes


@pytest.mark.parametrize("collection", ["items", "item_groups"])
def test_second_child_failure_rolls_back_native_links_and_retry_then_noop_replay(native, collection):
    parent, child, request = case(native, collection)
    before = deepcopy(native.records)
    native.failure_at = 2
    with pytest.raises(RuntimeError, match="Second native child"):
        call(parent, child, request, collection)
    assert native.records == before
    assert native.raw_records == {}
    assert len(native.writes) == 2
    assert native.transactions == ["rollback"]
    native.failure_at = None
    result = call(parent, child, request, collection)
    assert len(result[collection]) == 2
    assert native.transactions == ["rollback", "commit"]
    writes = len(native.writes)
    assert call(parent, child, request, collection) == result
    assert len(native.writes) == writes


@pytest.mark.parametrize("collection", ["items", "item_groups"])
def test_actual_writer_divergence_rolls_back_instead_of_receipting_modified_values(native, collection):
    parent, child, request = case(native, collection, count=1)
    before = deepcopy(native.records)
    native.corrupt_after_write = True
    with pytest.raises(OdmCollectionInitializationConflict, match="WRITE_DIVERGED"):
        call(parent, child, request, collection)
    assert native.records == before
    assert native.transactions == ["rollback"]


@pytest.mark.parametrize("collection", ["items", "item_groups"])
@pytest.mark.parametrize("property_name", ["mandatory", "collection_exception_condition_oid", "vendor", "unknown"])
def test_occupied_same_uid_conflicting_qualifiers_remain_byte_for_byte(native, collection, property_name):
    parent, child, request = case(native, collection, count=1)
    existing = deepcopy(request.children[0])
    if property_name == "mandatory":
        existing[property_name] = "Yes"
    elif property_name == "collection_exception_condition_oid":
        existing[property_name] = "Independent.condition"
    elif property_name == "vendor":
        existing[property_name]["attributes"][0]["value"] = "Independent value"
    else:
        existing["unknown_qualifier"] = {"retained": [False, None]}
    native.records["Parent"][collection] = [existing]
    request = request.model_copy(update={"expected_parent": deepcopy(native.records["Parent"])})
    before = deepcopy(native.records)
    with pytest.raises(OdmCollectionInitializationConflict):
        call(parent, child, request, collection)
    assert native.records == before
    assert native.writes == []


@pytest.mark.parametrize("collection", ["items", "item_groups"])
def test_changed_request_cannot_use_a_previously_occupied_snapshot_as_replacement_authority(native, collection):
    parent, child, original = case(native, collection, count=1)
    call(parent, child, original, collection)
    replacement = original.model_dump()
    replacement["expected_parent"] = deepcopy(native.records["Parent"])
    replacement["children"][0]["mandatory"] = "Yes"
    before = deepcopy(native.records)
    writes = len(native.writes)
    with pytest.raises(OdmCollectionInitializationConflict, match="EXISTING_CHILDREN_CONFLICT"):
        call(parent, child, OdmCollectionInitializationInput(**replacement), collection)
    assert native.records == before
    assert len(native.writes) == writes


@pytest.mark.parametrize("mutation", ["duplicate", "missing_snapshot", "wrong_snapshot_uid", "extra_snapshot", "extra_request_field"])
def test_request_requires_exact_child_snapshot_inventory(native, mutation):
    _, _, original = case(native, "items", count=1)
    value = original.model_dump()
    if mutation == "duplicate":
        value["children"].append(deepcopy(value["children"][0]))
    elif mutation == "missing_snapshot":
        value["expected_children"] = {}
    elif mutation == "wrong_snapshot_uid":
        value["expected_children"]["Child-0"]["uid"] = "Other"
    elif mutation == "extra_snapshot":
        value["expected_children"]["Other"] = {"uid": "Other"}
    else:
        value["permit_replace"] = True
    with pytest.raises(ValidationError):
        OdmCollectionInitializationInput(**value)
    assert native.writes == []
