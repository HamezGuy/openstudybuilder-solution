"""Compatibility HTTP adapter for the IL-owned, non-production OSB ledger.

The historical class name is retained; no IL database connection is opened.
ECRF_API_URL is a trusted HTTP(S) origin. ECRF_TENANT_ID is native IL scope.
Use either ECRF_API_TOKEN or ECRF_API_TOKEN_FILE (reread for each request).
A trusted external supplier refreshes scoped tokens; this client never mints them.
ECRF_ACCESS_GATE_TOKEN supplies the optional IL X-Access-Gate header.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import aiohttp

# 1.18 accompanies the coordinated native measured-unit preflight correction.
# The HTTP transport itself does not change native mapping or replay semantics.
IMPORTER_VERSION = "360i-importer/1.18"
_PAYLOAD_LIMIT = 256 * 1024 * 1024
_RESPONSE_LIMIT = 6 * _PAYLOAD_LIMIT + 1024 * 1024
_HASH = re.compile(r"^[0-9a-f]{64}$")


class EcrfLedgerError(RuntimeError):
    """Safe owner refusal or unverified outcome, retaining recovery identity."""

    def __init__(self, code, *, status=None, import_id=None):
        self.code, self.status, self.import_id = code, status, import_id
        suffix = f" (import_id={import_id})" if import_id else ""
        super().__init__(f"{code}{suffix}")


def _text(value, name):
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(
            f"{name} must be a non-empty trimmed string without control characters"
        )
    return value


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise EcrfLedgerError("LEGACY_OSB_RESPONSE_HASH_INVALID")
    return value


def _date(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone absent")
        return result
    except (AttributeError, TypeError, ValueError) as error:
        raise EcrfLedgerError("LEGACY_OSB_RESPONSE_TIMESTAMP_INVALID") from error


def _same_json(left, right):
    """JSON booleans are not numbers (Python otherwise considers False == 0)."""
    pending = [(left, right)]
    while pending:
        first, second = pending.pop()
        if isinstance(first, bool) != isinstance(second, bool):
            return False
        if isinstance(first, dict):
            if not isinstance(second, dict) or first.keys() != second.keys():
                return False
            pending.extend((value, second[key]) for key, value in first.items())
        elif isinstance(first, list):
            if not isinstance(second, list) or len(first) != len(second):
                return False
            pending.extend(zip(first, second))
        elif first != second:
            return False
    return True


class EcrfPlatformDb:
    """Scoped HTTP session preserving the former helper's return shapes."""

    def __init__(
        self, dsn=None, tenant_id=None, log=None, *, api_url=None, session_factory=None
    ):
        if dsn is not None:
            raise ValueError("ECRF_PG_DSN transport retired; configure ECRF_API_URL")
        origin = _text(api_url or os.environ.get("ECRF_API_URL"), "ECRF_API_URL")
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or "\\" in origin
        ):
            raise ValueError("ECRF_API_URL must be a trusted HTTP(S) origin")
        _ = parsed.port  # Reject malformed or out-of-range port declarations.
        self.origin = origin.rstrip("/")
        self.tenant_id = _text(
            tenant_id or os.environ.get("ECRF_TENANT_ID"), "ECRF_TENANT_ID"
        )
        self.log = log
        self._session_factory = session_factory or aiohttp.ClientSession
        self._closed = False
        self._platform_scopes = {}

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    @staticmethod
    def _headers():
        token = os.environ.get("ECRF_API_TOKEN", "").strip()
        token_file = os.environ.get("ECRF_API_TOKEN_FILE", "").strip()
        if bool(token) == bool(token_file):
            raise ValueError(
                "Configure exactly one ECRF_API_TOKEN or ECRF_API_TOKEN_FILE"
            )
        if token_file:
            with Path(token_file).open("rb") as handle:
                raw = handle.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ValueError("ECRF_API_TOKEN_FILE exceeds 64 KiB")
            token = raw.decode("utf-8", errors="strict").strip()
        headers = {
            "Authorization": f"Bearer {_text(token, 'ECRF_API_TOKEN')}",
            "Accept": "application/json",
        }
        gate = os.environ.get("ECRF_ACCESS_GATE_TOKEN")
        if gate:
            headers["X-Access-Gate"] = _text(gate, "ECRF_ACCESS_GATE_TOKEN")
        return headers

    def _request(self, study_id, suffix, *, body=None, import_id=None):
        study_id = _text(study_id, "study_id")
        url = f"{self.origin}/api/pipeline/study/{quote(study_id, safe='')}/legacy-osb{suffix}"
        encoded = (
            json.dumps(
                body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
            if body is not None
            else None
        )
        if encoded is not None and len(encoded) > _PAYLOAD_LIMIT:
            raise EcrfLedgerError("LEGACY_OSB_REQUEST_TOO_LARGE", import_id=import_id)
        if self._closed:
            raise EcrfLedgerError("LEGACY_OSB_CLIENT_CLOSED", import_id=import_id)
        # All current importer callers are synchronous. The event loop and HTTP
        # session are owned by this call and are always closed, including timeout.
        # No worker thread survives a cancellation or unknown write outcome.
        return asyncio.run(self._request_async(study_id, url, encoded, import_id))

    async def _request_async(self, study_id, url, encoded, import_id):
        timeout = aiohttp.ClientTimeout(total=250, connect=10, sock_read=240)
        async with self._session_factory(trust_env=False, timeout=timeout) as session:
            # One retry of identical bytes/UUID. A refreshed token may be supplied.
            for attempt in range(2):
                response_status = None
                try:
                    headers = self._headers()
                    if encoded is not None:
                        headers["Content-Type"] = "application/json"
                    async with session.request(
                        "POST" if encoded is not None else "GET",
                        url,
                        data=encoded,
                        headers=headers,
                        allow_redirects=False,
                    ) as response:
                        response_status = response.status
                        if response.status >= 500 and attempt == 0:
                            continue
                        maximum = (
                            _RESPONSE_LIMIT if response.status < 300 else 64 * 1024
                        )
                        chunks = bytearray()
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            if len(chunks) + len(chunk) > maximum:
                                raise EcrfLedgerError(
                                    "LEGACY_OSB_RESPONSE_TOO_LARGE", import_id=import_id
                                )
                            chunks.extend(chunk)
                        content_type = (
                            response.headers.get("Content-Type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if content_type != "application/json":
                            raise EcrfLedgerError(
                                "LEGACY_OSB_RESPONSE_CONTENT_TYPE",
                                status=response.status,
                                import_id=import_id,
                            )
                        try:
                            result = json.loads(chunks.decode("utf-8", errors="strict"))
                        except (UnicodeDecodeError, ValueError) as error:
                            raise EcrfLedgerError(
                                "LEGACY_OSB_RESPONSE_JSON_INVALID", import_id=import_id
                            ) from error
                        if not 200 <= response.status < 300:
                            code = (
                                result.get("code") if isinstance(result, dict) else None
                            )
                            if not isinstance(code, str) or not re.fullmatch(
                                r"[A-Z][A-Z0-9_]{1,100}", code
                            ):
                                code = "LEGACY_OSB_OWNER_REFUSED"
                            raise EcrfLedgerError(
                                code, status=response.status, import_id=import_id
                            )
                        self._check_envelope(result, study_id)
                        if encoded is not None and not isinstance(
                            result.get("appended"), bool
                        ):
                            raise EcrfLedgerError(
                                "LEGACY_OSB_RESPONSE_APPEND_INVALID",
                                import_id=import_id,
                            )
                        return result["data"]
                except (aiohttp.ClientError, TimeoutError):
                    if response_status is not None and 400 <= response_status < 500:
                        raise EcrfLedgerError(
                            "LEGACY_OSB_OWNER_RESPONSE_INCOMPLETE",
                            status=response_status,
                            import_id=import_id,
                        ) from None
                    if attempt == 1:
                        raise EcrfLedgerError(
                            "LEGACY_OSB_TRANSPORT_OUTCOME_UNKNOWN", import_id=import_id
                        ) from None
                except EcrfLedgerError as error:
                    if import_id is not None and error.import_id is None:
                        raise EcrfLedgerError(
                            error.code, status=error.status, import_id=import_id
                        ) from None
                    raise
        raise AssertionError("unreachable retry state")

    def _check_envelope(self, value, study_id):
        if (
            not isinstance(value, dict)
            or value.get("profile") != "il-legacy-osb-ledger/1"
            or value.get("tenant_id") != self.tenant_id
            or value.get("study_id") != study_id
            or value.get("production_eligible") is not False
            or "data" not in value
        ):
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_SCOPE_INVALID")
        scope = value.get("platform_scope")
        if not isinstance(scope, dict) or set(scope) != {"tenant_id", "study_id"}:
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_SCOPE_INVALID")
        try:
            for identifier in scope.values():
                if str(uuid.UUID(identifier)) != identifier:
                    raise ValueError("non-canonical UUID")
        except (ValueError, TypeError, AttributeError) as error:
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_SCOPE_INVALID") from error
        previous = self._platform_scopes.get(study_id)
        if previous is not None and previous != scope:
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_BINDING_CHANGED")
        self._platform_scopes[study_id] = dict(scope)

    def read_latest_payload(self, study_id):
        return self._row_to_payload(
            self._request(study_id, "/payloads/latest"), study_id
        )

    def read_payload(self, payload_hash, study_id=None):
        study_id = study_id or os.environ.get("ECRF_STUDY_ID")
        try:
            row = self._request(study_id, f"/payloads/{_hash(payload_hash)}")
        except EcrfLedgerError as error:
            if error.status == 404 and error.code == "LEGACY_OSB_PAYLOAD_NOT_FOUND":
                return None
            raise
        result = self._row_to_payload(row, study_id)
        if result is None or result["payload_hash"] != payload_hash:
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAYLOAD_INVALID")
        return result

    def _pages(self, study_id, resource):
        cursor, seen = None, set()
        while True:
            query = {"limit": 100}
            if cursor is not None:
                query["cursor"] = cursor
            page = self._request(study_id, f"/{resource}?{urlencode(query)}")
            if (
                not isinstance(page, dict)
                or not isinstance(page.get("items"), list)
                or "next_cursor" not in page
            ):
                raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAGE_INVALID")
            for row in page["items"]:
                if not isinstance(row, dict) or row.get("study_id") != study_id:
                    raise EcrfLedgerError("LEGACY_OSB_RESPONSE_SCOPE_INVALID")
                yield row
            cursor = page["next_cursor"]
            if cursor is None:
                return
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise EcrfLedgerError("LEGACY_OSB_RESPONSE_CURSOR_INVALID")
            seen.add(cursor)

    def iter_payloads(self, study_id):
        """Drain all metadata pages; no tenant-wide discovery or truncation."""
        yield from self._pages(study_id, "payloads")

    def iter_imports(self, study_id):
        """Drain metadata; read_import retrieves the complete census/UID map."""
        yield from self._pages(study_id, "imports")

    def list_payload_studies(self, study_ids=None):
        """Latest timestamps for every explicitly authorized configured study."""
        if study_ids is None:
            configured = os.environ.get("ECRF_STUDY_IDS")
            study_ids = (
                json.loads(configured)
                if configured
                else [os.environ.get("ECRF_STUDY_ID")]
            )
        if not isinstance(study_ids, list) or not study_ids:
            raise ValueError("Provide explicit authorized study_ids or ECRF_STUDY_IDS")
        result = []
        for study_id in dict.fromkeys(_text(item, "study_id") for item in study_ids):
            page = self._request(study_id, "/payloads?limit=1")
            if (
                not isinstance(page, dict)
                or not isinstance(page.get("items"), list)
                or len(page["items"]) > 1
            ):
                raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAGE_INVALID")
            if page["items"]:
                row = page["items"][0]
                if row.get("study_id") != study_id:
                    raise EcrfLedgerError("LEGACY_OSB_RESPONSE_SCOPE_INVALID")
                result.append((study_id, _date(row.get("created_at"))))
        return sorted(result, key=lambda item: item[1], reverse=True)

    @staticmethod
    def _row_to_payload(row, study_id):
        if row is None:
            return None
        if not isinstance(row, dict) or row.get("study_id") != study_id:
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAYLOAD_INVALID")
        text = row.get("payload_json")
        if not isinstance(text, str) or row.get("representation") not in {
            "payload_jsonb",
            "payload_gzip",
        }:
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAYLOAD_INVALID")
        raw = text.encode("utf-8")
        if (
            len(raw) > _PAYLOAD_LIMIT
            or len(raw) != row.get("payload_json_byte_size")
            or hashlib.sha256(raw).hexdigest() != row.get("payload_json_sha256")
        ):
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAYLOAD_BYTES_INVALID")
        try:
            payload = json.loads(text)
        except ValueError as error:
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAYLOAD_INVALID") from error
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("source"), dict)
            or payload["source"].get("studyId") != study_id
        ):
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAYLOAD_INVALID")
        keys = (
            "payload_hash",
            "study_id",
            "build_hash",
            "prior_payload_hash",
            "format_version",
            "census",
        )
        if any(key not in row for key in keys) or not isinstance(row["census"], dict):
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_PAYLOAD_INVALID")
        _hash(row["payload_hash"])
        return {**{key: row[key] for key in keys}, "payload": payload}

    @staticmethod
    def _import_row(row, study_id):
        if row is None:
            return None
        keys = (
            "import_id",
            "payload_hash",
            "osb_study_uid",
            "osb_project_number",
            "status",
            "census",
            "uid_map",
            "imported_at",
            "importer_version",
        )
        if (
            not isinstance(row, dict)
            or row.get("study_id") != study_id
            or any(key not in row for key in keys)
            or not isinstance(row["census"], dict)
            or not isinstance(row["uid_map"], dict)
        ):
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_IMPORT_INVALID")
        return {
            **{key: row[key] for key in keys},
            "imported_at": _date(row["imported_at"]),
        }

    def read_current_crosswalk(self, study_id):
        return self._import_row(self._request(study_id, "/imports/current"), study_id)

    def read_import(self, study_id, import_id):
        identifier = str(uuid.UUID(import_id))
        try:
            row = self._request(study_id, f"/imports/{identifier}")
        except EcrfLedgerError as error:
            if error.status == 404 and error.code == "LEGACY_OSB_IMPORT_NOT_FOUND":
                return None
            raise
        result = self._import_row(row, study_id)
        if result is None or result["import_id"] != identifier:
            raise EcrfLedgerError("LEGACY_OSB_RESPONSE_IMPORT_INVALID")
        return result

    def write_import_ledger(
        self,
        study_id,
        payload_hash,
        osb_study_uid,
        osb_project_number,
        status,
        census,
        uid_map,
        import_id=None,
    ):
        """Append an immutable attempt; keep its UUID for uncertain recovery."""
        if status == "partial" and not (
            census.get("stopped") or census.get("release_blockers")
        ):
            raise ValueError(
                "status='partial' requires stopped rows or release blockers"
            )
        identifier = str(uuid.UUID(import_id)) if import_id else str(uuid.uuid4())
        body = {
            "import_id": identifier,
            "payload_hash": _hash(payload_hash),
            "osb_study_uid": osb_study_uid,
            "osb_project_number": osb_project_number,
            "status": status,
            "census": census,
            "uid_map": uid_map,
            "importer_version": IMPORTER_VERSION,
        }
        row = self._request(study_id, "/imports", body=body, import_id=identifier)
        if not isinstance(row, dict) or any(
            key not in row or not _same_json(row[key], value)
            for key, value in body.items()
        ):
            raise EcrfLedgerError(
                "LEGACY_OSB_RESPONSE_IMPORT_MISMATCH", import_id=identifier
            )
        self._import_row(row, study_id)
        return identifier
