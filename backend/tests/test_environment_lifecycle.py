import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from scripts import environment_cli as cli
from scripts import environment_common as common
from scripts import environment_supervisor as supervisor
from scripts import environment_cli_config as cli_config


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def source_repo(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "main")
    (root / ".gitignore").write_text(".yiren/\n")
    (root / "value").write_text("version 1")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", "commit", "-qm", "initial")
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(root), str(remote)], check=True, capture_output=True)
    git(root, "remote", "add", "origin", str(remote))
    git(root, "fetch", "origin")
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setenv("AGENTCLUB_ENV_HOME", str(tmp_path / "environments"))
    return root


def test_alpha_sync_is_main_only_clean_and_stopped(source_repo, monkeypatch):
    alpha = cli.sync_alpha()
    assert git(alpha, "rev-parse", "HEAD") == git(source_repo, "rev-parse", "origin/main")
    assert git(alpha, "branch", "--show-current") == ""
    (alpha / "value").write_text("uncommitted work")
    with pytest.raises(common.EnvironmentError, match="local modifications"): cli.sync_alpha()
    assert (alpha / "value").read_text() == "uncommitted work"
    git(alpha, "restore", "value")
    monkeypatch.setattr(cli, "checkout_listening", lambda _: True)
    with pytest.raises(common.EnvironmentError, match="running"): cli.sync_alpha()


def test_features_have_separate_branches_and_resume_without_reset(source_repo):
    first = cli.feature("one")
    second = cli.feature("two")
    assert first != second
    assert git(first, "branch", "--show-current") == "codex/task-one"
    (first / "value").write_text("keep user change")
    assert cli.feature("one") == first
    assert (first / "value").read_text() == "keep user change"
    with pytest.raises(common.EnvironmentError): cli.feature("../escape")


def test_generic_start_cannot_label_a_feature_checkout_as_alpha(source_repo, monkeypatch):
    args = SimpleNamespace(real_cli=False, reload=False)
    monkeypatch.setattr(cli, "start", lambda *a, **k: pytest.fail("must not start a mislabeled alpha"))
    with pytest.raises(common.EnvironmentError, match="dedicated main mirror"):
        cli.launch(args, source_repo, "alpha")


def test_protected_or_unmanaged_live_checkout_cannot_be_used_for_development(source_repo, monkeypatch):
    args = SimpleNamespace(real_cli=False, reload=False)
    protected = common.home(source_repo) / "releases" / ("a" * 40)
    with pytest.raises(common.EnvironmentError, match="protected"):
        cli.launch(args, protected, "development")
    monkeypatch.setattr(cli, "checkout_listening", lambda _: True)
    monkeypatch.setattr(cli, "read_record", lambda *a: None)
    with pytest.raises(common.EnvironmentError, match="serving another instance"):
        cli.launch(args, source_repo, "development")


