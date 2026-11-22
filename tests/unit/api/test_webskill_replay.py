"""Behavior tests for scoped replay authorization and the real ReplayRunner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from octop_browser.record.models import RecordingManifest, SemanticStep, StepsDocument
from octop_browser.record.replay import ReplayRunner
from octop_browser.record.settings import RecordReplaySettings
from octop_browser.record.store import RecordingStore
from tests.unit.api.test_webskill_page_agent import FakeBrowser, resolver_for, tool_result

from octop.infra.control.browser.webskill.page_agent import read_page
from octop.infra.control.browser.webskill.replay import ReplayRegistry, ScopedReplayExecutor
from octop.infra.control.browser.webskill.sessions import BrowserAgentRegistry, BrowserAgentSession


def session() -> BrowserAgentSession:
    return BrowserAgentSession(
        id="session-1",
        user_id=1,
        profile="user-1",
        model_target="target-1",
        base_target_id="target-1",
        model_targets={"target-1"},
    )


def steps(kind: str = "click") -> StepsDocument:
    return StepsDocument(
        recordingId="rec_scope",
        startUrl="https://example.test/form",
        steps=[
            SemanticStep(
                id="step_1",
                kind=kind,
                target={"role": "button", "name": "Run once"},
            )
        ],
    )


def test_replay_grant_is_scoped_single_use_and_cancelled() -> None:
    registry = ReplayRegistry()
    current = session()
    run = registry.authorize(current, steps(), source_digest="source-1")
    document = current.document
    assert document is None
    from octop.infra.control.browser.webskill.sessions import DocumentIdentity

    document = DocumentIdentity("document-1", "target-1", "loader-1", "https://example.test")
    token, index = registry.issue_grant(
        run, document=document, action="click", payload={"ref": "fresh-ref"}
    )
    assert index == 1
    consumed = registry.consume_grant(
        run, token, document=document, action="click", payload={"ref": "fresh-ref"}
    )
    assert consumed.replay_id == run.id
    with pytest.raises(ValueError, match="already used"):
        registry.consume_grant(
            run, token, document=document, action="click", payload={"ref": "fresh-ref"}
        )
    with pytest.raises(ValueError, match="outside"):
        registry.issue_grant(run, document=document, action="eval_js", payload={})
    registry.cancel(run.id)
    with pytest.raises(ValueError, match="cancelled"):
        registry.get(run.id, current)


def test_deleted_recording_cancels_runs_and_cannot_authorize_again() -> None:
    registry = ReplayRegistry()
    current = session()
    run = registry.authorize(current, steps(), source_digest="source-1")
    registry.cancel_recording(steps().recording_id, current.user_id)
    with pytest.raises(ValueError, match="cancelled"):
        registry.get(run.id, current)
    with pytest.raises(ValueError, match="deleted"):
        registry.authorize(current, steps(), source_digest="source-1")


def test_replay_expires_and_does_not_cross_sessions() -> None:
    now = [10.0]
    registry = ReplayRegistry(now=lambda: now[0])
    current = session()
    run = registry.authorize(current, steps(), source_digest="source-1")
    other = BrowserAgentSession(id="other", user_id=2, profile="user-2")
    with pytest.raises(ValueError, match="not available"):
        registry.get(run.id, other)
    now[0] += 301
    with pytest.raises(ValueError, match="expired"):
        registry.get(run.id, current)


async def prepare_executor(
    tmp_path: Path,
    *,
    document: StepsDocument | None = None,
) -> tuple[
    FakeBrowser,
    BrowserAgentSession,
    ReplayRegistry,
    ScopedReplayExecutor,
    list[dict[str, Any]],
]:
    browser = FakeBrowser()
    registry = BrowserAgentRegistry()
    current = registry.create(1, "user-1")
    resolver = resolver_for(browser)
    await read_page(
        current,
        {"scope": {"include": ["body"]}},
        resolver=resolver,
        registry=registry,
    )
    replays = ReplayRegistry()
    source = document or steps()
    run = replays.authorize(current, source, source_digest="source-1")
    audits: list[dict[str, Any]] = []

    async def audit(action: str, index: int, data: dict[str, Any]) -> None:
        audits.append({"action": action, "index": index, **data})

    store = RecordingStore(RecordReplaySettings(base_dir=tmp_path))
    store.create(
        RecordingManifest(
            recordingId=source.recording_id,
            createdAt="2026-11-19T00:00:00Z",
            endedAt="2026-11-19T00:01:00Z",
            status="stopped",
            profile="user-1",
        )
    )
    store.write_steps(source.recording_id, source)
    executor = ScopedReplayExecutor(
        session=current,
        authorization=run,
        replays=replays,
        resolver=resolver,
        registry=registry,
        write_gate=lambda: True,
        audit=audit,
        store=store,
    )
    return browser, current, replays, executor, audits


async def test_real_replay_runner_uses_fresh_page_ref_and_one_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser, current, replays, executor, audits = await prepare_executor(tmp_path)
    monkeypatch.setattr(
        "octop_browser.record.replay.RecordingStore",
        lambda: RecordingStore(RecordReplaySettings(base_dir=tmp_path)),
    )
    old_ref = next(iter(current.refs))
    old_document = current.document
    result = await ReplayRunner(executor=executor).run("rec_scope", profile="user-1")

    assert result["status"] == "passed", result.get("error") or result["steps"]
    assert len(browser.clicks) == 1
    assert executor.authorization.actions_issued == 1
    assert current.refs
    # Refs are reusable across perceptions by design. The replay contract is
    # that it perceived the live document, consumed a fresh one-time grant and
    # never persisted the recording's old ref; string inequality is not the
    # criterion (a reused handle may legitimately stay the same).
    assert current.document is not None
    assert old_document is not None
    assert current.document.key == old_document.key
    assert old_ref in current.refs
    assert audits[0]["action"] == "click"
    assert audits[0]["index"] == 1
    assert len(audits[0]["grantDigest"]) == 64
    assert "fresh-ref" not in json.dumps(audits)
    assert replays.get(executor.authorization.id, current).active


async def test_replay_takeover_and_cancel_prevent_subsequent_actions(tmp_path: Path) -> None:
    browser, current, replays, executor, _audits = await prepare_executor(tmp_path)
    dom = await executor("dom_tree", profile="user-1")
    ref = json.loads(dom.content)["elements"][0]["ref"]
    replays.cancel(executor.authorization.id)
    with pytest.raises(ValueError, match="cancelled"):
        await executor("click", ref=ref, profile="user-1")
    assert browser.clicks == []


async def test_replay_rejects_unsupported_script_execution(tmp_path: Path) -> None:
    _browser, _current, _replays, executor, _audits = await prepare_executor(
        tmp_path, document=steps("set_checked")
    )
    result = await executor("eval_js", expression="document.title = 'unexpected'", profile="user-1")
    assert result.success is False
    assert "not supported" in (result.error or "")
    assert executor.authorization.actions_issued == 0


def navigation_steps() -> StepsDocument:
    return StepsDocument(
        recordingId="rec_scope",
        startUrl="https://example.test/form",
        steps=[SemanticStep(id="step_1", kind="open", url="https://example.test/form")],
    )


async def test_direct_navigation_gets_a_grant_without_logging_its_url(tmp_path: Path) -> None:
    browser, current, _replays, executor, audits = await prepare_executor(
        tmp_path, document=navigation_steps()
    )
    url = "https://example.test/next?token=not-written-to-audit"

    async def navigate(url: str):
        return tool_result(url)

    browser.navigate = navigate  # type: ignore[attr-defined]
    result = await executor("navigate", url=url, profile="user-1")

    assert result.success is True
    assert executor.authorization.actions_issued == 1
    assert audits[0]["action"] == "navigate"
    assert len(audits[0]["grantDigest"]) == 64
    assert url not in json.dumps(audits)


async def test_direct_navigation_rejects_non_http_urls(tmp_path: Path) -> None:
    _browser, _current, _replays, executor, _audits = await prepare_executor(
        tmp_path, document=navigation_steps()
    )
    with pytest.raises(ValueError, match="credentialess HTTP"):
        await executor("navigate", url="javascript:alert(1)")


class ScopedReplayClient:
    def __init__(self, parent: Any, session_id: str, calls: list[tuple[str, str]]) -> None:
        self.parent = parent
        self.session_id = session_id
        self.calls = calls

    def on(self, event: str, callback: Any) -> None:
        self.parent.on(event, callback)

    def off(self, event: str, callback: Any) -> None:
        self.parent.off(event, callback)

    async def enable_domain(self, domain: str, *, session_id: str | None = None) -> None:
        await self.parent.send(f"{domain}.enable", session_id=self.session_id)

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, self.session_id))
        if (
            method == "Runtime.evaluate"
            and params
            and params.get("expression") == "document.readyState"
        ):
            return {"result": {"value": "complete"}}
        result = await self.parent.send(method, params, session_id=self.session_id)
        if method == "Page.navigate":
            for callback in self.parent.context_listeners.get("Page.loadEventFired", []):
                callback({})
        return result

    async def close(self) -> None:
        await self.parent.send("Target.detachFromTarget", {"sessionId": self.session_id})


def enable_target_scope_spy(browser: FakeBrowser) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    browser.client.with_session = lambda session_id: ScopedReplayClient(
        browser.client, session_id, calls
    )
    return calls


async def test_replay_page_action_rejects_changed_origin_and_target(
    tmp_path: Path,
) -> None:
    browser, current, _replays, executor, _audits = await prepare_executor(tmp_path)
    enable_target_scope_spy(browser)
    dom = await executor("dom_tree", profile="user-1")
    ref = json.loads(dom.content)["elements"][0]["ref"]
    browser.client.url = "https://outside.example.test/"

    with pytest.raises(ValueError, match="origin scope"):
        await executor("click", ref=ref, profile="user-1")
    assert browser.clicks == []

    browser.client.url = "https://example.test/form"
    browser.client.new_target_url = "https://example.test/second"
    current.model_target = "target-2"
    with pytest.raises(ValueError, match="target scope"):
        await executor("click", ref=ref, profile="user-1")
    assert browser.clicks == []


async def test_direct_actions_use_the_model_target_client(tmp_path: Path) -> None:
    source = StepsDocument(
        recordingId="rec_scope",
        startUrl="https://example.test/form",
        steps=[
            SemanticStep(id="step_navigate", kind="open", url="https://example.test/form"),
            SemanticStep(id="step_press", kind="press", key="Enter"),
        ],
    )
    browser, current, _replays, executor, _audits = await prepare_executor(
        tmp_path, document=source
    )
    current.model_target = "target-2"
    browser.client.new_target_url = "https://example.test/second"
    executor.authorization.model_targets.add("target-2")
    writes = enable_target_scope_spy(browser)

    navigated = await executor("navigate", url="https://example.test/next", profile="user-1")
    pressed = await executor("press", key="Enter", profile="user-1")

    assert navigated.success and pressed.success
    actual_writes = [
        (method, target)
        for method, target in writes
        if method == "Page.navigate" or method == "Input.dispatchKeyEvent"
    ]
    assert actual_writes
    assert {target for _method, target in actual_writes} == {"cross-session-1"}


async def test_real_runner_switches_only_the_logical_replay_target(
    tmp_path: Path,
) -> None:
    source = StepsDocument(
        recordingId="rec_scope",
        startUrl="https://example.test/first",
        steps=[
            SemanticStep(
                id="step_new",
                kind="new_tab",
                url="https://example.test/second",
                target={"_targetId": "recorded-second"},
            ),
            SemanticStep(
                id="step_click",
                kind="click",
                target={
                    "_targetId": "recorded-second",
                    "role": "button",
                    "name": "Run once",
                },
            ),
        ],
    )
    browser, current, _replays, executor, _audits = await prepare_executor(
        tmp_path, document=source
    )
    enable_target_scope_spy(browser)

    async def new_tab(url: str, force_new: bool):
        assert force_new
        browser.client.new_target_url = url
        return tool_result("target-2")

    async def unexpected_switch(_tab_id: str):
        raise AssertionError("The user's visible tab must not be switched")

    browser.new_tab = new_tab  # type: ignore[method-assign]
    browser.switch_tab = unexpected_switch  # type: ignore[method-assign]
    runner = ReplayRunner(
        executor=executor,
        store=RecordingStore(RecordReplaySettings(base_dir=tmp_path)),
    )
    result = await runner.run("rec_scope", profile="user-1")

    assert result["status"] == "passed", result.get("error") or result["steps"]
    assert current.model_target == "target-2"
    assert len(browser.clicks) == 1
    assert "switch_tab" in executor.authorization.allowed_actions


async def test_deleted_recording_invalidates_pending_page_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _browser, current, replays, executor, _audits = await prepare_executor(tmp_path)
    dom = await executor("dom_tree", profile="user-1")
    ref = json.loads(dom.content)["elements"][0]["ref"]
    captured: dict[str, Any] = {}

    async def execute(
        _session: Any, _request: dict[str, Any], grant: str, **kwargs: Any
    ) -> dict[str, Any]:
        captured["grant"] = grant
        captured.update(kwargs)
        return {"outcome": {"ok": True, "target": {"role": "button"}}}

    monkeypatch.setattr("octop.infra.control.browser.webskill.replay.execute_action", execute)
    result = await executor("click", ref=ref, profile="user-1")
    assert result.success
    replays.cancel(executor.authorization.id)
    with pytest.raises(ValueError, match="invalid, stale or already used"):
        captured["consume_scoped_grant"](current.document, "click", {"ref": ref, "action": "click"})
    executor.store.delete("rec_scope", profile="user-1")
    with pytest.raises(ValueError, match="cancelled|deleted"):
        await executor("dom_tree", profile="user-1")


def route_recording(tmp_path: Path, step_count: int = 1) -> RecordingStore:
    store = RecordingStore(RecordReplaySettings(base_dir=tmp_path))
    store.create(
        RecordingManifest(
            recordingId="rec_route",
            createdAt="2026-11-19T00:00:00Z",
            endedAt="2026-11-19T00:01:00Z",
            status="stopped",
            profile="user-1",
        )
    )
    store.append_event("rec_route", {"type": "reviewed"})
    store.write_steps(
        "rec_route",
        StepsDocument(
            recordingId="rec_route",
            startUrl="https://example.test/form",
            steps=[
                SemanticStep(
                    id=f"step_{index}",
                    kind="click",
                    target={"role": "button", "name": "Run once"},
                )
                for index in range(1, step_count + 1)
            ],
        ),
    )
    return store


def patch_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    browser: FakeBrowser,
    *,
    step_count: int = 1,
) -> None:
    from octop_browser.settings import settings as browser_settings

    from octop.api.routers.browser import replay, webskill

    store = route_recording(tmp_path, step_count=step_count)
    registry = BrowserAgentRegistry()
    monkeypatch.setattr(browser_settings, "profiles_dir", tmp_path / "profiles")
    monkeypatch.setattr(replay, "browser_agent_registry", registry)
    monkeypatch.setattr(webskill, "browser_agent_registry", registry)
    monkeypatch.setattr(replay, "replay_registry", ReplayRegistry())
    monkeypatch.setattr(replay, "resolve_harness_session", resolver_for(browser))
    monkeypatch.setattr(replay, "RecordingStore", lambda: store)
    monkeypatch.setattr("octop_browser.record.replay.RecordingStore", lambda: store)
    monkeypatch.setattr("octop_browser.record.store.RecordingStore", lambda: store)


async def test_replay_route_runs_real_runner_and_does_not_leak_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.app import octop_client
    from tests.support.auth import auth_header, bootstrap_admin

    from octop.api.routers.browser import replay

    browser = FakeBrowser()
    patch_route(monkeypatch, tmp_path, browser)
    reports: list[dict[str, Any]] = []
    actual_run = replay.ReplayRunner.run

    async def observe_run(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        report = await actual_run(self, *args, **kwargs)
        reports.append(report)
        return report

    monkeypatch.setattr(replay.ReplayRunner, "run", observe_run)
    origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    async with octop_client(tmp_path / "home") as (client, server):
        await bootstrap_admin(client, tmp_path / "home")
        auth = await auth_header(client)
        created = await client.post(
            "/api/browser-agent/sessions", headers={**auth, **origin}, json={}
        )
        session_id = created.json()["sessionId"]
        response = await client.post(
            f"/api/browser-agent/sessions/{session_id}/replays",
            headers={**auth, **origin},
            json={"recordingId": "rec_route", "inputs": {"name": "not-public"}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "passed", (body, reports)
        assert body["steps"] == [{"id": "step_1", "kind": "click", "status": "passed"}]
        assert len(browser.clicks) == 1
        assert "not-public" not in response.text
        assert "reportPath" not in body
        assert "inputs" not in body
        assert server.services is not None
        started = server.services.audit_repo.query(action="webskill.replay.started")
        actions = server.services.audit_repo.query(action="webskill.replay.action")
        assert len(started) == len(actions) == 1
        assert "not-public" not in (started[0].payload or "") + (actions[0].payload or "")
        missing = await client.delete(
            f"/api/browser-agent/replays/{body['replayId']}",
            headers={**auth, **origin},
        )
        assert missing.status_code == 404


async def test_replay_route_enforces_origin_permission_and_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.app import octop_client
    from tests.support.auth import auth_header, bootstrap_admin, create_user

    browser = FakeBrowser()
    patch_route(monkeypatch, tmp_path, browser)
    origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    async with octop_client(tmp_path / "home") as (client, _server):
        await bootstrap_admin(client, tmp_path / "home")
        admin = await auth_header(client)
        created = await client.post(
            "/api/browser-agent/sessions", headers={**admin, **origin}, json={}
        )
        session_id = created.json()["sessionId"]
        denied_origin = await client.post(
            f"/api/browser-agent/sessions/{session_id}/replays",
            headers={
                **admin,
                "Origin": "https://attacker.invalid",
                "Sec-Fetch-Site": "same-origin",
            },
            json={"recordingId": "rec_route"},
        )
        assert denied_origin.status_code == 403
        without_browser = await create_user(
            client, admin, username="replay-no-browser", permissions=[]
        )
        other = await create_user(client, admin, username="replay-other", permissions=["browser"])
        for auth, status in ((without_browser, 403), (other, 404)):
            denied = await client.post(
                f"/api/browser-agent/sessions/{session_id}/replays",
                headers={**auth, **origin},
                json={"recordingId": "rec_route"},
            )
            assert denied.status_code == status
        assert browser.clicks == []


async def test_cancel_allows_no_more_than_the_in_flight_click(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.app import octop_client
    from tests.support.auth import auth_header, bootstrap_admin
    from tests.unit.api.test_webskill_page_agent import FakeClient

    from octop.api.routers.browser import replay

    class BlockingClient(FakeClient):
        def __init__(self, clicks: list[dict[str, Any]]) -> None:
            super().__init__(clicks)
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send(self, method: str, params: dict[str, Any] | None = None, **kwargs: Any):
            if (
                method == "Input.dispatchMouseEvent"
                and params
                and params.get("type") == "mouseReleased"
            ):
                self.started.set()
                await self.release.wait()
            return await super().send(method, params, **kwargs)

    class BlockingBrowser(FakeBrowser):
        def __init__(self) -> None:
            super().__init__()
            self.client = BlockingClient(self.clicks)
            self._internal = SimpleNamespace(client=self.client, ref_cache=self.ref_cache)

    browser = BlockingBrowser()
    patch_route(monkeypatch, tmp_path, browser, step_count=2)
    store = RecordingStore(RecordReplaySettings(base_dir=tmp_path))
    monkeypatch.setattr(replay, "RecordingStore", lambda: store)
    monkeypatch.setattr("octop_browser.record.replay.RecordingStore", lambda: store)
    monkeypatch.setattr("octop_browser.record.store.RecordingStore", lambda: store)
    origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    async with octop_client(tmp_path / "home") as (client, server):
        await bootstrap_admin(client, tmp_path / "home")
        auth = await auth_header(client)
        created = await client.post(
            "/api/browser-agent/sessions", headers={**auth, **origin}, json={}
        )
        session_id = created.json()["sessionId"]
        task = asyncio.create_task(
            client.post(
                f"/api/browser-agent/sessions/{session_id}/replays",
                headers={**auth, **origin},
                json={"recordingId": "rec_route"},
            )
        )
        await asyncio.wait_for(browser.client.started.wait(), timeout=5)
        cancelled = await client.delete(
            "/api/browser-agent/replays/current", headers={**auth, **origin}
        )
        assert cancelled.status_code == 404
        current = replay.replay_registry._runs
        run_id = next(iter(current))
        cancelled = await client.delete(
            f"/api/browser-agent/replays/{run_id}", headers={**auth, **origin}
        )
        assert cancelled.status_code == 200
        browser.client.release.set()
        response = await asyncio.wait_for(task, timeout=10)
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert len(browser.clicks) == 1
        assert server.services is not None
        audits = server.services.audit_repo.query(action="webskill.replay.cancelled")
        assert len(audits) == 1


async def test_recording_delete_removes_sources_and_blocks_draft_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.app import octop_client
    from tests.support.auth import auth_header, bootstrap_admin, create_user

    patch_route(monkeypatch, tmp_path, FakeBrowser())
    store = RecordingStore(RecordReplaySettings(base_dir=tmp_path))
    monkeypatch.setattr(
        "octop.infra.control.browser.webskill.skill_drafts.RecordingStore",
        lambda: store,
    )
    report = store.recording_dir("rec_route") / "replay-reports" / "private.json"
    report.write_text('{"inputs":{"password":"private-input"}}', encoding="utf-8")
    origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    async with octop_client(tmp_path / "home") as (client, server):
        await bootstrap_admin(client, tmp_path / "home")
        admin = await auth_header(client)
        created = await client.post(
            "/api/browser-agent/sessions", headers={**admin, **origin}, json={}
        )
        session_id = created.json()["sessionId"]
        denied = await client.delete(
            "/api/browser-agent/recordings/rec_route",
            headers={**admin, "Origin": "https://attacker.invalid"},
        )
        assert denied.status_code == 403
        other = await create_user(client, admin, username="delete-other", permissions=["browser"])
        denied = await client.delete(
            "/api/browser-agent/recordings/rec_route",
            headers={**other, **origin},
        )
        assert denied.status_code == 404
        deleted = await client.delete(
            "/api/browser-agent/recordings/rec_route",
            headers={**admin, **origin},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"deleted": True, "recordingId": "rec_route"}
        assert not report.exists()
        assert not store.recording_dir("rec_route").exists()
        for path in (
            f"/api/browser-agent/sessions/{session_id}/skill-drafts",
            f"/api/browser-agent/sessions/{session_id}/replays",
        ):
            unavailable = await client.post(
                path,
                headers={**admin, **origin},
                json={"recordingId": "rec_route"},
            )
            assert unavailable.status_code == 404
        assert server.services is not None
        audits = server.services.audit_repo.query(action="webskill.recording.deleted")
        assert len(audits) == 1
        assert "private-input" not in (audits[0].payload or "")
        tombstones = (tmp_path / "deletion-tombstones.jsonl").read_text(encoding="utf-8")
        assert "private-input" not in tombstones
