"""Raw CDP OOPIF probe; this does not claim BrowserSession already supports it."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets


async def attach_read_click(page_ws_url: str, target_id: str) -> dict[str, Any]:
    contexts: list[dict[str, Any]] = []
    next_id = 0
    async with websockets.connect(page_ws_url) as socket:

        async def call(
            method: str,
            params: dict[str, Any] | None = None,
            session_id: str | None = None,
        ) -> dict[str, Any]:
            nonlocal next_id
            next_id += 1
            request_id = next_id
            message: dict[str, Any] = {
                "id": request_id,
                "method": method,
                "params": params or {},
            }
            if session_id is not None:
                message["sessionId"] = session_id
            await socket.send(json.dumps(message))
            while True:
                response = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                if (
                    response.get("method") == "Runtime.executionContextCreated"
                    and response.get("sessionId") == session_id
                ):
                    context = response.get("params", {}).get("context", {})
                    if isinstance(context, dict):
                        contexts.append(context)
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise RuntimeError(f"CDP {method} failed: {response['error']}")
                result = response.get("result", {})
                if not isinstance(result, dict):
                    raise RuntimeError(f"CDP {method} returned no result object")
                return result

        attached = await call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = str(attached.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("OOPIF attach returned no sessionId")
        await call("Runtime.enable", session_id=session_id)
        default_context = next(
            (
                context
                for context in contexts
                if context.get("auxData", {}).get("isDefault") is True
            ),
            None,
        )
        if default_context is None:
            raise RuntimeError("OOPIF session exposed no default execution context")
        context_id = int(default_context["id"])
        observed = await call(
            "Runtime.evaluate",
            {
                "expression": "({origin: location.origin, marker: document.getElementById('frame-marker')?.textContent ?? ''})",
                "contextId": context_id,
                "returnByValue": True,
            },
            session_id=session_id,
        )
        document = await call("DOM.getDocument", {"depth": -1}, session_id)
        node = await call(
            "DOM.querySelector",
            {"nodeId": document["root"]["nodeId"], "selector": "#frame-action"},
            session_id,
        )
        quads = await call("DOM.getContentQuads", {"nodeId": node["nodeId"]}, session_id)
        quad = quads["quads"][0]
        x = (quad[0] + quad[4]) / 2
        y = (quad[1] + quad[5]) / 2
        for event_type in ("mousePressed", "mouseReleased"):
            await call(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
                session_id=session_id,
            )
        count = await call(
            "Runtime.evaluate",
            {
                "expression": "({clicks: window.frameClicks || 0, pointerDowns: window.pointerDowns || 0})",
                "contextId": context_id,
                "returnByValue": True,
            },
            session_id=session_id,
        )
        await call("Target.detachFromTarget", {"sessionId": session_id})
        observed_count = count.get("result", {}).get("value") or {}
        return {
            "attached": True,
            "sessionScoped": True,
            "executionContextId": context_id,
            "observed": observed.get("result", {}).get("value"),
            "actionCompleted": True,
            "clickCount": observed_count.get("clicks"),
            "pointerDownCount": observed_count.get("pointerDowns"),
            "actionKind": "CDP Input.dispatchMouseEvent mousePressed/mouseReleased in the attached OOPIF context; not BrowserSession",
        }
