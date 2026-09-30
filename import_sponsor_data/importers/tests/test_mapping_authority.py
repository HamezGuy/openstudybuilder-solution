"""Direct tests for fail-closed legacy helper policy."""

import pytest

from ..utils.mapping_authority import (
    assert_legacy_comparison_allowed,
    assert_unsafe_legacy_mutation_allowed,
    mapping_authority_mode,
)


def test_invalid_mode_is_rejected_instead_of_becoming_shadow(monkeypatch):
    monkeypatch.setenv("MAPPING_AUTHORITY_MODE", "shdaow")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "development")
    with pytest.raises(RuntimeError, match="MAPPING_AUTHORITY_MODE_INVALID"):
        mapping_authority_mode()


def test_production_requires_explicit_nonlegacy_mode(monkeypatch):
    monkeypatch.delenv("MAPPING_AUTHORITY_MODE", raising=False)
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="MAPPING_AUTHORITY_MODE_REQUIRED"):
        mapping_authority_mode()

    monkeypatch.setenv("MAPPING_AUTHORITY_MODE", "legacy")
    with pytest.raises(
        RuntimeError, match="MAPPING_AUTHORITY_LEGACY_PRODUCTION_PROHIBITED"
    ):
        mapping_authority_mode()


def test_shadow_allows_comparison_but_never_mutation(monkeypatch):
    monkeypatch.setenv("MAPPING_AUTHORITY_MODE", "shadow")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "development")
    assert_legacy_comparison_allowed("verify")
    with pytest.raises(RuntimeError, match="MAPPING_AUTHORITY_SHADOW"):
        assert_unsafe_legacy_mutation_allowed("publish")


def test_disposable_legacy_mutation_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("MAPPING_AUTHORITY_MODE", "legacy")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "development")
    monkeypatch.delenv("ALLOW_UNSAFE_LEGACY_EDC_HELPERS", raising=False)
    with pytest.raises(RuntimeError, match="LEGACY_HELPER_EXPLICIT_OPT_IN_REQUIRED"):
        assert_unsafe_legacy_mutation_allowed("publish")

    monkeypatch.setenv("ALLOW_UNSAFE_LEGACY_EDC_HELPERS", "1")
    assert_unsafe_legacy_mutation_allowed("publish")


def test_enforced_blocks_comparison_and_mutation(monkeypatch):
    monkeypatch.setenv("MAPPING_AUTHORITY_MODE", "enforced")
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "development")
    with pytest.raises(RuntimeError, match="MAPPING_AUTHORITY_ENFORCED"):
        assert_legacy_comparison_allowed("verify")
    with pytest.raises(RuntimeError, match="MAPPING_AUTHORITY_ENFORCED"):
        assert_unsafe_legacy_mutation_allowed("publish")