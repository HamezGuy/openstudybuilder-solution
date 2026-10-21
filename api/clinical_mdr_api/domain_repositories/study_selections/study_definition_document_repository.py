from neomodel import db

from clinical_mdr_api.domain_repositories._utils.helpers import (
    acquire_write_lock_study_value,
)
from clinical_mdr_api.domain_repositories.generic_repository import (
    _manage_versioning_with_relations,
)
from clinical_mdr_api.domain_repositories.models.study_audit_trail import Create, Edit
from clinical_mdr_api.domain_repositories.models.study_selections import (
    StudyDefinitionDocument,
)
from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyStatus,
)
from clinical_mdr_api.services._utils import ensure_transaction
from common.auth.user import user
from common.config import settings
from common.exceptions import (
    AlreadyExistsException,
    NotFoundException,
    ValidationException,
)


class StudyDefinitionDocumentRepository:
    """
    Repository for managing StudyDefinitionDocument nodes.
    """

    def __init__(self):
        self.author_id = user().id()

    @staticmethod
    def _study_value_query(
        study_uid: str, version: str | None = None
    ) -> tuple[str, dict[str, str | int | None]]:
        params = {
            "study_uid": study_uid,
        }
        if version:
            params["version"] = version
            params["study_status"] = StudyStatus.RELEASED.value
            query = "MATCH (study_root:StudyRoot {uid: $study_uid})-[:HAS_VERSION{status:'RELEASED',version:$version}]->(study_value:StudyValue)"
        else:
            query = "MATCH (study_root:StudyRoot {uid: $study_uid})-[:LATEST]->(study_value:StudyValue)"

        return query, params

    @ensure_transaction(db)
    def get_latest_protocol_header_version(
        self, study_uid: str, study_value_version: str | None = None
    ) -> str | None:
        query, params = self._study_value_query(study_uid, study_value_version)
        rows, _ = db.cypher_query(
            query
            + " OPTIONAL MATCH (study_value)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document:StudyDefinitionDocument)"
            + " RETURN DISTINCT study_value, document",
            params=params,
            resolve_objects=True,
        )
        if len(rows) > 1:
            raise ValidationException(
                msg="The selected study version has ambiguous document selections"
            )
        document = rows[0][1] if rows else None
        if study_value_version is None and (
            document is None
            or document.protocol_header_major_version is None
            or document.protocol_header_minor_version is None
        ):
            # Header-only drafts intentionally have no document selection. Report
            # the last released header, including after a headerless release, by
            # snapshot chronology rather than by the mutable audit chain's shape.
            rows, _ = db.cypher_query(
                "MATCH (:StudyRoot {uid:$study_uid})-[version:HAS_VERSION {status:'RELEASED'}]->(value:StudyValue)"
                "-[:HAS_STUDY_DEFINITION_DOCUMENT]->(header:StudyDefinitionDocument) "
                "WHERE header.protocol_header_major_version IS NOT NULL "
                "AND header.protocol_header_minor_version IS NOT NULL "
                "WITH DISTINCT value, version.start_date AS released_at "
                "ORDER BY released_at DESC LIMIT 1 "
                "MATCH (value)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document:StudyDefinitionDocument) "
                "RETURN DISTINCT value, document",
                {"study_uid": study_uid},
                resolve_objects=True,
            )
            if len(rows) > 1:
                raise ValidationException(
                    msg="The released study has ambiguous protocol headers"
                )
            document = rows[0][1] if rows else None
        if (
            document is None
            or document.protocol_header_major_version is None
            or document.protocol_header_minor_version is None
        ):
            return None
        return f"{document.protocol_header_major_version}.{document.protocol_header_minor_version}"

    @ensure_transaction(db)
    def get_authored_documents(
        self, study_uid: str, study_value_version: str | None = None
    ):
        """Read only the document directly attached to the selected StudyValue."""
        params = {"study_uid": study_uid, "version": study_value_version}
        selection = (
            "MATCH (root:StudyRoot {uid:$study_uid})-[:LATEST]->(value:StudyValue)"
            if study_value_version is None
            else "MATCH (root:StudyRoot {uid:$study_uid})-[:HAS_VERSION {version:$version}]->(value:StudyValue)"
        )
        rows, _ = db.cypher_query(
            selection
            + " OPTIONAL MATCH (value)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document:StudyDefinitionDocument) RETURN DISTINCT value, document",
            params,
            resolve_objects=True,
        )
        if not rows:
            raise NotFoundException("Study version", study_uid)
        if len(rows) != 1:
            raise ValidationException(
                msg="The selected study version has ambiguous document selections"
            )
        return rows[0][1]

    @ensure_transaction(db)
    def save_authored_documents(
        self,
        study_uid: str,
        content_json: str,
        content_hash: str,
        expected_hash: str | None,
        reason: str,
    ):
        """Copy-on-write the existing native selection, including its header."""
        acquire_write_lock_study_value(study_uid)
        before = self.get_authored_documents(study_uid)
        current_hash = getattr(before, "authored_documents_hash", None)
        if current_hash != expected_hash:
            raise AlreadyExistsException(
                msg="Authored documents changed; reload and reconcile before saving"
            )
        if current_hash == content_hash:
            return before
        rows, _ = db.cypher_query(
            "MATCH (root:StudyRoot {uid:$uid})-[:LATEST]->(value:StudyValue) RETURN root, value",
            {"uid": study_uid},
            resolve_objects=True,
        )
        if len(rows) != 1:
            raise NotFoundException("Study", study_uid)
        root, value = rows[0]
        # __properties__ also includes neomodel's element_id_property. Passing it
        # to a new node would turn save() into an update of the historical node.
        properties = before.to_dict() if before is not None else {}
        properties.update(
            authored_documents_json=content_json,
            authored_documents_hash=content_hash,
            authored_documents_reason=reason,
            authored_documents_author=self.author_id,
        )
        after = StudyDefinitionDocument(**properties).save()
        # Historical StudyValues keep their existing selection. Only LATEST moves.
        if before is not None:
            value.has_study_definition_document.disconnect(before)
        value.has_study_definition_document.connect(after)
        _manage_versioning_with_relations(
            study_root=root,
            action_type=Edit if before else Create,
            before=before,
            after=after,
            author_id=self.author_id,
        )
        return after

    def has_final_protocol_locked_version(
        self, study_uid: str, study_value_version: str | None = None
    ) -> bool:
        params: dict[str, str | int | None] = {
            "study_uid": study_uid,
            "final_protocol_submval": settings.final_protocol_term_submval,
        }
        if study_value_version:
            params["version"] = study_value_version
            query = "MATCH (:StudyRoot {uid: $study_uid})-[:HAS_VERSION{status:'LOCKED',version:$version}]->(:StudyValue)"
        else:
            query = "MATCH (:StudyRoot {uid: $study_uid})-[:HAS_VERSION{status:'LOCKED'}]->(:StudyValue)"
        query += """
            -[:HAS_STUDY_VERSION]->(sv:StudyVersion)
            -[:HAS_REASON_FOR_LOCK]->(:CTTermContext)-[:HAS_SELECTED_TERM]->(term_root:CTTermRoot)
            <-[:HAS_TERM_ROOT]-(codelist_term:CTCodelistTerm)
        WHERE codelist_term.submission_value = $final_protocol_submval
        RETURN count(sv) > 0 AS has_final_protocol
        """
        result, _ = db.cypher_query(query, params=params)
        return bool(result and result[0] and result[0][0])

    @ensure_transaction(db)
    def create_or_update_study_definition_document(
        self,
        study_uid: str,
        protocol_header_major_version: int,
        protocol_header_minor_version: int,
        version: str | None = None,
    ) -> StudyDefinitionDocument:

        acquire_write_lock_study_value(study_uid)

        query, params = self._study_value_query(study_uid, version=version)

        query += """
        OPTIONAL MATCH (study_value)-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document:StudyDefinitionDocument)
        RETURN DISTINCT study_root, study_value, document
        """
        rows, _ = db.cypher_query(query, params, resolve_objects=True)
        if not rows:
            raise NotFoundException("Study version", study_uid)
        if len(rows) != 1:
            raise ValidationException(
                msg="The selected study version has ambiguous document selections"
            )
        root, value, before = rows[0]
        if before is None:
            # Legacy header-only snapshots do not carry a document into a draft.
            # Keep their header audit chain without sourcing authored content from
            # any other StudyValue.
            rows, _ = db.cypher_query(
                "MATCH (:StudyRoot {uid:$study_uid})-[:HAS_VERSION]->(:StudyValue)"
                "-[:HAS_STUDY_DEFINITION_DOCUMENT]->(document:StudyDefinitionDocument) "
                "WHERE document.authored_documents_json IS NULL "
                "AND NOT (document)-[:BEFORE]-(:StudyAction) "
                "RETURN DISTINCT document",
                {"study_uid": study_uid},
                resolve_objects=True,
            )
            if len(rows) > 1:
                raise ValidationException(
                    msg="The study has ambiguous protocol headers"
                )
            before = rows[0][0] if rows else None

        # A release creates its next draft before updating the released header.
        # The audit helper unlinks BEFORE from LATEST, so retain that shared
        # authored selection explicitly after the header's copy-on-write.
        latest = root.latest_value.single()
        shared_draft = (
            before is not None
            and before.authored_documents_json is not None
            and latest is not None
            and latest.element_id != value.element_id
            and latest.has_study_definition_document.is_connected(before)
        )
        properties = before.to_dict() if before is not None else {}
        properties.update(
            protocol_header_major_version=protocol_header_major_version,
            protocol_header_minor_version=protocol_header_minor_version,
        )
        after = StudyDefinitionDocument(**properties).save()
        if before is not None:
            value.has_study_definition_document.disconnect(before)
        value.has_study_definition_document.connect(after)
        _manage_versioning_with_relations(
            study_root=root,
            action_type=Edit if before is not None else Create,
            before=before,
            after=after,
            author_id=self.author_id,
        )
        if shared_draft:
            latest.has_study_definition_document.connect(after)
        return after
