"""Version and readiness checks using the normally imported OSB consumer."""

import copy

import pytest

from clinical_mdr_api.services.integrations import native_package_v2 as package
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError

TENANT = "11111111-1111-4111-8111-111111111111"
STUDY = "22222222-2222-4222-8222-222222222222"


def approval_pair(version="1.1.0"):
    approval = {
        "approval_version": "PreReleaseApprovalV1",
        "approval_id": "33333333-3333-4333-8333-333333333333",
        "human_signature": {"tenant_id": TENANT, "platform_study_id": STUDY},
    }
    if version == "1.1.0":
        approval["readiness_basis"] = {
            "cslStudyId": "synthetic-semantic-study", "ready": True, "lossless": True,
            "counts": {"claims": 1, "unaccountedClaims": 0, "evidenceLessClaims": 0,
                       "unresolvedCritical": 0, "unverifiedMappingDecisions": 0},
            "expectedMappingSetHash": "sha256:" + "1" * 64,
            "fetchedAt": "2026-09-10T12:00:00.000Z",
        }
    return approval, reference(approval, version)


def reference(approval, version):
    return package._artifact_ref({
        "artifactId": approval["approval_id"],
        "artifactVersionId": "44444444-4444-4444-8444-444444444444",
        "kind": "pre-release-approval-v1", "tenantId": TENANT,
        "byteSize": len(package.canonical_json(approval).encode("utf-8")),
        "payloadContract": "accuratrials.cc.PreReleaseApprovalV1", "payloadContractVersion": version,
        "payloadHash": package.canonical_json_hash_ref(approval,
            schema_version=f"PreReleaseApprovalV1@{version}", media_type=package.PRE_RELEASE_APPROVAL_MEDIA_TYPE),
    })


def verify(approval, artifact):
    package._verify_artifact_ref(approval, artifact, kind="pre-release-approval-v1",
        tenant_id=TENANT, platform_study_id=STUDY,
        schema_version=package._approval_schema_version(artifact), media_type=package.PRE_RELEASE_APPROVAL_MEDIA_TYPE)


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0"])
def test_supported_versions_preserve_the_exact_approval(version):
    approval, artifact = approval_pair(version)
    before = copy.deepcopy((approval, artifact))
    verify(approval, artifact)
    assert (approval, artifact) == before


@pytest.mark.parametrize("version", ["1.0.0", "1.1", "1.2.0", None, ["1.1.0"]])
def test_mismatched_or_unknown_descriptor_versions_are_rejected(version):
    approval, artifact = approval_pair()
    artifact["payloadContractVersion"] = version
    with pytest.raises(OsbCandidateSetError) as error:
        verify(approval, artifact)
    assert error.value.code == "OSB_PRE_RELEASE_APPROVAL_CONTRACT_VERSION_INVALID"


@pytest.mark.parametrize("change", ["missing", "not-ready", "not-lossless", "unaccounted", "boolean-count", "date-only", "unknown-field"])
def test_invalid_readiness_cannot_be_authorized_by_rehashing_the_payload(change):
    approval, _ = approval_pair()
    basis = approval["readiness_basis"]
    if change == "missing":
        del approval["readiness_basis"]
    elif change == "not-ready":
        basis["ready"] = False
    elif change == "not-lossless":
        basis["lossless"] = False
    elif change == "unaccounted":
        basis["counts"]["unaccountedClaims"] = 1
    elif change == "boolean-count":
        basis["counts"]["claims"] = True
    elif change == "date-only":
        basis["fetchedAt"] = "2026-09-10"
    else:
        basis["override"] = True
    with pytest.raises(OsbCandidateSetError) as error:
        verify(approval, reference(approval, "1.1.0"))
    assert error.value.code == "OSB_PRE_RELEASE_APPROVAL_READINESS_INVALID"
