import datetime
from dataclasses import dataclass
from typing import Any

from neomodel import db

from clinical_mdr_api import utils
from clinical_mdr_api.domain_repositories._utils.helpers import (
    acquire_write_lock_study_value,
)
from clinical_mdr_api.domain_repositories.controlled_terminologies.ct_codelist_attributes_repository import (
    CTCodelistAttributesRepository,
)
from clinical_mdr_api.domain_repositories.generic_repository import (
    _manage_versioning_with_relations,
)
from clinical_mdr_api.domain_repositories.models.controlled_terminology import (
    CTTermContext,
    CTTermRoot,
)
from clinical_mdr_api.domain_repositories.models.study import StudyRoot, StudyValue
from clinical_mdr_api.domain_repositories.models.study_audit_trail import (
    Create,
    Delete,
    Edit,
    StudyAction,
)
from clinical_mdr_api.domain_repositories.models.study_selections import (
    StudyArm,
    StudyArmLifecycleEvent,
)
from clinical_mdr_api.domain_repositories.study_selections.study_arm_origin_repository import (
    StudyArmOriginRepository,
)
from clinical_mdr_api.domains.enums import StudyDesignClassEnum
from clinical_mdr_api.domains.study_selections.study_arm_lifecycle import (
    LifecycleTargetKind,
    StudyArmLifecycleEventVO,
    validate_lifecycle_target,
)
from clinical_mdr_api.domains.study_selections.study_arm_origin import (
    STUDY_ARM_ORIGIN_CATALOGUE,
    STUDY_ARM_ORIGIN_CODELIST_UID,
    validate_study_arm_origin,
)
from clinical_mdr_api.domains.study_selections.study_selection_arm import (
    StudySelectionArmAR,
    StudySelectionArmVO,
)
from clinical_mdr_api.services._utils import ensure_transaction
from common.config import settings
from common.exceptions import (
    AlreadyExistsException,
    BusinessLogicException,
    NotFoundException,
    ValidationException,
)
from common.telemetry import trace_calls
from common.utils import convert_to_datetime, get_db_result_as_dict


@dataclass
class SelectionHistoryArm:
    """Class for selection history items"""

    study_selection_uid: str
    study_uid: str | None
    arm_name: str
    arm_short_name: str
    arm_label: str | None
    arm_code: str | None
    arm_description: str | None
    arm_randomization_group: str | None
    arm_number_of_subjects: int | None
    arm_type: str | None
    # Study selection Versioning
    start_date: datetime.datetime
    author_id: str
    change_type: str
    end_date: datetime.datetime | None
    order: int
    status: str | None
    accepted_version: bool | None
    merge_branch_for_this_arm_for_sdtm_adam: bool
    data_origin_type_uid: str | None = None
    data_origin_description: str | None = None


