"""Bounded, rotating service credential for the configured signing gateway."""

import os
import re
from pathlib import Path


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
    except (OSError, UnicodeError, ValueError):
        raise ValueError("PLATFORM_SIGNING_CREDENTIAL_UNAVAILABLE") from None
