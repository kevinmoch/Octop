"""Model-target lifecycle independent of the user's visible browser tab."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .oopif import OopifSession, attach_page_target
from .sessions import MAX_MODEL_TARGETS, BrowserAgentError, BrowserAgentSession


async def reset_device_metrics_if_windows(browser: Any) -> None:
    if os.name != "nt":
        return
    await browser._internal.client.send("Emulation.clearDeviceMetricsOverride")  # noqa: SLF001


def _forget_target(session: BrowserAgentSession, target_id: str) -> None:
    session.model_targets.discard(target_id)
    for handle, target in list(session.tab_handles.items()):
        if target == target_id:
            session.tab_handles.pop(handle, None)


async def follow_active_target(session: BrowserAgentSession, browser: Any) -> str | None:
    """Explicit user follow: select the currently visible live tab.

    This is deliberately *not* called by list/perceive/attach. Those paths must
    fail closed when the model target is gone; only an explicit follow action
    may move the model pointer and admit the active page.
    """
    try:
        result = await browser.list_tabs()
    except Exception:  # noqa: BLE001 - follow is best effort
        return None
    raw = (result.metadata or {}).get("tabs", []) if result.success else []
    if not isinstance(raw, list):
        return None
    active = next(
        (
            tab
            for tab in raw
            if isinstance(tab, dict)
            and tab.get("active") is True
            and isinstance(tab.get("tab_id"), str)
        ),
        None,
    )
    if active is None:
        return None
    target = str(active["tab_id"])
    live = {
        str(tab["tab_id"])
        for tab in raw
        if isinstance(tab, dict) and isinstance(tab.get("tab_id"), str)
    }
    for stale in list(session.model_targets):
        if stale not in live:
            _forget_target(session, stale)
    if target not in session.model_targets and len(session.model_targets) >= MAX_MODEL_TARGETS:
        return None
    if session.base_target_id not in live:
        session.base_target_id = target
    session.model_targets.add(target)
    session.model_target = target
    session.document = None
    session.refs.clear()
    return target


async def attach_model_target(session: BrowserAgentSession, browser: Any) -> OopifSession | None:
    if session.model_target is None or session.base_target_id in {None, session.model_target}:
        return None
    try:
        return await attach_page_target(browser._internal.client, session.model_target)  # noqa: SLF001
    except BrowserAgentError:
        # Preserve the attach error's own code. A transient CDP/session failure
        # is not evidence that the user closed the model target.
        raise
    except Exception as error:
        raise BrowserAgentError(
            "TOOL_EXECUTION_FAILED",
            "The model target could not be attached",
        ) from error


async def page_target_ids(client: Any) -> set[str]:
    result = await client.send("Target.getTargets")
    return {
        str(info["targetId"])
        for info in result.get("targetInfos", [])
        if isinstance(info, dict)
        and info.get("type") == "page"
        and isinstance(info.get("targetId"), str)
    }


def admit_model_target(session: BrowserAgentSession, target_id: str) -> bool:
    if target_id in session.model_targets:
        session.model_target = target_id
        return True
    if len(session.model_targets) >= MAX_MODEL_TARGETS:
        return False
    session.model_targets.add(target_id)
    session.model_target = target_id
    return True


async def newly_opened_page_target(client: Any, previous: set[str]) -> str | None:
    current = await page_target_ids(client)
    created = current - previous
    if len(created) != 1:
        return None
    return next(iter(created))


async def wait_for_new_page_target(
    client: Any, previous: set[str], *, timeout: float = 1.2
) -> str | None:
    """Wait briefly for the tab a click just opened.

    Chrome creates the target asynchronously after the input event; reading
    Target.getTargets immediately can miss it and the model then perceives the
    old page and clicks again, producing duplicate tabs.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        target = await newly_opened_page_target(client, previous)
        if target is not None or loop.time() >= deadline:
            return target
        await asyncio.sleep(0.1)
