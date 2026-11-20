"""Release gate for the Octop PageAgent semantic contract."""

from __future__ import annotations

from octop.infra.control.browser.webskill.page_agent import _nest_nodes, _target
from octop.infra.control.browser.webskill.sessions import SUPPORTED_ACTIONS


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


def test_tree_and_semantic_target_are_compatible() -> None:
    elements = [
        {"node_id": 1, "tag": "div"},
        {"node_id": 2, "tag": "button"},
    ]
    nodes = [{"role": "generic"}, {"role": "button", "name": "保存"}]
    tree = _nest_nodes(elements, nodes, {1: None, 2: 1})
    assert tree[0]["children"][0]["name"] == "保存"
