from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from backend.app.domain.models import CLIProvider

if TYPE_CHECKING:
    from backend.app.settings import ProviderOverride


VERSION_TIMEOUT_SECONDS = 5
PROBE_TIMEOUT_SECONDS = 30
MAX_PROBE_PROMPT_LENGTH = 2000
INTERNAL_PROVIDER_IDS = {"fake"}


class ProviderNotFoundError(Exception):
    pass


class ProviderDisabledError(Exception):
    pass


class ProviderProbeError(Exception):
    pass


def load_cli_providers(
    config_dir: Path,
    overrides: Optional[Dict[str, "ProviderOverride"]] = None,
) -> list[CLIProvider]:
    config_path = config_dir / "cli-providers.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    providers = [CLIProvider.model_validate(item) for item in data]
    if overrides:
        for p in providers:
            ov = overrides.get(p.id)
            if ov is not None and ov.enabled is not None:
                p.enabled = ov.enabled
    return providers


def get_cli_provider(
    config_dir: Path,
    provider_id: str,
    overrides: Optional[Dict[str, "ProviderOverride"]] = None,
) -> CLIProvider:
    for provider in load_cli_providers(config_dir, overrides=overrides):
        if provider.id == provider_id:
            return provider
    raise ProviderNotFoundError("provider not found: {provider_id}".format(
        provider_id=provider_id
    ))


def diagnose_providers(
    config_dir: Path,
    overrides: Optional[Dict[str, "ProviderOverride"]] = None,
) -> list[dict[str, Any]]:
    return [_diagnose_provider(provider) for provider in load_cli_providers(config_dir, overrides=overrides)]


def diagnose_provider(
    config_dir: Path,
    provider_id: str,
    overrides: Optional[Dict[str, "ProviderOverride"]] = None,
) -> dict[str, Any]:
    """Diagnose one provider without probing every configured CLI."""
    return _diagnose_provider(
        get_cli_provider(config_dir, provider_id, overrides=overrides)
    )


def run_provider_probe(
    config_dir: Path,
    provider_id: str,
    prompt: str,
    overrides: Optional[Dict[str, "ProviderOverride"]] = None,
) -> dict[str, str]:
    prompt = prompt.strip()
    if not prompt:
        raise ProviderProbeError("prompt is required")
    if len(prompt) > MAX_PROBE_PROMPT_LENGTH:
        raise ProviderProbeError("prompt must be at most 2000 characters")

    provider = get_cli_provider(config_dir, provider_id, overrides=overrides)
    if not provider.enabled:
        raise ProviderDisabledError("provider is disabled: {provider_id}".format(
            provider_id=provider_id
        ))
    if _is_internal_provider(provider):
        output = "[{provider_id}] {prompt}".format(
            provider_id=provider.id,
            prompt=prompt,
        )
        return {
            "provider_id": provider.id,
            "status": "ok",
            "output": output,
            "summary": _summarize_probe_output(output),
        }
    if not provider.command.strip():
        raise ProviderProbeError("provider command is required")

    command = [provider.command, *provider.args, prompt]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderProbeError("provider probe timed out") from exc
    except OSError as exc:
        raise ProviderProbeError(str(exc)) from exc

    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        raise ProviderProbeError(
            output or "provider probe exited with {code}".format(
                code=completed.returncode
            )
        )

    return {
        "provider_id": provider.id,
        "status": "ok",
        "output": output,
        "summary": _summarize_probe_output(output),
    }


def _diagnose_provider(provider: CLIProvider) -> dict[str, Any]:
    if _is_internal_provider(provider):
        return {
            "id": provider.id,
            "label": provider.label,
            "command": provider.command,
            "args": provider.args,
            "enabled": provider.enabled,
            "available": True,
            "path": "internal",
            "version": "internal",
            "error": None,
        }

    resolved_path = shutil.which(provider.command)
    result = {
        "id": provider.id,
        "label": provider.label,
        "command": provider.command,
        "args": provider.args,
        "enabled": provider.enabled,
        "available": resolved_path is not None,
        "path": resolved_path,
        "version": None,
        "error": None,
    }

    if resolved_path is None:
        result["error"] = "command not found: {command}".format(
            command=provider.command
        )
        return result

    try:
        completed = subprocess.run(
            [provider.command, "--version"],
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        result["error"] = "version check timed out"
        return result
    except OSError as exc:
        result["error"] = str(exc)
        return result

    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        result["error"] = output or "version check exited with {code}".format(
            code=completed.returncode
        )
        return result

    result["version"] = output
    return result


def _is_internal_provider(provider: CLIProvider) -> bool:
    return provider.id in INTERNAL_PROVIDER_IDS


def _summarize_probe_output(output: str) -> str:
    if not output:
        return "Probe completed"
    first_line = output.splitlines()[0].strip()
    if len(first_line) <= 240:
        return first_line
    return "{summary}...".format(summary=first_line[:237])
