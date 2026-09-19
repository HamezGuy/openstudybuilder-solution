"""Create native capture drafts through the same services as the ODM library UI.

The signed mapping command and the separate source-draft staging command use
these services. Source bindings are append-only annotations, not substitutes
for native clinical properties. Neither path approves a library object or
changes a study lifecycle.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi.encoders import jsonable_encoder
from neomodel import db
from pydantic import ValidationError

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
)
from clinical_mdr_api.services.integrations.native_capture_projection import (
    CAPTURE_BINDING_SCHEMA,
    CAPTURE_FAMILY_TYPES,
    CAPTURE_READBACK_SCHEMA,
    DATATYPES,
    capture_field_receipts,
    fail,
    project_native_values,
    source_values,
)

_ASSERTIONS = {
    "odm_forms": "STUDY_FORM", "odm_item_groups": "STUDY_ITEM_GROUP",
    "odm_items": "STUDY_ITEM", "controlled_terminology_codelists": "CT_CODELIST_STATEMENT",
    "controlled_terminology": "CT_TERM_STATEMENT",
}
_REFERENCE_FIELDS = {
    "odm_forms": "formRef", "odm_item_groups": "itemGroupRef",
    "odm_items": "itemRef", "controlled_terminology_codelists": "codelistKey",
}
_PARENT_FAMILIES = {"odm_item_groups": "odm_forms", "odm_items": "odm_item_groups"}


def _key(intent):
    return f'{intent["factId"]}@{intent["revision"]}:{intent["targetKey"]}'


def _text(fields, name):
    value = fields.get(name)
    if not isinstance(value, str) or not value.strip():
        fail("OSB_CAPTURE_SOURCE_FIELD_REQUIRED", f"A source {name} is required.")
    return value


def _boolean(fields, name):
    value = fields.get(name)
    if not isinstance(value, bool):
        fail("OSB_CAPTURE_SOURCE_FIELD_REQUIRED", f"A source boolean {name} is required.")
    return "Yes" if value else "No"


def _order(fields):
    value = fields.get("order")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        fail("OSB_CAPTURE_SOURCE_ORDER_REQUIRED", "A positive source order is required.")
    return value


def _translated(kind, value):
    return {"text_type": kind, "language": "en", "text": value}


def plan_native_capture(intent: dict[str, Any], *, allow_pending_relationships=False) -> dict[str, Any]:
    """Validate source presence before any mutation; never fill clinical defaults."""
    family = intent.get("resourceFamily")
    if family not in CAPTURE_FAMILY_TYPES \
            or intent.get("source", {}).get("assertionType") != _ASSERTIONS[family]:
        fail("OSB_CAPTURE_SOURCE_TYPE_UNSUPPORTED", "A typed capture definition is required.")
    fields = source_values(intent)
    relationship_blockers = []

    def relationship_field(name, validate):
        if allow_pending_relationships and fields.get(name) is None:
            relationship_blockers.append({"code": f"OSB_CAPTURE_RELATIONSHIP_{name.upper()}_UNKNOWN"})
        else:
            validate(fields)

    if family in {"odm_forms", "odm_item_groups", "odm_items"}:
        body: dict[str, Any] = {"name": _text(fields, "name")}
        oid_field = {"odm_forms": "formRef", "odm_item_groups": "itemGroupRef", "odm_items": "itemRef"}[family]
        body["oid"] = _text(fields, oid_field)
        description = fields.get("description" if family == "odm_forms" else "label", fields["name"])
        if not isinstance(description, str):
            fail("OSB_CAPTURE_SOURCE_FIELD_REQUIRED", "The source description must be text.")
        body["translated_texts"] = [_translated("Description", description)]
        if family in {"odm_forms", "odm_item_groups"}:
            body["repeating"] = _boolean(fields, "repeating")
        if family == "odm_item_groups":
            body["sdtm_domain_uids"] = []
            relationship_field("formRef", lambda values: _text(values, "formRef"))
            relationship_field("order", _order)
            relationship_field("required", lambda values: _boolean(values, "required"))
        if family == "odm_items":
            datatype = _text(fields, "dataType")
            if datatype not in DATATYPES:
                fail("OSB_CAPTURE_DATATYPE_UNSUPPORTED", "The source datatype has no exact native mapping.")
            body["datatype"] = DATATYPES[datatype]
            for source, target in (("questionText", "prompt"), ("length", "length"),
                                   ("significantDigits", "significant_digits"),
                                   ("sdtmVariable", "sds_var_name")):
                if source in fields:
                    body[target] = fields[source]
            if "collectionInstruction" in fields:
                body["translated_texts"].append(_translated(
                    "osb:CompletionInstructions", _text(fields, "collectionInstruction")))
            relationship_field("formRef", lambda values: _text(values, "formRef"))
            relationship_field("itemGroupRef", lambda values: _text(values, "itemGroupRef"))
            relationship_field("order", _order)
            relationship_field("required", lambda values: _boolean(values, "required"))
            if fields.get("options"):
                _text(fields, "codelistKey")
                if not isinstance(fields.get("allowsMultiChoice"), bool):
                    # Widget cardinality is an explicit source fact, not inferred
                    # from the number of listed options or the datatype.
                    widget = fields.get("edcFieldType")
                    if widget not in {"select", "radio", "yesno", "checkbox"}:
                        fail("OSB_CAPTURE_CARDINALITY_REQUIRED", "Source choice cardinality is required.")
            if "unit" in fields and fields["unit"] is not None:
                _text(fields, "unit")
        return {"family": family, "fields": fields, "body": body,
                "relationshipBlockers": relationship_blockers}
    if family == "controlled_terminology_codelists":
        _text(fields, "name")
        _text(fields, "codelistKey")
        values = fields.get("values")
        if not isinstance(values, list) or not values:
            fail("OSB_CAPTURE_CODELIST_VALUES_REQUIRED", "Source permissible values are required.")
        codes, orders = set(), set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {"codedValue", "decode", "orderNumber"}:
                fail("OSB_CAPTURE_CODELIST_INVALID", "A codelist value is incomplete.")
            code = _text(value, "codedValue")
            _text(value, "decode")
            order = value.get("orderNumber")
            if isinstance(order, bool) or not isinstance(order, int) or order < 1 \
                    or code in codes or order in orders:
                fail("OSB_CAPTURE_CODELIST_INVALID", "Codelist codes and orders must be unique.")
            codes.add(code)
            orders.add(order)
    else:
        for name in ("codelistKey", "codedValue", "decode"):
            _text(fields, name)
        order = fields.get("orderNumber")
        if isinstance(order, bool) or not isinstance(order, int) or order < 1:
            fail("OSB_CAPTURE_SOURCE_ORDER_REQUIRED", "A positive source term order is required.")
    return {"family": family, "fields": fields}


def prepare_capture_create_offer(intent):
    """Offer only an executor that can preserve the declared source shape."""
    from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
    from clinical_mdr_api.services.integrations.osb_family_map import NATIVE_CREATE_FAMILIES, canonicalize_family

    family = canonicalize_family(str(intent.get("resourceFamily") or ""))
    option = intent.get("createOption")
    if not isinstance(option, dict) or option.get("allowed") is not True:
        return None
    if family not in CAPTURE_FAMILY_TYPES:
        return {"createOption": None, "blockers": ["OSB_NATIVE_SOURCE_CREATE_UNSUPPORTED"]} \
            if family in NATIVE_CREATE_FAMILIES or family == "odm_aliases" else None
    try:
        NativeCapturePort().validate(plan_native_capture(intent))
    except (OsbCandidateSetError, ValidationError) as error:
        return {"createOption": None, "blockers": [getattr(error, "code", "OSB_CAPTURE_NATIVE_DTO_INVALID")]}
    return {"createOption": {"allowed": True, "requestedNativeType": CAPTURE_FAMILY_TYPES[family]}, "blockers": []}


class NativeCapturePort:
    """Native services write; native DTOs and relationship queries read."""

    @staticmethod
    def _odm(family):
        from clinical_mdr_api.models.odms.form import OdmFormPostInput
        from clinical_mdr_api.models.odms.item import OdmItemPostInput
        from clinical_mdr_api.models.odms.item_group import OdmItemGroupPostInput
        from clinical_mdr_api.services.odms.forms import OdmFormService
        from clinical_mdr_api.services.odms.items import OdmItemService
        from clinical_mdr_api.services.odms.item_groups import OdmItemGroupService

        return {
            "odm_forms": (OdmFormService, OdmFormPostInput),
            "odm_item_groups": (OdmItemGroupService, OdmItemGroupPostInput),
            "odm_items": (OdmItemService, OdmItemPostInput),
        }[family]

    def validate(self, plan):
        if plan["family"].startswith("odm_"):
            _, model = self._odm(plan["family"])
            model(**plan["body"])

    def lock_study(self, tenant_id, platform_study_id, native_study_id):
        from clinical_mdr_api.services.integrations.candidate_set import active_osb_binding
        from clinical_mdr_api.services.integrations.study_metadata_mapping import (
            NativeStudyMetadataPort, _assert_draft,
        )

        if db._active_transaction is None:
            fail("OSB_CAPTURE_TRANSACTION_REQUIRED", "Native capture requires the mapping command transaction.")
        port = NativeStudyMetadataPort()
        port.lock(native_study_id)
        binding = active_osb_binding(tenant_id, platform_study_id)
        if binding["nativeIdentity"] != native_study_id:
            fail("OSB_CAPTURE_STUDY_SCOPE_MISMATCH", "Native capture study binding changed.")
        _assert_draft(port.read(native_study_id), native_study_id)

    def binding(self, binding_key):
        rows, _ = db.cypher_query(
            """MATCH (binding:PlatformNativeCaptureBinding {binding_key:$key})
               MATCH (study:StudyRoot)-[:HAS_PLATFORM_NATIVE_CAPTURE]->(binding)
               RETURN binding.payload_json,binding.payload_hash,study.uid,
                 binding.native_uid,binding.family LIMIT 2""", {"key": binding_key})
        if len(rows) > 1:
            fail("OSB_CAPTURE_BINDING_AMBIGUOUS", "Duplicate capture source bindings.")
        if not rows:
            return None
        import json

        binding = json.loads(rows[0][0])
        if canonical_json(binding) != rows[0][0] or binding.get("bindingKey") != binding_key \
                or binding.get("nativeStudyId") != rows[0][2] or binding.get("uid") != rows[0][3] \
                or binding.get("resourceFamily") != rows[0][4] \
                or canonical_json_hash_ref(binding, schema_version=CAPTURE_BINDING_SCHEMA)["value"] != rows[0][1]:
            fail("OSB_CAPTURE_BINDING_CONFLICT", "Persisted capture binding custody differs.")
        return binding

    def bind(self, binding):
        rows, _ = db.cypher_query(
            """MATCH (study:StudyRoot {uid:$study})
               MERGE (binding:PlatformNativeCaptureBinding {binding_key:$key})
               ON CREATE SET binding.payload_json=$payload,binding.payload_hash=$hash,
                 binding.native_uid=$uid,binding.family=$family,binding.created_at=datetime()
               MERGE (study)-[:HAS_PLATFORM_NATIVE_CAPTURE]->(binding)
               RETURN binding.payload_json""",
            {"study": binding["nativeStudyId"], "key": binding["bindingKey"],
             "uid": binding["uid"], "family": binding["resourceFamily"],
             "payload": canonical_json(binding),
             "hash": canonical_json_hash_ref(binding, schema_version=CAPTURE_BINDING_SCHEMA)["value"]})
        if len(rows) != 1 or rows[0][0] != canonical_json(binding):
            fail("OSB_CAPTURE_BINDING_CONFLICT", "Capture binding names different source content.")

    def create(self, family, body):
        service, model = self._odm(family)
        return jsonable_encoder(service().create(model(**body)), exclude_none=False)

    def create_codelist(self, fields):
        from clinical_mdr_api.models.controlled_terminologies.ct_codelist import CTCodelistCreateInput
        from clinical_mdr_api.models.controlled_terminologies.ct_term import CTTermCreateInput
        from clinical_mdr_api.services.controlled_terminologies.ct_codelist import CTCodelistService
        from clinical_mdr_api.services.controlled_terminologies.ct_term import CTTermService
        from common.config import settings

        service = CTCodelistService()
        codelist = service.create(CTCodelistCreateInput(
            catalogue_names=[], library_name=settings.sponsor_library_name,
            name=fields["name"], submission_value=fields["codelistKey"],
            definition=fields["name"], sponsor_preferred_name=fields["name"],
            extensible=False, is_ordinal=False, template_parameter=False, terms=[],
        ), approve=False)
        # The explicit initial-draft authoring operation keeps all four CT
        # name/attribute heads in Draft. Ordinary add_term requires Final, and
        # supplying terms to create() would approve the codelist attributes.
        for value in sorted(fields["values"], key=lambda item: item["orderNumber"]):
            term = CTTermService().create(CTTermCreateInput(
                catalogue_names=[], codelists=[], library_name=settings.sponsor_library_name,
                definition=value["decode"], sponsor_preferred_name=value["decode"],
                sponsor_preferred_name_sentence_case=value["decode"],
            ), approve=False)
            service.add_initial_draft_term(
                codelist.codelist_uid, term.term_uid, value["orderNumber"], value["codedValue"])
        return self.read("controlled_terminology_codelists", codelist.codelist_uid)

    def _parents(self, family, uid, version):
        if family == "odm_forms":
            return []
        child, parent, relation = (
            ("OdmItemGroup", "OdmForm", "ITEM_GROUP_REF") if family == "odm_item_groups"
            else ("OdmItem", "OdmItemGroup", "ITEM_REF")
        )
        rows, _ = db.cypher_query(
            f"""MATCH (child:{child}Root {{uid:$uid}})-[cv:HAS_VERSION]->(value:{child}Value)
                WHERE cv.version=$version
                MATCH (root:{parent}Root)-[:LATEST]->(parent:{parent}Value)-[edge:{relation}]->(value)
                MATCH (root)-[pv:HAS_VERSION]->(parent) WHERE pv.end_date IS NULL
                RETURN root.uid,pv.version,parent.oid,edge.order_number,edge.mandatory
                ORDER BY root.uid,pv.version""", {"uid": uid, "version": version})
        result = [{"uid": row[0], "version": row[1], "oid": row[2],
                   "order_number": row[3], "mandatory": row[4]} for row in rows]
        if family == "odm_items":
            for parent_value in result:
                parent_value["parents"] = self._parents(
                    "odm_item_groups", parent_value["uid"], parent_value["version"])
        return result

    def _codelist_items(self, uid):
        rows, _ = db.cypher_query(
            """MATCH (root:OdmItemRoot)-[:LATEST]->(value:OdmItemValue)-[:HAS_CODELIST]->
                 (:CTCodelistRoot {uid:$uid})
               MATCH (root)-[version:HAS_VERSION]->(value) WHERE version.end_date IS NULL
               RETURN root.uid,version.version,value.oid,value.datatype ORDER BY value.oid,root.uid""", {"uid": uid})
        return [{"uid": row[0], "version": row[1], "oid": row[2], "datatype": row[3]} for row in rows]

    def read(self, family, uid, version=None):
        def current(value):
            result = jsonable_encoder(value, exclude_none=False)
            if version is not None and result["version"] != version:
                fail("OSB_CAPTURE_NATIVE_VERSION_CHANGED", "The current native capture version differs from the retained target.")
            return result

        if family.startswith("odm_"):
            service, _ = self._odm(family)
            # Evidence currentness must not read an old historical version and
            # thereby hide a later native edit with the same root UID.
            native = current(service().get_by_uid(uid))
            native["parents"] = self._parents(family, uid, native["version"])
            return native
        if family == "controlled_terminology_codelists":
            from clinical_mdr_api.services.controlled_terminologies.ct_codelist import CTCodelistService
            from clinical_mdr_api.services.controlled_terminologies.ct_codelist_name import CTCodelistNameService
            from clinical_mdr_api.services.controlled_terminologies.ct_codelist_attributes import CTCodelistAttributesService

            # CT names and attributes have independent version clocks. Read
            # both current heads; the complete raw DTO hash binds both.
            name = CTCodelistNameService().get_by_uid(uid)
            attributes = CTCodelistAttributesService().get_by_uid(uid)
            terms = CTCodelistService().list_terms(codelist_uid=uid, include_removed=False).items
            return current({"uid": uid, "version": attributes.version,
                            "name": name, "attributes": attributes,
                            "terms": terms, "items": self._codelist_items(uid)})
        from clinical_mdr_api.services.controlled_terminologies.ct_term import CTTermService
        from clinical_mdr_api.services.controlled_terminologies.ct_term_name import CTTermNameService
        from clinical_mdr_api.services.controlled_terminologies.ct_term_attributes import CTTermAttributesService

        name = CTTermNameService().get_by_uid(uid)
        attributes = CTTermAttributesService().get_by_uid(uid)
        terms = CTTermService().get_all_terms(codelist_uid=None, codelist_name=None, library=None,
                                            package=None, filter_by={"term_uid": {"v": [uid], "op": "eq"}}).items
        if len(terms) != 1:
            fail("OSB_CAPTURE_NATIVE_INVALID", "Native term membership is ambiguous.")
        return current({"uid": uid, "version": attributes.version, "name": name,
                        "attributes": attributes, "codelists": terms[0].codelists})

    def read_unit(self, identity):
        from clinical_mdr_api.services.concepts.unit_definitions.unit_definition import UnitDefinitionService

        return jsonable_encoder(UnitDefinitionService().get_by_uid(
            identity["uid"], version=identity["version"]), exclude_none=False)

    def associate(self, family, uid, children):
        from clinical_mdr_api.models.odms.form import OdmFormItemGroupPostInput
        from clinical_mdr_api.models.odms.item_group import OdmItemGroupItemPostInput

        service, _ = self._odm(family)
        current = self.read(family, uid)
        field = "item_groups" if family == "odm_forms" else "items"
        expected = [{key: item[key] for key in ("uid", "order_number", "mandatory")} for item in children]
        actual = [{key: item[key] for key in ("uid", "order_number", "mandatory")} for item in current[field]]
        if canonical_json(actual) == canonical_json(expected):
            return
        if actual or current["status"] != "Draft":
            fail("OSB_CAPTURE_RELATIONSHIP_CONFLICT", "Existing native relationships differ from the source.")
        model = OdmFormItemGroupPostInput if family == "odm_forms" else OdmItemGroupItemPostInput
        operation = service().add_item_groups if family == "odm_forms" else service().add_items
        operation(uid, [model(**value, vendor={"attributes": []}) for value in children], preserve_order=True)


def _source_binding(intent, binding_key, native_study_id, uid):
    return {"bindingKey": binding_key, "nativeStudyId": native_study_id,
            "resourceFamily": intent["resourceFamily"], "uid": uid,
            "sourceInputHash": canonical_json_hash_ref(intent, schema_version="OsbTypedSourceIntentV1@1.0.0"),
            "source": deepcopy(intent["source"])}


def read_capture_target(target: dict[str, Any], *, port=None) -> dict[str, Any]:
    port = port or NativeCapturePort()
    binding = port.binding(target["bindingKey"])
    if not binding or binding["uid"] != target["uid"] or binding["resourceFamily"] != target["resourceFamily"]:
        fail("OSB_CAPTURE_SOURCE_BINDING_MISMATCH", "The native capture binding no longer matches.")
    native = port.read(target["resourceFamily"], target["uid"], target.get("version"))
    family = binding["resourceFamily"]
    if "candidateIdentity" in target:
        assert_selected_capture_identity(family, target["candidateIdentity"], native)
    name = native.get("name") if family.startswith("odm_") else native.get("attributes", {}).get("name")
    if family == "controlled_terminology":
        name = native.get("name", {}).get("sponsor_preferred_name")
    return {"uid": native["uid"], "version": native["version"], "label": name,
            "resourceFamily": family, "resourceType": CAPTURE_FAMILY_TYPES[family],
            "sourceBinding": binding, "native": native,
            "nativeValues": project_native_values(family, native)}


def assert_selected_capture_identity(family, identity, native):
    """Match an offered identity against the correct current native version clock."""
    resource_type = CAPTURE_FAMILY_TYPES.get(family)
    resource_types = {resource_type}
    aliases = {"controlled_terminology_codelists": "CTCodelist", "controlled_terminology": "CTTerm"}
    if family in aliases:
        resource_types.add(aliases[family])
    if resource_type is None or not isinstance(identity, dict) or identity.get("resourceFamily") != family \
            or identity.get("resourceType") not in resource_types \
            or any(not isinstance(identity.get(name), str) or not identity[name].strip()
                   for name in ("uid", "version")):
        fail("OSB_CAPTURE_SELECTED_IDENTITY_INVALID", "A selected dependency needs an exact native identity and version.")
    if not isinstance(native, dict) or native.get("uid") != identity["uid"]:
        fail("OSB_CAPTURE_SELECTED_IDENTITY_INVALID", "The selected dependency read returned a different native identity.")
    # Both CT candidate families pin the name head. The full readback independently
    # retains the attributes clock as its outer version; never substitute one.
    versioned = native.get("name") if family in aliases else native
    if not isinstance(versioned, dict):
        fail("OSB_CAPTURE_SELECTED_IDENTITY_INVALID", "The selected native version is unavailable.")
    if family in aliases and (not isinstance(native.get("attributes"), dict)
                              or native["attributes"].get("version") != native.get("version")
                              or not isinstance(native.get("version"), str) or not native["version"]):
        fail("OSB_CAPTURE_SELECTED_IDENTITY_INVALID", "The native CT attribute version is unavailable.")
    if versioned.get("version") != identity["version"]:
        fail("OSB_CAPTURE_NATIVE_VERSION_CHANGED", "The selected dependency is no longer the current native version.")
    if identity.get("status") is not None and versioned.get("status") != identity["status"]:
        fail("OSB_CAPTURE_SELECTED_IDENTITY_INVALID", "The selected dependency status changed.")


def _read_selected_dependency(item, fields, port):
    family = item["intent"]["resourceFamily"]
    identity = item["selection"].get("candidateIdentity")
    if not isinstance(identity, dict) or not isinstance(identity.get("uid"), str) or not identity["uid"]:
        fail("OSB_CAPTURE_SELECTED_IDENTITY_INVALID", "A selected dependency needs an exact native identity.")
    native = port.read(family, identity["uid"])
    assert_selected_capture_identity(family, identity, native)
    projected = project_native_values(family, native)
    names = [_REFERENCE_FIELDS.get(family, "codelistKey")]
    if family in {"odm_item_groups", "odm_items"}:
        names.append("formRef")
    if family == "odm_items":
        names.append("itemGroupRef")
    if any(projected.get(name) != fields.get(name) for name in names):
        fail("OSB_CAPTURE_SOURCE_REFERENCE_MISMATCH", "The selected native dependency does not match its source reference.")
    return native


def apply_native_capture_selections(
    items, *, tenant_id, platform_study_id, native_study_id, port=None,
    allow_pending_relationships=False, outcomes=None, observe_selected=False,
):
    """Create the dependency graph, then observe it after all links exist."""
    captures = [item for item in items if item["intent"].get("resourceFamily") in CAPTURE_FAMILY_TYPES
                and item["selection"]["action"] == "create"]
    capture_selects = [item for item in items if observe_selected
                       and item["intent"].get("resourceFamily") in CAPTURE_FAMILY_TYPES
                       and item["selection"]["action"] == "select"]
    if not captures and not capture_selects:
        return {}
    port = port or NativeCapturePort()
    capture_intents = {_key(item["intent"]): item["intent"] for item in captures}
    plans = {key: plan_native_capture(intent, allow_pending_relationships=allow_pending_relationships)
             for key, intent in capture_intents.items()}
    if len(plans) != len(captures):
        fail("OSB_CAPTURE_SOURCE_REFERENCE_AMBIGUOUS", "Duplicate selected capture source identity.")
    if len({_key(item["intent"]) for item in captures + capture_selects}) != len(captures) + len(capture_selects):
        fail("OSB_CAPTURE_SOURCE_REFERENCE_AMBIGUOUS", "Duplicate selected capture source identity.")
    for plan in plans.values():
        port.validate(plan)
    source_prefix = f"{tenant_id}|{platform_study_id}|"
    identities, refs, observations = {}, {}, {}
    source_fields = {key: plan["fields"] for key, plan in plans.items()}
    selected = {}
    # Reference keys are family-scoped; repeated identities are never silently
    # overwritten by the last fact encountered in a source package.
    for item in items:
        intent, selection = item["intent"], item["selection"]
        key, family = _key(intent), intent.get("resourceFamily")
        ref_field = _REFERENCE_FIELDS.get(family)
        if ref_field is None or selection["action"] not in {"create", "select"}:
            continue
        if selection["action"] == "select":
            if family == "odm_items":
                continue
            source = intent.get("source")
            if not isinstance(source, dict) or not isinstance(source.get("values"), list):
                continue
            fields = source_values(intent)
            if not isinstance(fields.get(ref_field), str) or not fields[ref_field].strip():
                continue
            if key in source_fields:
                fail("OSB_CAPTURE_SOURCE_REFERENCE_AMBIGUOUS", "Duplicate selected capture source identity.")
            source_fields[key], selected[key] = fields, item
        ref_key = (family, source_fields[key][ref_field])
        if ref_key in refs:
            fail("OSB_CAPTURE_SOURCE_REFERENCE_AMBIGUOUS", "Multiple source facts name the same capture definition.")
        refs[ref_key] = key

    def reference_key(family, ref):
        key = refs.get((family, ref))
        if key is None:
            fail("OSB_CAPTURE_SOURCE_REFERENCE_MISSING", "A source relationship has no mapped native definition.")
        return key

    def reference(family, ref):
        key = reference_key(family, ref)
        if key not in identities:
            fail("OSB_CAPTURE_SOURCE_REFERENCE_MISSING", "A source relationship has no mapped native definition.")
        return identities[key]

    def preflight_dependencies():
        dependencies, children_by_source = {}, {}
        for item in capture_selects:
            intent = item["intent"]
            key, binding_key = _key(intent), source_prefix + _key(intent)
            dependencies[key] = _read_selected_dependency(item, source_values(intent), port)
            existing = port.binding(binding_key)
            if existing and existing != _source_binding(intent, binding_key, native_study_id, dependencies[key]["uid"]):
                fail("OSB_CAPTURE_BINDING_CONFLICT", "Existing native binding has different source content.")

        def dependency(family, ref):
            key = reference_key(family, ref)
            if key in selected and key not in dependencies:
                dependencies[key] = _read_selected_dependency(selected[key], source_fields[key], port)
            return key

        for key, plan in plans.items():
            family, fields = plan["family"], plan["fields"]
            parent_family = _PARENT_FAMILIES.get(family)
            if parent_family and not plan.get("relationshipBlockers"):
                ref_field = _REFERENCE_FIELDS[parent_family]
                if not allow_pending_relationships or (parent_family, fields[ref_field]) in refs:
                    parent_key = dependency(parent_family, fields[ref_field])
                    if family == "odm_items" and source_fields[parent_key].get("formRef") != fields["formRef"]:
                        fail("OSB_CAPTURE_SOURCE_REFERENCE_MISMATCH", "Item form and group disagree.")
                    children_by_source.setdefault(parent_key, []).append((key, fields))
            if family == "controlled_terminology" or (family == "odm_items" and fields.get("options")):
                codelist_key = dependency("controlled_terminology_codelists", fields["codelistKey"])
                values = (
                    project_native_values("controlled_terminology_codelists", dependencies[codelist_key])["values"]
                    if codelist_key in dependencies else source_fields[codelist_key]["values"]
                )
                if family == "odm_items":
                    expected = [{"label": value["decode"], "value": value["codedValue"]}
                                for value in sorted(values, key=lambda value: value["orderNumber"])]
                    if expected != fields["options"]:
                        fail("OSB_CAPTURE_CODELIST_TERM_MISMATCH", "Item options differ from the mapped codelist.")
                elif sum(all(value[name] == fields[name] for name in ("codedValue", "decode", "orderNumber"))
                         for value in values) != 1:
                    fail("OSB_CAPTURE_CODELIST_TERM_MISMATCH", "The term differs from its source codelist.")
        for parent_key, children in children_by_source.items():
            if len({fields["order"] for _, fields in children}) != len(children):
                fail("OSB_CAPTURE_SOURCE_ORDER_AMBIGUOUS", "Sibling source orders must be unique.")
            if parent_key not in dependencies:
                continue
            parent = dependencies[parent_key]
            family = selected[parent_key]["intent"]["resourceFamily"]
            field = "item_groups" if family == "odm_forms" else "items"
            actual = [{name: value[name] for name in ("uid", "order_number", "mandatory")}
                      for value in parent[field]]
            expected = []
            for child_key, fields in sorted(children, key=lambda child: child[1]["order"]):
                binding_key = source_prefix + child_key
                binding = port.binding(binding_key)
                if binding and binding != _source_binding(
                    capture_intents[child_key], binding_key, native_study_id, binding["uid"]
                ):
                    fail("OSB_CAPTURE_BINDING_CONFLICT", "Existing native binding has different source content.")
                expected.append({"uid": binding["uid"] if binding else None,
                                 "order_number": fields["order"], "mandatory": _boolean(fields, "required")})
            if canonical_json(actual) != canonical_json(expected) and (actual or parent["status"] != "Draft"):
                fail("OSB_CAPTURE_RELATIONSHIP_CONFLICT", "The selected native parent cannot accept these source children.")
        return dependencies

    # Refuse known incompatible selections before any mutation, then re-read
    # their current identities inside the mapping command's study lock.
    identities.update(preflight_dependencies())
    port.lock_study(tenant_id, platform_study_id, native_study_id)
    if identities:
        identities.update(preflight_dependencies())

    priorities = {family: index for index, family in enumerate((
        "controlled_terminology_codelists", "odm_forms", "odm_item_groups", "odm_items", "controlled_terminology"))}
    ordered = sorted(captures, key=lambda item: priorities[item["intent"]["resourceFamily"]])
    for item in ordered:
        intent = item["intent"]
        key, family = _key(intent), intent["resourceFamily"]
        plan, binding_key = plans[key], source_prefix + key
        fields = plan["fields"]
        existing = port.binding(binding_key)
        if existing:
            if existing != _source_binding(intent, binding_key, native_study_id, existing["uid"]):
                fail("OSB_CAPTURE_BINDING_CONFLICT", "Existing native binding has different source content.")
            native = port.read(family, existing["uid"])
        elif family == "controlled_terminology_codelists":
            native = port.create_codelist(fields)
        elif family == "controlled_terminology":
            codelist = reference("controlled_terminology_codelists", fields["codelistKey"])
            matches = [term for term in codelist["terms"] if term["submission_value"] == fields["codedValue"]
                       and term["sponsor_preferred_name"] == fields["decode"]
                       and term["order"] == fields.get("orderNumber")]
            if len(matches) != 1:
                fail("OSB_CAPTURE_CODELIST_TERM_MISMATCH", "The term differs from its source codelist.")
            native = port.read(family, matches[0]["term_uid"])
        else:
            body = deepcopy(plan["body"])
            if family == "odm_items" and fields.get("options"):
                codelist = reference("controlled_terminology_codelists", fields["codelistKey"])
                terms = sorted(codelist["terms"], key=lambda term: term["order"])
                expected = [{"label": term["sponsor_preferred_name"], "value": term["submission_value"]} for term in terms]
                if expected != fields["options"]:
                    fail("OSB_CAPTURE_CODELIST_TERM_MISMATCH", "Item options differ from the mapped codelist.")
                body["codelist"] = {"uid": codelist["uid"], "allows_multi_choice": fields.get(
                    "allowsMultiChoice", fields.get("edcFieldType") == "checkbox")}
                body["terms"] = [{"uid": term["term_uid"], "order": term["order"],
                                  "display_text": term["sponsor_preferred_name"], "mandatory": False} for term in terms]
            if family == "odm_items" and fields.get("unit") is not None:
                units = []
                for source_item in items:
                    if source_item["intent"].get("resourceFamily") == "units" \
                            and source_item["selection"]["action"] == "select":
                        unit = port.read_unit(source_item["selection"]["candidateIdentity"])
                        if unit.get("name") == fields["unit"]:
                            units.append(unit)
                if len(units) != 1 and allow_pending_relationships:
                    plan.setdefault("stagingBlockers", []).append({"code": "OSB_CAPTURE_UNIT_REFERENCE_REQUIRED"})
                elif len(units) != 1:
                    fail("OSB_CAPTURE_UNIT_REFERENCE_REQUIRED", "One exact selected native unit is required.")
                else:
                    body["unit_definitions"] = [{"uid": units[0]["uid"], "order": 1, "mandatory": False}]
            native = port.create(family, body)
        identities[key] = native
        if outcomes is not None:
            outcomes[key] = {"outcome": "reused" if existing else "created",
                             "blockers": deepcopy(plan.get("relationshipBlockers", []) + plan.get("stagingBlockers", []))}
        if not existing:
            port.bind(_source_binding(intent, binding_key, native_study_id, native["uid"]))
    children_by_parent = {}
    for key, plan in plans.items():
        family, fields = plan["family"], plan["fields"]
        parent_family = _PARENT_FAMILIES.get(family)
        if parent_family:
            # A native Mandatory flag is a collection requirement. Retain the
            # draft definition, but do not invent a relationship value when the
            # semantic source has not stated one.
            if plan.get("relationshipBlockers"):
                continue
            ref_field = "formRef" if parent_family == "odm_forms" else "itemGroupRef"
            if allow_pending_relationships and (parent_family, fields[ref_field]) not in refs:
                if outcomes is not None:
                    outcomes[key]["blockers"].append({"code": "OSB_CAPTURE_SOURCE_REFERENCE_MISSING"})
                continue
            parent = reference(parent_family, fields[ref_field])
            if family == "odm_items":
                group_key = refs[("odm_item_groups", fields["itemGroupRef"])]
                if source_fields[group_key]["formRef"] != fields["formRef"]:
                    fail("OSB_CAPTURE_SOURCE_REFERENCE_MISMATCH", "Item form and group disagree.")
            children_by_parent.setdefault((parent_family, parent["uid"]), []).append(
                {"uid": identities[key]["uid"], "order_number": fields["order"],
                 "mandatory": _boolean(fields, "required")})
    for (family, uid), children in children_by_parent.items():
        if len({child["order_number"] for child in children}) != len(children):
            fail("OSB_CAPTURE_SOURCE_ORDER_AMBIGUOUS", "Sibling source orders must be unique.")
        port.associate(family, uid, sorted(children, key=lambda child: child["order_number"]))
    for key, item in selected.items():
        if key in identities:
            _read_selected_dependency(item, source_fields[key], port)
    for item in capture_selects:
        intent = item["intent"]
        key, binding_key = _key(intent), source_prefix + _key(intent)
        native = _read_selected_dependency(item, source_values(intent), port)
        expected = _source_binding(intent, binding_key, native_study_id, native["uid"])
        existing = port.binding(binding_key)
        if existing and existing != expected:
            fail("OSB_CAPTURE_BINDING_CONFLICT", "Existing native binding has different source content.")
        if not existing:
            port.bind(expected)
        observed = read_capture_target({
            "bindingKey": binding_key, "resourceFamily": intent["resourceFamily"],
            "uid": native["uid"], "version": native["version"],
            "candidateIdentity": item["selection"]["candidateIdentity"],
        }, port=port)
        # The annotation supplies custody only. Native DTO projection still proves
        # each clinical field, and mismatches/review debt remain checkpoint blockers.
        capture_field_receipts(intent, observed, binding_key=binding_key, native_study_id=native_study_id)
        observations[key] = observed
    for item in captures:
        intent = item["intent"]
        key = _key(intent)
        native = identities[key]
        observed = read_capture_target({"bindingKey": source_prefix + key, "resourceFamily": intent["resourceFamily"],
                                        "uid": native["uid"]}, port=port)
        # This also checks the retained source, including null/empty/false values.
        # Unmapped fields become explicit checkpoint blockers, never success flags.
        capture_field_receipts(intent, observed, binding_key=source_prefix + key,
                               native_study_id=native_study_id)
        observations[key] = observed
    return observations
