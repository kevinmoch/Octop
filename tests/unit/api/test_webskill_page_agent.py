"""Exercise the PageAgent adapter against browser-shaped behavior, not a fake adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from octop_browser.cdp.client import CDPSessionError
from octop_browser.dom.refs import RefCache
from octop_browser.models import ActionMetrics, ToolResult

from octop.infra.control.browser.webskill.frames import FrameRoute, discover_frame_routes
from octop.infra.control.browser.webskill.oopif import OopifSession, attach_oopif_route
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
        self.context_listeners: dict[str, list[Any]] = {}
        self.evaluation_context_ids: list[int] = []
        self.detached: list[str] = []
        self.page_target_available = True
        self.new_target_url: str | None = None
        self.open_target_on_release: str | None = None
        self.frame_url = "https://cross.example.test/frame"
        self.target_frame_url = self.frame_url

    def with_session(self, session_id: str):
        return ScopedFrameClient(self, session_id)

    def on(self, event: str, callback: Any) -> None:
        self.context_listeners.setdefault(event, []).append(callback)

    def off(self, event: str, callback: Any) -> None:
        listeners = self.context_listeners.get(event, [])
        if callback in listeners:
            listeners.remove(callback)

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if method in {"Page.enable", "DOM.enable", "Input.enable"}:
            return {}
        if method == "Runtime.enable":
            for callback in self.context_listeners.get("Runtime.executionContextCreated", []):
                callback(
                    {
                        "sessionId": session_id,
                        "context": {"id": 77, "auxData": {"isDefault": True}},
                    }
                )
            return {}
        if method == "Target.attachToTarget":
            return {"sessionId": "cross-session-1"}
        if method == "Target.getTargets":
            infos: list[dict[str, Any]] = []
            if self.page_target_available:
                infos.append({"targetId": "target-1", "type": "page", "url": self.url})
            if self.new_target_url is not None:
                infos.append({"targetId": "target-2", "type": "page", "url": self.new_target_url})
            infos.extend(
                [
                    {
                        "targetId": "cross-target-1",
                        "parentId": "target-1",
                        "type": "iframe",
                        "url": self.target_frame_url,
                    },
                    {
                        "targetId": "cross-target-2",
                        "parentId": "target-2",
                        "type": "iframe",
                        "url": self.frame_url,
                    },
                ]
            )
            return {"targetInfos": infos}
        if method == "Target.detachFromTarget":
            if params and isinstance(params.get("sessionId"), str):
                self.detached.append(params["sessionId"])
            return {}
        if session_id is not None and params and "contextId" in params:
            self.evaluation_context_ids.append(int(params["contextId"]))
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
        if method == "DOM.scrollIntoViewIfNeeded":
            return {}
        if method == "DOM.getBoxModel":
            if params and params.get("nodeId") == self.node_id:
                return {"model": {"content": [0, 0, 100, 0, 100, 100, 0, 100]}}
            return {"model": {}}
        if method == "Input.dispatchMouseEvent":
            if params and params.get("type") == "mouseReleased":
                self.clicks.append(params)
                if self.open_target_on_release is not None:
                    self.new_target_url = self.open_target_on_release
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


class FrameClient(FakeClient):
    def __init__(self, clicks: list[dict[str, Any]]) -> None:
        super().__init__(clicks)
        self.frame_loader_id = "frame-loader-1"

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if method == "Page.getFrameTree":
            return {
                "frameTree": {
                    "frame": {"id": "main-frame", "loaderId": self.loader_id},
                    "childFrames": [
                        {"frame": {"id": "frame-1", "loaderId": self.frame_loader_id}},
                        {
                            "frame": {
                                "id": "cross-target-1",
                                "loaderId": self.frame_loader_id,
                                "url": self.frame_url,
                            }
                        },
                    ],
                }
            }
        if method == "DOM.describeNode" and params == {"backendNodeId": 700}:
            return {"node": {"frameId": "cross-target-1"}}
        if method == "DOM.getFrameOwner":
            if params == {"frameId": "cross-target-1"}:
                return {"backendNodeId": 700}
            if params == {"frameId": "frame-1"}:
                return {"backendNodeId": 500}
            return {}
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
                            "nodeId": 50,
                            "backendNodeId": 500,
                            "nodeName": "IFRAME",
                            "attributes": ["id", "same-frame", "src", "/frame"],
                            "contentDocument": {
                                "nodeId": 51,
                                "backendNodeId": 501,
                                "nodeName": "#document",
                                "children": [
                                    {
                                        "nodeId": 60,
                                        "backendNodeId": 600,
                                        "nodeName": "BUTTON",
                                        "children": [
                                            {"nodeName": "#text", "nodeValue": "Frame action"}
                                        ],
                                    }
                                ],
                            },
                        },
                        {
                            "nodeId": 70,
                            "backendNodeId": 700,
                            "nodeName": "IFRAME",
                            "attributes": [
                                "id",
                                "cross-frame",
                                "src",
                                "https://cross.example.test/frame",
                            ],
                        },
                    ],
                }
            }
        if method == "DOM.resolveNode" and params == {"backendNodeId": 600}:
            return {"object": {"objectId": "frame-button-object"}}
        if (
            method == "Runtime.callFunctionOn"
            and params
            and params.get("objectId") == "frame-button-object"
        ):
            return {
                "result": {"value": {"role": "button", "name": "Frame action", "secret": False}}
            }
        if method == "DOM.requestNode" and params == {"objectId": "frame-button-object"}:
            return {"nodeId": 60}
        if method == "DOM.getBoxModel" and params and params.get("nodeId") == 60:
            return {"model": {"content": [0, 0, 100, 0, 100, 100, 0, 100]}}
        return await super().send(method, params, session_id=session_id)


class FrameBrowser(FakeBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.client = FrameClient(self.clicks)
        self._internal = SimpleNamespace(client=self.client, ref_cache=self.ref_cache)


class TimingFrameClient(FrameClient):
    def __init__(self, clicks: list[dict[str, Any]]) -> None:
        super().__init__(clicks)
        self.identity_calls = 0

    async def send(self, method, params=None, *, session_id=None):
        if method == "Page.getFrameTree":
            self.identity_calls += 1
            if self.identity_calls == 3:
                self.frame_loader_id = "frame-loader-2"
        return await super().send(method, params, session_id=session_id)


class ScopedFrameClient:
    def __init__(self, parent: FrameClient, session_id: str) -> None:
        self.parent = parent
        self.session_id = session_id

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if method == "Page.getFrameTree":
            return {
                "frameTree": {
                    "frame": {
                        "id": "cross-target-1",
                        "loaderId": self.parent.frame_loader_id,
                        "url": self.parent.frame_url,
                    }
                }
            }
        if method == "DOM.getDocument":
            return {
                "root": {
                    "nodeId": 51,
                    "backendNodeId": 501,
                    "nodeName": "#document",
                    "children": [
                        {
                            "nodeId": 60,
                            "backendNodeId": 600,
                            "nodeName": "BUTTON",
                            "children": [{"nodeName": "#text", "nodeValue": "Frame action"}],
                        }
                    ],
                }
            }
        if method == "Runtime.evaluate":
            if params and "contextId" in params:
                self.parent.evaluation_context_ids.append(int(params["contextId"]))
            if params and params.get("expression") == "document.readyState":
                return {"result": {"value": "complete"}}
            if params and params.get("expression") == "location.href":
                return {"result": {"value": self.parent.frame_url}}
            return {
                "result": {
                    "value": {
                        "url": self.parent.frame_url,
                        "title": "Cross frame",
                    }
                }
            }
        return await self.parent.send(method, params, session_id=self.session_id)

    async def enable_domain(self, domain: str, *, session_id: str | None = None) -> None:
        await self.parent.send(f"{domain}.enable", session_id=self.session_id)

    def on(self, event: str, callback: Any) -> None:
        self.parent.on(event, callback)

    def off(self, event: str, callback: Any) -> None:
        self.parent.off(event, callback)

    async def close(self) -> None:
        await self.parent.send("Target.detachFromTarget", {"sessionId": self.session_id})


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
    perceived = await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    assert perceived["type"] == "perceive-result"
    assert perceived["document"]["key"]
    assert session.model_target == "target-1"


async def test_new_target_updates_model_without_switching_view(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FrameBrowser()
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
    browser.client.open_target_on_release = "https://example.test/child"
    executed = await execute_action(
        session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    assert executed["outcome"]["ok"] is True
    assert session.model_target == "target-2"
    assert session.document is None
    assert not session.refs
    assert browser.active_target == "target-1"
    child = await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    assert child["type"] == "perceive-result"
    assert session.model_target == "target-2"


async def test_closed_model_target_fails_instead_of_following_view(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FrameBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    browser.client.page_target_available = False
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


async def test_same_origin_frame_read_grant_and_single_click(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FrameBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    main = await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    assert len(main["targets"]) == 1
    main_ref = main["targets"][0]["ref"]
    nested = main["result"]["nestedFrames"]
    assert nested == [
        {
            "url": "https://example.test/frame",
            "hint": "same-frame",
            "frame": "iframe#same-frame",
        },
        {
            "url": "https://cross.example.test/frame",
            "hint": "cross-frame",
            "frame": "iframe#cross-frame",
        },
    ]

    child = await read_page(
        session,
        {
            "type": "perceive",
            "scope": {"frames": [{"frame": ["iframe#same-frame"], "include": ["body"]}]},
        },
        resolver=resolver,
        registry=registry,
    )
    assert child["result"]["page"]["url"] == "https://example.test/frame"
    assert child["targets"][0]["name"] == "Frame action"
    frame_ref = child["targets"][0]["ref"]
    assert session.refs[frame_ref].frame == "frame-1:frame-loader-1"
    assert main_ref in session.refs

    request = {"ref": frame_ref, "action": "click"}
    grant = await issue_action_grant(
        session, request, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    executed = await execute_action(
        session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    assert executed["outcome"]["ok"] is True
    assert executed["outcome"]["target"]["name"] == "Frame action"
    assert len(browser.clicks) == 1


async def test_child_frame_navigation_invalidates_ref_and_grant(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FrameBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    perceived = await read_page(
        session,
        {
            "type": "perceive",
            "scope": {"frames": [{"frame": ["iframe#same-frame"], "include": ["body"]}]},
        },
        resolver=resolver,
        registry=registry,
    )
    request = {"ref": perceived["targets"][0]["ref"], "action": "click"}
    grant = await issue_action_grant(
        session, request, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    browser.client.frame_loader_id = "frame-loader-2"

    with pytest.raises(BrowserAgentError) as error:
        await execute_action(
            session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: True
        )
    assert error.value.code == "PAGE_ACTION_STALE_REF"
    with pytest.raises(BrowserAgentError) as grant_error:
        await issue_action_grant(
            session, request, registry=registry, resolver=resolver, write_gate=lambda: True
        )
    assert grant_error.value.code == "PAGE_ACTION_STALE_REF"
    assert not browser.clicks


async def test_unknown_frame_fails_with_scope_error(registry: BrowserAgentRegistry) -> None:
    browser = FrameBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    with pytest.raises(BrowserAgentError) as error:
        await read_page(
            session,
            {
                "type": "perceive",
                "scope": {"frames": [{"frame": ["iframe#missing"], "include": ["body"]}]},
            },
            resolver=resolver,
            registry=registry,
        )
    assert error.value.code == "PERCEPTION_SUBTREE_OUT_OF_SCOPE"
    assert str(error.value) == "This frame is outside the allowed perception scope."


async def test_oopif_read_grant_click_and_detach(
    registry: BrowserAgentRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octop.infra.control.browser.webskill import page_agent as page_agent_module

    browser = FrameBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    target_clients: list[object] = []
    original_target_diff = page_agent_module.newly_opened_page_target

    async def observe_target_diff(client: object, previous: set[str]) -> str | None:
        target_clients.append(client)
        return await original_target_diff(client, previous)

    monkeypatch.setattr(page_agent_module, "newly_opened_page_target", observe_target_diff)
    perceived = await read_page(
        session,
        {
            "type": "perceive",
            "scope": {"frames": [{"frame": ["iframe#cross-frame"], "include": ["body"]}]},
        },
        resolver=resolver,
        registry=registry,
    )
    assert perceived["type"] == "perceive-result"
    assert perceived["result"]["page"]["url"] == "https://cross.example.test/frame"
    frame_ref = perceived["targets"][0]["ref"]
    assert session.refs[frame_ref].frame == "oopif:cross-target-1:frame-loader-1"

    request = {"ref": frame_ref, "action": "click"}
    grant = await issue_action_grant(
        session, request, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    executed = await execute_action(
        session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    assert executed["outcome"]["ok"] is True
    assert executed["outcome"]["target"]["name"] == "Frame action"
    assert len(browser.clicks) == 1
    assert browser.client.detached == ["cross-session-1"] * 3
    assert browser.client.evaluation_context_ids
    assert set(browser.client.evaluation_context_ids) == {77}
    assert not browser.client.context_listeners.get("Runtime.executionContextCreated")
    assert target_clients == [browser.client]


async def test_oopif_navigation_invalidates_grant(registry: BrowserAgentRegistry) -> None:
    browser = FrameBrowser()
    session = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    perceived = await read_page(
        session,
        {
            "type": "perceive",
            "scope": {"frames": [{"frame": ["iframe#cross-frame"], "include": ["body"]}]},
        },
        resolver=resolver,
        registry=registry,
    )
    request = {"ref": perceived["targets"][0]["ref"], "action": "click"}
    grant = await issue_action_grant(
        session, request, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    browser.client.frame_loader_id = "frame-loader-2"

    with pytest.raises(BrowserAgentError) as error:
        await execute_action(
            session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: True
        )
    assert error.value.code == "PAGE_ACTION_STALE_REF"
    assert not browser.clicks


async def test_oopif_target_uses_owner_and_current_frame_url(
    registry: BrowserAgentRegistry,
) -> None:
    del registry
    browser = FrameBrowser()
    route = FrameRoute(
        path=("iframe#cross-frame",),
        url="https://cross.example.test/frame",
        hint="cross-frame",
        owner_backend_node_id=700,
        document=None,
    )
    attached = await attach_oopif_route(browser.client, route, "target-1")
    assert attached.target_id == "cross-target-1"
    await attached.close()

    browser.client.target_frame_url = "https://cross.example.test/frame?query=other"
    with pytest.raises(BrowserAgentError) as error:
        await attach_oopif_route(browser.client, route, "target-1")
    assert error.value.code == "PERCEPTION_SUBTREE_OUT_OF_SCOPE"

    browser.client.target_frame_url = browser.client.frame_url
    stale_src = FrameRoute(
        path=route.path,
        url="https://cross.example.test/frame-next",
        hint=route.hint,
        owner_backend_node_id=route.owner_backend_node_id,
        document=None,
    )
    attached = await attach_oopif_route(browser.client, stale_src, "target-1")
    assert attached.target_id == "cross-target-1"
    await attached.close()


async def test_oopif_close_removes_listener_and_suppresses_detach_error(
    registry: BrowserAgentRegistry,
) -> None:
    del registry
    browser = FrameBrowser()
    route = FrameRoute(
        path=("iframe#cross-frame",),
        url="https://cross.example.test/frame",
        hint="cross-frame",
        owner_backend_node_id=700,
        document=None,
    )
    attached: OopifSession = await attach_oopif_route(browser.client, route, "target-1")

    async def fail_close() -> None:
        raise CDPSessionError("The target is already closed")

    attached.client.close = fail_close
    await attached.close()
    assert attached.closed is True
    assert not browser.client.context_listeners.get("Runtime.executionContextCreated")


def test_shadow_frame_paths_include_the_host_and_stay_unique() -> None:
    root = {
        "nodeId": 1,
        "backendNodeId": 1,
        "nodeName": "#document",
        "children": [
            {
                "nodeId": host_id,
                "backendNodeId": host_id,
                "nodeName": "DIV",
                "attributes": ["id", f"host-{host_id}"],
                "shadowRoots": [
                    {
                        "nodeId": host_id + 100,
                        "backendNodeId": host_id + 100,
                        "nodeName": "#document",
                        "children": [
                            {
                                "nodeId": host_id + 200,
                                "backendNodeId": host_id + 200,
                                "nodeName": "IFRAME",
                                "attributes": ["src", "/frame"],
                            }
                        ],
                    }
                ],
            }
            for host_id in (10, 20)
        ],
    }
    routes = discover_frame_routes(root, "https://example.test/")
    assert [route.label for route in routes] == [
        "shadow(div#host-10) >>> iframe:nth-of-type(1)",
        "shadow(div#host-20) >>> iframe:nth-of-type(1)",
    ]


async def test_frame_snapshot_and_identity_must_match(
    registry: BrowserAgentRegistry,
) -> None:
    browser = FrameBrowser()
    browser.client = TimingFrameClient(browser.clicks)
    browser._internal = SimpleNamespace(client=browser.client, ref_cache=browser.ref_cache)
    session = registry.create(1, "user-1")
    with pytest.raises(BrowserAgentError) as error:
        await read_page(
            session,
            {
                "type": "perceive",
                "scope": {"frames": [{"frame": ["iframe#same-frame"], "include": ["body"]}]},
            },
            resolver=resolver_for(browser),
            registry=registry,
        )
    assert error.value.code == "PERCEPTION_FAILED"
    assert "snapshot" in str(error.value)


async def test_back_uses_model_target_instead_of_base_history(
    registry: BrowserAgentRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from octop.infra.control.browser.webskill import page_agent as page_agent_module

    browser = FrameBrowser()
    browser.client.new_target_url = "https://example.test/child"
    browser.go_back = AsyncMock()
    scoped_back = AsyncMock(return_value=tool_result("back"))
    monkeypatch.setattr(page_agent_module.navigate, "go_back", scoped_back)
    session = registry.create(1, "user-1")
    session.base_target_id = "target-1"
    session.model_target = "target-2"
    resolver = resolver_for(browser)
    await read_page(
        session,
        {"type": "perceive", "scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    request = {"ref": "@document", "action": "back"}
    grant = await issue_action_grant(
        session, request, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    result = await execute_action(
        session, request, grant, registry=registry, resolver=resolver, write_gate=lambda: True
    )
    assert result["outcome"]["ok"] is True
    scoped_back.assert_awaited_once()
    browser.go_back.assert_not_awaited()


async def test_attach_model_target_preserves_lease_timeout(
    registry: BrowserAgentRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octop.infra.control.browser.webskill import targets as target_module

    async def locked(*args, **kwargs):
        raise BrowserAgentError("WRITE_LOCKED", "Another writer holds the browser lease")

    monkeypatch.setattr(target_module, "attach_page_target", locked)
    session = registry.create(1, "user-1")
    session.base_target_id = "target-1"
    session.model_target = "target-2"
    with pytest.raises(BrowserAgentError) as error:
        await target_module.attach_model_target(session, FakeBrowser())
    assert error.value.code == "WRITE_LOCKED"


async def test_oopif_cleanup_does_not_mask_primary_failures() -> None:
    class FailingClient:
        def off(self, _event: str, _listener: object) -> None:
            raise RuntimeError("listener cleanup failed")

        async def close(self) -> None:
            raise RuntimeError("detach failed")

    attached = OopifSession(
        client=FailingClient(),
        target_id="target",
        session_id="session",
        frame_key="frame",
        execution_context_id=1,
        url="https://example.test",
        title="Example",
        context_listener=object(),
    )
    await attached.close()
    assert attached.closed is True


def test_target_workset_limit_does_not_move_the_model_pointer(
    registry: BrowserAgentRegistry,
) -> None:
    from octop.infra.control.browser.webskill.sessions import MAX_MODEL_TARGETS
    from octop.infra.control.browser.webskill.targets import admit_model_target

    session = registry.create(1, "user-1")
    session.model_target = "target-0"
    session.base_target_id = "target-0"
    session.model_targets = {f"target-{index}" for index in range(MAX_MODEL_TARGETS)}

    assert admit_model_target(session, "target-overflow") is False
    assert session.model_target == "target-0"
    assert "target-overflow" not in session.model_targets
