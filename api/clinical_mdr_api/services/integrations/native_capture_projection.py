"""Source-field verification for real ODM/CT readbacks.

The raw native DTOs are the authority for execution fields. Retained source
annotations account for provenance; they cannot stand in for an ODM property.
This module is pure so the CSL consumer can test the same wire fixtures.
"""

from __future__ import annotations

from typing import Any

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
)
from clinical_mdr_api.services.integrations.osb_vocabulary_registry import ROWS

CAPTURE_READBACK_SCHEMA = "OsbNativeCaptureReadBackV1@1.0.0"
CAPTURE_BINDING_SCHEMA = "OsbNativeCaptureSourceBindingV1@1.0.0"
# Family -> the resourceType string this read-back profile carries. It is a hashed evidence contract, so the two CT
# spellings (CtTerm, CtCodelist) stay as they were written; the registry states them per row as
# captureReadbackResourceType and the CSL read-back validator derives the same table from the same rows.
CAPTURE_FAMILY_TYPES: dict[str, str] = {
    row["osbFamily"]: row["captureReadbackResourceType"]
    for row in ROWS.values() if row["captureReadbackResourceType"]
}
# These fields describe origin, not a clinical control or a terminology approval.
SOURCE_ANNOTATION_FIELDS = frozenset({
    "memberClaimIds", "derivation", "fromStandards", "artifactRole",
    "artifactRoutingReason", "testCodeHintSource", "sdtmMappingSource",
})
DATATYPES = {
    "text": "text", "string": "string", "integer": "integer",
    "float": "float", "double": "double", "decimal": "decimal",
    "number": "float", "boolean": "boolean", "date": "date",
    "datetime": "datetime", "time": "time", "partialDate": "partialDate",
    "partialDatetime": "partialDatetime", "incompleteDate": "incompleteDate",
    "incompleteDatetime": "incompleteDatetime",
}


def fail(code: str, message: str):
    # candidate_set also prepares capture offers; avoid an import cycle.
    from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError

    raise OsbCandidateSetError(code, message, 422)


def source_values(intent: dict[str, Any]) -> dict[str, Any]:
    source = intent.get("source")
    values = source.get("values") if isinstance(source, dict) else None
    if not isinstance(values, list):
        fail("OSB_CAPTURE_SOURCE_INVALID", "Typed source values are required.")
    result = {}
    paths = set()
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str) \
                or not value["name"] or "value" not in value \
                or not isinstance(value.get("sourcePath"), str) \
                or value["name"] in result or value["sourcePath"] in paths:
            fail("OSB_CAPTURE_SOURCE_INVALID", "Source names and paths must be unique.")
        result[value["name"]] = value["value"]
        paths.add(value["sourcePath"])
    return result


def _boolean(value):
    if isinstance(value, bool) or value is None:
        return value
    if value in ("Yes", "yes", "true"):
        return True
    if value in ("No", "no", "false"):
        return False
    fail("OSB_CAPTURE_NATIVE_INVALID", "Native boolean is not recognized.")


def _text(native, kind):
    values = [value["text"] for value in native.get("translated_texts", [])
              if value.get("text_type") == kind and value.get("language") == "en"]
    if len(values) > 1:
        fail("OSB_CAPTURE_NATIVE_INVALID", "Native translated text is ambiguous.")
    return values[0] if values else None


def _one(values, label):
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        fail("OSB_CAPTURE_NATIVE_INVALID", f"Native {label} must be an object array.")
    if len(values) > 1:
        fail("OSB_CAPTURE_NATIVE_INVALID", f"Native {label} is ambiguous.")
    return values[0] if values else None


def _ordered(values):
    if not isinstance(values, list) or any(
        not isinstance(value, dict) or isinstance(value.get("order"), bool)
        or not isinstance(value.get("order"), int)
        or abs(value["order"]) > 9007199254740991 for value in values
    ):
        fail("OSB_CAPTURE_NATIVE_INVALID", "Native choice order must be an exact integer.")
    return sorted(values, key=lambda value: value["order"])


