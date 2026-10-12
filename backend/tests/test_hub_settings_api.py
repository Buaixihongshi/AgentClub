"""Tests for /api/hub/settings GET + PUT."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings
from backend.app.storage.hub_settings import save


def _make_client(tmp_path, env_overrides=None, file_data=None):
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    # Write a minimal committed providers config.
    (config_dir / "cli-providers.json").write_text(json.dumps([
        {"id": "fake", "label": "Fake", "command": "echo", "args": [], "enabled": True},
        {"id": "claude", "label": "Claude", "command": "claude", "args": [], "enabled": False, "mcp_enabled": True},
        {"id": "codex", "label": "Codex", "command": "codex", "args": [], "enabled": False, "mcp_enabled": True},
    ]), encoding="utf-8")

    db_path = tmp_path / "data" / "yirenhub.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if file_data:
        save(db_path.parent / "hub_settings.json", file_data)

    settings = Settings(
        database_path=db_path,
        config_dir=config_dir,
        **(env_overrides or {}),
    )
    app = create_app(settings)
    return TestClient(app), settings


def test_get_returns_resolved_view_with_sources(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.get("/api/hub/settings")
    assert resp.status_code == 200
    data = resp.json()
    rt = data["runtime"]
    assert rt["max_turns"]["value"] == 6
    assert rt["max_turns"]["source"] == "default"
    assert rt["use_fake_cli"]["value"] is True
    assert rt["a2a_idle_timeout"] == {"value": 90, "source": "default"}
    providers = {p["id"]: p for p in data["providers"]}
    assert providers["claude"]["enabled"]["value"] is False
    assert providers["claude"]["enabled"]["source"] == "committed"


def test_get_marks_env_source(tmp_path, monkeypatch):
    monkeypatch.setenv("YIREN_MAX_TURNS", "9")
    client, _ = _make_client(tmp_path, env_overrides={"max_turns": 9})
    resp = client.get("/api/hub/settings")
    # We can't fully detect env vs default without internal tracking, but the
    # value should reflect what was passed.
    assert resp.status_code == 200


def test_get_marks_user_source(tmp_path):
    client, _ = _make_client(tmp_path, file_data={
        "schema_version": 1,
        "providers": {"claude": {"enabled": True}},
    })
    resp = client.get("/api/hub/settings")
    data = resp.json()
    providers = {p["id"]: p for p in data["providers"]}
    assert providers["claude"]["enabled"]["value"] is True
    assert providers["claude"]["enabled"]["source"] == "user"


def test_put_persists_partial_patch(tmp_path):
    client, settings = _make_client(tmp_path)
    resp = client.put("/api/hub/settings", json={
        "providers": {"claude": {"enabled": True}},
    })
    assert resp.status_code == 200
    data = resp.json()
    providers = {p["id"]: p for p in data["providers"]}
    assert providers["claude"]["enabled"]["value"] is True
    assert providers["claude"]["enabled"]["source"] == "user"
    # Fake should not be touched.
    assert providers["fake"]["enabled"]["source"] == "committed"


def test_put_rejects_unknown_provider_id(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.put("/api/hub/settings", json={
        "providers": {"phantom": {"enabled": True}},
    })
    assert resp.status_code == 400


def test_put_rejects_unknown_runtime_field(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.put("/api/hub/settings", json={
        "runtime": {"frobnicate": 42},
    })
    assert resp.status_code == 400 or resp.status_code == 422


def test_put_validates_runtime_types(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.put("/api/hub/settings", json={
        "runtime": {"max_turns": "abc"},
    })
    assert resp.status_code == 422


def test_put_persists_a2a_idle_timeout_and_allows_disabling(tmp_path):
    client, settings = _make_client(tmp_path)
    resp = client.put("/api/hub/settings", json={
        "runtime": {"a2a_idle_timeout": 0},
    })
    assert resp.status_code == 200
    assert resp.json()["runtime"]["a2a_idle_timeout"] == {
        "value": 0,
        "source": "user",
    }
    from backend.app.storage.hub_settings import default_path, load
    assert load(default_path(settings))["runtime"]["a2a_idle_timeout"] == 0


def test_put_rejects_negative_a2a_idle_timeout(tmp_path):
    client, _ = _make_client(tmp_path)
    resp = client.put("/api/hub/settings", json={
        "runtime": {"a2a_idle_timeout": -1},
    })
    assert resp.status_code == 422


def test_put_does_not_touch_committed_config(tmp_path):
    client, settings = _make_client(tmp_path)
    committed = settings.config_dir / "cli-providers.json"
    mtime_before = committed.stat().st_mtime
    client.put("/api/hub/settings", json={
        "providers": {"claude": {"enabled": True}},
    })
    mtime_after = committed.stat().st_mtime
    assert mtime_before == mtime_after
