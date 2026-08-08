"""Import a 360i study (EDCProtocolToECRF) into OpenStudyBuilder.

Reads the OSB-shaped payload the 360i pipeline persisted to its Postgres
(`ecrf_platform.osb_study_payloads`, written at every study build), populates
OSB through OSB's own APIs in dependency order, and records what it did to
`ecrf_platform.osb_import_ledger` — whose latest row per study is the
360i-study -> OSB-study crosswalk this importer's own upsert reads back.

Order (the proven mockdatajson sequence):
  programme -> project -> units -> sponsor codelists -> study ->
  registry identifiers -> epochs -> visits -> arms ->
  x360i vendor namespace/attributes -> items -> item-groups -> forms ->
  study-event (+FORM_REF wiring = the form x visit matrix)

Honesty rules carried from the payload contract:
  * a payload with no stated epochs gets ONE carrier epoch, recorded in the
    census as importer_scaffolding — OSB requires an epoch per visit, and
    declaring the scaffold is what keeps "the protocol said" and "the
    importer needed" distinguishable.
  * unknown vocabulary (epoch subtype, visit type) STOPs that entity with a
    named census row; nothing is coerced. status='partial' REQUIRES stopped
    rows — the ledger write asserts it.
  * every skipped/created/updated entity lands in exactly one census bucket.

Upsert (same study -> update): the crosswalk row names the OSB study uid and
the refKey->uid map of the previous import. Study selections are diffed by
refKey (PATCH changed / POST new / DELETE removed); ODM library concepts go
through OSB's new-version -> PATCH -> approve cycle, with a content-compare
skip so an unchanged concept is not churned. A locked OSB study REFUSES the
import with an actionable error — never unlocked programmatically.

Env:
  ECRF_PG_DSN, ECRF_TENANT_ID     the 360i Postgres (see ecrf_platform_db.py)
  ECRF_STUDY_ID                   which study to import (or --study)
  API_BASE_URL, STUDYBUILDER_API_TOKEN   the OSB API (see utils/importer.py;
                                  local compose runs OAUTH_ENABLED=False and
                                  needs no token)
  OSB_CLINICAL_PROGRAMME          programme to file projects under (default 360i)
"""

import json

from .functions.utils import load_env
from .mappings import payload_to_osb as mapping
from .utils.ecrf_platform_db import EcrfPlatformDb
from .utils.importer import BaseImporter
from .utils.metrics import Metrics

API_BASE_URL = load_env("API_BASE_URL")
OSB_CLINICAL_PROGRAMME = load_env("OSB_CLINICAL_PROGRAMME", default="360i")

CODELIST_EPOCH_SUBTYPE = "Epoch Sub Type"
CODELIST_EPOCH_TYPE = "Epoch Type"
CODELIST_VISIT_TYPE = "VisitType"
CODELIST_TIMEPOINT_REFERENCE = "Time Point Reference"
CODELIST_VISIT_CONTACT_MODE = "Visit Contact Mode"
CODELIST_UNIT = "Unit"


class ImportCensus:
    """Every payload member's fate on the OSB side, in exactly one bucket."""

    def __init__(self):
        self.created = []
        self.updated = []
        self.unchanged = []
        self.stopped = []
        self.scaffolding = []
        self.carried = []

    def stop(self, kind, ref, reason):
        self.stopped.append({"kind": kind, "ref": ref, "reason": reason})

    def as_dict(self):
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "stopped": self.stopped,
            "importer_scaffolding": self.scaffolding,
            "carried": self.carried,
            "counts": {
                "created": len(self.created),
                "updated": len(self.updated),
                "unchanged": len(self.unchanged),
                "stopped": len(self.stopped),
                "importer_scaffolding": len(self.scaffolding),
                "carried": len(self.carried),
            },
        }

    @property
    def status(self):
        return "partial" if self.stopped else "succeeded"


