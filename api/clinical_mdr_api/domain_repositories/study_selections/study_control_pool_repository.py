"""Versioned native storage of control pools and adaptation references (O7).

Each record is a StudySelection revision owned by exact StudyValues. An edit
creates a new revision on the current draft only, so locked versions keep the
revision they were locked with. Reads resolve one exact StudyValue.
"""

from datetime import datetime, timezone
from typing import Any, Literal

from neomodel import db

from clinical_mdr_api.domain_repositories._utils.helpers import (
    acquire_write_lock_study_value,
)
from clinical_mdr_api.domain_repositories.generic_repository import (
    _manage_versioning_with_relations,
)
from clinical_mdr_api.domain_repositories.models.study import StudyRoot
from clinical_mdr_api.domain_repositories.models.study_audit_trail import (
    Create,
    Delete,
    Edit,
)
from clinical_mdr_api.domain_repositories.models.study_selections import (
    StudyAdaptationReference,
    StudyControlPool,
)
from clinical_mdr_api.domain_repositories.study_selections.study_selection_scope_repository import (
    StudySelectionScopeRepository,
)
from clinical_mdr_api.domain_repositories.study_selections.study_version_repository import (
    StudyVersionRepository,
)
from clinical_mdr_api.domains.study_selections.study_control_pool import (
    CslStatisticalReferenceVO,
    StudyAdaptationReferenceVO,
    StudyControlPoolVO,
)
from clinical_mdr_api.domains.study_selections.study_selection_scope import (
    StudySelectionScopeVO,
)
from clinical_mdr_api.services._utils import ensure_transaction
from clinical_mdr_api.services.user_info import UserInfoService
from common.exceptions import (
    AlreadyExistsException,
    BusinessLogicException,
    NotFoundException,
    ValidationException,
)

RecordKind = Literal["control_pool", "adaptation_reference"]
NODES = {
    "control_pool": StudyControlPool,
    "adaptation_reference": StudyAdaptationReference,
}
RELATIONS = {
    "control_pool": ("has_study_control_pool", "HAS_STUDY_CONTROL_POOL"),
    "adaptation_reference": (
        "has_study_adaptation_reference",
        "HAS_STUDY_ADAPTATION_REFERENCE",
    ),
}


def _properties(kind: RecordKind, record: Any) -> dict[str, Any]:
    reference = record.csl_reference.to_json() if record.csl_reference else None
    if kind == "control_pool":
        return {
            "uid": record.uid,
            "study_uid": record.study_uid,
            "name": record.name,
            "description": record.description,
            "donor_arm_uids": list(record.donor_arm_uids),
            "focal_arm_uids": list(record.focal_arm_uids),
            "csl_reference_json": reference,
        }
    return {
        "uid": record.uid,
        "study_uid": record.study_uid,
        "adaptation_type": record.adaptation_type,
        "description": record.description,
        "trigger_reference": record.trigger_reference,
        "affected_arm_uids": list(record.affected_arm_uids),
        "affected_epoch_uids": list(record.affected_epoch_uids),
        "csl_reference_json": reference,
    }


def _from_row(kind: RecordKind, node: dict[str, Any], action: dict[str, Any]) -> Any:
    reference = CslStatisticalReferenceVO.from_json(node.get("csl_reference_json"))
    common = {
        "uid": node["uid"],
        "study_uid": node["study_uid"],
        "description": node.get("description"),
        "author_id": action["author_id"],
        "start_date": action["date"],
        "author_username": UserInfoService.get_author_username_from_id(
            action["author_id"]
        ),
    }
    if kind == "control_pool":
        return StudyControlPoolVO(
            name=node["name"],
            donor_arm_uids=tuple(node.get("donor_arm_uids") or ()),
            focal_arm_uids=tuple(node.get("focal_arm_uids") or ()),
            csl_reference=reference,
            **common,
        )
    if reference is None:
        raise ValidationException(msg="A stored adaptation reference has no CSL pin")
    return StudyAdaptationReferenceVO(
        adaptation_type=node["adaptation_type"],
        csl_reference=reference,
        affected_arm_uids=tuple(node.get("affected_arm_uids") or ()),
        affected_epoch_uids=tuple(node.get("affected_epoch_uids") or ()),
        trigger_reference=node.get("trigger_reference"),
        **common,
    )


