import json
from pathlib import Path

import pytest

from backend.app.domain.agent_registry import AgentRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_builtin_registry_has_three_canonical_agents():
    registry = AgentRegistry.builtin()
    assert registry.ids() == ["chulan", "baobao", "wangye"]
    assert registry.display_name("chulan") == "张楚岚"
    assert registry.display_name("baobao") == "冯宝宝"
    assert registry.display_name("wangye") == "王也"


def test_display_name_falls_back_to_id_for_unknown_agent():
    registry = AgentRegistry.builtin()
    assert registry.display_name("luyi") == "luyi"
    assert registry.display_name(None) == "agent"
    assert registry.display_name("") == "agent"


def test_resolve_alias_matches_display_name_and_pinyin():
    registry = AgentRegistry.builtin()
    assert registry.resolve_alias("王也").id == "wangye"
    assert registry.resolve_alias("wangye").id == "wangye"
    assert registry.resolve_alias("老王").id == "wangye"
    assert registry.resolve_alias("WANGYE").id == "wangye"
    assert registry.resolve_alias("unknown") is None
    assert registry.resolve_alias("") is None


def test_require_raises_for_unknown_id():
    registry = AgentRegistry.builtin()
    with pytest.raises(KeyError):
        registry.require("nope")


def test_from_config_reads_agents_json(tmp_path: Path):
    (tmp_path / "agents.json").write_text(
        json.dumps({
            "agents": [
                {
                    "id": "alpha",
                    "display_name": "甲",
                    "role": "r",
                    "persona": "p",
                    "default_provider_id": "fake",
                    "aliases": ["甲", "alpha"],
                    "trigger_points": ["tp1"],
                }
            ]
        }),
        encoding="utf-8",
    )
    registry = AgentRegistry.from_config(tmp_path)
    assert registry.ids() == ["alpha"]
    assert registry.resolve_alias("甲").id == "alpha"
    assert registry.require("alpha").trigger_points == ["tp1"]


def test_from_config_accepts_legacy_top_level_list(tmp_path: Path):
    (tmp_path / "agents.json").write_text(
        json.dumps([
            {
                "id": "beta",
                "display_name": "乙",
                "role": "r",
                "persona": "p",
                "default_provider_id": "fake",
            }
        ]),
        encoding="utf-8",
    )
    registry = AgentRegistry.from_config(tmp_path)
    assert registry.ids() == ["beta"]


def test_from_config_falls_back_to_builtin_when_file_missing(tmp_path: Path):
    registry = AgentRegistry.from_config(tmp_path)
    assert registry.ids() == ["chulan", "baobao", "wangye"]


def test_canonical_agents_have_trigger_points_and_aliases():
    registry = AgentRegistry.builtin()
    for agent_id in ("chulan", "baobao", "wangye"):
        agent = registry.require(agent_id)
        assert agent.trigger_points, agent_id
        assert agent.aliases, agent_id


def test_canonical_agents_have_distinct_work_styles():
    for registry in (
        AgentRegistry.builtin(),
        AgentRegistry.from_config(REPO_ROOT / "config"),
    ):
        chulan = registry.require("chulan")
        baobao = registry.require("baobao")
        wangye = registry.require("wangye")

        assert "关键取舍和落地成本" in chulan.persona
        assert "事实和遗漏" in baobao.persona
        assert "人用起来顺不顺" in wangye.persona



def test_shipped_profiles_match_builtin_fallback():
    configured = AgentRegistry.from_config(REPO_ROOT / "config")
    assert [agent.model_dump() for agent in configured.all()] == [
        agent.model_dump() for agent in AgentRegistry.builtin().all()
    ]


def test_agents_have_specialized_roles_and_capability_hints():
    for registry in (
        AgentRegistry.builtin(),
        AgentRegistry.from_config(REPO_ROOT / "config"),
    ):
        assert registry.require("chulan").role == "首席架构师和核心开发者"
        assert registry.require("chulan").capability_hints == ["planning", "code"]
        assert registry.require("baobao").role == "代码审查员和安全专家"
        assert registry.require("baobao").capability_hints == ["analysis", "risk"]
        assert registry.require("wangye").role == "视觉设计师和创意思考者"
        assert registry.require("wangye").capability_hints == ["conversation"]
