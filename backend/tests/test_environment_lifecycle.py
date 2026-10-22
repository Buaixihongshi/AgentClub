import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from scripts import environment_cli as cli
from scripts import environment_common as common
from scripts import environment_supervisor as supervisor


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
    record = {"backend_port": 8100, "token_file": str(tmp_path / "token.json")}
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
