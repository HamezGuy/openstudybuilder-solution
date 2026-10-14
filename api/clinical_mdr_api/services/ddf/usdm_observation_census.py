"""Complete factual census using the installed JS kernel, never mapping authority."""

from __future__ import annotations

import math
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    PlatformHashError,
    canonical_json,
    canonical_json_hash_ref,
    parse_strict_json_bytes,
)
from clinical_mdr_api.services.ddf.usdm_correspondence_observations import _at
from common.exceptions import ValidationException

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
KERNEL_PROFILE = "usdm-v4-document-inventory/1.0.0"
VALUE_PROFILE = "OsbCorrespondenceJsonValueV1@1.0.0"
BRIDGE = Path(__file__).with_name("usdm_inventory_bridge.cjs")


def refuse(reason: str) -> None:
    """Expose a bounded error, never source values or child process diagnostics."""
    raise ValidationException(msg=f"OSB_OBSERVATION_RUNTIME_REFUSED: {reason}")


def safe_json(value: Any, depth: int = 0, ancestors: set[int] | None = None) -> None:
    """Transport preflight only: Python must not erase values before JS sees them."""
    value_type = type(value)
    if depth > 200:
        refuse("JSON depth limit")
    if value_type is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            refuse("invalid Unicode JSON string")
        return
    if value is None or value_type is bool:
        return
    if value_type in (int, float):
        if value_type is int and abs(value) > 2**53 - 1:
            refuse("unsafe JSON number")
        if not math.isfinite(value) or (value == int(value) and abs(value) > 2**53 - 1):
            refuse("unsafe JSON number")
        if value_type is float and value == 0 and math.copysign(1, value) < 0:
            refuse("unsafe JSON number")
        return
    if value_type not in (dict, list):
        refuse("non-JSON value")
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        refuse("cyclic JSON")
    ancestors.add(id(value))
    if value_type is dict and any(not isinstance(key, str) for key in value):
        refuse("non-string JSON key")
    if value_type is dict:
        for key in value:
            safe_json(key, depth, ancestors)
    for child in value.values() if value_type is dict else value:
        safe_json(child, depth + 1, ancestors)
    ancestors.remove(id(value))


def run_inventory_kernel(
    document: Any,
    *,
    node_root: Path,
    executable: Path,
    node_version: str,
    timeout: float = 15,
    include_dictionary: bool = False,
) -> dict[str, Any]:
    """Private adapter; callers must verify their independently qualified seal.

    Fixed argv, no shell or inherited Node hooks. Readers stop/kill on byte or
    chunk limits, including stderr. A killed/timed-out child yields no census.
    """
    if not 0 < timeout <= 15:
        refuse("process timeout bound")
    safe_json(document)
    payload = canonical_json(document).encode("utf-8")
    if len(payload) > MAX_INPUT_BYTES:
        refuse("input byte limit")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in ("SYSTEMROOT", "WINDIR")
    }
    output: list[bytes] = []
    refused = threading.Event()
    with subprocess.Popen(  # nosec: fixed bridge, explicit sealed executable
        [
            str(executable),
            "--max-old-space-size=256",
            str(BRIDGE),
            str(node_root),
            node_version,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=node_root,
        env=environment,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:

        def stop() -> None:
            refused.set()
            if process.poll() is None:
                process.kill()

        def read(stream, maximum: int, retained: list[bytes] | None) -> None:
            size, chunks = 0, 0
            try:
                while block := stream.read(65536):
                    size += len(block)
                    chunks += 1
                    if size > maximum or chunks > 4096:
                        stop()
                        break
                    if retained is not None:
                        retained.append(block)
            except OSError, ValueError:
                stop()
            finally:
                stream.close()

        def write() -> None:
            try:
                process.stdin.write(payload)
                process.stdin.close()
            except BrokenPipeError, OSError, ValueError:
                stop()

        threads = [
            threading.Thread(
                target=read, args=(process.stdout, MAX_OUTPUT_BYTES, output)
            ),
            threading.Thread(target=read, args=(process.stderr, 4096, None)),
            threading.Thread(target=write),
        ]
        try:
            for thread in threads:
                thread.start()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                stop()
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
            if (
                refused.is_set()
                or process.returncode != 0
                or any(t.is_alive() for t in threads)
            ):
                refuse("bounded kernel process failed")
            try:
                _, envelope = parse_strict_json_bytes(b"".join(output))
            except PlatformHashError, RecursionError:
                refuse("kernel output JSON is invalid")
            safe_json(envelope)
            if (
                not isinstance(envelope, dict)
                or set(envelope) != {"inventory", "referenceDictionary"}
                or not isinstance(envelope["referenceDictionary"], dict)
            ):
                refuse("kernel output envelope is invalid")
            result = envelope["inventory"]
            if (
                not isinstance(result, dict)
                or set(result)
                != {"profile", "jsonValid", "entities", "references", "issues"}
                or result["profile"] != KERNEL_PROFILE
                or result["jsonValid"] is not True
                or any(
                    not isinstance(result[key], list)
                    for key in ("entities", "references", "issues")
                )
            ):
                refuse("kernel did not return a complete JSON-safe inventory")
            return envelope if include_dictionary else result
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()


def census_from_inventory(document: Any, inventory: dict[str, Any]) -> dict[str, Any]:
    """Add exact values and terminals; reference semantics remain kernel-owned.

    This is internal composition, not an API accepting a caller-supplied census.
    Every occurrence is retained, including unselected and unsupported material.
    """
    safe_json(document)

    def key(value: str) -> bytes:
        return value.encode("utf-16-be")

    def value_hash(value: Any) -> dict[str, Any]:
        return canonical_json_hash_ref(value, schema_version=VALUE_PROFILE)

    objects = [
        {**row, "valueHash": value_hash(_at(document, row["pointer"]))}
        for row in inventory["entities"]
    ]
    references = [
        {**row, "valueHash": value_hash(_at(document, row["pointer"]))}
        for row in inventory["references"]
    ]
    objects.sort(key=lambda row: key(row["pointer"]))
    references.sort(
        key=lambda row: (key(row["pointer"]), key(row["resolutionScope"]["pointer"]))
    )
    if len({row["pointer"] for row in objects}) != len(objects) or len(
        {(row["pointer"], row["resolutionScope"]["pointer"]) for row in references}
    ) != len(references):
        refuse("duplicate inventory location")
    terminals: list[dict[str, Any]] = []

    def walk(value: Any, pointer: str) -> None:
        if isinstance(value, (dict, list)) and value:
            for token, child in (
                value.items() if isinstance(value, dict) else enumerate(value)
            ):
                escaped = str(token).replace("~", "~0").replace("/", "~1")
                walk(child, f"{pointer}/{escaped}")
        else:
            terminals.append({"pointer": pointer, "valueHash": value_hash(value)})

    walk(document, "")
    terminals.sort(key=lambda row: key(row["pointer"]))
    return {
        "objects": objects,
        "references": references,
        "terminals": terminals,
        "objectCensusHash": canonical_json_hash_ref(
            objects, schema_version="OsbCorrespondenceObjectCensusV1@1.0.0"
        ),
        "referenceCensusHash": canonical_json_hash_ref(
            references, schema_version="OsbCorrespondenceReferenceCensusV1@1.0.0"
        ),
        "terminalSetHash": canonical_json_hash_ref(
            terminals, schema_version="OsbCorrespondenceTerminalSetV1@1.0.0"
        ),
    }
