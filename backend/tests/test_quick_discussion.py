"""Quick discussions: independent opinions, a divergence card, then the user's choice."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.api.mission_requests import CreateMissionRequest, DiscussionControlRequest, create_requested_mission
from backend.app.domain.models import MissionStatus, TurnStatus
from backend.app.main import create_app
from backend.app.orchestration.deliberation import Deliberation
from backend.app.orchestration.roundtable import DiscussionError
from backend.app.orchestration.runner import MissionRunner


class SplitFake(FakeCLIAdapter):
    """Two voices disagree on one decision; exchange turns raise one issue."""

    def __init__(self):
        self.prompts = []
        self.views = []

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        self.prompts.append((agent_id, prompt))
        return super().run_turn(mission_id, agent_id, turn_id, provider_id, prompt, tools)

    def _discussion_turn(self, mission_id, agent_id, turn_id, provider_id, tools):
        view = tools.read_context(mission_id, agent_id=agent_id, turn_id=turn_id, provider_id=provider_id)
        self.views.append((agent_id, view))
        if view['phase'] == 'independent':
            for source_id in view.get('required_read_ids', []):
                tools.read_discussion_result(mission_id, agent_id, turn_id, provider_id, source_id, 0)
            stance = '默认开启' if agent_id == 'chulan' else '暂缓开启'
            tools.submit_discussion_result(mission_id, agent_id, turn_id, provider_id, {
                'kind': 'opinion', 'position_summary': f'{agent_id}：{stance}',
                'claims': [
                    {'key': 'main', 'decision_key': '是否默认开启', 'statement': stance, 'recommendation': stance},
                    {'key': 'confirm', 'decision_key': '写入前确认', 'statement': '需要确认', 'recommendation': '需要确认'},
                ]})
            return TurnOutcome(status=TurnStatus.COMPLETED)
        if view['phase'] == 'exchange':
            for source_id in view.get('required_read_ids', []):
                offset = 0
                while offset is not None:
                    offset = tools.read_discussion_result(mission_id, agent_id, turn_id, provider_id,
                                                          source_id, offset)['next_offset']
            claims = [c['id'] for r in view['results'] if r['kind'] == 'opinion' for c in r['data']['claims']
                      if c['decision_key'] == '是否默认开启']
            tools.submit_discussion_result(mission_id, agent_id, turn_id, provider_id, {
                'kind': 'contribution', 'issues': [{'claim_ids': claims, 'category': 'fact',
                    'problem': '回滚是否可用', 'impact': '决定能否默认开启', 'proposal': '先核对回滚'}]})
            return TurnOutcome(status=TurnStatus.COMPLETED)
        if view['phase'] == 'synthesis':
            from backend.app.domain.deliberation import SECTION_IDS
            refs = [c['id'] for r in view['results'] if r['kind'] == 'opinion' for c in r['data']['claims']]
            refs += list(view['issues'])
            tools.submit_discussion_result(mission_id, agent_id, turn_id, provider_id, {
                'kind': 'draft', 'sections': [{'id': sid, 'content': '综合', 'source_ids': refs} for sid in SECTION_IDS],
                'issue_decisions': [{'issue_id': key, 'status': 'unresolved', 'decision': '保留分歧'}
                                    for key in view['issues']]})
            return TurnOutcome(status=TurnStatus.COMPLETED)
        return super()._discussion_turn(mission_id, agent_id, turn_id, provider_id, tools)


def discuss(repo, settings, goal='/discuss 记忆要不要默认开启？', **discussion):
    payload = {'goal': goal, 'discussion': {'participant_ids': ['chulan', 'baobao'], **discussion}}
    return create_requested_mission(repo, settings, CreateMissionRequest(**payload))[0]


def control(repo, mission, action, **fields):
    policy = Deliberation(repo)
    state = policy.state(repo.get_mission(mission.id))
    return policy.control(mission.id, DiscussionControlRequest(action=action, request_id=uuid4().hex,
                                                              expected_revision=state['revision'], **fields))


def run(repo, settings, mission, adapter):
    MissionRunner(repo, settings, adapter).run(mission.id)
    current = repo.get_mission(mission.id)
    return current, Deliberation(repo).state(current)


def test_discuss_defaults_to_quick_and_explicit_v2_keeps_full(repository, temp_settings):
    assert discuss(repository, temp_settings).discussion.depth == 'quick'
    explicit = create_requested_mission(repository, temp_settings, CreateMissionRequest(
        goal='核验', room_mode='roundtable',
        discussion={'protocol': 'deliberation_v2', 'participant_ids': ['chulan', 'baobao']}))[0]
    assert explicit.discussion.depth == 'full'
    with pytest.raises(ValueError):
        CreateMissionRequest(goal='x', room_mode='roundtable',
                             discussion={'participant_ids': ['chulan', 'baobao'], 'depth': 'quick'})


def test_quick_stops_after_opinions_and_shows_them_with_a_divergence_card(repository, temp_settings):
    mission = discuss(repository, temp_settings)
    adapter = SplitFake()
    current, state = run(repository, temp_settings, mission, adapter)
    assert len(adapter.prompts) == 2
    assert current.status == MissionStatus.AWAITING_INPUT and state['awaiting_reason'] == 'choose_next'
    view = Deliberation(repository).user_view(current)
    assert view['depth'] == 'quick'
    assert view['choices'] == ['verify_conflicts', 'synthesize', 'user_decide', 'escalate', 'finish']
    [conflict] = view['divergence']['conflicts']
    assert {p['agent_id']: p['recommendation'] for p in conflict['positions']} == {
        'chulan': '默认开启', 'baobao': '暂缓开启'}
    assert [item['decision_key'] for item in view['divergence']['agreed']] == ['写入前确认']

    client = TestClient(create_app(temp_settings))
    detail = client.get(f'/api/conversations/{mission.conversation_id}').json()
    opinions = [m for m in detail['messages'] if m['role'] == 'agent']
    assert [m['metadata']['discussion_visibility'] for m in opinions] == ['opinion', 'opinion']
    assert {m['metadata']['discussion_phase'] for m in opinions} == {'independent'}


def test_synthesize_defaults_the_synthesizer_and_ends_unreviewed(repository, temp_settings):
    mission = discuss(repository, temp_settings)
    adapter = SplitFake()
    run(repository, temp_settings, mission, adapter)
    _, scheduled = control(repository, mission, 'synthesize')
    assert scheduled
    current, state = run(repository, temp_settings, mission, adapter)
    assert len(adapter.prompts) == 3 and adapter.prompts[-1][0] == 'chulan'
    assert state['phase'] == 'ended' and state['end_reason'] == 'synthesized_unreviewed'
    assert state['final_id'] == state['draft_id'] and state['reviews'] == []
    assert not any(lease['phase'] == 'review' for lease in state['leases'].values())
    client = TestClient(create_app(temp_settings))
    messages = client.get(f'/api/conversations/{mission.conversation_id}').json()['messages']
    assert [m['metadata'].get('discussion_visibility') for m in messages if m['role'] == 'agent'] == [
        'opinion', 'opinion', 'final']


def test_verify_runs_one_round_with_conflicting_voices_then_returns_to_choice(repository, temp_settings):
    mission = discuss(repository, temp_settings, max_exchange_rounds=2)
    adapter = SplitFake()
    run(repository, temp_settings, mission, adapter)
    control(repository, mission, 'verify_conflicts')
    current, state = run(repository, temp_settings, mission, adapter)
    assert len(adapter.prompts) == 4
    assert state['awaiting_reason'] == 'choose_next' and state['round_index'] == 1
    [issue] = state['issues'].values()
    assert issue['problem'] == '回滚是否可用' and issue['status'] == 'unresolved'
    control(repository, mission, 'verify_conflicts')
    current, state = run(repository, temp_settings, mission, adapter)
    assert state['round_index'] == 2
    assert 'verify_conflicts' not in Deliberation(repository).user_view(current)['choices']
    with pytest.raises(DiscussionError, match='No exchange rounds left'):
        control(repository, mission, 'verify_conflicts')


def test_user_decide_and_finish_end_without_model_calls(repository, temp_settings):
    decided = discuss(repository, temp_settings)
    adapter = SplitFake()
    run(repository, temp_settings, decided, adapter)
    with pytest.raises(DiscussionError, match='decision'):
        control(repository, decided, 'user_decide', text='  ')
    control(repository, decided, 'user_decide', text='先不默认开启')
    state = Deliberation(repository).state(repository.get_mission(decided.id))
    assert state['end_reason'] == 'user_decided'
    assert repository.list_messages(decided.id)[-1].content == '我的决定：先不默认开启'

    finished = discuss(repository, temp_settings)
    run(repository, temp_settings, finished, adapter)
    control(repository, finished, 'finish')
    current = repository.get_mission(finished.id)
    assert current.status == MissionStatus.COMPLETED
    assert Deliberation(repository).state(current)['end_reason'] == 'opinions_only'
    assert len(adapter.prompts) == 4


def test_supplements_join_the_next_step_instead_of_restarting(repository, temp_settings):
    mission = discuss(repository, temp_settings)
    adapter = SplitFake()
    run(repository, temp_settings, mission, adapter)
    policy = Deliberation(repository)
    policy.interrupt(mission.id, '补充：回滚下周才上线', None, 'supplement-1')
    current = repository.get_mission(mission.id)
    state = policy.state(current)
    assert current.status == MissionStatus.AWAITING_INPUT and state['awaiting_reason'] == 'choose_next'
    control(repository, mission, 'synthesize', agent_id='baobao')
    run(repository, temp_settings, mission, adapter)
    agent, view = adapter.views[-1]
    assert agent == 'baobao' and view['phase'] == 'synthesis'
    [supplement_id] = view['supplement_ids']
    assert any(m['id'] == supplement_id and '回滚下周才上线' in m['content'] for m in view['snapshot']['messages'])
    assert 'supplement_ids' in adapter.prompts[-1][1]
    assert policy.state(repository.get_mission(mission.id))['pending_inputs'] == []


def test_escalate_continues_with_the_full_review(repository, temp_settings):
    mission = discuss(repository, temp_settings)
    adapter = SplitFake()
    run(repository, temp_settings, mission, adapter)
    control(repository, mission, 'escalate')
    current, state = run(repository, temp_settings, mission, adapter)
    assert state['depth'] == 'full' and state['round_index'] >= 1
    assert state['awaiting_reason'] == 'select_synthesizer'
    control(repository, mission, 'select_synthesizer', agent_id='baobao')
    current, state = run(repository, temp_settings, mission, adapter)
    assert state['phase'] == 'ended' and state['reviews']


def test_choices_require_the_choose_step_and_current_revision(repository, temp_settings):
    mission = discuss(repository, temp_settings)
    with pytest.raises(DiscussionError, match='not waiting'):
        control(repository, mission, 'synthesize')
    run(repository, temp_settings, mission, SplitFake())
    policy = Deliberation(repository)
    with pytest.raises(DiscussionError, match='revision'):
        policy.control(mission.id, DiscussionControlRequest(action='synthesize', request_id='stale',
                                                            expected_revision=0))
    with pytest.raises(DiscussionError, match='participant'):
        control(repository, mission, 'synthesize', agent_id='wangye')
