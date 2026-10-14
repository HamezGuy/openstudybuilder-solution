"""Carrier custody boundaries using real contracts/parser and in-memory I/O.

Run with --noconftest; no service, database, or network fixture is needed.
The database double covers immutable comparisons, not Neo4j lock semantics.
"""

from __future__ import annotations

import base64
import copy
import importlib
import json
import socket
import sys

import pytest

from clinical_mdr_api.tests.unit.services.test_native_package_v2 import HASHING

TENANT = "00000000-0000-4000-8000-000000000001"
STUDY = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"
canonical = HASHING["canonical_json"]
hash_ref = HASHING["canonical_json_hash_ref"]


class RetainedArtifacts:
    """Keep original rows; MERGE never overwrites an existing identity."""

    def __init__(self):
        self.rows = {}
        self.calls = []

    def cypher_query(self, query, parameters):
        self.calls.append((query, copy.deepcopy(parameters)))
        key = tuple(
            parameters[field]
            for field in ("tenant_id", "artifact_id", "artifact_version_id")
        )
        if "MERGE (artifact:OsbInboundCarrierArtifact" in query:
            self.rows.setdefault(
                key,
                [
                    parameters[field]
                    for field in (
                        "platform_study_id",
                        "artifact_ref_json",
                        "payload_json",
                        "byte_size",
                        "kind",
                        "payload_hash",
                        "descriptor_hash",
                    )
                ],
            )
            return [copy.deepcopy(self.rows[key])], []
        assert "MATCH (artifact:OsbInboundCarrierArtifact" in query
        row = self.rows.get(key)
        if row is None or row[0] != parameters["platform_study_id"]:
            return [], []
        return [copy.deepcopy(row[1:])], []


