"""Cross-process CDP command leases backed by the operating system."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any, cast

from octop_browser.settings import settings as browser_settings

from .sessions import BrowserAgentError

CommandGate = Callable[[str, dict[str, Any]], AbstractAsyncContextManager[None]]


def _lock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt = cast(Any, importlib.import_module("msvcrt"))

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt = cast(Any, importlib.import_module("msvcrt"))

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def lease_path(profile: str) -> Path:
    digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()
    return Path(browser_settings.profiles_dir) / ".leases" / f"{digest}.json"


@asynccontextmanager
async def profile_lease(profile: str) -> AsyncIterator[None]:
    path = lease_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + 10.0
        while True:
            try:
                _lock(handle)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise BrowserAgentError(
                        "WRITE_LOCKED", "Another writer holds the browser lease"
                    ) from error
                await asyncio.sleep(0.05)
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "profile": profile,
                        "acquiredAt": time.time(),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            handle.flush()
            yield
        finally:
            _unlock(handle)


def profile_command_gate(profile: str) -> CommandGate:
    @asynccontextmanager
    async def gate(_method: str, _params: dict[str, Any]) -> AsyncIterator[None]:
        async with profile_lease(profile):
            yield

    return gate
