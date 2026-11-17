"""Behavior tests for shared browser frame capture."""

from __future__ import annotations

import asyncio

import pytest

from octop.infra.control.browser import frame_hub
from octop.infra.control.browser.webskill.sessions import BrowserAgentError


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
        assert await first.next_frame() == "frame-1"
        assert await second.next_frame() == "frame-1"
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
