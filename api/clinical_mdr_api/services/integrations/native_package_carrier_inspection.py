# Stored JSON integer and boolean types must remain distinct (bool is an int).
# pylint: disable=unidiomatic-typecheck

"""Immutable, bounded inspection of already verified carrier capture inputs.

The caller owns authentication, source closure verification and the transaction.
An index is published with its capture only after native freshness is rechecked.
Neither readers nor preparation follow locators or reconstruct source authority.
"""

from __future__ import annotations

import re
from typing import Any, NoReturn

from neomodel import db

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    hash_refs_equal,
    sha256_bytes,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from clinical_mdr_api.services.integrations.edc_study_exchange import (
    _pointer,
    _terminals,
)
from clinical_mdr_api.services.integrations.native_package_artifact_custody import (
    MAX_CARRIER_ARTIFACT_BYTES,
    _canonical_payload,
    _stored_bytes,
)
from clinical_mdr_api.services.integrations.native_package_carrier_contracts import (
    validate_artifact_ref,
    validate_carrier_contract,
)

PAGE_SIZE_LIMIT = 100
PAGE_BYTE_LIMIT = 262144
VALUE_SCHEMA = "OsbCarrierInspectionValueV1@1.0.0"
SECTIONS = ("crosswalkRows", "terminalAtoms", "nativeRecords", "canonicalDefinition")
REFERENCE_NAMES = (
    "carrierArtifact",
    "nativeStateArtifact",
    "canonicalDefinitionArtifact",
    "bundleArtifact",
    "crosswalkArtifact",
)


def _fail(code: str = "OSB_CARRIER_INSPECTION_INVALID", status: int = 409) -> NoReturn:
    raise OsbCandidateSetError(
        code, "The exact scoped carrier inspection is unavailable or invalid.", status
    ) from None


def _canonical(value: Any) -> str:
    try:
        return canonical_json(value)
    except ValueError, TypeError, UnicodeError, OverflowError, RecursionError:
        _fail()


def _query(statement: str, parameters: dict[str, Any]) -> list[Any]:
    try:
        rows, _ = db.cypher_query(statement, parameters)
        return rows
    except Exception:  # pylint: disable=broad-exception-caught
        _fail("OSB_CARRIER_INSPECTION_UNAVAILABLE", 503)


def _scope(tenant_id: str, platform_study_id: str) -> dict[str, str]:
    if any(
        not isinstance(item, str) or not item for item in (tenant_id, platform_study_id)
    ):
        _fail("OSB_CARRIER_INSPECTION_SCOPE_INVALID", 422)
    return {"tenant_id": tenant_id, "platform_study_id": platform_study_id}


def _references(references: dict[str, Any], tenant_id: str) -> None:
    for reference in references.values():
        try:
            validate_artifact_ref(reference)
        except ValueError, TypeError, UnicodeError, OverflowError, RecursionError:
            _fail("OSB_CARRIER_INSPECTION_REFERENCE_INVALID", 422)
        if reference["tenantId"] != tenant_id:
            _fail("OSB_CARRIER_INSPECTION_SCOPE_MISMATCH", 403)


def _index_key(scope: dict[str, str], references: dict[str, Any]) -> str:
    return sha256_bytes(
        _canonical(
            {
                "contractVersion": "OsbCarrierInspectionIndexV1@1.0.0",
                **scope,
                **{name: references[name] for name in REFERENCE_NAMES[:3]},
            }
        )
    )


def _payload_matches(payload: Any, reference: dict[str, Any]) -> None:
    raw = _canonical(payload).encode("utf-8")
    if (
        len(raw) != reference["byteSize"]
        or sha256_bytes(raw) != reference["payloadHash"]["value"]
    ):
        _fail("OSB_CARRIER_INSPECTION_SOURCE_MISMATCH")


