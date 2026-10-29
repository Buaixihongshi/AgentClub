import pytest

from backend.app.domain.models import AgentProfile
from backend.app.orchestration.speaker import select_workflow


AGENTS = [
    AgentProfile(id="chulan", display_name="张楚岚", role="规划", persona="拆解", default_provider_id="claude"),
    AgentProfile(id="baobao", display_name="冯宝宝", role="执行", persona="产物", default_provider_id="codex"),
    AgentProfile(id="wangye", display_name="王也", role="分析", persona="风险", default_provider_id="gemini"),
]


def test_new_conversation_without_mention_starts_with_first_configured_agent():
    assert [step.agent_id for step in select_workflow(AGENTS)] == ["chulan"]


def test_unaddressed_message_continues_with_last_speaker():
    workflow = select_workflow(AGENTS, last_speaker_id="baobao")

    assert [step.agent_id for step in workflow] == ["baobao"]


def test_unknown_last_speaker_falls_back_to_first_configured_agent():
    workflow = select_workflow(AGENTS, last_speaker_id="removed-role")

    assert [step.agent_id for step in workflow] == ["chulan"]


def test_mention_wins_over_last_speaker():
    workflow = select_workflow(AGENTS, ["wangye"], last_speaker_id="baobao")

    assert [step.agent_id for step in workflow] == ["wangye"]


def test_multiple_mentions_answer_once_each_in_mention_order():
    workflow = select_workflow(AGENTS, ["baobao", "chulan", "baobao"])

    assert [step.agent_id for step in workflow] == ["baobao", "chulan"]
    assert not any(step.is_closing for step in workflow)


def test_invalid_target_agent_raises_clear_error():
    with pytest.raises(ValueError, match="Unavailable agent_id: luyi"):
        select_workflow(AGENTS, ["chulan", "luyi"])


def test_no_available_agents_reports_clear_error():
    with pytest.raises(ValueError, match="No agents available"):
        select_workflow([])


def test_workflow_steps_default_to_declared_workflow_origin():
    workflow = select_workflow(AGENTS, ["chulan", "baobao", "wangye"])

    assert all(step.origin == "workflow" for step in workflow)
    assert all(step.sender_agent_id is None for step in workflow)
    assert all(step.sender_turn_id is None for step in workflow)
