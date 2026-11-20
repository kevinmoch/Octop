"""Behavioral parity checks for the P1 page semantics bridge."""

from __future__ import annotations

import asyncio
import json

import pytest

from octop.infra.control.browser.webskill.page_agent import (
    _action_visible,
    _assert_ref_in_action_scope,
    _click_dropdown_option,
    _file_inputs,
    _hidden_by_ancestor,
    _live_value,
    _nest_nodes,
    _node_parents,
    _promoted_elements,
    _recover_ref,
    _scope_selectors,
    _target,
    _within_scope,
)
from octop.infra.control.browser.webskill.sessions import (
    SUPPORTED_ACTIONS,
    BrowserAgentError,
    BrowserAgentRegistry,
    BrowserAgentSession,
    DocumentIdentity,
    PageRef,
    action_digest,
)


class _DocumentClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.calls.append(method)
        if method == "DOM.getDocument":
            return {
                "root": {
                    "nodeId": 1,
                    "nodeName": "#document",
                    "children": [
                        {
                            "nodeId": 2,
                            "backendNodeId": 20,
                            "nodeName": "BUTTON",
                            "attributes": ["title", "保存"],
                        }
                    ],
                }
            }
        raise AssertionError(method)


def _session() -> BrowserAgentSession:
    session = BrowserAgentSession(id="semantics", user_id=1, profile="user-1")
    session.document = DocumentIdentity(
        key="document-key",
        target_id="target-1",
        loader_id="loader-1",
        url="https://example.test/",
    )
    return session


def test_refs_are_reused_only_for_the_same_signature_frame_and_document() -> None:
    session = _session()
    registry = BrowserAgentRegistry()
    first = registry.issue_ref(session, 10, "self", '{"path":[0],"role":"button"}')
    same = registry.issue_ref(session, 11, "self", '{"path":[0],"role":"button"}')
    assert same == first
    assert registry.issue_ref(session, 12, "self", '{"path":[1],"role":"button"}') != first
    assert registry.issue_ref(session, 13, "frame-1", '{"path":[0],"role":"button"}') != first


def test_signature_recovery_uses_dom_path_and_tag() -> None:
    entry = PageRef(
        backend_node_id=999,
        document_key="document-key",
        frame="self",
        signature=json.dumps({"path": [0], "tag": "button", "role": "button"}),
    )
    client = _DocumentClient()
    recovered = asyncio.run(_recover_ref(client, entry))
    assert recovered is not None
    assert recovered.backend_node_id == 20
    assert "DOM.getDocument" in client.calls


def test_execution_revalidates_the_ref_against_the_action_scope() -> None:
    class ScopeClient:
        async def send(self, method: str, params: dict | None = None) -> dict:
            if method == "DOM.getDocument":
                return {
                    "root": {
                        "nodeId": 1,
                        "nodeName": "#document",
                        "children": [
                            {
                                "nodeId": 2,
                                "nodeName": "DIV",
                                "attributes": ["id", "form"],
                                "children": [
                                    {"nodeId": 3, "backendNodeId": 20, "nodeName": "BUTTON"}
                                ],
                            },
                            {"nodeId": 4, "backendNodeId": 21, "nodeName": "BUTTON"},
                        ],
                    }
                }
            if method == "DOM.querySelectorAll":
                return {"nodeIds": [2]}
            raise AssertionError(method)

    inside = PageRef(
        backend_node_id=20,
        document_key="document-key",
        action_scope=json.dumps({"include": ["#form"], "exclude": []}),
    )
    asyncio.run(_assert_ref_in_action_scope(ScopeClient(), inside))
    outside = PageRef(
        backend_node_id=21,
        document_key="document-key",
        action_scope=json.dumps({"include": ["#form"], "exclude": []}),
    )
    with pytest.raises(BrowserAgentError) as error:
        asyncio.run(_assert_ref_in_action_scope(ScopeClient(), outside))
    assert error.value.code == "PAGE_ACTION_OUT_OF_SCOPE"


def test_action_scope_is_a_subtree_with_exclusions() -> None:
    root = {
        "nodeName": "BODY",
        "nodeId": 1,
        "children": [
            {
                "nodeName": "DIV",
                "nodeId": 2,
                "children": [{"nodeName": "BUTTON", "nodeId": 3}],
            },
            {"nodeName": "BUTTON", "nodeId": 4},
        ],
    }
    parents = _node_parents(root)
    assert _within_scope(3, parents, {1}, set()) is True
    assert _within_scope(4, parents, {2}, set()) is False
    assert _within_scope(3, parents, {1}, {2}) is False


