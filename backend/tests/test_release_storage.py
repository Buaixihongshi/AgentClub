import json
from pathlib import Path
import sqlite3

import pytest

from scripts.environment_common import EnvironmentError
from scripts import release_storage as storage


def make_state(path):
    for name in storage.STATE_DIRS: (path / name).mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path / "data/yirenhub.sqlite3") as db:
        db.execute("CREATE TABLE missions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO missions VALUES ('old', ?)", (json.dumps({"status": "completed"}),))
    (path / "config/agents.json").write_text("[]")
    (path / "config/cli-providers.json").write_text("[]")
    (path / "data/hub_settings.json").write_text('{"saved":true}')
    (path / "data/connection-secrets").mkdir()
    (path / "data/connection-secrets/key").write_text("private-value")
    (path / "artifacts/result.txt").write_text("before")
    (path / "initialization.json").write_text('{"mode":"imported"}')
    return path


def test_wal_snapshot_and_restore_preserve_settings_secrets_and_artifacts(tmp_path):
    state = make_state(tmp_path / "state")
    source = sqlite3.connect(state / "data/yirenhub.sqlite3")
    source.execute("PRAGMA journal_mode=WAL")
    source.execute("INSERT INTO missions VALUES ('wal', ?)", ('{"status":"completed"}',))
    source.commit()
    snapshot = tmp_path / "backup"
    storage.backup(state, snapshot, {"sha": "old"})
    source.execute("INSERT INTO missions VALUES ('after', ?)", ('{"status":"completed"}',))
    source.commit();source.close()
    (state / "artifacts/result.txt").write_text("after")
    storage.restore(state, snapshot)
    with sqlite3.connect(state / "data/yirenhub.sqlite3") as db:
        assert {r[0] for r in db.execute("SELECT id FROM missions")} == {"old", "wal"}
    assert (state / "data/connection-secrets/key").read_text() == "private-value"
    assert (state / "data/hub_settings.json").read_text() == '{"saved":true}'
    assert (state / "artifacts/result.txt").read_text() == "before"
    assert (snapshot / "backup.json").stat().st_mode & 0o777 == 0o600


def test_corrupted_backup_is_rejected_before_changing_current_state(tmp_path):
    state = make_state(tmp_path / "state")
    snapshot = tmp_path / "backup"
    storage.backup(state, snapshot, {})
    (snapshot / "artifacts/result.txt").write_text("tampered")
    with pytest.raises(EnvironmentError, match="checksum"):
        storage.restore(state, snapshot)
    assert (state / "artifacts/result.txt").read_text() == "before"


def test_import_is_explicit_atomic_and_does_not_follow_artifact_symlinks(tmp_path):
    source = make_state(tmp_path / "source")
    outside = tmp_path / "outside"
    outside.write_text("do not read or chmod")
    outside.chmod(0o644)
    (source / "artifacts/link").symlink_to(outside)
    dest = tmp_path / "runtime"
    storage.initialize(dest, database=source / "data/yirenhub.sqlite3", config=source / "config", artifacts=source / "artifacts")
    assert (dest / "artifacts/link").is_symlink()
    assert outside.stat().st_mode & 0o777 == 0o644
    assert (source / "artifacts/result.txt").read_text() == "before"
    with pytest.raises(EnvironmentError, match="already exists"): storage.initialize(dest, empty=True)


def test_offline_migration_rejects_unfinished_missions(tmp_path):
    state = make_state(tmp_path / "source")
    with sqlite3.connect(state / "data/yirenhub.sqlite3") as db:
        db.execute("UPDATE missions SET data=?", ('{"status":"awaiting_input"}',))
    with pytest.raises(EnvironmentError, match="unfinished work"):
        storage.require_idle_database(state / "data/yirenhub.sqlite3")


def test_runtime_restart_preserves_existing_provider_configuration(tmp_path):
    from scripts.environment_common import environment
    state = make_state(tmp_path / "runtime-state")
    providers = [{"id": "fake", "enabled": True}, {"id": "codex", "enabled": True}]
    (state / "config/cli-providers.json").write_text(json.dumps(providers))
    root = tmp_path / "release"
    root.mkdir()
    env = environment(root, "runtime", backend=8000, frontend=5173, state=state)
    assert json.loads((state / "config/cli-providers.json").read_text()) == providers
    assert env["YIREN_DEMO_ONLY"] == "false"


def test_offline_migration_keeps_historical_blocked_tasks_without_rewriting_them(tmp_path):
    state = make_state(tmp_path / "source")
    database = state / "data/yirenhub.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE missions SET data=?", ('{"status":"blocked"}',))
    storage.require_idle_database(database)
    with sqlite3.connect(database) as db:
        assert json.loads(db.execute("SELECT data FROM missions").fetchone()[0])["status"] == "blocked"


@pytest.mark.parametrize("phase", ["independent", "ended", None])
def test_offline_blocked_v2_discussion_needs_verified_ended_state(tmp_path, phase):
    state = make_state(tmp_path / "source")
    database = state / "data/yirenhub.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE missions SET data=?", (json.dumps({"status": "blocked", "discussion": {"protocol": "deliberation_v2"}}),))
        if phase is not None:
            db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, mission_id TEXT, event_type TEXT, payload TEXT)")
            db.execute("INSERT INTO events VALUES(1,'old','discussion.v2.state',?)", (json.dumps({"phase": phase}),))
    if phase == "ended":
        storage.require_idle_database(database)
    else:
        with pytest.raises(EnvironmentError, match="unfinished work"):
            storage.require_idle_database(database)
