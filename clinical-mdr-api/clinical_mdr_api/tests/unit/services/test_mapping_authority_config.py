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