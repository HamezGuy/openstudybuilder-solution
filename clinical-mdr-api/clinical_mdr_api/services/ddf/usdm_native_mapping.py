"""Map native study domains that are separate from OSB's main metadata DTO.

Selection identities authorize relationships. Library labels, the latest library
version and raw imported carriers never resolve native selections.
"""

from decimal import Decimal, InvalidOperation
import json
from typing import Any

from usdm_model import (
    Activity,
    AliasCode,
    BiomedicalConceptSurrogate,
    Quantity,
    StudyArm,
    StudyCohort,
    StudyDefinitionDocument,
    StudyDefinitionDocumentVersion,
    StudyTitle,
    ScheduleTimeline,
    ScheduledActivityInstance,
    Timing,
    StudyCell,
)
from usdm_model.comment_annotation import CommentAnnotation
from usdm_model.extension import ExtensionAttribute
from usdm_model.schedule_timeline_exit import ScheduleTimelineExit

from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
    native_json,
)


SCHEDULE_FOOTNOTE_SCOPE_URL = "https://openstudybuilder.org/usdm/extensions/schedule-footnote-scope"
SCHEDULE_FOOTNOTE_SCOPE_REQUIRED = "USDM_SCHEDULE_FOOTNOTE_SCOPE_REQUIRED"


