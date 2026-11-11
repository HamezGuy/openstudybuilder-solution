"""The production review lane preserves exact signed custody and narrow scope.

Remote signatures are stubbed here; no test calls a signer or a database.
"""

import asyncio
import base64
import contextlib
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    canonical_json,
    canonical_json_hash_ref,
)
from clinical_mdr_api.routers.integrations import native_identity as routes
from clinical_mdr_api.services.integrations import (
    candidate_set as service,
    signing_authorization as transport,
)
from clinical_mdr_api.tests.unit.services.test_osb_candidate_set_generation import (
    STUDY,
    TENANT,
    _external_binding_bundle,
    _generate,
)
from common.config import Settings, settings


@pytest.fixture
def governed(monkeypatch, tmp_path):
    for field, value in {
        "governed_candidates_enabled": True,
        "deployment_environment": "production",
        "platform_commands_prototype_enabled": False,
        "oauth_enabled": True,
        "delegated_claims_required": True,
    }.items():
        monkeypatch.setattr(settings, field, value)
    for name in (
        "OSB_PLATFORM_COMMAND_SIGNING_URL",
        "OSB_PLATFORM_ARTIFACT_SIGNING_URL",
        "OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_URL",
    ):
        monkeypatch.setenv(name, "https://signer.invalid/" + name.lower())
    token = tmp_path / "token"
    token.write_text("synthetic-test-credential")
    monkeypatch.setenv("OSB_PLATFORM_SIGNING_TOKEN_FILE", str(token))
    monkeypatch.setenv("OSB_PLATFORM_ARTIFACT_REGION", "test-region")
    monkeypatch.setenv("OSB_PLATFORM_PRODUCER_VERSION", "test-revision")
    principal = SimpleNamespace(
        purpose="workflow-orchestration",
        tenant_id=TENANT,
        study_ids={STUDY},
        capabilities={"candidate:read", "candidate:generate"},
    )
    monkeypatch.setattr(routes, "user", lambda: principal)
    return principal


def _request():
    return SimpleNamespace(headers={"x-platform-study-id": STUDY})


def _verification(payload, envelope):
    return {
        "verified": True,
        "payloadHash": envelope["payloadHash"],
        "envelopeHash": canonical_json_hash_ref(
            envelope, schema_version="SignedArtifactEnvelopeV1@1.0.0"
        ),
        "signerKeyId": "synthetic",
        "trustedTime": "2026-11-09T00:00:00Z",
    }


def test_governed_candidates_default_off():
    assert Settings.model_fields["governed_candidates_enabled"].default is False


@pytest.mark.parametrize("field", ["oauth_enabled", "delegated_claims_required"])
def test_real_delegated_auth_is_required(governed, monkeypatch, field):
    monkeypatch.setattr(settings, field, False)
    with pytest.raises(HTTPException) as error:
        routes._candidate_review_enabled()
    assert error.value.status_code == 503


def test_governed_and_prototype_switches_together_are_refused(governed, monkeypatch):
    # With both on, the governed receipt read and upload header would replace
    # the prototype ones while prototype commands still ran.
    monkeypatch.setattr(settings, "platform_commands_prototype_enabled", True)
    monkeypatch.setattr(settings, "deployment_environment", "development")
    with pytest.raises(
        ValueError, match="OSB_GOVERNED_CANDIDATES_CONFIGURATION_REQUIRED"
    ):
        settings.assert_governed_candidates_startup_safe()
    with pytest.raises(HTTPException) as error:
        routes.get_platform_command_receipt("command", _request())
    assert (error.value.status_code, error.value.detail) == (
        503,
        "OSB_GOVERNED_CANDIDATES_CONFIGURATION_REQUIRED",
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://signer.invalid",
        "https://user:secret@signer.invalid",
        "https://signer.invalid?token=x",
        "https://signer.invalid/#x",
        "",
    ],
)
def test_all_production_custody_endpoints_require_clean_https(
    governed, monkeypatch, endpoint
):
    for key in (
        "OSB_PLATFORM_COMMAND_SIGNING_URL",
        "OSB_PLATFORM_ARTIFACT_SIGNING_URL",
        "OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_URL",
    ):
        with monkeypatch.context() as scoped:
            scoped.setenv(key, endpoint)
            with pytest.raises(
                ValueError, match="OSB_GOVERNED_CANDIDATES_CONFIGURATION_REQUIRED"
            ):
                settings.assert_governed_candidates_startup_safe()


