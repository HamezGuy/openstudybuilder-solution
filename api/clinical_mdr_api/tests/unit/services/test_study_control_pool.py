"""Offline checks for control pools and thin adaptation references (O7)."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from clinical_mdr_api.domains.study_selections.study_control_pool import (
    CslStatisticalReferenceVO,
    StudyAdaptationReferenceVO,
    StudyControlPoolVO,
)
from clinical_mdr_api.models.study_selections.study_control_pool import (
    CslStatisticalReference,
    StudyAdaptationReferenceInput,
    StudyControlPool,
    StudyControlPoolInput,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import STUDY_UID, VERSION
from common.exceptions import ValidationException

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def pins(collection="controlPools"):
    return {
        "publication_entity_id": "synthetic-publication",
        "publication_hash": "sha256:" + "a" * 64,
        "analysis_model_hash": "b" * 64,
        "collection": collection,
        "entry_id": "ControlPool_000001",
        "entry_hash": "sha256:" + "c" * 64,
    }


def pool(**changes):
    values = {
        "uid": "StudyControlPool_000001",
        "study_uid": "Study_1",
        "name": "Synthetic shared placebo",
        "donor_arm_uids": ("StudyArm_3", "StudyArm_1"),
        "focal_arm_uids": ("StudyArm_2",),
        "csl_reference": CslStatisticalReferenceVO(**pins()),
        "description": None,
        "author_id": "synthetic-author",
        "start_date": NOW,
    }
    values.update(changes)
    return StudyControlPoolVO(**values)


def test_pool_membership_is_explicit_sorted_and_disjoint():
    assert pool().donor_arm_uids == ("StudyArm_1", "StudyArm_3")
    assert pool().arm_uids() == ("StudyArm_1", "StudyArm_2", "StudyArm_3")
    assert pool(csl_reference=None).csl_reference is None
    for changes in (
        {"donor_arm_uids": ()},
        {"focal_arm_uids": ()},
        {"focal_arm_uids": ("StudyArm_1",)},
        {"donor_arm_uids": ("StudyArm_1", "StudyArm_1")},
        {"name": " "},
        {"csl_reference": CslStatisticalReferenceVO(**pins("interimAnalyses"))},
    ):
        with pytest.raises(ValidationException):
            pool(**changes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("publication_hash", "A" * 64),
        ("entry_hash", "sha256:abc"),
        ("analysis_model_hash", ""),
        ("entry_id", " "),
        ("collection", ""),
    ],
)
def test_reference_pins_are_exact_digests_and_identities(field, value):
    with pytest.raises(ValidationException):
        CslStatisticalReferenceVO(**{**pins(), field: value})
    with pytest.raises(ValidationError):
        CslStatisticalReference(**{**pins(), field: value if value else ""})


def test_reference_round_trips_without_copying_any_statistical_value():
    vo = CslStatisticalReferenceVO(**pins())
    assert CslStatisticalReferenceVO.from_json(vo.to_json()) == vo
    assert CslStatisticalReferenceVO.from_json(None) is None
    assert set(vo.__dict__) == set(pins())
    response = StudyControlPool.from_vo(pool())
    assert response.reference_verified_by_osb is False
    assert response.csl_reference.model_dump() == pins()


def test_adaptation_requires_a_reference_and_keeps_scope_sets():
    reference = CslStatisticalReferenceVO(**pins("interimAnalyses"))
    adaptation = StudyAdaptationReferenceVO(
        uid="StudyAdaptationReference_000001",
        study_uid="Study_1",
        adaptation_type="Synthetic futility review",
        csl_reference=reference,
        affected_arm_uids=("StudyArm_2", "StudyArm_1"),
        affected_epoch_uids=(),
    )
    assert adaptation.affected_arm_uids == ("StudyArm_1", "StudyArm_2")
    for changes in (
        {"csl_reference": None},
        {"adaptation_type": ""},
        {"trigger_reference": " "},
        {"affected_epoch_uids": ("",)},
    ):
        with pytest.raises(ValidationException):
            StudyAdaptationReferenceVO(
                **{
                    "uid": "U",
                    "study_uid": "Study_1",
                    "adaptation_type": "Synthetic",
                    "csl_reference": reference,
                    **changes,
                }
            )


def test_inputs_refuse_unknown_statistical_fields_and_missing_pins():
    body = {
        "expected_study_version": "None",
        "name": "Synthetic pool",
        "donor_arm_uids": ["StudyArm_1"],
        "focal_arm_uids": ["StudyArm_2"],
    }
    assert StudyControlPoolInput(**body).csl_reference is None
    for extra in ({"alpha": 0.025}, {"concurrency_window_days": 180}):
        with pytest.raises(ValidationError):
            StudyControlPoolInput(**body, **extra)
    with pytest.raises(ValidationError):
        StudyAdaptationReferenceInput(
            expected_study_version="None", adaptation_type="Synthetic"
        )


def test_usdm_retains_pools_as_descriptive_non_executable_extensions():
    source = NativeStudySource()
    source.graph["control_pools"] = [
        StudyControlPool.from_vo(pool(study_uid=STUDY_UID))
    ]
    with source.isolated():
        report = source.mapper().map_with_report(source.graph["study"], VERSION)
    design = report["document"]["study"]["versions"][0]["studyDesigns"][0]
    urls = [item["url"] for item in design["extensionAttributes"]]
    assert any(url.endswith("/native/studyControlPool") for url in urls)
    text = json.dumps(design["extensionAttributes"])
    assert "nativeExecutable" in text and "StudyArm_2" in text
    retained = [
        row for row in report["nativeRecords"] if row["kind"] == "studyControlPool"
    ]
    assert [row["record"]["uid"] for row in retained] == ["StudyControlPool_000001"]
    # No statistical value is invented: only the authored pins are carried.
    assert "alpha" not in text and "concurrency" not in text
