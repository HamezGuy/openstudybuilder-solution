"""Malformed sources must fail closed before JSON can discard source values."""

import base64
from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from clinical_mdr_api.services.integrations import edc_source_snapshot as subject


def carriers(raw, *, encoded=None, build="sha256:build-one", shard_size=41):
    if encoded is None:
        encoded = "gzip+base64:" + base64.b64encode(gzip.compress(raw, mtime=0)).decode()
    chunks = [encoded[index:index + shard_size] for index in range(0, len(encoded), shard_size)]
    snapshot_hash = hashlib.sha256(raw).hexdigest()
    generation = hashlib.sha256((snapshot_hash + "\n" + build).encode()).hexdigest()
    manifest = {"formatVersion": "1.0", "osbStudyUid": "Study_1", "sourceStudyId": "source-1",
                "sourceBuildHash": build, "snapshotHash": snapshot_hash,
                "encodedHash": hashlib.sha256(encoded.encode()).hexdigest(), "generationHash": generation,
                "byteLength": len(raw), "chunkCount": len(chunks)}
    def record(oid, role, index=None, chunk=None):
        descriptor = {**manifest, "role": role}
        if index is not None:
            descriptor.update(index=index, chunkHash=hashlib.sha256(chunk.encode()).hexdigest())
        attrs = {"ext": json.dumps({"semanticSourceSnapshot": descriptor}),
                 "studyId": "source-1", "buildHash": build}
        if chunk is not None:
            attrs["bundleMeta"] = chunk
        return {"uid": oid, "oid": oid, "item_groups": [],
                "vendor_attributes": [{"name": key, "value": value} for key, value in attrs.items()]}
    return [record("F.SEMANTIC.SNAPSHOT.HEAD.Study_1", "head"), *[
        record(f"F.SEMANTIC.SNAPSHOT.Study_1.{generation}.{index:04d}", "chunk", index, chunk)
        for index, chunk in enumerate(chunks, 1)]]


def read(records):
    return subject.read_source_snapshot(records, "Study_1", {"source-1"})


def test_shuffled_current_shards_reassemble_exactly_without_selecting_prior_generation():
    source = {"study": {"name": "Full text 😀\n" * 90}, "metadata": [False, 0, None, "", {}]}
    raw = json.dumps(source, ensure_ascii=False).encode()
    current = carriers(raw)
    old = carriers(b'{"study":{"name":"prior source"}}', build="sha256:old-build")
    before = deepcopy(current)
    result, selected = read([*reversed(current), *old[1:]])
    assert result == source
    assert {row["oid"] for row in selected} == {row["oid"] for row in current}
    assert current == before


@pytest.mark.parametrize("raw", [
    b'{"study":{"eligibility":"first complete source","eligibility":"silently chosen second"}}',
    b'{"sourceMetadata":{"first":false,"first":null}}',
    b'{"dose":NaN}', b'{"dose":Infinity}', b'{"dose":-Infinity}',
])
def test_hash_consistent_json_cannot_discard_duplicate_keys_or_accept_nonfinite_values(raw):
    with pytest.raises(subject.SourceSnapshotError, match="content"):
        read(carriers(raw))


def test_duplicate_manifest_identity_is_rejected_even_when_last_value_matches_scope():
    records = carriers(b'{"study":{"name":"exact"}}')
    attr = records[0]["vendor_attributes"][0]
    attr["value"] = attr["value"].replace('"sourceStudyId": "source-1"',
                                        '"sourceStudyId": "foreign", "sourceStudyId": "source-1"')
    with pytest.raises(subject.SourceSnapshotError, match="manifest"):
        read(records)


@pytest.mark.parametrize("encoded", [
    "gzip+base64:***",  # not base64
    "gzip+base64:" + base64.b64encode(b"not a gzip member").decode(),
    # Valid gzip header followed by an invalid DEFLATE block type.
    "gzip+base64:" + base64.b64encode(bytes.fromhex("1f8b0800000000000203") + b"\x07" + b"\x00" * 8).decode(),
    "gzip+base64:" + base64.b64encode(gzip.compress(b'{"study":{}}')[:-5]).decode(),
])
def test_invalid_compressed_snapshot_uses_explicit_retention_error(encoded):
    with pytest.raises(subject.SourceSnapshotError):
        read(carriers(b'{"study":{}}', encoded=encoded))


def test_bomb_and_declared_byte_length_mismatch_fail_at_bounded_reader(monkeypatch):
    raw = b'{"text":"' + b"x" * 5000 + b'"}'
    records = carriers(raw)
    # Legacy manifests omit byteLength; the decompressor still enforces its bound.
    for row in records:
        attrs = row["vendor_attributes"]
        descriptor = json.loads(attrs[0]["value"])
        del descriptor["semanticSourceSnapshot"]["byteLength"]
        attrs[0]["value"] = json.dumps(descriptor)
    monkeypatch.setattr(subject, "MAXIMUM_SOURCE_SNAPSHOT_BYTES", 1024)
    with pytest.raises(subject.SourceSnapshotError, match="content"):
        read(records)


@pytest.mark.parametrize("mutation", ["missing", "same-index", "foreign-stamp", "wrong-build", "mixed-generation", "wrong-length"])
def test_current_generation_never_borrows_shards_or_scope_from_other_readings(mutation):
    records = carriers(b'{"metadata":{"literal":"current complete source","values":[0,false,null]}}')
    attrs = {row["name"]: row for row in records[-1]["vendor_attributes"]}
    if mutation == "missing":
        records.pop()
    elif mutation == "same-index":
        records[-1] = deepcopy(records[1])
    elif mutation == "foreign-stamp":
        attrs["studyId"]["value"] = "foreign"
    else:
        descriptor = json.loads(attrs["ext"]["value"])
        descriptor["semanticSourceSnapshot"][{"wrong-build": "sourceBuildHash", "mixed-generation": "generationHash", "wrong-length": "byteLength"}[mutation]] = "different"
        attrs["ext"]["value"] = json.dumps(descriptor)
    with pytest.raises(subject.SourceSnapshotError):
        read(records)
