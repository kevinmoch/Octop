"""HTTP behavior for the restricted model tab workset."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin

from octop.api.routers.browser import webskill
from octop.infra.control.browser.webskill.sessions import BrowserAgentRegistry

ORIGIN_HEADERS = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


class FakeTabsBrowser:
    def __init__(self) -> None:
        self.tabs = [
            {"tab_id": "target-1", "title": "Origin", "url": "https://a.test/", "active": True},
            {"tab_id": "target-2", "title": "Detail", "url": "https://b.test/", "active": False},
        ]
        self.closed: list[str] = []
        self.switched: list[str] = []

    async def list_tabs(self) -> Any:
        return SimpleNamespace(success=True, metadata={"tabs": self.tabs}, error=None)

    async def close_tab(self, tab_id: str) -> Any:
        self.closed.append(tab_id)
        self.tabs = [tab for tab in self.tabs if tab["tab_id"] != tab_id]
        return SimpleNamespace(success=True, error=None)

    async def switch_tab(self, tab_id: str) -> Any:
        self.switched.append(tab_id)
        return SimpleNamespace(success=True)


@pytest.fixture
def tabs_host(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[BrowserAgentRegistry, FakeTabsBrowser]:
    registry = BrowserAgentRegistry()
    browser = FakeTabsBrowser()

    async def resolve(profile: str, create: bool = False) -> FakeTabsBrowser:
        return browser

    monkeypatch.setattr(webskill, "browser_agent_registry", registry)
    monkeypatch.setattr(webskill, "resolve_harness_session", resolve)
    monkeypatch.setattr(webskill, "control_owner_for", lambda _profile: "agent")
    return registry, browser


async def test_tabs_are_opaque_and_focus_does_not_switch_user_view(
    tmp_path: Path,
    tabs_host: tuple[BrowserAgentRegistry, FakeTabsBrowser],
) -> None:
    registry, browser = tabs_host
    async with octop_client(tmp_path) as (client, _server):
        await bootstrap_admin(client, tmp_path)
        auth = await auth_header(client)
        created = await client.post(
            "/api/browser-agent/sessions", headers={**auth, **ORIGIN_HEADERS}, json={}
        )
        assert created.status_code == 200, created.text
        session_id = created.json()["sessionId"]
        session = registry.get(session_id, 1)
        session.model_targets.add("target-2")

        listed = await client.get(f"/api/browser-agent/sessions/{session_id}/tabs", headers=auth)
        assert listed.status_code == 200, listed.text
        tabs = listed.json()["tabs"]
        assert [tab["title"] for tab in tabs] == ["Origin", "Detail"]
        assert all(tab["handle"].startswith("tab_") for tab in tabs)
        assert all("target-" not in tab["handle"] for tab in tabs)
        detail = next(tab for tab in tabs if tab["title"] == "Detail")
        origin = next(tab for tab in tabs if tab["title"] == "Origin")

        focused = await client.post(
            f"/api/browser-agent/sessions/{session_id}/tabs/{detail['handle']}/focus",
            headers={**auth, **ORIGIN_HEADERS},
        )
        assert focused.status_code == 200, focused.text
        assert focused.json() == {"focused": detail["handle"]}
        assert session.model_target == "target-2"
        assert browser.switched == []
        after_focus = await client.get(
            f"/api/browser-agent/sessions/{session_id}/tabs", headers=auth
        )
        assert after_focus.json()["tabs"][0]["active"] is True
        assert after_focus.json()["tabs"][1]["active"] is False

        base_close = await client.delete(
            f"/api/browser-agent/sessions/{session_id}/tabs/{origin['handle']}",
            headers={**auth, **ORIGIN_HEADERS},
        )
        assert base_close.status_code == 400

        closed = await client.delete(
            f"/api/browser-agent/sessions/{session_id}/tabs/{detail['handle']}",
            headers={**auth, **ORIGIN_HEADERS},
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["closed"] == detail["handle"]
        assert browser.closed == ["target-2"]
        assert browser.switched == []

        reset = await client.post(
            f"/api/browser-agent/sessions/{session_id}/tabs/reset",
            headers={**auth, **ORIGIN_HEADERS},
        )
        assert reset.status_code == 200
        assert session.tab_handles == {}
