"""Tests that each mission uses a fresh Settings snapshot at start time."""

import json
import threading
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings
from backend.app.storage.hub_settings import save


def _make_env(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "cli-providers.json").write_text(json.dumps([
        {"id": "fake", "label": "Fake", "command": "echo", "args": [], "enabled": True},
    ]), encoding="utf-8")
    (config_dir / "agents.json").write_text(json.dumps([
        {"id": "chulan", "display_name": "张楚岚", "role": "规划", "persona": "拆解",
         "default_provider_id": "fake", "fallback_provider_ids": []},
        {"id": "baobao", "display_name": "冯宝宝", "role": "执行", "persona": "产物",
         "default_provider_id": "fake", "fallback_provider_ids": []},
        {"id": "wangye", "display_name": "王也", "role": "分析", "persona": "风险",
         "default_provider_id": "fake", "fallback_provider_ids": []},
    ]), encoding="utf-8")
    db_path = tmp_path / "data" / "yirenhub.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(database_path=db_path, config_dir=config_dir, use_fake_cli=True)
    app = create_app(settings)
    return TestClient(app), settings


def test_mission_uses_current_settings_at_start(tmp_path):
    client, settings = _make_env(tmp_path)

    captured_max_turns = []
    original_run = None

    def _capture_runner_init(self, repo, s, adapter, **kw):
        captured_max_turns.append(s.max_turns)
        original_run(self, repo, s, adapter, **kw)

    from backend.app.orchestration.runner import MissionRunner
    original_run = MissionRunner.__init__

    with mock.patch.object(MissionRunner, "__init__", _capture_runner_init):
        # Mission A: default max_turns=6
        resp = client.post("/api/missions", json={"goal": "test A"})
        assert resp.status_code == 201

        import time
        time.sleep(0.3)

        # Change settings via PUT
        client.put("/api/hub/settings", json={"runtime": {"max_turns": 8}})

        # Mission B: should see max_turns=8
        resp = client.post("/api/missions", json={"goal": "test B"})
        assert resp.status_code == 201
        time.sleep(0.3)

    assert len(captured_max_turns) >= 2
    assert captured_max_turns[0] == 6
    assert captured_max_turns[1] == 8


def test_inflight_mission_unaffected_by_settings_change(tmp_path):
    client, settings = _make_env(tmp_path)

    captured_settings = []
    original_init = None

    def _capture_runner_init(self, repo, s, adapter, **kw):
        captured_settings.append(s)
        original_init(self, repo, s, adapter, **kw)

    from backend.app.orchestration.runner import MissionRunner
    original_init = MissionRunner.__init__

    with mock.patch.object(MissionRunner, "__init__", _capture_runner_init):
        resp = client.post("/api/missions", json={"goal": "test A"})
        assert resp.status_code == 201

        import time
        time.sleep(0.3)

        assert len(captured_settings) >= 1
        snapshot_a = captured_settings[0]
        assert snapshot_a.max_turns == 6

        client.put("/api/hub/settings", json={"runtime": {"max_turns": 12}})

        assert snapshot_a.max_turns == 6, (
            "in-flight mission's snapshot mutated after PUT — "
            "missions.py must hand each mission its own Settings instance"
        )
