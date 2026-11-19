"""Verify a published recording skill drives a real PageAgent action."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from octop_browser.cdp.launcher import find_chrome
from playwright.async_api import async_playwright
from replay_acceptance import create_recording
from ui_acceptance import CounterHandler, CounterServer, safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18088")
MODEL_URL = os.environ.get("WEBSKILL_P1_MODEL_URL", "http://127.0.0.1:1234/v1")


async def run() -> dict[str, Any]:
    errors: list[str] = []
    model_requests: list[str] = []
    grant_requests: list[str] = []
    grant_actions: list[str] = []
    replay_requests: list[str] = []
    execute_results: list[dict[str, Any]] = []
    skill_evidence = False
    counter = CounterServer(("127.0.0.1", 0), CounterHandler)
    threading.Thread(target=counter.serve_forever, daemon=True).start()
    fixture_url = f"http://127.0.0.1:{counter.server_port}/"
    create_recording(fixture_url)
    skill_name = "recorded-127-0-0-1"

    def record_request(request: Any) -> None:
        nonlocal skill_evidence
        if "/chat/completions" in request.url:
            model_requests.append(request.url)
            raw = request.post_data or ""
            if "never reuse recorded CDP references" in raw:
                skill_evidence = True
        if "/api/browser-agent/sessions/" in request.url and request.url.endswith("/grants"):
            grant_requests.append(request.url)
            try:
                grant_actions.append(str(json.loads(request.post_data or "{}")["request"]["action"]))
            except (KeyError, TypeError, json.JSONDecodeError):
                grant_actions.append("invalid")
        if "/api/browser-agent/sessions/" in request.url and request.url.endswith("/replays"):
            replay_requests.append(request.url)

    def record_websocket(socket: Any) -> None:
        def frame_received(payload: Any) -> None:
            if not isinstance(payload, str):
                return
            try:
                message = json.loads(payload)
            except json.JSONDecodeError:
                return
            if not isinstance(message, dict):
                return
            if message.get("type") == "execute-result":
                outcome = message.get("outcome", {})
                execute_results.append(
                    {
                        "ok": outcome.get("ok") if isinstance(outcome, dict) else None,
                        "reason": str(outcome.get("reason", ""))[:300]
                        if isinstance(outcome, dict)
                        else "invalid outcome",
                    }
                )
            elif message.get("type") == "error":
                execute_results.append(
                    {
                        "code": str(message.get("code", ""))[:100],
                        "message": str(message.get("message", ""))[:300],
                    }
                )

        socket.on("framereceived", frame_received)

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
        page.on("request", record_request)
        page.on("websocket", record_websocket)
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
            async with page.expect_response(
                lambda response: response.url.endswith("/handoff")
                and response.request.method == "POST",
                timeout=10000,
            ) as response_info:
                await return_control.click()
            handoff = await response_info.value
            assert handoff.ok, handoff.status
            handoff_body = await handoff.json()
            assert handoff_body["session"]["control_owner"] == "agent"

            await frame.get_by_test_id("recording-skill-create").click()
            allow = frame.get_by_role("button", name="允许", exact=True)
            await allow.wait_for(timeout=20000)
            await allow.click()
            await frame.locator(".webskill-console-root").wait_for(timeout=30000)
            await frame.get_by_text(skill_name, exact=True).first.wait_for(timeout=20000)
            publish = frame.get_by_role("button", name="发布", exact=True)
            await publish.wait_for(timeout=20000)
            await publish.click()
            installed = False
            for _ in range(200):
                installed = await frame.evaluate(
                    """async (name) => {
                      try {
                        const root = await navigator.storage.getDirectory();
                        const app = await root.getDirectoryHandle('octop-webskill-user-1');
                        const managed = await app.getDirectoryHandle('managed');
                        const skill = await managed.getDirectoryHandle(name);
                        const file = await skill.getFileHandle('SKILL.md');
                        return (await (await file.getFile()).text()).includes('never reuse recorded CDP references');
                      } catch (error) {
                        if (error.name === 'NotFoundError') return false;
                        throw error;
                      }
                    }""",
                    skill_name,
                )
                if installed:
                    break
                await asyncio.sleep(0.1)
            assert installed, "The published skill was not installed in managed storage"

            await page.keyboard.press("Control+k")
            palette = frame.locator("input").last
            await palette.fill("大模型")
            await frame.get_by_role("option", name="连接 / 大模型").click()
            await frame.get_by_test_id("console-model-add").click()
            dialog = frame.get_by_test_id("console-edit-dialog-model")
            fields = dialog.locator("input")
            await fields.nth(0).fill("Local Qwen")
            await fields.nth(1).fill("qwen3.5:9b")
            await fields.nth(2).fill(MODEL_URL)
            await frame.get_by_test_id("console-edit-dialog-model-save").click()
            await frame.get_by_role("button", name="助手", exact=True).click()
            await frame.locator(".local-model summary").click()
            await frame.locator('.local-model input[type="checkbox"]').check()
            composer = frame.locator("textarea:visible").last
            await composer.fill(
                f"Use the installed skill {skill_name}. Read its SKILL.md, then use the page tools to click Run once exactly once. Do not use ReplayRunner. After the click, reply exactly SKILL_EXECUTION_DONE."
            )
            await composer.press("Enter")
            await allow.wait_for(timeout=90000)
            await allow.click()
            await frame.get_by_text("SKILL_EXECUTION_DONE", exact=False).wait_for(
                timeout=90000
            )
            deadline = time.monotonic() + 10
            while counter.clicks < 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert counter.clicks == 1, counter.clicks
            assert skill_evidence, "The skill body did not enter the agent context"
            assert len(grant_requests) == 1, len(grant_requests)
            assert not replay_requests, replay_requests
            assert model_requests, "No real model request was made"
            assert not errors, errors
            return {
                "status": "passed",
                "skillPublished": True,
                "skillContentObserved": skill_evidence,
                "grantRequests": len(grant_requests),
                "replayRequests": len(replay_requests),
                "modelRequests": len(model_requests),
                "confirmedRealClickCount": counter.clicks,
                "pageErrors": errors,
            }
        except Exception as error:
            await page.screenshot(
                path=str(OUT / "skill-execution-failure.png"), full_page=True
            )
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "pageErrors": errors,
                "fixtureClickCount": counter.clicks,
                "skillContentObserved": skill_evidence,
                "grantRequests": len(grant_requests),
                "grantActions": grant_actions,
                "executeResults": execute_results,
                "replayRequests": len(replay_requests),
                "modelRequests": len(model_requests),
            }
        finally:
            await browser.close()
            counter.shutdown()
            counter.server_close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-skill-execution.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
