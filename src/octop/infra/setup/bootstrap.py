from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from octop.infra.connectors.custom_mcp import validate_servers_map
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.setup.provider_draft import (
    ProviderDraftBody,
    ProviderModelDraft,
    apply_provider_draft,
    validated_provider_parts,
)
from octop.infra.users.identity import Role

logger = logging.getLogger(__name__)
_STATE_KEY = "setup_bootstrap"
_SECRETS_ENV = "OCTOP_BOOTSTRAP_SECRETS_FILE"


class BootstrapAdmin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    display_name: str | None = None
    email: str | None = Field(default=None, max_length=254)
    locale: Literal["zh", "en"] = "zh"


class BootstrapProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    enabled: bool = True
    input: list[str] = Field(default_factory=list)
    thinking: Any | None = None
    reasoning: bool | None = None


class BootstrapProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    base_url: str
    models: list[BootstrapProviderModel] = Field(min_length=1)
    extras: dict[str, Any] | None = None

    def draft(self, api_key: str) -> ProviderDraftBody:
        return ProviderDraftBody(
            name=self.name,
            type=self.type,
            api_key=api_key,
            base_url=self.base_url,
            models=[ProviderModelDraft(**model.model_dump()) for model in self.models],
            extras=self.extras,
        )


class BootstrapDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = False
    admin: BootstrapAdmin | None = None
    provider: BootstrapProvider | None = None
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict, alias="mcpServers")

    @model_validator(mode="after")
    def enabled_has_admin(self) -> BootstrapDefinition:
        if self.enabled and self.admin is None:
            raise ValueError("bootstrap.admin is required")
        return self


class BootstrapSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admin_password: str = Field(default="", max_length=200)
    provider_api_key: str = ""
    mcp_headers: dict[str, dict[str, str]] = Field(default_factory=dict)


class BootstrapError(Exception):
    def __init__(self, code: str, step: str) -> None:
        super().__init__(code)
        self.code = code
        self.step = step


def _validation_error(exc: ValidationError, step: str = "validation") -> BootstrapError:
    fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
    logger.warning("bootstrap %s rejected fields: %s", step, ", ".join(fields))
    return BootstrapError("invalid_config", step)


def _read_state(server: Any) -> dict[str, Any]:
    raw = server.services.settings_repo.get(_STATE_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "failed", "error_code": "state_invalid"}
    return value if isinstance(value, dict) else {"status": "failed", "error_code": "state_invalid"}


def bootstrap_status(server: Any) -> dict[str, Any] | None:
    if server.services is None:
        return None
    state = _read_state(server)
    return state or None


def public_bootstrap_status(server: Any) -> dict[str, Any] | None:
    state = bootstrap_status(server)
    if state is None:
        return None
    return {
        key: state[key] for key in ("status", "step", "failed_step", "error_code") if key in state
    }


def _write_state(server: Any, state: dict[str, Any]) -> None:
    server.services.settings_repo.set(
        _STATE_KEY, json.dumps(state, sort_keys=True, separators=(",", ":"))
    )


def _secrets_file(server: Any) -> tuple[Path, bool]:
    override = (os.environ.get(_SECRETS_ENV) or "").strip()
    if override:
        return Path(override).expanduser(), False
    return server.paths.root / "bootstrap.secrets.json", True


def _load_secrets(server: Any) -> BootstrapSecrets:
    path, _ = _secrets_file(server)
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise BootstrapError("invalid_secrets", "validation") from None
        if not isinstance(loaded, dict):
            raise BootstrapError("invalid_secrets", "validation")
        raw = loaded
    if value := os.environ.get("OCTOP_BOOTSTRAP_ADMIN_PASSWORD"):
        raw["admin_password"] = value
    if value := os.environ.get("OCTOP_BOOTSTRAP_PROVIDER_API_KEY"):
        raw["provider_api_key"] = value
    try:
        return BootstrapSecrets.model_validate(raw)
    except ValidationError as exc:
        raise _validation_error(exc) from None


def _prepare(
    server: Any, raw: dict[str, Any]
) -> tuple[BootstrapDefinition, ProviderDraftBody | None, dict[str, Any], str]:
    try:
        definition = BootstrapDefinition.model_validate(raw)
        secrets = _load_secrets(server)
    except ValidationError as exc:
        raise _validation_error(exc) from None
    if not definition.enabled or definition.admin is None:
        raise BootstrapError("invalid_config", "validation")
    if not secrets.admin_password:
        raise BootstrapError("admin_password_required", "validation")

    provider: ProviderDraftBody | None = None
    if definition.provider is not None:
        if not secrets.provider_api_key.strip():
            raise BootstrapError("provider_api_key_required", "validation")
        provider = definition.provider.draft(secrets.provider_api_key)
        try:
            validated_provider_parts(provider)
        except OctopError:
            raise BootstrapError("invalid_provider", "validation") from None

    servers: dict[str, Any] = {}
    for name, value in definition.mcp_servers.items():
        if "headers" in value or "env" in value:
            raise BootstrapError("mcp_secrets_in_config", "validation")
        spec = dict(value)
        headers = secrets.mcp_headers.get(name)
        if headers:
            spec["headers"] = headers
        servers[name] = spec
    try:
        servers = validate_servers_map(servers, reserved_names=set())
    except ValueError:
        raise BootstrapError("invalid_mcp", "validation") from None
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return definition, provider, servers, digest