def test_state_isolation_and_explicit_real_mode(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    config = root / "config"
    config.mkdir(parents=True)
    (config / "agents.json").write_text("[]")
    providers = [{"id": "fake", "enabled": True}, {"id": "codex", "enabled": False}]
    (config / "cli-providers.json").write_text(json.dumps(providers))
    monkeypatch.setenv("YIREN_DATABASE_PATH", "do-not-inherit.sqlite3")
    dev = common.environment(root, "development", backend=8100, frontend=5174)
    real = common.environment(root, "development", real=True, backend=8400, frontend=5180)
    alpha = common.environment(root, "alpha", backend=8300, frontend=5176)
    for key in ("YIREN_DATABASE_PATH", "YIREN_CONFIG_DIR", "YIREN_ARTIFACT_ROOT", "YIREN_RUNTIME_DIR", "YIREN_MAINTENANCE_WORKTREE_ROOT"):
        assert len({dev[key], real[key], alpha[key]}) == 3
    assert dev["YIREN_DEMO_ONLY"] == "true" and real["YIREN_DEMO_ONLY"] == "false"
    assert json.loads((config / "cli-providers.json").read_text()) == providers
    assert real["YIREN_BACKEND_ORIGIN"] == "http://127.0.0.1:8400"


@pytest.fixture
def daily_cli(source_repo):
    config = source_repo / 'config'
    config.mkdir()
    agents = {'agents': [{'id': 'planner', 'default_provider_id': 'codex', 'fallback_provider_ids': []},
                         {'id': 'designer', 'default_provider_id': 'codex', 'fallback_provider_ids': []}]}
    providers = [{'id': 'fake', 'label': 'Demo', 'command': 'fake', 'enabled': True},
                 {'id': 'codex', 'label': 'Codex', 'command': 'codex', 'args': ['exec'], 'enabled': False},
                 {'id': 'antigravity', 'label': 'AGY', 'command': 'agy', 'args': ['-p'], 'enabled': False}]
    for path, data in ((config / 'agents.json', agents), (config / 'cli-providers.json', providers)):
        path.write_text(json.dumps(data))
    state = common.home(source_repo) / 'runtime-state'
    common.write_json(state / 'config/agents.json', agents)
    common.write_json(state / 'config/cli-providers.json', providers)
    overrides = {'runtime': {'use_fake_cli': False, 'cli_timeout': 600},
        'providers': {'codex': {'enabled': True}, 'antigravity': {'enabled': True}},
        'agents': {'designer': {'default_provider_id': 'antigravity', 'fallback_provider_ids': ['codex'],
            'provider_models': {'antigravity': 'daily-model', 'api-private': 'private-model'}}},
        'connections': {'api-private': {'key_ref': 'private-key-reference', 'base_url': 'https://private.invalid'}}}
    common.write_json(state / 'data/hub_settings.json', overrides)
    (state / 'data/daily-conversations.sqlite3').write_bytes(b'daily data must never be copied')
    return source_repo, state


def test_alpha_inherits_effective_daily_cli_without_copying_data_or_connections(daily_cli):
    root, source = daily_cli
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    env = common.environment(root, 'alpha', inherit_cli=True, backend=8300, frontend=5176)
    state = root / '.yiren/alpha'
    settings = json.loads((state / 'data/hub_settings.json').read_text())
    assert env['YIREN_USE_FAKE_CLI'] == 'false' and env['YIREN_DEMO_ONLY'] == 'false'
    assert env['YIREN_DATABASE_PATH'] != str(source / 'data/yirenhub.sqlite3')
    assert settings['agents']['designer']['default_provider_id'] == 'antigravity'
    assert settings['agents']['designer']['fallback_provider_ids'] == ['codex']
    assert settings['agents']['designer']['provider_models'] == {'antigravity': 'daily-model'}
    assert settings['runtime']['cli_timeout'] == 600
    assert settings['runtime']['maintenance_enabled'] is False
    assert 'connections' not in settings
    assert not (state / 'data/daily-conversations.sqlite3').exists()
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert (state / 'data/hub_settings.json').stat().st_mode & 0o777 == 0o600


def test_alpha_refreshes_daily_routes_on_next_launch_but_keeps_its_own_state(daily_cli):
    root, source = daily_cli
    common.environment(root, 'alpha', inherit_cli=True)
    state = root / '.yiren/alpha'
    (state / 'data/keep.sqlite3').write_bytes(b'alpha history')
    settings_path = source / 'data/hub_settings.json'
    settings = json.loads(settings_path.read_text())
    settings['agents']['designer']['default_provider_id'] = 'codex'
    common.write_json(settings_path, settings)
    common.environment(root, 'alpha', inherit_cli=True)
    assert json.loads((state / 'data/hub_settings.json').read_text())['agents']['designer']['default_provider_id'] == 'codex'
    assert (state / 'data/keep.sqlite3').read_bytes() == b'alpha history'
    assert common.state_path(root, 'alpha', real=True) == state


def test_cli_inheritance_refuses_unknown_api_route_without_replacing_it_with_codex(daily_cli):
    root, source = daily_cli
    path = source / 'data/hub_settings.json'
    settings = json.loads(path.read_text())
    settings['agents']['designer']['default_provider_id'] = 'api-private'
    common.write_json(path, settings)
    with pytest.raises(common.EnvironmentError, match='non-CLI connection'):
        common.environment(root, 'alpha', inherit_cli=True)
    assert not (root / '.yiren/alpha/data/hub_settings.json').exists()


def test_cli_inheritance_refuses_missing_daily_config(source_repo):
    with pytest.raises(common.EnvironmentError, match='missing'):
        cli_config.runtime_cli_snapshot(source_repo)


def test_conflicting_listener_is_never_killed_and_parallel_ports_are_distinct():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0));s.listen()
        occupied = s.getsockname()[1]
        with pytest.raises(common.EnvironmentError, match="occupied"):
            common.choose_ports("development", occupied, 5174 if occupied != 5174 else 5175)
        assert s.fileno() >= 0
        with socket.socket() as other:
            first = common.choose_ports("development", allocate=True)
            other.bind(("127.0.0.1", first[0]));other.listen()
            second = common.choose_ports("development", allocate=True)
            assert second != first


