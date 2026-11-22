"""Server-side, session-scoped completed downloads.

Chrome is redirected to a private temporary directory for the WebSkill profile.
The model never sees that path; it receives opaque ids and metadata only.
"""

from __future__ import annotations

import asyncio
import mimetypes
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .page_agent import resolve_browser
from .sessions import BrowserAgentError, BrowserAgentSession

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_TTL_SECONDS = 60 * 60
_PROFILE_LOCK = asyncio.Lock()
_PROFILE_OWNER: dict[str, str] = {}


@dataclass
class DownloadRecord:
    id: str
    name: str
    content_type: str
    size: int
    at: str
    path: Path


def _new_id() -> str:
    return f"dl_{secrets.token_urlsafe(18)}"


def _completed_file(directory: Path, preferred: str, guid: str | None = None) -> Path | None:
    try:
        files = [
            item
            for item in directory.iterdir()
            if item.is_file() and not item.name.endswith(".crdownload")
        ]
    except OSError:
        return None
    # Prefer the GUID-named file produced by ``allowAndName``. This is the only
    # CDP mode that gives a real GUID -> bytes binding even when two downloads
    # share a suggested filename. If the browser did not honor it, fall back to
    # the exact suggested filename; never fall back to "the newest file".
    if guid is not None:
        # In GUID mode there is no suggested-name fallback: that would reintroduce
        # the exact concurrent/collision ambiguity allowAndName was chosen to
        # remove. Missing GUID means the download is not published.
        return next((item for item in files if item.name == guid), None)
    return next((item for item in files if item.name == preferred), None)


def _allow_and_name_unsupported(error: Exception) -> bool:
    """Only an explicit invalid/unsupported behavior is a compatibility error.

    Do not match the method name ``Browser.setDownloadBehavior``: real CDP
    timeouts and send failures contain that substring and must surface as
    transient failures, not silently switch the profile to ``allow``.
    """
    text = str(error).lower()
    if "allowandname" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "not supported",
            "unsupported",
            "invalid parameter",
            "invalid parameters",
            "unknown parameter",
            "failed to parse",
        )
    )


async def ensure_downloads(
    session: BrowserAgentSession,
    *,
    resolver: Any,
) -> None:
    if session.downloads_initialized:
        return
    async with session.downloads_lock:
        if session.downloads_initialized:
            return
        browser = await resolve_browser(session, resolver)
        client = browser._internal.client  # noqa: SLF001
        async with _PROFILE_LOCK:
            owner = _PROFILE_OWNER.get(session.profile)
            if owner is not None and owner != session.id:
                raise BrowserAgentError(
                    "DOWNLOAD_SESSION_BUSY",
                    "Another WebSkill session owns this browser profile's downloads",
                )
            _PROFILE_OWNER[session.profile] = session.id
        directory = Path(tempfile.mkdtemp(prefix="octop-webskill-downloads-"))
        session.download_dir = str(directory)
        session.download_client = client
        session.download_guids = {}

        def will_begin(params: dict[str, Any]) -> None:
            if not session.downloads_initialized:
                return
            guid = params.get("guid")
            if isinstance(guid, str):
                session.download_guids[guid] = {
                    "name": str(params.get("suggestedFilename") or "download")[:300],
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }

        def progress(params: dict[str, Any]) -> None:
            if not session.downloads_initialized:
                return
            guid = params.get("guid")
            if not isinstance(guid, str) or params.get("state") != "completed":
                return
            meta = session.download_guids.pop(guid, None)
            if meta is None:
                return
            loop = asyncio.get_running_loop()
            task = loop.create_task(_finish_download(session, guid, meta, directory))
            session.download_tasks.add(task)
            task.add_done_callback(session.download_tasks.discard)

        session.download_listeners = [
            ("Browser.downloadWillBegin", will_begin),
            ("Browser.downloadProgress", progress),
        ]
        for event, callback in session.download_listeners:
            client.on(event, callback)
        try:
            await client.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allowAndName",
                    "downloadPath": str(directory),
                    "eventsEnabled": True,
                },
            )
            session.download_guid_named = True
        except Exception as error:
            # Only a recognizable "behavior not supported" error may fall back.
            # A transient CDP error must not silently change the whole profile's
            # download mode.
            if _allow_and_name_unsupported(error):
                try:
                    await client.send(
                        "Browser.setDownloadBehavior",
                        {
                            "behavior": "allow",
                            "downloadPath": str(directory),
                            "eventsEnabled": True,
                        },
                    )
                    session.download_guid_named = False
                except Exception:
                    _detach_listeners(session, client)
                    session.download_listeners = []
                    session.download_client = None
                    shutil.rmtree(directory, ignore_errors=True)
                    session.download_dir = None
                    async with _PROFILE_LOCK:
                        if _PROFILE_OWNER.get(session.profile) == session.id:
                            _PROFILE_OWNER.pop(session.profile, None)
                    raise
            else:
                _detach_listeners(session, client)
                session.download_listeners = []
                session.download_client = None
                shutil.rmtree(directory, ignore_errors=True)
                session.download_dir = None
                async with _PROFILE_LOCK:
                    if _PROFILE_OWNER.get(session.profile) == session.id:
                        _PROFILE_OWNER.pop(session.profile, None)
                raise
        session.downloads_initialized = True


