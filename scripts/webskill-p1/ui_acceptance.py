"""Real client-browser verification for the isolated P1 UI server."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from octop_browser.cdp.launcher import find_chrome
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
OUT.mkdir(parents=True, exist_ok=True)
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18088")


class CounterServer(ThreadingHTTPServer):
    clicks = 0


class CounterHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def respond(self, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/count":
            self.respond(json.dumps({"count": self.server.clicks}), "application/json")
            return
        self.respond(
            "<!doctype html><html><body><button onclick=\"fetch('/click', {method:'POST'})\">Run once</button></body></html>"
        )

    def do_POST(self) -> None:
        if self.path != "/click":
            self.send_error(404)
            return
        self.server.clicks += 1
        self.respond(json.dumps({"count": self.server.clicks}), "application/json")


async def run() -> dict[str, Any]:
    errors: list[str] = []
    expected_ticket_errors: list[str] = []
    websocket_queries: list[str] = []

    def record_websocket(socket: Any) -> None:
        parsed = urlsplit(socket.url)
        if parsed.path.endswith(("/browser-stream/ws", "/browser-agent/ws")):
            websocket_queries.extend(parse_qs(parsed.query).keys())

    def record_console(message: Any) -> None:
        if message.type != "error":
            return
        url = message.location.get("url", "")
        if (
            "/api/browser-agent/sessions/" in url
            and url.endswith("/tickets")
            and "404" in message.text
        ):
            expected_ticket_errors.append(message.text[:500])
            return
        errors.append(message.text[:500])

    counter = CounterServer(("127.0.0.1", 0), CounterHandler)
    threading.Thread(target=counter.serve_forever, daemon=True).start()
    fixture_url = f"http://127.0.0.1:{counter.server_port}/"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=find_chrome(), headless=True)
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            ignore_https_errors=os.environ.get("WEBSKILL_P1_ALLOW_SELF_SIGNED") == "1",
        )
        page.on("pageerror", lambda error: errors.append((error.stack or str(error))[:2000]))
        page.on("console", record_console)
        page.on("websocket", record_websocket)
        try:
            await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            if await page.locator("input").count() >= 2:
                await page.locator("input").nth(0).fill("admin")
                await page.locator("input").nth(1).fill("TestPass12")
                thumb = page.get_by_test_id("slide-captcha-thumb")
                box = await thumb.bounding_box()
                assert box is not None
                track = await page.get_by_test_id("slide-captcha-track").bounding_box()
                assert track is not None
                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await page.mouse.down()
                await page.mouse.move(
                    track["x"] + track["width"], box["y"] + box["height"] / 2, steps=12
                )
                await page.mouse.up()
                await page.get_by_role("button", name=re.compile("登")).click()
                await page.wait_for_url(re.compile(r"/chat/"))
            menu = page.get_by_role("button", name="浏览器", exact=True)
            if not await menu.is_visible():
                await page.get_by_role("button", name="控制", exact=True).click()
            await menu.click()
            await page.wait_for_url(f"{BASE_URL}/browser")
            menu_label = await menu.inner_text()
            dismiss = page.get_by_role("button", name="稍后再说")
            if await dismiss.is_visible():
                await dismiss.click()

            created_session_ids: list[str] = []
            expired_ticket = True

            async def observe_session_create(route: Any) -> None:
                response = await route.fetch()
                if response.ok:
                    body = await response.json()
                    created_session_ids.append(body["sessionId"])
                await route.fulfill(response=response)

            async def expire_first_ticket(route: Any) -> None:
                nonlocal expired_ticket
                if expired_ticket:
                    expired_ticket = False
                    await route.fulfill(
                        status=404,
                        content_type="application/json",
                        body=json.dumps(
                            {"error": {"code": "NOT_FOUND", "message": "Session expired"}}
                        ),
                    )
                    return
                await route.continue_()

            await page.route("**/api/browser-agent/sessions", observe_session_create)
            await page.route("**/api/browser-agent/sessions/*/tickets", expire_first_ticket)
            await page.get_by_role("button", name="启动浏览器").click()
            frame_element = page.locator('iframe[title="WebSkill"]')
            await frame_element.wait_for(state="attached", timeout=30_000)
            await page.wait_for_timeout(2000)
            frame = next((frame for frame in page.frames if "/webskill/" in frame.url), None)
            assert frame is not None, "The WebSkill iframe did not load"
            await frame.locator(".host-header").wait_for(timeout=20_000)
            assert len(set(created_session_ids)) == 2, created_session_ids
            text = await frame.locator("body").inner_text()
            assert "助手" in text
            assert "控制台" in text
            await page.get_by_role("button", name="admin", exact=True).click()
            dark_button = page.locator("button:has(svg.lucide-moon)")
            await dark_button.wait_for(state="visible")
            await dark_button.click()
            await frame.locator('.host-shell[data-theme="dark"]').wait_for(timeout=10_000)

            await frame.get_by_role("button", name="控制台", exact=True).click()
            await frame.locator(".webskill-console-root").wait_for(timeout=30_000)
            await page.keyboard.press("Control+k")
            palette_input = frame.locator("input").last
            await palette_input.fill("大模型")
            await frame.get_by_role("option", name="连接 / 大模型").click()
            await frame.get_by_test_id("console-model-add").click()
            dialog = frame.get_by_test_id("console-edit-dialog-model")
            fields = dialog.locator("input")
            await fields.nth(0).fill("Local Qwen")
            await fields.nth(1).fill("qwen3.5:9b")
            await fields.nth(2).fill("http://127.0.0.1:1234/v1")
            await frame.get_by_test_id("console-edit-dialog-model-save").click()
            await frame.get_by_role("button", name="助手", exact=True).click()
            await frame.locator(".local-model summary").click()
            await frame.locator('.local-model input[type="checkbox"]').check()
            if os.environ.get("WEBSKILL_P1_LNA_DENY") == "1":
                permission_states = await page.evaluate("""async () => {
                    const states = {};
                    for (const name of ['local-network', 'loopback-network']) {
                        try { states[name] = (await navigator.permissions.query({name})).state; }
                        catch { states[name] = 'unsupported'; }
                    }
                    return states;
                }""")
                if "unsupported" in permission_states.values():
                    return {"status": "unsupported", "permissionStates": permission_states}
                if os.environ.get("WEBSKILL_P1_LNA_POLICY") != "1":
                    cdp = await page.context.new_cdp_session(page)
                    for name in permission_states:
                        await cdp.send(
                            "Browser.setPermission",
                            {"permission": {"name": name}, "setting": "denied", "origin": BASE_URL},
                        )
                if os.environ.get("WEBSKILL_P1_LNA_POLICY") == "1":
                    cdp = await page.context.new_cdp_session(page)
                    await cdp.send(
                        "Browser.setPermission",
                        {
                            "permission": {"name": "local-network-access"},
                            "setting": "denied",
                            "origin": BASE_URL,
                        },
                    )
                denied_states = await page.evaluate("""async () => {
                    const states = {};
                    for (const name of ['local-network', 'loopback-network']) {
                        states[name] = (await navigator.permissions.query({name})).state;
                    }
                    return states;
                }""")
                if os.environ.get("WEBSKILL_P1_LNA_POLICY") == "1":
                    response = await page.request.get(f"{BASE_URL}/webskill/")
                    policy = response.headers.get("permissions-policy", "")
                    assert "local-network=()" in policy and "loopback-network=()" in policy
                else:
                    assert set(denied_states.values()) == {"denied"}, denied_states
                denied_composer = frame.locator("textarea:visible").last
                await denied_composer.fill(
                    "Use the configured local model. Do not use another provider."
                )
                await denied_composer.press("Enter")
                alert = frame.locator('[role="alert"]').last
                await alert.wait_for(timeout=60_000)
                alert_text = await alert.inner_text()
                assert "LLM_REQUEST_FAILED" in alert_text, alert_text
                assert await frame.get_by_text("P1_MODEL_OK", exact=True).count() == 0
                return {
                    "status": "passed",
                    "lnaDenied": True,
                    "permissionStates": denied_states,
                    "structuredErrorVisible": True,
                }
            composer = frame.locator("textarea:visible").last
            await composer.fill("Reply with exactly P1_MODEL_OK. Do not use any tools.")
            await composer.press("Enter")
            await (
                frame.get_by_test_id("assistant-ui-assistant-message")
                .get_by_text("P1_MODEL_OK", exact=True)
                .wait_for(timeout=90_000)
            )
            if os.environ.get("WEBSKILL_P1_UI_DEBUG") == "1":
                return {
                    "status": "diagnostic",
                    "frameText": await frame.locator("body").inner_text(),
                    "actionContainers": await frame.locator(
                        '[data-testid="assistant-ui-message-actions"]'
                    ).count(),
                    "pageErrors": errors,
                }

            await frame.get_by_test_id("header-settings").click()
            await frame.locator(".webskill-console-root").wait_for(timeout=20_000)
            await page.keyboard.press("Control+k")
            await frame.locator("input").last.fill("运行记录")
            await frame.get_by_role("option", name="运行 / 运行记录").click()
            await frame.locator('button[aria-label^="Inspect "]').first.click()
            await frame.locator(".webskill-console-root").wait_for(timeout=20_000)
            open_inspector = frame.get_by_role("button", name="打开运行详情")
            if await open_inspector.is_visible():
                await open_inspector.click()
            await frame.get_by_text("llm.response", exact=True).first.wait_for(
                state="attached", timeout=30_000
            )
            trace_text = await frame.locator("body").text_content()
            assert "llm.response" in trace_text, (
                "The model response event was not found in Console trace"
            )
            assert "llm.usage" in trace_text, "The model usage event was not found in Console trace"
            close_inspector = frame.get_by_role("button", name="Close panel", exact=True)
            if await close_inspector.is_visible():
                await close_inspector.click()

            await frame.get_by_role("button", name="助手", exact=True).click()
            address = page.get_by_placeholder("输入网址或 https://…")
            await address.fill(fixture_url)
            await address.press("Enter")
            await page.get_by_role("button", name="交还助手").click()
            composer = frame.locator("textarea:visible").last
            await composer.fill(
                "Use the page tools to click the button named Run once exactly once. Do not fill forms or open links. After the click, reply exactly CLICK_DONE."
            )
            await composer.press("Enter")
            confirmation = frame.get_by_role("button", name="允许", exact=True)
            await confirmation.wait_for(timeout=90_000)
            await confirmation.click()
            deadline = time.monotonic() + 15
            while counter.clicks < 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert counter.clicks == 1, f"Expected one page click, got {counter.clicks}"
            await frame.get_by_text("CLICK_DONE", exact=False).wait_for(timeout=90_000)
            certificate_errors = [
                error
                for error in errors
                if os.environ.get("WEBSKILL_P1_ALLOW_SELF_SIGNED") == "1"
                and (
                    "An SSL certificate error occurred when fetching the script" in error
                    or "[SW] Registration failed" in error
                )
            ]
            unexpected_errors = [error for error in errors if error not in certificate_errors]
            assert not unexpected_errors, unexpected_errors
            assert "token" not in websocket_queries
            assert len(expected_ticket_errors) == 1, expected_ticket_errors
            return {
                "status": "passed",
                "menu": f"Control > {menu_label}",
                "chatbotMounted": True,
                "consoleModelConfigured": True,
                "localNoAuthChat": "P1_MODEL_OK",
                "consoleTraceHasLlmResponse": True,
                "expiredTicketRecoveredWithNewSession": len(set(created_session_ids)) == 2,
                "expectedExpiredTicketResponses": len(expected_ticket_errors),
                "confirmedRealClickCount": counter.clicks,
                "pageErrors": unexpected_errors,
                "environmentCertificateErrors": certificate_errors,
                "webSocketQueryKeys": websocket_queries,
            }
        except Exception as error:
            await page.screenshot(path=str(OUT / "ui-failure.png"), full_page=True)
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": str(error)[:500],
                "pageErrors": errors,
                "fixtureClickCount": counter.clicks,
                "frames": [frame.url for frame in page.frames],
                "bodyText": (await page.locator("body").inner_text())[:2000],
            }
        finally:
            await browser.close()
            counter.shutdown()
            counter.server_close()


if __name__ == "__main__":
    result = asyncio.run(run())
    result_name = "ui-result-https.json" if BASE_URL.startswith("https://") else "ui-result.json"
    (OUT / result_name).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
