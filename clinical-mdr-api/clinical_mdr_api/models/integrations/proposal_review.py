"""Strict Proposal V2.1 intake and OSB-owned item-review contracts."""

import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic.alias_generators import to_camel

from clinical_mdr_api.models.utils import BaseModel

Hash64 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BoundedString = Annotated[str, Field(max_length=16_384)]
ShortString = Annotated[str, Field(min_length=1, max_length=512)]
Confidence = Annotated[float, Field(ge=0, le=1)]


class StrictProposalModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
    )


class ProposalSourceDocument(StrictProposalModel):
    document_version_id: ShortString
    content_hash: Hash64


class ProposalSourceFactRef(StrictProposalModel):
    fact_id: ShortString
    revision: Annotated[int, Field(ge=1)]
    eligible_approved: StrictBool
    lifecycle: ShortString
    last_operation: ShortString
    fact_content_hash: Hash64
    review_decision: Literal["accepted", "rejected"] | None
    signature_id: Annotated[str, Field(max_length=512)] | None
    document_version_ids: list[ShortString] = Field(min_length=1, max_length=100)


class ProposalEvidenceBox(StrictProposalModel):
    x: StrictFloat | StrictInt
    y: StrictFloat | StrictInt
    width: StrictFloat | StrictInt
    height: StrictFloat | StrictInt
    space: ShortString
    render_scale: StrictFloat | StrictInt | None


class ProposalEvidenceReference(StrictProposalModel):
    provenance_id: ShortString
    document_version_id: ShortString
    document_content_hash: Hash64 | None
    source_span_ref: BoundedString | None
    document_part_ref: BoundedString | None
    page: Annotated[int, Field(ge=1)] | None
    box: ProposalEvidenceBox | None
    exact: StrictBool
    verbatim_text: BoundedString | None
    text_hash: Hash64 | None


class ProposalCandidate(StrictProposalModel):
    candidate_key: Hash64
    resource_family: ShortString
    resource_type: ShortString
    uid: ShortString
    version: ShortString
    package_uid: ShortString | None
    catalogue_name: ShortString | None
    package_version: ShortString | None
    package_effective_date: ShortString | None
    library_name: ShortString | None
    parent_resource_type: ShortString | None
    parent_uid: ShortString | None
    parent_version: ShortString | None
    parent_submission_value: BoundedString | None
    model_uid: ShortString | None
    model_version: ShortString | None
    implementation_guide_uid: ShortString | None
    implementation_guide_version: ShortString | None
    mapping_target_uid: ShortString | None
    mapping_target_version: ShortString | None
    status: ShortString
    valid_from: ShortString | None
    valid_to: ShortString | None
    submission_value: BoundedString | None
    ucum_expression: BoundedString | None
    extensible: StrictBool | None
    dimension: BoundedString | None
    conversion_factor_to_master: StrictFloat | StrictInt | None
    stable_oid: BoundedString | None
    criteria_type_uid: ShortString | None
    parameter_count: Annotated[int, Field(ge=0)] | None
    context_hash: Hash64
    label: BoundedString
    code: BoundedString | None


ProposalScalar = StrictStr | StrictInt | StrictFloat | StrictBool | None
ProposalScalarList = list[StrictStr] | list[StrictInt | StrictFloat] | list[StrictBool]
MAX_SOURCE_VALUE_DEPTH = 10
MAX_SOURCE_VALUE_NODES = 10_000
MAX_SOURCE_LIST_VALUES = 100
MAX_SOURCE_OBJECT_PROPERTIES = 1_000


