"""Exact conservation and native evidence lineage join; synthetic I/O only."""

import copy
import importlib

import pytest

from clinical_mdr_api.tests.unit.services.test_native_package_carrier_flow import (
    h,
    network_custody,
    prepared,
)


@pytest.fixture
def accounting(network_custody):
    del network_custody
    module = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_carrier_coverage"
    )
    source = copy.deepcopy(prepared())
    arguments = {
        key: source[key]
        for key in ("bundle", "crosswalk", "census", "carrier", "authority")
    }
    return module, source, {**arguments, "records": [], "lineages": []}


def test_actual_csl_terminal_conservation_matches_independent_python_accounting(
    accounting,
):
    module, source, arguments = accounting
    assert (
        module.verify_carrier_terminal_accounting(**arguments) == source["conservation"]
    )


@pytest.mark.parametrize("change", ["missing", "duplicate", "type", "value", "order"])
def test_terminal_loss_duplication_or_changed_value_is_refused(accounting, change):
    module, _, arguments = accounting
    rows = arguments["crosswalk"]["rows"]
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif change == "type":
        atom = rows[0]["coverageAtoms"][0]
        atom["type"] = "string" if atom["type"] == "null" else "null"
    elif change == "value":
        rows[0]["coverageAtoms"][0]["valueHash"]["value"] = "sha256:" + "0" * 64
    else:
        rows.reverse()
    with pytest.raises(module.OsbCandidateSetError):
        module.verify_carrier_terminal_accounting(**arguments)


def native_case(accounting):
    module, _, arguments = accounting
    # Deliberately synthetic native observation: this unit test isolates the
    # lineage join. Actual observation/source projection is covered by the
    # inherited-checkpoint tests in test_native_package_carrier_state.
    row = arguments["crosswalk"]["rows"][0]
    atom = row["coverageAtoms"][0]
    value = module._resolve(arguments["bundle"], atom["pointer"])
    evidence_version = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    record = {
        "kind": "native",
        "sourceProjectionVerified": True,
        "operationId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "targetIdentity": {
            "resourceFamily": "synthetic",
            "resourceType": "synthetic",
            "uid": "synthetic",
            "version": "1",
        },
        "payload": {
            "value": value,
            "sourceBinding": {
                "bindingKey": "synthetic|key",
                "sourceInputHash": h({}, "OsbTypedSourceIntentV1@1.0.0"),
            },
        },
        "readBackHash": h({"value": value}, "OsbNativeCaptureReadBackV1@1.0.0"),
    }
    row["evidence"] = {
        "disposition": "native-observed",
        "evidenceSetArtifactVersionId": evidence_version,
        "operationId": record["operationId"],
        "bindingKey": record["payload"]["sourceBinding"]["bindingKey"],
        "sourceInputHash": record["payload"]["sourceBinding"]["sourceInputHash"],
        "readBackHash": record["readBackHash"],
        "nativeRecordHash": h(record, "OsbPackageNativeRecordV1@1.0.0"),
        "targetIdentity": record["targetIdentity"],
        "fieldMatches": [
            {
                "nativePointer": "/value",
                "bundlePointer": atom["pointer"],
                "observedValueHash": atom["valueHash"],
                "bundleValueHash": atom["valueHash"],
            }
        ],
    }
    arguments["records"] = [record]
    arguments["lineages"] = [
        {
            "state": {
                "nativeEvidenceSetArtifact": {"artifactVersionId": evidence_version},
                "records": [copy.deepcopy(record)],
            }
        }
    ]
    return module, arguments


def test_named_evidence_set_must_contain_the_exact_native_record(accounting):
    module, arguments = native_case(accounting)
    assert (
        module.verify_carrier_terminal_accounting(**arguments)["counts"][
            "nativeObservedAtoms"
        ]
        == 1
    )
    arguments["lineages"][0]["state"]["nativeEvidenceSetArtifact"][
        "artifactVersionId"
    ] = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    with pytest.raises(module.OsbCandidateSetError):
        module.verify_carrier_terminal_accounting(**arguments)


def test_matching_version_in_another_lineage_does_not_prove_record_membership(
    accounting,
):
    module, arguments = native_case(accounting)
    empty_lineage = copy.deepcopy(arguments["lineages"][0])
    empty_lineage["state"]["records"] = []
    arguments["lineages"][0]["state"]["nativeEvidenceSetArtifact"][
        "artifactVersionId"
    ] = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    arguments["lineages"].append(empty_lineage)
    with pytest.raises(module.OsbCandidateSetError):
        module.verify_carrier_terminal_accounting(**arguments)


@pytest.mark.parametrize(
    "field,value",
    [
        ("createdAt", "not a timestamp"),
        ("createdAt", "2026-99-99T00:00:00Z"),
        ("stableLocator", "not a URI"),
    ],
)
def test_required_jsonschema_uri_and_datetime_formats_are_enforced(
    network_custody, field, value
):
    module, _ = network_custody
    ref = copy.deepcopy(prepared()["artifacts"][0]["artifact"])
    ref[field] = value
    with pytest.raises(module["OsbCandidateSetError"]):
        module["validate_artifact_ref"](ref)
