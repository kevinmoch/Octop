from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch


async def test_record_replay_status_returns_daemon_status(env: Any) -> None:
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(return_value={"ok": True, "active": None}),
        ) as mock_send,
        patch(
            "octop.api.routers.browser.record_replay._latest_recording_id",
            return_value="rec_latest",
        ),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)

    assert r.status_code == 200
    assert r.json() == {"ok": True, "active": None, "latestRecordingId": "rec_latest"}
    mock_send.assert_awaited_once_with({"command": "status"})


async def test_record_replay_start_ensures_daemon_and_starts_recording(env: Any) -> None:
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.ensure_record_daemon",
            new=AsyncMock(return_value={"ok": True, "pid": 123}),
        ) as mock_ensure,
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(return_value={"ok": True, "recordingId": "rec_1", "daemon": True}),
        ) as mock_send,
    ):
        r = await client.post(
            "/api/browser/record-replay/start",
            headers=auth,
            json={"profile": "thr_demo", "name": "demo"},
        )

    assert r.status_code == 200
    assert r.json()["recordingId"] == "rec_1"
    mock_ensure.assert_awaited_once_with()
    mock_send.assert_awaited_once_with(
        {
            "command": "start",
            "profile": "user-1",
            "name": "demo",
            "privacy": "mask-sensitive",
            "screenshots": "off",
        }
    )


async def test_record_replay_start_ignores_requested_profile(env: Any) -> None:
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.ensure_record_daemon",
            new=AsyncMock(return_value={"ok": True, "pid": 123}),
        ),
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(return_value={"ok": True, "recordingId": "rec_1"}),
        ) as mock_send,
    ):
        r = await client.post(
            "/api/browser/record-replay/start",
            headers=auth,
            json={"profile": "thr_demo", "name": "demo"},
        )

    assert r.status_code == 200
    mock_send.assert_awaited_once_with(
        {
            "command": "start",
            "profile": "user-1",
            "name": "demo",
            "privacy": "mask-sensitive",
            "screenshots": "off",
        }
    )


async def test_record_replay_start_returns_503_when_daemon_fails(env: Any) -> None:
    client, _srv, auth = env

    with patch(
        "octop.api.routers.browser.record_replay.ensure_record_daemon",
        new=AsyncMock(return_value={"ok": False, "error": "Daemon did not start"}),
    ):
        r = await client.post(
            "/api/browser/record-replay/start",
            headers=auth,
            json={"profile": "thr_demo"},
        )

    assert r.status_code == 503
    body = r.json()
    assert body["error"]["details"]["recordReplay"]["error"] == "Daemon did not start"


async def test_record_replay_start_rejects_user_takeover(
    env: Any, monkeypatch: Any, tmp_path: Any
) -> None:
    from octop_browser.settings import settings as browser_settings

    from octop.infra.control.browser.webskill import ownership

    client, _srv, auth = env
    monkeypatch.setattr(browser_settings, "profiles_dir", tmp_path / "browser-profiles")
    ownership.set_control_owner("user-1", "user")
    with patch(
        "octop.api.routers.browser.record_replay.ensure_record_daemon",
        new=AsyncMock(),
    ) as mock_ensure:
        r = await client.post("/api/browser/record-replay/start", headers=auth, json={})

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "FORBIDDEN"
    mock_ensure.assert_not_awaited()


async def test_record_replay_stop_generates_steps(env: Any) -> None:
    client, _srv, auth = env
    owned = SimpleNamespace(read_manifest=lambda _rid: SimpleNamespace(profile="user-1"))

    with (
        patch("octop_browser.record.store.RecordingStore", return_value=owned),
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(
                return_value={"ok": True, "recordingId": "rec_1", "events": 4, "steps": 2}
            ),
        ) as mock_send,
    ):
        r = await client.post(
            "/api/browser/record-replay/stop",
            headers=auth,
            json={"recordingId": "rec_1", "name": "demo"},
        )

    assert r.status_code == 200
    assert r.json()["steps"] == 2
    mock_send.assert_awaited_once_with(
        {
            "command": "stop",
            "recording_id": "rec_1",
            "generate_steps": True,
            "name": "demo",
        }
    )


async def test_record_replay_start_ignores_requested_agent_profile(env: Any) -> None:
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.ensure_record_daemon",
            new=AsyncMock(return_value={"ok": True, "pid": 123}),
        ),
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(return_value={"ok": True, "recordingId": "rec_default"}),
        ) as mock_send,
    ):
        r = await client.post(
            "/api/browser/record-replay/start",
            headers=auth,
            json={"profile": "thr_123", "agentProfile": "default"},
        )

    assert r.status_code == 200
    mock_send.assert_awaited_once_with(
        {
            "command": "start",
            "profile": "user-1",
            "name": None,
            "privacy": "mask-sensitive",
            "screenshots": "off",
        }
    )


async def test_record_replay_replay_runs_runner(env: Any) -> None:
    client, _srv, auth = env
    runner = AsyncMock(return_value={"status": "passed", "recordingId": "rec_1"})

    with (
        patch(
            "octop_browser.record.store.RecordingStore",
            return_value=SimpleNamespace(
                read_manifest=lambda _rid: SimpleNamespace(profile="user-1")
            ),
        ),
        patch(
            "octop.api.routers.browser.record_replay.run_replay_recording",
            new=runner,
        ),
    ):
        r = await client.post(
            "/api/browser/record-replay/replay",
            headers=auth,
            json={"recordingId": "rec_1", "profile": "thr_demo-replay"},
        )

    assert r.status_code == 200
    assert r.json()["status"] == "passed"
    runner.assert_awaited_once_with("rec_1", profile="user-1", inputs={})


