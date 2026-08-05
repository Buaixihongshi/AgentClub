import os
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel


class ProviderOverride(BaseModel):
    enabled: Optional[bool] = None


class AgentOverride(BaseModel):
    default_provider_id: Optional[str] = None
    fallback_provider_ids: Optional[list[str]] = None


class Settings(BaseModel):
    database_path: Path = Path("data/yirenhub.sqlite3")
    artifact_root: Path = Path("artifacts")
    config_dir: Path = Path("config")
    use_fake_cli: bool = True
    max_turns: int = 6
    max_a2a_invocations: int = 8
    cli_timeout: int = 120
    provider_overrides: Dict[str, ProviderOverride] = {}
    agent_overrides: Dict[str, AgentOverride] = {}


_RUNTIME_FIELDS = {"use_fake_cli", "max_turns", "max_a2a_invocations", "cli_timeout"}


def get_settings() -> Settings:
    database_path = _env_path("YIREN_DATABASE_PATH", default=Path("data/yirenhub.sqlite3"))

    kwargs: dict = dict(
        database_path=database_path,
        artifact_root=_env_path("YIREN_ARTIFACT_ROOT", default=Path("artifacts")),
        config_dir=_env_path("YIREN_CONFIG_DIR", default=Path("config")),
        use_fake_cli=_env_bool("YIREN_USE_FAKE_CLI", default=True),
        max_turns=_env_int("YIREN_MAX_TURNS", default=6),
        max_a2a_invocations=_env_int("YIREN_MAX_A2A_INVOCATIONS", default=8),
        cli_timeout=_env_int("YIREN_CLI_TIMEOUT", default=120),
    )

    from backend.app.storage.hub_settings import default_path, load, retire_provider
    settings_path = default_path(Settings(database_path=database_path))
    retire_provider(settings_path, "gemini", "antigravity")
    file_data = load(settings_path)

    runtime = file_data.get("runtime")
    if isinstance(runtime, dict):
        for key, value in runtime.items():
            if key not in _RUNTIME_FIELDS:
                continue
            kwargs[key] = value

    provider_overrides: Dict[str, ProviderOverride] = {}
    providers_raw = file_data.get("providers")
    if isinstance(providers_raw, dict):
        for pid, pdata in providers_raw.items():
            if isinstance(pdata, dict):
                provider_overrides[pid] = ProviderOverride(**{
                    k: v for k, v in pdata.items() if k in {"enabled"}
                })
    kwargs["provider_overrides"] = provider_overrides

    agent_overrides: Dict[str, AgentOverride] = {}
    agents_raw = file_data.get("agents")
    if isinstance(agents_raw, dict):
        for aid, adata in agents_raw.items():
            if isinstance(adata, dict):
                agent_overrides[aid] = AgentOverride(**{
                    k: v for k, v in adata.items()
                    if k in {"default_provider_id", "fallback_provider_ids"}
                })
    kwargs["agent_overrides"] = agent_overrides

    return Settings(**kwargs)


def snapshot_settings(app_settings: "Settings") -> "Settings":
    """Re-read ``hub_settings.json`` for fresh runtime / provider / agent
    overrides, preserving the infrastructure paths from the app-level Settings.

    Called at mission start so UI toggles take effect on the next mission
    without restarting the backend. Every code path that spawns a
    ``MissionRunner`` must go through this — otherwise the runner sees the
    settings that were cached at process start.
    """
    from backend.app.storage.hub_settings import default_path, load, retire_provider
    settings_path = default_path(app_settings)
    retire_provider(settings_path, "gemini", "antigravity")
    file_data = load(settings_path)

    kwargs: dict = {
        "database_path": app_settings.database_path,
        "artifact_root": app_settings.artifact_root,
        "config_dir": app_settings.config_dir,
        "use_fake_cli": app_settings.use_fake_cli,
        "max_turns": app_settings.max_turns,
        "max_a2a_invocations": app_settings.max_a2a_invocations,
        "cli_timeout": app_settings.cli_timeout,
    }

    runtime = file_data.get("runtime") or {}
    for key, value in runtime.items():
        if key in _RUNTIME_FIELDS:
            kwargs[key] = value

    provider_overrides: Dict[str, ProviderOverride] = {}
    for pid, pdata in (file_data.get("providers") or {}).items():
        if isinstance(pdata, dict):
            provider_overrides[pid] = ProviderOverride(**{
                k: v for k, v in pdata.items() if k in {"enabled"}
            })
    kwargs["provider_overrides"] = provider_overrides

    agent_overrides: Dict[str, AgentOverride] = {}
    for aid, adata in (file_data.get("agents") or {}).items():
        if isinstance(adata, dict):
            agent_overrides[aid] = AgentOverride(**{
                k: v for k, v in adata.items()
                if k in {"default_provider_id", "fallback_provider_ids"}
            })
    kwargs["agent_overrides"] = agent_overrides

    return Settings(**kwargs)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return Path(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)
