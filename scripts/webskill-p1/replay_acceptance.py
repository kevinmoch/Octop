"""Verify one confirmed recording replay against a real local page."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octop_browser.cdp.launcher import find_chrome
from octop_browser.record.models import RecordingManifest, SemanticStep, StepsDocument
from octop_browser.record.settings import RecordReplaySettings
from octop_browser.record.store import RecordingStore
from playwright.async_api import async_playwright

from ui_acceptance import CounterHandler, CounterServer, safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18088")


def create_recording(url: str) -> None:
    recording_id = f"rec_{uuid.uuid4().hex}"
    now = datetime.now(UTC).isoformat()
    directory = Path(
        os.environ.get("BROWSER_RECORD_REPLAY_DIR", str(OUT / "record-replay"))
    )
    store = RecordingStore(RecordReplaySettings(base_dir=directory))
    store.create(
        RecordingManifest(
            recordingId=recording_id,
            createdAt=now,
            endedAt=now,
            status="stopped",
            profile="user-1",
            target={"startUrl": url, "title": "Replay fixture"},
        )
    )
    store.append_event(recording_id, {"type": "click", "page": {"url": url}})
    store.write_steps(
        recording_id,
        StepsDocument(
            recordingId=recording_id,
            startUrl=url,
            steps=[
                SemanticStep(id="step_open", kind="open", url=url),
                SemanticStep(
                    id="step_click",
                    kind="click",
                    target={"role": "button", "name": "Run once"},
                ),
            ],
        ),
    )


async def run() -> dict[str, Any]:
    errors: list[str] = []
    model_requests: list[str] = []
    counter = CounterServer(("127.0.0.1", 0), CounterHandler)
    threading.Thread(target=counter.serve_forever, daemon=True).start()
    fixture_url = f"http://127.0.0.1:{counter.server_port}/"
    create_recording(fixture_url)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            ignore_https_errors=os.environ.get("WEBSKILL_P1_ALLOW_SELF_SIGNED") == "1",
        )
        page.on("pageerror", lambda error: errors.append(safe_error(error)))
        page.on(
            "request",
            lambda request: model_requests.append(request.url)
            if "/chat/completions" in request.url
            else None,
        )
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
                await page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                await page.mouse.down()
                await page.mouse.move(
                    track["x"] + track["width"],
                    box["y"] + box["height"] / 2,
                    steps=12,
                )
                await page.mouse.up()
                await page.get_by_role("button", name=re.compile("登")).click()
                await page.wait_for_url(re.compile(r"/chat/"))
            dismiss = page.get_by_role("button", name="稍后再说")
            if await dismiss.is_visible():
                await dismiss.click()
            menu = page.get_by_role("button", name="浏览器", exact=True)
            if not await menu.is_visible():
                await page.get_by_role("button", name="控制", exact=True).click()
            await menu.click()
            await page.wait_for_url(f"{BASE_URL}/browser")
            await page.get_by_role("button", name="启动浏览器").click()
            frame_element = page.locator('iframe[title="WebSkill"]')
            await frame_element.wait_for(state="attached", timeout=30000)
            await page.wait_for_timeout(1000)
            frame = next(
                (item for item in page.frames if "/webskill/" in item.url), None
            )
            assert frame is not None
            address = page.get_by_placeholder("输入网址或 https://…")
            await address.fill(fixture_url)
            await address.press("Enter")
            await page.wait_for_timeout(2000)
            return_control = page.get_by_role("button", name="交还助手", exact=True)
            await return_control.wait_for(timeout=10000)
            await return_control.click()
            await page.get_by_role("button", name="接管", exact=True).wait_for()
            await frame.get_by_test_id("recording-replay").click()
            confirmation = frame.get_by_role("button", name="允许", exact=True)
            await confirmation.wait_for(timeout=20000)
            await confirmation.click()
            await frame.get_by_text("录制回放已完成。", exact=True).wait_for(
                timeout=45000
            )
            assert counter.clicks == 1, counter.clicks
            assert not model_requests, model_requests
            assert not errors, errors
            return {
                "status": "passed",
                "scopeConfirmed": True,
                "confirmedRealClickCount": counter.clicks,
                "modelCalls": len(model_requests),
                "pageErrors": errors,
            }
        except Exception as error:
            await page.screenshot(
                path=str(OUT / "replay-failure.png"), full_page=True
            )
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "pageErrors": errors,
                "fixtureClickCount": counter.clicks,
                "bodyText": (await page.locator("body").inner_text())[:3000],
            }
        finally:
            await browser.close()
            counter.shutdown()
            counter.server_close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-replay.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
