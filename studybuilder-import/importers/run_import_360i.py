"""
# Patched 20260826-draftgate: an already-Draft parent is not re-drafted.
# Patched 20260826-stealfix: stale item->group attachments are detached
# from their pre-1.9 holders before the owner write retries.
# Patched 20260826-patchfix: ODM Patch-input completeness + authoritative
# relationship writes. See tmp-ora-wire-20260826/patch-360i-importer.py.
Import a 360i study (EDCProtocolToECRF) into OpenStudyBuilder.

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

import base64
import gzip
import html
import json
import re
import sys

from .functions.utils import load_env
from .mappings import payload_to_osb as mapping
from .utils.ecrf_platform_db import EcrfPlatformDb, IMPORTER_VERSION
from .utils.importer import BaseImporter
from .utils.mapping_authority import assert_unsafe_legacy_mutation_allowed
from .utils.metrics import Metrics

OSB_CLINICAL_PROGRAMME = load_env("OSB_CLINICAL_PROGRAMME", default="360i")
CARRIER_COMPRESSION_PREFIX = "gzip+base64:"
CARRIER_CHUNK_PREFIX = "chunk:"
CARRIER_COMPRESSION_THRESHOLD = 128 * 1024
CARRIER_EPOCH_DESCRIPTION = (
    "Carrier epoch created by the 360i importer: OSB requires an epoch per visit "
    "and the protocol stated none. NOT protocol content."
)

CODELIST_EPOCH_SUBTYPE = "Epoch Sub Type"
CODELIST_EPOCH_TYPE = "Epoch Type"
CODELIST_VISIT_TYPE = "VisitType"
CODELIST_TIMEPOINT_REFERENCE = "Time Point Reference"
CODELIST_VISIT_CONTACT_MODE = "Visit Contact Mode"
CODELIST_UNIT = "Unit"
CODELIST_STUDY_TYPE = "Study Type"
CODELIST_TRIAL_PHASE = "Trial Phase"
CODELIST_CONTROL_TYPE = "Control Type"
CODELIST_INTERVENTION_MODEL = "Intervention Model"
CODELIST_TRIAL_BLINDING_SCHEMA = "Trial Blinding Schema"
CODELIST_SEX_OF_PARTICIPANTS = "Sex of Participants"
CODELIST_FLOWCHART_GROUP = "Flowchart Group"
CODELIST_OBJECTIVE_LEVEL = "Objective Level"
CODELIST_ENDPOINT_LEVEL = "Endpoint Level"
CODELIST_CRITERIA_TYPE = "Criteria Type"

NATIVE_CODELIST_UIDS = {
    CODELIST_STUDY_TYPE: "C99077",
    CODELIST_TRIAL_PHASE: "C66737",
    CODELIST_CONTROL_TYPE: "C66785",
    CODELIST_INTERVENTION_MODEL: "C99076",
    CODELIST_TRIAL_BLINDING_SCHEMA: "C66735",
    CODELIST_SEX_OF_PARTICIPANTS: "C66732",
}

NATIVE_TERM_ALIASES = {
    "study_type_code": {
        "interventional": "Interventional",
        "observational": "Observational Study",
    },
    "trial_phase_code": {
        "i": "Phase 1",
        "phase i": "Phase 1",
        "ii": "Phase 2",
        "phase ii": "Phase 2",
        "iii": "Phase 3",
        "phase iii": "Phase 3",
        "iv": "Phase 4",
        "phase iv": "Phase 4",
    },
    "control_type_code": {
        "placebo": "Placebo",
        "active": "Active",
        "historical": "Historical",
        "uncontrolled": "Uncontrolled",
    },
    "intervention_model_code": {
        "parallel": "Parallel",
        "crossover": "Crossover",
        "cross over": "Crossover",
        "factorial": "Factorial",
        "single group": "Single Group",
        "sequential": "Sequential",
    },
    "trial_blinding_schema_code": {
        "open label": "Open Label",
        "single blind": "Single Blind",
        "double blind": "Double Blind",
        "triple blind": "Triple Blind",
    },
    "sex_of_participants_code": {
        "both": "Both",
        "male": "Male",
        "female": "Female",
    },
}


def _encode_carrier(value):
    """Keep small JSON readable; deterministically compress large carriers."""
    if not value or value.startswith(
        (CARRIER_COMPRESSION_PREFIX, CARRIER_CHUNK_PREFIX)
    ):
        return value
    raw = value.encode("utf-8")
    if len(raw) <= CARRIER_COMPRESSION_THRESHOLD:
        return value
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    return CARRIER_COMPRESSION_PREFIX + base64.b64encode(compressed).decode("ascii")


def _chunk_carrier(value, available_entities):
    """Distribute one large encoded carrier across bounded ODM entities."""
    encoded = _encode_carrier(value)
    if not encoded or len(encoded) <= CARRIER_COMPRESSION_THRESHOLD:
        return [encoded] if encoded else []
    chunk_size = (len(encoded) + available_entities - 1) // available_entities
    chunks = [
        encoded[offset : offset + chunk_size]
        for offset in range(0, len(encoded), chunk_size)
    ]
    total = len(chunks)
    return [
        f"{CARRIER_CHUNK_PREFIX}{index:04d}/{total:04d}:{chunk}"
        for index, chunk in enumerate(chunks, start=1)
    ]


class ImportCensus:
    """Every payload member's fate on the OSB side, in exactly one bucket."""

    def __init__(self):
        self.created = []
        self.updated = []
        self.unchanged = []
        self.stopped = []
        self.scaffolding = []
        self.carried = []
        self.release_blockers = []

    def stop(self, kind, ref, reason):
        self.stopped.append({"kind": kind, "ref": ref, "reason": reason})

    def block_release(self, kind, ref, reason):
        self.release_blockers.append({"kind": kind, "ref": ref, "reason": reason})

    def as_dict(self):
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "stopped": self.stopped,
            "importer_scaffolding": self.scaffolding,
            "carried": self.carried,
            "release_blockers": self.release_blockers,
            "counts": {
                "created": len(self.created),
                "updated": len(self.updated),
                "unchanged": len(self.unchanged),
                "stopped": len(self.stopped),
                "importer_scaffolding": len(self.scaffolding),
                "carried": len(self.carried),
                "release_blockers": len(self.release_blockers),
            },
        }

    @property
    def status(self):
        # 2026-08-23: release blockers are retained-by-design registry attributes
        # (recorded in full in the census); only stopped entities mean the
        # import did not fully apply. This restores the documented contract
        # ("status='partial' REQUIRES stopped rows").
        return "partial" if self.stopped else "succeeded"



# Fields that OSB requires on an ODM *Patch* input but not on the matching
# *Post* input (computed from the live OpenAPI: the union of required keys on
# every Odm*PatchInput minus the union on every Odm*PostInput). _reconcile
# builds Post-shaped bodies, so _patch_through_version carries these forward
# from the concept being patched.
PATCH_ONLY_REQUIRED_FIELDS = (
    "codelist",
    "comment",
    "data_type",
    "effective_date",
    "is_reference_data",
    "length",
    "method_type",
    "origin",
    "prompt",
    "purpose",
    "retired_date",
    "sas_dataset_name",
    "sas_field_name",
    "sds_var_name",
    "sdtm_version",
    "significant_digits",
    "terms",
    "value_regex",
)

