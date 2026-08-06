"""Pure mapping from an `Osb360iPayloadV1` (the 360i pipeline's OSB-shaped
projection, read from ecrf_platform.osb_study_payloads) to OSB API request
bodies. No network, no database — unit-testable against a fixture payload.

Doctrine carried over from the payload's own contract:
  * epochs come ONLY from the payload's first-class `epochs` (the protocol's
    stated SoA bands). When it is empty, ONE carrier epoch is emitted because
    OSB structurally requires `study_epoch_uid` on every visit — and the
    carrier is declared `importer_scaffolding` in the census, never passed
    off as protocol content.
  * every EXTEND-disposition property rides as an `x360i` vendor attribute —
    the no-data-loss carrier into Neo4j and the OSB->EDC exporter's
    restoration source.
  * unknown vocabulary STOPs with a census entry; nothing is coerced.
"""

import hashlib
import json
import re

X360I_NAMESPACE = {
    "name": "x360i",
    "prefix": "x360i",
    "url": "https://360i.example.org/odm/v1",
}

# The x360i vendor ATTRIBUTES this importer declares once per instance.
# `compatible_types` uses OSB's own vocabulary for where an attribute may sit.
X360I_ATTRIBUTES = [
    {"name": "refKey", "compatible_types": ["FormDef", "ItemGroupDef", "ItemDef", "StudyEventDef"], "data_type": "string"},
    {"name": "fieldType", "compatible_types": ["ItemDef"], "data_type": "string"},
    {"name": "visitRefKey", "compatible_types": ["StudyEventDef"], "data_type": "string"},
    {"name": "studyId", "compatible_types": ["StudyEventDef", "FormDef"], "data_type": "string"},
    {"name": "buildHash", "compatible_types": ["StudyEventDef", "FormDef"], "data_type": "string"},
    {"name": "ext", "compatible_types": ["FormDef", "ItemGroupDef", "ItemDef", "StudyEventDef"], "data_type": "string"},
]

# Payload visit `type` -> (OSB VisitType term name, visit_class). The
# payload's vocabulary is closed ('scheduled' | 'unscheduled'); anything else
# STOPs. OSB's VisitType codelist ships both names in the standard seed.
VISIT_TYPE_MAP = {
    "scheduled": {"visit_type_name": "Visit", "visit_class": "MANUALLY_DEFINED_VISIT"},
    "unscheduled": {
        "visit_type_name": "Unscheduled",
        "visit_class": "UNSCHEDULED_VISIT",
    },
}

CARRIER_EPOCH_NAME = "Study Period"


