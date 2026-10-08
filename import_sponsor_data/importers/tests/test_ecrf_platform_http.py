"""Owner HTTP boundary regressions. No native database or OSB API is opened."""

# Inspect injected transport policy and drive its async core on a real local socket.
# pylint: disable=protected-access

import hashlib
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock

import aiohttp
import pytest

from ..utils.ecrf_platform_db import IMPORTER_VERSION, EcrfLedgerError, EcrfPlatformDb

TENANT = "native-tenant"
STUDY = "NCT04255433"
HASH = "a" * 64
PLATFORM = {
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "study_id": "22222222-2222-4222-8222-222222222222",
}


def envelope(data, **changes):
    return {
        "profile": "il-legacy-osb-ledger/1",
        "tenant_id": TENANT,
        "study_id": STUDY,
        "platform_scope": dict(PLATFORM),
        "production_eligible": False,
        "data": data,
        **changes,
    }


def payload_row():
    payload = {
        "source": {"studyId": STUDY},
        "values": {"10": 0, "2": False, "unit": "µg", "missing": None},
        "ordered": ["second", "first"],
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    raw = text.encode("utf-8")
    return {
        "payload_hash": HASH,
        "study_id": STUDY,
        "build_hash": "build",
        "prior_payload_hash": None,
        "format_version": "osb360i/1.0",
        "census": {
            "total": 3,
            "mapped": 1,
            "carried": 2,
            "declaredOutOfScope": 0,
            "unmapped": 0,
        },
        "payload_json": text,
        "representation": "payload_gzip",
        "byte_size": len(raw),
        "payload_json_byte_size": len(raw),
        "payload_json_sha256": hashlib.sha256(raw).hexdigest(),
    }


async def chunks_from(chunks):
    for chunk in chunks:
        yield chunk


def response(body, status=200, content_type="application/json"):
    result = MagicMock(status=status, headers={"Content-Type": content_type})
    result.close.return_value = None
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(side_effect=lambda *_args: result.close())
    content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    # A fresh stream for each invocation, including repeated response fixtures.
    result.content.iter_chunked.side_effect = lambda *_args: chunks_from(
        [content[:19], content[19:]]
    )
    return result


def session_factory(session):
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return Mock(return_value=session)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ECRF_API_TOKEN", "synthetic-scoped-token")
    monkeypatch.delenv("ECRF_API_TOKEN_FILE", raising=False)
    session = MagicMock()
    adapter = EcrfPlatformDb(
        api_url="http://il-owner:3000",
        tenant_id=TENANT,
        session_factory=session_factory(session),
    )
    yield adapter, session
    adapter.close()
    assert session.__aexit__.call_count == session.__aenter__.call_count


def test_payload_exact_values_scope_destination_and_cleanup(client):
    adapter, session = client
    source = payload_row()
    reply = response(envelope(source))
    session.request.return_value = reply
    result = adapter.read_latest_payload(STUDY)
    assert result == {
        **{
            key: source[key]
            for key in (
                "payload_hash",
                "study_id",
                "build_hash",
                "prior_payload_hash",
                "format_version",
                "census",
            )
        },
        "payload": json.loads(source["payload_json"]),
    }
    args, options = session.request.call_args
    assert args == (
        "GET",
        f"http://il-owner:3000/api/pipeline/study/{STUDY}/legacy-osb/payloads/latest",
    )
    assert options["allow_redirects"] is False
    assert options["headers"]["Authorization"] == "Bearer synthetic-scoped-token"
    assert adapter._session_factory.call_args.kwargs["trust_env"] is False
    assert adapter._session_factory.call_args.kwargs["timeout"].total == 250
    reply.close.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": "other"},
        {"study_id": "other"},
        {"production_eligible": True},
        {"platform_scope": {**PLATFORM, "study_id": "native-study"}},
        {"profile": "other/1"},
    ],
)
def test_cross_scope_or_authority_response_refused(client, change):
    adapter, session = client
    session.request.return_value = response(envelope(payload_row(), **change))
    with pytest.raises(EcrfLedgerError, match="SCOPE_INVALID"):
        adapter.read_latest_payload(STUDY)


@pytest.mark.parametrize(
    "field,value",
    [
        ("payload_json_sha256", "b" * 64),
        ("payload_json_byte_size", 0),
        ("study_id", "wrong"),
        ("representation", "unknown"),
    ],
)
def test_corrupt_payload_refused(client, field, value):
    adapter, session = client
    row = payload_row()
    row[field] = value
    session.request.return_value = response(envelope(row))
    with pytest.raises(EcrfLedgerError):
        adapter.read_latest_payload(STUDY)


