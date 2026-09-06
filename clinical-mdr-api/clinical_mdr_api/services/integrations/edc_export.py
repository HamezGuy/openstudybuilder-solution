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

import base64
import gzip
import json
import logging
import re
from copy import deepcopy
from typing import Any

import httpx

from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyComponentEnum,
)
from clinical_mdr_api.services.integrations.edc_field_types import (
    resolve_edc_field_type,
)
from clinical_mdr_api.services.integrations.edc_source_snapshot import (
    SourceSnapshotError,
    is_source_snapshot,
    read_source_snapshot,
)
from common import config

log = logging.getLogger(__name__)

EDC_BUNDLE_FORMAT_VERSION = "1.0"
X360I_EVENT_OID = re.compile(r"^SE\.360I\.(?P<study_id>.+)\.(?P<visit_ref>[^.]+)$")
X360I_STUDY_DESCRIPTION = re.compile(
    r"^Imported from 360i study (?P<study_id>.+?) \(build [^)]+\)$"
)
CARRIER_COMPRESSION_PREFIX = "gzip+base64:"
CARRIER_CHUNK_PREFIX = "chunk:"


def authority_disclosure(mode: str, source_overlay_active: bool) -> dict[str, Any]:
    """Describe who actually controls this V1 projection.

    Until the released-version package replaces the source restoration helpers,
    this exporter is not allowed to claim OSB mapping authority. Keeping the
    verdict in one pure function makes the API response, warning, and tests agree.
    """
    return {
        "mode": mode,
        # Name the source that actually controls this carrier projection. The
        # non-authoritative/deployment flags below prevent it being mistaken for
        # OSB authority; calling an active overlay "none" instead concealed the
        # exact provenance reviewers need to diagnose mapping differences.
        "mappingAuthority": (
            "legacy-source-overlay"
            if source_overlay_active
            else "none-legacy-comparison"
        ),
        "authoritative": False,
        "deploymentAllowed": False,
        "sourceOverlayActive": source_overlay_active,
        "studyDefinitionStandard": "CDISC USDM 4",
        "crfMetadataStandard": "CDISC ODM 1.3.2",
    }


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


def _carrier_json(value: str) -> Any:
    """Decode a plain or transport-compressed x360i JSON carrier."""
    if value.startswith(CARRIER_COMPRESSION_PREFIX):
        encoded = value[len(CARRIER_COMPRESSION_PREFIX) :]
        value = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    return json.loads(value)


def _source_study_id(study: dict[str, Any]) -> str | None:
    """Recover the 360i identity stamped on the OSB study at import time."""
    identification = (study.get("current_metadata") or {}).get(
        "identification_metadata"
    ) or {}
    match = X360I_STUDY_DESCRIPTION.match(
        str(identification.get("description") or study.get("description") or "")
    )
    return match.group("study_id") if match else None


def _english_text(entity: dict, text_type: str) -> str | None:
    for tt in entity.get("translated_texts", []) or []:
        if tt.get("text_type") == text_type and tt.get("language", "").startswith("en"):
            return tt.get("text")
    return None


def _term_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    name = value.get("sponsor_preferred_name") or value.get("name")
    return str(name).strip() if name else None


def _term_names(values: Any) -> str | None:
    names = [_term_name(value) for value in (values or [])]
    present = list(dict.fromkeys(name for name in names if name))
    return "; ".join(present) if present else None


def _phase_for_edc(value: Any) -> str | None:
    name = _term_name(value)
    if not name:
        return None
    match = re.search(r"\b(?:phase\s*)?([1-4]|I{1,3}|IV)\b", name, re.IGNORECASE)
    if not match:
        return name
    token = match.group(1).upper()
    return {"1": "I", "2": "II", "3": "III", "4": "IV"}.get(token, token)


def _native_study_projection(study: dict[str, Any]) -> dict[str, Any]:
    """Project OSB-owned StudyMetadata to the portable EDC study contract.

    Only fields with an actual native OSB representation are emitted. Missing is
    omitted, ``False`` is preserved, and CT objects become their canonical OSB
    display values rather than opaque UIDs. Source-carrier fields are merged later
    and may fill only properties this projection does not own.
    """
    metadata = study.get("current_metadata") or {}
    ident = metadata.get("identification_metadata") or {}
    description = metadata.get("study_description") or {}
    design = metadata.get("high_level_study_design") or {}
    population = metadata.get("study_population") or {}
    intervention = metadata.get("study_intervention") or {}
    registry = ident.get("registry_identifiers") or {}

    title = description.get("study_title")
    acronym = ident.get("study_acronym")
    study_id = ident.get("study_id")
    result: dict[str, Any] = {
        "name": str(acronym or study_id or title or study.get("uid") or ""),
        "studyParameters": {},
    }

    def put(key, value):
        if value is not None and value != "":
            result[key] = value

    put("uniqueIdentifier", study_id)
    put("secondaryIdentifier", registry.get("ct_gov_id"))
    put("nctNumber", registry.get("ct_gov_id"))
    put("officialTitle", title)
    put("studyAcronym", acronym)

    study_type = _term_name(design.get("study_type_code"))
    put("protocolType", study_type.casefold() if study_type else None)
    put("phase", _phase_for_edc(design.get("trial_phase_code")))

    randomized = intervention.get("is_trial_randomised")
    if isinstance(randomized, bool):
        result["allocation"] = "Randomized" if randomized else "Non-Randomized"
    masking = _term_name(intervention.get("trial_blinding_schema_code"))
    put("masking", masking.replace(" ", "-") if masking == "Open Label" else masking)
    put("control", _term_name(intervention.get("control_type_code")))
    put("assignment", _term_name(intervention.get("intervention_model_code")))
    put("purpose", _term_names(intervention.get("trial_intent_types_codes")))

    put("expectedTotalEnrollment", population.get("number_of_expected_subjects"))
    sex = _term_name(population.get("sex_of_participants_code"))
    put("gender", sex.casefold() if sex else None)
    for target, source in (
        ("ageMin", "planned_minimum_age_of_subjects"),
        ("ageMax", "planned_maximum_age_of_subjects"),
    ):
        duration = population.get(source)
        if isinstance(duration, dict) and duration.get("duration_value") is not None:
            result[target] = str(duration["duration_value"])
    healthy = population.get("healthy_subject_indicator")
    if isinstance(healthy, bool):
        result["healthyVolunteerAccepted"] = healthy
    put("therapeuticArea", _term_names(population.get("therapeutic_area_codes")))
    put(
        "indication",
        _term_names(population.get("disease_condition_or_indication_codes")),
    )
    put("conditions", _term_names(population.get("diagnosis_group_codes")))
    return result


