"""Restricted WebSkill PageAgent API, using tickets instead of long-lived WS JWTs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import require_permission
from octop.api.routers.browser.harness import control_owner_for, resolve_harness_session
from octop.infra.control.browser.webskill.page_agent import (
    capture_image,
    execute_action,
    issue_action_grant,
    public_error_code,
    read_page,
)
from octop.infra.control.browser.webskill.sessions import (
    GRANT_TTL_SECONDS,
    TICKET_TTL_SECONDS,
    BrowserAgentError,
    BrowserAgentSession,
    browser_agent_registry,
)
from octop.infra.control.browser.webskill.skill_drafts import (
    RECORDING_ID_PATTERN,
    create_recorded_skill_draft,
)
from octop.infra.control.browser.webskill.tabs import (
    admit_workset_tab,
    close_workset_tab,
    focus_workset_tab,
    list_workset_tabs,
    reset_workset,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.permissions import user_has_permission
from octop.infra.utils.browser_media import user_browser_profile
from octop.infra.utils.url import normalize_nav_url

logger = logging.getLogger(__name__)
router = APIRouter()
MAX_PAGE_AGENT_MESSAGE_CHARS = 65536


class CreateBrowserAgentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_url: str = Field(default="", alias="startUrl", max_length=2048)


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: dict[str, Any]


class SkillDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_id: str | None = Field(
        default=None, alias="recordingId", min_length=1, max_length=128
    )


class TabAdmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, max_length=2048)


def _origin_parts(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise BrowserAgentError("ORIGIN_INVALID", "Origin is invalid", status=403)
    if parsed.username is not None or parsed.password is not None:
        raise BrowserAgentError("ORIGIN_INVALID", "Origin credentials are not allowed", status=403)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise BrowserAgentError(
            "ORIGIN_INVALID", "Origin must contain only scheme, host and port", status=403
        )
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise BrowserAgentError("ORIGIN_INVALID", "Origin port is invalid", status=403) from error
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


def _check_origin(origin: str | None, *, scheme: str, host: str) -> None:
    if not origin or not host:
        raise BrowserAgentError(
            "ORIGIN_INVALID", "A matching dashboard Origin is required", status=403
        )
    expected = _origin_parts(f"{scheme}://{host}")
    actual = _origin_parts(origin)
    if actual != expected:
        raise BrowserAgentError(
            "ORIGIN_INVALID", "Origin must exactly match the Octop dashboard origin", status=403
        )


def check_request_origin(request: Request) -> None:
    _check_origin(
        request.headers.get("origin"),
        scheme=request.url.scheme,
        host=request.headers.get("host", ""),
    )
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site != "same-origin":
        raise BrowserAgentError(
            "FETCH_METADATA_INVALID", "Cross-site requests are not allowed", status=403
        )


def check_websocket_origin(websocket: WebSocket) -> None:
    # Older Uvicorn releases rewrite https to wsss; newer ones may expose https.
    secure = websocket.url.scheme in {"https", "wss", "wsss"}
    try:
        _check_origin(
            websocket.headers.get("origin"),
            scheme="https" if secure else "http",
            host=websocket.headers.get("host", ""),
        )
    except BrowserAgentError as error:
        logger.info(
            "Browser WebSocket origin rejected: scheme=%s code=%s",
            websocket.url.scheme,
            error.code,
        )
        raise
    fetch_site = websocket.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site != "same-origin":
        raise BrowserAgentError(
            "FETCH_METADATA_INVALID", "Cross-site requests are not allowed", status=403
        )


def _http_error(error: BrowserAgentError) -> OctopError:
    if error.status == 401:
        code = ErrorCode.AUTH_FAILED
    elif error.status == 404:
        code = ErrorCode.NOT_FOUND
    elif error.status == 403:
        code = ErrorCode.FORBIDDEN
    else:
        code = ErrorCode.RESOURCE_NOT_ALLOWED
    return OctopError(code, error.message, status=error.status)


def _audit(
    request: Request, user_id: int, action: str, session_id: str, data: dict[str, Any]
) -> None:
    server = request.app.state.octop_server
    if server.services is None:
        return
    server.services.audit_repo.write(
        actor=f"user:{user_id}",
        action=action,
        target=session_id,
        payload=json.dumps(data, sort_keys=True, separators=(",", ":")),
    )


def _assert_websocket_user(websocket: WebSocket, session: BrowserAgentSession) -> None:
    manager = websocket.app.state.octop_server.user_manager
    user = manager.get_by_id(session.user_id) if manager is not None else None
    if user is None or not user_has_permission(user, "browser"):
        raise BrowserAgentError("FORBIDDEN", "Browser permission is required", status=403)


@router.post("/browser-agent/sessions", summary="Create a restricted WebSkill browser session")
async def create_session(
    body: CreateBrowserAgentBody,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
        session = browser_agent_registry.create(user.id, user_browser_profile(user.id))
        await asyncio.to_thread(
            _audit,
            request,
            user.id,
            "webskill.session.created",
            session.id,
            {"capabilities": session.public()["capabilities"]},
        )
        return {**session.public(), "startUrl": normalize_nav_url(body.start_url)}
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.get("/browser-agent/sessions/{session_id}", summary="Read WebSkill session capabilities")
async def get_session(
    session_id: str,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        session = browser_agent_registry.get(session_id, user.id)
        return {**session.public(), "controlOwner": control_owner_for(session.profile)}
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.delete(
    "/browser-agent/sessions/{session_id}",
    summary="Release a WebSkill session without stopping Chrome",
)
async def delete_session(
    session_id: str,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, bool]:
    try:
        check_request_origin(request)
        browser_agent_registry.close(session_id, user.id)
        await asyncio.to_thread(
            _audit,
            request,
            user.id,
            "webskill.session.closed",
            session_id,
            {"chromeKilled": False},
        )
        return {"ok": True}
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.post(
    "/browser-agent/sessions/{session_id}/tickets",
    summary="Issue a one-shot PageAgent WebSocket ticket",
)
async def issue_ticket(
    session_id: str,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
        ticket = browser_agent_registry.issue_ticket(session_id, user.id)
        return {"ticket": ticket, "expiresIn": TICKET_TTL_SECONDS}
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.post(
    "/browser-agent/sessions/{session_id}/grants", summary="Issue a one-shot page-action grant"
)
async def issue_grant(
    session_id: str,
    body: GrantBody,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
        session = browser_agent_registry.get(session_id, user.id)
        grant = await issue_action_grant(
            session,
            body.request,
            registry=browser_agent_registry,
            resolver=resolve_harness_session,
            write_gate=lambda: control_owner_for(session.profile) != "user",
        )
        await asyncio.to_thread(
            _audit,
            request,
            user.id,
            "webskill.grant.issued",
            session.id,
            {
                "action": body.request.get("action"),
                "grantDigest": hashlib.sha256(grant.encode("utf-8")).hexdigest(),
                **(
                    {
                        "document": hashlib.sha256(
                            session.document.key.encode("utf-8")
                        ).hexdigest(),
                        "documentTarget": session.document.target_id,
                    }
                    if session.document is not None
                    else {}
                ),
                **(
                    {"ref": ref}
                    if isinstance((ref := body.request.get("ref")), str) and len(ref) <= 256
                    else {}
                ),
            },
        )
        return {"grant": grant, "expiresIn": GRANT_TTL_SECONDS}
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.get(
    "/browser-agent/sessions/{session_id}/tabs",
    summary="List the restricted model tab workset",
)
async def list_tabs(
    session_id: str,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        session = browser_agent_registry.get(session_id, user.id)
        tabs = await list_workset_tabs(session, resolver=resolve_harness_session)
        return {"tabs": tabs}
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.post(
    "/browser-agent/sessions/{session_id}/tabs/reset",
    summary="Release the model tab workset without closing any page",
)
async def reset_tabs(
    session_id: str,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, bool]:
    try:
        check_request_origin(request)
        session = browser_agent_registry.get(session_id, user.id)
        reset_workset(session)
        browser_agent_registry.revoke_grants(session_id=session.id)
        return {"ok": True}
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.post(
    "/browser-agent/sessions/{session_id}/tabs/{handle}/focus",
    summary="Focus a page for the model without changing the user's view",
)
async def focus_tab(
    session_id: str,
    handle: str,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
        session = browser_agent_registry.get(session_id, user.id)
        result = await focus_workset_tab(session, handle, resolver=resolve_harness_session)
        browser_agent_registry.revoke_grants(session_id=session.id)
        await asyncio.to_thread(
            _audit,
            request,
            user.id,
            "webskill.tab.focused",
            session.id,
            {"handle": handle},
        )
        return result
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.delete(
    "/browser-agent/sessions/{session_id}/tabs/{handle}",
    summary="Close a page in the model tab workset",
)
async def close_tab(
    session_id: str,
    handle: str,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
        session = browser_agent_registry.get(session_id, user.id)
        result = await close_workset_tab(session, handle, resolver=resolve_harness_session)
        browser_agent_registry.revoke_grants(session_id=session.id)
        await asyncio.to_thread(
            _audit,
            request,
            user.id,
            "webskill.tab.closed",
            session.id,
            {"handle": handle},
        )
        return result
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.post(
    "/browser-agent/sessions/{session_id}/tabs/admit",
    summary="Admit a page opened by a model action into the workset",
)
async def admit_tab(
    session_id: str,
    body: TabAdmitBody,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
        session = browser_agent_registry.get(session_id, user.id)
        handle = await admit_workset_tab(
            session,
            {"title": body.title, "url": body.url},
            resolver=resolve_harness_session,
        )
        await asyncio.to_thread(
            _audit,
            request,
            user.id,
            "webskill.tab.admitted",
            session.id,
            {"handle": handle},
        )
        return {"handle": handle}
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.post(
    "/browser-agent/sessions/{session_id}/skill-drafts",
    summary="Create a fixed-template skill draft from an owned recording",
)
async def create_skill_draft(
    session_id: str,
    body: SkillDraftBody,
    request: Request,
    user: Any = Depends(require_permission("browser")),
) -> dict[str, Any]:
    try:
        check_request_origin(request)
        if body.recording_id is not None and not RECORDING_ID_PATTERN.fullmatch(body.recording_id):
            raise BrowserAgentError("RECORDING_NOT_FOUND", "Recording not found", status=404)
        session = browser_agent_registry.get(session_id, user.id)
        result = await asyncio.to_thread(create_recorded_skill_draft, session, body.recording_id)
        provenance = result["provenance"]
        await asyncio.to_thread(
            _audit,
            request,
            user.id,
            "webskill.skill-draft.created",
            session.id,
            {
                "recordingId": provenance["recordingId"],
                "sourceDigest": provenance["sourceDigest"],
                "draftDigest": provenance["draftDigest"],
                "evidence": "recording",
            },
        )
        return result
    except BrowserAgentError as error:
        raise _http_error(error) from error


@router.websocket("/browser-agent/ws")
async def page_agent_ws(
    websocket: WebSocket,
    ticket: str | None = Query(default=None),
) -> None:
    try:
        check_websocket_origin(websocket)
        if not ticket or len(ticket) > 128:
            raise BrowserAgentError("TICKET_INVALID", "A one-shot ticket is required", status=401)
        session = browser_agent_registry.consume_ticket(ticket)
        _assert_websocket_user(websocket, session)
        connection_id = browser_agent_registry.attach(session)
    except BrowserAgentError:
        await websocket.close(code=4001, reason="Browser-agent authentication failed")
        return
    await websocket.accept()

    async def send_error(error: BrowserAgentError, request_id: str | None = None) -> None:
        await websocket.send_json(
            {
                "type": "error",
                "code": public_error_code(error.code),
                "message": error.message,
                **({"requestId": request_id} if request_id is not None else {}),
            }
        )

    try:
        async for raw in websocket.iter_text():
            request_id: str | None = None
            try:
                if len(raw) > MAX_PAGE_AGENT_MESSAGE_CHARS:
                    raise BrowserAgentError("REQUEST_INVALID", "The PageAgent request is too large")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise BrowserAgentError(
                        "REQUEST_INVALID", "PageAgent request must be an object"
                    )
                raw_request_id = message.get("requestId")
                if raw_request_id is not None:
                    if (
                        not isinstance(raw_request_id, str)
                        or not raw_request_id
                        or len(raw_request_id) > 128
                    ):
                        raise BrowserAgentError(
                            "REQUEST_INVALID", "requestId must be a short non-empty string"
                        )
                    request_id = raw_request_id
                live = browser_agent_registry.get(session.id, session.user_id)
                _assert_websocket_user(websocket, live)

                def write_gate(profile: str = live.profile) -> bool:
                    return control_owner_for(profile) != "user"

                if message.get("type") == "perceive":
                    if set(message) - {"type", "requestId", "scope", "ref", "capture"}:
                        raise BrowserAgentError(
                            "REQUEST_INVALID", "Perception contains unknown fields"
                        )
                    reply = await read_page(
                        live,
                        message,
                        resolver=resolve_harness_session,
                        registry=browser_agent_registry,
                    )
                elif message.get("type") == "capture-image":
                    if set(message) - {"type", "requestId", "ref", "maxBytes"}:
                        raise BrowserAgentError(
                            "REQUEST_INVALID", "Image capture contains unknown fields"
                        )
                    reply = await capture_image(
                        live,
                        message,
                        registry=browser_agent_registry,
                        resolver=resolve_harness_session,
                    )
                elif message.get("type") == "execute":
                    if set(message) - {"type", "requestId", "request", "grant"}:
                        raise BrowserAgentError(
                            "REQUEST_INVALID", "Execution contains unknown fields"
                        )
                    request = message.get("request")
                    grant = message.get("grant")
                    if (
                        not isinstance(request, dict)
                        or not isinstance(grant, str)
                        or len(grant) > 128
                    ):
                        raise BrowserAgentError(
                            "GRANT_REQUIRED", "A matching one-shot action grant is required"
                        )
                    expected_document = live.document
                    try:
                        reply = await execute_action(
                            live,
                            request,
                            grant,
                            registry=browser_agent_registry,
                            resolver=resolve_harness_session,
                            write_gate=write_gate,
                        )
                    except BrowserAgentError as error:
                        await _audit_ws(
                            websocket,
                            live,
                            request,
                            {"ok": False},
                            code=error.code,
                            grant=grant,
                            expected_document=expected_document,
                        )
                        raise
                    await _audit_ws(
                        websocket,
                        live,
                        request,
                        reply["outcome"],
                        grant=grant,
                        expected_document=expected_document,
                    )
                else:
                    raise BrowserAgentError(
                        "REQUEST_UNSUPPORTED",
                        "Only the restricted PageAgent request types are available",
                    )
                await websocket.send_json(
                    {**reply, **({"requestId": request_id} if request_id is not None else {})}
                )
            except BrowserAgentError as error:
                await send_error(error, request_id)
            except (json.JSONDecodeError, UnicodeError):
                await send_error(
                    BrowserAgentError("REQUEST_INVALID", "PageAgent request must be valid JSON"),
                    request_id,
                )
            except Exception:
                logger.error("WebSkill PageAgent request failed without payload logging")
                await send_error(
                    BrowserAgentError("TOOL_EXECUTION_FAILED", "The browser-agent request failed"),
                    request_id,
                )
    except WebSocketDisconnect:
        return
    finally:
        browser_agent_registry.disconnect(session.id, connection_id)


async def _audit_ws(
    websocket: WebSocket,
    session: BrowserAgentSession,
    request: dict[str, Any],
    outcome: dict[str, Any],
    *,
    code: str | None = None,
    grant: str | None = None,
    expected_document: Any = None,
) -> None:
    server = websocket.app.state.octop_server
    if server.services is None:
        return
    document = expected_document or session.document
    await asyncio.to_thread(
        server.services.audit_repo.write,
        actor=f"user:{session.user_id}",
        action="webskill.page.action",
        target=session.id,
        payload=json.dumps(
            {
                "action": request.get("action"),
                "ok": outcome.get("ok"),
                **({"code": public_error_code(code)} if code is not None else {}),
                **(
                    {
                        "document": hashlib.sha256(document.key.encode("utf-8")).hexdigest(),
                        "documentTarget": document.target_id,
                    }
                    if document is not None
                    else {}
                ),
                **(
                    {"ref": ref}
                    if isinstance((ref := request.get("ref")), str) and len(ref) <= 256
                    else {}
                ),
                **(
                    {"grantDigest": hashlib.sha256(grant.encode("utf-8")).hexdigest()}
                    if grant is not None
                    else {}
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
