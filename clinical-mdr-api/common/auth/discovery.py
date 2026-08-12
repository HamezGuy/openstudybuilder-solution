import logging

from common.auth.dependencies import oauth_scheme, oidc_client

log = logging.getLogger(__name__)


async def reconfigure_with_openid_discovery():
    log.info("Reconfiguring Swagger UI settings with OpenID Connect discovery.")

    try:
        metadata = await oidc_client.load_server_metadata()
    except Exception as exc:  # discovery is retried by JWK validation on requests
        # This function configures Swagger URLs only. Crashing the entire API when
        # the identity provider starts a few seconds later creates a restart loop.
        # Protected requests remain fail-closed in JWKService.validate_jwt().
        log.warning(
            "OpenID discovery unavailable during startup; Swagger OAuth URLs will "
            "remain unset until restart: %s",
            exc,
        )
        return

    if authorization_endpoint := metadata.get("authorization_endpoint"):
        oauth_scheme.model.flows.authorizationCode.authorizationUrl = (
            authorization_endpoint
        )

    if token_endpoint := metadata.get("token_endpoint"):
        oauth_scheme.model.flows.authorizationCode.tokenUrl = token_endpoint
