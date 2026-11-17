"""Restricted same-origin frame routing for the WebSkill PageAgent adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from octop_browser.cdp.client import CDPSessionError

from .sessions import BrowserAgentError

FRAME_SCOPE_ERROR = "This frame is outside the allowed perception scope."
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class FrameRoute:
    path: tuple[str, ...]
    url: str
    hint: str
    owner_backend_node_id: int
    document: dict[str, Any] | None

    @property
    def label(self) -> str:
        return " >>> ".join(self.path)


def _attributes(node: dict[str, Any]) -> dict[str, str]:
    raw = node.get("attributes", [])
    return dict(zip(raw[::2], raw[1::2], strict=False)) if isinstance(raw, list) else {}


def _selector(node: dict[str, Any], siblings: list[dict[str, Any]]) -> str:
    tag = str(node.get("nodeName") or "").lower()
    attrs = _attributes(node)
    element_id = attrs.get("id", "")
    if _SAFE_ID.fullmatch(element_id):
        return f"{tag}#{element_id}"
    position = [
        sibling for sibling in siblings if str(sibling.get("nodeName") or "").lower() == tag
    ].index(node) + 1
    return f"{tag}:nth-of-type({position})"


def discover_frame_routes(root: dict[str, Any], page_url: str) -> list[FrameRoute]:
    routes: list[FrameRoute] = []

    def visit(node: dict[str, Any], parent_path: tuple[str, ...]) -> None:
        children = [child for child in node.get("children", []) if isinstance(child, dict)]
        for child in children:
            tag = str(child.get("nodeName") or "").lower()
            if tag not in {"iframe", "frame"}:
                visit(child, parent_path)
                continue
            owner = child.get("backendNodeId")
            if not isinstance(owner, int):
                continue
            path = (*parent_path, _selector(child, children))
            attrs = _attributes(child)
            url = urljoin(page_url, attrs.get("src", ""))
            if urlsplit(url).scheme not in {"http", "https"}:
                url = ""
            hint = next(
                (attrs[key] for key in ("aria-label", "title", "name", "id") if attrs.get(key)),
                "unnamed",
            )
            content = child.get("contentDocument")
            route = FrameRoute(
                path=path,
                url=url,
                hint=hint,
                owner_backend_node_id=owner,
                document=content if isinstance(content, dict) else None,
            )
            routes.append(route)
            if route.document is not None:
                visit(route.document, path)
        for shadow in node.get("shadowRoots", []):
            if isinstance(shadow, dict):
                shadow_path = (*parent_path, f"shadow({_selector(node, children)})")
                visit(shadow, shadow_path)

    visit(root, ())
    return routes


def select_frame_route(routes: list[FrameRoute], scope: object) -> FrameRoute | None:
    if not isinstance(scope, dict):
        raise BrowserAgentError("PERCEPTION_FAILED", "Perception scope must be an object")
    if "frames" not in scope:
        if scope.get("include") != ["body"]:
            raise BrowserAgentError(
                "PERCEPTION_FAILED", "Perception is restricted to the document body"
            )
        return None
    frames = scope.get("frames")
    if not isinstance(frames, list) or len(frames) != 1 or not isinstance(frames[0], dict):
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    frame_scope = frames[0]
    if frame_scope.get("include") != ["body"]:
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    raw_path = frame_scope.get("frame", "self")
    if isinstance(raw_path, str):
        path = (raw_path,)
    elif isinstance(raw_path, list) and all(isinstance(item, str) for item in raw_path):
        path = tuple(raw_path)
    else:
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    if len(path) > 8:
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    if path in {(), ("self",)}:
        return None
    route = next((candidate for candidate in routes if candidate.path == path), None)
    if route is None:
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)
    return route


def nested_frame_refs(
    routes: list[FrameRoute], parent_path: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    depth = len(parent_path) + 1
    return [
        {"url": route.url, "hint": route.hint, "frame": route.label}
        for route in routes
        if route.url and len(route.path) == depth and route.path[: len(parent_path)] == parent_path
    ]


def projection_document(root: dict[str, Any], route: FrameRoute | None) -> dict[str, Any]:
    selected = root if route is None else route.document
    if selected is None:
        raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)

    def clone(node: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: value
            for key, value in node.items()
            if key not in {"children", "shadowRoots", "contentDocument"}
        }
        result["children"] = [
            clone(child) for child in node.get("children", []) if isinstance(child, dict)
        ]
        shadows = node.get("shadowRoots", [])
        if isinstance(shadows, list):
            result["shadowRoots"] = [
                clone(shadow) for shadow in shadows if isinstance(shadow, dict)
            ]
        return result

    return clone(selected)


def _frames(tree: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        frame = node.get("frame")
        if isinstance(frame, dict):
            result.append(frame)
        for child in node.get("childFrames", []):
            if isinstance(child, dict):
                visit(child)

    visit(tree.get("frameTree", {}))
    return result


async def frame_identity(client: Any, route: FrameRoute | None) -> str:
    if route is None:
        return "self"
    tree = await client.send("Page.getFrameTree")
    for frame in _frames(tree):
        frame_id = frame.get("id")
        loader_id = frame.get("loaderId")
        if not isinstance(frame_id, str) or not isinstance(loader_id, str):
            continue
        try:
            owner = await client.send("DOM.getFrameOwner", {"frameId": frame_id})
        except CDPSessionError:
            continue
        if owner.get("backendNodeId") == route.owner_backend_node_id:
            return f"{frame_id}:{loader_id}"
    raise BrowserAgentError("PERCEPTION_SUBTREE_OUT_OF_SCOPE", FRAME_SCOPE_ERROR)


async def assert_frame_identity_current(client: Any, expected: str) -> None:
    if expected == "self":
        return
    frame_id, separator, loader_id = expected.partition(":")
    if not separator or not frame_id or not loader_id:
        raise BrowserAgentError("PAGE_ACTION_STALE_REF", "The frame reference is invalid")
    tree = await client.send("Page.getFrameTree")
    current = next((frame for frame in _frames(tree) if frame.get("id") == frame_id), None)
    if current is None or current.get("loaderId") != loader_id:
        raise BrowserAgentError("PAGE_ACTION_STALE_REF", "The frame navigated or no longer exists")
