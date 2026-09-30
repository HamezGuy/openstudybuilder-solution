"""Prove complete source retention when native clinical mapping is partial."""

import base64
import gzip
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from clinical_mdr_api.services.integrations.edc_export import (
    EdcExportError,
    EdcExportService,
)
from clinical_mdr_api.services.integrations.edc_source_snapshot import (
    SourceSnapshotError,
    _decode,
    read_source_snapshot,
)


def fixture():
    source = {
        "_provenance": {"builtBy": "csl.bundle-builder/1.0.0/preview"},
        "study": {"name": "Semantic source"},
        "forms": {
            "forms": [
                {
                    "refKey": "F1",
                    "name": "Present",
                    "fields": [
                        {
                            "refKey": "I1",
                            "label": "Entire source quote — 𝛼",
                            "unknown": [False, 0, None],
                        }
                    ],
                },
                {
                    "refKey": "F2",
                    "name": "Native child held",
                    "fields": [{"refKey": "I2", "text": "Full source information"}],
                },
            ]
        },
        "semanticSourceCustody": {
            "source": {
                "assertions": [{"metadata": {"nested": [0, False, None, "世界"]}}]
            }
        },
    }
    raw = json.dumps(
        source, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    encoded = "gzip+base64:" + base64.b64encode(gzip.compress(raw)).decode("ascii")
    chunks = [encoded[index : index + 83] for index in range(0, len(encoded), 83)]
    manifest = {
        "formatVersion": "1.0",
        "osbStudyUid": "Study_1",
        "sourceStudyId": "source-1",
        "sourceBuildHash": "sha256:build-full",
        "snapshotHash": hashlib.sha256(raw).hexdigest(),
        "encodedHash": hashlib.sha256(encoded.encode()).hexdigest(),
        "chunkCount": len(chunks),
    }

    def carrier(oid, descriptor, chunk=None):
        attrs = {
            "studyId": "source-1",
            "buildHash": "sha256:build-full",
            "ext": json.dumps({"semanticSourceSnapshot": descriptor}),
        }
        if chunk is not None:
            attrs["bundleMeta"] = chunk
        return {
            "uid": oid,
            "oid": oid,
            "item_groups": [],
            "vendor_attributes": [
                {"name": name, "value": value} for name, value in attrs.items()
            ],
        }

    records = [
        carrier("F.SEMANTIC.SNAPSHOT.HEAD.Study_1", {**manifest, "role": "head"})
    ]
    records.extend(
        carrier(
            f"F.SEMANTIC.SNAPSHOT.Study_1.{manifest['snapshotHash']}.{index:04d}",
            {
                **manifest,
                "role": "chunk",
                "index": index,
                "chunkHash": hashlib.sha256(chunk.encode()).hexdigest(),
            },
            chunk,
        )
        for index, chunk in enumerate(chunks, 1)
    )
    return source, records


def test_committed_snapshot_recovers_every_source_value_and_unicode_byte():
    source, records = fixture()
    assert read_source_snapshot(records, "Study_1", {"source-1"}) == (source, records)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "content",
        "index",
        "build",
        "foreign",
        "head",
        "clinical",
        "attribute",
    ],
)
def test_incomplete_or_conflicting_snapshot_is_never_silently_downgraded(mutation):
    _, records = fixture()
    if mutation == "missing":
        records.pop()
    elif mutation == "duplicate":
        records.append(deepcopy(records[-1]))
    elif mutation == "content":
        records[-1]["vendor_attributes"][-1]["value"] += "corruption"
    elif mutation in ("index", "build", "foreign"):
        ext = records[-1]["vendor_attributes"][2]
        data = json.loads(ext["value"])
        data["semanticSourceSnapshot"][
            {"index": "index", "build": "sourceBuildHash", "foreign": "sourceStudyId"}[
                mutation
            ]
        ] = "different"
        ext["value"] = json.dumps(data)
    elif mutation == "head":
        records.append(deepcopy(records[0]))
    elif mutation == "clinical":
        records[-1]["item_groups"] = [{"uid": "PatientData"}]
    else:
        records[-1]["vendor_attributes"].append(
            deepcopy(records[-1]["vendor_attributes"][-1])
        )
    with pytest.raises(SourceSnapshotError):
        read_source_snapshot(records, "Study_1", {"source-1"})


