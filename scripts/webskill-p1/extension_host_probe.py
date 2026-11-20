"""Open the built MV3 extension in Chromium and verify its two surfaces."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "extension"
DIST = Path("/Users/mochunhui/Git/web-skill-sdk/examples/browser-extension/dist")
PROFILE = Path("/tmp/webskill-extension-host-profile")


async def run() -> dict[str, Any]:
    errors: list[str] = []
    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            ignore_default_args=["--disable-extensions"],
            args=[
                f"--disable-extensions-except={DIST}",
                f"--load-extension={DIST}",
                "--no-proxy-server",
                "--no-first-run",
            ],
            viewport={"width": 1000, "height": 1000},
            locale="zh-CN",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("pageerror", lambda error: errors.append(safe_error(error)))
        try:
            await asyncio.sleep(2)
            workers = [worker.url for worker in context.service_workers]
            assert workers, "extension service worker did not start"
            extension_id = workers[0].split("/")[2]
            sidepanel = await context.new_page()
            sidepanel.on("pageerror", lambda error: errors.append(safe_error(error)))
            await sidepanel.goto(
                f"chrome-extension://{extension_id}/sidepanel.html",
                wait_until="domcontentloaded",
            )
            await sidepanel.wait_for_timeout(1500)
            sidepanel_text = (await sidepanel.locator("body").inner_text())[:1500]
            options = await context.new_page()
            await options.goto(
                f"chrome-extension://{extension_id}/options.html",
                wait_until="domcontentloaded",
            )
            await options.wait_for_timeout(1500)
            options_text = (await options.locator("body").inner_text())[:1500]
            return {
                "status": "passed"
                if "DeepSeek" in sidepanel_text and "技能库" in options_text
                else "failed",
                "extensionId": extension_id,
                "serviceWorkers": workers,
                "sidepanelOpened": True,
                "optionsOpened": True,
                "sidepanelText": sidepanel_text,
                "optionsText": options_text,
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
            await context.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "host-probe.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