def test_governed_review_does_not_enable_other_mutations_or_inventory(governed):
    with pytest.raises(HTTPException, match="503"):
        routes.execute_conformance_command({"action": "platform.demo.conformance"})
    with pytest.raises(HTTPException, match="503"):
        routes.inventory_native_study_roots()
    with pytest.raises(HTTPException) as error:
        routes.execute_candidate_set_command(
            {"action": "osb.mapping.apply", "targetCapability": "candidate:generate"},
            _request(),
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "other"),
        ("tenant_id", ""),
        ("study_ids", {"other"}),
        ("capabilities", {"candidate:apply"}),
    ],
)
def test_exact_proof_read_checks_scope_before_storage(
    governed, monkeypatch, field, value
):
    setattr(governed, field, value)
    monkeypatch.setattr(
        routes,
        "read_native_binding_proof",
        lambda **_: pytest.fail("read outside scope"),
    )
    with pytest.raises(HTTPException) as error:
        routes.get_native_study_binding("receipt", _request())
    assert error.value.status_code == 403


def test_exact_native_proof_preserves_receipt_and_checks_current_hash(monkeypatch):
    store, values = _external_binding_bundle()
    monkeypatch.setattr(service, "db", store)
    receipt = json.loads(store.binding_receipts[0][0])
    proof = service.read_native_binding_proof(
        tenant_id=TENANT, platform_study_id=STUDY, receipt_id=receipt["receiptId"]
    )
    assert proof["receipt"] == receipt
    assert proof["signedReceiptEnvelope"] == json.loads(store.binding_receipts[0][1])
    assert (
        canonical_json_hash_ref(
            proof["targetState"], schema_version="OSBNativeStudyRootStateV1@1.0.0"
        )
        == receipt["targetStateHash"]
    )
    store.native_checkpoint = ("1.0", "RELEASED")
    with pytest.raises(service.OsbCandidateSetError):
        service.read_native_binding_proof(
            tenant_id=TENANT, platform_study_id=STUDY, receipt_id=receipt["receiptId"]
        )
    assert json.loads(store.binding_receipts[0][0]) == receipt
    values[0]["osbStudyIdentity"]["contractVersion"] = "1.0.0"
    with pytest.raises(service.OsbCandidateSetError, match=r"version 1\.1"):
        service.assert_governed_candidate_identity(values[0], TENANT, STUDY)


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        [[None, "actor"]],
        [["invalid-time", "actor"]],
        [["2026-11-09T00:00:00Z", ""]],
    ],
)
def test_binding_metadata_missing_or_invalid_fails_closed(monkeypatch, metadata):
    store, _ = _external_binding_bundle()
    original = store.cypher_query

    def query(text, params=None):
        if "RETURN toString(binding.created_at)" in text:
            return (metadata or [], None)
        return original(text, params)

    monkeypatch.setattr(store, "cypher_query", query)
    monkeypatch.setattr(service, "db", store)
    receipt = json.loads(store.binding_receipts[0][0])
    with pytest.raises(service.OsbCandidateSetError):
        service.read_native_binding_proof(
            tenant_id=TENANT,
            platform_study_id=STUDY,
            receipt_id=receipt["receiptId"],
            include_binding_metadata=True,
        )


