"""Read committed semantic source snapshots independently of clinical ODM forms.

The importer writes immutable chunks before replacing a study's manifest. A
reader must never silently fall back to incomplete clinical-form metadata when
that committed snapshot is corrupt, ambiguous, or belongs to a different build.
"""

import base64
import gzip
import hashlib
import io
import json
import math
import re
import zlib
from typing import Any

SOURCE_SNAPSHOT_OID_PREFIX = "F.SEMANTIC.SNAPSHOT."
SOURCE_SNAPSHOT_REF_PREFIX = "__SEMANTIC_SOURCE_SNAPSHOT__"
MAXIMUM_SOURCE_SNAPSHOT_BYTES = 1024 * 1024 * 1024


class SourceSnapshotError(ValueError):
    """A committed source snapshot cannot be read without losing information."""


def _strict_json(value: str | bytes) -> Any:
    """Never select one of two same-key source readings during deserialization."""
    def unique_object(pairs):
        result = {}
        for key, child in pairs:
            if key in result:
                raise SourceSnapshotError(f"Duplicate source JSON key: {key}")
            result[key] = child
        return result

    def invalid_constant(value):
        raise SourceSnapshotError("Nonfinite source JSON number")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            invalid_constant(value)
        return number

    return json.loads(value, object_pairs_hook=unique_object,
                      parse_constant=invalid_constant, parse_float=finite_float)


def _attributes(record: dict) -> dict:
    result = {}
    for attribute in record.get("vendor_attributes") or []:
        name = attribute.get("name")
        if name in {"ext", "bundleMeta", "studyId", "buildHash", "refKey"}:
            if name in result:
                raise SourceSnapshotError(f"Duplicate snapshot attribute: {name}")
            result[name] = attribute.get("value")
    return result


def is_source_snapshot(record: dict) -> bool:
    return str(record.get("oid") or "").startswith(SOURCE_SNAPSHOT_OID_PREFIX) or any(
        attr.get("name") == "refKey"
        and str(attr.get("value") or "").startswith(SOURCE_SNAPSHOT_REF_PREFIX)
        for attr in record.get("vendor_attributes") or []
    )


def _decode(value: str, limit: int) -> bytes:
    if not isinstance(value, str):
        raise SourceSnapshotError("Source snapshot carrier must be text")
    if value.startswith("gzip+base64:"):
        compressed = base64.b64decode(value[12:], validate=True)
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                raw = stream.read(limit + 1)
        except zlib.error as error:
            raise SourceSnapshotError("Invalid source snapshot compression") from error
    else:
        raw = value.encode("utf-8")
    if len(raw) > limit:
        raise SourceSnapshotError("Source snapshot exceeds the decoded byte limit")
    return raw


def _descriptor(record: dict) -> tuple[dict, dict]:
    attrs = _attributes(record)
    try:
        descriptor = _strict_json(_decode(attrs["ext"], 1024 * 1024))[
            "semanticSourceSnapshot"
        ]
    except (KeyError, TypeError, ValueError, OSError, EOFError) as error:
        raise SourceSnapshotError("Invalid snapshot manifest") from error
    if not isinstance(descriptor, dict):
        raise SourceSnapshotError("Snapshot manifest must be an object")
    if record.get("item_groups"):
        raise SourceSnapshotError("Source snapshot carrier has clinical children")
    return descriptor, attrs


