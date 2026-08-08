"""Project an OSB study into an AccuraTrial EDC StudyBundleV1 and (optionally)
push it to the EDC's import endpoint.

THE CONTRACT (EDC side, `validateStudyBundle` in study-bundle.service.ts):
    formatVersion === '1.0', study.name present, visits[] array,
    visitFormAssignments[] array, forms.forms[] array (each form: name +
    fields array). Everything else is optional; unknown keys are ignored but
    REPORTED by the EDC's import census — so this exporter carries an
    `_exportCensus` of its own and never relies on silence.

WHAT IT READS:
  * StudyService/StudyVisitService/StudyArmSelectionService — the study
    definition (title, registry ids, visit calendar, arms).
  * OdmStudyEventService/OdmFormService/OdmItemGroupService/OdmItemService —
    the CRF metadata. For studies imported by the 360i importer, study-events
    are per-visit containers whose OID carries the visit refKey
    (SE.360I.<studyId>.<visitRef>) and items carry `x360i:fieldType` — the
    lossless type restoration path. Native OSB studies fall back to
    name-joins, with every ambiguity censused.

FIELD TYPES: two-tier, never silent — see edc_field_types.py. The EDC maps
unknown types to 'text' without a word; this exporter refuses to participate
in that: every downgrade is a census row.
"""

import json
import logging
import re
from typing import Any

import httpx

from clinical_mdr_api.services.integrations.edc_field_types import (
    resolve_edc_field_type,
)
from common import config

log = logging.getLogger(__name__)

EDC_BUNDLE_FORMAT_VERSION = "1.0"
X360I_EVENT_OID = re.compile(r"^SE\.360I\.(?P<study_id>.+)\.(?P<visit_ref>[^.]+)$")


class EdcExportError(Exception):
    """A study that cannot produce a valid bundle — the caller maps to 422."""


