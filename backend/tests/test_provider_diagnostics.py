import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.app.config.providers import (
    ProviderProbeError,
    diagnose_providers,
    load_cli_providers,
    run_provider_probe,
)
from backend.app.main import create_app


def write_provider_config(config_dir, providers):
    config_dir.mkdir()
    (config_dir / "cli-providers.json").write_text(
        json.dumps(providers),
        encoding="utf-8",
    )


def test_load_cli_providers_reads_config_file(tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": ["--prompt"],
                "enabled": True,
            }
        ],
    )

    providers = load_cli_providers(config_dir)

    assert providers[0].id == "demo"
    assert providers[0].label == "Demo CLI"
    assert providers[0].args == ["--prompt"]
    assert providers[0].enabled is True


def test_diagnose_providers_reports_missing_command(tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "missing",
                "label": "Missing CLI",
                "command": "definitely-not-installed-yiren",
                "args": [],
                "enabled": True,
            }
        ],
    )

    diagnostics = diagnose_providers(config_dir)

    assert diagnostics[0]["id"] == "missing"
    assert diagnostics[0]["available"] is False
    assert diagnostics[0]["path"] is None
    assert "not found" in diagnostics[0]["error"]


def test_diagnose_providers_reads_version(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": [],
                "enabled": False,
            }
        ],
    )
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/demo")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout="demo 1.2.3\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    diagnostics = diagnose_providers(config_dir)

    assert diagnostics[0]["available"] is True
    assert diagnostics[0]["path"] == "/usr/local/bin/demo"
    assert diagnostics[0]["version"] == "demo 1.2.3"
    assert diagnostics[0]["error"] is None


def test_diagnose_providers_marks_fake_as_internal_available(tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "fake",
                "label": "Fake CLI",
                "command": "fake",
                "args": [],
                "enabled": True,
            }
        ],
    )

    diagnostics = diagnose_providers(config_dir)

    assert diagnostics[0]["available"] is True
    assert diagnostics[0]["path"] == "internal"
    assert diagnostics[0]["version"] == "internal"
    assert diagnostics[0]["error"] is None


def test_get_providers_returns_provider_diagnostics(temp_settings, monkeypatch):
    monkeypatch.setattr(
        "shutil.which",
        lambda command: "/usr/local/bin/{command}".format(command=command),
    )

    def fake_run(command, **kwargs):
        provider_command = command[0]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="{command} 1.0.0\n".format(command=provider_command),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(create_app(temp_settings))

    response = client.get("/api/providers")

    assert response.status_code == 200
    providers = response.json()
    assert [provider["id"] for provider in providers] == [
        "claude",
        "codex",
        "antigravity",
    ]
    for provider in providers:
        assert "available" in provider


def test_get_providers_uses_saved_enablement_override(temp_settings, monkeypatch):
    from backend.app.storage import hub_settings

    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/" + command)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="demo 1.0.0\n", stderr="",
        ),
    )
    hub_settings.save(
        hub_settings.default_path(temp_settings),
        {"schema_version": 1, "providers": {"codex": {"enabled": True}}},
    )
    client = TestClient(create_app(temp_settings))

    response = client.get("/api/providers")

    assert response.status_code == 200
    codex = next(provider for provider in response.json() if provider["id"] == "codex")
    assert codex["enabled"] is True


def test_probe_unknown_provider_returns_404(temp_settings, tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": ["--prompt"],
                "enabled": True,
            }
        ],
    )
    temp_settings.config_dir = config_dir
    client = TestClient(create_app(temp_settings))

    response = client.post(
        "/api/providers/missing/probe",
        json={"prompt": "hello"},
    )

    assert response.status_code == 404


def test_probe_disabled_provider_returns_409(temp_settings, tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": ["--prompt"],
                "enabled": False,
            }
        ],
    )
    temp_settings.config_dir = config_dir
    client = TestClient(create_app(temp_settings))

    response = client.post(
        "/api/providers/demo/probe",
        json={"prompt": "hello"},
    )

    assert response.status_code == 409


def test_probe_enabled_provider_runs_configured_command_and_args(
    temp_settings,
    tmp_path,
    monkeypatch,
):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": ["--prompt"],
                "enabled": True,
            }
        ],
    )
    temp_settings.config_dir = config_dir
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="probe ok\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = TestClient(create_app(temp_settings))

    response = client.post(
        "/api/providers/demo/probe",
        json={"prompt": "hello"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider_id": "demo",
        "status": "ok",
        "output": "probe ok",
        "summary": "probe ok",
    }
    assert calls == [
        (
            ["demo", "--prompt", "hello"],
            {
                "stdin": subprocess.DEVNULL,
                "text": True,
                "capture_output": True,
                "check": False,
                "timeout": 30,
            },
        )
    ]


def test_probe_internal_fake_provider_does_not_use_subprocess(
    temp_settings,
    tmp_path,
    monkeypatch,
):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "fake",
                "label": "Fake CLI",
                "command": "fake",
                "args": [],
                "enabled": True,
            }
        ],
    )
    temp_settings.config_dir = config_dir

    def fail_run(command, **kwargs):
        raise AssertionError("fake provider should not spawn subprocess")

    monkeypatch.setattr(subprocess, "run", fail_run)
    client = TestClient(create_app(temp_settings))

    response = client.post(
        "/api/providers/fake/probe",
        json={"prompt": "hello"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider_id": "fake",
        "status": "ok",
        "output": "[fake] hello",
        "summary": "[fake] hello",
    }


def test_probe_rejects_request_body_command_and_args(temp_settings, tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": ["--prompt"],
                "enabled": True,
            }
        ],
    )
    temp_settings.config_dir = config_dir
    client = TestClient(create_app(temp_settings))

    response = client.post(
        "/api/providers/demo/probe",
        json={
            "prompt": "hello",
            "command": "untrusted",
            "args": ["--unsafe"],
        },
    )

    assert response.status_code == 422


