"""Protocol tests use real SQLite, HTTP, MCP and the production batch runner."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.api.mission_requests import CreateMissionRequest, DiscussionControlRequest, create_requested_mission
from backend.app.domain.deliberation import SECTION_IDS
from backend.app.domain.models import MissionStatus, TurnStatus
from backend.app.main import create_app
from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.orchestration.deliberation import Deliberation, occupies_conversation
from backend.app.orchestration.roundtable import DiscussionError, Roundtable
from backend.app.orchestration.runner import MissionRunner


PARTICIPANTS = ['chulan', 'baobao', 'wangye']


def test_public_failures_keep_safe_categories_and_exit_codes_without_sealed_text(repository, temp_settings):
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    lease = policy.claim_batch(mission.id, temp_settings.artifact_root)[0]
    policy.authorize(mission.id, lease['turn_id'], 'claude')
    policy.failed_attempt(mission.id, lease['turn_id'],
                          'EPERM: private path /secret/SEALED_OPINION_CANARY', exit_code=1)
    view = policy.user_view(repository.get_mission(mission.id))
    item = next(p for p in view['progress'] if p['turn_id'] == lease['turn_id'])
    assert item['failure_code'] == 'filesystem_permission'
    assert item['exit_code'] == 1
    assert item['provider_id'] == 'claude'
    assert '临时目录' in item['error']
    assert 'SEALED_OPINION_CANARY' not in json.dumps(view)
    assert '/secret' not in json.dumps(view)


def create(repo, settings, n=3, rounds=2):
    payload = CreateMissionRequest(goal='对同一任务独立提出方案，再核验分歧', room_mode='roundtable',
        discussion={'protocol': 'deliberation_v2', 'participant_ids': PARTICIPANTS[:n], 'max_exchange_rounds': rounds})
    return create_requested_mission(repo, settings, payload)[0]


def control(repo, settings, mid, action, **fields):
    policy = Deliberation(repo)
    state = policy.state(repo.get_mission(mid))
    payload = DiscussionControlRequest(action=action, request_id=uuid4().hex,
                                      expected_revision=state['revision'], **fields)
    return policy.control(mid, payload, settings)


def tools_for(repo, settings, mid, lease, provider='fake'):
    auth = Deliberation(repo).authorize(mid, lease['turn_id'], provider)
    tools = MCPTools(repo, settings.artifact_root, lease_token=auth['token'])
    return tools, auth


def opinion(summary='独立判断', recommendation='验证方案'):
    return {'kind': 'opinion', 'position_summary': summary,
            'claims': [{'key': 'a', 'decision_key': 'main', 'statement': summary, 'recommendation': recommendation}]}


def submit(repo, settings, mid, lease, body):
    tools, auth = tools_for(repo, settings, mid, lease)
    tools.read_context(mid, agent_id=lease['agent_id'], turn_id=lease['turn_id'], provider_id='fake')
    result = tools.submit_discussion_result(mid, lease['agent_id'], lease['turn_id'], 'fake', body)
    return result, tools, auth


class ScriptedAdapter:
    def __init__(self, change=None):
        self.calls = []
        self.change = change

    def run_turn(self, mid, agent, turn, provider, prompt, tools):
        view = tools.read_context(mid, agent_id=agent, turn_id=turn, provider_id=provider)
        view = hydrate(tools, mid, agent, turn, provider, view)
        self.calls.append((agent, view['phase'], copy.deepcopy(view)))
        body = result_for(view, agent)
        if self.change:
            body = self.change(agent, view, body)
        tools.submit_discussion_result(mid, agent, turn, provider, body)
        return TurnOutcome(TurnStatus.COMPLETED)


def hydrate(tools, mid, agent, turn, provider, view):
    for source_id in view.get('required_read_ids', []):
        text, offset = '', 0
        while True:
            page = tools.read_discussion_result(mid, agent, turn, provider, source_id, offset)
            text += page['text']
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        full = json.loads(text)
        if source_id in view['issues']:
            view['issues'][source_id] = full
        view['results'] = [full if r['id'] == source_id else r for r in view['results']]
    return view


def result_for(view, agent):
    phase = view['phase']
    if phase == 'independent':
        return opinion(recommendation=agent)
    if phase == 'exchange':
        return {'kind': 'contribution', 'no_addition': True}
    if phase == 'review':
        return {'kind': 'review', 'draft_id': view['draft_id'], 'no_findings': True}
    refs = [c['id'] for r in view['results'] if r['kind'] == 'opinion' for c in r['data']['claims']]
    refs += list(view['issues'])
    if phase == 'finalizing':
        original = next(r['data'] for r in view['results'] if r['id'] == view['draft_id'])
        body = copy.deepcopy(original)
        findings = [f for r in view['results'] if r['kind'] == 'review' for f in r['data']['findings']]
        body['dispositions'] = [{'finding_id': f['id'], 'action': 'accepted', 'reason': '按原文修正遗漏'} for f in findings]
        for section in body['sections']:
            if any(f['section_id'] == section['id'] for f in findings):
                section['content'] += '\n已修正遗漏；尚未核验的事实保持未知。'
        return body
    issue_decisions = [{'issue_id': key,
                        'status': 'resolved' if issue['status'] == 'resolved' else 'unresolved',
                        'decision': '按现有依据处理'}
                       for key, issue in view['issues'].items() if issue['category'] != 'needs_human']
    return {'kind': 'draft', 'issue_decisions': issue_decisions,
            'sections': [{'id': sid, 'content': f'{sid}：保留各自意见及未知', 'source_ids': refs} for sid in SECTION_IDS]}


@pytest.mark.parametrize('n,rounds,budget', [(3, 2, 13), (3, 3, 16), (2, 2, 9)])
def test_budget_and_full_no_disagreement_flow(repository, temp_settings, n, rounds, budget):
    mission = create(repository, temp_settings, n, rounds)
    assert mission.discussion.max_turns == budget
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mission.id)
    policy = Deliberation(repository)
    state = policy.state(repository.get_mission(mission.id))
    assert state['phase'] == 'awaiting_synthesizer'
    assert repository.get_mission(mission.id).status == MissionStatus.AWAITING_INPUT
    assert state['turns_used'] == n
    before = len(repository.list_events_after(mission.id, 0))
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mission.id)
    assert len(repository.list_events_after(mission.id, 0)) == before
    control(repository, temp_settings, mission.id, 'select_synthesizer', agent_id='baobao')
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mission.id)
    view = policy.user_view(repository.get_mission(mission.id))
    assert view['phase'] == 'ended'
    assert view['turns_used'] == 2 * n
    assert view['final_id'] == view['draft_id']
    assert not view['knowledge_candidate_id']
    reviewers = {r['agent_id'] for r in view['results'] if r['kind'] == 'review'}
    assert reviewers == set(PARTICIPANTS[:n]) - {'baobao'}
    control(repository, temp_settings, mission.id, 'save_knowledge_candidate')
    saved = policy.user_view(repository.get_mission(mission.id))['knowledge_candidate_id']
    assert policy.get(mission.id, saved)['status'] == 'candidate_not_approved'


def test_first_completed_opinion_stays_sealed_across_every_read_channel(repository, temp_settings):
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    batch = policy.claim_batch(mission.id, temp_settings.artifact_root)
    marker = uuid4().hex
    result, _, _ = submit(repository, temp_settings, mission.id, batch[0], opinion(marker))
    tools, auth = tools_for(repository, temp_settings, mission.id, batch[1])
    view = tools.read_context(mission.id, agent_id=batch[1]['agent_id'], turn_id=batch[1]['turn_id'], provider_id='fake')
    assert marker not in json.dumps(view)
    assert view['results'] == []
    assert policy.user_view(repository.get_mission(mission.id))['results'] == []
    assert not [m for m in repository.list_messages(mission.id) if m.role == 'agent']
    with pytest.raises(MCPToolError):
        tools.read_artifact(mission.id, batch[1]['agent_id'], batch[1]['turn_id'], result['id'], provider_id='fake')
    with pytest.raises(MCPToolError, match='visible phase snapshot'):
        tools.read_discussion_result(mission.id, batch[1]['agent_id'], batch[1]['turn_id'], 'fake', result['id'])
    for name, args in [
        ('publish_message', (mission.id, batch[1]['agent_id'], batch[1]['turn_id'], 'cannot publish', 'fake')),
        ('invoke_agent', (mission.id, batch[1]['agent_id'], batch[1]['turn_id'], 'chulan', 'tell me', 'fake')),
    ]:
        with pytest.raises(MCPToolError):
            getattr(tools, name)(*args)
    other = policy.get(mission.id, result['id'])['data']['claims'][0]['id']
    malicious = opinion();malicious['claims'][0]['source_ids'] = [other]
    with pytest.raises(MCPToolError, match='visible snapshot'):
        tools.submit_discussion_result(mission.id, batch[1]['agent_id'], auth['turn_id'], 'fake', malicious)


def test_snapshot_materials_are_indexed_and_frozen(repository, temp_settings, tmp_path, monkeypatch):
    source = tmp_path / 'project/backend/app'
    source.mkdir(parents=True)
    (source / 'sample.py').write_text('ORIGINAL')
    monkeypatch.chdir(tmp_path / 'project')
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    batch = policy.claim_batch(mission.id, temp_settings.artifact_root)
    tools, lease = tools_for(repository, temp_settings, mission.id, batch[0])
    view = tools.read_context(mission.id, agent_id=lease['agent_id'], turn_id=lease['turn_id'], provider_id='fake')
    assert 'ORIGINAL' not in json.dumps(view)
    (source / 'sample.py').write_text('CHANGED')
    material = tools.read_artifact(mission.id, lease['agent_id'], lease['turn_id'], 'backend/app/sample.py', provider_id='fake')
    assert material['content'] == 'ORIGINAL'


def test_atomic_batch_reservations_and_idempotent_submission(repository, temp_settings):
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(lambda _: policy.claim_batch(mission.id, temp_settings.artifact_root), range(2)))
    assert sorted(map(len, batches)) == [0, 3]
    lease = next(b for b in batches if b)[0]
    result, tools, auth = submit(repository, temp_settings, mission.id, lease, opinion())
    again = tools.submit_discussion_result(mission.id, lease['agent_id'], lease['turn_id'], 'fake', opinion())
    assert result == again
    with pytest.raises(MCPToolError, match='different input'):
        tools.submit_discussion_result(mission.id, lease['agent_id'], lease['turn_id'], 'fake', opinion('changed'))
    tools.lease_token = uuid4().hex
    with pytest.raises(MCPToolError):
        tools.submit_discussion_result(mission.id, lease['agent_id'], lease['turn_id'], 'fake', opinion())


def test_exchange_early_exit_uses_previous_batch_not_live_outputs(repository, temp_settings):
    mission = create(repository, temp_settings)
    adapter = ScriptedAdapter()
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    exchanges = [v for _, phase, v in adapter.calls if phase == 'exchange']
    assert len(exchanges) == 3
    assert all(len(v['results']) == 3 for v in exchanges)
    assert all({r['kind'] for r in v['results']} == {'opinion'} for v in exchanges)
    state = Deliberation(repository).state(repository.get_mission(mission.id))
    assert state['exchange_end_reason'] == 'no_new_information'
    assert state['round_index'] == 1


def test_wording_and_source_differences_do_not_force_an_exchange(repository, temp_settings):
    mission = create(repository, temp_settings)
    def agree(agent, view, body):
        if view['phase'] == 'independent':
            return {'kind': 'opinion', 'position_summary': f'{agent} 的不同措辞',
                    'claims': [{'key': 'main', 'decision_key': 'release',
                                'statement': f'{agent} 对同一方案的不同表述',
                                'recommendation': ' 先灰度发布 ', 'conditions': ['保留回滚路径'],
                                'source_ids': [], 'evidence_status': 'unverified'}]}
        return body
    adapter = ScriptedAdapter(agree)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    state = Deliberation(repository).state(repository.get_mission(mission.id))
    assert state['agreement']['skip'] is True
    assert state['agreement']['reason'] == 'decisions_agree'
    assert state['exchange_agents'] == []
    assert not [call for call in adapter.calls if call[1] == 'exchange']
    assert state['phase'] == 'awaiting_synthesizer'
    assert state['turns_used'] == 3


def test_exchange_only_invites_participants_with_conflicting_decisions(repository, temp_settings):
    mission = create(repository, temp_settings)
    def split(agent, view, body):
        if view['phase'] == 'independent':
            if agent == 'wangye':
                key, recommendation = 'monitoring', '保留监控'
            else:
                key = 'release'
                recommendation = '直接全量' if agent == 'baobao' else '先灰度发布'
            return {'kind': 'opinion', 'position_summary': f'{agent} 的判断',
                    'claims': [{'key': 'main', 'decision_key': key,
                                'statement': '发布或监控策略', 'recommendation': recommendation,
                                'conditions': [], 'source_ids': [], 'evidence_status': 'unverified'}]}
        return body
    adapter = ScriptedAdapter(split)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    state = Deliberation(repository).state(repository.get_mission(mission.id))
    assert state['agreement']['reason'] == 'conflicting_decisions'
    assert state['exchange_agents'] == ['chulan', 'baobao']
    assert {agent for agent, phase, _ in adapter.calls if phase == 'exchange'} == {'chulan', 'baobao'}
    assert state['turns_used'] == 5


def test_synthesis_must_record_each_technical_issue_decision(repository, temp_settings):
    mission = create(repository, temp_settings)
    def conflict(agent, view, body):
        if view['phase'] == 'exchange':
            claims = [c['id'] for r in view['results'] if r['kind'] == 'opinion' for c in r['data']['claims']]
            return {'kind': 'contribution', 'issues': [{'claim_ids': claims, 'category': 'approach',
                    'problem': '发布策略不兼容', 'impact': '影响上线风险', 'proposal': '采用灰度方案'}]}
        if view['phase'] == 'synthesis':
            body['issue_decisions'] = [{'issue_id': key, 'status': 'resolved', 'decision': '采用灰度方案并保留回滚'}
                                       for key in view['issues']]
        return body
    adapter = ScriptedAdapter(conflict)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    control(repository, temp_settings, mission.id, 'select_synthesizer', agent_id='chulan')
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    view = Deliberation(repository).user_view(repository.get_mission(mission.id))
    issue = view['issues'][0]
    assert issue['technical_decision'] == '采用灰度方案并保留回滚'
    assert issue['decision_status'] == 'resolved'
    assert issue['status'] == 'resolved'
    from backend.app.api.mission_detail import build_mission_detail
    detail = build_mission_detail(repository, temp_settings, mission.id)
    final_message = next(message for message in detail['messages']
                         if message['metadata'].get('discussion_visibility') == 'final')
    assert '问题处置' in final_message['content']
    assert '采用灰度方案并保留回滚' in final_message['content']


def test_synthesis_without_technical_issue_decisions_is_blocked(repository, temp_settings):
    mission = create(repository, temp_settings)
    def conflict(agent, view, body):
        if view['phase'] == 'exchange':
            claims = [c['id'] for r in view['results'] if r['kind'] == 'opinion' for c in r['data']['claims']]
            return {'kind': 'contribution', 'issues': [{'claim_ids': claims, 'category': 'approach',
                    'problem': '发布策略不兼容', 'impact': '影响上线风险', 'proposal': '采用灰度方案'}]}
        if view['phase'] == 'synthesis':
            body['issue_decisions'] = []
        return body
    adapter = ScriptedAdapter(conflict)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    control(repository, temp_settings, mission.id, 'select_synthesizer', agent_id='chulan')
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    state = Deliberation(repository).state(repository.get_mission(mission.id))
    assert repository.get_mission(mission.id).status == MissionStatus.BLOCKED
    assert state['final_id'] is None
    assert state['error'] == 'stage_submission_failed'


@pytest.mark.parametrize('rounds', [2, 3])
def test_unresolved_conflict_stops_at_configured_limit(repository, temp_settings, rounds):
    mission = create(repository, temp_settings, rounds=rounds)
    def conflict(agent, view, body):
        if view['phase'] == 'exchange':
            claims = [c['id'] for r in view['results'] if r['kind'] == 'opinion' for c in r['data']['claims']]
            return {'kind': 'contribution', 'issues': [{'claim_ids': claims, 'category': 'approach',
                    'problem': '方案不兼容', 'impact': '影响实施路径', 'proposal': f'仍建议{agent}'}]}
        return body
    adapter = ScriptedAdapter(conflict)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    state = Deliberation(repository).state(repository.get_mission(mission.id))
    assert len([p for _, p, _ in adapter.calls if p == 'exchange']) == 3 * rounds
    assert state['phase'] == 'awaiting_synthesizer'
    assert all(i['status'] == 'unresolved' for i in state['issues'].values())


def test_human_selection_is_versioned_and_idempotent(repository, temp_settings):
    mission = create(repository, temp_settings)
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mission.id)
    policy = Deliberation(repository)
    request = DiscussionControlRequest(action='select_synthesizer', request_id='choose',
        expected_revision=policy.state(repository.get_mission(mission.id))['revision'], agent_id='wangye')
    assert policy.control(mission.id, request, temp_settings) == (mission.id, True)
    assert policy.control(mission.id, request, temp_settings) == (mission.id, False)
    with pytest.raises(DiscussionError):
        policy.control(mission.id, request.model_copy(update={'agent_id': 'chulan'}), temp_settings)
    with pytest.raises(DiscussionError):
        policy.control(mission.id, request.model_copy(update={'request_id': 'stale'}), temp_settings)


@pytest.mark.parametrize('disposition', ['accepted', 'not_accepted'])
def test_reviews_are_isolated_and_revision_is_bounded(repository, temp_settings, disposition):
    mission = create(repository, temp_settings)
    def review(agent, view, body):
        if view['phase'] == 'review':
            source = next(r['data']['claims'][0]['id'] for r in view['results'] if r['kind'] == 'opinion')
            body = {'kind': 'review', 'draft_id': view['draft_id'], 'findings': [{
                'section_id': 'pending', 'category': 'omission', 'problem': '遗漏一个未验证条件',
                'source_ids': [source], 'desired_change': '保留未知', 'blocking': True}]}
        elif view['phase'] == 'finalizing' and disposition == 'not_accepted':
            original = next(r['data'] for r in view['results'] if r['id'] == view['draft_id'])
            body['sections'] = copy.deepcopy(original['sections'])
            for item in body['dispositions']:
                item.update(action='not_accepted', reason='现有证据不足，暂不采纳该纠正。')
        return body
    adapter = ScriptedAdapter(review)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    control(repository, temp_settings, mission.id, 'select_synthesizer', agent_id='wangye')
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    review_calls = [(a, v) for a, p, v in adapter.calls if p == 'review']
    assert {a for a, _ in review_calls} == {'chulan', 'baobao'}
    assert all(not [r for r in v['results'] if r['kind'] == 'review'] for _, v in review_calls)
    assert len([p for _, p, _ in adapter.calls if p == 'finalizing']) == 1
    view = Deliberation(repository).user_view(repository.get_mission(mission.id))
    assert view['phase'] == 'ended' and view['final_id'] != view['draft_id']
    final = next(r for r in view['results'] if r['id'] == view['final_id'])
    assert len(final['data']['dispositions']) == 2
    from backend.app.api.mission_detail import build_mission_detail
    detail = build_mission_detail(repository, temp_settings, mission.id)
    final_message = next(m for m in detail['messages'] if m['metadata'].get('discussion_visibility') == 'final')
    assert final_message['metadata']['discussion_revision_reviewed'] is False
    audit = final_message['metadata']['discussion_audit']
    assert audit['sources'] and len(audit['reviews']) == 2
    assert all(review['problem'] == '遗漏一个未验证条件' and review['action'] == disposition for review in audit['reviews'])
    if disposition == 'not_accepted':
        assert all(review['reason'] == '现有证据不足，暂不采纳该纠正。' for review in audit['reviews'])


def test_revision_cannot_silently_rewrite_unreviewed_sections(repository, temp_settings):
    mission = create(repository, temp_settings)
    def modify(agent, view, body):
        if view['phase'] == 'review':
            source = next(r['data']['claims'][0]['id'] for r in view['results'] if r['kind'] == 'opinion')
            return {'kind': 'review', 'draft_id': view['draft_id'], 'findings': [{'section_id': 'pending',
                    'category': 'omission', 'problem': '遗漏', 'source_ids': [source], 'desired_change': '补充'}]}
        if view['phase'] == 'finalizing':
            body['sections'][0]['content'] = '偷偷改掉推荐方案'
        return body
    adapter = ScriptedAdapter(modify)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    control(repository, temp_settings, mission.id, 'select_synthesizer', agent_id='chulan')
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    assert repository.get_mission(mission.id).status == MissionStatus.BLOCKED
    assert Deliberation(repository).state(repository.get_mission(mission.id))['final_id'] is None


def test_final_audit_exposes_material_paths_without_snapshot_bodies(repository, temp_settings, tmp_path, monkeypatch):
    source = tmp_path / 'project/backend/app'
    source.mkdir(parents=True)
    (source / 'sample.py').write_text('PRIVATE_SOURCE_BODY_CANARY')
    monkeypatch.chdir(tmp_path / 'project')
    mission = create(repository, temp_settings)
    def cite_material(agent, view, body):
        if view['phase'] == 'independent':
            body['claims'][0]['source_ids'] = [next(iter(view['snapshot']['materials']))]
        return body
    adapter = ScriptedAdapter(cite_material)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    control(repository, temp_settings, mission.id, 'select_synthesizer', agent_id='chulan')
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    from backend.app.api.mission_detail import build_mission_detail
    detail = build_mission_detail(repository, temp_settings, mission.id)
    final_message = next(m for m in detail['messages'] if m['metadata'].get('discussion_visibility') == 'final')
    audit = final_message['metadata']['discussion_audit']
    assert any(item['label'] == 'backend/app/sample.py' for item in audit['sources'])
    assert 'PRIVATE_SOURCE_BODY_CANARY' not in json.dumps(audit)


@pytest.mark.parametrize('option_id,confirmation', [('speed', '选择「速度」'), ('defer', '暂不选择，待以后决定')])
def test_value_choices_wait_for_explicit_human_decision(repository, temp_settings, option_id, confirmation):
    mission = create(repository, temp_settings)
    def value(agent, view, body):
        if view['phase'] == 'synthesis':
            body['decisions'] = [{'id': 'risk', 'question': '优先速度还是稳健？',
                                  'options': [{'id': 'speed', 'label': '速度'}, {'id': 'safety', 'label': '稳健'}]}]
        return body
    adapter = ScriptedAdapter(value)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    control(repository, temp_settings, mission.id, 'select_synthesizer', agent_id='chulan')
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    policy = Deliberation(repository)
    assert policy.state(repository.get_mission(mission.id))['awaiting_reason'] == 'resolve_value_choice'
    Roundtable(repository).recover()
    assert repository.get_mission(mission.id).status == MissionStatus.AWAITING_INPUT
    decision_request = DiscussionControlRequest(action='record_decision', request_id='record-choice',
        expected_revision=policy.state(repository.get_mission(mission.id))['revision'],
        decision_id='risk', option_id=option_id)
    assert policy.control(mission.id, decision_request, temp_settings) == (mission.id, False)
    assert policy.control(mission.id, decision_request, temp_settings) == (mission.id, False)
    assert policy.state(repository.get_mission(mission.id))['phase'] == 'ended'
    client = TestClient(create_app(temp_settings))
    refreshed = client.get(f'/api/missions/{mission.id}').json()
    choices = [m for m in refreshed['messages'] if m['role'] == 'user' and confirmation in m['content']]
    assert len(choices) == 1
    assert choices[0]['content'] == f'关于「优先速度还是稳健？」，{confirmation}。'
    assert 'discussion_visibility' not in choices[0]['metadata']
    create_requested_mission(repository, temp_settings, CreateMissionRequest(goal='继续普通任务'), mission.conversation_id)
    history = client.get(f'/api/conversations/{mission.conversation_id}').json()['messages']
    assert [m['id'] for m in history if m['role'] == 'user' and confirmation in m['content']] == [choices[0]['id']]


def test_failure_keeps_other_opinions_sealed_and_retry_does_not_replay_success(repository, temp_settings):
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    batch = policy.claim_batch(mission.id, temp_settings.artifact_root)
    submit(repository, temp_settings, mission.id, batch[0], opinion())
    for lease in batch[1:]:
        tools_for(repository, temp_settings, mission.id, lease)
        policy.failed_attempt(mission.id, lease['turn_id'], 'simulated failure')
    policy.complete_batch(mission.id)
    assert occupies_conversation(repository.get_mission(mission.id))
    assert policy.user_view(repository.get_mission(mission.id))['results'] == []
    control(repository, temp_settings, mission.id, 'retry', turn_id=batch[1]['turn_id'])
    adapter = ScriptedAdapter()
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    assert {a for a, p, _ in adapter.calls if p == 'independent'} == {'baobao', 'wangye'}
    assert policy.state(repository.get_mission(mission.id))['turns_used'] <= 6


def test_stop_revokes_inflight_tokens(repository, temp_settings):
    mission = create(repository, temp_settings)
    batch = Deliberation(repository).claim_batch(mission.id, temp_settings.artifact_root)
    tools, lease = tools_for(repository, temp_settings, mission.id, batch[0])
    control(repository, temp_settings, mission.id, 'stop')
    with pytest.raises(MCPToolError):
        tools.submit_discussion_result(mission.id, lease['agent_id'], lease['turn_id'], 'fake', opinion())
    Deliberation(repository).complete_batch(mission.id)
    assert not occupies_conversation(repository.get_mission(mission.id))


def test_new_user_conditions_create_a_new_snapshot_only_after_explicit_action(repository, temp_settings):
    mission = create(repository, temp_settings)
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mission.id)
    policy = Deliberation(repository)
    old = policy.state(repository.get_mission(mission.id))['snapshot_id']
    new_condition = '新增条件:' + uuid4().hex
    policy.interrupt(mission.id, new_condition, None, 'new-condition')
    assert policy.state(repository.get_mission(mission.id))['awaiting_reason'] == 'revise_input'
    assert new_condition not in json.dumps(policy.get(mission.id, old)['messages'], ensure_ascii=False)
    inputs = policy.state(repository.get_mission(mission.id))['pending_inputs']
    target, scheduled = control(repository, temp_settings, mission.id, 'restart_with_input', input_ids=inputs, goal='在一周预算内评估方案')
    assert target != mission.id and scheduled
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(target)
    assert policy.state(repository.get_mission(target))['snapshot_id'] != old


def test_http_projection_hides_capabilities_and_waiting_task_locks_conversation(repository, temp_settings, monkeypatch):
    monkeypatch.setattr('backend.app.api.missions._run_mission_in_background', lambda *args: None)
    monkeypatch.setattr('backend.app.api.conversations._run_mission_in_background', lambda *args: None)
    client = TestClient(create_app(temp_settings))
    response = client.post('/api/missions', json={'goal': '讨论', 'room_mode': 'roundtable',
        'discussion': {'protocol': 'deliberation_v2', 'participant_ids': PARTICIPANTS}})
    assert response.status_code == 201
    mid = response.json()['mission']['id']
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mid)
    detail = client.get(f'/api/missions/{mid}').json()
    assert detail['discussion']['awaiting_reason'] == 'select_synthesizer'
    tokens = [l['token'] for l in Deliberation(repository).state(repository.get_mission(mid))['leases'].values()]
    assert all(t not in json.dumps(detail) for t in tokens)
    cid = detail['mission']['conversation_id']
    assert client.post(f'/api/conversations/{cid}/missions', json={'goal': '抢占'}).status_code == 409
    assert client.patch(f'/api/conversations/{cid}', json={'status': 'archived'}).status_code == 409
    rev = detail['discussion']['revision']
    bad = client.post(f'/api/missions/{mid}/discussion-controls', json={'action': 'select_synthesizer',
        'agent_id': 'outsider', 'expected_revision': rev, 'request_id': 'bad'})
    assert bad.status_code == 422


def test_mcp_catalog_and_stdio_share_v2_permissions(repository, temp_settings):
    from backend.app.mcp.server import _Context, _handle_tools_list, _handle_tools_call
    mission = create(repository, temp_settings)
    batch = Deliberation(repository).claim_batch(mission.id, temp_settings.artifact_root)
    tools, lease = tools_for(repository, temp_settings, mission.id, batch[0])
    ctx = _Context(mission.id, lease['agent_id'], lease['turn_id'], 'fake', tools)
    assert {t['name'] for t in _handle_tools_list(ctx, {})['tools']} == {'read_context', 'read_artifact', 'read_discussion_result', 'submit_discussion_result'}
    denied = _handle_tools_call(ctx, {'name': 'publish_message', 'arguments': {'content': 'leak'}})
    assert denied['isError']
    from backend.tests.test_mcp_server import MCPServerHarness
    harness = MCPServerHarness(db_path=temp_settings.database_path, artifact_root=temp_settings.artifact_root,
        mission_id=mission.id, agent_id=lease['agent_id'], turn_id=lease['turn_id'], provider_id='fake',
        extra_env={'YIREN_MCP_LEASE_TOKEN': lease['token']})
    try:
        harness.request('tools/call', {'name': 'read_context', 'arguments': {}}, request_id=11)
        result = harness.request('tools/call', {'name': 'submit_discussion_result', 'arguments': {'result': opinion()}}, request_id=12)
        assert not result['result'].get('isError')
    finally:
        harness.close()


def test_long_opinions_require_complete_paged_reads_before_no_addition(repository, temp_settings):
    from backend.app.orchestration.deliberation_runner import build_prompt
    from backend.app.domain.models import AgentProfile
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    batch = policy.claim_batch(mission.id, temp_settings.artifact_root)
    for lease in batch:
        body = opinion('独立长意见' + '甲' * 5000, recommendation=lease['agent_id'])
        body['claims'][0]['statement'] = '一项重要约束' + '乙' * 3000
        submit(repository, temp_settings, mission.id, lease, body)
    policy.complete_batch(mission.id)
    exchange = policy.claim_batch(mission.id, temp_settings.artifact_root)
    tools, lease = tools_for(repository, temp_settings, mission.id, exchange[0])
    view = tools.read_context(mission.id, agent_id=lease['agent_id'], turn_id=lease['turn_id'], provider_id='fake')
    assert len(view['required_read_ids']) == 3
    assert all(r.get('truncated') for r in view['results'])
    profile = AgentProfile.model_validate(mission.discussion.profiles[0])
    prompt = build_prompt(profile, lease, view)
    assert len(prompt.encode()) < 50_000
    assert '甲' * 100 not in prompt
    with pytest.raises(MCPToolError, match='Read complete source entries'):
        tools.submit_discussion_result(mission.id, lease['agent_id'], lease['turn_id'], 'fake',
                                       {'kind': 'contribution', 'no_addition': True})
    full = hydrate(tools, mission.id, lease['agent_id'], lease['turn_id'], 'fake', view)
    assert all(len(r['data']['position_summary']) > 5000 for r in full['results'])
    tools.submit_discussion_result(mission.id, lease['agent_id'], lease['turn_id'], 'fake',
                                   {'kind': 'contribution', 'no_addition': True})


def test_user_selection_race_schedules_exactly_one_synthesizer(repository, temp_settings):
    mission = create(repository, temp_settings)
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mission.id)
    policy = Deliberation(repository)
    revision = policy.state(repository.get_mission(mission.id))['revision']
    def choose(agent):
        try:
            return policy.control(mission.id, DiscussionControlRequest(action='select_synthesizer',
                agent_id=agent, request_id=agent, expected_revision=revision), temp_settings)[1]
        except DiscussionError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(choose, ['chulan', 'wangye']))
    assert sorted(outcomes) == [False, True]
    assert len([e for e in repository.list_events_after(mission.id, 0) if e.event_type == 'discussion.synthesizer_selected']) == 1
