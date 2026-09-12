"""Real study aggregate/service with authored, offline repository boundaries."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, fields, replace
from types import SimpleNamespace
from unittest.mock import patch

from neomodel import db
from neomodel.sync_.transaction import TransactionProxy

from clinical_mdr_api.domains.study_definition_aggregates.registry_identifiers import (
    RegistryIdentifiersVO,
)
from clinical_mdr_api.domains.study_definition_aggregates.root import (
    _DEF_INITIAL_HIGH_LEVEL_STUDY_DESIGN,
    _DEF_INITIAL_STUDY_INTERVENTION,
    _DEF_INITIAL_STUDY_POPULATION,
    StudyDefinitionAR,
)
from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    StudyComponentEnum,
    StudyIdentificationMetadataVO,
)
from clinical_mdr_api.models.study_selections.null_adjudication import (
    StudyNullAdjudicationRequest,
)
from clinical_mdr_api.models.study_selections.study import StudyPatchRequestJsonModel
from clinical_mdr_api.services.studies.study import StudyService
from clinical_mdr_api.services.user_info import UserInfoService

STUDY_UID = "OFFLINE_NULL_STUDY"
VALUE_PATH = "high_level_study_design.is_extension_trial"
COMPANION_PATH = "high_level_study_design.is_extension_trial_null_value_code"


def request_payload(**row_overrides):
    return {
        "contract_version": "StudyNullAdjudicationV1@1.0.0",
        "adjudications": [
            {
                "value_path": VALUE_PATH,
                "companion_path": COMPANION_PATH,
                "expected_value": {"present": True, "value": None},
                "expected_null_companion": {"present": True, "value": None},
                "null_term_uid": "CT_UNK",
                **row_overrides,
            }
        ],
    }


def authored_study(
    uid=STUDY_UID, parent=None, design=None, population=None, intervention=None
):
    identifiers = RegistryIdentifiersVO(
        **{entry.name: None for entry in fields(RegistryIdentifiersVO)}
    )
    study = StudyDefinitionAR.from_initial_values(
        generate_uid_callback=lambda: uid,
        initial_id_metadata=StudyIdentificationMetadataVO.from_input_values(
            project_number="123",
            study_number="17",
            subpart_id="1" if parent else None,
            study_acronym="AUTHORED",
            study_subpart_acronym="PART" if parent else None,
            description="retained identification",
            registry_identifiers=identifiers,
        ),
        initial_high_level_study_design=design
        or deepcopy(_DEF_INITIAL_HIGH_LEVEL_STUDY_DESIGN),
        initial_study_population=population or deepcopy(_DEF_INITIAL_STUDY_POPULATION),
        initial_study_intervention=intervention
        or deepcopy(_DEF_INITIAL_STUDY_INTERVENTION),
        study_title_exists_callback=lambda *_: False,
        study_short_title_exists_callback=lambda *_: False,
        author_id="offline-author",
        is_subpart=parent is not None,
    )
    study.study_parent_part_uid = parent
    return study


class AuthoredStudyRepository:
    def __init__(self, study, parent=None):
        self.studies = {study.uid: deepcopy(study)}
        if parent:
            self.studies[parent.uid] = deepcopy(parent)
        self.calls = []
        self.on_locked_read = None

    def find_by_uid(self, uid, for_update=False, study_value_version=None):
        assert study_value_version is None
        self.calls.append(("read", uid, for_update))
        if for_update:
            assert db._active_transaction is not None
            if self.on_locked_read:
                callback, self.on_locked_read = self.on_locked_read, None
                callback()
        return deepcopy(self.studies.get(uid))

    def save(self, study):
        self.calls.append(("save", study.uid))
        self.studies[study.uid] = deepcopy(study)

    def update_subpart_relationship(self, prior, parent):
        self.calls.append(("parent", prior.uid, parent))

    @staticmethod
    def study_number_exists(*_):
        return False

    @staticmethod
    def study_acronym_exists(*_):
        return False


@contextmanager
def native_harness(parent=False, design=None, population=None, intervention=None):
    with patch.object(
        UserInfoService, "get_author_username_from_id", side_effect=lambda uid: uid
    ):
        parent_study = authored_study("OFFLINE_PARENT") if parent else None
        study = authored_study(
            parent=parent_study.uid if parent else None,
            design=design,
            population=population,
            intervention=intervention,
        )
        repository = AuthoredStudyRepository(study, parent_study)
        service = object.__new__(StudyService)
        service.author_id = "offline-author"
        project = SimpleNamespace(
            name="Authored project", clinical_programme_uid="OFFLINE_CP"
        )
        terms = SimpleNamespace(
            term_exists=lambda uid: uid in {"CT_NA", "CT_UNK", "CT_A", "CT_B"},
            find_by_uids=lambda *args, **kwargs: None,
        )
        service._repos = SimpleNamespace(
            study_definition_repository=repository,
            project_repository=SimpleNamespace(
                project_number_exists=lambda *_: True,
                find_by_project_number=lambda *_: project,
            ),
            clinical_programme_repository=SimpleNamespace(
                find_by_uid=lambda *_: SimpleNamespace(name="Authored programme")
            ),
            ct_term_name_repository=terms,
            dictionary_term_generic_repository=SimpleNamespace(
                term_exists=lambda *_: True, find_by_uid=lambda *_: None
            ),
            unit_definition_repository=SimpleNamespace(
                find_all=lambda *args, **kwargs: ([], 0), find_by_uid_2=lambda *_: None
            ),
            study_title_repository=SimpleNamespace(
                study_title_exists=lambda *_: False,
                study_short_title_exists=lambda *_: False,
            ),
            study_standard_version_repository=SimpleNamespace(
                find_standard_versions_in_study=lambda **_: []
            ),
            close=lambda: repository.calls.append(("close",)),
        )
        # This is an authored active-transaction seam, not a Neo4j lock proof.
        previous_transaction = db._active_transaction
        db._active_transaction = object()
        try:
            # Preserve the public GET's real reader/projection. Only its database
            # transaction transport is authored, as for the PATCH above.
            with patch.object(
                TransactionProxy, "__enter__", lambda transaction: transaction
            ), patch.object(
                TransactionProxy,
                "__exit__",
                lambda *_: None,
            ):
                yield SimpleNamespace(
                    service=service,
                    repository=repository,
                    current=lambda: repository.studies[STUDY_UID],
                    guarded=lambda data: service.patch(
                        STUDY_UID,
                        False,
                        StudyPatchRequestJsonModel(),
                        null_adjudication=StudyNullAdjudicationRequest.model_validate(
                            data
                        ),
                    ),
                    ordinary=lambda metadata: service.patch(
                        STUDY_UID,
                        False,
                        StudyPatchRequestJsonModel(current_metadata=metadata),
                    ),
                    readback=lambda: service.get_by_uid(
                        STUDY_UID,
                        include_sections=[
                            StudyComponentEnum.STUDY_DESIGN,
                            StudyComponentEnum.STUDY_POPULATION,
                            StudyComponentEnum.STUDY_INTERVENTION,
                        ],
                    ),
                )
        finally:
            db._active_transaction = previous_transaction


def metadata_without_version(study):
    result = asdict(study.current_metadata)
    result.pop("ver_metadata")
    return result
