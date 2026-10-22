"""Private, consistent state snapshots for explicitly managed releases."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from scripts.environment_common import EnvironmentError, private_dir, write_json

STATE_DIRS = ("data", "config", "artifacts")


def secure_tree(root):
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink(): path.chmod(0o700 if path.is_dir() else 0o600)


def sqlite_snapshot(source, dest):
    if not source.is_file(): raise EnvironmentError("Source SQLite database is missing")
    private_dir(dest.parent)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='missions'").fetchone():
            raise EnvironmentError("Source database is not an AgentClub mission database")
        with sqlite3.connect(dest) as target: db.backup(target)
    dest.chmod(0o600)


def copy_state(database, config, artifacts, destination):
    private_dir(destination)
    for name, source in (("data", database.parent), ("config", config), ("artifacts", artifacts)):
        if source.resolve() == destination.resolve() or destination.resolve().is_relative_to(source.resolve()):
            raise EnvironmentError("Snapshot destination must not be inside its source")
        target = destination / name
        if source.is_dir():
            ignore = shutil.ignore_patterns(database.name, database.name + "-wal", database.name + "-shm") if name == "data" else None
            shutil.copytree(source, target, symlinks=True, ignore=ignore)
        else: private_dir(target)
    sqlite_snapshot(database, destination / "data/yirenhub.sqlite3")
    secure_tree(destination)


def file_hashes(root):
    return {str(path.relative_to(root)): (
        "link:" + os.readlink(path) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest())
        for folder in STATE_DIRS for path in (root / folder).rglob("*") if path.is_file() or path.is_symlink()}


def backup(state, destination, metadata, allow_empty=False):
    if destination.exists(): raise EnvironmentError("Backup destination already exists")
    if allow_empty and not (state / "data/yirenhub.sqlite3").exists():
        private_dir(destination)
        for name in STATE_DIRS:
            if (state / name).exists(): shutil.copytree(state / name, destination / name, symlinks=True)
            else: private_dir(destination / name)
        secure_tree(destination)
    else:
        copy_state(state / "data/yirenhub.sqlite3", state / "config", state / "artifacts", destination)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "metadata": metadata, "files": file_hashes(destination)}
    write_json(destination / "backup.json", manifest)
    return manifest


def restore(state, snapshot):
    manifest = json.loads((snapshot / "backup.json").read_text())
    if manifest["files"] != file_hashes(snapshot): raise EnvironmentError("Backup checksum mismatch; state has not been changed")
    prepared = state.parent / (".restore-" + uuid.uuid4().hex)
    private_dir(prepared)
    for name in STATE_DIRS: shutil.copytree(snapshot / name, prepared / name, symlinks=True)
    secure_tree(prepared)
    previous = private_dir(state.parent / (".retired-" + uuid.uuid4().hex))
    replaced = []
    try:
        for name in STATE_DIRS:
            if (state / name).exists(): os.replace(state / name, previous / name)
            replaced.append(name)
            os.replace(prepared / name, state / name)
    except BaseException:
        for name in reversed(replaced):
            if (state / name).exists(): shutil.rmtree(state / name)
            if (previous / name).exists(): os.replace(previous / name, state / name)
        raise
    finally:
        shutil.rmtree(prepared, ignore_errors=True)
    return previous


def initialize(state, *, empty=False, database=None, config=None, artifacts=None):
    if state.exists() and any(state.iterdir()): raise EnvironmentError("Runtime state already exists; refusing to overwrite it")
    prepared = state.parent / (".import-" + uuid.uuid4().hex)
    if empty:
        private_dir(prepared)
        for name in STATE_DIRS: private_dir(prepared / name)
        mode = "explicit-empty"
    else:
        if not config or not (config / "agents.json").is_file() or not (config / "cli-providers.json").is_file():
            raise EnvironmentError("Source provider/agent configuration is missing")
        copy_state(database, config, artifacts, prepared)
        mode = "imported"
    record = {"mode": mode, "created_at": datetime.now(timezone.utc).isoformat()}
    if not empty:
        record["source"] = {"database": str(database), "config": str(config), "artifacts": str(artifacts), "running_sha": None}
    write_json(prepared / "initialization.json", record)
    if state.exists(): state.rmdir()
    os.replace(prepared, state)


def require_idle_database(database):
    if not database.exists(): return
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "missions" not in tables: raise EnvironmentError("Cannot verify idle state of an unknown database")
        count = db.execute("SELECT count(*) FROM missions WHERE json_extract(data,'$.status') NOT IN ('completed','failed','completed_with_warnings')").fetchone()[0]
        if "events" in tables:
            count += db.execute("""SELECT count(*) FROM missions WHERE json_extract(data,'$.discussion.protocol')='deliberation_v2'
                AND coalesce((SELECT json_extract(e.payload,'$.phase') FROM events e WHERE e.mission_id=missions.id
                  AND e.event_type='discussion.v2.state' ORDER BY e.id DESC LIMIT 1),'unknown')!='ended'""").fetchone()[0]
        if "execution_grants" in tables: count += db.execute("SELECT count(*) FROM execution_grants WHERE state='active'").fetchone()[0]
        if "maintenance_runs" in tables:
            count += db.execute("SELECT count(*) FROM maintenance_runs WHERE json_extract(data,'$.phase') NOT IN ('completed','blocked','failed','cancelled')").fetchone()[0]
        if count: raise EnvironmentError("State contains unfinished work; finish or stop it through its application before migrating/releasing")
