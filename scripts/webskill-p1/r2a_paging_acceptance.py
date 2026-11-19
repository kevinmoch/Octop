"""R2A live perception-paging acceptance on a large page."""

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

from octop_browser.cdp.launcher import find_chrome
from playwright.async_api import async_playwright
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18089")
MODEL_URL = os.environ.get("WEBSKILL_P1_MODEL_URL", "http://localhost:1234/v1")
MODEL_NAME = os.environ.get("WEBSKILL_P1_MODEL_NAME", "qwen3.5:9b")


def _segment_indexes(value: Any) -> list[int]:
    indexes: list[int] = []
    if isinstance(value, str):
        try:
            return _segment_indexes(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            return []
    if isinstance(value, list):
        for item in value:
            indexes.extend(_segment_indexes(item))
        return indexes
    if isinstance(value, dict):
        segment = value.get("segment")
        if isinstance(segment, dict) and isinstance(segment.get("index"), int):
            indexes.append(segment["index"])
        for item in value.values():
            indexes.extend(_segment_indexes(item))
    return indexes


def _cursor_calls(value: Any) -> bool:
    if isinstance(value, str):
        try:
            return _cursor_calls(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            return False
    if isinstance(value, list):
        return any(_cursor_calls(item) for item in value)
    if isinstance(value, dict):
        if value.get("role") == "assistant":
            for call in value.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict) or function.get("name") != "perceive_page":
                    continue
                try:
                    arguments = json.loads(str(function.get("arguments") or "{}"))
                except json.JSONDecodeError:
                    continue
                if isinstance(arguments, dict) and isinstance(arguments.get("cursor"), str):
                    return True
        return any(_cursor_calls(item) for item in value.values())
    return False


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        controls = "".join(
            f"<button>row {index:03d} " + ("x" * 1400) + "</button>" for index in range(500)
        )
        body = f"<!doctype html><title>Large</title><h1>Large page</h1>{controls}"
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


async def _idle(frame: Any, timeout: float = 240.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        composer = frame.locator("textarea:visible").last
        if await composer.count() and await composer.is_enabled():
            return
        await asyncio.sleep(0.25)


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
    await fields.nth(0).fill("Local Qwen R2A paging")
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


async def run() -> dict[str, Any]:
    errors: list[str] = []
    segment_indexes: set[int] = set()
    cursor_seen = False
    model_requests = 0
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{fixture.server_port}/large"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(safe_error(error)))

        def request_record(request: Any) -> None:
            nonlocal cursor_seen, model_requests
            if "/chat/completions" not in request.url:
                return
            model_requests += 1
            body = request.post_data or ""
            segment_indexes.update(_segment_indexes(body))
            if _cursor_calls(body):
                cursor_seen = True

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
            address = page.get_by_placeholder("输入网址或 https://…")
            await address.fill(url)
            await address.press("Enter")
            await page.wait_for_timeout(2000)
            handoff = page.get_by_role("button", name="交还助手", exact=True)
            if await handoff.count() and await handoff.is_visible():
                await handoff.click()
            await page.wait_for_timeout(400)
            await _configure(frame, page)
            handoff = page.get_by_role("button", name="交还助手", exact=True)
            if await handoff.count() and await handoff.is_visible():
                await handoff.click()
            for _attempt in range(2):
                composer = frame.locator("textarea:visible").last
                await composer.fill(
                    "Call perceive_page once on the current large page. "
                    "If the result has segment.cursor, call perceive_page again "
                    "with exactly that cursor. Do not continue beyond the second "
                    "segment. Reply PERCEIVED_SECOND."
                )
                await composer.press("Enter")
                await page.wait_for_timeout(500)
                await _idle(frame, timeout=300)
                if 2 in segment_indexes:
                    break
            body = (await frame.locator("body").inner_text())[-3000:]
            first = 1 in segment_indexes
            second = 2 in segment_indexes
            return {
                "status": "passed" if (first and second and cursor_seen) else "failed",
                "model": MODEL_NAME,
                "pagingSegmentObserved": first,
                "pagingContinuationObserved": second,
                "cursorCallObserved": cursor_seen,
                "segmentIndexes": sorted(segment_indexes),
                "modelRequests": model_requests,
                "pageErrors": errors,
                "body": body,
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
                "pagingSegmentObserved": 1 in segment_indexes,
                "pagingContinuationObserved": 2 in segment_indexes,
                "cursorCallObserved": cursor_seen,
                "segmentIndexes": sorted(segment_indexes),
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
    (OUT / "ui-result-r2a-paging.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