def stable_suffix(value, length=8):
    """Deterministic short hash for minted names (same value -> same name)."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:length]


def project_number_for(payload):
    """Deterministic OSB project_number for the payload's 360i project.

    `360I-<hash8 of the 360i project id>`; a payload with no project (a study
    outside any workspace) gets a study-scoped fallback so it still imports.
    """
    source = payload.get("source", {})
    key = source.get("projectId") or f"study:{source.get('studyId', 'unknown')}"
    return f"360I-{stable_suffix(key)}"


def study_number_for(payload):
    """OSB study_number: digits only, unique per 360i study.

    OSB validates study_number as a short numeric string; our study ids are
    uuids. Deterministic: first 4 bytes of the sha256, decimal, so the same
    study always lands on the same number (collision handling is the
    importer's, which can see OSB's existing numbers).
    """
    study_id = payload.get("source", {}).get("studyId", "")
    digest = hashlib.sha256(study_id.encode("utf-8")).digest()
    number = int.from_bytes(digest[:4], "big") % 9000 + 1000
    return str(number)


def study_acronym_for(payload):
    study = payload.get("study", {})
    acronym = (study.get("acronym") or "").strip()
    if acronym:
        return acronym[:20]
    name = (study.get("name") or "STUDY").strip()
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", name)[:12].upper()
    return cleaned or "STUDY"


def epochs_plan(payload):
    """The epochs to create, in order, plus whether one is scaffolding.

    Every epoch maps to OSB's generic 'Treatment' / 'Screening' subtypes when
    the stated name matches one, else the neutral carrier subtype — the
    importer resolves subtype names against OSB's Epoch Sub Type codelist and
    STOPs on a miss (never invents CT).
    """
    stated = payload.get("epochs", [])
    if stated:
        return (
            [
                {
                    "name": e["name"],
                    "order": e["ordinal"],
                    "visit_refs": list(e.get("visitRefs", [])),
                    "scaffolding": False,
                }
                for e in stated
            ],
            False,
        )
    # OSB requires an epoch on every visit; the protocol stated none. One
    # carrier, declared as importer scaffolding in the census.
    return (
        [
            {
                "name": CARRIER_EPOCH_NAME,
                "order": 1,
                "visit_refs": [v["refKey"] for v in payload.get("visits", [])],
                "scaffolding": True,
            }
        ],
        True,
    )


def epoch_subtype_candidates(epoch_name):
    """Names to try against OSB's Epoch Sub Type codelist, most specific
    first — mirrors mockdatajson's suffix retries, extended with the stated
    band's own words stripped of 'Period/Phase' noise.
    """
    name = epoch_name.strip()
    base = re.sub(r"\s*(period|phase)\s*$", "", name, flags=re.IGNORECASE).strip()
    candidates = [name, f"{name} Epoch", base, f"{base} Epoch"]
    # The neutral fallbacks OSB seeds: keep last so a stated match wins.
    candidates += ["Treatment", "Observation"]
    seen, unique = set(), []
    for c in candidates:
        key = c.lower()
        if c and key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def visit_plan(payload, epoch_uid_by_ref):
    """StudyVisit create-bodies in anchor-first order.

    * The first visit with scheduleDay == 0, else the first visit, becomes the
      global anchor (OSB requires exactly one time-reference root).
    * Days ride verbatim; a visit with no derived day gets time_value 0 on the
      anchor reference and a census note (the payload already warned).
    * Names are the protocol's stated names: visit_class MANUALLY_DEFINED_VISIT
      with explicit visit_name/short/number — never OSB's derived "Visit N".
    """
    visits = list(payload.get("visits", []))
    if not visits:
        return []

    anchor_idx = next(
        (i for i, v in enumerate(visits) if v.get("scheduleDay") == 0), 0
    )

    plans = []
    for i, v in enumerate(visits):
        type_key = (v.get("type") or "scheduled").strip().lower()
        mapping = VISIT_TYPE_MAP.get(type_key)
        if mapping is None:
            plans.append(
                {
                    "refKey": v["refKey"],
                    "stop": f"visit type '{type_key}' is outside the payload contract",
                }
            )
            continue
        day = v.get("scheduleDay")
        is_anchor = i == anchor_idx and mapping["visit_class"] == "MANUALLY_DEFINED_VISIT"
        # Windows relative to the scheduled day (OSB's convention): a payload
        # visit carries absolute minDay/maxDay; OSB wants offsets like -2/+2.
        min_window = 0
        max_window = 0
        if day is not None and v.get("minDay") is not None:
            min_window = int(v["minDay"]) - int(day)
        if day is not None and v.get("maxDay") is not None:
            max_window = int(v["maxDay"]) - int(day)
        body = {
            "visit_class": mapping["visit_class"],
            "visit_type_name": mapping["visit_type_name"],
            "epoch_ref": v.get("epochRef"),
            "refKey": v["refKey"],
            "is_global_anchor_visit": is_anchor,
            "time_value": int(day) if day is not None else 0,
            "day_missing": day is None,
            "visit_name": v["name"],
            "visit_short_name": v["refKey"][:20],
            "visit_number": v["ordinal"],
            "unique_visit_number": v["ordinal"] * 100,
            "description": v.get("description"),
            "show_visit": True,
            "min_window": min_window,
            "max_window": max_window,
        }
        plans.append(body)
    return plans


def arms_plan(payload):
    """StudyArm create-bodies — genuine arms only (the payload already
    separated non-arm group classes, which ride the census as carried)."""
    plans = []
    for i, arm in enumerate(payload.get("arms", []), start=1):
        name = arm["name"].strip()
        plans.append(
            {
                "name": name[:200],
                "short_name": name[:20],
                "order": i,
                "description": arm.get("description"),
            }
        )
    return plans


def codelists_plan(payload):
    """Sponsor codelist create-plans from the payload's deduplicated
    codelists. All our terms are sponsor terms (no C-codes extracted yet)."""
    plans = []
    for cl in payload.get("odm", {}).get("codelists", []):
        plans.append(
            {
                "name": cl["name"],
                "terms": [
                    {
                        "name": t["decode"],
                        "submission_value": str(t["value"])[:200],
                        "order": t.get("order"),
                    }
                    for t in cl.get("terms", [])
                ],
            }
        )
    return plans


def units_plan(payload):
    """Distinct unit display names the items reference — created as OSB
    unit definitions when absent (matched case-insensitively by name)."""
    return list(payload.get("odm", {}).get("units", []))


def odm_item_body(item, codelist_uid_by_name, unit_uid_by_name):
    """POST /odms/items body for one payload item."""
    unit_defs = []
    unit_name = item.get("unitName")
    if unit_name and unit_name.lower() in unit_uid_by_name:
        unit_defs.append({"uid": unit_uid_by_name[unit_name.lower()], "mandatory": False})
    codelist = None
    terms = []
    cl_ref = item.get("codelistRef")
    if cl_ref and cl_ref in codelist_uid_by_name:
        codelist = {
            "uid": codelist_uid_by_name[cl_ref]["codelist_uid"],
            "allows_multi_choice": bool(item.get("allowsMultiChoice")),
        }
        terms = [
            {
                "uid": t["term_uid"],
                "mandatory": False,
                "order": t.get("order"),
                "display_text": t.get("name"),
            }
            for t in codelist_uid_by_name[cl_ref]["terms"]
        ]
    return {
        "name": item["name"][:200],
        "oid": item["refKey"],
        "datatype": item["datatype"],
        "prompt": item.get("prompt") or item["name"],
        "length": item.get("length"),
        "significant_digits": None,
        "sas_field_name": None,
        "sds_var_name": item.get("sdsVarName"),
        "origin": None,
        "comment": None,
        "allows_multi_choice": bool(item.get("allowsMultiChoice")),
        "descriptions": [],
        "aliases": [],
        "codelist": codelist,
        "unit_definitions": unit_defs,
        "terms": terms,
    }


def vendor_ext_value(entity):
    """One JSON blob per entity carrying every vendorExtensions key — stamped
    as the single `x360i:ext` attribute (attribute-per-key would need one OSB
    vendor-attribute concept per distinct key, which is churn without gain;
    the blob is machine-readable either way and the census names its keys)."""
    ext = entity.get("vendorExtensions", {})
    return json.dumps(ext, sort_keys=True) if ext else None