def test_binding_metadata_retains_native_time_and_actual_actor(monkeypatch):
    store, _ = _external_binding_bundle()
    original = store.cypher_query
    metadata = ["2026-11-09T00:00:00Z", "service:real-platform-caller"]

    def query(text, params=None):
        if "RETURN toString(binding.created_at)" in text:
            assert (
                params["tenant_id"] == TENANT and params["platform_study_id"] == STUDY
            )
            assert params["binding_id"] == store.binding[0]
            return ([metadata], None)
        return original(text, params)

    monkeypatch.setattr(store, "cypher_query", query)
    monkeypatch.setattr(service, "db", store)
    receipt = json.loads(store.binding_receipts[0][0])
    proof = service.read_native_binding_proof(
        tenant_id=TENANT,
        platform_study_id=STUDY,
        receipt_id=receipt["receiptId"],
        include_binding_metadata=True,
    )
    assert proof["bindingMetadata"] == dict(zip(("createdAt", "createdBy"), metadata))
    assert proof["receipt"] == receipt


def test_receipt_read_is_action_scoped_and_rejects_unsigned(governed, monkeypatch):
    calls = []
    result = {
        "receipt": {"commandId": "command"},
        "publicationMode": "signed",
        "signedReceiptEnvelope": {"test": True},
    }

    def lookup(tenant, study, command, **kwargs):
        calls.append((tenant, study, command, kwargs))
        return "candidate:generate", result

    monkeypatch.setattr(
        routes, "platform_command_store", SimpleNamespace(lookup_with_capability=lookup)
    )
    assert routes.get_platform_command_receipt("command", _request())["data"] is result
    assert calls == [
        (TENANT, STUDY, "command", {"allowed_actions": routes.CANDIDATE_ACTIONS})
    ]
    result["publicationMode"] = "prototype_unsigned"
    with pytest.raises(HTTPException) as error:
        routes.get_platform_command_receipt("command", _request())
    assert error.value.status_code == 403


def test_production_generation_signs_exact_artifact_and_preserves_source(
    governed, monkeypatch
):
    calls = []

    def signer(_endpoint, request, _environment):
        payload = json.loads(base64.b64decode(request["platformArtifactBase64"]))
        descriptor = request["artifactDescriptor"]
        assert descriptor["producerEnvironment"] == "production"
        assert descriptor["purpose"] == "osb-candidate-set"
        assert descriptor["region"] == "test-region"
        assert (
            canonical_json_hash_ref(
                payload,
                schema_version="OsbCandidateSetV1@1.0.0",
                media_type=service.CANDIDATE_SET_MEDIA_TYPE,
            )
            == descriptor["payloadHash"]
        )
        envelope = {
            "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
            "artifactDescriptor": descriptor,
            "payloadHash": descriptor["payloadHash"],
            "signingStatement": {"signingPurpose": "osb-candidate-set"},
            "syntheticSignature": "test-only",
        }
        calls.append((payload, envelope))
        return {
            "signedArtifactEnvelope": envelope,
            "verification": _verification(payload, envelope),
            "productionEligible": True,
        }

    monkeypatch.setattr(service, "platform_signing_request", signer)
    generated, *_ = _generate(monkeypatch)
    assert len(calls) == 1
    assert generated["payload"] == calls[0][0]
    assert generated["signedEnvelope"] == calls[0][1]


@pytest.mark.parametrize(
    "mutation",
    [
        "unverified",
        "wrong_hash",
        "hash_not_an_object",
        "wrong_envelope",
        "not_production",
    ],
)
def test_signature_verifier_never_accepts_unbound_or_untrusted_result(
    governed, monkeypatch, mutation
):
    payload = {"fixture": "exact"}
    envelope = {
        "payloadHash": canonical_json_hash_ref(payload, schema_version="fixture")
    }
    response = {
        "productionEligible": True,
        "verification": _verification(payload, envelope),
    }
    if mutation == "unverified":
        response["verification"]["verified"] = False
    elif mutation == "wrong_hash":
        response["verification"]["payloadHash"] = {"value": "sha256:" + "0" * 64}
    elif mutation == "hash_not_an_object":
        response["verification"]["payloadHash"] = envelope["payloadHash"]["value"]
    elif mutation == "wrong_envelope":
        response["verification"]["envelopeHash"] = {"value": "sha256:" + "0" * 64}
    else:
        response["productionEligible"] = False
    monkeypatch.setattr(routes, "platform_signing_request", lambda *_: response)
    with pytest.raises(service.OsbCandidateSetError) as error:
        routes._verify_candidate_request_signature(payload, envelope)
    assert (error.value.code, error.value.status_code) == (
        "OSB_CANDIDATE_REQUEST_SIGNATURE_UNVERIFIED",
        422,
    )


