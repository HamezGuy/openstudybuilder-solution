"""Disposable file seals and actual installed-kernel transport, no native DB.

File-manifest fixtures are deliberately NOT qualified images. The seven statuses
supported by the installed metadata run unchanged; unsupported-metadata currently
has no registered attribute and is separately tested as synthetic kernel output.
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json_hash_ref,
)
from clinical_mdr_api.services.ddf import usdm_observation_census as adapter
from clinical_mdr_api.services.ddf.usdm_observation_runtime import (
    PROFILE,
    ObservationRuntime,
    ObservationRuntimePaths,
    build_runtime_manifest,
    enumerate_runtime_files,
    file_manifest_sha256,
    validate_runtime_manifest,
    verify_archive,
    verify_runtime_files,
)
from clinical_mdr_api.tests.fixtures.usdm_native_source import NativeStudySource
from clinical_mdr_api.tests.fixtures.usdm_native_study import VERSION
from clinical_mdr_api.tests.unit.services.test_authored_protocol_documents import (
    authored_source,
)
from common.exceptions import ValidationException

# Operator-provided published installation, never a network install/test fallback.
NODE_ROOT = Path(
    os.environ.get("OSB_TEST_KERNEL_NODE_ROOT", "C:/Projects/ClinicalSemanticLayer")
)
ARCHIVE = Path(
    os.environ.get(
        "OSB_TEST_KERNEL_ARCHIVE",
        "C:/Projects/ClinicalSemanticLayer/vendor/accura-trial-shared-types-0.2.0.114ce8e05c2f24ee.tgz",
    )
)


@lru_cache(maxsize=2)
def node_fixture(require_kernel=True):
    """Optional qualification prerequisites never break ordinary test collection."""
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("OSB runtime qualification unexecuted: Node fixture unavailable")
    if require_kernel and (
        not (
            NODE_ROOT / "node_modules/@accura-trial/shared-types/package.json"
        ).is_file()
        or not ARCHIVE.is_file()
    ):
        pytest.skip(
            "OSB runtime qualification unexecuted: explicit installed114ce kernel/archive fixture unavailable"
        )
    version = subprocess.check_output(
        [executable, "--version"], text=True, timeout=5
    ).strip()
    return Path(executable), version


def inventory(document, **options):
    executable, version = node_fixture()
    return adapter.run_inventory_kernel(
        document,
        node_root=NODE_ROOT,
        executable=executable,
        node_version=version,
        **options,
    )


def document_fixture():
    def activity(identifier):
        return {"instanceType": "Activity", "id": identifier}

    return {
        "outside": {
            "instanceType": "ScheduledActivityInstance",
            "activityIds": ["same"],
        },
        "study": {
            "instanceType": "Study",
            "id": "study",
            "shared": {
                "instanceType": "ScheduledActivityInstance",
                "activityIds": ["same"],
            },
            "versions": [
                {
                    "instanceType": "StudyVersion",
                    "id": "v1",
                    "activities": [
                        activity("same"),
                        activity("dup"),
                        activity("dup"),
                        activity("unreferenced-dup"),
                        activity("unreferenced-dup"),
                        activity("__proto__"),
                        activity(""),
                        {"instanceType": "Activity"},
                    ],
                    "other": {"instanceType": "Organization", "id": "org"},
                    "instances": [
                        {
                            "instanceType": "ScheduledActivityInstance",
                            "activityIds": [
                                "same",
                                "dup",
                                "missing",
                                "org",
                                None,
                                7,
                                "__proto__",
                                "",
                            ],
                        }
                    ],
                    "unknown~field/": {
                        "false": False,
                        "zero": 0,
                        "null": None,
                        "empty": [],
                        "object": {},
                    },
                },
                {
                    "instanceType": "StudyVersion",
                    "id": "v2",
                    "activities": [activity("same")],
                },
            ],
        },
    }


def test_actual_kernel_preserves_all_reachable_statuses_scopes_candidates_and_ids():
    document = document_fixture()
    before = deepcopy(document)
    result = inventory(document)
    assert document == before
    assert {row["status"] for row in result["references"]} == {
        "resolved",
        "ambiguous",
        "unresolved",
        "invalid-value",
        "type-mismatch",
        "explicit-null",
        "out-of-scope",
    }
    shared = [
        row
        for row in result["references"]
        if row["pointer"] == "/study/shared/activityIds/0"
    ]
    assert [row["resolutionScope"]["pointer"] for row in shared] == [
        "/study/versions/0",
        "/study/versions/1",
    ]
    assert [row["targets"][0]["pointer"] for row in shared] == [
        "/study/versions/0/activities/0",
        "/study/versions/1/activities/0",
    ]
    ambiguous = next(
        row for row in result["references"] if row["status"] == "ambiguous"
    )
    assert [row["pointer"] for row in ambiguous["targets"]] == [
        "/study/versions/0/activities/1",
        "/study/versions/0/activities/2",
    ]
    assert any(row.get("id") == "" for row in result["entities"])
    assert any("id" not in row for row in result["entities"])
    assert (
        next(row for row in result["references"] if row.get("id") == "__proto__")[
            "status"
        ]
        == "resolved"
    )
    assert sum(row["code"] == "USDM_ID_DUPLICATE" for row in result["issues"]) == 2
    census = adapter.census_from_inventory(document, result)
    assert len(census["objects"]) == len(result["entities"])
    assert len(census["references"]) == len(result["references"])
    assert [row["targets"] for row in census["references"]] == [
        row["targets"]
        for row in sorted(
            result["references"],
            key=lambda row: (row["pointer"], row["resolutionScope"]["pointer"]),
        )
    ]
    terminals = {row["pointer"]: row["valueHash"] for row in census["terminals"]}
    for name, value in (
        ("false", False),
        ("zero", 0),
        ("null", None),
        ("empty", []),
        ("object", {}),
    ):
        assert terminals[
            f"/study/versions/0/unknown~0field~1/{name}"
        ] == canonical_json_hash_ref(value, schema_version=adapter.VALUE_PROFILE)
    # Diagnostic text cannot alter any normative census or terminal preimage.
    result["issues"] = [{"message": "deliberately changed diagnostic"}]
    assert adapter.census_from_inventory(document, result) == census


def test_synthetic_eighth_status_is_preserved_without_execution_claim():
    document = {"study": {"unknown": "target"}}
    synthetic = {
        "entities": [],
        "references": [
            {
                "pointer": "/study/unknown",
                "entityPointer": "/study",
                "attribute": "unknown",
                "sourceScope": {"kind": "shared", "pointer": "/study"},
                "resolutionScope": {"kind": "shared", "pointer": "/study"},
                "id": "target",
                "status": "unsupported-metadata",
                "targets": [],
            }
        ],
        "issues": [],
    }
    census = adapter.census_from_inventory(document, synthetic)
    assert census["references"][0]["status"] == "unsupported-metadata"
    assert "expectedType" not in census["references"][0]


def test_installed_dictionary_is_complete_and_node_hooks_are_not_inherited(monkeypatch):
    node_fixture()
    monkeypatch.setenv("NODE_OPTIONS", "--require=fixture-must-never-load")
    monkeypatch.setenv("NODE_PATH", "fixture-must-never-resolve")
    result = inventory({}, include_dictionary=True)
    dictionary = result["referenceDictionary"]
    assert set(dictionary) == {
        "sources",
        "attributes",
        "terms",
        "rules",
        "inheritance",
        "references",
        "unresolvedReferenceAttributes",
    }
    assert dictionary["unresolvedReferenceAttributes"] == []
    assert result["inventory"]["profile"] == adapter.KERNEL_PROFILE
    assert (
        dictionary["references"]["ScheduledActivityInstance.activityIds"] == "Activity"
    )


def test_scan_failure_cannot_seal_a_silently_incomplete_tree(monkeypatch, tmp_path):
    denied = tmp_path / "unreadable"
    denied.mkdir()
    (denied / "mapper.py").write_text("must be included", encoding="utf-8")
    original = os.scandir

    def scandir(path):
        if Path(path) == denied:
            raise PermissionError("Synthetic unreadable directory")
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(ValidationException, match="scan failed"):
        enumerate_runtime_files(tmp_path)


def test_installed_file_hardlinks_are_not_accepted(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"fixture")
    os.link(source, tmp_path / "alias")
    with pytest.raises(ValidationException, match="links"):
        enumerate_runtime_files(tmp_path)


def test_complete_terminals_use_utf16_order_and_include_unselected_metadata():
    document = {
        "\ue000": 1,
        "\U00010000": 2,
        "selected": {"x": False},
        "unselected": [None, {}, []],
    }
    census = adapter.census_from_inventory(document, inventory(document))
    pointers = [row["pointer"] for row in census["terminals"]]
    assert pointers == sorted(pointers, key=lambda value: value.encode("utf-16-be"))
    assert pointers[-2:] == ["/\U00010000", "/\ue000"]
    assert len(pointers) == 6


def test_installed_canonicalizer_preserves_numeric_keys_and_number_boundaries():
    document = {
        "2": {"\ue000": 1e-6, "\U00010000": 1e-7},
        "10": [2**53 - 1, -(2**53 - 1), 0, False, None],
    }
    result = inventory(document)
    assert result["jsonValid"] is True
    census = adapter.census_from_inventory(document, result)
    terminals = {row["pointer"]: row["valueHash"] for row in census["terminals"]}
    assert len(terminals) == 7
    assert terminals["/2/\U00010000"] == canonical_json_hash_ref(
        1e-7, schema_version=adapter.VALUE_PROFILE
    )
    assert terminals["/10/0"] == canonical_json_hash_ref(
        2**53 - 1, schema_version=adapter.VALUE_PROFILE
    )


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), -0.0, 2**53, -(2**53), 10**1000, {1: "bad"}, (1, 2)],
)
def test_unsafe_python_values_fail_before_lossy_transport(value):
    with pytest.raises(ValidationException):
        adapter.safe_json({"value": value})


def test_cycles_depth_limits_and_input_byte_bounds(monkeypatch):
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValidationException):
        adapter.safe_json(cycle)
    value = {}
    for _ in range(201):
        value = {"nested": value}
    with pytest.raises(ValidationException):
        adapter.safe_json(value)
    monkeypatch.setattr(adapter, "MAX_INPUT_BYTES", 10)
    with pytest.raises(ValidationException):
        adapter.run_inventory_kernel(
            {"large": "not truncated"},
            node_root=Path("unused"),
            executable=Path("unused"),
            node_version="unused",
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":1,"\\u0078":2}',
        b"\xef\xbb\xbf{}",
        b'{"x":NaN}',
        b'{"x":-0}',
        b'{"x":9007199254740993}',
        b'{"x": "\xff"}',
        b"{}{}",
    ],
)
def test_bridge_rejects_noncanonical_duplicate_or_unsafe_raw_json(raw):
    executable, version = node_fixture()
    result = subprocess.run(
        [str(executable), str(adapter.BRIDGE), str(NODE_ROOT), version],
        input=raw,
        capture_output=True,
        check=False,
        timeout=20,
        env={
            key: value
            for key, value in os.environ.items()
            if key.upper() in ("SYSTEMROOT", "WINDIR")
        },
    )
    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr == b"OSB_INVENTORY_BRIDGE_REFUSED\n"


def test_timeout_output_limit_and_malformed_output_refuse(monkeypatch, tmp_path):
    executable, version = node_fixture(False)
    script = tmp_path / "bridge.cjs"
    monkeypatch.setattr(adapter, "BRIDGE", script)
    for content in (
        "setTimeout(()=>{},30000)",
        "process.stdout.write('x'.repeat(1024));",
        'process.stdout.write(\'{"x":1,"x":2}\')',
        "process.exit(7)",
    ):
        script.write_text(content, encoding="utf-8")
        monkeypatch.setattr(adapter, "MAX_OUTPUT_BYTES", 128)
        with pytest.raises(ValidationException):
            adapter.run_inventory_kernel(
                {},
                node_root=tmp_path,
                executable=executable,
                node_version=version,
                timeout=0.1 if "setTimeout" in content else 5,
            )


def pack(root, target):
    with tarfile.open(target, "w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(root).as_posix())


@pytest.fixture(name="file_seal", scope="module")
def fixture_file_seal(tmp_path_factory):
    _, node_version = node_fixture()
    temporary = tmp_path_factory.mktemp("observation-seal")
    api, python, node = (temporary / name for name in ("api", "python", "node"))
    for root in (api, python, node):
        root.mkdir()
    for name in (
        "Pipfile.lock",
        "clinical_mdr_api/services/ddf/usdm_mapper.py",
        "clinical_mdr_api/services/ddf/usdm_observation_runtime.py",
        "clinical_mdr_api/services/ddf/usdm_observation_census.py",
        "clinical_mdr_api/services/ddf/usdm_inventory_bridge.cjs",
    ):
        path = api / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Unqualified disposable file fixture.\n", encoding="utf-8")
    (python / "python.fixture").write_bytes(b"Not an interpreter")
    (node / "node.fixture").write_bytes(b"Not an interpreter")
    kernel_root = node / "node_modules/@accura-trial/shared-types"
    shutil.copytree(NODE_ROOT / "node_modules/@accura-trial/shared-types", kernel_root)
    api_archive = temporary / "api.tar"
    pack(api, api_archive)
    rows = enumerate_runtime_files(api)
    release_path = kernel_root / "release-manifest.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    kernel_rows = enumerate_runtime_files(kernel_root)
    manifest = {
        "contractVersion": PROFILE,
        "sourceCommit": "1" * 40,
        "sourceTreeOid": "2" * 40,
        "apiArchiveSha256": hashlib.sha256(api_archive.read_bytes()).hexdigest(),
        "apiArchiveByteSize": api_archive.stat().st_size,
        "files": rows,
        "filesSha256": file_manifest_sha256(rows),
        "pipfileLockSha256": next(
            row["sha256"] for row in rows if row["path"] == "Pipfile.lock"
        ),
        "runtimeImageDigest": "sha256:" + "3" * 64,
        "runtimeFiles": {
            name: {
                "executable": f"{name}.fixture",
                "version": "3.14.0" if name == "python" else node_version,
                "files": enumerate_runtime_files(root),
                "filesSha256": file_manifest_sha256(enumerate_runtime_files(root)),
            }
            for name, root in (("python", python), ("node", node))
        },
        "inventoryKernel": {
            "profile": adapter.KERNEL_PROFILE,
            "packageName": "@accura-trial/shared-types",
            "packageVersion": "0.2.0",
            "packagePath": "node_modules/@accura-trial/shared-types",
            "export": "@accura-trial/shared-types/usdm/validation",
            "symbol": "inventoryUsdmV4Document",
            "archiveSha256": "114ce8e05c2f24ee1352ff61772d4e54a70990cc0920dc32e986fa2eecd04278",
            "archiveByteSize": 4745319,
            "archiveIntegrity": "sha512-9w2vP11UEaHBBh+vfW19vLes3h2zAagv7PuH2MSTD9eRctKih5Phsxraa/4EGmX7g4CbVlVcAY1KxLzwS61z5Q==",
            "releaseManifestSha256": hashlib.sha256(
                release_path.read_bytes()
            ).hexdigest(),
            "sourceTreeSha256": release["sourceTreeSha256"],
            "runtimeTreeSha256": release["runtimeTreeSha256"],
            "files": kernel_rows,
            "filesSha256": file_manifest_sha256(kernel_rows),
        },
        "executionQualifiedFamilies": [],
    }
    return manifest, ObservationRuntimePaths(api, python, node, api_archive, ARCHIVE)


def test_complete_file_and_archive_closures_verify_without_qualifying_image(file_seal):
    manifest, paths = file_seal
    verify_runtime_files(manifest, paths)
    assert (
        build_runtime_manifest(
            paths=paths,
            source_commit=manifest["sourceCommit"],
            source_tree_oid=manifest["sourceTreeOid"],
            runtime_image_digest=manifest["runtimeImageDigest"],
            python_executable="python.fixture",
            python_version="3.14.0",
            node_executable="node.fixture",
            node_version=manifest["runtimeFiles"]["node"]["version"],
        )
        == manifest
    )
    runtime = ObservationRuntime(
        manifest=manifest,
        paths=paths,
        expected_manifest_hash=canonical_json_hash_ref(
            manifest, schema_version=PROFILE
        ),
        assert_deployment_binding=lambda *_: False,
    )
    with pytest.raises(ValidationException, match="unbound"):
        runtime.observe({})
    with pytest.raises(ValidationException, match="binding required"):
        ObservationRuntime(
            manifest=manifest,
            paths=paths,
            expected_manifest_hash={},
            assert_deployment_binding=None,
        )


@pytest.mark.parametrize(
    "change", ["extra", "execution", "archive", "paths", "rows", "version"]
)
def test_closed_manifest_and_independent_hash_refuse_drift(file_seal, change):
    original, paths = file_seal
    manifest = deepcopy(original)
    if change == "extra":
        manifest["verified"] = True
    elif change == "execution":
        manifest["executionQualifiedFamilies"] = ["visits"]
    elif change == "archive":
        manifest["inventoryKernel"]["archiveSha256"] = "4" * 64
    elif change == "paths":
        manifest["files"][0]["path"] = "../outside"
    elif change == "rows":
        manifest["runtimeFiles"]["node"]["files"][0]["extra"] = True
    else:
        manifest["contractVersion"] = "OsbMapperObservationRuntimeV1@1.0"
    with pytest.raises(ValidationException):
        validate_runtime_manifest(manifest)
    with pytest.raises(ValidationException):
        ObservationRuntime(
            manifest=original,
            paths=paths,
            expected_manifest_hash=canonical_json_hash_ref(
                manifest, schema_version=PROFILE
            ),
            assert_deployment_binding=lambda *_: True,
        )


def test_extra_missing_changed_files_and_archive_mismatch_fail_closed(file_seal):
    manifest, paths = file_seal
    target = paths.api_root / "extra.py"
    try:
        target.write_text("extra", encoding="utf-8")
        with pytest.raises(ValidationException):
            verify_runtime_files(manifest, paths)
    finally:
        target.unlink()
    target = paths.api_root / "Pipfile.lock"
    original = target.read_bytes()
    try:
        target.write_bytes(b"changed")
        with pytest.raises(ValidationException):
            verify_runtime_files(manifest, paths)
        target.unlink()
        with pytest.raises(ValidationException):
            verify_runtime_files(manifest, paths)
    finally:
        target.write_bytes(original)
    mismatched = deepcopy(manifest)
    mismatched["apiArchiveSha256"] = "5" * 64
    with pytest.raises(ValidationException):
        verify_runtime_files(mismatched, paths)


@pytest.mark.parametrize("kind", ["duplicate", "traversal", "symlink", "hardlink"])
def test_unsafe_archive_members_never_extract(tmp_path, kind):
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as output:
        member = tarfile.TarInfo("../outside" if kind == "traversal" else "member")
        if kind in ("symlink", "hardlink"):
            member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            member.linkname = "outside"
        output.addfile(member, io.BytesIO(b""))
        if kind == "duplicate":
            output.addfile(member, io.BytesIO(b""))
    with pytest.raises(ValidationException):
        verify_archive(archive, [])
    assert not (tmp_path.parent / "outside").exists()


def test_opt_in_actual_mapper_uses_same_final_document_and_restores_default():
    graph, content = authored_source()
    graph["header"].authored_documents = content.model_dump(mode="json")
    graph["header"].authored_documents_hash = content.content_hash()
    source = NativeStudySource(graph)
    calls = []

    def observe(document):
        calls.append(deepcopy(document))
        result = inventory(document)
        return {
            "census": adapter.census_from_inventory(document, result),
            "diagnostics": result["issues"],
            "executionQualifiedFamilies": [],
            "correspondenceAuthority": False,
        }

    with source.isolated():
        mapper = source.mapper()
        standard = mapper.map_with_report(source.graph["study"], VERSION)
        old_observed = mapper.map_with_correspondence_report(
            source.graph["study"], VERSION
        )
        observed = mapper.map_with_correspondence_report(
            source.graph["study"],
            VERSION,
            observation_runtime=SimpleNamespace(observe=observe),
        )
        assert mapper.map_with_report(source.graph["study"], VERSION) == standard
    assert calls == [standard["document"]]
    assert {
        key: value for key, value in observed.items() if key != "observationRuntime"
    } == old_observed
    assert observed["observationRuntime"]["correspondenceAuthority"] is False


def test_import_shadow_is_refused_even_when_fixture_binding_returns_true(file_seal):
    manifest, paths = file_seal
    runtime = ObservationRuntime(
        manifest=manifest,
        paths=paths,
        expected_manifest_hash=canonical_json_hash_ref(
            manifest, schema_version=PROFILE
        ),
        assert_deployment_binding=lambda *_: True,
    )
    with pytest.raises(ValidationException, match="import shadow"):
        runtime.observe({})
