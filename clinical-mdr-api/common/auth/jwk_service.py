import base64
import json
import logging
import time
import uuid
from typing import Any, Mapping, MutableMapping

from authlib.integrations.base_client import OAuth2Mixin
from authlib.jose import JsonWebKey, JWTClaims, Key, KeySet, jwt
from httpx import AsyncClient

from common.exceptions import NotAuthenticatedException

log = logging.getLogger(__name__)


class JWKService(KeySet):
    """JWK store and JWT validator, relies on AsyncRemoteApp for metadata and HTTP client"""

    keys: MutableMapping[str, Key]
    cooldown = 120

    def __init__(
        self,
        oauth_client: OAuth2Mixin,
        audience: str | list[str],
        leeway_seconds: int | float = 15,
        cache_ttl_seconds: int = 300,
        revoked_kids: set[str] | None = None,
    ):
        self.oauth_client = oauth_client
        self.audience = audience
        self._keys_updated = 0.0
        self._http_client = AsyncClient()
        self.jwks_uri = None
        self.leeway = leeway_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.revoked_kids = revoked_kids or set()
        self._last_unknown_key_refresh = 0.0
        self.claims_options: dict[str, Any] = {}
        super().__init__({})

    async def init(self) -> None:
        """Async initializer, as we depend on async oauth_client"""

        if self._keys_updated:
            # Already initialized
            return

        # authlib.integrations.base_client.async_app.AsyncRemoteApp.load_server_metadata() caches forever
        metadata = await self.oauth_client.load_server_metadata()

        self.jwks_uri = metadata.get("jwks_uri")
        if not self.jwks_uri:
            raise RuntimeError("OpenID Provider Info doesn't have jwks_uri")

        iss = metadata.get("issuer")
        if iss:
            if isinstance(iss, str):
                iss = [iss]
            self.claims_options["iss"] = {"values": iss}

        if aud := self.audience:
            if isinstance(aud, str):
                aud = [aud]
            self.claims_options["aud"] = {"values": aud}
        else:
            raise RuntimeError(
                "OAuth application id not configured, set in OAUTH_API_APP_ID environment variable"
            )

        log.info(
            "JWKService: required claims of access tokens are %s",
            repr(self.claims_options),
        )

        await self.fetch_jwk_set()

    def find_by_kid(self, kid: str) -> Key:
        """KeySet interface for jwt.decode()"""
        if not kid or kid in self.revoked_kids:
            raise UnknownKeyError(f"Signing key is absent or revoked: {kid!s}")
        try:
            return self.keys[kid]
        except KeyError as exc:
            # KeySet interface
            raise UnknownKeyError(f"Unknown key id: {kid!s}") from exc

    async def fetch_jwk_set(self) -> Mapping[str, Key]:
        log.debug("Fetching JWKs: %s", self.jwks_uri)

        if not self.jwks_uri:
            raise RuntimeError("JWKs URI is not set")

        payload = await self._fetch_json(self.jwks_uri)
        if not isinstance(payload, dict):
            raise RuntimeError(f"JWKS data is not dict, but {type(payload)}")

        keys = payload.get("keys", {})
        keys_dict = self.update_keys(keys)

        return keys_dict

    def update_keys(self, keys: list[Mapping[str, Any]]) -> dict[str, Key]:
        keys_dict = {}

        for key in keys:
            if not isinstance(key, dict) or "kid" not in key:
                log.debug("Invalid key: %s", repr(key))
            keys_dict[key["kid"]] = JsonWebKey.import_key(key)

        self.keys = keys_dict
        self._keys_updated = time.time()

        return keys_dict

    def generate_key(
        self, kty="RSA", crv_or_size=2048, options=None, is_private=True
    ) -> Key:
        """Generate a Key, useful for testing with self-signed tokens"""

        if options is None:
            options = {}
        if "kid" not in options:
            options["kid"] = uuid.uuid4().hex

        key = JsonWebKey.generate_key(
            kty=kty, crv_or_size=crv_or_size, options=options, is_private=is_private
        )

        self.keys[key.kid] = key
        self._keys_updated = time.time()

        return key

    async def refresh_jwk_set(self, *, unknown_key: bool = False) -> bool:
        """Update keys only if cooldown seconds has elapsed"""
        now = time.time()
        if unknown_key:
            if self._last_unknown_key_refresh + self.cooldown >= now:
                return False
            self._last_unknown_key_refresh = now
            await self.fetch_jwk_set()
            return True
        if self._keys_updated + self.cache_ttl_seconds < now:
            await self.fetch_jwk_set()
            return True

        return False

    async def _fetch_json(self, url: str):
        # pylint: disable=protected-access
        resp = await self._http_client.request("GET", url)
        resp.raise_for_status()
        return resp.json()

    async def validate_jwt(self, token: str | bytes) -> JWTClaims:
        """Validates JWT, fetching JWKs, checking signature and iss & aud claims (if init), then returns claims."""
        await self.init()
        await self.refresh_jwk_set()

        try:
            encoded_header = (token.decode() if isinstance(token, bytes) else token).split(".", 1)[0]
            padding = "=" * (-len(encoded_header) % 4)
            token_header = json.loads(base64.urlsafe_b64decode(encoded_header + padding))
            token_kid = token_header.get("kid")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise NotAuthenticatedException("JWT protected header is invalid") from exc
        if not token_kid or token_kid in self.revoked_kids:
            raise NotAuthenticatedException("JWT signing key is absent or revoked")

        try:
            claims = jwt.decode(
                token,
                key=self,
                claims_options=self.claims_options,
            )

        except UnknownKeyError as exc:
            # Re-fetch the list of keys if key-id was not found in local key-set
            if not await self.refresh_jwk_set(unknown_key=True):
                # Keys were not re-fetched within the cooldown period, so no need to retry decoding
                raise NotAuthenticatedException(exc.args[0]) from exc

            if token_kid not in self.keys:
                raise NotAuthenticatedException(f"Unknown key id: {token_kid!s}") from exc

            # retry decoding of JWT
            claims = jwt.decode(token, key=self, claims_options=self.claims_options)

        claims.validate(leeway=self.leeway)

        return claims


class UnknownKeyError(ValueError):
    pass
