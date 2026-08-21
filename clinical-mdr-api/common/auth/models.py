from dataclasses import dataclass

from authlib.jose import JWTClaims
from pydantic import AliasChoices, BaseModel, Field, field_validator

from common.exceptions import ForbiddenException

AUTHORIZATION_ERROR_CODES = {
    "invalid_request",
    "unauthorized_client",
    "access_denied",
    "unsupported_response_type",
    "invalid_scope",
    "server_error",
    "temporarily_unavailable",
    # OpenID Connect Core 1.0
    "login_required",
    "interaction_required",
    # https://docs.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-auth-code-flow#error-codes-for-authorization-endpoint-errors
    "invalid_resource",
}


class JWTTokenClaims(BaseModel):
    """ID Token claims -- as per OpenID Connect 1.0 specification"""

    # RFC-7519 defines them optional, but mandated by OpenID Connect Core 1.0 for id-tokens (except nbf and jti)
    iss: str
    sub: str
    aud: list[str]
    exp: int
    nbf: int | None = None
    iat: int
    jti: str | None = None
    # OIDC session identifier. Command Center preserves this signed claim when
    # it exchanges a browser token for a fresh child-API audience token.
    sid: str | None = None

    # RFC-8693 #4.2 common for both id and access token
    scp: list[str] | None = None

    @field_validator("aud", "scp", mode="before")
    # pylint: disable=no-self-argument
    def split_str(cls, elm):
        """Splits claim space-separated-string into a list of str elements"""
        if isinstance(elm, str):
            return elm.split()
        return elm


class AccessTokenClaims(JWTTokenClaims):
    """Access token claims"""

    roles: set[str] | None = None
    type: str | None = None

    # OpenID Connect Core 1.0 Standard Claims
    name: str | None = None
    preferred_username: str | None = None
    email: str | None = None
    email_verified: bool | None = None

    # Seen in Active Directory tokens
    username: str | None = None
    oid: str | None = None
    tid: str | None = None
    # Microsoft Entra access tokens commonly expose the unique token id as
    # ``uti`` rather than the RFC 7519 ``jti`` claim.
    uti: str | None = None

    # Command Center authorization scope.  Keep the external camelCase names
    # as validation aliases so the signed JWT values are not silently dropped.
    tenant_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("tenantId", "tenant_id"),
    )
    study_ids: list[int | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("studyIds", "study_ids"),
    )
    organization_ids: list[int | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("organizationIds", "organization_ids"),
    )

    azp: str | None = None
    client_id: str | None = None
    subject_type: str | None = None
    human_subject: str | None = None
    service_actor: str | None = None
    act: dict[str, str] | None = None
    actor_chain: list[dict[str, str]] = Field(default_factory=list)
    idp_iss: str | None = None
    purpose: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    auth_time: int | None = None
    acr: str | None = None
    amr: list[str] = Field(default_factory=list)


