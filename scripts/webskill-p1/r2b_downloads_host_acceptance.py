"""Host-session download acceptance: direct page click, then Chatbot model reads."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import threading
import time
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
MODEL_URL = os.environ.get("WEBSKILL_P1_MODEL_URL", "http://localhost:1234/v1")
MODEL_NAME = os.environ.get("WEBSKILL_P1_MODEL_NAME", "qwen3.5:9b")


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/report.txt":
            payload = b"host download body\n"
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
          return {status: response.status, body: await response.json()};
        }""",
        {"method": method, "path": path, "body": body},
    )
    if result["status"] < 200 or result["status"] >= 300:
        raise RuntimeError(f"API {method} {path} -> {result['status']}: {result['body']}")
    return result["body"]


async def _configure(frame: Any, page: Any) -> None:
    await frame.get_by_role("button", name="控制台", exact=True).click()
    await page.wait_for_timeout(700)
    await page.keyboard.press("Control+k")
    palette = frame.locator("input").last
    await palette.fill("大模型")
    await frame.get_by_role("option", name="连接 / 大模型").click()
    await frame.get_by_test_id("console-model-add").click()
    dialog = frame.get_by_test_id("console-edit-dialog-model")
    fields = dialog.locator("input")
    await fields.nth(0).fill("Local Qwen R2B downloads host")
    await fields.nth(1).fill(MODEL_NAME)
    await fields.nth(2).fill(MODEL_URL)
    await frame.get_by_test_id("console-edit-dialog-model-save").click()
    await frame.get_by_role("button", name="助手", exact=True).click()
    await page.wait_for_timeout(500)
    if await frame.locator(".local-model summary").count():
        await frame.locator(".local-model summary").click()
        box = frame.locator('.local-model input[type="checkbox"]')
        if await box.count() and not await box.is_checked():
            await box.check()


async def _allow(frame: Any, timeout: float = 240.0) -> None:
    deadline = time.monotonic() + timeout
    quiet: float | None = None
    while time.monotonic() < deadline:
        allow = frame.get_by_role("button", name="允许", exact=True)
        if await allow.count():
            try:
                if await allow.first.is_visible():
                    await allow.first.click()
                    quiet = None
                    await asyncio.sleep(0.5)
                    continue
            except Exception:
                pass
        composer = frame.locator("textarea:visible").last
        if await composer.count() and await composer.is_enabled():
            if quiet is None:
                quiet = time.monotonic()
            elif time.monotonic() - quiet >= 1.5:
                return
        else:
            quiet = None
        await asyncio.sleep(0.25)


async def _download_click(page: Any, session_id: str) -> None:
    ticket = (await _api(page, "POST", f"/api/browser-agent/sessions/{session_id}/tickets"))[
        "ticket"
    ]
    ws_url = f"{BASE_URL.replace('http://', 'ws://')}/api/browser-agent/ws?ticket={ticket}"
    async with websockets.connect(ws_url, origin=BASE_URL) as ws:
        await ws.send(
            json.dumps({"type": "perceive", "requestId": "p1", "scope": {"include": ["body"]}})
        )
        perceived = json.loads(await ws.recv())
        target = next(
            (
                item
                for item in perceived.get("targets", [])
                if item.get("name") == "Download report"
            ),
            None,
        )
        if target is None:
            raise RuntimeError(f"download link missing: {perceived}")
        request = {"ref": target["ref"], "action": "click"}
        grant = (
            await _api(
                page,
                "POST",
                f"/api/browser-agent/sessions/{session_id}/grants",
                {"request": request},
            )
        )["grant"]
        await ws.send(
            json.dumps({"type": "execute", "requestId": "e1", "request": request, "grant": grant})
        )
        executed = json.loads(await ws.recv())
        if not executed.get("outcome", {}).get("ok"):
            raise RuntimeError(f"click failed: {executed}")


