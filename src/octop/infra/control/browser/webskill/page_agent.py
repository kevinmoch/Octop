"""Translate the existing octop-browser session into the public PageAgent contract."""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, cast
from urllib.parse import urlsplit

from octop_browser.actions import interact, navigate
from octop_browser.dom.builder import DOMBuilder
from octop_browser.dom.refs import RefCache
from octop_browser.session import BrowserSession

from .frames import (
    assert_frame_identity_current,
    discover_frame_routes,
    frame_identity,
    frame_scope_path,
    nested_frame_refs,
    projection_document,
    select_frame_route_step,
)
from .lease import profile_lease
from .oopif import (
    FrameAttachment,
    OopifSession,
    attach_frame_expected,
    attach_oopif_route,
)
from .sessions import (
    DOCUMENT_REF,
    BrowserAgentError,
    BrowserAgentRegistry,
    BrowserAgentSession,
    DocumentIdentity,
    PageRef,
)
from .targets import (
    admit_model_target,
    attach_model_target,
    page_target_ids,
    wait_for_new_page_target,
)

SessionResolver = Callable[..., Awaitable[Any]]
WriteGate = Callable[[], bool]
MAX_P1_NODES = 1500


def public_error_code(code: str) -> str:
    return {
        "PERCEPTION_SUBTREE_UNSUPPORTED": "PERCEPTION_SUBTREE_UNSUPPORTED",
        "PERCEPTION_SUBTREE_OUT_OF_SCOPE": "PERCEPTION_SUBTREE_OUT_OF_SCOPE",
        "PERCEPTION_SUBTREE_UNKNOWN_REF": "PERCEPTION_SUBTREE_UNKNOWN_REF",
        "PERCEPTION_FAILED": "PERCEPTION_FAILED",
        "PAGE_ACTION_STALE_REF": "PAGE_ACTION_STALE_REF",
        "GRANT_INVALID": "PAGE_ACTION_STALE_REF",
        "GRANT_MISMATCH": "PAGE_ACTION_STALE_REF",
        "GRANT_REQUIRED": "PAGE_ACTION_OUT_OF_SCOPE",
        "DOCUMENT_CHANGED": "PAGE_ACTION_STALE_REF",
        "DOCUMENT_REQUIRED": "PAGE_ACTION_STALE_REF",
        "PAGE_TAB_EXPIRED": "PAGE_TAB_EXPIRED",
        "PAGE_TAB_UNKNOWN": "PAGE_TAB_UNKNOWN",
        "WRITE_LOCKED": "WRITE_LOCKED",
        "WRITE_BLOCKED": "PAGE_ACTION_OUT_OF_SCOPE",
        "ACTION_UNSUPPORTED": "PAGE_ACTION_OUT_OF_SCOPE",
        "ACTION_INVALID": "PAGE_ACTION_OUT_OF_SCOPE",
    }.get(code, "TOOL_EXECUTION_FAILED")


async def resolve_browser(
    session: BrowserAgentSession,
    resolver: SessionResolver,
) -> BrowserSession:
    browser = cast(BrowserSession | None, await resolver(session.profile, create=True))
    if browser is None:
        raise BrowserAgentError("PAGE_TAB_UNKNOWN", "The user's browser session is not available")
    return browser


async def _current_target(browser: BrowserSession) -> str:
    result = await browser.list_tabs()
    tabs_value: object = (result.metadata or {}).get("tabs", [])
    if not isinstance(tabs_value, list):
        raise BrowserAgentError("PAGE_TAB_UNKNOWN", "The browser target list is unavailable")
    tabs: list[dict[str, Any]] = [tab for tab in tabs_value if isinstance(tab, dict)]
    active = next((tab for tab in tabs if tab.get("active") is True), None)
    if active is None or not isinstance(active.get("tab_id"), str):
        raise BrowserAgentError("PAGE_TAB_UNKNOWN", "No active page target is available")
    return str(active["tab_id"])


