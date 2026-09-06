"""Exercise real approval/cascade methods with observable lifecycle operations."""
from types import SimpleNamespace

import pytest
from neomodel import db
from pydantic import BaseModel

from clinical_mdr_api.services.odms.generic_service import OdmGenericService
from common.exceptions import BusinessLogicException


class Response(BaseModel):
    uid: str
    status: str


class Aggregate:
    def __init__(self, uid, events, status="Draft", failure=None, **children):
        self.uid, self.events, self.status, self.failure = uid, events, status, failure
        self.odm_vo = SimpleNamespace(**children)

    def approve(self, *, author_id):
        self.events.append(("approve", self.uid, author_id))
        if self.failure:
            raise BusinessLogicException(msg=self.failure)
        if self.status != "Draft":
            raise BusinessLogicException(msg="The object isn't in draft status.")
        self.status = "Final"


class Service(OdmGenericService):
    def __init__(self, nodes, events):
        self.nodes, self.events, self.author_id = nodes, events, "Reviewer"

    def __del__(self):
        pass

    @property
    def repository(self):
        return SimpleNamespace(save=lambda node: self.events.append(("save", node.uid)))

    def _find_by_uid_or_raise_not_found(self, uid, **kwargs):
        self.events.append(("find", uid, kwargs))
        return self.nodes[uid]

    def _transform_aggregate_root_to_pydantic_model(self, node):
        self.events.append(("project", node.uid))
        return Response(uid=node.uid, status=node.status)

    def _create_aggregate_root(self, *args, **kwargs):
        raise NotImplementedError

    def _edit_aggregate(self, *args, **kwargs):
        raise NotImplementedError


@pytest.fixture
def graph(monkeypatch):
    # The real decorators must reuse an existing transaction. No database
    # operation is performed by this focused unit test.
    transaction = object()
    monkeypatch.setattr(db, "_active_transaction", transaction)
    events, nodes = [], {}
    service = Service(nodes, events)
    modules = {
        "forms": "OdmFormService", "item_groups": "OdmItemGroupService",
        "items": "OdmItemService", "vendor_attributes": "OdmVendorAttributeService",
        "vendor_elements": "OdmVendorElementService", "vendor_namespaces": "OdmVendorNamespaceService",
    }
    for module, name in modules.items():
        monkeypatch.setattr(f"clinical_mdr_api.services.odms.{module}.{name}", lambda: service)
    return service, nodes, events, transaction


def test_cascade_retains_every_lifecycle_operation_and_only_projects_root(graph):
    service, nodes, events, transaction = graph
    nodes["root"] = Aggregate("root", events, form_uids=["form"])
    nodes["form"] = Aggregate("form", events, item_group_uids=["group"], vendor_attribute_uids=["attribute"], vendor_element_uids=["element"])
    nodes["group"] = Aggregate("group", events, item_uids=["item"])
    nodes["item"] = Aggregate("item", events, vendor_namespace_uids=["namespace"])
    for uid in ["attribute", "element", "namespace"]:
        nodes[uid] = Aggregate(uid, events)
    result = service.approve("root", cascade_edit_and_approve=True)
    assert result == Response(uid="root", status="Final")
    assert [event for event in events if event[0] == "project"] == [("project", "root")]
    assert {event[1] for event in events if event[0] == "save"} == set(nodes)
    assert all(event[2] == {"for_update": True} for event in events if event[0] == "find")
    assert db._active_transaction is transaction


def test_final_children_still_validate_and_recurse_without_projection(graph):
    service, nodes, events, _ = graph
    nodes["root"] = Aggregate("root", events, item_uids=["item"])
    nodes["item"] = Aggregate("item", events, status="Final", vendor_attribute_uids=["attribute"])
    nodes["attribute"] = Aggregate("attribute", events)
    service.approve("root", cascade_edit_and_approve=True)
    assert ("approve", "item", "Reviewer") in events
    assert ("save", "item") not in events
    assert ("save", "attribute") in events
    assert ("project", "item") not in events


def test_child_validation_failure_propagates_and_prevents_root_projection(graph):
    service, nodes, events, _ = graph
    nodes["root"] = Aggregate("root", events, item_uids=["item"])
    nodes["item"] = Aggregate("item", events, failure="Required lifecycle invariant failed")
    with pytest.raises(BusinessLogicException, match="Required lifecycle invariant failed"):
        service.approve("root", cascade_edit_and_approve=True)
    assert not any(event[0] == "project" for event in events)


def test_repeated_identities_are_not_deduplicated_and_public_final_error_remains(graph):
    service, nodes, events, _ = graph
    nodes["root"] = Aggregate("root", events, item_uids=["item", "item"])
    nodes["item"] = Aggregate("item", events)
    service.approve("root", cascade_edit_and_approve=True)
    assert events.count(("approve", "item", "Reviewer")) == 2
    assert events.count(("save", "item")) == 1
    with pytest.raises(BusinessLogicException, match="isn't in draft"):
        service.approve("root")


def test_public_non_cascade_response_contract_is_unchanged(graph):
    service, nodes, events, _ = graph
    nodes["root"] = Aggregate("root", events, item_uids=["not-read"])
    assert service.approve("root").model_dump() == {"uid": "root", "status": "Final"}
    assert [event[1] for event in events if event[0] == "find"] == ["root"]
