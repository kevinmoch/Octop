"""Page image capture resolves only fresh refs and respects the byte budget."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.control.browser.webskill.page_agent import capture_image
from octop.infra.control.browser.webskill.sessions import (
    BrowserAgentError,
    BrowserAgentRegistry,
    BrowserAgentSession,
    DocumentIdentity,
    PageRef,
)


class FakeCaptureClient:
    def __init__(self, payload: bytes = b"png-bytes") -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(method)
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "target-1", "type": "page"}]}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"loaderId": "loader-1"}}}
        if method == "Runtime.evaluate":
            return {"result": {"value": {"url": "https://example.test/", "title": "Example"}}}
        if method == "DOM.scrollIntoViewIfNeeded":
            return {}
        if method == "DOM.getBoxModel":
            return {"model": {"border": [10, 20, 110, 20, 110, 80, 10, 80]}}
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(self.payload).decode()}
        raise AssertionError(method)


class FakeCaptureBrowser:
    def __init__(self, client: FakeCaptureClient) -> None:
        self._internal = SimpleNamespace(client=client)


def _session() -> BrowserAgentSession:
    session = BrowserAgentSession(
        id="session-1",
        user_id=1,
        profile="user-1",
        model_target="target-1",
        base_target_id="target-1",
        model_targets={"target-1"},
    )
    key = json.dumps(
        [1, "user-1", "target-1", "loader-1", "https://example.test/"],
        separators=(",", ":"),
    )
    session.document = DocumentIdentity(
        key=key, target_id="target-1", loader_id="loader-1", url="https://example.test/"
    )
    session.refs = {"ws_ref": PageRef(backend_node_id=5, document_key=key, frame="self")}
    return session


def _resolver(browser: FakeCaptureBrowser):
    async def resolve(profile: str, create: bool = False) -> FakeCaptureBrowser:
        return browser

    return resolve


def test_capture_image_is_bounded_and_returns_document_image() -> None:
    client = FakeCaptureClient()
    browser = FakeCaptureBrowser(client)
    result = asyncio.run(
        capture_image(
            _session(),
            {"ref": "ws_ref", "maxBytes": 1024},
            registry=BrowserAgentRegistry(),
            resolver=_resolver(browser),
        )
    )
    assert result["type"] == "capture-image-result"
    image = result["result"]
    assert image["mimeType"] == "image/png"
    assert image["width"] == 100
    assert image["height"] == 60
    assert image["bytes"] == len(b"png-bytes")
    assert "DOM.scrollIntoViewIfNeeded" in client.calls
    assert "Page.captureScreenshot" in client.calls


def test_capture_image_rejects_stale_ref_and_over_budget() -> None:
    client = FakeCaptureClient(payload=b"x" * 100)
    browser = FakeCaptureBrowser(client)
    with pytest.raises(BrowserAgentError) as stale:
        asyncio.run(
            capture_image(
                _session(),
                {"ref": "ws_missing", "maxBytes": 1024},
                registry=BrowserAgentRegistry(),
                resolver=_resolver(browser),
            )
        )
    assert stale.value.code == "PAGE_ACTION_STALE_REF"

    result = asyncio.run(
        capture_image(
            _session(),
            {"ref": "ws_ref", "maxBytes": 10},
            registry=BrowserAgentRegistry(),
            resolver=_resolver(browser),
        )
    )
    assert result["result"]["id"] == "ws_ref"
    assert "over the 10-byte budget" in result["result"]["reason"]