def _sanitize_ref(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_").upper()
    return cleaned or "REF"


def _vendor_attr(entity: dict, name: str) -> str | None:
    """Read one x360i vendor attribute value off an ODM response model."""
    for attr in entity.get("vendor_attributes", []) or []:
        if attr.get("name") == name:
            return attr.get("value")
    return None


def _english_text(entity: dict, text_type: str) -> str | None:
    for tt in entity.get("translated_texts", []) or []:
        if tt.get("text_type") == text_type and tt.get("language", "").startswith("en"):
            return tt.get("text")
    return None


class EdcExportService:
    """Pure projection over the OSB services; push is a separate call."""

    def __init__(self):
        # Imported here (not module level) so importing this module never
        # drags the whole service graph in (test collection, docs build).
        from clinical_mdr_api.services.odms.forms import OdmFormService
        from clinical_mdr_api.services.odms.item_groups import OdmItemGroupService
        from clinical_mdr_api.services.odms.items import OdmItemService
        from clinical_mdr_api.services.odms.study_events import OdmStudyEventService
        from clinical_mdr_api.services.studies.study import StudyService
        from clinical_mdr_api.services.studies.study_visit import StudyVisitService

        self.study_service = StudyService()
        self.visit_service_cls = StudyVisitService
        self.study_event_service = OdmStudyEventService()
        self.form_service = OdmFormService()
        self.item_group_service = OdmItemGroupService()
        self.item_service = OdmItemService()
        self.census: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def build_bundle(self, study_uid: str) -> dict[str, Any]:
        self.census = []
        study = self.study_service.get_by_uid(study_uid)
        study_dict = study.model_dump() if hasattr(study, "model_dump") else dict(study)

        ident = (study_dict.get("current_metadata") or {}).get(
            "identification_metadata"
        ) or {}
        desc = (study_dict.get("current_metadata") or {}).get("study_description") or {}

        name = (
            ident.get("study_acronym")
            or ident.get("study_id")
            or desc.get("study_title")
            or study_uid
        )

        visits, visit_ref_by_uid, visit_ref_by_name = self._visits(study_uid)
        # Scope the form projection to THIS study's own forms (those reachable
        # from its study-events' FORM_REFs), not every form in the shared ODM
        # library — otherwise a multi-study instance leaks OSB's baked DDF seed
        # forms and other studies' forms into the bundle.
        event_form_uids = self._study_event_form_uids(visit_ref_by_name, visits)
        forms, form_ref_by_uid, form_ref_by_oid = self._forms(event_form_uids)
        assignments = self._assignments(
            study_uid, visit_ref_by_name, form_ref_by_uid, form_ref_by_oid, visits
        )
        arms = self._group_classes(study_uid)

        registry = ident.get("registry_identifiers") or {}
        bundle: dict[str, Any] = {
            "formatVersion": EDC_BUNDLE_FORMAT_VERSION,
            "exportedAt": "1970-01-01T00:00:00.000Z",
            "exportedBy": "openstudybuilder-edc-export",
            "sourceStudyName": str(name),
            "study": {
                "name": str(name),
                **(
                    {"officialTitle": desc.get("study_title")}
                    if desc.get("study_title")
                    else {}
                ),
                **(
                    {"secondaryIdentifier": registry.get("ct_gov_id")}
                    if registry.get("ct_gov_id")
                    else {}
                ),
                **(
                    {"studyAcronym": ident.get("study_acronym")}
                    if ident.get("study_acronym")
                    else {}
                ),
                "studyParameters": {},
            },
            "visits": visits,
            "visitFormAssignments": assignments,
            "forms": {
                "formatVersion": EDC_BUNDLE_FORMAT_VERSION,
                "exportedAt": "1970-01-01T00:00:00.000Z",
                "exportedBy": "openstudybuilder-edc-export",
                "forms": forms,
            },
            **({"studyGroupClasses": arms} if arms else {}),
            "_exportCensus": {
                "rows": self.census,
                "counts": {
                    "total": len(self.census),
                    "downgrades": sum(
                        1 for r in self.census if r["kind"] == "field_type_downgrade"
                    ),
                    "ambiguous_joins": sum(
                        1 for r in self.census if r["kind"] == "ambiguous_join"
                    ),
                },
            },
        }

        # The EDC's own hard validation, applied HERE so a bad bundle fails on
        # this side of the wire with a nameable reason.
        if not bundle["study"]["name"].strip():
            raise EdcExportError("study.name resolved empty — the EDC refuses this")
        if not forms:
            raise EdcExportError(
                "study has no ODM forms reachable from its study events — "
                "nothing for an EDC to import"
            )
        return bundle

    def _visits(self, study_uid: str):
        result = self.visit_service_cls.get_all_visits(study_uid, page_size=0)
        items = result.items if hasattr(result, "items") else result
        visits = []
        ref_by_uid: dict[str, str] = {}
        ref_by_name: dict[str, str] = {}
        used: set[str] = set()
        ordered = sorted(
            (v.model_dump() if hasattr(v, "model_dump") else dict(v) for v in items),
            key=lambda v: (v.get("visit_number") or 0),
        )
        for i, v in enumerate(ordered, start=1):
            base = _sanitize_ref(v.get("visit_short_name") or v.get("visit_name") or f"V{i}")
            ref = base
            n = 2
            while ref in used:
                ref = f"{base}_{n}"
                n += 1
            used.add(ref)
            ref_by_uid[v["uid"]] = ref
            ref_by_name[(v.get("visit_name") or "").strip().lower()] = ref

            visit_class = str(v.get("visit_class") or "")
            visit: dict[str, Any] = {
                "refKey": ref,
                "name": v.get("visit_name") or ref,
                "ordinal": i,
                "type": "unscheduled" if "UNSCHEDULED" in visit_class else "scheduled",
                "repeating": False,
            }
            epoch = v.get("study_epoch") or {}
            epoch_name = epoch.get("sponsor_preferred_name")
            if epoch_name:
                visit["category"] = epoch_name
            day = v.get("study_day_number")
            if day is not None:
                visit["scheduleDay"] = int(day)
                min_w = v.get("min_visit_window_value")
                max_w = v.get("max_visit_window_value")
                # OSB defaults ±9999 mean "no window stated" — never export those.
                if min_w is not None and abs(min_w) < 9999:
                    visit["minDay"] = int(day) + int(min_w)
                if max_w is not None and abs(max_w) < 9999:
                    visit["maxDay"] = int(day) + int(max_w)
            visits.append(visit)
        return visits, ref_by_uid, ref_by_name

    def _study_event_form_uids(self, visit_ref_by_name, visits):
        """OdmForm UIDs reached from THIS study's study-events' FORM_REFs. A
        study-event belongs to the study when its OID carries a known visit
        refKey (SE.360I.<studyId>.<visitRef>) or its name equals a visit name
        (native OSB studies). These are the forms actually scheduled onto the
        study calendar; forms the study defines but never schedules are added
        by the x360i stamp in _forms (the direct 360i->EDC path ships those too)."""
        events_result = self.study_event_service.get_all_odms(page_size=0)
        events = (
            events_result.items if hasattr(events_result, "items") else events_result
        )
        visit_refs = {v["refKey"] for v in visits}
        form_uids: set[str] = set()
        for event_model in events:
            event = (
                event_model.model_dump()
                if hasattr(event_model, "model_dump")
                else dict(event_model)
            )
            oid = event.get("oid") or ""
            match = X360I_EVENT_OID.match(oid)
            claims_visit = bool(match and match.group("visit_ref") in visit_refs)
            if not claims_visit:
                claims_visit = (event.get("name") or "").strip().lower() in visit_ref_by_name
            if not claims_visit:
                continue
            for form_ref_model in event.get("forms", []) or []:
                if form_ref_model.get("uid"):
                    form_uids.add(form_ref_model["uid"])
        return form_uids

    def _forms(self, event_form_uids=None):
        """The study's ODM forms, projected with sections (item groups) and
        fields (items). refKey = the form's OID when it has one (the 360i
        importer sets OID = refKey), else a sanitized name.

        Scoping: a form is projected when it is reached from this study's
        study-events (event_form_uids) OR it carries an x360i vendor stamp (a
        360i-created form, including ones the study defines but never schedules).
        This excludes OSB's baked DDF seed forms and other unrelated library
        forms. When event_form_uids is None AND no form carries an x360i stamp
        (a fully-native OSB study), fall back to projecting the whole library so
        the bundle is never silently empty."""
        forms_result = self.form_service.get_all_odms(page_size=0)
        forms_items = (
            forms_result.items if hasattr(forms_result, "items") else forms_result
        )
        all_forms = [
            form_model.model_dump()
            if hasattr(form_model, "model_dump")
            else dict(form_model)
            for form_model in forms_items
        ]
        event_form_uids = event_form_uids or set()

        def _is_x360i(form):
            # A 360i-created form carries at least one x360i vendor attribute
            # (refKey/content/ext); OSB's baked DDF seed forms carry none.
            return any(
                attr.get("name") in ("refKey", "content", "ext", "fieldType")
                for attr in (form.get("vendor_attributes") or [])
            )

        any_x360i = any(_is_x360i(f) for f in all_forms)

        def _in_scope(form):
            if form.get("uid") in event_form_uids:
                return True
            if _is_x360i(form):
                return True
            # Fully-native OSB study (no x360i stamps anywhere, no matching
            # study-events): don't silently emit an empty bundle — project all.
            return not any_x360i and not event_form_uids

        forms = []
        ref_by_uid: dict[str, str] = {}
        ref_by_oid: dict[str, str] = {}
        used: set[str] = set()

        for form in all_forms:
            if not _in_scope(form):
                continue
            base = _sanitize_ref(form.get("oid") or form.get("name"))
            ref = base
            n = 2
            while ref in used:
                ref = f"{base}_{n}"
                n += 1
            used.add(ref)
            ref_by_uid[form["uid"]] = ref
            if form.get("oid"):
                ref_by_oid[form["oid"]] = ref

            sections = []
            fields = []
            for gi, group_ref in enumerate(form.get("item_groups", []) or [], start=1):
                group_uid = group_ref.get("uid")
                group = self.item_group_service.get_by_uid(group_uid)
                group = (
                    group.model_dump() if hasattr(group, "model_dump") else dict(group)
                )
                section_id = _sanitize_ref(group.get("oid") or group.get("name"))
                sections.append(
                    {
                        "id": section_id,
                        "name": group.get("name"),
                        "order": group_ref.get("order_number") or gi,
                    }
                )
                for ii, item_ref in enumerate(group.get("items", []) or [], start=1):
                    item = self.item_service.get_by_uid(item_ref.get("uid"))
                    item = (
                        item.model_dump() if hasattr(item, "model_dump") else dict(item)
                    )
                    fields.append(
                        self._field(
                            form_ref=ref,
                            section_name=group.get("name"),
                            item=item,
                            item_ref=item_ref,
                            order=ii,
                        )
                    )
            forms.append(
                {
                    "refKey": ref,
                    "name": form.get("name"),
                    **(
                        {"description": _english_text(form, "Description")}
                        if _english_text(form, "Description")
                        else {}
                    ),
                    "sections": sections,
                    "fields": fields,
                    "editChecks": [],
                    "validationRuleRecords": [],
                    "formLinks": [],
                }
            )
        return forms, ref_by_uid, ref_by_oid

    def _field(self, form_ref, section_name, item, item_ref, order):
        stamped_type = _vendor_attr(item, "fieldType")
        has_codelist = bool(item.get("codelist"))
        multi = bool((item.get("codelist") or {}).get("allows_multi_choice"))
        edc_type, downgrade = resolve_edc_field_type(
            item.get("datatype"), stamped_type, has_codelist, multi
        )
        field_ref = _sanitize_ref(item.get("oid") or item.get("name"))
        if downgrade:
            self.census.append(
                {
                    "kind": "field_type_downgrade",
                    "ref": f"{form_ref}/{field_ref}",
                    "detail": downgrade,
                }
            )

        field: dict[str, Any] = {
            "refKey": field_ref,
            "name": item.get("name"),
            "type": edc_type,
            "order": item_ref.get("order_number") or order,
        }
        prompt = item.get("prompt") or _english_text(item, "Question")
        if prompt:
            field["label"] = prompt
        mandatory = item_ref.get("mandatory")
        if mandatory is not None:
            field["required"] = str(mandatory).lower() in ("yes", "true", "1")
        if item.get("length") is not None:
            field["length"] = item["length"]
        if item.get("sds_var_name"):
            field["sdtmVariable"] = item["sds_var_name"]
        if section_name:
            field["section"] = section_name
            field["group"] = section_name
        units = item.get("unit_definitions") or []
        if units:
            field["unit"] = units[0].get("name")
        terms = item.get("terms") or []
        if terms:
            field["options"] = [
                {
                    "label": t.get("display_text") or t.get("name") or str(t.get("uid")),
                    "value": t.get("display_text") or t.get("name") or str(t.get("uid")),
                    "order": t.get("order") or ti + 1,
                }
                for ti, t in enumerate(terms)
            ]
        # Restore carried 360i extensions (helpText, showWhen, validation
        # rules, SDTM annotation parts) from the ext blob, when stamped.
        ext_json = _vendor_attr(item, "ext")
        if ext_json:
            try:
                ext = json.loads(ext_json)
            except (TypeError, ValueError):
                ext = {}
                self.census.append(
                    {
                        "kind": "ext_unparseable",
                        "ref": f"{form_ref}/{field_ref}",
                        "detail": "x360i:ext is not valid JSON; extensions not restored",
                    }
                )
            for key in ("helpText", "placeholder", "format", "sdtmAnnotation",
                        "sdtmDomain", "sdtmTestcd", "sdtmQnam"):
                if key in ext and key not in field:
                    field[key] = ext[key] if not isinstance(ext[key], str) else ext[key]
            for key in ("showWhen", "requiredWhen", "validationRules", "options",
                        "min", "max", "criteriaItems"):
                if key in ext and key not in field:
                    try:
                        field[key] = json.loads(ext[key]) if isinstance(ext[key], str) else ext[key]
                    except (TypeError, ValueError):
                        pass
        return field

    def _assignments(
        self, study_uid, visit_ref_by_name, form_ref_by_uid, form_ref_by_oid, visits
    ):
        """visitFormAssignments from OdmStudyEvent FORM_REFs.

        360i-imported studies: the study-event OID carries the visit refKey
        (SE.360I.<studyId>.<visitRef>) — an exact join. Native OSB studies:
        join by study-event name == visit name; ambiguity is censused and the
        assignment still ships (better a nameable guess than a dropped cell).
        """
        events_result = self.study_event_service.get_all_odms(page_size=0)
        events = (
            events_result.items if hasattr(events_result, "items") else events_result
        )
        visit_refs = {v["refKey"] for v in visits}
        assignments = []
        for event_model in events:
            event = (
                event_model.model_dump()
                if hasattr(event_model, "model_dump")
                else dict(event_model)
            )
            oid = event.get("oid") or ""
            match = X360I_EVENT_OID.match(oid)
            visit_ref = None
            if match:
                candidate = match.group("visit_ref")
                if candidate in visit_refs:
                    visit_ref = candidate
            if visit_ref is None:
                by_name = visit_ref_by_name.get((event.get("name") or "").strip().lower())
                if by_name:
                    visit_ref = by_name
                    if not match:
                        self.census.append(
                            {
                                "kind": "ambiguous_join",
                                "ref": oid or event.get("name", "?"),
                                "detail": "study-event joined to visit by NAME equality "
                                "(no x360i OID stamp); verify the calendar",
                            }
                        )
            if visit_ref is None:
                # An event no visit claims — not this study's calendar.
                continue
            for fi, form_ref_model in enumerate(event.get("forms", []) or [], start=1):
                form_uid = form_ref_model.get("uid")
                ref = form_ref_by_uid.get(form_uid)
                if ref is None:
                    self.census.append(
                        {
                            "kind": "dangling_form_ref",
                            "ref": f"{oid}/{form_uid}",
                            "detail": "FORM_REF names a form this export did not project",
                        }
                    )
                    continue
                assignments.append(
                    {
                        "visitRef": visit_ref,
                        "formRef": ref,
                        "required": str(form_ref_model.get("mandatory", "")).lower()
                        in ("yes", "true", "1"),
                        "ordinal": form_ref_model.get("order_number") or fi,
                    }
                )
        return assignments

    def _group_classes(self, study_uid):
        from clinical_mdr_api.services.studies.study_arm_selection import (
            StudyArmSelectionService,
        )

        service = StudyArmSelectionService()
        result = service.get_all_selection(study_uid=study_uid)
        arms = result.items if hasattr(result, "items") else result
        groups = []
        for arm_model in arms or []:
            arm = (
                arm_model.model_dump()
                if hasattr(arm_model, "model_dump")
                else dict(arm_model)
            )
            groups.append(
                {
                    "name": arm.get("name"),
                    **(
                        {"description": arm.get("description")}
                        if arm.get("description")
                        else {}
                    ),
                }
            )
        if not groups:
            return []
        return [
            {
                "name": "Arms",
                "groupClassTypeName": "Arm",
                "groups": groups,
            }
        ]

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    def send_to_edc(self, study_uid: str, dry_run: bool = True) -> dict[str, Any]:
        """Push the bundle to the EDC's import-study-bundle with the M2M
        x-api-key. ALWAYS available as dry_run first; the caller decides.
        """
        base_url = getattr(config.settings, "edc_base_url", "") or ""
        api_key = ""
        key_setting = getattr(config.settings, "edc_api_key", None)
        if key_setting is not None:
            api_key = (
                key_setting.get_secret_value()
                if hasattr(key_setting, "get_secret_value")
                else str(key_setting)
            )
        if not base_url or not api_key:
            raise EdcExportError(
                "EDC push is not configured: set EDC_BASE_URL and EDC_API_KEY"
            )

        bundle = self.build_bundle(study_uid)
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/forms/import-study-bundle",
            json={"bundle": bundle, "dryRun": dry_run},
            headers={"x-api-key": api_key},
            timeout=120.0,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        return {
            "dryRun": dry_run,
            "statusCode": response.status_code,
            "edcResponse": body,
            "exportCensus": bundle.get("_exportCensus"),
        }
