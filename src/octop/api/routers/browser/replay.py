"""Restricted server-orchestrated recording replay endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from octop_browser.record.replay import ReplayRunner
from octop_browser.record.store import RecordingStore
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import require_permission
from octop.api.routers.browser.harness import resolve_harness_session
from octop.api.routers.browser.record_replay import _require_owned_recording
from octop.api.routers.browser.webskill import (
    _audit,
    _http_error,
    check_request_origin,
)
from octop.infra.control.browser.webskill.ownership import control_owner_for
from octop.infra.control.browser.webskill.page_agent import read_page
from octop.infra.control.browser.webskill.replay import ReplayRegistry, ScopedReplayExecutor
from octop.infra.control.browser.webskill.sessions import (
    BrowserAgentError,
    browser_agent_registry,
)
from octop.infra.control.browser.webskill.skill_drafts import (
    RECORDING_ID_PATTERN,
    _source_digest,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.browser_media import user_browser_profile

router = APIRouter()
replay_registry = ReplayRegistry()


class ReplayStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_id: str = Field(
        alias="recordingId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
    )
    inputs: dict[str, str] = Field(default_factory=dict)


def _read_owned_steps(recording_id: str, profile: str) -> tuple[Any, Any, RecordingStore]:
    manifest = _require_owned_recording(recording_id, profile)
    if manifest.status != "stopped":
        raise BrowserAgentError(
            "RECORDING_NOT_READY", "Stop the recording before replay", status=409
        )
    store = RecordingStore()
    try:
        return manifest, store.read_steps(recording_id), store
    except (OSError, ValueError) as error:
        raise BrowserAgentError("RECORDING_NOT_FOUND", "Recording not found", status=404) from error


def _public_report(run_id: str, recording_id: str, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "replayId": run_id,
        "recordingId": recording_id,
        "status": "passed" if report.get("status") == "passed" else "failed",
        "steps": [
            {
                "id": str(step.get("id", ""))[:128],
                "kind": str(step.get("kind", ""))[:64],
                "status": str(step.get("status", ""))[:32],
            }
            for step in report.get("steps", [])
            if isinstance(step, dict)
        ],
        **({"startedAt": report["startedAt"]} if isinstance(report.get("startedAt"), str) else {}),
        **({"endedAt": report["endedAt"]} if isinstance(report.get("endedAt"), str) else {}),
        **({"errorCode": "REPLAY_FAILED"} if report.get("status") != "passed" else {}),
    }


@router.post(
    "/browser-agent/sessions/{session_id}/replays",
    summary="Replay an owned recording within one server-side authorization scope",
)
async def start_replay(
    session_id: str,
    body: ReplayStartBody,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
        if len(body.inputs) > 50 or any(
            len(key) > 128 or len(value) > 32768 for key, value in body.inputs.items()
        ):
            raise BrowserAgentError(
                "REPLAY_INPUT_INVALID", "Replay inputs exceed the allowed limits", status=400
            )
        session = browser_agent_registry.get(session_id, user.id)
        profile = user_browser_profile(user.id)
        deleted = await asyncio.to_thread(RecordingStore().enforce_retention, profile)
        for recording_id in deleted:
            replay_registry.cancel_recording(recording_id, user.id)
        manifest, steps, store = await asyncio.to_thread(
            _read_owned_steps, body.recording_id, profile
        )
        source_digest = await asyncio.to_thread(_source_digest, store, body.recording_id)
        await read_page(
            session,
            {"scope": {"include": ["body"]}},
            resolver=resolve_harness_session,
            registry=browser_agent_registry,
        )
        authorization = replay_registry.authorize(session, steps, source_digest=source_digest)

        async def audit_action(action: str, index: int, data: dict[str, Any]) -> None:
            await asyncio.to_thread(
                _audit,
                request,
                user.id,
                "webskill.replay.action",
                session.id,
                {
                    "replayId": authorization.id,
                    "recordingId": body.recording_id,
                    "action": action,
                    "actionIndex": index,
                    **data,
                },
            )

        executor = ScopedReplayExecutor(
            session=session,
            authorization=authorization,
            replays=replay_registry,
            resolver=resolve_harness_session,
            registry=browser_agent_registry,
            write_gate=lambda: control_owner_for(session.profile) != "user",
            audit=audit_action,
        )
        await asyncio.to_thread(
            _audit,
            request,
            user.id,
            "webskill.replay.started",
            session.id,
            {
                "replayId": authorization.id,
                "recordingId": body.recording_id,
                "sourceDigest": source_digest,
                "initialTarget": authorization.initial_target,
                "maxActions": authorization.max_actions,
                "expiresIn": 300,
                "manifestStatus": manifest.status,
            },
        )
        try:
            report = await ReplayRunner(executor=executor).run(
                body.recording_id, profile=profile, inputs=body.inputs
            )
            return _public_report(authorization.id, body.recording_id, report)
        finally:
            replay_registry.finish(authorization.id)
    except BrowserAgentError as error:
        raise _http_error(error) from error
    except ValueError as error:
        raise OctopError(ErrorCode.RESOURCE_NOT_ALLOWED, str(error), status=409) from error


@router.delete(
    "/browser-agent/replays/{replay_id}",
    summary="Cancel replay without executing any subsequent steps",
)
async def cancel_replay(
    replay_id: str,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, bool]:
    check_request_origin(request)
    run = replay_registry.cancel_for_user(replay_id, user.id)
    if run is None:
        raise OctopError(ErrorCode.NOT_FOUND, "Replay not found", status=404)
    await asyncio.to_thread(
        _audit,
        request,
        user.id,
        "webskill.replay.cancelled",
        run.session_id,
        {"replayId": run.id, "recordingId": run.recording_id},
    )
    return {"cancelled": True}


@router.delete(
    "/browser-agent/recordings/{recording_id}",
    summary="Delete an owned recording, its source data and replay reports",
)
async def delete_recording(
    recording_id: str,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
    except BrowserAgentError as error:
        raise _http_error(error) from error
    if RECORDING_ID_PATTERN.fullmatch(recording_id) is None:
        raise OctopError(ErrorCode.NOT_FOUND, "Recording not found", status=404)
    profile = user_browser_profile(user.id)
    try:
        manifest = await asyncio.to_thread(_require_owned_recording, recording_id, profile)
        if manifest.status == "recording":
            raise BrowserAgentError(
                "RECORDING_NOT_READY", "Stop the recording before deletion", status=409
            )
        replay_registry.cancel_recording(recording_id, user.id)
        await asyncio.to_thread(
            RecordingStore().delete, recording_id, profile=profile, reason="user"
        )
    except BrowserAgentError as error:
        raise _http_error(error) from error
    except (OSError, ValueError) as error:
        raise BrowserAgentError("RECORDING_NOT_FOUND", "Recording not found", status=404) from error
    await asyncio.to_thread(
        _audit,
        request,
        user.id,
        "webskill.recording.deleted",
        f"recording:{recording_id}",
        {"recordingId": recording_id, "artifactsDeleted": True},
    )
    return {"deleted": True, "recordingId": recording_id}
