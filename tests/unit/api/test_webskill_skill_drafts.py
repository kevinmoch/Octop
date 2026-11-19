"""Behavior tests for deterministic recording-to-skill conversion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from octop_browser.record.models import RecordingManifest, SemanticStep, StepsDocument
from octop_browser.record.settings import RecordReplaySettings
from octop_browser.record.store import RecordingStore

from octop.infra.control.browser.webskill.sessions import BrowserAgentError, BrowserAgentSession
from octop.infra.control.browser.webskill.skill_drafts import (
    _digest_draft,
    canonical_json,
    create_recorded_skill_draft,
)


def recording_store(tmp_path: Path) -> RecordingStore:
    store = RecordingStore(RecordReplaySettings(base_dir=tmp_path))
    store.create(
        RecordingManifest(
            recordingId="rec_test",
            createdAt="2026-11-18T00:00:00Z",
            endedAt="2026-11-18T00:01:00Z",
            status="stopped",
            profile="user-1",
        )
    )
    store.append_event("rec_test", {"type": "click", "page": {"url": "https://example.test"}})
    store.write_steps(
        "rec_test",
        StepsDocument(
            recordingId="rec_test",
            startUrl="https://example.test/form?token=raw-token&name=raw-name",
            steps=[
                SemanticStep(
                    id="step_1",
                    kind="fill",
                    target={"label": "Password"},
                    value="never-copy-this-value",
                ),
                SemanticStep(id="step_2", kind="click", target={"accessibleName": "Save changes"}),
            ],
        ),
    )
    return store


def session() -> BrowserAgentSession:
    return BrowserAgentSession(id="session-1", user_id=1, profile="user-1")


def test_fixed_template_is_deterministic_and_does_not_publish_recorded_values(
    tmp_path: Path,
) -> None:
    store = recording_store(tmp_path)
    first = create_recorded_skill_draft(session(), "rec_test", store=store)
    second = create_recorded_skill_draft(session(), "rec_test", store=store)

    assert first == second
    draft = first["draft"]
    assert set(draft) == {"name", "description", "content"}
    assert "steps" not in draft
    assert "never-copy-this-value" not in draft["content"]
    assert "raw-token" not in draft["content"]
    assert "raw-name" not in draft["content"]
    assert "fresh references" in draft["content"]
    assert first["provenance"]["evidence"] == "recording"
    assert first["provenance"]["draftDigest"] == _digest_draft(draft)


def test_source_and_draft_digests_have_separate_scope(tmp_path: Path) -> None:
    store = recording_store(tmp_path)
    before = create_recorded_skill_draft(session(), "rec_test", store=store)
    store.append_event("rec_test", {"type": "review-marker"})
    after = create_recorded_skill_draft(session(), "rec_test", store=store)

    assert before["provenance"]["sourceDigest"] != after["provenance"]["sourceDigest"]
    assert before["provenance"]["draftDigest"] == after["provenance"]["draftDigest"]
    assert "sourceDigest" not in before["draft"]


def test_draft_digest_recursively_sorts_keys() -> None:
    draft = {"name": "recorded-example", "description": "中文", "content": "line 1\nline 2"}
    reordered = {"content": "line 1\nline 2", "description": "中文", "name": "recorded-example"}
    expected = hashlib.sha256(
        json.dumps(draft, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert _digest_draft(draft) == _digest_draft(reordered) == expected
    assert canonical_json({"outer": {"z": 1, "a": [2, True]}}) == '{"outer":{"a":[2,true],"z":1}}'
    with pytest.raises(ValueError, match="Non-finite"):
        canonical_json({"invalid": float("nan")})
    with pytest.raises(ValueError, match="Only JSON"):
        canonical_json({"invalid": object()})


@pytest.mark.parametrize("profile", ["user-2"])
def test_cross_profile_recording_is_not_found(tmp_path: Path, profile: str) -> None:
    other = BrowserAgentSession(id="session-2", user_id=2, profile=profile)
    with pytest.raises(BrowserAgentError) as error:
        create_recorded_skill_draft(other, "rec_test", store=recording_store(tmp_path))
    assert error.value.status == 404


def test_empty_recording_cannot_become_a_candidate(tmp_path: Path) -> None:
    store = recording_store(tmp_path)
    store.write_steps("rec_test", StepsDocument(recordingId="rec_test", steps=[]))
    with pytest.raises(BrowserAgentError) as error:
        create_recorded_skill_draft(session(), "rec_test", store=store)
    assert error.value.code == "RECORDING_EMPTY"


async def test_skill_draft_route_enforces_origin_permission_and_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.app import octop_client
    from tests.support.auth import auth_header, bootstrap_admin, create_user

    from octop.api.routers.browser import webskill
    from octop.infra.control.browser.webskill import skill_drafts
    from octop.infra.control.browser.webskill.sessions import BrowserAgentRegistry

    store = recording_store(tmp_path)
    registry = BrowserAgentRegistry()
    monkeypatch.setattr(webskill, "browser_agent_registry", registry)
    monkeypatch.setattr(skill_drafts, "RecordingStore", lambda: store)
    async with octop_client(tmp_path / "home") as (client, server):
        await bootstrap_admin(client, tmp_path / "home")
        admin = await auth_header(client)
        origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
        created = await client.post(
            "/api/browser-agent/sessions", headers={**admin, **origin}, json={}
        )
        session_id = created.json()["sessionId"]
        response = await client.post(
            f"/api/browser-agent/sessions/{session_id}/skill-drafts",
            headers={**admin, **origin},
            json={"recordingId": "rec_test"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["draft"]["name"] == "recorded-example-test"
        assert "steps" not in body["draft"]
        assert body["provenance"]["draftDigest"] == _digest_draft(body["draft"])
        assert server.services is not None
        audits = server.services.audit_repo.query(action="webskill.skill-draft.created")
        assert len(audits) == 1
        audit = json.loads(audits[0].payload or "{}")
        assert audit["recordingId"] == "rec_test"
        assert audit["sourceDigest"] == body["provenance"]["sourceDigest"]
        assert "never-copy-this-value" not in (audits[0].payload or "")
        assert "raw-token" not in (audits[0].payload or "")

        for headers in (
            {"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "same-origin"},
            {**origin, "Sec-Fetch-Site": "cross-site"},
        ):
            denied = await client.post(
                f"/api/browser-agent/sessions/{session_id}/skill-drafts",
                headers={**admin, **headers},
                json={},
            )
            assert denied.status_code == 403

        without_browser = await create_user(
            client, admin, username="candidate-no-browser", permissions=[]
        )
        other = await create_user(
            client, admin, username="candidate-other", permissions=["browser"]
        )
        for auth, status in ((without_browser, 403), (other, 404)):
            denied = await client.post(
                f"/api/browser-agent/sessions/{session_id}/skill-drafts",
                headers={**auth, **origin},
                json={},
            )
            assert denied.status_code == status
        missing = await client.post(
            f"/api/browser-agent/sessions/{session_id}/skill-drafts",
            headers={**admin, **origin},
            json={"recordingId": "../other-user"},
        )
        assert missing.status_code == 404
