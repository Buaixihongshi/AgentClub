"""GET / PUT /api/hub/settings — resolved settings view with source annotation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.app.config.providers import load_cli_providers
from backend.app.domain.agent_registry import get_agent_registry
from backend.app.settings import AgentOverride, ProviderOverride, Settings
from backend.app.storage import hub_settings


class RuntimePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_fake_cli: Optional[bool] = None
    max_turns: Optional[int] = None
    max_a2a_invocations: Optional[int] = None
    cli_timeout: Optional[int] = None


class AgentOverridePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_provider_id: Optional[str] = None
    fallback_provider_ids: Optional[List[str]] = None
    clear: Optional[bool] = None


class HubSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: Optional[Dict[str, ProviderOverride]] = None
    runtime: Optional[RuntimePatch] = None
    agents: Optional[Dict[str, AgentOverridePatch]] = None


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/hub", tags=["hub-settings"])

    def _settings_file_path():
        return hub_settings.default_path(settings)

    def _committed_provider_ids() -> set:
        return {p.id for p in load_cli_providers(settings.config_dir)}

    def _build_view() -> dict:
        file_data = hub_settings.load(_settings_file_path())
        file_runtime = file_data.get("runtime") or {}
        file_providers = file_data.get("providers") or {}

        committed = load_cli_providers(settings.config_dir)

        runtime_view = {}
        _RUNTIME_DEFAULTS = {
            "use_fake_cli": True,
            "max_turns": 6,
            "max_a2a_invocations": 8,
            "cli_timeout": 120,
        }
        for key, default in _RUNTIME_DEFAULTS.items():
            current = getattr(settings, key)
            if key in file_runtime:
                source = "user"
                value = file_runtime[key]
            elif current != default:
                source = "env"
                value = current
            else:
                source = "default"
                value = default
            runtime_view[key] = {"value": value, "source": source}

        provider_list: List[Dict[str, Any]] = []
        for p in committed:
            pid = p.id
            if pid in file_providers and "enabled" in file_providers[pid]:
                source = "user"
                value = file_providers[pid]["enabled"]
            else:
                source = "committed"
                value = p.enabled
            provider_list.append({
                "id": pid,
                "label": p.label,
                "enabled": {"value": value, "source": source},
            })

        file_agents = file_data.get("agents") or {}
        registry = get_agent_registry(settings.config_dir)
        agent_list: List[Dict[str, Any]] = []
        for agent in registry.all():
            override = file_agents.get(agent.id) if isinstance(file_agents, dict) else None
            base_default = agent.default_provider_id
            base_fallback = list(agent.fallback_provider_ids)
            if isinstance(override, dict) and "default_provider_id" in override:
                default_source = "user"
                default_value = override["default_provider_id"]
            else:
                default_source = "committed"
                default_value = base_default
            if isinstance(override, dict) and "fallback_provider_ids" in override:
                fallback_source = "user"
                fallback_value = list(override["fallback_provider_ids"])
            else:
                fallback_source = "committed"
                fallback_value = base_fallback
            agent_list.append({
                "id": agent.id,
                "display_name": agent.display_name,
                "default_provider_id": {"value": default_value, "source": default_source},
                "fallback_provider_ids": {"value": fallback_value, "source": fallback_source},
                "base_default_provider_id": base_default,
                "base_fallback_provider_ids": base_fallback,
            })

        return {
            "runtime": runtime_view,
            "providers": provider_list,
            "agents": agent_list,
        }

    @router.get("/settings")
    def get_hub_settings():
        return _build_view()

    @router.put("/settings")
    def put_hub_settings(patch: HubSettingsPatch):
        valid_ids = _committed_provider_ids()

        if patch.providers:
            for pid in patch.providers:
                if pid not in valid_ids:
                    raise HTTPException(
                        status_code=400,
                        detail="unknown provider: {pid}".format(pid=pid),
                    )

        if patch.agents:
            registry = get_agent_registry(settings.config_dir)
            valid_agent_ids = set(registry.ids())
            for aid, ov in patch.agents.items():
                if aid not in valid_agent_ids:
                    raise HTTPException(
                        status_code=400,
                        detail="unknown agent: {aid}".format(aid=aid),
                    )
                if ov.default_provider_id is not None and ov.default_provider_id not in valid_ids:
                    raise HTTPException(
                        status_code=400,
                        detail="unknown provider in agent override: {pid}".format(
                            pid=ov.default_provider_id,
                        ),
                    )
                if ov.fallback_provider_ids is not None:
                    for pid in ov.fallback_provider_ids:
                        if pid not in valid_ids:
                            raise HTTPException(
                                status_code=400,
                                detail="unknown provider in agent fallback: {pid}".format(pid=pid),
                            )

        file_path = _settings_file_path()
        existing = hub_settings.load(file_path)
        existing.setdefault("schema_version", 1)

        if patch.providers:
            providers_section = existing.setdefault("providers", {})
            for pid, ov in patch.providers.items():
                if ov.enabled is not None:
                    providers_section.setdefault(pid, {})["enabled"] = ov.enabled

        if patch.runtime:
            runtime_section = existing.setdefault("runtime", {})
            for field_name in ("use_fake_cli", "max_turns", "max_a2a_invocations", "cli_timeout"):
                val = getattr(patch.runtime, field_name)
                if val is not None:
                    runtime_section[field_name] = val

        if patch.agents:
            agents_section = existing.setdefault("agents", {})
            for aid, ov in patch.agents.items():
                if ov.clear:
                    agents_section.pop(aid, None)
                    continue
                section = agents_section.setdefault(aid, {})
                if ov.default_provider_id is not None:
                    section["default_provider_id"] = ov.default_provider_id
                if ov.fallback_provider_ids is not None:
                    section["fallback_provider_ids"] = list(ov.fallback_provider_ids)

        hub_settings.save(file_path, existing)
        return _build_view()

    return router
