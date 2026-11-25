from __future__ import annotations

import hmac
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from octop.infra.desktop import desktop_shell_enabled

_FILENAME = "desktop-bootstrap-nonce"
_LOCK = threading.Lock()


def nonce_path(server: Any) -> Path:
    return Path(server.paths.root) / _FILENAME


def issue_nonce(server: Any) -> None:
    if not desktop_shell_enabled():
        return
    path = nonce_path(server)
    value = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)
    if os.name == "posix":
        os.chmod(path, 0o600)


def clear_nonce(server: Any) -> None:
    nonce_path(server).unlink(missing_ok=True)


def consume_nonce(server: Any, provided: str) -> bool:
    if not provided or len(provided) > 256:
        return False
    with _LOCK:
        try:
            expected = nonce_path(server).read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if not hmac.compare_digest(expected, provided):
            return False
        clear_nonce(server)
        return True
