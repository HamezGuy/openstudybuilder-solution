"""Startup posture for the OSB mapping-authority boundary."""

from pathlib import Path

import pytest

from common.config import Settings, assert_mapping_authority_configuration


def _governed_creation_settings(monkeypatch, **overrides):
    monkeypatch.setenv(
        "OSB_NATIVE_IDENTITY_SIGNING_URL", "https://signer.example/receipt"
    )
    monkeypatch.setenv(
        "OSB_PLATFORM_SIGNING_TOKEN_FILE", str(Path("test-signing-token").resolve())
    )
    return Settings.model_construct(
        deployment_environment="production",
        governed_study_creation_enabled=True,
        oauth_enabled=True,
        delegated_claims_required=True,
        **overrides,
    )


def test_governed_creation_is_default_off():
    assert Settings.model_construct().governed_study_creation_enabled is False


def test_governed_creation_startup_keeps_prototypes_disabled(monkeypatch):
    configured = _governed_creation_settings(monkeypatch)
    configured.assert_native_identity_startup_safe()
    for field in (
        "native_identity_endpoint_enabled",
        "native_identity_inventory_enabled",
        "platform_commands_prototype_enabled",
    ):
        configured = _governed_creation_settings(monkeypatch, **{field: True})
        with pytest.raises(
            ValueError, match="OSB_NATIVE_IDENTITY_PRODUCTION_PROHIBITED"
        ):
            configured.assert_native_identity_startup_safe()


@pytest.mark.parametrize("field", ["oauth_enabled", "delegated_claims_required"])
def test_governed_creation_requires_strict_delegation(monkeypatch, field):
    configured = _governed_creation_settings(monkeypatch)
    setattr(configured, field, False)
    with pytest.raises(
        ValueError, match="OSB_GOVERNED_STUDY_CREATION_CONFIGURATION_REQUIRED"
    ):
        configured.assert_native_identity_startup_safe()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://signer.example/receipt",
        "https://user:secret@signer.example/receipt",
        "https://signer.example/receipt?token=x",
        "https://signer.example/receipt#x",
    ],
)
def test_governed_creation_requires_authenticated_https_signing(monkeypatch, url):
    configured = _governed_creation_settings(monkeypatch)
    monkeypatch.setenv("OSB_NATIVE_IDENTITY_SIGNING_URL", url)
    with pytest.raises(
        ValueError, match="OSB_GOVERNED_STUDY_CREATION_CONFIGURATION_REQUIRED"
    ):
        configured.assert_native_identity_startup_safe()


def test_governed_creation_requires_an_absolute_token_file(monkeypatch):
    configured = _governed_creation_settings(monkeypatch)
    monkeypatch.setenv("OSB_PLATFORM_SIGNING_TOKEN_FILE", "relative-token")
    with pytest.raises(
        ValueError, match="OSB_GOVERNED_STUDY_CREATION_CONFIGURATION_REQUIRED"
    ):
        configured.assert_native_identity_startup_safe()


def test_production_requires_explicit_authority_mode():
    with pytest.raises(ValueError, match="MAPPING_AUTHORITY_MODE_REQUIRED"):
        assert_mapping_authority_configuration(
            environment="production",
            mode="shadow",
            mode_explicit=False,
            allow_unsafe_legacy_edc_send=False,
        )


def test_production_rejects_legacy_and_unsafe_send():
    with pytest.raises(
        ValueError, match="MAPPING_AUTHORITY_LEGACY_PRODUCTION_PROHIBITED"
    ):
        assert_mapping_authority_configuration(
            environment="production",
            mode="legacy",
            mode_explicit=True,
            allow_unsafe_legacy_edc_send=False,
        )

    with pytest.raises(ValueError, match="LEGACY_EDC_SEND_PRODUCTION_PROHIBITED"):
        assert_mapping_authority_configuration(
            environment="production",
            mode="shadow",
            mode_explicit=True,
            allow_unsafe_legacy_edc_send=True,
        )


def test_explicit_production_shadow_and_enforced_are_startup_safe():
    for mode in ("shadow", "enforced"):
        assert_mapping_authority_configuration(
            environment="production",
            mode=mode,
            mode_explicit=True,
            allow_unsafe_legacy_edc_send=False,
        )


def test_explicit_mode_loaded_from_dotenv_is_recognized(tmp_path, monkeypatch):
    monkeypatch.delenv("MAPPING_AUTHORITY_MODE", raising=False)
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "NEO4J_DSN=bolt://neo4j:test@127.0.0.1:7687/mdrdb",
                "DEPLOYMENT_ENVIRONMENT=production",
                "MAPPING_AUTHORITY_MODE=shadow",
                "ALLOW_UNSAFE_LEGACY_EDC_SEND=false",
            )
        ),
        encoding="utf-8",
    )

    loaded = Settings(_env_file=env_file)

    assert "mapping_authority_mode" in loaded.model_fields_set
    loaded.assert_mapping_authority_startup_safe()


def test_missing_mode_in_dotenv_remains_implicit_and_fails_closed(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MAPPING_AUTHORITY_MODE", raising=False)
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "NEO4J_DSN=bolt://neo4j:test@127.0.0.1:7687/mdrdb",
                "DEPLOYMENT_ENVIRONMENT=production",
            )
        ),
        encoding="utf-8",
    )

    loaded = Settings(_env_file=env_file)

    assert "mapping_authority_mode" not in loaded.model_fields_set
    with pytest.raises(ValueError, match="MAPPING_AUTHORITY_MODE_REQUIRED"):
        loaded.assert_mapping_authority_startup_safe()


