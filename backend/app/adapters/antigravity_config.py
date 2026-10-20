"""Guarded mutate-restore of Antigravity CLI's global MCP config.

Antigravity CLI 1.0.2 loads MCP servers ONLY from the hardcoded global
``~/.gemini/config/mcp_config.json`` — it ignores ``GEMINI_CLI_HOME`` /
``XDG_CONFIG_HOME`` / ``--app_data_dir``, ``HOME`` redirect breaks its OS
keyring auth, and there is no ``--mcp-config`` flag. So the only way to inject
the AgentClub MCP server is to temporarily rewrite that global file for the
duration of the agy subprocess, then restore it.

Safety mechanisms (all required, see the amending ADR
2026-05-27-antigravity-global-config-exception.md):
  1. ``fcntl.flock`` cross-process exclusive lock held for the whole turn.
  2. ``finally`` restore (original content, or delete if it did not exist).
  3. A sentinel file persists the true-original so a hard-kill mid-turn can be
     recovered on the next run / at startup.
  4. We only write ``{"mcpServers": {"yiren_hub": ...}}`` — the user's own MCP
     servers are hidden during the run, never leaked into agent turns.

Paths are computed via functions (not module-level constants) so tests can
monkeypatch ``Path.home``.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator, Optional

# Mirror of MCPSubprocessAdapter.MCP_SERVER_NAME; duplicated to keep this module
# dependency-free (mcp_subprocess imports us, not the other way around).
_SERVER_NAME = "yiren_hub"

# agy 1.0.3 swallows several fatal conditions into silent ``exit 0`` (no stdout
# / no stderr) — notably "not logged into Antigravity" and Google-side quota
# 429 ``RESOURCE_EXHAUSTED``. Without context the BLOCKED summary just reads
# ``agent did not call publish_message via MCP``, which is true but useless.
# ``read_agy_log_hint`` peeks the most recent agy log file written during the
# current run and lifts a one-line hint out, so the BLOCKED summary surfaces
# the real cause.
_AGY_LOG_TAIL_BYTES = 256 * 1024
_QUOTA_PATTERN = re.compile(r"RESOURCE_EXHAUSTED[^\n]*", re.IGNORECASE)
_EXECUTOR_PATTERN = re.compile(r"agent executor error:\s*([^\n]{1,240})")


def _config_dir() -> Path:
    return Path.home() / ".gemini" / "config"


def global_mcp_config_path() -> Path:
    return _config_dir() / "mcp_config.json"


def _lock_path() -> Path:
    return _config_dir() / ".yiren-mcp.lock"


def _sentinel_path() -> Path:
    return _config_dir() / "mcp_config.json.yiren-bak"


def _read_or_none(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _write_sentinel(original: Optional[str]) -> None:
    payload = {"existed": original is not None, "content": original}
    _sentinel_path().write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _restore(original: Optional[str]) -> None:
    target = global_mcp_config_path()
    if original is None:
        target.unlink(missing_ok=True)
    else:
        target.write_text(original, encoding="utf-8")


def _recover_if_crashed() -> None:
    """If a sentinel exists, a previous run was hard-killed before restoring.

    The sentinel holds the true-original content; restore from it and delete
    the sentinel. Must be called while holding the lock.
    """
    sentinel = _sentinel_path()
    if not sentinel.is_file():
        return
    try:
        payload = json.loads(sentinel.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        sentinel.unlink(missing_ok=True)
        return
    original = payload.get("content") if payload.get("existed") else None
    _restore(original)
    sentinel.unlink(missing_ok=True)


@contextlib.contextmanager
def _flock(*, wait_timeout=None, cancelled=None) -> Iterator[None]:
    """Cross-process exclusive lock on ~/.gemini/config/.yiren-mcp.lock."""
    cfg_dir = _config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_lock_path()), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if wait_timeout is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + wait_timeout
            while True:
                if cancelled and cancelled():
                    raise RuntimeError('Discussion stopped while waiting for provider')
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Provider configuration lock wait timed out')
                    time.sleep(.1)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextlib.contextmanager
def guarded_global_mcp_config(server_spec: dict[str, Any], *, wait_timeout=None, cancelled=None) -> Iterator[Path]:
    """Temporarily replace the global MCP config with only our yiren_hub server.

    Holds the lock for the entire ``with`` body (the caller runs and waits on
    the agy subprocess inside it). Restores the original on exit, even on
    exception / kill-then-finally.
    """
    with _flock(wait_timeout=wait_timeout, cancelled=cancelled):
        _recover_if_crashed()
        target = global_mcp_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        original = _read_or_none(target)
        _write_sentinel(original)
        target.write_text(
            json.dumps({"mcpServers": {_SERVER_NAME: server_spec}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            yield target
        finally:
            _restore(original)
            _sentinel_path().unlink(missing_ok=True)


def recover_antigravity_global_config() -> None:
    """Startup crash recovery. No-op when ~/.gemini/config does not exist.

    Covers the window where a hard-kill left our temp config behind and the
    user ran agy manually before the next AgentClub turn.

    Fast path: if no sentinel exists there is nothing to recover, so we skip
    acquiring the lock entirely — this keeps the common (no-crash) case
    side-effect-free (no lock file created) on every ProviderRouterAdapter
    construction. The authoritative check still happens under the lock.
    """
    if not _config_dir().exists():
        return
    if not _sentinel_path().is_file():
        return
    with _flock():
        _recover_if_crashed()


def _agy_log_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "log"


def read_agy_log_hint(run_start: float) -> Optional[str]:
    """Best-effort: pull a one-line diagnostic out of the most recent agy log.

    Returns ``None`` when no log was written during this run, the directory is
    missing, or no known pattern matches. All I/O errors are swallowed — this
    is purely a UX enrichment, never a failure source.

    ``run_start`` is the wall-clock time (``time.time()``) captured right
    before launching the agy subprocess; we ignore logs older than that
    (with a 1s grace for clock skew). Patterns in priority order:

    1. ``RESOURCE_EXHAUSTED`` (Google quota 429) — most actionable.
    2. ``not logged into Antigravity`` (OAuth missing).
    3. Generic ``agent executor error:`` line as a fallback.
    """
    try:
        log_dir = _agy_log_dir()
        if not log_dir.is_dir():
            return None
        candidates = [
            p for p in log_dir.glob("cli-*.log")
            if p.is_file() and p.stat().st_mtime >= run_start - 1.0
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        try:
            size = latest.stat().st_size
            with latest.open("rb") as fh:
                if size > _AGY_LOG_TAIL_BYTES:
                    fh.seek(-_AGY_LOG_TAIL_BYTES, os.SEEK_END)
                data = fh.read()
            text = data.decode("utf-8", errors="replace")
        except OSError:
            return None

        m = _QUOTA_PATTERN.search(text)
        if m:
            return f"agy quota exhausted: {m.group(0).strip()[:240]}"
        # Authentication probes can fail before keyring/silent OAuth succeeds.
        # The last observed auth event decides the diagnostic.
        lower = text.lower()
        last_failure = lower.rfind("not logged into antigravity")
        last_success = max(lower.rfind(marker) for marker in (
            "authenticated via keyring", "oauth: authenticated successfully",
            "silent auth succeeded",
        ))
        if last_failure >= 0 and last_failure > last_success:
            return "agy not authenticated — run `agy` interactively to OAuth login"
        m = _EXECUTOR_PATTERN.search(text)
        if m:
            return f"agy agent executor error: {m.group(1).strip()}"
        return None
    except Exception:
        return None
