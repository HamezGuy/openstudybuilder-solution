"""Offline public StudyService/DTO tests; real Neo4j race proof is separate."""

import csv
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from clinical_mdr_api.domains.study_definition_aggregates.root import (
    _DEF_INITIAL_HIGH_LEVEL_STUDY_DESIGN,
    _DEF_INITIAL_STUDY_POPULATION,
)
from clinical_mdr_api.models.study_selections.null_adjudication import (
    StudyNullAdjudicationRequest,
)
from clinical_mdr_api.models.study_selections.study import StudyPatchRequestJsonModel
from clinical_mdr_api.services.integrations.canonical_json import canonical_json
from clinical_mdr_api.services.studies.null_adjudication import (
    NullAdjudicationConflict,
    NullAdjudicationInvalid,
    metadata_identity,
    null_adjudication_capability,
    null_adjudication_receipt,
)
from clinical_mdr_api.tests.fixtures.null_adjudication import (
    COMPANION_PATH,
    STUDY_UID,
    VALUE_PATH,
    metadata_without_version,
    native_harness,
    request_payload,
)


def test_native_allowlist_matches_all_44_configured_pairs_and_actual_api_alias():
    config = (
        Path(__file__).resolve().parents[5]
        / "studybuilder-import/datafiles/configuration/study_fields_configuration.csv"
    )
    with config.open(encoding="utf-8") as stream:
        configured = [
            row for row in csv.DictReader(stream) if row["study_field_null_value_code"]
        ]
    expected = {}
    for row in configured:
        section = row["study_field_grouping"].replace(
            "id_metadata", "identification_metadata", 1
        )
        companion = row["study_field_null_value_code"]
        if companion == "trial_intent_type_null_value_code":
            companion = "trial_intent_types_null_value_code"
        expected[f'{section}.{row["study_field_name_api"]}'] = f"{section}.{companion}"
    capability = null_adjudication_capability(STUDY_UID)
    assert len(expected) == len(capability.fields) == 44
    assert {row.value_path: row.companion_path for row in capability.fields} == expected
    assert capability.atomic_preconditions is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.pop("contract_version"),
        lambda data: data.update(contract_version="unsupported"),
        lambda data: data.update(current_metadata={}),
        lambda data: data.update(study_parent_part_uid="OTHER"),
        lambda data: data.update(dry=True),
        lambda data: data["adjudications"][0].pop("expected_value"),
        lambda data: data["adjudications"][0].pop("expected_null_companion"),
        lambda data: data["adjudications"][0].update(expected_value=None),
        lambda data: data["adjudications"][0].update(expected_value={"present": True}),
        lambda data: data["adjudications"][0].update(
            expected_value={"present": False, "value": None}
        ),
        lambda data: data["adjudications"][0].update(
            expected_value={"present": 1, "value": None}
        ),
        lambda data: data["adjudications"][0].update(null_term_uid=" CT_UNK "),
        lambda data: data["adjudications"][0].update(extra="discard me"),
        lambda data: data["adjudications"].append(deepcopy(data["adjudications"][0])),
        lambda data: data.update(adjudications=[]),
    ],
)
def test_guard_dto_requires_exact_closed_observations(mutation):
    data = request_payload()
    mutation(data)
    with pytest.raises(ValidationError):
        StudyNullAdjudicationRequest.model_validate(data)


def test_guard_observations_do_not_trim_coerce_or_reorder():
    data = request_payload(
        expected_value={"present": True, "value": [" b ", False, 0, None, ""]}
    )
    parsed = StudyNullAdjudicationRequest.model_validate(data)
    assert parsed.model_dump(mode="json") == data
    assert canonical_json(metadata_identity(False)) != canonical_json(
        metadata_identity(0)
    )
    assert canonical_json(metadata_identity([False, 0])) != canonical_json(
        metadata_identity([0, False])
    )
    assert metadata_identity(
        {"term_uid": "CT_NA", "sponsor_preferred_name": "display A"}
    ) == metadata_identity({"term_uid": "CT_NA", "sponsor_preferred_name": "display B"})


