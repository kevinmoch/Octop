"""Reject the WebSkill UI on a real non-loopback plain-HTTP origin."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from octop_browser.cdp.launcher import find_chrome
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parents[2] / ".artifacts" / "webskill-p1" / "ui"


async def run() -> dict[str, object]:
    base_url = os.environ["WEBSKILL_P1_INSECURE_URL"]
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=find_chrome(), headless=True)
        page = await browser.new_page(locale="zh-CN")
        try:
            await page.goto(f"{base_url}/login", wait_until="networkidle")
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
            await page.wait_for_url(f"{base_url}/browser")
            assert await page.evaluate("window.isSecureContext") is False
            await page.get_by_text("当前环境无法运行 WebSkill", exact=True).wait_for()
            assert await page.locator('iframe[title="WebSkill"]').count() == 0
            return {
                "status": "passed",
                "secureContext": False,
                "iframeMounted": False,
                "httpsInstructionVisible": True,
            }
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    (OUT / "insecure-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
