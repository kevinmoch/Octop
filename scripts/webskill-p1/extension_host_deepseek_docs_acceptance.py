"""Run DOCX/PPTX/XLSX/screen document journeys in the MV3 extension with DeepSeek."""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from playwright.async_api import async_playwright
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "extension"
DIST = Path("/Users/mochunhui/Git/web-skill-sdk/examples/browser-extension/dist")
PROFILE = Path("/tmp/webskill-extension-deepseek-docs-profile")


async def allow(side: Any) -> None:
    for name in ("允许", "确认"):
        buttons = side.get_by_role("button", name=name, exact=True)
        visible = [
            buttons.nth(index)
            for index in range(await buttons.count())
            if await buttons.nth(index).is_visible()
        ]
        if visible:
            await visible[-1].click(force=True)
            await side.wait_for_timeout(200)


async def viewer_for(context: Any, before: set[Any], timeout: float = 180) -> Any:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        for page in context.pages:
            if "/view.html" in page.url and page not in before:
                await page.wait_for_timeout(1200)
                return page
        await asyncio.sleep(0.5)
    return None


async def run_document(
    context: Any, sidepanel_url: str, prompt: str, marker: str
) -> dict[str, Any]:
    before = set(context.pages)
    side = await context.new_page()
    await side.goto(sidepanel_url, wait_until="domcontentloaded")
    await side.wait_for_timeout(800)
    composer = side.locator("textarea:visible").last
    await composer.fill(prompt)
    await composer.press("Enter")
    viewer = None
    body = ""
    end = asyncio.get_running_loop().time() + 180
    while asyncio.get_running_loop().time() < end:
        await allow(side)
        body = await side.locator("body").inner_text()
        if "打开文档" in body:
            button = side.get_by_role("button", name="打开文档", exact=True)
            if await button.count() and await button.first.is_visible():
                await button.first.click(force=True)
        viewer = await viewer_for(context, before, timeout=3)
        if viewer is not None:
            viewer_text = (await viewer.locator("body").inner_text())[-2000:]
            if marker in viewer_text:
                return {
                    "status": "passed",
                    "sidepanelText": body[-1000:],
                    "viewerUrl": viewer.url,
                    "viewerText": viewer_text,
                }
        await side.wait_for_timeout(700)
    return {
        "status": "failed",
        "sidepanelText": body[-2000:],
        "viewerUrl": viewer.url if viewer is not None else None,
        "viewerText": (await viewer.locator("body").inner_text())[-2000:]
        if viewer is not None
        else None,
    }


async def run() -> dict[str, Any]:
    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True)
    errors: list[str] = []
    results: dict[str, Any] = {}
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
            viewport={"width": 1200, "height": 950},
            locale="zh-CN",
        )
        try:
            await asyncio.sleep(2)
            extension_id = [worker.url.split("/")[2] for worker in context.service_workers][0]
            sidepanel_url = f"chrome-extension://{extension_id}/sidepanel.html"
            scenarios = {
                "docx": (
                    "Use authored-bulletin to create a DOCX titled 'Octop DeepSeek docx'. "
                    "Read the skill, call publish with dataSource 'fixture:local', and reply "
                    "after the document is available.",
                    "Octop DeepSeek docx",
                ),
                "pptx": (
                    "Use authored-slides to create a three-slide deck titled 'Octop DeepSeek pptx'. "
                    "Read the skill, call publish with dataSource 'fixture:local', and reply "
                    "after the document is available.",
                    "Octop DeepSeek pptx",
                ),
                "screen": (
                    "Use authored-screen to create a dashboard titled 'Octop DeepSeek screen'. "
                    "Read the skill, call publish with dataSource 'fixture:local', and reply "
                    "after the document is available.",
                    "Octop DeepSeek screen",
                ),
            }
            only = os.environ.get("ONLY", "")
            for name, (prompt, marker) in scenarios.items():
                if only and name != only:
                    continue
                results[name] = await run_document(
                    context, sidepanel_url, prompt=prompt, marker=marker
                )
                results[name]["model"] = "deepseek-flash"
            # XLSX is a tool result, not a viewer.
            if only and only != "xlsx":
                return {
                    "status": all(item["status"] == "passed" for item in results.values()),
                    "extensionId": extension_id,
                    "model": "deepseek-flash",
                    "capabilities": results,
                    "pageErrors": errors,
                }
            side = await context.new_page()
            await side.goto(sidepanel_url, wait_until="domcontentloaded")
            await side.wait_for_timeout(800)
            composer = side.locator("textarea:visible").last
            await composer.fill(
                'Call export_spreadsheet exactly once with fileName "octop-deepseek.xlsx" '
                'and spec {"sheets":[{"name":"Summary","columns":["Metric","Value"],'
                '"rows":[["Octop DeepSeek xlsx",42]]}]}. Do not call other tools. '
                "Reply XLSX_DONE after the tool result."
            )
            await composer.press("Enter")
            body = ""
            artifacts: list[dict[str, Any]] = []
            end = asyncio.get_running_loop().time() + 120
            while asyncio.get_running_loop().time() < end:
                await allow(side)
                body = await side.locator("body").inner_text()
                if "XLSX_DONE" in body:
                    try:
                        artifacts = await side.evaluate(
                            """async () => {
                          const root = await navigator.storage.getDirectory();
                          const app = await root.getDirectoryHandle('octop-webskill-user-1');
                          const found = [];
                          async function walk(directory, prefix, depth) {
                            if (depth > 8) return;
                            for await (const [name, entry] of directory.entries()) {
                              const path = prefix ? `${prefix}/${name}` : name;
                              if (entry.kind === 'directory') await walk(entry, path, depth + 1);
                              else if (name.endsWith('.xlsx')) {
                                const file = await entry.getFile();
                                found.push({ path, bytes: Array.from(new Uint8Array(await file.arrayBuffer())) });
                              }
                            }
                          }
                          await walk(app, '', 0);
                          return found;
                        }"""
                        )
                    except Exception:
                        artifacts = []
                    break
                await side.wait_for_timeout(700)
            values = None
            if artifacts:
                (OUT / "deepseek-deck.xlsx").write_bytes(bytes(artifacts[-1]["bytes"]))
                workbook = load_workbook(io.BytesIO(bytes(artifacts[-1]["bytes"])), read_only=True)
                values = list(workbook["Summary"].iter_rows(values_only=True))
                workbook.close()
            results["xlsx"] = {
                "status": "passed"
                if values and values[1][0] == "Octop DeepSeek xlsx"
                else "failed",
                "artifact": artifacts[-1]["path"] if artifacts else None,
                "values": values,
                "sidepanelText": body[-1500:],
            }
            return {
                "status": all(item["status"] == "passed" for item in results.values()),
                "extensionId": extension_id,
                "model": "deepseek-flash",
                "capabilities": results,
                "pageErrors": errors,
            }
        except Exception as error:
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "capabilities": results,
                "pageErrors": errors,
            }
        finally:
            await context.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"deepseek-docs-{os.environ.get('ONLY', 'all')}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["status"]:
        raise SystemExit(1)