class Import360i(BaseImporter):
    logging_name = "import_360i"

    def __init__(self, api=None, metrics_inst=None, db=None):
        super().__init__(api=api, metrics_inst=metrics_inst)
        self.db = db or EcrfPlatformDb(log=self.log)
        self.census = ImportCensus()
        # refKey -> OSB uid, per concept type. Written to the ledger; the
        # upsert diff of the NEXT import joins on it.
        self.uid_map = {
            "epochs": {},
            "visits": {},
            "arms": {},
            "forms": {},
            "item_groups": {},
            "items": {},
            "codelists": {},
            "study_events": {},
        }

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def _lookup_ct_term(self, codelist_name, term_name):
        """Case-insensitive sponsor-preferred-name lookup in one codelist."""
        terms = self.api.get_all_from_api(
            f"/ct/terms/names?codelist_name={codelist_name}"
        )
        wanted = term_name.strip().lower()
        for term in terms or []:
            if (term.get("sponsor_preferred_name") or "").strip().lower() == wanted:
                return term["term_uid"]
        return None

    def _lookup_unit(self, unit_name):
        units = self.api.get_all_from_api("/concepts/unit-definitions")
        wanted = unit_name.strip().lower()
        for unit in units or []:
            if (unit.get("name") or "").strip().lower() == wanted:
                return unit["uid"]
        return None

    # ------------------------------------------------------------------
    # Prerequisites: programme, project, units, codelists
    # ------------------------------------------------------------------

    def ensure_programme_and_project(self, payload):
        programmes = self.api.get_all_identifiers(
            self.api.get_all_from_api("/clinical-programmes"),
            identifier="name",
            value="uid",
        )
        programme_uid = programmes.get(OSB_CLINICAL_PROGRAMME)
        if programme_uid is None:
            self.log.info("Creating clinical programme '%s'", OSB_CLINICAL_PROGRAMME)
            res = self.api.simple_post_to_api(
                "/clinical-programmes", {"name": OSB_CLINICAL_PROGRAMME}
            )
            programme_uid = res["uid"]
            self.census.created.append({"kind": "clinical_programme", "ref": OSB_CLINICAL_PROGRAMME})
        else:
            self.census.unchanged.append({"kind": "clinical_programme", "ref": OSB_CLINICAL_PROGRAMME})

        project_number = mapping.project_number_for(payload)
        projects = self.api.get_all_from_api("/projects")
        existing = next(
            (p for p in projects or [] if p.get("project_number") == project_number),
            None,
        )
        if existing is None:
            source = payload.get("source", {})
            name = source.get("projectName") or f"360i project {project_number}"
            self.log.info("Creating project '%s' (%s)", name, project_number)
            self.api.simple_post_to_api(
                "/projects",
                {
                    "project_number": project_number,
                    "name": name,
                    "description": "Imported from 360i (EDCProtocolToECRF)",
                    "clinical_programme_uid": programme_uid,
                },
            )
            self.census.created.append({"kind": "project", "ref": project_number})
        else:
            self.census.unchanged.append({"kind": "project", "ref": project_number})
        return project_number

    def ensure_units(self, payload):
        """Every distinct unit display string the items reference."""
        unit_uid_by_name = {}
        for unit_name in mapping.units_plan(payload):
            uid = self._lookup_unit(unit_name)
            if uid:
                self.census.unchanged.append({"kind": "unit", "ref": unit_name})
            else:
                self.log.info("Creating unit definition '%s'", unit_name)
                res = self.api.simple_post_to_api(
                    "/concepts/unit-definitions",
                    {
                        "name": unit_name,
                        "library_name": "Sponsor",
                        "convertible_unit": False,
                        "display_unit": True,
                        "master_unit": False,
                        "si_unit": False,
                        "us_conventional_unit": False,
                        "ct_units": [],
                        "unit_subsets": [],
                    },
                )
                if res is None:
                    self.census.stop("unit", unit_name, "unit-definition create failed")
                    continue
                uid = res["uid"]
                self.api.simple_approve(f"/concepts/unit-definitions/{uid}/approvals")
                self.census.created.append({"kind": "unit", "ref": unit_name})
            unit_uid_by_name[unit_name.lower()] = uid
        return unit_uid_by_name

    def ensure_codelists(self, payload):
        """Sponsor codelists for the payload's deduplicated option lists.

        Codelist names are content-addressed (X360I_CL_<hash8>), so re-import
        of the same options finds the same codelist — match by name, create
        sponsor codelist + sponsor terms on miss. All payload terms are
        sponsor terms (no C-codes extracted yet, crosswalk §2/§5).
        """
        codelist_by_ref = {}
        existing = self.api.get_all_identifiers(
            self.api.get_all_from_api("/ct/codelists/names"),
            identifier="name",
            value="codelist_uid",
        )
        for plan in mapping.codelists_plan(payload):
            name = plan["name"]
            codelist_uid = existing.get(name)
            if codelist_uid is None:
                self.log.info("Creating sponsor codelist '%s'", name)
                res = self.api.simple_post_to_api(
                    "/ct/codelists",
                    {
                        # OSB's CTCodelistCreateInput requires catalogue_names
                        # (plural array) and is_ordinal — verified against the
                        # live /ct/codelists schema on the 2026.06 instance.
                        "catalogue_names": ["SDTM CT"],
                        "name": name,
                        "submission_value": name,
                        "nci_preferred_name": name,
                        "definition": "360i option list (content-addressed)",
                        "extensible": True,
                        "is_ordinal": False,
                        "sponsor_preferred_name": name,
                        "template_parameter": False,
                        "library_name": "Sponsor",
                        "terms": [],
                    },
                )
                if res is None:
                    self.census.stop("codelist", name, "codelist create failed")
                    continue
                codelist_uid = res["codelist_uid"]
                self.api.simple_approve(f"/ct/codelists/{codelist_uid}/names/approvals")
                self.api.simple_approve(
                    f"/ct/codelists/{codelist_uid}/attributes/approvals"
                )
                self.census.created.append({"kind": "codelist", "ref": name})
            else:
                self.census.unchanged.append({"kind": "codelist", "ref": name})

            terms = []
            existing_terms = self.api.get_all_from_api(
                f"/ct/codelists/{codelist_uid}/terms"
            )
            # /ct/codelists/{uid}/terms returns sponsor_preferred_name as a
            # DIRECT key on this OSB version (verified live: no nested "name"
            # object); older/full term shapes nest it under "name". Support
            # both — an empty match-index makes every re-imported term look
            # new, and the duplicate POSTs then fail the whole import.
            existing_by_name = {}
            for t in existing_terms or []:
                term_name = t.get("sponsor_preferred_name") or (
                    t.get("name") or {}
                ).get("sponsor_preferred_name")
                if term_name:
                    existing_by_name[term_name.lower()] = t
            for term in plan["terms"]:
                found = existing_by_name.get(term["name"].lower())
                if found:
                    terms.append(
                        {
                            "term_uid": found["term_uid"],
                            "name": term["name"],
                            "order": term.get("order"),
                        }
                    )
                    continue
                res = self.api.simple_post_to_api(
                    "/ct/terms",
                    {
                        # OSB's CTTermCreateInput (2026.06) takes catalogue_names
                        # (plural array) and a nested codelists[] with
                        # codelist_uid/submission_value/order — not the old flat
                        # catalogue_name/codelist_uid/code_submission_value.
                        "catalogue_names": ["SDTM CT"],
                        "codelists": [
                            {
                                "codelist_uid": codelist_uid,
                                "submission_value": term["submission_value"],
                                "order": term.get("order"),
                            }
                        ],
                        "nci_preferred_name": term["name"],
                        "definition": term["name"],
                        "sponsor_preferred_name": term["name"],
                        "sponsor_preferred_name_sentence_case": term["name"].lower(),
                        "library_name": "Sponsor",
                    },
                )
                if res is None:
                    self.census.stop(
                        "codelist_term", f"{name}/{term['name']}", "term create failed"
                    )
                    continue
                term_uid = res["term_uid"]
                self.api.approve_item_names_and_attributes(term_uid, "/ct/terms")
                terms.append(
                    {"term_uid": term_uid, "name": term["name"], "order": term.get("order")}
                )
                self.census.created.append(
                    {"kind": "codelist_term", "ref": f"{name}/{term['name']}"}
                )
            codelist_by_ref[name] = {"codelist_uid": codelist_uid, "terms": terms}
            self.uid_map["codelists"][name] = codelist_uid
        return codelist_by_ref

    # ------------------------------------------------------------------
    # Study + structure
    # ------------------------------------------------------------------

    def ensure_study(self, payload, project_number, crosswalk):
        """Create the study, or verify the crosswalked one is usable."""
        if crosswalk:
            study_uid = crosswalk["osb_study_uid"]
            study = self.api.get_all_from_api(f"/studies/{study_uid}")
            if study is None:
                self.census.stop(
                    "study",
                    study_uid,
                    "crosswalk names an OSB study that no longer exists; "
                    "re-import as new by clearing the ledger row",
                )
                return None
            status = (
                study.get("current_metadata", {})
                .get("version_metadata", {})
                .get("study_status")
            )
            if status == "LOCKED":
                self.census.stop(
                    "study",
                    study_uid,
                    "the OSB study is LOCKED; unlock it in OSB (with reason) and re-run "
                    "— this importer never unlocks studies",
                )
                return None
            self.census.unchanged.append({"kind": "study", "ref": study_uid})
            return study_uid

        study_number = mapping.study_number_for(payload)
        # Collision handling: OSB study numbers AND acronyms are unique. Walk
        # both to a free value (deterministic start, stable across re-runs via
        # the crosswalk once created). OSB's uniqueness check spans SOFT-DELETED
        # studies too (verified live: creating with a deleted study's number is
        # refused), so the walk must read both the active and the deleted lists.
        existing_numbers = set()
        existing_acronyms = set()
        studies = self.api.get_all_from_api("/studies") or []
        studies += (
            self.api.get_all_from_api("/studies", params={"deleted": True}) or []
        )
        for s in studies:
            ident = s.get("current_metadata", {}).get("identification_metadata", {})
            if ident.get("study_number"):
                existing_numbers.add(str(ident["study_number"]))
            if ident.get("study_acronym"):
                existing_acronyms.add(str(ident["study_acronym"]))
        candidate = int(study_number)
        while str(candidate) in existing_numbers:
            candidate += 1
        study_number = str(candidate)

        base_acronym = mapping.study_acronym_for(payload)
        study_acronym = base_acronym
        n = 2
        while study_acronym in existing_acronyms:
            # Keep within OSB's 20-char acronym cap while suffixing.
            suffix = str(n)
            study_acronym = base_acronym[: 20 - len(suffix)] + suffix
            n += 1

        body = {
            "study_number": study_number,
            "study_acronym": study_acronym,
            "project_number": project_number,
            "description": f"Imported from 360i study {payload['source']['studyId']} "
            f"(build {payload['source']['buildHash'][:12]})",
        }
        self.log.info("Creating study number %s", study_number)
        res = self.api.simple_post_to_api("/studies", body, "/studies")
        if res is None:
            self.census.stop("study", payload["source"]["studyId"], "study create failed")
            return None
        study_uid = res["uid"]
        self.census.created.append({"kind": "study", "ref": study_uid})

        # Registry identifiers: typed nulls today (the payload's block is the
        # slot, not yet data). PATCH only when anything is non-null.
        reg = payload.get("study", {}).get("registryIdentifiers", {})
        non_null = {k: v for k, v in reg.items() if v is not None}
        if non_null:
            self.api.patch_to_api(
                {
                    "uid": study_uid,
                    "current_metadata": {
                        "identification_metadata": {"registry_identifiers": non_null}
                    },
                },
                "/studies/",
            )
            self.census.created.append({"kind": "registry_identifiers", "ref": study_uid})

        # Study title from the payload's stated title.
        title = payload.get("study", {}).get("officialTitle") or payload.get(
            "study", {}
        ).get("name")
        if title:
            self.api.patch_to_api(
                {
                    "uid": study_uid,
                    "current_metadata": {
                        "study_description": {"study_title": str(title)[:800]}
                    },
                },
                "/studies/",
            )

        # Everything else the protocol stated about the study rides the
        # census as carried — visible, not silently dropped.
        for key in sorted(payload.get("study", {}).get("attributes", {})):
            self.census.carried.append(
                {"kind": "study_attribute", "ref": key, "reason": "no OSB v1 landing zone; in payload.study.attributes"}
            )
        return study_uid

    def ensure_epochs(self, payload, study_uid):
        """Create the payload's epochs (or the declared carrier)."""
        plans, is_scaffolding = mapping.epochs_plan(payload)
        epoch_uid_by_ref = {}
        existing = self.api.get_all_from_api(f"/studies/{study_uid}/study-epochs") or []
        existing_by_name = {e.get("epoch_name", "").lower(): e for e in existing}

        for plan in plans:
            name = plan["name"]
            found = existing_by_name.get(name.lower())
            if found:
                epoch_uid_by_ref[name] = found["uid"]
                self.uid_map["epochs"][name] = found["uid"]
                self.census.unchanged.append({"kind": "epoch", "ref": name})
                continue

            subtype_uid = None
            for candidate in mapping.epoch_subtype_candidates(name):
                subtype_uid = self._lookup_ct_term(CODELIST_EPOCH_SUBTYPE, candidate)
                if subtype_uid:
                    break
            if subtype_uid is None:
                self.census.stop(
                    "epoch",
                    name,
                    f"no Epoch Sub Type term matches '{name}' (tried "
                    f"{mapping.epoch_subtype_candidates(name)}); add a sponsor term "
                    "to the Epoch Sub Type codelist and re-run",
                )
                continue

            preview = self.api.simple_post_to_api(
                f"/studies/{study_uid}/study-epochs/preview",
                {"study_uid": study_uid, "epoch_subtype": subtype_uid},
                "/study-epochs/preview",
            )
            if preview is None:
                self.census.stop("epoch", name, "epoch preview failed")
                continue
            body = {
                "study_uid": study_uid,
                "epoch_subtype": subtype_uid,
                "epoch": preview.get("epoch"),
                "order": preview.get("order"),
                "description": name if not plan["scaffolding"] else (
                    "Carrier epoch created by the 360i importer: OSB requires an epoch "
                    "per visit and the protocol stated none. NOT protocol content."
                ),
            }
            res = self.api.simple_post_to_api(
                f"/studies/{study_uid}/study-epochs", body, "/study-epochs"
            )
            if res is None:
                self.census.stop("epoch", name, "epoch create failed")
                continue
            epoch_uid_by_ref[name] = res["uid"]
            self.uid_map["epochs"][name] = res["uid"]
            if plan["scaffolding"]:
                self.census.scaffolding.append({"kind": "epoch", "ref": name})
            else:
                self.census.created.append({"kind": "epoch", "ref": name})

        # Map each epoch plan's visits to its uid, for the visit pass.
        ref_to_epoch_uid = {}
        for plan in plans:
            uid = epoch_uid_by_ref.get(plan["name"])
            if uid:
                for visit_ref in plan["visit_refs"]:
                    ref_to_epoch_uid[visit_ref] = uid
        return ref_to_epoch_uid, is_scaffolding

    def _current_visits_by_ref(self, study_uid):
        """Snapshot of OSB's current study-visits keyed by our stamped refKey.

        The refKey->uid join comes from the seeded uid_map (the previous
        import's ledger). For each ref still present in OSB, expose the
        payload-plan-shaped compare fields so the pure diff can decide
        patch/unchanged. Refs whose uid vanished from OSB are dropped (the
        diff then treats them as create). On first import the uid_map is empty
        and this returns {} — every plan is a create, as before.
        """
        osb_by_uid = {
            v["uid"]: v
            for v in (self.api.get_all_from_api(f"/studies/{study_uid}/study-visits") or [])
        }
        current = {}
        for ref, uid in self.uid_map["visits"].items():
            v = osb_by_uid.get(uid)
            if v is None:
                continue
            current[ref] = {
                "uid": uid,
                "visit_name": v.get("visit_name"),
                "visit_short_name": v.get("visit_short_name"),
                "visit_number": v.get("visit_number"),
                "unique_visit_number": v.get("unique_visit_number"),
                "time_value": v.get("time_value"),
                "min_window": v.get("min_visit_window_value"),
                "max_window": v.get("max_visit_window_value"),
                "visit_class": v.get("visit_class"),
                # This OSB version nests the type under visit_type (an object
                # with sponsor_preferred_name); older shapes had a flat
                # visit_type_name. Read both — a None here makes every visit
                # look changed and PATCH-storm on each re-import.
                "visit_type_name": v.get("visit_type_name")
                or (v.get("visit_type") or {}).get("sponsor_preferred_name"),
                "is_global_anchor_visit": v.get("is_global_anchor_visit"),
                "description": v.get("description"),
            }
        return current

    def _visit_body(self, plan, study_uid, epoch_uid_by_visit_ref, ctx):
        """Build the create/edit body for one visit plan, resolving the visit
        type and epoch. Returns (body, None) or (None, stop_reason)."""
        # Try each candidate VisitType term name against the seeded CT (the
        # payload's 'scheduled' has no literal "Visit" term in CDISC CT, so it
        # falls back to "Treatment"); STOP only if none of the candidates exist.
        candidates = plan.get("visit_type_names") or [plan.get("visit_type_name")]
        visit_type_uid = None
        for cand in candidates:
            visit_type_uid = self._lookup_ct_term(CODELIST_VISIT_TYPE, cand)
            if visit_type_uid:
                break
        if visit_type_uid is None:
            return None, f"no VisitType term among {candidates}"
        epoch_uid = epoch_uid_by_visit_ref.get(plan["refKey"])
        if epoch_uid is None:
            epoch_uid = next(iter(self.uid_map["epochs"].values()), None)
            if epoch_uid is None:
                return None, "no epoch available"
            self.census.scaffolding.append(
                {
                    "kind": "visit_epoch_assignment",
                    "ref": plan["refKey"],
                    "reason": "epoch join unverified; filed under the first epoch",
                }
            )
        body = {
            "study_epoch_uid": epoch_uid,
            "visit_type": {"term_uid": visit_type_uid},
            "time_reference": {"term_uid": ctx["anchor_ref_uid"]},
            "time_value": plan["time_value"],
            "time_unit_uid": ctx["day_unit_uid"],
            "visit_class": plan["visit_class"],
            "is_global_anchor_visit": plan["is_global_anchor_visit"],
            "show_visit": True,
            "min_visit_window_value": plan["min_window"],
            "max_visit_window_value": plan["max_window"],
            "visit_window_unit_uid": ctx["day_unit_uid"],
            "description": plan.get("description"),
            "visit_contact_mode": {"term_uid": ctx["contact_uid"]},
        }
        if plan["visit_class"] == "MANUALLY_DEFINED_VISIT":
            # Protocol-stated names, never OSB's derived "Visit N" (the
            # visit-naming doctrine rides the payload; honor it here).
            body["visit_name"] = plan["visit_name"]
            body["visit_short_name"] = plan["visit_short_name"]
            body["visit_number"] = plan["visit_number"]
            body["unique_visit_number"] = plan["unique_visit_number"]
        return body, None

    def ensure_visits(self, payload, study_uid, epoch_uid_by_visit_ref):
        """Reconcile the visit calendar to the payload (create/patch/delete).

        First import: uid_map empty -> the diff is all-create (prior behavior).
        Re-import of an edited payload: refKey diff drives targeted PATCH of
        changed windows/timing/names, POST of new visits, DELETE of removed
        ones — the create-only census-as-unchanged gap this closes.
        """
        anchor_ref_uid = self._lookup_ct_term(
            CODELIST_TIMEPOINT_REFERENCE, "Global anchor visit"
        )
        contact_uid = self._lookup_ct_term(CODELIST_VISIT_CONTACT_MODE, "On Site Visit")
        day_unit_uid = self._lookup_unit("day")
        if anchor_ref_uid is None or day_unit_uid is None:
            self.census.stop(
                "visits",
                "*",
                "OSB standard terms missing (Global anchor visit / day unit) — "
                "run the standard-codelist imports first",
            )
            return
        ctx = {
            "anchor_ref_uid": anchor_ref_uid,
            "contact_uid": contact_uid,
            "day_unit_uid": day_unit_uid,
        }

        current_by_ref = self._current_visits_by_ref(study_uid)
        diff = mapping.visit_diff(payload, current_by_ref, epoch_uid_by_visit_ref)

        for plan in diff["stop"]:
            self.census.stop("visit", plan["refKey"], plan["stop"])

        for plan in diff["create"]:
            body, stop = self._visit_body(plan, study_uid, epoch_uid_by_visit_ref, ctx)
            if stop:
                self.census.stop("visit", plan["refKey"], stop)
                continue
            res = self.api.simple_post_to_api(
                f"/studies/{study_uid}/study-visits", body, "/study-visits"
            )
            if res is None:
                self.census.stop("visit", plan["refKey"], "visit create failed")
                continue
            self.uid_map["visits"][plan["refKey"]] = res["uid"]
            self.census.created.append({"kind": "visit", "ref": plan["refKey"]})
            if plan["day_missing"]:
                self.census.carried.append(
                    {
                        "kind": "visit_day",
                        "ref": plan["refKey"],
                        "reason": "the protocol stated no numeric day; imported at day 0 "
                        "of the anchor reference — set the timing in OSB",
                    }
                )

        for entry in diff["patch"]:
            plan = entry["plan"]
            body, stop = self._visit_body(plan, study_uid, epoch_uid_by_visit_ref, ctx)
            if stop:
                self.census.stop("visit", plan["refKey"], stop)
                continue
            body["uid"] = entry["uid"]
            res = self.api.patch_to_api(body, f"/studies/{study_uid}/study-visits")
            if res is None:
                self.census.stop("visit", plan["refKey"], "visit patch failed")
                continue
            self.uid_map["visits"][plan["refKey"]] = entry["uid"]
            self.census.updated.append(
                {"kind": "visit", "ref": plan["refKey"], "changed": entry["changed"]}
            )

        for entry in diff["unchanged"]:
            self.uid_map["visits"][entry["ref"]] = entry["uid"]
            self.census.unchanged.append({"kind": "visit", "ref": entry["ref"]})

        for entry in diff["delete"]:
            ok = self.api.simple_delete(
                f"/studies/{study_uid}/study-visits/{entry['uid']}", "/study-visits"
            )
            if not ok:
                self.census.stop("visit", entry["ref"], "visit delete failed")
                continue
            self.uid_map["visits"].pop(entry["ref"], None)
            self.census.updated.append({"kind": "visit_removed", "ref": entry["ref"]})

    def _current_arms_by_ref(self, study_uid):
        """OSB study-arms keyed by name (the arm's stable ref), for the diff."""
        osb_by_uid = {}
        for a in self.api.get_all_from_api(f"/studies/{study_uid}/study-arms") or []:
            osb_by_uid[a.get("arm_uid") or a.get("uid")] = a
        current = {}
        for ref, uid in self.uid_map["arms"].items():
            a = osb_by_uid.get(uid)
            if a is None:
                continue
            current[ref] = {
                "uid": uid,
                "name": a.get("name"),
                "short_name": a.get("short_name"),
                "description": a.get("description"),
            }
        return current

    def ensure_arms(self, payload, study_uid):
        """Reconcile study-arms to the payload (create/patch/delete). Genuine
        arms only; non-arm group classes ride the census as carried."""
        current_by_ref = self._current_arms_by_ref(study_uid)
        diff = mapping.arm_diff(payload, current_by_ref)

        for plan in diff["create"]:
            body = {
                "name": plan["name"],
                "short_name": plan["short_name"],
                "description": plan.get("description"),
            }
            res = self.api.simple_post_to_api(
                f"/studies/{study_uid}/study-arms", body, "/study-arms"
            )
            if res is None:
                self.census.stop("arm", plan["name"], "arm create failed")
                continue
            self.uid_map["arms"][plan["name"]] = res.get("arm_uid") or res.get("uid")
            self.census.created.append({"kind": "arm", "ref": plan["name"]})

        for entry in diff["patch"]:
            plan = entry["plan"]
            body = {
                "uid": entry["uid"],
                "name": plan["name"],
                "short_name": plan["short_name"],
                "description": plan.get("description"),
            }
            res = self.api.patch_to_api(body, f"/studies/{study_uid}/study-arms")
            if res is None:
                self.census.stop("arm", plan["name"], "arm patch failed")
                continue
            self.uid_map["arms"][plan["name"]] = entry["uid"]
            self.census.updated.append(
                {"kind": "arm", "ref": plan["name"], "changed": entry["changed"]}
            )

        for entry in diff["unchanged"]:
            self.uid_map["arms"][entry["ref"]] = entry["uid"]
            self.census.unchanged.append({"kind": "arm", "ref": entry["ref"]})

        for entry in diff["delete"]:
            ok = self.api.simple_delete(
                f"/studies/{study_uid}/study-arms/{entry['uid']}", "/study-arms"
            )
            if not ok:
                self.census.stop("arm", entry["ref"], "arm delete failed")
                continue
            self.uid_map["arms"].pop(entry["ref"], None)
            self.census.updated.append({"kind": "arm_removed", "ref": entry["ref"]})

        for gc in payload.get("nonArmGroupClasses", []):
            self.census.carried.append(
                {
                    "kind": "group_class",
                    "ref": gc["name"],
                    "reason": f"groupClassTypeName '{gc['groupClassTypeName']}' is not an "
                    "arm (doctrine: never presented to OSB as StudyArm)",
                }
            )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, study_id=None):
        study_id = study_id or load_env("ECRF_STUDY_ID")
        self.log.info("Importing 360i study '%s' into OSB", study_id)

        record = self.db.read_latest_payload(study_id)
        if record is None:
            raise SystemExit(
                f"No OSB payload for study '{study_id}' in ecrf_platform "
                f"(tenant '{self.db.tenant_id}'). Build the study in 360i first."
            )
        payload = record["payload"]
        if record["census"]["unmapped"] != 0:
            raise SystemExit(
                f"Payload {record['payload_hash'][:12]} claims {record['census']['unmapped']} "
                "unmapped members — the table CHECK should have refused this; not importing."
            )
        self.log.info(
            "Payload %s (build %s): %d visits, %d forms",
            record["payload_hash"][:12],
            record["build_hash"][:12],
            len(payload.get("visits", [])),
            len(payload.get("odm", {}).get("forms", [])),
        )

        crosswalk = self.db.read_current_crosswalk(study_id)
        if crosswalk:
            # The hash-gate no-op is only valid if the crosswalked OSB study
            # STILL EXISTS. A crosswalk can outlive its OSB study (the instance
            # was wiped/re-seeded, or the study deleted) — in that case an
            # identical payload must be RE-IMPORTED fresh, not reported as
            # "already done". Verify existence before trusting the gate.
            # NB: a direct GET /studies/{uid} still returns 200 for a
            # SOFT-DELETED study (verified live), so existence must be checked
            # against the studies LIST, which excludes deleted ones.
            osb_uid = crosswalk.get("osb_study_uid")
            active_studies = self.api.get_all_from_api("/studies") or []
            osb_study_exists = bool(
                osb_uid and any(s.get("uid") == osb_uid for s in active_studies)
            )
            if not osb_study_exists:
                self.log.info(
                    "Crosswalk names OSB study '%s' which no longer exists — "
                    "re-importing the payload fresh.",
                    osb_uid,
                )
                crosswalk = None
            elif (
                crosswalk["payload_hash"] == record["payload_hash"]
                and crosswalk.get("status") == "succeeded"
            ):
                # Only a SUCCEEDED import may no-op: a partial one has named
                # stopped rows — the whole point of re-running is to finish them.
                self.log.info(
                    "Payload %s already imported (import %s) — nothing to do.",
                    record["payload_hash"][:12],
                    crosswalk["import_id"],
                )
                return crosswalk
            else:
                self.log.info(
                    "Study previously imported as OSB study '%s' — updating in place.",
                    crosswalk["osb_study_uid"],
                )
                # Seed the uid map with the previous import's joins so unchanged
                # entities resolve without re-creation.
                for kind, refs in (crosswalk.get("uid_map") or {}).items():
                    if kind in self.uid_map and isinstance(refs, dict):
                        self.uid_map[kind].update(refs)

        project_number = self.ensure_programme_and_project(payload)
        unit_uid_by_name = self.ensure_units(payload)
        codelist_by_ref = self.ensure_codelists(payload)

        study_uid = self.ensure_study(payload, project_number, crosswalk)
        if study_uid is None:
            self._finish(study_id, record, None, project_number)
            return None

        epoch_uid_by_visit_ref, _scaffolded = self.ensure_epochs(payload, study_uid)
        self.ensure_visits(payload, study_uid, epoch_uid_by_visit_ref)
        self.ensure_arms(payload, study_uid)

        # ODM (forms/item-groups/items + the form x visit matrix) — B3.
        self.ensure_odm(payload, study_uid, codelist_by_ref, unit_uid_by_name)

        return self._finish(study_id, record, study_uid, project_number)

    # ------------------------------------------------------------------
    # ODM: vendor namespace/attributes, items, item-groups, forms,
    # study-event + FORM_REF wiring (the form x visit matrix)
    # ------------------------------------------------------------------

    def ensure_vendor_namespace(self):
        """The x360i vendor namespace + its attribute concepts, once per
        instance. Attribute VALUES are attached per-entity at create time."""
        namespaces = self.api.get_all_from_api("/odms/vendor-namespaces") or []
        ns = next(
            (n for n in namespaces if n.get("prefix") == mapping.X360I_NAMESPACE["prefix"]),
            None,
        )
        if ns is None:
            res = self.api.simple_post_to_api(
                "/odms/vendor-namespaces", dict(mapping.X360I_NAMESPACE)
            )
            if res is None:
                self.census.stop("vendor_namespace", "x360i", "namespace create failed")
                return {}
            ns = res
            self.api.simple_approve(f"/odms/vendor-namespaces/{ns['uid']}/approvals")
            self.census.created.append({"kind": "vendor_namespace", "ref": "x360i"})
        else:
            self.census.unchanged.append({"kind": "vendor_namespace", "ref": "x360i"})

        existing_attrs = self.api.get_all_from_api("/odms/vendor-attributes") or []
        by_name = {
            a.get("name"): a
            for a in existing_attrs
            if (a.get("vendor_namespace") or {}).get("uid") == ns["uid"]
        }
        attr_uid_by_name = {}
        for spec in mapping.X360I_ATTRIBUTES:
            found = by_name.get(spec["name"])
            if found:
                attr_uid_by_name[spec["name"]] = found["uid"]
                continue
            res = self.api.simple_post_to_api(
                "/odms/vendor-attributes",
                {
                    "name": spec["name"],
                    "compatible_types": spec["compatible_types"],
                    "data_type": spec["data_type"],
                    "vendor_namespace_uid": ns["uid"],
                },
            )
            if res is None:
                self.census.stop(
                    "vendor_attribute", spec["name"], "attribute create failed"
                )
                continue
            self.api.simple_approve(f"/odms/vendor-attributes/{res['uid']}/approvals")
            attr_uid_by_name[spec["name"]] = res["uid"]
            self.census.created.append(
                {"kind": "vendor_attribute", "ref": spec["name"]}
            )
        return attr_uid_by_name

    def _entity_vendor_attributes(self, attr_uids, ref_key, ext_json=None, field_type=None):
        """The x360i vendor_attributes array for one ODM entity's POST body."""
        attrs = []
        if attr_uids.get("refKey"):
            attrs.append({"uid": attr_uids["refKey"], "value": ref_key})
        if field_type and attr_uids.get("fieldType"):
            attrs.append({"uid": attr_uids["fieldType"], "value": field_type})
        if ext_json and attr_uids.get("ext"):
            attrs.append({"uid": attr_uids["ext"], "value": ext_json})
        return attrs

    def ensure_odm(self, payload, study_uid, codelist_by_ref, unit_uid_by_name):
        """Items -> item-groups -> forms -> ONE study-event per visit, whose
        FORM_REFs ARE the form x visit matrix's form axis.

        Concepts are matched by OID (= our refKey — OSB keeps the OID we
        supply), so a re-import finds its own concepts instead of duplicating
        them. For an EDITED payload, a matched concept whose canonical content
        changed is reconciled through OSB's version -> PATCH -> approve cycle;
        a content-identical match is left untouched (censused unchanged) so an
        unchanged concept is never version-churned. The `x360i:content` vendor
        attribute stamps the content sha at create/patch time, so the next
        import's content-compare needs only OSB state, not the payload hash.
        """
        attr_uids = self.ensure_vendor_namespace()
        odm = payload.get("odm", {})
        # OSB enforces one-item-one-group and rejects an item-ref batch atomically
        # if any item is already claimed. Resolve each item's single canonical
        # owner group up front; duplicate (item, group) references are censused as
        # `carried` so a catch-all group re-listing domain fields can't silently
        # drop the items that were unique to it (see payload_to_osb.item_group_ownership).
        ownership = mapping.item_group_ownership(odm)
        item_owner = ownership["owner"]
        for dup in ownership["carried"]:
            self.census.carried.append(
                {
                    "kind": "item_ref",
                    "ref": f"{dup['group']}/{dup['item']}",
                    "reason": (
                        f"item {dup['item']} owned by group {dup['owner']} "
                        f"(OSB one-item-one-group); not re-wired into {dup['group']}"
                    ),
                }
            )

        def _find_by_oid(path, oid):
            items = self.api.get_all_from_api(
                path,
                params={
                    "filters": json.dumps({"oid": {"v": [oid], "op": "eq"}}),
                    "page_number": 1,
                    "page_size": 0,
                },
            )
            return items[0] if items else None

        def _stamp_content(body, sha):
            """Add the content sha as an x360i:content vendor attribute so the
            next import can content-compare from OSB state alone."""
            if attr_uids.get("content"):
                body.setdefault("vendor_attributes", []).append(
                    {"uid": attr_uids["content"], "value": sha}
                )

        def _existing_content_sha(existing):
            """Read the x360i:content sha a prior import stamped, if any."""
            for attr in existing.get("vendor_attributes", []) or []:
                if attr.get("name") == "content" or attr.get("uid") == attr_uids.get(
                    "content"
                ):
                    return attr.get("value")
            return None

        def _create_and_approve(kind, path, body, ref, defer_approve=False):
            # defer_approve=True leaves the new element in Draft so the caller
            # can attach child refs (ITEM_GROUP_REF / ITEM_REF / FORM_REF) —
            # OSB only accepts those on a Draft element — then approve via
            # _approve(). Otherwise approve immediately (leaf concepts).
            res = self.api.simple_post_to_api(path, body)
            if res is None:
                self.census.stop(kind, ref, f"{kind} create failed")
                return None
            if not defer_approve:
                self.api.simple_approve(f"{path}/{res['uid']}/approvals")
            self.census.created.append({"kind": kind, "ref": ref})
            return res

        def _approve(path, uid):
            self.api.simple_approve(f"{path}/{uid}/approvals")

        def _patch_through_version(kind, path, existing, body, ref):
            """OSB version -> PATCH -> approve for an EDITED concept.

            An approved concept must be drafted (new version) before it accepts
            a PATCH, then re-approved. Content-compare has already decided this
            concept changed; this just executes the lifecycle. Returns the uid
            on success, None on failure (census stopped)."""
            uid = existing["uid"]
            status = (existing.get("status") or "").lower()
            if status not in ("draft",):
                if self.api.simple_post_to_api(f"{path}/{uid}/versions", {}) is None:
                    self.census.stop(kind, ref, f"{kind} new-version (draft) failed")
                    return None
            body = dict(body)
            body["uid"] = uid
            # OSB's ODM *Patch* inputs require these fields that the *Post*
            # inputs don't: a change_description and the (empty) vendor-element
            # arrays. Supply them so the PATCH validates.
            body.setdefault("change_description", "360i re-import: content changed")
            body.setdefault("vendor_elements", [])
            body.setdefault("vendor_element_attributes", [])
            if self.api.patch_to_api(body, path) is None:
                self.census.stop(kind, ref, f"{kind} patch failed")
                return None
            self.api.simple_approve(f"{path}/{uid}/approvals")
            self.census.updated.append({"kind": kind, "ref": ref})
            return uid

        def _reconcile(kind, path, ref, body_no_content, defer_approve=False):
            """Create / patch-through-version / skip one ODM concept by OID.

            body_no_content is the desired body WITHOUT the content stamp; the
            sha is computed over it, compared against the existing stamp, and
            appended before the write. Returns (uid, created_bool) or (None, _).

            defer_approve=True: when the concept is newly CREATED, leave it in
            Draft (the caller must attach child refs then call _approve). Only
            the create path can defer — a patched-through-version concept is
            re-approved by _patch_through_version as before.
            """
            sha = mapping.content_sha(body_no_content)
            existing = _find_by_oid(path, ref)
            if existing is None:
                body = dict(body_no_content)
                _stamp_content(body, sha)
                res = _create_and_approve(kind, path, body, ref, defer_approve=defer_approve)
                return (res["uid"], True) if res else (None, False)
            if _existing_content_sha(existing) == sha:
                self.census.unchanged.append({"kind": kind, "ref": ref})
                return existing["uid"], False
            body = dict(body_no_content)
            _stamp_content(body, sha)
            uid = _patch_through_version(kind, path, existing, body, ref)
            return (uid, False) if uid else (None, False)

        # Items (all groups, all forms), then groups, then forms — leaf first.
        for form in odm.get("forms", []):
            for group in form.get("itemGroups", []):
                for item in group.get("items", []):
                    body = mapping.odm_item_body(item, codelist_by_ref, unit_uid_by_name)
                    body["vendor_attributes"] = self._entity_vendor_attributes(
                        attr_uids,
                        item["refKey"],
                        ext_json=mapping.vendor_ext_value(item),
                        field_type=item.get("datatypeHint"),
                    )
                    if item.get("prompt"):
                        body["translated_texts"] = [
                            {"text_type": "Question", "language": "en", "text": item["prompt"]}
                        ]
                    uid, _created = _reconcile("item", "/odms/items", item["refKey"], body)
                    if uid:
                        self.uid_map["items"][item["refKey"]] = uid

        # Item groups + their item refs.
        for form in odm.get("forms", []):
            for group in form.get("itemGroups", []):
                body = {
                    "name": group["name"][:200],
                    "oid": group["refKey"],
                    "repeating": "no",
                    "translated_texts": [
                        {"text_type": "Description", "language": "en", "text": group.get("description") or group["name"]}
                    ],
                    "sdtm_domain_uids": [],
                    "vendor_attributes": self._entity_vendor_attributes(
                        attr_uids, group["refKey"]
                    ),
                }
                # Create as Draft: ITEM_REFs attach only to a Draft element,
                # so defer approval until after they're wired.
                uid, created = _reconcile(
                    "item_group", "/odms/item-groups", group["refKey"], body,
                    defer_approve=True,
                )
                if uid is None:
                    continue
                self.uid_map["item_groups"][group["refKey"]] = uid
                # (Re)wire the item refs only when the group was created or
                # patched — an unchanged group keeps its existing refs.
                if not created:
                    continue
                group_uid = uid
                # Wire ONLY the items this group canonically owns — a duplicate
                # reference (already censused `carried` above) would make OSB
                # reject the whole batch atomically.
                group_ref = group["refKey"]
                item_refs = [
                    {
                        "uid": self.uid_map["items"][item["refKey"]],
                        "order_number": item["orderNumber"],
                        "mandatory": "yes" if item.get("mandatory") else "no",
                        "key_sequence": None,
                        "method_oid": None,
                        "imputation_method_oid": None,
                        "role": None,
                        "role_codelist_oid": None,
                        "collection_exception_condition_oid": None,
                        "vendor": {"attributes": []},
                    }
                    for item in group.get("items", [])
                    if item["refKey"] in self.uid_map["items"]
                    and item_owner.get(item["refKey"]) == group_ref
                ]
                if item_refs:
                    # A failed batch would silently orphan every item in this
                    # group (verified live: item-ref POST is atomic). Check the
                    # result and STOP-with-census rather than approve an empty
                    # group — the no-data-loss guarantee must never fail silently.
                    if (
                        self.api.simple_post_to_api(
                            f"/odms/item-groups/{group_uid}/items", item_refs
                        )
                        is None
                    ):
                        self.census.stop(
                            "item_group_items",
                            group_ref,
                            f"item-ref batch POST failed ({len(item_refs)} items) "
                            "— group left unwired to avoid a silent empty group",
                        )
                        continue
                # Approve AFTER item refs are attached (Draft -> Final).
                _approve("/odms/item-groups", group_uid)

        # Forms + their item-group refs.
        for form in odm.get("forms", []):
            body = {
                "name": form["name"][:200],
                "oid": form["refKey"],
                "repeating": "no",
                "translated_texts": [
                    {"text_type": "Description", "language": "en", "text": form.get("description") or form["name"]}
                ],
                "vendor_attributes": self._entity_vendor_attributes(
                    attr_uids, form["refKey"], ext_json=mapping.vendor_ext_value(form)
                ),
            }
            # Create as Draft: ITEM_GROUP_REFs attach only to a Draft element,
            # so defer approval until after they're wired.
            uid, created = _reconcile(
                "form", "/odms/forms", form["refKey"], body, defer_approve=True
            )
            if uid is None:
                continue
            self.uid_map["forms"][form["refKey"]] = uid
            if not created:
                continue
            form_uid = uid
            group_refs = [
                {
                    "uid": self.uid_map["item_groups"][group["refKey"]],
                    "order_number": group["orderNumber"],
                    "mandatory": "yes",
                    "collection_exception_condition_oid": None,
                    "vendor": {"attributes": []},
                }
                for group in form.get("itemGroups", [])
                if group["refKey"] in self.uid_map["item_groups"]
            ]
            if group_refs:
                self.api.simple_post_to_api(
                    f"/odms/forms/{form_uid}/item-groups", group_refs
                )
            # Approve AFTER item-group refs are attached (Draft -> Final).
            _approve("/odms/forms", form_uid)

        # ONE study-event PER VISIT — ODM's own semantics (a StudyEventDef IS
        # a visit; crosswalk §8: the form x visit anchor is StudyEvent
        # -FORM_REF-> Form, NEVER StudyActivitySchedule). The visit refKey
        # rides the OID itself (`SE.360I.<studyId>.<visitRef>`) because
        # OdmStudyEventPostInput carries no vendor_attributes — and OSB
        # preserves supplied OIDs, so the join survives to Leg C.
        matrix_by_visit = {}
        for assignment in payload.get("formVisitMatrix", []):
            matrix_by_visit.setdefault(assignment["visitRef"], []).append(assignment)

        study_id = payload["source"]["studyId"]
        for visit in payload.get("visits", []):
            visit_ref = visit["refKey"]
            assignments = matrix_by_visit.get(visit_ref, [])
            if not assignments:
                continue
            event_oid = f"SE.360I.{study_id}.{visit_ref}"
            existing = _find_by_oid("/odms/study-events", event_oid)
            if existing:
                event_uid = existing["uid"]
                self.uid_map["study_events"][visit_ref] = event_uid
                self.census.unchanged.append({"kind": "study_event", "ref": event_oid})
                continue
            # Create the study-event as a DRAFT (do NOT approve yet): FORM_REFs
            # can only be attached while the ODM element is in Draft. OSB
            # rejects `POST .../forms` on an approved element ("ODM element is
            # not in Draft"), so the order must be create -> wire forms ->
            # approve.
            res = self.api.simple_post_to_api(
                "/odms/study-events",
                {
                    "name": visit["name"][:200],
                    "oid": event_oid,
                    "description": f"Visit '{visit['name']}' imported from 360i study "
                    f"{study_id}; FORM_REFs are the visit's scheduled forms.",
                },
            )
            if res is None:
                self.census.stop("study_event", event_oid, "study_event create failed")
                continue
            event_uid = res["uid"]
            self.uid_map["study_events"][visit_ref] = event_uid
            self.census.created.append({"kind": "study_event", "ref": event_oid})

            form_refs = []
            for assignment in sorted(
                assignments, key=lambda a: a.get("ordinal") or 0
            ):
                form_uid = self.uid_map["forms"].get(assignment["formRef"])
                if form_uid is None:
                    self.census.stop(
                        "form_ref",
                        f"{visit_ref}/{assignment['formRef']}",
                        "assignment names a form that did not import",
                    )
                    continue
                form_refs.append(
                    {
                        "uid": form_uid,
                        "order_number": assignment.get("ordinal") or len(form_refs) + 1,
                        "mandatory": "yes" if assignment.get("required", True) else "no",
                        "locked": "No",
                        "collection_exception_condition_oid": None,
                    }
                )
            if form_refs:
                self.api.simple_post_to_api(
                    f"/odms/study-events/{event_uid}/forms", form_refs
                )
                self.census.created.append(
                    {"kind": "form_refs", "ref": event_oid, "count": len(form_refs)}
                )
            # Approve the study-event AFTER its FORM_REFs are attached.
            self.api.simple_approve(f"/odms/study-events/{event_uid}/approvals")
            # Assignment provenance (_derivedFrom, _conditionalNote, ...) has
            # no FORM_REF slot — carried, per assignment, never silently lost.
            derived = [
                a for a in assignments
                if any(k.startswith("_") for k in a.keys())
            ]
            if derived:
                self.census.carried.append(
                    {
                        "kind": "assignment_provenance",
                        "ref": event_oid,
                        "reason": f"{len(derived)} assignment(s) carry _derivedFrom/"
                        "_conditionalNote provenance with no FORM_REF slot; "
                        "verbatim in the payload for Leg C",
                    }
                )

    def _finish(self, study_id, record, study_uid, project_number):
        census = self.census.as_dict()
        status = self.census.status if study_uid else "failed"
        import_id = self.db.write_import_ledger(
            study_id=study_id,
            payload_hash=record["payload_hash"],
            osb_study_uid=study_uid,
            osb_project_number=project_number,
            status=status,
            census=census,
            uid_map=self.uid_map,
        )
        self.log.info(
            "Import %s finished: %s (created %d, unchanged %d, stopped %d, scaffolding %d)",
            import_id,
            status,
            census["counts"]["created"],
            census["counts"]["unchanged"],
            census["counts"]["stopped"],
            census["counts"]["importer_scaffolding"],
        )
        for row in census["stopped"]:
            self.log.warning("STOPPED %s '%s': %s", row["kind"], row["ref"], row["reason"])
        return {
            "import_id": import_id,
            "osb_study_uid": study_uid,
            "status": status,
            "census": census,
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="run_import_360i.py")
    parser.add_argument("--study", help="360i study id (else ECRF_STUDY_ID)")
    args = parser.parse_args()

    metr = Metrics()
    importer = Import360i(metrics_inst=metr)
    try:
        importer.run(study_id=args.study)
    finally:
        importer.db.close()
    metr.print()


if __name__ == "__main__":
    main()
