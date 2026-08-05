"""Tests for Settings resolution priority: file > env > default."""

import json

from backend.app.settings import Settings, get_settings
from backend.app.storage.hub_settings import save


def test_settings_default_when_no_file_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("YIREN_MAX_TURNS", raising=False)
    monkeypatch.delenv("YIREN_USE_FAKE_CLI", raising=False)
    db = tmp_path / "data" / "yirenhub.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(db))
    s = get_settings()
    assert s.max_turns == 6
    assert s.provider_overrides == {}


def test_env_overrides_default(tmp_path, monkeypatch):
    db = tmp_path / "data" / "yirenhub.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(db))
    monkeypatch.setenv("YIREN_MAX_TURNS", "10")
    s = get_settings()
    assert s.max_turns == 10


def test_file_overrides_env(tmp_path, monkeypatch):
    db = tmp_path / "data" / "yirenhub.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(db))
    monkeypatch.setenv("YIREN_MAX_TURNS", "10")
    settings_file = tmp_path / "data" / "hub_settings.json"
    save(settings_file, {"schema_version": 1, "runtime": {"max_turns": 12}})
    s = get_settings()
    assert s.max_turns == 12


def test_provider_overrides_loaded(tmp_path, monkeypatch):
    db = tmp_path / "data" / "yirenhub.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(db))
    settings_file = tmp_path / "data" / "hub_settings.json"
    save(settings_file, {"schema_version": 1, "providers": {"claude": {"enabled": True}}})
    s = get_settings()
    assert "claude" in s.provider_overrides
    assert s.provider_overrides["claude"].enabled is True


def test_retired_gemini_provider_is_removed_during_settings_load(tmp_path, monkeypatch):
    db = tmp_path / "data" / "yirenhub.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(db))
    settings_file = tmp_path / "data" / "hub_settings.json"
    save(
        settings_file,
        {
            "schema_version": 1,
            "providers": {"gemini": {"enabled": True}},
            "agents": {
                "wangye": {
                    "default_provider_id": "gemini",
                    "fallback_provider_ids": ["codex", "gemini"],
                }
            },
        },
    )

    settings = get_settings()
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))

    assert "gemini" not in settings.provider_overrides
    assert settings.agent_overrides["wangye"].default_provider_id == "antigravity"
    assert settings.agent_overrides["wangye"].fallback_provider_ids == ["codex"]
    assert "gemini" not in persisted["providers"]


def test_unknown_runtime_key_ignored(tmp_path, monkeypatch):
    db = tmp_path / "data" / "yirenhub.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(db))
    settings_file = tmp_path / "data" / "hub_settings.json"
    save(settings_file, {"schema_version": 1, "runtime": {"frobnicate": 42}})
    s = get_settings()
    assert s.max_turns == 6  # default, not affected by unknown key