def _validate_structured_source_value(value: Any) -> None:
    """Enforce the same bounded JSON subset as the TypeScript proposal builder."""
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_SOURCE_VALUE_NODES:
            raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_NODE_LIMIT_EXCEEDED")
        if depth > MAX_SOURCE_VALUE_DEPTH:
            raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_DEPTH_LIMIT_EXCEEDED")
        if item is None or type(item) is bool:
            return
        if type(item) is str:
            if len(item) > 16_384:
                raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_STRING_TOO_LONG")
            return
        if type(item) is int:
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_NON_FINITE")
            return
        if type(item) is list:
            if len(item) > MAX_SOURCE_LIST_VALUES:
                raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_LIST_TOO_LONG")
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict:
            if len(item) > MAX_SOURCE_OBJECT_PROPERTIES:
                raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_OBJECT_TOO_LARGE")
            for key, child in item.items():
                if type(key) is not str or len(key) > 16_384:
                    raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_KEY_INVALID")
                visit(child, depth + 1)
            return
        raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_NOT_JSON")

    visit(value, 0)


class ProposalTypedSourceValue(StrictProposalModel):
    name: ShortString
    source_path: BoundedString
    value_type: Literal[
        "null",
        "string",
        "number",
        "boolean",
        "string_list",
        "number_list",
        "boolean_list",
        "object",
        "json_list",
    ]
    value: ProposalScalar | ProposalScalarList | list[Any] | dict[str, Any]

    @model_validator(mode="after")
    def validate_discriminated_value(self):
        if not self.source_path.startswith("/") or self.source_path == "/":
            raise ValueError("OSB_PROPOSAL_SOURCE_PATH_INVALID")
        for token in self.source_path[1:].split("/"):
            index = 0
            while index < len(token):
                if token[index] == "~":
                    if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                        raise ValueError("OSB_PROPOSAL_SOURCE_PATH_INVALID")
                    index += 2
                else:
                    index += 1
        value = self.value
        valid = {
            "null": value is None,
            "string": type(value) is str,
            "number": type(value) in {int, float} and not isinstance(value, bool),
            "boolean": type(value) is bool,
            "string_list": type(value) is list
            and all(type(item) is str for item in value),
            "number_list": type(value) is list
            and all(
                type(item) in {int, float}
                and not isinstance(item, bool)
                and (type(item) is int or math.isfinite(item))
                for item in value
            ),
            "boolean_list": type(value) is list
            and all(type(item) is bool for item in value),
            "object": type(value) is dict,
            "json_list": type(value) is list,
        }[self.value_type]
        if not valid:
            raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_TYPE_MISMATCH")
        if self.value_type in {"object", "json_list"}:
            _validate_structured_source_value(value)
        elif type(value) is list and len(value) > MAX_SOURCE_LIST_VALUES:
            raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_LIST_TOO_LONG")
        elif type(value) is str and len(value) > 16_384:
            raise ValueError("OSB_PROPOSAL_SOURCE_VALUE_STRING_TOO_LONG")
        return self


class ProposalObjectSource(StrictProposalModel):
    assertion_type: ShortString
    clinical_domain: ShortString
    exact_quote: BoundedString | None
    values: list[ProposalTypedSourceValue] = Field(max_length=100)
    label: BoundedString | None

    @model_validator(mode="after")
    def validate_unique_source_value_paths(self):
        paths = [item.source_path for item in self.values]
        if len(paths) != len(set(paths)):
            raise ValueError("OSB_PROPOSAL_SOURCE_PATH_DUPLICATE")
        return self


class ProposalObjectMapping(StrictProposalModel):
    fact_ids: list[ShortString] = Field(min_length=1, max_length=1)
    evidence: list[ProposalEvidenceReference] = Field(max_length=100)
    proposed_resource_type: ShortString
    candidates: list[ProposalCandidate] = Field(max_length=25)
    selected_candidate: ProposalCandidate | None
    match_method: Literal[
        "none", "uid", "code", "exact_text", "synonym", "semantic_rank"
    ]
    extraction_confidence: Confidence | None
    mapping_confidence: Confidence | None
    disposition: Literal["exact", "review", "create_request", "unresolved"]
    reviewer_decision: None = None


