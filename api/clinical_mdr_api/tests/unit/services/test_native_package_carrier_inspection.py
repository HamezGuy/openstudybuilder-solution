"""Actual inspection code and pinned schemas with retained in-memory I/O only.

Run with --noconftest. The socket guard refuses all service connections. The
storage double checks immutable behavior, not Neo4j locks or transactions.
"""

from __future__ import annotations

import copy
import importlib
import json

import pytest

from clinical_mdr_api.tests.unit.services.test_native_package_artifact_custody import (
    OTHER,
)
from clinical_mdr_api.tests.unit.services.test_native_package_artifact_custody import (
    custody as network_custody,
)
from clinical_mdr_api.tests.unit.services.test_native_package_carrier_state import (
    STUDY,
    TENANT,
    artifact,
    canonical,
    hash_ref,
)

OPERATION = "00000000-0000-4000-8000-000000000070"
ENTRY_FIELDS = (
    "section",
    "position",
    "payload_json",
    "payload_hash",
    "byte_size",
    "value_json",
    "value_size",
    "oversize",
)


class InspectionStore:
    def __init__(self):
        self.headers = {}
        self.entries = {}
        self.calls = []
        self.read_duplicate = False

    def cypher_query(self, query, parameters):
        self.calls.append((query, copy.deepcopy(parameters)))
        scope = tuple(
            parameters[name] for name in ("tenant_id", "platform_study_id", "index_key")
        )
        if "MERGE (inspection:OsbCarrierInspection" in query:
            retained = self.headers.setdefault(
                scope,
                [
                    parameters[name]
                    for name in ("payload_json", "payload_hash", "byte_size")
                ],
            )
            return [copy.deepcopy(retained)], []
        if "MATCH (inspection:OsbCarrierInspection" in query:
            retained = self.headers.get(scope)
            return ([] if retained is None else [copy.deepcopy(retained)]), []
        if "UNWIND $entries AS item" in query:
            rows = []
            for entry in parameters["entries"]:
                retained = self.entries.setdefault(
                    (*scope, entry["section"], entry["position"]), copy.deepcopy(entry)
                )
                rows.append([retained[name] for name in ENTRY_FIELDS])
            return copy.deepcopy(rows), []
        assert "MATCH (entry:OsbCarrierInspectionEntry" in query
        rows = []
        for key, entry in sorted(self.entries.items()):
            if key[:3] != scope or key[3] != parameters["section"]:
                continue
            if "position" in parameters and key[4] != parameters["position"]:
                continue
            if (
                "offset" in parameters
                and not parameters["offset"] <= key[4] < parameters["end"]
            ):
                continue
            rows.append(
                [
                    entry["position"],
                    entry["payload_json"],
                    entry["payload_hash"],
                    entry["byte_size"],
                    (
                        None
                        if "CASE WHEN" in query and entry["oversize"]
                        else entry["value_json"]
                    ),
                    entry["value_size"],
                    entry["oversize"],
                ]
            )
        if rows and self.read_duplicate:
            rows.insert(0, copy.deepcopy(rows[0]))
        return copy.deepcopy(rows[: parameters.get("row_limit", len(rows))]), []


@pytest.fixture
def inspection(network_custody, monkeypatch):
    # network_custody installs the socket prohibition before real runtime import.
    module = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_carrier_inspection"
    )
    database = InspectionStore()
    monkeypatch.setattr(module, "db", database)
    return module, database


def value_hash(value):
    return hash_ref(value, "AuthoredStudyTerminalValueV1@1.0.0")


