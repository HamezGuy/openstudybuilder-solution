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

# OSB validates a vendor-namespace `prefix` as letters-only, so the prefix
# cannot literally be "x360i" (contains a digit). The prefix is an opaque
# identifier — Leg C's exporter matches x360i attributes by their NAME
# (refKey/fieldType/ext/…), never by prefix — so a letters-only prefix keeps
# the no-data-loss round-trip intact. The human-facing namespace NAME stays
# "x360i".
X360I_NAMESPACE = {
    "name": "x360i",
    "prefix": "xthreesixtyi",
    "url": "https://360i.example.org/odm/v1",
}

# The x360i vendor ATTRIBUTES this importer declares once per instance.
# `compatible_types` uses OSB's own vocabulary for where an attribute may sit.
# OSB's OdmVendorAttribute compatible_types enum is
# {FormDef, ItemGroupDef, ItemDef, ItemGroupRef, ItemRef} — it has NO
# StudyEventDef, so study-events cannot carry vendor attributes on this
# instance. That is by design here: a study-event carries its 360i join in
# its OID (SE.360I.<studyId>.<visitRef>), which Leg C parses directly — so no
# StudyEventDef-scoped attribute is needed. (Hence visitRefKey is dropped.)
X360I_ATTRIBUTES = [
    {"name": "refKey", "compatible_types": ["FormDef", "ItemGroupDef", "ItemDef"], "data_type": "string"},
    {"name": "fieldType", "compatible_types": ["ItemDef"], "data_type": "string"},
    {"name": "studyId", "compatible_types": ["FormDef"], "data_type": "string"},
    {"name": "buildHash", "compatible_types": ["FormDef"], "data_type": "string"},
    {"name": "ext", "compatible_types": ["FormDef", "ItemGroupDef", "ItemDef"], "data_type": "string"},
    # Content sha of the last-imported concept body — the upsert's
    # content-compare reads it back from OSB state so an unchanged concept is
    # not version-churned on re-import (no dependency on the payload hash).
    {"name": "content", "compatible_types": ["FormDef", "ItemGroupDef", "ItemDef"], "data_type": "string"},
]

