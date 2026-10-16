import json
from pathlib import Path

import pytest

from backend.app.adapters import antigravity_config as agc


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Point Path.home() at a tmp dir so we never touch the real ~/.gemini."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


SPEC = {"command": "py", "args": ["-m", "backend.app.mcp.server"], "env": {"X": "1"}}


def _global_text(fake_home):
    return (fake_home / ".gemini" / "config" / "mcp_config.json").read_text(encoding="utf-8")


def test_guard_injects_yiren_hub_during_block_and_removes_when_absent(fake_home):
    # No pre-existing global config.
    assert not agc.global_mcp_config_path().exists()
    with agc.guarded_global_mcp_config(SPEC):
        injected = json.loads(_global_text(fake_home))
        assert injected == {"mcpServers": {"yiren_hub": SPEC}}
    # Original did not exist -> file removed after the block.
    assert not agc.global_mcp_config_path().exists()
    assert not agc._sentinel_path().exists()


def test_guard_restores_pre_existing_content_exactly(fake_home):
    cfg = agc.global_mcp_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    original = '{"mcpServers": {"user_server": {"command": "x"}}}\n'
    cfg.write_text(original, encoding="utf-8")
    with agc.guarded_global_mcp_config(SPEC):
        injected = json.loads(_global_text(fake_home))
        assert "user_server" not in injected["mcpServers"]  # user server hidden during run
        assert "yiren_hub" in injected["mcpServers"]
    assert cfg.read_text(encoding="utf-8") == original  # byte-exact restore
    assert not agc._sentinel_path().exists()


def test_recover_if_crashed_restores_from_sentinel(fake_home):
    cfg = agc.global_mcp_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # Simulate a crash mid-turn: sentinel says original did not exist, and a
    # stale yiren config was left behind pointing at a dead temp server.
    agc._sentinel_path().write_text(json.dumps({"existed": False, "content": None}), encoding="utf-8")
    cfg.write_text('{"mcpServers": {"yiren_hub": {"command": "dead"}}}', encoding="utf-8")
    agc.recover_antigravity_global_config()
    assert not cfg.exists()  # restored to "did not exist"
    assert not agc._sentinel_path().exists()


def test_recover_is_noop_when_config_dir_absent(fake_home):
    # User who never ran gemini/agy: ~/.gemini/config does not exist.
    assert not (fake_home / ".gemini" / "config").exists()
    agc.recover_antigravity_global_config()  # must not create the dir
    assert not (fake_home / ".gemini" / "config").exists()


def test_recover_if_crashed_restores_preexisting_content(fake_home):
    cfg = agc.global_mcp_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    original = '{"mcpServers": {"user_server": {"command": "x"}}}\n'
    agc._sentinel_path().write_text(
        json.dumps({"existed": True, "content": original}), encoding="utf-8"
    )
    cfg.write_text('{"mcpServers": {"yiren_hub": {"command": "dead"}}}', encoding="utf-8")
    agc.recover_antigravity_global_config()
    assert cfg.read_text(encoding="utf-8") == original
    assert not agc._sentinel_path().exists()


def test_guard_restores_on_exception_inside_block(fake_home):
    cfg = agc.global_mcp_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    original = '{"mcpServers": {"user_server": {}}}\n'
    cfg.write_text(original, encoding="utf-8")
    with pytest.raises(RuntimeError):
        with agc.guarded_global_mcp_config(SPEC):
            raise RuntimeError("simulated caller failure")
    assert cfg.read_text(encoding="utf-8") == original
    assert not agc._sentinel_path().exists()


def test_sequential_guards_do_not_deadlock(fake_home):
    with agc.guarded_global_mcp_config(SPEC):
        pass
    with agc.guarded_global_mcp_config(SPEC):
        pass
    assert not agc.global_mcp_config_path().exists()


def test_recover_creates_no_lock_file_when_no_sentinel(fake_home):
    # Config dir exists (user uses gemini) but there was no crash (no sentinel).
    cfg_dir = fake_home / ".gemini" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    agc.recover_antigravity_global_config()
    # Fast path must not acquire the lock, so no lock file is created.
    assert not agc._lock_path().exists()


