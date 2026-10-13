from __future__ import annotations

import subprocess
from pathlib import Path

from backend.app.domain.maintenance import MaintenanceRun, RegisteredRepository


class WorkspaceError(RuntimeError):
    pass


def validate_repository_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise WorkspaceError("Repository path does not exist or is not a directory")
    result = _run_git(resolved, ["rev-parse", "--show-toplevel"])
    root = Path(result.stdout.strip()).resolve()
    if root != resolved:
        raise WorkspaceError("Register the Git repository root, not a nested directory")
    return resolved


class WorkspaceManager:
    def __init__(self, worktree_root: Path):
        self.worktree_root = worktree_root.expanduser().resolve()

    def prepare(self, repository: RegisteredRepository, run: MaintenanceRun) -> MaintenanceRun:
        source = validate_repository_path(repository.local_path)
        target = (self.worktree_root / run.id).resolve()
        if target == self.worktree_root or self.worktree_root not in target.parents:
            raise WorkspaceError("Maintenance workspace escaped configured root")
        if target.exists():
            raise WorkspaceError("Maintenance workspace already exists")

        _run_git(source, ["fetch", repository.remote_name, repository.default_branch])
        branch = "codex/maintenance-{suffix}".format(suffix=run.id.removeprefix("mr_")[:12])
        base_ref = "{remote}/{branch}".format(
            remote=repository.remote_name,
            branch=repository.default_branch,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        _run_git(source, ["worktree", "add", "-b", branch, str(target), base_ref])
        return run.model_copy(update={"branch_name": branch, "workspace_path": target})


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        detail = stderr.strip() or str(exc)
        raise WorkspaceError("git {args} failed: {detail}".format(
            args=" ".join(args), detail=detail[:500]
        )) from exc
