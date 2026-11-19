"""Deterministic real-browser download acceptance through the PageAgent API.

This bypasses the local model for the click, so a model-tool omission cannot
mask whether the server-side download capture, opaque ids and read path work.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import websockets
from octop_browser.cdp.launcher import find_chrome
from playwright.async_api import async_playwright
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18089")
ORIGIN = BASE_URL


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/report.txt":
            payload = b"download body from fixture\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", 'attachment; filename="report.txt"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        body = (
            b"<!doctype html><title>Downloads</title><h1>Downloads</h1>"
            b'<a href="/report.txt" download="report.txt">Download report</a>'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def _login(page: Any) -> None:
    await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    if await page.locator("input").count() >= 2:
        await page.locator("input").nth(0).fill("admin")
        await page.locator("input").nth(1).fill("TestPass12")
        thumb = page.get_by_test_id("slide-captcha-thumb")
        box = await thumb.bounding_box()
        track = await page.get_by_test_id("slide-captcha-track").bounding_box()
        if box and track:
            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await page.mouse.down()
            await page.mouse.move(
                track["x"] + track["width"], box["y"] + box["height"] / 2, steps=12
            )
            await page.mouse.up()
        await page.get_by_role("button", name=re.compile("登")).click()
        await page.wait_for_url(re.compile(r"/chat/"))


async def _api(page: Any, method: str, path: str, body: Any | None = None) -> dict[str, Any]:
    result = await page.evaluate(
        """async ({method, path, body}) => {
          const token = localStorage.getItem('auth_token') ||
            sessionStorage.getItem('auth_token') || '';
          const response = await fetch(path, {
            method,
            credentials: 'same-origin',
            headers: {
              'Accept': 'application/json',
              'Content-Type': 'application/json',
              ...(token ? {Authorization: `Bearer ${token}`} : {}),
              ...(method === 'GET' ? {} : {
                'Origin': window.location.origin,
                'Sec-Fetch-Site': 'same-origin',
              }),
            },
            ...(body === null ? {} : {body: JSON.stringify(body)}),
          });
          const text = await response.text();
          let json;
          try { json = JSON.parse(text); } catch { json = {raw: text}; }
          return {status: response.status, body: json};
        }""",
        {"method": method, "path": path, "body": body},
    )
    if result["status"] < 200 or result["status"] >= 300:
        raise RuntimeError(f"API {method} {path} returned {result['status']}: {result['body']}")
    return result["body"]


async def run() -> dict[str, Any]:
    errors: list[str] = []
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    fixture_url = f"http://127.0.0.1:{fixture.server_port}/"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(safe_error(error)))
        try:
            await _login(page)
            await page.goto(f"{BASE_URL}/browser", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            start = page.get_by_role("button", name="启动浏览器")
            if await start.count():
                await start.click()
            await page.wait_for_timeout(2000)
            address = page.get_by_placeholder("输入网址或 https://…")
            await address.fill(fixture_url)
            await address.press("Enter")
            await page.wait_for_timeout(2000)
            handoff = page.get_by_role("button", name="交还助手", exact=True)
            if await handoff.count() and await handoff.is_visible():
                await handoff.click()
                await page.wait_for_timeout(400)
            session = await _api(page, "POST", "/api/browser-agent/sessions", {})
            session_id = session["sessionId"]
            ticket = (
                await _api(
                    page,
                    "POST",
                    f"/api/browser-agent/sessions/{session_id}/tickets",
                )
            )["ticket"]
            ws_url = f"{BASE_URL.replace('http://', 'ws://')}/api/browser-agent/ws?ticket={ticket}"
            async with websockets.connect(ws_url, origin=ORIGIN) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "perceive",
                            "requestId": "p1",
                            "scope": {"include": ["body"]},
                        }
                    )
                )
                perceived = json.loads(await ws.recv())
                if perceived.get("type") != "perceive-result":
                    raise RuntimeError(f"perceive failed: {perceived}")
                target = next(
                    (
                        item
                        for item in perceived.get("targets", [])
                        if item.get("name") == "Download report"
                    ),
                    None,
                )
                if target is None:
                    raise RuntimeError(f"download link missing: {perceived.get('targets')}")
                ref = target["ref"]
                request = {"ref": ref, "action": "click"}
                grant = (
                    await _api(
                        page,
                        "POST",
                        f"/api/browser-agent/sessions/{session_id}/grants",
                        {"request": request},
                    )
                )["grant"]
                await ws.send(
                    json.dumps(
                        {
                            "type": "execute",
                            "requestId": "e1",
                            "request": request,
                            "grant": grant,
                        }
                    )
                )
                executed = json.loads(await ws.recv())
                if executed.get("type") != "execute-result" or not executed.get("outcome", {}).get(
                    "ok"
                ):
                    raise RuntimeError(f"click failed: {executed}")
            await page.wait_for_timeout(2500)
            listed = await _api(
                page,
                "GET",
                f"/api/browser-agent/sessions/{session_id}/downloads",
            )
            downloads = listed.get("downloads", [])
            if not downloads:
                raise RuntimeError("download list stayed empty")
            entry = next((item for item in downloads if item.get("name") == "report.txt"), None)
            if entry is None:
                raise RuntimeError(f"report.txt missing: {downloads}")
            read = await _api(
                page,
                "GET",
                f"/api/browser-agent/sessions/{session_id}/downloads/{entry['id']}/bytes",
            )
            data = base64.b64decode(read["data"])
            assert data == b"download body from fixture\n", data
            assert "path" not in entry
            return {
                "status": "passed",
                "sessionId": session_id,
                "downloads": downloads,
                "readBytes": len(data),
                "downloadClickConfirmed": True,
                "pageErrors": errors,
            }
        except Exception as error:
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "pageErrors": errors,
            }
        finally:
            await browser.close()
            fixture.shutdown()
            fixture.server_close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-r2b-downloads-backend.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