ProposalSectionName = Literal[
    "studySetup",
    "standards",
    "objectives",
    "endpoints",
    "criteria",
    "productsDosing",
    "armsCohortsBranches",
    "epochsElementsCells",
    "visitsTiming",
    "activitiesItems",
    "soa",
    "odm",
    "extensions",
    "retainedNarrative",
    "unresolved",
]


class ProposalObject(StrictProposalModel):
    proposal_object_id: Hash64
    concept_id: Hash64
    target_key: ShortString
    section: ProposalSectionName
    dependency_target_keys: list[ShortString] = Field(max_length=100)
    source: ProposalObjectSource
    mapping: ProposalObjectMapping


class ProposalSections(StrictProposalModel):
    study_setup: list[ProposalObject] = Field(alias="studySetup", max_length=15_000)
    standards: list[ProposalObject] = Field(max_length=15_000)
    objectives: list[ProposalObject] = Field(max_length=15_000)
    endpoints: list[ProposalObject] = Field(max_length=15_000)
    criteria: list[ProposalObject] = Field(max_length=15_000)
    products_dosing: list[ProposalObject] = Field(
        alias="productsDosing", max_length=15_000
    )
    arms_cohorts_branches: list[ProposalObject] = Field(
        alias="armsCohortsBranches", max_length=15_000
    )
    epochs_elements_cells: list[ProposalObject] = Field(
        alias="epochsElementsCells", max_length=15_000
    )
    visits_timing: list[ProposalObject] = Field(alias="visitsTiming", max_length=15_000)
    activities_items: list[ProposalObject] = Field(
        alias="activitiesItems", max_length=15_000
    )
    soa: list[ProposalObject] = Field(max_length=15_000)
    odm: list[ProposalObject] = Field(max_length=15_000)
    extensions: list[ProposalObject] = Field(max_length=15_000)
    retained_narrative: list[ProposalObject] = Field(
        alias="retainedNarrative", max_length=15_000
    )
    unresolved: list[ProposalObject] = Field(max_length=15_000)


class ProposalSourceDisposition(StrictProposalModel):
    fact_id: ShortString
    kind: Literal[
        "duplicate",
        "superseded",
        "quarantined",
        "rejected",
        "not_build_feeding",
        "archived",
        "unreviewed",
        "not_applicable",
        "signed_exclusion",
    ]
    related_fact_id: ShortString | None
    reason: BoundedString
    signature_id: ShortString | None


class ProposalReconciliation(StrictProposalModel):
    source_facts: Annotated[int, Field(ge=0, le=5_000)]
    eligible_approved_facts: Annotated[int, Field(ge=0, le=5_000)]
    proposed_objects: Annotated[int, Field(ge=0, le=15_000)]
    native_study_mutation_targets: Annotated[int, Field(ge=0, le=15_000)]
    governed_library_reference_targets: Annotated[int, Field(ge=0, le=15_000)]
    governed_extension_targets: Annotated[int, Field(ge=0, le=15_000)]
    retained_narrative_targets: Annotated[int, Field(ge=0, le=15_000)]
    unresolved_targets: Annotated[int, Field(ge=0, le=15_000)]
    native_target_source_facts: Annotated[int, Field(ge=0, le=5_000)]
    fully_native_target_source_facts: Annotated[int, Field(ge=0, le=5_000)]
    mapped_source_facts: Annotated[int, Field(ge=0, le=5_000)]
    exact: Annotated[int, Field(ge=0, le=15_000)]
    review: Annotated[int, Field(ge=0, le=15_000)]
    create_requests: Annotated[int, Field(ge=0, le=15_000)]
    unresolved: Annotated[int, Field(ge=0, le=15_000)]
    not_applicable: Annotated[int, Field(ge=0, le=5_000)]
    excluded: Annotated[int, Field(ge=0, le=5_000)]
    quarantined: Annotated[int, Field(ge=0, le=5_000)]
    rejected: Annotated[int, Field(ge=0, le=5_000)]
    not_build_feeding: Annotated[int, Field(ge=0, le=5_000)]
    archived: Annotated[int, Field(ge=0, le=5_000)]
    unreviewed: Annotated[int, Field(ge=0, le=5_000)]
    duplicate_links: Annotated[int, Field(ge=0, le=5_000)]
    supersession_links: Annotated[int, Field(ge=0, le=5_000)]
    signed_exclusions: Annotated[int, Field(ge=0, le=5_000)]
    balanced: Literal[True]
    duplicate_fact_ids: list[ShortString] = Field(max_length=5_000)
    missing_source_fact_ids: list[ShortString] = Field(max_length=5_000)
    dispositions: list[ProposalSourceDisposition] = Field(max_length=5_000)


