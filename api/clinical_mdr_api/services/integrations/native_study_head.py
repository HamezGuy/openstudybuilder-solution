"""Current study metadata follows the active draft, otherwise the active lock."""

from collections.abc import Callable
from typing import Any


def select_current_study_head(heads: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the native study repository's rule and verify its LATEST pointer."""
    by_relationship = {head["relationship"]: head for head in heads}
    if len(by_relationship) != len(heads) or "LATEST_DRAFT" not in by_relationship:
        raise ValueError("The native study heads are missing or ambiguous.")
    draft = by_relationship["LATEST_DRAFT"]
    active_draft = draft["endDate"] is None
    current = draft if active_draft else by_relationship.get("LATEST_LOCKED")
    if current is None or current["isLatest"] is not True or current["endDate"] is not None:
        raise ValueError("The native study has no active current metadata head.")
    status = current["status"]
    if not isinstance(status, str) or status.strip().upper() != ("DRAFT" if active_draft else "LOCKED"):
        raise ValueError("The native study head status differs from its relationship.")
    status = status.strip()
    # Native drafts normally have no version number. The status is their
    # checkpoint; a lock has the version assigned by the native study lifecycle.
    version = str(current["version"]).strip() if current["version"] is not None else status.lower()
    if not version:
        raise ValueError("The native study head has no version checkpoint.")
    return {
        **current,
        "nativeVersion": version,
        "nativeStatus": status,
        "draftEndedAt": draft["endDate"],
    }


def read_current_study_head(study_uid: str, *, query: Callable) -> dict[str, Any] | None:
    """Read all heads; an ended draft must not win through query ordering."""
    rows, _ = query(
        """MATCH (study:StudyRoot {uid:$study_uid})
           OPTIONAL MATCH (study)-[:LATEST]->(latest:StudyValue)
           OPTIONAL MATCH (study)-[head:LATEST_DRAFT|LATEST_LOCKED|LATEST_RELEASED]->(value:StudyValue)
           RETURN study.uid,type(head),head.version,head.status,
                  toString(head.start_date),toString(head.end_date),value=latest""",
        {"study_uid": study_uid},
    )
    if not rows:
        return None
    if any(len(row) != 7 or row[0] != study_uid for row in rows):
        raise ValueError("The native study head identity differs.")
    return select_current_study_head([
        {
            "relationship": row[1], "version": row[2], "status": row[3],
            "startDate": row[4], "endDate": row[5], "isLatest": row[6],
        }
        for row in rows
    ])
