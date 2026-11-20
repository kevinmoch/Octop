"""Release gate for the Octop PageAgent semantic contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from octop.infra.control.browser.webskill.page_agent import _actual_target, _nest_nodes, _target
from octop.infra.control.browser.webskill.sessions import (
    SUPPORTED_ACTIONS,
    DocumentIdentity,
    PageRef,
)


def test_supported_action_surface_is_explicit() -> None:
    assert {
        "click",
        "fill",
        "select",
        "scroll",
        "back",
        "set",
        "hover",
        "drag",
        "submit",
        "attach",
    } <= SUPPORTED_ACTIONS


def test_trace_target_has_no_dom_location() -> None:
    target = _target(
        {"tag": "combobox", "role": "combobox", "accessibleName": "问题属性"},
        "ws_ref",
    )
    assert target == {"ref": "ws_ref", "role": "combobox", "name": "问题属性"}
    assert set(target) == {"ref", "role", "name"}


def test_trace_target_carries_the_frame_label() -> None:
    target = _target(
        {"tag": "combobox", "role": "combobox", "accessibleName": "问题属性"},
        "ws_ref",
        frame="iframe#orders",
    )
    assert target == {
        "ref": "ws_ref",
        "role": "combobox",
        "name": "问题属性",
        "frame": "iframe#orders",
    }


def test_tree_and_semantic_target_are_compatible() -> None:
    elements = [
        {"node_id": 1, "tag": "div"},
        {"node_id": 2, "tag": "button"},
    ]
    nodes = [{"role": "generic"}, {"role": "button", "name": "保存"}]
    tree = _nest_nodes(elements, nodes, {1: None, 2: 1})
    assert tree[0]["children"][0]["name"] == "保存"


def test_action_outcome_target_carries_the_frame_label() -> None:
    class Client:
        async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "object-1"}}
            if method == "Runtime.callFunctionOn":
                return {"result": {"value": {"role": "button", "name": "保存", "secret": False}}}
            raise AssertionError(method)

    entry = PageRef(backend_node_id=42, document_key="doc", frame="iframe#orders")
    document = DocumentIdentity(
        key="doc", target_id="target", loader_id="loader", url="https://x.test"
    )
    target = asyncio.run(
        _actual_target(SimpleNamespace(), entry, document, Client())  # type: ignore[arg-type]
    )
    assert target == {"role": "button", "name": "保存", "frame": "iframe#orders"}
