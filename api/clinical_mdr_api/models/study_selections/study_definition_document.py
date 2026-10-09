"""Authored content on the existing native StudyDefinitionDocument selection.

Document, section, content-item and organization shapes are the USDM models,
not another protocol model. Native bindings record applicability and author
dispositions which USDM does not otherwise represent.
"""

import hashlib
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from usdm_model import Organization, StudyDefinitionDocument
from usdm_model.narrative_content import NarrativeContentItem


class DocumentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1)
    role: Literal["master-protocol", "arm-appendix"]
    arm_uids: list[str] = Field(min_length=1)
    master_document_id: str | None = None
    sponsor_organization_id: str | None = None


class SectionDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_id: str = Field(min_length=1)
    state: Literal["authored", "not-applicable"]
    reason: str | None = None


def _reject_lost_fields(raw: Any, parsed: Any, path: str = "source") -> None:
    """USDM ignores unknown fields by default; authoring must reject data loss."""
    if isinstance(raw, dict) and isinstance(parsed, dict):
        for key, value in raw.items():
            if key not in parsed:
                raise ValueError(f"Unsupported field {path}.{key}; no data was saved")
            if key == "instanceType" and value != parsed[key]:
                raise ValueError(f"Incorrect USDM instanceType at {path}")
            _reject_lost_fields(value, parsed[key], f"{path}.{key}")
    elif isinstance(raw, list) and isinstance(parsed, list):
        for index, value in enumerate(raw):
            _reject_lost_fields(value, parsed[index], f"{path}[{index}]")


def _validate_section_hierarchy(contents: dict[str, Any]) -> None:
    heights: dict[str, int] = {}

    def visit(uid: str, ancestors: set[str]) -> int:
        if uid in ancestors:
            raise ValueError("Narrative section hierarchy must not contain a cycle")
        if len(ancestors) > 32:
            raise ValueError("Narrative hierarchy exceeds the supported depth of 32")
        if uid not in heights:
            heights[uid] = max(
                (
                    1 + visit(child, ancestors | {uid})
                    for child in contents[uid].childIds
                ),
                default=0,
            )
        if len(ancestors) + heights[uid] > 32:
            raise ValueError("Narrative hierarchy exceeds the supported depth of 32")
        return heights[uid]

    for uid in contents:
        visit(uid, set())