def _resolve(payload: Any, pointer: str) -> Any:
    if (
        not isinstance(pointer, str)
        or not pointer.startswith("/")
        or re.search(r"~(?![01])", pointer)
    ):
        _fail("OSB_CARRIER_INSPECTION_POINTER_INVALID", 422)
    try:
        return _pointer(payload, pointer)
    except ValueError, TypeError, OverflowError, RecursionError:
        _fail("OSB_CARRIER_INSPECTION_POINTER_INVALID", 422)


def _matches_hash(value: Any, reference: dict[str, Any]) -> bool:
    return hash_refs_equal(
        reference,
        canonical_json_hash_ref(
            value,
            schema_version=reference["schemaVersion"],
            media_type=reference["mediaType"],
        ),
    )


def _descriptor(header: dict[str, Any]) -> dict[str, Any]:
    return {
        name: header[name]
        for name in ("indexKey", "counts", "pageSizeLimit", "pageByteLimit")
    }


def _page(
    section: str, offset: int, total: int, items: list[dict[str, Any]]
) -> dict[str, Any]:
    next_offset = offset + len(items)
    return {
        "section": section,
        "offset": offset,
        "total": total,
        "items": items,
        "nextOffset": next_offset if next_offset < total else None,
        "pageSizeLimit": PAGE_SIZE_LIMIT,
        "pageByteLimit": PAGE_BYTE_LIMIT,
    }


def _native_source(
    record: dict[str, Any], position: int, references: dict[str, Any]
) -> dict[str, Any]:
    return {
        "stateArtifact": references["nativeStateArtifact"],
        "contentIndexPointer": f"/contentIndex/{position}",
        "recordHash": canonical_json_hash_ref(
            record, schema_version="OsbPackageNativeRecordV1@1.0.0"
        ),
    }


def _entries(crosswalk, bundle, records, canonical_definition, references):
    record_by_operation = {}
    for position, record in enumerate(records):
        operation_id = record.get("operationId") if isinstance(record, dict) else None
        if (
            not isinstance(operation_id, str)
            or not operation_id
            or operation_id in record_by_operation
        ):
            _fail("OSB_CARRIER_INSPECTION_NATIVE_RECORD_INVALID")
        record_by_operation[operation_id] = (position, record)
    atom_position = 0
    for row_index, row in enumerate(crosswalk["rows"]):
        validate_carrier_contract(row, "CrosswalkRow")
        row_source = {
            "artifact": references["crosswalkArtifact"],
            "pointer": f"/rows/{row_index}",
        }
        yield "crosswalkRows", row_index, {
            "rowIndex": row_index,
            "item": row,
        }, row_source
        evidence = row["evidence"]
        matches = {}
        native_record = None
        native_position = None
        if evidence["disposition"] == "native-observed":
            resolved = record_by_operation.get(evidence["operationId"])
            if resolved is None:
                _fail("OSB_CARRIER_INSPECTION_NATIVE_RECORD_MISSING")
            native_position, native_record = resolved
            if not hash_refs_equal(
                evidence["nativeRecordHash"],
                canonical_json_hash_ref(
                    native_record, schema_version="OsbPackageNativeRecordV1@1.0.0"
                ),
            ):
                _fail("OSB_CARRIER_INSPECTION_NATIVE_RECORD_MISMATCH")
            for match in evidence["fieldMatches"]:
                if match["bundlePointer"] in matches:
                    _fail("OSB_CARRIER_INSPECTION_FIELD_MATCH_AMBIGUOUS")
                matches[match["bundlePointer"]] = match
        for atom_index, atom in enumerate(row["coverageAtoms"]):
            actual = _resolve(bundle, atom["pointer"])
            try:
                terminal = next(_terminals(actual, atom["pointer"]))
            except ValueError, TypeError, RecursionError, StopIteration:
                _fail("OSB_CARRIER_INSPECTION_ATOM_MISMATCH")
            if terminal[:2] != (atom["pointer"], atom["type"]) or not _matches_hash(
                actual, atom["valueHash"]
            ):
                _fail("OSB_CARRIER_INSPECTION_ATOM_MISMATCH")
            match = matches.get(atom["pointer"])
            native_value = None
            source = {
                "crosswalk": row_source,
                "bundle": {
                    "artifact": references["bundleArtifact"],
                    "pointer": atom["pointer"],
                },
            }
            if match is not None:
                native_value = _resolve(
                    native_record.get("payload"), match["nativePointer"]
                )
                if not _matches_hash(
                    actual, match["bundleValueHash"]
                ) or not _matches_hash(native_value, match["observedValueHash"]):
                    _fail("OSB_CARRIER_INSPECTION_FIELD_MATCH_MISMATCH")
                source["nativeRecord"] = {
                    **_native_source(native_record, native_position, references),
                    "nativePointer": match["nativePointer"],
                }
            yield "terminalAtoms", atom_position, {
                "rowIndex": row_index,
                "atomIndex": atom_index,
                "member": row["member"],
                "disposition": evidence["disposition"],
                "atom": atom,
                "actualValue": actual,
                "fieldMatch": match,
                "nativeValue": native_value,
            }, source
            atom_position += 1
    for position, record in enumerate(records):
        yield "nativeRecords", position, record, _native_source(
            record, position, references
        )
    yield "canonicalDefinition", 0, canonical_definition, {
        "artifact": references["canonicalDefinitionArtifact"],
        "pointer": "",
    }


