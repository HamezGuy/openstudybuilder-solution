"""Author control pools and adaptation references on the current draft (O7)."""

from datetime import datetime, timezone

from neomodel import db

from clinical_mdr_api.domain_repositories.study_selections.study_control_pool_repository import (
    StudyControlPoolRepository,
)
from clinical_mdr_api.domains.study_selections.study_control_pool import (
    StudyAdaptationReferenceVO,
    StudyControlPoolVO,
)
from clinical_mdr_api.models.study_selections.study_control_pool import (
    StudyAdaptationReference,
    StudyAdaptationReferenceInput,
    StudyControlPool,
    StudyControlPoolInput,
)
from clinical_mdr_api.services._utils import ensure_transaction
from common.auth.user import user


class StudyControlPoolService:
    """Each write rechecks the draft and its version under the study lock."""

    def __init__(self):
        self.author = user().id()
        self.repository = StudyControlPoolRepository(self.author)

    def _pool(self, study_uid: str, uid: str, data: StudyControlPoolInput):
        return StudyControlPoolVO(
            uid=uid,
            study_uid=study_uid,
            name=data.name,
            donor_arm_uids=tuple(data.donor_arm_uids),
            focal_arm_uids=tuple(data.focal_arm_uids),
            csl_reference=data.csl_reference.to_vo() if data.csl_reference else None,
            description=data.description,
            author_id=self.author,
            start_date=datetime.now(timezone.utc),
        )

    def _adaptation(
        self, study_uid: str, uid: str, data: StudyAdaptationReferenceInput
    ):
        return StudyAdaptationReferenceVO(
            uid=uid,
            study_uid=study_uid,
            adaptation_type=data.adaptation_type,
            csl_reference=data.csl_reference.to_vo(),
            affected_arm_uids=tuple(data.affected_arm_uids),
            affected_epoch_uids=tuple(data.affected_epoch_uids),
            trigger_reference=data.trigger_reference,
            description=data.description,
            author_id=self.author,
            start_date=datetime.now(timezone.utc),
        )

    @ensure_transaction(db)
    def create_control_pool(
        self, study_uid: str, data: StudyControlPoolInput
    ) -> StudyControlPool:
        record = self._pool(study_uid, self.repository.next_uid("control_pool"), data)
        return StudyControlPool.from_vo(
            self.repository.save(
                "control_pool", record, data.expected_study_version, replaces=False
            )
        )

    @ensure_transaction(db)
    def update_control_pool(
        self, study_uid: str, uid: str, data: StudyControlPoolInput
    ) -> StudyControlPool:
        return StudyControlPool.from_vo(
            self.repository.save(
                "control_pool",
                self._pool(study_uid, uid, data),
                data.expected_study_version,
                replaces=True,
            )
        )

    @ensure_transaction(db)
    def get_control_pools(
        self, study_uid: str, study_value_version: str | None = None
    ) -> list[StudyControlPool]:
        return [
            StudyControlPool.from_vo(item)
            for item in self.repository.find(
                "control_pool", study_uid, study_value_version
            )
        ]

    @ensure_transaction(db)
    def create_adaptation_reference(
        self, study_uid: str, data: StudyAdaptationReferenceInput
    ) -> StudyAdaptationReference:
        record = self._adaptation(
            study_uid, self.repository.next_uid("adaptation_reference"), data
        )
        return StudyAdaptationReference.from_vo(
            self.repository.save(
                "adaptation_reference",
                record,
                data.expected_study_version,
                replaces=False,
            )
        )

    @ensure_transaction(db)
    def update_adaptation_reference(
        self, study_uid: str, uid: str, data: StudyAdaptationReferenceInput
    ) -> StudyAdaptationReference:
        return StudyAdaptationReference.from_vo(
            self.repository.save(
                "adaptation_reference",
                self._adaptation(study_uid, uid, data),
                data.expected_study_version,
                replaces=True,
            )
        )

    @ensure_transaction(db)
    def get_adaptation_references(
        self, study_uid: str, study_value_version: str | None = None
    ) -> list[StudyAdaptationReference]:
        return [
            StudyAdaptationReference.from_vo(item)
            for item in self.repository.find(
                "adaptation_reference", study_uid, study_value_version
            )
        ]

    @ensure_transaction(db)
    def delete(
        self, kind, study_uid: str, uid: str, expected_study_version: str
    ) -> None:
        self.repository.delete(kind, study_uid, uid, expected_study_version)
