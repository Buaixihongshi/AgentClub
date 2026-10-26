import json
from pathlib import Path
import sqlite3

import pytest

from scripts import releases
from scripts import release_storage as storage
from scripts.environment_common import EnvironmentError, write_json

A, B = "a" * 40, "b" * 40


def seed(path):
    for name in storage.STATE_DIRS: (path / name).mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path / "data/yirenhub.sqlite3") as db:
        db.execute("CREATE TABLE missions(id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO missions VALUES('before','{\"status\":\"completed\"}')")
    (path / "config/agents.json").write_text("[]")
    (path / "config/cli-providers.json").write_text("[]")
    (path / "artifacts/result").write_text("before")
    write_json(path / "initialization.json", {"mode": "imported"})


def ids(state):
    with sqlite3.connect(state / "data/yirenhub.sqlite3") as db: return {r[0] for r in db.execute("SELECT id FROM missions")}


def add(state, name):
    with sqlite3.connect(state / "data/yirenhub.sqlite3") as db:
        db.execute("INSERT INTO missions VALUES (?, '{\"status\":\"completed\"}')", (name,))


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    root = tmp_path / "environments"
    state = root / "runtime-state"
    seed(state)
    manifests = {sha: {"sha": sha, "checkout": str(root / "releases" / sha), "repository": "owner/project",
        "source_pr": 1, "merge_commit_sha": sha, "verified_head_sha": "c" * 40,
        "ci": {"Backend tests": True, "Frontend tests and build": True}} for sha in (A, B)}
    token = state / "private-token.json"
    write_json(token, {"token": "test-private-control"})
    live = {"record": {"checkout": manifests[A]["checkout"], "backend_port": 12345, "token_file": str(token)}}
    stopped, started, requests = [], [], []
    write_json(root / "current.json", manifests[A])
    monkeypatch.setattr(releases, "home", lambda: root)
    monkeypatch.setattr(releases, "load_stage", lambda sha: manifests[sha])
    monkeypatch.setattr(releases, "release_provenance", lambda api, sha: manifests[sha])
    monkeypatch.setattr(releases, "gate", lambda api: {"owner_required": False})
    monkeypatch.setattr(releases, "ports_free", lambda: live["record"] is None)
    monkeypatch.setattr(releases.manager, "read_record", lambda *a, **k: live["record"])
    monkeypatch.setattr(releases.manager, "alive", lambda rec: rec is not None and rec is live["record"])
    monkeypatch.setattr(releases.manager, "verify", lambda rec: rec)
    monkeypatch.setattr(releases, "request", lambda *a, **k: requests.append(a))
    def stop(path, *a, **k):
        assert str(path) == live["record"]["checkout"]
        stopped.append(str(path));live["record"] = None
    monkeypatch.setattr(releases.manager, "stop", stop)
    def start(manifest, runtime):
        started.append(manifest["sha"])
        live["record"] = {"checkout": manifest["checkout"], "backend_port": 12345, "token_file": str(token)}
        return live["record"]
    monkeypatch.setattr(releases, "start_runtime", start)
    return root, state, manifests, live, started, stopped, requests, start


@pytest.mark.parametrize("legacy_previous", [False, True])
def test_activate_then_rollback_records_sha_mapping_and_preserves_post_release_work(deployment, legacy_previous):
    root, state, manifests, _, started, stopped, requests, _ = deployment
    if legacy_previous:
        manifests[A].pop("verified_head_sha")
        manifests[A].pop("ci")
        manifests[A].update(approved_head_sha="d" * 40, approval_run="historical-fixture", approved_by="owner")
    activated = releases.switch(None, B, A)
    assert activated["sha"] == B and activated["previous_sha"] == A
    assert requests[0][1] == "/api/runtime/drain"
    assert releases.transition()["phase"] == "committed"
    add(state, "after")
    rolled_back = releases.switch(None, None, B, rollback=True)
    assert rolled_back["sha"] == A and ids(state) == {"before"}
    with sqlite3.connect(Path(rolled_back["backup"]) / "data/yirenhub.sqlite3") as db:
        assert {r[0] for r in db.execute("SELECT id FROM missions")} == {"before", "after"}
    assert started == [B, A] and len(stopped) == 2
    if legacy_previous:
        assert rolled_back["approved_head_sha"] == "d" * 40
        assert "verified_head_sha" not in rolled_back
    else:
        assert rolled_back["verified_head_sha"] == "c" * 40
        assert "approval_run" not in rolled_back


