import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.domain.maintenance import MaintenancePhase
from backend.app.main import create_app
from backend.app.storage.repository import Repository


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _make_repository(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")

    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "AgentClub Test")
    _git(source, "config", "user.email", "agentclub@example.com")
    (source / "README.md").write_text("# Fixture\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "init")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "push", "-u", "origin", "main")
    return source


def _register(client: TestClient, source: Path, commands=None, forbidden_paths=None) -> dict:
    response = client.post(
        "/api/maintenance/repositories",
        json={
            "name": "Fixture",
            "local_path": str(source),
            "github_repo": "owner/fixture",
            "validation_commands": commands or [[sys.executable, "-c", "print('ok')"]],
            "forbidden_paths": forbidden_paths or [".env", "secrets/"],
            "max_changed_files": 3,
            "max_repair_rounds": 3,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_register_prepare_and_validate_maintenance_run(temp_settings, tmp_path):
    source = _make_repository(tmp_path)
    temp_settings.maintenance_worktree_root = tmp_path / "worktrees"
    client = TestClient(create_app(temp_settings))

    registered = _register(client, source)
    created = client.post(
        "/api/maintenance/runs",
        json={"repository_id": registered["id"], "goal": "Update documentation"},
    )
    assert created.status_code == 201, created.text
    detail = created.json()
    assert detail["run"]["phase"] == "planning"
    assert detail["run"]["branch_name"].startswith("codex/maintenance-")
    workspace = Path(detail["run"]["workspace_path"])
    assert workspace.is_dir()
    assert (workspace / ".git").exists()

    validated = client.post(
        f"/api/maintenance/runs/{detail['run']['id']}/validate"
    )
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["run"]["phase"] == "reviewing"
    assert body["validation_results"][0]["exit_code"] == 0
    assert body["validation_results"][0]["command"][0] == sys.executable
    event_types = [item["event_type"] for item in body["events"]]
    assert "workspace.preparing" in event_types
    assert "validation.completed" in event_types


def test_validation_blocks_forbidden_path_change(temp_settings, tmp_path):
    source = _make_repository(tmp_path)
    temp_settings.maintenance_worktree_root = tmp_path / "worktrees"
    client = TestClient(create_app(temp_settings))
    registered = _register(client, source)
    created = client.post(
        "/api/maintenance/runs",
        json={"repository_id": registered["id"], "goal": "Unsafe change"},
    ).json()
    workspace = Path(created["run"]["workspace_path"])
    (workspace / ".env").write_text("SECRET=nope\n", encoding="utf-8")

    response = client.post(
        f"/api/maintenance/runs/{created['run']['id']}/validate"
    )
    assert response.status_code == 200
    assert response.json()["run"]["phase"] == "blocked"
    assert "Forbidden path changed" in response.json()["run"]["last_error"]
    assert response.json()["validation_results"] == []


def test_validation_checks_both_sides_of_renamed_files(temp_settings, tmp_path):
    source = _make_repository(tmp_path)
    (source / "protected.txt").write_text("protected\n", encoding="utf-8")
    _git(source, "add", "protected.txt")
    _git(source, "commit", "-m", "add protected fixture")
    _git(source, "push", "origin", "main")
    temp_settings.maintenance_worktree_root = tmp_path / "worktrees"
    client = TestClient(create_app(temp_settings))
    registered = _register(client, source, forbidden_paths=["protected.txt"])
    created = client.post(
        "/api/maintenance/runs",
        json={"repository_id": registered["id"], "goal": "Rename protected file"},
    ).json()
    workspace = Path(created["run"]["workspace_path"])
    (workspace / "protected.txt").rename(workspace / "renamed.txt")

    response = client.post(f"/api/maintenance/runs/{created['run']['id']}/validate")

    assert response.status_code == 200
    assert response.json()["run"]["phase"] == "blocked"
    assert "protected.txt" in response.json()["run"]["last_error"]


def test_registration_rejects_invalid_repository_and_shell_string(temp_settings, tmp_path):
    client = TestClient(create_app(temp_settings))
    missing = client.post(
        "/api/maintenance/repositories",
        json={
            "name": "Missing",
            "local_path": str(tmp_path / "missing"),
            "github_repo": "owner/missing",
            "validation_commands": [["pytest"]],
        },
    )
    assert missing.status_code == 422

    source = _make_repository(tmp_path)
    shell_string = client.post(
        "/api/maintenance/repositories",
        json={
            "name": "Bad command",
            "local_path": str(source),
            "github_repo": "owner/fixture",
            "validation_commands": ["pytest -q"],
        },
    )
    assert shell_string.status_code == 422

    invalid_github_repo = client.post(
        "/api/maintenance/repositories",
        json={
            "name": "Bad identity",
            "local_path": str(source),
            "github_repo": "not-an-owner-repository-pair",
            "validation_commands": [["pytest", "-q"]],
        },
    )
    assert invalid_github_repo.status_code == 422


def test_approval_feedback_and_evolution_are_persisted(temp_settings, tmp_path):
    source = _make_repository(tmp_path)
    temp_settings.maintenance_worktree_root = tmp_path / "worktrees"
    client = TestClient(create_app(temp_settings))
    registered = _register(client, source)
    run_id = client.post(
        "/api/maintenance/runs",
        json={"repository_id": registered["id"], "goal": "Improve tests"},
    ).json()["run"]["id"]

    repository = Repository(temp_settings.database_path)
    run = repository.get_maintenance_run(run_id)
    assert run is not None
    repository.update_maintenance_run(
        run.model_copy(update={"phase": MaintenancePhase.READY_FOR_APPROVAL})
    )
    approval = client.post(
        f"/api/maintenance/runs/{run_id}/approvals",
        json={"decision": "approved", "note": "CI is green"},
    )
    assert approval.status_code == 201

    run = repository.get_maintenance_run(run_id)
    assert run is not None
    repository.update_maintenance_run(
        run.model_copy(update={"phase": MaintenancePhase.COMPLETED})
    )

    feedback = client.post(
        f"/api/maintenance/runs/{run_id}/feedback",
        json={
            "rating": "satisfied",
            "reason_tags": ["focused-diff"],
            "comment": "Good repair loop",
            "objective_success": True,
            "agent_evaluation": "Review and checks passed",
        },
    )
    assert feedback.status_code == 201
    assert feedback.json()["memory_candidate"]["status"] == "candidate"

    batch = client.post(
        "/api/maintenance/evolution-batches",
        json={
            "repository_id": registered["id"],
            "trigger": "manual",
            "proposal": "Improve validation failure summaries",
        },
    )
    assert batch.status_code == 201
    assert batch.json()["status"] == "proposed"
    assert len(batch.json()["eval_cases"]) == 2

    detail = client.get(f"/api/maintenance/runs/{run_id}").json()
    assert len(detail["approvals"]) == 1
    assert len(detail["feedback"]) == 1
    assert len(client.get("/api/maintenance/memory-candidates").json()) == 1
    assert len(client.get("/api/maintenance/evolution-batches").json()) == 1


def test_feedback_requires_finished_run(temp_settings, tmp_path):
    source = _make_repository(tmp_path)
    temp_settings.maintenance_worktree_root = tmp_path / "worktrees"
    client = TestClient(create_app(temp_settings))
    registered = _register(client, source)
    run_id = client.post(
        "/api/maintenance/runs",
        json={"repository_id": registered["id"], "goal": "Still in progress"},
    ).json()["run"]["id"]

    response = client.post(
        f"/api/maintenance/runs/{run_id}/feedback",
        json={
            "rating": "satisfied",
            "objective_success": True,
        },
    )

    assert response.status_code == 409
    assert "finished maintenance run" in response.json()["detail"]


def test_startup_recovery_blocks_and_resume_restores_interrupted_run(temp_settings, tmp_path):
    source = _make_repository(tmp_path)
    temp_settings.maintenance_worktree_root = tmp_path / "worktrees"
    client = TestClient(create_app(temp_settings))
    registered = _register(client, source)
    run_id = client.post(
        "/api/maintenance/runs",
        json={"repository_id": registered["id"], "goal": "Recover me"},
    ).json()["run"]["id"]

    repository = Repository(temp_settings.database_path)
    run = repository.get_maintenance_run(run_id)
    assert run is not None
    repository.update_maintenance_run(
        run.model_copy(update={"phase": MaintenancePhase.LOCAL_VERIFYING})
    )

    restarted_client = TestClient(create_app(temp_settings))
    recovered = restarted_client.get(f"/api/maintenance/runs/{run_id}").json()
    assert recovered["run"]["phase"] == "blocked"
    assert "restarted" in recovered["run"]["last_error"]

    resumed = restarted_client.post(f"/api/maintenance/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["run"]["phase"] == "planning"
    assert resumed.json()["run"]["last_error"] is None
    assert resumed.json()["events"][-1]["event_type"] == "run.resumed"
