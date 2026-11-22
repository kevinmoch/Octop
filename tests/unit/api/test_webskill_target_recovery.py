"""Target pointer safety: reads/lists never move an implicit target."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from octop.infra.control.browser.webskill.sessions import BrowserAgentSession
from octop.infra.control.browser.webskill.tabs import (
    follow_active_workset_tab,
    list_workset_tabs,
)
from octop.infra.control.browser.webskill.targets import follow_active_target


def _session() -> BrowserAgentSession:
    session = BrowserAgentSession(id="s", user_id=1, profile="p")
    session.base_target_id = "base"
    session.model_target = "closed"
    session.model_targets = {"base", "closed"}
    session.tab_handles = {"tab_base": "base", "tab_closed": "closed"}
    return session


class _Browser:
    def __init__(self, tabs: list[dict[str, Any]]) -> None:
        self.tabs = tabs

    async def list_tabs(self) -> Any:
        return SimpleNamespace(success=True, metadata={"tabs": self.tabs})


def _resolver(browser: _Browser):
    async def resolve(profile: str, create: bool = False) -> _Browser:
        return browser

    return resolve


def test_follow_active_target_is_explicit_and_clears_refs() -> None:
    session = _session()
    session.refs["ws_old"] = None  # type: ignore[assignment]
    browser = _Browser(
        [
            {"tab_id": "base", "title": "Base", "url": "https://a", "active": False},
            {"tab_id": "daily", "title": "Daily", "url": "https://b", "active": True},
        ]
    )
    assert asyncio.run(follow_active_target(session, browser)) == "daily"
    assert session.model_target == "daily"
    assert session.base_target_id == "base"
    assert session.model_targets == {"base", "daily"}
    assert "tab_closed" not in session.tab_handles
    assert session.document is None
    assert session.refs == {}


def test_list_does_not_follow_active_or_revive_closed_target() -> None:
    session = _session()
    browser = _Browser(
        [
            {"tab_id": "base", "title": "Base", "url": "https://a", "active": False},
            {"tab_id": "daily", "title": "Daily", "url": "https://b", "active": True},
        ]
    )
    tabs = asyncio.run(list_workset_tabs(session, resolver=_resolver(browser)))
    # The closed model target is not silently replaced with daily; the list is
    # still useful for an explicit focus.
    assert [tab["title"] for tab in tabs] == ["Base"]
    assert session.model_target == "closed"
    assert "daily" not in session.model_targets


def test_list_fails_closed_when_base_is_gone() -> None:
    session = _session()
    session.base_target_id = "closed"
    browser = _Browser([{"tab_id": "daily", "title": "Daily", "url": "https://b", "active": True}])
    try:
        asyncio.run(list_workset_tabs(session, resolver=_resolver(browser)))
    except Exception as error:
        assert getattr(error, "code", "") == "PAGE_TAB_EXPIRED"
    else:
        raise AssertionError("a missing base must fail closed")
    assert session.model_target == "closed"


def test_public_capabilities_match_implemented_host_features() -> None:
    caps = _session().public()["capabilities"]
    assert caps["oopif"] is True
    assert caps["imageCapture"] is True


def test_explicit_follow_returns_a_handle_for_the_active_page() -> None:
    session = _session()
    browser = _Browser(
        [
            {"tab_id": "base", "title": "Base", "url": "https://a", "active": False},
            {"tab_id": "daily", "title": "Daily", "url": "https://b", "active": True},
        ]
    )
    result = asyncio.run(follow_active_workset_tab(session, resolver=_resolver(browser)))
    assert result["focused"].startswith("tab_")
    assert session.model_target == "daily"
