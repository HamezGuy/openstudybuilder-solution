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
import os
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
    # Exact source objects are the closed-loop carrier. `ext` contains only
    # properties which Leg A did not map first-class; `source` also preserves
    # handled values (option codes, original refKeys, null-vs-default, etc.) so
    # Leg C can prove and restore byte-semantic parity after an OSB round trip.
    {"name": "source", "compatible_types": ["FormDef", "ItemDef"], "data_type": "string"},
    # One deterministic form carries the non-structural StudyBundle envelope:
    # study metadata, tasks, provenance, narrative, evidence, and build census.
    # This keeps OSB as the system of record without making Leg C read Postgres.
    {"name": "bundleMeta", "compatible_types": ["FormDef"], "data_type": "string"},
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


PURPOSE_LEVEL_NAMES = {
    "objective": {
        "PRIMARY": "Primary Objective",
        "SECONDARY": "Secondary Objective",
        "EXPLORATORY": "Exploratory Objective",
    },
    "endpoint": {
        "PRIMARY": "Primary Outcome Measure",
        "SECONDARY": "Secondary Outcome Measure",
        "EXPLORATORY": "Exploratory Outcome Measure",
        "ADDITIONAL": "Additional Outcome Measure",
    },
    "criterion": {
        "INCLUSION": "Inclusion Criteria",
        "EXCLUSION": "Exclusion Criteria",
    },
}


FLOWCHART_GROUP_BY_CDASH_DOMAIN = {
    "AE": "SAFETY",
    "CM": "SAFETY",
    "EG": "SAFETY",
    "ECG": "SAFETY",
    "LB": "SAFETY",
    "VS": "SAFETY",
    "DM": "SUBJECT RELATED INFORMATION",
    "DS": "SUBJECT RELATED INFORMATION",
    "MH": "SUBJECT RELATED INFORMATION",
    "PC": "PHARMACOKINETICS",
    "PK": "PHARMACOKINETICS",
    "PP": "PHARMACOKINETICS",
    "PD": "PHARMACODYNAMICS",
}

# Exact normalized source labels only. This is deliberately not substring/fuzzy
# classification: a row that does not state one of these semantics remains a
# blocker rather than being pushed into a generic SoA bucket.
FLOWCHART_GROUP_BY_ACTIVITY_NAME = {
    "admission and discharge": "SUBJECT RELATED INFORMATION",
    "demographics": "SUBJECT RELATED INFORMATION",
    "medical history": "SUBJECT RELATED INFORMATION",
    "physical exam": "SUBJECT RELATED INFORMATION",
    "physical examination": "SUBJECT RELATED INFORMATION",
    "randomization": "SUBJECT RELATED INFORMATION",
    "study termination": "SUBJECT RELATED INFORMATION",
    "disposition": "SUBJECT RELATED INFORMATION",
    "study administration": "SUBJECT RELATED INFORMATION",
    "study design randomization": "SUBJECT RELATED INFORMATION",
    "exposure": "SUBJECT RELATED INFORMATION",
    "procedures": "SUBJECT RELATED INFORMATION",
    "endpoints": "SUBJECT RELATED INFORMATION",
    "protocol deviations": "SUBJECT RELATED INFORMATION",
    "inclusion exclusion criteria": "ELIGIBILITY AND OTHER CRITERIA",
    "adverse event": "SAFETY",
    "adverse events": "SAFETY",
    "adverse event grading": "SAFETY",
    "concomitant medication": "SAFETY",
    "concomitant medications": "SAFETY",
    "ecg": "SAFETY",
    "laboratory": "SAFETY",
    "pregnancy test": "SAFETY",
    "urinalysis": "SAFETY",
    "vital signs": "SAFETY",
    "blood sample for pk": "PHARMACOKINETICS",
    "urine collection": "PHARMACODYNAMICS",
    # ORA-D-017 (NCT04150107) SoA rows, added 20260826-orasoa. Exact payload
    # labels; terms verified Final in the live Flowchart Group codelist.
    "informed consent": "INFORMED CONSENT",
    "serum pregnancy test": "SAFETY",
    "urine pregnancy test": "SAFETY",
    "infectious serology hiv hbv hcv": "SAFETY",
    "c peptide tsh fsh": "SAFETY",
    "hematology": "SAFETY",
    "serum chemistry with fpg": "SAFETY",
    # HbA1c and CGM are the trial's glycemic-control measures (T1D crossover):
    # efficacy, not safety monitoring.
    "hba1c": "EFFICACY",
    "cgm monitoring": "EFFICACY",
    "treatment crossover": "SUBJECT RELATED INFORMATION",
    "treatment compliance": "SUBJECT RELATED INFORMATION",
    "treatment dispensing returning": "TRIAL MATERIAL",
    "diary 3 dispensing collecting": "TRIAL MATERIAL",
    "study disposition table 1 footnotes": "SUBJECT RELATED INFORMATION",
}