@pytest.fixture
def custody(monkeypatch):
    connection_attempts = []
    original_connect = socket.socket.connect

    def no_connection(*args, **_kwargs):
        # Windows asyncio constructs an internal self-pipe through stdlib's
        # socketpair fallback. Permit only that function's exact fresh listener;
        # no arbitrary localhost service or database connection is permitted.
        caller = sys._getframe(1)
        if (
            caller.f_code.co_name == "_fallback_socketpair"
            and caller.f_code.co_filename == socket.__file__
            and len(args) == 2
            and "lsock" in caller.f_locals
            and args[1] == caller.f_locals["lsock"].getsockname()
        ):
            return original_connect(*args)
        connection_attempts.append(args)
        raise AssertionError("Carrier custody unit tests must not connect to a service")

    monkeypatch.setattr(socket.socket, "connect", no_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", no_connection)
    monkeypatch.setattr(socket, "create_connection", no_connection)
    monkeypatch.setenv("NEO4J_DSN", "bolt://localhost:1")
    runtime = importlib.import_module(
        "clinical_mdr_api.services.integrations.native_package_artifact_custody"
    )
    database = RetainedArtifacts()
    monkeypatch.setattr(runtime, "db", database)
    monkeypatch.setattr(runtime, "MAX_CARRIER_ARTIFACT_BYTES", 128 * 1024 * 1024)
    yield vars(runtime), database
    assert not connection_attempts


def reference(payload):
    descriptor = {
        "contractVersion": "ArtifactDescriptorV1@1.0.0",
        "artifactId": "00000000-0000-4000-8000-000000000004",
        "artifactVersionId": "00000000-0000-4000-8000-000000000005",
        "kind": "osb-package-native-state",
        "stableLocator": "artifact://osb/native-state/synthetic-custody-test",
        "payloadHash": hash_ref(
            payload, schema_version="OsbPackageNativeStateV1@1.2.0"
        ),
        "byteSize": len(canonical(payload).encode("utf-8")),
        "classification": "regulated-non-phi",
        "tenantId": TENANT,
        "region": "us-central1",
        "producerService": "osb.clinical-mdr-api",
        "producerEnvironment": "prototype",
        "producerVersion": "synthetic-custody-test",
        "payloadContract": "accuratrials.osb.OsbPackageNativeStateV1",
        "payloadContractVersion": "1.2.0",
        "purpose": "synthetic-custody-test",
        "createdAt": "2026-10-13T00:00:00Z",
    }
    return {
        **descriptor,
        "contractVersion": "ArtifactRefV1@1.0.0",
        "descriptorHash": HASHING["descriptor_hash"](descriptor),
    }


def rehash_descriptor(artifact):
    fields = {
        key: value
        for key, value in artifact.items()
        if key not in {"contractVersion", "descriptorHash"}
    }
    artifact["descriptorHash"] = HASHING["descriptor_hash"](
        {"contractVersion": "ArtifactDescriptorV1@1.0.0", **fields}
    )
    return artifact


@pytest.fixture
def example():
    payload = {
        "contractVersion": "OsbPackageNativeStateV1@1.2.0",
        "tenantId": TENANT,
        "platformStudyId": STUDY,
        "root": {
            "bindingId": OTHER,
            "nativeStudyId": "Study_Synthetic",
            "relationship": "LATEST_LOCKED",
            "nativeVersion": "1",
            "nativeStatus": "Locked",
            "versionTimestamp": "2026-10-13T00:00:00Z",
            "draftEndedAt": "2026-10-13T00:00:00Z",
            "title": None,
            "studyNumber": None,
            "acronym": None,
            "projectNumber": None,
        },
        "canonicalDefinitionHash": hash_ref(
            {}, schema_version="OsbCanonicalStudyDefinitionV1@1.0.0"
        ),
        "contentIndex": [],
        "nativeEvidenceBasis": {
            "kind": "no-native-operations",
            "reason": "Authored execution is carried under its accepted canonical mapping and must be explicitly reviewed as extensions; no native-operation claim is made.",
        },
        "sourceBasisHash": hash_ref(
            {}, schema_version="AcceptedCanonicalSourceBasisV1@1.0.0"
        ),
        "carrierHash": hash_ref(
            {}, schema_version="AcceptedStudyExchangeCarrierV1@1.0.0"
        ),
        "bundlePayloadHash": hash_ref(
            {},
            schema_version="StudyExchangeBundleV2@2.0.0",
            media_type="application/vnd.accuratrial.studybundle+json",
        ),
        "crosswalkHash": hash_ref(
            {}, schema_version="AuthoredStudyMemberCrosswalkV1@1.0.0"
        ),
    }
    return payload, reference(payload)


def store(module, payload, artifact, **overrides):
    parameters = {
        "artifact_ref": artifact,
        "tenant_id": TENANT,
        "platform_study_id": STUDY,
        "bytes_value": canonical(payload).encode("utf-8"),
        "expected_hash": artifact["payloadHash"]["value"],
    }
    return module["store_carrier_artifact_bytes"](**{**parameters, **overrides})


def load(module, artifact, **overrides):
    return module["load_carrier_artifact"](
        **{
            "artifact_ref": artifact,
            "tenant_id": TENANT,
            "platform_study_id": STUDY,
            **overrides,
        }
    )


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def test_exact_descriptor_and_idless_payload_retain_and_replay(custody, example):
    module, database = custody
    payload, artifact = example
    assert "artifactId" not in payload and "artifactVersionId" not in payload
    first = store(module, payload, artifact)
    assert first == store(module, payload, artifact)
    assert first["artifactRef"] == artifact
    assert load(module, artifact) == payload
    assert len(database.rows) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("region", "eu-central1"),
        ("stableLocator", "https://invalid.example/never-fetch"),
        ("purpose", "different-purpose"),
    ],
)
def test_same_identity_cannot_replace_full_descriptor(custody, example, field, value):
    module, database = custody
    payload, artifact = example
    store(module, payload, artifact)
    before = copy.deepcopy(database.rows)
    changed = rehash_descriptor({**artifact, field: value})
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        store(module, payload, changed)
    assert error.value.code == "OSB_CARRIER_ARTIFACT_TRANSFER_CONFLICT"
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        load(module, changed)
    assert error.value.code == "OSB_CARRIER_ARTIFACT_REFERENCE_CONFLICT"
    assert database.rows == before


