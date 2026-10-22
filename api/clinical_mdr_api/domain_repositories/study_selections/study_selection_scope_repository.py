"""Resolve authored selection scope against the selected native study value."""

from neomodel import db

from clinical_mdr_api.domains.study_selections.study_selection_scope import (
    SCOPE_FIELDS,
    SCOPE_TARGETS,
    StudySelectionScopeVO,
)
from common.exceptions import BusinessLogicException, ValidationException

SCOPED_SELECTION_RELATIONS = (
    "HAS_STUDY_ENDPOINT",
    "HAS_STUDY_OBJECTIVE",
    "HAS_STUDY_CRITERIA",
)


class StudySelectionScopeRepository:
    """Scope targets are exact selections of the current draft StudyValue."""

    @staticmethod
    def validate_targets(study_uid: str, scope: StudySelectionScopeVO) -> None:
        for field, (relation, label) in SCOPE_TARGETS.items():
            uids = list(getattr(scope, field))
            if not uids:
                continue
            rows, _ = db.cypher_query(
                f"""
                MATCH (:StudyRoot {{uid:$study_uid}})-[:LATEST]->(:StudyValue)
                    -[:{relation}]->(target:{label})
                WHERE target.uid IN $uids
                RETURN target.uid, count(target)
                """,
                {"study_uid": study_uid, "uids": uids},
            )
            counts = dict(rows)
            ValidationException.raise_if(
                any(count != 1 for count in counts.values()),
                msg=f"Scope targets in {field} have ambiguous native membership",
            )
            missing = sorted(set(uids) - set(counts))
            ValidationException.raise_if(
                bool(missing),
                msg=f"Scope targets in {field} are not selected in this study: "
                + ", ".join(missing),
            )

    @staticmethod
    def current_references(study_uid: str, field: str, target_uid: str) -> list[str]:
        """Current scoped selections that name one target in one dimension."""
        if field not in SCOPE_FIELDS:
            raise ValueError(f"Unknown scope dimension {field}")
        rows, _ = db.cypher_query(
            f"""
            MATCH (:StudyRoot {{uid:$study_uid}})-[:LATEST]->(:StudyValue)
                -[:{'|'.join(SCOPED_SELECTION_RELATIONS)}]->(selection:StudySelection)
            WHERE $target_uid IN coalesce(selection[$field], [])
            RETURN DISTINCT selection.uid ORDER BY selection.uid
            """,
            {"study_uid": study_uid, "target_uid": target_uid, "field": field},
        )
        return [row[0] for row in rows]

    @classmethod
    def refuse_referenced_target(
        cls, study_uid: str, field: str, target_uid: str
    ) -> None:
        """Deleting a scope target must not silently broaden a scoped selection."""
        references = cls.current_references(study_uid, field, target_uid)
        BusinessLogicException.raise_if(
            bool(references),
            msg=f"'{target_uid}' is the authored scope of study selections "
            + ", ".join(references)
            + ". Clear or change that scope first.",
        )
