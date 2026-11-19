"""Deterministic WebSkill candidates from user-owned browser recordings."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from octop_browser.record.models import PrivacySettings, StepsDocument
from octop_browser.record.privacy import redact_url
from octop_browser.record.store import RecordingStore

from octop.infra.control.browser.webskill.sessions import BrowserAgentError, BrowserAgentSession

MAX_DRAFT_BYTES = 65536
MAX_RECORDING_STEPS = 200
RECORDING_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
SOURCE_FILES = ("manifest.json", "events.jsonl", "steps.json")


def canonical_json(value: Any) -> str:
    if value is None or isinstance(value, (bool, str)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("Non-finite numbers are not valid JSON")
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return (
            "{"
            + ",".join(
                f"{json.dumps(key, ensure_ascii=False)}:{canonical_json(value[key])}"
                for key in sorted(value)
            )
            + "}"
        )
    raise ValueError("Only JSON values can be canonicalized")


def _digest_draft(draft: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json(draft).encode("utf-8")).hexdigest()


def _source_digest(store: RecordingStore, recording_id: str) -> str:
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        digest.update(name.encode("ascii") + b"\0")
        path: Path = getattr(store, f"{name.split('.')[0]}_path")(recording_id)
        with path.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _text(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(value.split())[:160]
    return cleaned.replace("`", "\\`") or fallback


def _safe_url(value: str | None) -> str:
    if not value:
        return ""
    redacted = redact_url(value, PrivacySettings(url_query_policy="mask-all"))
    try:
        parts = urlsplit(redacted)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        netloc = host + (f":{parts.port}" if parts.port is not None else "")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))[:2048]
    except ValueError:
        return ""


def _target_label(step: dict[str, Any]) -> str:
    target = step.get("target")
    if not isinstance(target, dict):
        return "the recorded element"
    for key in ("accessibleName", "label", "name", "text", "placeholder"):
        if target.get(key):
            return _text(target[key], "the recorded element")
    return "the recorded element"


def _step_instruction(step: dict[str, Any], index: int, start_url: str) -> str:
    kind = step.get("kind")
    if kind in {"open", "navigate", "new_tab"}:
        url = _safe_url(step.get("url")) or start_url
        if url:
            return f"{index}. Open `{url}` using the host navigation control."
        return f"{index}. Ask the user to open the reviewed start page."
    if kind == "click":
        return f"{index}. Click `{_target_label(step)}` using a fresh page reference."
    if kind in {"fill", "select", "set_checked"}:
        return (
            f"{index}. Update `{_target_label(step)}` with a value confirmed by the user; "
            "never reuse a recorded secret."
        )
    if kind == "press":
        key = step.get("key")
        key_text = (
            key
            if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9+ -]{1,32}", key)
            else "the recorded key"
        )
        return f"{index}. Press `{key_text}`."
    if kind == "scroll_to":
        return f"{index}. Scroll using a fresh page reference and the user's requested direction."
    return f"{index}. Review the recorded `{_text(kind, 'step')}` action with the user before continuing."


def _draft_name(steps: StepsDocument) -> str:
    try:
        hostname = urlsplit(steps.start_url).hostname or "browser-workflow"
    except ValueError:
        hostname = "browser-workflow"
    suffix = re.sub(r"[^a-z0-9]+", "-", hostname.lower()).strip("-") or "browser-workflow"
    return f"recorded-{suffix}"[:64].rstrip("-")


def _read_recording(
    session: BrowserAgentSession, recording_id: str | None, store: RecordingStore
) -> StepsDocument:
    store.enforce_retention(session.profile)
    if recording_id is None:
        owned = [item for item in store.list_recordings() if item.profile == session.profile]
        if not owned:
            raise BrowserAgentError(
                "RECORDING_NOT_FOUND", "No owned recording is available", status=404
            )
        recording_id = max(owned, key=lambda item: item.created_at).recording_id
    if not RECORDING_ID_PATTERN.fullmatch(recording_id):
        raise BrowserAgentError("RECORDING_NOT_FOUND", "Recording not found", status=404)
    try:
        manifest = store.read_manifest(recording_id)
        if manifest.profile != session.profile:
            raise FileNotFoundError(recording_id)
        if manifest.status != "stopped":
            raise BrowserAgentError(
                "RECORDING_NOT_READY",
                "Stop the recording before creating a skill draft",
                status=409,
            )
        steps = store.read_steps(recording_id)
    except (FileNotFoundError, ValueError, OSError) as error:
        raise BrowserAgentError("RECORDING_NOT_FOUND", "Recording not found", status=404) from error
    if steps.recording_id != recording_id or manifest.recording_id != recording_id:
        raise BrowserAgentError("RECORDING_NOT_FOUND", "Recording not found", status=404)
    if not steps.steps:
        raise BrowserAgentError("RECORDING_EMPTY", "The recording contains no steps", status=409)
    if len(steps.steps) > MAX_RECORDING_STEPS:
        raise BrowserAgentError(
            "SKILL_DRAFT_TOO_LARGE", "The recording exceeds the skill draft step limit", status=409
        )
    return steps


def create_recorded_skill_draft(
    session: BrowserAgentSession,
    recording_id: str | None = None,
    *,
    store: RecordingStore | None = None,
) -> dict[str, Any]:
    store = store or RecordingStore()
    steps = _read_recording(session, recording_id, store)
    start_url = _safe_url(steps.start_url)
    name = _draft_name(steps)
    description = (
        "Repeat a reviewed browser recording with fresh perception and user-confirmed actions."
    )
    workflow = [
        _step_instruction(step.model_dump(), index, start_url)
        for index, step in enumerate(steps.steps, start=1)
    ]
    content = f"""---
name: {name}
description: {description}
---

# Recorded browser workflow

## When to use

Use this skill only after the user asks to repeat the reviewed workflow. Ask for any required values; recorded values are not included.

## Workflow

{chr(10).join(workflow)}

## Safety and verification

- Read the current page with `perceive_page` before every action.
- Use only fresh references accepted by `act_on_page`; never reuse recorded CDP references, selectors, or coordinates.
- Stop when the page or target is ambiguous. Do not claim success without observing the expected result.
- This document is not a ReplayRunner input and does not authorize execution by itself.
"""
    if len(content.encode("utf-8")) > MAX_DRAFT_BYTES:
        raise BrowserAgentError(
            "SKILL_DRAFT_TOO_LARGE", "The generated skill draft is too large", status=409
        )
    draft = {"name": name, "description": description, "content": content}
    return {
        "draft": draft,
        "provenance": {
            "recordingId": steps.recording_id,
            "sourceDigest": _source_digest(store, steps.recording_id),
            "draftDigest": _digest_draft(draft),
            "evidence": "recording",
        },
    }