def read_source_snapshot(
    records: list[dict],
    study_uid: str | None,
    source_study_ids: set[str],
) -> tuple[dict[str, Any], list[dict]] | None:
    """Return a verified source object and its native carrier records, or none.

    Hash the stored bytes, not reserialized JSON: numbers, Unicode and whitespace
    must not acquire language-dependent interpretations during verification.
    Uncommitted chunks and previous generations are never selected as a head.
    """
    if not study_uid:
        return None
    head_oid = f"{SOURCE_SNAPSHOT_OID_PREFIX}HEAD.{study_uid}"
    heads = [record for record in records if record.get("oid") == head_oid]
    if not heads:
        if any(
            str(record.get("oid") or "").startswith(
                f"{SOURCE_SNAPSHOT_OID_PREFIX}{study_uid}."
            )
            for record in records
        ):
            raise SourceSnapshotError("Source snapshot chunks have no committed head")
        return None
    if len(heads) != 1:
        raise SourceSnapshotError("Ambiguous committed source snapshot head")
    head = heads[0]
    manifest, attrs = _descriptor(head)
    if (
        manifest.get("formatVersion") != "1.0"
        or manifest.get("role") != "head"
        or manifest.get("osbStudyUid") != study_uid
        or manifest.get("sourceStudyId") not in source_study_ids
        or attrs.get("studyId") != manifest.get("sourceStudyId")
        or attrs.get("buildHash") != manifest.get("sourceBuildHash")
        or not isinstance(manifest.get("sourceBuildHash"), str)
        or not manifest["sourceBuildHash"]
    ):
        raise SourceSnapshotError("Committed source snapshot identity/build mismatch")
    count = manifest.get("chunkCount")
    if type(count) is not int or count < 1:
        raise SourceSnapshotError("Invalid snapshot chunk count")
    for key in ("snapshotHash", "encodedHash"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(key) or "")):
            raise SourceSnapshotError(f"Invalid snapshot {key}")
    generation = manifest.get("generationHash", manifest["snapshotHash"])
    if (
        "generationHash" in manifest
        and generation
        != hashlib.sha256(
            (manifest["snapshotHash"] + "\n" + manifest["sourceBuildHash"]).encode(
                "utf-8"
            )
        ).hexdigest()
    ):
        raise SourceSnapshotError("Source snapshot generation hash mismatch")
    byte_length = manifest.get("byteLength")
    if byte_length is not None and (
        type(byte_length) is not int
        or not 1 <= byte_length <= MAXIMUM_SOURCE_SNAPSHOT_BYTES
    ):
        raise SourceSnapshotError("Invalid source snapshot byte length")
    prefix = f"{SOURCE_SNAPSHOT_OID_PREFIX}{study_uid}.{generation}."
    selected = [
        record for record in records if str(record.get("oid") or "").startswith(prefix)
    ]
    if len(selected) != count:
        raise SourceSnapshotError(
            "Missing or duplicate committed source snapshot chunks"
        )
    chunks: dict[int, str] = {}
    manifest_keys = (
        "formatVersion",
        "osbStudyUid",
        "sourceStudyId",
        "sourceBuildHash",
        "snapshotHash",
        "encodedHash",
        "chunkCount",
    )
    for record in selected:
        descriptor, chunk_attrs = _descriptor(record)
        index = descriptor.get("index")
        chunk = chunk_attrs.get("bundleMeta")
        if (
            descriptor.get("role") != "chunk"
            or any(descriptor.get(key) != manifest[key] for key in manifest_keys)
            or descriptor.get("generationHash") != manifest.get("generationHash")
            or descriptor.get("byteLength") != byte_length
            or type(index) is not int
            or not 1 <= index <= count
            or index in chunks
            or record.get("oid") != f"{prefix}{index:04d}"
            or chunk_attrs.get("studyId") != manifest["sourceStudyId"]
            or chunk_attrs.get("buildHash") != manifest["sourceBuildHash"]
            or not isinstance(chunk, str)
        ):
            raise SourceSnapshotError("Conflicting committed source snapshot chunk")
        if hashlib.sha256(chunk.encode("utf-8")).hexdigest() != descriptor.get(
            "chunkHash"
        ):
            raise SourceSnapshotError("Source snapshot chunk hash mismatch")
        chunks[index] = chunk
    encoded = "".join(chunks[index] for index in range(1, count + 1))
    if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != manifest["encodedHash"]:
        raise SourceSnapshotError("Source snapshot encoded hash mismatch")
    try:
        raw = _decode(encoded, MAXIMUM_SOURCE_SNAPSHOT_BYTES)
        if byte_length is not None and len(raw) != byte_length:
            raise SourceSnapshotError("Source snapshot byte length mismatch")
        if hashlib.sha256(raw).hexdigest() != manifest["snapshotHash"]:
            raise SourceSnapshotError("Source snapshot content hash mismatch")
        snapshot = _strict_json(raw)
    except (TypeError, ValueError, OSError, EOFError) as error:
        raise SourceSnapshotError(
            "Invalid committed source snapshot content"
        ) from error
    if not isinstance(snapshot, dict) or not snapshot:
        raise SourceSnapshotError("Source snapshot must contain a source bundle object")
    return snapshot, [head, *selected]
