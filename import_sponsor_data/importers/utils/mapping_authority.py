"""Fail-closed policy for legacy OSB helpers and EDC deployment boundaries."""

from __future__ import annotations

import os


def mapping_authority_mode() -> str:
    configured = os.environ.get("MAPPING_AUTHORITY_MODE")
    if configured is None or not configured.strip():
        if deployment_environment() in {"prod", "production"}:
            raise RuntimeError(
                "MAPPING_AUTHORITY_MODE_REQUIRED: production must explicitly select "
                "shadow or enforced"
            )
        return "shadow"
    mode = configured.strip().lower()
    if mode not in {"legacy", "shadow", "enforced"}:
        raise RuntimeError(
            f"MAPPING_AUTHORITY_MODE_INVALID:{configured}: expected legacy, shadow, or enforced"
        )
    if mode == "legacy" and deployment_environment() in {"prod", "production"}:
        raise RuntimeError("MAPPING_AUTHORITY_LEGACY_PRODUCTION_PROHIBITED")
    return mode


def deployment_environment() -> str:
    return (
        os.environ.get("DEPLOYMENT_ENVIRONMENT")
        or os.environ.get("ENVIRONMENT")
        or "development"
    ).strip().lower()


def assert_legacy_comparison_allowed(operation: str) -> None:
    if mapping_authority_mode() == "enforced":
        raise RuntimeError(
            f"MAPPING_AUTHORITY_ENFORCED:{operation}: legacy/source-overlay helpers "
            "cannot establish OSB authority or consume a Package V2 release"
        )


def assert_unsafe_legacy_mutation_allowed(operation: str) -> None:
    mode = mapping_authority_mode()
    if mode != "legacy":
        raise RuntimeError(
            f"MAPPING_AUTHORITY_{mode.upper()}:{operation}: direct legacy mutation or "
            "EDC publication is prohibited; shadow is comparison-only and enforced "
            "requires a verified Package V2 release"
        )
    if deployment_environment() in {"prod", "production"}:
        raise RuntimeError(
            f"LEGACY_HELPER_PRODUCTION_PROHIBITED:{operation}"
        )
    if os.environ.get("ALLOW_UNSAFE_LEGACY_EDC_HELPERS") != "1":
        raise RuntimeError(
            f"LEGACY_HELPER_EXPLICIT_OPT_IN_REQUIRED:{operation}: set "
            "ALLOW_UNSAFE_LEGACY_EDC_HELPERS=1 only in a disposable migration environment"
        )