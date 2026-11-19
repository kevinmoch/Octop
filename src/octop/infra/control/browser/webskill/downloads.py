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


def _completed_file(directory: Path, preferred: str) -> Path | None:
    try:
        files = [
            item
            for item in directory.iterdir()
            if item.is_file() and not item.name.endswith(".crdownload")
        ]
    except OSError:
        return None
    exact = next((item for item in files if item.name == preferred), None)
    if exact is not None:
        return exact
    return max(files, key=lambda item: item.stat().st_mtime, default=None)


async def ensure_downloads(
    session: BrowserAgentSession,
    *,
    resolver: Any,
) -> None:
    if session.downloads_initialized:
        return
    browser = await resolve_browser(session, resolver)
    client = browser._internal.client  # noqa: SLF001
    directory = Path(tempfile.mkdtemp(prefix="octop-webskill-downloads-"))
    session.download_dir = str(directory)
    session.download_guids = {}

    def will_begin(params: dict[str, Any]) -> None:
        guid = params.get("guid")
        if isinstance(guid, str):
            session.download_guids[guid] = {
                "name": str(params.get("suggestedFilename") or "download")[:300],
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

    def progress(params: dict[str, Any]) -> None:
        guid = params.get("guid")
        if not isinstance(guid, str) or params.get("state") != "completed":
            return
        meta = session.download_guids.pop(guid, None)
        if meta is None:
            return
        loop = asyncio.get_running_loop()
        loop.create_task(_finish_download(session, guid, meta, directory))

    client.on("Browser.downloadWillBegin", will_begin)
    client.on("Browser.downloadProgress", progress)
    await client.send(
        "Browser.setDownloadBehavior",
        {
            "behavior": "allow",
            "downloadPath": str(directory),
            "eventsEnabled": True,
        },
    )
    session.downloads_initialized = True


async def _finish_download(
    session: BrowserAgentSession,
    guid: str,
    meta: dict[str, str],
    directory: Path,
) -> None:
    await asyncio.sleep(0.05)
    source = _completed_file(directory, meta["name"])
    if source is None:
        return
    try:
        size = source.stat().st_size
    except OSError:
        return
    if size > MAX_DOWNLOAD_BYTES:
        source.unlink(missing_ok=True)
        return
    download_id = _new_id()
    target = directory / download_id
    try:
        source.replace(target)
    except OSError:
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
    session.downloads.clear()
    session.download_guids.clear()
    if session.download_dir:
        shutil.rmtree(session.download_dir, ignore_errors=True)
        session.download_dir = None