def test_other_study_and_uncommitted_generations_are_never_read_as_current():
    _, records = fixture()
    assert read_source_snapshot(records, "Study_2", {"source-1"}) is None
    with pytest.raises(SourceSnapshotError, match="no committed head"):
        read_source_snapshot(records[1:], "Study_1", {"source-1"})
    with pytest.raises(SourceSnapshotError, match="identity"):
        read_source_snapshot(records, "Study_1", {"different-source"})


def test_all_canonical_forms_survive_partial_native_mapping_without_carrier_forms():
    source, records = fixture()
    records.append(
        {
            "uid": "Native1",
            "oid": "F1",
            "name": "Native name",
            "item_groups": [],
            "vendor_attributes": [
                {"name": "studyId", "value": "source-1"},
                {"name": "source", "value": json.dumps(source["forms"]["forms"][0])},
                # The legacy form carrier is incomplete. The committed snapshot wins.
                {"name": "bundleMeta", "value": "chunk:1/14:incomplete"},
            ],
        }
    )
    exporter = object.__new__(EdcExportService)
    exporter.census = []
    exporter.source_bundle_meta = {}
    exporter.form_service = SimpleNamespace(get_all_odms=lambda **kwargs: records)
    forms, _, _ = exporter._forms(set(), {"source-1"}, "Study_1")
    assert forms == source["forms"]["forms"]
    assert exporter.source_bundle_meta == source
    assert len(forms) == 2
    assert any(row["ref"] == "forms/F2/nativePresence" for row in exporter.census)
    assert not any(row["kind"] == "bundle_meta_unparseable" for row in exporter.census)
    assert sum(record["kind"] == "form" for record in exporter._native_records) == 1


def test_corrupt_committed_snapshot_refuses_export_instead_of_using_legacy_metadata():
    _, records = fixture()
    records.pop()
    exporter = object.__new__(EdcExportService)
    exporter.census = []
    exporter.source_bundle_meta = {}
    exporter.form_service = SimpleNamespace(get_all_odms=lambda **kwargs: records)
    with pytest.raises(EdcExportError, match="SEMANTIC_SOURCE_SNAPSHOT_INVALID"):
        exporter._forms(set(), {"source-1"}, "Study_1")


def test_same_source_can_be_retained_in_a_new_build_generation():
    source, records = fixture()
    _, old_records = fixture()
    for record in records:
        attrs = {
            attribute["name"]: attribute for attribute in record["vendor_attributes"]
        }
        descriptor = json.loads(attrs["ext"]["value"])["semanticSourceSnapshot"]
        descriptor["sourceBuildHash"] = "sha256:different-build"
        descriptor["generationHash"] = hashlib.sha256(
            (descriptor["snapshotHash"] + "\n" + descriptor["sourceBuildHash"]).encode()
        ).hexdigest()
        attrs["buildHash"]["value"] = descriptor["sourceBuildHash"]
        attrs["ext"]["value"] = json.dumps({"semanticSourceSnapshot": descriptor})
        if descriptor["role"] == "chunk":
            record["oid"] = record["oid"].replace(
                descriptor["snapshotHash"], descriptor["generationHash"]
            )
    assert read_source_snapshot(
        [*records, *old_records[1:]], "Study_1", {"source-1"}
    ) == (source, records)


def test_compressed_carrier_cannot_inflate_past_the_declared_reader_limit():
    value = "gzip+base64:" + base64.b64encode(gzip.compress(b"a" * 2000)).decode()
    with pytest.raises(SourceSnapshotError, match="byte limit"):
        _decode(value, 1000)
    assert _decode(value, 2000) == b"a" * 2000


def test_truncated_compressed_manifest_has_an_explicit_snapshot_error():
    _, records = fixture()
    ext = records[0]["vendor_attributes"][2]
    ext["value"] = (
        "gzip+base64:"
        + base64.b64encode(gzip.compress(ext["value"].encode())[:-5]).decode()
    )
    with pytest.raises(SourceSnapshotError, match="manifest"):
        read_source_snapshot(records, "Study_1", {"source-1"})
