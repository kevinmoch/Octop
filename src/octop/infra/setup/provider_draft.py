from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class ProviderModelDraft(BaseModel):
    id: str
    name: str
    enabled: bool = True
    input: list[str] = Field(default_factory=list)
    thinking: Any | None = None
    reasoning: bool | None = None


class ProviderDraftBody(BaseModel):
    name: str
    type: str
    api_key: str = ""
    base_url: str | None = None
    models: list[ProviderModelDraft] = Field(default_factory=list)
    extras: dict[str, Any] | None = None


def validated_provider_parts(
    draft: ProviderDraftBody,
) -> tuple[str, str, list[dict[str, Any]]]:
    from octop.infra.errors import ErrorCode, OctopError

    api_key = (draft.api_key or "").strip()
    base_url = (draft.base_url or "").strip()
    if not api_key:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "api_key is required", status=400)
    if not base_url:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "base_url is required", status=400)

    def model_entry(model: ProviderModelDraft) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": model.id,
            "name": model.name,
            "enabled": model.enabled,
            "input": model.input,
            "thinking": model.thinking,
        }
        if model.reasoning:
            entry["reasoning"] = True
        return entry

    models = [model_entry(model) for model in draft.models if model.enabled]
    if not models and draft.models:
        models = [model_entry(draft.models[0])]
        models[0]["enabled"] = True
    if not models:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "at least one model is required", status=400)
    return api_key, base_url, models


async def apply_provider_draft(
    server: Any, draft: ProviderDraftBody, *, upsert: bool = False
) -> None:
    api_key, base_url, models = validated_provider_parts(draft)
    values = {
        "kind": draft.type,
        "base_url": base_url,
        "api_key": api_key,
        "models_json": json.dumps(models),
        "extra_json": json.dumps(draft.extras) if draft.extras else None,
    }
    existing = server.services.provider_repo.get_by_name(draft.name)
    if upsert and existing is not None:
        server.services.provider_repo.update(existing.id, **values)
    else:
        server.services.provider_repo.create(name=draft.name, **values)
    server.services.settings_repo.set_active_model(draft.name, models[0]["id"])
    if server.app_runtime is not None:
        await server.app_runtime.agent_registry.on_provider_changed()
