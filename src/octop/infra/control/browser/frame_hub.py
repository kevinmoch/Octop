"""Share one browser capture loop across subscribers of the same profile."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress

from .webskill.sessions import BrowserAgentError

logger = logging.getLogger(__name__)

MAX_STREAM_SUBSCRIBERS_PER_PROFILE = 4


class CaptureSubscription:
    def __init__(self, hub: ProfileFrameHub) -> None:
        self.hub = hub
        self.last_sequence = 0
        self.closed = False

    async def next_frame(self) -> str | None:
        async with self.hub.condition:
            if self.closed:
                return None
            if self.hub.task is None:
                self.hub.start_locked()
            if self.last_sequence == self.hub.sequence:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.hub.condition.wait(), timeout=0.5)
            if self.closed or self.last_sequence == self.hub.sequence:
                return None
            self.last_sequence = self.hub.sequence
            return self.hub.last_frame

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.hub.release(self)


class ProfileFrameHub:
    def __init__(
        self,
        capture: Callable[[], Awaitable[str | None]],
        *,
        interval: float = 1 / 12,
    ) -> None:
        self.capture = capture
        self.interval = interval
        self.condition = asyncio.Condition()
        self.last_frame: str | None = None
        self.sequence = 0
        self.subscriber_count = 0
        self.task: asyncio.Task[None] | None = None

    def start_locked(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def subscribe(self) -> CaptureSubscription:
        async with self.condition:
            if self.subscriber_count >= MAX_STREAM_SUBSCRIBERS_PER_PROFILE:
                raise BrowserAgentError(
                    "STREAM_LIMIT", "This browser already has too many active viewers"
                )
            self.subscriber_count += 1
            if self.task is None:
                self.start_locked()
            return CaptureSubscription(self)

    async def _run(self) -> None:
        try:
            while True:
                try:
                    frame = await self.capture()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("Browser frame capture failed", exc_info=True)
                    frame = None
                async with self.condition:
                    if self.subscriber_count == 0:
                        return
                    if frame is not None:
                        self.last_frame = frame
                        self.sequence += 1
                        self.condition.notify_all()
                await asyncio.sleep(self.interval)
        finally:
            async with self.condition:
                if self.task is asyncio.current_task():
                    self.task = None
                self.condition.notify_all()

    async def release(self, subscription: CaptureSubscription) -> None:
        async with self.condition:
            self.subscriber_count -= 1
            self.condition.notify_all()
            if self.subscriber_count > 0:
                return
            task = self.task
            self.task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


_HUBS: dict[str, ProfileFrameHub] = {}


def capture_subscription(
    profile: str,
    capture: Callable[[], Awaitable[str | None]],
    *,
    interval: float = 1 / 12,
) -> AbstractAsyncContextManager[CaptureSubscription]:
    hub = _HUBS.get(profile)
    if hub is None:
        hub = ProfileFrameHub(capture, interval=interval)
        _HUBS[profile] = hub

    @asynccontextmanager
    async def subscription() -> AsyncIterator[CaptureSubscription]:
        try:
            active = await hub.subscribe()
        except BaseException:
            if hub.subscriber_count == 0 and hub.task is None and _HUBS.get(profile) is hub:
                _HUBS.pop(profile, None)
            raise
        try:
            yield active
        finally:
            await active.close()
            if hub.subscriber_count == 0 and _HUBS.get(profile) is hub:
                _HUBS.pop(profile, None)

    return subscription()
