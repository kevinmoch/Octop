"""Behavior tests for the restricted WebSkill session and grant registry."""

from __future__ import annotations

import pytest

from octop.infra.control.browser.webskill import sessions
from octop.infra.control.browser.webskill.sessions import (
    BrowserAgentError,
    BrowserAgentRegistry,
    DocumentIdentity,
)


def document(key: str = "document-1", target: str = "target-1") -> DocumentIdentity:
    return DocumentIdentity(key, target, f"loader-{key}", f"https://example.test/{key}")


def perceived_session(registry: BrowserAgentRegistry, user_id: int = 1):
    session = registry.create(user_id, f"user-{user_id}")
    session.document = document()
    return session


def test_ticket_is_short_lived_and_one_shot(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr(sessions.time, "monotonic", lambda: now)
    registry = BrowserAgentRegistry()
    session = registry.create(1, "user-1")
    ticket = registry.issue_ticket(session.id, 1)
    assert registry.consume_ticket(ticket) is session
    with pytest.raises(BrowserAgentError, match="already used"):
        registry.consume_ticket(ticket)

    second = registry.issue_ticket(session.id, 1)
    now += sessions.TICKET_TTL_SECONDS
    with pytest.raises(BrowserAgentError) as error:
        registry.consume_ticket(second)
    assert error.value.code == "TICKET_INVALID"


def test_stream_and_page_agent_tickets_do_not_invalidate_each_other() -> None:
    registry = BrowserAgentRegistry()
    session = registry.create(1, "user-1")
    first = registry.issue_ticket(session.id, 1)
    second = registry.issue_ticket(session.id, 1)
    assert registry.consume_ticket(first) is session
    assert registry.consume_ticket(second) is session


def test_disconnect_invalidates_old_grant_and_allows_safe_reuse() -> None:
    registry = BrowserAgentRegistry()
    session = perceived_session(registry)
    ref = registry.issue_ref(session, 100)
    grant = registry.issue_grant(
        session, document=document(), request={"ref": ref, "action": "click"}
    )
    connection = registry.attach(session)
    registry.disconnect(session.id, "stale-connection")
    assert session.connection_id == connection
    registry.disconnect(session.id, connection)
    assert session.connection_id is None
    assert session.document is None
    with pytest.raises(BrowserAgentError):
        registry.resolve_ref(session, ref, document())
    with pytest.raises(BrowserAgentError):
        registry.consume_grant(
            session, grant, document=document(), request={"ref": ref, "action": "click"}
        )
    reused = registry.create(1, "user-1")
    assert reused is session
    assert reused.model_target is None


def test_second_connection_and_unclaimed_session_reuse_are_rejected() -> None:
    registry = BrowserAgentRegistry()
    session = registry.create(1, "user-1")
    other = registry.create(1, "user-1")
    assert other.id != session.id
    connection = registry.attach(session)
    with pytest.raises(BrowserAgentError) as error:
        registry.attach(session)
    assert error.value.code == "SESSION_CONNECTED"
    assert error.value.status == 409
    registry.disconnect(session.id, connection)
    assert registry.create(1, "user-1") is session


def test_unclaimed_session_ttl_reclaims_capacity_without_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(sessions.time, "monotonic", lambda: now)
    registry = BrowserAgentRegistry()
    stale = [registry.create(1, "user-1") for _ in range(sessions.MAX_SESSIONS_PER_USER)]
    with pytest.raises(BrowserAgentError) as error:
        registry.create(1, "user-1")
    assert error.value.code == "SESSION_LIMIT"

    now += sessions.UNCLAIMED_SESSION_TTL_SECONDS
    replacement = registry.create(1, "user-1")
    assert replacement.id not in {session.id for session in stale}
    for session in stale:
        with pytest.raises(BrowserAgentError) as expired:
            registry.issue_ticket(session.id, 1)
        assert expired.value.code == "SESSION_NOT_FOUND"


def test_active_stream_disconnect_gets_full_idle_recovery_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(sessions.time, "monotonic", lambda: now)
    registry = BrowserAgentRegistry()
    session = registry.create(1, "user-1")
    first = registry.attach_stream(session)
    second = registry.attach_stream(session)

    now += sessions.UNCLAIMED_SESSION_TTL_SECONDS
    assert registry.create(1, "user-1").id != session.id
    ticket = registry.issue_ticket(session.id, 1)
    assert registry.consume_ticket(ticket) is session

    registry.detach_stream(session.id, first)
    assert session.stream_connection_ids == {second}
    registry.detach_stream(session.id, second)
    assert session.last_activity_at == now
    reconnected = registry.issue_ticket(session.id, 1)
    assert registry.consume_ticket(reconnected) is session

    now += sessions.UNCLAIMED_SESSION_TTL_SECONDS
    with pytest.raises(BrowserAgentError) as expired:
        registry.issue_ticket(session.id, 1)
    assert expired.value.code == "SESSION_NOT_FOUND"


def test_session_and_ticket_do_not_cross_user_boundaries() -> None:
    registry = BrowserAgentRegistry()
    session = registry.create(1, "user-1")
    with pytest.raises(BrowserAgentError) as error:
        registry.issue_ticket(session.id, 2)
    assert error.value.status == 404
    registry.close(session.id, 1)
    with pytest.raises(BrowserAgentError):
        registry.get(session.id, 1)


def test_grant_binds_the_complete_action_and_document() -> None:
    registry = BrowserAgentRegistry()
    session = perceived_session(registry)
    request = {"ref": "ws_test", "action": "fill", "value": "hello"}
    grant = registry.issue_grant(session, document=document(), request=request)
    assert (
        registry.consume_grant(session, grant, document=document(), request=request).frame == "self"
    )
    with pytest.raises(BrowserAgentError) as error:
        registry.consume_grant(session, grant, document=document(), request=request)
    assert error.value.code == "GRANT_INVALID"

    changed = registry.issue_grant(session, document=document(), request=request)
    with pytest.raises(BrowserAgentError) as error:
        registry.consume_grant(
            session, changed, document=document(), request={**request, "value": "changed"}
        )
    assert error.value.code == "GRANT_MISMATCH"
    # A mismatch consumes the token instead of allowing repeated guesses.
    with pytest.raises(BrowserAgentError) as error:
        registry.consume_grant(session, changed, document=document(), request=request)
    assert error.value.code == "GRANT_INVALID"


def test_grant_rejects_other_document_user_frame_and_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 50.0
    monkeypatch.setattr(sessions.time, "monotonic", lambda: now)
    registry = BrowserAgentRegistry()
    session = perceived_session(registry)
    request = {"ref": "ws_test", "action": "click"}
    grant = registry.issue_grant(session, document=document(), request=request)
    with pytest.raises(BrowserAgentError) as error:
        registry.consume_grant(session, grant, document=document("document-2"), request=request)
    assert error.value.code == "PAGE_ACTION_STALE_REF"

    other = perceived_session(registry, user_id=2)
    other_grant = registry.issue_grant(other, document=document(), request=request)
    with pytest.raises(BrowserAgentError):
        registry.consume_grant(session, other_grant, document=document(), request=request)

    current_grant = registry.issue_grant(session, document=document(), request=request)
    now += sessions.GRANT_TTL_SECONDS
    with pytest.raises(BrowserAgentError) as error:
        registry.consume_grant(session, current_grant, document=document(), request=request)
    assert error.value.code == "GRANT_INVALID"


def test_takeover_revokes_all_profile_grants_without_closing_chrome() -> None:
    registry = BrowserAgentRegistry()
    session = perceived_session(registry)
    grant = registry.issue_grant(
        session, document=document(), request={"ref": "ws_test", "action": "click"}
    )
    registry.revoke_grants(profile=session.profile)
    assert registry.get(session.id, session.user_id) is session
    with pytest.raises(BrowserAgentError):
        registry.consume_grant(
            session, grant, document=document(), request={"ref": "ws_test", "action": "click"}
        )


@pytest.mark.parametrize(
    "action_request",
    [
        {"ref": "ws_test", "action": "eval_js", "value": "alert(1)"},
        {"ref": "ws_test", "action": "click", "targetId": "forged"},
        {"ref": "ws_test", "action": "fill"},
        {"ref": "ws_test", "action": "scroll", "value": "diagonal"},
        {"ref": "ws_test", "action": "back"},
    ],
)
def test_action_whitelist_rejects_unsupported_or_malformed_inputs(action_request) -> None:
    with pytest.raises(BrowserAgentError):
        sessions.action_digest(action_request)


def test_backend_node_refs_expire_with_document_and_perception() -> None:
    registry = BrowserAgentRegistry()
    session = perceived_session(registry)
    ref = registry.issue_ref(session, 101)
    assert registry.resolve_ref(session, ref, document()).backend_node_id == 101
    with pytest.raises(BrowserAgentError) as error:
        registry.resolve_ref(session, ref, document("next"))
    assert error.value.code == "PAGE_ACTION_STALE_REF"
    session.refs.clear()
    with pytest.raises(BrowserAgentError):
        registry.resolve_ref(session, ref, document())
