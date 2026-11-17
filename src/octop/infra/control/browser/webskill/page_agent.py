"""Translate the existing octop-browser session into the public PageAgent contract."""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, cast

from octop_browser.actions import interact
from octop_browser.dom.builder import DOMBuilder
from octop_browser.dom.refs import RefCache
from octop_browser.session import BrowserSession

from .sessions import (
    DOCUMENT_REF,
    BrowserAgentError,
    BrowserAgentRegistry,
    BrowserAgentSession,
    DocumentIdentity,
    PageRef,
)

SessionResolver = Callable[..., Awaitable[Any]]
WriteGate = Callable[[], bool]
MAX_P1_NODES = 1500


def public_error_code(code: str) -> str:
    return {
        "PERCEPTION_SUBTREE_UNSUPPORTED": "PERCEPTION_SUBTREE_UNSUPPORTED",
        "PERCEPTION_FAILED": "PERCEPTION_FAILED",
        "PAGE_ACTION_STALE_REF": "PAGE_ACTION_STALE_REF",
        "GRANT_INVALID": "PAGE_ACTION_STALE_REF",
        "GRANT_MISMATCH": "PAGE_ACTION_STALE_REF",
        "GRANT_REQUIRED": "PAGE_ACTION_OUT_OF_SCOPE",
        "DOCUMENT_CHANGED": "PAGE_ACTION_STALE_REF",
        "DOCUMENT_REQUIRED": "PAGE_ACTION_STALE_REF",
        "PAGE_TAB_EXPIRED": "PAGE_TAB_EXPIRED",
        "PAGE_TAB_UNKNOWN": "PAGE_TAB_UNKNOWN",
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
) -> DocumentIdentity:
    current = await _current_target(browser)
    if session.model_target is None:
        session.model_target = current
    elif current != session.model_target:
        raise BrowserAgentError(
            "PAGE_TAB_EXPIRED",
            "The model target is no longer bound to the visible target; rebind explicitly instead of following a user tab switch",
        )
    client = browser._internal.client  # noqa: SLF001
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
            [session.user_id, session.profile, current, loader, url],
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        target_id=current,
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
        else str(element.get("accessibleName") or element.get("text") or element.get("label") or "")
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


async def read_page(
    session: BrowserAgentSession,
    request: dict[str, Any],
    *,
    resolver: SessionResolver,
    registry: BrowserAgentRegistry,
) -> dict[str, Any]:
    scope = request.get("scope")
    if not isinstance(scope, dict) or scope.get("include") != ["body"] or "frames" in scope:
        raise BrowserAgentError(
            "PERCEPTION_FAILED", "P1 perception is restricted to the main document body"
        )
    if "ref" in request:
        raise BrowserAgentError(
            "PERCEPTION_SUBTREE_UNSUPPORTED",
            "Subtree perception is not available in P1; no full-page fallback is performed",
        )
    capture = request.get("capture")
    if isinstance(capture, dict) and capture.get("images") is True:
        raise BrowserAgentError("PERCEPTION_FAILED", "Image capture is not available in P1")
    browser = await resolve_browser(session, resolver)
    before = await document_identity(session, browser)
    raw = await browser._internal.client.send("DOM.getDocument", {"depth": -1})  # noqa: SLF001
    root = raw.get("root", {})
    if not isinstance(root, dict):
        raise BrowserAgentError("PERCEPTION_FAILED", "The browser document is unavailable")
    try:
        dom = DOMBuilder(
            dom=root,
            url=before.url,
            title=before.title,
            ref_cache=RefCache(),
        ).build(level="structured")
        raw_elements = json.loads(dom).get("elements", [])
    except (json.JSONDecodeError, AttributeError) as error:
        raise BrowserAgentError(
            "PERCEPTION_FAILED", "The browser DOM response is invalid"
        ) from error
    if not isinstance(raw_elements, list):
        raise BrowserAgentError("PERCEPTION_FAILED", "The browser DOM response is invalid")
    excluded = _sensitive_subtrees(root)
    backend_ids = _node_index(root)
    if any(element.get("framePath") for element in raw_elements if isinstance(element, dict)):
        raise BrowserAgentError(
            "PERCEPTION_SUBTREE_UNSUPPORTED",
            "Frame perception requires the P2 target/frame adapter",
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
            f"The document has more than {MAX_P1_NODES} readable controls; P1 does not silently truncate it",
        )
    after = await document_identity(session, browser)
    if before.key != after.key:
        raise BrowserAgentError(
            "PERCEPTION_FAILED", "The document changed while it was being perceived"
        )
    session.document = after
    session.refs.clear()
    nodes: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for element in elements:
        backend_node_id = backend_ids.get(element["node_id"])
        if backend_node_id is None:
            raise BrowserAgentError(
                "PERCEPTION_FAILED", "Could not bind a visible control to its backend node identity"
            )
        ref = registry.issue_ref(session, backend_node_id)
        nodes.append(_node(element, ref))
        targets.append(_target(element, ref))
    return {
        "type": "perceive-result",
        "result": {
            "nodes": nodes,
            "page": {"url": after.url, **({"title": after.title} if after.title else {})},
        },
        "targets": targets,
        "document": after.public(),
    }


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
    document = await document_identity(session, browser)
    return registry.issue_grant(session, document=document, request=request)


