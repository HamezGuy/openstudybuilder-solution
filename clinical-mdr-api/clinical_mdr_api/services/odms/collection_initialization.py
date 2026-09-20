"""Initialize reviewed capture links without replacing or appending to a set.

The full parent and child snapshots are concurrency preconditions, not approval
or source authority. The authenticated caller supplies the reviewed relation
values; ordinary ODM services retain their native validation and transaction.
"""

import json
from copy import deepcopy

from fastapi.encoders import jsonable_encoder
from neomodel import db

from clinical_mdr_api.domain_repositories._utils.native_read_cache import uncached_native_reads
from clinical_mdr_api.models.odms.collection_initialization import (
    OdmCollectionInitializationInput,
)
from clinical_mdr_api.models.odms.common_models import (
    OdmRefVendorAttributeModel,
    OdmVendorRelationPostInput,
)
from clinical_mdr_api.models.odms.form import OdmFormItemGroupPostInput
from clinical_mdr_api.models.odms.item import OdmItemParentGroup, OdmItemRefModel
from clinical_mdr_api.models.odms.item_group import (
    OdmItemGroupItemPostInput,
    OdmItemGroupRefModel,
)
from clinical_mdr_api.services._utils import ensure_transaction
from common.exceptions import BusinessLogicException


class OdmCollectionInitializationConflict(BusinessLogicException):
    status_code = 409


def _refuse(code):
    raise OdmCollectionInitializationConflict(msg=code)


def _json(value):
    # Python equality conflates True and 1. These preconditions compare JSON
    # values without accepting non-finite numbers or converting unknown values.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _wire(service, uid):
    item = service._find_by_uid_or_raise_not_found(uid)
    return jsonable_encoder(service._transform_aggregate_root_to_pydantic_model(item), exclude_none=False)


def _linked_children(parent, snapshots, *, collection):
    """Derive only the native ITEM_REF backlink, retaining every other member."""
    linked = deepcopy(snapshots)
    if collection != "items":
        return linked
    if not {"uid", "oid", "name"}.issubset(parent):
        _refuse("ODM_COLLECTION_PARENT_BACKLINK_IDENTITY_UNPROVEN")
    parent_ref = OdmItemParentGroup(
        uid=parent["uid"], oid=parent["oid"], name=parent["name"],
    ).model_dump(mode="json")
    for child in linked.values():
        if "odm_item_group" not in child or (
            child["odm_item_group"] is not None
            and _json(child["odm_item_group"]) != _json(parent_ref)
        ):
            _refuse("ODM_COLLECTION_CHILD_BACKLINK_CUSTODY_UNPROVEN")
        child["odm_item_group"] = deepcopy(parent_ref)
    return linked


def _validate_native_item_custody(parent_service, uid, child_uids):
    # Reuse the ordinary writer's complete incoming ITEM_REF ownership check.
    # The projected scalar backlink alone cannot exclude another parent.
    # This existing method takes the already-held root lock and performs reads;
    # it neither disconnects nor connects a relationship.
    for child_uid in sorted(child_uids):
        parent_service.repository.validate_capture_item_custody(uid, child_uid)


def _vendor_value(value, *, native):
    if not isinstance(value, dict) or set(value) != {"attributes"}:
        _refuse("ODM_COLLECTION_VENDOR_SHAPE_UNPROVEN")
    attributes = value["attributes"]
    if not isinstance(attributes, list):
        _refuse("ODM_COLLECTION_VENDOR_SHAPE_UNPROVEN")
    source_fields = set(OdmVendorRelationPostInput.model_fields)
    native_fields = set(OdmRefVendorAttributeModel.model_fields)
    result = {}
    for row in attributes:
        if not isinstance(row, dict) or set(row) - (native_fields if native else source_fields):
            _refuse("ODM_COLLECTION_VENDOR_PROPERTY_UNPROVEN")
        if not source_fields.issubset(row):
            _refuse("ODM_COLLECTION_VENDOR_PROPERTY_UNPROVEN")
        uid = row["uid"]
        if not isinstance(uid, str) or not uid or uid in result:
            _refuse("ODM_COLLECTION_VENDOR_ID_UNPROVEN")
        result[uid] = {key: row[key] for key in source_fields}
    return [result[uid] for uid in sorted(result)]


