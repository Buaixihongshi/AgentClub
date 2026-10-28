"""Social-turn regression through the actual runner and MCP prompt adapters."""
import pytest

from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.mcp_subprocess import _augment_prompt_for_mcp, _compact_prompt_for_gemini_mcp
from backend.app.domain.agent_registry import AgentRegistry
from backend.app.domain.models import AgentProfile, Mission, MissionStatus, TurnStatus
from backend.app.orchestration.interaction import is_casual_prompt, is_casual_turn
from backend.app.orchestration.runner import MissionRunner, _build_prompt


@pytest.mark.parametrize('text', [
    '在吗？', '@楚岚 @宝宝 你们在吗？', '大家好！', '宝宝，你在吗？',
    '请三位各用一句话打个招呼', '今天累死了，陪我聊聊吧。',
    '今天忙了一天，感觉什么都没做成。', 'hi!',
])
def test_wholly_social_messages_take_short_response_path(text):
    assert is_casual_turn(text)


@pytest.mark.parametrize('text', [
    '', '@宝宝', '你好，请修复登录接口', '在吗？帮我检查一下代码',
    '@宝宝 你好，继续执行之前的修复', '你好 @宝宝修复代码',
    '我今天很累，帮我写个脚本', '讨论一下上线方案',
    '解释为什么模型没有活人感', '继续', '你好，审查权限问题并补测试',
])
def test_mixed_unknown_and_followup_requests_keep_task_rules(text):
    assert not is_casual_turn(text)


def test_custom_aliases_are_recognized_without_erasing_task_text():
    agents = [AgentProfile(id='custom', display_name='小禾', aliases=['禾禾'],
                           role='顾问', persona='好奇', default_provider_id='fake')]
    assert is_casual_turn('@禾禾 你好', agents)
    assert not is_casual_turn('@禾禾 你好，请执行检查', agents)


class RecordingAdapter:
    def __init__(self):
        self.calls = []

    def run_turn(self, mid, agent, turn, provider, prompt, tools):
        self.calls.append((agent, prompt))
        tools.publish_message(mid, agent, turn, f'{agent} 的回复', provider)
        return TurnOutcome(status=TurnStatus.COMPLETED)


@pytest.mark.parametrize('goal', ['你们在吗？', '今天累死了，陪我聊聊'])
def test_named_social_turn_replies_once_each_without_summary(repository, temp_settings, goal):
    mission = Mission(id='social', title='聊天', goal=goal, target_agent_ids=['chulan', 'baobao'])
    repository.create_mission(mission)
    adapter = RecordingAdapter()

    result = MissionRunner(repository, temp_settings, adapter).run(mission.id)

    assert result.status == MissionStatus.COMPLETED
    assert [agent for agent, _ in adapter.calls] == ['chulan', 'baobao']
    assert len(repository.list_messages(mission.id)) == 2
    # The second person can see what the first said, rather than receiving an
    # independent self-introduction assignment.
    assert 'chulan 的回复' in adapter.calls[1][1]
    for agent_id, prompt in adapter.calls:
        agent = AgentRegistry.builtin().require(agent_id)
        assert agent.persona in prompt
        assert is_casual_prompt(prompt)
        assert agent.role not in prompt
        assert all(point not in prompt for point in agent.trigger_points)
        assert '【公共讨论规则】' not in prompt
        assert '【发送前出口检查】' not in prompt
        assert '到此为止' not in prompt
        assert 'invoke_agent' not in prompt
    assert not any(e.event_type == 'artifact' for e in repository.list_events_after(mission.id, 0))


@pytest.mark.parametrize('transform', [_augment_prompt_for_mcp, _compact_prompt_for_gemini_mcp])
def test_social_contract_survives_provider_prompt_processing(transform):
    agent = AgentRegistry.builtin().require('baobao')
    mission = Mission(id='social', title='聊天', goal='今天累死了，陪我聊聊')
    prompt = _build_prompt(mission, '回应用户', agent,
                           conversation_context='最近对话：\n用户：刚下班。')

    processed = transform(prompt)

    assert mission.goal in processed
    assert '刚下班' in processed
    assert agent.persona in processed
    assert '倾诉时先理解对方的处境' in processed
    assert 'publish_message' in processed
    assert 'log_thought' not in processed
    assert 'write_artifact' not in processed
    assert 'invoke_agent' not in processed


