"""Read-only admission of an operator-verified, existing review workspace."""

import re
from typing import Any, Callable
from uuid import UUID

from clinical_mdr_api.generated.platform_contracts.native_identity_command_processor_v1 import NativeIdentityCommandError


def is_review_workspace_claim(intent: Any) -> bool:
    if not isinstance(intent, dict) or not isinstance(intent.get("requestedInitialState"), dict):
        return False
    state = intent["requestedInitialState"]
    try:
        migration_id = state["reviewWorkspaceMigrationId"]
        if not isinstance(migration_id, str) or str(UUID(migration_id)) != migration_id.lower():
            return False
    except (ValueError, TypeError, KeyError):
        return False
    return (
        intent.get("expectedAbsence") is False
        and intent.get("targetSystem") == "osb"
        and intent.get("namespace") == "accuratrials-osb"
        and intent.get("objectType") == "study-draft-root"
        and state.get("operation") == "claim_existing"
        and state.get("reviewOnly") is True
        and isinstance(state.get("nativeIdentity"), str)
        and bool(state["nativeIdentity"].strip())
        and isinstance(state.get("sourceManifestHash"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", state["sourceManifestHash"]) is not None
    )


def assert_review_workspace_claim(
    intent: dict[str, Any], tenant_id: str, platform_study_id: str, query: Callable
) -> None:
    if not is_review_workspace_claim(intent) or intent.get("tenantId") != tenant_id \
            or intent.get("platformStudyId") != platform_study_id:
        raise NativeIdentityCommandError(
            "REVIEW_WORKSPACE_MIGRATION_INTENT_INVALID",
            "A review-only claim of the exact migrated root and manifest is required.", 403,
        )
    state = intent["requestedInitialState"]
    rows, _ = query(
        """MATCH (migration:ReviewWorkspaceMigrationV1 {
             migration_id:$migration_id, tenant_id:$tenant_id, platform_study_id:$platform_study_id,
             native_study_id:$native_study_id, source_manifest_hash:$source_manifest_hash,
             conservation_verified:true, clinical_approval:false, release_eligible:false})
           WHERE migration.source_counts_json = migration.destination_counts_json
             AND migration.source_counts_json IS NOT NULL
             AND migration.recovery_ref IS NOT NULL AND migration.reason IS NOT NULL
           RETURN migration.migration_id""",
        {
            "migration_id": state["reviewWorkspaceMigrationId"], "tenant_id": tenant_id,
            "platform_study_id": platform_study_id, "native_study_id": state["nativeIdentity"],
            "source_manifest_hash": state["sourceManifestHash"],
        },
    )
    if len(rows) != 1:
        raise NativeIdentityCommandError(
            "REVIEW_WORKSPACE_MIGRATION_SCOPE_DENIED",
            "Verified migration custody must match the exact destination and source manifest.", 403,
        )