def test_guarded_public_patch_preserves_siblings_and_returns_actual_typed_metadata():
    design = replace(
        _DEF_INITIAL_HIGH_LEVEL_STUDY_DESIGN,
        study_stop_rules="new sibling",
        trial_type_codes=["CT_B", "CT_A", "CT_B"],
    )
    with native_harness(design=design) as h:
        before = metadata_without_version(h.current())
        h.guarded(request_payload())
        after = metadata_without_version(h.current())
        before["high_level_study_design"][
            "is_extension_trial_null_value_code"
        ] = "CT_UNK"
        assert after == before
        assert [call for call in h.repository.calls if call[0] == "read"] == [
            ("read", STUDY_UID, True)
        ]
        result = h.readback()
        assert (
            result.current_metadata.high_level_study_design.is_extension_trial_null_value_code.term_uid
            == "CT_UNK"
        )
        assert [
            term.term_uid
            for term in result.current_metadata.high_level_study_design.trial_type_codes
        ] == ["CT_B", "CT_A", "CT_B"]
        assert [call for call in h.repository.calls if call[0] == "read"] == [
            ("read", STUDY_UID, True),
            ("read", STUDY_UID, False),
        ]
        assert [call for call in h.repository.calls if call[0] == "save"] == [
            ("save", STUDY_UID)
        ]
        assert not any(call[0] == "parent" for call in h.repository.calls)


def test_guarded_patch_preserves_parent_from_locked_aggregate_without_reparenting():
    with native_harness(parent=True) as h:
        before = metadata_without_version(h.current())
        h.guarded(request_payload())
        before["high_level_study_design"][
            "is_extension_trial_null_value_code"
        ] = "CT_UNK"
        assert metadata_without_version(h.current()) == before
        assert h.current().study_parent_part_uid == "OFFLINE_PARENT"
        assert not any(call[0] == "parent" for call in h.repository.calls)
        assert [
            call for call in h.repository.calls if call[:2] == ("read", STUDY_UID)
        ] == [("read", STUDY_UID, True)]


def test_ordinary_patch_still_accepts_existing_contract_without_guard():
    with native_harness() as h:
        h.ordinary({"high_level_study_design": {"is_extension_trial": False}})
        result = h.readback()
        assert (
            result.current_metadata.high_level_study_design.is_extension_trial is False
        )
        assert (
            h.current().current_metadata.high_level_study_design.is_extension_trial
            is False
        )
        assert ("parent", STUDY_UID, None) in h.repository.calls


def test_concurrent_ordinary_null_patch_survives_guarded_request_recheck():
    with native_harness() as h:
        h.repository.on_locked_read = lambda: h.ordinary(
            {
                "high_level_study_design": {
                    "is_extension_trial_null_value_code": {"term_uid": "CT_NA"}
                },
            }
        )
        with pytest.raises(
            NullAdjudicationConflict, match="PRECONDITION_FAILED"
        ) as error:
            h.guarded(request_payload())
        assert error.value.status_code == 412
        assert (
            h.current().current_metadata.high_level_study_design.is_extension_trial_null_value_code
            == "CT_NA"
        )
        assert len([call for call in h.repository.calls if call[0] == "save"]) == 1


def test_batch_late_conflict_saves_none_of_the_earlier_rows():
    design = replace(
        _DEF_INITIAL_HIGH_LEVEL_STUDY_DESIGN, is_adaptive_design_null_value_code="CT_NA"
    )
    with native_harness(design=design) as h:
        data = request_payload()
        data["adjudications"].append(
            {
                **data["adjudications"][0],
                "value_path": "high_level_study_design.is_adaptive_design",
                "companion_path": "high_level_study_design.is_adaptive_design_null_value_code",
            }
        )
        before = metadata_without_version(h.current())
        with pytest.raises(NullAdjudicationConflict):
            h.guarded(data)
        assert metadata_without_version(h.current()) == before
        assert not any(call[0] == "save" for call in h.repository.calls)