def test_stale_supervisor_cannot_stop_another_service(tmp_path, monkeypatch):
    record = {"status": "running", "supervisor_pid": 123, "supervisor_started_at": "old birth"}
    monkeypatch.setattr(supervisor, "read_record", lambda *a, **k: record)
    monkeypatch.setattr(supervisor, "process_start", lambda _: "new birth")
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("must not kill reused PID"))
    with pytest.raises(common.EnvironmentError, match="No live managed"): supervisor.stop(tmp_path, "development")


def test_busy_stop_does_not_signal_supervisor(tmp_path, monkeypatch):
    record = {"backend_port": 8100, "token_file": str(tmp_path / "token.json"), "checkout": str(tmp_path), "environment": "development"}
    (tmp_path / "token.json").write_text(json.dumps({"token": "private"}))
    monkeypatch.setattr(supervisor, "read_record", lambda *a, **k: record)
    monkeypatch.setattr(supervisor, "alive", lambda _: True)
    monkeypatch.setattr(supervisor, "verify", lambda _: {})
    def busy(*a, **k): raise common.EnvironmentError("busy")
    monkeypatch.setattr(supervisor, "request", busy)
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("busy work must not be killed"))
    with pytest.raises(common.EnvironmentError, match="busy"): supervisor.stop(tmp_path, "development")


def test_frontend_proxy_and_reload_worker_identity_are_checked(monkeypatch):
    record = {"backend_port": 8100, "instance_id": "owned", "checkout": "/checkout", "backend_pid": 1}
    monkeypatch.setattr(supervisor, "request", lambda *_: {"runtime": {"started": {"instance_id": "owned", "checkout": "/checkout", "pid": 2}}})
    assert supervisor.verify(record)["pid"] == 2
    record["instance_id"] = "other"
    with pytest.raises(common.EnvironmentError): supervisor.verify(record)


def test_inventory_includes_managed_backend_only_ports(tmp_path, monkeypatch):
    from scripts import environment_status as status
    index = tmp_path / ".instances"
    index.mkdir()
    record = tmp_path / "instance.json"
    record.write_text(json.dumps({"checkout": str(tmp_path), "backend_port": 8400, "frontend_port": None, "status": "running"}))
    (index / "owned.json").write_text(json.dumps({"record": str(record)}))
    monkeypatch.setattr(common, "home", lambda: tmp_path)
    monkeypatch.setattr(status, "worktrees", lambda: [])
    monkeypatch.setattr(status, "listeners", lambda: {})
    monkeypatch.setattr(status, "get_json", lambda *a: {"status": "ok"})
    report = status.inventory([], [])
    assert len(report["services"]) == 1
    assert report["services"][0]["backend_port"] == 8400
    assert report["services"][0]["frontend_port"] is None
    assert "仅后端实例" in status.render(report)


def test_process_start_enforces_c_locale(monkeypatch):
    calls = []
    def fake_run(cmd, capture_output=True, text=True, env=None):
        calls.append((cmd, env))
        return subprocess.CompletedProcess(cmd, 0, stdout="Mon Oct 26 14:58:23 2026\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    res = supervisor.process_start(12345)
    assert res == "Mon Oct 26 14:58:23 2026"
    assert len(calls) == 1
    assert calls[0][0] == ["ps", "-p", "12345", "-o", "lstart="]
    assert calls[0][1].get("LC_ALL") == "C"
