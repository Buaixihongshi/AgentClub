"""GET / PUT /api/hub/settings — resolved settings view with source annotation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.app.config.providers import load_cli_providers
from backend.app.domain.agent_registry import get_agent_registry
from backend.app.settings import AgentOverride, ProviderOverride, Settings
from backend.app.storage import hub_settings
from backend.app.storage import api_connections


_settings_write_lock = threading.RLock()


class RuntimePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_fake_cli: Optional[bool] = None
    max_turns: Optional[int] = None
    max_a2a_invocations: Optional[int] = None
    cli_timeout: Optional[int] = None
    a2a_idle_timeout: Optional[int] = Field(default=None, ge=0)


class AgentOverridePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_provider_id: Optional[str] = None
    fallback_provider_ids: Optional[List[str]] = None
    provider_models: Optional[Dict[str, str]] = None
    clear: Optional[bool] = None
    fallback_context_approved: Optional[bool] = None


class HubSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: Optional[Dict[str, Dict[str, Any]]] = None
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
            "cli_timeout": 300,
            "a2a_idle_timeout": 90,
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
            has_model_override = isinstance(override, dict) and "provider_models" in override
            agent_list.append({
                "id": agent.id,
                "display_name": agent.display_name,
                "default_provider_id": {"value": default_value, "source": default_source},
                "fallback_provider_ids": {"value": fallback_value, "source": fallback_source},
                "provider_models": {
                    "value": override["provider_models"] if has_model_override else agent.provider_models,
                    "source": "user" if has_model_override else "committed",
                },
                "fallback_context_approved": {"value": bool(override.get("fallback_context_approved", False)) if isinstance(override, dict) else False, "source": "user" if isinstance(override, dict) and "fallback_context_approved" in override else "committed"},
                "base_provider_models": agent.provider_models,
                "base_default_provider_id": base_default,
                "base_fallback_provider_ids": base_fallback,
            })

        return {
            "connections": api_connections.connection_views(settings, file_data),
            "runtime": runtime_view,
            "providers": provider_list,
            "agents": agent_list,
        }

    @router.get("/connections")
    def get_connections():
        return {"connections": api_connections.connection_views(settings)}

    def _catalog(cid, refresh=False):
        from backend.app.adapters.api_catalog import catalog
        data = hub_settings.load(_settings_file_path())
        connection = data.get("connections", {}).get(cid)
        if connection is None:
            from backend.app.adapters.cli_catalog import cli_catalog
            provider = next((p for p in load_cli_providers(settings.config_dir) if p.id == cid), None)
            if provider is None:
                raise HTTPException(status_code=404, detail="unknown connection")
            return cli_catalog(provider, scope=str(settings.config_dir), refresh=refresh)
        return catalog(settings, cid, connection, refresh=refresh)

    @router.get("/connections/{connection_id}/models")
    def get_models(connection_id: str):
        return _catalog(connection_id)

    @router.post("/connections/{connection_id}/models/refresh")
    def refresh_models(connection_id: str):
        return _catalog(connection_id, refresh=True)

    @router.get("/settings")
    def get_hub_settings():
        return _build_view()

    @router.put("/settings")
    def put_hub_settings(patch: HubSettingsPatch):
        with _settings_write_lock:
            return _put_hub_settings(patch)

    def _put_hub_settings(patch: HubSettingsPatch):
        file_path = _settings_file_path()
        existing = hub_settings.load(file_path)
        previous_connections = existing.get("connections", {})
        try:
            connections, pending_keys = api_connections.prepare_connections(previous_connections, patch.connections or {})
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="invalid connection configuration; verify endpoint, credential action and field values") from None
        valid_ids = _committed_provider_ids() | set(connections)

        if patch.providers:
            for pid in patch.providers:
                if pid not in _committed_provider_ids():
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
                if ov.provider_models is not None:
                    for pid, model in ov.provider_models.items():
                        if pid not in valid_ids or pid not in {"codex", "claude", "antigravity"} | set(connections):
                            raise HTTPException(status_code=400, detail=f"unsupported model provider: {pid}")
                        if model != model.strip() or len(model) > 200 or any(ord(c) < 32 for c in model):
                            raise HTTPException(status_code=400, detail="invalid model ID")
                if ov.fallback_provider_ids is not None:
                    for pid in ov.fallback_provider_ids:
                        if pid not in valid_ids:
                            raise HTTPException(
                                status_code=400,
                                detail="unknown provider in agent fallback: {pid}".format(pid=pid),
                            )

        existing.setdefault("schema_version", 1)

        if patch.providers:
            providers_section = existing.setdefault("providers", {})
            for pid, ov in patch.providers.items():
                if ov.enabled is not None:
                    providers_section.setdefault(pid, {})["enabled"] = ov.enabled

        if patch.runtime:
            runtime_section = existing.setdefault("runtime", {})
            for field_name in (
                "use_fake_cli",
                "max_turns",
                "max_a2a_invocations",
                "cli_timeout",
                "a2a_idle_timeout",
            ):
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
                if ov.provider_models is not None:
                    section["provider_models"] = dict(ov.provider_models)
                if ov.default_provider_id is not None:
                    section["default_provider_id"] = ov.default_provider_id
                if ov.fallback_provider_ids is not None:
                    if section.get("fallback_provider_ids") != ov.fallback_provider_ids:
                        section["fallback_context_approved"] = False
                    section["fallback_provider_ids"] = list(ov.fallback_provider_ids)
                if ov.fallback_context_approved is not None:
                    section["fallback_context_approved"] = ov.fallback_context_approved

        registry = get_agent_registry(settings.config_dir)
        for agent in registry.all():
            override = existing.get("agents", {}).get(agent.id, {})
            primary = override.get("default_provider_id", agent.default_provider_id)
            fallbacks = override.get("fallback_provider_ids", agent.fallback_provider_ids)
            models = override.get("provider_models", agent.provider_models)
            for pid in [primary, *fallbacks]:
                if pid.startswith("api-") and pid not in valid_ids:
                    raise HTTPException(status_code=400, detail="connection is still referenced by an agent")
                if pid in connections and not models.get(pid, "").strip():
                    raise HTTPException(status_code=400, detail="API connections require an explicit model ID")
            for pid in fallbacks:
                old, new = previous_connections.get(pid), connections.get(pid)
                if old and new and any(old.get(k) != new.get(k) for k in ("base_url", "protocol", "allow_private")):
                    existing.setdefault("agents", {}).setdefault(agent.id, {})["fallback_context_approved"] = False
        # Approval binds the actual API endpoint/protocol/policy, not just a
        # reusable connection ID. A new fallback always needs renewed consent.
        from backend.app.adapters.fallback_policy import endpoint_binding
        for agent in registry.all():
            section = existing.get("agents", {}).get(agent.id)
            if section is None:
                continue
            fallbacks = section.get("fallback_provider_ids", agent.fallback_provider_ids)
            if section.get("fallback_context_approved"):
                section["fallback_context_targets"] = {pid: endpoint_binding(connections[pid])
                    for pid in fallbacks if pid in connections}
            else:
                section.pop("fallback_context_targets", None)
        staged_credentials = {}
        try:
            for cid, value in pending_keys.items():
                reference = api_connections.write_key(settings, value)
                staged_credentials[cid] = {"key_ref": reference}
                connections[cid]["key_ref"] = reference
            existing["connections"] = connections
            hub_settings.save(file_path, existing)
        except OSError:
            api_connections.retire_keys(settings, staged_credentials, {})
            raise HTTPException(status_code=500, detail="connection configuration could not be saved") from None
        api_connections.retire_keys(settings, previous_connections, connections)
        return _build_view()

    return router