def prepare_carrier_inspection(
    *,
    tenant_id: str,
    platform_study_id: str,
    carrier_artifact: dict[str, Any],
    native_state_artifact: dict[str, Any],
    canonical_definition_artifact: dict[str, Any],
    bundle_artifact: dict[str, Any],
    crosswalk_artifact: dict[str, Any],
    crosswalk: dict[str, Any],
    bundle: dict[str, Any],
    records: list[dict[str, Any]],
    canonical_definition: dict[str, Any],
) -> dict[str, Any]:
    """Prepare within the capture transaction; never independently publish this index."""
    scope = _scope(tenant_id, platform_study_id)
    references = dict(
        zip(
            REFERENCE_NAMES,
            (
                carrier_artifact,
                native_state_artifact,
                canonical_definition_artifact,
                bundle_artifact,
                crosswalk_artifact,
            ),
        )
    )
    _references(references, tenant_id)
    for payload, reference in (
        (crosswalk, crosswalk_artifact),
        (bundle, bundle_artifact),
        (canonical_definition, canonical_definition_artifact),
    ):
        _payload_matches(payload, reference)
    if (
        not isinstance(crosswalk, dict)
        or not isinstance(crosswalk.get("rows"), list)
        or not isinstance(records, list)
    ):
        _fail("OSB_CARRIER_INSPECTION_SOURCE_INVALID", 422)
    try:
        counts = {
            "crosswalkRows": len(crosswalk["rows"]),
            "terminalAtoms": sum(
                len(row["coverageAtoms"]) for row in crosswalk["rows"]
            ),
            "nativeRecords": len(records),
            "canonicalDefinition": 1,
        }
    except KeyError, TypeError:
        _fail("OSB_CARRIER_INSPECTION_SOURCE_INVALID", 422)
    index_key = _index_key(scope, references)
    parameters = {**scope, "index_key": index_key}
    header = {
        **scope,
        **references,
        "indexKey": index_key,
        "counts": counts,
        "pageSizeLimit": PAGE_SIZE_LIMIT,
        "pageByteLimit": PAGE_BYTE_LIMIT,
    }
    payload_json = _canonical(header)
    header_fields = {
        "payload_json": payload_json,
        "payload_hash": sha256_bytes(payload_json),
        "byte_size": len(payload_json.encode("utf-8")),
    }
    if header_fields["byte_size"] > PAGE_BYTE_LIMIT:
        _fail("OSB_CARRIER_INSPECTION_METADATA_SIZE_LIMIT", 413)
    rows = _query(
        """MERGE (inspection:OsbCarrierInspection {
        tenant_id:$tenant_id,platform_study_id:$platform_study_id,index_key:$index_key})
        ON CREATE SET inspection.payload_json=$payload_json,
          inspection.payload_hash=$payload_hash,inspection.byte_size=$byte_size
        RETURN inspection.payload_json,inspection.payload_hash,inspection.byte_size""",
        {**parameters, **header_fields},
    )
    if rows != [
        [header_fields[name] for name in ("payload_json", "payload_hash", "byte_size")]
    ]:
        _fail("OSB_CARRIER_INSPECTION_CONFLICT")
    pending = []
    pending_size = 0
    for section, position, value, source in _entries(
        crosswalk, bundle, records, canonical_definition, references
    ):
        value_json = _canonical(value)
        value_bytes = value_json.encode("utf-8")
        if len(value_bytes) > MAX_CARRIER_ARTIFACT_BYTES:
            _fail("OSB_CARRIER_INSPECTION_ENTRY_SIZE_LIMIT", 413)
        metadata = {
            "section": section,
            "position": position,
            "source": source,
            "valueHash": canonical_json_hash_ref(value, schema_version=VALUE_SCHEMA),
            "byteSize": len(value_bytes),
            "oversize": len(
                _canonical(
                    _page(
                        section,
                        position,
                        counts[section],
                        [{"position": position, "value": value}],
                    )
                ).encode("utf-8")
            )
            > PAGE_BYTE_LIMIT,
        }
        metadata_json = _canonical(metadata)
        fields = {
            "section": section,
            "position": position,
            "payload_json": metadata_json,
            "payload_hash": sha256_bytes(metadata_json),
            "byte_size": len(metadata_json.encode("utf-8")),
            "value_json": value_json,
            "value_size": len(value_bytes),
            "oversize": metadata["oversize"],
        }
        if fields["byte_size"] > PAGE_BYTE_LIMIT:
            _fail("OSB_CARRIER_INSPECTION_METADATA_SIZE_LIMIT", 413)
        size = fields["byte_size"] + fields["value_size"]
        if pending and (
            len(pending) == PAGE_SIZE_LIMIT or pending_size + size > 1024 * 1024
        ):
            _store_entries(parameters, pending)
            pending = []
            pending_size = 0
        pending.append({**fields, "ordinal": len(pending)})
        pending_size += size
    if pending:
        _store_entries(parameters, pending)
    return _descriptor(header)