class AuthoredProtocolDocuments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[StudyDefinitionDocument] = Field(
        default_factory=list, max_length=100
    )
    narrative_content_items: list[NarrativeContentItem] = Field(
        default_factory=list, max_length=10000
    )
    organizations: list[Organization] = Field(default_factory=list, max_length=100)
    bindings: list[DocumentBinding] = Field(default_factory=list, max_length=100)
    section_dispositions: list[SectionDisposition] = Field(
        default_factory=list, max_length=10000
    )
    synthetic: bool

    @model_validator(mode="wrap")
    @classmethod
    def preserve_fields(cls, value, handler):
        parsed = handler(value)
        if isinstance(value, dict):
            _reject_lost_fields(value, parsed.model_dump(mode="json"))
        return parsed

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        identities: set[str] = set()

        def unique(values, label):
            result = {}
            names = set()
            for item in values:
                if item.id in identities:
                    raise ValueError(f"Duplicate USDM identity {item.id} ({label})")
                identities.add(item.id)
                name = getattr(item, "name", None)
                if name is not None and name in names:
                    raise ValueError(
                        f"Duplicate USDM name {name} ({label}); use distinct names and retain the display title"
                    )
                names.add(name)
                result[item.id] = item
            return result

        documents = unique(self.documents, "document")
        items = unique(self.narrative_content_items, "content item")
        organizations = unique(self.organizations, "organization")
        bindings = {item.document_id: item for item in self.bindings}
        if len(bindings) != len(self.bindings) or set(bindings) != set(documents):
            raise ValueError(
                "Every document requires exactly one applicability binding"
            )
        masters = [item for item in self.bindings if item.role == "master-protocol"]
        if self.documents and len(masters) != 1:
            raise ValueError("An authored bundle requires exactly one master protocol")
        all_contents = {}
        referenced_items: set[str] = set()
        for document in self.documents:
            if document.type.code != "C70817":
                raise ValueError(
                    "Master protocols and arm appendices must have USDM document type Protocol (C70817)"
                )
            binding = bindings[document.id]
            if len(set(binding.arm_uids)) != len(binding.arm_uids) or any(
                not uid.strip() for uid in binding.arm_uids
            ):
                raise ValueError(
                    "Arm applicability must contain unique nonempty native arm UIDs"
                )
            if (
                binding.sponsor_organization_id is not None
                and binding.sponsor_organization_id not in organizations
            ):
                raise ValueError(
                    "Sponsor must reference an explicitly authored organization"
                )
            if binding.role == "master-protocol":
                if binding.master_document_id is not None:
                    raise ValueError(
                        "A master protocol cannot inherit another document"
                    )
            else:
                if binding.master_document_id != masters[0].document_id or not set(
                    binding.arm_uids
                ) <= set(masters[0].arm_uids):
                    raise ValueError(
                        "An arm appendix must reference its master and a subset of its native arms"
                    )
            children = [
                item.document_id
                for item in self.bindings
                if item.master_document_id == document.id
            ]
            if len(set(document.childIds)) != len(document.childIds) or set(
                document.childIds
            ) != set(children):
                raise ValueError(
                    "USDM childIds must match the explicit master/appendix bindings"
                )
            if len(document.versions) != 1:
                raise ValueError(
                    "Retain exactly one selected version per authored document; prior selections remain in the native audit history"
                )
            versions = unique(document.versions, "document version")
            version = next(iter(versions.values()))
            if (
                version.status.code != "C85255"
                or version.status.decode.lower() != "draft"
                or version.dateValues
            ):
                raise ValueError(
                    "This authoring path saves draft content only; approval decisions and dates belong to the document review workflow"
                )
            if not version.version.strip():
                raise ValueError("Document version must not be blank")
            contents = unique(version.contents, "narrative section")
            all_contents.update(contents)
            numbered = [item.sectionNumber for item in version.contents]
            if any(not number or not number.strip() for number in numbered) or len(
                set(numbered)
            ) != len(numbered):
                raise ValueError(
                    "Each document section requires a distinct section number"
                )
            for index, content in enumerate(version.contents):
                if not content.sectionTitle or not content.sectionTitle.strip():
                    raise ValueError("Each narrative section requires a title")
                if content.contentItemId:
                    if content.contentItemId not in items:
                        raise ValueError(
                            f"Unresolved content item {content.contentItemId}"
                        )
                    referenced_items.add(content.contentItemId)
                if len(set(content.childIds)) != len(content.childIds) or any(
                    uid not in contents or uid == content.id for uid in content.childIds
                ):
                    raise ValueError(
                        "Section childIds must resolve inside the same document version"
                    )
                if content.previousId is not None and content.previousId != (
                    version.contents[index - 1].id if index else None
                ):
                    raise ValueError(
                        "Section previousId must agree with the retained content order"
                    )
                if content.nextId is not None and content.nextId != (
                    version.contents[index + 1].id
                    if index + 1 < len(version.contents)
                    else None
                ):
                    raise ValueError(
                        "Section nextId must agree with the retained content order"
                    )

            _validate_section_hierarchy(contents)
        if referenced_items != set(items):
            raise ValueError(
                "Every retained narrative content item must be referenced by a document section"
            )
        dispositions = {item.content_id: item for item in self.section_dispositions}
        if len(dispositions) != len(self.section_dispositions) or not set(
            dispositions
        ) <= set(all_contents):
            raise ValueError(
                "Section dispositions must be unique and refer to retained sections"
            )
        for disposition in self.section_dispositions:
            if disposition.state == "not-applicable":
                if not disposition.reason or not disposition.reason.strip():
                    raise ValueError(
                        "Not-applicable sections require an explicit reason"
                    )
                if all_contents[disposition.content_id].contentItemId:
                    raise ValueError(
                        "A not-applicable section cannot also contain authored text"
                    )
        return self

    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class AuthoredProtocolDocumentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_content_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    expected_study_version: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=4000)
    content: AuthoredProtocolDocuments

    @model_validator(mode="after")
    def bound_size(self) -> Self:
        if (
            not self.reason.strip()
            or len(self.content.model_dump_json().encode("utf-8")) > 5_000_000
        ):
            raise ValueError(
                "Supply a change reason and authored content of at most 5 MB"
            )
        return self


def authored_content_from_json(value: str | None) -> AuthoredProtocolDocuments | None:
    return AuthoredProtocolDocuments.model_validate_json(value) if value else None