def _detach_listeners(session: BrowserAgentSession, client: Any) -> None:
    off = getattr(client, "off", None)
    if callable(off):
        for event, callback in session.download_listeners:
            try:
                off(event, callback)
            except Exception:  # noqa: BLE001 - best-effort teardown
                continue


async def _finish_download(
    session: BrowserAgentSession,
    guid: str,
    meta: dict[str, str],
    directory: Path,
) -> None:
    source: Path | None = None
    # The completed event can arrive before Chrome has published/renamed the
    # final file. Poll briefly, but still only accept the GUID or exact
    # suggested name - never "the newest file in the directory".
    for _attempt in range(20):
        if not session.downloads_initialized or session.download_dir != str(directory):
            return
        source = _completed_file(
            directory,
            meta["name"],
            guid if session.download_guid_named else None,
        )
        if source is not None:
            break
        await asyncio.sleep(0.05)
    if source is None:
        _remember_failure(session, guid, meta["name"], "download file was not published")
        return
    try:
        size = source.stat().st_size
    except OSError:
        _remember_failure(session, guid, meta["name"], "download file disappeared")
        return
    if size > MAX_DOWNLOAD_BYTES:
        source.unlink(missing_ok=True)
        _remember_failure(session, guid, meta["name"], "download exceeds the size limit")
        return
    download_id = _new_id()
    target = directory / download_id
    try:
        source.replace(target)
    except OSError:
        _remember_failure(session, guid, meta["name"], "download file could not be stored")
        return
    content_type = mimetypes.guess_type(meta["name"])[0] or "application/octet-stream"
    session.downloads[download_id] = DownloadRecord(
        id=download_id,
        name=meta["name"],
        content_type=content_type,
        size=size,
        at=meta["at"],
        path=target,
    )


def _remember_failure(session: BrowserAgentSession, guid: str, name: str, reason: str) -> None:
    session.download_failures.append(
        {
            "guid": guid,
            "name": name[:300],
            "reason": reason,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    del session.download_failures[:-50]


def _purge_expired(session: BrowserAgentSession) -> None:
    now = datetime.now(UTC)
    for download_id, record in list(session.downloads.items()):
        try:
            created = datetime.strptime(record.at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        if (now - created).total_seconds() <= DOWNLOAD_TTL_SECONDS:
            continue
        record.path.unlink(missing_ok=True)
        session.downloads.pop(download_id, None)


def list_downloads(session: BrowserAgentSession) -> list[dict[str, Any]]:
    _purge_expired(session)
    return [
        {
            "id": record.id,
            "name": record.name,
            "contentType": record.content_type,
            "size": record.size,
            "at": record.at,
        }
        for record in sorted(session.downloads.values(), key=lambda item: item.at, reverse=True)
    ]


def list_download_failures(session: BrowserAgentSession) -> list[dict[str, str]]:
    return list(session.download_failures)


def read_download(session: BrowserAgentSession, download_id: str) -> tuple[str, bytes]:
    _purge_expired(session)
    record = session.downloads.get(download_id)
    if record is None or not record.path.is_file():
        raise BrowserAgentError("TOOL_EXECUTION_FAILED", "The download is not available")
    try:
        data = record.path.read_bytes()
    except OSError as error:
        raise BrowserAgentError(
            "TOOL_EXECUTION_FAILED", "The download could not be read"
        ) from error
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise BrowserAgentError("TOOL_EXECUTION_FAILED", "The download exceeds the size limit")
    return record.content_type, data


def cleanup_downloads(session: BrowserAgentSession) -> None:
    session.downloads_initialized = False
    session.download_guid_named = False
    session.downloads.clear()
    session.download_guids.clear()
    session.download_failures.clear()
    for task in list(session.download_tasks):
        task.cancel()
    session.download_tasks.clear()
    if session.download_client is not None:
        _detach_listeners(session, session.download_client)
    session.download_listeners.clear()
    session.download_client = None
    if session.download_dir:
        shutil.rmtree(session.download_dir, ignore_errors=True)
        session.download_dir = None
    if _PROFILE_OWNER.get(session.profile) == session.id:
        _PROFILE_OWNER.pop(session.profile, None)