def rebind(request):
    for field, kind, contract, version, schema, media in (
        (
            "bundle",
            "accepted-canonical-study-exchange",
            "accuratrials.edc.StudyExchangeBundleV2",
            "2.0.0",
            "StudyExchangeBundleV2@2.0.0",
            "application/vnd.accuratrial.studybundle+json",
        ),
        (
            "crosswalk",
            "authored-study-member-crosswalk",
            "accuratrials.csl.AuthoredStudyMemberCrosswalkV1",
            "1.0.0",
            "AuthoredStudyMemberCrosswalkV1@1.0.0",
            "application/json",
        ),
        (
            "canonical_definition",
            "osb-canonical-study-definition",
            "accuratrials.osb.OsbCanonicalStudyDefinitionV1",
            "1.0.0",
            "OsbCanonicalStudyDefinitionV1@1.0.0",
            "application/json",
        ),
    ):
        request[field + "_artifact"] = artifact(
            request[field], kind, contract, version, schema, media
        )
    request["carrier_artifact"] = artifact(
        {
            "bundleArtifact": request["bundle_artifact"],
            "crosswalkArtifact": request["crosswalk_artifact"],
        },
        "accepted-study-exchange-carrier",
        "accuratrials.csl.AcceptedStudyExchangeCarrierV1",
        "1.0.0",
        "AcceptedStudyExchangeCarrierV1@1.0.0",
    )
    request["native_state_artifact"] = artifact(
        {
            "contentIndex": [
                {
                    "operationId": record["operationId"],
                    "recordHash": hash_ref(record, "OsbPackageNativeRecordV1@1.0.0"),
                }
                for record in request["records"]
            ]
        },
        "osb-package-native-state",
        "accuratrials.osb.OsbPackageNativeStateV1",
        "1.2.0",
        "OsbPackageNativeStateV1@1.2.0",
    )
    return request


@pytest.fixture
def request_data():
    record = {
        "operationId": OPERATION,
        "kind": "study-activity",
        "resourceFamily": "activities",
        "payload": {"nativeValues": {"unit": "mg"}},
        "sourceIntent": {"preserve": [None, False, 0]},
        "targetIdentity": {
            "resourceFamily": "activities",
            "resourceType": "StudyActivity",
            "uid": "Activity_Synthetic",
            "version": "1",
        },
        "readBackHash": value_hash({"nativeValues": {"unit": "mg"}}),
    }
    atoms = [
        {
            "pointer": "/execution/forms/0/unit",
            "type": "string",
            "valueHash": value_hash("mg"),
        },
        {
            "pointer": "/execution/forms/0/nullable",
            "type": "null",
            "valueHash": value_hash(None),
        },
        {
            "pointer": "/execution/forms/0/a~1b~0c",
            "type": "empty-object",
            "valueHash": value_hash({}),
        },
    ]
    member = {"kind": "form", "formRef": "F/1~literal", "pointer": "/execution/forms/0"}
    evidence = [
        {
            "disposition": "native-observed",
            "evidenceSetArtifactVersionId": OTHER,
            "operationId": OPERATION,
            "bindingKey": "synthetic-activity",
            "sourceInputHash": value_hash({}),
            "readBackHash": record["readBackHash"],
            "nativeRecordHash": hash_ref(record, "OsbPackageNativeRecordV1@1.0.0"),
            "targetIdentity": record["targetIdentity"],
            "fieldMatches": [
                {
                    "nativePointer": "/nativeValues/unit",
                    "bundlePointer": atoms[0]["pointer"],
                    "observedValueHash": value_hash("mg"),
                    "bundleValueHash": value_hash("mg"),
                }
            ],
        },
        {
            "disposition": "accepted-canonical-extension",
            "authorityHash": value_hash({}),
            "mappingDecisionIds": [OTHER],
            "canonicalSourcePointers": ["/forms/0/nullable"],
            "reason": "Synthetic accepted mapping for transport tests.",
        },
        {
            "disposition": "retained-source-only",
            "authorityHash": value_hash({}),
            "canonicalSourcePointers": ["/forms/0/a~1b~0c"],
            "reason": "Synthetic source retention for transport tests.",
        },
    ]
    rows = [
        {
            "member": member,
            "memberValueHash": value_hash({}),
            "evidence": item,
            "coverageAtoms": [atom],
        }
        for item, atom in zip(evidence, atoms)
    ]
    return rebind(
        {
            "tenant_id": TENANT,
            "platform_study_id": STUDY,
            "crosswalk": {"rows": rows},
            "bundle": {
                "formatVersion": "2.0",
                "execution": {"forms": [{"unit": "mg", "nullable": None, "a/b~c": {}}]},
            },
            "records": [record],
            "canonical_definition": {
                "document": {"name": "Exact synthetic study"},
                "selection": {"source": "all"},
                "mappingReport": {"complete": False, "unmappedPaths": ["/unmapped"]},
                "nativeRecords": [{"untouched": "mapper row"}],
            },
        }
    )


