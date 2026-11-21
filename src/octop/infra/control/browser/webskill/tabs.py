"""Model-side tab workset over the existing WebSkill browser session.

The model only sees opaque handles. Target IDs never leave the host, and
focusing a tab never calls browser.switch_tab: it only moves the logical
model_target pointer, so the user's visible page is unchanged.
"""

from __future__ import annotations

import secrets
from typing import Any

from octop_browser.session import BrowserSession

from .page_agent import resolve_browser
from .sessions import BrowserAgentError, BrowserAgentSession
from .targets import recover_target_from_tabs

TAB_HANDLE_PREFIX = "tab_"
MAX_HANDLE_LEN = 80


def _validate_handle(handle: str) -> str:
    if (
        not isinstance(handle, str)
        or not handle.startswith(TAB_HANDLE_PREFIX)
        or len(handle) > MAX_HANDLE_LEN
        or not all(ch.isalnum() or ch in "_-" for ch in handle)
    ):
        raise BrowserAgentError("PAGE_TAB_UNKNOWN", "The page handle is unknown")
    return handle


def handle_for_target(session: BrowserAgentSession, target_id: str) -> str:
    for handle, target in session.tab_handles.items():
        if target == target_id:
            return handle
    handle = f"{TAB_HANDLE_PREFIX}{secrets.token_urlsafe(18)}"
    session.tab_handles[handle] = target_id
    return handle


def _target_for_handle(session: BrowserAgentSession, handle: str) -> str:
    _validate_handle(handle)
    target = session.tab_handles.get(handle)
    if target is None:
        raise BrowserAgentError("PAGE_TAB_UNKNOWN", "The page handle is unknown")
    return target


async def _tabs_metadata(browser: BrowserSession) -> dict[str, dict[str, Any]]:
    result = await browser.list_tabs()
    if not result.success:
        raise BrowserAgentError("PAGE_TAB_UNKNOWN", "The browser target list is unavailable")
    value = (result.metadata or {}).get("tabs", [])
    if not isinstance(value, list):
        raise BrowserAgentError("PAGE_TAB_UNKNOWN", "The browser target list is unavailable")
    tabs: dict[str, dict[str, Any]] = {}
    for tab in value:
        if isinstance(tab, dict) and isinstance(tab.get("tab_id"), str):
            tabs[str(tab["tab_id"])] = tab
    return tabs


def _ensure_base(session: BrowserAgentSession, tabs: dict[str, dict[str, Any]]) -> str:
    if session.base_target_id is None or session.base_target_id not in tabs:
        active = next((tab for tab in tabs.values() if tab.get("active") is True), None)
        target = active.get("tab_id") if active is not None else None
        if not isinstance(target, str) or target not in tabs:
            target = next(iter(tabs), None)
        if not isinstance(target, str):
            raise BrowserAgentError("PAGE_TAB_UNKNOWN", "No page target is available")
        previous_base = session.base_target_id
        session.base_target_id = target
        if previous_base is not None:
            session.model_targets.discard(previous_base)
            for handle, mapped in list(session.tab_handles.items()):
                if mapped == previous_base:
                    session.tab_handles.pop(handle, None)
        if session.model_target not in tabs:
            session.model_target = target
        session.document = None
        session.refs.clear()
    if session.base_target_id not in session.model_targets:
        session.model_targets.add(session.base_target_id)
    handle_for_target(session, session.base_target_id)
    return session.base_target_id


def _workset_targets(session: BrowserAgentSession) -> list[str]:
    base = session.base_target_id
    if base is None:
        return []
    return [base, *sorted(session.model_targets - {base})]


