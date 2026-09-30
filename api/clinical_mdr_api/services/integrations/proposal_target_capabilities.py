"""Closed Proposal V2 target taxonomy backed by installed OSB native models.

Derived from the OSB vocabulary registry (osb_vocabulary_registry.py) since 2026-09-21; the dict and the five
frozensets below used to be kept by hand here and asserted against the registry by a test. The names are unchanged so
every consumer (proposal_review, mapping_decision_v1, the tests) reads exactly what it read before.
"""

from clinical_mdr_api.services.integrations.osb_vocabulary_registry import (
    ROWS,
    resource_types_where,
)

TARGET_CAPABILITIES: dict[str, str] = {name: row["osbCapability"] for name, row in ROWS.items()}

# Families with a complete typed existing-route operation/reconciliation plan.
# A valid OSB model name outside this set remains native but non-executable.
NATIVE_EXECUTOR_RESOURCE_TYPES = resource_types_where("osbNativeExecutor")

NATIVE_SELECTION_RESOURCE_TYPES = resource_types_where("osbSelection")

NATIVE_DUAL_MODE_RESOURCE_TYPES = resource_types_where("osbDualMode")

NATIVE_CREATE_REQUEST_RESOURCE_TYPES = resource_types_where("osbCreateRequest")

# Study attributes a reviewer may decline: a signed not_applicable decision on
# one of these is a recorded deferral, not an execution blocker. The study's
# spine (metadata, design, visits, activities, schedule) stays all-or-nothing.
NATIVE_DECLINABLE_RESOURCE_TYPES = resource_types_where("osbDeclinable")


def target_capability(resource_type: str) -> str | None:
    return TARGET_CAPABILITIES.get(resource_type)