def prepare(inspection, request):
    return inspection[0].prepare_carrier_inspection(**request)


def descriptor(module, request):
    return module.read_carrier_inspection_descriptor(
        **{
            key: request[key]
            for key in (
                "tenant_id",
                "platform_study_id",
                "carrier_artifact",
                "native_state_artifact",
                "canonical_definition_artifact",
            )
        }
    )


def page(module, index, section="terminalAtoms", offset=0, limit=100, **overrides):
    return module.read_carrier_inspection_page(
        **{
            "tenant_id": TENANT,
            "platform_study_id": STUDY,
            "index_key": index["indexKey"],
            "section": section,
            "offset": offset,
            "limit": limit,
            **overrides,
        }
    )


def entry(module, index, section, position=0, **overrides):
    return module.read_carrier_inspection_entry(
        **{
            "tenant_id": TENANT,
            "platform_study_id": STUDY,
            "index_key": index["indexKey"],
            "section": section,
            "position": position,
            **overrides,
        }
    )


def test_exact_values_dispositions_and_original_records_are_reviewable(
    inspection, request_data
):
    module, store = inspection
    index = prepare(inspection, request_data)
    assert index["counts"] == {
        "crosswalkRows": 3,
        "terminalAtoms": 3,
        "nativeRecords": 1,
        "canonicalDefinition": 1,
    }
    assert descriptor(module, request_data) == index
    assert prepare(inspection, request_data) == index
    rows = page(module, index, "crosswalkRows")
    assert rows["items"] == [
        {"position": i, "value": {"rowIndex": i, "item": row}}
        for i, row in enumerate(request_data["crosswalk"]["rows"])
    ]
    atoms = page(module, index)["items"]
    assert [item["value"]["actualValue"] for item in atoms] == ["mg", None, {}]
    assert [item["value"]["disposition"] for item in atoms] == [
        "native-observed",
        "accepted-canonical-extension",
        "retained-source-only",
    ]
    assert atoms[0]["value"]["nativeValue"] == "mg"
    assert (
        atoms[0]["value"]["fieldMatch"]
        == request_data["crosswalk"]["rows"][0]["evidence"]["fieldMatches"][0]
    )
    assert atoms[1]["value"]["fieldMatch"] is None
    assert atoms[2]["value"]["nativeValue"] is None
    assert page(module, index, "nativeRecords")["items"] == [
        {"position": 0, "value": request_data["records"][0]}
    ]
    assert (
        page(module, index, "canonicalDefinition")["items"][0]["value"]
        == request_data["canonical_definition"]
    )
    assert len(store.headers) == 1 and len(store.entries) == 8


def test_readers_only_use_index_and_never_reparse_source_bundle(
    inspection, request_data, monkeypatch
):
    module, store = inspection
    index = prepare(inspection, request_data)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Source reconstruction must not run while inspecting")

    monkeypatch.setattr(module, "_entries", unexpected)
    monkeypatch.setattr(module, "_resolve", unexpected)
    store.calls.clear()
    assert descriptor(module, request_data) == index
    first = page(module, index, limit=2)
    assert first["nextOffset"] == 2
    last = page(module, index, offset=first["nextOffset"], limit=2)
    assert [item["position"] for item in first["items"] + last["items"]] == [0, 1, 2]
    assert last["nextOffset"] is None
    exact = entry(module, index, "nativeRecords")
    assert exact["bytes"] == canonical(request_data["records"][0]).encode("utf-8")
    assert exact["source"]["stateArtifact"] == request_data["native_state_artifact"]
    assert exact["source"]["contentIndexPointer"] == "/contentIndex/0"
    assert all(query.lstrip().startswith("MATCH") for query, _ in store.calls)


