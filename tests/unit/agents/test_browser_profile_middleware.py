from unittest.mock import AsyncMock

import pytest
from langgraph.prebuilt.tool_node import ToolCallRequest

from octop.infra.agents.middleware import browser_profile


def _request(name: str, args: dict[str, object]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "call-1"},
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_browser_profile_is_forced_from_current_user(monkeypatch) -> None:
    monkeypatch.setattr(
        browser_profile,
        "get_config",
        lambda: {"configurable": {"user": "42"}},
    )
    from octop_browser import tool_interface

    monkeypatch.setattr(tool_interface, "_command_gate_factory", None)
    handler = AsyncMock(return_value=object())
    request = _request("browser_use", {"action": "navigate", "profile": "default"})

    await browser_profile.BrowserProfileMiddleware().awrap_tool_call(request, handler)

    forwarded = handler.await_args.args[0]
    assert forwarded.tool_call["args"] == {
        "action": "navigate",
        "profile": "user-42",
    }
    assert tool_interface._command_gate_factory is browser_profile.profile_command_gate


@pytest.mark.asyncio
async def test_non_browser_tool_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        browser_profile,
        "get_config",
        lambda: {"configurable": {"user": "42"}},
    )
    handler = AsyncMock(return_value=object())
    request = _request("read_file", {"path": "notes.txt"})

    await browser_profile.BrowserProfileMiddleware().awrap_tool_call(request, handler)

    assert handler.await_args.args[0] is request


@pytest.mark.asyncio
async def test_browser_profile_blocks_until_bundled_browser_is_enabled(monkeypatch) -> None:
    monkeypatch.setattr(browser_profile, "browser_enabled", lambda: False)
    monkeypatch.setattr(
        browser_profile,
        "get_config",
        lambda: {"configurable": {"user": "42"}},
    )
    handler = AsyncMock(return_value=object())
    request = _request("browser_use", {"action": "navigate", "profile": "default"})

    result = await browser_profile.BrowserProfileMiddleware().awrap_tool_call(request, handler)

    handler.assert_not_awaited()
    assert getattr(result, "status", None) == "error"
    assert "not enabled" in str(result.content)


@pytest.mark.asyncio
async def test_browser_profile_blocks_when_user_missing(monkeypatch) -> None:
    monkeypatch.setattr(browser_profile, "get_config", lambda: {"configurable": {}})
    handler = AsyncMock(return_value=object())
    request = _request("browser_use", {"action": "navigate", "profile": "default"})

    result = await browser_profile.BrowserProfileMiddleware().awrap_tool_call(request, handler)

    handler.assert_not_awaited()
    assert getattr(result, "status", None) == "error"
    assert "no Octop user id" in str(result.content)


@pytest.mark.asyncio
async def test_browser_profile_blocks_placeholder_user_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        browser_profile,
        "get_config",
        lambda: {"configurable": {"user": "0"}},
    )
    handler = AsyncMock(return_value=object())
    request = _request("browser_use", {"action": "navigate"})

    result = await browser_profile.BrowserProfileMiddleware().awrap_tool_call(request, handler)

    handler.assert_not_awaited()
    assert getattr(result, "status", None) == "error"


@pytest.mark.asyncio
async def test_browser_profile_blocks_writes_during_user_takeover(monkeypatch) -> None:
    from octop.infra.control.browser.webskill.ownership import _CONTROL_OWNERS

    monkeypatch.setattr(browser_profile, "browser_enabled", lambda: True)
    monkeypatch.setattr(
        browser_profile,
        "get_config",
        lambda: {"configurable": {"user": "42"}},
    )
    monkeypatch.setitem(_CONTROL_OWNERS, "user-42", "user")
    handler = AsyncMock(return_value=object())
    request = _request("browser_use", {"action": "eval_js", "expression": "1"})

    result = await browser_profile.BrowserProfileMiddleware().awrap_tool_call(request, handler)

    handler.assert_not_awaited()
    assert getattr(result, "status", None) == "error"
    assert "User control is active" in str(result.content)