def test_failed_candidate_restores_database_and_previous_release(deployment, monkeypatch):
    root, state, _, live, started, _, _, original_start = deployment
    def fail_new(manifest, runtime):
        if manifest["sha"] == B:
            add(state, "failed-migration")
            raise EnvironmentError("bad health")
        return original_start(manifest, runtime)
    monkeypatch.setattr(releases, "start_runtime", fail_new)
    with pytest.raises(EnvironmentError, match="were restored"): releases.switch(None, B, A)
    assert releases.current()["sha"] == A and ids(state) == {"before"}
    assert started == [A] and live["record"] is not None
    assert releases.transition()["phase"] == "failed_restored"


def test_wrong_expected_current_and_busy_service_do_not_stop_or_replace_state(deployment, monkeypatch):
    root, state, _, _, _, stopped, _, _ = deployment
    with pytest.raises(EnvironmentError, match="Runtime changed"): releases.switch(None, B, "wrong")
    def busy(*a, **k): raise EnvironmentError("busy")
    monkeypatch.setattr(releases, "request", busy)
    with pytest.raises(EnvironmentError, match="busy"): releases.switch(None, B, A)
    assert stopped == [] and ids(state) == {"before"}
    assert releases.transition()["phase"] == "aborted"


def test_invalid_main_protection_prevents_switch_before_touching_runtime(deployment, monkeypatch):
    _, state, _, _, started, stopped, requests, _ = deployment
    def invalid_gate(_):
        raise EnvironmentError("required CI protection is missing")
    monkeypatch.setattr(releases, "gate", invalid_gate)
    with pytest.raises(EnvironmentError, match="CI protection"):
        releases.switch(None, B, A)
    assert not started and not stopped and not requests
    assert ids(state) == {"before"} and releases.transition() is None


def test_unknown_live_startup_requires_recovery_before_any_restore(deployment, monkeypatch):
    root, state, manifests, live, _, _, _, original_start = deployment
    def pending(manifest, runtime):
        original_start(manifest, runtime)
        raise EnvironmentError("observation timed out while candidate remained alive")
    monkeypatch.setattr(releases, "start_runtime", pending)
    with pytest.raises(EnvironmentError, match="still live/unknown"): releases.switch(None, B, A)
    assert live["record"]["checkout"] == manifests[B]["checkout"]
    assert releases.transition()["phase"] == "recovery_required"
    assert releases.current()["sha"] == A
    monkeypatch.setattr(releases, "start_runtime", original_start)
    releases.recover(A)
    assert releases.transition()["phase"] == "recovered"
    assert live["record"]["checkout"] == manifests[A]["checkout"]


def test_staged_artifact_tampering_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(releases, "home", lambda: tmp_path)
    checkout = tmp_path / "releases" / A
    dist = checkout / "frontend/dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("original")
    checks = tmp_path / "checks.json"
    results = [{"group": group, "check": index, "exit_code": 0}
               for group, commands in releases.check_commands("python", tmp_path).items()
               if group in releases.GROUPS for index in range(len(commands))]
    write_json(checks, {"passed": True, "groups": list(releases.GROUPS), "source": {"git_sha": A, "source_digest": "digest"},
                       "source_changed": False, "results": results})
    write_json(tmp_path / "releases" / (A + ".json"), {"checkout": str(checkout), "source_digest": "digest", "dist": releases.dist_hashes(checkout), "checks": str(checks)})
    monkeypatch.setattr(releases, "source_snapshot", lambda _: {"git_sha": A, "dirty": False, "source_digest": "digest"})
    assert releases.load_stage(A)
    incomplete = json.loads(checks.read_text())
    incomplete["results"].pop()
    write_json(checks, incomplete)
    with pytest.raises(EnvironmentError, match="complete source-matched"): releases.load_stage(A)
    incomplete["results"] = results
    write_json(checks, incomplete)
    (dist / "index.html").write_text("different")
    with pytest.raises(EnvironmentError, match="has changed"): releases.load_stage(A)
