"""Opt-in, exact source/output observations; never correspondence authority.

Only call-site registrations are observed. Unregistered output is not inferred
from identifiers, labels, allocation order or an ``Id`` suffix. A consumer must
independently verify the pinned rules and complete USDM censuses before release.
"""

import hashlib
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
)
from common.exceptions import ValidationException

PROFILE = "OsbMapperCorrespondenceObservationsV1@1.0.0"
VALUE_PROFILE = "OsbCorrespondenceJsonValueV1@1.0.0"
RECORD_PROFILE = "OsbMapperNativeRecordV1@1.0.0"
RULES = (
    (
        "osb.authored-document.copy/1.0.0",
        r"/documents/[0-9]+",
        r"/study/documentedBy/[0-9]+",
        "StudyDefinitionDocument",
    ),
    (
        "osb.authored-narrative-item.copy/1.0.0",
        r"/narrative_content_items/[0-9]+",
        r"/study/versions/0/narrativeContentItems/[0-9]+",
        "NarrativeContentItem",
    ),
    (
        "osb.authored-organization.copy/1.0.0",
        r"/organizations/[0-9]+",
        r"/study/versions/0/organizations/[0-9]+",
        "Organization",
    ),
    (
        "osb.authored-first-document-version.reference/1.0.0",
        r"/documents/[0-9]+/versions/0/id",
        r"/study/versions/0/documentVersionIds/[0-9]+",
        "StudyDefinitionDocumentVersion.id",
    ),
)


def _hash(value: Any, profile: str) -> dict[str, Any]:
    return canonical_json_hash_ref(value, schema_version=profile)


def _fail(reason: str) -> None:
    raise ValidationException(msg=f"USDM_MAPPER_OBSERVATION_INVALID: {reason}")


def _safe_numbers(value: Any) -> None:
    """The new observation profile cannot qualify lossy JS-number hashes."""
    if isinstance(value, bool):
        return
    if isinstance(value, int) and not -(2**53 - 1) <= value <= 2**53 - 1:
        _fail("unsafe integer in exact observation")
    if isinstance(value, float) and not math.isfinite(value):
        _fail("nonfinite number in exact observation")
    if isinstance(value, float) and value == 0 and math.copysign(1, value) < 0:
        _fail("negative zero in exact observation")
    if isinstance(value, dict):
        for item in value.values():
            _safe_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _safe_numbers(item)


def _at(value: Any, pointer: str) -> Any:
    """Resolve exact JSON pointers, including null/empty/false/zero values."""
    if not isinstance(pointer, str) or not re.fullmatch(
        r"(?:/(?:[^~/]|~0|~1)*)*", pointer
    ):
        _fail("invalid JSON pointer")
    current = value
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key) or int(key) >= len(current):
                _fail("array pointer is absent")
            current = current[int(key)]
        elif isinstance(current, dict) and key in current:
            current = current[key]
        else:
            _fail("object pointer is absent")
    return current