@dataclass(init=False)
class User:
    sub: str
    azp: str
    oid: str
    name: str
    username: str
    email: str
    roles: set[str]
    tenant_id: str
    study_ids: set[str]
    subject_type: str
    issuer: str
    human_subject: str
    service_actor: str
    purpose: str
    capabilities: set[str]
    actor_chain: tuple[dict[str, str], ...]
    human_signature_eligible: bool

    def __init__(
        self,
        sub: str,
        azp: str,
        oid: str,
        name: str,
        username: str,
        email: str,
        roles: set[str] | None = None,
        tenant_id: str = "",
        study_ids: set[str] | list[int | str] | None = None,
        subject_type: str = "human",
        issuer: str = "",
        human_subject: str = "",
        service_actor: str = "",
        purpose: str = "",
        capabilities: set[str] | list[str] | None = None,
        actor_chain: list[dict[str, str]] | None = None,
    ) -> None:
        if roles is None:
            roles = set()

        self.sub = sub
        self.azp = azp
        self.oid = oid
        self.name = name
        self.username = username
        self.email = email
        self.roles = roles
        self.tenant_id = tenant_id
        self.study_ids = {str(value) for value in (study_ids or []) if str(value).strip()}
        self.subject_type = subject_type
        self.issuer = issuer
        self.human_subject = human_subject
        self.service_actor = service_actor
        self.purpose = purpose
        self.capabilities = set(capabilities or [])
        self.actor_chain = tuple(actor_chain or [])
        self.human_signature_eligible = subject_type == "human"

    # pylint: disable=invalid-name
    def id(self):
        """Returns the user id

        The issuer-qualified RFC 7519 subject is authoritative for both people
        and services. ``oid`` and ``azp`` remain metadata, never identity joins.
        """
        return self.sub

    def has_role(self, role: str) -> bool:
        """
        Checks if the user has the specified role.

        Args:
            role (str): The role to check.

        Returns:
            bool: True if the user has the specified role, False otherwise.
        """
        return role in self.roles

    def has_roles(self, *roles: str, has_all: bool = True) -> bool:
        """
        Checks if the user has any or all of the specified roles.

        Args:
            *roles (str): The roles to check.
            has_all (bool): Optional. If True, checks if the user has all of the specified roles.
            If False, checks if the user has any of the specified roles.
            Default is True.

        Returns:
            bool: True if the user has all specified roles (if `has_all` is True)
            or at least one of the specified roles (if `has_all` is False), False otherwise.
        """
        if has_all:
            return all(self.has_role(role) for role in roles)

        return any(self.has_role(role) for role in roles)

    def hasnt_role(self, role: str) -> bool:
        """
        Checks if the user doesn't have the specified role.

        Args:
            role (str): The role to check.

        Returns:
            bool: True if the user doesn't have the specified role, False otherwise.
        """
        return not self.has_role(role)

    def hasnt_roles(self, *roles: str, hasnt_any: bool = True) -> bool:
        """
        Checks if the user doesn't have any or doesn't have at least one of the specified roles.

        Args:
            *roles (str): The roles to check.
            hasnt_any (bool): Optional. If True, checks if the user doesn't have any of the specified roles.
            If False, checks if the user doesn't have at least one of the specified roles.
            Default is True.

        Returns:
            bool: True if the user doesn't have any of the specified roles (if `hasnt_any` is True)
            or doesn't have at least one of the specified roles (if `hasnt_any` is False), False otherwise.

        """
        if hasnt_any:
            return all(self.hasnt_role(role) for role in roles)

        return any(self.hasnt_role(role) for role in roles)

    def has_only_role(self, role: str) -> bool:
        """
        Checks if the user has only the specified role.

        Args:
            role (str): The role to check.

        Returns:
            bool: True if the user has only the specified role, False otherwise.

        """
        return {role} == self.roles

    def has_only_roles(self, *roles: str) -> bool:
        """
        Checks if the user has only the specified roles.

        Args:
            *roles (str): The roles to check.

        Returns:
            bool: True if the user has only the specified roles, False otherwise.

        """
        return set(roles) == self.roles

    def authorize(self, *roles: str, has_all: bool = False) -> bool:
        """
        Authorizes the user based on the specified roles.

        Args:
            *roles (str): The roles required for authorization.
            has_all (bool): Optional. If True, requires the user to have all specified roles for authorization.
            If False, requires the user to have at least one of the specified roles.
            Default is False.

        Returns:
            bool: True if the user is authorized based on the specified roles, False otherwise.

        Raises:
            ForbiddenException: If the user is not authorized, raises a ForbiddenException with a message indicating which roles are required.

        """
        if self.has_roles(*roles, has_all=has_all):
            return True

        raise ForbiddenException(
            msg=(
                f"At least one of the following roles is required: {list(roles)}"
                if not has_all
                else f"Following roles are required: {list(roles)}"
            )
        )