def test_hidden_ancestor_removes_action_eligibility() -> None:
    root = {
        "nodeName": "BODY",
        "nodeId": 1,
        "children": [
            {
                "nodeName": "DIV",
                "nodeId": 2,
                "attributes": ["aria-hidden", "true"],
                "children": [{"nodeName": "BUTTON", "nodeId": 3}],
            },
            {"nodeName": "BUTTON", "nodeId": 4},
        ],
    }
    parents = _node_parents(root)
    assert _hidden_by_ancestor(3, root, parents) is True
    assert _hidden_by_ancestor(4, root, parents) is False


def test_scope_frames_use_their_own_include_and_exclude() -> None:
    scope = {"frames": [{"frame": ["iframe#main"], "include": ["#form"], "exclude": [".danger"]}]}
    assert _scope_selectors(scope) == (["#form"], [".danger"])
    assert _scope_selectors({"include": ["body"]}) == (["body"], [])


def test_action_visibility_rejects_no_layout_and_zero_size() -> None:
    from octop_browser.cdp.client import CDPSessionError

    class BoxClient:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        async def send(self, method: str, params: dict | None = None) -> dict:
            if method != "DOM.getBoxModel":
                raise AssertionError(method)
            if self.mode == "error":
                raise CDPSessionError("not laid out")
            if self.mode == "zero":
                return {"model": {"border": [0, 0, 0, 0, 0, 0, 0, 0]}}
            return {"model": {"border": [10, 10, 30, 10, 30, 30, 10, 30]}}

    assert asyncio.run(_action_visible(BoxClient("visible"), 1)) is True
    assert asyncio.run(_action_visible(BoxClient("zero"), 1)) is False
    assert asyncio.run(_action_visible(BoxClient("error"), 1)) is False


def test_signature_recovery_can_follow_a_changed_path_when_unique() -> None:
    entry = PageRef(
        backend_node_id=999,
        document_key="document-key",
        frame="self",
        signature=json.dumps(
            {
                "path": [9, 9],
                "tag": "button",
                "role": "button",
                "name": "保存",
            }
        ),
    )
    recovered = asyncio.run(_recover_ref(_DocumentClient(), entry))
    assert recovered is not None
    assert recovered.backend_node_id == 20


def test_live_value_reads_text_control_state() -> None:
    class ValueClient:
        async def send(self, method: str, params: dict | None = None) -> dict:
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "control"}}
            if method == "Runtime.callFunctionOn":
                return {"result": {"value": "测试"}}
            raise AssertionError(method)

    assert asyncio.run(_live_value(ValueClient(), 10)) == "测试"


def test_custom_dropdown_requires_found_and_settled_readback() -> None:
    class SelectClient:
        def __init__(self, settled: bool) -> None:
            self.settled = settled
            self.calls: list[tuple[str, dict]] = []

        async def send(self, method: str, params: dict | None = None) -> dict:
            self.calls.append((method, params or {}))
            return {"result": {"value": {"found": True, "settled": self.settled}}}

    ok = SelectClient(True)
    assert asyncio.run(_click_dropdown_option(ok, "规格不符", "control")) is True
    assert ok.calls[0][0] == "Runtime.callFunctionOn"
    assert ok.calls[0][1]["arguments"] == [{"value": "规格不符"}]

    not_settled = SelectClient(False)
    assert asyncio.run(_click_dropdown_option(not_settled, "规格不符", "control")) is False


def test_generic_promotion_recognises_class_and_href_signals() -> None:
    root = {
        "nodeName": "BODY",
        "nodeId": 1,
        "children": [
            {
                "nodeName": "DIV",
                "nodeId": 2,
                "attributes": ["class", "menu-trigger"],
                "children": [{"nodeName": "#text", "nodeValue": "更多操作"}],
            },
            {
                "nodeName": "DIV",
                "nodeId": 3,
                "attributes": ["href", "/details"],
                "children": [{"nodeName": "#text", "nodeValue": "查看详情"}],
            },
        ],
    }
    promoted = _promoted_elements(root)
    assert {"node_id": 2, "role": "button", "name": "更多操作"} in promoted
    assert {"node_id": 3, "role": "link", "name": "查看详情"} in promoted


