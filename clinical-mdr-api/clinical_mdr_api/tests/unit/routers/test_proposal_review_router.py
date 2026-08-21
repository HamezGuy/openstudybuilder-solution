from types import SimpleNamespace

import pytest

from clinical_mdr_api.routers.integrations import proposal_review


@pytest.mark.parametrize(
    ("sid", "jti", "uti", "expected"),
    [
        ("session-id", "rfc-token-id", "entra-token-id", "session-id"),
        (None, "rfc-token-id", "entra-token-id", "rfc-token-id"),
        (None, None, "entra-token-id", "entra-token-id"),
    ],
)
def test_review_principal_uses_verified_session_or_token_identity(
    monkeypatch, sid, jti, uti, expected
):
    claims = SimpleNamespace(
        oid="edc:42",
        sid=sid,
        jti=jti,
        uti=uti,
        tenant_id="tenant-1",
        study_ids=["study-1", "Study_1"],
        organization_ids=["org-1"],
        roles={"Study.Read", "Study.Write"},
    )
    authenticated = SimpleNamespace(
        user=SimpleNamespace(id=lambda: "edc:42"),
        access_token_claims=claims,
        authentication_verified=True,
    )
    monkeypatch.setattr(proposal_review, "auth", lambda: authenticated)
    monkeypatch.setattr(
        proposal_review,
        "settings",
        SimpleNamespace(oauth_enabled=True, deployment_environment="production"),
    )

    principal = proposal_review._review_principal()

    assert principal.actor_id == "edc:42"
    assert principal.human_user_id == "edc:42"
    assert principal.token_id == expected
    assert principal.tenant_id == "tenant-1"
    assert principal.scoped_study_ids == frozenset({"study-1", "Study_1"})
    assert principal.organization_ids == frozenset({"org-1"})
    assert principal.authentication_verified is True
    assert principal.development_access is False
    principal.assert_can_sign(expected)


def test_review_principal_does_not_treat_auth_disabled_as_verified_signature(
    monkeypatch,
):
    claims = SimpleNamespace(
        oid="unknown-user",
        sid=None,
        jti=None,
        uti=None,
        tenant_id=None,
        study_ids=[],
        organization_ids=[],
        roles={"Study.Read", "Study.Write"},
    )
    authenticated = SimpleNamespace(
        user=SimpleNamespace(id=lambda: "unknown-user"),
        access_token_claims=claims,
        authentication_verified=False,
    )
    monkeypatch.setattr(proposal_review, "auth", lambda: authenticated)
    monkeypatch.setattr(
        proposal_review,
        "settings",
        SimpleNamespace(oauth_enabled=False, deployment_environment="development"),
    )

    principal = proposal_review._review_principal()

    assert principal.authentication_verified is False
    assert principal.development_access is True
    principal.assert_proposal_access("local-tenant", "local-study", "Study.Read")
    with pytest.raises(ValueError, match="AUTHENTICATION_NOT_VERIFIED"):
        principal.assert_can_sign("local-signature")