def _relations(value, input_model, reference_model, *, native):
    if not isinstance(value, list):
        _refuse("ODM_COLLECTION_INVENTORY_UNPROVEN")
    source_fields = set(input_model.model_fields)
    native_fields = set(reference_model.model_fields)
    result = {}
    orders = set()
    for row in value:
        if not isinstance(row, dict) or set(row) - (native_fields if native else source_fields):
            _refuse("ODM_COLLECTION_PROPERTY_UNPROVEN")
        if not source_fields.issubset(row):
            _refuse("ODM_COLLECTION_PROPERTY_UNPROVEN")
        relation = {key: deepcopy(row[key]) for key in source_fields}
        uid, order = relation["uid"], relation["order_number"]
        if not isinstance(uid, str) or not uid or uid in result:
            _refuse("ODM_COLLECTION_CHILD_ID_UNPROVEN")
        if type(order) is not int or order < 1 or order in orders:
            _refuse("ODM_COLLECTION_ORDER_UNPROVEN")
        if relation["mandatory"] not in {"Yes", "No"}:
            _refuse("ODM_COLLECTION_MANDATORY_UNPROVEN")
        relation["vendor"] = {"attributes": _vendor_value(relation["vendor"], native=native)}
        result[uid] = relation
        orders.add(order)
    return [result[uid] for uid in sorted(result)]


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _refuse("ODM_COLLECTION_STORED_JSON_AMBIGUOUS")
        result[key] = value
    return result


def _stored_relations(repository, uid, collection, input_model, reference_model):
    rows = []
    source_fields = set(input_model.model_fields) - {"uid"}
    for row in repository.read_capture_collection(uid, collection):
        owners, current = row["owner_uids"], row["current_owner_uids"]
        if (
            len(owners) != 1
            or not isinstance(owners[0], str)
            or not owners[0]
            or current != owners
        ):
            _refuse("ODM_COLLECTION_STORED_CHILD_CUSTODY_UNPROVEN")
        properties = row["properties"]
        if (
            not isinstance(properties, dict)
            or set(properties) - source_fields
            or not {"order_number", "mandatory", "vendor"}.issubset(properties)
            or type(properties["mandatory"]) is not bool
            or not isinstance(properties["vendor"], str)
        ):
            _refuse("ODM_COLLECTION_STORED_PROPERTY_UNPROVEN")
        # Neo4j omits null properties; the existing native input/response
        # contracts expose those declared optional properties as null.
        relation = {key: deepcopy(properties.get(key)) for key in source_fields}
        relation["uid"] = owners[0]
        relation["mandatory"] = "Yes" if properties["mandatory"] else "No"
        relation["vendor"] = json.loads(
            properties["vendor"], object_pairs_hook=_json_object,
            parse_constant=lambda _: _refuse("ODM_COLLECTION_STORED_JSON_INVALID"),
        )
        rows.append(relation)
    return _relations(rows, input_model, reference_model, native=False)


