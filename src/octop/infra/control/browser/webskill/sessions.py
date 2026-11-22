"""User-scoped sessions, one-shot tickets and action grants for WebSkill."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

TICKET_TTL_SECONDS = 30.0
GRANT_TTL_SECONDS = 10.0
UNCLAIMED_SESSION_TTL_SECONDS = 120.0
MAX_SESSIONS_PER_USER = 8
MAX_TICKETS_PER_SESSION = 4
MAX_MODEL_TARGETS = 8
SUPPORTED_ACTIONS = frozenset(
    {
        "click",
        "fill",
        "select",
        "scroll",
        "back",
        "set",
        "hover",
        "drag",
        "submit",
        "attach",
    }
)
DOCUMENT_REF = "@document"


class BrowserAgentError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class DocumentIdentity:
    key: str
    target_id: str
    loader_id: str
    url: str
    title: str = ""

    def public(self) -> dict[str, str]:
        return {"key": self.key, "url": self.url, **({"title": self.title} if self.title else {})}


@dataclass(frozen=True)
class PageRef:
    backend_node_id: int
    document_key: str
    frame: str = "self"
    # JSON identity used to restore a ref after the DOM node is replaced.
    signature: str = ""
    # JSON include/exclude selectors that gated this ref at issuance.
    action_scope: str = ""
    # True when this PageRef was restored by signature after a re-render.
    recovered: bool = False
    # "modal-elevated" for an element inside a dialog opened by this task.
    provenance: str = ""


@dataclass
class BrowserAgentSession:
    id: str
    user_id: int
    profile: str
    last_activity_at: float = field(default_factory=time.monotonic)
    active: bool = True
    model_target: str | None = None
    base_target_id: str | None = None
    model_targets: set[str] = field(default_factory=set)
    tab_handles: dict[str, str] = field(default_factory=dict)
    downloads: dict[str, Any] = field(default_factory=dict)
    download_guids: dict[str, dict[str, str]] = field(default_factory=dict)
    download_dir: str | None = None
    downloads_initialized: bool = False
    download_listeners: list[tuple[str, Any]] = field(default_factory=list)
    download_client: Any | None = None
    download_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    downloads_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    document: DocumentIdentity | None = None
    refs: dict[str, PageRef] = field(default_factory=dict)
    elevated_modals: set[str] = field(default_factory=set)
    # Octop-only attachment bridge: bytes uploaded by the host immediately
    # before an attach action, consumed by that action.
    attachments: list[dict[str, Any]] = field(default_factory=list)
    connection_id: str | None = None
    has_connected: bool = False
    stream_connection_ids: set[str] = field(default_factory=set)

    def public(self) -> dict[str, Any]:
        return {
            "sessionId": self.id,
            "active": self.active,
            "capabilities": {
                "frames": True,
                "oopif": True,
                "imageCapture": True,
                "targetWorkset": True,
                "actions": sorted(SUPPORTED_ACTIONS),
            },
            **({"document": self.document.public()} if self.document is not None else {}),
        }


@dataclass(frozen=True)
class Ticket:
    session_id: str
    user_id: int
    expires_at: float


@dataclass(frozen=True)
class Grant:
    session_id: str
    user_id: int
    profile: str
    document_key: str
    target_id: str
    frame: str
    action: str
    payload_digest: str
    expires_at: float


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def action_digest(request: dict[str, Any]) -> str:
    if not isinstance(request, dict):
        raise BrowserAgentError("ACTION_INVALID", "Page action must be an object")
    if set(request) - {"ref", "action", "value"}:
        raise BrowserAgentError("ACTION_INVALID", "Page action contains unknown fields")
    action = request.get("action")
    ref = request.get("ref")
    value = request.get("value")
    if action not in SUPPORTED_ACTIONS:
        raise BrowserAgentError("ACTION_UNSUPPORTED", "The action is not available in this host")
    if action == "back":
        if ref != DOCUMENT_REF:
            raise BrowserAgentError("ACTION_INVALID", "Back must use the document reference")
    elif not isinstance(ref, str) or not ref or len(ref) > 256:
        raise BrowserAgentError("ACTION_INVALID", "A valid element reference is required")
    if value is not None and (not isinstance(value, str) or len(value) > 32768):
        raise BrowserAgentError("ACTION_INVALID", "The action value is invalid or too large")
    if action == "fill" and not isinstance(value, str):
        raise BrowserAgentError("ACTION_INVALID", "Fill requires a text value")
    if action == "select" and not isinstance(value, str):
        raise BrowserAgentError("ACTION_INVALID", "Select requires an option value")
    if action == "scroll" and value not in {"up", "down", "left", "right"}:
        raise BrowserAgentError("ACTION_INVALID", "Scroll requires a supported direction")
    if action == "set" and value not in {"true", "false"}:
        raise BrowserAgentError("ACTION_INVALID", "Set requires true or false")
    if action == "drag" and (not isinstance(value, str) or not value):
        raise BrowserAgentError("ACTION_INVALID", "Drag requires a destination reference")
    if action == "attach" and value is not None:
        raise BrowserAgentError("ACTION_INVALID", "Attach files are selected by the host")
    normalized = {
        "ref": ref,
        "action": action,
        **({"value": value} if value is not None else {}),
    }
    return _digest(
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


class BrowserAgentRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, BrowserAgentSession] = {}
        self._tickets: dict[str, Ticket] = {}
        self._grants: dict[str, Grant] = {}
        self._write_locks: dict[str, asyncio.Lock] = {}

    def _expire_unclaimed(self, now: float) -> None:
        expired = [
            (session.id, session.user_id)
            for session in self._sessions.values()
            if session.active
            and not session.has_connected
            and not session.stream_connection_ids
            and now - session.last_activity_at >= UNCLAIMED_SESSION_TTL_SECONDS
        ]
        for session_id, user_id in expired:
            self.close(session_id, user_id)

    def create(self, user_id: int, profile: str) -> BrowserAgentSession:
        now = time.monotonic()
        self._expire_unclaimed(now)
        disconnected = next(
            (
                session
                for session in self._sessions.values()
                if session.user_id == user_id
                and session.profile == profile
                and session.active
                and session.has_connected
                and session.connection_id is None
            ),
            None,
        )
        if disconnected is not None:
            disconnected.model_target = None
            disconnected.model_targets.clear()
            disconnected.tab_handles.clear()
            disconnected.document = None
            disconnected.refs.clear()
            return disconnected
        active = [s for s in self._sessions.values() if s.user_id == user_id and s.active]
        if len(active) >= MAX_SESSIONS_PER_USER:
            raise BrowserAgentError(
                "SESSION_LIMIT", "The user has too many active browser-agent sessions", status=409
            )
        session = BrowserAgentSession(
            id=secrets.token_urlsafe(24), user_id=user_id, profile=profile, last_activity_at=now
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str, user_id: int) -> BrowserAgentSession:
        session = self._sessions.get(session_id)
        if session is None or not session.active or session.user_id != user_id:
            raise BrowserAgentError(
                "SESSION_NOT_FOUND", "The browser-agent session is not available", status=404
            )
        return session

    def attach_stream(self, session: BrowserAgentSession) -> str:
        self.get(session.id, session.user_id)
        connection_id = secrets.token_urlsafe(16)
        session.stream_connection_ids.add(connection_id)
        session.last_activity_at = time.monotonic()
        return connection_id

    def detach_stream(self, session_id: str, connection_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None and connection_id in session.stream_connection_ids:
            session.stream_connection_ids.remove(connection_id)
            session.last_activity_at = time.monotonic()

    def attach(self, session: BrowserAgentSession) -> str:
        self.get(session.id, session.user_id)
        if session.connection_id is not None:
            raise BrowserAgentError(
                "SESSION_CONNECTED",
                "The browser-agent session already has an active PageAgent connection",
                status=409,
            )
        connection_id = secrets.token_urlsafe(16)
        session.connection_id = connection_id
        session.has_connected = True
        session.last_activity_at = time.monotonic()
        return connection_id

    def disconnect(self, session_id: str, connection_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None or session.connection_id != connection_id:
            return
        session.connection_id = None
        session.document = None
        session.refs.clear()
        session.last_activity_at = time.monotonic()
        self.revoke_grants(session_id=session_id)

    def close(self, session_id: str, user_id: int) -> None:
        session = self.get(session_id, user_id)
        session.active = False
        from .downloads import cleanup_downloads  # runtime import avoids a cycle

        cleanup_downloads(session)
        self._sessions.pop(session_id, None)
        self._tickets = {
            key: ticket for key, ticket in self._tickets.items() if ticket.session_id != session_id
        }
        self._grants = {
            key: grant for key, grant in self._grants.items() if grant.session_id != session_id
        }

    def issue_ticket(self, session_id: str, user_id: int) -> str:
        now = time.monotonic()
        self._expire_unclaimed(now)
        session = self.get(session_id, user_id)
        session.last_activity_at = now
        self._tickets = {
            key: ticket for key, ticket in self._tickets.items() if ticket.expires_at > now
        }
        outstanding = sum(ticket.session_id == session_id for ticket in self._tickets.values())
        if outstanding >= MAX_TICKETS_PER_SESSION:
            raise BrowserAgentError(
                "TICKET_LIMIT", "Too many unused connection tickets", status=409
            )
        token = secrets.token_urlsafe(32)
        self._tickets[_digest(token)] = Ticket(
            session_id=session_id,
            user_id=user_id,
            expires_at=now + TICKET_TTL_SECONDS,
        )
        return token

    def consume_ticket(self, token: str) -> BrowserAgentSession:
        ticket = self._tickets.pop(_digest(token), None)
        if ticket is None or ticket.expires_at <= time.monotonic():
            raise BrowserAgentError(
                "TICKET_INVALID",
                "The browser-agent ticket is invalid, expired or already used",
                status=401,
            )
        return self.get(ticket.session_id, ticket.user_id)

    def issue_grant(
        self,
        session: BrowserAgentSession,
        *,
        document: DocumentIdentity,
        request: dict[str, Any],
    ) -> str:
        if session.document is None:
            raise BrowserAgentError(
                "DOCUMENT_REQUIRED", "Perceive the page before requesting an action grant"
            )
        if document.key != session.document.key:
            raise BrowserAgentError(
                "DOCUMENT_CHANGED", "The document changed before the grant was issued"
            )
        digest = action_digest(request)
        entry = (
            PageRef(0, document.key)
            if request["ref"] == DOCUMENT_REF
            else self.resolve_ref(session, str(request["ref"]), document)
        )
        self.revoke_grants(profile=session.profile)
        token = secrets.token_urlsafe(32)
        self._grants[_digest(token)] = Grant(
            session_id=session.id,
            user_id=session.user_id,
            profile=session.profile,
            document_key=document.key,
            target_id=document.target_id,
            frame=entry.frame,
            action=str(request["action"]),
            payload_digest=digest,
            expires_at=time.monotonic() + GRANT_TTL_SECONDS,
        )
        return token

    def consume_grant(
        self,
        session: BrowserAgentSession,
        token: str,
        *,
        document: DocumentIdentity,
        request: dict[str, Any],
    ) -> Grant:
        grant = self._grants.pop(_digest(token), None)
        if grant is None or grant.expires_at <= time.monotonic():
            raise BrowserAgentError(
                "GRANT_INVALID", "The action grant is invalid, expired or already used"
            )
        if (
            grant.session_id != session.id
            or grant.user_id != session.user_id
            or grant.profile != session.profile
        ):
            raise BrowserAgentError("GRANT_MISMATCH", "The action grant belongs to another session")
        entry = (
            PageRef(0, document.key)
            if request.get("ref") == DOCUMENT_REF
            else self.resolve_ref(session, str(request.get("ref") or ""), document)
        )
        if (
            grant.document_key != document.key
            or grant.target_id != document.target_id
            or grant.frame != entry.frame
        ):
            raise BrowserAgentError(
                "PAGE_ACTION_STALE_REF", "The action grant belongs to an expired document"
            )
        if grant.action != request.get("action") or grant.payload_digest != action_digest(request):
            raise BrowserAgentError(
                "GRANT_MISMATCH", "The action grant does not match the requested action"
            )
        return grant

    def issue_ref(
        self,
        session: BrowserAgentSession,
        backend_node_id: int,
        frame: str = "self",
        signature: str = "",
        action_scope: str = "",
        provenance: str = "",
    ) -> str:
        if signature:
            document_key = session.document.key if session.document else ""
            for existing_ref, existing in session.refs.items():
                if (
                    existing.signature == signature
                    and existing.frame == frame
                    and existing.document_key == document_key
                ):
                    return existing_ref
        ref = f"ws_{secrets.token_hex(12)}"
        session.refs[ref] = PageRef(
            backend_node_id,
            session.document.key if session.document else "",
            frame,
            signature,
            action_scope,
            recovered=False,
            provenance=provenance,
        )
        return ref

    def resolve_ref(
        self, session: BrowserAgentSession, ref: str, document: DocumentIdentity
    ) -> PageRef:
        entry = session.refs.get(ref)
        if entry is None or entry.document_key != document.key:
            raise BrowserAgentError(
                "PAGE_ACTION_STALE_REF", "The element reference is unknown or expired"
            )
        return entry

    def revoke_grants(self, *, session_id: str | None = None, profile: str | None = None) -> None:
        self._grants = {
            key: grant
            for key, grant in self._grants.items()
            if (session_id is None or grant.session_id != session_id)
            and (profile is None or grant.profile != profile)
        }

    def write_lock(self, profile: str) -> asyncio.Lock:
        return self._write_locks.setdefault(profile, asyncio.Lock())


browser_agent_registry = BrowserAgentRegistry()
