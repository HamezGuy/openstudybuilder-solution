"""Preview the collection definitions projected by CSL's 360i adapter."""

import json
from collections import Counter
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from clinical_mdr_api.domains.odms.item import validate_odm_item_length
from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    sha256_bytes,
)
from common.exceptions import BusinessLogicException, ValidationException

Ref = Annotated[str, Field(min_length=1)]
ScopeId = Annotated[str, Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]


class ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Item(ProjectionModel):
    refKey: Ref
    name: str
    orderNumber: int
    datatype: str
    datatypeHint: str
    vendorExtensions: dict[str, str]
    prompt: str | None = None
    description: str | None = None
    length: int | None = None
    significantDigits: int | None = None
    lengthBasis: Literal["stated", "finiteOptionDomainCapacity", "literalReferenceCapacity"] | None = None
    sdsVarName: str | None = None
    mandatory: bool | None = None
    unitName: str | None = None
    codelistRef: str | None = None
    allowsMultiChoice: bool | None = None


class Group(ProjectionModel):
    refKey: Ref
    name: str
    orderNumber: int
    description: str | None = None
    repeating: bool | None = None
    items: list[Item]
    vendorExtensions: dict[str, str] | None = None


class Form(ProjectionModel):
    refKey: Ref
    name: str
    orderNumber: int
    description: str | None = None
    repeating: bool | None = None
    itemGroups: list[Group]
    vendorExtensions: dict[str, str]


class CodelistTerm(ProjectionModel):
    decode: str
    value: str
    order: int


class Codelist(ProjectionModel):
    name: Ref
    terms: list[CodelistTerm]


class UnitGovernance(ProjectionModel):
    key: Ref
    spellings: list[str]
    catalogueTerm: str | None = None
    identity: str | None = None


class Odm(ProjectionModel):
    forms: list[Form]
    codelists: list[Codelist]
    units: list[Ref]
    unitGovernance: list[UnitGovernance] | None = None
    unitGovernanceVersion: Literal["measured-unit/1", "measured-unit/2"] | None = None
    unitGovernanceKernel: Literal["governed-unit/3"] | None = None


class FieldVisitScope(ProjectionModel):
    fieldRef: Ref
    fieldLabel: str | None = None
    text: str


class VisitAssignment(ProjectionModel):
    visitRef: Ref
    formRef: Ref
    formName: str | None = None
    required: bool | None = None
    doubleEntry: bool | None = None
    hideCrf: bool | None = None
    ordinal: int | None = None
    electronicSignature: bool | None = None
    nullValues: str | None = None
    field_scope: list[FieldVisitScope] | None = Field(None, alias="_fieldVisitScope")
    derived_from: JsonValue | None = Field(None, alias="_derivedFrom")
    derived_item: JsonValue | None = Field(None, alias="_derivedFromItem")
    derived_timing: JsonValue | None = Field(None, alias="_derivedFromTiming")
    derived_alias: JsonValue | None = Field(None, alias="_derivedFromVisitAlias")
    derived_activity: str | None = Field(None, alias="_derivedFromActivityCandidateId")
    derived_visit: JsonValue | None = Field(None, alias="_derivedFromStatedVisit")
    conditional_note: str | None = Field(None, alias="_conditionalNote")
    extensions: dict[str, JsonValue] | None = None


class DraftDefinition(ProjectionModel):
    schemaVersion: Literal["osb-draft-ecrf/1"]
    platformStudyId: ScopeId
    tenantId: ScopeId
    semanticStudyId: ScopeId
    osbStudyUid: Ref
    buildHash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    odm: Odm
    formVisitMatrix: list[VisitAssignment]


class DraftPreviewRequest(ProjectionModel):
    sourceDigest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dryRun: bool
    definition: DraftDefinition


def _check_design_extensions(value):
    """The 360i carrier may serialize design structures into extension strings."""
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            value = json.loads(value)
        except ValueError:
            return
    if isinstance(value, dict):
        forbidden = {
            "clinicaldata",
            "subjectdata",
            "itemdata",
            "subjectrows",
            "patientrows",
            "patientvalues",
            "subjectrecords",
            "patientrecords",
            "patientdata",
            "populations",
            "sourcebundle",
            "sourcecustody",
        }
        if any(key.replace("_", "").casefold() in forbidden for key in value):
            raise ValidationException(msg="OSB_DRAFT_PREVIEW_COLLECTION_METADATA_REQUIRED")
        for child in value.values():
            _check_design_extensions(child)
    elif isinstance(value, list):
        for child in value:
            _check_design_extensions(child)


