"""Recover the model target when the user closes its tab externally."""

from __future__ import annotations

from octop.infra.control.browser.webskill.sessions import BrowserAgentSession, DocumentIdentity
from octop.infra.control.browser.webskill.targets import recover_target_from_tabs


def _session() -> BrowserAgentSession:
    session = BrowserAgentSession(id="s", user_id=1, profile="p")
    session.base_target_id = "base"
    session.model_target = "closed"
    session.model_targets = {"base", "closed"}
    session.tab_handles = {"tab_base": "base", "tab_closed": "closed"}
    session.document = DocumentIdentity(
        key="old", target_id="closed", loader_id="l", url="https://x"
    )
    session.refs["ws_old"] = None  # type: ignore[assignment]
    return session


def test_closed_model_target_follows_active_tab() -> None:
    session = _session()
    tabs = {
        "base": {"tab_id": "base", "active": False},
        "daily": {"tab_id": "daily", "active": True},
    }
    assert recover_target_from_tabs(session, tabs) == "daily"
    assert session.model_target == "daily"
    assert session.base_target_id == "base"
    assert session.model_targets == {"base", "daily"}
    assert "tab_closed" not in session.tab_handles
    assert session.document is None
    assert session.refs == {}


def test_closed_base_is_replaced_by_active_tab() -> None:
    session = _session()
    session.base_target_id = "closed"
    tabs = {
        "daily": {"tab_id": "daily", "active": True},
    }
    assert recover_target_from_tabs(session, tabs) == "daily"
    assert session.base_target_id == "daily"
    assert session.model_target == "daily"
    assert session.model_targets == {"daily"}
    assert session.tab_handles == {}


def test_no_live_target_returns_none() -> None:
    session = _session()
    assert recover_target_from_tabs(session, {}) is None
