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


def test_download_is_session_scoped_and_cleanup_removes_bytes(tmp_path: Path) -> None:
    session = _session()
    directory = tmp_path / "downloads"
    directory.mkdir()
    path = directory / "secret.txt"
    path.write_bytes(b"secret")
    from octop.infra.control.browser.webskill.downloads import DownloadRecord, cleanup_downloads

    session.download_dir = str(directory)
    session.downloads_initialized = True
    session.downloads["dl_test"] = DownloadRecord(
        id="dl_test",
        name="secret.txt",
        content_type="text/plain",
        size=6,
        at="2026-11-19T00:00:00Z",
        path=path,
    )
    other = _session()
    other.id = "session-2"
    try:
        read_download(other, "dl_test")
    except Exception as error:
        assert "not available" in str(error)
    else:
        raise AssertionError("download must not cross sessions")
    cleanup_downloads(session)
    assert not path.exists()
    assert list_downloads(session) == []


def test_oversize_download_is_deleted_and_not_listed(tmp_path: Path, monkeypatch) -> None:
    import octop.infra.control.browser.webskill.downloads as downloads

    session = _session()
    directory = tmp_path / "downloads"
    directory.mkdir()
    session.download_dir = str(directory)
    session.downloads_initialized = True
    monkeypatch.setattr(downloads, "MAX_DOWNLOAD_BYTES", 5)
    (directory / "large.bin").write_bytes(b"123456")

    async def scenario() -> None:
        await downloads._finish_download(
            session,
            "guid-large",
            {"name": "large.bin", "at": "2026-11-19T00:00:00Z"},
            directory,
        )

    asyncio.run(scenario())
    assert list_downloads(session) == []
    assert not (directory / "large.bin").exists()
