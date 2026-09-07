"""Retain explicit library references from an already study-scoped native read.

This collector does not discover study ownership, search by names, or crawl a
terminology graph. It retains full definitions and independently versioned
readings without replacing any clinical selection or its original reference.
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

Reader = Callable[[str, str | None], Any]


def native_library_readers() -> dict[str, Reader]:
    """Use full service models; instantiate services only for requested kinds."""
    services: dict[str, Any] = {}

    def read(kind, uid, version):
        if kind not in services:
            if kind == "ctCodelistAttributes":
                from clinical_mdr_api.services.controlled_terminologies.ct_codelist_attributes import CTCodelistAttributesService

                services[kind] = CTCodelistAttributesService()
            elif kind == "ctCodelistName":
                from clinical_mdr_api.services.controlled_terminologies.ct_codelist_name import CTCodelistNameService

                services[kind] = CTCodelistNameService()
            elif kind == "ctTermAttributes":
                from clinical_mdr_api.services.controlled_terminologies.ct_term_attributes import CTTermAttributesService

                services[kind] = CTTermAttributesService()
            elif kind == "ctTermName":
                from clinical_mdr_api.services.controlled_terminologies.ct_term_name import CTTermNameService

                services[kind] = CTTermNameService()
            elif kind == "ctTermMemberships":
                from clinical_mdr_api.services.controlled_terminologies.ct_term import CTTermService

                services[kind] = CTTermService()
            elif kind == "ctCodelistTerms":
                from clinical_mdr_api.services.controlled_terminologies.ct_codelist import CTCodelistService

                services[kind] = CTCodelistService()
            elif kind == "dictionaryTerm":
                from clinical_mdr_api.services.dictionaries.dictionary_term_generic_service import DictionaryTermGenericService

                services[kind] = DictionaryTermGenericService()
            elif kind == "unitDefinition":
                from clinical_mdr_api.services.concepts.unit_definitions.unit_definition import UnitDefinitionService

                services[kind] = UnitDefinitionService()
            elif kind == "timeframe":
                from clinical_mdr_api.services.syntax_instances.timeframes import TimeframeService

                services[kind] = TimeframeService()
        service = services[kind]
        if kind == "ctTermMemberships":
            return service.get_codelists_by_uid(term_uid=uid)
        if kind == "dictionaryTerm":
            return service.get_by_uid(term_uid=uid)
        if kind == "ctCodelistTerms":
            result = service.list_terms(codelist_uid=uid, page_size=0, total_count=True).model_dump()
            if result["total"] != len(result["items"]):
                raise ValueError("codelist term inventory is incomplete")
            return {"codelist_uid": uid, "result": result}
        key = "codelist_uid" if kind.startswith("ctCodelist") else "term_uid" if kind.startswith("ctTerm") else "uid"
        return service.get_by_uid(**{key: uid, "version": version})

    return {kind: (lambda uid, version, kind=kind: read(kind, uid, version)) for kind in (
        "ctCodelistAttributes", "ctCodelistName", "ctTermAttributes", "ctTermName",
        "ctTermMemberships", "ctCodelistTerms", "dictionaryTerm", "unitDefinition", "timeframe",
    )}


def collect_native_library_definitions(
    scoped_records: list[dict], *, readers: Mapping[str, Reader] | None = None
) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (definition records, exact associations, unresolved census rows).

    Names and memberships have their own version histories. An ODM term or
    codelist's version pins attributes only; their name/membership reads are
    explicitly current observations. No failed version lookup retries latest.
    """
    readers = native_library_readers() if readers is None else readers
    definitions: list[dict] = []
    associations: list[dict] = []
    census: list[dict] = []
    cache: dict[tuple[str, str, str | None], tuple[dict | None, str | None]] = {}
    retained: dict[tuple[str, str, str | None], list[dict]] = {}

    def model(value):
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        return deepcopy(value) if isinstance(value, dict) else None

    def identity(record, kind):
        key = "codelist_uid" if kind.startswith("ctCodelist") else "term_uid" if kind.startswith("ctTerm") or kind == "dictionaryTerm" else "uid"
        return record.get(key)

    def add(source, path, reference, kind, uid, version=None, embedded=None):
        invalid_reference = ("reference has no explicit UID" if not isinstance(uid, str) or not uid else
                             "reference has an invalid explicit version" if version is not None and
                             (not isinstance(version, str) or not version.strip()) else None)
        if invalid_reference:
            association = {"sourceKind": source["kind"], "sourceUid": source.get("uid"),
                           "sourcePath": path, "sourceReference": deepcopy(reference), "targetKind": kind,
                           "targetUid": uid, "requestedVersion": deepcopy(version),
                           "status": "unresolved", "reason": invalid_reference}
            associations.append(association)
            census.append({"kind": "unresolved_native_reference", "ref": f"{source['kind']}/{source.get('uid')}{path}",
                           "detail": f"{kind}: {invalid_reference}", "association": deepcopy(association)})
            return None
        key = (kind, uid, version)
        reading = "embedded_selection" if embedded is not None else "referenced_version" if version else "current_reading"
        if embedded is not None:
            record, error = model(embedded), None
        else:
            if key not in cache:
                try:
                    reader = readers.get(kind)
                    if reader is None:
                        raise LookupError("reader unavailable")
                    result = model(reader(uid, version))
                    if result is None:
                        raise LookupError("definition not found")
                    if identity(result, kind) != uid:
                        raise ValueError("returned identity differs from reference")
                    if version is not None and result.get("version") != version:
                        raise ValueError("returned version differs from reference")
                    cache[key] = (result, None)
                except Exception as error:  # Keep the source reference, disclose lookup failure.
                    cache[key] = (None, f"{type(error).__name__}: {error}")
            record, error = cache[key]
        association = {
            "sourceKind": source["kind"], "sourceUid": source.get("uid"),
            "sourcePath": path, "sourceReference": deepcopy(reference),
            "targetKind": kind, "targetUid": uid, "requestedVersion": version,
            "reading": reading, "status": "retained" if record is not None else "unresolved",
        }
        if record is None:
            association["reason"] = error
            census.append({
                "kind": "unresolved_native_reference", "ref": f"{source['kind']}/{source.get('uid')}{path}",
                "detail": f"{kind}/{uid}: {error}", "association": deepcopy(association),
            })
        else:
            association["observedVersion"] = record.get("version")
            # Differing embedded readings with one UID/version remain distinct.
            seen = retained.setdefault(key, [])
            if record not in seen:
                seen.append(record)
                definitions.append({"kind": kind, "uid": uid, "record": deepcopy(record)})
        associations.append(association)
        return record

    def ct(source, path, reference, term=False, version=None):
        uid = reference.get("term_uid") if term else reference.get("codelist_uid", reference.get("uid"))
        prefix = "ctTerm" if term else "ctCodelist"
        add(source, path, reference, prefix + "Attributes", uid, version)
        add(source, path, reference, prefix + "Name", uid)
        if term:
            add(source, path, reference, "ctTermMemberships", uid)
        else:
            add(source, path, reference, "ctCodelistTerms", uid)

    def walk_ct(source, value, path):
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk_ct(source, child, f"{path}/{index}")
        elif isinstance(value, dict):
            if isinstance(value.get("term_uid"), str):
                # Only ODM item's terms[].version is known to pin CT attributes.
                version = value.get("version") if source["kind"] == "item" and path.startswith("/terms/") else None
                ct(source, path, value, term=True, version=version)
            if isinstance(value.get("codelist_uid"), str):
                ct(source, path, value)
            for key, child in value.items():
                # UCUM is a dictionary concept, not a CT term. Source carriers
                # and arbitrary extension payloads are opaque, not join inputs.
                if key not in {"ucum", "vendor_attributes", "vendor_elements", "vendor_element_attributes", "extensions"}:
                    escaped = key.replace("~", "~0").replace("/", "~1")
                    walk_ct(source, child, f"{path}/{escaped}")

    for source in scoped_records:
        kind = source.get("kind")
        record = source.get("record")
        if not isinstance(record, dict) or kind not in {
            "item", "study", "studyArm", "studyEpoch", "studyObjective", "studyEndpoint",
            "studyCriteria", "studyActivity", "studyElement", "studyDesignCell",
            "studyCohort", "studyBranchArm", "studyActivityGroup", "studyActivitySubGroup",
            "studyActivityInstance", "studyActivityInstruction", "studyActivitySchedule",
            "studyOperationalActivitySchedule",
        }:
            continue
        walk_ct(source, record, "")
        if kind == "item":
            codelist = record.get("codelist")
            if isinstance(codelist, dict):
                ct(source, "/codelist", codelist, version=codelist.get("version"))
            for index, reference in enumerate(record.get("unit_definitions") or []):
                if not isinstance(reference, dict):
                    continue
                unit = add(source, f"/unit_definitions/{index}", reference,
                           "unitDefinition", reference.get("uid"), reference.get("version"))
                if unit:
                    # Exactly one explicit dependency level from this definition;
                    # CT memberships are retained but never recursively expanded.
                    walk_ct({"kind": "unitDefinition", "uid": reference["uid"]}, unit, "")
                    ucum = unit.get("ucum")
                    if isinstance(ucum, dict) and ucum.get("term_uid"):
                        add({"kind": "unitDefinition", "uid": reference["uid"]}, "/ucum", ucum,
                            "dictionaryTerm", ucum.get("term_uid"))
        if kind == "studyEndpoint":
            for key in ("timeframe", "latest_timeframe"):
                reference = record.get(key)
                if isinstance(reference, dict):
                    complete = all(field in reference for field in ("parameter_terms", "template", "library", "name_plain"))
                    add(source, "/" + key, reference, "timeframe", reference.get("uid"), reference.get("version"),
                        embedded=reference if complete else None)
    return definitions, associations, census
