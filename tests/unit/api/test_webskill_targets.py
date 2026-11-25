from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from octop.infra.control.browser.webskill import targets


@pytest.mark.asyncio
async def test_windows_device_metrics_are_cleared_before_reapplying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(targets.os, "name", "nt")
    browser = SimpleNamespace(
        _internal=SimpleNamespace(client=SimpleNamespace(send=AsyncMock(return_value={})))
    )

    await targets.reset_device_metrics_if_windows(browser)

    browser._internal.client.send.assert_awaited_once_with("Emulation.clearDeviceMetricsOverride")


@pytest.mark.asyncio
async def test_non_windows_device_metrics_are_not_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(targets.os, "name", "posix")
    browser = SimpleNamespace(
        _internal=SimpleNamespace(client=SimpleNamespace(send=AsyncMock(return_value={})))
    )

    await targets.reset_device_metrics_if_windows(browser)

    browser._internal.client.send.assert_not_awaited()