def test_governed_request_check_reads_the_retained_receipt_once(governed, monkeypatch):
    payload = {
        "tenantId": TENANT,
        "platformStudyId": STUDY,
        "osbStudyIdentity": {"contractVersion": "1.1.0"},
    }
    proof = {"receipt": {"r": 1}, "signedReceiptEnvelope": {"e": 1}}
    binding = {"bindingId": "b", "nativeIdentity": "n", "nativeVersion": "1"}
    events = []
    monkeypatch.setattr(service, "active_osb_binding", lambda *_: binding)
    monkeypatch.setattr(
        service,
        "_assert_identity_binding",
        lambda *_, **__: events.append("proof") or proof,
    )
    monkeypatch.setattr(
        routes,
        "read_native_binding_proof",
        lambda **_: pytest.fail("read the retained receipt a second time"),
    )
    monkeypatch.setattr(
        routes,
        "_verify_candidate_request_signature",
        lambda signed, envelope: events.append((signed, envelope)) or {"ok": True},
    )
    assert routes._verify_governed_candidate_request(payload, {"q": 1}) == {"ok": True}
    assert events == [
        "proof",
        (proof["receipt"], proof["signedReceiptEnvelope"]),
        (payload, {"q": 1}),
    ]

    # Identity evidence that is not version 1.1 never reaches a verifier.
    events.clear()
    payload["osbStudyIdentity"]["contractVersion"] = "1.0.0"
    with pytest.raises(service.OsbCandidateSetError) as error:
        routes._verify_governed_candidate_request(payload, {"q": 1})
    assert error.value.code == "OSB_GOVERNED_IDENTITY_PROOF_REQUIRED"
    assert not events


def test_governed_store_leaves_the_identity_check_to_its_callback(monkeypatch):
    # The store used to read the retained receipt itself and then call the
    # callback, which read it again.
    monkeypatch.setattr(
        service,
        "assert_governed_candidate_identity",
        lambda *_: pytest.fail("the store read the identity proof itself"),
    )

    class Verified(Exception):
        pass

    def verify(payload, envelope):
        raise Verified()

    payload = {
        "contractVersion": sorted(service.ACCEPTED_REQUEST_CONTRACT_VERSIONS)[-1],
        "tenantId": TENANT,
        "platformStudyId": STUDY,
    }
    monkeypatch.setattr(
        service, "assert_candidate_request_transfer_envelope", lambda *_: {"e": 1}
    )
    data = canonical_json(payload).encode("utf-8")
    with pytest.raises(Verified):
        service.store_candidate_request_bytes(
            tenant_id=TENANT,
            platform_study_id=STUDY,
            bytes_value=data,
            expected_hash=service.sha256_bytes(data),
            signed_envelope={"e": 1},
            verify_signature=verify,
        )


def test_signing_transport_refuses_redirect_and_bounds_response(governed, monkeypatch):
    assert (
        transport._RejectSigningRedirects().redirect_request(
            None, None, 307, None, None, "https://other.invalid"
        )
        is None
    )
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, limit):
            captured.append(limit)
            return b"x" * limit

    def open_request(request, timeout):
        assert request.get_header("Authorization") == "Bearer synthetic-test-credential"
        assert timeout == 60
        return Response()

    monkeypatch.setattr(
        transport.urllib.request,
        "build_opener",
        lambda *_: SimpleNamespace(open=open_request),
    )
    with pytest.raises(ValueError, match="PLATFORM_SIGNING_REQUEST_FAILED"):
        transport.platform_signing_request("https://signer.invalid", {}, "production")
    assert captured == [2_000_001]


# The exact header of this envelope has a URL-safe character and would need
# padding, so it differs from the padded standard base64 of the prototype lane.
HEADER_ENVELOPE = {
    "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
    "note": "??>>~~",
}


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _exact_header(envelope) -> str:
    """The header Command Center sends and reads in the governed lane."""
    return _base64url(canonical_json(envelope).encode("utf-8"))


