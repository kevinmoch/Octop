"""Model-target lifecycle independent of the user's visible browser tab."""

from __future__ import annotations

from typing import Any

from .oopif import OopifSession, attach_page_target
from .sessions import BrowserAgentError, BrowserAgentSession


async def attach_model_target(session: BrowserAgentSession, browser: Any) -> OopifSession | None:
    if session.model_target is None or session.base_target_id in {None, session.model_target}:
        return None
    try:
        return await attach_page_target(browser._internal.client, session.model_target)  # noqa: SLF001
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


async def newly_opened_page_target(client: Any, previous: set[str]) -> str | None:
    current = await page_target_ids(client)
    created = current - previous
    if len(created) != 1:
        return None
    return next(iter(created))
