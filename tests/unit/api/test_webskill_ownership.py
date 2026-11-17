"""Behavior tests for profile control ownership shared with daemon processes."""

from __future__ import annotations

from pathlib import Path

from octop_browser.lease import read_control_owner
from octop_browser.settings import settings as browser_settings

from octop.infra.control.browser.webskill import ownership


def test_control_owner_is_persisted_and_cleared(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(browser_settings, "profiles_dir", tmp_path)
    try:
        ownership.set_control_owner("user-1", "user")
        assert ownership.control_owner_for("user-1") == "user"
        assert read_control_owner("user-1") == "user"
        ownership.clear_control_owner("user-1")
        assert ownership.control_owner_for("user-1") == "agent"
        assert read_control_owner("user-1") == "agent"

        from octop_browser.lease import write_control_owner

        write_control_owner("user-1", "user")
        ownership._CONTROL_OWNERS.pop("user-1", None)
        assert ownership.control_owner_for("user-1") == "user"
        ownership.clear_control_owner("user-1")
    finally:
        ownership._CONTROL_OWNERS.pop("user-1", None)
