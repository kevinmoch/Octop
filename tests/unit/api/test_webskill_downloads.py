"""Session-scoped completed downloads: opaque ids, no paths, bounded reads."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

from octop.infra.control.browser.webskill.downloads import (
    ensure_downloads,
    list_downloads,
    read_download,
)
from octop.infra.control.browser.webskill.sessions import BrowserAgentSession


class FakeDownloadClient:
    def __init__(self) -> None:
        self.listeners: dict[str, list[object]] = {}
        self.calls: list[str] = []

    def on(self, event: str, callback: object) -> None:
        self.listeners.setdefault(event, []).append(callback)

    async def send(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self.calls.append(method)
        assert method == "Browser.setDownloadBehavior"
        assert isinstance(params, dict)
        return {}

    def emit(self, event: str, params: dict[str, object]) -> None:
        for callback in self.listeners.get(event, []):
            assert callable(callback)
            callback(params)


class FakeDownloadBrowser:
    def __init__(self, client: FakeDownloadClient) -> None:
        self._internal = SimpleNamespace(client=client)


def _session() -> BrowserAgentSession:
    return BrowserAgentSession(id="session-1", user_id=1, profile="user-1")


def _resolver(
    browser: FakeDownloadBrowser,
) -> Callable[[str, bool], Awaitable[FakeDownloadBrowser]]:
    async def resolve(profile: str, create: bool = False) -> FakeDownloadBrowser:
        return browser

    return resolve


def test_completed_download_is_listed_and_read_by_opaque_id(tmp_path: Path) -> None:
    client = FakeDownloadClient()
    browser = FakeDownloadBrowser(client)
    session = _session()

    async def scenario() -> None:
        await ensure_downloads(session, resolver=_resolver(browser))
        assert session.downloads_initialized is True
        assert "Browser.setDownloadBehavior" in client.calls
        assert session.download_dir is not None
        directory = Path(session.download_dir)
        (directory / "report.txt").write_bytes(b"download body")
        client.emit(
            "Browser.downloadWillBegin",
            {"guid": "guid-1", "suggestedFilename": "report.txt"},
        )
        client.emit(
            "Browser.downloadProgress",
            {"guid": "guid-1", "state": "completed"},
        )
        await asyncio.sleep(0.15)

    asyncio.run(scenario())
    entries = list_downloads(session)
    assert len(entries) == 1
    assert entries[0]["name"] == "report.txt"
    assert entries[0]["contentType"] == "text/plain"
    assert entries[0]["size"] == len(b"download body")
    content_type, data = read_download(session, entries[0]["id"])
    assert content_type == "text/plain"
    assert data == b"download body"
    assert "download_dir" not in entries[0]


def test_download_rejects_unknown_id_and_cleans_up(tmp_path: Path) -> None:
    session = _session()
    directory = tmp_path / "downloads"
    directory.mkdir()
    session.download_dir = str(directory)
    session.downloads_initialized = True
    from octop.infra.control.browser.webskill.downloads import cleanup_downloads

    try:
        read_download(session, "dl_missing")
    except Exception as error:
        assert "not available" in str(error)
    else:
        raise AssertionError("missing download should fail")
    cleanup_downloads(session)
    assert session.download_dir is None
    assert not directory.exists()
