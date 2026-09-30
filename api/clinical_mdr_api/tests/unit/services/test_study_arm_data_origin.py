"""Native arm origin survives editing and history without value invention."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from clinical_mdr_api.domain_repositories.study_selections import study_arm_repository
from clinical_mdr_api.domain_repositories.study_selections.study_arm_origin_repository import (
    StudyArmOriginRepository,
)
from clinical_mdr_api.domain_repositories.study_selections.study_arm_repository import (
    SelectionHistoryArm, StudySelectionArmRepository,
)
from clinical_mdr_api.domains.study_selections.study_selection_arm import StudySelectionArmVO
from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionArm, StudySelectionArmBatchInput, StudySelectionArmBatchUpdateInput,
    StudySelectionArmCreateInput, StudySelectionArmInput,
    StudySelectionArmWithConnectedBranchArms,
)
from clinical_mdr_api.services.studies.study_arm_selection import StudyArmSelectionService
from clinical_mdr_api.services.user_info import UserInfoService
from common.exceptions import ValidationException


UID = "C188864_HISTORICAL"
DESCRIPTION = "  Source cohort predates the trial.\nSource wording retained.  "
AT = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
RAW_ORIGIN_DESCRIPTIONS = (
    DESCRIPTION,
    "\tSource first line.\r\nSecond line with α and β.\r\n ",
    "\u00a0Source with e\u0301 and é, \U0001f9ea and <literal evidence>.\u202f",
    "\u2003  Source:\tunits mg/mL; preserve \"quotes\" & codepoints.  \u2003",
)


def _vo(**changes):
    args = dict(
        study_uid="Study_synthetic", study_selection_uid="Arm_synthetic",
        name="Synthetic arm", short_name="SA", author_id="reviewer-A", start_date=AT,
        data_origin_type_uid=UID, data_origin_description=DESCRIPTION,
    )
    args.update(changes)
    with patch.object(UserInfoService, "get_author_username_from_id", return_value="reviewer-A"):
        return StudySelectionArmVO.from_input_values(**args)


class StudyArmOriginMutationTests(unittest.TestCase):
    def _patch(self, current, **changes):
        with patch.object(UserInfoService, "get_author_username_from_id", return_value="reviewer-B"):
            return StudyArmSelectionService._patch_prepare_new_study_arm(
                SimpleNamespace(author="reviewer-B"), StudySelectionArmInput(**changes), current
            )

    def test_unrelated_patch_preserves_origin_uid_and_verbatim_description(self):
        current = _vo()
        result = self._patch(current, name="Revised arm name")
        self.assertEqual(result.data_origin_type_uid, UID)
        self.assertEqual(result.data_origin_description, DESCRIPTION)
        self.assertEqual(result.author_id, "reviewer-B")
        self.assertEqual(current.name, "Synthetic arm")

    def test_explicit_null_pair_clears_both_fields(self):
        result = self._patch(_vo(), data_origin_type_uid=None, data_origin_description=None)
        self.assertIsNone(result.data_origin_type_uid)
        self.assertIsNone(result.data_origin_description)

    def test_one_sided_clear_is_rejected_after_patch_merge(self):
        for change in ({"data_origin_type_uid": None}, {"data_origin_description": None}):
            with self.subTest(change=change):
                with self.assertRaisesRegex(ValidationException, "ORIGIN_PAIR_REQUIRED"):
                    self._patch(_vo(), **change)

    def test_origin_description_can_be_edited_without_replacing_the_term(self):
        result = self._patch(_vo(), data_origin_description="Revised source rationale.")
        self.assertEqual(result.data_origin_type_uid, UID)
        self.assertEqual(result.data_origin_description, "Revised source rationale.")

    def test_unknown_origin_remains_unknown_after_an_unrelated_edit(self):
        current = _vo(data_origin_type_uid=None, data_origin_description=None)
        result = self._patch(current, description="Data Generated Within Study")
        self.assertIsNone(result.data_origin_type_uid)
        self.assertIsNone(result.data_origin_description)

    def test_new_partial_or_blank_origin_fails_before_allocating_a_uid(self):
        for uid, description in ((UID, None), (None, DESCRIPTION), ("", DESCRIPTION), (UID, " \n")):
            with self.subTest(uid=uid, description=description):
                allocate = Mock(return_value="unused")
                with self.assertRaisesRegex(ValidationException, "ORIGIN_PAIR_REQUIRED"):
                    StudySelectionArmVO.from_input_values(
                        author_id="reviewer-A", data_origin_type_uid=uid,
                        data_origin_description=description, generate_uid_callback=allocate,
                    )
                allocate.assert_not_called()

    def test_current_and_connected_response_factories_preserve_raw_origin(self):
        args = dict(
            study_uid="Study_synthetic", selection=_vo(), order=1,
            find_codelist_term_arm_type=lambda *_args, **_kwargs: None,
            terms_at_specific_datetime=None,
        )
        with patch.object(UserInfoService, "get_author_username_from_id", return_value="reviewer-A"):
            current = StudySelectionArm.from_study_selection_arm_ar_and_order(**args)
            connected = StudySelectionArmWithConnectedBranchArms.from_study_selection_arm_ar__order__connected_branch_arms(
                **args, find_multiple_connected_branch_arm=lambda **_kwargs: [],
                study_value_version="2",
            )
        for row in (current, connected):
            self.assertEqual(row.data_origin_type_uid, UID)
            self.assertEqual(row.data_origin_description, DESCRIPTION)
        self.assertEqual(connected.study_version, "2")

    def test_historical_response_keeps_the_recorded_pair_without_current_term_lookup(self):
        history = SelectionHistoryArm(
            study_selection_uid="Arm_synthetic", study_uid="Study_synthetic",
            arm_name="Old arm name", arm_short_name="SA", arm_label=None, arm_code=None,
            arm_description=None, arm_randomization_group=None, arm_number_of_subjects=None,
            arm_type=None, start_date=AT, author_id="reviewer-A", change_type="Edit",
            end_date=None, order=1, status=None, accepted_version=False,
            merge_branch_for_this_arm_for_sdtm_adam=False,
            data_origin_type_uid=UID, data_origin_description=DESCRIPTION,
        )
        lookup = Mock(side_effect=AssertionError("Origin history must retain its recorded UID."))
        with patch.object(UserInfoService, "get_author_username_from_id", return_value="reviewer-A"):
            row = StudySelectionArm.from_study_selection_history(
                history, "Study_synthetic", lookup, effective_date=AT
            )
        lookup.assert_not_called()
        self.assertEqual(row.data_origin_type_uid, UID)
        self.assertEqual(row.data_origin_description, DESCRIPTION)

    def test_invalid_new_membership_cannot_create_an_arm_node(self):
        with patch.object(StudyArmOriginRepository, "get_term", side_effect=ValidationException(
            msg="STUDY_ARM_DATA_ORIGIN_TERM_VERSION_REQUIRED"
        )), patch.object(study_arm_repository, "StudyArm") as node:
            with self.assertRaisesRegex(ValidationException, "TERM_VERSION_REQUIRED"):
                StudySelectionArmRepository._add_new_selection(
                    Mock(), Mock(), 1, _vo(), Mock(), "reviewer-A"
                )
        node.assert_not_called()

    def test_unchanged_origin_reuses_exact_context_when_term_is_later_retired(self):
        context = SimpleNamespace(
            has_selected_term=Mock(single=Mock(return_value=SimpleNamespace(uid=UID))),
            has_selected_codelist=Mock(single=Mock(return_value=SimpleNamespace(uid="C188727"))),
        )
        before = SimpleNamespace(data_origin_type=Mock(single=Mock(return_value=context)))
        with patch.object(StudyArmOriginRepository, "get_term") as lookup, \
                patch.object(study_arm_repository, "StudyArm") as node, \
                patch.object(study_arm_repository, "_manage_versioning_with_relations"):
            saved = node.return_value.save.return_value
            StudySelectionArmRepository._add_new_selection(
                Mock(), Mock(), 1, _vo(), Mock(), "reviewer-B", before_node=before
            )
        lookup.assert_not_called()
        saved.data_origin_type.connect.assert_called_once_with(context)
        self.assertEqual(node.call_args.kwargs["data_origin_description"], DESCRIPTION)

    def test_wrong_codelist_context_is_never_reused_for_an_unchanged_term_uid(self):
        context = SimpleNamespace(
            has_selected_term=Mock(single=Mock(return_value=SimpleNamespace(uid=UID))),
            has_selected_codelist=Mock(single=Mock(return_value=SimpleNamespace(uid="WRONG"))),
        )
        before = SimpleNamespace(data_origin_type=Mock(single=Mock(return_value=context)))
        with patch.object(study_arm_repository, "StudyArm") as node:
            with self.assertRaisesRegex(ValidationException, "CONTEXT_SCOPE_INVALID"):
                StudySelectionArmRepository._add_new_selection(
                    Mock(), Mock(), 1, _vo(), Mock(), "reviewer-B", before_node=before
                )
        node.assert_not_called()


class StudyArmOriginRawInputTests(unittest.TestCase):
    def test_create_patch_and_json_dtos_preserve_every_source_codepoint(self):
        for model in (StudySelectionArmCreateInput, StudySelectionArmInput,
                      StudySelectionArmBatchUpdateInput):
            for description in RAW_ORIGIN_DESCRIPTIONS:
                with self.subTest(model=model.__name__, description=description):
                    raw = {"data_origin_type_uid": UID,
                           "data_origin_description": description}
                    if model is StudySelectionArmBatchUpdateInput:
                        raw["arm_uid"] = "Arm_synthetic"
                    for request in (model(**raw), model.model_validate_json(json.dumps(raw))):
                        self.assertEqual(request.data_origin_description, description)
                        self.assertEqual(request.model_dump(mode="json")["data_origin_description"],
                                         description)
                        self.assertEqual(json.loads(request.model_dump_json())[
                            "data_origin_description"], description)
                        current = _vo(data_origin_description=request.data_origin_description)
                        with patch.object(UserInfoService, "get_author_username_from_id",
                                          return_value="reviewer-A"):
                            response = StudySelectionArm.from_study_selection_arm_ar_and_order(
                                study_uid="Study_synthetic", selection=current, order=1,
                                find_codelist_term_arm_type=lambda *_args, **_kwargs: None,
                                terms_at_specific_datetime=None,
                            )
                        self.assertEqual(response.data_origin_description, description)

    def test_absent_null_empty_and_blank_remain_distinct_at_the_input_boundary(self):
        for model in (StudySelectionArmCreateInput, StudySelectionArmInput):
            absent = model()
            self.assertNotIn("data_origin_description", absent.model_fields_set)
            self.assertNotIn("data_origin_description", absent.model_dump(exclude_unset=True))
            for value in (None, "", " \t\r\n\u00a0 "):
                with self.subTest(model=model.__name__, value=value):
                    request = model(data_origin_description=value)
                    self.assertIn("data_origin_description", request.model_fields_set)
                    self.assertEqual(request.model_dump(exclude_unset=True),
                                     {"data_origin_description": value})
                    self.assertEqual(request.data_origin_description, value)

    def test_invalid_origin_value_types_still_fail_the_actual_dto_schema(self):
        for value in (True, 1, 1.5, [], [" source "], {"source": " value "}):
            for model in (StudySelectionArmCreateInput, StudySelectionArmInput):
                with self.subTest(value=value, model=model.__name__):
                    with self.assertRaises(ValidationError) as error:
                        model(data_origin_type_uid=UID, data_origin_description=value)
                    self.assertTrue(any(
                        "data_origin_description" in item["loc"]
                        for item in error.exception.errors()
                    ))
            with self.subTest(value=value, model="batch"):
                with self.assertRaises(ValidationError) as error:
                    StudySelectionArmBatchInput(
                        method="PATCH", content={"arm_uid": "Arm_synthetic",
                                                "data_origin_description": value})
                self.assertTrue(any(
                    "data_origin_description" in item["loc"]
                    for item in error.exception.errors()
                ))

    def test_other_arm_fields_keep_the_existing_native_string_validation(self):
        for model in (StudySelectionArmCreateInput, StudySelectionArmInput):
            request = model(
                name="  Ordinary name  ", short_name="  SA  ",
                description="  Ordinary description  ",
                data_origin_type_uid=UID, data_origin_description=DESCRIPTION,
            )
            self.assertEqual((request.name, request.short_name, request.description),
                             ("Ordinary name", "SA", "Ordinary description"))
            self.assertEqual(request.data_origin_description, DESCRIPTION)
        batch = StudySelectionArmBatchInput(
            method="PATCH", content={
                "arm_uid": "Arm_synthetic", "name": "  Ordinary name  ",
                "description": "  Ordinary description  ",
                "data_origin_type_uid": UID, "data_origin_description": DESCRIPTION,
            })
        self.assertEqual(batch.content.name, "Ordinary name")
        self.assertEqual(batch.content.description, "Ordinary description")
        self.assertEqual(batch.content.data_origin_description, DESCRIPTION)

    def test_raw_blank_or_partial_origin_is_refused_before_identity_allocation(self):
        for uid, value in (
            (UID, None), (None, DESCRIPTION),
            (UID, ""), (UID, " \t\r\n\u00a0 "), (None, ""), (None, " \t "),
        ):
            with self.subTest(uid=uid, value=value):
                request = StudySelectionArmCreateInput(
                    data_origin_type_uid=uid, data_origin_description=value)
                before = request.model_dump(mode="json")
                allocate = Mock(return_value="must-not-be-used")
                with self.assertRaisesRegex(ValidationException, "ORIGIN_PAIR_REQUIRED"):
                    StudySelectionArmVO.from_input_values(
                        author_id="reviewer-A",
                        data_origin_type_uid=request.data_origin_type_uid,
                        data_origin_description=request.data_origin_description,
                        generate_uid_callback=allocate,
                    )
                allocate.assert_not_called()
                self.assertEqual(request.model_dump(mode="json"), before)
                self.assertEqual(request.data_origin_description, value)

    def test_ordinary_patch_retains_current_and_new_descriptions_verbatim(self):
        for description in RAW_ORIGIN_DESCRIPTIONS:
            with self.subTest(description=description):
                current = _vo(data_origin_description=description)
                for change in ({"name": "Changed arm name"},
                               {"data_origin_description": description + "\r\n  "}):
                    request = StudySelectionArmInput(**change)
                    with patch.object(UserInfoService, "get_author_username_from_id",
                                      return_value="reviewer-B"):
                        result = StudyArmSelectionService._patch_prepare_new_study_arm(
                            SimpleNamespace(author="reviewer-B"), request, current)
                    self.assertEqual(result.data_origin_description,
                                     change.get("data_origin_description", description))
                    self.assertEqual(result.data_origin_type_uid, UID)
                    self.assertEqual(current.data_origin_description, description)
                    self.assertEqual(current.name, "Synthetic arm")

    def test_batch_create_and_patch_do_not_recursively_strip_raw_content(self):
        for method, content_type in (("POST", StudySelectionArmCreateInput),
                                     ("PATCH", StudySelectionArmBatchUpdateInput)):
            for description in RAW_ORIGIN_DESCRIPTIONS:
                with self.subTest(method=method, description=description):
                    content = {"name": "  Ordinary name  ", "data_origin_type_uid": UID,
                               "data_origin_description": description}
                    if method == "PATCH":
                        content["arm_uid"] = "Arm_synthetic"
                    raw = {"method": method, "content": content}
                    before = json.dumps(raw, ensure_ascii=False)
                    for request in (
                        StudySelectionArmBatchInput(**raw),
                        StudySelectionArmBatchInput.model_validate_json(before),
                    ):
                        self.assertIsInstance(request.content, content_type)
                        self.assertEqual(request.content.name, "Ordinary name")
                        self.assertEqual(request.content.data_origin_description, description)
                        self.assertEqual(request.model_dump(mode="json")["content"][
                            "data_origin_description"], description)
                    self.assertEqual(json.dumps(raw, ensure_ascii=False), before)

    def test_batch_absence_clear_and_blank_are_not_conflated_before_patch_validation(self):
        for value, present in ((None, False), (None, True), ("", True), (" \t\u00a0 ", True)):
            with self.subTest(value=value, present=present):
                content = {"arm_uid": "Arm_synthetic", "name": "Changed name"}
                if present:
                    content["data_origin_description"] = value
                batch = StudySelectionArmBatchInput(method="PATCH", content=content)
                self.assertEqual("data_origin_description" in batch.content.model_fields_set,
                                 present)
                self.assertEqual(batch.content.data_origin_description, value)
                current = _vo()
                with patch.object(UserInfoService, "get_author_username_from_id",
                                  return_value="reviewer-B"):
                    if present:
                        with self.assertRaisesRegex(ValidationException, "ORIGIN_PAIR_REQUIRED"):
                            StudyArmSelectionService._patch_prepare_new_study_arm(
                                SimpleNamespace(author="reviewer-B"), batch.content, current)
                    else:
                        result = StudyArmSelectionService._patch_prepare_new_study_arm(
                            SimpleNamespace(author="reviewer-B"), batch.content, current)
                        self.assertEqual(result.data_origin_description, DESCRIPTION)
        cleared = StudySelectionArmBatchInput(method="PATCH", content={
            "arm_uid": "Arm_synthetic", "data_origin_type_uid": None,
            "data_origin_description": None,
        })
        with patch.object(UserInfoService, "get_author_username_from_id", return_value="reviewer-B"):
            result = StudyArmSelectionService._patch_prepare_new_study_arm(
                SimpleNamespace(author="reviewer-B"), cleared.content, _vo())
        self.assertIsNone(result.data_origin_type_uid)
        self.assertIsNone(result.data_origin_description)


class StudyArmOriginTerminologyTests(unittest.TestCase):
    def test_native_readback_requires_one_complete_origin_context(self):
        valid = {"term_uid": UID, "codelist_uid": "C188727"}
        self.assertEqual(StudyArmOriginRepository.selected_uid([valid], DESCRIPTION), UID)
        self.assertIsNone(StudyArmOriginRepository.selected_uid([], None))
        for rows, description in (
            ([valid, valid], DESCRIPTION),
            ([{"term_uid": UID, "codelist_uid": "OTHER"}], DESCRIPTION),
            ([{"term_uid": None, "codelist_uid": "C188727"}], DESCRIPTION),
            ([], DESCRIPTION),
            ([valid], None),
        ):
            with self.subTest(rows=rows, description=description):
                with self.assertRaises(ValidationException):
                    StudyArmOriginRepository.selected_uid(rows, description)

    def test_exact_native_uid_resolves_to_publisher_code_and_package_decode(self):
        columns = ["code", "code_system", "code_system_version", "decode"]
        with patch("clinical_mdr_api.domain_repositories.study_selections.study_arm_origin_repository.db.cypher_query",
                   return_value=([["C188864", "CDISC", "ddfct-2024-09-27", "Historical Data"]], columns)) as query:
            result = StudyArmOriginRepository.package_code(UID, "ddfct-2024-09-27", "2024-09-27")
        self.assertEqual(result["code"], "C188864")
        self.assertEqual(result["decode"], "Historical Data")
        params = query.call_args.args[1]
        self.assertEqual(params["term_uid"], UID)
        self.assertEqual(params["codelist_uid"], "C188727")
        self.assertEqual(params["catalogue"], "DDF CT")
        self.assertEqual(params["package_uid"], "ddfct-2024-09-27")

    def test_missing_ambiguous_and_incomplete_package_membership_refuse(self):
        columns = ["code", "code_system", "code_system_version", "decode"]
        good = ["C188864", "CDISC", "ddfct-2024-09-27", "Historical Data"]
        for rows in ([], [good, good], [[None, *good[1:]]], [[*good[:3], ""]]):
            with self.subTest(rows=rows):
                with patch("clinical_mdr_api.domain_repositories.study_selections.study_arm_origin_repository.db.cypher_query",
                           return_value=(rows, columns)):
                    with self.assertRaisesRegex(ValidationException, "ORIGIN_CT_PIN"):
                        StudyArmOriginRepository.package_code(UID, "ddfct-2024-09-27", "2024-09-27")

    def test_invalid_package_date_cannot_reach_the_database(self):
        for value in ("", "2024-02-31", "2024-09-27junk", "20240927"):
            with self.subTest(value=value):
                with patch("clinical_mdr_api.domain_repositories.study_selections.study_arm_origin_repository.db.cypher_query") as query:
                    with self.assertRaises(ValidationException):
                        StudyArmOriginRepository.package_code(UID, "ddfct-2024-09-27", value)
                query.assert_not_called()

    def test_missing_or_multiple_current_memberships_never_retry_latest(self):
        for rows in ([], [["duplicate"], ["duplicate"]]):
            with self.subTest(rows=rows):
                with patch("clinical_mdr_api.domain_repositories.study_selections.study_arm_origin_repository.db.cypher_query",
                           return_value=(rows, ["term_uid"])) as query:
                    with self.assertRaisesRegex(ValidationException, "ORIGIN_TERM_VERSION_REQUIRED"):
                        StudyArmOriginRepository.get_term(UID, at_specific_date_time=AT)
                self.assertEqual(query.call_count, 1)
