"""Regression: conversation follow-up missions must also re-read hub_settings.json.

Previously ``POST /api/conversations/{id}/missions`` handed the runner the
app-level Settings cached at startup, so UI overrides (provider toggles,
agent default provider) were silently ignored when the user continued an
existing conversation.
"""

import json
import time
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings


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


def test_conversation_followup_mission_sees_updated_runtime(tmp_path):
    client, _ = _make_env(tmp_path)

    captured = []
    original_init = None

    def _capture_runner_init(self, repo, s, adapter, **kw):
        captured.append(s)
        original_init(self, repo, s, adapter, **kw)

    from backend.app.orchestration.runner import MissionRunner
    original_init = MissionRunner.__init__

    with mock.patch.object(MissionRunner, "__init__", _capture_runner_init):
        conv = client.post("/api/conversations", json={"title": "t"}).json()
        conv_id = conv["id"]

        # First mission under conversation: default max_turns=6
        resp = client.post(
            "/api/conversations/{cid}/missions".format(cid=conv_id),
            json={"goal": "first"},
        )
        assert resp.status_code == 201
        time.sleep(0.3)

        client.put("/api/hub/settings", json={"runtime": {"max_turns": 9}})

        # Follow-up mission: must observe the new max_turns=9
        resp = client.post(
            "/api/conversations/{cid}/missions".format(cid=conv_id),
            json={"goal": "second"},
        )
        assert resp.status_code == 201
        time.sleep(0.3)

    assert len(captured) >= 2
    assert captured[0].max_turns == 6
    assert captured[1].max_turns == 9, (
        "conversation follow-up still using stale Settings — "
        "POST /api/conversations/{id}/missions must call snapshot_settings()"
    )


def test_conversation_followup_mission_sees_agent_override(tmp_path):
    client, _ = _make_env(tmp_path)

    captured = []
    original_init = None

    def _capture_runner_init(self, repo, s, adapter, **kw):
        captured.append(s)
        original_init(self, repo, s, adapter, **kw)

    from backend.app.orchestration.runner import MissionRunner
    original_init = MissionRunner.__init__

    with mock.patch.object(MissionRunner, "__init__", _capture_runner_init):
        conv = client.post("/api/conversations", json={"title": "t"}).json()
        conv_id = conv["id"]

        # Save an agent override BEFORE the conversation has any mission
        resp = client.put(
            "/api/hub/settings",
            json={"agents": {"wangye": {"default_provider_id": "fake"}}},
        )
        assert resp.status_code == 200

        resp = client.post(
            "/api/conversations/{cid}/missions".format(cid=conv_id),
            json={"goal": "hello"},
        )
        assert resp.status_code == 201
        time.sleep(0.3)

    assert len(captured) >= 1
    overrides = captured[0].agent_overrides
    assert "wangye" in overrides, (
        "agent override not in mission Settings — snapshot_settings() was not called"
    )
    assert overrides["wangye"].default_provider_id == "fake"
