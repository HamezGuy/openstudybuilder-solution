"""Startup posture for the OSB mapping-authority boundary."""

import pytest

from common.config import Settings, assert_mapping_authority_configuration


def test_production_requires_explicit_authority_mode():
    with pytest.raises(ValueError, match="MAPPING_AUTHORITY_MODE_REQUIRED"):
        assert_mapping_authority_configuration(
            environment="production",
            mode="shadow",
            mode_explicit=False,
            allow_unsafe_legacy_edc_send=False,
        )


def test_production_rejects_legacy_and_unsafe_send():
    with pytest.raises(ValueError, match="MAPPING_AUTHORITY_LEGACY_PRODUCTION_PROHIBITED"):
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


def test_missing_mode_in_dotenv_remains_implicit_and_fails_closed(tmp_path, monkeypatch):
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


def _settings(tmp_path, environment: str, delegated: str | None):
    lines = [
        "NEO4J_DSN=bolt://neo4j:test@127.0.0.1:7687/mdrdb",
        f"DEPLOYMENT_ENVIRONMENT={environment}",
        "MAPPING_AUTHORITY_MODE=shadow",
    ]
    if delegated is not None:
        lines.append(f"OIDC_DELEGATED_CLAIMS_REQUIRED={delegated}")
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(lines), encoding="utf-8")
    return Settings(_env_file=env_file)


def test_production_requires_delegated_claims_for_strict_visibility(tmp_path, monkeypatch):
    """Production forces strict study visibility: every API process refuses to start without delegated claims."""
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    monkeypatch.delenv("OIDC_DELEGATED_CLAIMS_REQUIRED", raising=False)
    with pytest.raises(ValueError, match="OIDC_DELEGATED_PROFILE_REQUIRED"):
        _settings(tmp_path, "production", None).assert_delegated_auth_startup_safe()
    with pytest.raises(ValueError, match="OIDC_DELEGATED_PROFILE_REQUIRED"):
        _settings(tmp_path, "production", "false").assert_delegated_auth_startup_safe()
    _settings(tmp_path, "production", "true").assert_delegated_auth_startup_safe()
    _settings(tmp_path, "development", None).assert_delegated_auth_startup_safe()


def test_every_api_process_asserts_the_production_posture_at_startup():
    """The consumer and extensions APIs call the same three startup assertions as the main API."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[4]
    for module in ("clinical_mdr_api/main.py", "consumer_api/consumer_api.py", "extensions/extensions_api.py"):
        source = (root / module).read_text(encoding="utf-8")
        for guard in (
            "settings.assert_mapping_authority_startup_safe()",
            "settings.assert_delegated_auth_startup_safe()",
            "settings.assert_native_identity_startup_safe()",
        ):
            assert guard in source, f"{module} does not call {guard}"

