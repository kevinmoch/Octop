"""Real P1 acceptance: isolated Octop HTTP/WS service and real Chromium."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / ".artifacts" / "webskill-p1" / uuid.uuid4().hex[:10]
OUT.mkdir(parents=True, exist_ok=True)
os.environ["BROWSER_USE_PROFILES_DIR"] = str(OUT / "profiles")
os.environ["BROWSER_USE_CDP_PORT_START"] = str(40000 + os.getpid() % 10000)
os.environ["BROWSER_USE_SCREENSHOTS_DIR"] = str(OUT / "screenshots")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402
from octop_browser.profile import ProfileManager  # noqa: E402
from octop_browser.session import BrowserSession  # noqa: E402
from octop_browser.tool_interface import _registry  # noqa: E402
from tests.support.app import ensure_control_plane_bound  # noqa: E402
from tests.support.auth import auth_header, bootstrap_admin, create_user  # noqa: E402

from octop.api.app import build_app  # noqa: E402
from octop.infra.control.browser.webskill.page_agent import (  # noqa: E402
    _node_index,
    _sensitive_subtrees,
)
from octop.infra.server import OctopServer  # noqa: E402


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/next":
            body = "<!doctype html><html><body><h1>Next document</h1></body></html>"
        else:
            body = """<!doctype html><html><head><title>P1 fixture</title></head><body>
