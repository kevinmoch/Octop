"""Faster preview frames must not multiply expensive tab/status polling."""

from unittest.mock import AsyncMock

import pytest

from octop.api.routers.browser import stream


@pytest.mark.asyncio
async def test_frames_refresh_independently_of_session_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = 0
    sent_frames = 0

    async def capture(_sess: object) -> str:
        nonlocal frames
        frames += 1
        return "jpeg"

    def record_send(message: dict[str, str]) -> None:
        nonlocal sent_frames
        if message["type"] == "frame":
            sent_frames += 1

    browser = object()
    snapshot = AsyncMock()
    send_json = AsyncMock(side_effect=record_send)
    monkeypatch.setattr(stream, "_send_session_snapshot", snapshot)
    monkeypatch.setattr(stream, "_capture_jpeg", capture)
    monkeypatch.setattr(stream, "resolve_harness_session", AsyncMock(return_value=browser))
    monkeypatch.setattr(stream, "_SESSION_INTERVAL_S", 60.0)
    await stream._stream_loop(
        send_json, lambda: sent_frames < 15, browser, "test", listen_only=False
    )
    assert sent_frames == 15
    assert frames in {15, 16}
    assert snapshot.await_count == 1