def semantic_identity(value):
    """Case/spacing/punctuation-insensitive identity, never fuzzy similarity."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


# Deployment-local Flowchart Group decisions, as DATA rather than a source edit.
#
# The table above is exact and small on purpose — a row stating no recognised
# semantic must block rather than land in a generic bucket. Real protocols
# still arrive with study-local row labels ("Blood draw for clinical lab
# tests", "Diary 3 dispensing/collecting"), and until now the only way to admit
# one was to edit the dict above per study. This reads the same decisions from
# a file the operator can review and diff, keyed by the SAME semantic identity.
#
# Consulted LAST (see `_flowchart_group_name`): an override cannot mask a CDASH
# domain or a category the payload itself stated, and a row with no entry here
# still blocks. No file = the behaviour before this existed.
FLOWCHART_GROUP_OVERRIDES_FILE = os.environ.get(
    "FLOWCHART_GROUP_OVERRIDES_FILE",
    "/opt/accuratrial/ingest/flowchart-overrides.json",
)


def _load_flowchart_overrides():
    try:
        with open(FLOWCHART_GROUP_OVERRIDES_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    loaded = {}
    for key, value in raw.items():
        identity = semantic_identity(key)
        group = str(value or "").strip().upper()
        if identity and group:
            loaded[identity] = group
    return loaded


FLOWCHART_GROUP_OVERRIDES = _load_flowchart_overrides()


def _flowchart_group_name(activity):
    domain = str(activity.get("cdashDomain") or "").strip().upper()
    if domain in FLOWCHART_GROUP_BY_CDASH_DOMAIN:
        return FLOWCHART_GROUP_BY_CDASH_DOMAIN[domain]
    category = str(activity.get("category") or "").strip().upper()
    if category in set(FLOWCHART_GROUP_BY_CDASH_DOMAIN.values()):
        return category
    identity = semantic_identity(activity.get("name"))
    return (
        FLOWCHART_GROUP_BY_ACTIVITY_NAME.get(identity)
        or FLOWCHART_GROUP_OVERRIDES.get(identity)
    )


def native_soa_plan(payload, library_activities):
    """Resolve a payload SoA to governed OSB activity selections and schedules.

    Only one Final library activity with the exact normalized source name may
    map. Drafts, partial names and ambiguous exact matches are explicit blockers.
    The function performs no API calls and never creates library concept ids.
    """
    section = payload.get("scheduleOfActivities")
    if section is None:
        return None
    reconciliation = section.get("reconciliation") or {}
    source_visits = list(section.get("visits") or [])
    source_activities = list(section.get("activities") or [])
    source_schedules = list(section.get("schedules") or [])
    if reconciliation.get("balanced") is not True:
        raise ValueError("OSB_NATIVE_SOA_RECONCILIATION_UNBALANCED")
    expected = {
        "sourceVisits": len(source_visits),
        "sourceActivities": len(source_activities),
        "sourceScheduleCells": len(source_schedules),
    }
    for key, count in expected.items():
        if reconciliation.get(key) != count:
            raise ValueError(f"OSB_NATIVE_SOA_{key.upper()}_COUNT_MISMATCH")
    joined_visits = sum(1 for visit in source_visits if visit.get("payloadVisitRef"))
    unjoined_visit_ids = [
        visit.get("sourceVisitId")
        for visit in source_visits
        if not visit.get("payloadVisitRef")
    ]
    joined_schedules = sum(
        1 for schedule in source_schedules if schedule.get("payloadVisitRef")
    )
    if reconciliation.get("joinedVisits") != joined_visits:
        raise ValueError("OSB_NATIVE_SOA_JOINED_VISIT_COUNT_MISMATCH")
    if reconciliation.get("unjoinedVisitIds") != unjoined_visit_ids:
        raise ValueError("OSB_NATIVE_SOA_UNJOINED_VISIT_IDS_MISMATCH")
    if reconciliation.get("joinedScheduleCells") != joined_schedules:
        raise ValueError("OSB_NATIVE_SOA_JOINED_SCHEDULE_COUNT_MISMATCH")
    if reconciliation.get("unjoinedScheduleCells") != len(source_schedules) - joined_schedules:
        raise ValueError("OSB_NATIVE_SOA_UNJOINED_SCHEDULE_COUNT_MISMATCH")

    activity_by_ref = {}
    for activity in source_activities:
        ref = str(activity.get("refKey") or "").strip()
        name = str(activity.get("name") or "").strip()
        if not ref or ref in activity_by_ref or not name:
            raise ValueError(f"OSB_NATIVE_SOA_ACTIVITY_INVALID:{ref or '?'}")
        activity_by_ref[ref] = activity
    visit_by_source_id = {}
    for visit in source_visits:
        source_id = str(visit.get("sourceVisitId") or "").strip()
        if not source_id or source_id in visit_by_source_id:
            raise ValueError(f"OSB_NATIVE_SOA_VISIT_INVALID:{source_id or '?'}")
        visit_by_source_id[source_id] = visit

    final_by_name = {}
    for concept in library_activities or []:
        if str(concept.get("status") or "").casefold() != "final":
            continue
        key = semantic_identity(concept.get("name"))
        if key:
            final_by_name.setdefault(key, []).append(concept)

    mapped = []
    blocked = []
    mapped_refs = set()
    ref_by_activity_uid = {}
    for ref, activity in activity_by_ref.items():
        matches = final_by_name.get(semantic_identity(activity["name"]), [])
        unique = {match.get("uid"): match for match in matches if match.get("uid")}
        if len(unique) != 1:
            blocked.append(
                {
                    "kind": "activity",
                    "ref": ref,
                    "reason": (
                        "no unique Final exact-normalized OSB Activity match"
                        if not unique
                        else "multiple Final exact-normalized OSB Activity matches"
                    ),
                }
            )
            continue
        flowchart_group_name = _flowchart_group_name(activity)
        if flowchart_group_name is None:
            blocked.append(
                {
                    "kind": "activity",
                    "ref": ref,
                    "reason": "no deterministic Flowchart Group mapping from source semantics",
                }
            )
            continue
        concept = next(iter(unique.values()))
        prior_ref = ref_by_activity_uid.get(concept["uid"])
        if prior_ref is not None:
            blocked.append(
                {
                    "kind": "activity",
                    "ref": ref,
                    "reason": (
                        "multiple source activity rows resolve to one OSB Activity "
                        f"already mapped by {prior_ref}"
                    ),
                }
            )
            continue
        groupings = list(concept.get("activity_groupings") or [])
        # A grouping is optional on StudySelectionActivity. Reuse it only when
        # unique; selecting one of several would invent an unreviewed hierarchy.
        grouping = groupings[0] if len(groupings) == 1 else {}
        mapped.append(
            {
                "ref": ref,
                "name": activity["name"],
                "activity_uid": concept["uid"],
                "activity_name": concept.get("name"),
                "flowchart_group_name": flowchart_group_name,
                "activity_group_uid": grouping.get("activity_group_uid"),
                "activity_subgroup_uid": grouping.get("activity_subgroup_uid"),
            }
        )
        mapped_refs.add(ref)
        ref_by_activity_uid[concept["uid"]] = ref

    schedules = []
    seen_source_cells = set()
    for index, schedule in enumerate(source_schedules):
        activity_ref = str(schedule.get("activityRef") or "").strip()
        source_visit_id = str(schedule.get("sourceVisitId") or "").strip()
        if activity_ref not in activity_by_ref:
            raise ValueError(f"OSB_NATIVE_SOA_SCHEDULE_ACTIVITY_MISSING:{activity_ref or index}")
        if source_visit_id not in visit_by_source_id:
            raise ValueError(f"OSB_NATIVE_SOA_SCHEDULE_VISIT_MISSING:{source_visit_id or index}")
        source_cell = (activity_ref, source_visit_id)
        if source_cell in seen_source_cells:
            raise ValueError(
                f"OSB_NATIVE_SOA_SCHEDULE_DUPLICATE:{activity_ref}::{source_visit_id}"
            )
        seen_source_cells.add(source_cell)
        payload_visit_ref = schedule.get("payloadVisitRef")
        if not payload_visit_ref:
            blocked.append(
                {
                    "kind": "activity_schedule",
                    "ref": f"{activity_ref}::{source_visit_id}",
                    "reason": "source SoA visit did not verify against a payload visit",
                }
            )
            continue
        if activity_ref not in mapped_refs:
            continue  # the activity-level blocker already accounts for these cells
        schedules.append(
            {
                "ref": f"{activity_ref}::{payload_visit_ref}",
                "activity_ref": activity_ref,
                "payload_visit_ref": payload_visit_ref,
                "source_visit_id": source_visit_id,
                "value": schedule.get("value"),
                "required": schedule.get("required") is True,
                "conditional": schedule.get("conditional"),
                "footnote_refs": list(schedule.get("footnoteRefs") or []),
            }
        )
    return {
        "activities": mapped,
        "schedules": schedules,
        "blocked": blocked,
        "source_activity_count": len(source_activities),
        "source_schedule_count": len(source_schedules),
    }


def study_purpose_plan(payload):
    """Validate and dependency-order native OSB Study Purpose records.

    The payload is the authority for reviewed text and relationships. This
    mapper never derives an endpoint from an ODM item or criteria from the
    aggregate eligibility prose carrier.
    """
    purpose = payload.get("studyPurpose")
    if purpose is None:
        return None
    reconciliation = purpose.get("reconciliation") or {}
    if reconciliation.get("balanced") is not True:
        raise ValueError("OSB_STUDY_PURPOSE_RECONCILIATION_UNBALANCED")

    seen = {"objective": set(), "endpoint": set(), "criterion": set()}
    result = {"objectives": [], "endpoints": [], "criteria": [], "blockers": []}

    def common(kind, item):
        ref = str(item.get("refKey") or "").strip()
        text = str(item.get("text") or "").strip()
        source_ids = item.get("sourceAssertionIds") or []
        if not ref or ref in seen[kind] or not text or not source_ids:
            raise ValueError(f"OSB_STUDY_PURPOSE_{kind.upper()}_INVALID:{ref or '?'}")
        seen[kind].add(ref)
        return ref, text, list(source_ids)

    objective_aliases = {}
    for item in purpose.get("objectives") or []:
        ref, text, source_ids = common("objective", item)
        level = str(item.get("level") or "").upper()
        level_name = PURPOSE_LEVEL_NAMES["objective"].get(level)
        if level_name is None:
            raise ValueError(f"OSB_STUDY_PURPOSE_OBJECTIVE_LEVEL_INVALID:{ref}")
        aliases = [str(value) for value in item.get("aliasRefKeys") or []]
        for alias in [ref, *aliases]:
            if alias in objective_aliases and objective_aliases[alias] != ref:
                raise ValueError(f"OSB_STUDY_PURPOSE_OBJECTIVE_ALIAS_DUPLICATE:{alias}")
            objective_aliases[alias] = ref
        result["objectives"].append(
            {
                "ref": ref,
                "text": text,
                "level": level,
                "level_name": level_name,
                "source_assertion_ids": source_ids,
                "evidence": list(item.get("evidence") or []),
            }
        )

    for item in purpose.get("endpoints") or []:
        ref, text, source_ids = common("endpoint", item)
        level = str(item.get("level") or "").upper()
        level_name = PURPOSE_LEVEL_NAMES["endpoint"].get(level)
        if level_name is None:
            raise ValueError(f"OSB_STUDY_PURPOSE_ENDPOINT_LEVEL_INVALID:{ref}")
        # AN ENDPOINT MAY SHIP UNLINKED. The payload states either `objectiveRef`
        # (a published objective) or `unresolvedObjectiveRef` (the id it stated,
        # plus why it could not be published) — never both, never neither. OSB's
        # own model makes study_objective_uid optional, so an endpoint whose
        # objective is absent or under review is created WITHOUT the link rather
        # than refused: refusing it deleted 60 reviewed ACTT-1 endpoints to
        # protect a relationship they did not own. The unresolved record rides
        # into the census so a reviewer can attach it in OSB.
        stated = item.get("objectiveRef")
        unresolved = item.get("unresolvedObjectiveRef") or None
        if stated and unresolved:
            raise ValueError(f"OSB_STUDY_PURPOSE_ENDPOINT_OBJECTIVE_AMBIGUOUS:{ref}")
        objective_ref = objective_aliases.get(str(stated or ""))
        if stated and objective_ref is None:
            # The payload named a published objective that this payload does not
            # contain. That is an INTERNAL inconsistency, not a degraded link.
            raise ValueError(f"OSB_STUDY_PURPOSE_ENDPOINT_OBJECTIVE_MISSING:{ref}")
        if objective_ref is None and unresolved is None:
            raise ValueError(f"OSB_STUDY_PURPOSE_ENDPOINT_OBJECTIVE_UNDECLARED:{ref}")
        result["endpoints"].append(
            {
                "ref": ref,
                "text": text,
                "level": level,
                "level_name": level_name,
                "objective_ref": objective_ref,
                "unresolved_objective": unresolved,
                "timeframe": item.get("timeframe"),
                "source_assertion_ids": source_ids,
                "evidence": list(item.get("evidence") or []),
            }
        )

    for item in purpose.get("criteria") or []:
        ref, text, source_ids = common("criterion", item)
        criterion_type = str(item.get("type") or "").upper()
        type_name = PURPOSE_LEVEL_NAMES["criterion"].get(criterion_type)
        if type_name is None:
            raise ValueError(f"OSB_STUDY_PURPOSE_CRITERION_TYPE_INVALID:{ref}")
        result["criteria"].append(
            {
                "ref": ref,
                "text": text,
                "type": criterion_type,
                "type_name": type_name,
                "category": item.get("category"),
                "source_assertion_ids": source_ids,
                "evidence": list(item.get("evidence") or []),
            }
        )

    result["blockers"] = list(purpose.get("blockers") or [])
    planned_assertions = {
        source_id
        for section in ("objectives", "endpoints", "criteria")
        for item in result[section]
        for source_id in item["source_assertion_ids"]
    }
    blocked_assertions = {
        source_id
        for blocker in result["blockers"]
        for source_id in blocker.get("sourceAssertionIds") or []
    }
    if planned_assertions & blocked_assertions:
        raise ValueError("OSB_STUDY_PURPOSE_ASSERTION_DOUBLE_DISPOSITION")
    if len(planned_assertions) != reconciliation.get("mappedAssertions"):
        raise ValueError("OSB_STUDY_PURPOSE_MAPPED_ASSERTION_COUNT_MISMATCH")
    if len(blocked_assertions) != reconciliation.get("blockedAssertions"):
        raise ValueError("OSB_STUDY_PURPOSE_BLOCKED_ASSERTION_COUNT_MISMATCH")
    if len(planned_assertions | blocked_assertions) != reconciliation.get(
        "sourceAssertions"
    ):
        raise ValueError("OSB_STUDY_PURPOSE_SOURCE_ASSERTION_COUNT_MISMATCH")
    return result


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

    # _DAY_MISSING_PLACEMENT. OSB enforces that visit_number increases with
    # visit TIMING, so a day-missing visit cannot sit at the anchor's day 0
    # while an earlier-numbered visit sits at day 56 — that is exactly the
    # "not defined in chronological order by study visit timing" rejection.
    # A day-missing visit therefore needs SOME slot, and the only honest source
    # for it is the order the protocol listed the visits in.
    #
    # WHICH SIDE. Placing every day-missing visit after the last dated one put
    # ACTT-1's "Screen" column at Day 30 — after the Day 29 follow-up — because
    # the protocol dates its Day 1..29 visits but heads the screening column with
    # a name rather than a day. A screening visit sorted last is not a censused
    # approximation, it is the calendar backwards. So the stated ORDER decides
    # the side: a visit the protocol lists BEFORE its first dated visit is placed
    # before it (descending from origin-1, keeping their stated order), and one
    # listed after is placed past the last dated visit, as before. Both remain
    # census-visible as `day_missing`; neither asserts a clinical day.
    _dated_tvs = [_effective_tv(idx, visits[idx]) for idx in range(len(visits))]
    _first_dated_idx = next((i for i in range(len(visits)) if _dated_tvs[i] is not None), None)
    _next_tv = max([tv for tv in _dated_tvs if tv is not None], default=0)
    _before_tv = min([tv for tv in _dated_tvs if tv is not None], default=0)
    _missing_tv = {}
    # Trailing placements ascend in stated order; leading ones descend, so that
    # reversing the stated order below keeps the earliest-listed visit earliest.
    for _idx in range(len(visits)):
        if _dated_tvs[_idx] is not None:
            continue
        if _first_dated_idx is None or _idx > _first_dated_idx:
            _next_tv += 1
            _missing_tv[_idx] = _next_tv
    for _idx in range(len(visits) - 1, -1, -1):
        if _dated_tvs[_idx] is not None or _idx in _missing_tv:
            continue
        _before_tv -= 1
        _missing_tv[_idx] = _before_tv

    # Number visits by their FINAL effective time — a day-missing visit's placed
    # slot included, which is what makes visit_number and timing agree. (Ranking
    # before placement sorted every day-missing visit last, so a leading one got
    # the lowest time and the highest number: OSB's own contradiction.) Ties break
    # on the protocol's stated order.
    def _final_tv(idx):
        tv = _dated_tvs[idx]
        return tv if tv is not None else _missing_tv[idx]

    chrono_order = sorted(range(len(visits)), key=lambda idx: (_final_tv(idx), idx))
    chrono_rank = {idx: rank + 1 for rank, idx in enumerate(chrono_order)}

    # OSB permits exactly ONE UNSCHEDULED_VISIT per study (study_visit.py), so a
    # protocol stating several would lose all but the first. Tracked here and
    # applied in the loop below.
    _unscheduled_claimed = False

    plans = []
    for i, v in enumerate(visits):
        type_key = (v.get("type") or "scheduled").strip().lower()
        mapping = VISIT_TYPE_MAP.get(type_key)
        # _ALL_VISITS_MANUALLY_DEFINED. OSB derives the name of any visit that is
        # not MANUALLY_DEFINED_VISIT — an UNSCHEDULED_VISIT always comes back as
        # settings.unscheduled_visit_name, whatever the protocol called it. The
        # study bundle then carries that derived name AND the protocol name from
        # the x360i carrier, and the EDC imports one visit as two events (11 for
        # 10 on NCT03472885). OSB also permits only one UNSCHEDULED_VISIT per
        # study, so a protocol stating several loses the rest outright.
        #
        # Every payload visit is therefore manually defined: names, short names,
        # numbers and refKeys survive, the counts match end to end, and the only
        # thing not natively represented is OSB's class-level "unscheduled" flag
        # — which the payload `type`, the x360i carrier and the EDC's own event
        # type all still state. Censused per visit, never silent.
        _unscheduled_demoted = False
        if mapping is not None and mapping["visit_class"] == "UNSCHEDULED_VISIT":
            mapping = {
                "visit_type_names": list(mapping["visit_type_names"])
                + list(VISIT_TYPE_MAP["scheduled"]["visit_type_names"]),
                "visit_class": "MANUALLY_DEFINED_VISIT",
            }
            _unscheduled_demoted = True
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
        time_value = tv if tv is not None else _missing_tv[i]
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
            "study_epoch_uid": epoch_uid_by_ref.get(v["refKey"]),
            "refKey": v["refKey"],
            "is_global_anchor_visit": is_anchor,
            "time_value": time_value,
            "day_missing": day is None,
            "unscheduled_demoted": _unscheduled_demoted,
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


# A placebo arm names itself. Word-bounded, and the `-controlled` lookahead is
# load-bearing: "Placebo-controlled Remdesivir" is the INVESTIGATIONAL arm of a
# placebo-controlled study, not the placebo arm, and a bare word boundary reads
# it the wrong way round because '-' is a non-word character.
_PLACEBO_ARM = re.compile(
    r"(?:^|\W)(placebo|sham)(?![\s-]*controlled)(?:\W|$)", re.IGNORECASE)
_COMPARATOR_ARM = re.compile(
    r"(?:^|\W)(comparator|active control|standard of care|standard-of-care|"
    r"usual care|best supportive care)(?:\W|$)",
    re.IGNORECASE,
)
# The study-level `control` values that mean "the non-control arms are the
# investigational ones". Read from the payload's own stated study block.
_PLACEBO_CONTROLLED = re.compile(r"placebo|sham", re.IGNORECASE)


def arm_type_names(arm_name, stated_control):
    """Candidate OSB 'Arm Type' term names for one arm, most specific first.

    Resolved from what the SOURCE says, never from position or count:

      * the arm's OWN NAME calls it a placebo/sham arm  -> Placebo Arm;
      * the arm's own name calls it a comparator/active control -> Comparator Arm;
      * otherwise, IF the study block states a placebo control, an arm that is
        not the placebo arm is by definition the investigational one -> Investigational Arm.

    A study that states no control type and an arm that names no role yields []
    — the arm is still created, with no type, exactly as before. The importer
    resolves these names against live CT and censuses a miss; nothing is coerced.
    """
    name = str(arm_name or "")
    if _PLACEBO_ARM.search(name):
        return ["Placebo Arm"]
    if _COMPARATOR_ARM.search(name):
        return ["Comparator Arm"]
    if stated_control and _PLACEBO_CONTROLLED.search(str(stated_control)):
        return ["Investigational Arm"]
    return []


def arms_plan(payload):
    """StudyArm create-bodies — genuine arms only (the payload already
    separated non-arm group classes, which ride the census as carried)."""
    stated_control = (
        ((payload.get("study") or {}).get("nativeMetadata") or {})
        .get("studyIntervention", {})
        .get("controlType")
    )
    plans = []
    for i, arm in enumerate(payload.get("arms", []), start=1):
        name = arm["name"].strip()
        plans.append(
            {
                "name": name[:200],
                "short_name": name[:20],
                "order": i,
                "description": arm.get("description"),
                "arm_type_names": arm_type_names(name, stated_control),
            }
        )
    return plans


# The element sub-type a scaffolding element carries. The carrier epoch is a
# Treatment epoch (see `epoch_subtype_candidates`), and an arm's assignment
# inside a treatment epoch is a treatment element. Tried in order against live
# CT; a miss is censused, never invented.
SCAFFOLDING_ELEMENT_SUBTYPES = ["Treatment", "Basic"]


def design_structure_plan(payload):
    """StudyElements + StudyDesignCells, so OSB's design matrix is not empty.

    WHAT OSB NEEDS AND WHY THIS EXISTS. OSB models a study's structure as a
    matrix of DESIGN CELLS: (arm x epoch) -> element. Without cells the Study
    Structure screen renders an empty grid, the arms and the epoch appear as two
    unrelated lists, and nothing downstream can tell which arm is active when —
    which is exactly what "the study is not linked up" looks like in the UI.
    Measured on ACTT-1: 3 arms, 1 epoch, **0 elements and 0 design cells**.

    WHAT IS AND IS NOT CLAIMED. A protocol that states its own epochs, elements
    and cells would ride them first-class; none of ours state elements, so this
    is SCAFFOLDING and is declared as such — the same treatment
    `CARRIER_EPOCH_NAME` already gets. It asserts nothing the arm does not: one
    element per arm, named for the arm, and one cell placing that arm's element
    in every epoch the study has. For a parallel-group study that IS the arm
    restated in OSB's structural vocabulary. It is not a schedule, a dose, or a
    duration, and it never invents an epoch the protocol did not state.
    """
    arms = [a["name"].strip() for a in payload.get("arms", []) if a.get("name")]
    elements = [
        {
            "arm_ref": name,
            "name": name[:200],
            "short_name": name[:20],
            "order": i,
            "element_subtype_names": list(SCAFFOLDING_ELEMENT_SUBTYPES),
            "scaffolding": True,
        }
        for i, name in enumerate(arms, start=1)
    ]
    return {"elements": elements, "arms": arms}


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
    # OSB pairs length with significant_digits for floats: both set or both
    # null. This body never states significant digits, so a float with a
    # stated length must drop it (20260826-floatpair); the collection
    # constraint remains in the EDC item definition.
    if str(datatype).lower() == "float":
        length = None
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


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def source_form_value(payload, form_ref):
    """Exact source form, including fields for one bounded lossless carrier."""
    for form in (
        payload.get("sourceBundle", {}).get("forms", {}).get("forms", [])
    ):
        if form.get("refKey") == form_ref:
            return _canonical_json(form)
    return None


def source_field_value(payload, form_ref, field_ref):
    """Exact source field for one form placement (compound identity)."""
    for form in (
        payload.get("sourceBundle", {}).get("forms", {}).get("forms", [])
    ):
        if form.get("refKey") != form_ref:
            continue
        for field in form.get("fields", []):
            if field.get("refKey") == field_ref:
                return _canonical_json(field)
    return None


def bundle_meta_value(payload):
    """Source StudyBundle except form rows, which ride their own FormDefs."""
    source = payload.get("sourceBundle", {})
    meta = {key: value for key, value in source.items() if key != "forms"}
    forms_envelope = source.get("forms")
    if isinstance(forms_envelope, dict):
        forms_meta = {
            key: value for key, value in forms_envelope.items() if key != "forms"
        }
        if forms_meta:
            meta["forms"] = forms_meta
    return _canonical_json(meta) if meta else None


def placement_item_key(group_ref, item_ref):
    """Stable uid-map key for an item placement, not merely its data element."""
    return f"{group_ref}::{item_ref}"


# ODM OIDs are STUDY-SCOPED, exactly as study-event OIDs already are.
#
# THE LOSS THIS CLOSES. A payload's form/group/item refKeys are minted from one
# study's own CRF ("F_LABORATORY", "F_MEDICAL_HISTORY_SEC_3", "AGE", "LBCAT"),
# so two studies importing into one OSB collided on every shared name — and an
# OSB ItemDef may belong to exactly ONE ItemGroupDef. Measured on ACTT-1 against
# an instance that already held NCT03167411:
#   * F_MEDICAL_HISTORY_SEC_3 held 136 of the OTHER study's items and none of
#     ACTT-1's 5; F_DISPOSITION_SEC_4 held 126 of them and none of ACTT-1's 3;
#   * 10 item-group writes failed outright with "OdmItem is already connected to
#     another OdmItemGroup", emptying F_LABORATORY_SEC_4 (42 fields),
#     F_PROCEDURES_INTERVENTIONS_SEC_4 (23), F_ADVERSE_EVENTS_SEC_4 (17) and
#     four more — 106 protocol-stated fields with nowhere to attach.
# Both studies were wrong afterwards, and neither import reported a loss,
# because each one's own writes had "succeeded".
#
# `SE.360I.<studyId>.<visitRef>` was already doing this correctly for visits.
# This is the same rule for the other three ODM kinds. Identity is unaffected:
# the exporter matches x360i vendor attributes by NAME (refKey), never by OID.
ODM_OID_PREFIX = {"form": "F", "item_group": "IG", "item": "IT"}


def odm_oid(kind, study_id, ref):
    """`<kind>.360I.<studyId>.<ref>` — one study's ODM object, addressable."""
    return f"{ODM_OID_PREFIX[kind]}.360I.{study_id}.{ref}"


