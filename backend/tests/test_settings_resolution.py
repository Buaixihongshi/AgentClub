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


def test_timeout_api_updates_next_mission_adapter_despite_stale_app_settings(temp_settings):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.settings import snapshot_settings
    from backend.app.api.missions import _build_mission_adapter
    from backend.app.storage.hub_settings import default_path

    # Existing user override wins over the newer 300s code default.
    app_settings = temp_settings.model_copy(update={"cli_timeout": 300, "use_fake_cli": False})
    save(default_path(app_settings), {"runtime": {"cli_timeout": 240, "max_turns": 9}})
    client = TestClient(create_app(app_settings))
    assert client.get("/api/hub/settings").json()["runtime"]["cli_timeout"]["value"] == 240
    before = _build_mission_adapter(snapshot_settings(app_settings))
    assert before.cli_timeout == 240
    response = client.put("/api/hub/settings", json={"runtime": {"cli_timeout": 300}})
    assert response.status_code == 200
    snapshot = snapshot_settings(app_settings)
    assert snapshot.max_turns == 9
    assert _build_mission_adapter(snapshot).cli_timeout == 300
    assert response.json()["runtime"]["cli_timeout"] == {"value": 300, "source": "user"}
