"""Hub settings file IO layer.

Reads/writes the local ``hub_settings.json`` file that stores user overrides
for provider enablement and runtime parameters.  The file is gitignored —
it never pollutes committed config.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.settings import Settings


class HubSettingsError(Exception):
    pass


def load(path: Path) -> dict:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HubSettingsError("invalid JSON in {p}: {e}".format(p=path, e=exc)) from exc
    if not isinstance(data, dict):
        raise HubSettingsError("expected JSON object in {p}".format(p=path))
    return data


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


def default_path(settings: "Settings") -> Path:
    return settings.database_path.parent / "hub_settings.json"


def retire_provider(path: Path, provider_id: str, replacement_id: str) -> bool:
    """Remove a retired provider from user overrides and route references.

    Existing mission/message history is intentionally untouched. Returns True
    when the settings file was rewritten.
    """
    data = load(path)
    changed = False

    providers = data.get("providers")
    if isinstance(providers, dict) and provider_id in providers:
        providers.pop(provider_id, None)
        changed = True

    agents = data.get("agents")
    if isinstance(agents, dict):
        for override in agents.values():
            if not isinstance(override, dict):
                continue
            if override.get("default_provider_id") == provider_id:
                override["default_provider_id"] = replacement_id
                changed = True
            fallbacks = override.get("fallback_provider_ids")
            if isinstance(fallbacks, list) and provider_id in fallbacks:
                rewritten = []
                for candidate in fallbacks:
                    candidate = replacement_id if candidate == provider_id else candidate
                    if candidate not in rewritten and candidate != override.get("default_provider_id"):
                        rewritten.append(candidate)
                override["fallback_provider_ids"] = rewritten
                changed = True

    onboarding = data.get("onboarding")
    if isinstance(onboarding, dict):
        verified = onboarding.get("verified_providers")
        if isinstance(verified, dict) and provider_id in verified:
            verified.pop(provider_id, None)
            changed = True
        if onboarding.get("verification_provider_id") == provider_id:
            for key in (
                "verification_provider_id",
                "verification_mission_id",
                "verification_started_at",
                "verification_error",
            ):
                onboarding.pop(key, None)
            changed = True
        if onboarding.get("activated_provider_id") == provider_id:
            onboarding.pop("activated_provider_id", None)
            onboarding.pop("provider_activated_at", None)
            changed = True

    if changed:
        data.setdefault("schema_version", 1)
        save(path, data)
    return changed
