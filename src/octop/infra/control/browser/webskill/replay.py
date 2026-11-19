"""Scoped, short-lived authorization for server-orchestrated recording replay."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from octop_browser.models import ActionMetrics, ToolResult
from octop_browser.record.models import StepsDocument

from octop.infra.control.browser.webskill.lease import profile_lease
from octop.infra.control.browser.webskill.page_agent import (
    document_identity,
    execute_action,
    issue_action_grant,
    read_page,
    resolve_browser,
)
from octop.infra.control.browser.webskill.sessions import (
    BrowserAgentRegistry,
    BrowserAgentSession,
    DocumentIdentity,
)
from octop.infra.control.browser.webskill.targets import admit_model_target

REPLAY_TTL_SECONDS = 300.0
DIRECT_ACTIONS = frozenset({"navigate", "new_tab", "press", "switch_tab"})
PAGE_ACTIONS = frozenset({"click", "fill", "select"})


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _payload_digest(value: dict[str, Any]) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _origin(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.hostname is None:
            return None
        return f"{parts.scheme}://{parts.hostname}:{parts.port or (443 if parts.scheme == 'https' else 80)}"
    except ValueError:
        return None


@dataclass
class ReplayAuthorization:
    id: str
    session_id: str
    user_id: int
    profile: str
    recording_id: str
    source_digest: str
    initial_target: str
    allowed_actions: frozenset[str]
    allowed_origins: frozenset[str]
    max_actions: int
    expires_at: float
    active: bool = True
    actions_issued: int = 0
    model_targets: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ReplayGrant:
    replay_id: str
    session_id: str
    document_key: str
    target_id: str
    action: str
    action_index: int
    payload_digest: str
    expires_at: float


class ReplayRegistry:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._runs: dict[str, ReplayAuthorization] = {}
        self._grants: dict[str, ReplayGrant] = {}
        self._deleted_recordings: set[tuple[int, str]] = set()

    def authorize(
        self,
        session: BrowserAgentSession,
        steps: StepsDocument,
        *,
        source_digest: str,
    ) -> ReplayAuthorization:
        if (session.user_id, steps.recording_id) in self._deleted_recordings:
            raise ValueError("The recording has been deleted")
        if not steps.steps:
            raise ValueError("A recording without steps cannot be replayed")
        target = session.model_target or session.base_target_id
        if target is None:
            raise ValueError("Perceive the starting page before replay authorization")
        actions: set[str] = set()
        for step in steps.steps:
            if step.kind in {"open", "navigate"}:
                actions.add("navigate")
            elif step.kind in {"click", "fill", "select", "new_tab", "press"}:
                actions.add(step.kind)
        origins = {
            origin
            for url in [steps.start_url, *(step.url for step in steps.steps)]
            if (origin := _origin(url)) is not None
        }
        self.cancel_session(session.id)
        run = ReplayAuthorization(
            id=secrets.token_urlsafe(24),
            session_id=session.id,
            user_id=session.user_id,
            profile=session.profile,
            recording_id=steps.recording_id,
            source_digest=source_digest,
            initial_target=target,
            allowed_actions=frozenset(actions),
            allowed_origins=frozenset(origins),
            max_actions=len(steps.steps) * 3,
            expires_at=self._now() + REPLAY_TTL_SECONDS,
            model_targets={target, *session.model_targets},
        )
        self._runs[run.id] = run
        return run

    def get(self, run_id: str, session: BrowserAgentSession) -> ReplayAuthorization:
        run = self._runs.get(run_id)
        if (
            run is None
            or not session.active
            or run.session_id != session.id
            or run.user_id != session.user_id
            or run.profile != session.profile
        ):
            raise ValueError("Replay authorization is not available")
        if not run.active or run.expires_at <= self._now():
            self.cancel(run.id)
            raise ValueError("Replay authorization has expired or been cancelled")
        return run

    def next_action(self, run: ReplayAuthorization, action: str) -> int:
        if action not in run.allowed_actions:
            raise ValueError("The action is outside the authorized recording scope")
        if run.actions_issued >= run.max_actions:
            raise ValueError("The replay action limit has been reached")
        run.actions_issued += 1
        return run.actions_issued

    def issue_grant(
        self,
        run: ReplayAuthorization,
        *,
        document: DocumentIdentity,
        action: str,
        payload: dict[str, Any],
    ) -> tuple[str, int]:
        action_index = self.next_action(run, action)
        self.revoke_grants(replay_id=run.id)
        token = secrets.token_urlsafe(32)
        self._grants[_digest(token)] = ReplayGrant(
            replay_id=run.id,
            session_id=run.session_id,
            document_key=document.key,
            target_id=document.target_id,
            action=action,
            action_index=action_index,
            payload_digest=_payload_digest(payload),
            expires_at=self._now() + 10,
        )
        return token, action_index

    def consume_grant(
        self,
        run: ReplayAuthorization,
        token: str,
        *,
        document: DocumentIdentity,
        action: str,
        payload: dict[str, Any],
    ) -> ReplayGrant:
        grant = self._grants.pop(_digest(token), None)
        if (
            grant is None
            or grant.expires_at <= self._now()
            or grant.replay_id != run.id
            or grant.session_id != run.session_id
            or grant.document_key != document.key
            or grant.target_id != document.target_id
            or grant.action != action
            or grant.payload_digest != _payload_digest(payload)
        ):
            raise ValueError("The replay action grant is invalid, stale or already used")
        return grant

    def cancel(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run is not None:
            run.active = False
        self.revoke_grants(replay_id=run_id)

    def cancel_session(self, session_id: str) -> None:
        for run in list(self._runs.values()):
            if run.session_id == session_id:
                self.cancel(run.id)

    def cancel_recording(self, recording_id: str, user_id: int) -> None:
        self._deleted_recordings.add((user_id, recording_id))
        for run in list(self._runs.values()):
            if run.user_id == user_id and run.recording_id == recording_id:
                self.cancel(run.id)

    def cancel_for_user(self, run_id: str, user_id: int) -> ReplayAuthorization | None:
        run = self._runs.get(run_id)
        if run is None or run.user_id != user_id:
            return None
        self.cancel(run.id)
        return run

    def finish(self, run_id: str) -> None:
        self.cancel(run_id)
        self._runs.pop(run_id, None)

    def revoke_grants(self, *, replay_id: str) -> None:
        self._grants = {
            key: grant for key, grant in self._grants.items() if grant.replay_id != replay_id
        }


class ScopedReplayExecutor:
    def __init__(
        self,
        *,
        session: BrowserAgentSession,
        authorization: ReplayAuthorization,
        replays: ReplayRegistry,
        resolver: Callable[..., Awaitable[Any]],
        registry: BrowserAgentRegistry,
        write_gate: Callable[[], bool],
        audit: Callable[[str, int, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.session = session
        self.authorization = authorization
        self.replays = replays
        self.resolver = resolver
        self.registry = registry
        self.write_gate = write_gate
        self.audit = audit

    def _assert_active(self) -> ReplayAuthorization:
        run = self.replays.get(self.authorization.id, self.session)
        if not self.write_gate():
            self.replays.cancel(run.id)
            raise ValueError("User control took precedence; replay was cancelled")
        return run

    @staticmethod
    def _result(
        action: str, success: bool, content: str = "", error: str | None = None
    ) -> ToolResult:
        return ToolResult(
            success=success,
            content=content,
            error=error,
            metrics=ActionMetrics(
                action=action, duration_ms=0, dom_nodes_scanned=0, estimated_tokens=0
            ),
        )

    async def _read_dom(self) -> ToolResult:
        self._assert_active()
        reply = await read_page(
            self.session,
            {"scope": {"include": ["body"]}},
            resolver=self.resolver,
            registry=self.registry,
        )
        elements = [
            {
                "ref": target["ref"],
                "role": target["role"],
                "accessibleName": target.get("name", ""),
            }
            for target in reply["targets"]
        ]
        return self._result("dom_tree", True, json.dumps({"elements": elements}))

    async def _page_action(self, action: str, kwargs: dict[str, Any]) -> ToolResult:
        run = self._assert_active()
        request: dict[str, Any] = {"ref": kwargs.get("ref"), "action": action}
        if action in {"fill", "select"}:
            value = kwargs.get("text") if action == "fill" else kwargs.get("value")
            if not isinstance(value, str):
                return self._result(action, False, error="A replay input value is required")
            request["value"] = value
        action_index = self.replays.next_action(run, action)
        document_key = self.session.document.key if self.session.document is not None else ""
        grant = await issue_action_grant(
            self.session,
            request,
            registry=self.registry,
            resolver=self.resolver,
            write_gate=self.write_gate,
        )
        self._assert_active()
        reply = await execute_action(
            self.session,
            request,
            grant,
            registry=self.registry,
            resolver=self.resolver,
            write_gate=self.write_gate,
        )
        outcome = reply["outcome"]
        if self.audit is not None:
            await self.audit(
                action,
                action_index,
                {
                    "document": _digest(document_key),
                    "target": outcome.get("target", {}).get("role", "unknown"),
                    "ok": outcome.get("ok") is True,
                    "grantDigest": _digest(grant),
                },
            )
        if outcome.get("ok") is not True:
            return self._result(
                action,
                False,
                error=str(outcome.get("reason") or "The scoped page action failed")[:500],
            )
        return self._result(action, True)

    async def _direct_action(self, action: str, kwargs: dict[str, Any]) -> ToolResult:
        run = self._assert_active()
        if action not in DIRECT_ACTIONS:
            return self._result(
                action,
                False,
                error="The recorded action is not supported by scoped replay",
            )
        if action == "switch_tab":
            target = kwargs.get("tab_id")
            if not isinstance(target, str) or target not in run.model_targets:
                return self._result(action, False, error="The replay target is outside its workset")
        browser = await resolve_browser(self.session, self.resolver)
        document = await document_identity(self.session, browser)
        payload = self._direct_payload(action, kwargs)
        if action in {"navigate", "new_tab"} and _origin(payload["url"]) not in run.allowed_origins:
            return self._result(
                action,
                False,
                error="The replay URL is outside the authorized origin scope",
            )
        async with profile_lease(self.session.profile):
            self._assert_active()
            grant, action_index = self.replays.issue_grant(
                run, document=document, action=action, payload=payload
            )
            current = await document_identity(self.session, browser)
            self.replays.consume_grant(run, grant, document=current, action=action, payload=payload)
            self._assert_active()
            method = getattr(browser, action)
            result: ToolResult = await method(**payload)
        if self.audit is not None:
            await self.audit(
                action,
                action_index,
                {
                    "document": _digest(document.key),
                    "target": document.target_id,
                    "ok": result.success,
                    "grantDigest": _digest(grant),
                },
            )
        if result.success and action in {"new_tab", "switch_tab"}:
            target = result.content if action == "new_tab" else payload["tab_id"]
            if not isinstance(target, str) or not admit_model_target(self.session, target):
                return self._result(
                    action,
                    False,
                    error="The new replay target exceeds the authorized workset",
                )
            run.model_targets.add(target)
            self.session.document = None
            self.session.refs.clear()
            self.registry.revoke_grants(session_id=self.session.id)
        return result

    @staticmethod
    def _direct_payload(action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if action in {"navigate", "new_tab"}:
            url = kwargs.get("url")
            if not isinstance(url, str) or len(url) > 2048:
                raise ValueError("A valid replay URL is required")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("Only credentialess HTTP(S) replay URLs are allowed")
            return {"url": url, **({"force_new": True} if action == "new_tab" else {})}
        if action == "press":
            key = kwargs.get("key")
            if not isinstance(key, str) or len(key) > 64:
                raise ValueError("A valid replay key is required")
            return {"key": key}
        if action == "switch_tab":
            return {"tab_id": kwargs["tab_id"]}
        raise ValueError("Unsupported direct replay action")

    async def __call__(self, action: str, **kwargs: Any) -> ToolResult:
        if action == "dom_tree":
            return await self._read_dom()
        if action == "wait":
            self._assert_active()
            browser = await resolve_browser(self.session, self.resolver)
            allowed = {
                key: value
                for key, value in kwargs.items()
                if key in {"url_contains", "text", "selector", "timeout_ms"}
            }
            return await browser.wait(**allowed)
        if action in PAGE_ACTIONS:
            return await self._page_action(action, kwargs)
        return await self._direct_action(action, kwargs)
