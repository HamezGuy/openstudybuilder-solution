"""Author and assess the existing versioned native protocol document selection."""

import json
import re
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any

from neomodel import db

from clinical_mdr_api.domain_repositories._utils.helpers import (
    acquire_write_lock_study_value,
)
from clinical_mdr_api.domain_repositories.study_selections.study_definition_document_repository import (
    StudyDefinitionDocumentRepository,
)
from clinical_mdr_api.models.study_selections.study_definition_document import (
    AuthoredProtocolDocuments,
    AuthoredProtocolDocumentsInput,
    authored_content_from_json,
)
from clinical_mdr_api.models.utils import sanitize_html
from clinical_mdr_api.services._utils import ensure_transaction
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from common.exceptions import (
    AlreadyExistsException,
    BusinessLogicException,
    ValidationException,
)


@lru_cache(maxsize=1)
def required_m11_sections() -> dict[str, str]:
    """Required narrative headings from the same pinned specification as M11."""
    path = (
        Path(__file__).parents[3]
        / "m11-templates"
        / "ICH_Step4_M11_Final_TechnicalSpecification_2025_1119.json"
    )
    with path.open(encoding="utf-8") as source:
        specification = json.load(source)
    sections = {}
    for row in specification:
        match = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$", row["term"], re.DOTALL)
        if match and row["dvh"] == "H" and row["conformance"] == "Required":
            sections[match[1]] = " ".join(match[2].split())
    return sections


def assess_authored_documents(content: AuthoredProtocolDocuments) -> dict[str, Any]:
    """Measure section coverage, never clinical adequacy or approval."""
    items = {item.id: item for item in content.narrative_content_items}
    dispositions = {item.content_id: item for item in content.section_dispositions}
    organizations = {item.id: item for item in content.organizations}
    bindings = {item.document_id: item for item in content.bindings}
    required = required_m11_sections()
    documents = []
    for document in content.documents:
        version = document.versions[0]
        sections = {item.sectionNumber: item for item in version.contents}
        issues = []
        for number, title in required.items():
            section = sections.get(number)
            if (
                section is not None
                and " ".join((section.sectionTitle or "").split()).casefold()
                != title.casefold()
            ):
                issues.append(
                    {
                        "code": "SECTION_TITLE_MISMATCH",
                        "section_number": number,
                        "title": title,
                    }
                )
            disposition = dispositions.get(section.id) if section is not None else None
            item = items.get(section.contentItemId) if section is not None else None
            if disposition is None or (
                disposition.state == "authored"
                and (
                    item is None
                    or not unescape(
                        re.sub(
                            r"<[^>]*>", "", sanitize_html(item.text, allow_tables=True)
                        )
                    ).strip()
                )
            ):
                issues.append(
                    {
                        "code": "SECTION_UNRESOLVED",
                        "section_number": number,
                        "title": title,
                    }
                )
        sponsor_id = bindings[document.id].sponsor_organization_id
        sponsor = organizations.get(sponsor_id) if sponsor_id else None
        address = sponsor.legalAddress if sponsor is not None else None
        if (
            sponsor is None
            or not sponsor.name.strip()
            or address is None
            or not any(value.strip() for value in [address.text or "", *address.lines])
        ):
            issues.append(
                {
                    "code": "SPONSOR_UNRESOLVED",
                    "title": "Explicit sponsor organization and legal address",
                }
            )
        documents.append(
            {
                "document_id": document.id,
                "version_id": version.id,
                "version": version.version,
                "name": document.name,
                "section_complete": not issues,
                "issues": issues,
            }
        )
    return {
        "profile": "M11-2025-1119-required-narrative-headings",
        "required_sections": required,
        "documents": documents,
        "section_complete": bool(documents)
        and all(row["section_complete"] for row in documents),
        "meaning": (
            "Required narrative headings have authored content or an explicit "
            "not-applicable reason and a sponsor. This does not assess clinical "
            "adequacy, all M11 data-element conformance, approval, or effective use."
        ),
    }


class StudyDefinitionDocumentService:
    def __init__(self):
        self.repository = StudyDefinitionDocumentRepository()

    @ensure_transaction(db)
    def get(
        self, study_uid: str, study_value_version: str | None = None
    ) -> dict[str, Any]:
        study = StudyService().get_by_uid(
            study_uid, study_value_version=study_value_version
        )
        selection = self.repository.get_authored_documents(
            study_uid, study_value_version
        )
        content = authored_content_from_json(
            getattr(selection, "authored_documents_json", None)
        )
        if content is not None and content.content_hash() != getattr(
            selection, "authored_documents_hash", None
        ):
            raise ValidationException(
                msg="Authored content does not match its retained native hash"
            )
        version = study.current_metadata.version_metadata
        return {
            "study_uid": study_uid,
            "study_value_version": str(version.version_number),
            "study_status": version.study_status,
            "content_hash": getattr(selection, "authored_documents_hash", None),
            "content": content.model_dump(mode="json") if content else None,
            "change_reason": getattr(selection, "authored_documents_reason", None),
            "author_id": getattr(selection, "authored_documents_author", None),
            "assessment": assess_authored_documents(content) if content else None,
            "required_sections": required_m11_sections(),
        }

    @ensure_transaction(db)
    def save(
        self, study_uid: str, request: AuthoredProtocolDocumentsInput
    ) -> dict[str, Any]:
        acquire_write_lock_study_value(study_uid)
        study = StudyService().get_by_uid(study_uid)
        metadata = study.current_metadata.version_metadata
        if str(metadata.version_number) != request.expected_study_version:
            raise AlreadyExistsException(
                msg="Study version changed; reload the authored document source"
            )
        if StudyService().check_if_study_is_locked(study_uid=study_uid):
            raise BusinessLogicException(
                msg="The study is locked; authored documents cannot be edited"
            )
        arms = StudyArmSelectionService().get_all_selection(study_uid=study_uid).items
        arm_uids = {item.arm_uid for item in arms}
        referenced = {
            uid for binding in request.content.bindings for uid in binding.arm_uids
        }
        if not referenced <= arm_uids:
            raise ValidationException(
                msg="Document applicability contains arms outside the current native study"
            )
        self.repository.save_authored_documents(
            study_uid,
            request.content.model_dump_json(),
            request.content.content_hash(),
            request.expected_content_hash,
            request.reason,
        )
        return self.get(study_uid)
