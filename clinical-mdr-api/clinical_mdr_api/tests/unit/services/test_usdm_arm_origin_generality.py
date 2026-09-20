"""Native arm export requires explicit origin and the selected DDF package."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from usdm_model import Code

from clinical_mdr_api.domain_repositories.study_selections.study_arm_origin_repository import (
    StudyArmOriginRepository,
)
from clinical_mdr_api.models.controlled_terminologies.ct_term import SimpleCodelistTermModel

from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionArm,
    StudySelectionArmCreateInput,
    StudySelectionArmInput,
)
from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper, USDMMappingAuthorityRequired
from common.exceptions import ValidationException


def _mapper():
    empty = lambda *_args, **_kwargs: []
    return USDMMapper(
        get_osb_study_design_cells=empty, get_osb_study_arms=empty,
        get_osb_study_epochs=empty, get_osb_study_elements=empty,
        get_osb_study_endpoints=empty, get_osb_study_visits=empty,
        get_osb_study_activities=empty, get_osb_activity_schedules=empty,
        get_osb_study_objectives=empty,
    )


def _native_arm(description=None, origin_uid=None, origin_description=None):
    request = StudySelectionArmCreateInput(
        name="Historical control", short_name="HC", description=description,
        data_origin_type_uid=origin_uid, data_origin_description=origin_description,
    )
    return StudySelectionArm(**{
        **request.model_dump(), "study_uid": "Study_synthetic", "arm_uid": "Arm_synthetic",
        "order": 1, "start_date": datetime(2026, 9, 5, tzinfo=timezone.utc),
    })


class NativeArmOriginGeneralityTests(unittest.TestCase):
    def test_create_patch_response_expose_the_same_raw_native_fields(self):
        for model in (StudySelectionArmCreateInput, StudySelectionArmInput, StudySelectionArm):
            for field in ("data_origin_type_uid", "data_origin_description"):
                self.assertIn(field, model.model_fields)
        row = _native_arm()
        self.assertIsNone(row.data_origin_type_uid)
        self.assertIsNone(row.data_origin_description)

    def test_name_arm_type_and_general_description_never_supply_origin(self):
        for description in (None, "External historical cohort", "Data Generated Within Study"):
            with self.subTest(description=description):
                row = _native_arm(description)
                before = row.model_dump(mode="json")
                mapper = _mapper()
                mapper._get_osb_study_arms = lambda *_args, **_kwargs: [row]
                mapper.get_ct_package_term_as_usdm_code = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("a guessed CT origin lookup must never occur"))
                with self.assertRaisesRegex(USDMMappingAuthorityRequired, "USDM_ARM_DATA_ORIGIN_AUTHORITY_REQUIRED") as error:
                    mapper._get_study_arms(SimpleNamespace(uid="Study_synthetic"))
                self.assertEqual(error.exception.status_code, 422)
                self.assertEqual(row.model_dump(mode="json"), before)

    def test_empty_native_arm_collection_does_not_invent_an_origin_or_raise_an_arm_hold(self):
        self.assertEqual(_mapper()._get_study_arms(SimpleNamespace(uid="Study_synthetic")), [])

    def test_distinct_explicit_origins_keep_exact_codes_descriptions_and_package(self):
        for uid, code, decode in (
            ("C188864_HISTORICAL", "C188864", "Historical Data"),
            ("C165830_REAL_WORLD", "C165830", "Real World Data"),
        ):
            with self.subTest(uid=uid):
                description = "  Source-specific origin rationale.\nRetain this evidence.  "
                row = _native_arm(origin_uid=uid, origin_description=description)
                row.arm_type = SimpleCodelistTermModel(term_uid="C174266", term_name="Control Arm")
                mapper = _mapper()
                mapper._get_osb_study_arms = lambda *_args, **_kwargs: [row]
                mapper._ct_packages = {"DDF CT": {"uid": "ddfct-2024-09-27", "effective_date": "2024-09-27"}}
                mapper.get_ct_package_term_as_usdm_code = lambda term: Code(
                    id="arm-type", code=term, codeSystem="CDISC",
                    codeSystemVersion="sdtmct-2024-09-27", decode="Control Arm",
                    instanceType="Code",
                )
                before = row.model_dump(mode="json")
                with patch.object(StudyArmOriginRepository, "package_code", return_value={
                    "code": code, "decode": decode, "code_system": "CDISC",
                    "code_system_version": "ddfct-2024-09-27",
                }) as lookup:
                    result = mapper._get_study_arms(SimpleNamespace(uid="Study_synthetic"))
                lookup.assert_called_once_with(uid, "ddfct-2024-09-27", "2024-09-27")
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].dataOriginType.code, code)
                self.assertEqual(result[0].dataOriginType.decode, decode)
                self.assertEqual(result[0].dataOriginType.codeSystemVersion, "ddfct-2024-09-27")
                self.assertEqual(result[0].dataOriginDescription, description)
                self.assertEqual(row.model_dump(mode="json"), before)

    def test_missing_origin_description_is_a_hold_even_with_an_explicit_uid(self):
        mapper = _mapper()
        mapper._get_osb_study_arms = lambda *_args, **_kwargs: [
            _native_arm(origin_uid="C188864", origin_description=" ")
        ]
        with patch.object(StudyArmOriginRepository, "package_code") as lookup:
            with self.assertRaisesRegex(USDMMappingAuthorityRequired, "ORIGIN_AUTHORITY_REQUIRED"):
                mapper._get_study_arms(SimpleNamespace(uid="Study_synthetic"))
        lookup.assert_not_called()

    def test_sdtm_package_cannot_substitute_for_missing_ddf_package(self):
        mapper = _mapper()
        mapper._get_osb_study_arms = lambda *_args, **_kwargs: [
            _native_arm(origin_uid="C188864", origin_description="Explicit historical source.")
        ]
        mapper._ct_packages = {"SDTM CT": {"uid": "sdtmct-2024-09-27", "effective_date": "2024-09-27"}}
        with patch.object(StudyArmOriginRepository, "package_code") as lookup:
            with self.assertRaisesRegex(USDMMappingAuthorityRequired, "ORIGIN_CT_PIN_REQUIRED"):
                mapper._get_study_arms(SimpleNamespace(uid="Study_synthetic"))
        lookup.assert_not_called()

    def test_missing_or_ambiguous_package_membership_stays_an_export_hold(self):
        mapper = _mapper()
        mapper._get_osb_study_arms = lambda *_args, **_kwargs: [
            _native_arm(origin_uid="C188864", origin_description="Explicit historical source.")
        ]
        mapper._ct_packages = {"DDF CT": {"uid": "ddfct-2024-09-27", "effective_date": "2024-09-27"}}
        with patch.object(StudyArmOriginRepository, "package_code", side_effect=ValidationException(
            msg="USDM_ARM_DATA_ORIGIN_CT_PIN_REQUIRED"
        )):
            with self.assertRaisesRegex(USDMMappingAuthorityRequired, "ORIGIN_CT_PIN_REQUIRED") as error:
                mapper._get_study_arms(SimpleNamespace(uid="Study_synthetic"))
        self.assertEqual(error.exception.status_code, 422)

    def test_explicit_origin_does_not_make_an_untyped_arm_exportable(self):
        mapper = _mapper()
        mapper._get_osb_study_arms = lambda *_args, **_kwargs: [
            _native_arm(origin_uid="C188864", origin_description="Explicit historical source.")
        ]
        mapper._ct_packages = {"DDF CT": {"uid": "ddfct-2024-09-27", "effective_date": "2024-09-27"}}
        with patch.object(StudyArmOriginRepository, "package_code", return_value={
            "code": "C188864", "decode": "Historical Data", "code_system": "CDISC",
            "code_system_version": "ddfct-2024-09-27",
        }):
            with self.assertRaisesRegex(USDMMappingAuthorityRequired, "ARM_TYPE_AUTHORITY_REQUIRED"):
                mapper._get_study_arms(SimpleNamespace(uid="Study_synthetic"))

    def test_selected_package_is_read_from_the_requested_study_version(self):
        mapper = _mapper()
        calls = []
        def selected(**kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(ct_package=SimpleNamespace(
                catalogue_name="DDF CT", uid="ddfct-2024-09-27", effective_date=date(2024, 9, 27)
            ))]
        mapper._get_osb_study_standard_versions = selected
        mapper._study_value_version = "3"
        mapper._load_selected_ct_packages("Study_synthetic")
        self.assertEqual(calls, [{"study_uid": "Study_synthetic", "study_value_version": "3"}])
        self.assertEqual(mapper._ct_packages["DDF CT"], {
            "uid": "ddfct-2024-09-27", "effective_date": "2024-09-27"
        })

    def test_duplicate_catalogue_selection_is_not_resolved_by_taking_the_latest(self):
        mapper = _mapper()
        mapper._get_osb_study_standard_versions = lambda **_kwargs: [
            SimpleNamespace(ct_package=SimpleNamespace(
                catalogue_name="DDF CT", uid=f"ddfct-{year}-09-27",
                effective_date=date(year, 9, 27),
            )) for year in (2023, 2024)
        ]
        with self.assertRaisesRegex(USDMMappingAuthorityRequired, "SELECTION_AMBIGUOUS"):
            mapper._load_selected_ct_packages("Study_synthetic")

    def test_incomplete_selected_package_is_not_silently_dropped(self):
        for package in (
            None,
            SimpleNamespace(catalogue_name="DDF CT", uid=None, effective_date=date(2024, 9, 27)),
            SimpleNamespace(catalogue_name="DDF CT", uid="ddfct-2024-09-27", effective_date=None),
        ):
            with self.subTest(package=package):
                mapper = _mapper()
                mapper._get_osb_study_standard_versions = lambda **_kwargs: [
                    SimpleNamespace(ct_package=package)
                ]
                with self.assertRaisesRegex(USDMMappingAuthorityRequired, "SELECTION_INCOMPLETE"):
                    mapper._load_selected_ct_packages("Study_synthetic")
