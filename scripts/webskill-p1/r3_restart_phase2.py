"""R3 phase 2: after UI server restart, reopen Console with the same profile."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from octop_browser.cdp.launcher import find_chrome
from playwright.async_api import async_playwright
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18089")
PROFILE = Path(os.environ.get("R3_PERSISTENT_PROFILE", "/tmp/octop-r3-persistent-profile"))


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


async def run() -> dict[str, Any]:
    errors: list[str] = []
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            executable_path=find_chrome(),
            headless=True,
            args=["--no-proxy-server"],
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("pageerror", lambda error: errors.append(safe_error(error)))
        try:
            await page.goto(f"{BASE_URL}/browser", wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            if await page.locator("input").count() >= 2:
                await _login(page)
                await page.goto(f"{BASE_URL}/browser", wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
            start = page.get_by_role("button", name="启动浏览器")
            if await start.count():
                await start.click()
            await page.wait_for_timeout(2000)
            frame = next((item for item in page.frames if "/webskill/" in item.url), None)
            assert frame is not None
            await frame.locator(".host-header").wait_for(timeout=30_000)
            await frame.get_by_role("button", name="控制台", exact=True).click()
            await frame.locator(".webskill-console-root").wait_for(timeout=30_000)
            expand = frame.get_by_test_id("console-sidebar-expand")
            if await expand.count() and await expand.is_visible():
                await expand.click()
            nav = frame.get_by_test_id("console-nav")
            await nav.get_by_role("button", name="运行", exact=True).click()
            await page.wait_for_timeout(1000)
            runs_text = (await frame.locator("body").inner_text())[-3000:]
            await nav.get_by_role("button", name="治理", exact=True).click()
            await page.wait_for_timeout(500)
            audit = nav.get_by_role("button", name="审计日志", exact=True)
            if await audit.count():
                await audit.click()
                await page.wait_for_timeout(800)
            audit_text = (await frame.locator("body").inner_text())[-3000:]
            return {
                "status": "passed"
                if "authored-bulletin" in runs_text and "document.surface.open" in audit_text
                else "failed",
                "phase": "recover",
                "profile": str(PROFILE),
                "installedSkillObserved": "authored-bulletin" in runs_text,
                "consoleRunObserved": "authored-bulletin" in runs_text,
                "auditObserved": "document.surface.open" in audit_text,
                "runsText": runs_text,
                "auditText": audit_text,
                "pageErrors": errors,
            }
        except Exception as error:
            return {
                "status": "failed",
                "phase": "recover",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "pageErrors": errors,
            }
        finally:
            await context.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-r3-restart-phase2.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