async def list_workset_tabs(
    session: BrowserAgentSession,
    *,
    resolver: Any,
) -> list[dict[str, Any]]:
    browser = await resolve_browser(session, resolver)
    tabs = await _tabs_metadata(browser)
    _ensure_base(session, tabs)
    # A user may close the model target outside `close_page_tab`; the tab list
    # itself must not be a dead end. Follow the active live tab and refresh refs.
    recover_target_from_tabs(session, tabs)
    output: list[dict[str, Any]] = []
    for target in _workset_targets(session):
        metadata = tabs.get(target)
        if metadata is None:
            session.model_targets.discard(target)
            continue
        handle = handle_for_target(session, target)
        output.append(
            {
                "handle": handle,
                "title": str(metadata.get("title") or "")[:300],
                "url": str(metadata.get("url") or "")[:2048],
                "active": bool(metadata.get("active")),
            }
        )
    if not output:
        raise BrowserAgentError("PAGE_TAB_UNKNOWN", "No page target is available")
    return output


async def focus_workset_tab(
    session: BrowserAgentSession,
    handle: str,
    *,
    resolver: Any,
) -> dict[str, Any]:
    browser = await resolve_browser(session, resolver)
    tabs = await _tabs_metadata(browser)
    _ensure_base(session, tabs)
    target = _target_for_handle(session, handle)
    if target not in tabs or target not in session.model_targets:
        session.tab_handles.pop(handle, None)
        session.model_targets.discard(target)
        raise BrowserAgentError("PAGE_TAB_EXPIRED", "The page target is no longer available")
    session.model_target = target
    session.document = None
    session.refs.clear()
    return {"focused": handle}


async def close_workset_tab(
    session: BrowserAgentSession,
    handle: str,
    *,
    resolver: Any,
) -> dict[str, Any]:
    browser = await resolve_browser(session, resolver)
    tabs = await _tabs_metadata(browser)
    base = _ensure_base(session, tabs)
    target = _target_for_handle(session, handle)
    if target == base:
        raise BrowserAgentError("PAGE_TAB_NOT_CLOSABLE", "The user's origin page cannot be closed")
    if target not in tabs:
        session.tab_handles.pop(handle, None)
        session.model_targets.discard(target)
        raise BrowserAgentError("PAGE_TAB_EXPIRED", "The page target is no longer available")
    result = await browser.close_tab(target)
    if not result.success:
        raise BrowserAgentError("TOOL_EXECUTION_FAILED", result.error or "Closing the page failed")
    session.tab_handles.pop(handle, None)
    session.model_targets.discard(target)
    if session.model_target == target:
        session.model_target = base
    session.document = None
    session.refs.clear()
    return {
        "closed": handle,
        "focused": next(
            (h for h, t in session.tab_handles.items() if t == session.model_target), None
        ),
    }


async def admit_workset_tab(
    session: BrowserAgentSession,
    opened: dict[str, Any],
    *,
    resolver: Any,
) -> str:
    browser = await resolve_browser(session, resolver)
    tabs = await _tabs_metadata(browser)
    _ensure_base(session, tabs)
    mapped = set(session.tab_handles.values())
    targets = [
        target
        for target in _workset_targets(session)
        if target != session.base_target_id and target in tabs
    ]
    unmapped = [target for target in targets if target not in mapped]
    url = opened.get("url") if isinstance(opened.get("url"), str) else None
    title = opened.get("title") if isinstance(opened.get("title"), str) else None

    def matches(target: str) -> bool:
        return (url is not None and str(tabs[target].get("url") or "") == url) or (
            title is not None and str(tabs[target].get("title") or "") == title
        )

    candidates = unmapped
    if len(candidates) > 1:
        matched = [target for target in candidates if matches(target)]
        if matched:
            candidates = matched
    if not candidates:
        # The SDK watcher may have listed the new target first; its opaque
        # handle already exists. Reuse it only when the watched metadata matches.
        matched_mapped = [target for target in targets if matches(target)]
        if matched_mapped:
            return handle_for_target(session, matched_mapped[0])
        raise BrowserAgentError("PAGE_TAB_EXPIRED", "The opened page is no longer available")
    target = candidates[0]
    session.model_target = target
    session.document = None
    session.refs.clear()
    return handle_for_target(session, target)


def reset_workset(session: BrowserAgentSession) -> None:
    session.tab_handles.clear()
