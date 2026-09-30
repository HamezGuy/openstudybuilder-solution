"""Exact request versions supported by the native intake and custody readers."""

ACCEPTED_REQUEST_CONTRACT_MINOR_VERSIONS = ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0")
ACCEPTED_REQUEST_CONTRACT_VERSIONS = tuple(
    f"OsbCandidateRequestV1@{version}" for version in ACCEPTED_REQUEST_CONTRACT_MINOR_VERSIONS
)
METADATA_REQUEST_CONTRACT_VERSIONS = ("OsbCandidateRequestV1@1.2.0", "OsbCandidateRequestV1@1.3.0", "OsbCandidateRequestV1@1.4.0")
SOURCE_CONTEXT_REQUEST_CONTRACT_VERSIONS = ("OsbCandidateRequestV1@1.3.0", "OsbCandidateRequestV1@1.4.0")
SELECTED_CAPTURE_REQUEST_CONTRACT_VERSIONS = ("OsbCandidateRequestV1@1.3.0", "OsbCandidateRequestV1@1.4.0")
# 1.4.0 (plan item A5, 2026-09-21): a typed intent may carry the CSL entity the fact materialised (cslEntity), which
# the decision executor stores on the PlatformManagedStudyConcept side-car.
CSL_ENTITY_REQUEST_CONTRACT_VERSIONS = ("OsbCandidateRequestV1@1.4.0",)
