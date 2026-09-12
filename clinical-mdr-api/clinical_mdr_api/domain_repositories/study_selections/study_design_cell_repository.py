import datetime
from dataclasses import dataclass
from textwrap import dedent

from neomodel import DoesNotExist, MultipleNodesReturned, db
from neomodel.sync_.match import Collect, Last, Path

from clinical_mdr_api import utils
from clinical_mdr_api.domain_repositories.generic_repository import (
    _manage_versioning_with_relations,
)
from clinical_mdr_api.domain_repositories.models.generic import ClinicalMdrNodeWithUID
from clinical_mdr_api.domain_repositories.models.study import (
    StudyArm,
    StudyBranchArm,
    StudyElement,
    StudyRoot,
    StudyValue,
)
from clinical_mdr_api.domain_repositories.models.study_audit_trail import (
    Create,
    Delete,
    Edit,
)
from clinical_mdr_api.domain_repositories.models.study_epoch import StudyEpoch
from clinical_mdr_api.domain_repositories.models.study_selections import StudyDesignCell
from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyStatus,
)
from clinical_mdr_api.domains.study_selections.study_design_cell import (
    StudyDesignCellVO,
)
from common import exceptions
from common.telemetry import trace_calls
from common.utils import convert_to_datetime

STUDY_VALUE_VERSION_QUALIFIER = "study_value__has_version|version"
STUDY_VALUE_UID_QUALIFIER = "study_value__has_version__uid"
STUDY_VALUE_LATEST_UID_QUALIFIER = "study_value__latest_value__uid"


@dataclass
class StudyDesignCellHistory:
    """Class for selection history items"""

    study_selection_uid: str
    study_uid: str
    study_arm_uid: str
    study_arm_name: str
    study_branch_arm_uid: str
    study_branch_arm_name: str
    study_epoch_uid: str
    study_epoch_name: str
    study_element_uid: str
    study_element_name: str
    author_id: str
    change_type: str
    start_date: datetime.datetime
    end_date: datetime.datetime | None
    transition_rule: str
    order: int