def _store_entries(parameters: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    rows = _query(
        """UNWIND $entries AS item
        MERGE (entry:OsbCarrierInspectionEntry {
          tenant_id:$tenant_id,platform_study_id:$platform_study_id,index_key:$index_key,
          section:item.section,position:item.position})
        ON CREATE SET entry.payload_json=item.payload_json,entry.payload_hash=item.payload_hash,
          entry.byte_size=item.byte_size,entry.value_json=item.value_json,
          entry.value_size=item.value_size,entry.oversize=item.oversize
        RETURN entry.section,entry.position,entry.payload_json,entry.payload_hash,entry.byte_size,
          entry.value_json,entry.value_size,entry.oversize ORDER BY item.ordinal""",
        {**parameters, "entries": entries},
    )
    if rows != [
        [
            entry[name]
            for name in (
                "section",
                "position",
                "payload_json",
                "payload_hash",
                "byte_size",
                "value_json",
                "value_size",
                "oversize",
            )
        ]
        for entry in entries
    ]:
        _fail("OSB_CARRIER_INSPECTION_CONFLICT")


def _read_json(value: Any, digest: Any, byte_size: Any, ceiling: int) -> dict[str, Any]:
    if type(byte_size) is not int or not 0 < byte_size <= ceiling:
        _fail()
    raw = _stored_bytes(value, byte_size)
    if sha256_bytes(raw) != digest:
        _fail()
    return _canonical_payload(raw, "OSB_CARRIER_INSPECTION_INVALID")


def _read_header(scope: dict[str, str], index_key: str) -> dict[str, Any]:
    if not isinstance(index_key, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", index_key
    ):
        _fail("OSB_CARRIER_INSPECTION_SELECTOR_INVALID", 422)
    rows = _query(
        """MATCH (inspection:OsbCarrierInspection {
        tenant_id:$tenant_id,platform_study_id:$platform_study_id,index_key:$index_key})
        RETURN inspection.payload_json,inspection.payload_hash,inspection.byte_size""",
        {**scope, "index_key": index_key},
    )
    if not rows:
        _fail("OSB_CARRIER_INSPECTION_NOT_FOUND", 404)
    if len(rows) != 1 or not isinstance(rows[0], (list, tuple)) or len(rows[0]) != 3:
        _fail()
    header = _read_json(*rows[0], PAGE_BYTE_LIMIT)
    if set(header) != set(scope) | set(REFERENCE_NAMES) | {
        "indexKey",
        "counts",
        "pageSizeLimit",
        "pageByteLimit",
    }:
        _fail()
    if any(header[key] != value for key, value in scope.items()):
        _fail()
    _references({name: header[name] for name in REFERENCE_NAMES}, scope["tenant_id"])
    if header["indexKey"] != index_key or _index_key(scope, header) != index_key:
        _fail()
    counts = header["counts"]
    if (
        not isinstance(counts, dict)
        or set(counts) != set(SECTIONS)
        or any(type(value) is not int or value < 0 for value in counts.values())
        or counts["canonicalDefinition"] != 1
    ):
        _fail()
    if (
        header["pageSizeLimit"] != PAGE_SIZE_LIMIT
        or header["pageByteLimit"] != PAGE_BYTE_LIMIT
    ):
        _fail()
    return header


def read_carrier_inspection_descriptor(
    *,
    tenant_id: str,
    platform_study_id: str,
    carrier_artifact: dict[str, Any],
    native_state_artifact: dict[str, Any],
    canonical_definition_artifact: dict[str, Any],
) -> dict[str, Any]:
    scope = _scope(tenant_id, platform_study_id)
    references = dict(
        zip(
            REFERENCE_NAMES[:3],
            (carrier_artifact, native_state_artifact, canonical_definition_artifact),
        )
    )
    _references(references, tenant_id)
    header = _read_header(scope, _index_key(scope, references))
    if any(
        _canonical(header[name]) != _canonical(reference)
        for name, reference in references.items()
    ):
        _fail()
    return _descriptor(header)


def _selector(section: str, position: int, limit: int = 1) -> None:
    if not isinstance(section, str) or section not in SECTIONS:
        _fail("OSB_CARRIER_INSPECTION_SELECTOR_INVALID", 422)
    if (
        type(position) is not int
        or position < 0
        or type(limit) is not int
        or not 1 <= limit <= PAGE_SIZE_LIMIT
    ):
        _fail("OSB_CARRIER_INSPECTION_SELECTOR_INVALID", 422)


def _read_entry(row, section: str, position: int, include_oversize: bool = False):
    if (
        not isinstance(row, (list, tuple))
        or len(row) != 7
        or type(row[0]) is not int
        or row[0] != position
    ):
        _fail()
    metadata = _read_json(row[1], row[2], row[3], PAGE_BYTE_LIMIT)
    if set(metadata) != {
        "section",
        "position",
        "source",
        "valueHash",
        "byteSize",
        "oversize",
    }:
        _fail()
    if (
        metadata["section"] != section
        or type(metadata["position"]) is not int
        or metadata["position"] != position
        or type(metadata["oversize"]) is not bool
    ):
        _fail()
    if (
        type(row[5]) is not int
        or row[5] != metadata["byteSize"]
        or type(row[6]) is not bool
        or row[6] != metadata["oversize"]
    ):
        _fail()
    if not 0 < row[5] <= MAX_CARRIER_ARTIFACT_BYTES or not isinstance(
        metadata["source"], dict
    ):
        _fail()
    if metadata["oversize"] and not include_oversize:
        if row[4] is not None:
            _fail()
        return metadata, None, None
    raw = _stored_bytes(row[4], row[5])
    value = _canonical_payload(raw, "OSB_CARRIER_INSPECTION_INVALID")
    if not hash_refs_equal(
        metadata["valueHash"],
        canonical_json_hash_ref(value, schema_version=VALUE_SCHEMA),
    ):
        _fail()
    return metadata, value, raw


def _oversize(metadata: dict[str, Any]) -> NoReturn:
    error = OsbCandidateSetError(
        "OSB_CARRIER_INSPECTION_ENTRY_TOO_LARGE",
        "This inspection entry requires exact indexed-entry or source-artifact download.",
        413,
    )
    error.details = {
        name: metadata[name] for name in ("section", "position", "valueHash", "source")
    }
    raise error


def read_carrier_inspection_page(
    *,
    tenant_id: str,
    platform_study_id: str,
    index_key: str,
    section: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    _selector(section, offset, limit)
    scope = _scope(tenant_id, platform_study_id)
    header = _read_header(scope, index_key)
    total = header["counts"][section]
    if offset > total:
        _fail("OSB_CARRIER_INSPECTION_SELECTOR_INVALID", 422)
    count = min(limit, total - offset)
    if not count:
        return _page(section, offset, total, [])
    rows = _query(
        """MATCH (entry:OsbCarrierInspectionEntry {
        tenant_id:$tenant_id,platform_study_id:$platform_study_id,index_key:$index_key,section:$section})
        WHERE entry.position >= $offset AND entry.position < $end
        RETURN entry.position,entry.payload_json,entry.payload_hash,entry.byte_size,
          CASE WHEN entry.oversize THEN null ELSE entry.value_json END,
          entry.value_size,entry.oversize
        ORDER BY entry.position LIMIT $row_limit""",
        {
            **scope,
            "index_key": index_key,
            "section": section,
            "offset": offset,
            "end": offset + count,
            "row_limit": count + 1,
        },
    )
    if len(rows) != count:
        _fail()
    items = []
    for index, row in enumerate(rows):
        metadata, value, _ = _read_entry(row, section, offset + index)
        if metadata["oversize"]:
            if not items:
                _oversize(metadata)
            break
        candidate = [*items, {"position": offset + index, "value": value}]
        if (
            len(_canonical(_page(section, offset, total, candidate)).encode("utf-8"))
            > PAGE_BYTE_LIMIT
        ):
            if not items:
                _oversize(metadata)
            break
        items = candidate
    return _page(section, offset, total, items)


def read_carrier_inspection_entry(
    *,
    tenant_id: str,
    platform_study_id: str,
    index_key: str,
    section: str,
    position: int,
) -> dict[str, Any]:
    """Return exact indexed canonical value bytes, not a new artifact identity."""
    _selector(section, position)
    scope = _scope(tenant_id, platform_study_id)
    header = _read_header(scope, index_key)
    if position >= header["counts"][section]:
        _fail("OSB_CARRIER_INSPECTION_SELECTOR_INVALID", 422)
    rows = _query(
        """MATCH (entry:OsbCarrierInspectionEntry {
        tenant_id:$tenant_id,platform_study_id:$platform_study_id,index_key:$index_key,
        section:$section,position:$position})
        RETURN entry.position,entry.payload_json,entry.payload_hash,entry.byte_size,
          entry.value_json,entry.value_size,entry.oversize""",
        {**scope, "index_key": index_key, "section": section, "position": position},
    )
    if len(rows) != 1:
        _fail()
    metadata, _, raw = _read_entry(rows[0], section, position, include_oversize=True)
    return {
        "bytes": raw,
        **{
            name: metadata[name]
            for name in ("section", "position", "valueHash", "source")
        },
    }