def _digest(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def _source_rows(definition: dict):
    odm = definition["odm"]
    units = set(odm["units"])
    lists = Counter(row["name"] for row in odm["codelists"])
    forms = Counter(row["refKey"] for row in odm["forms"])
    fields = {}
    for form in odm["forms"]:
        form_ref = form["refKey"]
        fields.setdefault(form_ref, Counter())
        yield "form", [form_ref], form, []
        for group in form["itemGroups"]:
            group_path = [form_ref, group["refKey"]]
            yield "group", group_path, group, []
            for item in group["items"]:
                fields[form_ref][item["refKey"]] += 1
                reasons = []
                try:
                    validate_odm_item_length(item["datatype"], item.get("length"), item.get("significantDigits"))
                except BusinessLogicException:
                    reasons.append("NATIVE_ODM_ITEM_LENGTH_UNRESOLVED")
                if item.get("codelistRef") and lists[item["codelistRef"]] != 1:
                    reasons.append("CODELIST_REFERENCE_UNRESOLVED")
                if item.get("unitName") and item["unitName"] not in units:
                    reasons.append("UNIT_REFERENCE_UNRESOLVED")
                yield "item", [*group_path, item["refKey"]], item, reasons
    for entry in odm["codelists"]:
        yield "codelist", [entry["name"]], entry, []
        for term in entry["terms"]:
            yield "codelistTerm", [entry["name"], term["value"]], term, []
    for unit in odm["units"]:
        yield "unit", [unit], unit, []
    for unit_group in odm.get("unitGovernance") or []:
        yield "unitGovernance", [unit_group["key"]], unit_group, []
    for assignment in definition["formVisitMatrix"]:
        form_ref = assignment["formRef"]
        reasons = []
        if forms[form_ref] != 1:
            reasons.append("FORM_REFERENCE_UNRESOLVED")
        if any(fields.get(form_ref, {}).get(row["fieldRef"], 0) != 1 for row in assignment.get("_fieldVisitScope") or []):
            reasons.append("FIELD_VISIT_REFERENCE_UNRESOLVED")
        yield "visitFormAssignment", [assignment["visitRef"], form_ref], assignment, reasons


def preview_draft(study_uid: str, request: DraftPreviewRequest, binding_status: str):
    if request.definition.osbStudyUid != study_uid:
        raise ValidationException(msg="OSB_DRAFT_PREVIEW_STUDY_MISMATCH")
    definition = request.definition.model_dump(by_alias=True, exclude_unset=True)
    _check_design_extensions(definition)
    rows = list(_source_rows(definition))
    occurrences = Counter((kind, tuple(path)) for kind, path, _, _ in rows)
    counts = Counter(kind for kind, _, _, _ in rows)
    objects, holds = [], []
    if binding_status != "verified":
        holds.append({"kind": "study", "ref": study_uid, "reason": binding_status})
    holds.append({"kind": "definition", "ref": study_uid, "reason": "SIGNED_ECRF_SOURCE_STAGE_REQUIRED"})
    for kind, path, content, reasons in rows:
        ref = canonical_json(path)
        if occurrences[(kind, tuple(path))] != 1:
            reasons.append("SOURCE_REFERENCE_AMBIGUOUS")
        holds.extend({"kind": kind, "ref": ref, "reason": reason} for reason in reasons)
        objects.append({"kind": kind, "ref": ref, "digest": _digest(content), "oid": None, "uid": None, "nativeStatus": "held", "reasons": [*reasons, "NATIVE_CREATION_UNVERIFIED"]})
    return {
        "schemaVersion": "osb-draft-ecrf-result/1",
        "dryRun": request.dryRun,
        "status": "held",
        "studyUid": study_uid,
        "libraryName": None,
        "sourceDigest": request.sourceDigest,
        "buildHash": definition["buildHash"],
        "definitionDigest": _digest(definition),
        "membershipDigest": _digest(objects),
        "bindingStatus": binding_status,
        "counts": {
            "forms": counts["form"],
            "groups": counts["group"],
            "items": counts["item"],
            "visitFormAssignments": counts["visitFormAssignment"],
            "codelists": counts["codelist"],
            "codelistTerms": counts["codelistTerm"],
            "units": counts["unit"],
            "unitGovernance": counts["unitGovernance"],
            "created": 0,
            "reused": 0,
            "held": len(objects),
        },
        "objects": objects,
        "holds": holds,
    }
