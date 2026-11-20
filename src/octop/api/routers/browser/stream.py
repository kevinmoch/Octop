"""WebSocket browser screencast — attaches to octop-browser sessions.

Wire protocol matches the dashboard ``useBrowserStream`` hook (screencast)
and ``useBrowserSessionState`` (listen-only status):

Client → Server::

  {"type": "start", "url": "", "width": 1280, "height": 800,
   "reuse_session": true, "session_id": "<profile>"}
  {"type": "navigate", "url": "https://..."}
  {"type": "click", "x": 100, "y": 200}
  {"type": "mousedown", "x": 100, "y": 200, "button": "left", "buttons": 1}
  {"type": "mousemove", "x": 150, "y": 250, "button": "left", "buttons": 1}
  {"type": "mousemove", "x": 160, "y": 260, "button": "none", "buttons": 0}
  {"type": "mouseup", "x": 150, "y": 250, "button": "left", "buttons": 0}
  {"type": "stop"}

Server → Client::

  {"type": "status", "status": "streaming"}
  {"type": "frame", "data": "<base64 jpeg>"}
  {"type": "tabs", "tabs": [{id, url, title, active}]}
  {"type": "session_update", "session_id": "...", "current_url": "...", ...}
  {"type": "error", "message": "..."}

Auth: enhanced sessions use a one-shot ``?ticket=...``. The legacy workbench
may use ``?token=<JWT>`` until the versioned removal gate below.
Listen-only (``?listen_only=1``): still requires ``start``, but never launches
Chrome — attaches to an existing harness session or pushes idle updates.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from packaging.version import Version
from starlette.websockets import WebSocketState

from octop import __version__
from octop.api.deps import resolve_user_from_token
from octop.api.routers.browser.harness import (
    control_owner_for,
    harness_list_tabs,
    harness_page_url,
    resolve_harness_session,
)
from octop.api.routers.browser.webskill import check_websocket_origin
from octop.infra.control.browser.frame_hub import capture_subscription
from octop.infra.control.browser.webskill.lease import profile_lease
from octop.infra.control.browser.webskill.ownership import set_control_owner
from octop.infra.control.browser.webskill.sessions import browser_agent_registry
from octop.infra.control.browser.webskill.targets import wait_for_new_page_target
from octop.infra.users.permissions import user_has_permission
from octop.infra.utils.browser_media import user_browser_profile

logger = logging.getLogger(__name__)

router = APIRouter()
LEGACY_STREAM_JWT_REMOVAL = "Octop 1.1.0"
if Version(__version__) >= Version("1.1.0"):
    raise RuntimeError(
        "Legacy browser-stream JWT authentication must be removed before Octop 1.1.0"
    )

_FRAME_INTERVAL_S = 1 / 12
_SESSION_INTERVAL_S = 1.0
_MOUSE_TARGETS: dict[int, set[str]] = {}


def _normalize_nav_url(raw: str) -> str:
    """Prefix a host with ``https://`` unless it already has an http(s) scheme.

    Matches :func:`octop.infra.utils.url.normalize_nav_url`, including the
    case-insensitive scheme check: ``HTTPS://host`` must not become
    ``https://HTTPS://host``.
    """
    t = raw.strip()
    if not t:
        return ""
    if t.lower().startswith(("http://", "https://")):
        return t
    return f"https://{t}"


async def _capture_jpeg(sess: Any) -> str | None:
    try:
        # Check if internal session is fully connected to prevent race conditions during tab switching
        if not getattr(sess._internal, "_connected", False):  # noqa: SLF001
            return None

        # Wrap in a timeout to prevent hanging if the CDP client gets into an inconsistent state during reconnect
        result = await asyncio.wait_for(
            sess._internal.client.send(  # noqa: SLF001
                "Page.captureScreenshot",
                {"format": "jpeg", "quality": 80},
            ),
            timeout=1.5,
        )
        data = result.get("data")
        return str(data) if data else None
    except Exception as exc:
        logger.debug("capture jpeg failed: %s", exc)
        return None


async def _capture_profile_jpeg(profile: str) -> str | None:
    sess = await resolve_harness_session(profile, create=False)
    return await _capture_jpeg(sess) if sess is not None else None


async def _send_json(ws: WebSocket, payload: dict[str, Any]) -> None:
    if ws.application_state == WebSocketState.CONNECTED:
        await ws.send_text(json.dumps(payload))


async def _send_session_snapshot(
    send_json: Any,
    profile: str,
    *,
    sess: Any | None,
) -> None:
    """Push one session_update (+ tabs) frame for *profile*."""
    if sess is None:
        await send_json(
            {
                "type": "session_update",
                "session_id": profile,
                "conversation_id": profile,
                "channel_source": "dashboard",
                "state": "idle",
                "control_owner": control_owner_for(profile),
                "current_url": "",
            },
        )
        await send_json({"type": "tabs", "tabs": []})
        return

    url = await harness_page_url(sess)
    await send_json(
        {
            "type": "session_update",
            "session_id": profile,
            "conversation_id": profile,
            "channel_source": "dashboard",
            "state": "streaming" if url else "idle",
            "control_owner": control_owner_for(profile),
            "current_url": url,
        },
    )
    tabs = await harness_list_tabs(sess)
    await send_json({"type": "tabs", "tabs": tabs})


async def _stream_loop(
    send_json: Any,
    is_connected: Any,
    sess: Any,
    profile: str,
    *,
    listen_only: bool,
) -> None:
    await send_json({"type": "status", "status": "browser_started"})
    await send_json({"type": "status", "status": "streaming"})

    next_snapshot = 0.0
    capture_context = (
        contextlib.nullcontext(None)
        if listen_only
        else capture_subscription(
            f"{profile}:{id(sess)}",
            lambda: _capture_jpeg(sess),
        )
    )
    async with capture_context as subscription:
        while is_connected():
            started = time.monotonic()
            try:
                if started >= next_snapshot:
                    await _send_session_snapshot(send_json, profile, sess=sess)
                    next_snapshot = started + _SESSION_INTERVAL_S

                if subscription is not None:
                    frame = await subscription.next_frame()
                    if frame:
                        await send_json({"type": "frame", "data": frame})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # transient CDP/target switch races
                logger.warning("browser stream tick failed: %s", exc)
                # Keep the WebSocket and tab state alive; the next tick uses the
                # reconnected CDP client after a tab switch.
                await asyncio.sleep(0.1)

            await asyncio.sleep(max(0, _FRAME_INTERVAL_S - (time.monotonic() - started)))


async def _listen_state_loop(
    send_json: Any,
    is_connected: Any,
    profile: str,
) -> None:
    """Push session_update events without launching Chrome or capturing frames.

    Re-resolves the harness registry each tick so a browser started later by
    the agent becomes visible without reconnecting. Idle (no session) ticks
    use a longer interval to avoid a request/render storm on the dashboard.
    """
    await send_json({"type": "status", "status": "streaming"})

    while is_connected():
        sess = await resolve_harness_session(profile, create=False)
        await _send_session_snapshot(send_json, profile, sess=sess)
        await asyncio.sleep(_SESSION_INTERVAL_S if sess is not None else 2.0)


_CDP_BUTTON_MASK = {"left": 1, "right": 2, "middle": 4, "none": 0}


def _cdp_button(raw: object) -> str:
    name = str(raw or "left").lower()
    return name if name in _CDP_BUTTON_MASK else "left"


def _cdp_buttons(msg: dict[str, Any], *, button: str, event: str) -> int:
    if "buttons" in msg and msg["buttons"] is not None:
        try:
            return max(0, int(msg["buttons"]))
        except (TypeError, ValueError):
            pass
    if event == "mouseup" or button == "none":
        return 0
    return _CDP_BUTTON_MASK.get(button, 0)


async def _dispatch_mouse(
    sess: Any,
    *,
    event_type: str,
    x: int,
    y: int,
    button: str = "none",
    buttons: int = 0,
    click_count: int = 0,
) -> None:
    params: dict[str, Any] = {
        "type": event_type,
        "x": x,
        "y": y,
        "button": button,
        "buttons": buttons,
    }
    if click_count:
        params["clickCount"] = click_count
    await sess._internal.client.send("Input.dispatchMouseEvent", params)  # noqa: SLF001


def _cdp_click_count(msg: dict[str, Any]) -> int:
    raw = msg.get("clickCount", msg.get("click_count", 1))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


_KEY_CODES = {
    "Backspace": 8,
    "Tab": 9,
    "Enter": 13,
    "Escape": 27,
    "PageUp": 33,
    "PageDown": 34,
    "End": 35,
    "Home": 36,
    "ArrowLeft": 37,
    "ArrowUp": 38,
    "ArrowRight": 39,
    "ArrowDown": 40,
    "Delete": 46,
}


async def _dispatch_key(sess: Any, msg: dict[str, Any]) -> None:
    key = str(msg.get("key") or "")
    if not key or key in {"Process", "Dead", "Unidentified"}:
        return
    modifiers = sum(
        bit
        for field, bit in (("altKey", 1), ("ctrlKey", 2), ("metaKey", 4), ("shiftKey", 8))
        if msg.get(field)
    )
    key_code = _KEY_CODES.get(key, ord(key.upper()) if len(key) == 1 and key.isascii() else 0)
    params: dict[str, Any] = {
        "key": key,
        "code": str(msg.get("code") or key),
        "windowsVirtualKeyCode": int(msg.get("keyCode") or key_code),
        "modifiers": modifiers,
    }
    down = {**params, "type": "rawKeyDown", "autoRepeat": bool(msg.get("repeat"))}
    if key == "Enter":
        down.update(type="keyDown", text="\r", unmodifiedText="\r")
    if key.lower() == "a" and modifiers & 6 and not modifiers & 1:
        # Chromium's native editing shortcuts depend on the server OS. Keep
        # Ctrl+A / Cmd+A working when the dashboard and Chrome run on different OSes.
        down["commands"] = ["selectAll"]
    await sess._internal.client.send("Input.dispatchKeyEvent", down)  # noqa: SLF001
    await sess._internal.client.send(  # noqa: SLF001
        "Input.dispatchKeyEvent", {**params, "type": "keyUp"}
    )


async def _snapshot_mouse_targets(sess: Any) -> None:
    try:
        targets = await asyncio.wait_for(
            sess._internal.client.send("Target.getTargets"),
            timeout=0.5,  # noqa: SLF001
        )
    except Exception:  # noqa: BLE001 - a click can still be forwarded
        _MOUSE_TARGETS.pop(id(sess), None)
        return
    _MOUSE_TARGETS[id(sess)] = {
        str(info.get("targetId"))
        for info in targets.get("targetInfos", [])
        if isinstance(info, dict) and info.get("type") == "page" and info.get("targetId")
    }


async def _activate_new_mouse_target(sess: Any) -> None:
    previous = _MOUSE_TARGETS.pop(id(sess), None)
    if not previous:
        return
    try:
        target_id = await wait_for_new_page_target(
            sess._internal.client,
            previous,
            timeout=1.0,  # noqa: SLF001
        )
    except Exception:  # noqa: BLE001 - target enumeration is best effort
        return
    if not target_id:
        return
    try:
        await asyncio.wait_for(sess.switch_tab(target_id), timeout=2.0)
    except Exception:  # noqa: BLE001 - normal click remains usable
        logger.debug("new target activation failed", exc_info=True)


async def _handle_client_event(sess: Any, msg: dict[str, Any]) -> None:
    t = msg.get("type")
    if t == "navigate":
        url = _normalize_nav_url(str(msg.get("url") or ""))
        if url:
            await sess.navigate(url)
    elif t == "goback":
        await sess.go_back()
    elif t == "goforward":
        await sess.go_forward()
    elif t == "reload":
        await sess.reload()
    elif t == "click":
        await sess.click(x=int(msg.get("x") or 0), y=int(msg.get("y") or 0))
    elif t == "dblclick":
        x, y = int(msg.get("x") or 0), int(msg.get("y") or 0)
        await sess.click(x=x, y=y)
        await sess.click(x=x, y=y)
    elif t in ("mousedown", "mouseup", "mousemove"):
        x, y = int(msg.get("x") or 0), int(msg.get("y") or 0)
        button = _cdp_button(msg.get("button"))
        buttons = _cdp_buttons(msg, button=button, event=str(t))
        click_count = _cdp_click_count(msg)
        if t == "mousedown":
            # Remember the target set before the press so a link opening a new
            # window can be detected after the matching release.
            await _snapshot_mouse_targets(sess)
            await _dispatch_mouse(
                sess,
                event_type="mousePressed",
                x=x,
                y=y,
                button=button if button != "none" else "left",
                buttons=buttons or _CDP_BUTTON_MASK.get(button, 1),
                click_count=click_count,
            )
        elif t == "mouseup":
            await _dispatch_mouse(
                sess,
                event_type="mouseReleased",
                x=x,
                y=y,
                button=button if button != "none" else "left",
                buttons=0,
                click_count=click_count,
            )
            # Opening a link in a new window must also move the viewer to that
            # target, as Chrome does for a user click. Otherwise the new tab is
            # visible while the stream client remains attached to the old page.
            await _activate_new_mouse_target(sess)
        else:
            await _dispatch_mouse(
                sess,
                event_type="mouseMoved",
                x=x,
                y=y,
                button=button,
                buttons=buttons,
            )
    elif t == "scroll":
        delta_x = float(msg.get("deltaX") or msg.get("delta_x") or 0)
        delta_y = float(msg.get("deltaY") or msg.get("delta_y") or 0)
        x = int(msg.get("x") or 0)
        y = int(msg.get("y") or 0)
        if abs(delta_x) > 0.5 or abs(delta_y) > 0.5:
            with contextlib.suppress(Exception):
                await _dispatch_mouse(sess, event_type="mouseMoved", x=x, y=y)
                await sess._internal.client.send(  # noqa: SLF001
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseWheel",
                        "x": x,
                        "y": y,
                        "deltaX": delta_x,
                        "deltaY": delta_y,
                    },
                )
        else:
            direction = "down" if delta_y > 0 else "up"
            amount = max(int(abs(delta_y)), 50)
            await sess.scroll(direction=direction, amount=amount)
    elif t == "type":
        text = str(msg.get("text") or "")
        if text:
            # Committed IME / pasted text is one insertion, not one CDP round
            # trip per character. The page's input event still reaches the recorder.
            await sess._internal.client.send("Input.insertText", {"text": text})  # noqa: SLF001
    elif t == "keydown":
        await _dispatch_key(sess, msg)
    elif t == "tab_switch":
        tab_id = msg.get("tab_id")
        if tab_id is not None:
            try:
                result = await asyncio.wait_for(sess.switch_tab(str(tab_id)), timeout=8.0)
            except TimeoutError:
                logger.warning("tab switch timed out; keeping stream open")
                return
            if not getattr(result, "success", False):
                logger.warning(
                    "tab switch failed for %s: %s",
                    tab_id,
                    getattr(result, "error", "unknown error"),
                )
                # The client may still hold a target id from before a browser
                # restart. Resolve the current tab by its visible identity
                # instead of leaving the user stuck on the old frame.
                tabs = await sess.list_tabs()
                metadata = (getattr(tabs, "metadata", None) or {}).get("tabs", [])
                wanted_url = str(msg.get("tab_url") or "")
                wanted_title = str(msg.get("tab_title") or "")
                fallback = next(
                    (
                        tab
                        for tab in metadata
                        if isinstance(tab, dict)
                        and (
                            (wanted_url and tab.get("url") == wanted_url)
                            or (wanted_title and tab.get("title") == wanted_title)
                        )
                    ),
                    None,
                )
                if fallback is not None and fallback.get("tab_id") != tab_id:
                    await sess.switch_tab(str(fallback["tab_id"]))
    elif t == "tab_close":
        tab_id = msg.get("tab_id")
        if tab_id is not None:
            await sess.close_tab(str(tab_id))
    elif t == "tab_new":
        await sess.new_tab()
    elif t == "resize":
        w = int(msg.get("width") or 0)
        h = int(msg.get("height") or 0)
        if w > 0 and h > 0:
            with contextlib.suppress(Exception):
                await sess._internal.client.send(  # noqa: SLF001
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": w,
                        "height": h,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )


@router.websocket("/browser-stream/ws")
async def browser_stream_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    ticket: str | None = Query(default=None),
    listen_only: int = Query(default=0),
    width: int = Query(default=1280),
    height: int = Query(default=800),
) -> None:
    server = websocket.app.state.octop_server
    stream_session_id: str | None = None
    stream_connection_id: str | None = None
    if token is not None and ticket is None:
        logger.warning(
            "Legacy browser-stream JWT auth is deprecated; removal target: %s",
            LEGACY_STREAM_JWT_REMOVAL,
        )
    try:
        if ticket is not None or websocket.headers.get("origin") is not None:
            check_websocket_origin(websocket)
        if ticket is not None:
            session = browser_agent_registry.consume_ticket(ticket)
            user_id = session.user_id
        elif token:
            user_id = resolve_user_from_token(server, token).id
        else:
            raise ValueError("missing credentials")
        user = server.user_manager.get_by_id(user_id) if server.user_manager is not None else None
        if user is None or not user_has_permission(user, "browser"):
            raise ValueError("browser permission required")
        if ticket is not None:
            stream_connection_id = browser_agent_registry.attach_stream(session)
            stream_session_id = session.id
    except Exception as error:
        logger.info("Browser-stream authentication failed: %s", type(error).__name__)
        await websocket.close(code=4001, reason="Browser-stream authentication failed")
        return

    async def send_json(payload: dict[str, Any]) -> None:
        await _send_json(websocket, payload)

    def is_connected() -> bool:
        return websocket.application_state == WebSocketState.CONNECTED

    async def receive_text() -> str:
        return await websocket.receive_text()

    try:
        await websocket.accept()
        await run_browser_stream_session(
            send_json=send_json,
            is_connected=is_connected,
            receive_text=receive_text,
            user_id=int(user.id),
            listen_only=bool(listen_only),
            default_width=width,
            default_height=height,
        )
    except WebSocketDisconnect:
        pass
    finally:
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close()
        if stream_session_id is not None and stream_connection_id is not None:
            browser_agent_registry.detach_stream(stream_session_id, stream_connection_id)


async def run_browser_stream_session(
    *,
    send_json: Any,
    is_connected: Any,
    receive_text: Any,
    user_id: int,
    listen_only: bool = False,
    default_width: int = 1280,
    default_height: int = 800,
    start_msg: dict[str, Any] | None = None,
) -> None:
    """Run one screencast / listen-only session over arbitrary send/receive hooks.

    Used by the local dashboard WebSocket and by Bridge peer relay so both share
    the same Chrome / harness lifecycle.
    """
    sess: Any | None = None
    profile = user_browser_profile(user_id)
    stream_task: asyncio.Task[None] | None = None

    try:
        if start_msg is None:
            raw = await asyncio.wait_for(receive_text(), timeout=15.0)
            start_msg = json.loads(raw)
        if not isinstance(start_msg, dict) or start_msg.get("type") != "start":
            await send_json({"type": "error", "message": "expected start message"})
            return

        if listen_only:
            stream_task = asyncio.create_task(_listen_state_loop(send_json, is_connected, profile))
        else:
            sess = await resolve_harness_session(profile)
            assert sess is not None  # create=True always returns or raises

            start_url = _normalize_nav_url(str(start_msg.get("url") or ""))
            if start_url and start_url not in {"about:blank"}:
                try:
                    await sess.navigate(start_url)
                except Exception as exc:
                    logger.warning("initial navigate to %s failed: %s", start_url, exc)
                    await send_json(
                        {
                            "type": "error",
                            "message": f"导航到 {start_url} 失败：{exc}",
                        },
                    )

            vw = int(start_msg.get("width") or default_width)
            vh = int(start_msg.get("height") or default_height)
            if vw > 0 and vh > 0:
                with contextlib.suppress(Exception):
                    await sess._internal.client.send(  # noqa: SLF001
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": vw,
                            "height": vh,
                            "deviceScaleFactor": 1,
                            "mobile": False,
                        },
                    )

            stream_task = asyncio.create_task(
                _stream_loop(
                    send_json,
                    is_connected,
                    sess,
                    profile,
                    listen_only=False,
                )
            )

        while is_connected():
            try:
                raw = await receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "stop":
                break
            if sess is not None:
                # Human input wins before it waits for an in-progress model write.
                if control_owner_for(profile) != "user":
                    await asyncio.to_thread(set_control_owner, profile, "user")
                browser_agent_registry.revoke_grants(profile=profile)
                if msg.get("type") == "tab_switch":
                    # Tab switching is a viewer command. Do not queue it behind
                    # a model write lock or an in-flight page action: otherwise a
                    # slow agent action makes every tab click look broken.
                    try:
                        await asyncio.wait_for(_handle_client_event(sess, msg), timeout=8.0)
                    except TimeoutError:
                        logger.warning("tab switch event timed out; stream kept open")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("tab switch event failed: %s", exc)
                    continue
                if msg.get("type") == "mousemove":
                    # Hover is best-effort. It must never hold the write lock or
                    # sit in front of a tab click when CDP is slow.
                    try:
                        await asyncio.wait_for(_handle_client_event(sess, msg), timeout=0.2)
                    except TimeoutError:
                        logger.debug("browser mousemove timed out; dropped")
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("browser mousemove failed: %s", exc)
                    continue
                async with (
                    browser_agent_registry.write_lock(profile),
                    profile_lease(profile),
                ):
                    try:
                        await asyncio.wait_for(_handle_client_event(sess, msg), timeout=2.0)
                    except TimeoutError:
                        logger.warning(
                            "browser input event %s timed out; stream kept open",
                            msg.get("type"),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("browser input event failed: %s", exc)
    except TimeoutError:
        await send_json({"type": "error", "message": "timed out waiting for start"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("browser stream failed")
        if is_connected():
            await send_json({"type": "error", "message": str(exc)})
            await send_json({"type": "status", "status": "error"})
    finally:
        if stream_task is not None:
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
