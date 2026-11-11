"""Bounded, rotating service credential for the configured signing gateway."""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SIGNING_RESPONSE_MAX_BYTES = 2_000_000


class _RejectSigningRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def platform_signing_request(endpoint: str, payload: dict, environment: str) -> dict:
    """Authenticated, bounded HTTPS transport for production artifact custody."""
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PLATFORM_SIGNING_ENDPOINT_INVALID")
    authorization = platform_signing_authorization(environment)
    if not authorization:
        raise ValueError("PLATFORM_SIGNING_CREDENTIAL_UNAVAILABLE")
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": authorization,
            "x-platform-producer-service": "osb.package",
        },
    )
    try:
        with urllib.request.build_opener(_RejectSigningRedirects()).open(
            request, timeout=60
        ) as response:
            body = response.read(SIGNING_RESPONSE_MAX_BYTES + 1)
        if len(body) > SIGNING_RESPONSE_MAX_BYTES:
            raise ValueError()
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (urllib.error.URLError, TimeoutError, ValueError, UnicodeError) as error:
        raise ValueError("PLATFORM_SIGNING_REQUEST_FAILED") from error


def platform_signing_authorization(environment: str) -> str | None:
    path = os.getenv("OSB_PLATFORM_SIGNING_TOKEN_FILE", "").strip()
    if not path and environment.strip().lower() not in {"prod", "production"}:
        return None
    try:
        token_file = Path(path)
        if not path or not token_file.is_absolute() or not token_file.is_file():
            raise ValueError()
        with token_file.open("rb") as handle:
            data = handle.read(4097)
        token = data.decode("ascii").strip()
        if len(data) > 4096 or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
            raise ValueError()
        return f"Bearer {token}"
    except OSError, UnicodeError, ValueError:
        raise ValueError("PLATFORM_SIGNING_CREDENTIAL_UNAVAILABLE") from None
