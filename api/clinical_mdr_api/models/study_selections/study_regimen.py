"""One authored regimen added to a draft study as one native transaction.

Entries reuse the canonical create inputs. Only native reference fields are
replaced, so that an entry can name another entry of the same request through
an explicit local key before its native UID exists. Nothing is resolved by
name or position, and no allocation, approval or execution is implied.
"""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from clinical_mdr_api.models.study_selections.study_definition_document import (
    AuthoredProtocolDocuments,
)
from clinical_mdr_api.models.study_selections.study_selection import (
    StudyCompoundDosingInput,
    StudyDesignCellCreateInput,
    StudySelectionArmCreateInput,
    StudySelectionBranchArmCreateInput,
    StudySelectionCohortCreateInput,
    StudySelectionCompoundCreateInput,
    StudySelectionElementCreateInput,
)

LocalKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="Request-local key. It is never stored and never becomes a native UID.",
    ),
]
NativeUid = Annotated[str, Field(min_length=1, description="Existing native UID")]
MAX_ENTRIES = 100


class StudyRegimenLocalReference(BaseModel):
    """Reference to another entry of the same regimen request."""

    model_config = ConfigDict(extra="forbid")

    local_key: LocalKey


Reference = StudyRegimenLocalReference | NativeUid


class StudyRegimenArmInput(StudySelectionArmCreateInput):
    model_config = ConfigDict(extra="forbid")

    local_key: LocalKey


class StudyRegimenBranchArmInput(StudySelectionBranchArmCreateInput):
    """A branch of one of the new regimen arms.

    New cohorts are created after branches and name their branches, so a branch
    may only join an existing cohort here.
    """

    model_config = ConfigDict(extra="forbid")

    local_key: LocalKey
    arm_uid: StudyRegimenLocalReference  # type: ignore[assignment]


class StudyRegimenCohortInput(StudySelectionCohortCreateInput):
    model_config = ConfigDict(extra="forbid")

    local_key: LocalKey
    arm_uids: list[Reference] | None = None  # type: ignore[assignment]
    branch_arm_uids: list[Reference] | None = None  # type: ignore[assignment]


class StudyRegimenElementInput(StudySelectionElementCreateInput):
    model_config = ConfigDict(extra="forbid")

    local_key: LocalKey


class StudyRegimenCompoundInput(StudySelectionCompoundCreateInput):
    model_config = ConfigDict(extra="forbid")

    local_key: LocalKey


class StudyRegimenCompoundDosingInput(StudyCompoundDosingInput):
    model_config = ConfigDict(extra="forbid")

    local_key: LocalKey
    study_compound_uid: Reference  # type: ignore[assignment]
    study_element_uid: Reference  # type: ignore[assignment]


class StudyRegimenDesignCellInput(StudyDesignCellCreateInput):
    """A cell of a new regimen arm or branch in an existing native epoch."""

    model_config = ConfigDict(extra="forbid")

    local_key: LocalKey
    study_arm_uid: StudyRegimenLocalReference | None = None  # type: ignore[assignment]
    study_branch_arm_uid: StudyRegimenLocalReference | None = None  # type: ignore[assignment]
    study_element_uid: Reference  # type: ignore[assignment]

    @model_validator(mode="after")
    def one_target(self) -> Self:
        if (self.study_arm_uid is None) == (self.study_branch_arm_uid is None):
            raise ValueError(
                "A regimen design cell names exactly one new arm or one new branch arm"
            )
        return self


class StudyRegimenLifecycleEventInput(BaseModel):
    """Authored lifecycle history for a new arm or branch, not an executed decision."""

    model_config = ConfigDict(extra="forbid")

    target_kind: Literal["arm", "branch_arm"]
    target: StudyRegimenLocalReference
    lifecycle_state: Annotated[str, Field(min_length=1)]
    effective_date: AwareDatetime
    decision_reference: Annotated[str, Field(min_length=1)]
    change_reason: Annotated[str, Field(min_length=1)]


class StudyRegimenProtocolDocumentsInput(BaseModel):
    """The complete authored bundle, saved through the existing document path.

    Binding arm UIDs equal to a declared token are replaced by the UID of the
    new arm it names. Every other entry must already be a native arm UID.
    """

    model_config = ConfigDict(extra="forbid")

    expected_content_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=4000)
    arm_tokens: dict[Annotated[str, Field(min_length=1)], LocalKey] = Field(
        default_factory=dict, max_length=MAX_ENTRIES
    )
    content: AuthoredProtocolDocuments

    @model_validator(mode="after")
    def tokens_are_used(self) -> Self:
        if not self.reason.strip():
            raise ValueError("Supply a change reason for the authored documents")
        if any(not token.strip() for token in self.arm_tokens):
            raise ValueError("Arm tokens must be nonblank")
        used = {uid for item in self.content.bindings for uid in item.arm_uids}
        unused = set(self.arm_tokens) - used
        if unused:
            raise ValueError(
                "Declared arm tokens are not used by any document binding: "
                + ", ".join(sorted(unused))
            )
        return self


class StudyRegimenCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_study_version: Annotated[
        str,
        Field(
            min_length=1,
            description="Current draft version token. It detects lock transitions, "
            "not every intervening draft edit.",
        ),
    ]
    arms: list[StudyRegimenArmInput] = Field(min_length=1, max_length=MAX_ENTRIES)
    branch_arms: list[StudyRegimenBranchArmInput] = Field(
        default_factory=list, max_length=MAX_ENTRIES
    )
    cohorts: list[StudyRegimenCohortInput] = Field(
        default_factory=list, max_length=MAX_ENTRIES
    )
    elements: list[StudyRegimenElementInput] = Field(
        default_factory=list, max_length=MAX_ENTRIES
    )
    compounds: list[StudyRegimenCompoundInput] = Field(
        default_factory=list, max_length=MAX_ENTRIES
    )
    compound_dosings: list[StudyRegimenCompoundDosingInput] = Field(
        default_factory=list, max_length=MAX_ENTRIES
    )
    design_cells: list[StudyRegimenDesignCellInput] = Field(
        default_factory=list, max_length=MAX_ENTRIES
    )
    lifecycle_events: list[StudyRegimenLifecycleEventInput] = Field(
        default_factory=list, max_length=MAX_ENTRIES
    )
    protocol_documents: StudyRegimenProtocolDocumentsInput | None = None

    def keys(self, kind: str) -> list[str]:
        return [entry.local_key for entry in getattr(self, kind)]

    @model_validator(mode="after")
    def references_resolve(self) -> Self:
        declared = {}
        for kind in (
            "arms",
            "branch_arms",
            "cohorts",
            "elements",
            "compounds",
            "compound_dosings",
            "design_cells",
        ):
            keys = self.keys(kind)
            if len(set(keys)) != len(keys):
                raise ValueError(f"Local keys must be unique within {kind}")
            declared[kind] = set(keys)

        def check(reference, kind: str, path: str) -> None:
            if (
                isinstance(reference, StudyRegimenLocalReference)
                and reference.local_key not in declared[kind]
            ):
                raise ValueError(
                    f"{path} names undeclared local key {reference.local_key!r} in {kind}"
                )

        for branch in self.branch_arms:
            check(branch.arm_uid, "arms", f"branch_arms[{branch.local_key}].arm_uid")
        for cohort in self.cohorts:
            for reference in cohort.arm_uids or []:
                check(reference, "arms", f"cohorts[{cohort.local_key}].arm_uids")
            for reference in cohort.branch_arm_uids or []:
                check(
                    reference,
                    "branch_arms",
                    f"cohorts[{cohort.local_key}].branch_arm_uids",
                )
        for dosing in self.compound_dosings:
            path = f"compound_dosings[{dosing.local_key}]"
            check(dosing.study_compound_uid, "compounds", path + ".study_compound_uid")
            check(dosing.study_element_uid, "elements", path + ".study_element_uid")
        for cell in self.design_cells:
            path = f"design_cells[{cell.local_key}]"
            if cell.study_arm_uid is not None:
                check(cell.study_arm_uid, "arms", path + ".study_arm_uid")
            if cell.study_branch_arm_uid is not None:
                check(
                    cell.study_branch_arm_uid,
                    "branch_arms",
                    path + ".study_branch_arm_uid",
                )
            check(cell.study_element_uid, "elements", path + ".study_element_uid")
        for index, event in enumerate(self.lifecycle_events):
            check(
                event.target,
                "arms" if event.target_kind == "arm" else "branch_arms",
                f"lifecycle_events[{index}].target",
            )
        if self.protocol_documents is not None:
            for token, key in self.protocol_documents.arm_tokens.items():
                if key not in declared["arms"]:
                    raise ValueError(
                        f"Arm token {token!r} names undeclared arm local key {key!r}"
                    )
        return self


class StudyRegimenCreated(BaseModel):
    """Native UIDs actually created, keyed by the request-local keys."""

    model_config = ConfigDict(extra="forbid")

    study_uid: str
    study_version: str
    arms: dict[str, str]
    branch_arms: dict[str, str]
    cohorts: dict[str, str]
    elements: dict[str, str]
    compounds: dict[str, str]
    compound_dosings: dict[str, str]
    design_cells: dict[str, str]
    lifecycle_event_uids: list[str]
    protocol_documents_content_hash: str | None = None
