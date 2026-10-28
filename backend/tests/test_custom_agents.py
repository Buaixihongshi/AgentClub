"""User-created roles: stored in hub settings, live without restart, usable
wherever a built-in role is (mentions, A2A targets, prompts, memory)."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.domain.agent_registry import get_agent_registry
from backend.app.domain.models import Mission
from backend.app.main import create_app
from backend.app.orchestration.runner import _build_prompt
from backend.app.storage import hub_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _role(**overrides):
    payload = {
        "display_name": "陆玲珑",
        "role": "测试工程师和质量把关者",
        "persona": "细致耐心，先复现再下结论。",
        "aliases": ["玲珑", "linglong"],
        "trigger_points": ["先写出复现步骤，再给修复建议。"],
        "default_provider_id": "codex",
        "fallback_provider_ids": ["claude"],
        "provider_models": {"codex": "gpt-5.5"},
    }
    payload.update(overrides)
    return payload


def _create(client, **overrides):
    response = client.post("/api/hub/agents", json=_role(**overrides))
    assert response.status_code == 200, response.text
    return next(a for a in response.json()["agents"] if a["display_name"] == overrides.get("display_name", "陆玲珑"))


def test_custom_role_is_saved_in_user_settings_and_listed_everywhere(temp_settings):
    client = TestClient(create_app(temp_settings))
    committed = (REPO_ROOT / "config" / "agents.json").read_text(encoding="utf-8")

    agent = _create(client, id="linglong")

    assert agent["id"] == "linglong"
    assert agent["custom"] is True
    assert agent["default_provider_id"]["value"] == "codex"
    assert agent["fallback_provider_ids"]["value"] == ["claude"]
    assert agent["provider_models"]["value"] == {"codex": "gpt-5.5"}
    stored = hub_settings.load(hub_settings.default_path(temp_settings))
    assert [entry["id"] for entry in stored["custom_agents"]] == ["linglong"]
    assert (REPO_ROOT / "config" / "agents.json").read_text(encoding="utf-8") == committed

    agents = client.get("/api/agents").json()["agents"]
    assert [a["id"] for a in agents] == ["chulan", "baobao", "wangye", "linglong"]
    assert agents[-1]["custom"] is True and agents[0]["custom"] is False
    # Both the app-scoped and the process-wide registry see it without a restart.
    for registry in (get_agent_registry(temp_settings.config_dir), get_agent_registry()):
        assert registry.resolve_alias("玲珑").id == "linglong"
        assert registry.is_custom("linglong") and not registry.is_custom("chulan")


def test_generated_id_and_edit_replace_route_overrides(temp_settings):
    client = TestClient(create_app(temp_settings))
    agent = _create(client)
    agent_id = agent["id"]
    assert agent_id.startswith("role-")

    # A table edit is a user override on top of the role's own route.
    patched = client.put("/api/hub/settings", json={"agents": {agent_id: {"default_provider_id": "claude", "fallback_provider_ids": []}}})
    assert patched.status_code == 200, patched.text

    response = client.put(f"/api/hub/agents/{agent_id}", json=_role(
        display_name="陆玲珑", role="QA", persona="更直接", aliases=["玲珑"],
        default_provider_id="antigravity", fallback_provider_ids=[], provider_models={},
    ))
    assert response.status_code == 200, response.text
    edited = next(a for a in response.json()["agents"] if a["id"] == agent_id)
    assert edited["role"] == "QA" and edited["persona"] == "更直接"
    assert edited["aliases"] == ["玲珑"]
    assert edited["default_provider_id"] == {"value": "antigravity", "source": "committed"}
    assert edited["fallback_provider_ids"]["value"] == []
    assert agent_id not in hub_settings.load(hub_settings.default_path(temp_settings)).get("agents", {})


def test_delete_custom_role_but_never_builtin(temp_settings):
    client = TestClient(create_app(temp_settings))
    agent_id = _create(client)["id"]
    client.put("/api/hub/settings", json={"agents": {agent_id: {"default_provider_id": "claude"}}})

    assert client.delete("/api/hub/agents/chulan").status_code == 403
    assert client.put("/api/hub/agents/chulan", json=_role(display_name="新楚岚", aliases=[])).status_code == 403
    assert client.delete("/api/hub/agents/nobody").status_code == 404

    response = client.delete(f"/api/hub/agents/{agent_id}")
    assert response.status_code == 200
    assert [a["id"] for a in response.json()["agents"]] == ["chulan", "baobao", "wangye"]
    stored = hub_settings.load(hub_settings.default_path(temp_settings))
    assert stored["custom_agents"] == [] and agent_id not in stored.get("agents", {})
    assert get_agent_registry().get(agent_id) is None


def test_custom_role_validation(temp_settings):
    client = TestClient(create_app(temp_settings))

    def detail(**overrides):
        response = client.post("/api/hub/agents", json=_role(**overrides))
        assert response.status_code == 400, response.text
        return response.json()["detail"]

    assert "冯宝宝" in detail(aliases=["宝宝"])
    assert "张楚岚" in detail(display_name="张楚岚", aliases=[])
    assert "ID" in detail(id="Bad ID")
    assert "已存在" in detail(id="chulan")
    assert "主用连接" in detail(default_provider_id="nowhere")
    assert "备用连接" in detail(fallback_provider_ids=["codex"])
    assert "不能为空" in detail(persona="  ")
    assert "别名" in detail(aliases=["带 空格"])
    connections = {"api-x": {"name": "X", "protocol": "openai_responses", "base_url": "https://example.com/v1",
                             "enabled": True, "timeout": 60, "allow_private": False}}
    assert client.put("/api/hub/settings", json={"connections": connections}).status_code == 200
    assert "模型" in detail(default_provider_id="api-x", provider_models={})
    assert client.get("/api/agents").json()["agents"][-1]["id"] == "wangye"


def test_custom_role_prompts_routes_and_mentions_without_restart(temp_settings):
    client = TestClient(create_app(temp_settings))
    _create(client, id="linglong")

    response = client.post("/api/missions", json={"goal": "帮我看看这个测试为什么不稳定", "target_agent_id": "linglong"})
    assert response.status_code == 201, response.text
    mission_id = response.json()["mission"]["id"]
    for _ in range(100):
        detail = client.get(f"/api/missions/{mission_id}").json()
        if detail["mission"]["status"] in {"completed", "failed", "blocked"}:
            break
        time.sleep(0.02)
    assert detail["mission"]["status"] == "completed"
    speakers = {m["agent_id"] for m in detail["messages"] if m.get("agent_id")}
    assert speakers == {"linglong"}

    registry = get_agent_registry(temp_settings.config_dir)
    agents = {a.id: a for a in registry.all()}
    prompt = _build_prompt(
        Mission(id="m_p", title="t", goal="请大家评审这个方案并补充测试"),
        "回应", agents["linglong"], agents_by_id=agents, max_a2a_invocations=4,
    )
    assert "你是陆玲珑，主要职责是测试工程师和质量把关者" in prompt
    assert "先写出复现步骤" in prompt
    assert "张楚岚=chulan" in prompt and "=linglong" not in prompt
    other = _build_prompt(Mission(id="m_q", title="t", goal="评审"), "回应", agents["chulan"],
                          agents_by_id=agents, max_a2a_invocations=4)
    assert "陆玲珑=linglong（测试工程师和质量把关者）" in other


def test_discuss_skill_defaults_to_three_roles_when_team_grows(temp_settings, repository):
    from backend.app.api.mission_requests import MissionRequest, create_requested_mission
    client = TestClient(create_app(temp_settings))
    _create(client, id="linglong")
    mission = create_requested_mission(repository, temp_settings, MissionRequest(goal="比较两种缓存方案", skill="discuss"))[0]
    assert mission.discussion.participant_ids == ["chulan", "baobao", "wangye"]


def test_mcp_subprocess_reads_custom_roles_beside_database(temp_settings, monkeypatch):
    settings_path = hub_settings.default_path(temp_settings)
    hub_settings.save(settings_path, {"custom_agents": [{
        "id": "linglong", "display_name": "陆玲珑", "role": "QA", "persona": "细致",
        "default_provider_id": "codex",
    }]})
    from backend.app.mcp import server
    from backend.app.storage.repository import Repository
    Repository(temp_settings.database_path).initialize()
    temp_settings.artifact_root.mkdir(parents=True, exist_ok=True)
    for key, value in {
        "YIREN_MCP_DB_PATH": str(temp_settings.database_path),
        "YIREN_MCP_ARTIFACT_ROOT": str(temp_settings.artifact_root),
        "YIREN_MCP_MISSION_ID": "m_x", "YIREN_MCP_AGENT_ID": "chulan",
        "YIREN_MCP_TURN_ID": "t_1", "YIREN_MCP_PROVIDER_ID": "codex",
        "YIREN_MCP_VALID_AGENTS": "chulan,linglong",
    }.items():
        monkeypatch.setenv(key, value)
    ctx = server._load_context()
    assert get_agent_registry().get("linglong").display_name == "陆玲珑"
    tools = server._handle_tools_list(ctx, {})["tools"]
    invoke = next(tool for tool in tools if tool["name"] == "invoke_agent")
    assert invoke["inputSchema"]["properties"]["target_agent_id"]["enum"] == ["chulan", "linglong"]


def test_malformed_custom_entries_are_skipped(tmp_path):
    from backend.app.domain.agent_registry import configure_custom_agents
    settings_path = tmp_path / "hub_settings.json"
    settings_path.write_text(json.dumps({"custom_agents": [
        {"id": "broken"},
        {"id": "chulan", "display_name": "冒名", "role": "r", "persona": "p", "default_provider_id": "codex"},
        {"id": "ok", "display_name": "好", "role": "r", "persona": "p", "default_provider_id": "codex"},
    ]}), encoding="utf-8")
    configure_custom_agents(REPO_ROOT / "config", settings_path)
    registry = get_agent_registry(REPO_ROOT / "config")
    assert registry.ids() == ["chulan", "baobao", "wangye", "ok"]
    assert registry.display_name("chulan") == "张楚岚"