async def _actual_target(
    browser: BrowserSession, entry: PageRef, document: DocumentIdentity
) -> dict[str, Any]:
    if entry.backend_node_id == 0:
        return {"role": "document", **({"name": document.title} if document.title else {})}
    resolved = await browser._internal.client.send(  # noqa: SLF001
        "DOM.resolveNode", {"backendNodeId": entry.backend_node_id}
    )
    object_id = resolved.get("object", {}).get("objectId")
    if not isinstance(object_id, str):
        raise BrowserAgentError("PAGE_ACTION_STALE_REF", "The element reference no longer resolves")
    described = await browser._internal.client.send(  # noqa: SLF001
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
) -> dict[str, Any]:
    browser = await resolve_browser(session, resolver)
    async with registry.write_lock(session.profile):
        document = await document_identity(session, browser)
        registry.consume_grant(session, grant_token, document=document, request=request)
        if not write_gate():
            raise BrowserAgentError(
                "WRITE_BLOCKED", "The action was not executed because user control took precedence"
            )
        ref = str(request.get("ref") or "")
        entry = (
            PageRef(0, document.key)
            if ref == DOCUMENT_REF
            else registry.resolve_ref(session, ref, document)
        )
        target = await _actual_target(browser, entry, document)
        if target.get("secret") is True:
            return {
                "type": "execute-result",
                "outcome": {
                    "ok": False,
                    "target": target,
                    "secret": True,
                    "reason": "Sensitive controls are outside the P1 action scope",
                },
            }
        action = request["action"]
        temporary_ref = f"ws_exec_{secrets.token_hex(12)}"
        try:
            if action == "back":
                result = await browser.go_back()
            else:
                resolved = await browser._internal.client.send(  # noqa: SLF001
                    "DOM.resolveNode", {"backendNodeId": entry.backend_node_id}
                )
                object_id = resolved.get("object", {}).get("objectId")
                if not isinstance(object_id, str):
                    raise BrowserAgentError(
                        "PAGE_ACTION_STALE_REF", "The element no longer resolves"
                    )
                node = await browser._internal.client.send(  # noqa: SLF001
                    "DOM.requestNode", {"objectId": object_id}
                )
                node_id = node.get("nodeId")
                if not isinstance(node_id, int) or node_id == 0:
                    raise BrowserAgentError(
                        "PAGE_ACTION_STALE_REF",
                        "The element no longer exists in the current document",
                    )
                action_cache = RefCache()
                action_cache.store(temporary_ref, node_id)
                client = browser._internal.client  # noqa: SLF001
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
                    result = await interact.scroll(client, action_cache, direction=request["value"])
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
        current = await browser._internal.client.send(  # noqa: SLF001
            "Runtime.evaluate", {"expression": "location.href", "returnByValue": True}
        )
        current_url = str(current.get("result", {}).get("value") or "")
        outcome: dict[str, Any] = {"ok": True, "target": target}
        if current_url and current_url != document.url:
            outcome["navigated"] = True
            outcome["documentUrl"] = current_url
        return {"type": "execute-result", "outcome": outcome}