class OsbStudyProposalV21(StrictProposalModel):
    format_version: Literal["osb-proposal/2.1"]
    canonicalization_version: Literal["canonical-json/1.0"]
    proposal_id: Hash64
    proposal_hash: Hash64
    source_build_hash: Hash64
    tenant_id: ShortString
    study_id: ShortString
    project_id: ShortString | None
    source_run_ids: list[ShortString] = Field(max_length=5_000)
    source_document_version_ids: list[ShortString] = Field(min_length=1, max_length=100)
    source_documents: list[ProposalSourceDocument] = Field(min_length=1, max_length=100)
    previous_proposal_hash: Hash64 | None
    osb_open_api_hash: Hash64
    osb_mapping_context_hash: Hash64
    authority_mode: Literal["shadow", "enforced"]
    sections: ProposalSections
    reconciliation: ProposalReconciliation
    source_fact_refs: list[ProposalSourceFactRef] = Field(max_length=5_000)


ProposalDecisionAction = Literal[
    "selected_candidate",
    "create_request",
    "not_applicable",
    "rejected",
]


class ProposalReviewIntake(StrictProposalModel):
    proposal: OsbStudyProposalV21
    worker_id: ShortString


class ProposalObjectDecisionInput(StrictProposalModel):
    action: ProposalDecisionAction
    candidate_key: Hash64 | None = None
    note: BoundedString | None = None
    signature_id: ShortString


class ProposalObjectDecision(BaseModel):
    decision_id: str
    proposal_object_id: str
    action: ProposalDecisionAction
    candidate_key: str | None = None
    note: str | None = None
    signature_id: str
    signature_verified: bool = False
    decision_content_hash: str
    actor_id: str
    decided_at: datetime


class ProposalReviewObject(BaseModel):
    proposal_object_id: str
    concept_id: str
    target_key: str
    section: str
    proposed_resource_type: str
    capability_kind: str
    dependency_target_keys: list[str] = Field(default_factory=list)
    missing_dependency_target_keys: list[str] = Field(default_factory=list)
    unselected_dependency_target_keys: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    source: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    candidates: list[dict] = Field(default_factory=list)
    match_method: str
    extraction_confidence: float | None = None
    mapping_confidence: float | None = None
    proposed_disposition: str
    latest_decision: ProposalObjectDecision | None = None


class ProposalReviewStatus(BaseModel):
    schema_version: Literal["osb-proposal-review/1.1"] = "osb-proposal-review/1.1"
    mapping_authority: Literal["OpenStudyBuilder"] = "OpenStudyBuilder"
    proposal_hash: str
    proposal_id: str
    source_build_hash: str
    source_study_id: str
    context_hash: str
    osb_openapi_hash: str
    accepted_at: datetime
    accepted_by_worker: str
    source_run_ids: list[str] = Field(default_factory=list)
    source_document_version_ids: list[str] = Field(default_factory=list)
    source_fact_refs: list[dict] = Field(default_factory=list)
    object_count: int
    decided_object_count: int
    rejected_object_count: int
    review_complete: bool
    native_execution_ready: bool
    execution_blockers: list[str] = Field(default_factory=list)
    objects: list[ProposalReviewObject] = Field(default_factory=list)