def test_same_identity_cannot_replace_payload(custody, example):
    module, database = custody
    payload, artifact = example
    store(module, payload, artifact)
    before = copy.deepcopy(database.rows)
    changed = copy.deepcopy(payload)
    changed["root"]["title"] = "Changed retained study"
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        store(module, changed, reference(changed))
    assert error.value.code == "OSB_CARRIER_ARTIFACT_TRANSFER_CONFLICT"
    assert database.rows == before


def test_scope_cannot_cross_tenant_or_study(custody, example):
    module, database = custody
    payload, artifact = example
    for overrides in ({"tenant_id": OTHER}, {"platform_study_id": OTHER}):
        with pytest.raises(module["OsbCandidateSetError"]):
            store(module, payload, artifact, **overrides)
    assert not database.calls
    store(module, payload, artifact)
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        load(module, artifact, platform_study_id=OTHER)
    assert error.value.code == "OSB_CARRIER_ARTIFACT_NOT_TRANSFERRED"
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        load(module, artifact, tenant_id=OTHER)
    assert error.value.code == "OSB_CARRIER_ARTIFACT_SCOPE_MISMATCH"


def test_same_bytes_may_have_distinct_complete_version_identities(custody, example):
    module, database = custody
    payload, artifact = example
    store(module, payload, artifact)
    other = rehash_descriptor({**artifact, "artifactVersionId": OTHER})
    store(module, payload, other)
    assert len(database.rows) == 2
    assert load(module, artifact) == load(module, other) == payload


@pytest.mark.parametrize(
    "column,value",
    [
        (1, "{}"),
        (2, "{}"),
        (3, 1),
        (4, "other-kind"),
        (5, "sha256:" + "0" * 64),
        (6, "sha256:" + "0" * 64),
    ],
)
def test_every_read_revalidates_retained_descriptor_bytes_and_metadata(
    custody, example, column, value
):
    module, database = custody
    payload, artifact = example
    store(module, payload, artifact)
    next(iter(database.rows.values()))[column] = value
    with pytest.raises(module["OsbCandidateSetError"]):
        load(module, artifact)


def test_read_revalidates_payload_scope_even_when_bytes_and_hash_are_valid(
    custody, example
):
    module, database = custody
    payload, _ = example
    changed = {**payload, "platformStudyId": OTHER}
    artifact = reference(changed)
    store(module, changed, artifact, platform_study_id=OTHER)
    next(iter(database.rows.values()))[0] = STUDY
    with pytest.raises(module["OsbCandidateSetError"]):
        load(module, artifact)


def test_byte_size_digest_and_reference_integrity_checked_before_storage(
    custody, example
):
    module, database = custody
    payload, artifact = example
    wrong_size = rehash_descriptor({**artifact, "byteSize": artifact["byteSize"] + 1})
    wrong_descriptor = {
        **artifact,
        "descriptorHash": {**artifact["descriptorHash"], "value": "sha256:" + "0" * 64},
    }
    for ref, overrides in [
        (artifact, {"expected_hash": "sha256:" + "0" * 64}),
        (wrong_size, {}),
        (wrong_descriptor, {}),
        (artifact, {"bytes_value": bytearray(b"{}")}),
    ]:
        with pytest.raises(module["OsbCandidateSetError"]):
            store(module, payload, ref, **overrides)
    assert not database.calls


