"""Closed native capture DTOs for reviewed Proposal V2 create requests.

Only an explicit nativeBody source value enters a write. No guessed library IDs,
review flags, datatype, string length, repeating policy, expression language or
clinical bounds are supplied here. All supplied fields participate in readback.
"""
from copy import deepcopy
import json

_BASE = {"name", "library_name", "oid", "translated_texts", "aliases"}
_VENDORS = {"vendor_elements", "vendor_element_attributes", "vendor_attributes"}
CAPTURE_CONTRACTS = {
    "OdmForm": ("/odms/forms", _BASE | _VENDORS | {"sdtm_version", "repeating"}, {"name", "library_name", "translated_texts", "repeating"}),
    "OdmItemGroup": ("/odms/item-groups", _BASE | _VENDORS | {"repeating", "is_reference_data", "sas_dataset_name", "origin", "purpose", "comment", "sdtm_domain_uids"}, {"name", "library_name", "translated_texts", "repeating", "sdtm_domain_uids"}),
    "OdmItem": ("/odms/items", _BASE | _VENDORS | {"datatype", "prompt", "length", "significant_digits", "sas_field_name", "sds_var_name", "origin", "comment", "codelist", "unit_definitions", "terms"}, {"name", "library_name", "datatype", "translated_texts"}),
    "OdmMethod": ("/odms/methods", _BASE | {"method_type", "formal_expressions"}, {"name", "library_name", "formal_expressions", "translated_texts"}),
    "OdmCondition": ("/odms/conditions", _BASE | {"formal_expressions"}, {"name", "library_name", "formal_expressions", "translated_texts", "aliases"}),
}

CAPTURE_COLLECTIONS = {
    "OdmFormItemGroupLink": ("OdmForm", "OdmItemGroup", "item_groups"),
    "OdmItemGroupItemLink": ("OdmItemGroup", "OdmItem", "items"),
}
COLLECTION_INITIALIZATION_CONTRACT = "odm-collection-initialization/1"


def complete_capture_relation(family, relation):
    """Include the native input contract's optional null relation properties."""
    fields = {"order_number", "mandatory", "collection_exception_condition_oid", "vendor"}
    if family == "OdmItemGroupItemLink":
        fields.update({"key_sequence", "method_oid", "imputation_method_oid", "role", "role_codelist_oid"})
    if (
        not isinstance(relation, dict)
        or set(relation) - fields
        or not {"order_number", "mandatory", "vendor"}.issubset(relation)
        or type(relation["order_number"]) is not int
        or relation["order_number"] < 1
        or relation["mandatory"] not in {"Yes", "No"}
    ):
        raise ValueError("OSB_NATIVE_V2_CAPTURE_REFERENCE_DTO_INVALID")
    value = {key: deepcopy(relation.get(key)) for key in sorted(fields)}
    for key in fields - {"order_number", "mandatory", "vendor"}:
        if value[key] is not None and not isinstance(value[key], str):
            raise ValueError("OSB_NATIVE_V2_CAPTURE_REFERENCE_DTO_INVALID")
    vendor = value["vendor"]
    if not isinstance(vendor, dict) or set(vendor) != {"attributes"} or not isinstance(vendor["attributes"], list):
        raise ValueError("OSB_NATIVE_V2_CAPTURE_REFERENCE_DTO_INVALID")
    attributes, uids = [], set()
    for attribute in vendor["attributes"]:
        if not isinstance(attribute, dict) or set(attribute) - {"uid", "value"}:
            raise ValueError("OSB_NATIVE_V2_CAPTURE_REFERENCE_DTO_INVALID")
        uid, content = attribute.get("uid"), attribute.get("value")
        if not isinstance(uid, str) or not uid or uid in uids or (content is not None and not isinstance(content, str)):
            raise ValueError("OSB_NATIVE_V2_CAPTURE_REFERENCE_DTO_INVALID")
        attributes.append({"uid": uid, "value": content})
        uids.add(uid)
    value["vendor"] = {"attributes": attributes}
    return value


def capture_collection_matches(actual, expected):
    """Compare all relationship values, retaining unknown-property refusal.

    The three native child-definition fields and the declared vendor-definition
    enrichment are read metadata. Everything else must equal the complete
    native relationship input; omitted optional properties cannot hide values.
    """
    if not isinstance(actual, list) or not isinstance(expected, list) or len(actual) != len(expected):
        return False

    def normalized(rows, native):
        values = {}
        for row in rows:
            if not isinstance(row, dict):
                return None
            relation = deepcopy(row)
            if native:
                for name in ("name", "oid", "version"):
                    relation.pop(name, None)
            uid = relation.get("uid")
            if not isinstance(uid, str) or not uid or uid in values:
                return None
            vendor = relation.get("vendor")
            if not isinstance(vendor, dict) or set(vendor) != {"attributes"} or not isinstance(vendor["attributes"], list):
                return None
            attributes = {}
            for attribute in vendor["attributes"]:
                if not isinstance(attribute, dict):
                    return None
                value = deepcopy(attribute)
                if native:
                    for name in ("name", "data_type", "value_regex", "vendor_namespace_uid"):
                        value.pop(name, None)
                if set(value) != {"uid", "value"} or not isinstance(value["uid"], str) or not value["uid"] or value["uid"] in attributes:
                    return None
                attributes[value["uid"]] = value
            relation["vendor"] = {"attributes": [attributes[key] for key in sorted(attributes)]}
            values[uid] = relation
        return [values[key] for key in sorted(values)]

    left, right = normalized(actual, True), normalized(expected, False)
    return left is not None and right is not None and json.dumps(
        left, sort_keys=True, separators=(",", ":"), allow_nan=False
    ) == json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)


def capture_operation_values(resource_type, values):
    path, allowed, required = CAPTURE_CONTRACTS[resource_type]
    body = values.get("nativeBody")
    if not isinstance(body, dict) or not required.issubset(body) or set(body) - allowed:
        return None, None, None, "OSB_NATIVE_V2_CAPTURE_DTO_INCOMPLETE_OR_UNKNOWN"
    if any(not isinstance(body.get(field), str) or not body[field].strip() for field in ("name", "library_name")):
        return None, None, None, "OSB_NATIVE_V2_CAPTURE_IDENTITY_UNSTATED"
    if not isinstance(body.get("translated_texts"), list) or not body["translated_texts"]:
        return None, None, None, "OSB_NATIVE_V2_CAPTURE_DESCRIPTION_UNSTATED"
    if "repeating" in body and body["repeating"] not in ("Yes", "No"):
        return None, None, None, "OSB_NATIVE_V2_CAPTURE_REPEATING_INVALID"
    if resource_type == "OdmItem" and str(body["datatype"]).lower() in ("text", "string") and (type(body.get("length")) is not int or body['length'] < 1):
        return None, None, None, "OSB_NATIVE_V2_CAPTURE_TEXT_LENGTH_UNSTATED"
    if "formal_expressions" in body and (not isinstance(body["formal_expressions"], list) or not body["formal_expressions"]):
        return None, None, None, "OSB_NATIVE_V2_CAPTURE_EXPRESSION_UNSTATED"
    readback = deepcopy(body)
    if resource_type == "OdmItemGroup":
        readback["sdtm_domains"] = [{"term_uid": uid} for uid in readback.pop("sdtm_domain_uids")]
    return path, deepcopy(body), readback, None
