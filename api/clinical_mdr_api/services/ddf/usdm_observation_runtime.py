"""Verify an independently qualified, complete immutable observation runtime.

The source commit and image digest are provenance, not self-issued authority.
An operator must qualify the whole API archive, Python and Node installations,
and supply a current deployment binding. This module never qualifies a mutable
checkout/venv or grants correspondence/execution/release authority.
"""

from __future__ import annotations

import hashlib
import os
import platform
import stat
import sys
import tarfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from jsonschema import Draft202012Validator

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
    hash_refs_equal,
    parse_strict_json_bytes,
)
from clinical_mdr_api.services.ddf.usdm_correspondence_observations import (
    correspondence_ruleset,
)
from clinical_mdr_api.services.ddf.usdm_observation_census import (
    census_from_inventory,
    refuse,
    run_inventory_kernel,
    safe_json,
)

PROFILE = "OsbMapperObservationRuntimeV1@1.0.0"
SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "schemas/platform/osb-mapper-observation-runtime-v1.schema.json"
)
MAX_FILES = 100000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TREE_BYTES = 8 * 1024 * 1024 * 1024
KERNEL_ARCHIVE_SHA256 = (
    "114ce8e05c2f24ee1352ff61772d4e54a70990cc0920dc32e986fa2eecd04278"
)
KERNEL_ARCHIVE_BYTES = 4745319


def _sort_key(value: str) -> bytes:
    return value.encode("utf-16-be")


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        refuse("unsafe artifact member path")
    if "\\" in value or ":" in value or any(ord(char) < 32 for char in value):
        refuse("unsafe artifact member path")
    if (
        any(part in ("", ".", "..") for part in value.split("/"))
        or PurePosixPath(value).is_absolute()
    ):
        refuse("unsafe artifact member path")
    return value


def _digest_stream(stream, limit: int = MAX_FILE_BYTES) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    while block := stream.read(1024 * 1024):
        size += len(block)
        if size > limit:
            refuse("artifact byte limit")
        digest.update(block)
    return size, digest.hexdigest()


def _file_row(path: Path, relative: str) -> dict[str, Any]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        refuse("artifact links or non-regular members")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            refuse("artifact changed while opening")
        size, digest = _digest_stream(stream)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        refuse("artifact changed while reading")
    return {"path": _relative(relative), "byteSize": size, "sha256": digest}


def enumerate_runtime_files(root: Path) -> list[dict[str, Any]]:
    """Enumerate every regular file; no ignore rules or executable exclusions."""
    root = Path(root)
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        refuse("runtime root is not an ordinary directory")
    rows, aliases, total = [], set(), 0

    def scan_error(_error: OSError) -> None:
        refuse("runtime directory scan failed")

    for directory, directories, files in os.walk(
        root, followlinks=False, onerror=scan_error
    ):
        for name in directories:
            child = Path(directory) / name
            if child.is_symlink() or child.is_junction():
                refuse("runtime directory link")
        for name in files:
            child = Path(directory) / name
            relative = child.relative_to(root).as_posix()
            alias = relative.casefold()
            if alias in aliases:
                refuse("duplicate artifact path")
            aliases.add(alias)
            row = _file_row(child, relative)
            rows.append(row)
            total += row["byteSize"]
            if len(rows) > MAX_FILES or total > MAX_TREE_BYTES:
                refuse("runtime closure limit")
    return sorted(rows, key=lambda row: _sort_key(row["path"]))


def file_manifest_sha256(rows: list[dict[str, Any]]) -> str:
    """The exact manifest preimage is platform canonical_json(File[]) UTF-8."""
    return hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()


def _verify_rows(root: Path, rows: list[dict[str, Any]], digest: str) -> None:
    if file_manifest_sha256(rows) != digest or enumerate_runtime_files(root) != rows:
        refuse("complete runtime closure mismatch")


def verify_archive(path: Path, rows: list[dict[str, Any]], *, prefix: str = "") -> None:
    """Compare all regular TAR members without extraction or pathname writes."""
    actual, aliases, total = [], set(), 0
    with tarfile.open(path, "r|*") as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            _relative(name)
            if name.casefold() in aliases:
                refuse("duplicate archive member")
            aliases.add(name.casefold())
            if len(aliases) > MAX_FILES * 2:
                refuse("archive member limit")
            if member.isdir():
                continue
            if not member.isfile() or member.size > MAX_FILE_BYTES:
                refuse("archive links or non-regular members")
            if prefix and not name.startswith(prefix):
                refuse("archive root mismatch")
            name = name[len(prefix) :]
            with archive.extractfile(member) as stream:
                size, digest = _digest_stream(stream)
            if size != member.size:
                refuse("archive member length mismatch")
            total += size
            if total > MAX_TREE_BYTES or len(actual) >= MAX_FILES:
                refuse("archive closure limit")
            actual.append({"path": _relative(name), "byteSize": size, "sha256": digest})
    actual.sort(key=lambda row: _sort_key(row["path"]))
    if actual != rows:
        refuse("archive and installed file closure differ")