def test_run_provider_probe_rejects_empty_prompt(tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": [],
                "enabled": True,
            }
        ],
    )

    with pytest.raises(ProviderProbeError, match="prompt is required"):
        run_provider_probe(config_dir, "demo", "  ")


def test_run_provider_probe_rejects_prompt_over_2000_characters(tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": [],
                "enabled": True,
            }
        ],
    )

    with pytest.raises(ProviderProbeError, match="at most 2000"):
        run_provider_probe(config_dir, "demo", "x" * 2001)


def test_run_provider_probe_rejects_missing_command(tmp_path):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": " ",
                "args": [],
                "enabled": True,
            }
        ],
    )

    with pytest.raises(ProviderProbeError, match="command is required"):
        run_provider_probe(config_dir, "demo", "hello")


def test_run_provider_probe_reports_timeout(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": [],
                "enabled": True,
            }
        ],
    )

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ProviderProbeError, match="timed out"):
        run_provider_probe(config_dir, "demo", "hello")


def test_run_provider_probe_reports_os_error(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": [],
                "enabled": True,
            }
        ],
    )

    def fake_run(command, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ProviderProbeError, match="permission denied"):
        run_provider_probe(config_dir, "demo", "hello")


def test_run_provider_probe_reports_non_zero_exit(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    write_provider_config(
        config_dir,
        [
            {
                "id": "demo",
                "label": "Demo CLI",
                "command": "demo",
                "args": [],
                "enabled": True,
            }
        ],
    )

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr="failed probe\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ProviderProbeError, match="failed probe"):
        run_provider_probe(config_dir, "demo", "hello")


# ── S3: provider overrides ────────────────────────────────────────

def test_load_cli_providers_applies_enabled_override(tmp_path):
    from backend.app.settings import ProviderOverride

    config_dir = tmp_path / "config"
    write_provider_config(config_dir, [
        {"id": "claude", "label": "Claude", "command": "claude", "args": [], "enabled": False, "mcp_enabled": True},
    ])
    overrides = {"claude": ProviderOverride(enabled=True)}
    providers = load_cli_providers(config_dir, overrides=overrides)
    assert len(providers) == 1
    assert providers[0].enabled is True
    assert providers[0].command == "claude"
    assert providers[0].mcp_enabled is True


def test_load_cli_providers_ignores_unknown_provider_override(tmp_path):
    from backend.app.settings import ProviderOverride

    config_dir = tmp_path / "config"
    write_provider_config(config_dir, [
        {"id": "fake", "label": "Fake", "command": "echo", "args": [], "enabled": True},
    ])
    overrides = {"phantom": ProviderOverride(enabled=True)}
    providers = load_cli_providers(config_dir, overrides=overrides)
    assert len(providers) == 1
    assert providers[0].id == "fake"


def test_diagnose_providers_reflects_overrides(tmp_path):
    from backend.app.settings import ProviderOverride

    config_dir = tmp_path / "config"
    write_provider_config(config_dir, [
        {"id": "fake", "label": "Fake", "command": "echo", "args": [], "enabled": False},
    ])
    overrides = {"fake": ProviderOverride(enabled=True)}
    results = diagnose_providers(config_dir, overrides=overrides)
    assert results[0]["enabled"] is True


def test_provider_router_init_recovers_crashed_agy_global_config(tmp_path, monkeypatch):
    """A hard-kill can leave our temp yiren_hub config in the user's global file.
    Constructing ProviderRouterAdapter must restore it from the sentinel."""
    import json as _json
    from pathlib import Path as _Path
    from backend.app.adapters import antigravity_config as _agc
    from backend.app.adapters.provider_router import ProviderRouterAdapter

    monkeypatch.setattr(_Path, "home", classmethod(lambda cls: tmp_path))
    cfg = _agc.global_mcp_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # Crash leftovers: sentinel says original did not exist; stale yiren config present.
    _agc._sentinel_path().write_text(_json.dumps({"existed": False, "content": None}), encoding="utf-8")
    cfg.write_text('{"mcpServers": {"yiren_hub": {"command": "dead"}}}', encoding="utf-8")

    ProviderRouterAdapter(config_dir=tmp_path / "config")

    assert not cfg.exists()
    assert not _agc._sentinel_path().exists()
