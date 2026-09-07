"""Closed native capture DTOs for reviewed Proposal V2 create requests.

Only an explicit nativeBody source value enters a write. No guessed library IDs,
review flags, datatype, string length, repeating policy, expression language or
clinical bounds are supplied here. All supplied fields participate in readback.
"""
from copy import deepcopy

_BASE = {"name", "library_name", "oid", "translated_texts", "aliases"}
_VENDORS = {"vendor_elements", "vendor_element_attributes", "vendor_attributes"}
CAPTURE_CONTRACTS = {
    "OdmForm": ("/odms/forms", _BASE | _VENDORS | {"sdtm_version", "repeating"}, {"name", "library_name", "translated_texts", "repeating"}),
    "OdmItemGroup": ("/odms/item-groups", _BASE | _VENDORS | {"repeating", "is_reference_data", "sas_dataset_name", "origin", "purpose", "comment", "sdtm_domain_uids"}, {"name", "library_name", "translated_texts", "repeating", "sdtm_domain_uids"}),
    "OdmItem": ("/odms/items", _BASE | _VENDORS | {"datatype", "prompt", "length", "significant_digits", "sas_field_name", "sds_var_name", "origin", "comment", "codelist", "unit_definitions", "terms"}, {"name", "library_name", "datatype", "translated_texts"}),
    "OdmMethod": ("/odms/methods", _BASE | {"method_type", "formal_expressions"}, {"name", "library_name", "formal_expressions", "translated_texts"}),
    "OdmCondition": ("/odms/conditions", _BASE | {"formal_expressions"}, {"name", "library_name", "formal_expressions", "translated_texts", "aliases"}),
}

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
