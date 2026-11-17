"""HTTP/WS integration for the restricted PageAgent host."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin, create_user
from tests.support.http import ws_connect
from tests.unit.api.test_webskill_page_agent import FakeBrowser, resolver_for

from octop.api.routers.browser import stream, webskill
from octop.infra.control.browser.webskill.sessions import BrowserAgentRegistry

ORIGIN_HEADERS = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
WS_ORIGIN_HEADERS = [(b"origin", b"http://testserver"), (b"sec-fetch-site", b"same-origin")]


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch):
    registry = BrowserAgentRegistry()
    monkeypatch.setattr(webskill, "browser_agent_registry", registry)
    monkeypatch.setattr(webskill, "resolve_harness_session", resolver_for(FakeBrowser()))
    monkeypatch.setattr(webskill, "control_owner_for", lambda _profile: "agent")
    return registry


async def new_session(client: httpx.AsyncClient, auth: dict[str, str]) -> str:
    response = await client.post(
        "/api/browser-agent/sessions", headers={**auth, **ORIGIN_HEADERS}, json={"startUrl": ""}
    )
    assert response.status_code == 200, response.text
    return response.json()["sessionId"]


async def test_perceive_grant_execute_and_audit(
    tmp_path, isolated_registry: BrowserAgentRegistry
) -> None:
    async with octop_client(tmp_path) as (client, server):
        await bootstrap_admin(client, tmp_path)
        auth = await auth_header(client)
        session_id = await new_session(client, auth)
        ticket_response = await client.post(
            f"/api/browser-agent/sessions/{session_id}/tickets",
            headers={**auth, **ORIGIN_HEADERS},
        )
        ticket = ticket_response.json()["ticket"]
        app = client._octop_app
        async with ws_connect(
            app,
            f"/api/browser-agent/ws?{urlencode({'ticket': ticket})}",
            headers=WS_ORIGIN_HEADERS,
        ) as ws:
            await ws.send_json(
                {"type": "perceive", "requestId": "read-1", "scope": {"include": ["body"]}}
            )
            perceived = await ws.receive_json()
            assert perceived["type"] == "perceive-result"
            assert perceived["requestId"] == "read-1"
            ref = perceived["targets"][0]["ref"]
            request = {"ref": ref, "action": "click"}
            granted = await client.post(
                f"/api/browser-agent/sessions/{session_id}/grants",
                headers={**auth, **ORIGIN_HEADERS},
                json={"request": request},
            )
            assert granted.status_code == 200, granted.text
            grant = granted.json()["grant"]
            await ws.send_json(
                {"type": "execute", "requestId": "write-1", "request": request, "grant": grant}
            )
            executed = await ws.receive_json()
            assert executed["type"] == "execute-result"
            assert executed["outcome"]["ok"] is True
            await ws.send_json(
                {"type": "execute", "requestId": "write-2", "request": request, "grant": grant}
            )
            replay = await ws.receive_json()
            assert replay["code"] == "PAGE_ACTION_STALE_REF"

        assert server.services is not None
        audit = server.services.audit_repo.query(action="webskill.page.action")
        assert len(audit) == 2
        rows = [json.loads(row.payload or "{}") for row in audit]
        expected_document = hashlib.sha256(perceived["document"]["key"].encode("utf-8")).hexdigest()
        expected_grant = hashlib.sha256(grant.encode("utf-8")).hexdigest()
        for row in rows:
            assert row["document"] == expected_document
            assert row["documentTarget"] == "target-1"
            assert row["grantDigest"] == expected_grant
            assert row["ref"] == ref
        payloads = "\n".join(row.payload or "" for row in audit)
        assert grant not in payloads
        assert ticket not in payloads
        grants = server.services.audit_repo.query(action="webskill.grant.issued")
        assert json.loads(grants[0].payload or "{}")["grantDigest"] == expected_grant
        deleted = await client.delete(
            f"/api/browser-agent/sessions/{session_id}", headers={**auth, **ORIGIN_HEADERS}
        )
        assert deleted.json() == {"ok": True}
        assert (
            await client.get(f"/api/browser-agent/sessions/{session_id}", headers=auth)
        ).status_code == 404


async def test_origin_ticket_replay_permission_and_cross_user_denied(
    tmp_path, isolated_registry: BrowserAgentRegistry
) -> None:
    async with octop_client(tmp_path) as (client, _server):
        await bootstrap_admin(client, tmp_path)
        admin = await auth_header(client)
        no_browser = await create_user(client, admin, username="without-browser", permissions=[])
        other = await create_user(client, admin, username="other-user", permissions=["browser"])
        for auth in (no_browser, other):
            assert (
                await client.post(
                    "/api/browser-agent/sessions", headers={**auth, **ORIGIN_HEADERS}, json={}
                )
            ).status_code == (403 if auth is no_browser else 200)
        session_id = await new_session(client, admin)
        assert (
            await client.get(f"/api/browser-agent/sessions/{session_id}", headers=other)
        ).status_code == 404
        ticket = (
            await client.post(
                f"/api/browser-agent/sessions/{session_id}/tickets",
                headers={**admin, **ORIGIN_HEADERS},
            )
        ).json()["ticket"]
        path = f"/api/browser-agent/ws?{urlencode({'ticket': ticket})}"
        app = client._octop_app
        with pytest.raises(WebSocketDisconnect) as wrong_origin:
            async with ws_connect(app, path, headers=[(b"origin", b"https://attacker.invalid")]):
                pass
        assert wrong_origin.value.code == 4001
        async with ws_connect(app, path, headers=WS_ORIGIN_HEADERS):
            pass
        with pytest.raises(WebSocketDisconnect) as replay:
            async with ws_connect(app, path, headers=WS_ORIGIN_HEADERS):
                pass
        assert replay.value.code == 4001


@pytest.mark.parametrize(
    ("origin", "accepted"),
    [
        ("http://testserver:80", True),
        ("https://testserver", False),
        ("http://testserver.attacker.invalid", False),
        ("http://user@testserver", False),
        ("http://testserver/path", False),
        ("null", False),
    ],
)
def test_origin_is_a_normalized_exact_tuple(origin: str, accepted: bool) -> None:
    if accepted:
        webskill._check_origin(origin, scheme="http", host="testserver")
    else:
        with pytest.raises(webskill.BrowserAgentError):
            webskill._check_origin(origin, scheme="http", host="testserver")


async def test_handoff_rejects_cross_site_and_fetch_metadata_requests(
    tmp_path, isolated_registry: BrowserAgentRegistry
) -> None:
    async with octop_client(tmp_path) as (client, _server):
        await bootstrap_admin(client, tmp_path)
        auth = await auth_header(client)
        for headers in (
            {"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "same-origin"},
            {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
        ):
            response = await client.post(
                "/api/browser/sessions/user-1/handoff",
                headers={**auth, **headers},
                json={"target": "user"},
            )
            assert response.status_code == 403, response.text
        allowed = await client.post(
            "/api/browser/sessions/user-1/handoff",
            headers={**auth, **ORIGIN_HEADERS},
            json={"target": "agent"},
        )
        assert allowed.status_code == 200, allowed.text


async def test_second_page_agent_connection_is_rejected_before_reuse(
    tmp_path, isolated_registry: BrowserAgentRegistry
) -> None:
    async with octop_client(tmp_path) as (client, _server):
        await bootstrap_admin(client, tmp_path)
        auth = await auth_header(client)
        session_id = await new_session(client, auth)
        app = client._octop_app
        first_ticket = (
            await client.post(
                f"/api/browser-agent/sessions/{session_id}/tickets",
                headers={**auth, **ORIGIN_HEADERS},
            )
        ).json()["ticket"]
        async with ws_connect(
            app,
            f"/api/browser-agent/ws?{urlencode({'ticket': first_ticket})}",
            headers=WS_ORIGIN_HEADERS,
        ):
            second_ticket = (
                await client.post(
                    f"/api/browser-agent/sessions/{session_id}/tickets",
                    headers={**auth, **ORIGIN_HEADERS},
                )
            ).json()["ticket"]
            with pytest.raises(WebSocketDisconnect) as second:
                async with ws_connect(
                    app,
                    f"/api/browser-agent/ws?{urlencode({'ticket': second_ticket})}",
                    headers=WS_ORIGIN_HEADERS,
                ):
                    pass
            assert second.value.code == 4001
        assert await new_session(client, auth) == session_id


async def test_ticket_stream_releases_unclaimed_session_registration(
    tmp_path,
    isolated_registry: BrowserAgentRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stream, "browser_agent_registry", isolated_registry)

    async def fake_stream(**kwargs) -> None:
        session = isolated_registry.get(session_id, 1)
        assert len(session.stream_connection_ids) == 1
        assert session.has_connected is False
        await kwargs["send_json"]({"type": "status", "status": "streaming"})

    monkeypatch.setattr(stream, "run_browser_stream_session", fake_stream)
    async with octop_client(tmp_path) as (client, _server):
        await bootstrap_admin(client, tmp_path)
        auth = await auth_header(client)
        session_id = await new_session(client, auth)
        ticket = (
            await client.post(
                f"/api/browser-agent/sessions/{session_id}/tickets",
                headers={**auth, **ORIGIN_HEADERS},
            )
        ).json()["ticket"]
        app = client._octop_app
        async with ws_connect(
            app,
            f"/api/browser-stream/ws?{urlencode({'ticket': ticket})}",
            headers=WS_ORIGIN_HEADERS,
        ) as ws:
            assert (await ws.receive_json())["status"] == "streaming"
        assert isolated_registry.get(session_id, 1).stream_connection_ids == set()


@pytest.mark.parametrize(
    ("client_host", "accepted"),
    [("127.0.0.1", True), ("203.0.113.20", False)],
)
async def test_origin_scheme_comes_only_from_a_trusted_proxy(
    client_host: str, accepted: bool
) -> None:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    async def endpoint(scope, receive, send) -> None:
        from fastapi import Request

        webskill.check_request_origin(Request(scope))

    app = ProxyHeadersMiddleware(endpoint, trusted_hosts="127.0.0.1")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/browser-agent/sessions",
        "raw_path": b"/api/browser-agent/sessions",
        "query_string": b"",
        "headers": [
            (b"host", b"dashboard.example"),
            (b"origin", b"https://dashboard.example"),
            (b"x-forwarded-proto", b"https"),
        ],
        "client": (client_host, 12345),
        "server": ("127.0.0.1", 8080),
    }
    if accepted:
        await app(scope, None, None)
    else:
        with pytest.raises(webskill.BrowserAgentError) as error:
            await app(scope, None, None)
        assert error.value.code == "ORIGIN_INVALID"


def test_legacy_stream_jwt_has_a_versioned_removal_gate() -> None:
    import importlib

    import octop

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(octop, "__version__", "1.1.0")
        with pytest.raises(RuntimeError, match="must be removed"):
            importlib.reload(stream)
    importlib.reload(stream)
