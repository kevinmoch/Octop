"""Model-target lifecycle independent of the user's visible browser tab."""

from __future__ import annotations

from typing import Any

from .oopif import OopifSession, attach_page_target
from .sessions import MAX_MODEL_TARGETS, BrowserAgentError, BrowserAgentSession


async def attach_model_target(session: BrowserAgentSession, browser: Any) -> OopifSession | None:
    if session.model_target is None or session.base_target_id in {None, session.model_target}:
        return None
    try:
        return await attach_page_target(browser._internal.client, session.model_target)  # noqa: SLF001
    except BrowserAgentError as error:
        if error.code == "WRITE_LOCKED":
            raise
        raise BrowserAgentError(
            "PAGE_TAB_EXPIRED", "The model target is no longer available"
        ) from error
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
