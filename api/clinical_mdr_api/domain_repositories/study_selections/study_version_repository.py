import json
from typing import Any

from neomodel import db

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
    CTTermRoot,
)
from clinical_mdr_api.domain_repositories.models.study import StudyRoot, StudyValue
from clinical_mdr_api.domain_repositories.models.study_audit_trail import Create, Edit
from clinical_mdr_api.domain_repositories.models.study_selections import StudyVersion
from clinical_mdr_api.domains.study_selections.study_amendment import StudyAmendmentVO
from clinical_mdr_api.repositories._utils import calculate_total_count_from_query_result
from clinical_mdr_api.services._utils import ensure_transaction
from common.auth.user import user
from common.config import settings
from common.exceptions import NotFoundException, ValidationException
from common.utils import (
    db_pagination_clause,
    get_db_result_as_dict,
    validate_page_number_and_page_size,
)


class StudyVersionRepository:
    """
    Repository for managing StudyVersion nodes.
    """

    def __init__(self, author_id: str | None = None):
        self.author_id = author_id if author_id is not None else user().id()

    @staticmethod
    def _study_value_query(
        study_uid: str, version: str | None = None
    ) -> tuple[str, dict[str, str | None]]:
        params = {"study_uid": study_uid, "version": version}
        selector = (
            "[:LATEST]" if version is None else "[:HAS_VERSION {version:$version}]"
        )
        return (
            "MATCH (study_root:StudyRoot {uid:$study_uid})-"
            + selector
            + "->(study_value:StudyValue) WITH DISTINCT study_root, study_value",
            params,
        )

    @ensure_transaction(db)
    def get_selected_version(
        self, study_uid: str, version: str | None = None
    ) -> tuple[StudyRoot, StudyValue, StudyVersion | None]:
        """Resolve ownership by exact membership, including historical records."""
        query, params = self._study_value_query(study_uid, version)
        return self._resolve_selected_version(study_uid, query, params)

    def _resolve_selected_version(
        self, study_uid: str, query: str, params: dict[str, str | None]
    ) -> tuple[StudyRoot, StudyValue, StudyVersion | None]:
        rows, _ = db.cypher_query(
            query + """
            OPTIONAL MATCH (study_value)-[:HAS_STUDY_VERSION]->(record:StudyVersion)
            RETURN study_root, study_value, record,
                [(owner:StudyRoot)-[:LATEST|HAS_VERSION]->(study_value) | owner.uid],
                [(owner_value:StudyValue)-[:HAS_STUDY_VERSION]->(record) | elementId(owner_value)]
            """,
            params,
            resolve_objects=True,
        )
        if not rows:
            raise NotFoundException("Study version", study_uid)
        if len(rows) != 1:
            raise ValidationException(
                msg="The selected study version has ambiguous version records"
            )
        root, value, record, owners, record_values = rows[0]
        if set(owners) != {study_uid}:
            raise ValidationException(
                msg="The selected study value has foreign ownership"
            )
        if record is not None and record_values != [value.element_id]:
            raise ValidationException(
                msg="The selected version record belongs to another study value"
            )
        if record is not None:
            self.amendment_from_record(record)
        return root, value, record

    @ensure_transaction(db)
    def get_subpart_pins(
        self, study_uid: str, version: str | None = None
    ) -> list[dict[str, Any]]:
        """The exact subpart values one selected parent value composes.

        Draft edits keep superseded child values linked, so a parent value can
        link several values of one subpart. A draft parent composes each
        subpart's current draft. A locked or released parent composes, per
        subpart, the value with the latest immutable state at or before the
        parent's own lock. Child LATEST never stands in for a historical pin.
        """
        _, value, _ = self.get_selected_version(study_uid, version)
        parent_start = None
        if version is not None:
            starts, _ = db.cypher_query(
                """
                MATCH (:StudyRoot {uid:$study_uid})-[state:HAS_VERSION]->(value:StudyValue)
                WHERE elementId(value)=$value_id AND state.version=$version
                  AND state.status IN ['LOCKED', 'RELEASED']
                RETURN min(state.start_date)
                """,
                {
                    "study_uid": study_uid,
                    "value_id": value.element_id,
                    "version": version,
                },
            )
            parent_start = starts[0][0] if starts else None
            ValidationException.raise_if(
                parent_start is None,
                msg="A platform version must be a locked or released parent version",
            )
        rows, _ = db.cypher_query(
            """
            MATCH (parent:StudyValue)-[:HAS_STUDY_SUBPART]->(child:StudyValue)
            WHERE elementId(parent)=$value_id
            WITH DISTINCT child
            RETURN elementId(child), child.study_subpart_acronym,
                [(root:StudyRoot)-[:LATEST|HAS_VERSION]->(child) | root.uid],
                [(root:StudyRoot)-[state:HAS_VERSION]->(child)
                    WHERE state.status IN ['LOCKED', 'RELEASED']
                    | [state.version, state.start_date]],
                exists((:StudyRoot)-[:LATEST]->(child))
            """,
            {"value_id": value.element_id},
        )
        candidates: dict[str, list[dict[str, Any]]] = {}
        for _, acronym, owners, states, is_latest in rows:
            owner_set = set(owners)
            ValidationException.raise_if(
                len(owner_set) != 1,
                msg="A subpart value has ambiguous or foreign ownership",
            )
            usable = [
                (start, str(label))
                for label, start in states
                if start is not None
                and label is not None
                and (parent_start is None or start <= parent_start)
            ]
            candidates.setdefault(next(iter(owner_set)), []).append(
                {
                    "acronym": acronym,
                    "is_latest": is_latest,
                    "latest_state": max(usable) if usable else None,
                }
            )
        pins = []
        for child_uid, values in sorted(candidates.items()):
            if version is None:
                current = [item for item in values if item["is_latest"]]
                ValidationException.raise_if(
                    len(current) != 1,
                    msg=f"A draft platform must link exactly one current draft of subpart {child_uid}",
                )
                pins.append(
                    {
                        "study_uid": child_uid,
                        "study_value_version": None,
                        "subpart_acronym": current[0]["acronym"],
                    }
                )
                continue
            locked = [item for item in values if item["latest_state"] is not None]
            ValidationException.raise_if(
                not locked,
                msg=f"Subpart {child_uid} has no immutable version at the parent lock",
            )
            newest = max(item["latest_state"] for item in locked)
            chosen = [item for item in locked if item["latest_state"] == newest]
            ValidationException.raise_if(
                len(chosen) != 1,
                msg=f"Subpart {child_uid} has an ambiguous pinned value",
            )
            pins.append(
                {
                    "study_uid": child_uid,
                    "study_value_version": newest[1],
                    "subpart_acronym": chosen[0]["acronym"],
                }
            )
        return pins

    @ensure_transaction(db)
    def carry_forward_draft_reasons(
        self, study_uid: str, previous_value_id: str, current_value_id: str
    ) -> StudyVersion | None:
        """Copy reason facts from the exact predecessor of an ordinary draft edit."""
        acquire_write_lock_study_value(study_uid)
        root, current_value, current_record = self.get_selected_version(study_uid)
        if current_value.element_id != current_value_id or current_record is not None:
            raise ValidationException(msg="The successor draft version changed")
        _, _, source = self._resolve_selected_version(
            study_uid,
            "MATCH (study_root:StudyRoot {uid:$study_uid})-[:HAS_VERSION]->(study_value:StudyValue) "
            "WHERE elementId(study_value)=$previous_value_id "
            "WITH DISTINCT study_root, study_value",
            {"study_uid": study_uid, "previous_value_id": previous_value_id},
        )
        if source is None:
            return None
        after = StudyVersion(
            uid=source.uid,
            other_reason_for_locking=source.other_reason_for_locking,
            other_reason_for_unlocking=source.other_reason_for_unlocking,
        ).save()
        current_value.has_study_version.connect(after)
        _manage_versioning_with_relations(
            study_root=root,
            action_type=Edit,
            before=source,
            after=after,
            author_id=self.author_id,
        )
        return after

    @staticmethod
    def amendment_from_record(record: StudyVersion) -> dict[str, Any] | None:
        return StudyVersionRepository.amendment_from_json(record.amendment_json)

    @staticmethod
    def amendment_from_json(value: str | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            return StudyAmendmentVO.from_json(value).to_dict()
        except (ValueError, TypeError) as exc:
            raise ValidationException(
                msg="The selected amendment record is invalid"
            ) from exc

    @ensure_transaction(db)
    def validate_amendment_arms(
        self,
        study_uid: str,
        amendment: StudyAmendmentVO,
        previous_locked_version: str | None,
    ) -> None:
        """Allow exact current arms and arms removed since the preceding lock."""
        acquire_write_lock_study_value(study_uid)
        _, current_value, _ = self.get_selected_version(study_uid)
        values = [current_value.element_id]
        if previous_locked_version is not None:
            _, previous_value, _ = self.get_selected_version(
                study_uid, previous_locked_version
            )
            values.append(previous_value.element_id)
        rows, _ = db.cypher_query(
            """
            MATCH (value:StudyValue)-[:HAS_STUDY_ARM]->(arm:StudyArm)
            WHERE elementId(value) IN $values
            WITH value, arm.uid AS arm_uid, collect(arm) AS selected_arms
            RETURN elementId(value), arm_uid, size(selected_arms),
                [selected IN selected_arms |
                    [(owner:StudyRoot)-[:LATEST|HAS_VERSION]->(:StudyValue)
                        -[:HAS_STUDY_ARM]->(selected) | owner.uid]]
            """,
            {"values": values},
        )
        if any(not uid or count != 1 for _, uid, count, _ in rows):
            raise ValidationException(
                msg="The selected study has ambiguous arm membership"
            )
        if any(
            set(owners) != {study_uid}
            for _, _, _, selected_owners in rows
            for owners in selected_owners
        ):
            raise ValidationException(msg="A selected arm has foreign study ownership")
        available = {uid for _, uid, _, _ in rows}
        missing = set(amendment.affected_arm_uids) - available
        if missing:
            raise ValidationException(
                msg="Affected arms do not belong to the current or preceding locked study version: "
                + ", ".join(sorted(missing))
            )
        if amendment.is_arm_metadata_change:
            # An arm whose metadata changed is still selected in this version;
            # an arm removed since the preceding lock is not a metadata change.
            current = {
                uid
                for value_id, uid, _, _ in rows
                if value_id == current_value.element_id
            }
            not_current = set(amendment.affected_arm_uids) - current
            if not_current:
                raise ValidationException(
                    msg="STUDY_AMENDMENT_ARM_NOT_CURRENT: an arm-metadata-change "
                    "amendment names only arms selected in the version being "
                    "locked: " + ", ".join(sorted(not_current))
                )

    @ensure_transaction(db)
    def create_or_update_study_version(
        self,
        study_uid: str,
        version: str | None = None,
        other_reason_for_unlocking: str | None = None,
        other_reason_for_locking: str | None = None,
        reason_for_lock_term_uid: str | None = None,
        reason_for_unlock_term_uid: str | None = None,
        release: bool = False,
        previous_version: StudyVersion | None = None,
        amendment: StudyAmendmentVO | None = None,
        previous_locked_version: str | None = None,
        parent_pin: dict[str, Any] | None = None,
    ) -> StudyVersion:
        """Write one exact version and carry only explicitly selected reason facts."""
        acquire_write_lock_study_value(study_uid)
        root, value, selected_before = self.get_selected_version(study_uid, version)
        source = previous_version if previous_version is not None else selected_before
        if not reason_for_unlock_term_uid and source is not None:
            other_reason_for_unlocking = source.other_reason_for_unlocking
        if (amendment is not None or parent_pin is not None) and version is None:
            raise ValidationException(
                msg="An amendment or parent pin belongs to a locked study version"
            )
        after = StudyVersion(
            uid=source.uid if source is not None else None,
            other_reason_for_locking=other_reason_for_locking,
            other_reason_for_unlocking=other_reason_for_unlocking,
            amendment_json=amendment.to_json() if amendment is not None else None,
            previous_locked_version=previous_locked_version,
            parent_pin_json=(
                json.dumps(parent_pin, sort_keys=True, separators=(",", ":"))
                if parent_pin is not None
                else None
            ),
        ).save()
        if selected_before is not None:
            value.has_study_version.disconnect(selected_before)
        value.has_study_version.connect(after)
        self._connect_reason_relationships(
            after,
            reason_for_lock_term_uid=reason_for_lock_term_uid,
            reason_for_unlock_term_uid=reason_for_unlock_term_uid,
        )
        _manage_versioning_with_relations(
            study_root=root,
            action_type=Edit if source is not None else Create,
            before=source,
            after=after,
            author_id=self.author_id,
            exclude_relationships=(
                [StudyVersion.has_reason_for_lock]
                if reason_for_lock_term_uid
                else [
                    StudyVersion.has_reason_for_lock,
                    StudyVersion.has_reason_for_unlock,
                ]
            ),
        )
        if release:
            # Release has already created a separate current draft. This applies
            # equally to the first release and later releases.
            _, draft_value, draft_before = self.get_selected_version(study_uid)
            if draft_value.element_id == value.element_id:
                raise ValidationException(
                    msg="A release must have a separate draft value"
                )
            draft = StudyVersion(
                uid=after.uid,
                other_reason_for_unlocking=after.other_reason_for_unlocking,
            ).save()
            if draft_before is not None:
                draft_value.has_study_version.disconnect(draft_before)
            draft_value.has_study_version.connect(draft)
            _manage_versioning_with_relations(
                study_root=root,
                action_type=Edit,
                before=after,
                after=draft,
                author_id=self.author_id,
                exclude_relationships=[StudyVersion.has_reason_for_lock],
            )
            return draft
        return after

    @ensure_transaction(db)
    def _connect_reason_relationships(
        self,
        study_version: StudyVersion,
        reason_for_lock_term_uid: str | None,
        reason_for_unlock_term_uid: str | None,
    ) -> None:
        """
        Helper method to connect reason relationships to StudyVersion.
        Uses get_or_create_selected_term to create CTTermContext nodes if needed.

        Args:
            study_version: The StudyVersion node to connect relationships to
            reason_for_lock_term_uid: UID of CTTerm from "Reason For Lock" codelist
            reason_for_unlock_term_uid: UID of CTTerm from "Reason For Unlock" codelist
        """
        ct_repo = CTCodelistAttributesRepository()

        if reason_for_lock_term_uid:
            term_node = CTTermRoot.nodes.get_or_none(uid=reason_for_lock_term_uid)
            if term_node is None:
                raise ValueError(
                    f"Lock CTTerm with UID '{reason_for_lock_term_uid}' not found"
                )

            ct_context = ct_repo.get_or_create_selected_term(
                term_node,
                codelist_submission_value=settings.reason_for_lock_cl_submval,
            )
            study_version.has_reason_for_lock.connect(ct_context)

        if reason_for_unlock_term_uid:
            term_node = CTTermRoot.nodes.get_or_none(uid=reason_for_unlock_term_uid)
            if term_node is None:
                raise ValueError(
                    f"Unlock CTTerm with UID '{reason_for_unlock_term_uid}' not found"
                )

            ct_context = ct_repo.get_or_create_selected_term(
                term_node,
                codelist_submission_value=settings.reason_for_unlock_cl_submval,
            )
            study_version.has_reason_for_unlock.connect(ct_context)

    def retrieve_study_snapshot_history(
        self,
        study_uid: str,
        page_size: int = 10,
        page_number: int = 1,
        total_count: bool = True,
        only_latest_major_protocol_version: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Retrieves the complete snapshot history of a Study, including all versioned states.

        This method queries the graph database to collect information about all study versions
        (locked, released, etc.) associated with a given Study.
        The query filters out deleted versions and excludes DRAFT versions except for the
        latest draft if the Study is currently in DRAFT status.
        """

        validate_page_number_and_page_size(page_number, page_size)
        match_query = """
        MATCH (study_root:StudyRoot {uid: $study_uid})-[hv:HAS_VERSION]->(study_value:StudyValue)

        WITH *, head([(study_root)-[latest_draft:LATEST_DRAFT]->(study_value) | latest_draft]) AS latest_draft
        WHERE NOT EXISTS((study_value)<-[:BEFORE]-(:Delete)) AND
        // Filter out all DRAFT versions except the latest one if Study is currently in DRAFT status
        (hv.status <> 'DRAFT' OR (latest_draft IS NOT NULL AND latest_draft.end_date IS NULL))
    
        WITH study_value, collect(hv) as all_versions, max(coalesce(hv.end_date, hv.start_date)) as max_date
    
        WITH study_value, all_versions, max_date,
        head([v IN all_versions WHERE coalesce(v.end_date, v.start_date) = max_date]) as latest_version

        WITH study_value, max_date, latest_version, apoc.coll.sort([v IN all_versions | v.status]) as all_statuses
    
        OPTIONAL MATCH (user:User)
        WHERE user.user_id = latest_version.author_id
        WITH study_value, latest_version, max_date as date, all_statuses as statuses, coalesce(user.username, latest_version.author_id) as modified_by

        OPTIONAL MATCH (study_value)-[:HAS_STUDY_VERSION]->(sv:StudyVersion)
        OPTIONAL MATCH (sv)-[:HAS_REASON_FOR_LOCK]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)-->(:CTTermNameRoot)-[:LATEST_FINAL]->(lock_term_val:CTTermNameValue)
        OPTIONAL MATCH (sv)-[:HAS_REASON_FOR_UNLOCK]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(:CTTermRoot)-->(:CTTermNameRoot)-[:LATEST_FINAL]->(unlock_term_val:CTTermNameValue)
        """
        protocol_header_version_match = f"{'' if only_latest_major_protocol_version else 'OPTIONAL'} MATCH (study_value)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(sdd:StudyDefinitionDocument)"

        return_query = """
        RETURN
            lock_term_val.name as reason_for_lock,
            unlock_term_val.name as reason_for_unlock,
            latest_version.version as metadata_version,
            latest_version.change_description AS description,
            sv.other_reason_for_unlocking AS other_reason_for_unlocking,
            sv.other_reason_for_locking AS other_reason_for_lock_release,
            sdd.protocol_header_major_version AS protocol_header_major_version,
            sdd.protocol_header_minor_version AS protocol_header_minor_version,
            elementId(study_value) AS _selected_value_id,
            [(owner:StudyRoot)-[:LATEST|HAS_VERSION]->(study_value) | owner.uid] AS _value_owners,
            size([(study_value)-[membership:HAS_STUDY_VERSION]->() | membership]) AS _record_memberships,
            elementId(sv) AS _record_id,
            [(owner_value:StudyValue)-[:HAS_STUDY_VERSION]->(sv) | elementId(owner_value)] AS _record_values,
            sv.amendment_json AS _amendment_json,
            sv.parent_pin_json AS _parent_pin_json,
            CASE WHEN statuses=["DRAFT"] THEN
                [(current_root:StudyRoot {uid:$study_uid})-[:LATEST]->(current_value:StudyValue) | elementId(current_value)]
            ELSE
                [(version_root:StudyRoot {uid:$study_uid})-[version_rel:HAS_VERSION]->(version_value:StudyValue)
                    WHERE version_rel.version=latest_version.version | elementId(version_value)]
            END AS _version_values,
            date, statuses, modified_by
        // If there is Draft version, return it first, then order by metadata_version descending
        ORDER BY 
            CASE WHEN statuses=["DRAFT"] THEN 0 ELSE 1 END, 
            toInteger(split(metadata_version,'.')[0]) DESC,
            // Applying coealesce(..) to handle cases where minor_version is null, for instance version="1" 
            toInteger(coalesce(split(metadata_version,'.')[1], '0')) DESC
        """
        query = "\n".join(
            [
                match_query,
                protocol_header_version_match,
                return_query,
                # if only latest major protocol header version is requested, we do pagination in memory after filtering, as we can't do filtering in the query
                (
                    db_pagination_clause(page_size, page_number, one_element_extra=True)
                    if not only_latest_major_protocol_version
                    else ""
                ),
            ]
        )
        rows, columns = db.cypher_query(query, {"study_uid": study_uid})
        if not rows or not rows[0]:
            return [], 0

        history = [get_db_result_as_dict(row, columns) for row in rows]
        for entry in history:
            value_id = entry.pop("_selected_value_id")
            owners = entry.pop("_value_owners")
            memberships = entry.pop("_record_memberships")
            record_id = entry.pop("_record_id")
            record_values = entry.pop("_record_values")
            amendment_json = entry.pop("_amendment_json")
            parent_pin_json = entry.pop("_parent_pin_json")
            version_values = entry.pop("_version_values")
            if set(owners) != {study_uid}:
                raise ValidationException(
                    msg="The selected study value has foreign ownership"
                )
            if set(version_values) != {value_id}:
                raise ValidationException(
                    msg="The selected study version has ambiguous values"
                )
            if memberships != (1 if record_id is not None else 0):
                raise ValidationException(
                    msg="The selected study version has ambiguous version records"
                )
            if record_id is not None and record_values != [value_id]:
                raise ValidationException(
                    msg="The selected version record belongs to another study value"
                )
            entry["amendment"] = self.amendment_from_json(amendment_json)
            entry["parent_pin"] = (
                json.loads(parent_pin_json) if parent_pin_json is not None else None
            )
        # If only_latest_major_protocol_version is requested, we have to leave only
        # major protocol header versions and leave only latest available version of it (with highest metadata version)
        if only_latest_major_protocol_version:
            phv_to_index: dict[str, int] = {}
            filtered_history: list[dict[str, Any]] = []
            for h in history:
                major_version = h.get("protocol_header_major_version")
                minor_version = h.get("protocol_header_minor_version")
                if major_version is not None and minor_version == 0:
                    phv = f"{major_version}.{minor_version}"
                    if phv not in phv_to_index:
                        h["original_metadata_version"] = h.get("metadata_version")
                        phv_to_index[phv] = len(filtered_history)
                        filtered_history.append(h)
                    else:
                        filtered_history[phv_to_index[phv]][
                            "original_metadata_version"
                        ] = h.get("metadata_version")
            history = filtered_history

        if not only_latest_major_protocol_version:
            total = (
                calculate_total_count_from_query_result(
                    result_count=len(history),
                    page_size=page_size,
                    page_number=page_number,
                    total_count=total_count,
                    extra_requested=True,
                )
                if total_count
                else 0
            )
        else:
            # Handle pagination in memory not db as there was python post-processing
            total = len(history) if total_count else 0
            if page_size > 0:
                start_idx = (page_number - 1) * page_size
                end_idx = start_idx + page_size
                history = history[start_idx:end_idx]

        # Remove last extra_requested item if it was added
        if 0 < page_size < len(history):
            history = history[:page_size]
        return history, total