@dataclass(frozen=True)
class ObservationRuntimePaths:
    api_root: Path
    python_root: Path
    node_root: Path
    api_archive: Path
    kernel_archive: Path


def validate_runtime_manifest(manifest: Any) -> None:
    safe_json(manifest)
    if len(canonical_json(manifest).encode("utf-8")) > 32 * 1024 * 1024:
        refuse("runtime manifest byte limit")
    _, schema = parse_strict_json_bytes(SCHEMA.read_bytes())
    Draft202012Validator.check_schema(schema)
    if next(Draft202012Validator(schema).iter_errors(manifest), None) is not None:
        refuse("closed runtime manifest schema")


def verify_runtime_files(
    manifest: dict[str, Any], paths: ObservationRuntimePaths
) -> None:
    """Check exact artifact bytes. This alone does not qualify a deployment."""
    validate_runtime_manifest(manifest)
    _verify_rows(paths.api_root, manifest["files"], manifest["filesSha256"])
    api_rows = {row["path"]: row for row in manifest["files"]}
    required = (
        "Pipfile.lock",
        "clinical_mdr_api/services/ddf/usdm_mapper.py",
        "clinical_mdr_api/services/ddf/usdm_observation_runtime.py",
        "clinical_mdr_api/services/ddf/usdm_observation_census.py",
        "clinical_mdr_api/services/ddf/usdm_inventory_bridge.cjs",
    )
    if any(path not in api_rows for path in required):
        refuse("incomplete API runtime")
    if api_rows["Pipfile.lock"]["sha256"] != manifest["pipfileLockSha256"]:
        refuse("Pipfile.lock mismatch")
    archive_row = _file_row(paths.api_archive, "api.tar")
    if (archive_row["byteSize"], archive_row["sha256"]) != (
        manifest["apiArchiveByteSize"],
        manifest["apiArchiveSha256"],
    ):
        refuse("API archive mismatch")
    verify_archive(paths.api_archive, manifest["files"])
    for key, root in (("python", paths.python_root), ("node", paths.node_root)):
        runtime = manifest["runtimeFiles"][key]
        _verify_rows(root, runtime["files"], runtime["filesSha256"])
        if runtime["executable"] not in {row["path"] for row in runtime["files"]}:
            refuse("runtime executable absent from seal")
    kernel = manifest["inventoryKernel"]
    kernel_root = paths.node_root / kernel["packagePath"]
    _verify_rows(kernel_root, kernel["files"], kernel["filesSha256"])
    archive_row = _file_row(paths.kernel_archive, "kernel.tgz")
    if (archive_row["byteSize"], archive_row["sha256"]) != (
        KERNEL_ARCHIVE_BYTES,
        KERNEL_ARCHIVE_SHA256,
    ):
        refuse("installed kernel archive mismatch")
    verify_archive(paths.kernel_archive, kernel["files"], prefix="package/")
    if (
        _file_row(kernel_root / "release-manifest.json", "release-manifest.json")[
            "sha256"
        ]
        != kernel["releaseManifestSha256"]
    ):
        refuse("installed kernel release manifest mismatch")
    _, release = parse_strict_json_bytes(
        (kernel_root / "release-manifest.json").read_bytes()
    )
    if any(
        release.get(key) != kernel[key]
        for key in ("sourceTreeSha256", "runtimeTreeSha256")
    ):
        refuse("installed kernel source/runtime pin mismatch")