@pytest.mark.parametrize(
    "field",
    ["carrier_artifact", "native_state_artifact", "canonical_definition_artifact"],
)
def test_context_pins_full_descriptors_not_just_payload_hashes(
    inspection, request_data, field
):
    module, _ = inspection
    prepare(inspection, request_data)
    request_data[field]["stableLocator"] += "/alias"
    hashing = importlib.import_module(
        "clinical_mdr_api.generated.platform_contracts.hash_signing_v1"
    )
    ref = request_data[field]
    ref["descriptorHash"] = hashing.descriptor_hash(
        {
            **{
                key: value
                for key, value in ref.items()
                if key not in {"contractVersion", "descriptorHash"}
            },
            "contractVersion": "ArtifactDescriptorV1@1.0.0",
        }
    )
    with pytest.raises(module.OsbCandidateSetError) as caught:
        descriptor(module, request_data)
    assert caught.value.status_code == 404


@pytest.mark.parametrize("scope", [{"tenant_id": OTHER}, {"platform_study_id": OTHER}])
def test_foreign_scope_cannot_read_headers_pages_or_exact_entries(
    inspection, request_data, scope
):
    module, _ = inspection
    index = prepare(inspection, request_data)
    for read in (
        lambda: descriptor(module, {**request_data, **scope}),
        lambda: page(module, index, **scope),
        lambda: entry(module, index, "nativeRecords", **scope),
    ):
        with pytest.raises(module.OsbCandidateSetError) as caught:
            read()
        assert caught.value.status_code in {403, 404}


@pytest.mark.parametrize(
    "selector",
    [
        {"section": "unknown"},
        {"section": []},
        {"offset": True},
        {"offset": -1},
        {"offset": 0.5},
        {"offset": "0"},
        {"limit": False},
        {"limit": 0},
        {"limit": 101},
        {"index_key": "anything"},
    ],
)
def test_client_selectors_are_strict_before_query(inspection, request_data, selector):
    module, store = inspection
    index = prepare(inspection, request_data)
    store.calls.clear()
    with pytest.raises(module.OsbCandidateSetError) as caught:
        page(module, index, **selector)
    assert caught.value.status_code == 422
    assert store.calls == []


def test_empty_native_section_and_end_offset_are_explicit(inspection, request_data):
    module, _ = inspection
    request_data["records"] = []
    request_data["crosswalk"]["rows"] = []
    index = prepare(inspection, rebind(request_data))
    result = page(module, index, "nativeRecords")
    assert (
        result["total"] == 0 and result["items"] == [] and result["nextOffset"] is None
    )
    assert page(module, index, "canonicalDefinition", offset=1)["items"] == []
    with pytest.raises(module.OsbCandidateSetError):
        page(module, index, "canonicalDefinition", offset=2)
    with pytest.raises(module.OsbCandidateSetError):
        entry(module, index, "nativeRecords")


@pytest.mark.parametrize(
    "mutation", ["hash", "size", "bytes", "metadata", "duplicate", "missing"]
)
def test_corrupt_or_missing_entries_never_become_success(
    inspection, request_data, mutation
):
    module, store = inspection
    index = prepare(inspection, request_data)
    key = (TENANT, STUDY, index["indexKey"], "terminalAtoms", 0)
    retained = store.entries[key]
    if mutation == "hash":
        retained["payload_hash"] = "sha256:" + "0" * 64
    elif mutation == "size":
        retained["value_size"] += 1
    elif mutation == "bytes":
        retained["value_json"] = retained["value_json"].replace('"mg"', '"kg"')
    elif mutation == "metadata":
        retained["payload_json"] += " "
    elif mutation == "duplicate":
        store.read_duplicate = True
    else:
        del store.entries[key]
    with pytest.raises(module.OsbCandidateSetError):
        page(module, index)
    with pytest.raises(module.OsbCandidateSetError):
        entry(module, index, "terminalAtoms")


def test_header_corruption_and_conflicting_immutable_replay_refuse(
    inspection, request_data
):
    module, store = inspection
    index = prepare(inspection, request_data)
    key = (TENANT, STUDY, index["indexKey"])
    store.headers[key][1] = "sha256:" + "0" * 64
    with pytest.raises(module.OsbCandidateSetError):
        descriptor(module, request_data)
    with pytest.raises(module.OsbCandidateSetError) as caught:
        prepare(inspection, request_data)
    assert caught.value.code == "OSB_CARRIER_INSPECTION_CONFLICT"


