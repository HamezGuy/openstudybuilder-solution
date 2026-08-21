from typing import Any

import pytest
from authlib.jose.rfc7519.claims import JWTClaims

from common.auth.dependencies import dummy_user
from common.auth.models import AccessTokenClaims, Auth, User, validate_delegated_claims
from common.exceptions import ForbiddenException

user_obj = dummy_user()


def test_user_model_constructor():
    data: dict[str, Any] = {
        "sub": "xyz",
        "azp": "unknown-user",
        "oid": "unknown-user",
        "name": "John Doe",
        "username": "john@example.com",
        "email": "john@example.com",
        "roles": {"Study.Read", "Library.Write", "a"},
    }
    _user = User(
        sub=data["sub"],
        azp=data["azp"],
        name=data["name"],
        username=data["username"],
        email=data["email"],
        oid=data["oid"],
        roles=data["roles"],
    )

    assert _user.sub == data["sub"]
    assert _user.name == data["name"]
    assert _user.username == data["username"]
    assert _user.email == data["email"]
    assert _user.oid == data["oid"]
    assert _user.roles == data["roles"]


def test_auth_projection_preserves_distinct_username_and_email_claims():
    claims = AccessTokenClaims(
        iss="https://command-center.example.test",
        sub="edc:42",
        aud=["accuratrial-openstudybuilder"],
        exp=2_000_000_000,
        iat=1_999_999_000,
        sid="authority-session-id",
        uti="entra-token-identity",
        oid="edc:42",
        azp="accuratrial-command-center",
        username="jdoe",
        preferred_username="jdoe",
        email="jdoe@example.com",
        name="Jane Doe",
        roles={"Study.Read"},
    )
    auth = Auth(
        jwt_claims=JWTClaims(dict(claims), {}),
        access_token_claims=claims,
        authentication_verified=True,
    )

    assert auth.user.id() == "edc:42"
    assert auth.user.username == "jdoe"
    assert auth.user.email == "jdoe@example.com"
    assert claims.uti == "entra-token-identity"
    assert claims.sid == "authority-session-id"
    assert auth.authentication_verified is True


def delegated_claims(**overrides):
    payload = {
        "iss": "https://command-center.example.test",
        "sub": "https://idp.example.test|human-42",
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
        "human_subject": "https://idp.example.test|human-42",
        "service_actor": "service:accuratrial-command-center",
        "act": {
            "sub": "service:accuratrial-command-center",
            "client_id": "accuratrial-command-center",
            "iss": "https://command-center.example.test",
        },
        "actor_chain": [
            {"subject": "https://idp.example.test|human-42", "type": "human", "issuer": "https://idp.example.test"},
            {"subject": "service:accuratrial-command-center", "type": "service", "issuer": "https://command-center.example.test"},
        ],
        "idp_iss": "https://idp.example.test",
        "purpose": "interactive-domain-access",
        "capabilities": ["study:read", "candidate:read"],
    }
    payload.update(overrides)
    return AccessTokenClaims.model_validate(payload)


def test_delegated_profile_preserves_human_and_rejects_upscope():
    claims = delegated_claims()
    validate_delegated_claims(
        claims,
        exchanging_clients={"accuratrial-command-center"},
        allowed_purposes={"interactive-domain-access"},
        allowed_capabilities={"study:read", "candidate:read"},
        allowed_roles={"Study.Read"},
    )
    auth = Auth(JWTClaims(dict(claims), {}), claims, authentication_verified=True)
    assert auth.user.id() == "https://idp.example.test|human-42"
    assert auth.user.human_signature_eligible is True
    with pytest.raises(ValueError, match="Capability"):
        validate_delegated_claims(
            delegated_claims(capabilities=["configuration:activate"]),
            exchanging_clients={"accuratrial-command-center"},
            allowed_purposes={"interactive-domain-access"},
            allowed_capabilities={"study:read"},
            allowed_roles={"Study.Read"},
        )


def test_service_profile_cannot_carry_human_reauthentication():
    claims = delegated_claims(
        sub="service:accuratrial-command-center",
        subject_type="service",
        human_subject=None,
        service_actor="service:accuratrial-command-center",
        act=None,
        actor_chain=[{"subject": "service:accuratrial-command-center", "type": "service", "issuer": "https://command-center.example.test"}],
        idp_iss=None,
        auth_time=1_999_999_950,
    )
    with pytest.raises(ValueError, match="forged human"):
        validate_delegated_claims(
            claims,
            exchanging_clients={"accuratrial-command-center"},
            allowed_purposes={"interactive-domain-access"},
            allowed_capabilities={"study:read", "candidate:read"},
            allowed_roles={"Study.Read"},
        )


def test_access_token_scope_claims_accept_command_center_camel_case_names():
    claims = AccessTokenClaims.model_validate(
        {
            "iss": "https://command-center.example.test",
            "sub": "worker-client",
            "aud": ["accuratrial-openstudybuilder"],
            "exp": 2_000_000_000,
            "iat": 1_999_999_000,
            "azp": "worker-client",
            "tenantId": "tenant-1",
            "studyIds": ["Study_1", 42],
            "organizationIds": ["org-1"],
        }
    )

    assert claims.tenant_id == "tenant-1"
    assert claims.study_ids == ["Study_1", 42]
    assert claims.organization_ids == ["org-1"]