class Auth:
    user: User
    jwt_claims: JWTClaims
    access_token_claims: AccessTokenClaims
    authentication_verified: bool

    def __init__(
        self,
        jwt_claims: JWTClaims,
        access_token_claims: AccessTokenClaims,
        *,
        authentication_verified: bool,
    ):
        self.user = User(
            sub=access_token_claims.sub,
            azp=access_token_claims.azp or "",
            oid=access_token_claims.oid or "",
            name=access_token_claims.name or "",
            username=(
                access_token_claims.username
                or access_token_claims.preferred_username
                or ""
            ),
            email=(
                access_token_claims.email
                or access_token_claims.preferred_username
                or ""
            ),
            roles=access_token_claims.roles,
            tenant_id=access_token_claims.tenant_id or "",
            study_ids=access_token_claims.study_ids,
            subject_type=access_token_claims.subject_type or "human",
            issuer=access_token_claims.iss,
            human_subject=access_token_claims.human_subject or "",
            service_actor=access_token_claims.service_actor or "",
            purpose=access_token_claims.purpose or "",
            capabilities=access_token_claims.capabilities,
            actor_chain=access_token_claims.actor_chain,
        )
        self.jwt_claims = jwt_claims
        self.access_token_claims = access_token_claims
        self.authentication_verified = authentication_verified


def validate_delegated_claims(
    claims: AccessTokenClaims,
    *,
    exchanging_clients: set[str],
    allowed_purposes: set[str],
    allowed_capabilities: set[str],
    allowed_roles: set[str],
    max_ttl_seconds: int = 300,
) -> None:
    """Validate mandatory OBO/downscope claims after JWT crypto validation."""

    if claims.type != "access":
        raise ValueError("Token is not an access token")
    if claims.exp <= claims.iat or claims.exp - claims.iat > max_ttl_seconds:
        raise ValueError("Access token violates the five-minute lifetime bound")
    client_id = claims.azp or claims.client_id or ""
    if not client_id or claims.azp != claims.client_id or client_id not in exchanging_clients:
        raise ValueError("Exchanging client is absent, inconsistent, or not allowlisted")
    if not claims.tenant_id:
        raise ValueError("tenant_id is required")
    if not claims.purpose or claims.purpose not in allowed_purposes:
        raise ValueError("Purpose is absent or outside the OSB profile")
    if not claims.capabilities or any(
        value not in allowed_capabilities or "*" in value or len(value) > 256
        for value in claims.capabilities
    ):
        raise ValueError("Capability is absent, wildcarded, or outside the OSB profile")
    if any("*" in str(value) or len(str(value)) > 256 for value in claims.study_ids):
        raise ValueError("Study scope contains a wildcard or oversized value")
    token_roles = set(claims.roles or set())
    if not token_roles or any(
        role not in allowed_roles or "*" in role or len(role) > 256
        for role in token_roles
    ):
        raise ValueError("Role is absent, wildcarded, or outside the OSB profile")
    if not claims.actor_chain or any(
        not actor.get("subject")
        or not actor.get("issuer")
        or actor.get("type") not in {"human", "service"}
        for actor in claims.actor_chain
    ):
        raise ValueError("Actor chain contains an invalid actor")

    if claims.subject_type == "human":
        first = claims.actor_chain[0] if claims.actor_chain else {}
        last = claims.actor_chain[-1] if claims.actor_chain else {}
        if (
            claims.human_subject != claims.sub
            or not claims.service_actor
            or not claims.act
            or claims.act.get("sub") != claims.service_actor
            or claims.act.get("client_id") != client_id
            or first.get("subject") != claims.sub
            or first.get("type") != "human"
            or last.get("subject") != claims.service_actor
            or last.get("type") != "service"
            or not claims.idp_iss
        ):
            raise ValueError("Delegated human actor chain is missing or inconsistent")
        return

    if claims.subject_type == "service":
        actor = claims.actor_chain[0] if len(claims.actor_chain) == 1 else {}
        if (
            claims.sub != f"service:{client_id}"
            or claims.service_actor != claims.sub
            or claims.human_subject
            or claims.auth_time is not None
            or claims.acr
            or claims.amr
            or actor.get("subject") != claims.sub
            or actor.get("type") != "service"
        ):
            raise ValueError("Service token contains a forged human or actor context")
        return

    raise ValueError("subject_type must be human or service")
