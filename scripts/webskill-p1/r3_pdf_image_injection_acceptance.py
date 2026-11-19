"""R2B PDF attachment acceptance through Chatbot + pdfjs reader."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
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
PDF = OUT / "bulletin-image.pdf"


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
    await fields.nth(0).fill("Local Qwen R2B PDF")
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
    tool_calls: list[str] = []
    pdf_image_observed = False
    fixture = None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(safe_error(error)))

        def request_record(request: Any) -> None:
            nonlocal model_requests, pdf_image_observed
            if "/chat/completions" not in request.url:
                return
            model_requests += 1
            body = request.post_data or ""
            if '"image_url"' in body or "data:image/png" in body:
                pdf_image_observed = True
            for name in ("read_document", "read_skill_file", "pdf"):
                if name in body:
                    tool_calls.append(name)

        page.on("request", request_record)
        frame: Any = None
        try:
            assert PDF.is_file(), PDF
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
            attach = frame.get_by_test_id("composer-attach")
            if await attach.count() == 0:
                raise AssertionError("no attachment button")
            async with page.expect_file_chooser() as chooser_info:
                await attach.click()
            chooser = await chooser_info.value
            await chooser.set_files(str(PDF))
            await page.wait_for_timeout(2500)
            composer = frame.locator("textarea:visible").last
            await composer.fill(
                'Read the attached PDF with read_document mode="images" and describe the image in one sentence.'
            )
            await composer.press("Enter")
            await _idle(frame, timeout=240)
            body = (await frame.locator("body").inner_text())[-2500:]
            return {
                "status": "passed" if (model_requests > 0 and pdf_image_observed) else "failed",
                "model": MODEL_NAME,
                "modelRequests": model_requests,
                "pdfImageObserved": pdf_image_observed,
                "toolCalls": sorted(set(tool_calls)),
                "pageErrors": errors,
                "body": body,
            }
        except Exception as error:
            body = ""
            try:
                if frame is not None:
                    body = (await frame.locator("body").inner_text())[-2500:]
            except Exception:
                pass
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "model": MODEL_NAME,
                "modelRequests": model_requests,
                "pdfImageObserved": pdf_image_observed,
                "toolCalls": sorted(set(tool_calls)),
                "pageErrors": errors,
                "body": body,
            }
        finally:
            await browser.close()
            if fixture is not None:
                fixture.shutdown()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-r3-pdf-image-injection.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
