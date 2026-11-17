"""Real P1 acceptance: isolated Octop HTTP/WS service and real Chromium."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
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
        elif self.path == "/child":
            body = """<!doctype html><html><body>
<button id="child-action" onclick="window.childClicks=(window.childClicks||0)+1">Child action</button>
<a href="/next">Child next</a>
</body></html>"""
        elif self.path in {"/frame-next", "/frame-next?cross=1"}:
            body = """<!doctype html><html><body><h1>Next frame</h1>
<button id="frame-action" onclick="window.frameClicks=(window.frameClicks||0)+1">Frame action</button>
</body></html>"""
        elif self.path in {"/frame", "/frame?cross=1"}:
            body = """<!doctype html><html><body>
<button id="frame-action" onclick="window.frameClicks=(window.frameClicks||0)+1">Frame action</button>
<input aria-label="Frame search">
<select aria-label="Frame count"><option value="10">Ten</option><option value="20">Twenty</option></select>
<div role="button" tabindex="0" aria-label="Frame scroll area" style="display:block;height:200px;overflow:auto"><div style="height:1500px">Scrollable frame</div></div>
</body></html>"""
        else:
            body = f"""<!doctype html><html><head><title>P1 fixture</title></head><body>
<button id="run" onclick="window.clickCount=(window.clickCount||0)+1">Run once</button>
<input aria-label="Search query">
<select aria-label="Result count"><option value="10">Ten</option><option value="20">Twenty</option></select>
<input type="password" aria-label="Secret input">
<div data-webskill-private><button>Private child</button></div>
<div role="button" tabindex="0" aria-label="Main scroll area" style="display:block;height:400px;overflow:auto"><div style="height:1500px">Scrollable document</div></div>
<iframe id="same-frame" src="/frame" style="width:800px;height:450px"></iframe>
<iframe id="cross-frame" src="http://localhost:{self.server.server_port}/frame?cross=1" style="width:800px;height:450px"></iframe>
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


async def perform_action(
    client: httpx.AsyncClient,
    ws: Any,
    headers: dict[str, str],
    session_id: str,
    request_id: str,
    ref: str,
    action: str,
    value: str | None = None,
) -> dict[str, Any]:
    request = {"ref": ref, "action": action, **({"value": value} if value is not None else {})}
    issued = await client.post(
        f"/api/browser-agent/sessions/{session_id}/grants",
        headers=headers,
        json={"request": request},
    )
    assert issued.status_code == 200, issued.text
    executed = await call(
        ws,
        {
            "type": "execute",
            "requestId": request_id,
            "request": request,
            "grant": issued.json()["grant"],
        },
    )
    assert executed.get("outcome", {}).get("ok") is True, executed
    return executed


async def browser_value(browser: BrowserSession, expression: str) -> Any:
    result = await browser._internal.client.send(  # noqa: SLF001
        "Runtime.evaluate", {"expression": expression, "returnByValue": True}
    )
    return result.get("result", {}).get("value")


