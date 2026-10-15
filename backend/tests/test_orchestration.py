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


@pytest.mark.parametrize("goal", ["解释 Python 报错", "写一个脚本", "分析风险", "聊聊周末"])
def test_simple_workflow_uses_configured_order_regardless_of_job_keywords(goal):
    workflow = select_workflow(TaskComplexity.SIMPLE, goal, AGENTS)

    assert [step.agent_id for step in workflow] == ["chulan"]


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


@pytest.mark.parametrize("first_id", ["chulan", "baobao", "wangye"])
def test_any_personality_can_open_and_close(first_id):
    agents = sorted(AGENTS, key=lambda agent: agent.id != first_id)
    workflow = select_workflow(TaskComplexity.COMPLEX, "设计并实现系统", agents)
    assert workflow[0].agent_id == first_id
    assert workflow[-1].agent_id == first_id
    assert workflow[-1].is_closing
    assert {step.agent_id for step in workflow} == {agent.id for agent in agents}


def test_no_available_agents_reports_clear_error():
    with pytest.raises(ValueError, match="No agents available"):
        select_workflow(TaskComplexity.SIMPLE, "聊聊", [])


@pytest.mark.parametrize("goal", ["修复代码并补测试", "比较方案和风险"])
def test_medium_workflow_uses_configured_order(goal):
    workflow = select_workflow(TaskComplexity.MEDIUM, goal, AGENTS)
    assert [step.agent_id for step in workflow] == ["chulan", "baobao"]


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
    assert baobao_step.purpose == workflow[0].purpose == workflow[2].purpose
    assert "产物" not in baobao_step.purpose
    assert "文件" not in baobao_step.purpose


def test_workflow_steps_default_to_declared_workflow_origin():
    workflow = select_workflow(TaskComplexity.COMPLEX, "设计并实现系统", AGENTS)

    assert all(step.origin == "workflow" for step in workflow)
    assert all(step.sender_agent_id is None for step in workflow)
    assert all(step.sender_turn_id is None for step in workflow)