def correspondence_ruleset() -> dict[str, Any]:
    """Describe these installed deterministic rules, including implementation pin.

    This is an observation manifest, not a consumer-supplied executable ruleset.
    Consumers must independently pin the installed implementation they support.
    """
    implementation = (
        "sha256:"
        + hashlib.sha256(
            Path(__file__).read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
    )
    return {
        "contractVersion": "OsbMapperObservationRulesetV1@1.0.0",
        "implementation": {
            "module": "clinical_mdr_api.services.ddf.usdm_correspondence_observations",
            "version": "1.0.0",
            "sourceCanonicalization": "utf8-lf",
            "sourceHash": implementation,
        },
        "rules": [
            {
                "ruleId": rule_id,
                "sourceKind": "studyAuthoredDocuments",
                "sourcePointerPattern": source,
                "outputPointerPattern": target,
                "inputClass": kind,
                "semantics": "exact-json-value-copy-after-finalization",
                "identityPolicy": "preserve-all-identifiers-and-reference-values",
                "nativeOperationAttribution": "none",
                "referenceValidation": "independent-pinned-USDM-dictionary-required",
            }
            for rule_id, source, target, kind in sorted(RULES)
        ],
    }


class CorrespondenceObservations:
    """Run-local immutable snapshots of explicit mapper call-site registrations."""

    def __init__(self) -> None:
        self._registrations: list[dict[str, Any]] = []
        self._targets: set[str] = set()
        self._sources: dict[str, dict[str, Any]] = {}
        self._source_hashes: dict[str, dict[str, Any]] = {}

    def retain_source(self, native_record: dict[str, Any]) -> str:
        """Snapshot/hash a retained source once; registrations use its handle."""
        if (
            native_record.get("kind") != "studyAuthoredDocuments"
            or not isinstance(native_record.get("uid"), str)
            or not native_record["uid"]
        ):
            _fail("native record identity")
        scope = native_record.get("scope")
        if not isinstance(scope, dict) or scope.get("studyUid") != native_record["uid"]:
            _fail("native record study scope")
        _safe_numbers(native_record)
        record_hash = _hash(native_record, RECORD_PROFILE)
        handle = record_hash["value"]
        if handle not in self._sources:
            self._sources[handle] = deepcopy(native_record)
            self._source_hashes[handle] = record_hash
        return handle

    def register(
        self,
        *,
        rule_id: str,
        source_handle: str,
        source_pointer: str,
        output_pointer: str,
    ) -> None:
        rules = [rule for rule in RULES if rule[0] == rule_id]
        if len(rules) != 1:
            _fail("unsupported rule")
        _, source_pattern, output_pattern, kind = rules[0]
        if source_handle not in self._sources:
            _fail("unknown immutable source handle")
        native_record = self._sources[source_handle]
        if (
            not re.fullmatch(source_pattern, source_pointer)
            or not re.fullmatch(output_pattern, output_pointer)
            or output_pointer in self._targets
        ):
            _fail("registration scope or target collision")
        scope = native_record["scope"]
        value = _at(native_record.get("record"), source_pointer)
        if kind.endswith(".id"):
            if not isinstance(value, str) or not value:
                _fail("reference requires original identifier")
        elif not isinstance(value, dict) or value.get("instanceType") != kind:
            _fail("source class differs from installed rule")
        record_hash = self._source_hashes[source_handle]
        self._registrations.append(
            {
                "ruleId": rule_id,
                "sourceRecord": {
                    "kind": native_record["kind"],
                    "uid": native_record["uid"],
                    "scope": deepcopy(scope),
                    "recordHash": record_hash,
                },
                "sourcePointer": source_pointer,
                "outputPointer": output_pointer,
                "sourceValueHash": _hash(value, VALUE_PROFILE),
            }
        )
        self._targets.add(output_pointer)

    def report(self, document: dict[str, Any]) -> dict[str, Any]:
        """Compare final bytes, without modifying either side or hiding drift."""
        registrations, blockers = [], []
        for snapshot in self._registrations:
            entry = deepcopy(snapshot)
            source = self._sources[entry["sourceRecord"]["recordHash"]["value"]]
            source_value = _at(source["record"], entry["sourcePointer"])
            try:
                output = _at(document, entry["outputPointer"])
            except ValidationException:
                entry["outputValueHash"] = None
                entry["state"] = "absent-after-finalization"
            else:
                _safe_numbers(output)
                entry["outputValueHash"] = _hash(output, VALUE_PROFILE)
                entry["state"] = (
                    "exact"
                    if canonical_json(output) == canonical_json(source_value)
                    else "changed-after-registration"
                )
            registrations.append(entry)
            if entry["state"] != "exact":
                blockers.append(
                    {
                        "code": "USDM_REGISTERED_SOURCE_CHANGED",
                        "sourcePointer": entry["sourcePointer"],
                        "outputPointer": entry["outputPointer"],
                    }
                )
        ruleset = correspondence_ruleset()
        return {
            "contractVersion": PROFILE,
            "coverage": "partial-call-site-registrations-only",
            "correspondenceAuthority": False,
            "nativeOperationAttribution": "none",
            "ruleset": ruleset,
            "rulesetHash": _hash(ruleset, "OsbMapperObservationRulesetV1@1.0.0"),
            "registrations": registrations,
            "sourceRecords": [
                {
                    "recordHash": deepcopy(self._source_hashes[handle]),
                    "nativeRecord": deepcopy(record),
                }
                for handle, record in self._sources.items()
            ],
            "blockers": blockers,
            "requiredIndependentChecks": [
                "exact-locked-native-read-dependencies",
                "original-accepted-source-custody",
                "complete-source-and-export-object-reference-terminal-censuses",
                "installed-pinned-correspondence-rules",
                "selected-unsupported-execution-refusal",
            ],
        }