def test_conflicting_retained_entry_does_not_overwrite_original(
    inspection, request_data
):
    module, store = inspection
    index = prepare(inspection, request_data)
    key = (TENANT, STUDY, index["indexKey"], "nativeRecords", 0)
    store.entries[key]["value_json"] = '{"retained":"different"}'
    before = copy.deepcopy(store.entries[key])
    with pytest.raises(module.OsbCandidateSetError) as caught:
        prepare(inspection, request_data)
    assert caught.value.code == "OSB_CARRIER_INSPECTION_CONFLICT"
    assert store.entries[key] == before


@pytest.mark.parametrize(
    "pointer",
    [
        "/execution/forms/00/unit",
        "/execution/forms/0/a~2b",
        "/execution/forms/-/unit",
        "/execution/forms/0/missing",
        "/execution/forms/999999999999999999999/unit",
        "execution/forms/0/unit",
    ],
)
def test_pointer_failures_do_not_silently_replace_values(
    inspection, request_data, pointer
):
    module, _ = inspection
    request_data["crosswalk"]["rows"][0]["coverageAtoms"][0]["pointer"] = pointer
    with pytest.raises(module.OsbCandidateSetError):
        prepare(inspection, rebind(request_data))


@pytest.mark.parametrize(
    "change",
    [
        "atom-hash",
        "atom-type",
        "record-hash",
        "native-pointer",
        "native-hash",
        "duplicate-match",
        "missing-record",
        "duplicate-record",
    ],
)
def test_native_and_terminal_commitments_are_checked(inspection, request_data, change):
    module, _ = inspection
    row = request_data["crosswalk"]["rows"][0]
    if change == "atom-hash":
        row["coverageAtoms"][0]["valueHash"] = value_hash("other")
    elif change == "atom-type":
        row["coverageAtoms"][0]["type"] = "number"
    elif change == "record-hash":
        row["evidence"]["nativeRecordHash"] = hash_ref(
            {}, "OsbPackageNativeRecordV1@1.0.0"
        )
    elif change == "native-pointer":
        row["evidence"]["fieldMatches"][0][
            "nativePointer"
        ] = "/payload/nativeValues/unit"
    elif change == "native-hash":
        row["evidence"]["fieldMatches"][0]["observedValueHash"] = value_hash("other")
    elif change == "duplicate-match":
        row["evidence"]["fieldMatches"].append(
            copy.deepcopy(row["evidence"]["fieldMatches"][0])
        )
    elif change == "missing-record":
        request_data["records"] = []
    else:
        request_data["records"].append(copy.deepcopy(request_data["records"][0]))
    with pytest.raises(module.OsbCandidateSetError):
        prepare(inspection, rebind(request_data))


def test_source_bytes_must_match_exact_supplied_artifacts_before_storage(
    inspection, request_data
):
    module, store = inspection
    request_data["bundle"]["execution"]["forms"][0]["unit"] = "kg"
    with pytest.raises(module.OsbCandidateSetError) as caught:
        prepare(inspection, request_data)
    assert caught.value.code == "OSB_CARRIER_INSPECTION_SOURCE_MISMATCH"
    assert store.calls == []


def test_large_scalar_retains_exact_bytes_and_accurate_download_sources(
    inspection, request_data
):
    module, store = inspection
    large = "é" * 140000
    request_data["bundle"]["execution"]["forms"][0]["nullable"] = large
    atom = request_data["crosswalk"]["rows"][1]["coverageAtoms"][0]
    atom.update(type="string", valueHash=value_hash(large))
    index = prepare(inspection, rebind(request_data))
    first = page(module, index)
    assert [item["position"] for item in first["items"]] == [0]
    assert first["nextOffset"] == 1
    with pytest.raises(module.OsbCandidateSetError) as caught:
        page(module, index, offset=1)
    assert caught.value.status_code == 413
    assert caught.value.details["source"]["bundle"] == {
        "artifact": request_data["bundle_artifact"],
        "pointer": atom["pointer"],
    }
    exact = entry(module, index, "terminalAtoms", position=1)
    assert json.loads(exact["bytes"])["actualValue"] == large
    assert exact["bytes"] == canonical(json.loads(exact["bytes"])).encode("utf-8")
    assert len(exact["bytes"]) > module.PAGE_BYTE_LIMIT
    page_queries = [query for query, _ in store.calls if "CASE WHEN" in query]
    assert page_queries and all(
        "THEN null ELSE entry.value_json END" in query for query in page_queries
    )


