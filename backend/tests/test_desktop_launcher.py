import os
import subprocess
from pathlib import Path

from backend.app import desktop


def test_merge_path_keeps_shell_order_and_adds_cli_fallbacks():
    merged = desktop.merge_path("/custom/bin:/usr/bin", "/usr/bin:/bin").split(os.pathsep)
    assert merged[:3] == ["/custom/bin", "/usr/bin", "/bin"]
    assert "/opt/homebrew/bin" in merged and len(merged) == len(set(merged))


def test_login_shell_path_ignores_rc_banner(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=f"welcome!\n{desktop.PATH_MARKER}/a:/b\n")
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.login_shell_path() == "/a:/b"

    monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, stdout="no marker"))
    assert desktop.login_shell_path() is None


def test_desktop_env_keeps_state_outside_bundle_and_drops_inherited_yiren(tmp_path):
    root, state = tmp_path / "Unity.app/Contents/Resources/app", tmp_path / "support"
    env = desktop.desktop_env(root, state, {"PATH": "/usr/bin", "YIREN_DATABASE_PATH": "/leak.sqlite3"}, "/shell/bin")

    assert env["YIREN_DATABASE_PATH"] == str(state / "data/yirenhub.sqlite3")
    assert env["YIREN_CONFIG_DIR"] == str(root / "config")
    assert env["YIREN_FRONTEND_DIST"] == str(root / "frontend/dist")
    assert env["YIREN_MAINTENANCE_ENABLED"] == "false" and env["YIREN_USE_FAKE_CLI"] == "false"
    assert env["PATH"].startswith("/shell/bin:/usr/bin")
    assert not Path(env["PYTHONPYCACHEPREFIX"]).is_relative_to(root)
    assert all(not v.startswith(str(root)) for k, v in env.items()
               if k.startswith("YIREN_") and k not in {"YIREN_CONFIG_DIR", "YIREN_FRONTEND_DIST"})


def test_prepare_state_creates_private_dirs(tmp_path):
    workspace = desktop.prepare_state(tmp_path / "support")
    assert workspace.is_dir()
    assert (tmp_path / "support/data").stat().st_mode & 0o777 == 0o700


def test_packaged_source_reports_frozen_build_identity(tmp_path):
    import json
    from backend.app.runtime_identity import BUILD_IDENTITY, source_snapshot

    frozen = {"git_sha": "a" * 40, "branch": "main", "dirty": False, "source_digest": "d" * 64,
              "checkout_id": "c" * 16, "environment": "desktop", "captured_at": "2026-10-28T00:00:00+00:00"}
    (tmp_path / BUILD_IDENTITY).write_text(json.dumps(frozen), encoding="utf-8")
    snapshot = source_snapshot(tmp_path)
    assert snapshot == {k: frozen[k] for k in ("git_sha", "branch", "dirty", "source_digest", "checkout_id")}


def test_backend_stops_when_shell_process_disappears(monkeypatch):
    import signal
    parents = iter([100, 100, 1])
    killed = []
    monkeypatch.setattr(desktop.os, "getppid", lambda: next(parents))
    monkeypatch.setattr(desktop.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    desktop.exit_with_parent(100, interval=0.01).join(timeout=2)
    assert killed == [(os.getpid(), signal.SIGTERM)]