class EdcExportService:
    """Pure projection over the OSB services; push is a separate call."""

    def __init__(self):
        # Imported here (not module level) so importing this module never
        # drags the whole service graph in (test collection, docs build).
        from clinical_mdr_api.services.odms.conditions import OdmConditionService
        from clinical_mdr_api.services.odms.forms import OdmFormService
        from clinical_mdr_api.services.odms.item_groups import OdmItemGroupService
        from clinical_mdr_api.services.odms.items import OdmItemService
        from clinical_mdr_api.services.odms.methods import OdmMethodService
        from clinical_mdr_api.services.odms.study_events import OdmStudyEventService
        from clinical_mdr_api.services.studies.study import StudyService
        from clinical_mdr_api.services.studies.study_visit import StudyVisitService

        self.study_service = StudyService()
        self.visit_service_cls = StudyVisitService
        self.study_event_service = OdmStudyEventService()
        self.form_service = OdmFormService()
        self.item_group_service = OdmItemGroupService()
        self.item_service = OdmItemService()
        self.method_service = OdmMethodService()
        self.condition_service = OdmConditionService()
        self.census: list[dict[str, Any]] = []
        self.source_bundle_meta: dict[str, Any] = {}

    def _retain_native(
        self,
        kind: str,
        record: dict[str, Any],
        *,
        uid: str | None = None,
        scope: dict[str, Any] | None = None,
    ) -> None:
        """Keep the complete native reading, including relationship properties.

        EDC's editable fields are a projection, so their scalar shape cannot
        stand in for native aliases, translations, multiple units or method
        and condition links. Identical repeated reads are shared; different
        readings of the same UID are both retained.
        """
        if not hasattr(self, "_native_records"):
            self._native_records: list[dict[str, Any]] = []
            self._native_record_keys: set[str] = set()
        key = kind + ":" + json.dumps(record, sort_keys=True, default=str)
        if key not in self._native_record_keys:
            self._native_record_keys.add(key)
            self._native_records.append(
                {
                    "kind": kind,
                    "uid": uid if uid is not None else record.get("uid"),
                    "record": deepcopy(record),
                    **({"scope": deepcopy(scope)} if scope is not None else {}),
                }
            )

    def _uses_semantic_source(self) -> bool:
        """Identify the existing CSL producer envelopes, including legacy previews.

        This selects projection precedence, not release authorization. Preview
        exports remain non-authoritative for deployment and require review.
        """
        provenance = self.source_bundle_meta.get("_provenance") or {}
        custody = self.source_bundle_meta.get("semanticSourceCustody") or {}
        return (
            isinstance(provenance, dict)
            and str(provenance.get("builtBy") or "").startswith("csl.bundle-builder/")
        ) or (
            isinstance(custody, dict)
            and (
                custody.get("contractVersion") or custody.get("custodyContractVersion")
            )
            == "SemanticSourceCustodyV1@1.0.0"
        )

    def _semantic_difference(self, ref: str, source: Any, native: Any) -> None:
        if source != native:
            self.census.append(
                {
                    "kind": "semantic_native_difference",
                    "ref": ref,
                    "detail": "Semantic source value retained; native variation requires reconciliation in the semantic layer",
                    "sourceValue": deepcopy(source),
                    "nativeValue": deepcopy(native),
                }
            )

    def _retain_linked_definitions(self) -> None:
        for kind, property_name in (
            ("method", "method_oid"),
            ("condition", "collection_exception_condition_oid"),
        ):
            refs = set()

            def collect(value):
                if isinstance(value, dict):
                    if value.get(property_name):
                        refs.add(str(value[property_name]))
                    for child in value.values():
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            # Read native relationship properties, not opaque source carriers.
            for entry in getattr(self, "_native_records", []):
                collect(entry["record"])
            if not refs:
                continue
            service = getattr(self, kind + "_service")
            result = service.get_all_odms(
                page_size=0, filter_by={"oid": {"v": sorted(refs), "op": "eq"}}
            )
            items = result.items if hasattr(result, "items") else result
            found = set()
            for model in items:
                record = (
                    model.model_dump() if hasattr(model, "model_dump") else dict(model)
                )
                if record.get("oid") in refs:
                    found.add(record["oid"])
                    self._retain_native(kind, record)
            for missing in sorted(refs - found):
                self.census.append(
                    {
                        "kind": "unresolved_native_reference",
                        "ref": f"{kind}/{missing}",
                        "detail": "Native relationship retained, but its referenced definition was not found",
                    }
                )

    def _retain_native_associated_records(self, study_uid: str) -> None:
        from clinical_mdr_api.services.integrations.edc_native_library_definitions import (
            collect_native_library_definitions,
        )
        from clinical_mdr_api.services.integrations.edc_native_study_records import (
            NativeStudyRecordError,
            collect_study_native_records,
        )

        try:
            records, census = collect_study_native_records(study_uid)
        except NativeStudyRecordError as error:
            raise EdcExportError(str(error)) from error
        for entry in records:
            self._retain_native(
                entry["kind"],
                entry["record"],
                uid=entry["uid"],
                scope=entry.get("scope"),
            )
        self.census.extend(census)
        definitions, associations, unresolved = collect_native_library_definitions(
            self._native_records
        )
        for entry in definitions:
            self._retain_native(entry["kind"], entry["record"], uid=entry["uid"])
        self._native_associations = associations
        self.census.extend(unresolved)

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def build_bundle(self, study_uid: str) -> dict[str, Any]:
        self.census = []
        self.source_bundle_meta = {}
        self._native_records = []
        self._native_record_keys = set()
        self._native_associations = []

        authority_mode = config.settings.mapping_authority_mode
        if authority_mode == "enforced":
            raise EdcExportError(
                "MAPPING_AUTHORITY_ENFORCED: the carrier-compatible V1 exporter is disabled because it can restore Intelligence Layer source values over native OSB state. Use the released OSB authority package once its V2 EDC importer is enabled."
            )
        study = self.study_service.get_by_uid(
            study_uid,
            include_sections=[
                StudyComponentEnum.STUDY_DESIGN,
                StudyComponentEnum.STUDY_POPULATION,
                StudyComponentEnum.STUDY_INTERVENTION,
            ],
        )

        study_dict = study.model_dump() if hasattr(study, "model_dump") else dict(study)
        self._retain_native("study", study_dict)

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
        source_study_id = _source_study_id(study_dict)
        event_form_uids, source_study_ids = self._study_event_form_uids(
            visit_ref_by_name, visits, source_study_id
        )
        forms, form_ref_by_uid, form_ref_by_oid = self._forms(
            event_form_uids, source_study_ids, study_uid
        )
        assignments = self._assignments(
            study_uid,
            visit_ref_by_name,
            form_ref_by_uid,
            form_ref_by_oid,
            visits,
            source_study_ids,
        )
        arms = self._group_classes(study_uid)
        self._retain_source_owned_odm_definitions(source_study_id)
        self._retain_native_associated_records(study_uid)
        self._retain_linked_definitions()

        source_meta = dict(self.source_bundle_meta)
        semantic_source_custody = source_meta.pop("semanticSourceCustody", None)
        source_study = dict(source_meta.get("study") or {})
        source_forms = dict(source_meta.get("forms") or {})
        source_overlay_active = bool(source_meta)
        native_study = _native_study_projection(study_dict)
        merged_study = dict(source_study)
        native_keys = set(native_study)
        for key, value in native_study.items():
            if key == "studyParameters" and isinstance(value, dict):
                # An empty native parameter projection says nothing about the
                # source's design parameters; it must not erase that dictionary.
                value = {**(source_study.get(key) or {}), **value}
            if self._uses_semantic_source() and key in source_study:
                self._semantic_difference(f"study.{key}", source_study[key], value)
                continue
            if key in source_study and source_study[key] != value:
                self.census.append(
                    {
                        "kind": "study_value_conflict",
                        "ref": key,
                        "detail": (
                            "OpenStudyBuilder native value won over the historical "
                            f"source carrier (source={source_study[key]!r}, osb={value!r})"
                        ),
                    }
                )
            merged_study[key] = value
        for key in sorted(set(source_study) - native_keys):
            self.census.append(
                {
                    "kind": "carrier_preserved",
                    "ref": f"study.{key}",
                    "detail": (
                        "No native OSB StudyMetadata landing exists for this EDC property; "
                        "the historical source value is retained without native authority credit."
                    ),
                }
            )
        disclosure = authority_disclosure(authority_mode, source_overlay_active)
        if "_mappingAuthority" in source_meta:
            disclosure["sourceAuthority"] = deepcopy(source_meta["_mappingAuthority"])
        if self._uses_semantic_source():
            disclosure["sourceTruthSystem"] = "ClinicalSemanticLayer"
            disclosure["nativeDifferencesRequireSemanticReconciliation"] = True
        authority_warning = (
            "NON-AUTHORITATIVE SHADOW EXPORT: this StudyBundleV1 may restore legacy Intelligence Layer source-carrier values for properties the native OSB V1 projection cannot yet represent. It is for parity review only; OpenStudyBuilder release authority has not been established."
            if authority_mode == "shadow"
            else "LEGACY EXPORT: mapping authority is not established by this StudyBundleV1."
        )
        if self._uses_semantic_source():
            authority_warning = (
                "PREVIEW EXPORT: ClinicalSemanticLayer supplies the source values. "
                "Native OpenStudyBuilder differences are retained for semantic reconciliation; "
                "this export does not authorize deployment."
            )
        self.census.append(
            {
                "kind": "mapping_authority",
                "ref": study_uid,
                "detail": authority_warning,
            }
        )
        bundle: dict[str, Any] = {
            **source_meta,
            "formatVersion": source_meta.get(
                "formatVersion", EDC_BUNDLE_FORMAT_VERSION
            ),
            "exportedAt": source_meta.get("exportedAt", "1970-01-01T00:00:00.000Z"),
            "exportedBy": source_meta.get("exportedBy", "openstudybuilder-edc-export"),
            "sourceStudyName": source_meta.get("sourceStudyName", str(name)),
            "study": merged_study or native_study,
            "visits": self._restore_source_visits(visits),
            "visitFormAssignments": self._restore_source_assignments(assignments),
            "forms": {
                **source_forms,
                "formatVersion": source_forms.get(
                    "formatVersion", EDC_BUNDLE_FORMAT_VERSION
                ),
                "exportedAt": source_forms.get(
                    "exportedAt", "1970-01-01T00:00:00.000Z"
                ),
                "exportedBy": source_forms.get(
                    "exportedBy", "openstudybuilder-edc-export"
                ),
                "exportWarnings": [
                    *(source_forms.get("exportWarnings") or []),
                    authority_warning,
                ],
                "forms": forms,
            },
            **self._restore_source_group_classes(arms),
            "_mappingAuthority": disclosure,
            "_osbNative": {
                "formatVersion": "1.0",
                "studyUid": study_uid,
                "scope": "exported-study-records",
                "records": self._native_records,
                "associations": self._native_associations,
                "unresolved": [
                    deepcopy(row)
                    for row in self.census
                    if row["kind"] == "unresolved_native_reference"
                ],
            },
            "_exportCensus": {
                "rows": self.census,
                "counts": self._export_census_counts(),
                **(
                    {"sourceCensus": deepcopy(source_meta["_exportCensus"])}
                    if "_exportCensus" in source_meta
                    else {}
                ),
                **(
                    {"semanticSourceCustody": semantic_source_custody}
                    if semantic_source_custody is not None
                    else {}
                ),
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

    def _export_census_counts(self) -> dict[str, int]:
        """Census counts with the lossy total kept apart from informational
        rows (the unconditional mapping_authority row, carrier notes, ...):
        consumers gate on `lossy`, which counts only rows naming actual loss.
        """
        downgrades = sum(1 for r in self.census if r["kind"] == "field_type_downgrade")
        ambiguous_joins = sum(1 for r in self.census if r["kind"] == "ambiguous_join")
        return {
            "total": len(self.census),
            "downgrades": downgrades,
            "ambiguous_joins": ambiguous_joins,
            "lossy": sum(
                1
                for row in self.census
                if row["kind"]
                in {
                    "field_type_downgrade",
                    "ambiguous_join",
                    "dangling_form_ref",
                    "bundle_meta_unparseable",
                    "source_form_unparseable",
                    "source_field_unparseable",
                    "ext_unparseable",
                    "ext_value_unparseable",
                    "unresolved_native_reference",
                }
            ),
        }

    @staticmethod
    def _ref(value: Any) -> str:
        return _sanitize_ref(str(value or ""))

    @staticmethod
    def _unique_index(rows, key, label):
        index = {}
        for row in rows:
            identity = key(row)
            if identity in index and index[identity] != row:
                raise EdcExportError(
                    f"AMBIGUOUS_SOURCE_REFERENCE:{label}:{identity}: "
                    "distinct records cannot share one normalized lookup key"
                )
            index[identity] = row
        return index

    def _restore_source_visits(self, current: list[dict[str, Any]]):
        source = self.source_bundle_meta.get("visits") or []
        if self._uses_semantic_source() and "visits" in self.source_bundle_meta:
            native_by_ref = {self._ref(row.get("refKey")): row for row in current}
            for row in source:
                native = native_by_ref.get(self._ref(row.get("refKey")))
                if native:
                    for key in ("name", "ordinal", "type", "repeating"):
                        if key in row and key in native:
                            self._semantic_difference(
                                f"visits/{row.get('refKey')}/{key}",
                                row[key],
                                native[key],
                            )
                else:
                    self._semantic_difference(f"visits/{row.get('refKey')}", row, None)
            return deepcopy(source)
        if not source:
            return current
        current_by_ref = self._unique_index(
            current,
            lambda row: self._ref(row.get("refKey") or row.get("name")),
            "native visit",
        )
        self._unique_index(
            source,
            lambda row: self._ref(row.get("refKey") or row.get("name")),
            "source visit",
        )
        restored = []
        for original in source:
            current_visit = current_by_ref.pop(
                self._ref(original.get("refKey") or original.get("name")), {}
            )
            merged = dict(original)
            for key in (
                "name",
                "ordinal",
                "repeating",
            ):
                if key in original and key in current_visit:
                    merged[key] = current_visit[key]
            # _VISIT_TYPE_FROM_SOURCE. `type` is NOT taken from OSB unless OSB's
            # class actually encodes it. OSB derives type from visit_class, and
            # permits one UNSCHEDULED_VISIT per study (whose name it also
            # hard-derives), so every further protocol visit must be imported as
            # MANUALLY_DEFINED_VISIT to keep its name — and that class says
            # nothing about scheduled-ness. Letting it win turned the protocol's
            # stated `unscheduled` into `scheduled` on the way to the EDC.
            if current_visit.get("type") == "unscheduled":
                merged["type"] = "unscheduled"
            elif "type" not in merged and "type" in current_visit:
                merged["type"] = current_visit["type"]
            if original.get("refKey"):
                merged["refKey"] = original["refKey"]
            restored.append(merged)
        restored.extend(current_by_ref.values())
        return restored

    def _restore_source_assignments(self, current: list[dict[str, Any]]):
        source = self.source_bundle_meta.get("visitFormAssignments") or []
        if (
            self._uses_semantic_source()
            and "visitFormAssignments" in self.source_bundle_meta
        ):
            native_by_ref: dict[tuple, list[dict]] = {}
            for native in current:
                native_by_ref.setdefault(
                    (native.get("visitRef"), native.get("formRef")), []
                ).append(native)
            for row in source:
                candidates = native_by_ref.get(
                    (row.get("visitRef"), row.get("formRef")), []
                )
                if len(candidates) != 1:
                    self._semantic_difference(
                        f"assignments/{row.get('visitRef')}/{row.get('formRef')}/nativeOccurrenceCount",
                        1,
                        len(candidates),
                    )
                else:
                    native = candidates[0]
                    for key in ("required", "ordinal"):
                        if key in row and key in native:
                            self._semantic_difference(
                                f"assignments/{row.get('visitRef')}/{row.get('formRef')}/{key}",
                                row[key],
                                native[key],
                            )
            return deepcopy(source)
        if not source:
            return current
        current_by_ref = self._unique_index(
            current,
            lambda row: (self._ref(row.get("visitRef")), self._ref(row.get("formRef"))),
            "native assignment",
        )
        self._unique_index(
            source,
            lambda row: (self._ref(row.get("visitRef")), self._ref(row.get("formRef"))),
            "source assignment",
        )
        restored = []
        for original in source:
            key = (
                self._ref(original.get("visitRef")),
                self._ref(original.get("formRef")),
            )
            assignment = current_by_ref.pop(key, {})
            merged = {**assignment, **original}
            for key in ("required", "ordinal"):
                if key in original and key in assignment:
                    merged[key] = assignment[key]
            if original.get("visitRef"):
                merged["visitRef"] = original["visitRef"]
            if original.get("formRef"):
                merged["formRef"] = original["formRef"]
            restored.append(merged)
        restored.extend(current_by_ref.values())
        return restored

    def _restore_source_group_classes(self, arms: list[dict[str, Any]]):
        source = self.source_bundle_meta.get("studyGroupClasses") or []
        if (
            self._uses_semantic_source()
            and "studyGroupClasses" in self.source_bundle_meta
        ):
            return {"studyGroupClasses": deepcopy(source)}
        if not source:
            return {"studyGroupClasses": arms} if arms else {}
        current_arms = iter(arms)
        restored = []
        for group_class in source:
            if group_class.get("groupClassTypeName") != "Arm":
                restored.append(group_class)
                continue
            current = next(current_arms, None)
            if current and "groups" in group_class:
                restored.append({**current, **group_class, "groups": current["groups"]})
            else:
                restored.append(group_class)
        restored.extend(current_arms)
        return {"studyGroupClasses": restored} if restored else {}

    def _restore_source_fields(
        self, current: list[dict[str, Any]], source_form: dict[str, Any]
    ):
        if self._uses_semantic_source() and "fields" in source_form:
            native_by_ref = {row.get("refKey"): row for row in current}
            for row in source_form["fields"]:
                native = native_by_ref.get(row.get("refKey"))
                if native:
                    for key in (
                        "name",
                        "label",
                        "type",
                        "required",
                        "sdtmVariable",
                        "unit",
                        "options",
                        "section",
                        "group",
                    ):
                        if key in row and key in native:
                            self._semantic_difference(
                                f"fields/{source_form.get('refKey')}/{row.get('refKey')}/{key}",
                                row[key],
                                native[key],
                            )
                else:
                    self._semantic_difference(
                        f"fields/{source_form.get('refKey')}/{row.get('refKey')}",
                        row,
                        None,
                    )
            return deepcopy(source_form["fields"])
        source_by_ref = self._unique_index(
            source_form.get("fields", []),
            lambda row: self._ref(row.get("refKey") or row.get("name")),
            "source field",
        )
        restored = []
        for field in current:
            original = source_by_ref.get(
                self._ref(field.get("refKey") or field.get("name"))
            )
            if not original:
                restored.append(field)
                continue
            # Source wins for values OSB cannot represent faithfully (option
            # codes, validation objects, evidence annotations, null-vs-default).
            merged = {**field, **original}
            # Current OSB state wins for fields it does model and users can edit.
            for key in (
                "name",
                "label",
                "type",
                "required",
                "sdtmVariable",
                "section",
                "group",
            ):
                if key == "group" and field.get("group") == original.get("section"):
                    # The importer may name a generated ODM group with its
                    # section ID. That identifier is not a new display label.
                    continue
                if key in original and key in field:
                    merged[key] = field[key]
            if "length" in original and "length" in field:
                merged["length"] = field["length"]
            merged["refKey"] = original.get("refKey") or field["refKey"]
            restored.append(merged)
        return restored

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
            self._retain_native("visit", v)
            base = _sanitize_ref(
                v.get("visit_short_name") or v.get("visit_name") or f"V{i}"
            )
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

    def _study_event_form_uids(
        self, visit_ref_by_name, visits, expected_source_study_id=None
    ):
        """OdmForm UIDs reached from THIS study's study-events' FORM_REFs. A
        stamped event belongs to its explicit source study even if its native
        calendar projection is held. Unstamped native events require a visit
        name match. Retaining an event definition does not establish a native
        visit or assignment; those are projected separately."""
        events_result = self.study_event_service.get_all_odms(page_size=0)
        events = (
            events_result.items if hasattr(events_result, "items") else events_result
        )
        visit_refs = {v["refKey"] for v in visits}
        form_uids: set[str] = set()
        source_study_ids: set[str] = (
            {expected_source_study_id} if expected_source_study_id else set()
        )
        for event_model in events:
            event = (
                event_model.model_dump()
                if hasattr(event_model, "model_dump")
                else dict(event_model)
            )
            oid = event.get("oid") or ""
            match = X360I_EVENT_OID.match(oid)
            if (
                match
                and not expected_source_study_id
                and match.group("visit_ref") in visit_refs
            ):
                raise EdcExportError(
                    "AMBIGUOUS_SOURCE_STUDY: a matching visit key does not establish ownership "
                    "of a stamped source study; an explicit study identity is required"
                )
            if expected_source_study_id and not match:
                continue
            if (
                match
                and expected_source_study_id
                and match.group("study_id") != expected_source_study_id
            ):
                # A stamped event from another 360i study must never fall
                # through to the weaker name join.
                continue
            claims_visit = bool(
                match
                and expected_source_study_id
                and match.group("study_id") == expected_source_study_id
            )
            if not claims_visit:
                if match:
                    continue
                claims_visit = (
                    event.get("name") or ""
                ).strip().lower() in visit_ref_by_name
            if not claims_visit:
                continue
            self._retain_native("studyEvent", event)
            for form_ref_model in event.get("forms", []) or []:
                if form_ref_model.get("uid"):
                    form_uids.add(form_ref_model["uid"])
        return form_uids, source_study_ids

    def _retain_source_owned_odm_definitions(self, source_study_id):
        """Retain definitions whose parent projection failed after creation.

        Exact source-stamped OIDs establish ownership independently of a
        successful form/group link. Filtering is only an optimization; the
        exact prefix and any vendor ownership stamp are checked again here.
        """
        if not source_study_id:
            return
        for kind, service_name, marker in (
            ("itemGroup", "item_group_service", "IG"),
            ("item", "item_service", "IT"),
        ):
            prefix = f"{marker}.360I.{source_study_id}."
            result = getattr(self, service_name).get_all_odms(
                page_size=0, filter_by={"oid": {"v": [prefix], "op": "co"}}
            )
            models = result.items if hasattr(result, "items") else result
            reached = {
                row["uid"]
                for row in getattr(self, "_native_records", [])
                if row["kind"] == kind
            }
            for model in models:
                record = (
                    model.model_dump() if hasattr(model, "model_dump") else dict(model)
                )
                if not str(record.get("oid") or "").startswith(prefix):
                    continue
                stamped_study = _vendor_attr(record, "studyId")
                if stamped_study and stamped_study != source_study_id:
                    raise EdcExportError(
                        f"AMBIGUOUS_SOURCE_REFERENCE: conflicting owner of {record.get('oid')}"
                    )
                self._retain_native(kind, record)
                if record.get("uid") not in reached:
                    self.census.append(
                        {
                            "kind": "native_definition_retained_without_parent",
                            "ref": f"{kind}/{record.get('uid')}",
                            "detail": "Source-owned definition retained independently of its incomplete parent projection",
                        }
                    )

    def _forms(self, event_form_uids=None, source_study_ids=None, study_uid=None):
        """The study's ODM forms, projected with sections (item groups) and
        fields (items). refKey = the form's OID when it has one (the 360i
        importer sets OID = refKey), else a sanitized name.

        Scoping: a form is projected when it is reached from this study's
        study-events (event_form_uids) OR it carries an x360i vendor stamp (a
        360i-created form, including ones the study defines but never schedules).
        This excludes OSB's baked DDF seed forms and other unrelated library
        forms. A study with no scoped event links or source identity cannot
        claim ownership of the shared ODM library."""
        forms_result = self.form_service.get_all_odms(page_size=0)
        forms_items = (
            forms_result.items if hasattr(forms_result, "items") else forms_result
        )
        all_forms = [
            (
                form_model.model_dump()
                if hasattr(form_model, "model_dump")
                else dict(form_model)
            )
            for form_model in forms_items
        ]
        event_form_uids = event_form_uids or set()
        source_study_ids = source_study_ids or set()

        try:
            snapshot = read_source_snapshot(all_forms, study_uid, source_study_ids)
        except SourceSnapshotError as error:
            raise EdcExportError(
                f"SEMANTIC_SOURCE_SNAPSHOT_INVALID: {error}"
            ) from error
        if snapshot:
            self.source_bundle_meta, carriers = snapshot
            for carrier in carriers:
                self._retain_native("sourceSnapshotCarrier", carrier)
            self.census.append(
                {
                    "kind": "semantic_source_snapshot",
                    "ref": study_uid,
                    "detail": "Complete committed semantic source snapshot verified independently of native clinical form mapping",
                }
            )
        # Source snapshot storage is metadata, never a patient form. This also
        # excludes uncommitted chunks and older generations from clinical joins.
        all_forms = [form for form in all_forms if not is_source_snapshot(form)]

        bundle_meta_chunks: dict[int, str] = {}
        bundle_meta_chunk_total: int | None = None
        for form in ([] if snapshot else all_forms):
            if (
                not source_study_ids
                or _vendor_attr(form, "studyId") not in source_study_ids
            ):
                continue
            raw_meta = _vendor_attr(form, "bundleMeta")
            if not raw_meta:
                continue
            if raw_meta.startswith(CARRIER_CHUNK_PREFIX):
                try:
                    header, chunk = raw_meta[len(CARRIER_CHUNK_PREFIX) :].split(":", 1)
                    index_text, total_text = header.split("/", 1)
                    index, total = int(index_text), int(total_text)
                    if not 1 <= index <= total:
                        raise ValueError("chunk index is out of range")
                    if (
                        bundle_meta_chunk_total is not None
                        and bundle_meta_chunk_total != total
                    ):
                        raise ValueError("inconsistent chunk totals")
                    if (
                        index in bundle_meta_chunks
                        and bundle_meta_chunks[index] != chunk
                    ):
                        raise EdcExportError(
                            "AMBIGUOUS_BUNDLE_METADATA: conflicting legacy source chunks"
                        )
                    bundle_meta_chunk_total = total
                    bundle_meta_chunks[index] = chunk
                except (TypeError, ValueError):
                    self.census.append(
                        {
                            "kind": "bundle_meta_unparseable",
                            "ref": form.get("oid") or form.get("uid") or "?",
                            "detail": "x360i:bundleMeta chunk header is invalid",
                        }
                    )
                continue
            try:
                parsed_meta = _carrier_json(raw_meta)
                if isinstance(parsed_meta, dict):
                    self.source_bundle_meta = parsed_meta
                    break
            except (TypeError, ValueError, OSError):
                self.census.append(
                    {
                        "kind": "bundle_meta_unparseable",
                        "ref": form.get("oid") or form.get("uid") or "?",
                        "detail": "x360i:bundleMeta is not valid JSON",
                    }
                )

        if bundle_meta_chunks and not self.source_bundle_meta:
            expected = bundle_meta_chunk_total or 0
            if set(bundle_meta_chunks) != set(range(1, expected + 1)):
                self.census.append(
                    {
                        "kind": "bundle_meta_unparseable",
                        "ref": "bundleMeta",
                        "detail": "x360i:bundleMeta carrier has missing chunks",
                    }
                )
            else:
                try:
                    parsed_meta = _carrier_json(
                        "".join(
                            bundle_meta_chunks[index]
                            for index in range(1, expected + 1)
                        )
                    )
                    if isinstance(parsed_meta, dict):
                        self.source_bundle_meta = parsed_meta
                except (TypeError, ValueError, OSError):
                    self.census.append(
                        {
                            "kind": "bundle_meta_unparseable",
                            "ref": "bundleMeta",
                            "detail": "x360i:bundleMeta chunks are not valid JSON",
                        }
                    )

        def _in_scope(form):
            stamped_study = _vendor_attr(form, "studyId")
            if stamped_study and stamped_study not in source_study_ids:
                return False
            if form.get("uid") in event_form_uids:
                return True
            if _vendor_attr(form, "studyId") in source_study_ids:
                return True
            return False

        forms = []
        ref_by_uid: dict[str, str] = {}
        ref_by_oid: dict[str, str] = {}
        used: set[str] = set()
        exported_refs: set[str] = set()

        for form in all_forms:
            if not _in_scope(form):
                continue
            self._retain_native("form", form)
            source_form = {}
            raw_source = _vendor_attr(form, "source")
            if raw_source:
                try:
                    candidate = _carrier_json(raw_source)
                    if isinstance(candidate, dict):
                        source_form = candidate
                except (TypeError, ValueError, OSError):
                    self.census.append(
                        {
                            "kind": "source_form_unparseable",
                            "ref": form.get("oid") or form.get("uid") or "?",
                            "detail": "x360i:source is not valid JSON",
                        }
                    )
            base = _sanitize_ref(
                source_form.get("refKey") or form.get("oid") or form.get("name")
            )
            ref = base
            n = 2
            while ref in used:
                ref = f"{base}_{n}"
                n += 1
            used.add(ref)
            exported_ref = source_form.get("refKey") or ref
            if exported_ref in exported_refs:
                raise EdcExportError(
                    f"AMBIGUOUS_FORM_IDENTITY:{exported_ref}: multiple scoped native forms "
                    "share this portable identity; reconcile their native records before export"
                )
            exported_refs.add(exported_ref)
            ref_by_uid[form["uid"]] = exported_ref
            if form.get("oid"):
                ref_by_oid[form["oid"]] = exported_ref

            sections = []
            fields = []
            for gi, group_ref in enumerate(form.get("item_groups", []) or [], start=1):
                group_uid = group_ref.get("uid")
                group = self.item_group_service.get_by_uid(group_uid)
                group = (
                    group.model_dump() if hasattr(group, "model_dump") else dict(group)
                )
                self._retain_native("itemGroup", group)
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
            fields = self._restore_source_fields(fields, source_form)
            if source_form:
                restored_form = {**source_form, "fields": fields}
                if "name" in source_form and not self._uses_semantic_source():
                    restored_form["name"] = form.get("name")
                description = _english_text(form, "Description")
                if (
                    "description" in source_form
                    and description
                    and not self._uses_semantic_source()
                ):
                    restored_form["description"] = description
                forms.append(restored_form)
            else:
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
        source_forms = (self.source_bundle_meta.get("forms") or {}).get("forms")
        if self._uses_semantic_source() and isinstance(source_forms, list):
            # A native form can be blocked by an unmapped child. Its entire
            # semantic definition must remain visible in the preview bundle.
            # Native records document implementation coverage; they never
            # replace the semantic source of truth.
            canonical = self._unique_index(
                source_forms, lambda form: str(form.get("refKey") or ""), "source forms"
            )
            native = self._unique_index(
                forms, lambda form: str(form.get("refKey") or ""), "projected forms"
            )
            for ref in canonical.keys() - native.keys():
                self._semantic_difference(f"forms/{ref}/nativePresence", True, False)
            for ref in native.keys() - canonical.keys():
                self._semantic_difference(f"forms/{ref}/sourcePresence", False, True)
            forms = deepcopy(source_forms)
        return forms, ref_by_uid, ref_by_oid

    def _field(self, form_ref, section_name, item, item_ref, order):
        self._retain_native("item", item)
        stamped_type = _vendor_attr(item, "fieldType")
        field_ref = _sanitize_ref(
            _vendor_attr(item, "refKey") or item.get("oid") or item.get("name")
        )
        # The source UI historically stamped scalar SYSBP/DIABP questions with
        # its composite `blood_pressure` widget type. OSB now owns each as a
        # separate numeric ODM ItemDef (`float`). Restoring the stale widget stamp
        # would make AccuraTrial store the numeric value as text. Prefer the native
        # ODM datatype for these exact scalar identities and disclose the override.
        if (
            stamped_type == "blood_pressure"
            and str(item.get("datatype") or "").lower()
            in {"float", "double", "decimal"}
            and field_ref in {"SYSBP", "DIABP"}
        ):
            self.census.append(
                {
                    "kind": "carrier_type_overridden",
                    "ref": f"{form_ref}/{field_ref}",
                    "detail": (
                        "OpenStudyBuilder native ODM numeric datatype won over stale "
                        "x360i:fieldType 'blood_pressure'; exported as decimal"
                    ),
                }
            )
            stamped_type = None
        has_codelist = bool(item.get("codelist"))
        multi = bool((item.get("codelist") or {}).get("allows_multi_choice"))
        edc_type, downgrade = resolve_edc_field_type(
            item.get("datatype"), stamped_type, has_codelist, multi
        )
        if downgrade:
            self.census.append(
                {
                    "kind": "field_type_downgrade",
                    "ref": f"{form_ref}/{field_ref}",
                    "detail": downgrade,
                }
            )

        source_field: dict[str, Any] = {}
        source_json = _vendor_attr(item, "source")
        if source_json:
            try:
                candidate = _carrier_json(source_json)
                if isinstance(candidate, dict):
                    source_field = candidate
            except (TypeError, ValueError, OSError):
                self.census.append(
                    {
                        "kind": "source_field_unparseable",
                        "ref": f"{form_ref}/{field_ref}",
                        "detail": "x360i:source is not valid JSON",
                    }
                )

        field: dict[str, Any] = {
            **source_field,
            "refKey": source_field.get("refKey") or field_ref,
            "name": item.get("name"),
            "type": edc_type,
        }
        current_order = item_ref.get("order_number") or order
        if "ordinal" in source_field:
            field["ordinal"] = current_order
            field.pop("order", None)
        else:
            field["order"] = current_order
        prompt = item.get("prompt") or _english_text(item, "Question")
        if prompt:
            field["label"] = prompt
        mandatory = item_ref.get("mandatory")
        if mandatory is not None:
            field["required"] = str(mandatory).lower() in ("yes", "true", "1")
        # Text items with no stated length are defaulted to 200 solely because
        # OSB requires one. Do not manufacture that default into the EDC bundle.
        if item.get("length") is not None and (
            "length" in source_field or item.get("length") != 200
        ):
            field["length"] = item["length"]
        elif "length" not in source_field:
            field.pop("length", None)
        if item.get("comment"):
            field["description"] = item["comment"]
        if item.get("sds_var_name"):
            field["sdtmVariable"] = item["sds_var_name"]
        if section_name:
            field["section"] = section_name
            field["group"] = section_name
        units = item.get("unit_definitions") or []
        if units:
            field["unit"] = units[0].get("name")
        terms = item.get("terms") or []
        if terms and "options" not in source_field:
            field["options"] = [
                {
                    "label": t.get("display_text")
                    or t.get("name")
                    or str(t.get("uid")),
                    "value": (
                        str(t["submission_value"])
                        if t.get("submission_value") is not None
                        else t.get("display_text")
                        or t.get("name")
                        or str(t.get("term_uid") or t.get("uid"))
                    ),
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
            for key, value in ext.items():
                if key in field:
                    continue
                if isinstance(value, str) and value.startswith("json:"):
                    try:
                        value = json.loads(value[len("json:") :])
                    except (TypeError, ValueError):
                        self.census.append(
                            {
                                "kind": "ext_value_unparseable",
                                "ref": f"{form_ref}/{field_ref}/{key}",
                                "detail": "tagged x360i:ext value is not valid JSON",
                            }
                        )
                elif isinstance(value, str) and value[:1] in ("[", "{"):
                    try:
                        value = json.loads(value)
                    except (TypeError, ValueError):
                        self.census.append(
                            {
                                "kind": "ext_value_unparseable",
                                "ref": f"{form_ref}/{field_ref}/{key}",
                                "detail": "structured x360i:ext value is not valid JSON",
                            }
                        )
                field[key] = value
        return field

    def _assignments(
        self,
        study_uid,
        visit_ref_by_name,
        form_ref_by_uid,
        form_ref_by_oid,
        visits,
        source_study_ids=None,
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
        source_study_ids = source_study_ids or set()
        assignments = []
        for event_model in events:
            event = (
                event_model.model_dump()
                if hasattr(event_model, "model_dump")
                else dict(event_model)
            )
            oid = event.get("oid") or ""
            match = X360I_EVENT_OID.match(oid)
            if source_study_ids and not match:
                continue
            visit_ref = None
            if match:
                if match.group("study_id") not in source_study_ids:
                    continue
                candidate = match.group("visit_ref")
                if candidate in visit_refs:
                    visit_ref = candidate
            if visit_ref is None:
                if match:
                    continue
                by_name = visit_ref_by_name.get(
                    (event.get("name") or "").strip().lower()
                )
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
            self._retain_native("studyArm", arm)
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
        x-api-key. V1 transfer is an explicitly opted-in, non-production legacy
        recovery action only. Shadow mode may generate comparison bytes locally,
        but must not transmit them across the EDC boundary.
        """
        authority_mode = config.settings.mapping_authority_mode
        if authority_mode != "legacy":
            raise EdcExportError(
                f"MAPPING_AUTHORITY_{authority_mode.upper()}: StudyBundleV1 cannot be sent "
                "to EDC; shadow is local comparison-only and enforced requires a verified "
                "Package V2 release."
            )
        if not dry_run:
            raise EdcExportError(
                "LEGACY_EDC_ACTIVATION_PROHIBITED: StudyBundleV1 may be sent only as a comparison dry-run. Native execution, reconciliation, Package V2, and EDC V2 deployment receipts are not implemented."
            )
        deployment_environment = config.settings.deployment_environment.strip().lower()
        if deployment_environment in {"prod", "production"}:
            raise EdcExportError(
                "LEGACY_EDC_SEND_PRODUCTION_PROHIBITED: StudyBundleV1 cannot cross the "
                "EDC boundary in production, including dry-run."
            )
        if not config.settings.allow_unsafe_legacy_edc_send:
            raise EdcExportError(
                "LEGACY_EDC_SEND_EXPLICIT_OPT_IN_REQUIRED: set "
                "ALLOW_UNSAFE_LEGACY_EDC_SEND=true only in a disposable migration environment."
            )
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
        # The export census STAYS on the bundle: the EDC's import treats
        # underscore-prefixed blocks as stored, so stripping it here silently
        # discarded the audit trail on the receiving side.
        export_census = bundle.get("_exportCensus")
        mapping_authority = bundle.get("_mappingAuthority")

        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/api/forms/import-study-bundle",
                json={"bundle": bundle, "dryRun": dry_run},
                headers={"x-api-key": api_key},
                timeout=120.0,
            )
        except httpx.HTTPError as exc:
            raise EdcExportError(f"EDC transfer failed: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if not response.is_success:
            raise EdcExportError(
                f"EDC rejected the transfer (HTTP {response.status_code}): {body}"
            )
        if not body.get("success", False):
            raise EdcExportError(f"EDC returned HTTP 2xx without success=true: {body}")
        # success=true alone is not acceptance: the EDC may have narrowed the
        # bundle (partial import, unknown census rows, skipped assignments or
        # study tasks). Name exactly what narrowed instead of reporting success.
        narrowed: list[str] = []
        if body.get("partial"):
            narrowed.append(f"partial={body.get('partial')!r}")
        response_census = body.get("census")
        if isinstance(response_census, dict):
            unknown = (response_census.get("counts") or {}).get("unknown")
            if unknown:
                narrowed.append(f"census.counts.unknown={unknown!r}")
        for key in ("assignmentsSkipped", "studyTasksSkipped"):
            value = body.get(key)
            if not value and isinstance(response_census, dict):
                value = response_census.get(key)
            if value:
                narrowed.append(f"{key}={value!r}")
        if narrowed:
            raise EdcExportError(
                "EDC_IMPORT_NARROWED: the EDC accepted the bundle but narrowed "
                f"it ({', '.join(narrowed)}): {body}"
            )
        return {
            "dryRun": dry_run,
            "statusCode": response.status_code,
            "edcResponse": body,
            "exportCensus": export_census,
            "mappingAuthority": mapping_authority,
        }
