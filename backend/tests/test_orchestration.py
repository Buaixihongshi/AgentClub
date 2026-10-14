import pytest

from backend.app.domain.models import AgentProfile, TaskComplexity
from backend.app.orchestration.complexity import classify_task
from backend.app.orchestration.speaker import select_workflow


AGENTS = [
    AgentProfile(id="chulan", display_name="张楚岚", role="规划", persona="拆解", default_provider_id="claude"),
    AgentProfile(id="baobao", display_name="冯宝宝", role="执行", persona="产物", default_provider_id="codex"),
    AgentProfile(id="wangye", display_name="王也", role="分析", persona="风险", default_provider_id="gemini"),
]


def test_classifies_simple_task():
    assert classify_task("解释这个报错") == TaskComplexity.SIMPLE


def test_classifies_complex_task():
    assert classify_task("设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP") == TaskComplexity.COMPLEX


def test_explicit_multi_agent_demo_is_complex():
    assert classify_task("真实 CLI 演示：请三位 Agent 各用一句话说明当前联调状态") == TaskComplexity.COMPLEX


def test_simple_workflow_uses_single_best_agent():
    workflow = select_workflow(TaskComplexity.SIMPLE, "解释 Python 报错", AGENTS)

    assert [step.agent_id for step in workflow] == ["wangye"]


def test_target_agent_uses_only_requested_agent():
    workflow = select_workflow(
        TaskComplexity.COMPLEX,
        "设计并实现系统",
        AGENTS,
        target_agent_id="baobao",
    )

    assert [step.agent_id for step in workflow] == ["baobao"]


def test_invalid_target_agent_raises_clear_error():
    with pytest.raises(ValueError, match="Unavailable agent_id: luyi"):
        select_workflow(TaskComplexity.SIMPLE, "解释 Python 报错", AGENTS, target_agent_id="luyi")


def test_multi_target_builds_discussion_workflow_alternating_with_closer():
    """`@chulan @baobao 讨论...` → 5 alternation steps + last-mentioned closes."""
    workflow = select_workflow(
        TaskComplexity.SIMPLE,
        "你俩讨论下接下来该做 M4 还是 Gemini",
        AGENTS,
        target_agent_ids=["chulan", "baobao"],
    )

    assert [step.agent_id for step in workflow] == [
        "chulan", "baobao", "chulan", "baobao", "chulan",  # alternation
        "baobao",  # closing by last-mentioned
    ]
    assert workflow[-1].purpose.startswith("收束讨论")


def test_multi_target_three_agents_cycles_then_closes():
    workflow = select_workflow(
        TaskComplexity.SIMPLE,
        "三人讨论",
        AGENTS,
        target_agent_ids=["chulan", "baobao", "wangye"],
    )

    assert [step.agent_id for step in workflow] == [
        "chulan", "baobao", "wangye", "chulan", "baobao",  # alternation
        "wangye",  # closing by last-mentioned
    ]


def test_target_agent_ids_singleton_falls_back_to_single_step():
    workflow = select_workflow(
        TaskComplexity.COMPLEX,
        "设计并实现系统",
        AGENTS,
        target_agent_ids=["baobao"],
    )

    assert [step.agent_id for step in workflow] == ["baobao"]


def test_target_agent_ids_invalid_member_raises():
    with pytest.raises(ValueError, match="Unavailable agent_id: luyi"):
        select_workflow(
            TaskComplexity.SIMPLE,
            "讨论",
            AGENTS,
            target_agent_ids=["chulan", "luyi"],
        )


def test_simple_workflow_missing_required_agent_raises_clear_error():
    agents = [agent for agent in AGENTS if agent.id != "wangye"]

    with pytest.raises(ValueError, match="Unavailable agent_id: wangye"):
        select_workflow(TaskComplexity.SIMPLE, "解释 Python 报错", agents)


def test_medium_workflow_uses_primary_and_reviewer():
    workflow = select_workflow(TaskComplexity.MEDIUM, "修复代码并补测试", AGENTS)

    assert [step.agent_id for step in workflow] == ["baobao", "wangye"]


def test_complex_workflow_uses_three_agents():
    workflow = select_workflow(TaskComplexity.COMPLEX, "设计并实现系统", AGENTS)

    assert [step.agent_id for step in workflow] == ["chulan", "baobao", "wangye", "chulan"]


def test_complex_workflow_does_not_force_baobao_to_create_an_artifact():
    workflow = select_workflow(
        TaskComplexity.COMPLEX,
        "三位 Agent 讨论最近的发展趋势并给我讲讲",
        AGENTS,
    )

    baobao_step = next(step for step in workflow if step.agent_id == "baobao")
    assert baobao_step.purpose == "补充具体做法、实例和可行性；按用户需求执行"
    assert "产物" not in baobao_step.purpose
    assert "文件" not in baobao_step.purpose


def test_workflow_steps_default_to_declared_workflow_origin():
    workflow = select_workflow(TaskComplexity.COMPLEX, "设计并实现系统", AGENTS)

    assert all(step.origin == "workflow" for step in workflow)
    assert all(step.sender_agent_id is None for step in workflow)
    assert all(step.sender_turn_id is None for step in workflow)