async def bootstrap_setup_default_agent(
    server: Any, *, user_id: int, locale: str = "zh", pin_main: bool = True
) -> None:
    from octop.infra.agents.experts.default_agent import (
        SETUP_DEFAULT_AGENT_ID,
        bootstrap_first_expert,
    )

    if server.app_runtime is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "app_runtime not ready")
    await bootstrap_first_expert(
        server,
        user_id=user_id,
        locale=locale,
        agent_id=SETUP_DEFAULT_AGENT_ID if pin_main else None,
    )


def _failed_state(state: dict[str, Any], error: BootstrapError) -> dict[str, Any]:
    return {
        **state,
        "status": "failed",
        "failed_step": error.step,
        "error_code": error.code,
    }


async def run_config_bootstrap(server: Any) -> None:
    if server.services is None or server.user_manager is None or server.config is None:
        return
    raw = server.config.bootstrap
    state = _read_state(server)
    if state.get("status") == "complete":
        return
    owned = state.get("owned") is True
    user_count = server.user_manager.count()
    if user_count > 0 and not owned:
        if state:
            server.services.settings_repo.delete(_STATE_KEY)
        return
    if not raw.get("enabled"):
        return

    step = str(state.get("failed_step") or state.get("step") or "validation")
    try:
        definition, provider, servers, digest = _prepare(server, raw)
        assert definition.admin is not None
        if owned and state.get("config_digest") != digest:
            raise BootstrapError("config_changed", "validation")
        if not owned:
            state = {
                "v": 1,
                "owned": True,
                "status": "in_progress",
                "step": "admin",
                "config_digest": digest,
                "username": definition.admin.username,
                "completed": [],
            }
            _write_state(server, state)
        completed = set(state.get("completed") or [])

        step = "admin"
        user = server.user_manager.get(definition.admin.username)
        if "admin" not in completed:
            if user is None:
                if server.user_manager.count() > 0:
                    raise BootstrapError("admin_missing", step)
                from octop.infra.db.repos.user_roles import ADMIN_USER_ROLE_ID, UserRoleRepo

                admin_role = UserRoleRepo(server.services.db).get(ADMIN_USER_ROLE_ID)
                secrets = _load_secrets(server)
                user = await server.user_manager.create(
                    username=definition.admin.username,
                    password=secrets.admin_password,
                    role=Role.ADMIN,
                    display_name=definition.admin.display_name,
                    email=definition.admin.email,
                    locale=definition.admin.locale,
                    role_name=admin_role.user_role_name if admin_role is not None else "管理员",
                )
            elif not user.is_admin:
                raise BootstrapError("admin_role_conflict", step)
            completed.add("admin")
        if user is None or not user.is_admin:
            raise BootstrapError("admin_missing", step)
        if state.get("user_id") not in {None, user.id}:
            raise BootstrapError("admin_identity_changed", step)
        state.update({"user_id": user.id, "completed": sorted(completed), "step": "provider"})
        _write_state(server, state)

        step = "provider"
        if provider is not None and "provider" not in completed:
            await apply_provider_draft(server, provider, upsert=True)
            completed.add("provider")
        state.update({"completed": sorted(completed), "step": "mcp"})
        _write_state(server, state)

        step = "mcp"
        if "mcp" not in completed:
            if servers:
                from octop.infra.connectors.service import ConnectorService

                service = ConnectorService(
                    repo=server.services.repos.connector_repo,
                    secret_repo=server.services.secret_repo,
                    settings_repo=server.services.settings_repo,
                    config=server.services.config,
                )
                existing = service.get_custom_servers(user.id)
                service.put_custom_servers(user.id, {**existing, **servers})
            completed.add("mcp")
        state.update({"completed": sorted(completed), "step": "agent"})
        _write_state(server, state)

        step = "agent"
        if "agent" not in completed:
            await bootstrap_setup_default_agent(
                server, user_id=user.id, locale=definition.admin.locale, pin_main=True
            )
            if server.app_runtime.agent_registry.get_row("main") is None:
                raise BootstrapError("default_agent_failed", step)
            completed.add("agent")
        state = {
            **state,
            "status": "complete",
            "step": "complete",
            "completed": sorted(completed),
            "completed_at": int(time.time()),
        }
        state.pop("failed_step", None)
        state.pop("error_code", None)
        _write_state(server, state)
        secrets_path, remove_after_use = _secrets_file(server)
        if remove_after_use:
            try:
                secrets_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("could not remove the completed bootstrap secrets file")
        from octop.infra.setup.desktop_bootstrap import issue_nonce

        try:
            await asyncio.to_thread(issue_nonce, server)
        except OSError:
            logger.warning("could not issue the desktop bootstrap nonce")
        logger.info("configuration bootstrap completed for user_id=%s", user.id)
    except BootstrapError as error:
        state = _failed_state(state, error)
        _write_state(server, state)
        logger.error("configuration bootstrap failed step=%s code=%s", error.step, error.code)
    except Exception as error:
        failure = BootstrapError("internal_error", step)
        state = _failed_state(state, failure)
        _write_state(server, state)
        logger.error(
            "configuration bootstrap failed step=%s code=internal_error type=%s",
            step,
            type(error).__name__,
        )
