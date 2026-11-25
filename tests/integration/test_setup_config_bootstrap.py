from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from octop.api.app import build_app
from octop.infra.connectors.service import ConnectorService
from octop.infra.server import OctopServer
from octop.infra.setup.bootstrap import (
    BootstrapError,
    bootstrap_status,
    run_config_bootstrap,
)
from tests.support.app import seed_cached_general_assistant, write_octop_config
from tests.support.harness import patch_harness


def _bootstrap() -> dict[str, Any]:
    return {
        "enabled": True,
        "admin": {"username": "ccs", "display_name": "基建部", "locale": "zh"},
        "provider": {
            "name": "default",
            "type": "openai-compatible",
            "base_url": "https://llm.example.test/v1",
            "models": [{"id": "model-1", "name": "默认模型"}],
        },
        "mcpServers": {
            "default": {
                "transport": "streamable_http",
                "url": "https://mcp.example.test/mcp",
            }
        },
    }


def _write_setup(home: Path, *, secrets: bool = True) -> None:
    write_octop_config(
        home,
        require_setup_password=False,
        database={"driver": "sqlite"},
        bootstrap=_bootstrap(),
    )
    if secrets:
        (home / "bootstrap.secrets.json").write_text(
            json.dumps(
                {
                    "admin_password": "TestPass12",
                    "provider_api_key": "test-provider-key",
                    "mcp_headers": {"default": {"Authorization": "Bearer test-mcp"}},
                }
            ),
            encoding="utf-8",
        )


def _connector_service(server: OctopServer) -> ConnectorService:
    assert server.services is not None
    return ConnectorService(
        repo=server.services.repos.connector_repo,
        secret_repo=server.services.secret_repo,
        settings_repo=server.services.settings_repo,
        config=server.services.config,
    )


async def test_config_bootstrap_creates_complete_install_and_is_idempotent(
    tmp_octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOP_DESKTOP_SHELL", "1")
    seed_cached_general_assistant(tmp_octop_home)
    _write_setup(tmp_octop_home)
    with patch_harness():
        server = OctopServer(home=tmp_octop_home)
        await server.start()
        try:
            assert server.user_manager is not None
            assert server.services is not None
            user = server.user_manager.get("ccs")
            assert user is not None and user.is_admin
            provider = server.services.provider_repo.get_by_name("default")
            assert provider is not None
            assert provider.get_models()[0]["id"] == "model-1"
            assert server.services.settings_repo.get_active_model() == ("default", "model-1")
            servers = _connector_service(server).get_custom_servers(user.id)
            assert servers["default"]["headers"] == {"Authorization": "Bearer test-mcp"}
            assert server.app_runtime is not None
            assert server.app_runtime.agent_registry.get_row("main") is not None
            assert bootstrap_status(server)["status"] == "complete"  # type: ignore[index]
            assert not (tmp_octop_home / "bootstrap.secrets.json").exists()

            from octop.infra.setup.desktop_bootstrap import nonce_path

            nonce = nonce_path(server).read_text(encoding="utf-8")
            device_token = "bootstrap-device-" + "x" * 40
            app = build_app(server)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 51234)),
                base_url="http://testserver",
            ) as client:
                public_status = (await client.get("/api/setup/status")).json()["bootstrap"]
                assert public_status["status"] == "complete"
                assert "user_id" not in public_status
                assert "config_digest" not in public_status
                bound = await client.post(
                    "/api/auth/device/bootstrap",
                    json={"device_token": device_token, "label": "windows-amd64"},
                    headers={"X-Octop-Bootstrap-Nonce": nonce},
                )
                assert bound.status_code == 200
                renewed = await client.post(
                    "/api/auth/device/token", json={"device_token": device_token}
                )
                assert renewed.status_code == 200
                me = await client.get(
                    "/api/auth/me",
                    headers={"Authorization": f"Bearer {renewed.json()['access_token']}"},
                )
                assert me.status_code == 200
                assert me.json()["username"] == "ccs"

            password_hash = server.services.user_repo.get(user.id).password_hash
            server.services.provider_repo.update(provider.id, note="user changed")
            await run_config_bootstrap(server)
            assert server.services.user_repo.get(user.id).password_hash == password_hash
            assert server.services.provider_repo.get_by_name("default").note == "user changed"
        finally:
            await server.stop()


async def test_config_bootstrap_rejects_missing_secrets_before_creating_a_user(
    tmp_octop_home: Path,
) -> None:
    _write_setup(tmp_octop_home, secrets=False)
    with patch_harness():
        server = OctopServer(home=tmp_octop_home)
        await server.start()
        try:
            assert server.user_manager is not None
            assert server.user_manager.count() == 0
            assert bootstrap_status(server)["error_code"] == "admin_password_required"  # type: ignore[index]
        finally:
            await server.stop()


async def test_config_bootstrap_resumes_without_repeating_completed_steps(
    tmp_octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_cached_general_assistant(tmp_octop_home)
    _write_setup(tmp_octop_home)

    async def fail_agent(*args: Any, **kwargs: Any) -> None:
        raise BootstrapError("default_agent_failed", "agent")

    monkeypatch.setattr("octop.infra.setup.bootstrap.bootstrap_setup_default_agent", fail_agent)
    with patch_harness():
        server = OctopServer(home=tmp_octop_home)
        await server.start()
        try:
            assert server.services is not None
            assert server.user_manager is not None
            user = server.user_manager.get("ccs")
            assert user is not None
            password_hash = server.services.user_repo.get(user.id).password_hash
            provider_id = server.services.provider_repo.get_by_name("default").id
            assert bootstrap_status(server)["error_code"] == "default_agent_failed"  # type: ignore[index]
            monkeypatch.undo()

            await run_config_bootstrap(server)
            assert bootstrap_status(server)["status"] == "complete"  # type: ignore[index]
            assert server.services.user_repo.get(user.id).password_hash == password_hash
            assert server.services.provider_repo.get_by_name("default").id == provider_id
            assert server.app_runtime.agent_registry.get_row("main") is not None
            assert len(_connector_service(server).get_custom_servers(user.id)) == 1
        finally:
            await server.stop()


async def test_config_bootstrap_skips_an_existing_unowned_installation(
    patched_app_client: Any,
) -> None:
    client, server, home = patched_app_client
    from tests.support.auth import bootstrap_admin

    await bootstrap_admin(client, home)
    original = server.user_manager.get("admin")
    server.config = replace(
        server.config,
        bootstrap={"enabled": True, "admin": {"username": "other"}, "unknown": True},
    )
    await run_config_bootstrap(server)
    assert server.user_manager.get("admin").id == original.id
    assert server.user_manager.get("other") is None
    assert bootstrap_status(server) is None