def test_missing_is_distinct_from_access_denial(client):
    adapter, session = client
    session.request.side_effect = [
        response(envelope(None)),
        response({"code": "LEGACY_OSB_PAYLOAD_NOT_FOUND"}, 404),
        response({"code": "PROJECT_ACCESS_DENIED"}, 404),
    ]
    assert adapter.read_latest_payload(STUDY) is None
    assert adapter.read_payload(HASH, STUDY) is None
    with pytest.raises(EcrfLedgerError, match="PROJECT_ACCESS_DENIED"):
        adapter.read_payload(HASH, STUDY)


def test_import_retry_keeps_exact_uuid_bytes_and_version(client):
    adapter, session = client
    submitted = []
    census, uid_map = {"stopped": [{"value": None}], "count": 0}, {
        "10": "Item_1",
        "2": False,
    }

    def transport(*_args, **options):
        submitted.append(options["data"])
        if len(submitted) == 1:
            raise aiohttp.ClientConnectionError(
                "untrusted secret-bearing transport detail"
            )
        body = json.loads(options["data"])
        return response(
            envelope(
                {
                    **body,
                    "study_id": STUDY,
                    "imported_at": "2026-10-08T08:00:00.123456Z",
                },
                appended=False,
            ),
            201,
        )

    session.request.side_effect = transport
    identifier = adapter.write_import_ledger(
        STUDY, HASH, "Study_1", None, "partial", census, uid_map
    )
    assert submitted[0] == submitted[1]
    body = json.loads(submitted[0])
    assert body["import_id"] == identifier
    assert body["importer_version"] == IMPORTER_VERSION == "360i-importer/1.18"
    assert body["uid_map"] == uid_map and body["census"] == census


def test_uncertain_import_retains_recovery_id_without_secrets(client):
    adapter, session = client
    session.request.side_effect = TimeoutError("secret-token")
    identifier = "33333333-3333-4333-8333-333333333333"
    with pytest.raises(EcrfLedgerError) as raised:
        adapter.write_import_ledger(
            STUDY, HASH, None, None, "failed", {}, {}, import_id=identifier
        )
    assert raised.value.import_id == identifier
    assert "secret-token" not in str(raised.value)
    assert session.request.call_count == 2


def test_current_crosswalk_preserves_full_map_and_timestamp(client):
    adapter, session = client
    row = {
        "study_id": STUDY,
        "import_id": "33333333-3333-4333-8333-333333333333",
        "payload_hash": HASH,
        "osb_study_uid": "Study_1",
        "osb_project_number": None,
        "status": "succeeded",
        "census": {"skipped": []},
        "uid_map": {"zero": 0, "false": False, "null": None},
        "imported_at": "2026-10-08T08:00:00.123456Z",
        "importer_version": IMPORTER_VERSION,
    }
    session.request.return_value = response(envelope(row))
    result = adapter.read_current_crosswalk(STUDY)
    assert result["uid_map"] == row["uid_map"]
    assert isinstance(result["imported_at"], datetime)
    assert result["imported_at"].microsecond == 123456


def test_complete_paging_and_cursor_loop_refusal(client):
    adapter, session = client
    session.request.side_effect = [
        response(
            envelope(
                {
                    "items": [{"study_id": STUDY, "payload_hash": HASH}],
                    "next_cursor": "opaque+/",
                }
            )
        ),
        response(
            envelope(
                {
                    "items": [{"study_id": STUDY, "payload_hash": "b" * 64}],
                    "next_cursor": None,
                }
            )
        ),
    ]
    assert len(list(adapter.iter_payloads(STUDY))) == 2
    assert "cursor=opaque%2B%2F" in session.request.call_args.args[1]
    session.request.side_effect = None
    session.request.return_value = response(
        envelope({"items": [], "next_cursor": "same"})
    )
    with pytest.raises(EcrfLedgerError, match="CURSOR_INVALID"):
        list(adapter.iter_imports(STUDY))


def test_binding_change_between_read_and_write_is_refused(client):
    adapter, session = client
    session.request.side_effect = [
        response(envelope(None)),
        response(
            envelope(
                None,
                platform_scope={
                    **PLATFORM,
                    "study_id": "44444444-4444-4444-8444-444444444444",
                },
            )
        ),
    ]
    assert adapter.read_latest_payload(STUDY) is None
    with pytest.raises(EcrfLedgerError, match="BINDING_CHANGED"):
        adapter.read_current_crosswalk(STUDY)


