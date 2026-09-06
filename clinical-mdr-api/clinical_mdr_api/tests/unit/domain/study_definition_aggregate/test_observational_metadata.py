"""Focused optional-field semantics; no database setup or mutation."""

from dataclasses import asdict, replace
from unittest import TestCase, mock

from clinical_mdr_api.domains.study_definition_aggregates.study_configuration import (
    FieldConfiguration,
    OBSERVATIONAL_STUDY_CODELISTS,
    StudyFieldConfigurationEntry,
)
from clinical_mdr_api.domains.study_definition_aggregates.study_metadata import (
    HighLevelStudyDesignVO,
)
from clinical_mdr_api.models.controlled_terminologies.configuration import (
    StudyFieldType,
)
from clinical_mdr_api.models.study_selections.study import HighLevelStudyDesignJsonModel
from clinical_mdr_api.services.studies.study import StudyService
from common.exceptions import BusinessLogicException, ValidationException


class ObservationalMetadataTests(TestCase):
    def test_omission_and_explicit_null_patch_are_distinct(self):
        current = HighLevelStudyDesignVO(
            observational_model_code="C15208",
            observational_time_perspective_code="C15273",
        )
        for patch, expected in [
            ({}, current),
            (
                {"observational_model_code": None},
                replace(current, observational_model_code=None),
            ),
        ]:
            actual = StudyService._patch_prepare_new_high_level_study_design(
                current,
                HighLevelStudyDesignJsonModel(**patch),
                lambda _: ([], 0),
                lambda _: None,
            )
            self.assertEqual(actual, expected)

    def test_input_and_fix_preserve_exact_nullable_ids(self):
        value = HighLevelStudyDesignVO(observational_model_code="C15208")
        self.assertEqual(
            HighLevelStudyDesignVO.from_input_values(**asdict(value)), value
        )
        self.assertEqual(value.fix_some_values().observational_model_code, "C15208")
        self.assertIsNone(
            value.fix_some_values(
                observational_model_code=None
            ).observational_model_code
        )

    def test_new_codes_require_their_own_authority_callback(self):
        value = HighLevelStudyDesignVO(
            observational_model_code="model", observational_time_perspective_code="time"
        )
        value.validate(
            observational_model_exists_callback=lambda uid: uid == "model",
            observational_time_perspective_exists_callback=lambda uid: uid == "time",
        )
        for callback in [
            "observational_model_exists_callback",
            "observational_time_perspective_exists_callback",
        ]:
            with self.assertRaises(ValidationException):
                value.validate(**{callback: lambda _: False})

    def test_null_codes_do_not_require_a_term_or_get_defaulted(self):
        fail = lambda _: self.fail("Null is not a CT selection")
        HighLevelStudyDesignVO().validate(
            observational_model_exists_callback=fail,
            observational_time_perspective_exists_callback=fail,
        )

    def test_native_configuration_is_additive_and_does_not_modify_cached_rows(self):
        existing = StudyFieldConfigurationEntry(
            StudyFieldType.TEXT,
            "study_type_code",
            None,
            "C99077",
            None,
            "high_level_study_design",
            HighLevelStudyDesignVO,
            "study_type_code",
            False,
        )
        baseline = [existing]
        with mock.patch.object(FieldConfiguration, "field_config", baseline):
            config = FieldConfiguration.default_field_config()
            self.assertEqual(baseline, [existing])
            for name, uid in OBSERVATIONAL_STUDY_CODELISTS.items():
                actual = next(row for row in config if row.study_field_name == name)
                self.assertEqual(actual.configured_codelist_uid, uid)
                self.assertEqual(actual.study_field_data_type, StudyFieldType.TEXT)

    def test_conflicting_native_codelist_configuration_is_rejected(self):
        bad = StudyFieldConfigurationEntry(
            StudyFieldType.TEXT,
            "observational_model_code",
            None,
            "WRONG",
            None,
            "high_level_study_design",
            HighLevelStudyDesignVO,
            "observational_model_code",
            False,
        )
        with mock.patch.object(
            FieldConfiguration, "field_config", [bad]
        ), self.assertRaises(BusinessLogicException):
            FieldConfiguration.default_field_config()

    def test_conflicting_native_storage_alias_is_rejected(self):
        bad = StudyFieldConfigurationEntry(
            StudyFieldType.TEXT,
            "another_field",
            None,
            "C127259",
            None,
            "high_level_study_design",
            HighLevelStudyDesignVO,
            "observational_model_code",
            False,
        )
        with mock.patch.object(
            FieldConfiguration, "field_config", [bad]
        ), self.assertRaises(BusinessLogicException):
            FieldConfiguration.default_field_config()