class StudyDesignCellRepository:

    @classmethod
    @trace_calls(args=[1, 2], kwargs=["study_uid", "uid"])
    def find_by_uid(cls, study_uid: str, uid: str) -> StudyDesignCellVO:
        found = cls.find_all_design_cells_by_study(
            study_uid=study_uid, study_design_cell_uid=uid
        )

        exceptions.ValidationException.raise_if(
            len(found) > 1,
            msg=f"Found more than one StudyDesignCell node with UID '{uid}' in the Study with UID '{study_uid}'.",
        )
        exceptions.ValidationException.raise_if(
            len(found) == 0,
            msg=f"The StudyDesignCell with UID '{uid}' could not be found in the Study with UID '{study_uid}'.",
        )

        return found[0]

    @staticmethod
    @trace_calls(
        args=[0, 1],
        kwargs=[
            "study_uid",
            "study_value_version",
            "study_design_cell_uid",
            "study_arm_uid",
            "study_branch_arm_uid",
            "study_element_uid",
            "study_epoch_uid",
        ],
    )
    def find_all_design_cells_by_study(
        study_uid: str,
        study_value_version: str | None = None,
        *,  # prevent call errors when replacing get_design_cells_* functions
        study_design_cell_uid: str | None = None,
        study_arm_uid: str | None = None,
        study_branch_arm_uid: str | None = None,
        study_element_uid: str | None = None,
        study_epoch_uid: str | None = None,
    ) -> list[StudyDesignCellVO]:
        """Returns all StudyDesignCellVO as list for a given Study & version, with optional filters, sorted by order."""

        # query parameters
        params = {
            "study_uid": study_uid,
            "study_version": study_value_version,
            "study_design_cell_uid": study_design_cell_uid,
            "study_arm_uid": study_arm_uid,
            "study_branch_arm_uid": study_branch_arm_uid,
            "study_element_uid": study_element_uid,
            "study_epoch_uid": study_epoch_uid,
            "study_status": StudyStatus.RELEASED.value,
        }

        # filter expressions
        filter_map = {
            "study_design_cell_uid": "sdc.uid",
            "study_arm_uid": "sarm.uid",
            "study_branch_arm_uid": "sbarm.uid",
            "study_element_uid": "sel.uid",
            "study_epoch_uid": "sep.uid",
        }
        filters = [
            f"{value} = ${key}"
            for key, value in filter_map.items()
            if params[key] is not None
        ]

        # build query
        if study_value_version:
            query = [
                "MATCH (sr:StudyRoot {uid: $study_uid})-[:HAS_VERSION {status: $study_status, version: $study_version}]->(sv:StudyValue)"
            ]
        else:
            query = [
                "MATCH (sr:StudyRoot {uid: $study_uid})-[:LATEST]->(sv:StudyValue)"
            ]

        query.append(dedent("""
            MATCH (sv)-[:HAS_STUDY_DESIGN_CELL]->(sdc:StudyDesignCell)<-[:AFTER]-(study_action:StudyAction)
            MATCH (sdc)<-[:STUDY_EPOCH_HAS_DESIGN_CELL]-(sep:StudyEpoch)<-[:HAS_STUDY_EPOCH]-(sv)
            MATCH (sep)-[:HAS_EPOCH]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(epoch_term_root:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST_FINAL]->(epoch_name:CTTermNameValue)
            MATCH (sdc)<-[:STUDY_ELEMENT_HAS_DESIGN_CELL]-(sel:StudyElement)<-[:HAS_STUDY_ELEMENT]-(sv)
            OPTIONAL MATCH (sdc)<-[:STUDY_ARM_HAS_DESIGN_CELL]-(sarm:StudyArm)<-[:HAS_STUDY_ARM]-(sv)
            OPTIONAL MATCH (sdc)<-[:STUDY_BRANCH_ARM_HAS_DESIGN_CELL]-(sbarm:StudyBranchArm)<-[:HAS_STUDY_BRANCH_ARM]-(sv)
            OPTIONAL MATCH (user:User {user_id: study_action.author_id}) 
        """).strip())

        if filters:
            query.append("WITH *")
            query.append("WHERE " + " AND ".join(filters))

        query.append(dedent("""
            RETURN DISTINCT sdc {
                uid: sdc.uid,
                study_uid: sr.uid,
                order: sdc.order,
                study_arm_uid: sarm.uid,
                study_arm_name: sarm.name,
                study_branch_arm_uid: sbarm.uid,
                study_branch_arm_name: sbarm.name,
                study_epoch_uid: sep.uid,
                study_epoch_name: epoch_name.name,
                study_element_uid: sel.uid,
                study_element_name: sel.name,
                transition_rule: sdc.transition_rule,
                start_date: study_action.date,
                author_id: study_action.author_id,
                author_username: user.username
            } AS vo
            ORDER BY vo.order
        """).strip())

        results, _ = db.cypher_query("\n".join(query), params=params)

        return [StudyDesignCellVO(**result[0]) for result in results]

    @classmethod
    @trace_calls
    def _from_repository_values(
        cls,
        study_uid: str,
        design_cell: StudyDesignCell,
        study_value_version: str | None = None,
    ) -> StudyDesignCellVO:
        study_action = design_cell.has_after.all()[0]
        if study_value_version:
            filters = {
                "has_version|version": study_value_version,
                "has_version__uid": study_uid,
            }
        else:
            filters = {
                "latest_value__uid": study_uid,
            }
        study_value: StudyValue = (
            StudyValue.nodes.filter(**filters)
            .intermediate_transform({"studyvalue": {"source": "studyvalue"}})
            .annotate(study_value=Last(Collect("studyvalue", distinct=True)))
            .get_or_none()
        )
        assert isinstance(study_value, StudyValue)

        assert len(set(ith.uid for ith in design_cell.study_arm.all())) <= 1
        assert len(set(ith.uid for ith in design_cell.study_branch_arm.all())) <= 1
        assert len(set(ith.uid for ith in design_cell.study_element.all())) <= 1
        assert len(set(ith.uid for ith in design_cell.study_epoch.all())) <= 1

        study_epoch: StudyEpoch = cls.get_current_outbound_node(
            node=design_cell, outbound_rel_name="study_epoch", study_value=study_value
        )
        study_arm: StudyArm = cls.get_current_outbound_node(
            node=design_cell, outbound_rel_name="study_arm", study_value=study_value
        )
        study_branch_arm: StudyBranchArm = cls.get_current_outbound_node(
            node=design_cell,
            outbound_rel_name="study_branch_arm",
            study_value=study_value,
        )
        study_element: StudyElement = cls.get_current_outbound_node(
            node=design_cell, outbound_rel_name="study_element", study_value=study_value
        )

        study_epoch_name = (
            study_epoch.has_epoch.single()
            .has_selected_term.single()
            .has_name_root.single()
            .has_latest_value.single()
            .name
        )
        return StudyDesignCellVO(
            uid=design_cell.uid,
            study_uid=study_uid,
            order=design_cell.order,
            study_arm_uid=study_arm.uid if study_arm is not None else None,
            study_arm_name=study_arm.name if study_arm is not None else None,
            study_branch_arm_uid=(
                study_branch_arm.uid if study_branch_arm is not None else None
            ),
            study_branch_arm_name=(
                study_branch_arm.name if study_branch_arm is not None else None
            ),
            study_epoch_uid=study_epoch.uid,
            study_epoch_name=study_epoch_name,
            study_element_uid=study_element.uid,
            study_element_name=study_element.name,
            transition_rule=design_cell.transition_rule,
            start_date=study_action.date,
            author_id=study_action.author_id,
        )

    @staticmethod
    def get_current_outbound_node(
        node: ClinicalMdrNodeWithUID,
        outbound_rel_name: str,
        study_value: StudyValue,
    ):
        outbound_node = None
        outbound_nodes = getattr(node, outbound_rel_name).all()
        for i_outbound_node in outbound_nodes:
            if i_outbound_node.study_value.is_connected(study_value):
                outbound_node = i_outbound_node
        return outbound_node

    # pylint: disable=unused-argument
    @classmethod
    @trace_calls
    def save(
        cls,
        design_cell_vo: StudyDesignCellVO,
        author_id: str,
        create: bool = False,
        allow_none_arm_branch_arm=False,
        previous_vo: StudyDesignCellVO | None = None,
    ) -> StudyDesignCellVO:
        """Creates or updates a StudyDesignCell"""

        # Fetch nodes referenced by uids
        query = [
            "MATCH (study_root:StudyRoot {uid: $study_uid})-[:LATEST]->(latest_value:StudyValue)",
            "OPTIONAL MATCH (latest_value)-[:HAS_STUDY_EPOCH]->(study_epoch:StudyEpoch {uid: $study_epoch_uid})",
        ]
        params = {
            "study_uid": design_cell_vo.study_uid,
            "study_epoch_uid": design_cell_vo.study_epoch_uid,
        }
        returns = ["study_root", "latest_value", "study_epoch"]

        if design_cell_vo.study_arm_uid:
            query.append(
                "OPTIONAL MATCH (latest_value)-[:HAS_STUDY_ARM]->(study_arm:StudyArm {uid: $study_arm_uid})"
            )
            params["study_arm_uid"] = design_cell_vo.study_arm_uid
            returns.append("study_arm")
            # query if StudyArm has StudyBranchArm assigned to it
            returns.append(
                "exists((study_arm)-[:STUDY_ARM_HAS_BRANCH_ARM]->(:StudyBranchArm)<-[:HAS_STUDY_BRANCH_ARM]-(latest_value)) AS study_arm_has_branch_arm"
            )

        if design_cell_vo.study_branch_arm_uid:
            query.append(
                "OPTIONAL MATCH (latest_value)-[:HAS_STUDY_BRANCH_ARM]->(study_branch_arm:StudyBranchArm {uid: $study_branch_arm_uid})"
            )
            params["study_branch_arm_uid"] = design_cell_vo.study_branch_arm_uid
            returns.append("study_branch_arm")

        if design_cell_vo.study_element_uid is not None:
            query.append(
                "OPTIONAL MATCH (latest_value)-[:HAS_STUDY_ELEMENT]->(study_element:StudyElement {uid: $study_element_uid})"
            )
            params["study_element_uid"] = design_cell_vo.study_element_uid
            returns.append("study_element")

        if not create and design_cell_vo.uid:
            query.append(
                "OPTIONAL MATCH (latest_value)-[:HAS_STUDY_DESIGN_CELL]->(study_design_cell:StudyDesignCell {uid: $study_design_cell_uid})"
            )
            params["study_design_cell_uid"] = design_cell_vo.uid
            returns.append("study_design_cell")

        query.append(f"RETURN {', '.join(returns)}")
        query_str = "\n".join(query)
        results, keys = db.cypher_query(query_str, params, resolve_objects=True)

        if len(results) < 1:
            raise exceptions.NotFoundException("Study", design_cell_vo.study_uid)
        if len(results) > 1:
            raise exceptions.AlreadyExistsException(
                msg=f"Multiple StudyRoot nodes found with uid '{design_cell_vo.study_uid}'."
            )

        nodes = dict(zip(keys, results[0]))
        study_root_node: StudyRoot = nodes["study_root"]
        study_value_node: StudyValue = nodes["latest_value"]
        study_epoch_node: StudyEpoch | None = nodes.get("study_epoch")
        study_arm_node: StudyArm | None = nodes.get("study_arm")
        study_branch_arm_node: StudyBranchArm | None = nodes.get("study_branch_arm")
        study_element_node: StudyElement | None = nodes.get("study_element")
        study_arm_has_branch_arm: bool | None = nodes.get("study_arm_has_branch_arm")
        previous_node: StudyDesignCell | None = nodes.get("study_design_cell")

        # Check if something has changed
        if not create:
            exceptions.NotFoundException.raise_if_not(
                previous_node, "StudyDesignCell", design_cell_vo.uid
            )

            # get the previous VO
            if previous_vo is None:
                previous_vo = cls.find_by_uid(
                    study_uid=design_cell_vo.study_uid, uid=design_cell_vo.uid
                )

            exceptions.BusinessLogicException.raise_if(
                not previous_vo.study_arm_uid
                and not previous_vo.study_branch_arm_uid
                and not allow_none_arm_branch_arm,
                msg="Broken Existing Design Cell without Arm and BranchArm",
            )

            compare_props = (
                "study_arm_uid",
                "study_branch_arm_uid",
                "study_epoch_uid",
                "study_element_uid",
                "transition_rule",
                "order",
            )

            previous_props = {
                prop: getattr(previous_vo, prop) for prop in compare_props
            }
            if previous_vo.study_branch_arm_uid:
                previous_props["study_arm_uid"] = None

            props = {prop: getattr(design_cell_vo, prop) for prop in compare_props}

            # Return previous VO if nothing has changed
            if props == previous_props:
                return previous_vo

        # Selected StudyArm can not have any StudyBranchArm (connected to latest StudyValue)
        exceptions.BusinessLogicException.raise_if(
            study_arm_has_branch_arm,
            msg=f"The Study Arm with UID '{design_cell_vo.study_arm_uid}' cannot be "
            "assigned to a Study Design Cell because it has Study Branch Arms assigned to it",
        )

        # Require StudyArm or StudyBranchArm
        exceptions.NotFoundException.raise_if_not(
            study_arm_node or study_branch_arm_node,
            msg=f"Study Arm with UID '{design_cell_vo.study_arm_uid}' or Study Branch Arm with UID '{design_cell_vo.study_branch_arm_uid}' must exist.",
        )

        # Validate StudyEpoch
        exceptions.NotFoundException.raise_if_not(
            study_epoch_node, "Study Epoch", design_cell_vo.study_epoch_uid
        )

        # Validate StudyElement
        if design_cell_vo.study_element_uid is not None:
            exceptions.NotFoundException.raise_if(
                study_element_node is None,
                "Study Element",
                design_cell_vo.study_element_uid,
            )

        # Create new node
        design_cell = StudyDesignCell(
            uid=design_cell_vo.uid,
            transition_rule=design_cell_vo.transition_rule,
            order=design_cell_vo.order,
        )
        design_cell.save()

        # Connect relations
        design_cell.study_value.connect(study_value_node)
        if study_branch_arm_node:
            # study_branch_arm was defined even if the arm was specified will be connected to study branch arm
            design_cell.study_branch_arm.connect(study_branch_arm_node)
        elif study_arm_node:
            # just arm was defined
            design_cell.study_arm.connect(study_arm_node)
        design_cell.study_epoch.connect(study_epoch_node)
        design_cell.study_element.connect(study_element_node)

        if create:
            _manage_versioning_with_relations(
                study_root=study_root_node,
                action_type=Create,
                before=None,
                after=design_cell,
                author_id=author_id,
            )

        else:
            exclude_study_selection_relationships = [
                StudyArm,
                StudyBranchArm,
                StudyEpoch,
                StudyElement,
            ]

            _manage_versioning_with_relations(
                study_root=study_root_node,
                action_type=Edit,
                before=previous_node,
                after=design_cell,
                exclude_relationships=exclude_study_selection_relationships,
                author_id=author_id,
            )

        # return created/updated StudyDesignCellVO
        return cls.find_by_uid(study_uid=design_cell_vo.study_uid, uid=design_cell.uid)

    @classmethod
    @trace_calls
    def patch_study_arm(
        cls,
        study_uid: str,
        design_cell_uid: str,
        study_arm_uid: str,
        author_id: str,
        allow_none_arm_branch_arm=False,
    ):
        study_design_cell = cls.find_by_uid(study_uid=study_uid, uid=design_cell_uid)
        study_design_cell.study_arm_uid = study_arm_uid
        study_design_cell.study_branch_arm_uid = None

        cls.save(
            study_design_cell,
            author_id,
            create=False,
            allow_none_arm_branch_arm=allow_none_arm_branch_arm,
        )

    @staticmethod
    @trace_calls
    def delete(study_uid: str, design_cell_uid: str, author_id: str):
        try:
            (
                study_root_node,
                _,
                design_cell,
            ) = StudyRoot.nodes.traverse(
                Path("latest_value", include_rels_in_return=False),
                Path(
                    "latest_value__has_study_design_cell",
                    optional=True,
                    include_rels_in_return=False,
                ),
            ).get(
                uid=study_uid, latest_value__has_study_design_cell__uid=design_cell_uid
            )
        except DoesNotExist as exc:
            raise exceptions.NotFoundException("Study", study_uid) from exc
        except MultipleNodesReturned as exc:
            raise exceptions.AlreadyExistsException(
                msg=f"Multiple StudyRoot nodes found with uid '{study_uid}'."
            ) from exc

        exceptions.NotFoundException.raise_if_not(
            design_cell, "Study Design Cell", design_cell_uid
        )

        # create delete version
        new_design_cell = StudyDesignCell(
            uid=design_cell.uid,
            transition_rule=design_cell.transition_rule,
            order=design_cell.order,
        )
        new_design_cell.save()

        # Connect relations
        study_arm_node = design_cell.study_arm.single()
        study_branch_arm_node = design_cell.study_branch_arm.single()
        # at least one of the two has to be defined
        if study_arm_node:
            new_design_cell.study_arm.connect(study_arm_node)
        elif study_branch_arm_node:
            new_design_cell.study_branch_arm.connect(study_branch_arm_node)
        else:
            raise exceptions.NotFoundException(
                msg="Study arm or Study Branch Arm must exist"
            )

        # get StudyEpoch
        study_epoch_node = design_cell.study_epoch.single()

        exceptions.NotFoundException.raise_if_not(
            study_epoch_node, msg="Study epoch must exists"
        )

        new_design_cell.study_epoch.connect(study_epoch_node)

        # gest StudyElement
        study_element_node = design_cell.study_element.single()

        exceptions.NotFoundException.raise_if_not(
            study_element_node, msg="Study element must exists"
        )

        new_design_cell.study_element.connect(study_element_node)

        # Audit trail
        _manage_versioning_with_relations(
            study_root=study_root_node,
            action_type=Delete,
            before=design_cell,
            after=new_design_cell,
            exclude_relationships=(
                StudyArm,
                StudyBranchArm,
                StudyEpoch,
                StudyElement,
            ),
            author_id=author_id,
        )

    @staticmethod
    def generate_uid() -> str:
        return StudyDesignCell.get_next_free_uid_and_increment_counter()

    @staticmethod
    @trace_calls
    def _get_selection_with_history(study_uid: str, design_cell_uid: str | None = None):
        """
        returns the audit trail for study design cell either for a
        specific selection or for all study design cells for the study.
        """
        # if some DesignCell is specified
        if design_cell_uid:
            # then query just that specific design cell
            cypher = """
            MATCH (sr:StudyRoot { uid: $study_uid})-[:AUDIT_TRAIL]->(:StudyAction)-[:BEFORE|AFTER]->(sdc:StudyDesignCell {uid: $design_cell_uid})
            WITH sdc
            MATCH (sdc)-[:AFTER|BEFORE*0..]-(all_sdc:StudyDesignCell)
            WITH distinct(all_sdc)
            """
        # if get all study design cells history is called
        else:
            # then query all the design cells
            cypher = """
            MATCH (sr:StudyRoot { uid: $study_uid})-[:AUDIT_TRAIL]->(:StudyAction)-[:BEFORE|AFTER]->(all_sdc:StudyDesignCell)
            WITH DISTINCT all_sdc
            """
        specific_design_cells_audit_trail = db.cypher_query(
            cypher + """
            OPTIONAL MATCH (all_sdc)<-[:STUDY_BRANCH_ARM_HAS_DESIGN_CELL]-(sba:StudyBranchArm)
            OPTIONAL MATCH (all_sdc)<-[:STUDY_ARM_HAS_DESIGN_CELL]-(sa:StudyArm)
            MATCH (all_sdc)<-[:STUDY_EPOCH_HAS_DESIGN_CELL]-(se:StudyEpoch)
            MATCH (se)-[:HAS_EPOCH]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)-[:LATEST_FINAL]->(epoch_name:CTTermNameValue)
            OPTIONAL MATCH (all_sdc)<-[:STUDY_ELEMENT_HAS_DESIGN_CELL]-(sel:StudyElement)
            MATCH (all_sdc)<-[:AFTER]-(asa:StudyAction)
            OPTIONAL MATCH (all_sdc)<-[:BEFORE]-(bsa:StudyAction)
            WITH all_sdc, sa, se, sel, asa, bsa, sba, epoch_name
              ORDER BY asa.date DESC
            RETURN DISTINCT
                all_sdc.uid AS uid,
                all_sdc.transition_rule AS transition_rule,
                all_sdc.order AS order,
                sa.uid AS study_arm_uid,
                sa.name AS study_arm_name,
                sba.uid AS study_branch_arm_uid,
                sba.name AS study_branch_arm_name,
                se.uid AS study_epoch_uid,
                epoch_name.name AS study_epoch_name,
                sel.uid AS study_element_uid,
                sel.name AS study_element_name,
                labels(asa) AS change_type,
                asa.date AS start_date,
                bsa.date AS end_date,
                asa.author_id AS author_id
            """,
            {"study_uid": study_uid, "design_cell_uid": design_cell_uid},
        )
        result = []
        for res in utils.db_result_to_list(specific_design_cells_audit_trail):
            change_type = ""
            for action in res["change_type"]:
                if "StudyAction" not in action:
                    change_type = action
            end_date = (
                convert_to_datetime(value=res["end_date"]) if res["end_date"] else None
            )
            result.append(
                StudyDesignCellHistory(
                    study_uid=study_uid,
                    study_selection_uid=res["uid"],
                    study_arm_uid=res["study_arm_uid"],
                    study_arm_name=res["study_arm_name"],
                    study_branch_arm_uid=res["study_branch_arm_uid"],
                    study_branch_arm_name=res["study_branch_arm_name"],
                    study_epoch_uid=res["study_epoch_uid"],
                    study_epoch_name=res["study_epoch_name"],
                    study_element_uid=res["study_element_uid"],
                    study_element_name=res["study_element_name"],
                    author_id=res["author_id"],
                    change_type=change_type,
                    start_date=convert_to_datetime(value=res["start_date"]),
                    transition_rule=res["transition_rule"],
                    order=res["order"],
                    end_date=end_date,
                )
            )
        return result

    @classmethod
    @trace_calls
    def find_selection_history(
        cls, study_uid: str, design_cell_uid: str | None = None
    ) -> list[StudyDesignCellHistory]:
        if design_cell_uid:
            return cls._get_selection_with_history(
                study_uid=study_uid, design_cell_uid=design_cell_uid
            )
        return cls._get_selection_with_history(study_uid=study_uid)

    @staticmethod
    def close() -> None:
        pass
