from __future__ import annotations

import pytest
from octop_browser.settings import settings

from octop.infra.utils.browser_media import configure_browser_launch_mode


def test_windows_browser_mode_defaults_to_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("octop.infra.utils.browser_media.os.name", "nt")
    monkeypatch.delenv("BROWSER_USE_MODE", raising=False)
    monkeypatch.setattr(settings, "browser_mode", "auto")

    configure_browser_launch_mode()

    assert settings.browser_mode == "headless"


def test_windows_browser_mode_preserves_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("octop.infra.utils.browser_media.os.name", "nt")
    monkeypatch.setenv("BROWSER_USE_MODE", "headed")
    monkeypatch.setattr(settings, "browser_mode", "auto")

    configure_browser_launch_mode()

    assert settings.browser_mode == "headed"