async def document_identity(
    session: BrowserAgentSession,
    browser: BrowserSession,
    cdp_client: Any = None,
) -> DocumentIdentity:
    if session.model_target is None:
        current = await _current_target(browser)
        session.model_target = current
        session.base_target_id = current
    elif session.base_target_id is None:
        session.base_target_id = session.model_target
    target = session.model_target
    session.model_targets.add(target)
    client = cdp_client or browser._internal.client  # noqa: SLF001
    targets = await client.send("Target.getTargets")
    if not any(
        isinstance(info, dict) and info.get("type") == "page" and info.get("targetId") == target
        for info in targets.get("targetInfos", [])
    ):
        raise BrowserAgentError(
            "PAGE_TAB_EXPIRED", "The model target was closed; select a target explicitly"
        )
    tree = await client.send("Page.getFrameTree")
    page = await client.send(
        "Runtime.evaluate",
        {
            "expression": "({url: location.href, title: document.title})",
            "returnByValue": True,
        },
    )
    frame = tree.get("frameTree", {}).get("frame", {})
    info = page.get("result", {}).get("value", {})
    url = str(info.get("url") or "")
    loader = str(frame.get("loaderId") or "")
    if not url or not loader:
        raise BrowserAgentError("PERCEPTION_FAILED", "The current document identity is unavailable")
    return DocumentIdentity(
        key=json.dumps(
            [session.user_id, session.profile, target, loader, url],
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        target_id=target,
        loader_id=loader,
        url=url,
        title=str(info.get("title") or ""),
    )


def _attributes(node: dict[str, Any]) -> dict[str, str]:
    raw = node.get("attributes", [])
    return dict(zip(raw[::2], raw[1::2], strict=False)) if isinstance(raw, list) else {}


def _node_index(root: dict[str, Any]) -> dict[int, int]:
    index: dict[int, int] = {}

    def visit(node: dict[str, Any]) -> None:
        node_id = node.get("nodeId")
        backend_node_id = node.get("backendNodeId")
        if isinstance(node_id, int) and isinstance(backend_node_id, int):
            index[node_id] = backend_node_id
        for key in ("children", "shadowRoots"):
            for child in node.get(key, []):
                if isinstance(child, dict):
                    visit(child)
        content = node.get("contentDocument")
        if isinstance(content, dict):
            visit(content)

    visit(root)
    return index


def _sensitive_subtrees(root: dict[str, Any]) -> set[int]:
    excluded: set[int] = set()

    def visit(node: dict[str, Any], excluded_parent: bool) -> None:
        attrs = _attributes(node)
        name = attrs.get("name", "").lower()
        autocomplete = attrs.get("autocomplete", "").lower()
        excluded_here = excluded_parent or (
            attrs.get("type", "").lower() == "password"
            or autocomplete == "one-time-code"
            or autocomplete.startswith("cc-")
            or name in {"cvc", "cvv", "cardnumber"}
            or "data-webskill-private" in attrs
        )
        node_id = node.get("nodeId")
        if excluded_here and isinstance(node_id, int):
            excluded.add(node_id)
        for key in ("children", "shadowRoots"):
            for child in node.get(key, []):
                if isinstance(child, dict):
                    visit(child, excluded_here)
        content = node.get("contentDocument")
        if isinstance(content, dict):
            visit(content, excluded_here)

    visit(root, False)
    return excluded


def _role(element: dict[str, Any]) -> str:
    declared = str(element.get("role") or "")
    tag = str(element.get("tag") or "")
    input_type = str(element.get("type") or "")
    if declared and declared != tag:
        return declared
    if tag == "a":
        return "link"
    if tag == "select":
        return "combobox"
    if tag == "input":
        if input_type in {"checkbox", "radio"}:
            return input_type
        return "textbox"
    return declared or tag


def _secret(element: dict[str, Any]) -> bool:
    return str(element.get("type") or "").lower() == "password"


def _node(element: dict[str, Any], ref: str) -> dict[str, Any]:
    role = _role(element)
    name = (
        ""
        if _secret(element)
        else str(
            element.get("accessibleName")
            or element.get("text")
            or element.get("label")
            or element.get("name")
            or ""
        )
    )
    node: dict[str, Any] = {"role": role, "ref": ref}
    if name:
        node["name"] = name
    href = element.get("href")
    if role == "link" and isinstance(href, str) and href:
        node["href"] = href
    return node


def _target(element: dict[str, Any], ref: str) -> dict[str, Any]:
    node = _node(element, ref)
    target = {"ref": ref, "role": node["role"]}
    if "name" in node:
        target["name"] = node["name"]
    if _secret(element):
        target["secret"] = True
    return target


def _promoted_elements(root: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose clickable-looking divs the accessibility tree omits.

    Enterprise UI kits often render menu cards as ``div[title]`` with a Vue
    click handler and no ``role``/``href``. The SDK accessibility builder keeps
    only native controls, so the model sees the page as textless while the
    extension can act on those cards. Promote only elements with an explicit
    title/button-like hint; this is a perception aid, not an authorization
    bypass (the same scope and sensitive exclusions still apply).
    """
    promoted: list[dict[str, Any]] = []
    sensitive_tags = {"script", "style", "template", "noscript"}

    def visit(node: dict[str, Any]) -> None:
        tag = str(node.get("nodeName") or "").lower()
        if tag in sensitive_tags:
            return
        attrs = {
            str(key): str(value)
            for key, value in zip(
                node.get("attributes", [])[::2], node.get("attributes", [])[1::2], strict=False
            )
        }
        role = attrs.get("role", "").lower()
        title = attrs.get("title", "").strip()
        aria = attrs.get("aria-label", "").strip()
        tabindex = attrs.get("tabindex", "")
        # The nodeValue is usually on #text children, not the element; use the
        # element's title/aria as the stable accessible name for these cards.
        name = title or aria
        if (
            name
            and tag not in {"html", "body", "iframe", "frame", "img", "input", "textarea", "select"}
            and role not in {"button", "link", "tab", "menuitem", "option", "checkbox", "radio"}
            and (title or aria or tabindex in {"0", "true"} or role in {"button", "link"})
        ):
            promoted.append(
                {
                    "node_id": node.get("nodeId"),
                    "role": "link" if title else "button",
                    "name": name[:300],
                }
            )
        for child in node.get("children", []):
            if isinstance(child, dict):
                visit(child)
        for shadow in node.get("shadowRoots", []):
            if isinstance(shadow, dict):
                visit(shadow)

    visit(root)
    return [node for node in promoted if isinstance(node.get("node_id"), int)]


async def read_page(
    session: BrowserAgentSession,
    request: dict[str, Any],
    *,
    resolver: SessionResolver,
    registry: BrowserAgentRegistry,
) -> dict[str, Any]:
    scope = request.get("scope")
    if "ref" in request:
        raise BrowserAgentError(
            "PERCEPTION_SUBTREE_UNSUPPORTED",
            "Subtree perception is not available in P1; no full-page fallback is performed",
        )
    # P1 perception is DOM-only. Models may still ask for page images (the
    # SDK enables that option independently of the host); failing the whole
    # perception makes the assistant look like it lost browser control after
    # a handoff. Ignore the image budget and return the readable DOM instead.
    browser = await resolve_browser(session, resolver)
    model_attachment = await attach_model_target(session, browser)
    attached: FrameAttachment | None = None
    try:
        before = await document_identity(
            session,
            browser,
            model_attachment.client if model_attachment is not None else None,
        )
        client = (
            model_attachment.client if model_attachment is not None else browser._internal.client
        )  # noqa: SLF001
        raw = await client.send("DOM.getDocument", {"depth": -1, "pierce": True})
        root = raw.get("root", {})
        if not isinstance(root, dict):
            raise BrowserAgentError("PERCEPTION_FAILED", "The browser document is unavailable")
        routes = discover_frame_routes(root, before.url)
        route = select_frame_route_step(routes, scope)
        selected_client = client
        remaining_path = frame_scope_path(scope)
        document_prefix: tuple[str, ...] = ()
        if route is not None and route.document is None:
            sessions: list[OopifSession] = []
            while route is not None and route.document is None:
                parent_target = before.target_id if not sessions else sessions[-1].target_id
                attached_session = await attach_oopif_route(selected_client, route, parent_target)
                sessions.append(attached_session)
                attached = FrameAttachment(
                    sessions=sessions,
                    frame_key=">".join(item.frame_key for item in sessions),
                )
                selected_client = attached.client
                remaining_path = remaining_path[len(route.path) :]
                document_prefix = (*document_prefix, *route.path)
                raw = await selected_client.send("DOM.getDocument", {"depth": -1, "pierce": True})
                root = raw.get("root", {})
                if not isinstance(root, dict):
                    raise BrowserAgentError(
                        "PERCEPTION_FAILED", "The OOPIF document is unavailable"
                    )
                routes = discover_frame_routes(root, attached.url)
                route = select_frame_route_step(
                    routes,
                    {"frames": [{"frame": list(remaining_path), "include": ["body"]}]},
                )
        local_frame = await frame_identity(selected_client, route) if route is not None else None
        if route is not None:
            refreshed = await selected_client.send("DOM.getDocument", {"depth": -1, "pierce": True})
            refreshed_root = refreshed.get("root", {})
            if not isinstance(refreshed_root, dict):
                raise BrowserAgentError("PERCEPTION_FAILED", "The frame snapshot is unavailable")
            refreshed_routes = discover_frame_routes(
                refreshed_root, attached.url if attached is not None else before.url
            )
            refreshed_route = select_frame_route_step(
                refreshed_routes,
                {"frames": [{"frame": list(remaining_path), "include": ["body"]}]},
            )
            if (
                refreshed_route is None
                or refreshed_route.path != route.path
                or refreshed_route.owner_backend_node_id != route.owner_backend_node_id
                or await frame_identity(selected_client, refreshed_route) != local_frame
            ):
                raise BrowserAgentError(
                    "PERCEPTION_FAILED", "The frame changed while its snapshot was being bound"
                )
            root = refreshed_root
            route = refreshed_route
            routes = refreshed_routes
        selected_frame = (
            attached.frame_key + (f">{local_frame}" if local_frame else "")
            if attached is not None
            else local_frame or "self"
        )
        projected_root = projection_document(root, route)
        backend_ids = _node_index(root)
        if attached is not None:
            parent_path = (*document_prefix, *(route.path if route is not None else ()))
            prefixed_routes = [
                replace(candidate, path=(*document_prefix, *candidate.path)) for candidate in routes
            ]
            nested = nested_frame_refs(prefixed_routes, parent_path)
            selected_url = route.url if route is not None and route.url else attached.url
            selected_title = attached.title if route is None else ""
        else:
            nested = nested_frame_refs(routes, route.path if route is not None else ())
            selected_url = route.url if route is not None and route.url else before.url
            selected_title = before.title if route is None else ""
        try:
            dom = DOMBuilder(
                dom=projected_root,
                url=selected_url,
                title=selected_title,
                ref_cache=RefCache(),
            ).build(level="structured")
            raw_elements = json.loads(dom).get("elements", [])
        except (json.JSONDecodeError, AttributeError) as error:
            raise BrowserAgentError(
                "PERCEPTION_FAILED", "The browser DOM response is invalid"
            ) from error
        if not isinstance(raw_elements, list):
            raise BrowserAgentError("PERCEPTION_FAILED", "The browser DOM response is invalid")
        known_nodes = {
            element.get("node_id") for element in raw_elements if isinstance(element, dict)
        }
        raw_elements = [
            *raw_elements,
            *[
                element
                for element in _promoted_elements(projected_root)
                if element.get("node_id") not in known_nodes
            ],
        ]
        excluded = _sensitive_subtrees(projected_root)
        if any(element.get("framePath") for element in raw_elements if isinstance(element, dict)):
            raise BrowserAgentError(
                "PERCEPTION_SUBTREE_UNSUPPORTED",
                "Frame projection exceeded its selected document",
            )
        elements = [
            element
            for element in raw_elements
            if isinstance(element, dict)
            and isinstance(element.get("node_id"), int)
            and element.get("node_id") not in excluded
        ]
        if len(elements) > MAX_P1_NODES:
            raise BrowserAgentError(
                "PERCEPTION_FAILED",
                f"The document has more than {MAX_P1_NODES} readable controls; it will not be silently truncated",
            )
        after = await document_identity(session, browser, client)
        if before.key != after.key:
            raise BrowserAgentError(
                "PERCEPTION_FAILED", "The document changed while it was being perceived"
            )
        if attached is not None:
            try:
                await attached.assert_current()
            except BrowserAgentError as error:
                raise BrowserAgentError(
                    "PERCEPTION_FAILED", "The frame path changed while it was being perceived"
                ) from error
        elif selected_frame != await frame_identity(client, route):
            raise BrowserAgentError(
                "PERCEPTION_FAILED", "The frame changed while it was being perceived"
            )
        if (
            not frame_scope_path(scope)
            or session.document is None
            or session.document.key != after.key
        ):
            session.refs.clear()
        else:
            for ref, entry in list(session.refs.items()):
                if entry.frame == selected_frame:
                    session.refs.pop(ref, None)
        session.document = after
        nodes: list[dict[str, Any]] = []
        targets: list[dict[str, Any]] = []
        for element in elements:
            backend_node_id = backend_ids.get(element["node_id"])
            if backend_node_id is None:
                raise BrowserAgentError(
                    "PERCEPTION_FAILED",
                    "Could not bind a visible control to its backend node identity",
                )
            ref = registry.issue_ref(session, backend_node_id, selected_frame)
            nodes.append(_node(element, ref))
            targets.append(_target(element, ref))
        return {
            "type": "perceive-result",
            "result": {
                "nodes": nodes,
                "page": {
                    "url": selected_url,
                    **({"title": selected_title} if selected_title else {}),
                },
                **({"nestedFrames": nested} if nested else {}),
            },
            "targets": targets,
            "document": after.public(),
        }
    finally:
        if attached is not None:
            await attached.close()
        if model_attachment is not None:
            await model_attachment.close()


async def capture_image(
    session: BrowserAgentSession,
    request: dict[str, Any],
    *,
    registry: BrowserAgentRegistry,
    resolver: SessionResolver,
) -> dict[str, Any]:
    """Capture one already-authorized page reference as a normalized image payload.

    The request carries only a ref from a fresh perception and a byte budget.
    Coordinates and target identity stay in the host; no selector, URL or path is
    accepted from the model.
    """
    ref = request.get("ref")
    max_bytes = request.get("max_bytes", request.get("maxBytes"))
    if not isinstance(ref, str) or not ref or len(ref) > 256:
        raise BrowserAgentError("ACTION_INVALID", "A page reference is required")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise BrowserAgentError("ACTION_INVALID", "A positive image byte budget is required")
    max_bytes = min(max_bytes, 10 * 1024 * 1024)

    browser = await resolve_browser(session, resolver)
    model_attachment = await attach_model_target(session, browser)
    attached: FrameAttachment | None = None
    try:
        client = (
            model_attachment.client if model_attachment is not None else browser._internal.client
        )  # noqa: SLF001
        document = await document_identity(session, browser, client)
        entry = registry.resolve_ref(session, ref, document)
        if entry.frame.startswith("oopif:"):
            attached = await attach_frame_expected(client, entry.frame)
            client = attached.client
        elif entry.frame != "self":
            await assert_frame_identity_current(client, entry.frame)
        await client.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": entry.backend_node_id})
        box = await client.send("DOM.getBoxModel", {"backendNodeId": entry.backend_node_id})
        quad = box.get("model", {}).get("border", [])
        if not isinstance(quad, list) or len(quad) != 8:
            return {
                "type": "capture-image-result",
                "result": {
                    "id": ref,
                    "reason": "The element has no visible box",
                    "triedLevels": ["cdp-screenshot"],
                },
            }
        xs = [float(quad[index]) for index in (0, 2, 4, 6)]
        ys = [float(quad[index]) for index in (1, 3, 5, 7)]
        x, y = min(xs), min(ys)
        width, height = max(xs) - x, max(ys) - y
        if width <= 0 or height <= 0:
            return {
                "type": "capture-image-result",
                "result": {
                    "id": ref,
                    "reason": "The element has an empty visible box",
                    "triedLevels": ["cdp-screenshot"],
                },
            }
        screenshot = await client.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "clip": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "scale": 1,
                },
            },
        )
        data = screenshot.get("data")
        if not isinstance(data, str) or not data:
            return {
                "type": "capture-image-result",
                "result": {
                    "id": ref,
                    "reason": "The browser returned no screenshot bytes",
                    "triedLevels": ["cdp-screenshot"],
                },
            }
        try:
            decoded = base64.b64decode(data, validate=True)
        except Exception as error:
            raise BrowserAgentError(
                "TOOL_EXECUTION_FAILED", "The screenshot bytes are invalid"
            ) from error
        if len(decoded) > max_bytes:
            return {
                "type": "capture-image-result",
                "result": {
                    "id": ref,
                    "reason": f"The captured image is {len(decoded)} bytes, over the {max_bytes}-byte budget",
                    "triedLevels": ["cdp-screenshot"],
                },
            }
        return {
            "type": "capture-image-result",
            "result": {
                "data": data,
                "mimeType": "image/png",
                "width": max(1, round(width)),
                "height": max(1, round(height)),
                "bytes": len(decoded),
            },
        }
    finally:
        if attached is not None:
            await attached.close()
        if model_attachment is not None:
            await model_attachment.close()


MAX_LINKED_DOCUMENT_BYTES = 20 * 1024 * 1024


def _same_origin(left: str, right: str) -> bool:
    def parts(value: str) -> tuple[str, str, int] | None:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            return None
        return (
            parsed.scheme.lower(),
            parsed.hostname.lower(),
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
        )

    a = parts(left)
    b = parts(right)
    return a is not None and a == b


async def fetch_linked_document(
    session: BrowserAgentSession,
    url: str,
    *,
    resolver: SessionResolver,
) -> dict[str, Any]:
    """Fetch a linked document in the page's own session, not the harness.

    The extension host performs this in the bound tab's content script. Octop's
    host lives in a different origin, so a plain fetch would lose the site's
    cookies and return a login page. Execute the fetch inside the controlled
    page instead, with the same-origin gate enforced host-side.
    """
    if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
        raise BrowserAgentError("ACTION_INVALID", "Only absolute http(s) documents can be read")
    if len(url) > 2048:
        raise BrowserAgentError("ACTION_INVALID", "The document URL is too long")
    browser = await resolve_browser(session, resolver)
    model_attachment = await attach_model_target(session, browser)
    try:
        client = (
            model_attachment.client if model_attachment is not None else browser._internal.client
        )  # noqa: SLF001
        document = await document_identity(session, browser, client)
        if not _same_origin(document.url, url):
            raise BrowserAgentError(
                "PAGE_ACTION_OUT_OF_SCOPE",
                "Linked documents must stay on the current page's origin",
            )
        result = await client.send(
            "Runtime.evaluate",
            {
                "expression": f"""async () => {{
                    const response = await fetch({json.dumps(url)}, {{ credentials: "include" }});
                    if (!response.ok) return {{ ok: false, status: response.status }};
                    const buffer = await response.arrayBuffer();
                    if (buffer.byteLength > {MAX_LINKED_DOCUMENT_BYTES}) {{
                        return {{ ok: false, reason: "document-too-large" }};
                    }}
                    let binary = "";
                    const bytes = new Uint8Array(buffer);
                    for (let i = 0; i < bytes.length; i += 0x8000) {{
                        binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
                    }}
                    return {{
                        ok: true,
                        contentType: response.headers.get("content-type") || "application/octet-stream",
                        data: btoa(binary),
                    }};
                }}""",
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        value = result.get("result", {}).get("value")
        if not isinstance(value, dict) or value.get("ok") is not True:
            reason = value.get("reason") if isinstance(value, dict) else None
            if reason == "document-too-large":
                raise BrowserAgentError("TOOL_EXECUTION_FAILED", "The linked document is too large")
            status = value.get("status") if isinstance(value, dict) else None
            raise BrowserAgentError(
                "TOOL_EXECUTION_FAILED",
                f"The linked document responded with HTTP {status}"
                if status
                else "The linked document could not be read",
            )
        content_type = value.get("contentType")
        data = value.get("data")
        if not isinstance(content_type, str) or not isinstance(data, str):
            raise BrowserAgentError(
                "TOOL_EXECUTION_FAILED", "The linked document response is invalid"
            )
        return {"contentType": content_type, "data": data}
    finally:
        if model_attachment is not None:
            await model_attachment.close()


async def issue_action_grant(
    session: BrowserAgentSession,
    request: dict[str, Any],
    *,
    registry: BrowserAgentRegistry,
    resolver: SessionResolver,
    write_gate: WriteGate,
) -> str:
    if not write_gate():
        raise BrowserAgentError(
            "WRITE_BLOCKED", "User control is active; no new model write is allowed"
        )
    browser = await resolve_browser(session, resolver)
    model_attachment = await attach_model_target(session, browser)
    try:
        client = (
            model_attachment.client if model_attachment is not None else browser._internal.client
        )  # noqa: SLF001
        document = await document_identity(session, browser, client)
        if request.get("ref") != DOCUMENT_REF:
            entry = registry.resolve_ref(session, str(request.get("ref") or ""), document)
            if entry.frame.startswith("oopif:"):
                attached = await attach_frame_expected(client, entry.frame)
                await attached.close()
            else:
                await assert_frame_identity_current(client, entry.frame)
        return registry.issue_grant(session, document=document, request=request)
    finally:
        if model_attachment is not None:
            await model_attachment.close()


async def _actual_target(
    browser: BrowserSession, entry: PageRef, document: DocumentIdentity, client: Any = None
) -> dict[str, Any]:
    if entry.backend_node_id == 0:
        return {"role": "document", **({"name": document.title} if document.title else {})}
    cdp = client or browser._internal.client  # noqa: SLF001
    resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": entry.backend_node_id})
    object_id = resolved.get("object", {}).get("objectId")
    if not isinstance(object_id, str):
        raise BrowserAgentError("PAGE_ACTION_STALE_REF", "The element reference no longer resolves")
    described = await cdp.send(
        "Runtime.callFunctionOn",
        {
            "objectId": object_id,
            "functionDeclaration": """function () {
                const tag = this.tagName.toLowerCase();
                const type = String(this.getAttribute('type') || '').toLowerCase();
                const declared = this.getAttribute('role') || '';
                const role = declared || (tag === 'a' ? 'link' : tag === 'select' ? 'combobox' : tag === 'input' || tag === 'textarea' ? (type === 'checkbox' || type === 'radio' ? type : 'textbox') : tag);
                const secret = type === 'password';
                const name = secret ? '' : String(this.getAttribute('aria-label') || this.innerText || this.getAttribute('aria-labelledby') || '').slice(0, 300);
                return { role, name, secret };
            }""",
            "returnByValue": True,
        },
    )
    target = described.get("result", {}).get("value")
    if not isinstance(target, dict) or not isinstance(target.get("role"), str):
        raise BrowserAgentError(
            "PAGE_ACTION_STALE_REF", "The element no longer has an actionable identity"
        )
    if not target.get("name"):
        target.pop("name", None)
    if target.get("secret") is not True:
        target.pop("secret", None)
    return target


async def execute_action(
    session: BrowserAgentSession,
    request: dict[str, Any],
    grant_token: str,
    *,
    registry: BrowserAgentRegistry,
    resolver: SessionResolver,
    write_gate: WriteGate,
    consume_scoped_grant: (Callable[[DocumentIdentity, str, dict[str, Any]], object] | None) = None,
) -> dict[str, Any]:
    browser = await resolve_browser(session, resolver)
    async with (
        registry.write_lock(session.profile),
        profile_lease(session.profile),
    ):
        if not write_gate():
            raise BrowserAgentError(
                "WRITE_BLOCKED",
                "The action was not executed because user control took precedence",
            )
        model_attachment = await attach_model_target(session, browser)
        attached: FrameAttachment | None = None
        try:
            client = (
                model_attachment.client
                if model_attachment is not None
                else browser._internal.client
            )  # noqa: SLF001
            document = await document_identity(session, browser, client)
            previous_target_ids = await page_target_ids(browser._internal.client)  # noqa: SLF001
            if consume_scoped_grant is None:
                registry.consume_grant(session, grant_token, document=document, request=request)
            else:
                consume_scoped_grant(document, str(request["action"]), request)
            if not write_gate():
                raise BrowserAgentError(
                    "WRITE_BLOCKED",
                    "The action was not executed because user control took precedence",
                )
            ref = str(request.get("ref") or "")
            entry = (
                PageRef(0, document.key)
                if ref == DOCUMENT_REF
                else registry.resolve_ref(session, ref, document)
            )
            if entry.frame.startswith("oopif:"):
                attached = await attach_frame_expected(client, entry.frame)
                client = attached.client
                await client.send("DOM.getDocument", {"depth": -1})
            else:
                await assert_frame_identity_current(client, entry.frame)
            if (
                model_attachment is not None
                and attached is None
                and request.get("action") != "back"
            ):
                await client.send("DOM.getDocument", {"depth": -1})
            target = await _actual_target(browser, entry, document, client)
            if target.get("secret") is True:
                return {
                    "type": "execute-result",
                    "outcome": {
                        "ok": False,
                        "target": target,
                        "secret": True,
                        "reason": "Sensitive controls are outside the action scope",
                    },
                }
            action = request["action"]
            temporary_ref = f"ws_exec_{secrets.token_hex(12)}"
            action_cache = RefCache()
            try:
                if action == "back" and model_attachment is not None:
                    result = await navigate.go_back(client, RefCache())
                elif action == "back":
                    result = await browser.go_back()
                else:
                    resolved = await client.send(
                        "DOM.resolveNode", {"backendNodeId": entry.backend_node_id}
                    )
                    object_id = resolved.get("object", {}).get("objectId")
                    if not isinstance(object_id, str):
                        raise BrowserAgentError(
                            "PAGE_ACTION_STALE_REF", "The element no longer resolves"
                        )
                    node = await client.send("DOM.requestNode", {"objectId": object_id})
                    node_id = node.get("nodeId")
                    if not isinstance(node_id, int) or node_id == 0:
                        raise BrowserAgentError(
                            "PAGE_ACTION_STALE_REF",
                            "The element no longer exists in the current document",
                        )
                    if action in {"click", "fill", "scroll"}:
                        await client.send("DOM.scrollIntoViewIfNeeded", {"nodeId": node_id})
                    action_cache = RefCache()
                    action_cache.store(temporary_ref, node_id)
                    if action == "click":
                        result = await interact.click(client, action_cache, ref=temporary_ref)
                    elif action == "fill":
                        result = await interact.fill_text(
                            client, action_cache, text=request["value"], ref=temporary_ref
                        )
                    elif action == "select":
                        result = await interact.select_value(
                            client, action_cache, value=request["value"], ref=temporary_ref
                        )
                    elif action == "scroll":
                        result = await interact.scroll(
                            client,
                            action_cache,
                            direction=request["value"],
                            ref=temporary_ref,
                        )
                    else:
                        return {
                            "type": "execute-result",
                            "outcome": {
                                "ok": False,
                                "target": target,
                                "reason": "The action is not supported by this host",
                            },
                        }
            except BrowserAgentError:
                raise
            except Exception as error:
                return {
                    "type": "execute-result",
                    "outcome": {"ok": False, "target": target, "reason": str(error)[:500]},
                }
            if not result.success:
                return {
                    "type": "execute-result",
                    "outcome": {
                        "ok": False,
                        "target": target,
                        "reason": result.error or "The browser action failed",
                    },
                }
            evaluation: dict[str, Any] = {"expression": "location.href", "returnByValue": True}
            if attached is not None:
                evaluation["contextId"] = attached.execution_context_id
            current = await client.send("Runtime.evaluate", evaluation)
            current_url = str(current.get("result", {}).get("value") or "")
            outcome: dict[str, Any] = {"ok": True, "target": target}
            action_url = attached.url if attached is not None else document.url
            if current_url and current_url != action_url:
                outcome["navigated"] = True
                outcome["documentUrl"] = current_url
            new_target = await wait_for_new_page_target(
                browser._internal.client,  # noqa: SLF001
                previous_target_ids,
            )
            if new_target is not None:
                # A user click that opens a tab is a foreground navigation in a
                # normal browser. The model target follows the new page, and the
                # browser's active tab must follow too; otherwise the viewer keeps
                # rendering the old page and the UI loses Chrome's default action.
                await browser.switch_tab(new_target)
                if admit_model_target(session, new_target):
                    session.document = None
                    session.refs.clear()
                    registry.revoke_grants(session_id=session.id)
                else:
                    outcome["reason"] = (
                        "The new page exceeds the model target workset limit; "
                        "it was not added to the model workset"
                    )
                outcome["navigated"] = True
            return {"type": "execute-result", "outcome": outcome}
        finally:
            if attached is not None:
                await attached.close()
            if model_attachment is not None:
                await model_attachment.close()
