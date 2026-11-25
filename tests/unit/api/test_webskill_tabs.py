"""Model-side tab workset behavior: opaque handles, logical focus, close/admit."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.control.browser.webskill import targets as targets_module
from octop.infra.control.browser.webskill.sessions import BrowserAgentError, BrowserAgentSession
from octop.infra.control.browser.webskill.tabs import (
    admit_workset_tab,
    close_workset_tab,
    focus_workset_tab,
    list_workset_tabs,
    reset_workset,
)


class FakeCDPClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send(self, method: str, _params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(method)
        return {}


class FakeTabBrowser:
    def __init__(self) -> None:
        self.client = FakeCDPClient()
        self._internal = SimpleNamespace(client=self.client)
        self.tabs = [
            {"tab_id": "target-1", "title": "Origin", "url": "https://a.test/", "active": True},
            {"tab_id": "target-2", "title": "Detail", "url": "https://b.test/", "active": False},
        ]
        self.closed: list[str] = []
        self.switch_calls: list[str] = []

    async def list_tabs(self) -> Any:
        return SimpleNamespace(success=True, metadata={"tabs": self.tabs}, error=None)

    async def close_tab(self, tab_id: str) -> Any:
        self.closed.append(tab_id)
        self.tabs = [tab for tab in self.tabs if tab["tab_id"] != tab_id]
        return SimpleNamespace(success=True, error=None)

    async def switch_tab(self, tab_id: str) -> Any:
        self.switch_calls.append(tab_id)
        return SimpleNamespace(success=True)


def session(targets: set[str] | None = None) -> BrowserAgentSession:
    current = BrowserAgentSession(
        id="session-1",
        user_id=1,
        profile="user-1",
        model_target="target-1",
        base_target_id="target-1",
        model_targets=targets or {"target-1", "target-2"},
    )
    current.tab_handles = {"tab_origin_0000000000000000": "target-1"}
    return current


def resolver_for(
    browser: FakeTabBrowser,
) -> Callable[[str, bool], Awaitable[FakeTabBrowser]]:
    async def resolve(profile: str, create: bool = False) -> FakeTabBrowser:
        return browser

    return resolve


def test_list_focus_and_close_are_opaque_and_do_not_switch_user_view() -> None:
    browser = FakeTabBrowser()
    current = session()
    tabs = asyncio.run(list_workset_tabs(current, resolver=resolver_for(browser)))
    assert [tab["title"] for tab in tabs] == ["Origin", "Detail"]
    assert all(tab["handle"].startswith("tab_") for tab in tabs)
    assert all("target-" not in tab["handle"] for tab in tabs)

    detail = next(tab for tab in tabs if tab["title"] == "Detail")
    result = asyncio.run(
        focus_workset_tab(current, detail["handle"], resolver=resolver_for(browser))
    )
    assert result == {"focused": detail["handle"]}
    assert current.model_target == "target-2"
    assert browser.switch_calls == []
    assert current.document is None

    origin = next(tab for tab in tabs if tab["title"] == "Origin")
    with pytest.raises(BrowserAgentError) as error:
        asyncio.run(close_workset_tab(current, origin["handle"], resolver=resolver_for(browser)))
    assert error.value.code == "PAGE_TAB_NOT_CLOSABLE"

    closed = asyncio.run(
        close_workset_tab(current, detail["handle"], resolver=resolver_for(browser))
    )
    assert closed["closed"] == detail["handle"]
    assert browser.closed == ["target-2"]
    assert "target-2" not in current.model_targets
    assert browser.switch_calls == []


def test_admit_returns_opaque_handle_and_reset_forgets_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(targets_module.os, "name", "nt")
    browser = FakeTabBrowser()
    current = session({"target-1", "target-2"})
    current.tab_handles = {"tab_origin_0000000000000000": "target-1"}
    handle = asyncio.run(
        admit_workset_tab(
            current,
            {"title": "Detail", "url": "https://b.test/"},
            resolver=resolver_for(browser),
        )
    )
    assert handle.startswith("tab_")
    assert "target-2" not in handle
    assert current.tab_handles[handle] == "target-2"
    assert current.model_target == "target-2"
    assert browser.switch_calls == ["target-2"]
    assert browser.client.calls == ["Emulation.clearDeviceMetricsOverride"]
    reset_workset(current)
    assert current.tab_handles == {}
    assert browser.closed == []
