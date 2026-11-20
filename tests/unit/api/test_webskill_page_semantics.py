"""Behavioral parity checks for the P1 page semantics bridge."""

from __future__ import annotations

import asyncio
import json

import pytest

from octop.infra.control.browser.webskill.page_agent import (
    _action_visible,
    _click_dropdown_option,
    _live_value,
    _node_parents,
    _promoted_elements,
    _recover_ref,
    _scope_selectors,
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


def test_action_contract_includes_set_hover_drag_submit_but_not_attach() -> None:
    assert {"set", "hover", "drag", "submit"} <= SUPPORTED_ACTIONS
    assert "attach" not in SUPPORTED_ACTIONS
    action_digest({"ref": "ws_action", "action": "set", "value": "true"})
    action_digest({"ref": "ws_action", "action": "drag", "value": "ws_drop"})
    with pytest.raises(BrowserAgentError) as error:
        action_digest({"ref": "ws_action", "action": "attach"})
    assert error.value.code == "ACTION_UNSUPPORTED"