def test_token_file_is_reread_and_access_gate_is_forwarded(
    client, monkeypatch, tmp_path
):
    adapter, session = client
    token_file = tmp_path / "scoped-token"
    monkeypatch.delenv("ECRF_API_TOKEN")
    monkeypatch.setenv("ECRF_API_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("ECRF_ACCESS_GATE_TOKEN", "synthetic-gate")
    session.request.return_value = response(envelope(None))
    for token in ("token-one", "token-two"):
        token_file.write_text(token, encoding="utf-8")
        adapter.read_latest_payload(STUDY)
        assert (
            session.request.call_args.kwargs["headers"]["Authorization"]
            == f"Bearer {token}"
        )
        assert (
            session.request.call_args.kwargs["headers"]["X-Access-Gate"]
            == "synthetic-gate"
        )


@pytest.mark.parametrize(
    "origin",
    [
        "http://user:pass@owner",
        "http://owner/path",
        "http://owner?query=x",
        "http://owner/#fragment",
        "file:///owner",
        "http://owner\\other",
    ],
)
def test_unsafe_destination_rejected(origin):
    with pytest.raises(ValueError):
        EcrfPlatformDb(api_url=origin, tenant_id=TENANT)


def test_partial_and_unscoped_requests_fail_before_http(client, monkeypatch):
    adapter, session = client
    monkeypatch.delenv("ECRF_STUDY_ID", raising=False)
    monkeypatch.delenv("ECRF_STUDY_IDS", raising=False)
    with pytest.raises(ValueError):
        adapter.write_import_ledger(STUDY, HASH, None, None, "partial", {}, {})
    with pytest.raises(ValueError):
        adapter.read_payload(HASH)
    with pytest.raises(ValueError):
        adapter.list_payload_studies()
    session.request.assert_not_called()


def test_malformed_and_redirect_responses_never_succeed(client):
    adapter, session = client
    bad = response({})
    bad.content.iter_chunked.side_effect = lambda *_args: chunks_from([b"\xff"])
    session.request.side_effect = [bad, response({}, 302, "text/html")]
    for _ in range(2):
        with pytest.raises(EcrfLedgerError):
            adapter.read_latest_payload(STUDY)


def test_retired_proposal_entry_refuses_before_clients(monkeypatch):
    from .. import run_import_osb_proposal_v2 as worker
    from ..utils import osb_proposal_db

    init, connect = Mock(side_effect=AssertionError("must not initialize")), Mock(
        side_effect=AssertionError("must not connect")
    )
    monkeypatch.setattr(worker.BaseImporter, "__init__", init)
    monkeypatch.setattr(osb_proposal_db.psycopg, "connect", connect)
    for entry in (worker.main, worker.ImportOsbProposalV2):
        with pytest.raises(RuntimeError, match="OSB_PROPOSAL_V2_DELIVERY_RETIRED"):
            entry()
    init.assert_not_called()
    connect.assert_not_called()


def test_import_readback_cannot_conflate_false_with_zero(client):
    adapter, session = client

    def transport(*_args, **options):
        body = json.loads(options["data"])
        body["uid_map"]["value"] = 0
        return response(
            envelope(
                {
                    **body,
                    "study_id": STUDY,
                    "imported_at": "2026-10-08T08:00:00.123456Z",
                },
                appended=True,
            ),
            201,
        )

    session.request.side_effect = transport
    with pytest.raises(EcrfLedgerError, match="IMPORT_MISMATCH"):
        adapter.write_import_ledger(
            STUDY, HASH, None, None, "failed", {}, {"value": False}
        )


def test_actual_owner_envelopes_when_explicitly_qualified(monkeypatch):
    """Opt-in native owner fixture gate; a skip is not native qualification."""
    import os
    from pathlib import Path

    configured = os.environ.get("ECRF_W21_OWNER_FIXTURE_DIR")
    if not configured:
        pytest.skip(
            "Set ECRF_W21_OWNER_FIXTURE_DIR to actual qualified IL owner envelopes"
        )
    root = Path(configured)

    def load(name):
        return json.loads((root / f"{name}.json").read_text(encoding="utf-8-sig"))

    first = load("payload_jsonb")
    study_id, tenant_id = first["study_id"], first["tenant_id"]
    monkeypatch.setenv("ECRF_API_TOKEN", "synthetic-transport-token")
    monkeypatch.delenv("ECRF_API_TOKEN_FILE", raising=False)
    session = MagicMock()
    with EcrfPlatformDb(
        api_url="http://qualified-owner",
        tenant_id=tenant_id,
        session_factory=session_factory(session),
    ) as adapter:
        for name in ("payload_jsonb", "payload_gzip", "csl_producer_readback"):
            actual = load(name)
            session.request.return_value = response(actual)
            result = adapter.read_payload(actual["data"]["payload_hash"], study_id)
            assert result["payload"] == json.loads(actual["data"]["payload_json"])
            assert result["prior_payload_hash"] == actual["data"]["prior_payload_hash"]
            assert result["census"] == actual["data"]["census"]
        for name in ("current_import", "import_readback"):
            actual = load(name)
            session.request.return_value = response(actual)
            result = adapter.read_import(study_id, actual["data"]["import_id"])
            for key in (
                "census",
                "uid_map",
                "importer_version",
                "osb_study_uid",
                "osb_project_number",
                "status",
            ):
                assert result[key] == actual["data"][key]
        for resource in ("payload", "import"):
            pages = [load(f"{resource}_page1"), load(f"{resource}_page2")]
            session.request.side_effect = [response(page) for page in pages]
            iterator = (
                adapter.iter_payloads if resource == "payload" else adapter.iter_imports
            )
            assert list(iterator(study_id)) == [
                row for page in pages for row in page["data"]["items"]
            ]
            session.request.side_effect = None
        for name, reader in (
            ("latest_empty", adapter.read_latest_payload),
            ("current_empty", adapter.read_current_crosswalk),
        ):
            session.request.return_value = response(load(name))
            assert reader(study_id) is None


def test_partial_success_body_retries_same_attempt(client):
    adapter, session = client
    submitted = []

    async def interrupted():
        yield b'{"profile":'
        raise aiohttp.ClientPayloadError("secret-bearing body error")

    def transport(*_args, **options):
        submitted.append(options["data"])
        if len(submitted) == 1:
            lost = response({}, 201)
            lost.content.iter_chunked.side_effect = lambda *_args: interrupted()
            return lost
        body = json.loads(options["data"])
        return response(
            envelope(
                {
                    **body,
                    "study_id": STUDY,
                    "imported_at": "2026-10-08T08:00:00.123456Z",
                },
                appended=False,
            ),
            201,
        )

    session.request.side_effect = transport
    adapter.write_import_ledger(STUDY, HASH, None, None, "failed", {}, {})
    assert submitted[0] == submitted[1]


def test_actual_aiohttp_total_deadline_stops_dripping_body_and_closes_sessions(
    monkeypatch,
):
    import asyncio
    import socket
    import time

    from aiohttp import web

    monkeypatch.setenv("ECRF_API_TOKEN", "synthetic-token")
    monkeypatch.delenv("ECRF_API_TOKEN_FILE", raising=False)

    async def exercise():
        sessions = []

        def bounded_factory(**options):
            assert options["timeout"].total == 250
            assert options["trust_env"] is False
            # Accelerate the same production total-timeout mechanism. A byte
            # arrives every20ms, so the50ms idle timeout cannot enforce120ms.
            options["timeout"] = aiohttp.ClientTimeout(
                total=0.12, connect=0.05, sock_read=0.05
            )
            session = aiohttp.ClientSession(**options)
            sessions.append(session)
            return session

        async def drip(request):
            result = web.StreamResponse(headers={"Content-Type": "application/json"})
            await result.prepare(request)
            try:
                for _ in range(100):
                    await result.write(b" ")
                    await asyncio.sleep(0.02)
            except (aiohttp.ClientConnectionError, ConnectionResetError):
                pass
            return result

        app = web.Application()
        app.router.add_get("/drip", drip)
        runner = web.AppRunner(app, shutdown_timeout=0.1, handler_cancellation=True)
        await runner.setup()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        site = web.SockSite(runner, listener)
        await site.start()
        adapter = EcrfPlatformDb(
            api_url=f"http://127.0.0.1:{port}",
            tenant_id=TENANT,
            session_factory=bounded_factory,
        )
        started = time.monotonic()
        try:
            with pytest.raises(EcrfLedgerError, match="TRANSPORT_OUTCOME_UNKNOWN"):
                await adapter._request_async(
                    STUDY, f"http://127.0.0.1:{port}/drip", None, None
                )
            assert time.monotonic() - started < 1
            assert all(session.closed for session in sessions)
        finally:
            adapter.close()
            await runner.cleanup()
            listener.close()

    asyncio.run(exercise())