class Import360i(BaseImporter):
    logging_name = "import_360i"

    def __init__(self, api=None, metrics_inst=None, db=None):
        super().__init__(api=api, metrics_inst=metrics_inst)
        self.db = db or EcrfPlatformDb(log=self.log)
        self.census = ImportCensus()
        self.same_payload_replay = False
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
            "objectives": {},
            "endpoints": {},
            "criteria": {},
            "timeframes": {},
            # Protocol SoA row ref -> StudyActivity selection uid, and source
            # activity::visit ref -> StudyActivitySchedule uid. The owned map
            # contains ONLY schedules this feature created; re-used external
            # schedules are never claimed for later deletion.
            "native_soa_activities": {},
            "native_soa_schedules": {},
            "native_soa_owned_schedules": {},
        }
        self._purpose_template_cache = {}
        self._purpose_timeframe_cache = {}

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

    @staticmethod
    def _semantic_key(value):
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    def _lookup_native_ct_term(self, codelist_name, field_name, source_value):
        """Resolve one source semantic value inside one exact OSB codelist.

        Intelligence Layer values are never accepted as final CT identity. OSB
        owns the term UID; a missing or ambiguous codelist-scoped lookup blocks
        the field instead of falling back to a display-label or global match.
        """
        source_key = self._semantic_key(source_value)
        wanted = NATIVE_TERM_ALIASES.get(field_name, {}).get(
            source_key, str(source_value or "").strip()
        )
        wanted_key = self._semantic_key(wanted)
        codelist_uid = NATIVE_CODELIST_UIDS[codelist_name]
        terms = self.api.get_all_from_api(
            "/ct/terms", params={"codelist_uid": codelist_uid, "page_size": 0}
        ) or []
        matches = [
            term
            for term in terms
            if self._semantic_key((term.get("name") or {}).get("sponsor_preferred_name"))
            == wanted_key
            and str((term.get("name") or {}).get("status") or "").lower() == "final"
            and str((term.get("attributes") or {}).get("status") or "").lower()
            == "final"
        ]
        unique = {term.get("term_uid"): term for term in matches if term.get("term_uid")}
        if len(unique) != 1:
            return None, (
                f"expected one Final OSB term named '{wanted}' in codelist "
                f"'{codelist_name}', found {len(unique)}"
            )
        term = next(iter(unique.values()))
        return {
            "term_uid": term["term_uid"],
            "sponsor_preferred_name": (term.get("name") or {}).get(
                "sponsor_preferred_name"
            ),
        }, None

    def _lookup_final_ct_term(self, codelist_name, term_name):
        """Resolve one unique exact Final term from one live OSB codelist."""
        codelists = self.api.get_all_from_api(
            "/ct/codelists/names", params={"page_size": 0}
        ) or []
        matches = [
            item
            for item in codelists
            if self._semantic_key(item.get("name")) == self._semantic_key(codelist_name)
            and item.get("codelist_uid")
        ]
        unique_codelists = {item["codelist_uid"]: item for item in matches}
        if len(unique_codelists) != 1:
            return None, (
                f"expected one OSB codelist named '{codelist_name}', "
                f"found {len(unique_codelists)}"
            )
        codelist_uid = next(iter(unique_codelists))
        terms = self.api.get_all_from_api(
            "/ct/terms", params={"codelist_uid": codelist_uid, "page_size": 0}
        ) or []
        wanted = self._semantic_key(term_name)
        term_matches = [
            term
            for term in terms
            if self._semantic_key((term.get("name") or {}).get("sponsor_preferred_name"))
            == wanted
            and str((term.get("name") or {}).get("status") or "").lower() == "final"
            and str((term.get("attributes") or {}).get("status") or "").lower()
            == "final"
            and term.get("term_uid")
        ]
        unique_terms = {term["term_uid"]: term for term in term_matches}
        if len(unique_terms) != 1:
            return None, (
                f"expected one Final OSB term named '{term_name}' in codelist "
                f"'{codelist_name}', found {len(unique_terms)}"
            )
        return next(iter(unique_terms.values())), None

    def _matching_age_year_units(self):
        """Final Age Unit subset definitions bound to CDISC C29848 / year."""
        units = self.api.get_all_from_api("/concepts/unit-definitions") or []

        def term_name(term):
            return self._semantic_key(
                term.get("term_name")
                or term.get("name")
                or term.get("submission_value")
            )

        matches = []
        for unit in units:
            if str(unit.get("status") or "").lower() != "final":
                continue
            subsets = {term_name(term) for term in unit.get("unit_subsets", []) or []}
            ct_units = unit.get("ct_units", []) or []
            has_year_ct = any(
                (term.get("term_uid") == "C29848")
                or term_name(term) in {"year", "years"}
                for term in ct_units
            )
            if "age unit" in subsets and has_year_ct and unit.get("uid"):
                matches.append(unit)
        return list({unit["uid"]: unit for unit in matches}.values())

    def _lookup_age_year_unit(self):
        """Resolve a governed CDISC year unit in the Age Unit subset.

        Some seeds carry both `year` and `years`. OSB's study-population write
        canonicalizes to `years`, so that name wins when both exist.
        """
        unique = self._matching_age_year_units()
        if not unique:
            return None, (
                "expected one Final OSB UnitDefinition for CDISC C29848 in the "
                "Age Unit subset, found 0"
            )
        if len(unique) == 1:
            unit = unique[0]
            return {"uid": unit["uid"], "name": unit.get("name")}, None

        def named(label):
            return [
                unit
                for unit in unique
                if self._semantic_key(unit.get("name")) == label
            ]

        for label in ("years", "year"):
            preferred = named(label)
            if len(preferred) == 1:
                unit = preferred[0]
                return {"uid": unit["uid"], "name": unit.get("name")}, None
        return None, (
            "expected one Final OSB UnitDefinition for CDISC C29848 in the "
            f"Age Unit subset, found {len(unique)}"
        )

    def _age_year_unit_uids(self):
        return {unit["uid"] for unit in self._matching_age_year_units()}

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
            if res is None:
                self.census.stop(
                    "clinical_programme",
                    OSB_CLINICAL_PROGRAMME,
                    "clinical programme create failed",
                )
                return None
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
            res = self.api.simple_post_to_api(
                "/projects",
                {
                    "project_number": project_number,
                    "name": name,
                    "description": "Imported from 360i (EDCProtocolToECRF)",
                    "clinical_programme_uid": programme_uid,
                },
            )
            if res is None:
                self.census.stop("project", project_number, "project create failed")
                return None
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
                if not self.api.simple_approve(
                    f"/concepts/unit-definitions/{uid}/approvals"
                ):
                    self.census.stop("unit", unit_name, "unit-definition approval failed")
                    continue
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
                names_approved = self.api.simple_approve(
                    f"/ct/codelists/{codelist_uid}/names/approvals"
                )
                attributes_approved = self.api.simple_approve(
                    f"/ct/codelists/{codelist_uid}/attributes/approvals"
                )
                if not names_approved or not attributes_approved:
                    self.census.stop("codelist", name, "codelist approval failed")
                    continue
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
                if not self.api.approve_item_names_and_attributes(
                    term_uid, "/ct/terms"
                ):
                    self.census.stop(
                        "codelist_term",
                        f"{name}/{term['name']}",
                        "term approval failed",
                    )
                    continue
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

    @staticmethod
    def _at(value, path):
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    def _sync_native_study_metadata(self, payload, study_uid, current_metadata):
        """Resolve and patch exact OSB-owned StudyMetadata fields.

        The payload supplies source semantics only. This method resolves live OSB
        CT/unit identity, validates the complete patch with ``dry=true``, applies
        it once, then reads the study back and compares every written property.
        Missing/ambiguous identities and read-back mismatches are release-blocking
        census rows, never text fallbacks.
        """
        native = payload.get("study", {}).get("nativeMetadata")
        if not isinstance(native, dict):
            return False

        for blocker in native.get("blockers", []) or []:
            self.census.stop(
                "study_native_metadata",
                blocker.get("sourceKey") or "?",
                f"{blocker.get('code') or 'OSB_NATIVE_METADATA_BLOCKED'}: "
                f"{blocker.get('detail') or 'source value requires review'}",
            )

        patch_sections = {}
        expected = []

        def add_term(section_name, target_field, source_value, codelist_name):
            if source_value is None or str(source_value).strip() == "":
                return
            term, error = self._lookup_native_ct_term(
                codelist_name, target_field, source_value
            )
            if error:
                self.census.stop("study_native_metadata", target_field, error)
                return
            patch_sections.setdefault(section_name, {})[target_field] = term
            expected.append(((section_name, target_field, "term_uid"), term["term_uid"]))

        high = native.get("highLevelStudyDesign") or {}
        population = native.get("studyPopulation") or {}
        intervention = native.get("studyIntervention") or {}
        add_term(
            "high_level_study_design",
            "study_type_code",
            high.get("studyType"),
            CODELIST_STUDY_TYPE,
        )
        add_term(
            "high_level_study_design",
            "trial_phase_code",
            high.get("trialPhase"),
            CODELIST_TRIAL_PHASE,
        )
        add_term(
            "study_population",
            "sex_of_participants_code",
            population.get("sexOfParticipants"),
            CODELIST_SEX_OF_PARTICIPANTS,
        )
        add_term(
            "study_intervention",
            "control_type_code",
            intervention.get("controlType"),
            CODELIST_CONTROL_TYPE,
        )
        add_term(
            "study_intervention",
            "intervention_model_code",
            intervention.get("interventionModel"),
            CODELIST_INTERVENTION_MODEL,
        )
        add_term(
            "study_intervention",
            "trial_blinding_schema_code",
            intervention.get("trialBlindingSchema"),
            CODELIST_TRIAL_BLINDING_SCHEMA,
        )

        if isinstance(intervention.get("isTrialRandomised"), bool):
            value = intervention["isTrialRandomised"]
            patch_sections.setdefault("study_intervention", {})[
                "is_trial_randomised"
            ] = value
            expected.append((("study_intervention", "is_trial_randomised"), value))

        if isinstance(population.get("healthySubjectIndicator"), bool):
            value = population["healthySubjectIndicator"]
            patch_sections.setdefault("study_population", {})[
                "healthy_subject_indicator"
            ] = value
            expected.append((("study_population", "healthy_subject_indicator"), value))

        expected_subjects = population.get("numberOfExpectedSubjects")
        if isinstance(expected_subjects, int) and not isinstance(expected_subjects, bool):
            patch_sections.setdefault("study_population", {})[
                "number_of_expected_subjects"
            ] = expected_subjects
            expected.append(
                (("study_population", "number_of_expected_subjects"), expected_subjects)
            )

        age_values = {
            "planned_minimum_age_of_subjects": population.get(
                "plannedMinimumAgeYears"
            ),
            "planned_maximum_age_of_subjects": population.get(
                "plannedMaximumAgeYears"
            ),
        }
        requested_ages = {
            key: value
            for key, value in age_values.items()
            if value is not None
        }
        for key, value in requested_ages.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                self.census.stop(
                    "study_native_metadata",
                    key,
                    f"age must be a non-negative integer number of years, got {value!r}",
                )
                continue
            current_duration = self._at(current_metadata, ("study_population", key))
            current_unit = (
                (current_duration or {}).get("duration_unit_code")
                if isinstance(current_duration, dict)
                else None
            )
            # OSB is authoritative on reimport: when its reviewed numeric value
            # agrees, preserve its exact unit identity even if the global library
            # has duplicate year definitions. A new/different age still blocks on
            # ambiguous library identity.
            if (
                isinstance(current_duration, dict)
                and current_duration.get("duration_value") == value
                and isinstance(current_unit, dict)
                and current_unit.get("uid")
            ):
                age_unit = {
                    "uid": current_unit["uid"],
                    "name": current_unit.get("name"),
                }
                error = None
            else:
                age_unit, error = self._lookup_age_year_unit()
            if error:
                self.census.stop("study_native_metadata", "age_unit", error)
                continue
            duration = {
                "duration_value": value,
                "duration_unit_code": age_unit,
            }
            patch_sections.setdefault("study_population", {})[key] = duration
            expected.extend(
                [
                    (("study_population", key, "duration_value"), value),
                    (
                        ("study_population", key, "duration_unit_code", "uid"),
                        age_unit["uid"],
                    ),
                ]
            )

        if not patch_sections:
            return False

        if all(
            self._at(current_metadata, path) == wanted
            for path, wanted in expected
        ):
            self.census.unchanged.append(
                {
                    "kind": "study_native_metadata",
                    "ref": study_uid,
                    "fields": [".".join(path) for path, _ in expected],
                }
            )
            return False

        body = {
            "uid": study_uid,
            "current_metadata": patch_sections,
        }
        if self.api.patch_to_api(body, "/studies/", params={"dry": True}) is None:
            self.census.stop(
                "study_native_metadata", study_uid, "OSB dry validation rejected the patch"
            )
            return False
        if self.api.patch_to_api(body, "/studies/") is None:
            self.census.stop(
                "study_native_metadata", study_uid, "OSB native metadata patch failed"
            )
            return False

        read_back = self.api.get_all_from_api(
            f"/studies/{study_uid}",
            params={
                "include_sections": [
                    "high_level_study_design",
                    "study_population",
                    "study_intervention",
                ]
            },
        ) or {}
        actual_metadata = read_back.get("current_metadata") or {}
        mismatches = [
            {
                "path": ".".join(path),
                "expected": wanted,
                "actual": self._at(actual_metadata, path),
            }
            for path, wanted in expected
            if self._at(actual_metadata, path) != wanted
        ]
        age_year_uids = self._age_year_unit_uids()
        mismatches = [
            row
            for row in mismatches
            if not (
                row["path"].endswith("duration_unit_code.uid")
                and row["expected"] in age_year_uids
                and row["actual"] in age_year_uids
            )
        ]
        if mismatches:
            self.census.stop(
                "study_native_metadata",
                study_uid,
                f"read-after-write reconciliation failed: {json.dumps(mismatches, sort_keys=True)}",
            )
            return True
        self.census.updated.append(
            {
                "kind": "study_native_metadata",
                "ref": study_uid,
                "fields": [".".join(path) for path, _ in expected],
            }
        )
        return True

    def _sync_study_metadata(self, payload, study_uid, current=None, created=False):
        """Patch mutable study metadata and account for every failed write."""
        changed = False
        current_metadata = (current or {}).get("current_metadata", {})
        current_ident = current_metadata.get("identification_metadata") or {}
        current_desc = current_metadata.get("study_description") or {}
        changed = self._sync_native_study_metadata(
            payload, study_uid, current_metadata
        ) or changed

        reg = payload.get("study", {}).get("registryIdentifiers", {})
        non_null = {key: value for key, value in reg.items() if value is not None}
        current_registry = current_ident.get("registry_identifiers") or {}
        if non_null and any(current_registry.get(key) != value for key, value in non_null.items()):
            result = self.api.patch_to_api(
                {
                    "uid": study_uid,
                    "current_metadata": {
                        "identification_metadata": {
                            "registry_identifiers": non_null
                        }
                    },
                },
                "/studies/",
            )
            if result is None:
                self.census.stop(
                    "registry_identifiers", study_uid, "registry identifier patch failed"
                )
            else:
                changed = True
                bucket = self.census.created if created else self.census.updated
                bucket.append({"kind": "registry_identifiers", "ref": study_uid})

        study_block = payload.get("study", {})
        title = study_block.get("officialTitle") or study_block.get("name")
        short_title = study_block.get("shortTitle")
        current_title = current_desc.get("study_title")
        source_title = str(title)[:800] if title else None
        current_short_title = current_desc.get("study_short_title")
        source_short_title = str(short_title)[:800] if short_title else None
        placeholder_titles = {
            str(payload.get("study", {}).get("name") or "").strip().casefold(),
            "new study",
        }
        description_patch = {}
        if source_title and (
            not current_title
            or current_title.strip().casefold() != source_title.strip().casefold()
        ):
            if (
                current_title
                and current_title.strip().casefold() not in placeholder_titles
                and not created
            ):
                self.census.stop(
                    "study_title",
                    study_uid,
                    "merge required: current OSB title differs from the source proposal; "
                    "OSB/human value was preserved",
                )
            else:
                description_patch["study_title"] = source_title
        if source_short_title and (
            not current_short_title
            or current_short_title.strip().casefold()
            != source_short_title.strip().casefold()
        ):
            if (
                current_short_title
                and current_short_title.strip().casefold() not in placeholder_titles
                and not created
            ):
                self.census.stop(
                    "study_short_title",
                    study_uid,
                    "merge required: current OSB short title differs from the source "
                    "proposal; OSB/human value was preserved",
                )
            else:
                description_patch["study_short_title"] = source_short_title
        if description_patch:
            result = self.api.patch_to_api(
                {
                    "uid": study_uid,
                    "current_metadata": {"study_description": description_patch},
                },
                "/studies/",
            )
            if result is None:
                self.census.stop(
                    "study_title", study_uid, "study title/short-title patch failed"
                )
            else:
                changed = True
                if not created:
                    self.census.updated.append(
                        {
                            "kind": "study_title",
                            "ref": study_uid,
                            "fields": sorted(description_patch),
                        }
                    )
        return changed

    def ensure_study(self, payload, project_number, crosswalk):
        """Create the study, or verify the crosswalked one is usable."""
        if crosswalk:
            study_uid = crosswalk["osb_study_uid"]
            active_studies = self.api.get_all_from_api("/studies") or []
            if not any(study.get("uid") == study_uid for study in active_studies):
                self.census.stop(
                    "study",
                    study_uid,
                    "crosswalk names a soft-deleted or inactive OSB study; restore it "
                    "in OSB or clear the ledger row to import as a new study",
                )
                return None
            study = self.api.get_all_from_api(
                f"/studies/{study_uid}",
                params={
                    "include_sections": [
                        "high_level_study_design",
                        "study_population",
                        "study_intervention",
                    ]
                },
            )
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
            changed = self._sync_study_metadata(
                payload, study_uid, current=study, created=False
            )
            if not changed:
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
        self._sync_study_metadata(payload, study_uid, created=True)

        return study_uid

    def ensure_epochs(self, payload, study_uid):
        """Reconcile the payload's epochs (or the declared carrier).

        Existing epochs are updated when their order/description changes.
        Epochs removed or renamed are returned for deletion after visits have
        moved off them, because OSB will not delete a referenced epoch.
        """
        plans, is_scaffolding = mapping.epochs_plan(payload)
        epoch_uid_by_ref = {}
        existing = self.api.get_all_from_api(f"/studies/{study_uid}/study-epochs") or []
        existing_by_name = {e.get("epoch_name", "").lower(): e for e in existing}
        existing_by_uid = {e.get("uid"): e for e in existing}
        desired_names = {plan["name"] for plan in plans}
        if is_scaffolding:
            # Older importer versions created a fresh carrier on every replay
            # because OSB derives epoch_name from CT ("Treatment 1", ...).
            # Retain the carrier that the most visits currently use; this lets
            # a repair converge without trying to move the whole calendar to
            # an empty later duplicate (which OSB's epoch chronology rejects).
            visits = self.api.get_all_from_api(
                f"/studies/{study_uid}/study-visits"
            ) or []
            use_count = {}
            for visit in visits:
                epoch_uid = visit.get("study_epoch_uid") or (
                    visit.get("study_epoch") or {}
                ).get("uid")
                if epoch_uid:
                    use_count[epoch_uid] = use_count.get(epoch_uid, 0) + 1
            mapped_carrier_uid = self.uid_map["epochs"].get(
                mapping.CARRIER_EPOCH_NAME
            )
            carriers = [
                epoch
                for epoch in existing
                if epoch.get("description") == CARRIER_EPOCH_DESCRIPTION
            ]
            if carriers:
                retained = max(
                    carriers,
                    key=lambda epoch: (
                        use_count.get(epoch.get("uid"), 0),
                        epoch.get("uid") == mapped_carrier_uid,
                    ),
                )
                self.uid_map["epochs"][
                    mapping.CARRIER_EPOCH_NAME
                ] = retained["uid"]
        stale_epochs = [
            {"ref": ref, "uid": uid}
            for ref, uid in self.uid_map["epochs"].items()
            if ref not in desired_names and uid in existing_by_uid
        ]

        for plan in plans:
            name = plan["name"]
            mapped_uid = self.uid_map["epochs"].get(name)
            found = existing_by_uid.get(mapped_uid) or existing_by_name.get(
                name.lower()
            )
            if found:
                description = (
                    name
                    if not plan["scaffolding"]
                    else CARRIER_EPOCH_DESCRIPTION
                )
                changed = []
                if found.get("order") != plan["order"]:
                    changed.append("order")
                if found.get("description") != description:
                    changed.append("description")
                if changed:
                    res = self.api.patch_to_api(
                        {
                            "uid": found["uid"],
                            "study_uid": study_uid,
                            "order": plan["order"],
                            "description": description,
                            "change_description": "360i re-import: epoch changed",
                        },
                        f"/studies/{study_uid}/study-epochs",
                    )
                    if res is None:
                        self.census.stop("epoch", name, "epoch patch failed")
                        continue
                    self.census.updated.append(
                        {"kind": "epoch", "ref": name, "changed": changed}
                    )
                else:
                    self.census.unchanged.append({"kind": "epoch", "ref": name})
                epoch_uid_by_ref[name] = found["uid"]
                self.uid_map["epochs"][name] = found["uid"]
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
                "order": plan["order"],
                "description": name if not plan["scaffolding"] else (
                    CARRIER_EPOCH_DESCRIPTION
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
        desired_uids = set(epoch_uid_by_ref.values())
        if is_scaffolding:
            # OSB derives the displayed epoch_name from CT ("Treatment 1",
            # "Treatment 2", ...), so the source carrier name cannot be used
            # to find old attempts. Remove every duplicate carrier by its
            # importer-only description after visits have moved to the one
            # retained uid.
            stale_by_uid = {entry["uid"]: entry for entry in stale_epochs}
            for epoch in existing:
                if (
                    epoch.get("description") == CARRIER_EPOCH_DESCRIPTION
                    and epoch.get("uid") not in desired_uids
                ):
                    stale_by_uid[epoch["uid"]] = {
                        "ref": mapping.CARRIER_EPOCH_NAME,
                        "uid": epoch["uid"],
                    }
            stale_epochs = list(stale_by_uid.values())
        stale_epochs = [
            entry for entry in stale_epochs if entry["uid"] not in desired_uids
        ]
        return ref_to_epoch_uid, is_scaffolding, stale_epochs

    def remove_stale_epochs(self, study_uid, stale_epochs):
        """Delete prior-import epochs only after visit reconciliation."""
        for entry in stale_epochs:
            ok = self.api.simple_delete(
                f"/studies/{study_uid}/study-epochs/{entry['uid']}",
                "/study-epochs",
            )
            if not ok:
                self.census.stop(
                    "epoch", entry["ref"], "removed epoch delete failed"
                )
                continue
            self.uid_map["epochs"].pop(entry["ref"], None)
            self.census.updated.append(
                {"kind": "epoch_removed", "ref": entry["ref"]}
            )

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
                "study_epoch_uid": v.get("study_epoch_uid")
                or (v.get("study_epoch") or {}).get("uid"),
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
            epoch_uid = next(iter(epoch_uid_by_visit_ref.values()), None)
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
        """Reconcile active visits and return stale visits for deferred deletion.

        First import: uid_map empty -> the diff is all-create (prior behavior).
        Re-import of an edited payload: refKey diff drives targeted PATCH of
        changed windows/timing/names and POST of new visits. Removed visits are
        deleted only after native SoA and ODM dependencies have reconciled.
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
            return []
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
                        "reason": "the protocol stated no numeric day; placed after the last "
                        f"dated visit at day {plan['time_value']} of the anchor reference to "
                        "satisfy OSB's chronological-order rule — set the real timing in OSB",
                    }
                )
            if plan.get("unscheduled_demoted"):
                self.census.carried.append(
                    {
                        "kind": "visit_class",
                        "ref": plan["refKey"],
                        "reason": "the payload typed this visit 'unscheduled'; OSB permits one "
                        "UNSCHEDULED_VISIT per study, so it is imported as a manually-defined "
                        "visit keeping its protocol name, number and schedule links",
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

        return diff["delete"]

    def remove_stale_visits(self, study_uid, stale_visits):
        """Delete visits after owned schedules and ODM event links are removed."""
        for entry in stale_visits:
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
    # Native Schedule of Activities: governed activity -> study selection -> cell
    # ------------------------------------------------------------------

    def ensure_native_soa(self, payload, study_uid):
        """Reconcile the protocol's activity×visit matrix into native OSB SoA.

        Activity concepts are never created here. The pure mapper admits only a
        unique Final exact-normalized library match. Existing study selections
        and schedules are reused; only schedules created by this feature are
        considered owned and removable on a later payload.
        """
        section = payload.get("scheduleOfActivities")
        if section is None:
            return
        get_paged = getattr(self.api, "get_all_from_api_paged", None)
        library_activities = (
            get_paged("/concepts/activities/activities")
            if callable(get_paged)
            else self.api.get_all_from_api(
                "/concepts/activities/activities", params={"page_size": 0}
            )
        ) or []
        plan = mapping.native_soa_plan(payload, library_activities)
        if plan is None:
            return

        source_activity_refs = {
            str(activity.get("refKey"))
            for activity in section.get("activities") or []
            if activity.get("refKey")
        }
        for ref in list(self.uid_map["native_soa_activities"]):
            if ref not in source_activity_refs:
                self.uid_map["native_soa_activities"].pop(ref, None)

        for blocker in plan["blocked"]:
            self.census.stop(blocker["kind"], blocker["ref"], blocker["reason"])
            self.census.block_release(
                blocker["kind"], blocker["ref"], blocker["reason"]
            )

        selected = self.api.get_all_from_api(
            f"/studies/{study_uid}/study-activities", params={"page_size": 0}
        ) or []
        selected_by_activity_uid = {}
        for row in selected:
            activity_uid = (row.get("activity") or {}).get("uid")
            if activity_uid:
                selected_by_activity_uid.setdefault(activity_uid, []).append(row)

        selection_uid_by_ref = {}
        for activity in plan["activities"]:
            ref = activity["ref"]
            flowchart_term, flowchart_error = self._lookup_final_ct_term(
                CODELIST_FLOWCHART_GROUP, activity["flowchart_group_name"]
            )
            if flowchart_error:
                self.census.stop("study_activity", ref, flowchart_error)
                self.census.block_release("study_activity", ref, flowchart_error)
                continue
            flowchart_uid = flowchart_term["term_uid"]
            matches = selected_by_activity_uid.get(activity["activity_uid"], [])
            if len(matches) > 1:
                reason = "multiple study activity selections reference the exact library activity"
                self.census.stop("study_activity", ref, reason)
                self.census.block_release("study_activity", ref, reason)
                continue
            if len(matches) == 1:
                uid = matches[0].get("study_activity_uid")
                if not uid:
                    reason = "matched study activity selection has no uid"
                    self.census.stop("study_activity", ref, reason)
                    self.census.block_release("study_activity", ref, reason)
                    continue
                existing_flowchart_uid = (
                    matches[0].get("study_soa_group") or {}
                ).get("soa_group_term_uid")
                if existing_flowchart_uid != flowchart_uid:
                    reason = (
                        "matched study activity has a conflicting or missing "
                        "Flowchart Group term"
                    )
                    self.census.stop("study_activity", ref, reason)
                    self.census.block_release("study_activity", ref, reason)
                    continue
                selection_uid_by_ref[ref] = uid
                self.uid_map["native_soa_activities"][ref] = uid
                self.census.unchanged.append(
                    {"kind": "study_activity", "ref": ref, "uid": uid}
                )
                continue

            body = {
                "activity_uid": activity["activity_uid"],
                "soa_group_term_uid": flowchart_uid,
                "show_activity_in_protocol_flowchart": True,
            }
            if activity.get("activity_group_uid"):
                body["activity_group_uid"] = activity["activity_group_uid"]
            if activity.get("activity_subgroup_uid"):
                body["activity_subgroup_uid"] = activity["activity_subgroup_uid"]
            created = self.api.simple_post_to_api(
                f"/studies/{study_uid}/study-activities", body, "/study-activities"
            )
            uid = (created or {}).get("study_activity_uid")
            if not uid:
                reason = "study activity selection create failed"
                self.census.stop("study_activity", ref, reason)
                self.census.block_release("study_activity", ref, reason)
                continue
            selection_uid_by_ref[ref] = uid
            self.uid_map["native_soa_activities"][ref] = uid
            self.census.created.append(
                {"kind": "study_activity", "ref": ref, "uid": uid}
            )

        current_schedules = self.api.get_all_from_api(
            f"/studies/{study_uid}/study-activity-schedules"
        ) or []
        schedules_by_pair = {}
        schedules_by_uid = {}
        for row in current_schedules:
            uid = row.get("study_activity_schedule_uid")
            pair = (row.get("study_activity_uid"), row.get("study_visit_uid"))
            if all(pair):
                schedules_by_pair.setdefault(pair, []).append(row)
            if uid:
                schedules_by_uid[uid] = row

        prior_owned = dict(self.uid_map["native_soa_owned_schedules"])
        desired_source_refs = {
            f"{cell.get('activityRef')}::{cell.get('payloadVisitRef')}"
            for cell in section.get("schedules") or []
            if cell.get("activityRef") and cell.get("payloadVisitRef")
        }
        desired_operational_refs = set()
        desired_pair_by_ref = {}
        for schedule in plan["schedules"]:
            ref = schedule["ref"]
            selection_uid = selection_uid_by_ref.get(schedule["activity_ref"])
            visit_uid = self.uid_map["visits"].get(schedule["payload_visit_ref"])
            if not selection_uid:
                # The parent activity already has a specific release blocker
                # (library ambiguity, CT failure, duplicate selection, or failed
                # create). Do not multiply that one root cause by every cell.
                continue
            if not visit_uid:
                reason = "native SoA schedule has no resolved payload visit uid"
                self.census.stop("study_activity_schedule", ref, reason)
                self.census.block_release("study_activity_schedule", ref, reason)
                continue
            desired_operational_refs.add(ref)
            pair = (selection_uid, visit_uid)
            desired_pair_by_ref[ref] = pair
            matches = schedules_by_pair.get(pair, [])
            if len(matches) > 1:
                reason = "multiple native schedules exist for the same activity and visit"
                self.census.stop("study_activity_schedule", ref, reason)
                self.census.block_release("study_activity_schedule", ref, reason)
                continue
            if len(matches) == 1:
                uid = matches[0].get("study_activity_schedule_uid")
                self.uid_map["native_soa_schedules"][ref] = uid
                self.census.unchanged.append(
                    {"kind": "study_activity_schedule", "ref": ref, "uid": uid}
                )
                continue
            created = self.api.simple_post_to_api(
                f"/studies/{study_uid}/study-activity-schedules",
                {
                    "study_activity_uid": selection_uid,
                    "study_visit_uid": visit_uid,
                },
                "/study-activity-schedules",
            )
            uid = (created or {}).get("study_activity_schedule_uid")
            if not uid:
                reason = "study activity schedule create failed"
                self.census.stop("study_activity_schedule", ref, reason)
                self.census.block_release("study_activity_schedule", ref, reason)
                continue
            self.uid_map["native_soa_schedules"][ref] = uid
            self.uid_map["native_soa_owned_schedules"][ref] = uid
            schedules_by_pair[pair] = [created]
            schedules_by_uid[uid] = created
            self.census.created.append(
                {"kind": "study_activity_schedule", "ref": ref, "uid": uid}
            )

        # Delete only source cells removed from the payload AND only schedules
        # this feature created previously. A reused schedule may belong to another
        # OSB workflow and is intentionally never claimed.
        for ref, uid in prior_owned.items():
            if ref in desired_source_refs:
                continue
            if uid not in schedules_by_uid:
                self.uid_map["native_soa_owned_schedules"].pop(ref, None)
                self.uid_map["native_soa_schedules"].pop(ref, None)
                continue
            if not self.api.simple_delete(
                f"/studies/{study_uid}/study-activity-schedules/{uid}",
                "/study-activity-schedules",
            ):
                self.census.stop(
                    "study_activity_schedule_removed", ref, "owned schedule delete failed"
                )
                continue
            self.uid_map["native_soa_owned_schedules"].pop(ref, None)
            self.uid_map["native_soa_schedules"].pop(ref, None)
            self.census.updated.append(
                {"kind": "study_activity_schedule_removed", "ref": ref, "uid": uid}
            )

        for ref in list(self.uid_map["native_soa_schedules"]):
            if ref not in desired_source_refs:
                self.uid_map["native_soa_schedules"].pop(ref, None)

        # Verify durable API state, not merely successful response bodies.
        read_back = self.api.get_all_from_api(
            f"/studies/{study_uid}/study-activity-schedules"
        ) or []
        read_back_by_pair = {}
        for row in read_back:
            pair = (row.get("study_activity_uid"), row.get("study_visit_uid"))
            if all(pair):
                read_back_by_pair.setdefault(pair, []).append(row)
        for ref in sorted(desired_operational_refs):
            matches = read_back_by_pair.get(desired_pair_by_ref[ref], [])
            if len(matches) != 1:
                self.uid_map["native_soa_schedules"].pop(ref, None)
                reason = f"read-after-write expected one native schedule, found {len(matches)}"
                self.census.stop("native_soa_reconciliation", ref, reason)
                self.census.block_release("native_soa_reconciliation", ref, reason)
                continue
            self.uid_map["native_soa_schedules"][ref] = matches[0][
                "study_activity_schedule_uid"
            ]

        # Preserve schedules this feature did not create, but never let an extra
        # cell on a mapped activity silently contradict the source matrix.
        desired_pairs = set(desired_pair_by_ref.values())
        mapped_selection_uids = set(selection_uid_by_ref.values())
        for row in read_back:
            pair = (row.get("study_activity_uid"), row.get("study_visit_uid"))
            if pair[0] not in mapped_selection_uids or pair in desired_pairs:
                continue
            uid = row.get("study_activity_schedule_uid") or "?"
            reason = "pre-existing native schedule is outside the source SoA matrix"
            self.census.stop("activity_schedule_external", uid, reason)
            self.census.block_release("activity_schedule_external", uid, reason)

    # ------------------------------------------------------------------
    # Native Study Purpose: objectives -> endpoints; criteria independently
    # ------------------------------------------------------------------

    @staticmethod
    def _purpose_plain(value):
        """Normalize OSB HTML/plain syntax content for exact reconciliation."""
        without_tags = re.sub(r"<[^>]+>", " ", str(value or ""))
        return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()

    @staticmethod
    def _purpose_html(value):
        return f"<p>{html.escape(str(value), quote=False)}</p>"

    def _ensure_purpose_template(
        self, kind, study_uid, ref, text, criteria_type_uid=None
    ):
        config = {
            "objective": ("/objective-templates", "ObjectiveTemplate"),
            "endpoint": ("/endpoint-templates", "EndpointTemplate"),
            "criterion": ("/criteria-templates", "CriteriaTemplate"),
            "timeframe": ("/timeframe-templates", "TimeframeTemplate"),
        }[kind]
        path, label = config
        cache_key = (kind, self._semantic_key(text), criteria_type_uid)
        if cache_key in self._purpose_template_cache:
            return self._purpose_template_cache[cache_key]
        current = self.api.get_all_from_api(path, params={"page_size": 0}) or []
        matches = [
            item
            for item in current
            if self._semantic_key(item.get("name_plain") or self._purpose_plain(item.get("name")))
            == self._semantic_key(text)
            and (
                kind != "criterion"
                or (item.get("type") or {}).get("term_uid") == criteria_type_uid
            )
        ]
        finals = [item for item in matches if str(item.get("status") or "").lower() == "final"]
        unique = {item.get("uid"): item for item in finals if item.get("uid")}
        if len(unique) > 1:
            self.census.stop(
                f"study_{kind}_template",
                ref,
                f"multiple Final exact OSB {label} matches",
            )
            return None
        if len(unique) == 1:
            uid = next(iter(unique))
            self._purpose_template_cache[cache_key] = uid
            return uid
        body = {
            "name": self._purpose_html(text),
            "guidance_text": f"Imported from governed 360i source record {ref}.",
            "library_name": "Sponsor",
        }
        if kind != "timeframe":
            body["study_uid"] = study_uid
        if kind == "criterion":
            body["type_uid"] = criteria_type_uid
        res = self.api.simple_post_to_api(path, body, path)
        if res is None or not res.get("uid"):
            self.census.stop(
                f"study_{kind}_template", ref, f"{label} create failed"
            )
            return None
        uid = res["uid"]
        if not self.api.simple_approve(f"{path}/{uid}/approvals"):
            self.census.stop(
                f"study_{kind}_template", ref, f"{label} approval failed"
            )
            return None
        self._purpose_template_cache[cache_key] = uid
        self.census.created.append(
            {"kind": f"study_{kind}_template", "ref": ref, "uid": uid}
        )
        return uid

    def _ensure_purpose_timeframe(self, study_uid, ref, text):
        if not text:
            return None
        key = self._semantic_key(text)
        if key in self._purpose_timeframe_cache:
            return self._purpose_timeframe_cache[key]
        current = self.api.get_all_from_api("/timeframes", params={"page_size": 0}) or []
        matches = [
            item
            for item in current
            if self._semantic_key(item.get("name_plain") or self._purpose_plain(item.get("name")))
            == key
            and str(item.get("status") or "").lower() == "final"
            and item.get("uid")
        ]
        unique = {item["uid"]: item for item in matches}
        if len(unique) > 1:
            self.census.stop(
                "study_endpoint_timeframe", ref, "multiple Final exact OSB Timeframes"
            )
            return None
        if len(unique) == 1:
            uid = next(iter(unique))
            self._purpose_timeframe_cache[key] = uid
            self.uid_map["timeframes"][key] = uid
            return uid
        template_uid = self._ensure_purpose_template(
            "timeframe", study_uid, ref, text
        )
        if template_uid is None:
            return None
        res = self.api.simple_post_to_api(
            "/timeframes",
            {
                "parameter_terms": [],
                "timeframe_template_uid": template_uid,
                "library_name": "Sponsor",
            },
            "/timeframes",
        )
        if res is None or not res.get("uid"):
            self.census.stop(
                "study_endpoint_timeframe", ref, "Timeframe create failed"
            )
            return None
        uid = res["uid"]
        if not self.api.simple_approve(f"/timeframes/{uid}/approvals"):
            self.census.stop(
                "study_endpoint_timeframe", ref, "Timeframe approval failed"
            )
            return None
        self._purpose_timeframe_cache[key] = uid
        self.uid_map["timeframes"][key] = uid
        self.census.created.append(
            {"kind": "study_endpoint_timeframe", "ref": ref, "uid": uid}
        )
        return uid

    def _purpose_existing(self, study_uid, section, data_key):
        rows = self.api.get_all_from_api(
            f"/studies/{study_uid}/{section}", params={"page_size": 0}
        ) or []
        by_text = {}
        for row in rows:
            data = row.get(data_key) or {}
            key = self._semantic_key(
                data.get("name_plain") or self._purpose_plain(data.get("name"))
            )
            if key:
                by_text.setdefault(key, []).append(row)
        return rows, by_text

    def ensure_study_purpose(self, payload, study_uid):
        """Create/reconcile native OSB objectives, endpoints and criteria.

        Exact source text becomes a study-scoped syntax template and instance.
        Endpoint relationships and levels are resolved against live OSB CT. ODM
        collection fields are never inputs to this stage.
        """
        plan = mapping.study_purpose_plan(payload)
        if plan is None:
            reason = (
                "legacy payload has no governed studyPurpose section; native "
                "objectives/endpoints/criteria were not derived from carriers"
            )
            self.census.block_release("study_purpose", study_uid, reason)
            return
        for blocker in plan["blockers"]:
            reason = f"{blocker.get('code')}: {blocker.get('detail')}"
            self.census.carried.append(
                {
                    "kind": f"study_{blocker.get('kind')}",
                    "ref": blocker.get("refKey"),
                    "reason": reason,
                    "source_assertion_ids": blocker.get("sourceAssertionIds") or [],
                }
            )
            self.census.block_release(
                f"study_{blocker.get('kind')}", blocker.get("refKey"), reason
            )

        prior_owned = {
            kind: dict(self.uid_map[kind])
            for kind in ("objectives", "endpoints", "criteria")
        }
        objective_rows, objectives_by_text = self._purpose_existing(
            study_uid, "study-objectives", "objective"
        )
        objective_uid_by_ref = {}
        for item in plan["objectives"]:
            ref = item["ref"]
            term, error = self._lookup_final_ct_term(
                CODELIST_OBJECTIVE_LEVEL, item["level_name"]
            )
            if error:
                self.census.stop("study_objective", ref, error)
                continue
            matches = objectives_by_text.get(self._semantic_key(item["text"]), [])
            compatible = [
                row
                for row in matches
                if (row.get("objective_level") or {}).get("term_uid")
                == term["term_uid"]
            ]
            if matches and len(compatible) != 1:
                self.census.stop(
                    "study_objective",
                    ref,
                    "exact objective text exists with conflicting or duplicate level",
                )
                continue
            if len(compatible) == 1:
                uid = compatible[0].get("study_objective_uid")
                self.census.unchanged.append(
                    {"kind": "study_objective", "ref": ref, "uid": uid}
                )
            else:
                template_uid = self._ensure_purpose_template(
                    "objective", study_uid, ref, item["text"]
                )
                if template_uid is None:
                    continue
                res = self.api.simple_post_to_api(
                    f"/studies/{study_uid}/study-objectives",
                    {
                        "objective_level_uid": term["term_uid"],
                        "objective_data": {
                            "parameter_terms": [],
                            "objective_template_uid": template_uid,
                            "library_name": "Sponsor",
                        },
                    },
                    "/study-objectives",
                    params={"create_objective": True},
                )
                if res is None or not res.get("study_objective_uid"):
                    self.census.stop(
                        "study_objective", ref, "objective selection create failed"
                    )
                    continue
                uid = res["study_objective_uid"]
                self.census.created.append(
                    {"kind": "study_objective", "ref": ref, "uid": uid}
                )
            objective_uid_by_ref[ref] = uid
            self.uid_map["objectives"][ref] = uid

        endpoint_rows, endpoints_by_text = self._purpose_existing(
            study_uid, "study-endpoints", "endpoint"
        )
        for item in plan["endpoints"]:
            ref = item["ref"]
            objective_uid = objective_uid_by_ref.get(item["objective_ref"])
            if objective_uid is None:
                self.census.stop(
                    "study_endpoint", ref, "linked objective was not reconciled"
                )
                continue
            term, error = self._lookup_final_ct_term(
                CODELIST_ENDPOINT_LEVEL, item["level_name"]
            )
            if error:
                self.census.stop("study_endpoint", ref, error)
                continue
            timeframe_uid = self._ensure_purpose_timeframe(
                study_uid, ref, item.get("timeframe")
            )
            if item.get("timeframe") and timeframe_uid is None:
                continue
            matches = endpoints_by_text.get(self._semantic_key(item["text"]), [])
            compatible = [
                row
                for row in matches
                if (row.get("endpoint_level") or {}).get("term_uid")
                == term["term_uid"]
                and (row.get("study_objective") or {}).get("study_objective_uid")
                == objective_uid
                and (
                    not timeframe_uid
                    or (row.get("timeframe") or {}).get("uid") == timeframe_uid
                )
            ]
            if matches and len(compatible) != 1:
                self.census.stop(
                    "study_endpoint",
                    ref,
                    "exact endpoint text exists with conflicting/duplicate level, objective or timeframe",
                )
                continue
            if len(compatible) == 1:
                uid = compatible[0].get("study_endpoint_uid")
                self.census.unchanged.append(
                    {"kind": "study_endpoint", "ref": ref, "uid": uid}
                )
            else:
                template_uid = self._ensure_purpose_template(
                    "endpoint", study_uid, ref, item["text"]
                )
                if template_uid is None:
                    continue
                res = self.api.simple_post_to_api(
                    f"/studies/{study_uid}/study-endpoints",
                    {
                        "study_objective_uid": objective_uid,
                        "endpoint_level_uid": term["term_uid"],
                        "endpoint_sublevel_uid": None,
                        "endpoint_data": {
                            "parameter_terms": [],
                            "endpoint_template_uid": template_uid,
                            "library_name": "Sponsor",
                        },
                        "endpoint_units": {"units": [], "separator": None},
                        "timeframe_uid": timeframe_uid,
                    },
                    "/study-endpoints",
                    params={"create_endpoint": True},
                )
                if res is None or not res.get("study_endpoint_uid"):
                    self.census.stop(
                        "study_endpoint", ref, "endpoint selection create failed"
                    )
                    continue
                uid = res["study_endpoint_uid"]
                self.census.created.append(
                    {"kind": "study_endpoint", "ref": ref, "uid": uid}
                )
            self.uid_map["endpoints"][ref] = uid

        criteria_rows, criteria_by_text = self._purpose_existing(
            study_uid, "study-criteria", "criteria"
        )
        for item in plan["criteria"]:
            ref = item["ref"]
            term, error = self._lookup_final_ct_term(
                CODELIST_CRITERIA_TYPE, item["type_name"]
            )
            if error:
                self.census.stop("study_criterion", ref, error)
                continue
            matches = criteria_by_text.get(self._semantic_key(item["text"]), [])
            compatible = [
                row
                for row in matches
                if (row.get("criteria_type") or {}).get("term_uid")
                == term["term_uid"]
            ]
            if matches and len(compatible) != 1:
                self.census.stop(
                    "study_criterion",
                    ref,
                    "exact criterion text exists with conflicting or duplicate type",
                )
                continue
            if len(compatible) == 1:
                uid = compatible[0].get("study_criteria_uid")
                self.census.unchanged.append(
                    {"kind": "study_criterion", "ref": ref, "uid": uid}
                )
            else:
                template_uid = self._ensure_purpose_template(
                    "criterion",
                    study_uid,
                    ref,
                    item["text"],
                    criteria_type_uid=term["term_uid"],
                )
                if template_uid is None:
                    continue
                res = self.api.simple_post_to_api(
                    f"/studies/{study_uid}/study-criteria",
                    {
                        "criteria_data": {
                            "parameter_terms": [],
                            "criteria_template_uid": template_uid,
                            "library_name": "Sponsor",
                        }
                    },
                    "/study-criteria",
                    params={"create_criteria": True},
                )
                if res is None or not res.get("study_criteria_uid"):
                    self.census.stop(
                        "study_criterion", ref, "criteria selection create failed"
                    )
                    continue
                uid = res["study_criteria_uid"]
                self.census.created.append(
                    {"kind": "study_criterion", "ref": ref, "uid": uid}
                )
            self.uid_map["criteria"][ref] = uid

        # Remove only stale selections previously owned by this importer. Human
        # OSB entries have no ledger ref and are never candidates for deletion.
        desired = {
            "objectives": {item["ref"] for item in plan["objectives"]},
            "endpoints": {item["ref"] for item in plan["endpoints"]},
            "criteria": {item["ref"] for item in plan["criteria"]},
        }
        delete_config = {
            "endpoints": ("study-endpoints", "study_endpoint_removed"),
            "criteria": ("study-criteria", "study_criterion_removed"),
            "objectives": ("study-objectives", "study_objective_removed"),
        }
        for kind in ("endpoints", "criteria", "objectives"):
            section, census_kind = delete_config[kind]
            for ref, uid in prior_owned[kind].items():
                if ref in desired[kind] or not uid:
                    continue
                if self.api.simple_delete(
                    f"/studies/{study_uid}/{section}/{uid}", f"/{section}"
                ):
                    self.uid_map[kind].pop(ref, None)
                    self.census.updated.append(
                        {"kind": census_kind, "ref": ref, "uid": uid}
                    )
                else:
                    self.census.stop(census_kind, ref, "stale selection delete failed")

        # Read-after-write count/text/link reconciliation over source-owned refs.
        actual = {
            "objectives": self.api.get_all_from_api(
                f"/studies/{study_uid}/study-objectives", params={"page_size": 0}
            )
            or [],
            "endpoints": self.api.get_all_from_api(
                f"/studies/{study_uid}/study-endpoints", params={"page_size": 0}
            )
            or [],
            "criteria": self.api.get_all_from_api(
                f"/studies/{study_uid}/study-criteria", params={"page_size": 0}
            )
            or [],
        }
        actual_uids = {
            "objectives": {row.get("study_objective_uid") for row in actual["objectives"]},
            "endpoints": {row.get("study_endpoint_uid") for row in actual["endpoints"]},
            "criteria": {row.get("study_criteria_uid") for row in actual["criteria"]},
        }
        for kind in ("objectives", "endpoints", "criteria"):
            missing = sorted(
                ref
                for ref in desired[kind]
                if self.uid_map[kind].get(ref) not in actual_uids[kind]
            )
            if missing:
                self.census.stop(
                    "study_purpose_reconciliation",
                    kind,
                    f"read-after-write missing source refs: {','.join(missing)}",
                )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, study_id=None):
        # Retired authority path. Keep the implementation readable for historical
        # replay/forensics, but never let a normal shadow/enforced environment mutate
        # OSB from the EDC-shaped V1 carrier. Proposal V2 is the sole runtime route.
        assert_unsafe_legacy_mutation_allowed("run_import_360i")
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
            self.same_payload_replay = bool(
                crosswalk["payload_hash"] == record["payload_hash"]
            )
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
                and crosswalk.get("importer_version") == IMPORTER_VERSION
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
                    "Study previously imported as OSB study '%s' — updating in place "
                    "(stored importer %s, current %s).",
                    crosswalk["osb_study_uid"],
                    crosswalk.get("importer_version") or "unknown",
                    IMPORTER_VERSION,
                )
                # Seed the uid map with the previous import's joins so unchanged
                # entities resolve without re-creation.
                for kind, refs in (crosswalk.get("uid_map") or {}).items():
                    if kind in self.uid_map and isinstance(refs, dict):
                        self.uid_map[kind].update(refs)

        project_number = self.ensure_programme_and_project(payload)
        if project_number is None:
            return self._finish(study_id, record, None, None)
        unit_uid_by_name = self.ensure_units(payload)
        codelist_by_ref = self.ensure_codelists(payload)

        study_uid = self.ensure_study(payload, project_number, crosswalk)
        if study_uid is None:
            self._finish(study_id, record, None, project_number)
            return None

        # Unsupported EDC-oriented study properties remain recoverable but do not
        # count as native mappings. Record them on every import attempt (including
        # updates), not only on first study creation.
        for key in sorted(payload.get("study", {}).get("attributes", {})):
            if key == "eligibility" and payload.get("studyPurpose") is not None:
                self.census.carried.append(
                    {
                        "kind": "study_attribute",
                        "ref": key,
                        "reason": (
                            "aggregate eligibility prose retained in x360i:bundleMeta; "
                            "governed native criteria and item-level blockers are "
                            "reconciled through studyPurpose"
                        ),
                    }
                )
                continue
            reason = (
                "no reviewed native OSB landing in this payload version; retained in "
                "x360i:bundleMeta and release-blocking"
            )
            self.census.carried.append(
                {
                    "kind": "study_attribute",
                    "ref": key,
                    "reason": reason,
                }
            )
            self.census.block_release("study_attribute", key, reason)

        (
            epoch_uid_by_visit_ref,
            _scaffolded,
            stale_epochs,
        ) = self.ensure_epochs(payload, study_uid)
        stale_visits = self.ensure_visits(payload, study_uid, epoch_uid_by_visit_ref)
        self.ensure_arms(payload, study_uid)

        # This is the protocol's activity×visit matrix, independent of ODM
        # FORM_REF. It selects only governed exact-match activity concepts.
        self.ensure_native_soa(payload, study_uid)

        # Native Study Purpose is study-definition content, independent of ODM
        # collection fields. Objectives precede their linked endpoints; criteria
        # are reconciled from governed eligibility assertions.
        self.ensure_study_purpose(payload, study_uid)

        # ODM (forms/item-groups/items + the form x visit matrix) — B3.
        self.ensure_odm(payload, study_uid, codelist_by_ref, unit_uid_by_name)

        # Dependents go first: schedules/event links may otherwise prevent OSB
        # from deleting a removed visit, and visits may prevent epoch deletion.
        self.remove_stale_visits(study_uid, stale_visits)
        self.remove_stale_epochs(study_uid, stale_epochs)

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
            if not self.api.simple_approve(
                f"/odms/vendor-namespaces/{ns['uid']}/approvals"
            ):
                self.census.stop(
                    "vendor_namespace", "x360i", "namespace approval failed"
                )
                return {}
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
            if not self.api.simple_approve(
                f"/odms/vendor-attributes/{res['uid']}/approvals"
            ):
                self.census.stop(
                    "vendor_attribute", spec["name"], "attribute approval failed"
                )
                continue
            attr_uid_by_name[spec["name"]] = res["uid"]
            self.census.created.append(
                {"kind": "vendor_attribute", "ref": spec["name"]}
            )
        return attr_uid_by_name

    def _entity_vendor_attributes(
        self,
        attr_uids,
        ref_key,
        ext_json=None,
        field_type=None,
        source_json=None,
        bundle_meta_json=None,
        study_id=None,
        build_hash=None,
    ):
        """The x360i vendor_attributes array for one ODM entity's POST body."""
        attrs = []
        if attr_uids.get("refKey"):
            attrs.append({"uid": attr_uids["refKey"], "value": ref_key})
        if field_type and attr_uids.get("fieldType"):
            attrs.append({"uid": attr_uids["fieldType"], "value": field_type})
        if ext_json and attr_uids.get("ext"):
            attrs.append({"uid": attr_uids["ext"], "value": ext_json})
        if source_json and attr_uids.get("source"):
            attrs.append(
                {"uid": attr_uids["source"], "value": _encode_carrier(source_json)}
            )
        if bundle_meta_json and attr_uids.get("bundleMeta"):
            attrs.append(
                {
                    "uid": attr_uids["bundleMeta"],
                    "value": _encode_carrier(bundle_meta_json),
                }
            )
        if study_id and attr_uids.get("studyId"):
            attrs.append({"uid": attr_uids["studyId"], "value": study_id})
        if build_hash and attr_uids.get("buildHash"):
            attrs.append({"uid": attr_uids["buildHash"], "value": build_hash})
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
        # if any item is already claimed. Keep one canonical ItemDef and mint a
        # deterministic clone for every additional placement. The clone carries
        # the exact source field/refKey, so repeated fields remain visible in
        # every source form instead of being "carried" but absent from the EDC.
        ownership = mapping.item_group_ownership(odm)
        item_owner = ownership["owner"]
        duplicate_placements = {
            (entry["group"], entry["item"]) for entry in ownership["duplicates"]
        }

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
            if not defer_approve and not _approve(kind, path, res["uid"], ref):
                return None
            self.census.created.append({"kind": kind, "ref": ref})
            return res

        def _approve(kind, path, uid, ref):
            if not self.api.simple_approve(f"{path}/{uid}/approvals"):
                self.census.stop(kind, ref, f"{kind} approval failed")
                return False
            return True

        def _patch_through_version(
            kind, path, existing, body, ref, defer_approve=False
        ):
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
            body.setdefault("vendor_attributes", [])
            # OSB's ODM *Patch* inputs require a further set of fields that the
            # matching *Post* inputs treat as optional -- verified against the
            # live OpenAPI, 23 of them across Odm*PatchInput (item groups alone
            # add is_reference_data / sas_dataset_name / origin / purpose /
            # comment). A PATCH that does not intend to change one must still
            # SEND it: null validates, absent is a 400 RequestValidationError.
            # Carry the concept's current values forward, and only fields the
            # fetched object actually carries, so no kind is sent a key its
            # own Patch input does not define.
            for field in PATCH_ONLY_REQUIRED_FIELDS:
                if field not in body and field in existing:
                    body[field] = existing.get(field)
            if self.api.patch_to_api(body, path) is None:
                self.census.stop(kind, ref, f"{kind} patch failed")
                return None
            if not defer_approve and not _approve(kind, path, uid, ref):
                return None
            self.census.updated.append({"kind": kind, "ref": ref})
            return uid

        def _reconcile(kind, path, ref, body_no_content, defer_approve=False):
            """Create / patch-through-version / skip one ODM concept by OID.

            body_no_content is the desired body WITHOUT the content stamp; the
            sha is computed over it, compared against the existing stamp, and
            appended before the write. Returns
            (uid, state, prior_object), where state is created|patched|unchanged.

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
                return (res["uid"], "created", None) if res else (None, None, None)
            if _existing_content_sha(existing) == sha:
                self.census.unchanged.append({"kind": kind, "ref": ref})
                return existing["uid"], "unchanged", existing
            body = dict(body_no_content)
            _stamp_content(body, sha)
            uid = _patch_through_version(
                kind,
                path,
                existing,
                body,
                ref,
                defer_approve=defer_approve,
            )
            return (uid, "patched", existing) if uid else (None, None, existing)

        def _sync_refs(kind, path, uid, ref, desired, prior, state, child_key):
            """Reconcile an ODM reference collection, never silently.

            Missing refs are appended on a Draft version. Removed refs or
            changed relation metadata use the endpoint's explicit override
            mode so the relationship set is atomically replaced.
            """
            current = (prior or {}).get(child_key, []) or []
            current_by_uid = {entry.get("uid"): entry for entry in current}
            desired_by_uid = {entry["uid"]: entry for entry in desired}

            extras = sorted(set(current_by_uid) - set(desired_by_uid))
            changed = []
            compare_keys = {
                "items": ("order_number", "mandatory"),
                "item_groups": ("order_number", "mandatory"),
                "forms": ("order_number", "mandatory", "locked"),
            }[child_key]

            def _relation_value(value):
                if isinstance(value, bool):
                    return "yes" if value else "no"
                normalized = str(value).strip().lower() if value is not None else ""
                return {"true": "yes", "false": "no"}.get(normalized, normalized)

            for child_uid in set(current_by_uid) & set(desired_by_uid):
                if any(
                    _relation_value(current_by_uid[child_uid].get(key))
                    != _relation_value(desired_by_uid[child_uid].get(key))
                    for key in compare_keys
                ):
                    changed.append(child_uid)
            replace = bool(extras or changed)
            missing = [
                entry for entry in desired if entry["uid"] not in current_by_uid
            ]
            if not replace and not missing:
                if state in ("created", "patched"):
                    return _approve(kind, path, uid, ref)
                return True

            if state == "unchanged" and str((prior or {}).get("status") or "").lower() != "draft":
                # A parent left in Draft by an earlier partial run must NOT be
                # re-drafted: POST /versions on a Draft is a 400 ("New draft
                # version can be created only for FINAL versions") and used to
                # census-stop the whole group. Draft only a Final parent.
                if self.api.simple_post_to_api(f"{path}/{uid}/versions", {}) is None:
                    self.census.stop(kind, ref, f"{kind} new-version (draft) failed")
                    return False
            refs_to_write = desired if replace else missing
            result = self.api.simple_post_to_api(
                f"{path}/{uid}/{child_key.replace('_', '-')}",
                refs_to_write,
                params={"override": "true"} if replace else None,
            )
            if result is None and child_key == "items":
                # OSB enforces one OdmItem <-> one OdmItemGroup, and importer
                # versions <=1.8 attached base-OID items to whichever group
                # claimed them first. Under the 1.9+ placement-ownership rules
                # those are STALE holders: override=true replaces only the
                # TARGET group's set and never detaches an item from its
                # current group, so the write is refused with "already
                # connected to another OdmItemGroup". Resolve authoritatively:
                # rewrite each stale holder without the contested items (draft
                # it first if Final, re-approve after), then retry the target.
                desired_uids = {entry["uid"] for entry in desired}
                stale_holders = []
                for holder in self.api.get_all_from_api(
                    path, params={"page_number": 1, "page_size": 0}
                ) or []:
                    if holder.get("uid") == uid:
                        continue
                    held = holder.get(child_key) or []
                    contested = [c for c in held if c.get("uid") in desired_uids]
                    if contested:
                        stale_holders.append((holder, contested))
                for holder, contested in stale_holders:
                    h_uid = holder["uid"]
                    if (holder.get("status") or "").lower() != "draft":
                        self.api.simple_post_to_api(f"{path}/{h_uid}/versions", {})
                    keep = [c for c in (holder.get(child_key) or [])
                            if c.get("uid") not in desired_uids]
                    kept_refs = []
                    for order, entry in enumerate(keep, start=1):
                        mand = entry.get("mandatory")
                        if isinstance(mand, bool):
                            mand = "Yes" if mand else "No"
                        kept_refs.append({
                            "uid": entry["uid"],
                            "order_number": entry.get("order_number") or order,
                            "mandatory": mand or "No",
                            "key_sequence": None,
                            "method_oid": None,
                            "imputation_method_oid": None,
                            "role": None,
                            "role_codelist_oid": None,
                            "collection_exception_condition_oid": None,
                            "vendor": {"attributes": []},
                        })
                    detached = self.api.simple_post_to_api(
                        f"{path}/{h_uid}/{child_key.replace('_', '-')}",
                        kept_refs,
                        params={"override": "true"},
                    )
                    self.log.info(
                        "Detached %d stale item(s) from %s (%s): %s",
                        len(contested), holder.get("oid") or h_uid, h_uid,
                        "ok" if detached is not None else "FAILED",
                    )
                    if detached is not None:
                        self.api.simple_approve(f"{path}/{h_uid}/approvals")
                if stale_holders:
                    if state == "unchanged":
                        self.api.simple_post_to_api(f"{path}/{uid}/versions", {})
                    refs_to_write = desired
                    replace = True
                    result = self.api.simple_post_to_api(
                        f"{path}/{uid}/{child_key.replace('_', '-')}",
                        refs_to_write,
                        params={"override": "true"},
                    )
            elif result is None and not replace:
                # Non-items relationship refused in append mode (e.g. a parent
                # no longer in Draft): draft the parent and assert the desired
                # set once.
                self.api.simple_post_to_api(f"{path}/{uid}/versions", {})
                refs_to_write = desired
                replace = True
                result = self.api.simple_post_to_api(
                    f"{path}/{uid}/{child_key.replace('_', '-')}",
                    refs_to_write,
                    params={"override": "true"},
                )
            if result is None:
                self.census.stop(
                    kind,
                    ref,
                    f"{kind} {child_key} POST failed "
                    f"({len(refs_to_write)} refs, override={replace})",
                )
                return False
            return _approve(kind, path, uid, ref)

        # Items (all groups, all forms), then groups, then forms — leaf first.
        placement_item_uids = {}
        existing_items_by_oid = {}
        if self.same_payload_replay:
            # One bulk snapshot avoids 1,200 serial filtered GETs during a
            # carrier-only upgrade or exact-payload partial repair. A previous
            # interrupted attempt may already have created some placement
            # clones; reusing them is safe because the source hash is identical.
            existing_items_by_oid = {
                item.get("oid"): item
                for item in (self.api.get_all_from_api("/odms/items") or [])
                if item.get("oid")
            }
        for form in odm.get("forms", []):
            for group in form.get("itemGroups", []):
                for item in group.get("items", []):
                    group_ref = group["refKey"]
                    item_ref = item["refKey"]
                    placement_key = mapping.placement_item_key(group_ref, item_ref)
                    item_oid = mapping.placement_item_oid(
                        item_ref, group_ref, item_owner[item_ref]
                    )
                    body = mapping.odm_item_body(item, codelist_by_ref, unit_uid_by_name)
                    body["oid"] = item_oid
                    body["vendor_attributes"] = self._entity_vendor_attributes(
                        attr_uids,
                        item_ref,
                        ext_json=mapping.vendor_ext_value(item),
                        field_type=item.get("datatypeHint"),
                    )
                    if item.get("prompt"):
                        body["translated_texts"] = [
                            {"text_type": "Question", "language": "en", "text": item["prompt"]}
                        ]
                    existing_item = existing_items_by_oid.get(item_oid)
                    if self.same_payload_replay and existing_item:
                        # An exact-payload replay must not rewrite the 1,180
                        # unchanged base ItemDefs. Exact fields ride their
                        # parent FormDef; existing placement clones are equally
                        # reusable. A changed payload does not set this flag.
                        uid = existing_item["uid"]
                        if (existing_item.get("status") or "").lower() == "draft":
                            if not self.api.simple_approve(
                                f"/odms/items/{uid}/approvals"
                            ):
                                self.census.stop(
                                    "item", item_oid, "draft item approval failed"
                                )
                                continue
                        self.census.unchanged.append(
                            {"kind": "item", "ref": item_oid}
                        )
                    else:
                        uid, _state, _prior = _reconcile(
                            "item", "/odms/items", item_oid, body
                        )
                    if uid:
                        placement_item_uids[(group_ref, item_ref)] = uid
                        self.uid_map["items"][placement_key] = uid
                        if (group_ref, item_ref) not in duplicate_placements:
                            # Preserve the historic base-item lookup for ledger
                            # compatibility while adding placement-scoped keys.
                            self.uid_map["items"][item_ref] = uid

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
                uid, state, prior = _reconcile(
                    "item_group", "/odms/item-groups", group["refKey"], body,
                    defer_approve=True,
                )
                if uid is None:
                    continue
                self.uid_map["item_groups"][group["refKey"]] = uid
                group_ref = group["refKey"]
                item_refs = []
                for local_order, item in enumerate(group.get("items", []), start=1):
                    placement = (group_ref, item["refKey"])
                    if placement not in placement_item_uids:
                        continue
                    item_refs.append(
                        {
                            "uid": placement_item_uids[placement],
                            # ODM order_number is local to this ItemGroup.
                            # The source field's form-wide order remains in its
                            # exact x360i:source carrier for Leg C restoration.
                            "order_number": local_order,
                            "mandatory": "yes" if item.get("mandatory") else "no",
                            "key_sequence": None,
                            "method_oid": None,
                            "imputation_method_oid": None,
                            "role": None,
                            "role_codelist_oid": None,
                            "collection_exception_condition_oid": None,
                            "vendor": {"attributes": []},
                        }
                    )
                _sync_refs(
                    "item_group_items",
                    "/odms/item-groups",
                    uid,
                    group_ref,
                    item_refs,
                    prior,
                    state,
                    "items",
                )

        # Forms + their item-group refs.
        bundle_meta = mapping.bundle_meta_value(payload)
        forms = odm.get("forms", [])
        bundle_meta_carriers = _chunk_carrier(bundle_meta, max(len(forms), 1))
        for form_index, form in enumerate(forms):
            body = {
                "name": form["name"][:200],
                "oid": form["refKey"],
                "sdtm_version": None,
                "repeating": "no",
                "translated_texts": [
                    {"text_type": "Description", "language": "en", "text": form.get("description") or form["name"]}
                ],
                "vendor_attributes": self._entity_vendor_attributes(
                    attr_uids,
                    form["refKey"],
                    ext_json=mapping.vendor_ext_value(form),
                    source_json=mapping.source_form_value(payload, form["refKey"]),
                    bundle_meta_json=(
                        bundle_meta_carriers[form_index]
                        if form_index < len(bundle_meta_carriers)
                        else None
                    ),
                    study_id=payload["source"]["studyId"],
                    build_hash=payload["source"]["buildHash"],
                ),
            }
            # Create as Draft: ITEM_GROUP_REFs attach only to a Draft element,
            # so defer approval until after they're wired.
            uid, state, prior = _reconcile(
                "form", "/odms/forms", form["refKey"], body, defer_approve=True
            )
            if uid is None:
                continue
            self.uid_map["forms"][form["refKey"]] = uid
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
            _sync_refs(
                "form_item_groups",
                "/odms/forms",
                uid,
                form["refKey"],
                group_refs,
                prior,
                state,
                "item_groups",
            )

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
            event_oid = f"SE.360I.{study_id}.{visit_ref}"
            existing = _find_by_oid("/odms/study-events", event_oid)
            if not assignments:
                if existing:
                    event_uid = existing["uid"]
                    self.uid_map["study_events"][visit_ref] = event_uid
                    self.census.unchanged.append(
                        {"kind": "study_event", "ref": event_oid}
                    )
                    _sync_refs(
                        "study_event_forms",
                        "/odms/study-events",
                        event_uid,
                        event_oid,
                        [],
                        existing,
                        "unchanged",
                        "forms",
                    )
                continue

            event_body = {
                "name": visit["name"][:200],
                "oid": event_oid,
                "description": f"Visit '{visit['name']}' imported from 360i study "
                f"{study_id}; FORM_REFs are the visit's scheduled forms.",
            }
            if existing:
                event_uid = existing["uid"]
                self.uid_map["study_events"][visit_ref] = event_uid
                event_changed = any(
                    existing.get(key) != value
                    for key, value in event_body.items()
                )
                if event_changed:
                    if (
                        self.api.simple_post_to_api(
                            f"/odms/study-events/{event_uid}/versions", {}
                        )
                        is None
                    ):
                        self.census.stop(
                            "study_event",
                            event_oid,
                            "study_event new-version (draft) failed",
                        )
                        continue
                    patch_body = {
                        **event_body,
                        "uid": event_uid,
                        "effective_date": existing.get("effective_date"),
                        "retired_date": existing.get("retired_date"),
                        "display_in_tree": existing.get("display_in_tree", True),
                        "change_description": "360i re-import: event changed",
                    }
                    if (
                        self.api.patch_to_api(
                            patch_body, "/odms/study-events"
                        )
                        is None
                    ):
                        self.census.stop(
                            "study_event", event_oid, "study_event patch failed"
                        )
                        continue
                    event_state = "patched"
                    self.census.updated.append(
                        {"kind": "study_event", "ref": event_oid}
                    )
                else:
                    event_state = "unchanged"
                    self.census.unchanged.append(
                        {"kind": "study_event", "ref": event_oid}
                    )
            else:
                # Create as Draft; FORM_REFs attach only before approval.
                res = self.api.simple_post_to_api(
                    "/odms/study-events", event_body
                )
                if res is None:
                    self.census.stop(
                        "study_event", event_oid, "study_event create failed"
                    )
                    continue
                event_uid = res["uid"]
                self.uid_map["study_events"][visit_ref] = event_uid
                self.census.created.append(
                    {"kind": "study_event", "ref": event_oid}
                )
                event_state = "created"

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
            if _sync_refs(
                "study_event_forms",
                "/odms/study-events",
                event_uid,
                event_oid,
                form_refs,
                existing,
                event_state,
                "forms",
            ):
                self.census.created.append(
                    {"kind": "form_refs", "ref": event_oid, "count": len(form_refs)}
                )
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
    result = None
    try:
        result = importer.run(study_id=args.study)
    finally:
        importer.db.close()
    metr.print()
    return 0 if result and result.get("status") == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