def build_runtime_manifest(
    *,
    paths: ObservationRuntimePaths,
    source_commit: str,
    source_tree_oid: str,
    runtime_image_digest: str,
    python_executable: str,
    python_version: str,
    node_executable: str,
    node_version: str,
) -> dict[str, Any]:
    """Operator packaging helper, not runtime qualification or an allowlist.

    Archives and complete immutable installations must already exist. This does
    not resolve dependencies, build an image, infer a Git revision, write files,
    or assert deployment identity. Provenance is independently checked at the
    operator's image qualification boundary before pinning the resulting hash.
    """
    _, schema = parse_strict_json_bytes(SCHEMA.read_bytes())
    kernel = {
        key: value["const"]
        for key, value in schema["properties"]["inventoryKernel"]["properties"].items()
        if "const" in value
    }
    rows = enumerate_runtime_files(paths.api_root)
    api_archive = _file_row(paths.api_archive, "api.tar")
    runtimes = {}
    for name, root, executable, version in (
        ("python", paths.python_root, python_executable, python_version),
        ("node", paths.node_root, node_executable, node_version),
    ):
        runtime_rows = enumerate_runtime_files(root)
        runtimes[name] = {
            "executable": _relative(executable),
            "version": version,
            "files": runtime_rows,
            "filesSha256": file_manifest_sha256(runtime_rows),
        }
    kernel_rows = enumerate_runtime_files(paths.node_root / kernel["packagePath"])
    kernel.update(files=kernel_rows, filesSha256=file_manifest_sha256(kernel_rows))
    manifest = {
        "contractVersion": PROFILE,
        "sourceCommit": source_commit,
        "sourceTreeOid": source_tree_oid,
        "apiArchiveSha256": api_archive["sha256"],
        "apiArchiveByteSize": api_archive["byteSize"],
        "files": rows,
        "filesSha256": file_manifest_sha256(rows),
        "pipfileLockSha256": _file_row(paths.api_root / "Pipfile.lock", "Pipfile.lock")[
            "sha256"
        ],
        "runtimeImageDigest": runtime_image_digest,
        "runtimeFiles": runtimes,
        "inventoryKernel": kernel,
        "executionQualifiedFamilies": [],
    }
    verify_runtime_files(manifest, paths)
    return manifest


class ObservationRuntime:
    """An explicit, independently allowlisted runtime, checked on every use.

    assert_deployment_binding receives immutable copies of manifest/hash and the
    exact roots. It MUST verify current external image identity, Python process
    and dependency search paths, read-only mount topology and absence of import
    shadows. It returns exactly True or raises/refuses. A digest repeated from
    this manifest is insufficient. There is intentionally no default callback.
    """

    def __init__(
        self,
        *,
        manifest: dict[str, Any],
        expected_manifest_hash: dict[str, Any],
        paths: ObservationRuntimePaths,
        assert_deployment_binding: Callable[..., bool],
    ) -> None:
        if not callable(assert_deployment_binding):
            refuse("independent deployment binding required")
        validate_runtime_manifest(manifest)
        self._manifest = deepcopy(manifest)
        self._hash = canonical_json_hash_ref(self._manifest, schema_version=PROFILE)
        if not hash_refs_equal(self._hash, expected_manifest_hash):
            refuse("runtime manifest is not independently pinned")
        self._paths = paths
        self._binding = assert_deployment_binding

    def assert_current(self) -> None:
        manifest, paths = self._manifest, self._paths
        if self._binding(deepcopy(manifest), deepcopy(self._hash), paths) is not True:
            refuse("runtime deployment is unbound")
        if (
            Path(__file__).resolve()
            != (
                paths.api_root
                / "clinical_mdr_api/services/ddf/usdm_observation_runtime.py"
            ).resolve()
        ):
            refuse("API runtime import shadow")
        for name, module in tuple(sys.modules.items()):
            if name.split(".", 1)[0] in ("clinical_mdr_api", "common", "extensions"):
                origin = getattr(module, "__file__", None)
                if origin and not Path(origin).resolve().is_relative_to(
                    paths.api_root.resolve()
                ):
                    refuse("API dependency import shadow")
        python = manifest["runtimeFiles"]["python"]
        if (
            Path(sys.executable).resolve()
            != (paths.python_root / python["executable"]).resolve()
            or platform.python_version() != python["version"]
        ):
            refuse("Python runtime differs from qualified deployment")
        verify_runtime_files(manifest, paths)
        if self._binding(deepcopy(manifest), deepcopy(self._hash), paths) is not True:
            refuse("runtime deployment changed during verification")

    def observe(self, document: Any) -> dict[str, Any]:
        self.assert_current()
        node = self._manifest["runtimeFiles"]["node"]
        snapshot = deepcopy(document)
        envelope = run_inventory_kernel(
            snapshot,
            node_root=self._paths.node_root,
            executable=self._paths.node_root / node["executable"],
            node_version=node["version"],
            include_dictionary=True,
        )
        inventory = envelope["inventory"]
        census = census_from_inventory(snapshot, inventory)
        ruleset = correspondence_ruleset()
        self.assert_current()
        return {
            "runtimeManifest": deepcopy(self._manifest),
            "runtimeHash": deepcopy(self._hash),
            "rulesetHash": canonical_json_hash_ref(
                ruleset, schema_version="OsbMapperObservationRulesetV1@1.0.0"
            ),
            "inventoryProfile": inventory["profile"],
            "usdmReferenceDictionaryHash": canonical_json_hash_ref(
                envelope["referenceDictionary"],
                schema_version="UsdmReferenceDictionaryV4@4.0.0",
            ),
            "census": census,
            "diagnostics": deepcopy(inventory["issues"]),
            "executionQualifiedFamilies": [],
            "correspondenceAuthority": False,
        }
