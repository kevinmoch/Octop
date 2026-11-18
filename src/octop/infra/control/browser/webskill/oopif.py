"""Attach restricted OOPIF operations to their actual CDP target."""

from __future__ import annotations

import asyncio
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
        with contextlib.suppress(Exception):
            self.client.off("Runtime.executionContextCreated", self.context_listener)
        with contextlib.suppress(Exception):
            await self.client.close()


@dataclass
class FrameAttachment:
    sessions: list[OopifSession]
    frame_key: str

    @property
    def client(self) -> Any:
        return self.sessions[-1].client

    @property
    def target_id(self) -> str:
        return self.sessions[-1].target_id

    @property
    def execution_context_id(self) -> int:
        return self.sessions[-1].execution_context_id

    @property
    def url(self) -> str:
        return self.sessions[-1].url

    @property
    def title(self) -> str:
        return self.sessions[-1].title

    async def close(self) -> None:
        for session in reversed(self.sessions):
            await session.close()

    async def assert_current(self) -> None:
        client = self.sessions[0].client
        session_index = 0
        for segment in self.frame_key.split(">"):
            if segment.startswith("oopif:"):
                session = self.sessions[session_index]
                session_index += 1
                client = session.client
                tree = await client.send("Page.getFrameTree")
                loader = str(tree.get("frameTree", {}).get("frame", {}).get("loaderId") or "")
                if f"oopif:{session.target_id}:{loader}" != segment:
                    raise BrowserAgentError(
                        "PAGE_ACTION_STALE_REF", "The OOPIF document navigated or was replaced"
                    )
            else:
                from .frames import assert_frame_identity_current

                await assert_frame_identity_current(client, segment)


async def _wait_for_document(scoped: Any, context_id: int) -> None:
    for _ in range(20):
        result = await scoped.send(
            "Runtime.evaluate",
            {
                "expression": "document.readyState",
                "contextId": context_id,
                "returnByValue": True,
            },
        )
        if result.get("result", {}).get("value") in {"interactive", "complete"}:
            return
        await asyncio.sleep(0.05)
    raise BrowserAgentError("PERCEPTION_FAILED", "The target document did not become ready in time")


async def _attach(client: Any, target_id: str, kind: str = "oopif") -> OopifSession:
    attached = await client.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    session_id = str(attached.get("sessionId") or "")
    if not session_id:
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    scope_owner = getattr(client, "_parent", client)
    scoped = scope_owner.with_session(session_id)
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
        tree = await scoped.send("Page.getFrameTree")
        frame = tree.get("frameTree", {}).get("frame", {})
        root_frame_id = frame.get("id")
        default_contexts = [
            candidate
            for candidate in contexts
            if candidate.get("auxData", {}).get("isDefault") is True
        ]
        context = next(
            (
                candidate
                for candidate in default_contexts
                if candidate.get("auxData", {}).get("frameId") == root_frame_id
            ),
            default_contexts[0] if len(default_contexts) == 1 else None,
        )
        if context is None or not isinstance(context.get("id"), int):
            raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
        context_id = int(context["id"])
        if kind == "target":
            await _wait_for_document(scoped, context_id)
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
            frame_key=f"{kind}:{target_id}:{loader_id}",
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
    described = await client.send(
        "DOM.describeNode", {"backendNodeId": route.owner_backend_node_id}
    )
    frame_id = described.get("node", {}).get("frameId")
    if not isinstance(frame_id, str) or not frame_id:
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    targets = await client.send("Target.getTargets")
    for target in targets.get("targetInfos", []):
        if (
            not isinstance(target, dict)
            or target.get("type") != "iframe"
            or target.get("parentId") != parent_target_id
            or not isinstance(target.get("targetId"), str)
        ):
            continue
        session = await _attach(client, str(target["targetId"]))
        tree = await session.client.send("Page.getFrameTree")
        frame = tree.get("frameTree", {}).get("frame", {})
        current_url = str(frame.get("url") or session.url)
        if (
            frame.get("id") == frame_id
            and _url_key(current_url)
            and _url_key(session.url) == _url_key(current_url)
            and _url_key(str(target.get("url") or "")) == _url_key(current_url)
        ):
            return session
        await session.close()
    raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)


async def attach_page_target(client: Any, target_id: str) -> OopifSession:
    return await _attach(client, target_id, kind="target")


async def attach_target_expected(client: Any, expected: str) -> OopifSession:
    if not expected.startswith("target:"):
        raise BrowserAgentError("PAGE_TAB_EXPIRED", "The model target is invalid")
    target_id, separator, loader_id = expected.removeprefix("target:").partition(":")
    if not separator or not target_id or not loader_id:
        raise BrowserAgentError("PAGE_TAB_EXPIRED", "The model target is invalid")
    try:
        session = await _attach(client, target_id, kind="target")
    except (BrowserAgentError, CDPSessionError) as error:
        raise BrowserAgentError(
            "PAGE_TAB_EXPIRED", "The model target is no longer available"
        ) from error
    if session.frame_key != expected:
        await session.close()
        raise BrowserAgentError("PAGE_TAB_EXPIRED", "The model target navigated or was replaced")
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


async def attach_frame_expected(client: Any, expected: str) -> FrameAttachment:
    sessions: list[OopifSession] = []
    current_client = client
    try:
        for segment in expected.split(">"):
            if segment.startswith("oopif:"):
                session = await attach_oopif_expected(current_client, segment)
                sessions.append(session)
                current_client = session.client
            else:
                if not sessions:
                    raise BrowserAgentError(
                        "PAGE_ACTION_STALE_REF", "The frame reference is invalid"
                    )
                from .frames import assert_frame_identity_current

                await assert_frame_identity_current(current_client, segment)
        return FrameAttachment(sessions=sessions, frame_key=expected)
    except Exception:
        for session in reversed(sessions):
            await session.close()
        raise
