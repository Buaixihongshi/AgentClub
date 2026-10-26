"""Owner-operated release preparation, provenance, backups and guarded switching.

Agents may prepare and verify candidates; actual runtime activation/import/
rollback is a separate explicit owner action. No approval is generated here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.runtime_identity import source_snapshot
from scripts.environment_common import (
    EnvironmentError, available, checkout_listening, git, home, lock, private_dir,
    profiles, request, setup, write_json,
)
from scripts.environment_cli import managed_checkout
from scripts import environment_supervisor as manager
from scripts.release_policy import GitHub, check_sha, gate, prepare, release_provenance
from scripts import release_storage as storage
from scripts.checks import commands as check_commands

GROUPS = {"backend", "frontend", "smoke", "eval", "privacy", "hygiene", "audit"}


def dist_hashes(checkout):
    root = checkout / "frontend/dist"
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def load_stage(sha):
    check_sha(sha)
    manifest = json.loads((home() / "releases" / (sha + ".json")).read_text())
    checkout = Path(manifest["checkout"])
    source = source_snapshot(checkout)
    if (source["git_sha"] != sha or source["dirty"] is not False or source["source_digest"] != manifest["source_digest"]
            or manifest["dist"] != dist_hashes(checkout)):
        raise EnvironmentError("Staged source or frontend artifact has changed; refuse activation")
    evidence = json.loads(Path(manifest["checks"]).read_text())
    required = {(group, index) for group, checks in check_commands("python", Path("evidence")).items()
                if group in GROUPS for index in range(len(checks))}
    successes = {(r["group"], r["check"]) for r in evidence.get("results", []) if r["exit_code"] == 0}
    if (not evidence["passed"] or not GROUPS <= set(evidence["groups"]) or
            evidence.get("source_changed") is not False or not required <= successes or
            evidence["source"]["git_sha"] != sha or evidence["source"]["source_digest"] != source["source_digest"]):
        raise EnvironmentError("Stage lacks complete source-matched verification evidence")
    return manifest


def stage(api, sha):
    provenance = release_provenance(api, check_sha(sha))
    target = home() / "releases" / sha
    path = home() / "releases" / (sha + ".json")
    with lock(home() / ".release.lock"):
        if path.exists(): return load_stage(sha)
        git(ROOT, "fetch", "origin", "main")
        managed_checkout(target)
        if not target.exists(): git(ROOT, "worktree", "add", "--detach", str(target), sha)
        if git(target, "branch", "--show-current") or git(target, "rev-parse", "HEAD") != sha or git(target, "status", "--porcelain", "--untracked-files=all"):
            raise EnvironmentError("Candidate worktree is not the exact clean release SHA")
        if checkout_listening(target): raise EnvironmentError("Cannot rebuild an active release")
        if not (target / "backend/app/runtime_control.py").exists(): raise EnvironmentError("Release does not support managed lifecycle")
        setup(target)
        output = home() / "releases/.evidence" / sha
        env = {k: v for k, v in os.environ.items() if not k.startswith("YIREN_")}
        env["YIREN_ENVIRONMENT"] = "runtime"
        subprocess.run([str(target / "scripts/check.sh"), "all", "--output", str(output)], cwd=target, env=env, check=True)
        source = source_snapshot(target)
        front = json.loads((target / "frontend/dist/__environment").read_text())
        if front["environment"] != "runtime" or front["git_sha"] != sha or front["source_digest"] != source["source_digest"]:
            raise EnvironmentError("Frontend build identity does not match the release")
        manifest = {**provenance, "sha": sha, "checkout": str(target), "source_digest": source["source_digest"],
                    "dist": dist_hashes(target), "checks": str(output / "checks.json"), "staged_at": datetime.now(timezone.utc).isoformat()}
        write_json(path, manifest)
        return load_stage(sha)


def current():
    path = home() / "current.json"
    return json.loads(path.read_text()) if path.exists() else None


def require_expected(old, expected):
    actual = old["sha"] if old else "none"
    if expected != actual: raise EnvironmentError(f"Runtime changed: expected {expected}, actual {actual}")


TERMINAL = {"committed", "failed_restored", "recovered", "aborted"}


def transition():
    path = home() / "transition.json"
    return json.loads(path.read_text()) if path.exists() else None


def phase(intent, name):
    intent["phase"] = name
    intent["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(home() / "transition.json", intent)


def recover(expected):
    with lock(home() / ".release.lock"):
        deployed = current()
        require_expected(deployed, expected)
        intent = transition()
        if not intent or intent["phase"] in TERMINAL: raise EnvironmentError("No interrupted release transition to recover")
        state = home() / "runtime-state"
        target = load_stage(intent["target_sha"])
        record = manager.read_record(Path(target["checkout"]), "runtime", state=state)
        if deployed and deployed["sha"] == intent["target_sha"]:
            if not manager.alive(record): raise EnvironmentError("Recorded target is not live; inspect runtime status")
            manager.verify(record)
            phase(intent, "committed")
            write_json(home() / ".release-history" / (uuid.uuid4().hex + ".json"), {"status": "committed_recovered", **intent})
            return deployed
        require_expected(intent["previous"], expected)
        old = intent["previous"]
        if manager.alive(record):
            allowed = {target["checkout"], old["checkout"] if old else None}
            if record["checkout"] not in allowed: raise EnvironmentError("Recovery found an unrelated live runtime")
            if record["checkout"] == (old["checkout"] if old else None) and not Path(intent["backup"]).exists():
                token = json.loads(Path(record["token_file"]).read_text())["token"]
                request(record["backend_port"], "/api/runtime/resume", token=token)
                phase(intent, "aborted")
                return old
            manager.stop(Path(record["checkout"]), "runtime", state=state)
            for _ in range(50):
                if ports_free(): break
                time.sleep(0.1)
        if not ports_free(): raise EnvironmentError("Unknown runtime listener prevents recovery")
        snapshot = Path(intent["backup"])
        if snapshot.exists():
            # Preserve post-switch work too; recovery never silently discards it.
            storage.backup(state, home() / ".backups" / uuid.uuid4().hex, {"action": "before_recovery"}, allow_empty=True)
            storage.restore(state, snapshot)
        if old: start_runtime(load_stage(old["sha"]), state)
        phase(intent, "recovered")
        write_json(home() / ".release-history" / (uuid.uuid4().hex + ".json"), {"status": "recovered", **intent})
        return old


def start_runtime(manifest, state):
    return manager.start(Path(manifest["checkout"]), "runtime", detach=True, state=state,
        backend=profiles()["runtime"]["backend_port"], frontend=profiles()["runtime"]["frontend_port"],
        install=False, real=False, reload=False)


def ports_free():
    return all(available(profiles()["runtime"][key]) for key in ("backend_port", "frontend_port"))


def switch(api, target_sha, expected, *, rollback=False):
    with lock(home() / ".release.lock"):
        pending = transition()
        if pending and pending["phase"] not in TERMINAL: raise EnvironmentError("An interrupted release requires explicit recover first")
        old = current()
        require_expected(old, expected)
        if rollback:
            if not old or not old.get("previous_sha"): raise EnvironmentError("No previous release recorded")
            target_sha = old["previous_sha"]
        target = load_stage(check_sha(target_sha))
        # Historical manifests keep their original approval evidence on rollback;
        # new releases record exact-head CI without inventing an owner approval.
        provenance = ({k: target[k] for k in ("repository", "source_pr", "merge_commit_sha", "verified_head_sha", "ci",
                                            "approved_head_sha", "approval_run", "approved_by") if k in target}
                      if rollback else release_provenance(api, target_sha))
        if not rollback: gate(api)  # Required CI and PR protection remain in force.
        if old and target_sha == old["sha"]: raise EnvironmentError("Requested release is already active")
        state = home() / "runtime-state"
        initialization = state / "initialization.json"
        if not initialization.is_file(): raise EnvironmentError("Initialize/import runtime state explicitly before activating; no implicit empty database")
        record = manager.read_record(Path(old["checkout"]), "runtime", state=state) if old else None
        running = manager.alive(record)
        if running and record["checkout"] != old["checkout"]: raise EnvironmentError("Runtime record differs from the deployed version; recover before switching")
        snapshot = home() / ".backups" / uuid.uuid4().hex
        intent = {"target_sha": target_sha, "previous": old, "backup": str(snapshot),
                  "action": "rollback" if rollback else "activate", "created_at": datetime.now(timezone.utc).isoformat()}
        phase(intent, "prepared")
        drained = False
        token = None
        try:
            if running:
                manager.verify(record)
                token = json.loads(Path(record["token_file"]).read_text())["token"]
                request(record["backend_port"], "/api/runtime/drain", token=token)
                drained = True
            elif not ports_free():
                raise EnvironmentError("Unmanaged runtime ports are occupied; existing services were not stopped")
            else:
                storage.require_idle_database(state / "data/yirenhub.sqlite3")
            storage.backup(state, snapshot, old or {"sha": "none"}, allow_empty=old is None and
                           json.loads(initialization.read_text())["mode"] == "explicit-empty")
            phase(intent, "state_snapshotted")
            if running:
                manager.stop(Path(old["checkout"]), "runtime", state=state)
                for _ in range(50):
                    if ports_free(): break
                    time.sleep(0.1)
                if not ports_free():
                    raise EnvironmentError("Runtime ports remained occupied after managed stop")
            phase(intent, "old_stopped")
        except BaseException:
            if drained and manager.alive(record):
                request(record["backend_port"], "/api/runtime/resume", token=token)
            elif drained and old and ports_free():
                start_runtime(load_stage(old["sha"]), state)
            phase(intent, "aborted")
            raise
        failure = None
        try:
            if rollback: storage.restore(state, Path(old["backup"]))
            phase(intent, "candidate_starting")
            start_runtime(target, state)
            updated = {**provenance, "sha": target_sha, "checkout": target["checkout"],
                       "previous_sha": old["sha"] if old else None, "backup": str(snapshot),
                       "changed_at": datetime.now(timezone.utc).isoformat(), "action": "rollback" if rollback else "activate"}
            write_json(home() / "current.json", updated)
            phase(intent, "committed")
            write_json(home() / ".release-history" / (uuid.uuid4().hex + ".json"), {"status": "success", **updated})
            return updated
        except BaseException as exc:
            failure = exc
            # A failed startup is recoverable only after its own supervisor and
            # listeners are demonstrably gone. Never restore under a live writer.
            attempt = manager.read_record(Path(target["checkout"]), "runtime", state=state)
            if manager.alive(attempt) or not ports_free():
                phase(intent, "recovery_required")
                write_json(home() / ".release-history" / (uuid.uuid4().hex + ".json"),
                           {"status": "recovery_required", "target_sha": target_sha, "backup": str(snapshot)})
                raise EnvironmentError(f"Runtime startup is still live/unknown; inspect its supervisor before recovery. Backup: {snapshot}") from exc
            storage.restore(state, snapshot)
            if old: start_runtime(load_stage(old["sha"]), state)
            phase(intent, "failed_restored")
            write_json(home() / ".release-history" / (uuid.uuid4().hex + ".json"),
                       {"status": "failed_restored", "target_sha": target_sha, "previous_sha": old["sha"] if old else None, "backup": str(snapshot)})
            raise EnvironmentError("Activation failed; previous state and managed release were restored") from failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="GitHub owner/repo; defaults to configured origin")
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare", help="Read-only exact-head CI and review readiness")
    prep.add_argument("--pr", type=int, required=True)
    gates = sub.add_parser("gate")
    gates.add_argument("operation", choices=("status", "remove-owner"))
    candidate = sub.add_parser("stage")
    candidate.add_argument("--sha", required=True)
    activate = sub.add_parser("activate", help="Owner-only explicit runtime switch")
    activate.add_argument("--sha", required=True)
    activate.add_argument("--expected-current", required=True, help="Full deployed SHA, or none for explicit first activation")
    back = sub.add_parser("rollback", help="Owner-only restore of the previous release AND pre-deployment state")
    back.add_argument("--expected-current", required=True)
    back.add_argument("--restore-state", action="store_true", required=True)
    recovery = sub.add_parser("recover", help="Owner-only recovery of an interrupted managed switch")
    recovery.add_argument("--expected-current", required=True)
    init = sub.add_parser("init", help="Owner-only explicit fresh runtime state")
    init.add_argument("--empty", action="store_true", required=True)
    migrate = sub.add_parser("import-state", help="Owner-only cold migration; source must be stopped and idle")
    migrate.add_argument("--from-checkout", type=Path, required=True)
    migrate.add_argument("--database", type=Path)
    migrate.add_argument("--config", type=Path)
    migrate.add_argument("--artifacts", type=Path)
    sub.add_parser("status")
    args = parser.parse_args()
    api = GitHub(args.repo)
    if args.action == "prepare": result = prepare(api, args.pr)
    elif args.action == "gate": result = gate(api, remove_owner=args.operation == "remove-owner")
    elif args.action == "stage": result = stage(api, args.sha)
    elif args.action == "activate": result = switch(api, args.sha, args.expected_current)
    elif args.action == "rollback": result = switch(api, None, args.expected_current, rollback=True)
    elif args.action == "recover": result = recover(args.expected_current)
    elif args.action == "status": result = {"current": current(), "transition": transition()}
    else:
        with lock(home() / ".release.lock"):
            if current(): raise EnvironmentError("An existing deployment cannot be reinitialized")
            if args.action == "init": storage.initialize(home() / "runtime-state", empty=True)
            else:
                root = args.from_checkout.expanduser().resolve()
                if checkout_listening(root): raise EnvironmentError("Source checkout still serves a running instance; cold migration refused")
                database = (args.database or root / "data/yirenhub.sqlite3").resolve()
                storage.require_idle_database(database)
                storage.initialize(home() / "runtime-state", database=database,
                    config=(args.config or root / "config").resolve(), artifacts=(args.artifacts or root / "artifacts").resolve())
            result = {"initialized": True, "state": str(home() / "runtime-state")}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try: main()
    except (EnvironmentError, subprocess.CalledProcessError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
