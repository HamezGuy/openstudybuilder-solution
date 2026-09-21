"""Canonical JSON compatible with the Intelligence Layer TypeScript contract.

The implementation is the generated platform contract
(clinical_mdr_api.generated.platform_contracts.hash_signing_v1, P1-HASH-001,
verified against the cross-language fixture). This module re-exports it so
that a service hashing through this name and a service hashing through the
contract produce the same bytes; it used to carry a second copy of the same
algorithm. canonical_hash keeps returning the bare hex digest that the
persisted mapping-context, association, proposal and null-adjudication hashes
were computed with. Invalid input raises the contract's PlatformHashError, a
ValueError carrying the code in `.code`.
"""

import hashlib
from typing import Any

from clinical_mdr_api.generated.platform_contracts.hash_signing_v1 import (
    CANONICAL_JSON_VERSION,
    PlatformHashError,
    canonical_json,
)

__all__ = [
    "CANONICAL_JSON_VERSION",
    "PlatformHashError",
    "canonical_hash",
    "canonical_json",
]


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
