"""Source-bound mapping diagnostics and honest, incomplete USDM drafts.

A draft may omit a required USDM property whose value is unknown. It must never
pass off a fabricated clinical value as a conformant document. Strict callers
receive an actionable error; preview callers receive the same source and issues.
"""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import inspect
from typing import Any, Callable, Literal, NotRequired, TypedDict
from urllib.parse import quote

from common.exceptions import ValidationException
from pydantic import BaseModel, ValidationError
from usdm_model.extension import ExtensionAttribute, BaseQuantity


class USDMMappingAuthorityRequired(ValidationException):
    status_code = 422


class StudyCellCanonicalScope(TypedDict):
    studyCellId: str
    armId: str
    epochId: str
    elementId: str
    parentArmId: NotRequired[str]


class CellTransitionExecutionReview(TypedDict):
    kind: Literal["cell-transition"]
    state: Literal["requires-review"]
    sourceScope: dict[str, str | None]
    canonicalScope: StudyCellCanonicalScope | dict[str, str]
    draftTargets: dict[str, str]
    documentPointers: dict[str, str]
    requiredBindings: list[str]


class NativeExecutionReview(TypedDict):
    kind: Literal["study-stop", "epoch-start", "epoch-end", "property-acquisition"]
    state: Literal["requires-review"]
    sourceScope: dict[str, Any]
    canonicalScope: dict[str, str]
    draftTargets: dict[str, str]
    documentPointers: dict[str, str]
    requiredBindings: list[str]


ExecutionReview = CellTransitionExecutionReview | NativeExecutionReview


class MappingIssue(TypedDict):
    code: str
    sourcePath: str
    targetPath: str
    message: str
    resolution: str
    executionReview: NotRequired[ExecutionReview]


