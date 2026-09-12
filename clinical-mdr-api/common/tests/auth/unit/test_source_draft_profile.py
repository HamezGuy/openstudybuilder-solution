"""The broker's existing draft capabilities must pass the OSB token profile."""

import pytest

from common.auth.models import AccessTokenClaims, validate_delegated_claims
from common.config import Settings


def broker_claims(capabilities: list[str]) -> AccessTokenClaims:
    return AccessTokenClaims.model_validate(
        {
            "iss": "https://command-center.example.test",
            "sub": "https://idp.example.test|reviewer",
            "aud": ["accuratrial-openstudybuilder"],
            "exp": 2_000_000_000,
            "iat": 1_999_999_900,
            "azp": "accuratrial-command-center",
            "client_id": "accuratrial-command-center",
            "tenant_id": "tenant-1",
            "study_ids": ["Study_1"],
            "roles": ["Study.Read"],
            "type": "access",
            "subject_type": "human",
            "human_subject": "https://idp.example.test|reviewer",
            "service_actor": "service:accuratrial-command-center",
            "act": {
                "sub": "service:accuratrial-command-center",
                "client_id": "accuratrial-command-center",
                "iss": "https://command-center.example.test",
            },
            "actor_chain": [
                {
                    "subject": "https://idp.example.test|reviewer",
                    "type": "human",
                    "issuer": "https://idp.example.test",
                },
                {
                    "subject": "service:accuratrial-command-center",
                    "type": "service",
                    "issuer": "https://command-center.example.test",
                },
            ],
            "idp_iss": "https://idp.example.test",
            "purpose": "interactive-domain-access",
            "capabilities": capabilities,
        }
    )


def validate(claims: AccessTokenClaims) -> None:
    fields = Settings.model_fields
    validate_delegated_claims(
        claims,
        exchanging_clients=set(fields["ENV_OAUTH_EXCHANGING_CLIENTS"].default.split(",")),
        allowed_purposes=set(fields["ENV_OAUTH_ALLOWED_PURPOSES"].default.split(",")),
        allowed_capabilities=set(fields["ENV_OAUTH_ALLOWED_CAPABILITIES"].default.split(",")),
        allowed_roles=set(fields["ENV_OAUTH_ALLOWED_ROLES"].default.split(",")),
    )


def test_broker_osb_profile_accepts_supported_draft_capabilities():
    # Current Command Center admin profile, including the source-draft routes.
    validate(
        broker_claims(
            [
                "study:read", "study:write", "candidate:read", "candidate:generate",
                "candidate:apply", "draft:stage", "draft:read", "package:release",
                "native-identity:bind", "native-identity:inventory",
            ]
        )
    )


@pytest.mark.parametrize("capability", ["draft:stage", "draft:read"])
def test_downscoped_draft_workflow_still_requires_the_delegated_profile(capability):
    claims = broker_claims([capability])
    claims.purpose = "workflow-orchestration"
    validate(claims)
    claims.tenant_id = None
    with pytest.raises(ValueError):
        validate(claims)


@pytest.mark.parametrize("capability", ["*", "draft:*", "configuration:activate", "draft:delete"])
def test_draft_support_does_not_accept_wildcards_or_unrecognized_capabilities(capability):
    with pytest.raises(ValueError, match="Capability"):
        validate(broker_claims(["study:read", capability]))
