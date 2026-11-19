"""Verify a real multi-target replay without writing to the initial page."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import traceback
import urllib.request
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


class TargetHandler(CounterHandler):
    def do_GET(self) -> None:
        self.server.requested_paths.append(self.path)
        if self.path == "/first":
            self.respond(
                "<!doctype html><html><body><h1>Base document</h1></body></html>"
            )
            return
        if self.path in {"/second", "/second/next"}:
            self.respond(
                "<!doctype html><html><body><h1>Model document</h1>"
                "<button onclick=\"fetch('/click', {method:'POST'})\">Run once</button>"
                "</body></html>"
            )
            return
        super().do_GET()


class TargetServer(CounterServer):
    requested_paths: list[str] = []
    clicks = 0


def create_target_recording(url: str) -> None:
    directory = Path(os.environ["BROWSER_RECORD_REPLAY_DIR"])
    store = RecordingStore(RecordReplaySettings(base_dir=directory))
    now = datetime.now(UTC).isoformat()
    recording_id = "rec_target_scope"
    store.create(
        RecordingManifest(
            recordingId=recording_id,
            createdAt=now,
            endedAt=now,
            status="stopped",
            profile="user-1",
            target={"startUrl": f"{url}/first", "title": "Target scope"},
        )
    )
    store.write_steps(
        recording_id,
        StepsDocument(
            recordingId=recording_id,
            startUrl=f"{url}/first",
            steps=[
                SemanticStep(
                    id="new-tab",
                    kind="new_tab",
                    url=f"{url}/second",
                    target={"_targetId": "recorded-second"},
                ),
                SemanticStep(
                    id="navigate-second",
                    kind="navigate",
                    url=f"{url}/second/next",
                    target={"_targetId": "recorded-second"},
                ),
                SemanticStep(
                    id="click-second",
                    kind="click",
                    target={
                        "_targetId": "recorded-second",
                        "role": "button",
                        "name": "Run once",
                    },
                ),
            ],
        ),
    )


def page_targets() -> list[dict[str, Any]]:
    with urllib.request.urlopen("http://127.0.0.1:43000/json", timeout=5) as response:
        return json.load(response)


async def run() -> dict[str, Any]:
    errors: list[str] = []
    model_requests: list[str] = []
    counter = TargetServer(("127.0.0.1", 0), TargetHandler)
    counter.requested_paths = []
    counter.clicks = 0
    threading.Thread(target=counter.serve_forever, daemon=True).start()
    fixture_url = f"http://127.0.0.1:{counter.server_port}"
    create_target_recording(fixture_url)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(safe_error(error)))
        page.on(
            "request",
            lambda request: model_requests.append(request.url)
            if "/chat/completions" in request.url
            else None,
        )
        frame: Any = None
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
            await page.locator('iframe[title="WebSkill"]').wait_for(
                state="attached", timeout=30000
            )
            frame = next(
                (item for item in page.frames if item.url.endswith("/webskill/")), None
            )
            assert frame is not None
            address = page.get_by_placeholder("输入网址或 https://…")
            await address.fill(f"{fixture_url}/first")
            await address.press("Enter")
            return_control = page.get_by_role("button", name="交还助手", exact=True)
            await return_control.wait_for(timeout=10000)
            await return_control.click()
            await page.get_by_role("button", name="接管", exact=True).wait_for()
            await frame.get_by_test_id("recording-replay").click()
            allow = frame.get_by_role("button", name="允许", exact=True)
            await allow.wait_for(timeout=20000)
            await allow.click()
            await frame.get_by_text("录制回放已完成。", exact=True).wait_for(
                timeout=60000
            )
            assert counter.clicks == 1, counter.clicks
            assert not model_requests, model_requests
            targets = await asyncio.to_thread(page_targets)
            urls = [str(item.get("url", "")) for item in targets]
            assert any(url.endswith("/first") for url in urls), urls
            assert any(url.endswith("/second/next") for url in urls), urls
            assert counter.requested_paths.count("/first") == 1
            assert "/second" in counter.requested_paths
            assert "/second/next" in counter.requested_paths
            assert not errors, errors
            return {
                "status": "passed",
                "confirmedRealClickCount": counter.clicks,
                "initialPagePreserved": True,
                "modelTargetNavigated": True,
                "logicalTargetSwitch": True,
                "modelCalls": 0,
                "pageErrors": errors,
            }
        except Exception as error:
            await page.screenshot(
                path=str(OUT / "replay-target-failure.png"), full_page=True
            )
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "traceback": traceback.format_exc()[-3000:],
                "pageErrors": errors,
                "modelCalls": len(model_requests),
                "requestedPaths": counter.requested_paths,
                "frameText": await frame.locator("body").inner_text()
                if frame is not None
                else "",
            }
        finally:
            await browser.close()
            counter.shutdown()
            counter.server_close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-replay-target.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
