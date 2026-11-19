"""Verify a real recording-to-candidate flow through Octop, Chatbot and Console."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octop_browser.cdp.launcher import find_chrome
from octop_browser.record.models import RecordingManifest, SemanticStep, StepsDocument
from octop_browser.record.settings import RecordReplaySettings
from octop_browser.record.store import RecordingStore
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18088")


def safe_error(error: BaseException | str) -> str:
    return re.sub(
        r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", str(error)
    )[:2000]


def create_recording(marker: str) -> str:
    recording_id = f"rec_{uuid.uuid4().hex}"
    directory = Path(
        os.environ.get("BROWSER_RECORD_REPLAY_DIR", str(OUT / "record-replay"))
    )
    store = RecordingStore(RecordReplaySettings(base_dir=directory))
    now = datetime.now(UTC).isoformat()
    store.create(
        RecordingManifest(
            recordingId=recording_id,
            createdAt=now,
            endedAt=now,
            status="stopped",
            profile="user-1",
            target={"startUrl": "https://example.test/", "title": "Reviewed fixture"},
        )
    )
    store.append_event(
        recording_id, {"type": "click", "page": {"url": "https://example.test/"}}
    )
    store.write_steps(
        recording_id,
        StepsDocument(
            recordingId=recording_id,
            startUrl="https://example.test/",
            steps=[
                SemanticStep(
                    id="step_1", kind="click", target={"accessibleName": marker}
                )
            ],
        ),
    )
    return recording_id


async def run() -> dict[str, Any]:
    errors: list[str] = []
    model_requests: list[str] = []
    marker = f"Reviewed action {uuid.uuid4().hex}"
    recording_id = create_recording(marker)
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
            await frame.get_by_test_id("recording-skill-create").wait_for(timeout=30000)
            await frame.get_by_test_id("recording-skill-create").click()
            confirmation = frame.get_by_role("button", name="允许", exact=True)
            await confirmation.wait_for(timeout=20000)
            assert "Recorded browser workflow" in await frame.locator("body").inner_text()
            await confirmation.click()
            await frame.locator(".webskill-console-root").wait_for(timeout=30000)
            await frame.get_by_text("recorded-example-test", exact=True).first.wait_for(
                timeout=20000
            )
            await frame.get_by_text(marker, exact=False).first.wait_for(timeout=20000)
            candidate = await frame.evaluate(
                """async (marker) => {
                  const root = await navigator.storage.getDirectory();
                  const app = await root.getDirectoryHandle('octop-webskill-user-1');
                  const managed = await app.getDirectoryHandle('managed');
                  const webskill = await managed.getDirectoryHandle('.webskill');
                  const candidates = await webskill.getDirectoryHandle('candidates');
                  for await (const [name, handle] of candidates.entries()) {
                    if (handle.kind !== 'file' || !name.endsWith('.json')) continue;
                    const text = await (await handle.getFile()).text();
                    const candidate = JSON.parse(text);
                    if (candidate.files.some((file) => file.content.includes(marker))) {
                      return candidate;
                    }
                  }
                  throw new Error('The submitted candidate was not persisted in OPFS');
                }""",
                marker,
            )
            assert candidate["status"] == "draft"
            assert candidate["source"] == "generated"
            assert isinstance(candidate["metadata"].get("confirmedAt"), str)
            assert "sessionId" not in candidate["metadata"]
            serialized = json.dumps(candidate, ensure_ascii=False)
            assert "sourceDigest" not in serialized
            assert recording_id not in serialized
            assert "steps" not in candidate
            assert not model_requests, "The fixed template must not call a model"
            assert not errors, errors
            return {
                "status": "passed",
                "recordingConverted": True,
                "draftDigestRecomputed": True,
                "explicitConfirmation": True,
                "candidatePersisted": True,
                "candidateStatus": candidate["status"],
                "consolePreviewed": True,
                "modelCalls": len(model_requests),
                "pageErrors": errors,
            }
        except Exception as error:
            await page.screenshot(
                path=str(OUT / "skill-draft-failure.png"), full_page=True
            )
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "pageErrors": errors,
                "bodyText": (await page.locator("body").inner_text())[:3000],
            }
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-skill-draft.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