def project_native_values(family: str, native: dict[str, Any]) -> dict[str, Any]:
    """Project ONLY independently read native values, including actual edges."""
    if family not in CAPTURE_FAMILY_TYPES or not isinstance(native, dict):
        fail("OSB_CAPTURE_NATIVE_INVALID", "Unknown capture readback family.")
    if family in {"odm_forms", "odm_item_groups", "odm_items"}:
        result = {"name": native.get("name")}
        if family == "odm_forms":
            result.update(formRef=native.get("oid"), repeating=_boolean(native.get("repeating")),
                          description=_text(native, "Description"))
        elif family == "odm_item_groups":
            parent = _one(native.get("parents", []), "parent form")
            result.update(itemGroupRef=native.get("oid"), repeating=_boolean(native.get("repeating")),
                          label=_text(native, "Description"))
            if parent:
                result.update(formRef=parent.get("oid"), order=parent.get("order_number"),
                              required=_boolean(parent.get("mandatory")))
        else:
            parent = _one(native.get("parents", []), "parent group")
            result.update(itemRef=native.get("oid"), label=_text(native, "Description"),
                          questionText=native.get("prompt"), dataType=native.get("datatype"),
                          length=native.get("length"), significantDigits=native.get("significant_digits"),
                          sdtmVariable=native.get("sds_var_name"),
                          collectionInstruction=_text(native, "osb:CompletionInstructions"))
            if parent:
                form = _one(parent.get("parents", []), "parent form")
                result.update(itemGroupRef=parent.get("oid"), order=parent.get("order_number"),
                              required=_boolean(parent.get("mandatory")))
                if form:
                    result["formRef"] = form.get("oid")
            unit = _one(native.get("unit_definitions", []), "unit")
            if unit:
                result["unit"] = unit.get("name")
            result["options"] = [
                {"label": term.get("display_text") if term.get("display_text") is not None else term.get("name"),
                 "value": term.get("submission_value")}
                for term in _ordered(native.get("terms", []))
            ]
            codelist = native.get("codelist")
            if codelist:
                result["codelistName"] = codelist.get("name")
                result["codelistKey"] = codelist.get("submission_value")
                result["allowsMultiChoice"] = codelist.get("allows_multi_choice")
        return result
    if family == "controlled_terminology_codelists":
        attributes = native.get("attributes", {})
        item_types = sorted(set(item.get("datatype") for item in native.get("items", [])
                                if isinstance(item.get("datatype"), str)))
        result = {
            "name": attributes.get("name"),
            "codelistKey": attributes.get("submission_value"),
            "values": [{"codedValue": term.get("submission_value"), "decode": term.get("sponsor_preferred_name"),
                        "orderNumber": term.get("order")}
                       for term in _ordered(native.get("terms", []))],
            "itemRefs": sorted(item["oid"] for item in native.get("items", [])),
        }
        if len(item_types) == 1:
            result["dataType"] = item_types[0]
        return result
    membership = _one(native.get("codelists", []), "term codelist")
    result = {"decode": native.get("name", {}).get("sponsor_preferred_name")}
    if membership:
        result.update(codedValue=membership.get("submission_value"), orderNumber=membership.get("order"),
                      codelistKey=membership.get("codelist_submission_value"),
                      codelistName=membership.get("codelist_name"))
    return result


def field_matches(name: str, source: Any, observed: Any) -> bool:
    if name == "dataType":
        return isinstance(source, str) and source in DATATYPES and DATATYPES[source] == observed
    if name == "itemRefs" and isinstance(source, list) and isinstance(observed, list):
        return len(source) == len(set(source)) and sorted(source) == sorted(observed)
    return canonical_json(source) == canonical_json(observed)


