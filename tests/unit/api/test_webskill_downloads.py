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
    def __init__(self, *, reject_allow_and_name: bool = False) -> None:
        self.listeners: dict[str, list[object]] = {}
        self.calls: list[str] = []
        self.reject_allow_and_name = reject_allow_and_name

    def on(self, event: str, callback: object) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def off(self, event: str, callback: object) -> None:
        listeners = self.listeners.get(event, [])
        if callback in listeners:
            listeners.remove(callback)

    async def send(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self.calls.append(method)
        assert method == "Browser.setDownloadBehavior"
        assert isinstance(params, dict)
        if self.reject_allow_and_name and params.get("behavior") == "allowAndName":
            raise RuntimeError("allowAndName is not supported")
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
        (directory / "guid-1").write_bytes(b"download body")
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
    from octop.infra.control.browser.webskill.downloads import cleanup_downloads

    cleanup_downloads(session)


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


def test_expired_download_is_purged(tmp_path: Path) -> None:
    session = _session()
    directory = tmp_path / "downloads"
    directory.mkdir()
    path = directory / "old.txt"
    path.write_bytes(b"old")
    from octop.infra.control.browser.webskill.downloads import DownloadRecord

    session.download_dir = str(directory)
    session.downloads_initialized = True
    session.downloads["dl_old"] = DownloadRecord(
        id="dl_old",
        name="old.txt",
        content_type="text/plain",
        size=3,
        at="2020-01-01T00:00:00Z",
        path=path,
    )
    assert list_downloads(session) == []
    assert not path.exists()


def test_ambiguous_download_name_is_not_bound_to_the_newest_file(tmp_path: Path) -> None:
    client = FakeDownloadClient()
    browser = FakeDownloadBrowser(client)
    session = _session()

    async def scenario() -> None:
        await ensure_downloads(session, resolver=_resolver(browser))
        directory = Path(session.download_dir or "")
        (directory / "unrelated.txt").write_bytes(b"wrong bytes")
        client.emit(
            "Browser.downloadWillBegin",
            {"guid": "guid-missing", "suggestedFilename": "wanted.txt"},
        )
        client.emit(
            "Browser.downloadProgress",
            {"guid": "guid-missing", "state": "completed"},
        )
        await asyncio.sleep(1.2)

    asyncio.run(scenario())
    assert list_downloads(session) == []
    from octop.infra.control.browser.webskill.downloads import (
        cleanup_downloads,
        list_download_failures,
    )

    failures = list_download_failures(session)
    assert len(failures) == 1
    assert failures[0]["reason"] == "download file was not published"
    cleanup_downloads(session)


def test_cleanup_detaches_listeners_and_ignores_late_events() -> None:
    client = FakeDownloadClient()
    browser = FakeDownloadBrowser(client)
    session = _session()

    async def scenario() -> None:
        await ensure_downloads(session, resolver=_resolver(browser))
        directory = Path(session.download_dir or "")
        (directory / "late.txt").write_bytes(b"late")
        from octop.infra.control.browser.webskill.downloads import cleanup_downloads

        cleanup_downloads(session)
        assert client.listeners["Browser.downloadWillBegin"] == []
        assert client.listeners["Browser.downloadProgress"] == []
        client.emit(
            "Browser.downloadWillBegin",
            {"guid": "late", "suggestedFilename": "late.txt"},
        )
        client.emit(
            "Browser.downloadProgress",
            {"guid": "late", "state": "completed"},
        )
        await asyncio.sleep(0.15)
        assert list_downloads(session) == []

    asyncio.run(scenario())


def test_allow_and_name_falls_back_to_exact_suggested_name() -> None:
    client = FakeDownloadClient(reject_allow_and_name=True)
    browser = FakeDownloadBrowser(client)
    session = _session()

    async def scenario() -> None:
        await ensure_downloads(session, resolver=_resolver(browser))
        assert session.download_guid_named is False
        directory = Path(session.download_dir or "")
        (directory / "fallback.txt").write_bytes(b"fallback")
        client.emit(
            "Browser.downloadWillBegin",
            {"guid": "guid-fallback", "suggestedFilename": "fallback.txt"},
        )
        client.emit(
            "Browser.downloadProgress",
            {"guid": "guid-fallback", "state": "completed"},
        )
        await asyncio.sleep(0.15)

    asyncio.run(scenario())
    entries = list_downloads(session)
    assert [entry["name"] for entry in entries] == ["fallback.txt"]
    from octop.infra.control.browser.webskill.downloads import cleanup_downloads

    cleanup_downloads(session)


def test_second_session_cannot_take_over_profile_downloads() -> None:
    client = FakeDownloadClient()
    browser = FakeDownloadBrowser(client)
    first = _session()

    async def scenario() -> None:
        await ensure_downloads(first, resolver=_resolver(browser))
        second = BrowserAgentSession(id="session-2", user_id=1, profile="user-1")
        try:
            await ensure_downloads(second, resolver=_resolver(browser))
        except Exception as error:
            assert getattr(error, "code", "") == "DOWNLOAD_SESSION_BUSY"
        else:
            raise AssertionError("a second owner must be rejected")
        assert second.downloads_initialized is False

    asyncio.run(scenario())
    from octop.infra.control.browser.webskill.downloads import cleanup_downloads

    cleanup_downloads(first)


def test_two_same_named_guids_keep_distinct_bytes() -> None:
    client = FakeDownloadClient()
    browser = FakeDownloadBrowser(client)
    session = _session()

    async def scenario() -> None:
        await ensure_downloads(session, resolver=_resolver(browser))
        directory = Path(session.download_dir or "")
        (directory / "guid-a").write_bytes(b"alpha")
        (directory / "guid-b").write_bytes(b"beta")
        for guid in ("guid-a", "guid-b"):
            client.emit(
                "Browser.downloadWillBegin",
                {"guid": guid, "suggestedFilename": "same.txt"},
            )
        for guid in ("guid-b", "guid-a"):
            client.emit(
                "Browser.downloadProgress",
                {"guid": guid, "state": "completed"},
            )
        for _ in range(20):
            if len(list_downloads(session)) == 2:
                break
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    entries = list_downloads(session)
    assert len(entries) == 2
    assert {entry["name"] for entry in entries} == {"same.txt"}
    bodies = {read_download(session, entry["id"])[1] for entry in entries}
    assert bodies == {b"alpha", b"beta"}
    from octop.infra.control.browser.webskill.downloads import cleanup_downloads

    cleanup_downloads(session)


def test_guid_mode_does_not_fall_back_to_suggested_name() -> None:
    client = FakeDownloadClient()
    browser = FakeDownloadBrowser(client)
    session = _session()

    async def scenario() -> None:
        await ensure_downloads(session, resolver=_resolver(browser))
        assert session.download_guid_named is True
        directory = Path(session.download_dir or "")
        (directory / "suggested.txt").write_bytes(b"must not be used")
        client.emit(
            "Browser.downloadWillBegin",
            {"guid": "missing-guid", "suggestedFilename": "suggested.txt"},
        )
        client.emit(
            "Browser.downloadProgress",
            {"guid": "missing-guid", "state": "completed"},
        )
        await asyncio.sleep(1.2)

    asyncio.run(scenario())
    assert list_downloads(session) == []
    from octop.infra.control.browser.webskill.downloads import (
        cleanup_downloads,
        list_download_failures,
    )

    failures = list_download_failures(session)
    assert len(failures) == 1
    assert failures[0]["reason"] == "download file was not published"
    cleanup_downloads(session)
