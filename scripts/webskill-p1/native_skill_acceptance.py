"""Real native-skill generation and cross-session execution acceptance.

This script drives the actual WebSkill Chatbot with a real OpenAI-compatible
local model. It does not create a recording or use ReplayRunner.
"""

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
from ui_acceptance import CounterHandler, CounterServer, safe_error

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".artifacts" / "webskill-p1" / "ui"
BASE_URL = os.environ.get("WEBSKILL_P1_UI_URL", "http://127.0.0.1:18089")
MODEL_URL = os.environ.get("WEBSKILL_P1_MODEL_URL", "http://localhost:1234/v1")
MODEL_NAME = os.environ.get("WEBSKILL_P1_MODEL_NAME", "qwen3.5:9b")
SKILL_NAME = "native-r1-demo"


def _record_request(request: Any, state: dict[str, Any]) -> None:
    if "/chat/completions" not in request.url:
        return
    state["modelRequests"] += 1
    body = request.post_data or ""
    if "generate_skill" in body:
        state["skillGenerationToolObserved"] = True
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return
    messages = payload.get("messages") or []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and function.get("name") == "generate_skill":
                    state["skillGenerationToolCalled"] = True
        if message.get("role") == "tool":
            content = message.get("content")
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            if SKILL_NAME in text and "Run once" in text:
                state["skillContentInModelContext"] = True


