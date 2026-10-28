"""Read-only source and process identity. Never infer a running SHA from disk."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATHS = ("backend", "frontend", "scripts", "config", "requirements.txt")
# Packaged builds have no Git checkout; the packager freezes the identity the
# frontend bundle was built with so both sides still report the same source.
BUILD_IDENTITY = "build-identity.json"
IDENTITY_FIELDS = ("git_sha", "branch", "dirty", "source_digest", "checkout_id")


def git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL,
            timeout=5, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        ).decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.SubprocessError):
        return None


def source_snapshot(root: Path = ROOT) -> dict:
    root = root.resolve()
    sha = git(root, "rev-parse", "--verify", "HEAD")
    if not sha and (root / BUILD_IDENTITY).is_file():
        try:
            frozen = json.loads((root / BUILD_IDENTITY).read_text(encoding="utf-8"))
            return {key: frozen.get(key) for key in IDENTITY_FIELDS}
        except (OSError, ValueError):
            pass
    status = git(root, "status", "--porcelain", "--untracked-files=normal")
    digest = None
    # Hash source contents, including staged and untracked code, so two dirty
    # checkouts at the same SHA are not reported as the same version.
    if sha:
        try:
            paths = subprocess.check_output(
                ["git", "-C", str(root), "ls-files", "-z", "--cached",
                 "--others", "--exclude-standard", "--", *SOURCE_PATHS],
                stderr=subprocess.DEVNULL, timeout=5,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            ).split(b"\0")
            hasher = hashlib.sha256()
            for raw in sorted(set(p for p in paths if p)):
                path = root / os.fsdecode(raw)
                hasher.update(raw + b"\0")
                if path.is_symlink():
                    hasher.update(b"symlink\0" + os.fsencode(os.readlink(path)))
                elif path.is_file():
                    hasher.update(path.read_bytes())
                else:
                    hasher.update(b"deleted\0")
                hasher.update(b"\0")
            digest = hasher.hexdigest()
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "git_sha": sha,
        "branch": git(root, "symbolic-ref", "--quiet", "--short", "HEAD") if sha else None,
        "dirty": bool(status) if status is not None else None,
        "source_digest": digest,
        "checkout_id": hashlib.sha256(os.fsencode(root)).hexdigest()[:16],
    }


def frontend_identity(root: Path = ROOT) -> dict:
    return {
        **source_snapshot(root),
        "environment": os.environ.get("YIREN_ENVIRONMENT", "local"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


# Freeze source identity when this backend module is loaded, not when a later
# health request happens to arrive. Multiple apps in one process share code.
PROCESS_IDENTITY = frontend_identity()


def capture_runtime(settings: Settings, root: Path | None = None) -> dict:
    return {
        **(frontend_identity(root) if root else PROCESS_IDENTITY),
        "pid": os.getpid(),
        "instance_id": os.environ.get("YIREN_INSTANCE_ID"),
        "python": sys.version.split()[0],
        "checkout": str((root or ROOT).resolve()),
        "database": str(settings.database_path.resolve()),
        "config": str(settings.config_dir.resolve()),
        "artifacts": str(settings.artifact_root.resolve()),
        "maintenance": str(settings.maintenance_worktree_root.resolve()),
        "runtime_dir": str(Path(os.environ.get("YIREN_RUNTIME_DIR", ROOT / ".yiren")).resolve()),
        # This is a configuration flag, not proof that a real call succeeded.
        "use_fake_cli": settings.use_fake_cli,
    }


def runtime_view(started: dict) -> dict:
    current = source_snapshot(Path(started["checkout"]))
    known = all(s.get("git_sha") and s.get("source_digest") for s in (started, current))
    return {
        "started": started,
        "current": current,
        "source_changed": (
            (started["git_sha"], started["source_digest"]) !=
            (current["git_sha"], current["source_digest"])
        ) if known else None,
    }