async def test_record_status_hides_other_user_active(env: Any) -> None:
    client, _srv, auth = env
    with (
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "active": {"recordingId": "rec_x", "profile": "user-99"},
                }
            ),
        ),
        patch(
            "octop.api.routers.browser.record_replay._latest_recording_id",
            return_value=None,
        ),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)
    assert r.status_code == 200
    assert r.json()["active"] is None


async def test_record_skill_content_hides_other_user_recording(env: Any) -> None:
    client, _srv, auth = env
    store = SimpleNamespace(
        read_manifest=lambda _rid: SimpleNamespace(profile="user-99"),
    )
    with patch("octop_browser.record.store.RecordingStore", return_value=store):
        r = await client.post(
            "/api/browser/record-replay/skill-content",
            headers=auth,
            json={"recordingId": "rec_other"},
        )
    assert r.status_code == 404


async def test_replay_executor_uses_the_gated_browser_session(tmp_path, monkeypatch) -> None:
    from octop_browser.models import ActionMetrics, ToolResult
    from octop_browser.record.models import (
        RecordingManifest,
        SemanticStep,
        StepsDocument,
    )
    from octop_browser.record.settings import RecordReplaySettings
    from octop_browser.record.store import RecordingStore

    from octop.api.routers.browser import record_replay

    calls: list[str] = []

    async def navigate(url: str) -> ToolResult:
        calls.append(url)
        return ToolResult(
            success=True,
            content="ok",
            metrics=ActionMetrics(
                action="navigate",
                duration_ms=0,
                dom_nodes_scanned=0,
                estimated_tokens=0,
            ),
        )

    session = SimpleNamespace(_internal=SimpleNamespace(_command_gate=object()), navigate=navigate)
    monkeypatch.setattr(
        "octop.api.routers.browser.harness.resolve_harness_session",
        AsyncMock(return_value=session),
    )

    async def unexpected_default(*args, **kwargs):
        raise AssertionError("The default browser_tool must not be used")

    monkeypatch.setattr("octop_browser.tool_interface.browser_tool", unexpected_default)
    store = RecordingStore(RecordReplaySettings(base_dir=tmp_path))
    store.create(RecordingManifest(recordingId="rec_gate", createdAt="now"))
    store.write_steps(
        "rec_gate",
        StepsDocument(
            recordingId="rec_gate",
            steps=[SemanticStep(id="step_001", kind="open", url="https://example.test")],
        ),
    )
    monkeypatch.setattr("octop_browser.record.replay.RecordingStore", lambda: store)

    report = await record_replay.run_replay_recording("rec_gate", profile="user-1", inputs={})

    assert report["status"] == "passed", report.get("error") or report["steps"]
    assert calls == ["https://example.test"]


async def test_replay_stops_when_user_takes_control(tmp_path, monkeypatch) -> None:
    from octop_browser.models import ActionMetrics, ToolResult
    from octop_browser.record.models import RecordingManifest, SemanticStep, StepsDocument
    from octop_browser.record.settings import RecordReplaySettings
    from octop_browser.record.store import RecordingStore
    from octop_browser.settings import settings as browser_settings

    from octop.api.routers.browser import record_replay
    from octop.infra.control.browser.webskill import ownership

    calls: list[str] = []
    monkeypatch.setattr(browser_settings, "profiles_dir", tmp_path / "browser-profiles")
    ownership.set_control_owner("user-1", "agent")

    async def navigate(url: str) -> ToolResult:
        calls.append(url)
        ownership.set_control_owner("user-1", "user")
        return ToolResult(
            success=True,
            content="ok",
            metrics=ActionMetrics(
                action="navigate", duration_ms=0, dom_nodes_scanned=0, estimated_tokens=0
            ),
        )

    session = SimpleNamespace(_internal=SimpleNamespace(_command_gate=object()), navigate=navigate)
    monkeypatch.setattr(
        "octop.api.routers.browser.harness.resolve_harness_session",
        AsyncMock(return_value=session),
    )
    store = RecordingStore(RecordReplaySettings(base_dir=tmp_path))
    store.create(RecordingManifest(recordingId="rec_takeover", createdAt="now"))
    store.write_steps(
        "rec_takeover",
        StepsDocument(
            recordingId="rec_takeover",
            steps=[
                SemanticStep(id="step_001", kind="open", url="https://example.test/1"),
                SemanticStep(id="step_002", kind="open", url="https://example.test/2"),
            ],
        ),
    )
    monkeypatch.setattr("octop_browser.record.replay.RecordingStore", lambda: store)

    report = await record_replay.run_replay_recording("rec_takeover", profile="user-1", inputs={})

    assert report["status"] == "failed"
    assert "User control is active" in report["error"]
    assert calls == ["https://example.test/1"]


async def test_replay_rejects_a_session_without_write_coordination(
    monkeypatch,
) -> None:
    import pytest

    from octop.api.routers.browser import record_replay
    from octop.infra.errors import OctopError

    session = SimpleNamespace(_internal=SimpleNamespace(_command_gate=None))
    monkeypatch.setattr(
        "octop.api.routers.browser.harness.resolve_harness_session",
        AsyncMock(return_value=session),
    )
    with pytest.raises(OctopError) as error:
        await record_replay.run_replay_recording("rec_missing_gate", profile="user-1", inputs={})
    assert error.value.status == 503
