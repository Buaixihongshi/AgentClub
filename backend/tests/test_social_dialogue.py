"""Who answers, and what each speaker is told, in an ordinary chat."""
from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.mcp_subprocess import _augment_prompt_for_mcp
from backend.app.domain.agent_registry import AgentRegistry
from backend.app.domain.models import Mission, MissionStatus, TurnStatus
from backend.app.orchestration.runner import MissionRunner, _build_prompt


class RecordingAdapter:
    def __init__(self):
        self.calls = []

    def run_turn(self, mid, agent, turn, provider, prompt, tools):
        self.calls.append((agent, prompt))
        tools.publish_message(mid, agent, turn, f'{agent} 的回复', provider)
        return TurnOutcome(status=TurnStatus.COMPLETED)


def _run(repository, temp_settings, mission):
    repository.create_mission(mission)
    adapter = RecordingAdapter()
    result = MissionRunner(repository, temp_settings, adapter).run(mission.id)
    return result, [agent for agent, _ in adapter.calls], adapter


def test_unaddressed_follow_up_stays_with_the_agent_user_was_talking_to(repository, temp_settings):
    _run(repository, temp_settings, Mission(id='m1', title='聊', goal='@冯宝宝 我感觉你有些没有活人感',
                                            conversation_id='c1', target_agent_ids=['baobao']))

    _, speakers, _ = _run(repository, temp_settings,
                          Mission(id='m2', title='聊', goal='是呀，你觉得是哪里出了问题？', conversation_id='c1'))

    assert speakers == ['baobao']


def test_unaddressed_follow_up_uses_the_most_recent_speaker_after_a_handoff(repository, temp_settings):
    _run(repository, temp_settings, Mission(id='m1', title='聊', goal='@王也 你说说', conversation_id='c1',
                                            target_agent_ids=['wangye']))
    _run(repository, temp_settings, Mission(id='m2', title='聊', goal='@宝宝 你呢', conversation_id='c1',
                                            target_agent_ids=['baobao']))

    _, speakers, _ = _run(repository, temp_settings, Mission(id='m3', title='聊', goal='继续', conversation_id='c1'))

    assert speakers == ['baobao']


def test_other_conversations_do_not_choose_the_speaker(repository, temp_settings):
    _run(repository, temp_settings, Mission(id='m1', title='聊', goal='你好', conversation_id='other',
                                            target_agent_ids=['baobao']))

    _, speakers, _ = _run(repository, temp_settings, Mission(id='m2', title='聊', goal='你好', conversation_id='new'))

    assert speakers == ['chulan']


def test_named_agents_reply_once_each_and_later_speakers_see_earlier_replies(repository, temp_settings):
    result, speakers, adapter = _run(repository, temp_settings, Mission(
        id='m1', title='聊', goal='你们在吗？', target_agent_ids=['chulan', 'baobao']))

    assert result.status == MissionStatus.COMPLETED
    assert speakers == ['chulan', 'baobao']
    assert 'chulan 的回复' in adapter.calls[1][1]


def test_body_mentions_never_select_additional_speakers(repository, temp_settings):
    _, speakers, _ = _run(repository, temp_settings, Mission(
        id='no-text-routing', title='聊天', goal='@宝宝 你好', target_agent_id='chulan'))

    assert speakers == ['chulan']


def test_one_short_prompt_carries_persona_and_register_without_review_ceremony():
    agent = AgentRegistry.builtin().require('baobao')
    mission = Mission(id='m', title='聊', goal='我感觉你有些没有活人感', conversation_id='c1')

    prompt = _augment_prompt_for_mcp(_build_prompt(mission, '接着和用户聊', agent, max_a2a_invocations=2))

    assert agent.persona in prompt
    assert all(point in prompt for point in agent.trigger_points)
    assert '闲聊、吐槽、问感受时就自然地聊' in prompt
    assert mission.goal in prompt
    for removed in ('【公共讨论规则】', '【发送前出口检查】', '务必先调 log_thought', 'read_artifact 核对'):
        assert removed not in prompt
    assert prompt.count('publish_message') <= 2
    # Everything besides memory/history should stay small enough that the
    # persona, not the rulebook, dominates what the model reads.
    assert len(prompt) < 1500


def test_mcp_guidance_is_appended_once():
    agent = AgentRegistry.builtin().require('baobao')
    prompt = _augment_prompt_for_mcp(_build_prompt(Mission(id='m', title='聊', goal='你好'), '回应', agent))

    assert prompt.count('【发声】') == 1


def test_message_sent_during_the_only_turn_is_answered_by_the_same_agent(repository, temp_settings):
    class InterruptingAdapter(RecordingAdapter):
        def run_turn(self, mid, agent, turn, provider, prompt, tools):
            outcome = super().run_turn(mid, agent, turn, provider, prompt, tools)
            if len(self.calls) == 1:
                repository.add_interruption(mid, '还有，顺便看看日志')
            return outcome

    mission = Mission(id='m1', title='聊', goal='@宝宝 在吗', target_agent_ids=['baobao'])
    repository.create_mission(mission)
    adapter = InterruptingAdapter()
    MissionRunner(repository, temp_settings, adapter).run(mission.id)

    assert [agent for agent, _ in adapter.calls] == ['baobao', 'baobao']
    assert '还有，顺便看看日志' in adapter.calls[1][1]
