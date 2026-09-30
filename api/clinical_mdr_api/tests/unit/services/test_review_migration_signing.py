import io
import json
from unittest.mock import Mock

import pytest

from clinical_mdr_api.generated.platform_contracts.native_identity_command_processor_v1 import NativeIdentityCommandError
from clinical_mdr_api.services.integrations.signing_authorization import platform_signing_authorization
from clinical_mdr_api.services.integrations.review_workspace_migration import is_review_workspace_claim, assert_review_workspace_claim
from clinical_mdr_api.routers.integrations import native_identity
from common.config import settings


def intent():
    return {
        "targetSystem": "osb", "namespace": "accuratrials-osb", "objectType": "study-draft-root",
        "tenantId": "tenant", "platformStudyId": "study", "expectedAbsence": False,
        "requestedInitialState": {
            "operation": "claim_existing", "nativeIdentity": "Study_fixture", "reviewOnly": True,
            "reviewWorkspaceMigrationId": "11111111-1111-4111-8111-111111111111",
            "sourceManifestHash": "sha256:" + "a" * 64,
        },
    }


def test_native_publisher_sends_rotated_credential_outside_receipt(monkeypatch, tmp_path):
    token = tmp_path / "service.token"
    monkeypatch.setenv("OSB_PLATFORM_SIGNING_TOKEN_FILE", str(token))
    monkeypatch.setattr(settings, "deployment_environment", "production")
    publisher = native_identity._RemoteNativeIdentityReceiptPublisher("https://signer.invalid/identity")
    publisher.opener.open = Mock(side_effect=lambda *args, **kwargs: io.BytesIO(json.dumps({
        "signedReceiptEnvelope": {"checked": True}, "verification": {"verified": True}
    }).encode()))
    receipt = {"fixture": "receipt"}
    for value in ("first-service-token", "rotated-service-token"):
        token.write_text(value + "\n")
        assert publisher.publish(receipt) == {"checked": True}
        request = publisher.opener.open.call_args.args[0]
        assert request.get_header("Authorization") == "Bearer " + value
        assert json.loads(request.data) == {
            "receipt": receipt, "producerService": "osb.package", "signingPurpose": "native-identity-binding"
        }
    monkeypatch.delenv("OSB_PLATFORM_SIGNING_TOKEN_FILE")
    with pytest.raises(NativeIdentityCommandError) as error:
        publisher.publish(receipt)
    assert error.value.code == "OSB_SIGNED_PUBLICATION_UNAVAILABLE"
    assert publisher.opener.open.call_count == 2


@pytest.mark.parametrize("value", ["", "bad\nheader", "x" * 4097])
def test_invalid_signing_credentials_are_bounded_and_private(monkeypatch, tmp_path, value):
    path = tmp_path / "token"
    path.write_text(value)
    monkeypatch.setenv("OSB_PLATFORM_SIGNING_TOKEN_FILE", str(path))
    with pytest.raises(ValueError, match="^PLATFORM_SIGNING_CREDENTIAL_UNAVAILABLE$"):
        platform_signing_authorization("production")


def test_signing_transport_rejects_redirects_and_production_http(monkeypatch):
    monkeypatch.setattr(settings, "deployment_environment", "production")
    with pytest.raises(NativeIdentityCommandError):
        native_identity._RemoteNativeIdentityReceiptPublisher("http://signer.invalid/identity")
    assert native_identity._RejectSigningRedirects().redirect_request(None, None, 307, "", {}, "https://other.invalid") is None


def test_review_claim_requires_exact_verified_destination_custody():
    body = intent()
    assert is_review_workspace_claim(body)
    query = Mock(return_value=([[body["requestedInitialState"]["reviewWorkspaceMigrationId"]]], []))
    assert_review_workspace_claim(body, "tenant", "study", query)
    parameters = query.call_args.args[1]
    assert parameters == {
        "migration_id": body["requestedInitialState"]["reviewWorkspaceMigrationId"],
        "tenant_id": "tenant", "platform_study_id": "study",
        "native_study_id": "Study_fixture", "source_manifest_hash": "sha256:" + "a" * 64,
    }
    query.return_value = ([], [])
    with pytest.raises(NativeIdentityCommandError) as error:
        assert_review_workspace_claim(body, "tenant", "study", query)
    assert error.value.code == "REVIEW_WORKSPACE_MIGRATION_SCOPE_DENIED"
    query.reset_mock()
    with pytest.raises(NativeIdentityCommandError):
        assert_review_workspace_claim(body, "other-tenant", "study", query)
    query.assert_not_called()


@pytest.mark.parametrize("change", [
    {"operation": "create"}, {"reviewOnly": False}, {"sourceManifestHash": "arbitrary"},
    {"reviewWorkspaceMigrationId": "invalid"}, {"nativeIdentity": ""},
])
def test_review_custody_cannot_admit_ordinary_creation_or_unpinned_claims(change):
    body = intent()
    body["requestedInitialState"].update(change)
    assert not is_review_workspace_claim(body)


def test_production_endpoint_only_admits_review_migration_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(settings, "deployment_environment", "production")
    monkeypatch.setattr(settings, "native_identity_endpoint_enabled", False)
    monkeypatch.setattr(settings, "review_workspace_identity_enabled", True)
    processor = Mock()
    monkeypatch.setattr(native_identity, "processor", processor)
    body = intent()
    body["requestedInitialState"]["operation"] = "create"
    with pytest.raises(native_identity.HTTPException) as error:
        native_identity.create_or_bind_native_study_root(body)
    assert error.value.status_code == 503
    processor.process.assert_not_called()
