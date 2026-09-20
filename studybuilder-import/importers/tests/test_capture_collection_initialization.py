"""Public reviewed worker regressions; transport and persistence are synthetic."""

from copy import deepcopy

import pytest
import requests

from ..mappings.proposal_v2_native_operations import native_operation_plan
from ..run_import_osb_proposal_v2 import NativeOperationReconciliationError, _stable_hash
from ..utils.osb_proposal_db import OsbProposalIntegrityError
from .test_proposal_v2_native_operations import (
    candidate, envelope, mark_create_request, proposal_object, receipt,
)
from .test_proposal_v2_worker import (
    FakeNativeApi, FakeNativeDb, install_review_response, native_activity_proposal,
    native_activity_review, native_worker,
)


PATHS = {"OdmForm": "/odms/forms", "OdmItemGroup": "/odms/item-groups", "OdmItem": "/odms/items"}


def capture_body(family, name):
    body = {"name": name, "library_name": "Sponsor", "translated_texts": [
        {"language": "en", "text_type": "Description", "text": "Exact reviewed source"}
    ]}
    if family == "OdmItem":
        body["datatype"] = "integer"
    else:
        body["repeating"] = "No"
    if family == "OdmItemGroup":
        body["sdtm_domain_uids"] = []
    return body


def capture_case(collection="items", *, full_graph=False, relation_extra=None):
    families = [("group", "OdmItemGroup"), ("item", "OdmItem")]
    links = [("group-link", "OdmItemGroupItemLink", "group", "item")]
    if collection == "item_groups":
        families = [("form", "OdmForm"), ("group", "OdmItemGroup")]
        links = [("form-link", "OdmFormItemGroupLink", "form", "group")]
    if full_graph:
        families = [("form", "OdmForm"), ("group", "OdmItemGroup"), ("item", "OdmItem")]
        links = [("group-link", "OdmItemGroupItemLink", "group", "item"),
                 ("form-link", "OdmFormItemGroupLink", "form", "group")]
    objects, offers = [], []
    for name, family in families:
        offer = candidate(name, family, "offered-" + name)
        item = proposal_object(name, name, family, offer)
        item["source"] = {"values": [{"name": "nativeBody", "value": capture_body(family, name)}]}
        objects.append(item)
        offers.append(offer)
    for name, family, parent, child in links:
        offer = candidate(name, family, "offered-" + name)
        item = proposal_object(name, name, family, offer)
        item["source"] = {"values": [
            {"name": "parentProposalObjectId", "value": parent},
            {"name": "children", "value": [{"proposalObjectId": child, "relation": {
                "order_number": 7, "mandatory": "No", "vendor": {"attributes": []},
                **(relation_extra or {}),
            }}]},
        ]}
        objects.append(item)
        offers.append(offer)
    proposal = {**envelope(objects), "studyId": "source-study"}
    review = receipt(objects, offers, source_study_id="source-study", release_ready=True, release_blockers=[])
    review["execution_authorization"]["target_version_start_date"] = "2026-08-10T12:00:00+00:00"
    for item in objects:
        mark_create_request(review, item["proposalObjectId"])
    return proposal, review