class NativeStudyMapping:
    def __init__(self, mapper):
        self.mapper = mapper
        self.context = mapper._context
        self.rows = mapper._native_rows

    def identifier(self, kind: str, source: str) -> str:
        return self.mapper._id_manager.get_id(kind, source)

    def extension(self, kind: str, uid: str, row: Any):
        return self.mapper._native_extension(kind, uid, row)

    def issue(self, code, source, target, message, resolution):
        self.context.unresolved(code, source, target, message, resolution)

    def apply(self, study, document, version) -> None:
        metadata = study.current_metadata
        identification = getattr(metadata, "identification_metadata", None)
        description = getattr(metadata, "study_description", None)
        for field, source, code in (
            ("study_short_title", description, "C207615"),
            ("study_acronym", identification, "C207646"),
        ):
            value = getattr(source, field, None)
            if value is not None:
                version.titles.append(StudyTitle(
                    id=self.identifier("StudyTitle", field),
                    text=value,
                    type=self.mapper.get_ct_package_term_as_usdm_code(code),
                ))
        self._history(study, document, version)
        from clinical_mdr_api.services.ddf.usdm_native_interventions import NativeInterventionMapping

        NativeInterventionMapping(self.mapper).apply(version)
        if not version.studyDesigns:
            return
        design = version.studyDesigns[0]
        for kind in ("studyDataSupplier", "studyDesignClass", "studySourceVariable"):
            for row in self.rows.get(kind, []):
                uid = getattr(row, "study_data_supplier_uid", None) or row.study_uid
                design.extensionAttributes.append(self.extension(kind, uid, row))
        self._cohorts_and_branches(study, design)
        self._activities(study, design, version)
        self._footnotes(design, version)
        from clinical_mdr_api.services.ddf.usdm_cell_transitions import project_cell_transitions

        project_cell_transitions(self.mapper, study, version, design)
        from clinical_mdr_api.services.ddf.usdm_scoped_transitions import project_scoped_transitions

        project_scoped_transitions(self.mapper, study, version, design)
        # Full scoped records supplement the standard properties. This is
        # navigable source metadata and never a competing executable definition.
        for entry in self.context.native_records:
            if entry["kind"] in {
                "studyStandardVersion", "studyOperationalActivitySchedule",
                "studyDiseaseMilestone", "studySnapshotHistory",
            }:
                version.extensionAttributes.append(
                    self.extension(entry["kind"] + "-version", entry["uid"], entry["record"])
                )

    def _cohorts_and_branches(self, study, design) -> None:
        arms = {
            entry["record"]["arm_uid"]: entry["record"]
            for entry in self.context.native_records if entry["kind"] == "studyArm"
        }
        canonical_arms = {
            uid: next((arm for arm in design.arms
                       if arm.id == self.identifier("StudyArm", uid)), None)
            for uid in arms
        }
        branch_arms = {}
        for row in self.rows.get("studyBranchArm", []):
            uid = row.branch_arm_uid
            parent_uid = getattr(getattr(row, "arm_root", None), "arm_uid", None)
            parent = canonical_arms.get(parent_uid)
            if parent is None:
                self.issue(
                    "USDM_BRANCH_PARENT_UNRESOLVED", f"study-branch-arms/{uid}/arm_root",
                    "StudyArm", "The branch's exact selected parent arm is absent.",
                    "Resolve the native parent arm selection.",
                )
            # Branches define native treatment paths. USDM uses StudyArm for
            # each path; preserve its explicit parent link as scoped metadata.
            values = dict(
                id=self.identifier("StudyArm", "branch:" + uid),
                name=row.name, label=row.short_name, description=row.description,
                extensionAttributes=[
                    self.extension("studyBranchArm", uid, row),
                    self.extension("branch-parent", uid, {"studyArmId": parent.id if parent else None}),
                ],
            )
            # The native parent supplies the arm type, but not a missing data
            # origin. Keep required unsupported values absent in a draft.
            parent_source = arms.get(parent_uid, {})
            arm_type = parent_source.get("arm_type")
            if arm_type:
                values["type"] = self.mapper.get_ct_package_term_as_usdm_code(arm_type.get("term_uid"))
            branch = self.context.build(StudyArm, f"study-branch-arms/{uid}", **values)
            design.arms.append(branch)
            branch_arms[uid] = branch
        root_healthy = getattr(getattr(study.current_metadata, "study_population", None),
                               "healthy_subject_indicator", None)
        cohorts = self.rows.get("studyCohort", [])
        root_count = getattr(design.population, "plannedEnrollmentNumber", None)
        cohort_counts = [getattr(row, "number_of_subjects", None) for row in cohorts]
        if root_count is not None and any(count is not None for count in cohort_counts):
            self.issue(
                "USDM_ENROLLMENT_SCOPE_REVIEW_REQUIRED", "study-cohorts/number_of_subjects",
                "StudyDesignPopulation/plannedEnrollmentNumber",
                "Native population and cohort counts are both present. DDF00133 requires one canonical count scope.",
                "Review the count scope. The population count is projected and every cohort count remains in its typed source metadata.",
            )
        if root_count is None and any(count is not None for count in cohort_counts) and any(
            count is None for count in cohort_counts
        ):
            self.issue(
                "USDM_COHORT_ENROLLMENT_INCOMPLETE", "study-cohorts/number_of_subjects",
                "StudyCohort/plannedEnrollmentNumber",
                "Only some cohorts have a native count; DDF00133 requires all cohorts when this scope is used.",
                "Complete the native cohort counts or provide a governed population count.",
            )
        for row in cohorts:
            uid = row.cohort_uid
            count = getattr(row, "number_of_subjects", None)
            enrollment = None
            if count is not None and root_count is None:
                enrollment = Quantity(
                    id=self.identifier("Quantity", "cohort:" + uid),
                    value=count,
                )
            # A population with no healthy subjects cannot contain a healthy
            # cohort. A mixed population does not prove each cohort is mixed.
            healthy = False if root_healthy is False else None
            cohort = self.context.build(
                StudyCohort, f"study-cohorts/{uid}/includesHealthySubjects",
                id=self.identifier("StudyCohort", uid), name=row.name,
                label=row.short_name, description=row.description,
                includesHealthySubjects=healthy, plannedEnrollmentNumber=enrollment,
                extensionAttributes=[self.extension("studyCohort", uid, row)],
            )
            design.population.cohorts.append(cohort)
            for sources, targets, field in (
                (getattr(row, "arm_roots", None) or [], canonical_arms, "arm_uid"),
                (getattr(row, "branch_arm_roots", None) or [], branch_arms, "branch_arm_uid"),
            ):
                for source in sources:
                    target = targets.get(getattr(source, field, None))
                    if target is None:
                        self.issue(
                            "USDM_COHORT_ARM_UNRESOLVED", f"study-cohorts/{uid}/{field}",
                            "StudyArm/populationIds", "A cohort names an absent selected arm.",
                            "Resolve the native arm/cohort association.",
                        )
                    elif cohort.id not in target.populationIds:
                        target.populationIds.append(cohort.id)

    def _activities(self, study, design, version) -> None:
        suppliers = {
            row.study_data_supplier_uid: row
            for row in self.rows.get("studyDataSupplier", [])
        }
        activity_by_uid = {
            entry["record"]["study_activity_uid"]: next(
                (activity for activity in design.activities
                 if activity.id == self.identifier("Activity", entry["record"]["study_activity_uid"])), None
            )
            for entry in self.context.native_records if entry["kind"] == "studyActivity"
        }
        self._activity_targets = {
            ("StudyActivity", uid): activity for uid, activity in activity_by_uid.items()
            if activity is not None
        }
        for kind, identity, label in (
            ("studySoAGroup", "study_soa_group_uid", "soa_group_term_name"),
            ("studyActivityGroup", "study_activity_group_uid", "activity_group_name",
             ),
            ("studyActivitySubGroup", "study_activity_subgroup_uid", "activity_subgroup_name",
             ),
        ):
            for row in self.rows.get(kind, []):
                uid = getattr(row, identity)
                activity = self.context.build(
                    Activity, f"{kind}/{uid}", id=self.identifier("Activity", kind + ":" + uid),
                    name=getattr(row, label), label=getattr(row, label),
                    extensionAttributes=[self.extension(kind, uid, row)],
                )
                design.activities.append(activity)
                owner_kind = {"studyActivityGroup": "StudyActivityGroup",
                              "studyActivitySubGroup": "StudyActivitySubGroup",
                              "studySoAGroup": "StudySoAGroup"}[kind]
                self._activity_targets[(owner_kind, uid)] = activity
        for kind, identity, child_field, child_kind in (
            ("studySoAGroup", "study_soa_group_uid", "study_activity_group_uids", "StudyActivityGroup"),
            ("studyActivityGroup", "study_activity_group_uid", "study_activity_subgroup_uids", "StudyActivitySubGroup"),
            ("studyActivitySubGroup", "study_activity_subgroup_uid", "study_activity_uids", "StudyActivity"),
        ):
            owner_kind = {"studyActivityGroup": "StudyActivityGroup",
                          "studyActivitySubGroup": "StudyActivitySubGroup",
                          "studySoAGroup": "StudySoAGroup"}[kind]
            for row in self.rows.get(kind, []):
                owner = self._activity_targets[(owner_kind, getattr(row, identity))]
                for uid in getattr(row, child_field, None) or []:
                    child = self._activity_targets.get((child_kind, uid))
                    if child is None:
                        self.issue(
                            "USDM_ACTIVITY_CHILD_UNRESOLVED", f"{kind}/{getattr(row, identity)}/{child_field}",
                            "Activity/childIds", f"Selected child {uid} is absent.",
                            "Resolve the native group membership.",
                        )
                    else:
                        owner.childIds.append(child.id)
        for row in self.rows.get("studyActivityInstance", []):
            uid = row.study_activity_instance_uid
            parent = activity_by_uid.get(row.study_activity_uid)
            selected = getattr(row, "activity_instance", None)
            if parent is None or selected is None:
                self.issue(
                    "USDM_ACTIVITY_INSTANCE_SELECTION_UNRESOLVED", f"study-activity-instances/{uid}",
                    "Activity/childIds", "The selected native activity or instance is missing.",
                    "Select the native instance and resolve its exact parent activity.",
                )
                continue
            definition = self._instance_definition(row, selected)
            from clinical_mdr_api.services.ddf.usdm_acquisition_mapping import project_acquisition

            concept_id = project_acquisition(self.mapper, row, definition, version, design)
            # An instance with no item definitions still needs a surrogate.
            # Available items instead remain editable property drafts.
            surrogate_id = None
            if concept_id is None:
                surrogate = self.context.build(
                    BiomedicalConceptSurrogate, f"study-activity-instances/{uid}",
                    id=self.identifier("BiomedicalConceptSurrogate", uid),
                    name=definition.get("name"), label=definition.get("name"),
                    reference=(f"urn:openstudybuilder:activity-instance:{selected.uid}:version:{selected.version}"
                               if getattr(selected, "uid", None) and getattr(selected, "version", None) else None),
                    extensionAttributes=[
                        self.extension("selected-activity-instance", uid, row),
                        self.extension("activity-instance-definition", uid, definition),
                    ],
                )
                version.bcSurrogates.append(surrogate)
                surrogate_id = surrogate.id
            activity = self.context.build(
                Activity, f"study-activity-instances/{uid}",
                id=self.identifier("Activity", "instance:" + uid),
                name=definition.get("name"), label=definition.get("name"),
                bcSurrogateIds=[surrogate_id] if surrogate_id is not None else [],
                biomedicalConceptIds=[concept_id] if concept_id is not None else [],
                extensionAttributes=[self.extension("instance-activity", uid, row)],
            )
            supplier_uid = getattr(row, "study_data_supplier_uid", None)
            if supplier_uid is not None:
                supplier = suppliers.get(supplier_uid)
                if supplier is None:
                    self.issue(
                        "USDM_ACTIVITY_DATA_SUPPLIER_UNRESOLVED",
                        f"study-activity-instances/{uid}/study_data_supplier_uid",
                        "Activity/extensionAttributes",
                        "The selected activity instance names an absent study data supplier.",
                        "Resolve the exact supplier selection in the same native study version.",
                    )
                else:
                    activity.extensionAttributes.append(
                        self.extension("activity-data-supplier", uid, supplier)
                    )
            design.activities.append(activity)
            parent.childIds.append(activity.id)
            self._activity_targets[("StudyActivityInstance", uid)] = activity
        for row in self.rows.get("studyActivityInstruction", []):
            parent = activity_by_uid.get(row.study_activity_uid)
            if parent is None or row.activity_instruction_name is None:
                self.issue(
                    "USDM_ACTIVITY_INSTRUCTION_UNRESOLVED",
                    f"study-activity-instructions/{row.study_activity_instruction_uid}",
                    "Activity/notes", "The exact activity or instantiated instruction text is missing.",
                    "Resolve the native instruction selection.",
                )
                continue
            parent.notes.append(CommentAnnotation(
                id=self.identifier("CommentAnnotation", "instruction:" + row.study_activity_instruction_uid),
                text=row.activity_instruction_name,
                extensionAttributes=[self.extension("activityInstruction", row.study_activity_instruction_uid, row)],
            ))
        self._milestones(design)

    def _milestones(self, design) -> None:
        for row in self.rows.get("studyDiseaseMilestone", []):
            # A disease milestone defines a clinical event, not a dated visit.
            # Do not manufacture an Encounter or occurrence time from its audit date.
            source = getattr(row, "terminology_source", None)
            name = row.disease_milestone_type_name
            definition = row.disease_milestone_type_definition
            if source is None and self.mapper._study_value_version is not None:
                name, definition = None, None
                self.issue(
                    "USDM_MILESTONE_TERM_HISTORY_REQUIRED",
                    f"study-disease-milestones/{row.uid}/terminology_source",
                    "Activity/name",
                    "This historical selection has no dated native name/definition witness.",
                    "Read its exact native study snapshot and approved term history; "
                    "do not substitute current CT labels or invent a selected CT version.",
                )
            elif source is not None:
                if (
                    source.study_uid != self.mapper._study_uid
                    or source.study_value_version != self.mapper._study_value_version
                    or source.term_uid != row.disease_milestone_type
                    or source.as_of != self.mapper._study_as_of
                ):
                    raise USDMMappingAuthorityRequired("USDM_MILESTONE_TERM_SOURCE_SCOPE_MISMATCH")
                if (
                    name != (source.name.value.get("name") if source.name is not None else None)
                    or definition != (
                        source.attributes.value.get("definition")
                        if source.attributes is not None else None
                    )
                ):
                    raise USDMMappingAuthorityRequired("USDM_MILESTONE_TERM_SOURCE_TEXT_MISMATCH")
                if source.state != "resolved" or source.issues:
                    self.issue(
                        "USDM_MILESTONE_TERM_HISTORY_REQUIRED",
                        f"study-disease-milestones/{row.uid}/terminology_source",
                        "Activity/name",
                        "The selected snapshot's native term history is unresolved: "
                        + "; ".join(source.issues),
                        "Resolve the missing or ambiguous native history. Preserve known "
                        "values and supply governed missing facts before release.",
                    )
            activity = self.context.build(
                Activity, f"study-disease-milestones/{row.uid}",
                id=self.identifier("Activity", "milestone:" + row.uid),
                name=name,
                description=definition,
                extensionAttributes=[self.extension("diseaseMilestone", row.uid, row)],
            )
            design.activities.append(activity)
            self._activity_targets[("StudyDiseaseMilestone", row.uid)] = activity

    def _instance_definition(self, row, selected):
        uid, version = getattr(selected, "uid", None), getattr(selected, "version", None)
        reader = self.mapper._get_activity_instance_definition
        if not uid or not version or reader is None:
            self.issue(
                "USDM_SELECTED_INSTANCE_DEFINITION_REQUIRED",
                f"study-activity-instances/{row.study_activity_instance_uid}/activity_instance",
                "BiomedicalConceptSurrogate",
                "The full exact selected library definition is unavailable.",
                "Select a versioned native activity instance; never substitute latest_activity_instance.",
            )
            return native_json(selected)
        from clinical_mdr_api.services.ddf.usdm_mapping_context import accepts_keyword

        kwargs = {"version": version}
        for key, value in {
            "study_uid": self.mapper._study_uid,
            "study_value_version": self.mapper._study_value_version,
            "study_activity_instance_uid": row.study_activity_instance_uid,
            "as_of": self.mapper._study_as_of,
        }.items():
            if accepts_keyword(reader, key):
                kwargs[key] = value
        definition = native_json(reader(uid, **kwargs))
        if definition.get("uid") != uid or definition.get("version") != version:
            raise USDMMappingAuthorityRequired("USDM_ACTIVITY_INSTANCE_VERSION_MISMATCH")
        snapshot = definition.get("nativeSnapshot")
        if snapshot is not None and (
            snapshot.get("studyUid") != self.mapper._study_uid
            or snapshot.get("studyValueVersion") != self.mapper._study_value_version
            or snapshot.get("selection", {}).get("properties", {}).get("uid") != row.study_activity_instance_uid
            or snapshot.get("activityInstance", {}).get("uid") != uid
            or snapshot.get("activityInstance", {}).get("version") != version
            or (self.mapper._study_as_of is not None
                and snapshot.get("asOf") != native_json(self.mapper._study_as_of))
        ):
            raise USDMMappingAuthorityRequired("USDM_ACTIVITY_INSTANCE_SOURCE_SCOPE_MISMATCH")
        self.context.retain(
            "activityInstanceDefinition", f"{uid}@{version}/selection:{row.study_activity_instance_uid}", definition,
            scope={"studyUid": self.mapper._study_uid,
                   "studyValueVersion": self.mapper._study_value_version,
                   "studyActivityInstanceUid": row.study_activity_instance_uid},
        )
        return native_json(definition)

    def _footnotes(self, design, version) -> None:
        targets = dict(self._activity_targets)
        for kind, model, field, values in (
            ("StudyVisit", "Encounter", "uid", design.encounters),
            ("StudyEpoch", "StudyEpoch", "uid", design.epochs),
        ):
            record_kind = "studyVisit" if kind == "StudyVisit" else "studyEpoch"
            for entry in self.context.native_records:
                if entry["kind"] == record_kind:
                    uid = entry["record"][field]
                    target = next((item for item in values if item.id == self.identifier(model, uid)), None)
                    if target is not None:
                        targets[(kind, uid)] = target
        for row in self.rows.get("studySoAFootnote", []):
            footnote = getattr(row, "footnote", None)
            text = getattr(footnote, "name", None)
            if text is None:
                self.issue(
                    "USDM_FOOTNOTE_TEXT_UNRESOLVED", f"study-soa-footnotes/{row.uid}",
                    "CommentAnnotation/text", "The selected footnote is not instantiated.",
                    "Instantiate the native footnote template and its parameter values.",
                )
                continue
            references = getattr(row, "referenced_items", None) or []
            # Always retain the exact complete reference list once, including
            # schedule-cell annotations which cannot be widened to all visits.
            annotation = CommentAnnotation(
                id=self.identifier("CommentAnnotation", "footnote:" + row.uid),
                text=text, extensionAttributes=[self.extension("soaFootnote", row.uid, row)],
            )
            version.notes.append(annotation)
            scoped_schedules = set()
            for reference in references:
                kind = getattr(reference.item_type, "value", reference.item_type)
                if kind == "StudyActivitySchedule":
                    if reference.item_uid not in scoped_schedules:
                        scoped_schedules.add(reference.item_uid)
                        scope = self._schedule_footnote_scope(row, reference.item_uid, design, targets)
                        if scope is not None:
                            annotation.extensionAttributes.append(scope)
                    continue
                target = targets.get((kind, reference.item_uid))
                if target is not None:
                    target.notes.append(CommentAnnotation(
                        id=self.identifier("CommentAnnotation", f"footnote:{row.uid}:{kind}:{reference.item_uid}"),
                        text=text, extensionAttributes=[self.extension(
                            "footnote-target", f"{row.uid}:{kind}:{reference.item_uid}",
                            {"footnoteUid": row.uid, "reference": native_json(reference)},
                        )],
                    ))
                elif kind != "StudySoAFootnote":
                    self.issue(
                        "USDM_FOOTNOTE_TARGET_UNRESOLVED",
                        f"study-soa-footnotes/{row.uid}/referenced_items/{reference.item_uid}",
                        "CommentAnnotation", "The exact annotation target is absent.",
                        "Resolve the selected native footnote target.",
                    )

    def _schedule_footnote_scope(self, footnote, schedule_uid, design, targets):
        """Bind an annotation to exact source schedule and canonical occurrence.

        Native operational rows reuse the planning schedule UID and qualify it
        by selected activity instance. No label or activity/visit pair joins a
        different schedule UID into this scope.
        """
        source_path = f"study-soa-footnotes/{footnote.uid}/referenced_items/{schedule_uid}"

        def unresolved(message):
            self.issue(
                SCHEDULE_FOOTNOTE_SCOPE_REQUIRED, source_path,
                "CommentAnnotation/extensionAttributes", message,
                "Resolve this exact selected schedule's visit and activity-instance targets. "
                "Keep the note scoped to that occurrence; do not substitute a whole activity or visit.",
            )
            return None

        readings = [
            entry for entry in self.context.native_records
            if entry["kind"] in {"studyActivitySchedule", "studyOperationalActivitySchedule"}
            and entry["record"].get("study_activity_schedule_uid") == schedule_uid
        ]
        if not readings:
            return unresolved("The footnote names a schedule absent from this selected study version.")
        source_pairs = {
            (entry["record"].get("study_activity_uid"), entry["record"].get("study_visit_uid"))
            for entry in readings
        }
        if len(source_pairs) != 1:
            return unresolved("The native schedule readings disagree about the exact activity or visit.")
        activity_uid, visit_uid = next(iter(source_pairs))
        parent = targets.get(("StudyActivity", activity_uid))
        encounter = targets.get(("StudyVisit", visit_uid))
        if parent is None or encounter is None:
            return unresolved("The schedule's selected activity or visit has no canonical target.")
        operational = [entry for entry in readings if entry["kind"] == "studyOperationalActivitySchedule"]
        selected = operational or readings
        activity_targets = {}
        for entry in selected:
            instance_uid = entry["record"].get("study_activity_instance_uid")
            if operational:
                target = targets.get(("StudyActivityInstance", instance_uid)) if instance_uid else None
                if target is None or target.id not in parent.childIds:
                    return unresolved("An operational schedule instance is missing or belongs to another activity.")
            else:
                target = parent
            activity_targets[target.id] = target
        matches = [
            (timeline, instance)
            for timeline in design.scheduleTimelines for instance in timeline.instances
            if instance.instanceType == "ScheduledActivityInstance"
            and instance.encounterId == encounter.id
            and set(activity_targets).issubset(instance.activityIds)
        ]
        if len(matches) != 1:
            return unresolved("The schedule does not identify one exact canonical scheduled occurrence with all targets.")
        timeline, instance = matches[0]
        key = json.dumps([footnote.uid, schedule_uid], separators=(",", ":"), ensure_ascii=False)

        def value(name, **values):
            return ExtensionAttribute(
                id=self.identifier("ExtensionAttribute", f"schedule-footnote:{key}:{name}"),
                instanceType="ExtensionAttribute",
                url=f"{SCHEDULE_FOOTNOTE_SCOPE_URL}/{name}",
                **values,
            )

        # CommentAnnotation has no native scheduled-target field, and a
        # ScheduledActivityInstance has no notes property in pinned USDM4.
        # Standard valueId links preserve exact scope without widening it.
        return ExtensionAttribute(
            id=self.identifier("ExtensionAttribute", f"schedule-footnote:{key}"),
            instanceType="ExtensionAttribute",
            url=SCHEDULE_FOOTNOTE_SCOPE_URL,
            extensionAttributes=[
                value("timelineId", valueId=timeline.id),
                value("scheduledInstanceId", valueId=instance.id),
                value("encounterId", valueId=encounter.id),
                value("sourceActivityId", valueId=parent.id),
                value("activityIds", extensionAttributes=[
                    value("activityId/" + identifier, valueId=identifier)
                    for identifier in sorted(activity_targets)
                ]),
                self.extension("schedule-footnote-source", key, {
                    "nativeScheduleUid": schedule_uid,
                    "readings": readings,
                }),
            ],
        )

    def _history(self, study, document, version) -> None:
        from clinical_mdr_api.services.ddf.usdm_mapper import _items

        reader = self.mapper._get_snapshot_history
        if reader is None:
            return
        result = reader(study_uid=study.uid, page_size=0)
        rows = _items(result)
        metadata = getattr(study.current_metadata, "version_metadata", None)
        selected = self.mapper._study_value_version
        cutoff = getattr(metadata, "version_timestamp", None)
        for row in rows:
            row_version = getattr(row, "metadata_version", None)
            if selected is not None:
                try:
                    if row_version is None or Decimal(row_version) > Decimal(selected):
                        continue
                except InvalidOperation:
                    self.issue(
                        "USDM_HISTORY_VERSION_UNRESOLVED", "study-snapshot-history/metadata_version",
                        "StudyVersion/extensionAttributes", "History cannot be bounded by the selected native version.",
                        "Resolve the explicit native snapshot version.",
                    )
                    continue
                if cutoff is not None and row.modified_date > cutoff:
                    continue
            key = f"{row_version}@{row.modified_date.isoformat()}"
            self.context.retain(
                "studySnapshotHistory", key, row,
                scope={"studyUid": study.uid, "studyValueVersion": selected},
            )
            # An OSB metadata lock/release is not document approval. Keep history
            # visible without minting a final protocol status or approval date.

    def protocol_document(self, study):
        reader = self.mapper._get_protocol_header
        if reader is None:
            return None
        header = self.mapper._call(reader, study.uid)
        self.context.retain(
            "studyProtocolHeader", study.uid, header,
            scope={"studyUid": study.uid, "studyValueVersion": self.mapper._study_value_version},
        )
        number = getattr(header, "protocol_header_version", None)
        if number is None:
            return None
        version = self.context.build(
            StudyDefinitionDocumentVersion, "study-protocol-header/status",
            id=self.identifier("StudyDefinitionDocumentVersion", f"protocol:{study.uid}:{number}"),
            version=number,
            extensionAttributes=[self.extension("protocol-header-version", study.uid, header)],
        )
        return self.context.build(
            StudyDefinitionDocument, "study-protocol-header",
            id=self.identifier("StudyDefinitionDocument", "protocol:" + study.uid),
            name=self.mapper._get_study_name(study),
            label=self.mapper._get_study_label(study),
            type=self.mapper.get_ct_package_term_as_usdm_code("C70817"),
            versions=[version],
        )

    def cells(self, study):
        from clinical_mdr_api.services.ddf.usdm_mapper import _items, _stable_selection_order

        rows = _stable_selection_order(
            _items(self.mapper._call(self.mapper._get_osb_study_design_cells, study.uid)),
            "design_cell_uid",
        )
        groups = {}
        known = {}
        for key, reader, identity in (
            ("arms", self.mapper._get_osb_study_arms, "arm_uid"),
            ("epochs", self.mapper._get_osb_study_epochs, "uid"),
            ("elements", self.mapper._get_osb_study_elements, "element_uid"),
        ):
            known[key] = {getattr(row, identity) for row in _items(self.mapper._call(reader, study.uid))}
        branches = {row.branch_arm_uid: row for row in self.rows.get("studyBranchArm", [])}
        for row in rows:
            branch_uid = getattr(row, "study_branch_arm_uid", None)
            arm_uid = getattr(row, "study_arm_uid", None)
            epoch_uid = getattr(row, "study_epoch_uid", None)
            element_uid = getattr(row, "study_element_uid", None)
            if branch_uid is not None:
                branch = branches.get(branch_uid)
                parent_uid = getattr(getattr(branch, "arm_root", None), "arm_uid", None)
                if branch is None or (arm_uid is not None and parent_uid != arm_uid):
                    self.issue(
                        "USDM_CELL_BRANCH_UNRESOLVED", f"study-design-cells/{row.design_cell_uid}",
                        "StudyCell/armId", "The selected branch does not belong to this exact parent arm.",
                        "Resolve the native design-cell branch association.",
                    )
                    continue
                arm_key = "branch:" + branch_uid
            else:
                arm_key = arm_uid
            if (
                arm_key is None or epoch_uid not in known["epochs"] or element_uid not in known["elements"]
                or (branch_uid is None and arm_uid not in known["arms"])
                or (branch_uid is not None and parent_uid not in known["arms"])
            ):
                self.issue(
                    "USDM_CELL_SELECTION_UNRESOLVED", f"study-design-cells/{row.design_cell_uid}",
                    "StudyCell", "The native cell has no complete arm, epoch and element association.",
                    "Complete the native design cell selections.",
                )
                continue
            key = (arm_key, epoch_uid)
            if key not in groups:
                groups[key] = StudyCell(
                    id=self.identifier("StudyCell", f"arm:{arm_key}:epoch:{epoch_uid}"),
                    armId=self.identifier("StudyArm", arm_key),
                    epochId=self.identifier("StudyEpoch", epoch_uid), elementIds=[],
                )
            cell = groups[key]
            element_id = self.identifier("StudyElement", element_uid)
            if element_id not in cell.elementIds:
                cell.elementIds.append(element_id)
            self.context.cell_scopes[row.design_cell_uid] = {
                "studyCellId": cell.id,
                "armId": cell.armId,
                "epochId": cell.epochId,
                "elementId": element_id,
                **({"parentArmId": self.identifier("StudyArm", parent_uid)} if branch_uid is not None else {}),
            }
            cell.extensionAttributes.append(self.extension("studyDesignCell", row.design_cell_uid, row))
        return list(groups.values())

    def timelines(self, study):
        from clinical_mdr_api.services.ddf.usdm_mapper import (
            _items, _stable_selection_order, _accepts_keyword,
            get_ddf_timing_iso_duration_value,
        )

        visits = _stable_selection_order(
            _items(self.mapper._call(self.mapper._get_osb_study_visits, study.uid)), "uid"
        )
        if not visits:
            return []
        reader = self.mapper._get_osb_activity_schedules
        options = {"operational": False} if _accepts_keyword(reader, "operational") else {}
        schedules = _stable_selection_order(
            _items(self.mapper._call(reader, study.uid, **options)), "study_activity_schedule_uid"
        )
        operational = (
            sorted(
                _items(self.mapper._call(reader, study.uid, operational=True)),
                key=lambda row: (row.study_activity_schedule_uid, row.study_activity_instance_uid or ""),
            ) if _accepts_keyword(reader, "operational") else []
        )
        by_uid = {visit.uid: visit for visit in visits}
        ids = {uid: self.identifier("ScheduledActivityInstance", "visit:" + uid) for uid in by_uid}
        selected_instances = {
            row.study_activity_instance_uid: row
            for row in self.rows.get("studyActivityInstance", [])
            if getattr(row, "activity_instance", None) is not None
        }
        known_activities = {
            entry["uid"] for entry in self.context.native_records
            if entry["kind"] == "studyActivity"
        }
        # Activity definitions may not have been requested by a private-method
        # caller. The full mapper has already captured them before the timeline.
        if not self.mapper._mapping_active:
            known_activities = {
                row.study_activity_uid
                for row in _items(self.mapper._call(self.mapper._get_osb_study_activities, study.uid))
            }
        exit_ = ScheduleTimelineExit(id=self.identifier("ScheduleTimelineExit", study.uid))
        instances, timings = [], []
        for index, visit in enumerate(visits):
            specific = [row for row in operational if row.study_visit_uid == visit.uid]
            specific_parents = {row.study_activity_uid for row in specific}
            planned = [
                row for row in schedules
                if row.study_visit_uid == visit.uid and row.study_activity_uid not in specific_parents
            ]
            activity_ids = []
            for row in [*planned, *specific]:
                instance_uid = getattr(row, "study_activity_instance_uid", None)
                selected = selected_instances.get(instance_uid) if instance_uid else None
                if instance_uid:
                    valid = (selected is not None and selected.study_activity_uid == row.study_activity_uid
                             and row.study_activity_uid in known_activities)
                    key = "instance:" + instance_uid
                else:
                    valid = row.study_activity_uid in known_activities
                    key = row.study_activity_uid
                if not valid:
                    self.issue(
                        "USDM_SCHEDULE_ACTIVITY_UNRESOLVED",
                        f"study-activity-schedules/{row.study_activity_schedule_uid}",
                        "ScheduledActivityInstance/activityIds",
                        "The schedule has no exact selected activity/instance association.",
                        "Resolve the native operational schedule association.",
                    )
                    continue
                activity_id = self.identifier("Activity", key)
                if activity_id not in activity_ids:
                    activity_ids.append(activity_id)
            instance = ScheduledActivityInstance(
                id=ids[visit.uid],
                name=f"{visit.visit_short_name} [{visit.uid}]", label=visit.visit_name,
                encounterId=self.identifier("Encounter", visit.uid),
                epochId=self.identifier("StudyEpoch", visit.study_epoch_uid),
                activityIds=activity_ids,
                defaultConditionId=ids[visits[index + 1].uid] if index + 1 < len(visits) else None,
                timelineExitId=exit_.id if index + 1 == len(visits) else None,
                extensionAttributes=[self.extension("scheduledVisit", visit.uid, visit)],
            )
            instances.append(instance)
            if getattr(visit, "repeating_frequency", None) is not None:
                self.issue(
                    "USDM_VISIT_REPEAT_CONDITION_REQUIRED", f"study-visits/{visit.uid}/repeating_frequency",
                    "ScheduleTimeline", "A native repetition frequency does not specify a complete loop condition.",
                    "Define the governed repetition/exit condition before compiling an executable timeline.",
                )
            value, unit = getattr(visit, "time_value", None), getattr(visit, "time_unit_name", None)
            if value is None or not unit:
                continue
            anchor_uid = getattr(visit, "resolved_anchor_visit_uid", None)
            fixed = visit.is_global_anchor_visit or (anchor_uid == visit.uid)
            if fixed:
                anchor_uid = visit.uid
            elif anchor_uid not in by_uid:
                self.issue(
                    "USDM_VISIT_TIMING_ANCHOR_UNRESOLVED", f"study-visits/{visit.uid}/time_reference",
                    "Timing/relativeToScheduledInstanceId",
                    "The native timeline has not resolved an exact anchor for this timing.",
                    "Resolve the native visit time reference; a global anchor is not a fallback for a different reference.",
                )
            timing_values = dict(
                id=self.identifier("Timing", visit.uid), name=f"Timing {visit.uid}",
                type=(self.mapper.get_ddf_timing_type_code_fixed() if fixed
                      else self.mapper.get_ddf_timing_type_code_before() if value < 0
                      else self.mapper.get_ddf_timing_type_code_after()),
                value=get_ddf_timing_iso_duration_value(value, unit),
                valueLabel=f"{abs(value)} {unit}",
                relativeToFrom=self.mapper.get_ddf_timing_relative_to_from(),
                # Official USDM4 orientation: from=scheduled target, to=anchor.
                relativeFromScheduledInstanceId=ids[visit.uid],
                relativeToScheduledInstanceId=ids.get(anchor_uid),
            )
            lower, upper = visit.min_visit_window_value, visit.max_visit_window_value
            lower = None if lower == -9999 else lower
            upper = None if upper == 9999 else upper
            window_unit = visit.visit_window_unit_name
            has_window = (
                window_unit and (lower is not None or upper is not None)
                and (lower != 0 or upper != 0)
            )
            if has_window:
                if fixed or (lower is not None and lower > 0) or (upper is not None and upper < 0):
                    self.issue(
                        "USDM_VISIT_WINDOW_UNREPRESENTABLE", f"study-visits/{visit.uid}/visit_window",
                        "Timing/windowLower",
                        "This native window cannot be represented as bounds around a non-anchor timing.",
                        "Review the native anchor/window definition; original signed bounds remain in source metadata.",
                    )
                else:
                    if lower is not None:
                        timing_values["windowLower"] = get_ddf_timing_iso_duration_value(lower, window_unit)
                    if upper is not None:
                        timing_values["windowUpper"] = get_ddf_timing_iso_duration_value(upper, window_unit)
                    if lower is not None and upper is not None:
                        timing_values["windowLabel"] = f"{lower}..{upper} {window_unit}"
                    else:
                        self.issue(
                            "USDM_VISIT_WINDOW_INCOMPLETE",
                            f"study-visits/{visit.uid}/visit_window",
                            "Timing/windowLower" if lower is None else "Timing/windowUpper",
                            "The native window has an unbounded or unknown limit. DDF00006 requires a fully defined canonical window.",
                            "Review the missing bound before validation and release; the known limit and exact native values are preserved.",
                        )
            timings.append(Timing(**timing_values))
        for row in [*schedules, *operational]:
            if row.study_visit_uid not in by_uid:
                self.issue(
                    "USDM_SCHEDULE_VISIT_UNRESOLVED", f"study-activity-schedules/{row.study_activity_schedule_uid}",
                    "ScheduledActivityInstance/encounterId", "A schedule names an absent selected visit.",
                    "Resolve the native visit selection.",
                )
        if not any(timing.type.code == "C201358" for timing in timings):
            self.issue(
                "USDM_TIMELINE_ANCHOR_REQUIRED", "study-visits", "ScheduleTimeline/timings",
                "No exact native fixed reference is available.",
                "Select a native anchor visit before compiling the schedule.",
            )
        timeline = self.context.build(
            ScheduleTimeline, f"study-visits/{visits[0].uid}/start_rule",
            id=self.identifier("ScheduleTimeline", study.uid), name="Main Timeline",
            mainTimeline=True, entryId=instances[0].id,
            entryCondition=getattr(visits[0], "start_rule", None),
            instances=instances, timings=timings, exits=[exit_],
        )
        return [timeline]
