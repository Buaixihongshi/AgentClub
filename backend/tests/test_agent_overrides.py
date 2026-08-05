import json

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.orchestration.runner import _apply_agent_overrides
from backend.app.settings import AgentOverride
from backend.app.storage.hub_settings import default_path, save


def _make_client(temp_settings):
    return TestClient(create_app(temp_settings))


def test_apply_overrides_replaces_default_provider():
    from backend.app.domain.agent_registry import AgentRegistry
    base = AgentRegistry.builtin().all()
    overrides = {"wangye": AgentOverride(default_provider_id="claude")}
    result = _apply_agent_overrides(base, overrides)
    wangye = next(a for a in result if a.id == "wangye")
    assert wangye.default_provider_id == "claude"
    # fallback preserved
    assert wangye.fallback_provider_ids == ["codex"]


def test_apply_overrides_replaces_fallbacks_independently():
    from backend.app.domain.agent_registry import AgentRegistry
    base = AgentRegistry.builtin().all()
    overrides = {"wangye": AgentOverride(fallback_provider_ids=[])}
    result = _apply_agent_overrides(base, overrides)
    wangye = next(a for a in result if a.id == "wangye")
    assert wangye.default_provider_id == "antigravity"
    assert wangye.fallback_provider_ids == []


def test_apply_overrides_leaves_unmentioned_agents_alone():
    from backend.app.domain.agent_registry import AgentRegistry
    base = AgentRegistry.builtin().all()
    overrides = {"wangye": AgentOverride(default_provider_id="claude")}
    result = _apply_agent_overrides(base, overrides)
    baobao = next(a for a in result if a.id == "baobao")
    assert baobao.default_provider_id == "codex"


def test_hub_settings_view_includes_agents(temp_settings):
    client = _make_client(temp_settings)
    body = client.get("/api/hub/settings").json()
    assert "agents" in body
    wangye = next(a for a in body["agents"] if a["id"] == "wangye")
    assert wangye["default_provider_id"]["value"] == "antigravity"
    assert wangye["default_provider_id"]["source"] == "committed"


def test_put_agent_override_sets_default_and_persists(temp_settings):
    client = _make_client(temp_settings)
    resp = client.put("/api/hub/settings", json={
        "agents": {"wangye": {"default_provider_id": "claude"}},
    })
    assert resp.status_code == 200
    body = resp.json()
    wangye = next(a for a in body["agents"] if a["id"] == "wangye")
    assert wangye["default_provider_id"] == {"value": "claude", "source": "user"}
    assert wangye["base_default_provider_id"] == "antigravity"

    persisted = json.loads(default_path(temp_settings).read_text("utf-8"))
    assert persisted["agents"]["wangye"]["default_provider_id"] == "claude"


def test_put_agent_override_rejects_unknown_agent(temp_settings):
    client = _make_client(temp_settings)
    resp = client.put("/api/hub/settings", json={
        "agents": {"luyi": {"default_provider_id": "claude"}},
    })
    assert resp.status_code == 400
    assert "luyi" in resp.json()["detail"]


def test_put_agent_override_rejects_unknown_provider(temp_settings):
    client = _make_client(temp_settings)
    resp = client.put("/api/hub/settings", json={
        "agents": {"wangye": {"default_provider_id": "bogus"}},
    })
    assert resp.status_code == 400
    assert "bogus" in resp.json()["detail"]


def test_put_agent_override_clear_removes_entry(temp_settings):
    client = _make_client(temp_settings)
    client.put("/api/hub/settings", json={
        "agents": {"wangye": {"default_provider_id": "claude"}},
    })
    resp = client.put("/api/hub/settings", json={
        "agents": {"wangye": {"clear": True}},
    })
    assert resp.status_code == 200
    body = resp.json()
    wangye = next(a for a in body["agents"] if a["id"] == "wangye")
    assert wangye["default_provider_id"] == {"value": "antigravity", "source": "committed"}


def test_mission_runner_uses_overrides_from_disk(temp_settings):
    save(default_path(temp_settings), {
        "schema_version": 1,
        "agents": {"wangye": {"default_provider_id": "claude", "fallback_provider_ids": []}},
    })
    # Re-read via _snapshot_settings to mimic the mission start path
    from backend.app.api.missions import _snapshot_settings
    snap = _snapshot_settings(temp_settings)
    assert "wangye" in snap.agent_overrides
    assert snap.agent_overrides["wangye"].default_provider_id == "claude"
    assert snap.agent_overrides["wangye"].fallback_provider_ids == []

    from backend.app.adapters.fake import FakeCLIAdapter
    from backend.app.orchestration.runner import MissionRunner
    from backend.app.storage.repository import Repository
    repo = Repository(temp_settings.database_path)
    repo.initialize()
    runner = MissionRunner(repo, snap, FakeCLIAdapter())
    wangye = next(a for a in runner.agents if a.id == "wangye")
    assert wangye.default_provider_id == "claude"
    assert wangye.fallback_provider_ids == []