@ensure_transaction(db)
@uncached_native_reads()
def initialize_odm_collection(
    parent_service,
    child_service,
    uid: str,
    request: OdmCollectionInitializationInput,
    *,
    collection: str,
):
    if collection == "item_groups":
        input_model, reference_model = OdmFormItemGroupPostInput, OdmItemGroupRefModel
        writer = parent_service.add_item_groups
    elif collection == "items":
        input_model, reference_model = OdmItemGroupItemPostInput, OdmItemRefModel
        writer = parent_service.add_items
    else:
        _refuse("ODM_COLLECTION_KIND_UNSUPPORTED")
    if request.expected_parent.get("uid") != uid:
        _refuse("ODM_COLLECTION_PARENT_SNAPSHOT_ID_MISMATCH")

    parsed = []
    for source in request.children:
        if set(source) - set(input_model.model_fields):
            _refuse("ODM_COLLECTION_REQUEST_PROPERTY_UNPROVEN")
        # These are HTTP JSON inputs. The OSB model_validate override flattens
        # database nodes; ordinary construction validates this complete mapping.
        model = input_model(**source)
        if _json(model.model_dump(mode="json", exclude_unset=True)) != _json(source):
            _refuse("ODM_COLLECTION_REQUEST_NORMALIZATION_MISMATCH")
        parsed.append(model)
    requested = _relations(
        [row.model_dump(mode="json") for row in parsed],
        input_model, reference_model, native=False,
    )
    expected = deepcopy(request.expected_parent)
    expected_relations = _relations(expected.get(collection), input_model, reference_model, native=True)
    if expected_relations and _json(expected_relations) != _json(requested):
        _refuse("ODM_COLLECTION_EXISTING_CHILDREN_CONFLICT")

    # The same root lock is acquired by ordinary ODM relation mutations and
    # existing native version writers. Read only after all these locks are held.
    locks = [(parent_service, uid), *((child_service, key) for key in request.expected_children)]
    for service, key in sorted(
        locks, key=lambda pair: (pair[0].repository.root_class.__label__, pair[1])
    ):
        service.repository.lock_for_relationship_update(key)
    current = _wire(parent_service, uid)
    current_relations = _relations(current.get(collection), input_model, reference_model, native=True)
    stored = _stored_relations(parent_service.repository, uid, collection, input_model, reference_model)
    if _json(stored) != _json(current_relations):
        _refuse("ODM_COLLECTION_STORED_READBACK_DIVERGED")
    definition = lambda value: {key: child for key, child in value.items() if key != collection}
    if _json(definition(current)) != _json(definition(expected)):
        _refuse("ODM_COLLECTION_PARENT_SNAPSHOT_CHANGED")
    is_exact_replay = _json(current_relations) == _json(requested)
    linked_children = _linked_children(current, request.expected_children, collection=collection)
    # A replay may carry the original pre-link snapshots. Accept their exact
    # native-derived state only after proving the complete intended collection,
    # raw stored relationships, and unchanged parent definition above.
    admitted_children = linked_children if is_exact_replay else request.expected_children
    for key, snapshot in admitted_children.items():
        if _json(_wire(child_service, key)) != _json(snapshot):
            _refuse("ODM_COLLECTION_CHILD_SNAPSHOT_CHANGED")
    if collection == "items":
        _validate_native_item_custody(parent_service, uid, request.expected_children)
    if is_exact_replay:
        # Includes a lost-response replay of the original empty-parent request.
        return parent_service._transform_aggregate_root_to_pydantic_model(
            parent_service._find_by_uid_or_raise_not_found(uid)
        )
    if current_relations:
        _refuse("ODM_COLLECTION_EXISTING_CHILDREN_CONFLICT")
    if _json(current) != _json(expected):
        _refuse("ODM_COLLECTION_PARENT_SNAPSHOT_CHANGED")
    if current.get("status") != "Draft":
        _refuse("ODM_COLLECTION_PARENT_NOT_DRAFT")

    # This is initialization of the locked empty set. Never use override=True
    # or retry an incompatible set through the ordinary append operation.
    writer(uid, parsed, override=False, preserve_order=True)
    result = parent_service._transform_aggregate_root_to_pydantic_model(
        parent_service._find_by_uid_or_raise_not_found(uid)
    )
    after = jsonable_encoder(result, exclude_none=False)
    if _json(definition(after)) != _json(definition(current)):
        _refuse("ODM_COLLECTION_PARENT_WRITE_DIVERGED")
    if _json(_relations(after.get(collection), input_model, reference_model, native=True)) != _json(requested):
        _refuse("ODM_COLLECTION_WRITE_DIVERGED")
    if _json(_stored_relations(parent_service.repository, uid, collection, input_model, reference_model)) != _json(requested):
        _refuse("ODM_COLLECTION_STORED_WRITE_DIVERGED")
    for key, snapshot in linked_children.items():
        if _json(_wire(child_service, key)) != _json(snapshot):
            _refuse("ODM_COLLECTION_CHILD_WRITE_DIVERGED")
    if collection == "items":
        _validate_native_item_custody(parent_service, uid, request.expected_children)
    return result