def _standard_header(envelope) -> str:
    """The header Command Center sends in the prototype lane."""
    return base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")


def test_governed_upload_header_is_exact_base64url_canonical_json():
    header = _exact_header(HEADER_ENVELOPE)
    assert "-" in header and len(header) % 4 == 3
    assert (
        service.decode_signed_artifact_envelope_header(header, exact_base64url=True)
        == HEADER_ENVELOPE
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "padded",
        "standard_base64",
        "not_canonical",
        "not_an_object",
        "unused_bits_set",
        "too_long",
        "too_deep",
    ],
)
def test_governed_upload_header_refuses_every_other_encoding(mutation):
    header = _exact_header(HEADER_ENVELOPE)
    if mutation == "padded":
        header += "="
    elif mutation == "standard_base64":
        header = _standard_header(HEADER_ENVELOPE)
    elif mutation == "not_canonical":
        header = _base64url(json.dumps(HEADER_ENVELOPE, indent=1).encode("utf-8"))
    elif mutation == "not_an_object":
        header = _base64url(b'["envelope"]')
    elif mutation == "unused_bits_set":
        # Same decoded bytes, different text: the last character carries two
        # bits no byte uses.
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        changed = header[:-1] + alphabet[alphabet.index(header[-1]) ^ 1]
        assert base64.urlsafe_b64decode(changed + "=") == base64.urlsafe_b64decode(
            header + "="
        )
        header = changed
    elif mutation == "too_deep":
        # Inside the size bound, but nested deeper than the interpreter reads.
        header = _base64url(b'{"a":' + b"[" * 20_000 + b"]" * 20_000 + b"}")
        assert len(header) <= service.MAX_SIGNED_ENVELOPE_HEADER_CHARACTERS
    else:
        header = _exact_header({**HEADER_ENVELOPE, "note": "a" * 50_000})
        assert len(header) > service.MAX_SIGNED_ENVELOPE_HEADER_CHARACTERS
    with pytest.raises(service.OsbCandidateSetError) as error:
        service.decode_signed_artifact_envelope_header(header, exact_base64url=True)
    assert (error.value.code, error.value.status_code) == (
        "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID",
        422,
    )


@pytest.mark.parametrize("exact", [False, True])
def test_upload_header_is_required_in_both_lanes(exact):
    for value in (None, "", " "):
        with pytest.raises(service.OsbCandidateSetError) as error:
            service.decode_signed_artifact_envelope_header(value, exact_base64url=exact)
        assert error.value.code == "OSB_CANDIDATE_REQUEST_SIGNATURE_REQUIRED"


def test_prototype_upload_header_stays_padded_standard_base64():
    assert (
        service.decode_signed_artifact_envelope_header(
            _standard_header(HEADER_ENVELOPE)
        )
        == HEADER_ENVELOPE
    )
    with pytest.raises(service.OsbCandidateSetError) as error:
        service.decode_signed_artifact_envelope_header(_exact_header(HEADER_ENVELOPE))
    assert error.value.code == "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID"


def _upload(monkeypatch, header):
    stored = []
    monkeypatch.setattr(
        routes, "db", SimpleNamespace(transaction=contextlib.nullcontext())
    )
    monkeypatch.setattr(
        routes,
        "store_candidate_request_bytes",
        lambda **arguments: stored.append(
            {**arguments, "thread": threading.get_ident()}
        )
        or {"stored": True},
    )

    async def body():
        return b"{}"

    request = SimpleNamespace(
        headers={
            "x-platform-study-id": STUDY,
            "x-content-sha256": "sha256:" + "0" * 64,
            "x-signed-artifact-envelope": header,
        },
        body=body,
    )
    return stored, lambda: asyncio.run(routes.upload_candidate_request(request))


