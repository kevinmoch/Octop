"""R2A live browser acceptance for tab workset and perception paging."""

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
        if self.path == "/detail":
            body = "<!doctype html><title>Detail</title><h1>Detail page</h1>"
        elif self.path == "/large":
            items = "".join(f"<li>row {index:04d}</li>" for index in range(2200))
            body = f"<!doctype html><title>Large</title><h1>Large page</h1><ul>{items}</ul>"
        else:
            body = (
                "<!doctype html><title>Origin</title><h1>Origin page</h1>"
                '<a href="/detail" target="_blank" rel="noopener">Open detail</a>'
                "<button>Run once</button>"
            )
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


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


async def _wait_idle(frame: Any, timeout: float = 240.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        composer = frame.locator("textarea:visible").last
        if await composer.count() and await composer.is_enabled():
            return
        await asyncio.sleep(0.25)


async def _finish_action(frame: Any, timeout: float = 240.0) -> None:
    deadline = time.monotonic() + timeout
    quiet_since: float | None = None
    while time.monotonic() < deadline:
        allow = frame.get_by_role("button", name="允许", exact=True)
        if await allow.count():
            try:
                if await allow.first.is_visible():
                    await allow.first.click()
                    quiet_since = None
                    await asyncio.sleep(0.5)
                    continue
            except Exception:
                pass
        composer = frame.locator("textarea:visible").last
        if await composer.count() and await composer.is_enabled():
            if quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= 1.5:
                return
        else:
            quiet_since = None
        await asyncio.sleep(0.25)


async def _send(frame: Any, text: str) -> None:
    composer = frame.locator("textarea:visible").last
    await composer.wait_for(state="visible", timeout=10_000)
    await composer.fill(text)
    await asyncio.sleep(0.2)
    send = frame.get_by_role("button", name="发送", exact=True)
    if await send.count() and await send.first.is_enabled():
        await send.first.click()
    else:
        await composer.press("Enter")
    await asyncio.sleep(0.6)


async def _prompt(frame: Any, text: str, timeout: float = 300.0) -> None:
    await _send(frame, text)
    await _wait_idle(frame, timeout=timeout)


async def _configure_model(frame: Any, page: Any) -> None:
    await frame.get_by_role("button", name="控制台", exact=True).click()
    await page.wait_for_timeout(700)
    await page.keyboard.press("Control+k")
    palette = frame.locator("input").last
    await palette.fill("大模型")
    await frame.get_by_role("option", name="连接 / 大模型").click()
    await frame.get_by_test_id("console-model-add").click()
    dialog = frame.get_by_test_id("console-edit-dialog-model")
    fields = dialog.locator("input")
    await fields.nth(0).fill("Local Qwen R2A")
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


async def _api_tabs(page: Any, session_id: str) -> list[dict[str, Any]]:
    payload = await page.evaluate(
        """async (sid) => {
          const token =
            localStorage.getItem('auth_token') ||
            sessionStorage.getItem('auth_token') ||
            '';
          const response = await fetch(
            `/api/browser-agent/sessions/${encodeURIComponent(sid)}/tabs`,
            {
              credentials: 'same-origin',
              headers: {
                'Accept': 'application/json',
                ...(token ? {Authorization: `Bearer ${token}`} : {}),
              },
            },
          );
          return {status: response.status, body: await response.json()};
        }""",
        session_id,
    )
    assert payload["status"] == 200, payload
    tabs = payload["body"].get("tabs")
    assert isinstance(tabs, list), payload
    return tabs


async def run() -> dict[str, Any]:
    errors: list[str] = []
    tabs_responses: list[list[dict[str, Any]]] = []
    session_id: str | None = None
    model_requests = 0
    paging_segment = False
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{fixture.server_port}/"

    frame: Any = None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(safe_error(error)))

        async def response_record(response: Any) -> None:
            nonlocal session_id, paging_segment
            if response.request.method == "POST" and response.url.endswith(
                "/api/browser-agent/sessions"
            ):
                with contextlib.suppress(Exception):
                    session_id = str((await response.json())["sessionId"])
            if "/api/browser-agent/sessions/" in response.url and response.url.endswith("/tabs"):
                try:
                    payload = await response.json()
                    tabs = payload.get("tabs")
                    if isinstance(tabs, list):
                        tabs_responses.append(tabs)
                except Exception:
                    pass

        def request_record(request: Any) -> None:
            nonlocal model_requests, paging_segment
            if "/chat/completions" in request.url:
                model_requests += 1
                body = request.post_data or ""
                if "segment" in body or "nodesRemaining" in body:
                    paging_segment = True

        page.on("response", lambda response: asyncio.create_task(response_record(response)))
        page.on("request", request_record)
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
            address = page.get_by_placeholder("输入网址或 https://…")
            await address.fill(base)
            await address.press("Enter")
            await page.wait_for_timeout(2000)
            handoff = page.get_by_role("button", name="交还助手", exact=True)
            if await handoff.count() and await handoff.is_visible():
                await handoff.click()
            await page.wait_for_timeout(500)
            await _configure_model(frame, page)
            handoff = page.get_by_role("button", name="交还助手", exact=True)
            if await handoff.count() and await handoff.is_visible():
                await handoff.click()
                await page.wait_for_timeout(400)
            assert session_id is not None

            await _prompt(frame, "Call list_page_tabs once. Reply TABS1 only.")
            assert tabs_responses, "the model did not call list_page_tabs"
            first = tabs_responses[-1]
            assert len(first) == 1, first
            await _send(
                frame,
                "Call act_on_page to click the link named 'Open detail'. "
                "It opens a new tab. Click exactly once. Reply OPENED.",
            )
            await _finish_action(frame)
            assert tabs_responses, "no tabs response after opening detail"
            detail_deadline = time.monotonic() + 30
            detail = None
            while time.monotonic() < detail_deadline:
                after_open = tabs_responses[-1]
                detail = next(
                    (tab for tab in after_open if tab.get("title") == "Detail"),
                    None,
                )
                if detail is not None:
                    break
                await asyncio.sleep(0.2)
            assert detail is not None, after_open

            await _prompt(
                frame,
                "Call list_page_tabs. Find the page titled 'Detail' and call "
                "focus_page_tab with its handle from that same result. Reply FOCUSED.",
            )
            await page.wait_for_timeout(500)
            focused = await _api_tabs(page, session_id)
            origin = next(tab for tab in focused if tab.get("title") == "Origin")
            assert origin["active"] is True, "focus changed the user's active page"

            await _prompt(
                frame,
                "Call list_page_tabs. Find the page titled 'Detail' and call "
                "close_page_tab with its handle from that same result. Do not "
                "reply CLOSED until the close tool returns success.",
            )
            await page.wait_for_timeout(500)
            await _prompt(frame, "Call list_page_tabs once. Reply TABS_FINAL.")
            final_tabs = await _api_tabs(page, session_id)
            assert len(final_tabs) == 1, final_tabs

            await address.fill(f"{base}large")
            await address.press("Enter")
            await page.wait_for_timeout(1500)
            handoff = page.get_by_role("button", name="交还助手", exact=True)
            if await handoff.count() and await handoff.is_visible():
                await handoff.click()
            for _attempt in range(2):
                await _prompt(
                    frame,
                    "Call perceive_page once on the current large page. "
                    "If the result has a segment cursor, do not continue. "
                    "Do not reply until the tool returns.",
                    timeout=300,
                )
                if paging_segment:
                    break
            assert model_requests > 0
            assert paging_segment, "large-page tool result did not expose a paging segment"
            return {
                "status": "passed",
                "model": MODEL_NAME,
                "modelKind": "external",
                "fixture": base,
                "tabListRequests": len(tabs_responses),
                "initialTabCount": len(first),
                "openedDetail": True,
                "focusKeptUserActivePage": True,
                "detailClosed": True,
                "pagingSegmentObserved": paging_segment,
                "modelRequests": model_requests,
                "pageErrors": errors,
            }
        except Exception as error:
            body = ""
            try:
                if frame is not None:
                    body = (await frame.locator("body").inner_text())[-3000:]
            except Exception:
                pass
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "model": MODEL_NAME,
                "modelRequests": model_requests,
                "tabListRequests": len(tabs_responses),
                "tabsResponses": tabs_responses[-3:],
                "pagingSegmentObserved": paging_segment,
                "body": body,
                "pageErrors": errors,
            }
        finally:
            await browser.close()
            fixture.shutdown()
            fixture.server_close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-r2a-tabs-paging.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
