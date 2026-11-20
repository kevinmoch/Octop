"""Execution-level tests for the P1 page action set and its outcome shape."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from octop.infra.control.browser.webskill import page_agent
from octop.infra.control.browser.webskill.page_agent import _keyboard_replace, execute_action
from octop.infra.control.browser.webskill.sessions import (
    BrowserAgentError,
    BrowserAgentRegistry,
    BrowserAgentSession,
    DocumentIdentity,
)


class ActionClient:
    def __init__(self) -> None:
        self.role = "textbox"
        self.name = "字段"
        self.checked = False
        self.input_events: list[dict[str, Any]] = []
        self.functions: list[str] = []
        self.url = "https://example.test/form"
        self.node_names = {10: "INPUT", 11: "DIV"}
        self.native_drag = False
        self.fill_reason: str | None = None
        self.status_text: str | None = None
        self.change_counts = {"added": 2, "removed": 1}

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if method in {"Page.enable", "DOM.enable", "Input.enable"}:
            return {}
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "target-1", "type": "page", "url": self.url}]}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"loaderId": "loader-1"}}}
        if method == "Runtime.evaluate":
            expression = str(params.get("expression") or "")
            if expression == "location.href":
                return {"result": {"value": self.url}}
            if 'role="status"' in expression and self.status_text is not None:
                return {"result": {"value": self.status_text}}
            if "__octopPageChanges" in expression:
                if "MutationObserver" in expression:
                    return {"result": {"value": True}}
                return {"result": {"value": self.change_counts}}
            return {"result": {"value": {"url": self.url, "title": "Form"}}}
        if method == "DOM.getDocument":
            return {
                "root": {
                    "nodeId": 1,
                    "nodeName": "#document",
                    "children": [
                        {
                            "nodeId": 20,
                            "backendNodeId": 10,
                            "nodeName": "INPUT",
                            "attributes": ["title", "字段"],
                        },
                        {
                            "nodeId": 21,
                            "backendNodeId": 11,
                            "nodeName": "DIV",
                            "attributes": ["title", "投放区"],
                        },
                    ],
                }
            }
        if method == "DOM.resolveNode":
            backend = int(params.get("backendNodeId") or 0)
            return {"object": {"objectId": f"object-{backend}"}}
        if method == "DOM.requestNode":
            object_id = str(params.get("objectId") or "")
            return {"nodeId": 20 if object_id == "object-10" else 21}
        if method == "DOM.describeNode":
            backend = int(params.get("backendNodeId") or 0)
            return {"node": {"nodeName": self.node_names[backend]}}
        if method == "DOM.getBoxModel":
            return {"model": {"border": [10, 20, 110, 20, 110, 80, 10, 80]}}
        if method == "DOM.scrollIntoViewIfNeeded":
            return {}
        if method == "Input.dispatchMouseEvent":
            self.input_events.append(params)
            return {}
        if method == "Runtime.callFunctionOn":
            declaration = str(params.get("functionDeclaration") or "")
            self.functions.append(declaration)
            if "new DataTransfer()" in declaration and "this.files" in declaration:
                return {"result": {"value": {"ok": True, "count": 1}}}
            if "HTMLInputElement.prototype" in declaration:
                if self.fill_reason:
                    return {"result": {"value": {"ok": False, "reason": self.fill_reason}}}
                return {"result": {"value": {"ok": True, "value": "文本"}}}
            if "const tag = this.tagName" in declaration:
                return {
                    "result": {"value": {"role": self.role, "name": self.name, "secret": False}}
                }
            if "toggle" in declaration:
                return {"result": {"value": {"toggle": True, "current": self.checked}}}
            if "dragstart" in declaration:
                return {"result": {"value": self.native_drag}}
            if "requestSubmit" in declaration:
                return {"result": {"value": True}}
            if "pointerover" in declaration:
                return {"result": {"value": True}}
            if "scrollHeight" in declaration:
                return {"result": {"value": {"ok": True, "movedBy": 120, "atEnd": True}}}
            if "const wanted" in declaration:
                return {"result": {"value": {"found": True, "settled": True}}}
            return {"result": {"value": True}}
        raise AssertionError(f"Unexpected CDP call: {method}")


class ActionBrowser:
    def __init__(self, client: ActionClient) -> None:
        self._internal = type("Internal", (), {"client": client})()
        self.clicks: list[Any] = []

    async def switch_tab(self, tab_id: str) -> Any:
        return None

    async def go_back(self) -> Any:
        return type("Result", (), {"success": True, "error": None})()


def _resolver(browser: ActionBrowser):
    async def resolve(profile: str, create: bool = False) -> ActionBrowser:
        return browser

    return resolve


def _setup(
    client: ActionClient, action: str, *, value: str | None = None
) -> tuple[BrowserAgentSession, str, dict[str, Any], BrowserAgentRegistry]:
    registry = BrowserAgentRegistry()
    session = registry.create(1, "user-1")
    document = DocumentIdentity(
        key=json.dumps(
            [1, "user-1", "target-1", "loader-1", client.url],
            separators=(",", ":"),
        ),
        target_id="target-1",
        loader_id="loader-1",
        url=client.url,
    )
    session.document = document
    session.model_target = "target-1"
    session.base_target_id = "target-1"
    session.model_targets = {"target-1"}
    source = registry.issue_ref(session, 10, "self", '{"role":"button","name":"字段","path":[0]}')
    registry.issue_ref(session, 11, "self", '{"role":"button","name":"投放区","path":[1]}')
    request: dict[str, Any] = {"ref": source, "action": action}
    if value is not None:
        request["value"] = value
    grant = registry.issue_grant(session, document=document, request=request)
    return session, grant, request, registry


def _run(client: ActionClient, action: str, *, value: str | None = None) -> dict[str, Any]:
    session, grant, request, registry = _setup(client, action, value=value)
    browser = ActionBrowser(client)
    original = page_agent.wait_for_new_page_target

    async def no_new_target(*args: Any, **kwargs: Any) -> None:
        return None

    page_agent.wait_for_new_page_target = no_new_target
    try:
        return asyncio.run(
            execute_action(
                session,
                request,
                grant,
                registry=registry,
                resolver=_resolver(browser),
                write_gate=lambda: True,
            )
        )
    finally:
        page_agent.wait_for_new_page_target = original


def test_keyboard_replacement_selects_then_reads_back() -> None:
    class KeyboardClient:
        def __init__(self) -> None:
            self.inserted: list[str] = []

        async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            params = params or {}
            if method == "Runtime.callFunctionOn":
                declaration = str(params.get("functionDeclaration") or "")
                if "this.select" in declaration:
                    return {"result": {"value": True}}
                if "this.value" in declaration:
                    return {"result": {"value": "新值"}}
                return {"result": {"value": "新值"}}
            if method == "Input.dispatchKeyEvent":
                return {}
            if method == "Input.insertText":
                self.inserted.append(str(params.get("text")))
                return {}
            raise AssertionError(method)

    client = KeyboardClient()
    assert asyncio.run(_keyboard_replace(client, "control", "新值")) is True
    assert client.inserted == ["新值"]


def test_fill_uses_native_setter_and_reports_readonly() -> None:
    client = ActionClient()
    client.name = "输入"
    result = _run(client, "fill", value="文本")
    assert result["outcome"]["ok"] is True
    assert any("HTMLInputElement.prototype" in function for function in client.functions)

    readonly = ActionClient()
    readonly.name = "只读"
    readonly.fill_reason = "read-only"
    denied = _run(readonly, "fill", value="文本")
    assert denied["outcome"]["ok"] is False
    assert "read-only" in denied["outcome"]["reason"]


def test_attach_consumes_host_selected_files() -> None:
    client = ActionClient()
    client.name = "附件"
    session, grant, request, registry = _setup(client, "attach")
    session.attachments = [{"name": "a.txt", "type": "text/plain", "data": b"hello"}]
    browser = ActionBrowser(client)
    original = page_agent.wait_for_new_page_target

    async def no_new_target(*args: Any, **kwargs: Any) -> None:
        return None

    page_agent.wait_for_new_page_target = no_new_target
    try:
        result = asyncio.run(
            execute_action(
                session,
                request,
                grant,
                registry=registry,
                resolver=_resolver(browser),
                write_gate=lambda: True,
            )
        )
    finally:
        page_agent.wait_for_new_page_target = original
    assert result["outcome"]["ok"] is True
    assert session.attachments == []


def test_click_reports_dom_changes() -> None:
    client = ActionClient()
    client.role = "button"
    client.name = "提交"
    result = _run(client, "click")
    assert result["outcome"]["ok"] is True
    assert result["outcome"]["changes"] == {"added": 2, "removed": 1}


def test_click_without_page_reaction_is_noop() -> None:
    client = ActionClient()
    client.role = "button"
    client.name = "无反应按钮"
    client.change_counts = {"added": 0, "removed": 0}
    result = _run(client, "click")
    assert result["outcome"]["ok"] is True
    assert result["outcome"]["noop"] is True
    assert "changes" not in result["outcome"]


def test_click_reports_status_text_and_redacts_the_action_value() -> None:
    client = ActionClient()
    client.role = "button"
    client.name = "提交"
    client.status_text = "Saved VERIFY-STATUS"
    result = _run(client, "click", value="VERIFY-STATUS")
    assert result["outcome"]["changes"]["status"] == "Saved [redacted]"


def test_click_uses_real_pointer_sequence() -> None:
    client = ActionClient()
    client.role = "button"
    client.name = "提交"
    result = _run(client, "click")
    assert result["outcome"]["ok"] is True
    assert any(event.get("type") == "mouseReleased" for event in client.input_events)


def test_custom_combobox_select_uses_readback() -> None:
    client = ActionClient()
    client.role = "combobox"
    client.name = "问题属性"
    client.node_names[10] = "DIV"
    result = _run(client, "select", value="规格不符")
    assert result["outcome"]["ok"] is True
    assert any("const wanted" in function for function in client.functions)


def test_set_toggles_and_returns_noop_when_already_set() -> None:
    client = ActionClient()
    client.role = "checkbox"
    client.name = "启用"
    client.node_names[10] = "INPUT"
    result = _run(client, "set", value="true")
    assert result["outcome"]["ok"] is True
    assert any(event.get("type") == "mouseReleased" for event in client.input_events)

    client.checked = True
    client.input_events.clear()
    existing = _run(client, "set", value="true")
    assert existing["outcome"]["ok"] is True
    assert existing["outcome"]["noop"] is True
    assert client.input_events == []


def test_hover_dispatches_hover_function_without_click() -> None:
    client = ActionClient()
    client.name = "菜单"
    result = _run(client, "hover")
    assert result["outcome"]["ok"] is True
    assert result["outcome"]["target"]["role"] == "button"
    assert any(event.get("type") == "mouseMoved" for event in client.input_events)


def test_submit_invokes_form_request_submit() -> None:
    client = ActionClient()
    client.name = "保存"
    result = _run(client, "submit")
    assert result["outcome"]["ok"] is True
    assert any("requestSubmit" in function for function in client.functions)


def test_scroll_reports_moved_and_at_end() -> None:
    client = ActionClient()
    client.name = "列表"
    result = _run(client, "scroll", value="down")
    assert result["outcome"]["ok"] is True
    assert result["outcome"]["scrolled"] == {"atEnd": True, "movedBy": 120}


def test_drag_requires_a_known_destination_and_sends_pointer_sequence() -> None:
    client = ActionClient()
    client.name = "卡片"
    client.node_names[10] = "INPUT"
    with pytest.raises(BrowserAgentError):
        _run(client, "drag", value="ws_missing")

    session, _, request, registry = _setup(client, "drag", value="ws_placeholder")
    destination = next(ref for ref, entry in session.refs.items() if entry.backend_node_id == 11)
    request["value"] = destination
    browser = ActionBrowser(client)
    original = page_agent.wait_for_new_page_target

    async def no_new_target(*args: Any, **kwargs: Any) -> None:
        return None

    page_agent.wait_for_new_page_target = no_new_target
    try:
        result = asyncio.run(
            execute_action(
                session,
                request,
                registry.issue_grant(session, document=session.document, request=request),
                registry=registry,
                resolver=_resolver(browser),
                write_gate=lambda: True,
            )
        )
    finally:
        page_agent.wait_for_new_page_target = original
    assert result["outcome"]["ok"] is True
    assert sum(1 for event in client.input_events if event.get("type") == "mouseMoved") >= 2


def test_drag_prefers_html5_data_transfer_when_available() -> None:
    client = ActionClient()
    client.name = "卡片"
    client.native_drag = True
    session, _, request, registry = _setup(client, "drag", value="ws_placeholder")
    destination = next(ref for ref, entry in session.refs.items() if entry.backend_node_id == 11)
    request["value"] = destination
    browser = ActionBrowser(client)
    original = page_agent.wait_for_new_page_target

    async def no_new_target(*args: Any, **kwargs: Any) -> None:
        return None

    page_agent.wait_for_new_page_target = no_new_target
    try:
        result = asyncio.run(
            execute_action(
                session,
                request,
                registry.issue_grant(session, document=session.document, request=request),
                registry=registry,
                resolver=_resolver(browser),
                write_gate=lambda: True,
            )
        )
    finally:
        page_agent.wait_for_new_page_target = original
    assert result["outcome"]["ok"] is True
    assert any("dragstart" in function for function in client.functions)
    assert client.input_events == []
