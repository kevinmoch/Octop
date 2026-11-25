"""Behavior tests for shared browser frame capture."""

from __future__ import annotations

import asyncio

import pytest

from octop.api.routers.browser import stream
from octop.infra.control.browser import frame_hub
from octop.infra.control.browser.webskill.sessions import BrowserAgentError


async def _next_frame(
    subscription: frame_hub.CaptureSubscription,
) -> frame_hub.CapturedFrame:
    frame = await subscription.next_frame()
    assert frame is not None
    return frame


async def test_two_subscribers_share_one_capture_and_stop_on_close() -> None:
    captures = 0

    async def capture() -> str:
        nonlocal captures
        captures += 1
        return f"frame-{captures}"

    async with (
        frame_hub.capture_subscription("user-1", capture, interval=0.01) as first,
        frame_hub.capture_subscription("user-1", capture, interval=0.01) as second,
    ):
        captured = await _next_frame(first)
        assert captured.data == "frame-1"
        assert captured.frame_id == 1
        assert captured.captured_at > 0
        assert await _next_frame(second) == captured
        await asyncio.sleep(0.03)
        assert captures <= 4
        assert first.hub is second.hub
        assert first.hub.subscriber_count == 2
        task = first.hub.task
        assert task is not None

    assert task.done()
    assert "user-1" not in frame_hub._HUBS
    completed = captures
    await asyncio.sleep(0.03)
    assert captures == completed


async def test_capture_subscribers_have_a_bounded_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frame_hub, "MAX_STREAM_SUBSCRIBERS_PER_PROFILE", 1)

    async def capture() -> None:
        return None

    async with frame_hub.capture_subscription("user-1", capture):
        with pytest.raises(BrowserAgentError) as error:
            async with frame_hub.capture_subscription("user-1", capture):
                pass
    assert error.value.code == "STREAM_LIMIT"
    assert "user-1" not in frame_hub._HUBS


async def test_failed_first_subscription_removes_empty_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frame_hub, "MAX_STREAM_SUBSCRIBERS_PER_PROFILE", 0)

    async def capture() -> None:
        return None

    with pytest.raises(BrowserAgentError) as error:
        async with frame_hub.capture_subscription("user-empty", capture):
            pass
    assert error.value.code == "STREAM_LIMIT"
    assert "user-empty" not in frame_hub._HUBS


async def test_existing_subscriber_recovers_after_capture_failure() -> None:
    captures = 0

    async def capture() -> str:
        nonlocal captures
        captures += 1
        if captures == 1:
            raise RuntimeError("capture failed")
        return "recovered"

    async with frame_hub.capture_subscription(
        "user-recovery", capture, interval=0.01
    ) as subscription:
        assert (await _next_frame(subscription)).data == "recovered"
        assert captures >= 2
        assert subscription.hub.task is not None
        assert not subscription.hub.task.done()


async def test_shared_capture_re_resolves_the_current_profile_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_session = object()
    second_session = object()
    current = first_session

    async def resolve(profile: str, *, create: bool) -> object:
        assert profile == "user-current"
        assert create is False
        return current

    async def capture(sess: object) -> str:
        return "first" if sess is first_session else "second"

    monkeypatch.setattr(stream, "resolve_harness_session", resolve)
    monkeypatch.setattr(stream, "_capture_jpeg", capture)
    async with (
        frame_hub.capture_subscription(
            "user-current",
            lambda: stream._capture_profile_jpeg("user-current"),
            interval=0.01,
        ) as first,
        frame_hub.capture_subscription(
            "user-current",
            lambda: stream._capture_profile_jpeg("user-current"),
            interval=0.01,
        ) as second,
    ):
        assert (await _next_frame(first)).data == "first"
        assert (await _next_frame(second)).data == "first"
        current = second_session
        await first.close()
        assert (await _next_frame(second)).data == "second"
        assert second.hub.subscriber_count == 1
