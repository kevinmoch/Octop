"""Exercise the PageAgent adapter against browser-shaped behavior, not a fake adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from octop_browser.dom.refs import RefCache
from octop_browser.models import ActionMetrics, ToolResult

from octop.infra.control.browser.webskill.page_agent import (
    execute_action,
    issue_action_grant,
    public_error_code,
    read_page,
)
from octop.infra.control.browser.webskill.sessions import BrowserAgentError, BrowserAgentRegistry


def tool_result(content: str, metadata: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(
        success=True,
        content=content,
        metrics=ActionMetrics(
            action="test", duration_ms=0, dom_nodes_scanned=0, estimated_tokens=0
        ),
        metadata=metadata,
    )


class FakeClient:
    def __init__(self, clicks: list[dict[str, Any]]) -> None:
        self.clicks = clicks
        self.loader_id = "loader-1"
        self.url = "https://example.test/form"
        self.backend_node_id = 100
        self.node_id = 20

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"loaderId": self.loader_id}}}
        if method == "Runtime.evaluate":
            if params and params.get("expression") == "location.href":
                return {"result": {"value": self.url}}
            return {"result": {"value": {"url": self.url, "title": "Example form"}}}
        if method == "DOM.getDocument":
            return {
                "root": {
                    "nodeId": 1,
                    "backendNodeId": 1,
                    "nodeName": "#document",
                    "children": [
                        {
                            "nodeId": self.node_id,
                            "backendNodeId": self.backend_node_id,
                            "nodeName": "BUTTON",
                            "children": [{"nodeName": "#text", "nodeValue": "Run once"}],
                        },
                        {
                            "nodeId": 21,
                            "backendNodeId": 201,
                            "nodeName": "INPUT",
                            "attributes": ["type", "password"],
                        },
                        {
                            "nodeId": 30,
                            "backendNodeId": 301,
                            "nodeName": "DIV",
                            "attributes": ["data-webskill-private", ""],
                            "children": [
                                {"nodeId": 22, "backendNodeId": 202, "nodeName": "BUTTON"}
                            ],
                        },
                    ],
                }
            }
        if method == "DOM.resolveNode":
            if params == {"backendNodeId": self.backend_node_id}:
                return {"object": {"objectId": "button-object"}}
            return {"object": {}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": {"role": "button", "name": "Run once", "secret": False}}}
        if method == "DOM.requestNode":
            if params == {"objectId": "button-object"}:
                return {"nodeId": self.node_id}
            return {"nodeId": 0}
        if method == "DOM.getBoxModel":
            if params and params.get("nodeId") == self.node_id:
                return {"model": {"content": [0, 0, 100, 0, 100, 100, 0, 100]}}
            return {"model": {}}
        if method == "Input.dispatchMouseEvent":
            if params and params.get("type") == "mouseReleased":
                self.clicks.append(params)
            return {}
        raise AssertionError(f"Unexpected CDP call: {method}")


class FakeBrowser:
    def __init__(self) -> None:
        self.clicks: list[dict[str, Any]] = []
        self.client = FakeClient(self.clicks)
        self.ref_cache = RefCache()
        self._internal = SimpleNamespace(client=self.client, ref_cache=self.ref_cache)
        self.active_target = "target-1"

    async def list_tabs(self) -> ToolResult:
        return tool_result("", metadata={"tabs": [{"tab_id": self.active_target, "active": True}]})

    async def dom_tree(self, *, level: str) -> ToolResult:
        assert level == "structured"
        return tool_result(
            json.dumps(
                {
                    "elements": [
                        {
                            "ref": "btn_0",
                            "tag": "button",
                            "role": "button",
                            "accessibleName": "Run once",
                            "node_id": self.client.node_id,
                        },
                        {
                            "ref": "inp_0",
                            "tag": "input",
                            "role": "textbox",
                            "type": "password",
                            "node_id": 21,
                        },
                        {
                            "ref": "btn_1",
                            "tag": "button",
                            "role": "button",
                            "text": "private child",
                            "node_id": 22,
                        },
                    ]
                }
            ),
        )

    async def click(self, *, ref: str) -> ToolResult:
        assert ref.startswith("ws_exec_")
        self.clicks.append(ref)
        return tool_result("clicked")


@pytest.fixture
def registry() -> BrowserAgentRegistry:
    return BrowserAgentRegistry()


def resolver_for(browser: FakeBrowser):
    async def resolver(_profile: str, *, create: bool = True) -> FakeBrowser:
        assert create
        return browser

    return resolver


async def test_perceive_confirm_grant_and_single_click(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FakeBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    browser.ref_cache.store("btn_1", 42)
    reply = await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    assert reply["type"] == "perceive-result"
    assert reply["result"]["page"] == {"url": "https://example.test/form", "title": "Example form"}
    assert "key" not in reply["result"]["page"]
    assert len(reply["targets"]) == 1
    ref = reply["targets"][0]["ref"]
    assert ref.startswith("ws_") and ref != "btn_0"
    assert session.refs[ref].backend_node_id == 100
    assert "private child" not in json.dumps(reply)

    request = {"ref": ref, "action": "click"}
    grant = await issue_action_grant(
        session,
        request,
        registry=registry,
        resolver=resolver,
        write_gate=lambda: True,
    )
    executed = await execute_action(
        session,
        request,
        grant,
        registry=registry,
        resolver=resolver,
        write_gate=lambda: True,
    )
    assert executed == {
        "type": "execute-result",
        "outcome": {"ok": True, "target": {"role": "button", "name": "Run once"}},
    }
    assert len(browser.clicks) == 1
    assert browser.ref_cache.all_refs() == {"btn_1": 42}

    # Replaying either the WS message or grant cannot produce a second click.
    with pytest.raises(BrowserAgentError) as error:
        await execute_action(
            session,
            request,
            grant,
            registry=registry,
            resolver=resolver,
            write_gate=lambda: True,
        )
    assert error.value.code == "GRANT_INVALID"
    assert len(browser.clicks) == 1


async def test_dom_insertion_keeps_original_backend_node(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FakeBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    perceived = await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    request = {"ref": perceived["targets"][0]["ref"], "action": "click"}
    grant = await issue_action_grant(
        session, request, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    browser.client.node_id = 42
    result = await execute_action(
        session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    assert result["outcome"]["ok"] is True
    assert len(browser.clicks) == 1


async def test_document_change_and_takeover_prevent_side_effects(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FakeBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    perceived = await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    request = {"ref": perceived["targets"][0]["ref"], "action": "click"}
    grant = await issue_action_grant(
        session, request, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    with pytest.raises(BrowserAgentError) as error:
        await execute_action(
            session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: False
        )
    assert error.value.code == "WRITE_BLOCKED"
    assert not browser.clicks

    grant = await issue_action_grant(
        session, request, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    browser.client.loader_id = "loader-2"
    with pytest.raises(BrowserAgentError) as error:
        await execute_action(
            session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: True
        )
    assert error.value.code == "PAGE_ACTION_STALE_REF"
    assert not browser.clicks


async def test_tab_switch_does_not_silently_retarget_model(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FakeBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    browser.active_target = "target-2"
    with pytest.raises(BrowserAgentError) as error:
        await read_page(
            session,
            {"type": "perceive", "scope": {"include": ["body"]}},
            resolver=resolver,
            registry=registry,
        )
    assert error.value.code == "PAGE_TAB_EXPIRED"


async def test_subtree_and_frame_inputs_fail_closed(registry: BrowserAgentRegistry) -> None:
    browser = FakeBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    with pytest.raises(BrowserAgentError) as error:
        await read_page(
            session,
            {"type": "perceive", "scope": {"include": ["body"]}, "ref": "ws_missing"},
            resolver=resolver,
            registry=registry,
        )
    assert error.value.code == "PERCEPTION_SUBTREE_UNSUPPORTED"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("UNKNOWN_IMPLEMENTATION_ERROR", "TOOL_EXECUTION_FAILED"),
        ("WRITE_BLOCKED", "PAGE_ACTION_OUT_OF_SCOPE"),
    ],
)
def test_error_code_whitelist(source: str, expected: str) -> None:
    assert public_error_code(source) == expected
