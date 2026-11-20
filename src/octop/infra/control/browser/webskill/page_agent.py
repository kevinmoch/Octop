"""Translate the existing octop-browser session into the public PageAgent contract."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, cast
from urllib.parse import urlsplit

from octop_browser.actions import interact, navigate
from octop_browser.dom.builder import DOMBuilder
from octop_browser.dom.refs import RefCache
from octop_browser.models import ActionMetrics, ToolResult
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
ACTIONABLE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "form",
    "link",
    "radio",
    "searchbox",
    "switch",
    "textbox",
}
PROMOTABLE_ROLES = {
    "generic",
    "img",
    "cell",
    "listitem",
    "columnheader",
    "gridcell",
    "rowheader",
    "option",
    "treeitem",
    "tab",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
}
INTERACTIVE_TOKEN = re.compile(
    r"(?:^|[^a-z])(btn|button|actions?|click(?:able)?|link|menu|nav|tabs?|trigger|"
    r"operate|operations?|handle|entry|toolbar|option)(?:[^a-z]|$)",
    re.IGNORECASE,
)


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


def _node_paths(root: dict[str, Any]) -> dict[int, list[int]]:
    paths: dict[int, list[int]] = {}

    def visit(node: dict[str, Any], path: list[int]) -> None:
        node_id = node.get("nodeId")
        if isinstance(node_id, int):
            paths[node_id] = path
        children = [
            child
            for child in node.get("children", [])
            if isinstance(child, dict) and child.get("nodeName") != "#text"
        ]
        for index, child in enumerate(children):
            visit(child, [*path, index])
        for shadow in node.get("shadowRoots", []):
            if isinstance(shadow, dict):
                visit(shadow, path)

    visit(root, [])
    return paths


def _node_at_path(root: dict[str, Any], path: list[int]) -> dict[str, Any] | None:
    node = root
    for index in path:
        children = [
            child
            for child in node.get("children", [])
            if isinstance(child, dict) and child.get("nodeName") != "#text"
        ]
        if index < 0 or index >= len(children):
            return None
        node = children[index]
    return node


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


def _capturable_image(element: dict[str, Any]) -> bool:
    tag = str(element.get("tag") or "").lower()
    return tag in {"img", "canvas", "svg"} or str(element.get("role") or "").lower() == "img"


def _image_capture_options(request: dict[str, Any]) -> dict[str, int] | None:
    raw = request.get("capture")
    if not isinstance(raw, dict) or raw.get("images") is not True:
        return None
    try:
        max_images = int(raw.get("maxImages", 4))
    except (TypeError, ValueError):
        max_images = 4
    try:
        max_bytes = int(raw.get("maxImageBytes", 2 * 1024 * 1024))
    except (TypeError, ValueError):
        max_bytes = 2 * 1024 * 1024
    return {
        "max_images": max(1, min(max_images, 12)),
        "max_bytes": max(1, min(max_bytes, 10 * 1024 * 1024)),
    }


def _node(element: dict[str, Any], ref: str | None) -> dict[str, Any]:
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
    node: dict[str, Any] = {"role": role}
    if ref:
        node["ref"] = ref
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


def _image_elements(root: dict[str, Any], excluded: set[int]) -> list[dict[str, Any]]:
    """Return image-bearing elements omitted by the control-oriented DOM builder."""
    images: list[dict[str, Any]] = []
    image_tags = {"img", "canvas", "svg"}
    sensitive_tags = {"script", "style", "template", "noscript"}

    def visit(node: dict[str, Any]) -> None:
        tag = str(node.get("nodeName") or "").lower()
        if tag in sensitive_tags:
            return
        node_id = node.get("nodeId")
        attributes = _attributes(node)
        if tag in image_tags and isinstance(node_id, int) and node_id not in excluded:
            images.append(
                {
                    "tag": tag,
                    "role": "img",
                    "node_id": node_id,
                    "accessibleName": (
                        attributes.get("alt")
                        or attributes.get("aria-label")
                        or attributes.get("title")
                        or attributes.get("name")
                        or ""
                    ),
                }
            )
        for key in ("children", "shadowRoots"):
            for child in node.get(key, []):
                if isinstance(child, dict):
                    visit(child)
        content = node.get("contentDocument")
        if isinstance(content, dict):
            visit(content)

    visit(root)
    return images


def _text_content(node: dict[str, Any]) -> str:
    parts: list[str] = []

    def visit(current: dict[str, Any]) -> None:
        if current.get("nodeName") == "#text" and isinstance(current.get("nodeValue"), str):
            parts.append(current["nodeValue"])
        for child in current.get("children", []):
            if isinstance(child, dict):
                visit(child)

    visit(node)
    return " ".join(part.strip() for part in parts if part.strip())


def _field_controls(root: dict[str, Any], excluded: set[int]) -> list[dict[str, Any]]:
    """Infer labels for enterprise form fields whose label has no ``for``/aria link.

    The control kit renders ``div[data-field-item]`` with a title span and a
    nested input/custom combobox. The accessibility builder sees the control
    but not the sibling title, so the model receives an unnamed textbox. This
    bridge exposes the same field name the page displays.
    """
    controls: list[dict[str, Any]] = []
    field_markers = {"kd-cq-field", "kd-cq-field-with-title"}

    def find_label(node: dict[str, Any]) -> str:
        tag = str(node.get("nodeName") or "").lower()
        attrs = _attributes(node)
        title = attrs.get("title", "").strip()
        if title and tag in {"span", "label"}:
            return title
        for child in node.get("children", []):
            if isinstance(child, dict):
                found = find_label(child)
                if found:
                    return found
        return ""

    def find_control(node: dict[str, Any]) -> dict[str, Any] | None:
        tag = str(node.get("nodeName") or "").lower()
        attrs = _attributes(node)
        node_id = node.get("nodeId")
        classes = set(str(attrs.get("class", "")).split())
        if isinstance(node_id, int) and node_id not in excluded:
            if tag in {"input", "textarea", "select"}:
                return {"tag": tag, "node_id": node_id}
            if (
                tag == "div"
                and ("kd-cq-select" in classes or attrs.get("role") in {"combobox", "listbox"})
                and attrs.get("tabindex", "") not in {"", "-1"}
            ):
                return {"tag": tag, "node_id": node_id, "role": "combobox"}
        for child in node.get("children", []):
            if isinstance(child, dict):
                found = find_control(child)
                if found is not None:
                    return found
        for shadow in node.get("shadowRoots", []):
            if isinstance(shadow, dict):
                found = find_control(shadow)
                if found is not None:
                    return found
        return None

    def visit(node: dict[str, Any]) -> None:
        tag = str(node.get("nodeName") or "").lower()
        attrs = _attributes(node)
        classes = set(str(attrs.get("class", "")).split())
        if tag not in {"script", "style", "template", "noscript"} and (
            attrs.get("data-field-item") == "true" or classes & field_markers
        ):
            label = find_label(node)
            control = find_control(node)
            if label and control is not None and control["node_id"] not in excluded:
                controls.append(
                    {
                        "tag": control["tag"],
                        "role": control.get("role")
                        or ("combobox" if control["tag"] == "select" else "textbox"),
                        "node_id": control["node_id"],
                        "accessibleName": label,
                    }
                )
        for key in ("children", "shadowRoots"):
            for child in node.get(key, []):
                if isinstance(child, dict):
                    visit(child)
        content = node.get("contentDocument")
        if isinstance(content, dict):
            visit(content)

    visit(root)
    return controls


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

    def visit(node: dict[str, Any], in_field_title: bool = False) -> None:
        tag = str(node.get("nodeName") or "").lower()
        if tag in sensitive_tags:
            return
        attrs = {
            str(key): str(value)
            for key, value in zip(
                node.get("attributes", [])[::2], node.get("attributes", [])[1::2], strict=False
            )
        }
        role = attrs.get("role", "").lower() or "generic"
        title = attrs.get("title", "").strip()
        aria = attrs.get("aria-label", "").strip()
        tabindex = attrs.get("tabindex", "")
        # The nodeValue is usually on #text children, not the element; use the
        # element's title/aria as the stable accessible name for these cards.
        classes = str(attrs.get("class", ""))
        class_and_id = f"{classes} {attrs.get('id', '')}"
        href = attrs.get("href", "")
        interactive_token = INTERACTIVE_TOKEN.search(class_and_id) is not None
        text = _text_content(node)
        name = title or aria or text
        title_context = (
            in_field_title or "kd-cq-field-title-wrap" in classes or "_3wxkawCQ" in classes
        )
        if (
            not title_context
            and name
            and tag not in {"html", "body", "iframe", "frame", "img", "input", "textarea", "select"}
            and role not in {"button", "link", "tab", "menuitem", "option", "checkbox", "radio"}
            and role in PROMOTABLE_ROLES
            and (
                title
                or aria
                or tabindex not in {"", "-1"}
                or interactive_token
                or href
                or role in {"button", "link", "option", "tab", "menuitem"}
            )
        ):
            promoted.append(
                {
                    "node_id": node.get("nodeId"),
                    "role": "link" if href else "button",
                    "name": name[:300],
                }
            )
        elif (
            not title_context
            and tag in {"li", "div"}
            and "dropdown-menu-item" in classes
            and isinstance(node.get("nodeId"), int)
            and _text_content(node)
        ):
            promoted.append(
                {
                    "node_id": node.get("nodeId"),
                    "role": "option",
                    "name": _text_content(node)[:300],
                }
            )
        for child in node.get("children", []):
            if isinstance(child, dict):
                visit(child, title_context)
        for shadow in node.get("shadowRoots", []):
            if isinstance(shadow, dict):
                visit(shadow, title_context)

    visit(root)
    return [node for node in promoted if isinstance(node.get("node_id"), int)]


_LIVE_VALUE_FUNCTION = r"""function() {
    const tag = this.tagName.toLowerCase();
    if (tag === 'select') {
        return String(this.selectedOptions?.[0]?.label || this.value || '');
    }
    if (tag === 'input' && (this.type === 'checkbox' || this.type === 'radio')) {
        return this.checked ? 'true' : 'false';
    }
    if (tag === 'input' || tag === 'textarea') return String(this.value || '');
    if (this.getAttribute('role') === 'combobox' || String(this.getAttribute('class') || '').includes('kd-cq-select')) {
        return String(this.innerText || this.getAttribute('data-value') || '').replace(/\s+/g, ' ').trim();
    }
    return '';
}"""


async def _live_value(client: Any, backend_node_id: int) -> str | None:
    try:
        resolved = await client.send("DOM.resolveNode", {"backendNodeId": backend_node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not isinstance(object_id, str) or not object_id:
            return None
        result = await client.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": _LIVE_VALUE_FUNCTION,
                "returnByValue": True,
            },
        )
    except Exception:  # noqa: BLE001 - value is supplemental perception information
        return None
    value = result.get("result", {}).get("value")
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:200] if value else None


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
    image_options = _image_capture_options(request)
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
        promoted = _promoted_elements(projected_root)
        by_node = {
            element.get("node_id"): element
            for element in raw_elements
            if isinstance(element, dict) and isinstance(element.get("node_id"), int)
        }
        for element in promoted:
            node_id = element.get("node_id")
            existing = by_node.get(node_id)
            if existing is not None:
                if existing.get("role") in {None, "", "generic", "div", "span"}:
                    existing["role"] = element["role"]
                if not existing.get("accessibleName") and not existing.get("name"):
                    existing["accessibleName"] = element.get("name")
            elif node_id not in known_nodes:
                raw_elements.append(element)
        excluded = _sensitive_subtrees(projected_root)
        field_controls = _field_controls(projected_root, excluded)
        by_node = {
            element.get("node_id"): element
            for element in raw_elements
            if isinstance(element, dict) and isinstance(element.get("node_id"), int)
        }
        for field in field_controls:
            existing = by_node.get(field["node_id"])
            if existing is not None:
                # The control's displayed value (or selected label) is not its
                # field name. Always bind the explicit wrapper title so the model
                # sees "问题属性"/"问题描述" instead of "规格不符"/"测试".
                existing["accessibleName"] = field["accessibleName"]
                if field["role"] in {"combobox", "textbox"}:
                    existing["role"] = field["role"]
            else:
                raw_elements.append(field)
        if image_options is not None:
            known_nodes = {
                element.get("node_id")
                for element in raw_elements
                if isinstance(element, dict) and isinstance(element.get("node_id"), int)
            }
            raw_elements.extend(
                element
                for element in _image_elements(projected_root, excluded)
                if element.get("node_id") not in known_nodes
            )
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
        # A single-frame scope has an empty frame path; that must not invalidate
        # refs on every perception. Only a real document change does.
        if session.document is None or session.document.key != after.key:
            session.refs.clear()
        session.document = after
        node_paths = _node_paths(projected_root)
        nodes: list[dict[str, Any]] = []
        targets: list[dict[str, Any]] = []
        image_candidates: list[tuple[dict[str, Any], str]] = []
        for element in elements:
            backend_node_id = backend_ids.get(element["node_id"])
            if backend_node_id is None:
                raise BrowserAgentError(
                    "PERCEPTION_FAILED",
                    "Could not bind a visible control to its backend node identity",
                )
            role = _role(element)
            if _capturable_image(element):
                role = "img"
            needs_ref = role in ACTIONABLE_ROLES or _capturable_image(element)
            node = _node(element, None)
            node_ref: str | None = None
            if needs_ref:
                signature = json.dumps(
                    {
                        "role": node.get("role"),
                        "tag": str(element.get("tag") or "").lower(),
                        **({"name": node.get("name")} if node.get("name") else {}),
                        **({"href": node.get("href")} if node.get("href") else {}),
                        "path": node_paths.get(element["node_id"], []),
                        "frame": selected_frame,
                    },
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                node_ref = registry.issue_ref(session, backend_node_id, selected_frame, signature)
                node["ref"] = node_ref
            if (
                node_ref
                and role in {"textbox", "searchbox", "combobox", "checkbox", "radio", "switch"}
                and not _secret(element)
            ):
                value = await _live_value(client, backend_node_id)
                if value is not None:
                    node["value"] = value
            if _capturable_image(element):
                node["capturable"] = True
                image_candidates.append((node, node_ref or ""))
            nodes.append(node)
            if node_ref:
                targets.append(_target(element, node_ref))
        result: dict[str, Any] = {
            "nodes": nodes,
            "page": {
                "url": selected_url,
                **({"title": selected_title} if selected_title else {}),
            },
            **({"nestedFrames": nested} if nested else {}),
        }
        if image_options is not None:
            image_payloads: list[dict[str, Any]] = []
            omitted = 0
            failures = 0
            for node, ref in image_candidates:
                if len(image_payloads) >= image_options["max_images"]:
                    omitted += 1
                    node["imageNote"] = (
                        "Image omitted because the per-message image budget was reached."
                    )
                    continue
                try:
                    captured = await capture_image(
                        session,
                        {"ref": ref, "max_bytes": image_options["max_bytes"]},
                        registry=registry,
                        resolver=resolver,
                    )
                except Exception as error:  # noqa: BLE001 - one image must not fail perception
                    failures += 1
                    node["imageNote"] = f"Image capture failed: {str(error)[:200]}"
                    continue
                payload = captured.get("result") if isinstance(captured, dict) else None
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
                    failures += 1
                    reason = payload.get("reason") if isinstance(payload, dict) else None
                    node["imageNote"] = (
                        str(reason)
                        if isinstance(reason, str) and reason
                        else "Image capture returned no bytes."
                    )
                    continue
                image_id = f"img_{len(image_payloads) + 1}"
                node["imageId"] = image_id
                image_payloads.append(
                    {
                        "id": image_id,
                        "mimeType": str(payload.get("mimeType") or "image/png"),
                        "data": payload["data"],
                        "level": str(payload.get("level") or "canvas"),
                    }
                )
            if image_payloads:
                result["images"] = image_payloads
            if omitted:
                result["imagesOmitted"] = omitted
            if failures:
                result["imageFailures"] = failures
        return {
            "type": "perceive-result",
            "result": result,
            "targets": targets,
            "document": after.public(),
        }
    finally:
        if attached is not None:
            await attached.close()
        if model_attachment is not None:
            await model_attachment.close()


_IMAGE_SOURCE_FUNCTION = """
async function() {
  const source = this.currentSrc || this.src || "";
  if (source) {
    try {
      const response = await fetch(source, { credentials: "include" });
      if (!response.ok) {
        return { ok: false, reason: `HTTP ${response.status}` };
      }
      const type = (response.headers.get("content-type") || "").split(";")[0].trim();
      if (!type.startsWith("image/")) {
        return { ok: false, reason: `unexpected content type ${type || "unknown"}` };
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      let binary = "";
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
      }
      return {
        ok: true,
        level: "src",
        mimeType: type,
        width: this.naturalWidth || this.width || 0,
        height: this.naturalHeight || this.height || 0,
        data: btoa(binary),
      };
    } catch (error) {
      return { ok: false, reason: String(error).slice(0, 180) };
    }
  }
  if (typeof this.toDataURL === "function") {
    const url = this.toDataURL("image/png");
    const comma = url.indexOf(",");
    if (comma >= 0) {
      return {
        ok: true,
        level: "canvas",
        mimeType: "image/png",
        width: this.width || 0,
        height: this.height || 0,
        data: url.slice(comma + 1),
      };
    }
  }
  return { ok: false, reason: "The element has no readable image source" };
}
"""


async def _capture_image_source(
    client: Any,
    backend_node_id: int,
    max_bytes: int,
) -> dict[str, Any] | None:
    """Read image bytes through the page before falling back to a screenshot.

    Hidden/lazy images and images with no layout box still have a readable
    source; screenshot-only capture cannot reach them. Returning None means
    the caller should try the screenshot path.
    """
    try:
        resolved = await client.send("DOM.resolveNode", {"backendNodeId": backend_node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not isinstance(object_id, str) or not object_id:
            return None
        evaluated = await client.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": _IMAGE_SOURCE_FUNCTION,
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
    except Exception:  # noqa: BLE001 - screenshot path remains available
        return None
    value = evaluated.get("result", {}).get("value")
    if not isinstance(value, dict) or value.get("ok") is not True:
        return None
    data = value.get("data")
    if not isinstance(data, str) or not data:
        return None
    try:
        decoded = base64.b64decode(data, validate=True)
    except Exception:  # noqa: BLE001
        return None
    if len(decoded) > max_bytes:
        return {
            "failure": (
                f"The source image is {len(decoded)} bytes, over the {max_bytes}-byte budget"
            )
        }
    mime_type = value.get("mimeType")
    return {
        "data": data,
        "mimeType": str(mime_type) if isinstance(mime_type, str) else "image/png",
        "width": max(1, int(value.get("width") or 0)),
        "height": max(1, int(value.get("height") or 0)),
        "bytes": len(decoded),
        "level": str(value.get("level") or "src"),
    }


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
        entry = await _ensure_ref(client, session, ref, entry, browser, document)
        source = await _capture_image_source(client, entry.backend_node_id, max_bytes)
        if source is not None and "data" in source:
            return {"type": "capture-image-result", "result": source}
        if source is not None and "failure" in source:
            return {
                "type": "capture-image-result",
                "result": {
                    "id": ref,
                    "reason": str(source["failure"]),
                    "triedLevels": ["src-bytes"],
                },
            }
        try:
            await client.send(
                "DOM.scrollIntoViewIfNeeded", {"backendNodeId": entry.backend_node_id}
            )
            box = await client.send("DOM.getBoxModel", {"backendNodeId": entry.backend_node_id})
        except Exception as error:  # noqa: BLE001 - no layout/hidden image is a fact
            return {
                "type": "capture-image-result",
                "result": {
                    "id": ref,
                    "reason": f"The element has no visible box: {str(error)[:200]}",
                    "triedLevels": ["cdp-screenshot"],
                },
            }
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


async def _recover_ref(
    client: Any,
    entry: PageRef,
) -> PageRef | None:
    if not entry.signature:
        return None
    try:
        signature = json.loads(entry.signature)
    except json.JSONDecodeError:
        return None
    path = signature.get("path")
    expected_tag = str(signature.get("tag") or "").lower()
    if not isinstance(path, list) or not all(isinstance(index, int) for index in path):
        return None
    try:
        raw = await client.send("DOM.getDocument", {"depth": -1, "pierce": True})
    except Exception:  # noqa: BLE001
        return None
    root = raw.get("root", {})
    node = _node_at_path(root, path)
    if not isinstance(node, dict):
        return None
    tag = str(node.get("nodeName") or "").lower()
    if expected_tag and tag != expected_tag:
        return None
    backend_node_id = node.get("backendNodeId")
    if not isinstance(backend_node_id, int) or backend_node_id <= 0:
        return None
    return replace(entry, backend_node_id=backend_node_id)


async def _ensure_ref(
    client: Any,
    session: BrowserAgentSession,
    ref: str,
    entry: PageRef,
    browser: BrowserSession,
    document: DocumentIdentity,
) -> PageRef:
    if entry.backend_node_id <= 0:
        return entry
    try:
        await client.send("DOM.describeNode", {"backendNodeId": entry.backend_node_id})
        return entry
    except Exception:  # noqa: BLE001 - attempt signature recovery below
        recovered = await _recover_ref(client, entry)
        if recovered is None:
            raise BrowserAgentError(
                "PAGE_ACTION_STALE_REF",
                "The element reference is unknown or expired",
            ) from None
        target = await _actual_target(browser, recovered, document, client)
        try:
            signature = json.loads(entry.signature)
        except json.JSONDecodeError:
            signature = {}
        expected_role = signature.get("role")
        expected_name = signature.get("name")
        if (isinstance(expected_role, str) and expected_role != target.get("role")) or (
            isinstance(expected_name, str) and expected_name != target.get("name")
        ):
            raise BrowserAgentError(
                "PAGE_ACTION_STALE_REF",
                "The element reference no longer identifies the same control",
            ) from None
        session.refs[ref] = recovered
        return recovered


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
            "functionDeclaration": r"""function () {
                const tag = this.tagName.toLowerCase();
                const type = String(this.getAttribute('type') || '').toLowerCase();
                const declared = this.getAttribute('role') || '';
                let role = declared || (tag === 'a' ? 'link' : tag === 'select' ? 'combobox' : tag === 'input' || tag === 'textarea' ? (type === 'checkbox' || type === 'radio' ? type : 'textbox') : tag);
                const classes = String(this.getAttribute('class') || '');
                if (!declared && tag === 'div' && (classes.includes('kd-cq-select') || classes.includes('combobox'))) role = 'combobox';
                const secret = type === 'password';
                const text = (el) => String((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
                let name = '';
                if (!secret) {
                    const by = this.getAttribute('aria-labelledby');
                    if (by) {
                        const parts = by.split(/\s+/).map((id) => text(this.ownerDocument.getElementById(id))).filter(Boolean);
                        if (parts.length) name = parts.join(' ');
                    }
                    if (!name) name = String(this.getAttribute('aria-label') || '').trim();
                    const labels = this.labels;
                    if (!name && labels && labels.length) name = [...labels].map(text).filter(Boolean).join(' ');
                    if (!name) {
                        const wrapping = this.closest('label');
                        if (wrapping) name = text(wrapping);
                    }
                    if (!name) {
                        const field = this.closest('[data-field-item="true"], .kd-cq-field');
                        const title = field && (field.querySelector('span[title]') || field.querySelector('.kd-cq-field-title-wrap'));
                        if (title) name = String(title.getAttribute('title') || text(title)).replace(/\s*\*+\s*$/, '').trim();
                    }
                    if (!name) name = String(this.getAttribute('placeholder') || '').trim();
                    if (!name && tag === 'img') name = String(this.getAttribute('alt') || '').trim();
                    if (!name && !['input', 'textarea', 'select'].includes(tag) && role !== 'combobox') name = text(this);
                    if (!name) name = String(this.getAttribute('title') || '').trim();
                }
                return { role, name: name.slice(0, 300), secret };
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


_TOGGLE_STATE_FUNCTION = """function() {
    const aria = this.getAttribute('aria-checked') ?? this.getAttribute('aria-pressed');
    const current = aria !== null ? aria === 'true' : (this.tagName === 'INPUT' ? Boolean(this.checked) : undefined);
    return { toggle: current !== undefined, current: current === true };
}"""

_HOVER_FUNCTION = """function() {
    const r = this.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    const base = { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, button: 0 };
    const noBubble = { ...base, bubbles: false };
    const pointer = { pointerId: 1, pointerType: 'mouse', isPrimary: true };
    const P = this.ownerDocument.defaultView?.PointerEvent;
    if (P) {
        this.dispatchEvent(new P('pointerover', { ...base, ...pointer }));
        this.dispatchEvent(new P('pointerenter', { ...noBubble, ...pointer }));
    }
    this.dispatchEvent(new MouseEvent('mouseover', base));
    this.dispatchEvent(new MouseEvent('mouseenter', noBubble));
    if (P) this.dispatchEvent(new P('pointermove', { ...base, ...pointer }));
    this.dispatchEvent(new MouseEvent('mousemove', base));
    return true;
}"""

_SUBMIT_FUNCTION = """function() {
    const form = this.tagName === 'FORM' ? this : this.form;
    if (!form || typeof form.requestSubmit !== 'function') return false;
    form.requestSubmit();
    return true;
}"""


async def _element_center(client: Any, backend_node_id: int) -> tuple[float, float]:
    box = await client.send("DOM.getBoxModel", {"backendNodeId": backend_node_id})
    quad = box.get("model", {}).get("border", [])
    if not isinstance(quad, list) or len(quad) != 8:
        raise BrowserAgentError("PAGE_ACTION_STALE_REF", "The element has no visible box")
    xs = [float(quad[index]) for index in (0, 2, 4, 6)]
    ys = [float(quad[index]) for index in (1, 3, 5, 7)]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


async def _click_dropdown_option(
    client: Any, value: str, control_object_id: str | None = None
) -> bool:
    """Choose a custom-kit dropdown item by its visible text and verify it settled.

    These comboboxes are ``div[tabindex=0]`` widgets, not native ``select``
    elements. After the authorized ref is clicked open, the matching item is
    a portal ``li`` outside the field, so it is selected by text rather than by
    a model-supplied selector.
    """
    if control_object_id is not None:
        function = r"""async function(value) {
            const wanted = String(value || "").trim();
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
            };
            const nodes = [...document.querySelectorAll(
                '[role="option"], li, [class*="dropdown-menu-item"], [class*="option"]'
            )];
            const target = nodes.find((el) => visible(el) && (el.innerText || el.textContent || "").trim() === wanted)
                || nodes.find((el) => visible(el) && (el.getAttribute("aria-label") || el.getAttribute("data-value") || "").trim() === wanted);
            if (!target) return { found: false, settled: false };
            const rect = target.getBoundingClientRect();
            const init = {
                bubbles: true,
                cancelable: true,
                view: window,
                clientX: rect.left + 4,
                clientY: rect.top + 4,
                button: 0
            };
            target.dispatchEvent(new PointerEvent("pointerdown", init));
            target.dispatchEvent(new MouseEvent("mousedown", init));
            target.dispatchEvent(new PointerEvent("pointerup", init));
            target.dispatchEvent(new MouseEvent("mouseup", init));
            target.dispatchEvent(new MouseEvent("click", init));
            await new Promise((resolve) => setTimeout(resolve, 180));
            const shown = `${this.innerText || ""} ${this.value || ""} ${this.getAttribute("data-value") || ""}`
                .replace(/\s+/g, " ").trim();
            return { found: true, settled: shown.includes(wanted) };
        }"""
        result = await client.send(
            "Runtime.callFunctionOn",
            {
                "objectId": control_object_id,
                "functionDeclaration": function,
                "arguments": [{"value": value}],
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        payload = result.get("result", {}).get("value")
        return (
            isinstance(payload, dict)
            and payload.get("found") is True
            and payload.get("settled") is True
        )
    expression = f"""(() => {{
        const wanted = {json.dumps(value, ensure_ascii=False)}.trim();
        const visible = (el) => {{
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
        }};
        const nodes = [...document.querySelectorAll(
            '[role="option"], li, [class*="dropdown-menu-item"], [class*="option"]'
        )];
        const exact = nodes.filter((el) => visible(el) && (el.innerText || el.textContent || "").trim() === wanted);
        const target = exact[0] || nodes.find((el) =>
            visible(el) && (el.getAttribute("aria-label") || el.getAttribute("data-value") || "").trim() === wanted
        );
        if (!target) return false;
        const rect = target.getBoundingClientRect();
        const init = {{
            bubbles: true,
            cancelable: true,
            view: window,
            clientX: rect.left + 4,
            clientY: rect.top + 4,
            button: 0,
        }};
        target.dispatchEvent(new PointerEvent("pointerdown", init));
        target.dispatchEvent(new MouseEvent("mousedown", init));
        target.dispatchEvent(new PointerEvent("pointerup", init));
        target.dispatchEvent(new MouseEvent("mouseup", init));
        target.dispatchEvent(new MouseEvent("click", init));
        return true;
    }})()"""
    result = await client.send(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    return result.get("result", {}).get("value") is True


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
            entry = await _ensure_ref(client, session, ref, entry, browser, document)
            if (
                model_attachment is not None
                and attached is None
                and request.get("action") != "back"
            ):
                await client.send("DOM.getDocument", {"depth": -1})
            target = await _actual_target(browser, entry, document, client)
            target_tag = ""
            if entry.backend_node_id > 0:
                try:
                    described_node = await client.send(
                        "DOM.describeNode", {"backendNodeId": entry.backend_node_id}
                    )
                    target_tag = str(described_node.get("node", {}).get("nodeName") or "").lower()
                except Exception:  # noqa: BLE001 - tag is only used for select routing
                    target_tag = ""
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
            noop = False
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
                        if target_tag == "select":
                            result = await interact.select_value(
                                client,
                                action_cache,
                                value=request["value"],
                                ref=temporary_ref,
                            )
                        else:
                            # Vendor comboboxes are div[tabindex=0] widgets.
                            # Setting .value on them does nothing useful.
                            await interact.click(client, action_cache, ref=temporary_ref)
                            await asyncio.sleep(0.2)
                            if await _click_dropdown_option(client, str(request["value"])):
                                # The option click selected the item; the
                                # outcome is a select, not merely opening menu.
                                result = ToolResult(
                                    success=True,
                                    content=f"Selected {request['value']}",
                                    metrics=ActionMetrics(
                                        action="select",
                                        duration_ms=0,
                                        dom_nodes_scanned=0,
                                        estimated_tokens=0,
                                    ),
                                )
                            else:
                                return {
                                    "type": "execute-result",
                                    "outcome": {
                                        "ok": False,
                                        "target": target,
                                        "reason": (
                                            "The dropdown did not expose an option "
                                            f"matching {request['value']!r}"
                                        ),
                                    },
                                }
                    elif action == "scroll":
                        result = await interact.scroll(
                            client,
                            action_cache,
                            direction=request["value"],
                            ref=temporary_ref,
                        )
                    elif action == "set":
                        state = await client.send(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": object_id,
                                "functionDeclaration": _TOGGLE_STATE_FUNCTION,
                                "returnByValue": True,
                            },
                        )
                        info = state.get("result", {}).get("value")
                        if not isinstance(info, dict) or info.get("toggle") is not True:
                            return {
                                "type": "execute-result",
                                "outcome": {
                                    "ok": False,
                                    "target": target,
                                    "reason": "The element is not a toggle",
                                },
                            }
                        desired = request["value"] == "true"
                        if info.get("current") is desired:
                            result = ToolResult(
                                success=True,
                                content="Already in the requested state",
                                metrics=ActionMetrics(
                                    action="set",
                                    duration_ms=0,
                                    dom_nodes_scanned=0,
                                    estimated_tokens=0,
                                ),
                            )
                            noop = True
                        else:
                            result = await interact.click(client, action_cache, ref=temporary_ref)
                            noop = False
                    elif action == "hover":
                        await client.send(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": object_id,
                                "functionDeclaration": _HOVER_FUNCTION,
                                "returnByValue": True,
                            },
                        )
                        result = ToolResult(
                            success=True,
                            content="Hovered",
                            metrics=ActionMetrics(
                                action="hover",
                                duration_ms=0,
                                dom_nodes_scanned=0,
                                estimated_tokens=0,
                            ),
                        )
                    elif action == "submit":
                        submitted = await client.send(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": object_id,
                                "functionDeclaration": _SUBMIT_FUNCTION,
                                "returnByValue": True,
                            },
                        )
                        if submitted.get("result", {}).get("value") is not True:
                            return {
                                "type": "execute-result",
                                "outcome": {
                                    "ok": False,
                                    "target": target,
                                    "reason": "The element does not belong to a form",
                                },
                            }
                        result = ToolResult(
                            success=True,
                            content="Submitted",
                            metrics=ActionMetrics(
                                action="submit",
                                duration_ms=0,
                                dom_nodes_scanned=0,
                                estimated_tokens=0,
                            ),
                        )
                    elif action == "drag":
                        destination_ref = str(request["value"])
                        destination_entry = registry.resolve_ref(session, destination_ref, document)
                        if destination_entry.frame != entry.frame:
                            return {
                                "type": "execute-result",
                                "outcome": {
                                    "ok": False,
                                    "target": target,
                                    "reason": "Dragging across frames is not supported",
                                },
                            }
                        start_x, start_y = await _element_center(client, entry.backend_node_id)
                        end_x, end_y = await _element_center(
                            client, destination_entry.backend_node_id
                        )
                        for event_type, x, y, buttons in (
                            ("mouseMoved", start_x, start_y, 0),
                            ("mousePressed", start_x, start_y, 1),
                            ("mouseMoved", (start_x + end_x) / 2, (start_y + end_y) / 2, 1),
                            ("mouseMoved", end_x, end_y, 1),
                            ("mouseReleased", end_x, end_y, 0),
                        ):
                            await client.send(
                                "Input.dispatchMouseEvent",
                                {
                                    "type": event_type,
                                    "x": x,
                                    "y": y,
                                    "button": "left",
                                    "buttons": buttons,
                                    "clickCount": 1,
                                },
                            )
                        result = ToolResult(
                            success=True,
                            content="Dragged",
                            metrics=ActionMetrics(
                                action="drag",
                                duration_ms=0,
                                dom_nodes_scanned=0,
                                estimated_tokens=0,
                            ),
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
            if noop:
                outcome["noop"] = True
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
