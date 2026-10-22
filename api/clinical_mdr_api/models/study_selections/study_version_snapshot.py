"""Exact native structure and explicit limits of a version comparison."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class StudySnapshotCoverageIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    detail: str


class StudyVersionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_uid: str
    version: str
    content: dict[str, Any]
    structural_hash: str
    provenance: dict[str, Any]
    observations: list[dict[str, Any]]
    coverage_issues: list[StudySnapshotCoverageIssue]


class StudyVersionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["added", "removed", "changed"]
    before_present: bool
    after_present: bool
    before: Any = None
    after: Any = None


class StudyVersionDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_uid: str
    from_version: str
    to_version: str
    before: StudyVersionSnapshot
    after: StudyVersionSnapshot
    changes: list[StudyVersionChange]
    structurally_equal: bool
    semantic_comparison_complete: bool
