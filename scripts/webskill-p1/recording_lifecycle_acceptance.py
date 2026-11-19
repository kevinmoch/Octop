"""Verify real recording deletion and subsequent conversion/replay refusal."""

from __future__ import annotations

import asyncio
import json
import os
import re
import traceback
from pathlib import Path
from typing import Any

from octop_browser.cdp.launcher import find_chrome
from octop_browser.record.settings import RecordReplaySettings
from octop_browser.record.store import RecordingStore
from playwright.async_api import async_playwright
from replay_acceptance import create_recording
from ui_acceptance import safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18088")
MARKER = "private-input-lifecycle-fixture"


async def run() -> dict[str, Any]:
    directory = Path(os.environ["BROWSER_RECORD_REPLAY_DIR"])
    create_recording(f"https://example.test/form?token={MARKER}")
    store = RecordingStore(RecordReplaySettings(base_dir=directory))
    owned = [item for item in store.list_recordings() if item.profile == "user-1"]
    assert len(owned) == 1, [item.recording_id for item in owned]
    recording_id = owned[0].recording_id
    store.append_event(recording_id, {"kind": "fill", "value": MARKER})
    screenshot = store.recording_dir(recording_id) / "screenshots" / "private.png"
    screenshot.write_bytes(b"private-image")
    report = store.recording_dir(recording_id) / "replay-reports" / "private.json"
    report.write_text(json.dumps({"inputs": {"password": MARKER}}), encoding="utf-8")
    errors: list[str] = []
    model_requests: list[str] = []
    recording_responses: list[dict[str, Any]] = []
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
        async def observe_recording_response(response: Any) -> None:
            if not (
                "/browser-agent/recordings/" in response.url
                or response.url.endswith("/browser/record-replay/status")
            ):
                return
            recording_responses.append(
                {
                    "method": response.request.method,
                    "path": response.url.split("?", 1)[0],
                    "status": response.status,
                    "body": (await response.text())[:500],
                }
            )

        page.on(
            "response",
            lambda response: asyncio.create_task(observe_recording_response(response)),
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
            await frame.evaluate(
                """async () => {
                  const root = await navigator.storage.getDirectory();
                  const app = await root.getDirectoryHandle('octop-webskill-user-1', { create: true });
                  const managed = await app.getDirectoryHandle('managed', { create: true });
                  const skill = await managed.getDirectoryHandle('lifecycle-published', { create: true });
                  const file = await skill.getFileHandle('SKILL.md', { create: true });
                  const writable = await file.createWritable();
                  await writable.write('# Published independently of the recording');
                  await writable.close();
                }"""
            )
            await frame.get_by_test_id("recording-delete").click()
            allow = frame.get_by_role("button", name="允许", exact=True)
            await allow.wait_for(timeout=20000)
            async with page.expect_response(
                lambda response: response.request.method == "DELETE"
                and f"/browser-agent/recordings/{recording_id}" in response.url,
                timeout=20000,
            ) as deleted:
                await allow.click()
            response = await deleted.value
            assert response.ok, response.status
            await frame.get_by_text("录制及其原始数据已删除；已发布技能未改变。", exact=True).wait_for()
            published = await frame.evaluate(
                """async () => {
                  const root = await navigator.storage.getDirectory();
                  const app = await root.getDirectoryHandle('octop-webskill-user-1');
                  const managed = await app.getDirectoryHandle('managed');
                  const skill = await managed.getDirectoryHandle('lifecycle-published');
                  const file = await skill.getFileHandle('SKILL.md');
                  return (await file.getFile()).text();
                }"""
            )
            assert published == "# Published independently of the recording"
            assert not store.recording_dir(recording_id).exists()
            assert not screenshot.exists()
            assert not report.exists()
            tombstones = (directory / "deletion-tombstones.jsonl").read_text(
                encoding="utf-8"
            )
            assert MARKER not in tombstones
            assert f'"recordingId":"{recording_id}"' in tombstones
            await frame.get_by_test_id("recording-skill-create").click()
            await frame.get_by_text("无法转换录制。", exact=False).wait_for(timeout=10000)
            await frame.get_by_test_id("recording-replay").click()
            await allow.wait_for(timeout=10000)
            await allow.click()
            await frame.get_by_text("无法回放。", exact=False).wait_for(timeout=10000)
            assert not model_requests, model_requests
            assert not errors, errors
            return {
                "status": "passed",
                "sourceDirectoryRemoved": True,
                "screenshotsRemoved": True,
                "inputsRemoved": True,
                "replayReportsRemoved": True,
                "draftBlockedAfterDelete": True,
                "replayBlockedAfterDelete": True,
                "tombstoneContainsNoRawInput": True,
                "publishedSkillsUntouched": True,
                "modelCalls": 0,
                "pageErrors": errors,
            }
        except Exception as error:
            await page.screenshot(
                path=str(OUT / "recording-lifecycle-failure.png"), full_page=True
            )
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "traceback": traceback.format_exc()[-3000:],
                "pageErrors": errors,
                "modelCalls": len(model_requests),
                "recordingResponses": recording_responses,
                "frameText": await frame.locator("body").inner_text()
                if frame is not None
                else "",
            }
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-recording-lifecycle.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