def test_governed_upload_route_reads_the_exact_header_and_verifies(
    governed, monkeypatch
):
    stored, upload = _upload(monkeypatch, _exact_header(HEADER_ENVELOPE))
    assert upload() == {"ok": True, "data": {"stored": True}}
    assert len(stored) == 1
    assert stored[0]["signed_envelope"] == HEADER_ENVELOPE
    assert stored[0]["verify_signature"] is routes._verify_governed_candidate_request
    assert (stored[0]["tenant_id"], stored[0]["platform_study_id"]) == (TENANT, STUDY)
    # The governed store calls the verifier over HTTPS: never on the event loop
    # thread (asyncio.run puts the loop on this thread).
    assert stored[0]["thread"] != threading.get_ident()

    stored, upload = _upload(monkeypatch, _standard_header(HEADER_ENVELOPE))
    with pytest.raises(HTTPException) as error:
        upload()
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "OSB_CANDIDATE_REQUEST_SIGNATURE_INVALID"
    assert not stored


def test_prototype_upload_route_keeps_its_header_and_skips_governed_checks(
    governed, monkeypatch
):
    monkeypatch.setattr(settings, "governed_candidates_enabled", False)
    monkeypatch.setattr(settings, "platform_commands_prototype_enabled", True)
    monkeypatch.setattr(settings, "deployment_environment", "development")
    stored, upload = _upload(monkeypatch, _standard_header(HEADER_ENVELOPE))
    assert upload() == {"ok": True, "data": {"stored": True}}
    assert stored[0]["signed_envelope"] == HEADER_ENVELOPE
    assert stored[0]["verify_signature"] is None
    assert stored[0]["thread"] == threading.get_ident()


def test_governed_upload_route_reports_a_verifier_refusal_from_its_thread(
    governed, monkeypatch
):
    _, upload = _upload(monkeypatch, _exact_header(HEADER_ENVELOPE))

    def refuse(**_):
        raise service.OsbCandidateSetError(
            "OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_FAILED", "unavailable", 503
        )

    monkeypatch.setattr(routes, "store_candidate_request_bytes", refuse)
    with pytest.raises(HTTPException) as error:
        upload()
    assert error.value.status_code == 503
    assert (
        error.value.detail["code"]
        == "OSB_CANDIDATE_REQUEST_SIGNATURE_VERIFICATION_FAILED"
    )


@pytest.mark.parametrize("prototype", [False, True])
def test_candidate_routes_stay_closed_in_production_without_the_governed_switch(
    governed, monkeypatch, prototype
):
    monkeypatch.setattr(settings, "governed_candidates_enabled", False)
    monkeypatch.setattr(settings, "platform_commands_prototype_enabled", prototype)
    _, upload = _upload(monkeypatch, _exact_header(HEADER_ENVELOPE))
    command = {
        "action": "osb.candidate-set.generate",
        "targetCapability": "candidate:generate",
        "platformStudyId": STUDY,
    }
    for call in (
        lambda: routes.get_native_study_binding("receipt", _request()),
        lambda: routes.get_platform_command_receipt("command", _request()),
        lambda: routes.get_candidate_set("version", _request()),
        lambda: routes.execute_candidate_set_command(command, _request()),
        upload,
    ):
        with pytest.raises(HTTPException) as error:
            call()
        assert (error.value.status_code, error.value.detail) == (
            503,
            "OSB_PLATFORM_COMMANDS_DISABLED",
        )


def _governed_candidate_row(monkeypatch):
    """One signed candidate set as the governed lane stores it."""

    def signer(_endpoint, request, _environment):
        descriptor = request["artifactDescriptor"]
        envelope = {
            "contractVersion": "SignedArtifactEnvelopeV1@1.0.0",
            "artifactDescriptor": descriptor,
            "payloadHash": descriptor["payloadHash"],
            "signingStatement": {"signingPurpose": "osb-candidate-set"},
            "syntheticSignature": "test-only",
        }
        return {
            "signedArtifactEnvelope": envelope,
            "verification": _verification(None, envelope),
            "productionEligible": True,
        }

    monkeypatch.setattr(service, "platform_signing_request", signer)
    generated, *_ = _generate(monkeypatch)
    return generated, [
        canonical_json(generated["payload"]),
        generated["payloadHash"]["value"],
        canonical_json(generated["artifactRef"]),
        canonical_json(generated["signedEnvelope"]),
    ]


