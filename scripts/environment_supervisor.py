"""Own the processes we start; stop only after authenticated atomic draining."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
import urllib.error
import uuid

from scripts.environment_common import (
    ROOT, EnvironmentError, choose_ports, environment, home, lock, private_dir,
    profiles, request, setup, state_path, write_json,
)


def process_start(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def record_path(root, profile, real=False, state=None):
    return (state or state_path(root, profile, real)) / "runtime/instance.json"


def read_record(root, profile, real=False, state=None):
    path = record_path(root, profile, real, state)
    return json.loads(path.read_text()) if path.exists() else None


def alive(record):
    return bool(record and record.get("status") in {"starting", "running"}
                and process_start(record["supervisor_pid"]) == record["supervisor_started_at"])


def verify(record):
    started = request(record["backend_port"])["runtime"]["started"]
    if started.get("instance_id") != record["instance_id"] or started["checkout"] != record["checkout"]:
        raise EnvironmentError("Service identity no longer matches its ownership record")
    return started


def stop(root, profile, real=False, state=None):
    record = read_record(root, profile, real, state)
    if not alive(record): raise EnvironmentError("No live managed supervisor; stale/unmanaged processes are never killed")
    verify(record)
    token = json.loads(Path(record["token_file"]).read_text())["token"]
    request(record["backend_port"], "/api/runtime/drain", token=token)
    # Revalidate PID birth after the network roundtrip, not just the PID number.
    if not alive(record): raise EnvironmentError("Supervisor identity changed during drain")
    os.kill(record["supervisor_pid"], signal.SIGTERM)
    for _ in range(100):
        current = read_record(root, profile, real, state)
        if current and current.get("status") == "stopped":
            print(f"Stopped managed {profile}: {root}")
            return
        time.sleep(0.1)
    raise EnvironmentError("Shutdown still in progress; inspect status rather than killing by port")


def terminate(children):
    for child in reversed(children):
        try: os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    deadline = time.monotonic() + 10
    for child in children:
        try: child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait(timeout=5)


def supervise(root, profile, *, real=False, providers="codex", backend=None, frontend=None,
              allocate=False, backend_only=False, install=True, reload=None, state=None, open_browser=False, reset_onboarding=False):
    state = state or state_path(root, profile, real)
    private_dir(state / "runtime")
    with lock(state / "runtime/owner.lock"):
        py = setup(root, install, backend_only)
        with lock(home(root) / ".port-allocation.lock", wait=True):
            backend, frontend = choose_ports(profile, backend, frontend, allocate=allocate, backend_only=backend_only)
            env = environment(root, profile, real=real, providers=providers,
                              backend=backend, frontend=frontend, state=state)
            instance_id = uuid.uuid4().hex
            token_file = state / "runtime/control.json"
            token = secrets.token_urlsafe(32)
            write_json(token_file, {"token": token})
            env.update(YIREN_LIFECYCLE_TOKEN=token, YIREN_INSTANCE_ID=instance_id)
            record = {
                "instance_id": instance_id, "checkout": str(root), "environment": profile,
                "backend_port": backend, "frontend_port": None if backend_only else frontend,
                "supervisor_pid": os.getpid(), "supervisor_started_at": process_start(os.getpid()),
                "token_file": str(token_file), "status": "starting", "state": str(state),
                "real_cli": real,
            }
            path = record_path(root, profile, real, state)
            write_json(path, record)
            registry = private_dir(home(root) / ".instances")
            key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
            write_json(registry / (key + ".json"), {"record": str(path)})
            children = []
            logs = []
            stopping = False

            def on_signal(_signum, _frame):
                nonlocal stopping
                stopping = True

            old_handlers = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
            try:
                log = (state / "runtime/backend.log").open("a")
                logs.append(log)
                args = [str(py), "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(backend)]
                if reload if reload is not None else profiles()[profile]["reload"]: args.append("--reload")
                children.append(subprocess.Popen(args, cwd=root, env=env, stdout=log, stderr=log, start_new_session=True))
                if not backend_only:
                    production = profile in {"alpha", "runtime"}
                    if profile == "alpha":
                        subprocess.run(["npm", "run", "build"], cwd=root / "frontend", env=env, check=True)
                    if production and not (root / "frontend/dist/__environment").is_file():
                        raise EnvironmentError("Production frontend has not been staged with its source identity")
                    log = (state / "runtime/frontend.log").open("a")
                    logs.append(log)
                    front_command = ["npm", "exec", "vite", "--", "preview"] if production else ["npm", "run", "dev", "--"]
                    children.append(subprocess.Popen([*front_command, "--host", "127.0.0.1", "--port", str(frontend), "--strictPort"],
                        cwd=root / "frontend", env=env, stdout=log, stderr=log, start_new_session=True))
                deadline = time.monotonic() + 45
                while True:
                    if any(c.poll() is not None for c in children): raise EnvironmentError(f"Service exited; inspect {state / 'runtime'}")
                    try:
                        health = request(backend)
                        started = health["runtime"]["started"]
                        if started.get("instance_id") != instance_id: raise EnvironmentError("Unexpected service owns backend port")
                        if not backend_only:
                            front = request(frontend, "/__environment")
                            proxy = request(frontend)["runtime"]["started"]
                            if any(front.get(k) != started.get(k) for k in ("git_sha", "source_digest", "checkout_id", "environment")):
                                raise EnvironmentError("Frontend/backend source identities differ")
                            if proxy.get("instance_id") != instance_id: raise EnvironmentError("Frontend proxy targets another backend")
                        record.update(status="running", backend_pid=started["pid"], git_sha=started["git_sha"], source_digest=started["source_digest"])
                        write_json(path, record)
                        if reset_onboarding: request(backend, "/api/onboarding/reset", token=token)
                        break
                    except (OSError, ValueError, KeyError, urllib.error.URLError):
                        if time.monotonic() > deadline: raise EnvironmentError(f"Startup identity/health unavailable; inspect {state / 'runtime'}")
                        time.sleep(0.2)
            except BaseException:
                terminate(children)
                record["status"] = "stopped"
                write_json(path, record)
                for log in logs: log.close()
                for s, handler in old_handlers.items(): signal.signal(s, handler)
                raise
        print(f"{profile} ready: http://127.0.0.1:{frontend if not backend_only else backend} · {record['git_sha'][:7]} · {state}", flush=True)
        if open_browser and not backend_only:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{frontend}")
        try:
            while True:
                if any(c.poll() is not None for c in children): raise EnvironmentError("A managed service exited unexpectedly")
                if stopping:
                    try:
                        request(backend, "/api/runtime/drain", token=token)
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code != 409: raise
                        print("Waiting for active work to finish before stopping…", flush=True)
                time.sleep(0.5)
        finally:
            terminate(children)
            record["status"] = "stopped"
            write_json(path, record)
            token_file.unlink(missing_ok=True)
            for log in logs: log.close()
            for s, handler in old_handlers.items(): signal.signal(s, handler)


def start(root, profile, *, detach=False, **kwargs):
    record = read_record(root, profile, kwargs.get("real", False), kwargs.get("state"))
    if alive(record):
        verify(record)
        if kwargs.get("reset_onboarding"):
            token = json.loads(Path(record["token_file"]).read_text())["token"]
            request(record["backend_port"], "/api/onboarding/reset", token=token)
        print(f"Already running: {root} ({profile})")
        return record
    if not detach:
        supervise(root, profile, **kwargs)
        return
    py = setup(root, kwargs.get("install", True), kwargs.get("backend_only", False))
    state = kwargs.get("state") or state_path(root, profile, kwargs.get("real", False))
    private_dir(state / "runtime")
    spec = {"root": str(root), "profile": profile, **kwargs, "install": False}
    if spec.get("state"): spec["state"] = str(spec["state"])
    spec_path = state / "runtime/launch.json"
    write_json(spec_path, spec)
    with (state / "runtime/supervisor.log").open("a") as log:
        child = subprocess.Popen([py, ROOT / "scripts/environment_cli.py", "_supervise", str(spec_path)],
                                  cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
    for _ in range(500):
        record = read_record(root, profile, kwargs.get("real", False), kwargs.get("state"))
        if record and record.get("supervisor_pid") == child.pid and record.get("status") == "running":
            print(f"{profile} ready: http://127.0.0.1:{record['frontend_port'] or record['backend_port']} · {record['git_sha'][:7]}")
            return record
        if child.poll() is not None: raise EnvironmentError(f"Supervisor failed; inspect {state / 'runtime/supervisor.log'}")
        time.sleep(0.1)
    raise EnvironmentError("Supervisor startup still pending; inspect its live record before retrying")
