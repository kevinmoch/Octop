"""Apply the configured outbound proxy mode to the whole process.

Octop's outbound HTTP clients (httpx, urllib, and the langchain-openai layer
on top of them) read proxy settings from the environment by default. On
Windows that includes the per-user WinINET registry proxy — the very proxy a
browser uses — without the browser's ProxyOverride exception list. Inside
corporate networks that silently captures intranet-bound provider traffic
and returns a block page instead of the provider's API response.

``config.json → outbound_proxy_mode = "direct"`` (or
``OCTOP_OUTBOUND_PROXY_MODE=direct``) rewrites the process environment so
every default client goes straight to the target: httpx ``trust_env`` finds
``NO_PROXY="*"`` (via Octop's patched ``get_environment_proxies``) and
urllib's ``proxy_bypass`` declines every host. ``system`` (the default)
restores the inherited environment captured at first apply.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

_direct_applied = False
_saved: dict[str, str | None] = {}


def _snapshot_inherited() -> None:
    """Remember the inherited proxy env once, so ``system`` can restore it."""
    global _saved
    if _saved:
        return
    _saved = {name: os.environ.get(name) for name in _PROXY_ENV_VARS}


def apply_outbound_proxy_mode(mode: str) -> None:
    """Make default outbound clients honor ``mode`` (``system`` or ``direct``).

    Idempotent per mode: repeated calls with the same mode are no-ops, and
    switching back to ``system`` restores the inherited environment captured
    on the first call. Values other than ``direct`` are treated as ``system``;
    ``load_config`` already rejects invalid persisted values.
    """
    global _direct_applied
    _snapshot_inherited()
    if mode.strip().lower() != "direct":
        if _direct_applied:
            for name, value in _saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            _direct_applied = False
            logger.info("outbound proxy mode: system (restored inherited proxy env)")
        return
    if _direct_applied:
        return
    for name in _PROXY_ENV_VARS:
        os.environ.pop(name, None)
    # ``*`` bypasses every host in httpx (incl. Octop's patched mount builder)
    # and in urllib's proxy_bypass. A single NO_PROXY keeps both stacks direct.
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    _direct_applied = True
    logger.info("outbound proxy mode: direct (outbound clients bypass all proxies)")


def outbound_direct_active() -> bool:
    """True after ``direct`` has been applied and not restored."""
    return _direct_applied