async def _login(page: Any) -> None:
    await page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    if await page.locator("input").count() >= 2:
        await page.locator("input").nth(0).fill("admin")
        await page.locator("input").nth(1).fill("TestPass12")
        thumb = page.get_by_test_id("slide-captcha-thumb")
        box = await thumb.bounding_box()
        track = await page.get_by_test_id("slide-captcha-track").bounding_box()
        if box and track:
            await page.mouse.move(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
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


async def _open_browser(frame_page: Any) -> Any:
    await frame_page.goto(f"{BASE_URL}/browser", wait_until="domcontentloaded")
    await frame_page.wait_for_timeout(2000)
    dismiss = frame_page.get_by_role("button", name="稍后再说")
    if await dismiss.is_visible():
        await dismiss.click()
    start = frame_page.get_by_role("button", name="启动浏览器")
    if await start.count():
        await start.click()
    frame_element = frame_page.locator('iframe[title="WebSkill"]')
    await frame_element.wait_for(state="attached", timeout=30_000)
    await frame_page.wait_for_timeout(1500)
    frame = next(
        (item for item in frame_page.frames if "/webskill/" in item.url),
        None,
    )
    assert frame is not None, "WebSkill iframe missing"
    await frame.locator(".host-header").wait_for(timeout=20_000)
    return frame


async def _configure_model_and_skill_generation(frame: Any, frame_page: Any) -> None:
    await frame.get_by_role("button", name="控制台", exact=True).click()
    await frame_page.wait_for_timeout(800)
    await frame_page.keyboard.press("Control+k")
    palette = frame.locator("input").last
    await palette.fill("大模型")
    await frame.get_by_role("option", name="连接 / 大模型").click()
    await frame.get_by_test_id("console-model-add").click()
    dialog = frame.get_by_test_id("console-edit-dialog-model")
    fields = dialog.locator("input")
    await fields.nth(0).fill("Local Qwen native")
    await fields.nth(1).fill(MODEL_NAME)
    await fields.nth(2).fill(MODEL_URL)
    await frame.get_by_test_id("console-edit-dialog-model-save").click()
    await frame_page.wait_for_timeout(500)

    expand = frame.get_by_test_id("console-sidebar-expand")
    if await expand.count() and await expand.is_visible():
        await expand.click()
    nav = frame.get_by_test_id("console-nav")
    settings = nav.get_by_role("button", name="设置", exact=True)
    await settings.click()
    await frame.get_by_test_id("settings-agent-skill-generation").wait_for(timeout=10_000)
    collapse = frame.get_by_test_id("console-sidebar-collapse")
    if await collapse.count() and await collapse.is_visible():
        await collapse.click()
        await frame_page.wait_for_timeout(300)
    switch = frame.get_by_test_id("settings-agent-skill-generation")
    if (await switch.get_attribute("aria-checked")) != "true":
        await switch.click()
    assert (await switch.get_attribute("aria-checked")) == "true"

    await frame.get_by_role("button", name="助手", exact=True).click()
    await frame_page.wait_for_timeout(500)
    if await frame.locator(".local-model summary").count():
        await frame.locator(".local-model summary").click()
        checkbox = frame.locator('.local-model input[type="checkbox"]')
        if await checkbox.count() and not await checkbox.is_checked():
            await checkbox.check()


async def _allow_until(
    frame: Any,
    *,
    stop_click_count: int | None = None,
    stop_testid: str | None = None,
    timeout: float = 180.0,
    counter: Any = None,
) -> int:
    deadline = time.monotonic() + timeout
    clicked = 0
    while time.monotonic() < deadline:
        if stop_testid is not None and await frame.get_by_test_id(stop_testid).count():
            return clicked
        if (
            stop_click_count is not None
            and counter is not None
            and counter.clicks >= stop_click_count
        ):
            return clicked
        allow = frame.get_by_role("button", name="允许", exact=True)
        if await allow.count():
            try:
                if await allow.first.is_visible():
                    await allow.first.click()
                    clicked += 1
                    await asyncio.sleep(0.4)
                    continue
            except Exception:
                pass
        await asyncio.sleep(0.3)
    return clicked


async def _wait_run_idle(frame: Any, timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        composer = frame.locator("textarea:visible").last
        if await composer.count() and await composer.is_enabled():
            return
        await asyncio.sleep(0.25)


async def _publish_candidate(frame: Any, frame_page: Any) -> tuple[str, str]:
    card = frame.get_by_test_id("skill-candidate-card").first
    await card.wait_for(timeout=120_000)
    candidate_text = await card.inner_text()
    candidate_id = (await frame.get_by_test_id("skill-candidate-id").first.inner_text()).strip()

    # Open Console directly. The candidate review callback is host-specific; the
    # governance queue is the same real path and keeps the candidate in this
    # browser context.
    await frame.get_by_role("button", name="控制台", exact=True).click()
    await frame.locator(".webskill-console-root").wait_for(timeout=30_000)
    expand = frame.get_by_test_id("console-sidebar-expand")
    if await expand.count() and await expand.is_visible():
        await expand.click()
    nav = frame.get_by_test_id("console-nav")
    await nav.get_by_role("button", name="治理", exact=True).click()
    await frame.get_by_text("审批队列", exact=False).first.wait_for(timeout=20_000)
    queue = nav.get_by_role("button", name="审批队列", exact=True)
    if await queue.count():
        await queue.click()
    review = frame.get_by_role("button", name="查看详情").first
    if await review.count():
        await review.click()
    else:
        await frame.get_by_text(SKILL_NAME, exact=True).first.click()
    publish = frame.get_by_role("button", name="发布", exact=True)
    await publish.wait_for(timeout=30_000)
    await publish.click()
    await frame_page.wait_for_timeout(500)
    return candidate_text, candidate_id


async def _skill_installed(frame: Any) -> bool:
    for _ in range(200):
        ok = await frame.evaluate(
            """async (name) => {
              try {
                const root = await navigator.storage.getDirectory();
                const app = await root.getDirectoryHandle('octop-webskill-user-1');
                const managed = await app.getDirectoryHandle('managed');
                const skill = await managed.getDirectoryHandle(name);
                const file = await skill.getFileHandle('SKILL.md');
                const text = await (await file.getFile()).text();
                return text.includes('Run once') || text.includes('run once');
              } catch (error) {
                if (error.name === 'NotFoundError') return false;
                throw error;
              }
            }""",
            SKILL_NAME,
        )
        if ok:
            return True
        await asyncio.sleep(0.1)
    return False


async def _click_with_retries(
    frame: Any,
    counter: Any,
    *,
    target_count: int,
    prompts: list[str],
) -> None:
    for prompt in prompts:
        composer = frame.locator("textarea:visible").last
        await composer.fill(prompt)
        await composer.press("Enter")
        await _allow_until(
            frame,
            stop_click_count=target_count,
            timeout=120,
            counter=counter,
        )
        if counter.clicks >= target_count:
            await _wait_run_idle(frame, timeout=180)
            return
        await _wait_run_idle(frame, timeout=180)
        await asyncio.sleep(0.3)


async def _tool_step_files(frame: Any) -> list[str]:
    result = await frame.evaluate(
        """async () => {
          const names = [];
          async function walk(dir, prefix) {
            for await (const [name, handle] of dir.entries()) {
              const path = `${prefix}/${name}`;
              if (handle.kind === 'directory') await walk(handle, path);
              else names.push(path);
            }
          }
          try {
            const root = await navigator.storage.getDirectory();
            const app = await root.getDirectoryHandle('octop-webskill-user-1');
            const chat = await app.getDirectoryHandle('chat');
            const steps = await chat.getDirectoryHandle('skill-steps');
            await walk(steps, 'skill-steps');
          } catch (error) {
            if (error.name !== 'NotFoundError') throw error;
          }
          return names.sort();
        }"""
    )
    return list(result)


async def run() -> dict[str, Any]:
    errors: list[str] = []
    state: dict[str, Any] = {
        "modelRequests": 0,
        "skillGenerationToolObserved": False,
        "skillContentInModelContext": False,
        "skillGenerationToolCalled": False,
        "newChatClicked": False,
        "lastModelMessage": None,
    }
    grant_requests: list[str] = []
    replay_requests: list[str] = []
    candidate_text = ""
    candidate_id = ""
    tool_step_files: list[str] = []
    counter = CounterServer(("127.0.0.1", 0), CounterHandler)
    threading.Thread(target=counter.serve_forever, daemon=True).start()
    fixture_url = f"http://127.0.0.1:{counter.server_port}/"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=find_chrome(), headless=True, args=["--no-proxy-server"]
        )
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            ignore_https_errors=False,
        )
        page.on("pageerror", lambda error: errors.append(safe_error(error)))

        async def response_record(response: Any) -> None:
            if "/chat/completions" not in response.url:
                return
            try:
                payload = await response.json()
                choice = (payload.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                state["lastModelMessage"] = {
                    "content": str(message.get("content") or "")[:2000],
                    "toolCalls": [
                        {
                            "name": (call.get("function") or {}).get("name"),
                            "arguments": str((call.get("function") or {}).get("arguments") or "")[
                                :1000
                            ],
                        }
                        for call in (message.get("tool_calls") or [])
                    ],
                }
            except Exception:
                return

        page.on(
            "response",
            lambda response: asyncio.create_task(response_record(response)),
        )

        def request_record(request: Any) -> None:
            _record_request(request, state)
            if request.url.endswith("/grants"):
                grant_requests.append(request.url)
            if "/replays" in request.url:
                replay_requests.append(request.url)

        page.on("request", request_record)
        try:
            await _login(page)
            frame = await _open_browser(page)
            address = page.get_by_placeholder("输入网址或 https://…")
            await address.fill(fixture_url)
            await address.press("Enter")
            await page.wait_for_timeout(2000)
            return_control = page.get_by_role("button", name="交还助手", exact=True)
            if await return_control.count() and await return_control.is_visible():
                await return_control.click()
            await page.wait_for_timeout(500)

            await _configure_model_and_skill_generation(frame, page)

            await _click_with_retries(
                frame,
                counter,
                target_count=1,
                prompts=[
                    "Call perceive_page on the current page now. Reply PAGE_READY "
                    "after reading; do not click yet.",
                    "Now call act_on_page with action 'click' and the ref of the "
                    "button named 'Run once'. Click it exactly once. Reply CLICK_DONE "
                    "after the tool returns.",
                    "You have not clicked yet. Call perceive_page if needed, then call "
                    "act_on_page with action 'click' for the button named 'Run once' "
                    "exactly once. Do not answer CLICK_DONE until the click succeeds.",
                ],
            )
            assert counter.clicks == 1, counter.clicks
            await page.wait_for_timeout(400)

            composer = frame.locator("textarea:visible").last
            await composer.fill(
                "Now call generate_skill to save the procedure you just demonstrated "
                f"as a reusable skill named {SKILL_NAME}. Description: Click the Run once "
                "button once on the demo page. The complete SKILL.md must have YAML "
                "frontmatter with name and description and instruct the agent to click "
                "the button named 'Run once' exactly once. Do not use recording or replay. "
                "After the candidate is submitted, reply exactly NATIVE_SKILL_SUBMITTED."
            )
            await composer.press("Enter")
            allowed = await _allow_until(
                frame,
                stop_testid="skill-candidate-card",
                timeout=240,
            )
            card = frame.get_by_test_id("skill-candidate-card").first
            await card.wait_for(timeout=90_000)
            candidate_text = await card.inner_text()
            candidate_id = (
                await frame.get_by_test_id("skill-candidate-id").first.inner_text()
            ).strip()
            tool_step_files = await _tool_step_files(frame)
            assert tool_step_files, "native skill trace has no ToolStep records"
            assert SKILL_NAME in candidate_text, candidate_text
            candidate_text, candidate_id = await _publish_candidate(frame, page)
            assert await _skill_installed(frame), "published skill not installed"

            await frame.get_by_role("button", name="助手", exact=True).click()
            await page.wait_for_timeout(500)
            new_chat = frame.get_by_test_id("header-new-chat")
            await new_chat.wait_for(timeout=10_000)
            await new_chat.click()
            state["newChatClicked"] = True
            await page.wait_for_timeout(500)
            await _click_with_retries(
                frame,
                counter,
                target_count=2,
                prompts=[
                    f"Call read_skill_file with skillName {SKILL_NAME}, then follow the "
                    "installed skill: call perceive_page, find the button named 'Run "
                    "once' in the fresh result, and call act_on_page with its ref and "
                    "action 'click' exactly once. Do not use recording or replay.",
                    "You have not clicked yet. Read the installed skill if needed, then "
                    "call perceive_page and act_on_page with action 'click' for the "
                    "button named 'Run once' exactly once.",
                ],
            )
            deadline = time.monotonic() + 10
            while counter.clicks < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert counter.clicks == 2, (
                f"expected generation+execution clicks, got {counter.clicks}"
            )
            assert state["modelRequests"] > 0
            assert not replay_requests, replay_requests
            assert not errors, errors
            return {
                "status": "passed",
                "model": MODEL_NAME,
                "modelUrl": MODEL_URL,
                "modelKind": "external",
                "skillName": SKILL_NAME,
                "candidateText": candidate_text[:500],
                "candidateId": candidate_id,
                "toolStepFiles": tool_step_files,
                "skillPublished": True,
                "skillInstalled": True,
                "newChatClicked": state["newChatClicked"],
                "skillContentInModelContext": state["skillContentInModelContext"],
                "skillGenerationToolObserved": state["skillGenerationToolObserved"],
                "skillGenerationToolCalled": state["skillGenerationToolCalled"],
                "modelRequests": state["modelRequests"],
                "grantRequests": len(grant_requests),
                "replayRequests": len(replay_requests),
                "confirmedRealClickCount": counter.clicks,
                "pageErrors": errors,
                "allowClicks": allowed,
            }
        except Exception as error:
            await page.screenshot(path=str(OUT / "native-skill-failure.png"), full_page=True)
            body_text = ""
            try:
                frame = next(
                    (item for item in page.frames if "/webskill/" in item.url),
                    None,
                )
                if frame is not None:
                    body_text = (await frame.locator("body").inner_text())[-4000:]
            except Exception:
                pass
            return {
                "status": "failed",
                "errorType": type(error).__name__,
                "error": safe_error(error),
                "model": MODEL_NAME,
                "modelUrl": MODEL_URL,
                "modelRequests": state["modelRequests"],
                "skillGenerationToolObserved": state["skillGenerationToolObserved"],
                "skillGenerationToolCalled": state["skillGenerationToolCalled"],
                "skillContentInModelContext": state["skillContentInModelContext"],
                "candidateText": candidate_text[:500],
                "candidateId": candidate_id,
                "toolStepFiles": tool_step_files,
                "grantRequests": len(grant_requests),
                "replayRequests": len(replay_requests),
                "fixtureClickCount": counter.clicks,
                "pageErrors": errors,
                "bodyText": body_text,
                "lastModelMessage": state["lastModelMessage"],
            }
        finally:
            await browser.close()
            counter.shutdown()
            counter.server_close()


if __name__ == "__main__":
    result = asyncio.run(run())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ui-result-native-skill.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
