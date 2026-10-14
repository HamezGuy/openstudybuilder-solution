"""Independent exact terminal accounting over the retained carrier bytes.

CSL owns accepted-source reconstruction and the canonical builder. This module
checks the bytes OSB actually retains, including native field equality, rather
than asserting that a count supplied by another service proves conservation.
"""

import re
from typing import Any

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
from clinical_mdr_api.services.integrations.native_package_carrier_contracts import (
    validate_carrier_contract,
)


def _require(condition):
    if not condition:
        raise OsbCandidateSetError(
            "OSB_CARRIER_COVERAGE_MISMATCH",
            "Carrier terminal accounting differs from retained bytes.",
            409,
        )


def _hash(value, schema):
    return canonical_json_hash_ref(value, schema_version=schema)


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _under(pointer, parent):
    return pointer == parent or pointer.startswith(parent + "/")


def _resolve(value, pointer):
    _require(isinstance(pointer, str) and not re.search(r"~(?![01])", pointer))
    try:
        return _pointer(value, pointer)
    except (ValueError, TypeError, RecursionError) as error:
        raise OsbCandidateSetError(
            "OSB_CARRIER_COVERAGE_POINTER_INVALID",
            "An exact carrier pointer is unavailable.",
            422,
        ) from error


def _legacy_csl_json(value: Any) -> str:
    """Match CSL's existing sorted-object, JSON.stringify enumeration profile."""
    if isinstance(value, list):
        return "[" + ",".join(_legacy_csl_json(item) for item in value) + "]"
    if isinstance(value, dict):

        def index(key):
            return (
                key.isascii()
                and key.isdigit()
                and str(int(key)) == key
                and int(key) < 2**32 - 1
            )

        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        ordered = sorted((key for key in keys if index(key)), key=int) + [
            key for key in keys if not index(key)
        ]
        return (
            "{"
            + ",".join(
                canonical_json(key) + ":" + _legacy_csl_json(value[key])
                for key in ordered
            )
            + "}"
        )
    return canonical_json(value)


def _member_value(bundle, member):
    pointer, kind = member["pointer"], member["kind"]
    value = _resolve(bundle, pointer)
    if kind == "scoped-json-value":
        _resolve(bundle, member["scopePointer"])
        _require(_under(pointer, member["scopePointer"]))
    elif kind == "definition":
        _require(pointer == "/definition")
    elif kind == "visit-form-assignment":
        _require(
            re.fullmatch(r"/execution/visitFormAssignments/(?:0|[1-9]\d*)", pointer)
            and isinstance(value, dict)
            and all(member[key] == value.get(key) for key in ("visitRef", "formRef"))
        )
    elif kind in {"visit", "site"}:
        collection = "visits" if kind == "visit" else "sites"
        _require(
            re.fullmatch(f"/execution/{collection}/(?:0|[1-9]\\d*)", pointer)
            and isinstance(value, dict)
            and member[kind + "Ref"] == value.get("refKey")
        )
    else:
        match = re.fullmatch(
            r"/execution/forms/forms/(0|[1-9]\d*)(?:/(fields|repetition/groups)/(0|[1-9]\d*))?",
            pointer,
        )
        _require(match is not None and isinstance(value, dict))
        form = _resolve(bundle, "/execution/forms/forms/" + match[1])
        _require(member["formRef"] == form.get("refKey"))
        if kind == "form":
            _require(match[2] is None)
        else:
            _require(
                match[2] == ("fields" if kind == "field" else "repetition/groups")
                and member["fieldRef" if kind == "field" else "groupRef"]
                == value.get("refKey" if kind == "field" else "groupKey")
            )
    return value


