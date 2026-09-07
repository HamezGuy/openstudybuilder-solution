"""Current native arm DTO boundaries must not fabricate USDM data origin."""
from datetime import datetime, timezone
from types import SimpleNamespace

import unittest

from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionArm,
    StudySelectionArmCreateInput,
    StudySelectionArmInput,
)
from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper, USDMMappingAuthorityRequired


def _mapper():
    empty = lambda *_args, **_kwargs: []
    return USDMMapper(
        get_osb_study_design_cells=empty, get_osb_study_arms=empty,
        get_osb_study_epochs=empty, get_osb_study_elements=empty,
        get_osb_study_endpoints=empty, get_osb_study_visits=empty,
        get_osb_study_activities=empty, get_osb_activity_schedules=empty,
        get_osb_study_objectives=empty,
    )


def _native_arm(description=None):
    request = StudySelectionArmCreateInput(
        name="Historical control", short_name="HC", description=description,
    )
    return StudySelectionArm(**{
        **request.model_dump(), "study_uid": "Study_synthetic", "arm_uid": "Arm_synthetic",
        "order": 1, "start_date": datetime(2026, 9, 5, tzinfo=timezone.utc),
    })


class NativeArmOriginGeneralityTests(unittest.TestCase):
    def test_current_create_patch_response_contracts_have_no_persisted_origin_slot(self):
        for model in (StudySelectionArmCreateInput, StudySelectionArmInput, StudySelectionArm):
            for field in ("data_origin_type_code", "data_origin_type", "data_origin_description"):
                self.assertNotIn(field, model.model_fields)

    def test_real_native_arm_dto_requires_capability_instead_of_inferring_origin(self):
        for description in (None, "External historical cohort", "Data Generated Within Study"):
            with self.subTest(description=description):
                row = _native_arm(description)
                before = row.model_dump(mode="json")
                mapper = _mapper()
                mapper._get_osb_study_arms = lambda *_args, **_kwargs: [row]
                mapper.get_ct_package_term_as_usdm_code = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("a guessed CT origin lookup must never occur"))
                with self.assertRaisesRegex(USDMMappingAuthorityRequired, "USDM_ARM_DATA_ORIGIN_CAPABILITY_REQUIRED") as error:
                    mapper._get_study_arms(SimpleNamespace(uid="Study_synthetic"))
                self.assertEqual(error.exception.status_code, 422)
                self.assertEqual(row.model_dump(mode="json"), before)

    def test_empty_native_arm_collection_does_not_invent_an_origin_or_raise_an_arm_hold(self):
        self.assertEqual(_mapper()._get_study_arms(SimpleNamespace(uid="Study_synthetic")), [])