# Payload visit `type` -> (candidate OSB VisitType term names, visit_class).
# The payload's vocabulary is closed ('scheduled' | 'unscheduled'); anything
# else STOPs. The seeded CDISC VisitType codelist has NO generic "Visit" term
# — it ships specific types (Treatment, Screening, Follow-up, Unscheduled, …).
# So `scheduled` resolves against a candidate list (importer tries each in
# order, STOPs only if none exist); "Treatment" is the standard generic
# scheduled on-study visit type. `unscheduled` matches the seeded "Unscheduled"
# term exactly.
VISIT_TYPE_MAP = {
    "scheduled": {
        "visit_type_names": ["Treatment", "Visit", "Screening"],
        "visit_class": "MANUALLY_DEFINED_VISIT",
    },
    "unscheduled": {
        "visit_type_names": ["Unscheduled"],
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

    # OSB timing model: the global anchor visit sits at day 0 and every other
    # visit's time_value is measured RELATIVE to it. Choosing the anchor and a
    # rebasing origin correctly is what keeps OSB's two hard constraints —
    # (1) the anchor is at day 0, (2) visit_number increases with time — both
    # satisfiable at once.
    #
    #   * A visit explicitly at scheduleDay 0 is the natural anchor, origin 0.
    #   * Otherwise the chronologically EARLIEST dated visit is the anchor and
    #     its day becomes the origin, so it rebases to 0 and all later visits
    #     get positive offsets (no negatives before the anchor — which is what
    #     produced the "not in chronological order" rejection).
    dated = [(int(v["scheduleDay"]), i) for i, v in enumerate(visits)
             if v.get("scheduleDay") is not None]
    if any(d == 0 for d, _ in dated):
        anchor_idx = next(i for i, v in enumerate(visits) if v.get("scheduleDay") == 0)
        origin = 0
    elif dated:
        origin, anchor_idx = min(dated)  # earliest day -> anchor + rebase origin
    else:
        anchor_idx, origin = 0, 0  # no dated visits at all

    def _effective_tv(idx, v):
        if idx == anchor_idx:
            return 0
        d = v.get("scheduleDay")
        return int(d) - origin if d is not None else None

    # Number visits by effective time (anchor first at 0), day-missing last,
    # ties broken by original order — OSB requires monotonic visit_number.
    def _sort_key(idx):
        tv = _effective_tv(idx, visits[idx])
        return (tv if tv is not None else 10**9, idx)

    chrono_order = sorted(range(len(visits)), key=_sort_key)
    chrono_rank = {idx: rank + 1 for rank, idx in enumerate(chrono_order)}

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
        # time_value is rebased relative to the anchor's day (anchor -> 0).
        tv = _effective_tv(i, v)
        time_value = tv if tv is not None else 0
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
            # Candidate VisitType term names, tried in order by the importer
            # against the instance's seeded CT (first that exists wins).
            "visit_type_names": list(mapping["visit_type_names"]),
            # Back-compat single value = the first candidate (tests/readers).
            "visit_type_name": mapping["visit_type_names"][0],
            "epoch_ref": v.get("epochRef"),
            "refKey": v["refKey"],
            "is_global_anchor_visit": is_anchor,
            "time_value": time_value,
            "day_missing": day is None,
            "visit_name": v["name"],
            "visit_short_name": v["refKey"][:20],
            # Chronological rank (by effective day), NOT the payload ordinal —
            # OSB requires visit_number to increase with visit timing.
            "visit_number": chrono_rank[i],
            "unique_visit_number": chrono_rank[i] * 100,
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
    # OSB requires a non-null `length` for text/string datatypes. Honor a
    # stated length; otherwise default free-text fields to 200 (OSB's own
    # convention for un-sized text items) so the item validates instead of
    # being censused as a failed create.
    length = item.get("length")
    datatype = item["datatype"]
    if length is None and str(datatype).lower() in ("text", "string"):
        length = 200
    return {
        "name": item["name"][:200],
        "oid": item["refKey"],
        "datatype": datatype,
        "prompt": item.get("prompt") or item["name"],
        "length": length,
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


# ----------------------------------------------------------------------------
# Upsert diff — pure classification of desired-vs-current into an action plan.
#
# The importer's first pass (create-only) treats an OID/refKey that already
# exists as `unchanged`, which is honest for a byte-identical payload (the
# run() hash gate already skips those) but WRONG for an *edited* payload: a
# changed visit window or a removed form must be reconciled, not ignored.
#
# These functions are pure: given the payload's desired plans and a snapshot
# of what OSB currently holds (each keyed by the refKey the importer stamped
# on first import, read back from the ledger uid_map / OSB state), they return
# an explicit action list — create | patch | delete | unchanged — with the
# uid to act on and the fields that differ. The importer executes the list;
# it never re-derives the decision. A content-compare (only the fields the
# payload actually controls) yields `unchanged` so an untouched selection is
# never re-PATCHed or version-churned.
# ----------------------------------------------------------------------------

# Per selection kind, the payload-controlled fields whose values decide whether
# a PATCH is needed. Anything OSB derives (uids, order recomputed by preview,
# audit metadata) is deliberately excluded so it can't force spurious churn.
VISIT_COMPARE_FIELDS = (
    "visit_name",
    "visit_short_name",
    "visit_number",
    "unique_visit_number",
    "time_value",
    "min_window",
    "max_window",
    "visit_class",
    "visit_type_name",
    "is_global_anchor_visit",
    "description",
)
ARM_COMPARE_FIELDS = ("name", "short_name", "description")
EPOCH_COMPARE_FIELDS = ("name",)


def _selection_diff(desired_plans, current_by_ref, key_field, compare_fields):
    """Classify desired plans against current OSB state, keyed by refKey.

    `desired_plans`   list of create-body dicts (each carrying `key_field`).
    `current_by_ref`  {refKey: {"uid": ..., <compare_field>: <current value>}}
                      — the importer builds this from OSB state + the uid_map.
    Returns dict with four lists: create / patch / unchanged / delete.

    A plan whose ref is absent from current -> create.
    A plan whose ref is present and whose compared fields all match -> unchanged.
    A plan whose ref is present but some compared field differs -> patch
      (the entry names the changed fields, for the census and the PATCH body).
    A current ref that no desired plan mentions -> delete.
    Plans carrying a `stop` are passed through untouched (the importer stops them).
    """
    result = {"create": [], "patch": [], "unchanged": [], "delete": [], "stop": []}
    desired_refs = set()
    for plan in desired_plans:
        if plan.get("stop"):
            result["stop"].append(plan)
            continue
        ref = plan[key_field]
        desired_refs.add(ref)
        current = current_by_ref.get(ref)
        if current is None:
            result["create"].append(plan)
            continue
        changed = [
            f
            for f in compare_fields
            if _norm(plan.get(f)) != _norm(current.get(f))
        ]
        if changed:
            result["patch"].append(
                {"uid": current["uid"], "ref": ref, "plan": plan, "changed": changed}
            )
        else:
            result["unchanged"].append({"uid": current["uid"], "ref": ref})
    for ref, current in current_by_ref.items():
        if ref not in desired_refs:
            result["delete"].append({"uid": current["uid"], "ref": ref})
    return result


def _norm(value):
    """Compare-normalize: treat None/"" alike and numeric representations of
    the same number alike (1 vs 1.0 vs "1") so a round-trip through OSB — which
    stores visit numbers as floats and may echo "0" for 0 — doesn't look
    changed."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # 1 and 1.0 are the same number; keep a fraction only when real.
        return str(int(value)) if float(value) == int(value) else str(value)
    text = str(value).strip()
    if not text:
        return None
    # "1.0" from one side vs 1 from the other: normalize numeric strings too.
    try:
        num = float(text)
    except ValueError:
        return text
    return str(int(num)) if num == int(num) else str(num)


def visit_diff(payload, current_by_ref, epoch_uid_by_ref=None):
    """Action plan for study-visits (create/patch/delete/unchanged)."""
    return _selection_diff(
        visit_plan(payload, epoch_uid_by_ref or {}),
        current_by_ref,
        key_field="refKey",
        compare_fields=VISIT_COMPARE_FIELDS,
    )


def arm_diff(payload, current_by_ref):
    """Action plan for study-arms, keyed by arm name (the arm's stable ref)."""
    plans = arms_plan(payload)
    for p in plans:
        p.setdefault("refKey", p["name"])
    return _selection_diff(
        plans, current_by_ref, key_field="refKey", compare_fields=ARM_COMPARE_FIELDS
    )


def epoch_diff(payload, current_by_ref):
    """Action plan for study-epochs, keyed by epoch name.

    Epochs are never auto-deleted by the diff: an epoch may carry visits the
    payload still needs, and the carrier epoch is importer scaffolding, not
    payload content — so a `delete` here is advisory (the importer censuses it
    as carried rather than removing it). create/patch/unchanged behave normally.
    """
    plans, _scaffolded = epochs_plan(payload)
    for p in plans:
        p.setdefault("refKey", p["name"])
    return _selection_diff(
        plans, current_by_ref, key_field="refKey", compare_fields=EPOCH_COMPARE_FIELDS
    )


def odm_concept_diff(desired_by_ref, current_by_ref):
    """Action plan for ODM library concepts (items/item-groups/forms), keyed by
    OID (= refKey). ODM concepts are versioned in OSB, so the importer walks a
    new-version -> PATCH -> approve cycle for each `patch` entry; `content` is
    the sha of the payload body so an unchanged concept is skipped (no churn).

    `desired_by_ref` {refKey: {"content": <stable-sha of the create body>, ...}}
    `current_by_ref` {refKey: {"uid": ..., "content": <sha last imported>}}
    A missing current content (older ledger without content shas) forces a
    conservative `patch` so correctness never depends on the absent hash.
    """
    result = {"create": [], "patch": [], "unchanged": [], "delete": []}
    for ref, desired in desired_by_ref.items():
        current = current_by_ref.get(ref)
        if current is None:
            result["create"].append({"ref": ref, "desired": desired})
        elif current.get("content") is None or current["content"] != desired["content"]:
            result["patch"].append(
                {"uid": current["uid"], "ref": ref, "desired": desired}
            )
        else:
            result["unchanged"].append({"uid": current["uid"], "ref": ref})
    for ref, current in current_by_ref.items():
        if ref not in desired_by_ref:
            result["delete"].append({"uid": current["uid"], "ref": ref})
    return result


def item_group_ownership(odm):
    """Assign every ODM item to exactly ONE item-group.

    OSB enforces one-item-one-group: an OdmItem may be connected to a single
    OdmItemGroup, and the item-ref batch POST is atomic — a single already-claimed
    item rejects the whole array (verified live against OSB). A 360i payload can
    legitimately list the SAME item in several groups: a cross-domain catch-all
    group (e.g. a Medical History "MH_OTHER" section) re-lists AE/CM/LB/VS fields
    that also live in their canonical domain forms. Wiring such a group naively
    makes OSB reject its entire batch, silently dropping even the items that were
    UNIQUE to that group (the group ends up empty).

    This picks each item's canonical owner deterministically and returns the
    plan the importer executes:

      owner   {itemRef: groupRef}         -- the ONE group each item wires into
      wired   {groupRef: [itemRef, ...]}  -- items to POST per group, payload order
      carried [{"item": itemRef,          -- duplicate (item, group) references the
                "group": groupRef,          importer must NOT re-post; censused so the
                "owner": ownerGroupRef}]    no-data-loss guarantee stays honest

    Ownership rule (deterministic, favours domain forms over catch-alls): among
    the groups that list an item, prefer the one with the FEWEST shared items
    (a catch-all is mostly re-listed items, a domain group mostly unique), then
    the SMALLEST group (a focused domain group beats a large catch-all when the
    shared counts tie), then the lowest group orderNumber, then the group refKey
    — so a shared field like AETERM is owned by Adverse Events, not the Medical
    History catch-all.
    """
    members = {}
    for form in odm.get("forms", []):
        for group in form.get("itemGroups", []):
            for item in group.get("items", []):
                members.setdefault(item["refKey"], []).append(group["refKey"])

    group_rank = {}
    for form in odm.get("forms", []):
        for group in form.get("itemGroups", []):
            items = group.get("items", [])
            shared = sum(
                1 for item in items if len(members.get(item["refKey"], [])) > 1
            )
            group_rank[group["refKey"]] = (
                shared,
                len(items),
                group.get("orderNumber", 0),
            )

    owner = {}
    for ref, candidate_groups in members.items():
        owner[ref] = min(
            candidate_groups,
            key=lambda g: (group_rank[g][0], group_rank[g][1], group_rank[g][2], g),
        )

    wired = {}
    carried = []
    for form in odm.get("forms", []):
        for group in form.get("itemGroups", []):
            gref = group["refKey"]
            for item in group.get("items", []):
                iref = item["refKey"]
                if owner[iref] == gref:
                    wired.setdefault(gref, []).append(iref)
                else:
                    carried.append(
                        {"item": iref, "group": gref, "owner": owner[iref]}
                    )
    return {"owner": owner, "wired": wired, "carried": carried}


def content_sha(body):
    """Stable content hash of an ODM concept body for the content-compare skip.
    Canonical JSON (sorted keys) so re-serialization order can't force churn."""
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
