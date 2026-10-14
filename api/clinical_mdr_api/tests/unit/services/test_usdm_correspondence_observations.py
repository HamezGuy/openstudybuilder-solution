"""Explicit mapper registrations against actual authored/native fixture paths."""

from copy import deepcopy

import pytest

from clinical_mdr_api.services.ddf import (
    usdm_correspondence_observations as observation_module,
)
from clinical_mdr_api.services.ddf.usdm_correspondence_observations import (
    RECORD_PROFILE,
    CorrespondenceObservations,
    correspondence_ruleset,
)
from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import VERSION
from clinical_mdr_api.tests.unit.services.test_authored_protocol_documents import (
    authored_source,
)
from common.exceptions import ValidationException


def test_actual_mapper_observations_preserve_default_output_and_all_authored_ids():
    graph, content = authored_source()
    graph["header"].authored_documents = content.model_dump(mode="json")
    graph["header"].authored_documents_hash = content.content_hash()
    source = NativeStudySource(graph)
    before = native_json(source.graph)
    with source.isolated():
        mapper = source.mapper()
        standard = mapper.map_with_report(source.graph["study"], VERSION)
        observed = mapper.map_with_correspondence_report(source.graph["study"], VERSION)
        subsequent = mapper.map_with_report(source.graph["study"], VERSION)
    assert native_json(source.graph) == before
    assert standard == subsequent
    assert {
        key: value
        for key, value in observed.items()
        if key != "correspondenceObservations"
    } == standard
    report = observed["correspondenceObservations"]
    assert report["correspondenceAuthority"] is False
    assert report["nativeOperationAttribution"] == "none"
    assert report["coverage"] == "partial-call-site-registrations-only"
    assert len(report["blockers"]) == 0
    assert len(report["registrations"]) == 2 * len(content.documents) + len(
        content.narrative_content_items
    ) + len(content.organizations)
    assert all(row["state"] == "exact" for row in report["registrations"])
    assert all(
        row["sourceValueHash"] == row["outputValueHash"]
        for row in report["registrations"]
    )
    assert report["allocationEvents"]
    assert not any(
        event["assigned_id"] == content.documents[0].id
        for event in report["allocationEvents"]
    )
    organization = next(
        row
        for row in report["registrations"]
        if row["ruleId"] == "osb.authored-organization.copy/1.0.0"
    )
    assert organization["sourceRecord"]["scope"]["studyValueVersion"] == VERSION
    assert (
        report["sourceRecords"][0]["nativeRecord"]["record"]["bindings"][0][
            "sponsor_organization_id"
        ]
        == content.organizations[0].id
    )
    assert len(report["sourceRecords"]) == 1


def snapshot():
    return {
        "kind": "studyAuthoredDocuments",
        "uid": "Study_1",
        "scope": {"studyUid": "Study_1", "studyValueVersion": "2.0"},
        "record": {
            "documents": [
                {
                    "instanceType": "StudyDefinitionDocument",
                    "id": "authored-document",
                    "nullable": None,
                    "zero": 0,
                    "no": False,
                    "empty": [],
                    "map": {},
                    "versions": [
                        {
                            "id": "authored-version",
                            "instanceType": "StudyDefinitionDocumentVersion",
                        }
                    ],
                }
            ]
        },
    }


def registered(record=None):
    record = snapshot() if record is None else record
    observations = CorrespondenceObservations()
    observations.register(
        rule_id="osb.authored-document.copy/1.0.0",
        source_handle=observations.retain_source(record),
        source_pointer="/documents/0",
        output_pointer="/study/documentedBy/0",
    )
    return observations


def test_observation_snapshots_do_not_change_with_source_or_returned_report_mutation():
    record = snapshot()
    output = {"study": {"documentedBy": deepcopy(record["record"]["documents"])}}
    observations = registered(record)
    record["record"]["documents"][0]["zero"] = 123
    report = observations.report(output)
    assert len(report["blockers"]) == 0
    report["sourceRecords"][0]["nativeRecord"]["record"]["documents"][0]["zero"] = 456
    assert (
        observations.report(output)["sourceRecords"][0]["nativeRecord"]["record"][
            "documents"
        ][0]["zero"]
        == 0
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("nullable", ""),
        ("zero", False),
        ("no", 0),
        ("empty", None),
        ("map", []),
        ("id", "renamed"),
    ],
)
def test_finalized_value_changes_block_exact_copy_instead_of_normalizing(field, value):
    record = snapshot()
    output = {"study": {"documentedBy": deepcopy(record["record"]["documents"])}}
    output["study"]["documentedBy"][0][field] = value
    report = registered(record).report(output)
    assert report["registrations"][0]["state"] == "changed-after-registration"
    assert report["blockers"][0]["code"] == "USDM_REGISTERED_SOURCE_CHANGED"