def test_governed_candidate_read_returns_the_headers_command_center_checks(
    governed, monkeypatch
):
    generated, row = _governed_candidate_row(monkeypatch)
    version = generated["candidateSetVersionId"]
    verified = []

    def verifier(_endpoint, request, _environment):
        verified.append(request)
        return {
            "productionEligible": True,
            "verification": _verification(None, request["envelope"]),
        }

    monkeypatch.setattr(routes, "platform_signing_request", verifier)
    monkeypatch.setattr(
        routes, "db", SimpleNamespace(cypher_query=lambda *_: ([row], None))
    )
    response = routes.get_candidate_set(version, _request())
    payload_bytes = canonical_json(generated["payload"]).encode("utf-8")
    assert response.body == payload_bytes
    assert response.media_type == service.CANDIDATE_SET_MEDIA_TYPE
    assert response.headers["content-length"] == str(len(payload_bytes))
    # Command Center compares both headers with its own encoding of the
    # descriptor and the envelope: unpadded base64url of the canonical JSON.
    assert response.headers["x-artifact-reference"] == _exact_header(
        generated["artifactRef"]
    )
    envelope_header = response.headers["x-signed-artifact-envelope"]
    assert envelope_header == _exact_header(generated["signedEnvelope"])
    assert (
        service.decode_signed_artifact_envelope_header(
            envelope_header, exact_base64url=True
        )
        == generated["signedEnvelope"]
    )
    artifact = generated["artifactRef"]
    assert (
        artifact["kind"],
        artifact["producerService"],
        artifact["producerEnvironment"],
        artifact["purpose"],
        artifact["payloadContract"],
        artifact["payloadContractVersion"],
        artifact["stableLocator"],
        artifact["artifactId"],
        artifact["createdAt"],
    ) == (
        "osb-candidate-set",
        "osb.package",
        "production",
        "osb-candidate-set",
        "accuratrials.osb.OsbCandidateSetV1",
        "1.0.0",
        f"artifact://osb/candidate-set/{version}",
        generated["payload"]["candidateSetId"],
        generated["payload"]["createdAt"],
    )
    assert verified == [
        {
            "payloadHashValue": generated["payloadHash"]["value"],
            "payloadByteSize": len(payload_bytes),
            "envelope": generated["signedEnvelope"],
        }
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "other_envelope",
        "other_descriptor",
        "other_bytes",
        "hash_not_an_object",
        "unsigned",
    ],
)
def test_governed_candidate_read_refuses_custody_that_does_not_match(
    governed, monkeypatch, mutation
):
    generated, row = _governed_candidate_row(monkeypatch)
    if mutation == "other_envelope":
        envelope = json.loads(row[3])
        envelope["artifactDescriptor"]["producerVersion"] = "other-revision"
        row[3] = canonical_json(envelope)
    elif mutation == "other_descriptor":
        artifact = json.loads(row[2])
        artifact["region"] = "other-region"
        row[2] = canonical_json(artifact)
    elif mutation == "other_bytes":
        row[0] = canonical_json({**generated["payload"], "createdBy": "other"})
    elif mutation == "hash_not_an_object":
        artifact = json.loads(row[2])
        artifact["payloadHash"] = artifact["payloadHash"]["value"]
        row[2] = canonical_json(artifact)
    else:
        row[3] = None
    monkeypatch.setattr(
        routes,
        "platform_signing_request",
        lambda *_: pytest.fail("verified custody that does not match"),
    )
    monkeypatch.setattr(
        routes, "db", SimpleNamespace(cypher_query=lambda *_: ([row], None))
    )
    with pytest.raises(HTTPException) as error:
        routes.get_candidate_set(generated["candidateSetVersionId"], _request())
    assert (error.value.status_code, error.value.detail) == (
        409,
        "OSB_CANDIDATE_SET_CUSTODY_INVALID",
    )