async def run() -> dict[str, Any]:
    errors: list[str] = []
    session_id: str | None = None
    downloads: list[dict[str, Any]] = []
    model_requests = 0
    download_text_observed = False
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{fixture.server_port}/"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(safe_error(error)))

        async def response_record(response: Any) -> None:
            nonlocal session_id, downloads
            if response.request.method == "POST" and response.url.endswith(
                "/api/browser-agent/sessions"
            ):
                with contextlib.suppress(Exception):
                    session_id = str((await response.json())["sessionId"])
            if response.url.endswith("/downloads"):
                with contextlib.suppress(Exception):
                    payload = await response.json()
                    if isinstance(payload.get("downloads"), list):
                        downloads = payload["downloads"]

        def request_record(request: Any) -> None:
            nonlocal model_requests, download_text_observed
            if "/chat/completions" in request.url:
                model_requests += 1
                if "host download body" in (request.post_data or ""):
                    download_text_observed = True

        page.on("response", lambda response: asyncio.create_task(response_record(response)))
        page.on("request", request_record)
        frame: Any = None
        try:
            await _login(page)
            await page.goto(f"{BASE_URL}/browser", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            start = page.get_by_role("button", name="启动浏览器")
            if await start.count():
                await start.click()
            await page.wait_for_timeout(2000)
            frame = next((item for item in page.frames if "/webskill/" in item.url), None)
            assert frame is not None
            await frame.locator(".host-header").wait_for(timeout=30_000)
            await _configure(frame, page)
            await frame.evaluate(
                """() => {
                  const key = Object.keys(localStorage).find(
                    (item) => item.startsWith('octop.webskill.runtime.'),
                  );
                  if (!key) throw new Error('runtime config key missing');
                  const config = JSON.parse(localStorage.getItem(key));
                  config.sandbox.downloadedFiles = true;
                  localStorage.setItem(key, JSON.stringify(config));
                }"""
            )
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            frame = next((item for item in page.frames if "/webskill/" in item.url), None)
            assert frame is not None
            await frame.locator(".host-header").wait_for(timeout=30_000)
            address = page.get_by_placeholder("输入网址或 https://…")
            await address.fill(url)
            await address.press("Enter")
            await page.wait_for_timeout(2000)
            handoff = page.get_by_role("button", name="交还助手", exact=True)
            if await handoff.count() and await handoff.is_visible():
                await handoff.click()
                await page.wait_for_timeout(400)
            assert session_id is not None
            await _download_click(page, session_id)
            await page.wait_for_timeout(2000)
            listed = await _api(page, "GET", f"/api/browser-agent/sessions/{session_id}/downloads")
            downloads = listed.get("downloads", [])
            assert downloads, "host download list empty"

            composer = frame.locator("textarea:visible").last
            await composer.fill(
                "Call list_downloaded_files. Find report.txt, then call "
                "read_downloaded_file for its id. Reply DOWNLOAD_READ."
            )
            await composer.press("Enter")
            for _attempt in range(8):
                await _allow(frame, timeout=8)
                await page.wait_for_timeout(300)
                if download_text_observed:
                    break
            body = (await frame.locator("body").inner_text())[-2500:]
            return {
                "status": "passed" if download_text_observed else "failed",
                "model": MODEL_NAME,
                "sessionId": session_id,
                "downloads": downloads,
                "downloadTextObserved": download_text_observed,
                "modelRequests": model_requests,
                "pageErrors": errors,
                "body": body,
            }
        except Exception as error:
            body = ""
            with contextlib.suppress(Exception):
                if frame is not None:
                    body = (await frame.locator("body").inner_text())[-2500:]
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "sessionId": session_id,
                "downloads": downloads,
                "downloadTextObserved": download_text_observed,
                "modelRequests": model_requests,
                "pageErrors": errors,
                "body": body,
            }
        finally:
            await browser.close()
            fixture.shutdown()
            fixture.server_close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-r2b-downloads-host.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