class CaptureApi(FakeNativeApi):
    """Observable native HTTP boundary, including the former replacement route.

    The real native transaction/DTO implementation is covered in the API test
    module. This double verifies that the worker sends its captured snapshots.
    It also models the old route so the incompatible-parent regression fails on
    the unpatched worker without a mock suppressing the dangerous operation.
    """

    def __init__(self):
        super().__init__()
        self.documents = {}
        self.binding_writes = []
        self.initialize_requests = []
        self.before_initialize = None
        self.lose_response_once = False
        self.fail_create_once = None
        self.after_create = None
        self.created_snapshots = {}

    def seed(self, family, name, *, uid=None, body=None):
        record = {**deepcopy(capture_body(family, name) if body is None else body), "uid": uid or "Native-" + name,
                  "status": "Draft", "version": "0.1", "native_annotations": {"untouched": [None, False, 0]}}
        # The actual native response DTO includes these declared nullable fields.
        record.setdefault("oid", None)
        if family == "OdmItem":
            record["odm_item_group"] = None
        if family == "OdmForm":
            record["item_groups"] = []
        if family == "OdmItemGroup":
            record["items"] = []
            record["sdtm_domains"] = [{"term_uid": uid} for uid in record.pop("sdtm_domain_uids")]
        self.documents[record["uid"]] = (family, record)
        return record

    def proposal_v2_get(self, path, params=None):
        if path == "/studies/Study_1":
            return deepcopy(self.study)
        for family, base in PATHS.items():
            if path == base:
                return {"items": [deepcopy(record) for kind, record in self.documents.values() if kind == family]}
            if path.startswith(base + "/"):
                uid = path[len(base) + 1:]
                if uid in self.documents:
                    return deepcopy(self.documents[uid][1])
        raise AssertionError("Unexpected capture read: " + path)

    def proposal_v2_post(self, path, body, params=None, *, idempotency_key, proposal_object_id):
        self.post_calls.append((path, deepcopy(body), params, idempotency_key, proposal_object_id))
        for family, base in PATHS.items():
            if path == base:
                if proposal_object_id == self.fail_create_once:
                    self.fail_create_once = None
                    raise requests.ConnectionError("A later child could not be created")
                record = self.seed(family, body["name"], body=body)
                response = deepcopy(record)
                self.created_snapshots[record["uid"]] = deepcopy(record)
                if self.after_create is not None:
                    hook, self.after_create = self.after_create, None
                    hook(self, record)
                return response
        base, uid, collection, *suffix = path.removeprefix("/odms/").split("/")
        record = self.documents[uid][1]
        field = collection.replace("-", "_")
        if suffix == ["initialize"]:
            if self.before_initialize is not None:
                hook, self.before_initialize = self.before_initialize, None
                hook(record)
            self.initialize_requests.append(deepcopy(body))
            if record != body["expected_parent"]:
                raise NativeOperationReconciliationError("SERVER_PARENT_SNAPSHOT_CHANGED")
            if any(self.documents[key][1] != value for key, value in body["expected_children"].items()):
                raise NativeOperationReconciliationError("SERVER_CHILD_SNAPSHOT_CHANGED")
            desired = body["children"]
            if record[field] and record[field] != desired:
                # Read enrichment is not present in this synthetic HTTP model.
                raise NativeOperationReconciliationError("SERVER_CHILDREN_CONFLICT")
            if not record[field]:
                if field == "items":
                    parent_ref = {key: record[key] for key in ("uid", "oid", "name")}
                    for row in desired:
                        child = self.documents[row["uid"]][1]
                        if child["odm_item_group"] not in (None, parent_ref):
                            raise NativeOperationReconciliationError("SERVER_FOREIGN_ITEM_PARENT")
                record[field] = deepcopy(desired)
                if field == "items":
                    for row in desired:
                        self.documents[row["uid"]][1]["odm_item_group"] = deepcopy(parent_ref)
                self.binding_writes.append((uid, deepcopy(desired)))
            if self.lose_response_once:
                self.lose_response_once = False
                raise requests.ConnectionError("The server committed; its response was lost")
        else:
            # Actual old native API semantics: override deletes the prior set.
            assert params == {"override": True}
            record[field] = deepcopy(body)
            self.binding_writes.append((uid, deepcopy(body)))
        return deepcopy(record)


def execute(monkeypatch, proposal, review, api, prior=()):
    database = FakeNativeDb(proposal, item_results=deepcopy(prior))
    install_review_response(monkeypatch, review)
    value = native_worker(database, api)
    return value, database


@pytest.mark.parametrize("collection,parent_family,parent_name", [
    ("items", "OdmItemGroup", "group"), ("item_groups", "OdmForm", "form"),
])
def test_existing_incompatible_parent_is_refused_before_any_native_write(
    monkeypatch, collection, parent_family, parent_name
):
    proposal, review = capture_case(collection)
    api = CaptureApi()
    parent = api.seed(parent_family, parent_name)
    parent[collection] = [{"uid": "Independent-child", "order_number": 11, "mandatory": "Yes",
                           "vendor": {"attributes": []}, "source_owned_qualifier": "preserve"}]
    before = deepcopy(api.documents)
    worker, database = execute(monkeypatch, proposal, review, api)
    with pytest.raises(NativeOperationReconciliationError, match="EXISTING_CHILDREN_CONFLICT"):
        worker.run_once()
    assert api.documents == before
    assert api.post_calls == []
    assert database.native_successes == []