def verify_carrier_terminal_accounting(
    *, bundle, crosswalk, census, carrier, authority, records, lineages
):
    validate_carrier_contract(crosswalk, "Crosswalk")
    terminals = [
        {
            "pointer": pointer,
            "type": kind,
            "valueHash": _hash(value, "AuthoredExecutionTerminalValueV1@1.0.0"),
        }
        for root in ("/definition/execution", "/execution")
        for pointer, kind, value in _terminals(_resolve(bundle, root), root)
    ]
    by_pointer = {
        atom["pointer"]: (position, atom) for position, atom in enumerate(terminals)
    }
    native_records = {record["operationId"]: record for record in records}
    _require(len(native_records) == len(records))
    assigned, members = set(), set()
    counts = {
        "members": len(crosswalk["rows"]),
        "atoms": len(terminals),
        "nativeObservedAtoms": 0,
        "acceptedCanonicalExtensionAtoms": 0,
        "retainedSourceOnlyAtoms": 0,
        "unmappedAtoms": 0,
        "multiplyAssignedAtoms": 0,
        "unsupportedExecutionAtoms": 0,
    }
    disposition_keys = {
        "native-observed": "nativeObservedAtoms",
        "accepted-canonical-extension": "acceptedCanonicalExtensionAtoms",
        "retained-source-only": "retainedSourceOnlyAtoms",
    }
    authority_hash = _hash(authority, "AcceptedCanonicalStudyAuthorityV1@1.0.0")
    for row in crosswalk["rows"]:
        member, evidence, coverage = (
            row["member"],
            row["evidence"],
            row["coverageAtoms"],
        )
        identity = canonical_json(member)
        _require(identity not in members)
        members.add(identity)
        _require(
            hash_refs_equal(
                row["memberValueHash"],
                _hash(
                    {"member": member, "value": _member_value(bundle, member)},
                    "AuthoredStudyMemberValueV1@1.0.0",
                ),
            )
        )
        previous = -1
        for atom in coverage:
            pointer = atom["pointer"]
            _require(
                pointer in by_pointer
                and pointer not in assigned
                and _under(pointer, member["pointer"])
            )
            position, actual = by_pointer[pointer]
            _require(position > previous and _same(atom, actual))
            previous = position
            assigned.add(pointer)
        disposition = evidence["disposition"]
        counts[disposition_keys[disposition]] += len(coverage)
        if disposition == "native-observed":
            record = native_records.get(evidence["operationId"])
            _require(
                record is not None
                and record.get("kind") == "native"
                and record.get("sourceProjectionVerified") is True
            )
            _require(
                any(
                    lineage["state"]["nativeEvidenceSetArtifact"]["artifactVersionId"]
                    == evidence["evidenceSetArtifactVersionId"]
                    and any(_same(item, record) for item in lineage["state"]["records"])
                    for lineage in lineages
                )
            )
            payload = record["payload"]
            _require(
                hash_refs_equal(
                    evidence["nativeRecordHash"],
                    _hash(record, "OsbPackageNativeRecordV1@1.0.0"),
                )
                and _same(evidence["readBackHash"], record["readBackHash"])
                and _same(evidence["targetIdentity"], record["targetIdentity"])
                and _same(
                    evidence["sourceInputHash"],
                    payload["sourceBinding"]["sourceInputHash"],
                )
                and evidence["bindingKey"] == payload["sourceBinding"]["bindingKey"]
            )
            matched = set()
            for field in evidence["fieldMatches"]:
                pointer = field["bundlePointer"]
                _require(
                    pointer not in matched
                    and any(atom["pointer"] == pointer for atom in coverage)
                )
                matched.add(pointer)
                actual = _hash(
                    _resolve(bundle, pointer), "AuthoredExecutionTerminalValueV1@1.0.0"
                )
                native = _hash(
                    _resolve(payload, field["nativePointer"]),
                    "AuthoredExecutionTerminalValueV1@1.0.0",
                )
                _require(
                    _same(field["bundleValueHash"], actual)
                    and _same(field["observedValueHash"], native)
                    and _same(actual, native)
                )
            _require(len(matched) == len(coverage))
        else:
            _require(hash_refs_equal(evidence["authorityHash"], authority_hash))
            if disposition == "retained-source-only":
                _require(not coverage)
            else:
                ids = evidence["mappingDecisionIds"]
                _require(
                    len(set(ids)) == len(ids)
                    and set(ids)
                    <= {pin["decisionId"] for pin in authority["mappingDecisions"]}
                )
    _require(assigned == set(by_pointer))
    expected_order = sorted(
        crosswalk["rows"],
        key=lambda row: tuple(
            value.encode("utf-16-be")
            for value in (
                row["member"]["pointer"],
                row["member"]["kind"],
                canonical_json(row["member"]),
            )
        ),
    )
    _require(
        _same(expected_order, crosswalk["rows"])
        and _same(
            census,
            bundle.get("extensions", {}).get("cslStudyBuild", {}).get("exportCensus"),
        )
    )
    result = {
        "contractVersion": "AuthoredExecutionConservationV1@1.0.0",
        "bundlePayloadHash": carrier["bundleArtifact"]["payloadHash"],
        "crosswalkRowsHash": crosswalk["rowsHash"],
        "exportCensusHash": carrier["exportCensusArtifact"]["payloadHash"],
        "valueLedgerHash": {
            "profile": "csl-semantic-custody/legacy",
            "value": sha256_bytes(_legacy_csl_json(bundle["source"]["valueLedger"])),
        },
        "counts": counts,
        "coverageProfile": "authored-execution-terminal-coverage/1.0.0",
        "terminalSetHash": _hash(terminals, "AuthoredExecutionTerminalSetV1@1.0.0"),
    }
    validate_carrier_contract(result, "Conservation")
    return result
