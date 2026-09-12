"""Native capture behavior with real DTO validation and synthetic persistence.

No application/database fixtures, client studies, signatures or approvals.
"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.native_capture_mapping import (
    NativeCapturePort, apply_native_capture_selections, plan_native_capture, read_capture_target,
)
from clinical_mdr_api.services.integrations.native_capture_projection import (
    capture_field_receipts, project_native_values,
)


TYPES = {"odm_forms": "STUDY_FORM", "odm_item_groups": "STUDY_ITEM_GROUP", "odm_items": "STUDY_ITEM",
         "controlled_terminology_codelists": "CT_CODELIST_STATEMENT", "controlled_terminology": "CT_TERM_STATEMENT"}


def source(fact, family, **fields):
    value_types = {type(None): "null", bool: "boolean", str: "string", int: "number", float: "number", list: "array", dict: "object"}
    return {"intent": {"factId": fact, "revision": 1, "targetKey": "primary", "resourceFamily": family,
                       "source": {"assertionType": TYPES[family], "clinicalDomain": None,
                                  "candidateType": None, "exactQuote": None, "label": fact, "values": [
                           {"name": name, "sourcePath": f"/fields/{name}", "valueType": value_types[type(value)], "value": value}
                           for name, value in fields.items()]}},
            "selection": {"action": "create", "candidateIdentity": None}}


def fixture():
    return [
        source("form", "odm_forms", formRef="F.SOURCE", name="Source form", repeating=False, description=""),
        source("group", "odm_item_groups", itemGroupRef="G.SOURCE", formRef="F.SOURCE",
               name="Source group", label="Group label", repeating=False, order=7, required=False),
        source("codelist", "controlled_terminology_codelists", codelistKey="CL.SOURCE", name="Source choices",
               values=[{"codedValue": "N", "decode": "No", "orderNumber": 2},
                       {"codedValue": "Y", "decode": "Yes", "orderNumber": 5}],
               dataType="text", itemRefs=["I.CHOICE"]),
        source("choice", "odm_items", itemRef="I.CHOICE", itemGroupRef="G.SOURCE", formRef="F.SOURCE",
               name="Choice", label="Original question", dataType="text", length=8, order=3, required=False,
               codelistKey="CL.SOURCE", codelistName="Source choices", allowsMultiChoice=False,
               options=[{"value": "N", "label": "No"}, {"value": "Y", "label": "Yes"}]),
        source("number", "odm_items", itemRef="I.NUMBER", itemGroupRef="G.SOURCE", formRef="F.SOURCE",
               name="Number", label="Number label", questionText="", dataType="integer",
               order=9, required=True, memberClaimIds=["claim-1"], derivation="assembled-capture-structure"),
        source("term", "controlled_terminology", codelistKey="CL.SOURCE", codelistName="Source choices",
               codedValue="N", decode="No", orderNumber=2),
    ]


class NativePort(NativeCapturePort):
    def __init__(self):
        self.values = {}
        self.bindings = {}
        self.creates = []
        self.associations = []
        self.locked = []

    def lock_study(self, *scope):
        self.locked.append(scope)

    def binding(self, key):
        return deepcopy(self.bindings.get(key))

    def bind(self, binding):
        assert binding["bindingKey"] not in self.bindings
        self.bindings[binding["bindingKey"]] = deepcopy(binding)

    def create(self, family, body):
        # The production Pydantic model validates types, length and text rules.
        _, model = self._odm(family)
        parsed = model(**body).model_dump(mode="json")
        uid = f"{family}_{len(self.values)}"
        self.values[uid] = {"uid": uid, "version": "0.1", "status": "Draft",
                            **parsed, "parents": [], "item_groups": [], "items": []}
        if parsed.get("codelist"):
            codelist = self.values[parsed["codelist"]["uid"]]
            self.values[uid]["codelist"] = {**parsed["codelist"],
                "name": codelist["attributes"]["name"],
                "submission_value": codelist["attributes"]["submission_value"]}
            self.values[uid]["terms"] = [
                {"term_uid": term["uid"], "order": term["order"], "display_text": term["display_text"],
                 "name": self.values[term["uid"]]["name"]["sponsor_preferred_name"],
                 "submission_value": self.values[term["uid"]]["codelists"][0]["submission_value"]}
                for term in parsed["terms"]]
        self.creates.append((family, deepcopy(body)))
        return deepcopy(self.values[uid])

    def create_codelist(self, fields):
        uid = "CL.native"
        terms = []
        for index, value in enumerate(fields["values"]):
            term_uid = f"Term.native.{index}"
            membership = {"codelist_uid": uid, "codelist_name": fields["name"],
                          "codelist_submission_value": fields["codelistKey"],
                          "submission_value": value["codedValue"], "order": value["orderNumber"]}
            self.values[term_uid] = {"uid": term_uid, "version": "0.1",
                "name": {"sponsor_preferred_name": value["decode"], "version": "0.1", "status": "Draft"},
                "attributes": {"version": "0.1", "status": "Draft"}, "codelists": [membership]}
            terms.append({"term_uid": term_uid, "submission_value": value["codedValue"],
                          "sponsor_preferred_name": value["decode"], "order": value["orderNumber"],
                          "name_status": "Draft", "attributes_status": "Draft"})
        self.values[uid] = {"uid": uid, "version": "0.1",
            "name": {"version": "0.1", "status": "Draft"},
            "attributes": {"name": fields["name"], "submission_value": fields["codelistKey"], "version": "0.1", "status": "Draft"},
            "terms": terms, "items": []}
        self.creates.append(("controlled_terminology_codelists", deepcopy(fields)))
        return self.read("controlled_terminology_codelists", uid)

    def read(self, family, uid, version=None):
        value = deepcopy(self.values[uid])
        if version is not None and version != value["version"]:
            raise OsbCandidateSetError("OSB_CAPTURE_NATIVE_VERSION_CHANGED", "The current native version changed.", 422)
        if family == "controlled_terminology_codelists":
            value["items"] = [{"oid": item["oid"], "datatype": item["datatype"]}
                              for item in self.values.values() if (item.get("codelist") or {}).get("uid") == uid]
        if family == "odm_items":
            for parent in value["parents"]:
                parent["parents"] = deepcopy(self.values[parent["uid"]]["parents"])
        return value

    def associate(self, family, uid, children):
        field = "item_groups" if family == "odm_forms" else "items"
        if self.values[uid][field] == children:
            return
        assert not self.values[uid][field]
        self.values[uid][field] = deepcopy(children)
        for child in children:
            self.values[child["uid"]]["parents"].append({**child, "uid": uid, "version": self.values[uid]["version"],
                                                        "oid": self.values[uid]["oid"]})
        self.associations.append((family, uid, deepcopy(children)))


def apply(items, port, **options):
    return apply_native_capture_selections(items, tenant_id="tenant", platform_study_id="study",
                                           native_study_id="Study_synthetic", port=port, **options)


def test_native_definitions_relationships_choices_and_false_survive_readback():
    items, port = fixture(), NativePort()
    observed = apply(items, port)
    assert len(port.creates) == 5
    assert len(observed) == 6
    assert port.locked == [("tenant", "study", "Study_synthetic")]
    assert observed["form@1:primary"]["nativeValues"]["repeating"] is False
    assert observed["group@1:primary"]["nativeValues"]["order"] == 7
    assert observed["choice@1:primary"]["nativeValues"]["order"] == 3
    assert observed["choice@1:primary"]["nativeValues"]["required"] is False
    assert observed["number@1:primary"]["nativeValues"]["order"] == 9
    assert observed["number@1:primary"]["nativeValues"]["questionText"] == ""
    for item in items:
        key = item["intent"]["factId"] + "@1:primary"
        receipts, blockers = capture_field_receipts(item["intent"], observed[key])
        assert len(receipts) == len(item["intent"]["source"]["values"])
        assert blockers == [{"code": "NATIVE_CAPTURE_LIBRARY_REVIEW_REQUIRED"}], (key, blockers)
        assert all(receipt["disposition"] in {"native", "governed_extension"} for receipt in receipts)
    assert observed["number@1:primary"]["nativeValues"].get("memberClaimIds") is None
    assert observed["number@1:primary"]["sourceBinding"]["source"] == items[4]["intent"]["source"]


def test_repeated_source_mapping_reuses_native_objects_without_rewriting():
    items, port = fixture(), NativePort()
    first = apply(items, port)
    creates, associations = deepcopy(port.creates), deepcopy(port.associations)
    assert apply(items, port) == first
    assert port.creates == creates and port.associations == associations


@pytest.mark.parametrize("fact,name", [("form", "repeating"), ("choice", "length"),
                                     ("choice", "required"), ("choice", "order")])
def test_missing_clinical_values_refuse_before_creation(fact, name):
    items, port = fixture(), NativePort()
    intent = next(item["intent"] for item in items if item["intent"]["factId"] == fact)
    intent["source"]["values"] = [entry for entry in intent["source"]["values"] if entry["name"] != name]
    with pytest.raises((OsbCandidateSetError, ValidationError)):
        apply(items, port)
    assert port.creates == []
    assert port.associations == []


def test_unknown_source_field_is_retained_but_cannot_be_claimed_native():
    items, port = fixture(), NativePort()
    items[4]["intent"]["source"]["values"].append(
        {"name": "collectionContext", "sourcePath": "/fields/collectionContext", "value": {"fasting": False}})
    observed = apply(items, port)["number@1:primary"]
    receipts, blockers = capture_field_receipts(items[4]["intent"], observed)
    assert receipts[-1]["targetValueHash"] is None
    assert receipts[-1]["disposition"] == "missing_in_read_back"
    assert blockers[0]["sourcePath"] == "/fields/collectionContext"
    assert observed["sourceBinding"]["source"]["values"][-1]["value"] == {"fasting": False}


def test_forged_projection_is_rejected_even_when_its_hash_is_recomputed():
    items, port = fixture(), NativePort()
    observed = apply(items, port)["choice@1:primary"]
    observed["nativeValues"]["required"] = True
    with pytest.raises(OsbCandidateSetError, match="Projection differs"):
        capture_field_receipts(items[3]["intent"], observed)


def test_actual_native_edit_is_observed_as_a_field_mismatch():
    items, port = fixture(), NativePort()
    observed = apply(items, port)["choice@1:primary"]
    uid = observed["uid"]
    port.values[uid]["parents"][0]["mandatory"] = "Yes"
    current = read_capture_target({"bindingKey": "tenant|study|choice@1:primary", "uid": uid,
                                  "resourceFamily": "odm_items", "version": "0.1"}, port=port)
    assert current != observed
    receipts, blockers = capture_field_receipts(items[3]["intent"], current)
    assert next(row for row in receipts if row["sourcePath"] == "/fields/required")["disposition"] == "mismatch"
    assert blockers[0]["code"] == "NATIVE_CAPTURE_FIELD_NOT_PRESERVED"


def test_same_source_key_cannot_rebind_to_foreign_study_or_modified_source():
    items, port = fixture(), NativePort()
    apply(items, port)
    port.bindings["tenant|study|choice@1:primary"]["nativeStudyId"] = "Study_foreign"
    with pytest.raises(OsbCandidateSetError, match="different source content"):
        apply(items, port)


def test_duplicate_source_identity_and_path_are_not_collapsed():
    item = fixture()[0]["intent"]
    item["source"]["values"].append(deepcopy(item["source"]["values"][0]))
    with pytest.raises(OsbCandidateSetError, match="unique"):
        plan_native_capture(item)


def test_raw_native_order_and_term_values_are_not_replaced_by_source():
    native = {"attributes": {"name": "Native", "submission_value": "CL.N"},
              "terms": [{"submission_value": "0", "sponsor_preferred_name": "Changed", "order": 4}],
              "items": []}
    assert project_native_values("controlled_terminology_codelists", native)["values"] == [
        {"codedValue": "0", "decode": "Changed", "orderNumber": 4}]


def selected_dependency_fixture(*, select_group=False):
    items, port = fixture(), NativePort()
    for index in (0, 2):
        item = items[index]
        plan = plan_native_capture(item["intent"])
        family = plan["family"]
        if family == "controlled_terminology_codelists":
            native = port.create_codelist(plan["fields"])
            stored = port.values[native["uid"]]
            # Candidate production pins the CT name head, while capture readback
            # reports the independent attribute head's version.
            stored["version"] = stored["attributes"]["version"] = "5.0"
            stored["name"].update(version="2.0", status="Final")
            stored["attributes"]["status"] = "Final"
            for term in stored["terms"]:
                term["name_status"] = term["attributes_status"] = "Final"
            identity = {"resourceType": "CTCodelist", "version": "2.0", "status": "Final"}
        else:
            native = port.create(family, plan["body"])
            identity = {"resourceType": "OdmForm", "version": native["version"], "status": "Draft"}
        item["selection"] = {
            "action": "select",
            "candidateIdentity": {"resourceFamily": family, "uid": native["uid"], **identity},
        }
    if select_group:
        plan = plan_native_capture(items[1]["intent"])
        native = port.create(plan["family"], plan["body"])
        form_uid = items[0]["selection"]["candidateIdentity"]["uid"]
        port.associate("odm_forms", form_uid, [{"uid": native["uid"], "order_number": 7, "mandatory": "No"}])
        items[1]["selection"] = {"action": "select", "candidateIdentity": {
            "resourceFamily": "odm_item_groups", "resourceType": "OdmItemGroup",
            "uid": native["uid"], "version": native["version"], "status": "Draft",
        }}
    port.creates.clear()
    port.associations.clear()
    return items, port


@pytest.mark.parametrize("select_group", [False, True])
def test_selected_current_parents_support_created_children_and_exact_replay(select_group):
    items, port = selected_dependency_fixture(select_group=select_group)
    before = deepcopy(items)
    observed = apply(items, port)
    assert set(observed) == ({"choice@1:primary", "number@1:primary", "term@1:primary"}
                             | (set() if select_group else {"group@1:primary"}))
    assert [family for family, _ in port.creates] == (
        ["odm_items", "odm_items"] if select_group else ["odm_item_groups", "odm_items", "odm_items"])
    assert observed["choice@1:primary"]["nativeValues"]["formRef"] == "F.SOURCE"
    assert observed["choice@1:primary"]["nativeValues"]["itemGroupRef"] == "G.SOURCE"
    assert observed["choice@1:primary"]["nativeValues"]["required"] is False
    assert observed["choice@1:primary"]["nativeValues"]["order"] == 3
    assert observed["choice@1:primary"]["nativeValues"]["options"] == [
        {"value": "N", "label": "No"}, {"value": "Y", "label": "Yes"}]
    assert observed["number@1:primary"]["sourceBinding"]["source"] == items[4]["intent"]["source"]
    assert items == before
    assert all("form@" not in key and "codelist@" not in key for key in port.bindings)
    effects = deepcopy((port.creates, port.bindings, port.associations))
    assert apply(items, port) == observed
    assert (port.creates, port.bindings, port.associations) == effects


@pytest.mark.parametrize("mutation,code", [
    ("missing-version", "OSB_CAPTURE_SELECTED_IDENTITY_INVALID"),
    ("wrong-type", "OSB_CAPTURE_SELECTED_IDENTITY_INVALID"),
    ("wrong-family", "OSB_CAPTURE_SELECTED_IDENTITY_INVALID"),
    ("stale-version", "OSB_CAPTURE_NATIVE_VERSION_CHANGED"),
    ("wrong-native-uid", "OSB_CAPTURE_SELECTED_IDENTITY_INVALID"),
    ("wrong-native-ref", "OSB_CAPTURE_SOURCE_REFERENCE_MISMATCH"),
    ("final-parent", "OSB_CAPTURE_RELATIONSHIP_CONFLICT"),
    ("occupied-parent", "OSB_CAPTURE_RELATIONSHIP_CONFLICT"),
    ("stale-ct-name", "OSB_CAPTURE_NATIVE_VERSION_CHANGED"),
    ("changed-ct-options", "OSB_CAPTURE_CODELIST_TERM_MISMATCH"),
    ("duplicate-ref", "OSB_CAPTURE_SOURCE_REFERENCE_AMBIGUOUS"),
])
def test_incompatible_selected_dependency_fails_before_any_native_write(mutation, code):
    items, port = selected_dependency_fixture()
    identity = items[0]["selection"]["candidateIdentity"]
    native = port.values[identity["uid"]]
    codelist = port.values[items[2]["selection"]["candidateIdentity"]["uid"]]
    if mutation == "missing-version":
        identity["version"] = None
    elif mutation == "wrong-type":
        identity["resourceType"] = "OdmItem"
    elif mutation == "wrong-family":
        identity["resourceFamily"] = "odm_items"
    elif mutation == "stale-version":
        native["version"] = "0.2"
    elif mutation == "wrong-native-uid":
        native["uid"] = "OdmForm_other"
    elif mutation == "wrong-native-ref":
        native["oid"] = "F.OTHER"
    elif mutation == "final-parent":
        native["status"] = identity["status"] = "Final"
    elif mutation == "occupied-parent":
        native["item_groups"] = [{"uid": "Other_group", "order_number": 1, "mandatory": "Yes"}]
    elif mutation == "stale-ct-name":
        codelist["name"]["version"] = "3.0"
    elif mutation == "changed-ct-options":
        codelist["terms"][0]["submission_value"] = "OTHER"
    else:
        duplicate = deepcopy(items[0])
        duplicate["intent"]["factId"] = "another-form"
        items.append(duplicate)
    before = deepcopy(port.values)
    with pytest.raises(OsbCandidateSetError) as error:
        apply(items, port)
    assert error.value.code == code
    assert port.creates == port.associations == port.locked == []
    assert port.bindings == {}
    assert port.values == before


def test_selected_group_must_have_the_exact_native_parent_form_before_child_creation():
    items, port = selected_dependency_fixture(select_group=True)
    group = port.values[items[1]["selection"]["candidateIdentity"]["uid"]]
    group["parents"][0]["oid"] = "F.OTHER"
    with pytest.raises(OsbCandidateSetError) as error:
        apply(items, port)
    assert error.value.code == "OSB_CAPTURE_SOURCE_REFERENCE_MISMATCH"
    assert port.creates == port.associations == port.locked == []
    assert port.bindings == {}


def test_dependency_version_is_rechecked_after_taking_the_study_lock():
    items, port = selected_dependency_fixture()
    lock = port.lock_study

    def change_dependency(*scope):
        lock(*scope)
        port.values[items[0]["selection"]["candidateIdentity"]["uid"]]["version"] = "0.2"

    port.lock_study = change_dependency
    with pytest.raises(OsbCandidateSetError) as error:
        apply(items, port)
    assert error.value.code == "OSB_CAPTURE_NATIVE_VERSION_CHANGED"
    assert port.creates == port.associations == []
    assert port.bindings == {}
    assert len(port.locked) == 1


def test_selected_parent_replay_cannot_use_a_foreign_child_binding_before_lock():
    items, port = selected_dependency_fixture()
    apply(items, port)
    port.bindings["tenant|study|group@1:primary"]["nativeStudyId"] = "Study_foreign"
    port.locked.clear()
    before = deepcopy((port.creates, port.associations, port.values))
    with pytest.raises(OsbCandidateSetError) as error:
        apply(items, port)
    assert error.value.code == "OSB_CAPTURE_BINDING_CONFLICT"
    assert port.locked == []
    assert (port.creates, port.associations, port.values) == before


def selected_library_fixture():
    """An existing complete native library, built through the actual capture executor."""
    items, port = fixture(), NativePort()
    observed = apply(items, port)
    for item in items:
        intent = item["intent"]
        family = intent["resourceFamily"]
        native = port.values[observed[f'{intent["factId"]}@1:primary']["uid"]]
        if family.startswith("odm_"):
            native.update(version="1.0", status="Final")
            version, resource_type = "1.0", {"odm_forms": "OdmForm", "odm_item_groups": "OdmItemGroup", "odm_items": "OdmItem"}[family]
        else:
            version = "2.0" if family == "controlled_terminology_codelists" else "3.0"
            native["version"] = native["attributes"]["version"] = "5.0" if family == "controlled_terminology_codelists" else "7.0"
            native["name"].update(version=version, status="Final")
            native["attributes"]["status"] = "Final"
            for term in native.get("terms", []):
                term["name_status"] = term["attributes_status"] = "Final"
            resource_type = "CTCodelist" if family == "controlled_terminology_codelists" else "CTTerm"
        item["selection"] = {"action": "select", "candidateIdentity": {
            "resourceFamily": family, "resourceType": resource_type, "uid": native["uid"],
            "version": version, "status": "Final",
        }}
    for native in port.values.values():
        for parent in native.get("parents", []):
            parent["version"] = port.values[parent["uid"]]["version"]
    port.creates.clear()
    port.associations.clear()
    port.locked.clear()
    return items, port


@pytest.mark.parametrize("select_group", [False, True])
def test_selected_capture_readbacks_cover_the_whole_created_dependency_graph(select_group):
    items, port = selected_dependency_fixture(select_group=select_group)
    observed = apply(items, port, observe_selected=True)
    assert set(observed) == {f'{item["intent"]["factId"]}@1:primary' for item in items}
    for item in items:
        key = f'{item["intent"]["factId"]}@1:primary'
        value = observed[key]
        assert value["sourceBinding"] == port.bindings[f"tenant|study|{key}"]
        assert value["native"] == port.read(item["intent"]["resourceFamily"], value["uid"])
        receipts, blockers = capture_field_receipts(item["intent"], value)
        assert all(receipt["disposition"] in {"native", "governed_extension"} for receipt in receipts)
        assert blockers == ([] if item["intent"]["resourceFamily"] == "controlled_terminology_codelists"
                            else [{"code": "NATIVE_CAPTURE_LIBRARY_REVIEW_REQUIRED"}])
    codelist = observed["codelist@1:primary"]
    assert codelist["native"]["name"]["version"] == "2.0"
    assert codelist["version"] == codelist["native"]["attributes"]["version"] == "5.0"
    before = deepcopy((port.creates, port.associations, port.bindings))
    assert apply(items, port, observe_selected=True) == observed
    assert (port.creates, port.associations, port.bindings) == before


@pytest.mark.parametrize("canonical_aliases", [False, True])
def test_selected_only_capture_reads_all_families_without_creating_native_objects(canonical_aliases):
    items, port = selected_library_fixture()
    if canonical_aliases:
        for item in items:
            identity = item["selection"]["candidateIdentity"]
            identity["resourceType"] = {"CTCodelist": "CtCodelist", "CTTerm": "CtTerm"}.get(identity["resourceType"], identity["resourceType"])
    observed = apply(items, port, observe_selected=True)
    assert len(observed) == len(items)
    assert port.creates == port.associations == []
    term = observed["term@1:primary"]
    assert term["native"]["name"]["version"] == "3.0"
    assert term["version"] == term["native"]["attributes"]["version"] == "7.0"
    for item in items:
        receipts, blockers = capture_field_receipts(item["intent"], observed[f'{item["intent"]["factId"]}@1:primary'])
        assert blockers == []
        assert all(receipt["disposition"] in {"native", "governed_extension"} for receipt in receipts)


@pytest.mark.parametrize("fact", ["codelist", "term"])
def test_selected_capture_cannot_use_the_attribute_clock_as_the_candidate_version(fact):
    items, port = selected_library_fixture()
    selected = next(item for item in items if item["intent"]["factId"] == fact)
    selected["selection"]["candidateIdentity"]["version"] = port.values[selected["selection"]["candidateIdentity"]["uid"]]["version"]
    before = deepcopy(port.bindings)
    with pytest.raises(OsbCandidateSetError) as error:
        apply(items, port, observe_selected=True)
    assert error.value.code == "OSB_CAPTURE_NATIVE_VERSION_CHANGED"
    assert port.creates == port.associations == port.locked == []
    assert port.bindings == before


def test_selected_capture_source_annotation_cannot_prove_a_changed_native_property():
    items, port = selected_library_fixture()
    identity = items[0]["selection"]["candidateIdentity"]
    port.values[identity["uid"]]["name"] = "Changed native form"
    observed = apply(items, port, observe_selected=True)["form@1:primary"]
    receipts, blockers = capture_field_receipts(items[0]["intent"], observed)
    assert observed["nativeValues"]["name"] == "Changed native form"
    assert observed["sourceBinding"]["source"] == items[0]["intent"]["source"]
    assert next(receipt for receipt in receipts if receipt["sourcePath"] == "/fields/name")["disposition"] == "mismatch"
    assert blockers == [{"code": "NATIVE_CAPTURE_FIELD_NOT_PRESERVED", "sourcePath": "/fields/name"}]
    assert port.creates == port.associations == []


def test_selected_capture_annotation_cannot_be_rebound_to_a_different_source():
    items, port = selected_dependency_fixture()
    apply(items, port, observe_selected=True)
    port.bindings["tenant|study|form@1:primary"]["nativeStudyId"] = "Study_foreign"
    port.locked.clear()
    before = deepcopy((port.creates, port.associations, port.values, port.bindings))
    with pytest.raises(OsbCandidateSetError) as error:
        apply(items, port, observe_selected=True)
    assert error.value.code == "OSB_CAPTURE_BINDING_CONFLICT"
    assert port.locked == []
    assert (port.creates, port.associations, port.values, port.bindings) == before