def test_real_plan_initializes_full_form_group_item_graph_and_replays_receipts(monkeypatch):
    proposal, review = capture_case(full_graph=True)
    api = CaptureApi()
    worker, database = execute(monkeypatch, proposal, review, api)
    assert worker.run_once()["status"] == "succeeded"
    assert len(api.binding_writes) == 2
    group_request, form_request = api.initialize_requests
    assert group_request["expected_parent"]["items"] == []
    assert form_request["expected_children"]["Native-group"]["items"] == api.documents["Native-group"][1]["items"]
    assert api.documents["Native-form"][1]["item_groups"][0]["order_number"] == 7
    original_item = api.created_snapshots["Native-item"]
    expected_item = deepcopy(original_item)
    expected_item["odm_item_group"] = {
        key: api.documents["Native-group"][1][key] for key in ("uid", "oid", "name")
    }
    assert original_item["odm_item_group"] is None
    assert api.documents["Native-item"][1] == expected_item
    before = deepcopy(api.documents)
    receipts = [row for row in database.item_results if row.get("status") == "reconciled"]
    assert len(receipts) == 5
    item_receipt = next(row for row in receipts if row["family"] == "OdmItem")
    assert item_receipt["capture_definition_hash"] == _stable_hash(original_item)
    assert item_receipt["capture_definition_hash"] != _stable_hash(expected_item)
    retry, retry_db = execute(monkeypatch, proposal, review, api, database.item_results)
    assert retry.run_once()["status"] == "succeeded"
    assert api.documents == before
    assert len(api.binding_writes) == 2
    assert [row for row in retry_db.item_results if row.get("status") == "reconciled"] == receipts


@pytest.mark.parametrize("change", [
    "version", "oid", "unknown", "foreign_parent", "parent_name", "backlink_unknown",
    "backlink_null", "backlink_missing",
])
def test_cold_replay_only_accepts_the_exact_bound_native_backlink(monkeypatch, change):
    proposal, review = capture_case()
    api = CaptureApi()
    worker, database = execute(monkeypatch, proposal, review, api)
    assert worker.run_once()["status"] == "succeeded"
    value = api.documents["Native-item"][1]
    if change == "version":
        value["version"] = "0.2"
    elif change == "oid":
        value["oid"] = "Changed.source.oid"
    elif change == "unknown":
        value["native_annotations"]["additional"] = [None, False, 0, ""]
    elif change == "foreign_parent":
        value["odm_item_group"]["uid"] = "Another-parent"
    elif change == "parent_name":
        value["odm_item_group"]["name"] = "Changed parent"
    elif change == "backlink_unknown":
        value["odm_item_group"]["unknown"] = {"keep": None}
    elif change == "backlink_null":
        value["odm_item_group"] = None
    else:
        del value["odm_item_group"]
    before, posts, bindings = deepcopy(api.documents), len(api.post_calls), len(api.binding_writes)
    prior = deepcopy(database.item_results)
    retry, retry_db = execute(monkeypatch, proposal, review, api, prior)
    with pytest.raises((OsbProposalIntegrityError, NativeOperationReconciliationError)):
        retry.run_once()
    assert api.documents == before and len(api.post_calls) == posts
    assert len(api.binding_writes) == bindings
    assert retry_db.native_successes == []
    assert database.item_results == prior


def test_foreign_native_item_parent_refuses_before_any_first_run_write(monkeypatch):
    proposal, review = capture_case()
    api = CaptureApi()
    item = api.seed("OdmItem", "item")
    foreign = api.seed("OdmItemGroup", "foreign", uid="Foreign-parent")
    item["odm_item_group"] = {key: foreign[key] for key in ("uid", "oid", "name")}
    foreign["items"] = [{"uid": item["uid"], "order_number": 29, "mandatory": "No",
                         "vendor": {"attributes": []}}]
    before = deepcopy(api.documents)
    worker, database = execute(monkeypatch, proposal, review, api)
    with pytest.raises(NativeOperationReconciliationError, match="CHILD_BACKLINK_UNPROVEN"):
        worker.run_once()
    assert api.documents == before and api.post_calls == []
    assert api.binding_writes == [] and database.native_successes == []