async def oopif_value(browser: BrowserSession, frame_key: str, expression: str) -> Any:
    from octop.infra.control.browser.webskill.oopif import attach_oopif_expected

    attached = await attach_oopif_expected(browser._internal.client, frame_key)  # noqa: SLF001
    try:
        result = await attached.client.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "contextId": attached.execution_context_id,
                "returnByValue": True,
            },
        )
        return result.get("result", {}).get("value")
    finally:
        await attached.close()


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
            from octop.infra.control.browser.webskill.lease import profile_command_gate

            browser = await BrowserSession.create(
                profile=profile,
                mode="headless",
                profile_manager=ProfileManager(base_dir=OUT / "profiles"),
                command_gate=profile_command_gate(profile),
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
                from octop.infra.control.browser.webskill.sessions import browser_agent_registry

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
                assert {target.get("name") for target in perceived["targets"]} == {
                    "Run once", "Search query", "Result count", "Main scroll area"
                }, perceived["targets"]
                request = {
                    "ref": next(
                        target["ref"] for target in perceived["targets"]
                        if target.get("name") == "Run once"
                    ),
                    "action": "click",
                }
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

                controls = {target.get("name"): target["ref"] for target in perceived["targets"]}
                await perform_action(
                    client, ws, headers, session_id, "main-fill",
                    controls["Search query"], "fill", "Octop query",
                )
                assert await browser_value(
                    browser, "document.querySelector('[aria-label=\"Search query\"]').value"
                ) == "Octop query"
                await perform_action(
                    client, ws, headers, session_id, "main-select",
                    controls["Result count"], "select", "20",
                )
                assert await browser_value(
                    browser, "document.querySelector('[aria-label=\"Result count\"]').value"
                ) == "20"
                await perform_action(
                    client, ws, headers, session_id, "main-scroll",
                    controls["Main scroll area"], "scroll", "down",
                )
                deadline = time.monotonic() + 5
                while await browser_value(
                    browser, "document.querySelector('[aria-label=\"Main scroll area\"]').scrollTop"
                ) == 0:
                    assert time.monotonic() < deadline, "The main document did not scroll"
                    await asyncio.sleep(0.05)

                nested = perceived["result"].get("nestedFrames", [])
                assert [frame["frame"] for frame in nested] == [
                    "iframe#same-frame",
                    "iframe#cross-frame",
                ]
                frame_perceived = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "frame-read",
                        "scope": {
                            "frames": [{"frame": ["iframe#same-frame"], "include": ["body"]}]
                        },
                    },
                )
                assert frame_perceived["type"] == "perceive-result", frame_perceived
                assert frame_perceived["targets"][0]["name"] == "Frame action"
                frame_request = {
                    "ref": frame_perceived["targets"][0]["ref"],
                    "action": "click",
                }
                frame_issued = await client.post(
                    f"/api/browser-agent/sessions/{session_id}/grants",
                    headers=headers,
                    json={"request": frame_request},
                )
                assert frame_issued.status_code == 200, frame_issued.text
                frame_grant = frame_issued.json()["grant"]
                frame_executed = await call(
                    ws,
                    {
                        "type": "execute",
                        "requestId": "frame-click",
                        "request": frame_request,
                        "grant": frame_grant,
                    },
                )
                assert frame_executed["outcome"]["ok"] is True, frame_executed
                assert (
                    await browser_value(
                        browser,
                        "document.getElementById('same-frame').contentWindow.frameClicks || 0",
                    )
                    == 1
                )
                frame_replay = await call(
                    ws,
                    {
                        "type": "execute",
                        "requestId": "frame-replay",
                        "request": frame_request,
                        "grant": frame_grant,
                    },
                )
                assert frame_replay["code"] == "PAGE_ACTION_STALE_REF", frame_replay

                frame_controls = {
                    target.get("name"): target["ref"]
                    for target in frame_perceived["targets"]
                }
                for action, name, value in (
                    ("fill", "Frame search", "Frame query"),
                    ("select", "Frame count", "20"),
                ):
                    await perform_action(
                        client, ws, headers, session_id, f"same-frame-{action}",
                        frame_controls[name], action, value,
                    )
                assert await browser_value(
                    browser,
                    "({value: document.getElementById('same-frame').contentDocument.querySelector('[aria-label=\"Frame search\"]').value, selected: document.getElementById('same-frame').contentDocument.querySelector('select').value})",
                ) == {"value": "Frame query", "selected": "20"}
                await perform_action(
                    client, ws, headers, session_id, "same-frame-scroll",
                    frame_controls["Frame scroll area"], "scroll", "down",
                )
                deadline = time.monotonic() + 5
                while await browser_value(
                    browser, "document.getElementById('same-frame').contentDocument.querySelector('[aria-label=\"Frame scroll area\"]').scrollTop"
                ) == 0:
                    assert time.monotonic() < deadline, "The same-origin frame did not scroll"
                    await asyncio.sleep(0.05)

                stale_grant = (
                    await client.post(
                        f"/api/browser-agent/sessions/{session_id}/grants",
                        headers=headers,
                        json={"request": frame_request},
                    )
                ).json()["grant"]
                await browser_value(
                    browser,
                    "document.getElementById('same-frame').contentWindow.location.assign('/frame-next')",
                )
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    frame_path = await browser_value(
                        browser,
                        "document.getElementById('same-frame').contentWindow.location.pathname",
                    )
                    if frame_path == "/frame-next":
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("The child frame did not navigate")
                stale_frame = await call(
                    ws,
                    {
                        "type": "execute",
                        "requestId": "stale-frame",
                        "request": frame_request,
                        "grant": stale_grant,
                    },
                )
                assert stale_frame["code"] == "PAGE_ACTION_STALE_REF", stale_frame
                assert (
                    await browser_value(
                        browser,
                        "document.getElementById('same-frame').contentWindow.frameClicks || 0",
                    )
                    == 0
                )

                cross_url = f"http://localhost:{fixture.server_port}/frame?cross=1"
                oopif_perceived = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "oopif-read",
                        "scope": {
                            "frames": [{"frame": ["iframe#cross-frame"], "include": ["body"]}]
                        },
                    },
                )
                if oopif_perceived.get("type") != "perceive-result":
                    tree = await browser._internal.client.send("Page.getFrameTree")  # noqa: SLF001
                    targets = await browser._internal.client.send("Target.getTargets")  # noqa: SLF001
                    owners = []
                    for child in tree.get("frameTree", {}).get("childFrames", []):
                        frame = child.get("frame", {})
                        try:
                            owner = await browser._internal.client.send(  # noqa: SLF001
                                "DOM.getFrameOwner", {"frameId": frame.get("id")}
                            )
                        except Exception as error:
                            owner = {"errorType": type(error).__name__, "error": str(error)}
                        owners.append({"frame": frame, "owner": owner})
                    (OUT / "oopif-target-diagnostics.json").write_text(
                        json.dumps({"reply": oopif_perceived, "owners": owners, "targets": targets}, indent=2),
                        encoding="utf-8",
                    )
                assert oopif_perceived["type"] == "perceive-result", oopif_perceived
                assert oopif_perceived["result"]["page"]["url"] == cross_url
                oopif_ref = oopif_perceived["targets"][0]["ref"]
                frame_key = browser_agent_registry.get(session_id, user_id).refs[oopif_ref].frame
                assert frame_key.startswith("oopif:")
                oopif_request = {"ref": oopif_ref, "action": "click"}
                oopif_issued = await client.post(
                    f"/api/browser-agent/sessions/{session_id}/grants",
                    headers=headers,
                    json={"request": oopif_request},
                )
                assert oopif_issued.status_code == 200, oopif_issued.text
                oopif_grant = oopif_issued.json()["grant"]
                oopif_executed = await call(
                    ws,
                    {
                        "type": "execute",
                        "requestId": "oopif-click",
                        "request": oopif_request,
                        "grant": oopif_grant,
                    },
                )
                assert oopif_executed["outcome"]["ok"] is True, oopif_executed
                assert await oopif_value(browser, frame_key, "window.frameClicks || 0") == 1
                oopif_replay = await call(
                    ws,
                    {
                        "type": "execute",
                        "requestId": "oopif-replay",
                        "request": oopif_request,
                        "grant": oopif_grant,
                    },
                )
                assert oopif_replay["code"] == "PAGE_ACTION_STALE_REF", oopif_replay
                assert await oopif_value(browser, frame_key, "window.frameClicks || 0") == 1

                oopif_controls = {
                    target.get("name"): target["ref"]
                    for target in oopif_perceived["targets"]
                }
                for action, name, value in (
                    ("fill", "Frame search", "Cross query"),
                    ("select", "Frame count", "20"),
                ):
                    await perform_action(
                        client, ws, headers, session_id, f"oopif-{action}",
                        oopif_controls[name], action, value,
                    )
                assert await oopif_value(
                    browser, frame_key,
                    "({value: document.querySelector('input').value, selected: document.querySelector('select').value})",
                ) == {"value": "Cross query", "selected": "20"}
                await perform_action(
                    client, ws, headers, session_id, "oopif-scroll",
                    oopif_controls["Frame scroll area"], "scroll", "down",
                )
                deadline = time.monotonic() + 5
                while await oopif_value(
                    browser, frame_key,
                    "document.querySelector('[aria-label=\"Frame scroll area\"]').scrollTop",
                ) == 0:
                    assert time.monotonic() < deadline, "The OOPIF document did not scroll"
                    await asyncio.sleep(0.05)

                stale_oopif_grant = (
                    await client.post(
                        f"/api/browser-agent/sessions/{session_id}/grants",
                        headers=headers,
                        json={"request": oopif_request},
                    )
                ).json()["grant"]
                await browser_value(
                    browser,
                    f"document.getElementById('cross-frame').setAttribute('src', 'http://localhost:{fixture.server_port}/frame-next?cross=1')",
                )
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    targets = await browser._internal.client.send("Target.getTargets")  # noqa: SLF001
                    if any(
                        target.get("type") == "iframe"
                        and str(target.get("url") or "").endswith("/frame-next?cross=1")
                        for target in targets.get("targetInfos", [])
                    ):
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("The OOPIF document did not navigate")
                stale_oopif = await call(
                    ws,
                    {
                        "type": "execute",
                        "requestId": "stale-oopif",
                        "request": oopif_request,
                        "grant": stale_oopif_grant,
                    },
                )
                assert stale_oopif["code"] == "PAGE_ACTION_STALE_REF", stale_oopif

                frame_next = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "oopif-next-read",
                        "scope": {
                            "frames": [{"frame": ["iframe#cross-frame"], "include": ["body"]}]
                        },
                    },
                )
                assert frame_next["type"] == "perceive-result", frame_next
                assert frame_next["result"]["page"]["url"] == (
                    f"http://localhost:{fixture.server_port}/frame-next?cross=1"
                )

                await browser.navigate(f"{fixture_url}next")
                next_perceived = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "main-next-read",
                        "scope": {"include": ["body"]},
                    },
                )
                assert next_perceived["result"]["page"]["url"] == f"{fixture_url}next"
                await perform_action(
                    client, ws, headers, session_id, "main-back",
                    "@document", "back",
                )
                assert await browser_value(browser, "location.href") == fixture_url
                returned_perceived = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "main-return-read",
                        "scope": {"include": ["body"]},
                    },
                )
                request = {
                    "ref": next(
                        target["ref"]
                        for target in returned_perceived["targets"]
                        if target.get("name") == "Run once"
                    ),
                    "action": "click",
                }
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

                returned = await client.post(
                    f"/api/browser/sessions/{profile}/handoff",
                    headers=headers,
                    json={"target": "agent", "reason": "P2 target acceptance"},
                )
                assert returned.status_code == 200, returned.text
                await browser_value(
                    browser,
                    "document.body.insertAdjacentHTML('beforeend', '<a id=\"new-window\" href=\"/child\" target=\"_blank\">Open child</a>')",
                )
                target_perceived = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "target-read",
                        "scope": {"include": ["body"]},
                    },
                )
                link = next(
                    target
                    for target in target_perceived["targets"]
                    if target.get("name") == "Open child"
                )
                original_target = browser_agent_registry.get(session_id, user_id).model_target
                target_request = {"ref": link["ref"], "action": "click"}
                target_issued = await client.post(
                    f"/api/browser-agent/sessions/{session_id}/grants",
                    headers=headers,
                    json={"request": target_request},
                )
                assert target_issued.status_code == 200, target_issued.text
                target_grant = target_issued.json()["grant"]
                target_executed = await call(
                    ws,
                    {
                        "type": "execute",
                        "requestId": "target-click",
                        "request": target_request,
                        "grant": target_grant,
                    },
                )
                assert target_executed["outcome"]["ok"] is True, target_executed
                model_target = browser_agent_registry.get(session_id, user_id).model_target
                assert model_target != original_target
                child_perceived = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "child-read",
                        "scope": {"include": ["body"]},
                    },
                )
                assert child_perceived["type"] == "perceive-result", child_perceived
                assert child_perceived["result"]["page"]["url"] == f"{fixture_url}child"
                assert any(
                    target.get("name") == "Child action"
                    for target in child_perceived["targets"]
                )
                child_next = next(
                    target["ref"] for target in child_perceived["targets"]
                    if target.get("name") == "Child next"
                )
                await perform_action(
                    client, ws, headers, session_id, "child-next",
                    child_next, "click",
                )
                child_next_perceived = await call(
                    ws,
                    {
                        "type": "perceive",
                        "requestId": "child-next-read",
                        "scope": {"include": ["body"]},
                    },
                )
                assert child_next_perceived["result"]["page"]["url"] == f"{fixture_url}next"
                await perform_action(
                    client, ws, headers, session_id, "model-target-back",
                    "@document", "back",
                )
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    child_after_back = await call(
                        ws,
                        {
                            "type": "perceive",
                            "requestId": "child-back-read",
                            "scope": {"include": ["body"]},
                        },
                    )
                    if child_after_back.get("result", {}).get("page", {}).get("url") == f"{fixture_url}child":
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("The model target did not navigate back")
                assert browser_agent_registry.get(session_id, user_id).model_target == model_target
                assert await browser_value(browser, "location.href") == f"{fixture_url}next"

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
            action_rows = [json.loads(row.payload or "{}") for row in audit]
            main_grant_digest = hashlib.sha256(grant.encode("utf-8")).hexdigest()
            main_audit = next(
                row for row in action_rows
                if row.get("grantDigest") == main_grant_digest and row.get("ok") is True
            )
            assert main_audit["documentTarget"] == original_target
            assert main_audit["ref"]
            assert all(row.get("document") for row in action_rows)
            assert all(grant not in (row.payload or "") for row in audit)
            return {
                "status": "passed",
                "checks": [
                    "real Chromium main-document perception and sensitive-subtree filtering",
                    "main-document fill, select, scroll and back have real side effects",
                    "same-origin frame read/click and child-navigation grant invalidation",
                    "same-origin frame fill, select and scroll have real side effects",
                    "OOPIF attach, default execution context, scoped click and stale-grant rejection",
                    "OOPIF fill, select and scroll have real side effects; back is document-scoped",
                    "target=_blank moves modelTarget into the new page without losing perception",
                    "back after target=_blank changes only modelTarget history, not the base page",
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