def test_perceived_nodes_keep_their_children_when_rebuilt_as_a_tree() -> None:
    elements = [
        {"node_id": 1, "tag": "div"},
        {"node_id": 2, "tag": "button"},
        {"node_id": 3, "tag": "input"},
    ]
    nodes = [{"role": "generic"}, {"role": "button"}, {"role": "textbox"}]
    parents = {1: None, 2: 1, 3: 1}
    roots = _nest_nodes(elements, nodes, parents)
    assert roots == [
        {
            "role": "generic",
            "children": [{"role": "button"}, {"role": "textbox"}],
        }
    ]


def test_invisible_file_inputs_are_exposed_for_attach() -> None:
    root = {
        "nodeName": "BODY",
        "nodeId": 1,
        "children": [
            {
                "nodeName": "INPUT",
                "nodeId": 2,
                "attributes": ["type", "file", "title", "上传附件"],
            },
            {
                "nodeName": "INPUT",
                "nodeId": 3,
                "attributes": ["type", "text", "title", "普通字段"],
            },
        ],
    }
    assert _file_inputs(root, set()) == [
        {
            "tag": "input",
            "type": "file",
            "role": "textbox",
            "node_id": 2,
            "accessibleName": "上传附件",
        }
    ]


def test_ambiguous_signature_recovery_is_rejected() -> None:
    class DuplicateClient:
        async def send(self, method: str, params: dict | None = None) -> dict:
            if method == "DOM.getDocument":
                return {
                    "root": {
                        "nodeId": 1,
                        "nodeName": "#document",
                        "children": [
                            {
                                "nodeId": 2,
                                "backendNodeId": 20,
                                "nodeName": "BUTTON",
                                "attributes": ["title", "保存"],
                            },
                            {
                                "nodeId": 3,
                                "backendNodeId": 21,
                                "nodeName": "BUTTON",
                                "attributes": ["title", "保存"],
                            },
                        ],
                    }
                }
            raise AssertionError(method)

    entry = PageRef(
        backend_node_id=999,
        document_key="document-key",
        frame="self",
        signature=json.dumps({"path": [9], "tag": "button", "role": "button", "name": "保存"}),
    )
    assert asyncio.run(_recover_ref(DuplicateClient(), entry)) is None


def test_action_target_has_semantic_identity_only() -> None:
    target = _target(
        {"tag": "button", "role": "button", "accessibleName": "保存"},
        "ws_ref",
    )
    assert target == {"ref": "ws_ref", "role": "button", "name": "保存"}
    assert "selector" not in target
    assert "x" not in target and "y" not in target


def test_cursor_pointer_is_supplemental_promotion_evidence() -> None:
    root = {
        "nodeName": "BODY",
        "nodeId": 1,
        "children": [
            {
                "nodeName": "DIV",
                "nodeId": 2,
                "backendNodeId": 20,
                "children": [{"nodeName": "#text", "nodeValue": "更多"}],
            }
        ],
    }
    assert _promoted_elements(root) == []
    promoted = _promoted_elements(root, cursor_backends={20})
    assert {"node_id": 2, "role": "button", "name": "更多"} in promoted


def test_dom_click_listener_is_supplemental_promotion_evidence() -> None:
    root = {
        "nodeName": "BODY",
        "nodeId": 1,
        "children": [
            {
                "nodeName": "DIV",
                "nodeId": 2,
                "backendNodeId": 20,
                "children": [{"nodeName": "#text", "nodeValue": "打开"}],
            }
        ],
    }
    promoted = _promoted_elements(root, click_backends={20})
    assert {"node_id": 2, "role": "button", "name": "打开"} in promoted


def test_parent_with_actionable_descendant_is_not_promoted_again() -> None:
    root = {
        "nodeName": "BODY",
        "nodeId": 1,
        "children": [
            {
                "nodeName": "DIV",
                "nodeId": 2,
                "attributes": ["title", "拥有按钮的卡片"],
                "children": [{"nodeName": "BUTTON", "nodeId": 3}],
            }
        ],
    }
    assert _promoted_elements(root) == []


def test_action_contract_includes_set_hover_drag_submit_but_not_attach() -> None:
    assert {"set", "hover", "drag", "submit", "attach"} <= SUPPORTED_ACTIONS
    action_digest({"ref": "ws_action", "action": "set", "value": "true"})
    action_digest({"ref": "ws_action", "action": "drag", "value": "ws_drop"})
    action_digest({"ref": "ws_action", "action": "attach"})
    with pytest.raises(BrowserAgentError):
        action_digest({"ref": "ws_action", "action": "attach", "value": "path"})