def _settings(
    tmp_path,
    environment: str,
    delegated: str | None,
    *,
    oauth: str = "true",
    clients: str | None = None,
):
    lines = [
        "NEO4J_DSN=bolt://neo4j:test@127.0.0.1:7687/mdrdb",
        f"DEPLOYMENT_ENVIRONMENT={environment}",
        "MAPPING_AUTHORITY_MODE=shadow",
        f"OAUTH_ENABLED={oauth}",
    ]
    if delegated is not None:
        lines.append(f"OIDC_DELEGATED_CLAIMS_REQUIRED={delegated}")
    if clients is not None:
        lines.append(f"OIDC_EXCHANGING_CLIENTS={clients}")
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(lines), encoding="utf-8")
    return Settings(_env_file=env_file)


@pytest.fixture
def posture_env(monkeypatch):
    # The posture under test comes from the .env file alone; CI runs the suite
    # with OAuth off.
    for name in (
        "DEPLOYMENT_ENVIRONMENT",
        "OIDC_DELEGATED_CLAIMS_REQUIRED",
        "OAUTH_ENABLED",
        "OIDC_EXCHANGING_CLIENTS",
    ):
        monkeypatch.delenv(name, raising=False)


# The synthetic sandbox's harness issuer
# (CommandCenter synthetic-trial/sandbox/osb/compose.osb.yaml).
OTHER_ISSUER_CLIENTS = "at-syn-osb-designer,at-syn-osb-librarian"


def test_production_requires_delegated_claims_for_strict_visibility(
    tmp_path, posture_env
):
    """Production forces strict study visibility: every API process refuses to start without delegated claims."""
    other = OTHER_ISSUER_CLIENTS
    for refused in (
        _settings(tmp_path, "production", None, clients=other),
        _settings(tmp_path, "production", "false", clients=other),
        _settings(tmp_path, "production", "true", oauth="false"),
    ):
        with pytest.raises(ValueError, match="OIDC_DELEGATED_PROFILE_REQUIRED"):
            refused.assert_delegated_auth_startup_safe()
    for started in (
        _settings(tmp_path, "production", "true", clients=other),
        _settings(tmp_path, "development", None, clients=other),
    ):
        started.assert_delegated_auth_startup_safe()


@pytest.mark.parametrize("environment", ["development", "staging", "production"])
@pytest.mark.parametrize("delegated", [None, "false", "true"])
@pytest.mark.parametrize(
    "clients",
    [
        None,
        "accuratrial-command-center",
        "accuratrial-control-worker",
        "accuratrial-command-center,accuratrial-control-worker,other-client",
    ],
)
def test_trusting_command_center_holds_its_tokens_to_the_delegated_profile(
    tmp_path, posture_env, environment, delegated, clients
):
    """(hole) OAuth on with Command Center's clients: the delegated profile, always."""
    configured = _settings(tmp_path, environment, delegated, clients=clients)
    assert configured.trusts_command_center
    assert configured.delegated_claims_required is True
    configured.assert_delegated_auth_startup_safe()


def test_oauth_off_and_other_issuers_keep_the_configured_profile(
    tmp_path, posture_env
):
    """Local compose (OAuth off) and the sandbox's harness issuer are unchanged."""
    other = OTHER_ISSUER_CLIENTS
    for delegated in (None, "False"):
        local = _settings(tmp_path, "development", delegated, oauth="False")
        assert not local.trusts_command_center
        assert local.delegated_claims_required is False
        local.assert_delegated_auth_startup_safe()
        sandbox = _settings(tmp_path, "development", delegated, clients=other)
        assert not sandbox.trusts_command_center
        assert sandbox.delegated_claims_required is False
    explicit = _settings(tmp_path, "development", "true", clients=other)
    assert explicit.delegated_claims_required is True


@pytest.mark.asyncio
async def test_a_command_center_viewer_token_outside_the_profile_is_refused(
    tmp_path, posture_env, monkeypatch
):
    """(hole) A staging OSB behind Command Center holds tokens to the profile."""
    from authlib.jose import JWTClaims

    from common.auth import dependencies
    from common.exceptions import NotAuthenticatedException

    configured = _settings(tmp_path, "staging", "false")
    monkeypatch.setattr(dependencies, "settings", configured)
    now = 1_900_000_000
    # A Command Center viewer's OSB roles and read capability, with an hour's
    # lifetime and no tenant.
    viewer = {
        "iss": "https://control.example.test",
        "sub": "edc:7",
        "aud": ["accuratrial-openstudybuilder"],
        "iat": now,
        "exp": now + 3600,
        "type": "access",
        "azp": "accuratrial-command-center",
        "client_id": "accuratrial-command-center",
        "purpose": "interactive-domain-access",
        "capabilities": ["study:read"],
        "roles": ["Study.Read", "Library.Read"],
        "subject_type": "human",
    }

    async def verified(_token):
        return JWTClaims(viewer, {})

    monkeypatch.setattr(dependencies.jwks_service, "validate_jwt", verified)
    with pytest.raises(
        NotAuthenticatedException, match="Delegated access-token profile is invalid"
    ):
        await dependencies.validate_token("synthetic.viewer.token")


def test_every_api_process_asserts_the_production_posture_at_startup():
    """The consumer and extensions APIs call the same three startup assertions as the main API."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[4]
    for module in (
        "clinical_mdr_api/main.py",
        "consumer_api/consumer_api.py",
        "extensions/extensions_api.py",
    ):
        source = (root / module).read_text(encoding="utf-8")
        for guard in (
            "settings.assert_mapping_authority_startup_safe()",
            "settings.assert_delegated_auth_startup_safe()",
            "settings.assert_native_identity_startup_safe()",
        ):
            assert guard in source, f"{module} does not call {guard}"