def placement_item_oid(item_ref, group_ref, owner_group_ref):
    """OSB permits one group per ItemDef, while EDC permits repeat placements.

    The canonical owner keeps the original OID. Every additional placement gets
    a deterministic clone OID; its x360i:source/refKey still restores the exact
    EDC field identity on export.
    """
    if group_ref == owner_group_ref:
        return item_ref
    return f"{item_ref}__X360I_PL_{stable_suffix(group_ref, 12)}"


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
    "study_epoch_uid",
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
    """Choose the canonical placement of every repeated ODM item.

    OSB enforces one-item-one-group: an OdmItem may be connected to a single
    OdmItemGroup, and the item-ref batch POST is atomic — a single already-claimed
    item rejects the whole array (verified live against OSB). A 360i payload can
    legitimately list the SAME item in several groups: a cross-domain catch-all
    group (e.g. a Medical History "MH_OTHER" section) re-lists AE/CM/LB/VS fields
    that also live in their canonical domain forms. Wiring such a group naively
    makes OSB reject its entire batch, silently dropping even the items that were
    UNIQUE to that group (the group ends up empty).

    This picks each item's canonical owner deterministically. The importer keeps
    the owner's original ItemDef and creates one deterministic ItemDef clone for
    every other placement, so all EDC form fields remain visible in OSB and
    round-trip instead of being merely census-carried:

      owner   {itemRef: groupRef}         -- group using the base ItemDef
      wired   {groupRef: [itemRef, ...]}  -- base placements, payload order
      duplicates [{"item": itemRef,       -- placements using cloned ItemDefs
                   "group": groupRef,
                   "owner": ownerGroupRef}]

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
    duplicates = []
    for form in odm.get("forms", []):
        for group in form.get("itemGroups", []):
            gref = group["refKey"]
            for item in group.get("items", []):
                iref = item["refKey"]
                if owner[iref] == gref:
                    wired.setdefault(gref, []).append(iref)
                else:
                    duplicates.append(
                        {"item": iref, "group": gref, "owner": owner[iref]}
                    )
    return {"owner": owner, "wired": wired, "duplicates": duplicates}


def content_sha(body):
    """Stable content hash of an ODM concept body for the content-compare skip.
    Canonical JSON (sorted keys) so re-serialization order can't force churn."""
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