@pytest.mark.parametrize("change", ["mandatory", "condition", "method", "vendor", "unknown"])
def test_full_relationship_properties_prevent_same_uid_reuse(monkeypatch, change):
    proposal, review = capture_case()
    api = CaptureApi()
    parent = api.seed("OdmItemGroup", "group")
    api.seed("OdmItem", "item")
    plan = native_operation_plan(proposal, review, "Study_1", "DRAFT")
    relation = deepcopy(next(row for row in plan["operations"] if row["family"] == "OdmItemGroupItemLink")["body"][0])
    relation["uid"] = "Native-item"
    if change == "mandatory":
        relation["mandatory"] = "Yes"
    elif change == "condition":
        relation["collection_exception_condition_oid"] = "Source-only-condition"
    elif change == "method":
        relation["method_oid"] = "Existing-method"
    elif change == "vendor":
        relation["vendor"]["attributes"] = [{"uid": "Independent-attribute", "value": "kept"}]
    else:
        relation["unrepresented_qualifier"] = {"exact": "retain"}
    parent["items"] = [relation]
    before = deepcopy(api.documents)
    worker, _ = execute(monkeypatch, proposal, review, api)
    with pytest.raises(NativeOperationReconciliationError, match="EXISTING_CHILDREN_CONFLICT"):
        worker.run_once()
    assert api.documents == before
    assert api.post_calls == []


def test_parent_change_between_preflight_and_guard_is_not_resealed(monkeypatch):
    proposal, review = capture_case()
    api = CaptureApi()
    api.seed("OdmItemGroup", "group")
    api.seed("OdmItem", "item")
    api.before_initialize = lambda value: value.update(native_annotations={"concurrent": "operator edit"})
    worker, database = execute(monkeypatch, proposal, review, api)
    with pytest.raises(NativeOperationReconciliationError, match="SERVER_PARENT_SNAPSHOT_CHANGED"):
        worker.run_once()
    assert api.documents["Native-group"][1]["items"] == []
    assert api.documents["Native-group"][1]["native_annotations"] == {"concurrent": "operator edit"}
    assert api.binding_writes == []
    assert database.native_successes == []


def test_lost_committed_response_retries_exactly_without_expansion(monkeypatch):
    proposal, review = capture_case()
    api = CaptureApi()
    api.lose_response_once = True
    worker, database = execute(monkeypatch, proposal, review, api)
    with pytest.raises(requests.ConnectionError, match="response was lost"):
        worker.run_once()
    assert len(api.binding_writes) == 1
    before = deepcopy(api.documents)
    original_receipts = [deepcopy(row) for row in database.item_results if row.get("status") == "reconciled"]
    original_item = api.created_snapshots["Native-item"]
    assert original_item["odm_item_group"] is None
    assert api.documents["Native-item"][1]["odm_item_group"]["uid"] == "Native-group"
    retry, _ = execute(monkeypatch, proposal, review, api, database.item_results)
    assert retry.run_once()["status"] == "succeeded"
    assert api.documents == before
    assert len(api.binding_writes) == 1
    assert [row for row in database.item_results if row.get("status") == "reconciled"] == original_receipts


def test_removed_previously_receipted_collection_is_not_restored(monkeypatch):
    proposal, review = capture_case()
    api = CaptureApi()
    worker, database = execute(monkeypatch, proposal, review, api)
    assert worker.run_once()["status"] == "succeeded"
    api.documents["Native-group"][1]["items"] = []
    before = deepcopy(api.documents)
    post_count = len(api.post_calls)
    retry, _ = execute(monkeypatch, proposal, review, api, database.item_results)
    with pytest.raises(OsbProposalIntegrityError, match="PRIOR_COLLECTION_REMOVED"):
        retry.run_once()
    assert api.documents == before
    assert len(api.post_calls) == post_count


@pytest.mark.parametrize("renamed_object", ["group", "item"])
def test_receipt_reads_exact_uid_and_cannot_adopt_a_lookalike_after_original_renamed(monkeypatch, renamed_object):
    proposal, review = capture_case()
    api = CaptureApi()
    worker, database = execute(monkeypatch, proposal, review, api)
    assert worker.run_once()["status"] == "succeeded"
    uid = "Native-" + renamed_object
    family, original = api.documents[uid]
    original["name"] = "An independently renamed original"
    api.seed(family, renamed_object, uid="Different-" + renamed_object)
    before = deepcopy(api.documents)
    post_count = len(api.post_calls)
    retry, retry_db = execute(monkeypatch, proposal, review, api, database.item_results)
    with pytest.raises(NativeOperationReconciliationError, match="CAPTURE_SOURCE_CHANGED"):
        retry.run_once()
    assert api.documents == before
    assert len(api.post_calls) == post_count
    assert retry_db.native_successes == []


