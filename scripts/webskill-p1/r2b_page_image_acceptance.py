"""R2B live page-image capture through the real WebSkill host and local model."""

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

# 1x1 transparent PNG; the page renders it at a visible size for screenshot capture.
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/pixel.png":
            payload = __import__("base64").b64decode(PNG)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        body = (
            b"<!doctype html><title>Image</title><h1>Image page</h1>"
            b'<img src="/pixel.png" alt="Tiny pixel" width="240" height="160">'
            b'<button style="width:240px;height:160px">Visible button</button>'
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


async def _idle(frame: Any, timeout: float = 180.0) -> None:
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
    await fields.nth(0).fill("Local Qwen R2B image")
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
    model_requests = 0
    image_result: dict[str, Any] | None = None
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{fixture.server_port}/"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(safe_error(error)))

        def request_record(request: Any) -> None:
            nonlocal model_requests
            if "/chat/completions" in request.url:
                model_requests += 1

        def websocket_record(socket: Any) -> None:
            def on_frame(payload: Any) -> None:
                nonlocal image_result
                if not isinstance(payload, str):
                    return
                try:
                    message = json.loads(payload)
                except json.JSONDecodeError:
                    return
                if (
                    isinstance(message, dict)
                    and message.get("type") == "capture-image-result"
                    and isinstance(message.get("result"), dict)
                ):
                    image_result = message["result"]

            socket.on("framereceived", on_frame)

        page.on("request", request_record)
        page.on("websocket", websocket_record)
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
            await _configure(frame, page)
            await frame.evaluate(
                """() => {
                  const key = Object.keys(localStorage).find(
                    (item) => item.startsWith('octop.webskill.runtime.'),
                  );
                  if (!key) throw new Error('runtime config key missing');
                  const config = JSON.parse(localStorage.getItem(key));
                  config.multimodal.pageImageCapture = true;
                  localStorage.setItem(key, JSON.stringify(config));
                }"""
            )
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            frame = next((item for item in page.frames if "/webskill/" in item.url), None)
            assert frame is not None
            await frame.locator(".host-header").wait_for(timeout=30_000)
            handoff = page.get_by_role("button", name="交还助手", exact=True)
            if await handoff.count() and await handoff.is_visible():
                await handoff.click()
            for _attempt in range(3):
                composer = frame.locator("textarea:visible").last
                await composer.fill(
                    "First call perceive_page. Then, in the same run, call "
                    "capture_page_image with a fresh ref from that result for the "
                    "button named 'Image Tiny pixel'. Do not ask for permission "
                    "and do not reply IMAGE_DONE until the capture tool returns."
                )
                await composer.press("Enter")
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if image_result is not None:
                        break
                    allow = frame.get_by_role("button", name="允许", exact=True)
                    if await allow.count():
                        try:
                            if await allow.first.is_visible():
                                await allow.first.click()
                        except Exception:
                            pass
                    await asyncio.sleep(0.3)
                await _idle(frame)
                if image_result is not None:
                    break
            body = (await frame.locator("body").inner_text())[-2000:]
            ok = bool(
                image_result
                and image_result.get("data")
                and image_result.get("mimeType") == "image/png"
                and isinstance(image_result.get("bytes"), int)
            )
            return {
                "status": "passed" if ok else "failed",
                "model": MODEL_NAME,
                "imageResult": image_result,
                "modelRequests": model_requests,
                "pageErrors": errors,
                "body": body,
            }
        except Exception as error:
            body = ""
            try:
                if frame is not None:
                    body = (await frame.locator("body").inner_text())[-2000:]
            except Exception:
                pass
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "model": MODEL_NAME,
                "imageResult": image_result,
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
    (OUT / "ui-result-r2b-page-image.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
