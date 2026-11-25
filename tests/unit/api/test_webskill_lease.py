"""Real cross-process behavior for the browser command lease."""

from __future__ import annotations

import asyncio
import multiprocessing
import time
from contextlib import asynccontextmanager
from pathlib import Path

from octop.infra.control.browser.webskill import lease as lease_module
from octop.infra.control.browser.webskill.lease import (
    profile_command_gate,
    profile_lease,
)


def _writer(profile: str, directory: str, barrier, results) -> None:
    async def run() -> None:
        from octop_browser.settings import settings

        settings.profiles_dir = directory
        barrier.wait(timeout=10)
        async with profile_lease(profile):
            started = time.monotonic()
            await asyncio.sleep(0.2)
            results.put((started, time.monotonic()))

    asyncio.run(run())


def _hold_lease(profile: str, directory: str, ready) -> None:
    async def run() -> None:
        from octop_browser.settings import settings

        settings.profiles_dir = directory
        async with profile_lease(profile):
            ready.set()
            await asyncio.sleep(30)

    asyncio.run(run())


async def test_command_gate_uses_the_real_lease(tmp_path: Path, monkeypatch) -> None:
    from octop_browser.settings import settings

    monkeypatch.setattr(settings, "profiles_dir", str(tmp_path))
    async with profile_command_gate("user-1")("Runtime.evaluate", {}):
        assert (tmp_path / ".leases").is_dir()


async def test_screenshot_gate_bypasses_the_write_lease(monkeypatch) -> None:
    acquisitions: list[str] = []

    @asynccontextmanager
    async def tracked_lease(profile: str):
        acquisitions.append(profile)
        yield

    monkeypatch.setattr(lease_module, "profile_lease", tracked_lease)
    gate = lease_module.profile_command_gate("user-1")
    async with gate("DOM.getDocument", {}):
        pass
    async with gate("Page.captureScreenshot", {}):
        pass
    async with gate("Runtime.evaluate", {}):
        pass
    async with gate("Input.insertText", {}):
        pass

    assert acquisitions == ["user-1", "user-1"]


async def test_lease_timeout_fails_closed(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    from contextvars import Context

    import pytest
    from octop_browser import lease as lease_module

    from octop.infra.control.browser.webskill.sessions import BrowserAgentError

    monkeypatch.setattr(lease_module.browser_settings, "profiles_dir", str(tmp_path))
    now = [100.0]
    monkeypatch.setattr(lease_module.time, "monotonic", lambda: now[0])

    async def advance_time(_seconds: float) -> None:
        now[0] += 11.0

    async def compete() -> None:
        async with profile_lease("user-1"):
            pass

    monkeypatch.setattr(lease_module.asyncio, "sleep", advance_time)
    async with profile_lease("user-1"):
        with pytest.raises(BrowserAgentError) as error:
            await Context().run(asyncio.create_task, compete())
    assert error.value.code == "WRITE_LOCKED"


def test_windows_lock_and_unlock_use_the_same_byte(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from octop_browser import lease as lease_module

    modes: list[int] = []
    offsets: list[int] = []

    def locking(_fd: int, mode: int, _count: int) -> None:
        modes.append(mode)
        offsets.append(handle.tell())

    msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
    monkeypatch.setattr(lease_module.os, "name", "nt")
    monkeypatch.setattr(lease_module.importlib, "import_module", lambda _name: msvcrt)
    with (tmp_path / "lease.json").open("a+b") as handle:
        handle.write(b"metadata")
        lease_module._lock(handle)
        handle.seek(len(b"metadata"))
        lease_module._unlock(handle)
    assert offsets == [0, 0]
    assert modes == [1, 2]


def test_two_processes_cannot_write_at_the_same_time(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.JoinableQueue()
    processes = [
        context.Process(
            target=_writer,
            args=("user-1", str(tmp_path), barrier, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    intervals = sorted(results.get(timeout=1) for _ in processes)
    assert len(intervals) == 2
    first, second = intervals
    assert second[0] >= first[1] - 0.02


def test_process_crash_releases_the_lease(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_hold_lease, args=("user-1", str(tmp_path), ready))
    process.start()
    assert ready.wait(timeout=10)
    process.terminate()
    process.join(timeout=10)
    assert process.exitcode != 0

    async def acquire() -> None:
        from octop_browser.settings import settings

        settings.profiles_dir = str(tmp_path)
        async with profile_lease("user-1"):
            return

    asyncio.run(acquire())


def _create_browser(profile: str, directory: str, barrier, results) -> None:
    async def run() -> None:
        import octop_browser.session as session_module
        from octop_browser import BrowserSession
        from octop_browser.profile import ProfileManager
        from octop_browser.settings import settings

        settings.profiles_dir = directory
        started: float | None = None

        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def connect(self, _ws_url: str) -> None:
                assert started is not None
                await asyncio.sleep(0.02)
                results.put((started, time.monotonic()))

            async def enable_domain(self, _domain: str) -> None:
                return

            async def send(self, *_args, **_kwargs) -> dict:
                return {}

            async def close(self) -> None:
                return

        async def launch(_profile, *, headless: bool) -> None:
            nonlocal started
            started = time.monotonic()
            await asyncio.sleep(0.2)

        async def page_ws_url(_port: int, *, profile=None) -> str:
            return "ws://fixture"

        session_module.CDPClient = FakeClient
        session_module.launch_or_attach = launch
        session_module.get_page_ws_url = page_ws_url
        barrier.wait(timeout=10)
        browser = await BrowserSession.create(
            profile=profile,
            mode="headless",
            profile_manager=ProfileManager(base_dir=Path(directory)),
            command_gate=profile_command_gate(profile),
        )
        await browser.close()

    asyncio.run(run())


def test_two_processes_serialize_browser_session_connect(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.JoinableQueue()
    processes = [
        context.Process(
            target=_create_browser,
            args=("user-1", str(tmp_path), barrier, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    intervals = sorted(results.get(timeout=2) for _ in processes)
    assert len(intervals) == 2
    first, second = intervals
    assert second[0] >= first[1] - 0.02
