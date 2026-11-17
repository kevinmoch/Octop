"""Attach restricted OOPIF operations to their actual CDP target."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from octop_browser.cdp.client import CDPSessionError

from .frames import FRAME_SCOPE_ERROR, FrameRoute
from .sessions import BrowserAgentError


def _url_key(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}{parsed.path}{query}"


@dataclass
class OopifSession:
    client: Any
    target_id: str
    session_id: str
    frame_key: str
    execution_context_id: int
    url: str
    title: str
    context_listener: Any
    closed: bool = False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.client.off("Runtime.executionContextCreated", self.context_listener)
        with contextlib.suppress(CDPSessionError):
            await self.client.close()


async def _attach(client: Any, target_id: str) -> OopifSession:
    attached = await client.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    session_id = str(attached.get("sessionId") or "")
    if not session_id:
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    scoped = client.with_session(session_id)
    try:
        contexts: list[dict[str, Any]] = []

        def on_context(params: dict[str, Any]) -> None:
            if params.get("sessionId") != session_id:
                return
            context = params.get("context")
            if isinstance(context, dict):
                contexts.append(context)

        scoped.on("Runtime.executionContextCreated", on_context)
        await scoped.enable_domain("Runtime")
        await scoped.enable_domain("DOM")
        await scoped.enable_domain("Page")
        context = next(
            (
                candidate
                for candidate in contexts
                if candidate.get("auxData", {}).get("isDefault") is True
            ),
            None,
        )
        if context is None or not isinstance(context.get("id"), int):
            raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
        context_id = int(context["id"])
        tree = await scoped.send("Page.getFrameTree")
        frame = tree.get("frameTree", {}).get("frame", {})
        loader_id = str(frame.get("loaderId") or "")
        if not loader_id:
            raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
        page = await scoped.send(
            "Runtime.evaluate",
            {
                "expression": "({url: location.href, title: document.title})",
                "contextId": context_id,
                "returnByValue": True,
            },
        )
        info = page.get("result", {}).get("value", {})
        return OopifSession(
            client=scoped,
            target_id=target_id,
            session_id=session_id,
            frame_key=f"oopif:{target_id}:{loader_id}",
            execution_context_id=context_id,
            url=str(info.get("url") or ""),
            title=str(info.get("title") or ""),
            context_listener=on_context,
        )
    except Exception as error:
        with contextlib.suppress(Exception):
            scoped.off("Runtime.executionContextCreated", on_context)
            await scoped.close()
        if isinstance(error, BrowserAgentError):
            raise
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR) from error


async def attach_oopif_route(client: Any, route: FrameRoute, parent_target_id: str) -> OopifSession:
    targets = await client.send("Target.getTargets")
    candidates = [
        target
        for target in targets.get("targetInfos", [])
        if isinstance(target, dict)
        and target.get("type") == "iframe"
        and target.get("parentId") == parent_target_id
        and _url_key(str(target.get("url") or "")) == _url_key(route.url)
    ]
    if len(candidates) != 1 or not isinstance(candidates[0].get("targetId"), str):
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    session = await _attach(client, str(candidates[0]["targetId"]))
    if _url_key(session.url) != _url_key(route.url):
        await session.close()
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    return session


async def attach_oopif_expected(client: Any, expected: str) -> OopifSession:
    if not expected.startswith("oopif:"):
        raise BrowserAgentError("PAGE_ACTION_STALE_REF", "The OOPIF reference is invalid")
    target_id, separator, loader_id = expected.removeprefix("oopif:").partition(":")
    if not separator or not target_id or not loader_id:
        raise BrowserAgentError("PAGE_ACTION_STALE_REF", "The OOPIF reference is invalid")
    try:
        session = await _attach(client, target_id)
    except (BrowserAgentError, CDPSessionError) as error:
        raise BrowserAgentError(
            "PAGE_ACTION_STALE_REF", "The OOPIF target is unavailable"
        ) from error
    if session.frame_key != expected:
        await session.close()
        raise BrowserAgentError(
            "PAGE_ACTION_STALE_REF", "The OOPIF document navigated or was replaced"
        )
    return session
