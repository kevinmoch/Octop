"""R3: real local model drives an installed document skill into the viewer."""

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
SDK_ROOT = ROOT.parent / "web-skill-sdk"
SKILL_NAME = "authored-bulletin"
SKILL_ROOT = SDK_ROOT / "examples/browser-extension/skills/builtin" / SKILL_NAME
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18089")
MODEL_URL = os.environ.get("WEBSKILL_P1_MODEL_URL", "http://localhost:1234/v1")
MODEL_NAME = os.environ.get("WEBSKILL_P1_MODEL_NAME", "qwen3.5:9b")
MARKER = "Octop real-model bulletin"


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
    await fields.nth(0).fill("Local Qwen R3 document")
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


async def _install_skill(frame: Any) -> None:
    files = {
        name: (SKILL_ROOT / name).read_text(encoding="utf-8")
        for name in ("SKILL.md", "scripts/publish.js", "references/authoring.md")
    }
    await frame.evaluate(
        """async ({ name, files }) => {
          const root = await navigator.storage.getDirectory();
          const app = await root.getDirectoryHandle('octop-webskill-user-1', { create: true });
          const skills = await app.getDirectoryHandle('skills', { create: true });
          const skillDir = await skills.getDirectoryHandle(name, { create: true });
          for (const [path, content] of Object.entries(files)) {
            const parts = path.split('/');
            let directory = skillDir;
            for (const part of parts.slice(0, -1)) {
              directory = await directory.getDirectoryHandle(part, { create: true });
            }
            const file = await directory.getFileHandle(parts.at(-1), { create: true });
            const writable = await file.createWritable();
            await writable.write(content);
            await writable.close();
          }
        }""",
        {"name": SKILL_NAME, "files": files},
    )


async def run() -> dict[str, Any]:
    errors: list[str] = []
    state = {"requests": 0}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(safe_error(error)))

        def request_record(request: Any) -> None:
            if "/chat/completions" in request.url:
                state["requests"] += 1

        page.on("request", request_record)
        frame: Any = None
        viewer: Any = None
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
            await _install_skill(frame)
            await frame.goto(frame.url)
            await page.wait_for_timeout(1500)
            await _configure(frame, page)
            composer = frame.locator("textarea:visible").last
            await composer.fill(
                f"Use the authored-bulletin skill to produce a DOCX bulletin titled "
                f"'{MARKER}'. Read its SKILL.md first, then call the skill's publish "
                "tool. Reply ARTIFACT_READY only after the viewer is available."
            )
            await composer.press("Enter")
            open_document = frame.get_by_role("button", name="打开文档", exact=True)
            for _wait in range(480):
                if await open_document.count() and await open_document.first.is_visible():
                    break
                submit = frame.get_by_role("button", name="提交", exact=True)
                if await submit.count() and await submit.first.is_visible():
                    inputs = frame.locator("input:visible")
                    if await inputs.count() >= 1:
                        await inputs.nth(0).fill("page: current page fixture")
                    textareas = frame.locator("textarea:visible")
                    if await textareas.count() >= 2:
                        await textareas.nth(1).fill("R3 real model document with a concise summary")
                    elif await textareas.count() == 1:
                        await textareas.nth(0).fill("R3 real model document with a concise summary")
                    await submit.first.click()
                    await page.wait_for_timeout(500)
                    continue
                await page.wait_for_timeout(500)
            await open_document.wait_for(timeout=30_000)
            await open_document.click()
            allow = frame.get_by_role("button", name="允许", exact=True)
            await allow.wait_for(timeout=30_000)
            async with page.expect_event(
                "framenavigated",
                lambda item: item.url.startswith(f"{BASE_URL}/webskill/viewer/"),
                timeout=30_000,
            ) as navigated:
                await allow.click()
            viewer = await navigated.value
            await viewer.get_by_text(MARKER, exact=True).first.wait_for(timeout=30_000)
            return {
                "status": "passed" if state["requests"] > 0 else "failed",
                "model": MODEL_NAME,
                "modelMode": "external",
                "modelRequests": state["requests"],
                "skill": SKILL_NAME,
                "viewerOpened": True,
                "markerVisible": True,
                "pageErrors": errors,
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
                "modelRequests": state["requests"],
                "pageErrors": errors,
                "body": body,
            }
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-r3-real-model-document.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
