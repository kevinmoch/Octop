"""R3: real local model creates an XLSX artifact through export_spreadsheet."""

from __future__ import annotations

import asyncio
import io
import json
import os
from typing import Any

from openpyxl import load_workbook
from playwright.async_api import async_playwright
from r3_real_model_document_acceptance import BASE_URL, OUT, _configure, _login
from ui_acceptance import safe_error

MODEL_NAME = os.environ.get("WEBSKILL_P1_MODEL_NAME", "qwen3.5:9b")
MARKER = "Octop real-model xlsx"


async def run() -> dict[str, Any]:
    errors: list[str] = []
    state = {"requests": 0}
    async with async_playwright() as playwright:
        from octop_browser.cdp.launcher import find_chrome

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
            await _configure(frame, page)
            composer = frame.locator("textarea:visible").last
            await composer.fill(
                'Call export_spreadsheet exactly once with fileName "octop-r3.xlsx" '
                'and spec {"sheets":[{"name":"Summary","columns":["Metric","Value"],'
                f'"rows":[["{MARKER}",42]]}}]}}. Do not call other tools. '
                "Reply ARTIFACT_READY after the tool result."
            )
            await composer.press("Enter")
            await frame.get_by_text("ARTIFACT_READY", exact=True).wait_for(timeout=180_000)
            artifacts = await frame.evaluate(
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
            assert len(artifacts) >= 1, artifacts
            artifact = artifacts[-1]
            target = OUT / "octop-r3-real-model.xlsx"
            target.write_bytes(bytes(artifact["bytes"]))
            workbook = load_workbook(io.BytesIO(target.read_bytes()), read_only=True)
            values = list(workbook["Summary"].iter_rows(values_only=True))
            workbook.close()
            return {
                "status": "passed",
                "model": MODEL_NAME,
                "modelMode": "external",
                "modelRequests": state["requests"],
                "artifact": artifact["path"],
                "values": values,
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
                "modelRequests": state["requests"],
                "pageErrors": errors,
                "body": body,
            }
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-r3-real-model-spreadsheet.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