class StudySelectionArmRepository:

    @staticmethod
    def _lifecycle_study_value(
        study_uid: str, study_value_version: str | None = None
    ) -> tuple[StudyRoot, StudyValue]:
        if study_value_version is None:
            query = (
                "MATCH (root:StudyRoot {uid:$study_uid})-[:LATEST]->(value:StudyValue)"
            )
        else:
            query = (
                "MATCH (root:StudyRoot {uid:$study_uid})"
                "-[:HAS_VERSION {status:'RELEASED',version:$version}]->(value:StudyValue)"
            )
        rows, _ = db.cypher_query(
            query + " RETURN DISTINCT root, value",
            {"study_uid": study_uid, "version": study_value_version},
            resolve_objects=True,
        )
        NotFoundException.raise_if_not(
            rows, msg="The requested native study version does not exist."
        )
        ValidationException.raise_if(
            len(rows) != 1,
            msg="Lifecycle history requires one exact native study value.",
        )
        return rows[0][0], rows[0][1]

    @staticmethod
    def _lifecycle_target(value: StudyValue, target_kind: str, target_uid: str):
        validate_lifecycle_target(target_kind, target_uid)
        relation, label = (
            ("HAS_STUDY_ARM", "StudyArm")
            if target_kind == "arm"
            else ("HAS_STUDY_BRANCH_ARM", "StudyBranchArm")
        )
        rows, _ = db.cypher_query(
            f"MATCH (value:StudyValue)-[:{relation}]->(target:{label} {{uid:$target_uid}}) "
            f"WHERE {db.get_id_method()}(value)=$value RETURN target",
            {"value": value.element_id, "target_uid": target_uid},
            resolve_objects=True,
        )
        NotFoundException.raise_if_not(
            rows, msg="The lifecycle target is not selected in this study version."
        )
        ValidationException.raise_if(
            len(rows) != 1,
            msg="Lifecycle history requires one exact selected target.",
        )
        return rows[0][0]

    @staticmethod
    def _restore_lifecycle_event(
        study_uid: str, record: dict[str, Any]
    ) -> StudyArmLifecycleEventVO:
        values = record["event"]
        target_kind = values.get("target_kind")
        target_uid = values.get("target_uid")
        validate_lifecycle_target(target_kind, target_uid)
        expected_relation, expected_label = (
            ("STUDY_ARM_HAS_LIFECYCLE_EVENT", "StudyArm")
            if target_kind == "arm"
            else ("STUDY_BRANCH_ARM_HAS_LIFECYCLE_EVENT", "StudyBranchArm")
        )
        targets = record["targets"]
        ValidationException.raise_if(
            values.get("study_uid") != study_uid
            or set(record["owners"]) != {study_uid}
            or not targets
            or any(
                target["uid"] != target_uid
                or target["relation"] != expected_relation
                or expected_label not in target["labels"]
                or set(target["owners"]) != {study_uid}
                for target in targets
            )
            or sum(target["selected"] for target in targets) != 1,
            msg="Lifecycle event target or study version ownership is inconsistent.",
        )
        actions = record["actions"]
        ValidationException.raise_if(
            record["has_before"]
            or len(actions) != 1
            or actions[0]["owners"] != [study_uid]
            or "Create" not in actions[0]["labels"]
            or any(label in actions[0]["labels"] for label in ("Edit", "Delete")),
            msg="Lifecycle event must retain exactly one native Create audit record.",
        )
        return StudyArmLifecycleEventVO(
            uid=values.get("uid"),
            study_uid=study_uid,
            target_kind=target_kind,
            target_uid=target_uid,
            lifecycle_state=values.get("lifecycle_state"),
            effective_date=convert_to_datetime(value=values.get("effective_date")),
            decision_reference=values.get("decision_reference"),
            change_reason=values.get("change_reason"),
            recorded_at=convert_to_datetime(value=actions[0].get("recorded_at")),
            author_id=actions[0].get("author_id"),
        )

    def find_lifecycle_events(
        self,
        study_uid: str,
        study_value_version: str | None = None,
        target_kind: LifecycleTargetKind | None = None,
        target_uid: str | None = None,
    ) -> list[StudyArmLifecycleEventVO]:
        ValidationException.raise_if(
            (target_kind is None) != (target_uid is None),
            msg="Lifecycle target kind and UID must be supplied together.",
        )
        _, value = self._lifecycle_study_value(study_uid, study_value_version)
        if target_kind is not None:
            assert target_uid is not None
            self._lifecycle_target(value, target_kind, target_uid)
        query = f"""
            MATCH (value:StudyValue) WHERE {db.get_id_method()}(value)=$value
            MATCH (value)-[:HAS_STUDY_ARM_LIFECYCLE_EVENT]->(event:StudyArmLifecycleEvent)
            RETURN properties(event) AS event,
                [(target)-[link:STUDY_ARM_HAS_LIFECYCLE_EVENT|STUDY_BRANCH_ARM_HAS_LIFECYCLE_EVENT]->(event) |
                    {{uid:target.uid, relation:type(link), labels:labels(target),
                      owners:[(target_owner:StudyRoot)-[:AUDIT_TRAIL]->(:StudyAction)-[:AFTER]->(target) | target_owner.uid],
                      selected:CASE type(link)
                        WHEN 'STUDY_ARM_HAS_LIFECYCLE_EVENT' THEN EXISTS {{ MATCH (value)-[:HAS_STUDY_ARM]->(target) }}
                        ELSE EXISTS {{ MATCH (value)-[:HAS_STUDY_BRANCH_ARM]->(target) }} END}}] AS targets,
                [(owner:StudyRoot)-[:HAS_VERSION|LATEST]->(:StudyValue)-[:HAS_STUDY_ARM_LIFECYCLE_EVENT]->(event) | owner.uid] AS owners,
                [(action:StudyAction)-[:AFTER]->(event) |
                    {{author_id:action.author_id, recorded_at:action.date, labels:labels(action),
                      owners:[(audit_owner:StudyRoot)-[:AUDIT_TRAIL]->(action) | audit_owner.uid]}}] AS actions,
                EXISTS {{ MATCH (:StudyAction)-[:BEFORE]->(event) }} AS has_before
        """
        records = utils.db_result_to_list(
            db.cypher_query(query, {"study_uid": study_uid, "value": value.element_id})
        )
        events = [
            self._restore_lifecycle_event(study_uid, record) for record in records
        ]
        ValidationException.raise_if(
            len({event.uid for event in events}) != len(events),
            msg="The selected study value contains duplicate lifecycle event identities.",
        )
        return sorted(
            (
                event
                for event in events
                if target_kind is None
                or (event.target_kind == target_kind and event.target_uid == target_uid)
            ),
            key=lambda event: event.sort_key,
        )

    @ensure_transaction(db)
    def create_lifecycle_event(
        self,
        study_uid: str,
        target_kind: LifecycleTargetKind,
        target_uid: str,
        expected_study_version: str,
        lifecycle_state: str,
        effective_date: datetime.datetime,
        decision_reference: str,
        change_reason: str,
        author_id: str,
    ) -> StudyArmLifecycleEventVO:
        acquire_write_lock_study_value(study_uid)
        root, value = self._lifecycle_study_value(study_uid)
        states, _ = db.cypher_query(
            "MATCH (:StudyRoot {uid:$study_uid})-[state:HAS_VERSION]->(value:StudyValue) "
            f"WHERE {db.get_id_method()}(value)=$value AND state.end_date IS NULL "
            "RETURN state.status, state.version",
            {"study_uid": study_uid, "value": value.element_id},
        )
        BusinessLogicException.raise_if(
            len(states) != 1 or states[0][0] != "DRAFT",
            msg="Lifecycle events can only be authored in the current draft study.",
        )
        AlreadyExistsException.raise_if(
            str(states[0][1]) != expected_study_version,
            msg="Study version changed; reload before adding a lifecycle event.",
        )
        target = self._lifecycle_target(value, target_kind, target_uid)
        # Existing corruption must remain visible, including other targets.
        self.find_lifecycle_events(study_uid)
        event = StudyArmLifecycleEventVO(
            uid=StudyArmLifecycleEvent.get_next_free_uid_and_increment_counter(),
            study_uid=study_uid,
            target_kind=target_kind,
            target_uid=target_uid,
            lifecycle_state=lifecycle_state,
            effective_date=effective_date,
            decision_reference=decision_reference,
            change_reason=change_reason,
            recorded_at=datetime.datetime.now(datetime.timezone.utc),
            author_id=author_id,
        )
        node = StudyArmLifecycleEvent(
            uid=event.uid,
            study_uid=event.study_uid,
            target_kind=event.target_kind,
            target_uid=event.target_uid,
            lifecycle_state=event.lifecycle_state,
            effective_date=event.effective_date,
            decision_reference=event.decision_reference,
            change_reason=event.change_reason,
        ).save()
        value.has_study_arm_lifecycle_event.connect(node)
        target.has_lifecycle_event.connect(node)
        _manage_versioning_with_relations(
            study_root=root,
            action_type=Create,
            after=node,
            date=event.recorded_at,
            author_id=event.author_id,
        )
        return event

    @staticmethod
    def assert_lifecycle_target_can_be_deleted(
        study_uid: str, target_kind: LifecycleTargetKind, target_uid: str
    ) -> None:
        acquire_write_lock_study_value(study_uid)
        events = StudySelectionArmRepository().find_lifecycle_events(study_uid)
        targets = {(target_kind, target_uid)}
        if target_kind == "arm" and events:
            rows, _ = db.cypher_query(
                "MATCH (:StudyRoot {uid:$study_uid})-[:LATEST]->(value:StudyValue)"
                "-[:HAS_STUDY_ARM]->(:StudyArm {uid:$target_uid})"
                "-[:STUDY_ARM_HAS_BRANCH_ARM]->(branch:StudyBranchArm)"
                "<-[:HAS_STUDY_BRANCH_ARM]-(value) RETURN DISTINCT branch.uid",
                {"study_uid": study_uid, "target_uid": target_uid},
            )
            targets.update(("branch_arm", row[0]) for row in rows)
        BusinessLogicException.raise_if(
            any((event.target_kind, event.target_uid) in targets for event in events),
            msg="An arm or branch with lifecycle history cannot be deleted. Retain the selection and author its lifecycle change.",
        )

    def arm_specific_has_connected_cell(self, study_uid: str, arm_uid: str) -> bool:
        """
        Returns True if StudyArm with specified uid has connected at least one StudyDesignCell.
        :return:
        """

        sdc_node = (
            StudyArm.nodes.traverse("has_design_cell", "has_after")
            .filter(
                study_value__latest_value__uid=study_uid,
                uid=arm_uid,
                has_design_cell__study_value__latest_value__uid=study_uid,
            )
            .resolve_subgraph()
        )
        return len(sdc_node) > 0

    def get_arms_branches_and_cohorts(
        self,
        study_uid: str | None = None,
        study_value_version: str | None = None,
    ) -> list[dict[str, Any]]:
        query = ""
        query_parameters = {}
        if study_value_version:
            if study_uid:
                query = "MATCH (sr:StudyRoot { uid: $uid})-[l:HAS_VERSION{status:'RELEASED', version:$study_value_version}]->(sv:StudyValue)"
                query_parameters["study_value_version"] = study_value_version
                query_parameters["uid"] = study_uid
            else:
                query = "MATCH (sr:StudyRoot)-[l:HAS_VERSION{status:'RELEASED', version:$study_value_version}]->(sv:StudyValue)"
                query_parameters["study_value_version"] = study_value_version
        else:
            if study_uid:
                query = "MATCH (sr:StudyRoot { uid: $uid})-[l:LATEST]->(sv:StudyValue)"
                query_parameters["uid"] = study_uid
            else:
                query = "MATCH (sr:StudyRoot)-[l:LATEST]->(sv:StudyValue)"

        query += """
            WITH sr, sv
            MATCH (sv)-[:HAS_STUDY_ARM]->(sar:StudyArm)
            WITH DISTINCT sr, sv, sar

            // Get StudyCohorts
            OPTIONAL MATCH (sv)-[:HAS_STUDY_COHORT]->(study_cohorts:StudyCohort)
            WITH DISTINCT  sr, sv, sar, study_cohorts.uid as cohort_uid

            // Get latest version of StudyCohorts
            CALL (cohort_uid) {
                MATCH (study_cohort:StudyCohort {uid:cohort_uid})-[:AFTER]-(action:StudyAction)
                WITH study_cohort, action
                ORDER BY action.date ASC
                WITH last(collect(study_cohort)) as study_cohort
                RETURN CASE WHEN exists((study_cohort)--(:Delete)) THEN NULL ELSE study_cohort END AS study_cohort
            }

            // Get StudyBranchArms
            OPTIONAL MATCH (study_cohort)<-[:STUDY_BRANCH_ARM_HAS_COHORT]-(study_branch_arms:StudyBranchArm)-[:STUDY_ARM_HAS_BRANCH_ARM]-(sar)
            OPTIONAL MATCH (sv)-[:HAS_STUDY_BRANCH_ARM]->(study_branch_arms)
            WITH DISTINCT study_branch_arms, sr, sar, study_cohort
            ORDER BY study_branch_arms.order
            WITH DISTINCT study_branch_arms.uid as branch_arm_uid, sr, sar, study_cohort

            // Get latest version of StudyBranchArms
            CALL (branch_arm_uid) {
                MATCH (study_branch_arm:StudyBranchArm {uid:branch_arm_uid})-[:AFTER]-(action:StudyAction)
                WITH study_branch_arm, action
                ORDER BY action.date ASC
                WITH last(collect(study_branch_arm)) as branch_arm
                RETURN CASE WHEN exists((branch_arm)--(:Delete)) THEN NULL ELSE branch_arm END AS branch_arm
            }

            // Collect StudyBranchArms for given StudyCohort
            WITH sr, sar, study_cohort, 
                collect(branch_arm {
                    .uid,
                    .name,
                    .short_name,
                    .randomization_group,
                    .branch_arm_code,
                    .number_of_subjects
                }) as branch_arms
            ORDER BY study_cohort.order, sar.order
            WITH sr, sar, 
                collect(study_cohort {
                    .uid,
                    .name,
                    .short_name,
                    number_of_subjects: apoc.coll.sum([branch_arm in branch_arms WHERE branch_arm.number_of_subjects is not null | branch_arm.number_of_subjects]),
                    study_branch_arms:branch_arms
                }) AS study_cohorts
            RETURN DISTINCT 
                sr.uid AS study_uid,
                sar.uid AS uid,
                sar.name AS name,
                sar.short_name AS short_name,
                sar.label AS label,
                apoc.coll.sum([cohort in study_cohorts WHERE cohort.number_of_subjects is not null | cohort.number_of_subjects])AS number_of_subjects,
                study_cohorts
        """

        rows, columns = db.cypher_query(query, query_parameters)
        return [get_db_result_as_dict(row, columns) for row in rows]

    def _retrieves_all_data(
        self,
        study_uid: str | None = None,
        project_name: str | None = None,
        project_number: str | None = None,
        study_value_version: str | None = None,
    ) -> tuple[StudySelectionArmVO]:
        query = ""
        query_parameters: dict[str, Any] = {}
        if study_value_version:
            if study_uid:
                query = "MATCH (sr:StudyRoot { uid: $uid})-[l:HAS_VERSION{status:'RELEASED', version:$study_value_version}]->(sv:StudyValue)"
                query_parameters["study_value_version"] = study_value_version
                query_parameters["uid"] = study_uid
            else:
                query = "MATCH (sr:StudyRoot)-[l:HAS_VERSION{status:'RELEASED', version:$study_value_version}]->(sv:StudyValue)"
                query_parameters["study_value_version"] = study_value_version
        else:
            if study_uid:
                query = "MATCH (sr:StudyRoot { uid: $uid})-[l:LATEST]->(sv:StudyValue)"
                query_parameters["uid"] = study_uid
            else:
                query = "MATCH (sr:StudyRoot)-[l:LATEST]->(sv:StudyValue)"

        if project_name is not None or project_number is not None:
            query += (
                "-[:HAS_PROJECT]->(:StudyProjectField)<-[:HAS_FIELD]-(proj:Project)"
            )
            filter_list = []
            if project_name is not None:
                filter_list.append("proj.name=$project_name")
                query_parameters["project_name"] = project_name
            if project_number is not None:
                filter_list.append("proj.project_number=$project_number")
                query_parameters["project_number"] = project_number
            query += " WHERE "
            query += " AND ".join(filter_list)
        query_parameters["is_cohort_stepper_defined"] = (
            StudyDesignClassEnum.STUDY_WITH_COHORTS_BRANCHES_AND_SUBPOPULATIONS.value
        )
        query += """
            WITH sr, sv
            MATCH (sv)-[:HAS_STUDY_ARM]->(sar:StudyArm)
            WITH DISTINCT sr, sar, sv
            OPTIONAL MATCH (sar)-[:HAS_ARM_TYPE]->(st:CTTermContext)-[:HAS_SELECTED_TERM]->(elr:CTTermRoot)
            CALL {
                WITH sar
                OPTIONAL MATCH (sar)-[:HAS_ARM_DATA_ORIGIN_TYPE]->(origin_context:CTTermContext)
                OPTIONAL MATCH (origin_context)-[:HAS_SELECTED_TERM]->(origin:CTTermRoot)
                OPTIONAL MATCH (origin_context)-[:HAS_SELECTED_CODELIST]->(origin_cl:CTCodelistRoot)
                RETURN collect(CASE WHEN origin_context IS NULL THEN null ELSE
                    {term_uid: origin.uid, codelist_uid: origin_cl.uid}
                END) AS origin_memberships
            }
            OPTIONAL MATCH (sv)-[:HAS_STUDY_BRANCH_ARM]-(bars:StudyBranchArm)<-[:STUDY_ARM_HAS_BRANCH_ARM]-(sar)
            WITH DISTINCT 
                bars.uid as branch_arm_uid, elr, origin_memberships, sr, sar,
                exists((sv)-[:HAS_STUDY_DESIGN_CLASS]->(:StudyDesignClass {value:$is_cohort_stepper_defined})) AS is_cohort_stepper_defined
            CALL (branch_arm_uid) {
                MATCH (study_branch_arm:StudyBranchArm {uid:branch_arm_uid})-[:AFTER]-(action:StudyAction)
                WITH study_branch_arm, action
                ORDER BY action.date ASC
                WITH last(collect(study_branch_arm)) as study_branch_arm
                RETURN CASE WHEN exists((study_branch_arm)--(:Delete)) THEN NULL ELSE study_branch_arm END AS study_branch_arm
            }
            MATCH (sar)<-[:AFTER]-(sa:StudyAction)
            WITH DISTINCT elr, origin_memberships, sr, sar, sa, sum(study_branch_arm.number_of_subjects) as branch_sum, is_cohort_stepper_defined
            RETURN DISTINCT 
                sr.uid AS study_uid,
                sar.uid AS study_selection_uid,
                sar.name AS arm_name,
                sar.short_name AS arm_short_name,
                sar.label AS arm_label,
                sar.arm_code AS arm_code,
                sar.description AS arm_description,
                sar.order AS order,
                sar.accepted_version AS accepted_version,
                CASE is_cohort_stepper_defined
                    WHEN true THEN branch_sum
                    ELSE sar.number_of_subjects
                END AS number_of_subjects,
                sar.randomization_group AS randomization_group,
                coalesce(sar.merge_branch_for_this_arm_for_sdtm_adam, false) AS merge_branch_for_this_arm_for_sdtm_adam,
                elr.uid AS arm_type_uid,
                origin_memberships AS data_origin_memberships,
                sar.data_origin_description AS data_origin_description,
                sar.text AS text,
                sa.date AS start_date,
                sa.author_id AS author_id
                ORDER BY order
            """

        all_arm_selections = db.cypher_query(query, query_parameters)
        all_selections = []

        for selection in utils.db_result_to_list(all_arm_selections):
            acv = selection.get("accepted_version", False)
            if acv is None:
                acv = False
            selection_vo = StudySelectionArmVO.from_input_values(
                author_id=selection["author_id"],
                study_uid=selection["study_uid"],
                name=selection["arm_name"],
                short_name=selection["arm_short_name"],
                label=selection["arm_label"],
                code=selection["arm_code"],
                description=selection["arm_description"],
                study_selection_uid=selection["study_selection_uid"],
                arm_type_uid=selection["arm_type_uid"],
                data_origin_type_uid=StudyArmOriginRepository.selected_uid(
                    selection["data_origin_memberships"],
                    selection["data_origin_description"],
                ),
                data_origin_description=selection["data_origin_description"],
                number_of_subjects=selection["number_of_subjects"],
                randomization_group=selection["randomization_group"],
                merge_branch_for_this_arm_for_sdtm_adam=selection[
                    "merge_branch_for_this_arm_for_sdtm_adam"
                ],
                start_date=convert_to_datetime(value=selection["start_date"]),
                accepted_version=selection["accepted_version"],
            )
            all_selections.append(selection_vo)
        return tuple(all_selections)

    def find_all(
        self,
        project_name: str | None = None,
        project_number: str | None = None,
    ) -> list[StudySelectionArmAR]:
        """
        Finds all the selected study endpoints for all studies, and create the aggregate
        :return: List of StudySelectionArmAR, potentially empty
        """
        all_selections = self._retrieves_all_data(
            project_name=project_name,
            project_number=project_number,
        )
        # Create a dictionary, with study_uid as key, and list of selections as value
        selection_aggregate_dict: dict[Any, Any] = {}
        selection_aggregates = []
        for selection in all_selections:
            if selection.study_uid in selection_aggregate_dict:
                selection_aggregate_dict[selection.study_uid].append(selection)
            else:
                selection_aggregate_dict[selection.study_uid] = [selection]
        # Then, create the list of VO from the dictionary
        for study_uid, selections in selection_aggregate_dict.items():
            selection_aggregates.append(
                StudySelectionArmAR.from_repository_values(
                    study_uid=study_uid, study_arms_selection=selections
                )
            )
        return selection_aggregates

    @trace_calls(
        args=[1, 2, 3], kwargs=["study_uid", "for_update", "study_value_version"]
    )
    def find_by_study(
        self,
        study_uid: str,
        for_update: bool = False,
        study_value_version: str | None = None,
    ) -> StudySelectionArmAR:
        """
        Finds all the selected study arms for a given study
        :param study_uid:
        :param for_update:
        :return:
        """
        if for_update:
            acquire_write_lock_study_value(study_uid)
        all_selections = self._retrieves_all_data(
            study_uid, study_value_version=study_value_version
        )
        selection_aggregate = StudySelectionArmAR.from_repository_values(
            study_uid=study_uid, study_arms_selection=all_selections
        )
        if for_update:
            selection_aggregate.repository_closure_data = all_selections
        return selection_aggregate

    def _get_audit_node(
        self, study_selection: StudySelectionArmAR, study_selection_uid: str
    ):
        all_current_ids = []
        for item in study_selection.study_arms_selection:
            all_current_ids.append(item.study_selection_uid)
        all_closure_ids = []
        for item in study_selection.repository_closure_data:
            all_closure_ids.append(item.study_selection_uid)
        # if uid is in current data
        if study_selection_uid in all_current_ids:
            # if uid is in closure data
            if study_selection_uid in all_closure_ids:
                return Edit()
            return Create()
        return Delete()

    @ensure_transaction(db)
    def save(
        self,
        study_selection: StudySelectionArmAR,
        author_id: str,
        create_action_properties: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """
        Persist the set of selected study arms from the aggregate to the database
        :param study_selection:
        :param author_id:
        :param create_action_properties: per new arm uid, extra properties for its
            Create action (the copy-from-study provenance)
        """
        assert study_selection.repository_closure_data is not None

        # get the closure_data
        closure_data = study_selection.repository_closure_data
        closure_data_length = len(closure_data)

        # getting the latest study value node
        study_root_node = StudyRoot.nodes.get(uid=study_selection.study_uid)
        latest_study_value_node = study_root_node.latest_value.single()

        BusinessLogicException.raise_if(
            study_root_node.latest_locked.get_or_none() == latest_study_value_node,
            msg="You cannot add or reorder a study selection when the study is in a locked state.",
        )

        selections_to_remove = []
        selections_to_add = []

        # check if object is removed from the selection list - delete have been called
        if len(closure_data) > len(study_selection.study_arms_selection):
            # remove the last item from old list, as there will no longer be any study objective with that high order
            selections_to_remove.append((len(closure_data), closure_data[-1]))

        # loop through new data - start=1 as order starts at 1 not at 0 and find what needs to be removed and added
        for order, selection in enumerate(
            study_selection.study_arms_selection, start=1
        ):
            # check whether something new is added
            if closure_data_length > order - 1:
                # check if anything has changed
                if selection is not closure_data[order - 1]:
                    # update the selection by removing the old if the old exists, and adding new selection
                    selections_to_remove.append((order, closure_data[order - 1]))
                    selections_to_add.append((order, selection))
            else:
                # else something new have been added
                selections_to_add.append((order, selection))

        # audit trail nodes dictionary, holds the new nodes created for the audit trail
        audit_trail_nodes = {}
        # dictionary of last nodes to traverse to their old connections
        last_nodes = {}
        # loop through and remove selections
        for order, selection in selections_to_remove:
            # traverse --> study_value__study_arm__uid
            last_study_selection_node = latest_study_value_node.has_study_arm.get(
                uid=selection.study_selection_uid
            )
            # detect if the action should be create, delete or edit, then create audit node of the that StudyAction type
            audit_node = self._get_audit_node(
                study_selection, selection.study_selection_uid
            )

            # storage of the removed node audit trail to after put the "after" relationship to the new one
            audit_trail_nodes[selection.study_selection_uid] = audit_node
            # storage of the removed node to after get its connections
            last_nodes[selection.study_selection_uid] = last_study_selection_node
            if isinstance(audit_node, Delete):
                self._add_new_selection(
                    study_root_node,
                    latest_study_value_node,
                    order,
                    selection,
                    audit_node,
                    author_id=author_id,
                    for_deletion=True,
                    before_node=last_study_selection_node,
                )

        # loop through and add selections
        for order, selection in selections_to_add:
            # create last_study_selection_node None as the new study_selection could not have an audit trial node
            last_study_selection_node = None
            # if the study selection already has an audit trail node
            if selection.study_selection_uid in audit_trail_nodes:
                # extract the audit_trail_node
                audit_node = audit_trail_nodes[selection.study_selection_uid]
                # extract the last "AFTER" selection that now is "BEFORE"
                last_study_selection_node = last_nodes[selection.study_selection_uid]
            else:
                # if the audi_node doesn't exists, then create a new one
                audit_node = Create()

            self._add_new_selection(
                study_root_node,
                latest_study_value_node,
                order,
                selection,
                audit_node,
                author_id=author_id,
                for_deletion=False,
                before_node=last_study_selection_node,
                action_properties=(
                    (create_action_properties or {}).get(selection.study_selection_uid)
                    if isinstance(audit_node, Create)
                    else None
                ),
            )

    @staticmethod
    def arm_exists_by(
        db_property: str, value: str, arm_vo: StudySelectionArmVO
    ) -> StudyArm | None:
        kwarg_value = getattr(arm_vo, value)
        arm_node = (
            StudyArm.nodes.has(study_value=True)
            .filter(
                uid__ne=arm_vo.study_selection_uid,
                study_value__latest_value__uid=arm_vo.study_uid,
            )
            .get_or_none(**{db_property: kwarg_value})
        )
        return arm_node

    @staticmethod
    def _add_new_selection(
        study_root: StudyRoot,
        latest_study_value_node: StudyValue,
        order: int,
        selection: StudySelectionArmVO,
        audit_node: StudyAction,
        author_id: str,
        for_deletion: bool = False,
        before_node: StudyArm | None = None,
        action_properties: dict[str, str] | None = None,
    ):
        if for_deletion:
            StudySelectionArmRepository.assert_lifecycle_target_can_be_deleted(
                study_root.uid, "arm", selection.study_selection_uid
            )
        validate_study_arm_origin(
            selection.data_origin_type_uid, selection.data_origin_description
        )
        origin_context = None
        if selection.data_origin_type_uid is not None:
            if before_node is not None:
                previous_context = before_node.data_origin_type.single()
                if previous_context is not None:
                    previous_term = previous_context.has_selected_term.single()
                    if (
                        previous_term is not None
                        and previous_term.uid == selection.data_origin_type_uid
                    ):
                        previous_codelist = (
                            previous_context.has_selected_codelist.single()
                        )
                        if (
                            previous_codelist is None
                            or previous_codelist.uid != STUDY_ARM_ORIGIN_CODELIST_UID
                        ):
                            raise ValidationException(
                                msg="STUDY_ARM_DATA_ORIGIN_CONTEXT_SCOPE_INVALID"
                            )
                        # An unchanged native selection keeps its existing
                        # context through edit/reorder/delete history.
                        origin_context = previous_context
            if origin_context is None:
                origin_term = StudyArmOriginRepository.get_term(
                    selection.data_origin_type_uid
                )
                origin_context = CTCodelistAttributesRepository().get_or_create_selected_term(
                    CTTermRoot.nodes.get(uid=selection.data_origin_type_uid),
                    codelist_uid=origin_term.ct_simple_codelist_term_vo.codelist_uid,
                    catalogue_name=STUDY_ARM_ORIGIN_CATALOGUE,
                )

        # Create new arm selection
        study_arm_selection_node: StudyArm = StudyArm(
            uid=selection.study_selection_uid,
            order=order,
            name=selection.name,
            short_name=selection.short_name,
            label=selection.label,
            arm_code=selection.code,
            description=selection.description,
            data_origin_description=selection.data_origin_description,
            randomization_group=selection.randomization_group,
            number_of_subjects=selection.number_of_subjects,
            merge_branch_for_this_arm_for_sdtm_adam=selection.merge_branch_for_this_arm_for_sdtm_adam,
            accepted_version=selection.accepted_version,
        ).save()

        # Connect new node with study value
        if not for_deletion:
            # Connect new node with study value
            latest_study_value_node.has_study_arm.connect(study_arm_selection_node)

        # check if arm type is set
        if selection.arm_type_uid:
            # find the CTTermRoot (but be sure that it is the actual one!!)
            study_arm_type = CTTermRoot.nodes.get(uid=selection.arm_type_uid)

            selected_arm_type_node = (
                CTCodelistAttributesRepository().get_or_create_selected_term(
                    study_arm_type,
                    codelist_submission_value=settings.study_arm_type_cl_submval,
                    catalogue_name=settings.sdtm_ct_catalogue_name,
                )
            )
            # connect to node
            # pylint: disable=no-member
            study_arm_selection_node.arm_type.connect(selected_arm_type_node)

        if origin_context is not None:
            study_arm_selection_node.data_origin_type.connect(origin_context)

        _manage_versioning_with_relations(
            study_root=study_root,
            action_type=type(audit_node),
            before=before_node,
            after=study_arm_selection_node,
            exclude_relationships=[CTTermContext],
            # The action belongs to the current editor, including deletion or
            # reordering of a selection last written by somebody else.
            author_id=author_id,
            **(action_properties or {}),
        )

    def generate_uid(self) -> str:
        return StudyArm.get_next_free_uid_and_increment_counter()

    def arm_specific_exists_by_uid(self, uid: str) -> bool:
        """
        Returns True if StudyArm with specified uid exists.
        :return:
        """
        query = """
            MATCH (study_arm:StudyArm {uid: $uid})
            RETURN study_arm
            """
        result, _ = db.cypher_query(query, {"uid": uid})
        return len(result) > 0

    def _get_selection_with_history(
        self, study_uid: str, study_selection_uid: str | None = None
    ):
        """
        returns the audit trail for study arm either for a specific selection or for all study arm for the study
        """
        if study_selection_uid:
            cypher = """
                    MATCH (sr:StudyRoot { uid: $study_uid})-[:AUDIT_TRAIL]->(:StudyAction)-[:BEFORE|AFTER]->(sa:StudyArm { uid: $study_selection_uid})
                    WITH sa
                    MATCH (sa)-[:AFTER|BEFORE*0..]-(all_sa:StudyArm)
                    WITH distinct(all_sa)
                    """
        else:
            cypher = """
                    MATCH (sr:StudyRoot { uid: $study_uid})-[:AUDIT_TRAIL]->(:StudyAction)-[:BEFORE|AFTER]->(all_sa:StudyArm)
                    WITH DISTINCT all_sa
                    """
        specific_arm_selections_audit_trail = db.cypher_query(
            cypher + """
            WITH DISTINCT all_sa
            OPTIONAL MATCH (all_sa)-[:HAS_ARM_TYPE]->(st:CTTermContext)-[:HAS_SELECTED_TERM]->(at:CTTermRoot)
            CALL {
                WITH all_sa
                OPTIONAL MATCH (all_sa)-[:HAS_ARM_DATA_ORIGIN_TYPE]->(origin_context:CTTermContext)
                OPTIONAL MATCH (origin_context)-[:HAS_SELECTED_TERM]->(origin:CTTermRoot)
                OPTIONAL MATCH (origin_context)-[:HAS_SELECTED_CODELIST]->(origin_cl:CTCodelistRoot)
                RETURN collect(CASE WHEN origin_context IS NULL THEN null ELSE
                    {term_uid: origin.uid, codelist_uid: origin_cl.uid}
                END) AS origin_memberships
            }
            OPTIONAL MATCH (bars:StudyBranchArm)<-[:STUDY_ARM_HAS_BRANCH_ARM]-(all_sa)
            WITH DISTINCT bars.uid as branch_arm_uid, at, origin_memberships, all_sa
            CALL (branch_arm_uid) {
                MATCH (study_branch_arm:StudyBranchArm {uid:branch_arm_uid})-[:AFTER]-(action:StudyAction)
                WITH study_branch_arm, action
                ORDER BY action.date ASC
                WITH collect(study_branch_arm) as branch_arms
                RETURN last(branch_arms) AS study_branch_arm
            }
            WITH DISTINCT all_sa, at, origin_memberships, sum(study_branch_arm.number_of_subjects) as branch_sum
            ORDER BY all_sa.order ASC
            MATCH (all_sa)<-[:AFTER]-(asa:StudyAction)
            OPTIONAL MATCH (all_sa)<-[:BEFORE]-(bsa:StudyAction)
            WITH all_sa, asa, bsa, at, origin_memberships, branch_sum
            ORDER BY all_sa.uid, asa.date DESC
            RETURN
                all_sa.uid AS study_selection_uid,
                all_sa.name AS arm_name,
                all_sa.short_name AS arm_short_name,
                all_sa.label AS arm_label,
                all_sa.arm_code AS arm_code,
                all_sa.description AS arm_description,
                all_sa.order AS order,
                all_sa.accepted_version AS accepted_version,
                CASE branch_sum
                    WHEN 0 THEN all_sa.number_of_subjects 
                    ELSE branch_sum
                END AS number_of_subjects,
                all_sa.randomization_group AS randomization_group,
                coalesce(all_sa.merge_branch_for_this_arm_for_sdtm_adam, false) AS merge_branch_for_this_arm_for_sdtm_adam,
                at.uid AS arm_type_uid,
                origin_memberships AS data_origin_memberships,
                all_sa.data_origin_description AS data_origin_description,
                all_sa.text AS text,
                asa.date AS start_date,
                asa.author_id AS author_id,
                labels(asa) AS change_type,
                bsa.date AS end_date
            """,
            {"study_uid": study_uid, "study_selection_uid": study_selection_uid},
        )
        result = []
        for res in utils.db_result_to_list(specific_arm_selections_audit_trail):
            change_type = ""
            for action in res["change_type"]:
                if "StudyAction" not in action:
                    change_type = action
            end_date = (
                convert_to_datetime(value=res["end_date"]) if res["end_date"] else None
            )
            result.append(
                SelectionHistoryArm(
                    study_selection_uid=res["study_selection_uid"],
                    study_uid=study_uid,
                    arm_name=res["arm_name"],
                    arm_short_name=res["arm_short_name"],
                    arm_label=res["arm_label"],
                    arm_code=res["arm_code"],
                    arm_description=res["arm_description"],
                    arm_randomization_group=res["randomization_group"],
                    arm_number_of_subjects=res["number_of_subjects"],
                    arm_type=res["arm_type_uid"],
                    data_origin_type_uid=StudyArmOriginRepository.selected_uid(
                        res["data_origin_memberships"], res["data_origin_description"]
                    ),
                    data_origin_description=res["data_origin_description"],
                    merge_branch_for_this_arm_for_sdtm_adam=res[
                        "merge_branch_for_this_arm_for_sdtm_adam"
                    ],
                    start_date=convert_to_datetime(value=res["start_date"]),
                    author_id=res["author_id"],
                    change_type=change_type,
                    end_date=end_date,
                    accepted_version=res["accepted_version"],
                    status=None,
                    order=res["order"],
                )
            )
        return result

    def find_selection_history(
        self, study_uid: str, study_selection_uid: str | None = None
    ) -> list[SelectionHistoryArm]:
        """
        Simple method to return all versions of a study objectives for a study.
        Optionally a specific selection uid is given to see only the response for a specific selection.
        """
        if study_selection_uid:
            return self._get_selection_with_history(
                study_uid=study_uid, study_selection_uid=study_selection_uid
            )
        return self._get_selection_with_history(study_uid=study_uid)

    def close(self) -> None:
        # Our repository guidelines state that repos should have a close method
        # But nothing needs to be done in this one
        pass