def native_json(value: Any) -> Any:
    """Use native API serialization, preserving null, false, empty and order."""
    if callable(getattr(value, "model_dump", None)):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return native_json(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: native_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [native_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "__dict__"):
        return native_json(vars(value))
    raise TypeError(f"Unsupported native source value: {type(value).__name__}")


def accepts_keyword(callback: Callable, name: str) -> bool:
    parameters = inspect.signature(callback).parameters
    return name in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


class MappingContext:
    def __init__(self, *, allow_incomplete: bool = False):
        self.allow_incomplete = allow_incomplete
        self.issues: list[MappingIssue] = []
        self.native_records: list[dict[str, Any]] = []
        self._records: dict[tuple[str, str, str | None], Any] = {}
        # Populated only while resolving actual native cells to this document.
        # Retained source extensions are never read back as relationship authority.
        self.cell_scopes: dict[str, StudyCellCanonicalScope] = {}

    def unresolved(
        self,
        code: str,
        source_path: str,
        target_path: str,
        message: str,
        resolution: str,
        *,
        execution_review: ExecutionReview | None = None,
    ) -> None:
        issue: MappingIssue = {
            "code": code,
            "sourcePath": source_path,
            "targetPath": target_path,
            "message": message,
            "resolution": resolution,
        }
        if execution_review is not None:
            issue["executionReview"] = deepcopy(execution_review)
        if issue not in self.issues:
            self.issues.append(issue)
        if not self.allow_incomplete:
            raise USDMMappingAuthorityRequired(
                f"{code}: {source_path}; {message} {resolution}"
            )

    def build(self, model_class: type[BaseModel], source_path: str, **values: Any):
        """Only missing required values may enter a preview as missing fields."""
        # The USDM package sets this discriminator in __init__, which
        # model_construct intentionally bypasses for incomplete drafts.
        if "instanceType" in model_class.model_fields:
            values.setdefault("instanceType", model_class.__name__)
        values = {
            key: value
            for key, value in values.items()
            if value is not None
            or key not in model_class.model_fields
            or not model_class.model_fields[key].is_required()
        }
        try:
            return model_class(**values)
        except ValidationError as error:
            if any(item["type"] != "missing" for item in error.errors()):
                raise
            for item in error.errors():
                field = "/".join(str(part) for part in item["loc"])
                self.unresolved(
                    "USDM_REQUIRED_SOURCE_VALUE_MISSING",
                    source_path,
                    f"{model_class.__name__}/{field}",
                    f"No authoritative native value is available for {field}.",
                    "Supply the corresponding governed native value before validation and release.",
                )
            return model_class.model_construct(**values)

    def retain(
        self, kind: str, uid: str, value: Any, *, scope: dict[str, Any] | None = None,
        reading_identity: str | None = None,
    ) -> None:
        if not isinstance(uid, str) or not uid:
            raise USDMMappingAuthorityRequired(f"USDM_SOURCE_IDENTITY_REQUIRED: {kind}")
        if reading_identity is not None and (
            kind != "studyOperationalActivitySchedule"
            or not isinstance(reading_identity, str) or not reading_identity.strip()
        ):
            raise USDMMappingAuthorityRequired(f"USDM_SOURCE_READING_IDENTITY_INVALID: {kind}")
        record = native_json(value)
        # The native operational query expands one schedule UID into its
        # selected activity-instance rows. Preserve that exact qualifier while
        # keeping the native UID and complete row unchanged in the report.
        key = (kind, uid, reading_identity)
        if key in self._records:
            if self._records[key] != record:
                raise USDMMappingAuthorityRequired(
                    f"USDM_SOURCE_SNAPSHOT_CONFLICT: {kind}/{uid}; "
                    "Two different native readings cannot authorize one mapping."
                )
            return
        self._records[key] = deepcopy(record)
        entry = {"kind": kind, "uid": uid, "record": record}
        if scope is not None:
            entry["scope"] = deepcopy(scope)
        self.native_records.append(entry)


def source_extension(id_manager, kind: str, uid: str, value: Any) -> ExtensionAttribute:
    """Expose native metadata as navigable, typed extension values, not JSON text.

    The shape child distinguishes null, empty object and empty array. Array child
    URLs are exact indexes; object child URLs contain escaped native field names.
    These extensions preserve source meaning without granting execution authority.
    """
    root = f"https://openstudybuilder.org/usdm/extensions/native/{quote(kind, safe='')}"

    def project(item: Any, path: str) -> ExtensionAttribute:
        identifier = id_manager.get_id("ExtensionAttribute", f"{kind}:{uid}:{path}")
        values: dict[str, Any] = {
            "id": identifier,
            "url": root + path,
            "instanceType": "ExtensionAttribute",
        }
        if item is None or isinstance(item, (dict, list)):
            shape = "null" if item is None else "object" if isinstance(item, dict) else "array"
            children = [
                ExtensionAttribute(
                    id=id_manager.get_id("ExtensionAttribute", f"{kind}:{uid}:{path}:shape"),
                    url="https://openstudybuilder.org/usdm/extensions/native-value-shape",
                    valueString=shape,
                    instanceType="ExtensionAttribute",
                )
            ]
            pairs = item.items() if isinstance(item, dict) else enumerate(item or [])
            children.extend(
                project(child, path + "/" + quote(str(key), safe=""))
                for key, child in pairs
            )
            values["extensionAttributes"] = children
        elif isinstance(item, bool):
            values["valueBoolean"] = item
        elif isinstance(item, int):
            values["valueInteger"] = item
        elif isinstance(item, float):
            values["valueQuantity"] = BaseQuantity(
                id=id_manager.get_id("Quantity", f"native:{kind}:{uid}:{path}"),
                value=item,
                instanceType="Quantity",
            )
        else:
            values["valueString"] = item
        return ExtensionAttribute(**values)

    return project(native_json(value), "")


def finalize_document(document: dict[str, Any], context: MappingContext) -> dict[str, Any]:
    """Remove unresolved code placeholders and give owned values unique IDs.

    USDM code/quantity/extension objects are embedded values, not shared entities.
    Repeated occurrences require separate IDs (DDF00083). Never repair a repeated
    identity for referenced clinical entities or silently choose one definition.
    """
    inline_types = {"Code", "AliasCode", "Quantity", "Range", "Duration", "ExtensionAttribute"}
    seen: set[str] = set()
    occurrences: dict[str, int] = {}

    def visit(value, path):
        if isinstance(value, list):
            groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    groups.setdefault((item.get("instanceType", ""), item["name"]), []).append(item)
            for items in groups.values():
                if len(items) > 1:
                    for item in items:
                        if item.get("label") is None:
                            item["label"] = item["name"]
                        item["name"] = f'{item["name"]} [{item["id"]}]'
            for index, item in enumerate(value):
                visit(item, f"{path}/{index}")
        elif isinstance(value, dict):
            kind, identifier = value.get("instanceType"), value.get("id")
            if isinstance(identifier, str) and isinstance(kind, str):
                if identifier in seen:
                    if kind not in inline_types:
                        context.unresolved(
                            "USDM_DUPLICATE_ENTITY_ID", path, path + "/id",
                            "Distinct clinical entity occurrences have the same identifier.",
                            "Resolve the native selection identity; no occurrence was discarded.",
                        )
                    else:
                        count = occurrences.get(identifier, 1) + 1
                        candidate = f"{identifier}_occurrence_{count}"
                        while candidate in seen:
                            count += 1
                            candidate = f"{identifier}_occurrence_{count}"
                        occurrences[identifier] = count
                        value["id"] = candidate
                        identifier = candidate
                seen.add(identifier)
            if kind == "Code":
                for field in ("code", "codeSystem", "codeSystemVersion", "decode"):
                    if value.get(field) == "":
                        context.unresolved(
                            "USDM_CODE_AUTHORITY_REQUIRED", path, path + "/" + field,
                            f"No authoritative {field} is available for this native term.",
                            "Resolve the exact term in the selected terminology or dictionary version.",
                        )
                        del value[field]
            for field, item in tuple(value.items()):
                visit(item, path + "/" + field)

    visit(document, "")
    return document
