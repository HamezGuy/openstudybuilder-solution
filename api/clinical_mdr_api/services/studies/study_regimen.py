"""Add one authored regimen to a draft study in one native transaction.

Every native record is created by its existing canonical service. This module
only orders the calls, resolves request-local references to the native UIDs
actually returned, and lets any failure roll back the whole regimen, including
audit actions and UID counters.
"""

from typing import Any

from neomodel import db

from clinical_mdr_api.domain_repositories._utils.helpers import (
    acquire_write_lock_study_value,
)
from clinical_mdr_api.models.study_selections.study_arm_lifecycle import (
    StudyArmLifecycleEventInput,
)
from clinical_mdr_api.models.study_selections.study_definition_document import (
    AuthoredProtocolDocuments,
    AuthoredProtocolDocumentsInput,
)
from clinical_mdr_api.models.study_selections.study_regimen import (
    Reference,
    StudyRegimenCreated,
    StudyRegimenCreateInput,
    StudyRegimenLocalReference,
    StudyRegimenProtocolDocumentsInput,
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
from clinical_mdr_api.services._utils import ensure_transaction
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from clinical_mdr_api.services.studies.study_branch_arm_selection import (
    StudyBranchArmSelectionService,
)
from clinical_mdr_api.services.studies.study_cohort_selection import (
    StudyCohortSelectionService,
)
from clinical_mdr_api.services.studies.study_compound_dosing_selection import (
    StudyCompoundDosingSelectionService,
)
from clinical_mdr_api.services.studies.study_compound_selection import (
    StudyCompoundSelectionService,
)
from clinical_mdr_api.services.studies.study_definition_document import (
    StudyDefinitionDocumentService,
)
from clinical_mdr_api.services.studies.study_design_cell import StudyDesignCellService
from clinical_mdr_api.services.studies.study_element_selection import (
    StudyElementSelectionService,
)
from common.exceptions import (
    AlreadyExistsException,
    BusinessLogicException,
    ValidationException,
)

# Default of StudyRegimenService.create: the native route checks the authored
# document hash only when the regimen saves documents.
DOCUMENTS_HASH_NOT_CHECKED = object()


def _canonical(entry, model, **references: Any):
    """Rebuild the canonical create input exactly as the client supplied it."""
    values = entry.model_dump(
        exclude={"local_key", *references}, exclude_unset=True, mode="json"
    )
    values.update(
        {name: value for name, value in references.items() if value is not None}
    )
    # The fork's BaseModel.model_validate reads attribute sources; request
    # bodies are validated through the constructor, as here.
    return model(**values)


def _created(uid: str | None, kind: str) -> str:
    ValidationException.raise_if(
        not isinstance(uid, str) or not uid,
        msg=f"The canonical {kind} service returned no native UID",
    )
    assert isinstance(uid, str)
    return uid


class StudyRegimenService:
    """Compose canonical services under one study lock and one transaction."""

    @staticmethod
    def _current_version(study_uid: str) -> str:
        study = StudyService().get_by_uid(study_uid)
        return str(study.current_metadata.version_metadata.version_number)

    @staticmethod
    def _resolve(reference: Reference | None, created: dict[str, str]) -> str | None:
        if reference is None:
            return None
        if isinstance(reference, StudyRegimenLocalReference):
            return created[reference.local_key]
        return reference

    @staticmethod
    def _documents(
        documents: StudyRegimenProtocolDocumentsInput,
        arms: dict[str, str],
        existing_arm_uids: set[str],
        version: str,
    ) -> AuthoredProtocolDocumentsInput:
        tokens = documents.arm_tokens
        collisions = (set(tokens) & existing_arm_uids) | (
            set(tokens) & set(arms.values())
        )
        ValidationException.raise_if(
            bool(collisions),
            msg="Arm tokens collide with native arm UIDs: "
            + ", ".join(sorted(collisions)),
        )
        content = documents.content.model_dump(mode="json")
        for binding in content["bindings"]:
            binding["arm_uids"] = [
                arms[tokens[uid]] if uid in tokens else uid
                for uid in binding["arm_uids"]
            ]
        return AuthoredProtocolDocumentsInput(
            expected_content_hash=documents.expected_content_hash,
            expected_study_version=version,
            reason=documents.reason,
            content=AuthoredProtocolDocuments.model_validate(content),
        )

    @ensure_transaction(db)
    def create(
        self,
        study_uid: str,
        regimen: StudyRegimenCreateInput,
        *,
        expected_documents_hash: str | None | object = DOCUMENTS_HASH_NOT_CHECKED,
    ) -> StudyRegimenCreated:
        """Add the regimen; a governed caller may also pin the authored documents.

        When the regimen saves documents, their expected hash is checked by the
        document save. A caller passing ``expected_documents_hash`` has the
        current bundle hash checked under the same lock for a regimen without
        documents as well (None when the study has no bundle).
        """
        # Route admission may predate a concurrent lock. Hold the study writer
        # lock for the whole regimen and recheck the draft under it.
        acquire_write_lock_study_value(study_uid)
        BusinessLogicException.raise_if(
            StudyService().check_if_study_is_locked(study_uid=study_uid),
            msg=f"Study with UID '{study_uid}' is locked.",
        )
        version = self._current_version(study_uid)
        AlreadyExistsException.raise_if(
            version != regimen.expected_study_version,
            msg="Study version changed; reload before adding a regimen.",
        )
        if (
            expected_documents_hash is not DOCUMENTS_HASH_NOT_CHECKED
            and regimen.protocol_documents is None
        ):
            current_hash = StudyDefinitionDocumentService().get(study_uid)[
                "content_hash"
            ]
            AlreadyExistsException.raise_if(
                current_hash != expected_documents_hash,
                msg="Authored protocol documents changed; reload before adding a regimen.",
            )
        existing_arm_uids = {
            item.arm_uid
            for item in StudyArmSelectionService()
            .get_all_selection(study_uid=study_uid)
            .items
        }

        arms: dict[str, str] = {}
        for arm in regimen.arms:
            arms[arm.local_key] = _created(
                StudyArmSelectionService()
                .make_selection(
                    study_uid,
                    _canonical(
                        arm,
                        StudySelectionArmCreateInput,
                        # The arm's own reason, else the regimen's (FX-OSB-09).
                        change_reason=arm.change_reason or regimen.change_reason,
                    ),
                )
                .arm_uid,
                "arm",
            )
        # Branches precede cells: the first branch of an arm takes over the
        # arm's existing cells. New cohorts follow and name their branches.
        branch_arms: dict[str, str] = {}
        for branch in regimen.branch_arms:
            branch_arms[branch.local_key] = _created(
                StudyBranchArmSelectionService()
                .make_selection(
                    study_uid,
                    _canonical(
                        branch,
                        StudySelectionBranchArmCreateInput,
                        arm_uid=self._resolve(branch.arm_uid, arms),
                    ),
                )
                .branch_arm_uid,
                "branch arm",
            )
        cohorts: dict[str, str] = {}
        for cohort in regimen.cohorts:
            cohorts[cohort.local_key] = _created(
                StudyCohortSelectionService()
                .make_selection(
                    study_uid,
                    _canonical(
                        cohort,
                        StudySelectionCohortCreateInput,
                        arm_uids=(
                            None
                            if cohort.arm_uids is None
                            else [self._resolve(ref, arms) for ref in cohort.arm_uids]
                        ),
                        branch_arm_uids=(
                            None
                            if cohort.branch_arm_uids is None
                            else [
                                self._resolve(ref, branch_arms)
                                for ref in cohort.branch_arm_uids
                            ]
                        ),
                    ),
                )
                .cohort_uid,
                "cohort",
            )
        elements: dict[str, str] = {}
        for element in regimen.elements:
            elements[element.local_key] = _created(
                StudyElementSelectionService()
                .make_selection(
                    study_uid, _canonical(element, StudySelectionElementCreateInput)
                )
                .element_uid,
                "element",
            )
        compounds: dict[str, str] = {}
        for compound in regimen.compounds:
            compounds[compound.local_key] = _created(
                StudyCompoundSelectionService()
                .make_selection(
                    study_uid, _canonical(compound, StudySelectionCompoundCreateInput)
                )
                .study_compound_uid,
                "compound",
            )
        dosings: dict[str, str] = {}
        for dosing in regimen.compound_dosings:
            dosings[dosing.local_key] = _created(
                StudyCompoundDosingSelectionService()
                .make_selection(
                    study_uid,
                    _canonical(
                        dosing,
                        StudyCompoundDosingInput,
                        study_compound_uid=self._resolve(
                            dosing.study_compound_uid, compounds
                        ),
                        study_element_uid=self._resolve(
                            dosing.study_element_uid, elements
                        ),
                    ),
                )
                .study_compound_dosing_uid,
                "compound dosing",
            )
        cells: dict[str, str] = {}
        for cell in regimen.design_cells:
            cells[cell.local_key] = _created(
                StudyDesignCellService()
                .create(
                    study_uid,
                    _canonical(
                        cell,
                        StudyDesignCellCreateInput,
                        study_arm_uid=self._resolve(cell.study_arm_uid, arms),
                        study_branch_arm_uid=self._resolve(
                            cell.study_branch_arm_uid, branch_arms
                        ),
                        study_element_uid=self._resolve(
                            cell.study_element_uid, elements
                        ),
                    ),
                )
                .design_cell_uid,
                "design cell",
            )
        events: list[str] = []
        for event in regimen.lifecycle_events:
            targets = arms if event.target_kind == "arm" else branch_arms
            events.append(
                StudyArmSelectionService()
                .create_lifecycle_event(
                    study_uid,
                    event.target_kind,
                    targets[event.target.local_key],
                    StudyArmLifecycleEventInput(
                        expected_study_version=version,
                        lifecycle_state=event.lifecycle_state,
                        effective_date=event.effective_date,
                        decision_reference=event.decision_reference,
                        change_reason=event.change_reason,
                    ),
                )
                .uid
            )
        content_hash = None
        if regimen.protocol_documents is not None:
            # The existing save checks the expected hash, current arms and the
            # draft state again, and reads the retained bundle back by hash.
            content_hash = StudyDefinitionDocumentService().save(
                study_uid,
                self._documents(
                    regimen.protocol_documents, arms, existing_arm_uids, version
                ),
            )["content_hash"]
        return StudyRegimenCreated(
            study_uid=study_uid,
            study_version=version,
            arms=arms,
            branch_arms=branch_arms,
            cohorts=cohorts,
            elements=elements,
            compounds=compounds,
            compound_dosings=dosings,
            design_cells=cells,
            lifecycle_event_uids=events,
            protocol_documents_content_hash=content_hash,
        )
