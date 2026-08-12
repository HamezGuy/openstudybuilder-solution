"""Populate a study's Schedule of Activities from its ODM study-events.

The 360i importer anchors the form-by-visit matrix on OdmStudyEvent FORM_REFs
(the EDC export reads those; see OSB_INTEGRATION_RUNBOOK.md rule 6). OSB's own
SoA pages, however, render StudyActivities + StudyActivitySchedules, which that
anchor deliberately does not create — so the SoA matrix shows empty for an
imported study even though the schedule exists.

This script projects the ODM anchor INTO the SoA layer, one activity per
scheduled form, scheduled at exactly the visits whose study-event carries the
form's FORM_REF. It is a PRESENTATION-layer derivation for OSB's UI: the ODM
study-events remain the export contract, and nothing here feeds the EDC bundle
(verified: the exporter reads study-events, not StudyActivities).

Explicitly sanctioned override of runbook rule 6 (owner decision 2026-08-08):
the rule guarded the EXPORT contract against faking; the export still never
reads these. Every created concept is stamped into the printed census.

Idempotent: activities are found by name in the Sponsor library before being
created; selections and schedules are diffed against what the study already
has. Re-run → no-op.

Usage (host, stacks up):
    python importers/populate_soa_from_odm.py --study Study_000017 \
        --api http://localhost:5005/api
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

from .utils.mapping_authority import assert_unsafe_legacy_mutation_allowed

# Flowchart-group CT term per form name; names not listed fall back to
# SUBJECT RELATED INFORMATION. Terms are looked up live by name (uids differ
# per instance seed), so this maps form -> sponsor_preferred_name.
FLOWCHART_GROUP_BY_FORM = {
    "Adverse Events": "SAFETY",
    "Concomitant Medications": "SAFETY",
    "Demographics": "SUBJECT RELATED INFORMATION",
    "Disposition": "SUBJECT RELATED INFORMATION",
    "ECG": "SAFETY",
    "Inclusion/Exclusion Criteria": "ELIGIBILITY AND OTHER CRITERIA",
    "Laboratory": "SAFETY",
    "Medical History": "SUBJECT RELATED INFORMATION",
    "Vital Signs": "SAFETY",
}
DEFAULT_FLOWCHART_GROUP = "SUBJECT RELATED INFORMATION"

ACTIVITY_GROUP_NAME = "360i Data Collection"
ACTIVITY_SUBGROUP_NAME = "360i CRF Forms"
LIBRARY = "Sponsor"


def request(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {detail}") from e


def get_paged(api, path, page_size=100):
    out, page = [], 1
    while True:
        d = request("GET", f"{api}{path}{'&' if '?' in path else '?'}page_number={page}&page_size={page_size}")
        items = d.get("items", d) if isinstance(d, dict) else d
        out.extend(items)
        if not isinstance(d, dict) or len(items) < page_size:
            return out
        page += 1


def term_name(t):
    nm = t.get("name")
    if isinstance(nm, dict) and nm.get("sponsor_preferred_name"):
        return nm["sponsor_preferred_name"]
    return t.get("sponsor_preferred_name")


def ensure_final(api, kind_path, uid, status):
    """Approve a draft concept; tolerate already-final."""
    if status == "Final":
        return
    request("POST", f"{api}/concepts/activities/{kind_path}/{uid}/approvals")


def find_or_create_group(api, kind_path, name, extra=None):
    for g in get_paged(api, f"/concepts/activities/{kind_path}?filters={{}}"):
        if g.get("name") == name and g.get("status") in ("Final", "Draft"):
            ensure_final(api, kind_path, g["uid"], g.get("status"))
            return g["uid"], False
    body = {"name": name, "name_sentence_case": name.lower(), "library_name": LIBRARY}
    if extra:
        body.update(extra)
    created = request("POST", f"{api}/concepts/activities/{kind_path}", body)
    request("POST", f"{api}/concepts/activities/{kind_path}/{created['uid']}/approvals")
    return created["uid"], True


def main():
    assert_unsafe_legacy_mutation_allowed("populate_soa_from_odm")
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", required=True)
    ap.add_argument("--api", default="http://localhost:5005/api")
    args = ap.parse_args()
    api, study = args.api.rstrip("/"), args.study
    census = {"created": [], "reused": [], "scheduled": 0, "skipped": 0}

    # 1. The form-by-visit matrix, from the ODM study-events (the source of truth).
    visits = get_paged(api, f"/studies/{study}/study-visits")
    if not visits:
        sys.exit(f"study {study} has no visits; nothing to schedule")
    # study-event OID suffix = the payload visitRef; join via visit_name-independent
    # route: study-events were created one-per-visit in visit order is NOT safe —
    # instead use the uid_map convention SE.360I.<studyId>.<visitRef> and match
    # the visit by its order among unique_visit_number-sorted visits recorded at
    # import time. The robust join available live: study-event OID visitRef ==
    # ledger uid_map['visits'] key -> StudyVisit uid.
    events = [e for e in get_paged(api, "/odms/study-events")
              if ".360I." in (e.get("oid") or "")]
    if not events:
        sys.exit("no 360i ODM study-events found; was the study imported?")

    visit_uids = {v["uid"] for v in visits}
    forms_by_visit_uid = {}
    unmatched = []
    for e in events:
        ref = e["oid"].rsplit(".", 1)[-1]
        # uid_map join is preferred, but reconstruct it live: importer stamped
        # the visitRef into nothing else queryable, so accept a ledger-provided
        # map via env when the heuristic fails.
        forms_by_visit_uid[ref] = [f["name"] for f in e.get("forms", [])]
    # Resolve refs -> StudyVisit uids via the import ledger's uid_map if given.
    uid_map_env = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    ref_to_uid = uid_map_env.get("visits", {})
    resolved = {}
    for ref, forms in forms_by_visit_uid.items():
        vuid = ref_to_uid.get(ref)
        if vuid and vuid in visit_uids:
            resolved[vuid] = forms
        else:
            unmatched.append(ref)
    if unmatched:
        sys.exit(f"STOP: study-event visitRefs {unmatched} not resolvable to "
                 f"study visits — pipe the ledger uid_map JSON on stdin")

    all_forms = sorted({f for fs in resolved.values() for f in fs})
    print(f"{len(resolved)} visits, {len(all_forms)} scheduled forms")

    # 2. Flowchart group CT terms, by name.
    fc_terms = {}
    for t in get_paged(api, "/ct/terms?codelist_name=Flowchart+Group"):
        n = term_name(t)
        if n:
            fc_terms[n] = t["term_uid"]

    # 3. Library scaffolding: one group + one subgroup for all 360i activities.
    group_uid, g_new = find_or_create_group(api, "activity-groups", ACTIVITY_GROUP_NAME)
    (census["created"] if g_new else census["reused"]).append(ACTIVITY_GROUP_NAME)
    sub_uid, s_new = find_or_create_group(
        api, "activity-sub-groups", ACTIVITY_SUBGROUP_NAME,
        {"activity_groups": [group_uid]},
    )
    (census["created"] if s_new else census["reused"]).append(ACTIVITY_SUBGROUP_NAME)

    # 4. One Activity per scheduled form (find-by-name first).
    existing = {a.get("name"): a for a in get_paged(api, "/concepts/activities/activities")}
    activity_uid_by_form = {}
    for form in all_forms:
        hit = existing.get(form)
        if hit and hit.get("status") in ("Final", "Draft"):
            ensure_final(api, "activities", hit["uid"], hit.get("status"))
            activity_uid_by_form[form] = hit["uid"]
            census["reused"].append(form)
            continue
        created = request("POST", f"{api}/concepts/activities/activities", {
            "name": form,
            "name_sentence_case": form.lower(),
            "library_name": LIBRARY,
            "is_data_collected": True,
            "activity_groupings": [{
                "activity_group_uid": group_uid,
                "activity_subgroup_uid": sub_uid,
            }],
        })
        request("POST", f"{api}/concepts/activities/activities/{created['uid']}/approvals")
        activity_uid_by_form[form] = created["uid"]
        census["created"].append(form)

    # 5. StudyActivity selections (skip ones the study already has).
    current = get_paged(api, f"/studies/{study}/study-activities")
    have = {sa.get("activity", {}).get("uid") for sa in current}
    sa_uid_by_form = {
        sa.get("activity", {}).get("name"): sa["study_activity_uid"]
        for sa in current if sa.get("activity")
    }
    for form in all_forms:
        auid = activity_uid_by_form[form]
        if auid in have:
            continue
        fc_name = FLOWCHART_GROUP_BY_FORM.get(form, DEFAULT_FLOWCHART_GROUP)
        fc_uid = fc_terms.get(fc_name) or fc_terms[DEFAULT_FLOWCHART_GROUP]
        sel = request("POST", f"{api}/studies/{study}/study-activities", {
            "activity_uid": auid,
            "soa_group_term_uid": fc_uid,
            "activity_group_uid": group_uid,
            "activity_subgroup_uid": sub_uid,
        })
        sa_uid_by_form[form] = sel["study_activity_uid"]

    # 6. Schedules: (study_activity, study_visit) pairs from the ODM matrix.
    scheduled = {(s["study_activity_uid"], s["study_visit_uid"])
                 for s in get_paged(api, f"/studies/{study}/study-activity-schedules")}
    for vuid, forms in resolved.items():
        for form in forms:
            sa = sa_uid_by_form.get(form)
            if not sa:
                census["skipped"] += 1
                continue
            if (sa, vuid) in scheduled:
                continue
            request("POST", f"{api}/studies/{study}/study-activity-schedules", {
                "study_activity_uid": sa,
                "study_visit_uid": vuid,
            })
            census["scheduled"] += 1

    print(json.dumps(census, indent=1))


if __name__ == "__main__":
    main()