@pytest.mark.parametrize(
    "section", ["nativeRecords", "canonicalDefinition", "crosswalkRows"]
)
def test_other_oversized_sections_remain_exact_without_fake_artifact_locators(
    inspection, request_data, section
):
    module, _ = inspection
    large = "x" * 270000
    if section == "nativeRecords":
        request_data["records"][0]["sourceIntent"] = {"large": large}
        request_data["crosswalk"]["rows"][0]["evidence"]["nativeRecordHash"] = hash_ref(
            request_data["records"][0], "OsbPackageNativeRecordV1@1.0.0"
        )
        expected = request_data["records"][0]
    elif section == "canonicalDefinition":
        request_data["canonical_definition"]["document"]["large"] = large
        expected = request_data["canonical_definition"]
    else:
        request_data["crosswalk"]["rows"][0] = copy.deepcopy(
            request_data["crosswalk"]["rows"][2]
        )
        request_data["crosswalk"]["rows"][0]["evidence"]["reason"] = large
        expected = {"rowIndex": 0, "item": request_data["crosswalk"]["rows"][0]}
    index = prepare(inspection, rebind(request_data))
    with pytest.raises(module.OsbCandidateSetError) as caught:
        page(module, index, section)
    assert caught.value.status_code == 413
    exact = entry(module, index, section)
    assert exact["bytes"] == canonical(expected).encode("utf-8")
    if section == "nativeRecords":
        assert set(exact["source"]) == {
            "stateArtifact",
            "contentIndexPointer",
            "recordHash",
        }
        assert exact["source"]["recordHash"] == hash_ref(
            expected, "OsbPackageNativeRecordV1@1.0.0"
        )
    else:
        assert (
            exact["source"]["artifact"]
            == request_data[
                (
                    "canonical_definition_artifact"
                    if section == "canonicalDefinition"
                    else "crosswalk_artifact"
                )
            ]
        )


def test_total_page_bytes_reduce_next_offset_without_skipping(inspection, request_data):
    module, _ = inspection
    request_data["records"] = []
    request_data["crosswalk"]["rows"] = []
    for number in range(3):
        request_data["records"].append(
            {
                "operationId": f"synthetic-record-{number}",
                "payload": {"text": "é" * 70000},
            }
        )
    index = prepare(inspection, rebind(request_data))
    offset = 0
    seen = []
    while offset is not None:
        result = page(module, index, "nativeRecords", offset=offset)
        assert len(canonical(result).encode("utf-8")) <= module.PAGE_BYTE_LIMIT
        assert len(result["items"]) == 1
        seen.extend(item["position"] for item in result["items"])
        offset = result["nextOffset"]
    assert seen == [0, 1, 2]


def test_entry_ceiling_and_sanitized_storage_failures(
    inspection, request_data, monkeypatch
):
    module, store = inspection
    monkeypatch.setattr(module, "MAX_CARRIER_ARTIFACT_BYTES", 10)
    with pytest.raises(module.OsbCandidateSetError) as caught:
        prepare(inspection, request_data)
    assert caught.value.status_code == 413

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("secret driver parameters and source values")

    monkeypatch.setattr(store, "cypher_query", unavailable)
    with pytest.raises(module.OsbCandidateSetError) as caught:
        descriptor(module, request_data)
    assert caught.value.status_code == 503
    assert "secret" not in str(caught.value)


def test_many_entries_use_bounded_batches(inspection, request_data):
    _, store = inspection
    request_data["crosswalk"]["rows"] = []
    request_data["records"] = [
        {"operationId": f"synthetic-{index}", "payload": {"number": index}}
        for index in range(205)
    ]
    prepare(inspection, rebind(request_data))
    batches = [
        parameters["entries"]
        for query, parameters in store.calls
        if "UNWIND $entries AS item" in query
    ]
    assert [len(batch) for batch in batches] == [100, 100, 6]