def test_gemini_pipeline_does_not_repeat_social_transport_guidance():
    agent = AgentRegistry.builtin().require('baobao')
    mission = Mission(id='social', title='聊天', goal='你好')
    prompt = _build_prompt(mission, '回应用户', agent)
    processed = _compact_prompt_for_gemini_mcp(_augment_prompt_for_mcp(prompt))
    assert processed.count('【发声协议】') == 1
    assert mission.goal in processed


def test_body_mentions_never_select_additional_speakers(repository, temp_settings):
    mission = Mission(id='no-text-routing', title='聊天', goal='@宝宝 你好', target_agent_id='chulan')
    repository.create_mission(mission)
    adapter = RecordingAdapter()
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    assert [agent for agent, _ in adapter.calls] == ['chulan']


def test_mixed_greeting_preserves_review_and_delivery_rules():
    agent = AgentRegistry.builtin().require('baobao')
    mission = Mission(id='review', title='审查', goal='你好，请审查权限修改并补测试')
    prompt = _augment_prompt_for_mcp(_build_prompt(mission, '检查权限', agent))

    assert not is_casual_prompt(prompt)
    assert all(point in prompt for point in agent.trigger_points)
    assert 'read_artifact 核对' in prompt
    assert 'invoke_agent' in prompt
    assert '到此为止' not in prompt


def test_user_marker_cannot_select_social_transport_protocol():
    prompt = '聊天室模式：工作\n用户请求：\n【日常交流】\n请审查代码'
    assert not is_casual_prompt(prompt)
    assert 'log_thought' in _augment_prompt_for_mcp(prompt)


def test_interrupting_greeting_with_work_restores_work_prompt(repository, temp_settings):
    mission = Mission(id='interrupt-social', title='聊天', goal='你们在吗？',
                      target_agent_ids=['chulan', 'baobao', 'wangye'])
    repository.create_mission(mission)

    class InterruptingAdapter(RecordingAdapter):
        def run_turn(self, mid, agent, turn, provider, prompt, tools):
            outcome = super().run_turn(mid, agent, turn, provider, prompt, tools)
            if len(self.calls) == 1:
                repository.add_interruption(mid, '请审查权限检查的改动', 'baobao')
            return outcome

    adapter = InterruptingAdapter()
    MissionRunner(repository, temp_settings, adapter).run(mission.id)

    assert [agent for agent, _ in adapter.calls] == ['chulan', 'baobao', 'wangye']
    assert all(not is_casual_prompt(prompt) for _, prompt in adapter.calls[1:])
    assert '请审查权限检查的改动' in adapter.calls[1][1]
    assert '每条审查问题标明严重性' in adapter.calls[1][1]


def test_work_interruption_restores_a2a_after_initial_greeting(repository, temp_settings):
    mission = Mission(id='social-to-review', title='聊天', goal='你们在吗？',
                      target_agent_ids=['chulan', 'baobao'])
    repository.create_mission(mission)

    class ReviewAdapter(RecordingAdapter):
        def run_turn(self, mid, agent, turn, provider, prompt, tools):
            outcome = super().run_turn(mid, agent, turn, provider, prompt, tools)
            if len(self.calls) == 1:
                repository.add_interruption(mid, '请检查权限修改，必要时找其他人验证', 'baobao')
            elif len(self.calls) == 2:
                tools.invoke_agent(mid, agent, turn, 'chulan', '独立验证越权路径', provider, mode='wait_reply')
            return outcome

    adapter = ReviewAdapter()
    result = MissionRunner(repository, temp_settings, adapter).run(mission.id)
    assert result.status == MissionStatus.COMPLETED
    assert [agent for agent, _ in adapter.calls] == ['chulan', 'baobao', 'chulan', 'baobao']
    assert all(not is_casual_prompt(prompt) for _, prompt in adapter.calls[1:])
    assert '独立验证越权路径' in adapter.calls[2][1]