def assert_capture_readback(observation: dict[str, Any], binding_key: str | None = None):
    fields = {"uid", "version", "label", "resourceFamily", "resourceType",
              "sourceBinding", "native", "nativeValues"}
    if not isinstance(observation, dict) or set(observation) != fields \
            or any(not isinstance(observation.get(key), str) or not observation[key]
                   for key in ("uid", "version", "resourceFamily", "resourceType")) \
            or not isinstance(observation.get("label"), str) \
            or CAPTURE_FAMILY_TYPES.get(observation["resourceFamily"]) != observation["resourceType"]:
        fail("OSB_CAPTURE_NATIVE_INVALID", "The native capture readback identity is invalid.")
    binding, native = observation.get("sourceBinding"), observation.get("native")
    if not isinstance(binding, dict) or set(binding) != {
        "bindingKey", "nativeStudyId", "resourceFamily", "uid", "sourceInputHash", "source"
    } or not isinstance(native, dict) or binding.get("uid") != observation["uid"] \
            or binding.get("resourceFamily") != observation["resourceFamily"] \
            or not isinstance(binding.get("bindingKey"), str) or not binding["bindingKey"] \
            or (binding_key is not None and binding["bindingKey"] != binding_key) \
            or not isinstance(binding.get("nativeStudyId"), str) or not binding["nativeStudyId"] \
            or native.get("uid") != observation["uid"] or native.get("version") != observation["version"]:
        fail("OSB_CAPTURE_SOURCE_BINDING_MISMATCH", "The capture source and native identities differ.")
    if canonical_json(project_native_values(observation["resourceFamily"], native)) \
            != canonical_json(observation.get("nativeValues")):
        fail("OSB_CAPTURE_PROJECTION_MISMATCH", "Projection differs from raw native values.")


def capture_field_receipts(
    intent: dict[str, Any], observation: dict[str, Any], *,
    binding_key: str | None = None, native_study_id: str | None = None,
):
    """Recompute the projection; caller-provided coverage flags are ignored."""
    source_values(intent)
    assert_capture_readback(observation, binding_key)
    family = intent["resourceFamily"]
    if observation.get("resourceFamily") != family \
            or observation.get("resourceType") != CAPTURE_FAMILY_TYPES.get(family) \
            or observation.get("sourceBinding", {}).get("source") != intent["source"] \
            or (native_study_id is not None and observation["sourceBinding"]["nativeStudyId"] != native_study_id) \
            or observation.get("sourceBinding", {}).get("sourceInputHash") != canonical_json_hash_ref(
                intent, schema_version="OsbTypedSourceIntentV1@1.0.0"):
        fail("OSB_CAPTURE_SOURCE_BINDING_MISMATCH", "Native source binding differs.")
    projected = project_native_values(family, observation["native"])
    if canonical_json(projected) != canonical_json(observation.get("nativeValues")):
        fail("OSB_CAPTURE_PROJECTION_MISMATCH", "Projection differs from raw native values.")
    receipts, blockers = [], []
    for index, entry in enumerate(intent["source"]["values"]):
        name, value = entry["name"], entry["value"]
        present = name in projected
        if name in SOURCE_ANNOTATION_FIELDS:
            target = observation["sourceBinding"]["source"]["values"][index]["value"]
            path = f"/sourceBinding/source/values/{index}/value"
            disposition = "governed_extension"
        elif present:
            target = projected[name]
            path = "/nativeValues/" + name.replace("~", "~0").replace("/", "~1")
            disposition = "native" if field_matches(name, value, target) else "mismatch"
        else:
            target, path, disposition = None, None, "missing_in_read_back"
        if disposition in {"mismatch", "missing_in_read_back"}:
            blockers.append({"code": "NATIVE_CAPTURE_FIELD_NOT_PRESERVED", "sourcePath": entry["sourcePath"]})
        receipts.append({
            "sourcePath": entry["sourcePath"], "targetPath": path,
            "sourceValueHash": canonical_json_hash_ref(value, schema_version="OsbTypedSourceValueV1@1.0.0"),
            "targetValueHash": None if path is None else canonical_json_hash_ref(
                target, schema_version="OsbTypedSourceValueV1@1.0.0"),
            "disposition": disposition,
        })
    native = observation["native"]
    statuses = [native.get("status")] if family.startswith("odm_") else [
        native.get("name", {}).get("status"), native.get("attributes", {}).get("status"),
        *[term.get("name_status") for term in native.get("terms", [])],
        *[term.get("attributes_status") for term in native.get("terms", [])],
    ]
    if not statuses or any(status != "Final" for status in statuses):
        blockers.append({"code": "NATIVE_CAPTURE_LIBRARY_REVIEW_REQUIRED"})
    return receipts, blockers