# -- read_agy_log_hint ------------------------------------------------------

def _write_log(fake_home, name: str, body: str, mtime: float | None = None) -> Path:
    log_dir = fake_home / ".gemini" / "antigravity-cli" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / name
    p.write_text(body, encoding="utf-8")
    if mtime is not None:
        import os as _os
        _os.utime(p, (mtime, mtime))
    return p


def test_log_hint_returns_none_when_dir_missing(fake_home):
    import time
    assert agc.read_agy_log_hint(time.time() - 5) is None


def test_log_hint_returns_none_when_no_recent_log(fake_home):
    # Old log written 1 hour before run_start must be ignored.
    import time
    now = time.time()
    _write_log(fake_home, "cli-20260101_000000.log", "RESOURCE_EXHAUSTED",
               mtime=now - 3600)
    assert agc.read_agy_log_hint(now) is None


def test_log_hint_extracts_quota_message(fake_home):
    import time
    now = time.time()
    body = (
        "I0528 15:42:49 server.go:1083] Sending user message\n"
        "E0528 15:42:51 log.go:398] agent executor error: "
        "RESOURCE_EXHAUSTED (code 429): Individual quota reached. "
        "Contact your administrator to enable overages. Resets in 18m20s.\n"
        "I0528 15:42:51 manager.go:459] CLI store manager shutting down\n"
    )
    _write_log(fake_home, "cli-20260528_154242.log", body, mtime=now)
    hint = agc.read_agy_log_hint(now - 1)
    assert hint is not None
    assert "quota exhausted" in hint
    assert "Resets in 18m20s." in hint


def test_log_hint_extracts_unauthenticated(fake_home):
    import time
    now = time.time()
    body = (
        "E ... server.go:609] Failed to get OAuth token: error getting "
        "token source from auth provider: You are not logged into Antigravity.\n"
        "I ... CLI program exited, shutting down\n"
    )
    _write_log(fake_home, "cli-20260528_153538.log", body, mtime=now)
    hint = agc.read_agy_log_hint(now - 1)
    assert hint == "agy not authenticated — run `agy` interactively to OAuth login"


def test_log_hint_falls_back_to_executor_error(fake_home):
    import time
    now = time.time()
    body = (
        "I ... server happy happy\n"
        "E ... log.go:398] agent executor error: something obscure happened\n"
    )
    _write_log(fake_home, "cli-20260528_120000.log", body, mtime=now)
    hint = agc.read_agy_log_hint(now - 1)
    assert hint is not None
    assert hint.startswith("agy agent executor error:")
    assert "something obscure happened" in hint


def test_log_hint_picks_latest_file(fake_home):
    import time
    now = time.time()
    _write_log(fake_home, "cli-20260528_100000.log",
               "not logged into Antigravity", mtime=now - 10)
    _write_log(fake_home, "cli-20260528_154242.log",
               "RESOURCE_EXHAUSTED (code 429): boom", mtime=now)
    hint = agc.read_agy_log_hint(now - 30)
    assert hint is not None
    # Latest file wins -> quota, not auth.
    assert "quota exhausted" in hint


@pytest.mark.parametrize("success", [
    "ChainedAuth: authenticated via keyring",
    "OAuth: authenticated successfully",
    "silent auth succeeded",
])
def test_auth_probe_failure_before_success_is_not_a_login_failure(fake_home, success):
    import time
    now = time.time()
    _write_log(fake_home, "cli-recovered.log",
               "not logged into Antigravity\n" + success + "\nprint timeout after 3m50s", mtime=now)
    assert agc.read_agy_log_hint(now) is None


def test_new_login_failure_after_success_is_reported(fake_home):
    import time
    now = time.time()
    _write_log(fake_home, "cli-expired.log",
               "authenticated via keyring\nnot logged into Antigravity", mtime=now)
    assert "not authenticated" in agc.read_agy_log_hint(now)