def test_absent_output_is_not_a_null_or_empty_value():
    report = registered().report({"study": {"documentedBy": []}})
    assert report["registrations"][0]["state"] == "absent-after-finalization"
    assert report["registrations"][0]["outputValueHash"] is None
    assert report["blockers"]


def test_unsupported_rules_duplicate_target_and_wrong_source_scope_refuse():
    observations = registered()
    valid = {
        "rule_id": "osb.authored-document.copy/1.0.0",
        "source_handle": observations.retain_source(snapshot()),
        "source_pointer": "/documents/0",
        "output_pointer": "/study/documentedBy/0",
    }
    with pytest.raises(ValidationException):
        observations.register(**valid)
    for change in (
        {"rule_id": "trust-any-id"},
        {"source_pointer": "/documents/00"},
        {"output_pointer": "/study/versions/0/organizations/0"},
        {"source_handle": "unknown-handle"},
    ):
        with pytest.raises(ValidationException):
            observations.register(**{**valid, **change})
    with pytest.raises(ValidationException):
        observations.retain_source({**snapshot(), "uid": "Other"})


@pytest.mark.parametrize("integer", [2**53, 2**53 + 1, -(2**53), -(2**53 + 1)])
def test_new_observation_refuses_unsafe_integers_without_changing_legacy_hashing(
    integer,
):
    record = snapshot()
    record["record"]["documents"][0]["zero"] = integer
    with pytest.raises(ValidationException, match="unsafe integer"):
        registered(record)
    safe = snapshot()
    safe["record"]["documents"][0]["zero"] = 2**53 - 1
    observations = registered(safe)
    output = {"study": {"documentedBy": deepcopy(safe["record"]["documents"])}}
    output["study"]["documentedBy"][0]["zero"] = integer
    with pytest.raises(ValidationException, match="unsafe integer"):
        observations.report(output)


def test_negative_zero_source_and_output_refuse_lossy_exact_comparison():
    record = snapshot()
    record["record"]["documents"][0]["zero"] = -0.0
    with pytest.raises(ValidationException, match="negative zero"):
        registered(record)
    safe = snapshot()
    observations = registered(safe)
    output = {"study": {"documentedBy": deepcopy(safe["record"]["documents"])}}
    output["study"]["documentedBy"][0]["zero"] = -0.0
    with pytest.raises(ValidationException, match="negative zero"):
        observations.report(output)
    output["study"]["documentedBy"][0]["zero"] = 0.0
    assert observations.report(output)["registrations"][0]["state"] == "exact"


def test_source_hash_runs_once_for_many_registrations(monkeypatch):
    record = snapshot()
    record["record"]["documents"] *= 100
    calls = []
    original = observation_module._hash

    def counted(value, profile):
        if profile == RECORD_PROFILE:
            calls.append(profile)
        return original(value, profile)

    monkeypatch.setattr(observation_module, "_hash", counted)
    observations = CorrespondenceObservations()
    handle = observations.retain_source(record)
    for index in range(100):
        observations.register(
            rule_id="osb.authored-document.copy/1.0.0",
            source_handle=handle,
            source_pointer=f"/documents/{index}",
            output_pointer=f"/study/documentedBy/{index}",
        )
    result = observations.report(
        {"study": {"documentedBy": record["record"]["documents"]}}
    )
    assert calls == [RECORD_PROFILE]
    assert len(result["sourceRecords"]) == 1
    assert len(result["registrations"]) == 100


def test_manifest_is_independent_and_never_grants_native_reference_authority():
    manifest = correspondence_ruleset()
    assert len(manifest["rules"]) == 4
    assert manifest["implementation"]["sourceHash"].startswith("sha256:")
    assert all(row["nativeOperationAttribution"] == "none" for row in manifest["rules"])
    manifest["rules"].clear()
    assert len(correspondence_ruleset()["rules"]) == 4