<button id="run" onclick="window.clickCount=(window.clickCount||0)+1">Run once</button>
<input type="password" aria-label="Secret input">
<div data-webskill-private><button>Private child</button></div>
</body></html>"""
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def call(ws: Any, message: dict[str, Any]) -> dict[str, Any]:
    await ws.send(json.dumps(message))
    reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
    assert isinstance(reply, dict)
    return reply


async def browser_value(browser: BrowserSession, expression: str) -> Any:
    result = await browser._internal.client.send(  # noqa: SLF001
        "Runtime.evaluate", {"expression": expression, "returnByValue": True}
    )
    return result.get("result", {}).get("value")


async def accept() -> dict[str, Any]:
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    fixture_url = f"http://127.0.0.1:{fixture.server_port}/"
    octop = OctopServer(home=OUT / "home")
    with contextlib.redirect_stdout(io.StringIO()):
        await octop.start()
    await ensure_control_plane_bound(octop)
    app = build_app(octop)
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(uv.serve())
    browser: BrowserSession | None = None
    while not uv.started:
        if serving.done():
            await serving
        await asyncio.sleep(0.02)
    try:
        async with httpx.AsyncClient(base_url=origin, timeout=30) as client:
            await bootstrap_admin(client, OUT / "home")
            admin = await auth_header(client)
            users = (await client.get("/api/users", headers=admin)).json()
            user_id = next(user["id"] for user in users if user["username"] == "admin")
            profile = f"user-{user_id}"
            browser = await BrowserSession.create(
                profile=profile,
                mode="headless",
                profile_manager=ProfileManager(base_dir=OUT / "profiles"),
            )
            _registry[profile] = browser
            navigation = await browser.navigate(fixture_url)
            assert navigation.success, navigation.error
            headers = {**admin, "Origin": origin, "Sec-Fetch-Site": "same-origin"}
            created = await client.post("/api/browser-agent/sessions", headers=headers, json={})
            assert created.status_code == 200, created.text
            session_id = created.json()["sessionId"]
            ticket = (
                await client.post(
                    f"/api/browser-agent/sessions/{session_id}/tickets", headers=headers
                )
            ).json()["ticket"]
            ws_url = f"ws://127.0.0.1:{port}/api/browser-agent/ws?{urlencode({'ticket': ticket})}"
            async with websockets.connect(
                ws_url, origin=origin, additional_headers={"Sec-Fetch-Site": "same-origin"}
            ) as ws:
                perceived = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "read-1",
                        "scope": {"include": ["body"]},
                    },
                )
                if (
                    perceived.get("type") != "perceive-result"
                    or len(perceived.get("targets", [])) != 1
                ):
                    dom = await browser.dom_tree(level="structured")
                    raw = await browser._internal.client.send("DOM.getDocument", {"depth": -1})
                    root = raw.get("root", {})
                    elements = json.loads(dom.content).get("elements", [])
                    diagnostics = {
                        "replyCode": perceived.get("code"),
                        "nodeIds": [element.get("node_id") for element in elements],
                        "backendNodeIds": sorted(_node_index(root).values()),
                        "excludedNodeIds": sorted(_sensitive_subtrees(root)),
                    }
                    (OUT / "dom-diagnostics.json").write_text(
                        json.dumps(diagnostics), encoding="utf-8"
                    )
                assert perceived["type"] == "perceive-result", perceived
                assert perceived["result"]["page"]["url"] == fixture_url
                assert len(perceived["targets"]) == 1, perceived["targets"]
                request = {"ref": perceived["targets"][0]["ref"], "action": "click"}
                issued = await client.post(
                    f"/api/browser-agent/sessions/{session_id}/grants",
                    headers=headers,
                    json={"request": request},
                )
                assert issued.status_code == 200, issued.text
                grant = issued.json()["grant"]
                executed = await call(
                    ws,
                    {"type": "execute", "requestId": "click-1", "request": request, "grant": grant},
                )
                assert executed["outcome"]["ok"] is True, executed
                assert await browser_value(browser, "window.clickCount || 0") == 1
                replay = await call(
                    ws,
                    {"type": "execute", "requestId": "click-2", "request": request, "grant": grant},
                )
                assert replay["code"] == "PAGE_ACTION_STALE_REF", replay
                assert await browser_value(browser, "window.clickCount || 0") == 1

                next_grant = (
                    await client.post(
                        f"/api/browser-agent/sessions/{session_id}/grants",
                        headers=headers,
                        json={"request": request},
                    )
                ).json()["grant"]
                stream_ticket = (
                    await client.post(
                        f"/api/browser-agent/sessions/{session_id}/tickets", headers=headers
                    )
                ).json()["ticket"]
                stream_url = f"ws://127.0.0.1:{port}/api/browser-stream/ws?{urlencode({'ticket': stream_ticket})}"
                async with websockets.connect(stream_url, origin=origin) as stream:
                    await stream.send(
                        json.dumps({"type": "start", "url": "", "width": 800, "height": 600})
                    )
                    await stream.send(json.dumps({"type": "navigate", "url": f"{fixture_url}next"}))
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        event = json.loads(await asyncio.wait_for(stream.recv(), timeout=15))
                        if event.get("type") == "tabs" and any(
                            tab.get("active") and str(tab.get("url", "")).endswith("/next")
                            for tab in event.get("tabs", [])
                        ):
                            break
                    else:
                        raise AssertionError("The human navigation did not reach the next document")
                denied = await call(
                    ws,
                    {
                        "type": "execute",
                        "requestId": "after-takeover",
                        "request": request,
                        "grant": next_grant,
                    },
                )
                after_navigation = await browser_value(
                    browser,
                    "({url: location.href, count: window.clickCount ?? null})",
                )
                (OUT / "takeover-diagnostics.json").write_text(
                    json.dumps({"deniedCode": denied.get("code"), "page": after_navigation}),
                    encoding="utf-8",
                )
                assert denied["type"] == "error", denied
                assert after_navigation == {"url": f"{fixture_url}next", "count": None}

                wrong_origin = await client.post(
                    f"/api/browser-agent/sessions/{session_id}/tickets",
                    headers={**admin, "Origin": "https://attacker.invalid"},
                )
                assert wrong_origin.status_code == 403
                other = await create_user(client, admin, username="other", permissions=["browser"])
                forbidden = await create_user(client, admin, username="forbidden", permissions=[])
                assert (
                    await client.get(f"/api/browser-agent/sessions/{session_id}", headers=other)
                ).status_code == 404
                assert (
                    await client.post(
                        "/api/browser-agent/sessions",
                        headers={**forbidden, "Origin": origin},
                        json={},
                    )
                ).status_code == 403
            deleted = await client.delete(
                f"/api/browser-agent/sessions/{session_id}", headers=headers
            )
            assert deleted.status_code == 200
            assert await browser_value(browser, "location.href") == f"{fixture_url}next"
            assert octop.services is not None
            audit = octop.services.audit_repo.query(action="webskill.page.action")
            assert any(row.payload == '{"action":"click","ok":true}' for row in audit)
            assert all(grant not in (row.payload or "") for row in audit)
            return {
                "status": "passed",
                "checks": [
                    "real Chromium main-document perception and sensitive-subtree filtering",
                    "one-shot ticket over real HTTP/WS",
                    "confirmed grant and exactly one real mouse click",
                    "grant replay rejection without another side effect",
                    "human navigation takes control and stale action cannot run",
                    "Origin, browser permission and cross-user isolation",
                    "session deletion preserves the running Chrome",
                    "real audit rows contain no grant token",
                ],
                "evidence": str(OUT / "result.json"),
            }
    finally:
        uv.should_exit = True
        await serving
        await octop.stop()
        if browser is not None:
            await browser.close(kill=True)
        _registry.pop(f"user-{locals().get('user_id', 1)}", None)
        fixture.shutdown()
        fixture.server_close()


if __name__ == "__main__":
    result: dict[str, Any]
    try:
        result = asyncio.run(accept())
    except Exception as error:
        result = {"status": "failed", "errorType": type(error).__name__}
        (OUT / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        raise
    (OUT / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