def test_create_response_identity_cannot_be_replaced_by_a_matching_different_uid(monkeypatch):
    proposal, review = capture_case()
    api = CaptureApi()

    def swap_after_create(native, original):
        family = native.documents[original["uid"]][0]
        old_name = original["name"]
        original["name"] = "Concurrent independent rename"
        native.seed(family, old_name, uid="Lookalike-with-another-UID")

    api.after_create = swap_after_create
    worker, database = execute(monkeypatch, proposal, review, api)
    with pytest.raises(NativeOperationReconciliationError, match="CAPTURE_WRITE_IDENTITY_DIVERGED"):
        worker.run_once()
    assert api.binding_writes == []
    assert database.native_successes == []
    assert not any(row.get("status") == "reconciled" for row in database.item_results)


@pytest.mark.parametrize("case_name", ["swapped_uid", "missing_receipt_uid"])
def test_general_native_receipt_replay_never_substitutes_another_identity(monkeypatch, case_name):
    proposal, review = native_activity_proposal(), native_activity_review()
    api = FakeNativeApi()
    worker, database = execute(monkeypatch, proposal, review, api)
    assert worker.run_once()["status"] == "succeeded"
    prior = deepcopy(database.item_results)
    if case_name == "swapped_uid":
        # The entire projection still matches; only the identity has changed.
        api.activities[0]["study_activity_uid"] = "A-different-native-activity"
    else:
        next(row for row in prior if row.get("status") == "reconciled").pop("native_uid")
    before, post_count = deepcopy(api.activities), len(api.post_calls)
    retry, retry_db = execute(monkeypatch, proposal, review, api, prior)
    with pytest.raises(OsbProposalIntegrityError, match="RECEIPT_NATIVE_UID"):
        retry.run_once()
    assert api.activities == before
    assert len(api.post_calls) == post_count
    assert retry_db.native_successes == []


def test_later_http_child_failure_retains_prior_receipts_and_retries_without_recreating_parents(monkeypatch):
    proposal, review = capture_case(full_graph=True)
    api = CaptureApi()
    api.fail_create_once = "item"
    worker, database = execute(monkeypatch, proposal, review, api)
    with pytest.raises(requests.ConnectionError, match="later child"):
        worker.run_once()
    assert set(api.documents) == {"Native-form", "Native-group"}
    assert api.documents["Native-form"][1]["item_groups"] == []
    assert api.documents["Native-group"][1]["items"] == []
    assert api.binding_writes == []
    assert database.native_successes == []
    prior = [deepcopy(row) for row in database.item_results if row.get("status") == "reconciled"]
    assert {row["native_uid"] for row in prior} == {"Native-form", "Native-group"}
    post_count = len(api.post_calls)
    retry, retry_db = execute(monkeypatch, proposal, review, api, database.item_results)
    assert retry.run_once()["status"] == "succeeded"
    assert [row for row in retry_db.item_results if row.get("status") == "reconciled"][:2] == prior
    assert [row[0] for row in api.post_calls[post_count:]] == [
        "/odms/items", "/odms/item-groups/Native-group/items/initialize",
        "/odms/forms/Native-form/item-groups/initialize",
    ]
    assert len(api.binding_writes) == 2


@pytest.mark.parametrize("sequence", ["2", "01", "source-key"])
def test_native_string_key_sequence_and_all_reviewed_qualifiers_survive(monkeypatch, sequence):
    proposal, review = capture_case(relation_extra={
        "key_sequence": sequence, "method_oid": "Reviewed.method",
        "imputation_method_oid": "Reviewed.imputation", "role": "Reviewed role",
        "role_codelist_oid": "Reviewed.codelist", "collection_exception_condition_oid": "Reviewed.condition",
    })
    relation = proposal["sections"]["objects"][-1]["source"]["values"][1]["value"][0]["relation"]
    api = CaptureApi()
    worker, _ = execute(monkeypatch, proposal, review, api)
    assert worker.run_once()["status"] == "succeeded"
    persisted = api.documents["Native-group"][1]["items"][0]
    assert {key: persisted[key] for key in relation} == relation


def test_duplicate_parent_collection_statements_block_the_entire_plan():
    proposal, review = capture_case()
    duplicate = deepcopy(proposal["sections"]["objects"][-1])
    duplicate["proposalObjectId"] = "second-group-link"
    proposal["sections"]["objects"].append(duplicate)
    decision = deepcopy(review["objects"][-1])
    decision["proposal_object_id"] = "second-group-link"
    review["objects"].append(decision)
    plan = native_operation_plan(proposal, review, "Study_1", "DRAFT")
    assert any(row["code"] == "OSB_NATIVE_V2_CAPTURE_PARENT_COLLECTION_AMBIGUOUS" for row in plan["blockers"])