class StudyControlPoolRepository:
    def __init__(self, author_id: str):
        self.author_id = author_id

    @staticmethod
    def _targets(kind: RecordKind, record: Any) -> StudySelectionScopeVO:
        if kind == "control_pool":
            return StudySelectionScopeVO.from_values(
                applies_to_arm_uids=record.arm_uids()
            )
        return StudySelectionScopeVO.from_values(
            applies_to_arm_uids=record.affected_arm_uids,
            applies_to_epoch_uids=record.affected_epoch_uids,
        )

    def _draft(self, study_uid: str, expected_study_version: str):
        acquire_write_lock_study_value(study_uid)
        root = StudyRoot.nodes.get_or_none(uid=study_uid)
        if root is None:
            raise NotFoundException("Study", study_uid)
        states, _ = db.cypher_query(
            "MATCH (root:StudyRoot {uid:$study_uid})-[:LATEST]->(value:StudyValue) "
            "MATCH (root)-[state:HAS_VERSION]->(value) WHERE state.end_date IS NULL "
            "RETURN state.status, state.version, value",
            {"study_uid": study_uid},
            resolve_objects=True,
        )
        BusinessLogicException.raise_if(
            len(states) != 1 or states[0][0] != "DRAFT",
            msg=f"Study with UID '{study_uid}' is locked.",
        )
        AlreadyExistsException.raise_if(
            str(states[0][1]) != expected_study_version,
            msg="Study version changed; reload before changing this record.",
        )
        return root, states[0][2]

    def _current(self, kind: RecordKind, value, uid: str):
        relation = getattr(value, RELATIONS[kind][0])
        nodes = [node for node in relation.all() if node.uid == uid]
        if not nodes:
            raise NotFoundException(
                msg=f"No {kind.replace('_', ' ')} '{uid}' in the current draft."
            )
        ValidationException.raise_if(
            len(nodes) != 1, msg=f"The {kind.replace('_', ' ')} '{uid}' is ambiguous."
        )
        return nodes[0]

    @ensure_transaction(db)
    def save(
        self,
        kind: RecordKind,
        record: Any,
        expected_study_version: str,
        replaces: bool,
    ) -> Any:
        root, value = self._draft(record.study_uid, expected_study_version)
        StudySelectionScopeRepository.validate_targets(
            record.study_uid, self._targets(kind, record)
        )
        before = self._current(kind, value, record.uid) if replaces else None
        after = NODES[kind](**_properties(kind, record)).save()
        relation = getattr(value, RELATIONS[kind][0])
        if before is not None:
            relation.disconnect(before)
        relation.connect(after)
        _manage_versioning_with_relations(
            study_root=root,
            action_type=Edit if before is not None else Create,
            before=before,
            after=after,
            author_id=self.author_id,
        )
        return self.find(kind, record.study_uid, uid=record.uid)[0]

    @ensure_transaction(db)
    def delete(
        self, kind: RecordKind, study_uid: str, uid: str, expected_study_version: str
    ) -> None:
        root, value = self._draft(study_uid, expected_study_version)
        before = self._current(kind, value, uid)
        getattr(value, RELATIONS[kind][0]).disconnect(before)
        _manage_versioning_with_relations(
            study_root=root, action_type=Delete, before=before, author_id=self.author_id
        )

    @ensure_transaction(db)
    def find(
        self,
        kind: RecordKind,
        study_uid: str,
        study_value_version: str | None = None,
        uid: str | None = None,
    ) -> list[Any]:
        _, value, _ = StudyVersionRepository(
            author_id=self.author_id
        ).get_selected_version(study_uid, study_value_version)
        rows, _ = db.cypher_query(
            f"""
            MATCH (value:StudyValue)-[:{RELATIONS[kind][1]}]->(node:{NODES[kind].__name__})
            WHERE elementId(value)=$value_id AND ($uid IS NULL OR node.uid=$uid)
            RETURN properties(node),
                [(node)<-[:AFTER]-(action:StudyAction) | {{author_id: action.author_id, date: action.date}}]
            ORDER BY node.uid
            """,
            {"value_id": value.element_id, "uid": uid},
        )
        uids = [row[0]["uid"] for row in rows]
        ValidationException.raise_if(
            len(set(uids)) != len(uids),
            msg=f"A study version holds one {kind.replace('_', ' ')} more than once.",
        )
        records = []
        for node, actions in rows:
            ValidationException.raise_if(
                len(actions) != 1,
                msg=f"The {kind.replace('_', ' ')} '{node['uid']}' has no single audit action.",
            )
            action = dict(actions[0])
            if not isinstance(action["date"], datetime):
                action["date"] = action["date"].to_native()
            if action["date"].tzinfo is None:
                action["date"] = action["date"].replace(tzinfo=timezone.utc)
            records.append(_from_row(kind, node, action))
        if uid is not None and not records:
            raise NotFoundException(
                msg=f"No {kind.replace('_', ' ')} '{uid}' in this study version."
            )
        return records

    @staticmethod
    def next_uid(kind: RecordKind) -> str:
        return NODES[kind].get_next_free_uid_and_increment_counter()
