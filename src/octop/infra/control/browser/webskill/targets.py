"""Model-target lifecycle independent of the user's visible browser tab."""

from __future__ import annotations

import asyncio
from typing import Any

from .oopif import OopifSession, attach_page_target
from .sessions import MAX_MODEL_TARGETS, BrowserAgentError, BrowserAgentSession


def _forget_target(session: BrowserAgentSession, target_id: str) -> None:
    session.model_targets.discard(target_id)
    for handle, target in list(session.tab_handles.items()):
        if target == target_id:
            session.tab_handles.pop(handle, None)


def recover_target_from_tabs(
    session: BrowserAgentSession, tabs: dict[str, dict[str, Any]]
) -> str | None:
    """Move the model pointer off a target the user closed.

    Prefer the tab the user is actually looking at, then the original base,
    then any live workset target. The model must not be trapped on a target
    that only exists in its own pointer after an external close.
    """
    live = set(tabs)
    closed = session.model_target
    changed = False
    for target in list(session.model_targets):
        if target not in live:
            _forget_target(session, target)
            changed = True
    if closed is not None and closed not in live:
        _forget_target(session, closed)
        changed = True
    active = next(
        (
            target
            for target, metadata in tabs.items()
            if metadata.get("active") is True and isinstance(target, str)
        ),
        None,
    )
    fallback = active
    if fallback is None and session.base_target_id in live:
        fallback = session.base_target_id
    if fallback is None:
        fallback = next(iter(live), None)
    if fallback is None:
        return None
    if session.base_target_id not in live:
        if session.base_target_id is not None:
            _forget_target(session, session.base_target_id)
        session.base_target_id = fallback
        changed = True
    if session.model_target != fallback:
        session.model_target = fallback
        changed = True
    session.model_targets.add(fallback)
    if changed:
        session.document = None
        session.refs.clear()
    return fallback


async def recover_closed_model_target(session: BrowserAgentSession, browser: Any) -> str | None:
    try:
        result = await browser.list_tabs()
    except Exception:  # noqa: BLE001 - recovery is best effort
        return None
    raw = (result.metadata or {}).get("tabs", []) if result.success else []
    if not isinstance(raw, list):
        return None
    tabs = {
        str(tab["tab_id"]): tab
        for tab in raw
        if isinstance(tab, dict) and isinstance(tab.get("tab_id"), str)
    }
    return recover_target_from_tabs(session, tabs)


async def attach_model_target(session: BrowserAgentSession, browser: Any) -> OopifSession | None:
    if session.model_target is None or session.base_target_id in {None, session.model_target}:
        return None
    try:
        return await attach_page_target(browser._internal.client, session.model_target)  # noqa: SLF001
    except BrowserAgentError as error:
        if error.code == "WRITE_LOCKED":
            raise
        recovered = await recover_closed_model_target(session, browser)
        if recovered is None:
            raise BrowserAgentError(
                "PAGE_TAB_EXPIRED", "The model target is no longer available"
            ) from error
        if session.base_target_id in {None, session.model_target}:
            return None
        try:
            return await attach_page_target(browser._internal.client, session.model_target)  # noqa: SLF001
        except Exception as retry_error:  # noqa: BLE001
            raise BrowserAgentError(
                "PAGE_TAB_EXPIRED", "The model target is no longer available"
            ) from retry_error
    except Exception as error:
        raise BrowserAgentError(
            "PAGE_TAB_EXPIRED", "The model target is no longer available"
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