@pytest.mark.parametrize("live,expected", [(False, None), (False, 0), (False, False)])
def test_live_false_is_never_coerced_to_empty_or_numeric_zero(live, expected):
    with native_harness(
        design=replace(_DEF_INITIAL_HIGH_LEVEL_STUDY_DESIGN, is_extension_trial=live)
    ) as h:
        with pytest.raises(NullAdjudicationConflict):
            h.guarded(
                request_payload(expected_value={"present": True, "value": expected})
            )
        assert (
            h.current().current_metadata.high_level_study_design.is_extension_trial
            is False
        )
        assert not any(call[0] == "save" for call in h.repository.calls)


@pytest.mark.parametrize("expected", [False, 0, None])
def test_live_zero_is_never_coerced_to_empty_or_false(expected):
    with native_harness(
        population=replace(_DEF_INITIAL_STUDY_POPULATION, number_of_expected_subjects=0)
    ) as h:
        with pytest.raises(NullAdjudicationConflict):
            h.guarded(
                request_payload(
                    value_path="study_population.number_of_expected_subjects",
                    companion_path="study_population.number_of_expected_subjects_null_value_code",
                    expected_value={"present": True, "value": expected},
                )
            )
        assert (
            h.current().current_metadata.study_population.number_of_expected_subjects
            == 0
        )
        assert not any(call[0] == "save" for call in h.repository.calls)


def test_absent_observation_does_not_match_explicit_native_null():
    with native_harness() as h:
        with pytest.raises(NullAdjudicationConflict, match="PRECONDITION_FAILED"):
            h.guarded(request_payload(expected_value={"present": False}))
        assert not any(call[0] == "save" for call in h.repository.calls)


def test_companion_labels_do_not_change_identity_but_existing_reason_still_cannot_be_replaced():
    with native_harness(
        design=replace(
            _DEF_INITIAL_HIGH_LEVEL_STUDY_DESIGN,
            is_extension_trial_null_value_code="CT_NA",
        )
    ) as h:
        with pytest.raises(NullAdjudicationConflict, match="SLOT_NOT_EMPTY"):
            h.guarded(
                request_payload(
                    expected_null_companion={
                        "present": True,
                        "value": {
                            "term_uid": "CT_NA",
                            "sponsor_preferred_name": "new display",
                        },
                    }
                )
            )
        assert not any(call[0] == "save" for call in h.repository.calls)


def test_wrong_companion_and_operational_paths_are_refused():
    for value_path, companion_path in [
        (VALUE_PATH, "high_level_study_design.is_adaptive_design_null_value_code"),
        ("version_metadata.study_status", COMPANION_PATH),
        ("identification_metadata.project_number", COMPANION_PATH),
    ]:
        with native_harness() as h:
            with pytest.raises(NullAdjudicationInvalid):
                h.guarded(
                    request_payload(
                        value_path=value_path, companion_path=companion_path
                    )
                )
            assert not any(call[0] == "save" for call in h.repository.calls)


def test_actual_plural_intent_api_companion_reaches_singular_domain_and_reads_back():
    data = request_payload(
        value_path="study_intervention.trial_intent_types_codes",
        companion_path="study_intervention.trial_intent_types_null_value_code",
        expected_value={"present": True, "value": []},
    )
    with native_harness() as h:
        h.guarded(data)
        assert (
            h.current().current_metadata.study_intervention.trial_intent_type_null_value_code
            == "CT_UNK"
        )
        result = h.readback()
        assert (
            result.current_metadata.study_intervention.trial_intent_types_null_value_code.term_uid
            == "CT_UNK"
        )
        receipt = null_adjudication_receipt(
            STUDY_UID, StudyNullAdjudicationRequest.model_validate(data)
        )
        assert receipt.checked_paths == [
            data["adjudications"][0]["value_path"],
            data["adjudications"][0]["companion_path"],
        ]


def test_guarded_internal_call_cannot_use_unlocked_dry_mode():
    with native_harness() as h:
        with pytest.raises(Exception, match="cannot use dry mode"):
            h.service.patch(
                STUDY_UID,
                True,
                StudyPatchRequestJsonModel(),
                null_adjudication=StudyNullAdjudicationRequest.model_validate(
                    request_payload()
                ),
            )
        assert h.repository.calls == []
