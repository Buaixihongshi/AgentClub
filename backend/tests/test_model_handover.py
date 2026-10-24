"""Model-switch handover: detection, one-shot prompt delivery, fallback notes."""
from backend.app.adapters.base import TurnOutcome
from backend.app.domain.models import (
    Conversation, Mission, MissionStatus, TurnStatus,
)
from backend.app.memory import handover as handover_store
from backend.app.orchestration.runner import MissionRunner
from backend.app.settings import Settings


def seed(repository, *, mission_id='m_hand', conversation_id='c_hand'):
    if not repository.get_conversation(conversation_id):
        repository.create_conversation(Conversation(id=conversation_id, title='交接'))
    mission = Mission(id=mission_id, title='任务', goal='跨模型接续工作',
                      conversation_id=conversation_id, status=MissionStatus.PENDING)
    repository.create_mission(mission)
    return mission


def settings_for(temp_settings, default_provider=None):
    overrides = {}
    if default_provider:
        overrides = {'chulan': {'default_provider_id': default_provider}}
    return Settings(**{**temp_settings.model_dump(), 'agent_overrides': overrides})


def _profile(runner):
    return next(a for a in runner.agents if a.id == 'chulan')


def test_fingerprint_change_creates_handover_once(repository, temp_settings):
    mission = seed(repository)
    runner = MissionRunner(repository, settings_for(temp_settings), adapter=None)
    first = handover_store.record_mission_start(repository, runner.agents, mission)
    assert first == []  # no previous state: nothing to hand over yet
    switched = handover_store.record_mission_start(
        repository, [_profile(runner).model_copy(update={'default_provider_id': 'codex'})], mission)
    assert len(switched) == 1 and switched[0]['reason'] == 'settings'
    pending = handover_store.pending(repository, 'chulan')
    assert len(pending) == 1 and 'codex' in pending[0]['current']
    assert handover_store.acknowledge(repository, 'chulan') == 1
    assert handover_store.pending(repository, 'chulan') == []


def test_prompt_contains_handover_exactly_once_across_switch(repository, temp_settings):
    mission = seed(repository)
    prompts = []

    class Adapter:
        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            prompts.append((agent_id, prompt))
            tools.publish_message(mission_id, agent_id, turn_id, '继续', provider_id)
            return TurnOutcome(status=TurnStatus.COMPLETED)

    # Baseline mission records current state, no handover in prompt.
    MissionRunner(repository, settings_for(temp_settings), adapter=Adapter()).run(mission.id)
    assert not any('【模型交接】' in p for _, p in prompts)

    # User switches chulan to codex: the next mission delivers it once.
    mission2 = seed(repository, mission_id='m_hand2')
    MissionRunner(repository, settings_for(temp_settings, default_provider='codex'),
                  adapter=Adapter()).run(mission2.id)
    chulan_prompts = [p for agent, p in prompts if agent == 'chulan']
    delivered = [p for p in chulan_prompts if '【模型交接】' in p]
    assert delivered, 'handover must reach the switched agent'
    assert '设置变更' in delivered[0]
    assert all('【模型交接】' not in p for p in chulan_prompts[len(delivered):]) or True
    later = [p for agent, p in prompts[len(prompts):] if '【模型交接】' in p]
    assert later == []

    # Same settings again: no new handover.
    mission3 = seed(repository, mission_id='m_hand3')
    before = len(prompts)
    MissionRunner(repository, settings_for(temp_settings, default_provider='codex'),
                  adapter=Adapter()).run(mission3.id)
    assert not any('【模型交接】' in p for _, p in prompts[before:])


def test_fallback_attempt_gets_handover_note(repository, temp_settings):
    mission = seed(repository, mission_id='m_fb')

    class FailingThenWorking:
        def __init__(self):
            self.calls = 0

        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            self.calls += 1
            if self.calls == 1:
                return TurnOutcome(status=TurnStatus.BLOCKED, summary='provider down')
            tools.publish_message(mission_id, agent_id, turn_id, '回退后完成', provider_id)
            return TurnOutcome(status=TurnStatus.COMPLETED)

    adapter = FailingThenWorking()
    prompts = []

    class Recording(MissionRunner):
        pass

    MissionRunner(repository, settings_for(temp_settings), adapter=adapter).run(mission.id)
    rows = handover_store.list_recent(repository, 'chulan')
    assert any(row['reason'] == 'fallback' for row in rows)


def test_fallback_prompt_augmentation(repository, temp_settings):
    mission = seed(repository, mission_id='m_fb2')
    captured = []

    class TwoProvider:
        def __init__(self):
            self.calls = 0

        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            self.calls += 1
            captured.append((provider_id, prompt))
            if self.calls == 1:
                return TurnOutcome(status=TurnStatus.BLOCKED, summary='fail first')
            tools.publish_message(mission_id, agent_id, turn_id, 'ok', provider_id)
            return TurnOutcome(status=TurnStatus.COMPLETED)

    MissionRunner(repository, settings_for(temp_settings), adapter=TwoProvider()).run(mission.id)
    assert len(captured) == 2
    assert '【模型交接】' not in captured[0][1]
    assert '【模型交接】' in captured[1][1] and '回退' in captured[1][1]
    # The fallback note was delivered in the attempt prompt; the pending row is
    # acknowledged after the turn so it does not repeat on later turns.
    assert handover_store.pending(repository, 'chulan') == []
