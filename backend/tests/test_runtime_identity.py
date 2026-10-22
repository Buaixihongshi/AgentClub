import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.runtime_identity import capture_runtime, runtime_view, source_snapshot
from scripts import environment_status


def make_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "backend").mkdir()
    (root / "backend/app.py").write_text("version = 1\n")
    (root / ".gitignore").write_text("data/\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "initial"], check=True)
    return root


def test_running_identity_stays_frozen_when_same_sha_source_changes(tmp_path, temp_settings):
    root = make_repo(tmp_path)
    started = capture_runtime(temp_settings, root)
    assert runtime_view(started)["source_changed"] is False
    (root / "backend/app.py").write_text("version = 2\n")
    # Stage it too: testing only an unstaged diff would miss this case.
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    view = runtime_view(started)
    assert view["started"]["git_sha"] == view["current"]["git_sha"]
    assert view["source_changed"] is True
    assert view["started"]["dirty"] is False
    assert view["current"]["dirty"] is True
    assert view["started"]["source_digest"] != view["current"]["source_digest"]


def test_untracked_code_counts_but_runtime_data_does_not(tmp_path):
    root = make_repo(tmp_path)
    before = source_snapshot(root)
    (root / "data").mkdir()
    (root / "data/private.txt").write_text("private runtime content")
    assert source_snapshot(root) == before
    (root / "backend/new.py").write_text("new_behavior = True")
    assert source_snapshot(root)["source_digest"] != before["source_digest"]
    (root / "backend/app.py").unlink()
    assert source_snapshot(root)["source_digest"]


def test_non_git_checkout_reports_unknown_instead_of_clean(tmp_path, temp_settings):
    view = runtime_view(capture_runtime(temp_settings, tmp_path))
    assert view["source_changed"] is None
    assert view["current"]["git_sha"] is None
    assert view["current"]["dirty"] is None


def test_health_keeps_its_identity_across_requests(temp_settings):
    client = TestClient(create_app(temp_settings))
    first = client.get("/api/health").json()["runtime"]
    second = client.get("/api/health").json()["runtime"]
    assert first["started"] == second["started"]
    assert Path(first["started"]["database"]) == temp_settings.database_path.resolve()
    assert first["started"]["pid"] > 0
    assert first["source_changed"] is False


def test_old_service_is_not_assigned_the_checkout_head(monkeypatch):
    monkeypatch.setattr(environment_status, "get_json", lambda *_: {"status": "ok", "version": "0.1.0"})
    row = environment_status.inspect_service((8000, 5173), {})
    assert row["healthy"] and row["proxy_healthy"]
    assert row["runtime"] is None
    assert "磁盘 HEAD 不能证明运行版本" in environment_status.render({
        "registered_worktrees": 0, "services": [row], "checkouts": [],
    })


def test_status_reports_frontend_proxy_identity_separately(monkeypatch):
    def response(port, path):
        if path == "/__environment": return {"git_sha": "frontend"}
        return {"status": "ok", "runtime": {"started": {"pid": port}}}
    monkeypatch.setattr(environment_status, "get_json", response)
    row = environment_status.inspect_service((8000, 5173), {})
    assert row["runtime"]["started"]["pid"] == 8000
    assert row["proxy_runtime"]["started"]["pid"] == 5173


def test_identity_command_only_emits_public_source_metadata():
    result = subprocess.run(["bash", "scripts/env.sh", "identity"], check=True, capture_output=True, text=True)
    identity = json.loads(result.stdout)
    assert identity["git_sha"]
    assert identity["source_digest"]
    assert "database" not in identity and "checkout" not in identity
