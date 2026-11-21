"""tests/unit/api/test_harness_tabs.py"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octop.api.routers.browser import harness as harness_mod


def _fake_sess(*, active_id: str | None = "T1", port: int = 9222) -> SimpleNamespace:
    profile = SimpleNamespace(
        cdp_port=port,
        load_target=MagicMock(return_value=active_id),
        save_target=MagicMock(),
    )
    client = SimpleNamespace(
        close=AsyncMock(),
        connect=AsyncMock(),
        enable_domain=AsyncMock(),
    )
    internal = SimpleNamespace(
        _profile=profile,
        _cfg=SimpleNamespace(cdp_host="127.0.0.1"),
        _client=client,
        _apply_viewport=AsyncMock(),
        _connected=False,
    )
    return SimpleNamespace(_internal=internal)


@pytest.mark.asyncio
async def test_status_probes_do_not_extend_browser_idle_timeout() -> None:
    passive = AsyncMock(
        side_effect=[
            {},
            {"result": {"value": "https://example.com"}},
        ]
    )
    active = AsyncMock()
    client = SimpleNamespace(send=active, send_passive=passive)
    sess = SimpleNamespace(_internal=SimpleNamespace(client=client))

    assert await harness_mod._is_session_alive(sess)
    assert await harness_mod.harness_page_url(sess) == "https://example.com"
    assert passive.await_count == 2
    active.assert_not_awaited()


@pytest.mark.asyncio
async def test_harness_list_tabs_marks_profile_target_active() -> None:
    sess = _fake_sess(active_id="TAB-B")
    targets = [
        {"type": "page", "id": "TAB-A", "url": "https://a.test", "title": "A"},
        {"type": "page", "id": "TAB-B", "url": "https://b.test", "title": "B"},
    ]

    class _JsonResp:
        def __init__(self, data: list[dict[str, object]]) -> None:
            self._data = data

        async def __aenter__(self) -> _JsonResp:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def json(self, content_type: object = None) -> list[dict[str, object]]:
            return self._data

    class _Session:
        def __init__(self, data: list[dict[str, object]]) -> None:
            self._data = data

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        def get(self, url: str, timeout: object = None) -> _JsonResp:
            return _JsonResp(self._data)

    with patch("aiohttp.ClientSession", new=lambda: _Session(targets)):
        tabs = await harness_mod.harness_list_tabs(sess)

    assert [t["id"] for t in tabs] == ["TAB-A", "TAB-B"]
    assert tabs[1]["active"] is True
    assert tabs[0]["active"] is False


@pytest.mark.asyncio
async def test_shutdown_browser_forces_current_user_profile() -> None:
    result = SimpleNamespace(success=True, error=None)
    with (
        patch(
            "octop_browser.tool_interface.browser_tool",
            new=AsyncMock(return_value=result),
        ) as tool,
        patch("octop_browser.tool_interface._registry", {"work": object()}),
    ):
        body = await harness_mod.shutdown_browser(
            user=SimpleNamespace(id=7),
        )
    assert body == {"ok": True, "profile": "user-7"}
    tool.assert_awaited_once_with(
        action="close_session",
        profile="user-7",
        kill=True,
    )


def test_stable_tab_order_survives_reconnect_and_close() -> None:
    port = 9331
    harness_mod._TAB_ORDER.pop(port, None)
    harness_mod._TAB_NEXT.pop(port, None)

    def page(target: str) -> dict[str, object]:
        return {"type": "page", "id": target, "url": f"https://{target}.test"}

    first = harness_mod._stable_tab_pages(port, [page("A"), page("B")])
    assert [item["id"] for item in first] == ["A", "B"]

    # /json order changed after a stream reconnect; the visible order must not.
    second = harness_mod._stable_tab_pages(port, [page("B"), page("A")])
    assert [item["id"] for item in second] == ["A", "B"]

    # Closing the first tab keeps B before newly appended C.
    third = harness_mod._stable_tab_pages(port, [page("C"), page("B")])
    assert [item["id"] for item in third] == ["B", "C"]