@pytest.mark.parametrize(
    "raw",
    [
        b'{"secret-field":1,"secret-field":2}',
        b"\xff",
        b"[]",
        b"null",
        b'{"x":NaN}',
        b'{"x":1e999}',
        b'{"x":"\\ud800"}',
        b'{ "x": 1 }',
    ],
)
def test_transfer_rejects_noncanonical_bytes_before_storage(custody, example, raw):
    module, database = custody
    payload, artifact = example
    changed = copy.deepcopy(artifact)
    changed["payloadHash"]["value"] = HASHING["sha256_bytes"](raw)
    changed["byteSize"] = len(raw)
    rehash_descriptor(changed)
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        store(module, payload, changed, bytes_value=raw)
    assert error.value.code == "OSB_CARRIER_ARTIFACT_JSON_INVALID"
    assert "secret-field" not in str(error.value)
    assert not database.calls


def test_transfer_rejects_unsupported_profile_before_storage(custody, example):
    module, database = custody
    payload, artifact = example
    changed = rehash_descriptor({**artifact, "payloadContractVersion": "99.0.0"})
    with pytest.raises(module["OsbCandidateSetError"]):
        store(module, payload, changed)
    assert not database.calls


@pytest.mark.parametrize("value", ["\ud800", "é" * 1000])
def test_stored_unicode_is_bounded_before_full_encoding(custody, example, value):
    module, database = custody
    payload, artifact = example
    store(module, payload, artifact)
    next(iter(database.rows.values()))[2] = value
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        load(module, artifact)
    assert error.value.code == "OSB_CARRIER_ARTIFACT_STORED_INVALID"


def test_declared_and_actual_size_limits_apply_before_storage(custody, example):
    module, database = custody
    payload, artifact = example
    module["MAX_CARRIER_ARTIFACT_BYTES"] = 16
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        store(module, payload, artifact)
    assert error.value.status_code == 413
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        load(module, artifact)
    assert error.value.status_code == 413
    assert not database.calls


def test_reference_header_round_trip_uses_complete_canonical_descriptor(
    custody, example
):
    module, _ = custody
    _, artifact = example
    assert (
        module["decode_artifact_reference_header"](
            encode(canonical(artifact).encode("utf-8"))
        )
        == artifact
    )


@pytest.mark.parametrize(
    "value", [None, 1, "", "=", "e30=", "a", "é", "+/", " e30", "e30\n", "e31"]
)
def test_reference_header_rejects_noncanonical_base64url(custody, value):
    module, database = custody
    with pytest.raises(module["OsbCandidateSetError"]):
        module["decode_artifact_reference_header"](value)
    assert not database.calls


@pytest.mark.parametrize(
    "raw",
    [
        b'{"secret-field":1,"secret-field":2}',
        b"\xff",
        b"[]",
        b"null",
        b'{"x":NaN}',
        b'{"x":1e999}',
        b'{"x":"\\ud800"}',
        b'{"x":' + b"9" * 400 + b"}",
    ],
)
def test_reference_header_rejects_invalid_json_without_echoing_content(custody, raw):
    module, _ = custody
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        module["decode_artifact_reference_header"](encode(raw))
    assert "secret-field" not in str(error.value)


def test_header_rejects_whitespace_json_corrupt_descriptor_and_oversize(
    custody, example
):
    module, _ = custody
    _, artifact = example
    for header in (
        encode(json.dumps(artifact, indent=2).encode("utf-8")),
        encode(canonical({**artifact, "purpose": "unhashed-change"}).encode("utf-8")),
        "A" * 8193,
    ):
        with pytest.raises(module["OsbCandidateSetError"]):
            module["decode_artifact_reference_header"](header)


def test_storage_failure_returns_sanitized_error(custody, example):
    module, database = custody
    payload, artifact = example

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("driver query parameters include secret-field")

    database.cypher_query = unavailable
    with pytest.raises(module["OsbCandidateSetError"]) as error:
        store(module, payload, artifact)
    assert error.value.code == "OSB_CARRIER_ARTIFACT_CUSTODY_UNAVAILABLE"
    assert error.value.status_code == 503
    assert "secret-field" not in str(error.value)
