"""Native arm export requires explicit origin and the selected DDF package."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
import json
from urllib.parse import quote, unquote
import unittest
from unittest.mock import patch

from fastapi import Request
from fastapi.encoders import jsonable_encoder
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
from clinical_mdr_api.services.ddf.usdm_mapping_context import MappingContext
from common.exceptions import ValidationException
from common.models.error import ErrorResponse


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
    def _read_native_extension(self, root):
        """Decode the public typed extension and require an unambiguous full tree."""
        identities = set()

        def require_identity(node, expected_type):
            self.assertEqual(node.instanceType, expected_type)
            self.assertIsInstance(node.id, str)
            self.assertTrue(node.id)
            self.assertNotIn(node.id, identities)
            identities.add(node.id)

        def read(node):
            require_identity(node, "ExtensionAttribute")
            values = {
                key: getattr(node, key) for key in type(node).model_fields
                if key.startswith("value") and getattr(node, key) is not None
            }
            children = node.extensionAttributes
            if children:
                self.assertEqual(values, {})
                shapes = [child for child in children if child.url ==
                          "https://openstudybuilder.org/usdm/extensions/native-value-shape"]
                self.assertEqual(len(shapes), 1)
                shape_node = shapes[0]
                shape = read(shape_node)
                self.assertIn(shape, ("null", "array", "object"))
                payload = [child for child in children if child is not shape_node]
                if shape == "null":
                    self.assertEqual(payload, [])
                    return None
                entries = []
                for child in payload:
                    prefix = node.url + "/"
                    self.assertTrue(child.url.startswith(prefix))
                    encoded = child.url[len(prefix):]
                    self.assertTrue(encoded)
                    self.assertNotIn("/", encoded)
                    key = unquote(encoded)
                    self.assertEqual(quote(key, safe=""), encoded)
                    entries.append((key, read(child)))
                keys = [key for key, _value in entries]
                self.assertEqual(len(set(keys)), len(keys))
                if shape == "array":
                    self.assertEqual(keys, [str(index) for index in range(len(entries))])
                    return [value for _key, value in entries]
                return dict(entries)
            self.assertEqual(len(values), 1)
            key, value = next(iter(values.items()))
            if key == "valueQuantity":
                require_identity(value, "Quantity")
                self.assertIsNone(value.unit)
                self.assertEqual(value.extensionAttributes, [])
                self.assertIs(type(value.value), float)
                return value.value
            primitive_types = {"valueString": str, "valueBoolean": bool, "valueInteger": int}
            self.assertIn(key, primitive_types)
            self.assertIs(type(value), primitive_types[key])
            return value

        self.assertEqual(root.url, "https://openstudybuilder.org/usdm/extensions/native/studyArm")
        return read(root)

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
                row.label = "Explicit cohort label Ω" if code == "C188864" else None
                row.code = ""
                row.randomization_group = ""
                row.number_of_subjects = 0
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
                self.assertEqual(result[0].label, before["label"] if before["label"] is not None else before["short_name"])
                self.assertEqual(len(result[0].extensionAttributes), 1)
                source = self._read_native_extension(result[0].extensionAttributes[0])
                # JSON comparison also distinguishes false from zero and keeps
                # the complete source keys, null/empty values and collection order.
                self.assertEqual(
                    json.dumps(source, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(before, ensure_ascii=False, separators=(",", ":")),
                )
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

    def test_authority_error_preserves_the_explicit_message_in_the_api_error_dto(self):
        request = Request({
            "type": "http", "method": "GET", "scheme": "http",
            "server": ("testserver", 80), "path": "/test-usdm-export",
            "query_string": b"", "headers": [],
        })
        for message in (
            "USDM_ARM_TYPE_CT_PIN_REQUIRED: study-arms/Arm_synthetic",
            "  USDM_SOURCE_AUTHORITY_REQUIRED: café / Ω / e\u0301\nPreserve source text: UID None.  ",
        ):
            with self.subTest(message=message):
                error = USDMMappingAuthorityRequired(message)
                self.assertEqual(str(error), message)
                self.assertEqual(error.msg, message)
                self.assertEqual(error.status_code, 422)
                body = jsonable_encoder(ErrorResponse(request, error))
                self.assertEqual(body["message"], message)
                self.assertEqual(body["type"], "USDMMappingAuthorityRequired")
                self.assertEqual(body["path"], "http://testserver/test-usdm-export")
                self.assertEqual(body["method"], "GET")
                self.assertEqual(body["details"], [])

    def _assert_partial_arm_type_refused(self, missing_attribute):
        values = {
            "id": "arm-type", "code": "C174266", "codeSystem": "CDISC",
            "codeSystemVersion": "sdtmct-2024-09-27", "decode": "Control Arm",
            "instanceType": "Code",
        }
        del values[missing_attribute]
        # Exercise the actual preserved WIP builder. The separately publishable
        # c26 regression uses Code.model_construct and imports no WIP module.
        context = MappingContext(allow_incomplete=True)
        partial_code = context.build(Code, "study-arms/Arm_synthetic/type", **values)
        self.assertEqual([
            (issue["code"], issue["sourcePath"], issue["targetPath"])
            for issue in context.issues
        ], [("USDM_REQUIRED_SOURCE_VALUE_MISSING", "study-arms/Arm_synthetic/type", "Code/" + missing_attribute)])
        self.assertFalse(hasattr(partial_code, missing_attribute))
        before_code = partial_code.model_dump(mode="json")
        row = _native_arm(
            origin_uid="C188864_HISTORICAL",
            origin_description="  Explicit historical source.\nKeep this wording.  ",
        )
        row.arm_type = SimpleCodelistTermModel(
            term_uid="C174266", term_name="Control Arm",
        )
        before_arm = row.model_dump(mode="json")
        mapper = _mapper()
        mapper._get_osb_study_arms = lambda *_args, **_kwargs: [row]
        mapper._get_osb_study_standard_versions = lambda **_kwargs: [
            SimpleNamespace(ct_package=SimpleNamespace(
                catalogue_name="DDF CT", uid="ddfct-2024-09-27",
                effective_date=date(2024, 9, 27),
            ))
        ]
        mapper._load_selected_ct_packages("Study_synthetic")
        with patch.object(
            mapper, "get_ct_package_term_as_usdm_code", return_value=partial_code,
        ) as type_lookup, patch.object(StudyArmOriginRepository, "package_code", return_value={
            "code": "C188864", "decode": "Historical Data", "code_system": "CDISC",
            "code_system_version": "ddfct-2024-09-27",
        }) as origin_lookup:
            with self.assertRaisesRegex(
                USDMMappingAuthorityRequired,
                "^USDM_ARM_TYPE_CT_PIN_REQUIRED: study-arms/Arm_synthetic$",
            ) as error:
                mapper._get_study_arms(SimpleNamespace(uid="Study_synthetic"))
        self.assertEqual(error.exception.status_code, 422)
        type_lookup.assert_called_once_with("C174266")
        origin_lookup.assert_called_once_with(
            "C188864_HISTORICAL", "ddfct-2024-09-27", "2024-09-27",
        )
        self.assertEqual(partial_code.model_dump(mode="json"), before_code)
        self.assertEqual(row.model_dump(mode="json"), before_arm)

    def test_absent_arm_type_code_refuses_a_partial_native_code(self):
        self._assert_partial_arm_type_refused("code")

    def test_absent_arm_type_version_refuses_a_partial_native_code(self):
        self._assert_partial_arm_type_refused("codeSystemVersion")

    def test_selected_package_is_read_from_the_requested_study_version(self):
        mapper = _mapper()
        calls = []
        source_metadata = {"enabled": False, "optional": None, "empty": "", "rows": [0, "", None]}
        source_package = SimpleNamespace(
            catalogue_name="DDF CT", uid="ddfct-2024-09-27",
            effective_date=date(2024, 9, 27), source_metadata=source_metadata,
        )
        def selected(**kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(ct_package=source_package)]
        mapper._get_osb_study_standard_versions = selected
        mapper._study_value_version = "3"
        mapper._load_selected_ct_packages("Study_synthetic")
        self.assertEqual(calls, [{"study_uid": "Study_synthetic", "study_value_version": "3", "page_size": 0}])
        selected_package = mapper._ct_packages["DDF CT"]
        self.assertEqual(selected_package["uid"], "ddfct-2024-09-27")
        self.assertEqual(selected_package["effective_date"], "2024-09-27")
        self.assertEqual(selected_package["source"], {
            "catalogue_name": "DDF CT", "uid": "ddfct-2024-09-27",
            "effective_date": "2024-09-27",
            "source_metadata": {"enabled": False, "optional": None, "empty": "", "rows": [0, "", None]},
        })
        self.assertEqual(vars(source_package), {
            "catalogue_name": "DDF CT", "uid": "ddfct-2024-09-27",
            "effective_date": date(2024, 9, 27),
            "source_metadata": {"enabled": False, "optional": None, "empty": "", "rows": [0, "", None]},
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