def test_has_role():
    assert user_obj.has_role("Study.Write") is True
    assert (
        dummy_user(roles={"Study.Read", "Library.Read"}).has_role("Study.Write")
        is False
    )


@pytest.mark.parametrize(
    "roles, has_all, expected_rs",
    [
        pytest.param(
            ("Study.Read", "Study.Write", "Library.Write", "Library.Read"), True, True
        ),
        pytest.param(("Study.Read", "Study.Write"), True, True),
        pytest.param(
            ("Study.Read", "Study.Write", "Library.Write", "Library.Read"), False, True
        ),
        pytest.param(("Study.Read", "Study.Write"), False, True),
    ],
)
def test_has_roles(roles, has_all, expected_rs):
    assert user_obj.has_roles(*roles, has_all=has_all) is expected_rs


def test_has_roles_negative():
    _user = dummy_user(roles={"Study.Read", "Study.Write", "Library.Write"})
    assert _user.has_roles("Library.Read", has_all=True) is False
    assert (
        _user.has_roles(
            "Study.Read", "Study.Write", "Library.Write", "Library.Read", has_all=True
        )
        is False
    )

    assert _user.has_roles("Library.Read", has_all=False) is False
    assert (
        _user.has_roles(
            "Study.Read", "Study.Write", "Library.Write", "Library.Read", has_all=False
        )
        is True
    )


def test_hasnt_role():
    assert user_obj.hasnt_role("Study.Read") is False
    assert (
        dummy_user({"Study.Read", "Study.Write", "Library.Write"}).hasnt_role(
            "Library.Read"
        )
        is True
    )


@pytest.mark.parametrize(
    "roles, hasnt_any, expected_rs",
    [
        pytest.param(
            ("Study.Read", "Study.Write", "Library.Write", "Library.Read"), True, False
        ),
        pytest.param(("Study.Read", "Study.Write"), True, False),
        pytest.param(
            ("Study.Read", "Study.Write", "Library.Write", "Library.Read"), False, False
        ),
        pytest.param(("Study.Read", "Study.Write"), False, False),
    ],
)
def test_hasnt_roles(roles, hasnt_any, expected_rs):
    assert user_obj.hasnt_roles(*roles, hasnt_any=hasnt_any) is expected_rs


def test_hasnt_roles_negative():
    _user = dummy_user({"Study.Read", "Study.Write", "Library.Write"})
    assert _user.hasnt_roles("Library.Read", hasnt_any=True) is True
    assert _user.hasnt_roles("Library.Read", "Study.Read", hasnt_any=True) is False
    assert (
        _user.hasnt_roles(
            "Study.Read", "Study.Write", "Library.Write", "Library.Read", hasnt_any=True
        )
        is False
    )

    assert _user.hasnt_roles("Library.Read", hasnt_any=False) is True
    assert _user.hasnt_roles("Library.Read", "Study.Read", hasnt_any=False) is True
    assert (
        _user.hasnt_roles(
            "Study.Read",
            "Study.Write",
            "Library.Write",
            "Library.Read",
            hasnt_any=False,
        )
        is True
    )


def test_has_only_role():
    assert dummy_user(roles={"Study.Read"}).has_only_role("Study.Read") is True
    assert user_obj.has_only_role("Study.Read") is False


@pytest.mark.parametrize(
    "roles, expected_rs",
    [
        pytest.param(
            (
                "Admin.Read",
                "Admin.Write",
                "Study.Read",
                "Study.Write",
                "Library.Write",
                "Library.Read",
            ),
            True,
        ),
        pytest.param(("Study.Read", "Study.Write"), False),
    ],
)
def test_has_only_roles(roles, expected_rs):
    assert user_obj.has_only_roles(*roles) is expected_rs


@pytest.mark.parametrize(
    "roles, has_all, expected_rs",
    [
        pytest.param(
            ("Study.Read", "Study.Write", "Library.Write", "Library.Read"), True, True
        ),
        pytest.param(("Study.Read", "Study.Write"), True, True),
    ],
)
def test_authorize(roles, has_all, expected_rs):
    assert user_obj.authorize(*roles, has_all=has_all) is expected_rs


def test_authorize_negative():
    _user = dummy_user({"Study.Read", "Study.Write"})
    assert (
        _user.hasnt_roles(
            "Study.Read",
            "Study.Write",
            "Library.Write",
            "Library.Read",
            hasnt_any=False,
        )
        is True
    )
    assert _user.hasnt_roles("Study.Read", "Library.Read", hasnt_any=False) is True

    with pytest.raises(ForbiddenException) as exc:
        _user.authorize("Library.Read", "Library.Write", has_all=True)
    assert (
        exc.value.msg
        == "Following roles are required: ['Library.Read', 'Library.Write']"
    )

    with pytest.raises(ForbiddenException) as exc:
        _user.authorize("Library.Read", "Library.Write", has_all=False)
    assert (
        exc.value.msg
        == "At least one of the following roles is required: ['Library.Read', 'Library.Write']"
    )
