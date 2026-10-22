"""Roundtable contract: real persistence/HTTP, deterministic offline adapters."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app.adapters.base import TurnOutcome
from backend.app.domain.models import Conversation, Mission, MissionStatus, TurnStatus
from backend.app.main import create_app
from backend.app.mcp.tools import MCPTools, MCPToolError
from backend.app.orchestration.runner import MissionRunner


PARTICIPANTS = ['chulan', 'baobao', 'wangye']


def request_body(**updates):
    return {'goal': 'Club 下一步优先做什么？', 'room_mode': 'roundtable',
            'discussion': {'participant_ids': PARTICIPANTS}, **updates}


class ScriptedAdapter:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        self.calls.append((agent_id, turn_id, prompt))
        tools.publish_message(mission_id, agent_id, turn_id, f'观点{len(self.calls)}：先保障离线讨论', provider_id)
        if self.callback:
            result = self.callback(self, mission_id, agent_id, turn_id, provider_id, tools)
            if result:
                return result
        return TurnOutcome(status=TurnStatus.COMPLETED)


@pytest.fixture
def idle_client(temp_settings, monkeypatch):
    # Keep scheduling deterministic; tests drive MissionRunner explicitly.
    monkeypatch.setattr('backend.app.api.missions._run_mission_in_background', lambda *args: None)
    monkeypatch.setattr('backend.app.api.conversations._run_mission_in_background', lambda *args: None)
    return TestClient(create_app(temp_settings))


def create(idle_client, **updates):
    response = idle_client.post('/api/missions', json=request_body(**updates))
    assert response.status_code == 201, response.text
    return response.json()['mission']['id']


@pytest.mark.parametrize('participants', [PARTICIPANTS, PARTICIPANTS[:2]])
def test_roundtable_defaults_and_summary(idle_client, repository, temp_settings, participants):
    mid = create(idle_client, discussion={'participant_ids': participants})
    adapter = ScriptedAdapter()
    MissionRunner(repository, temp_settings, adapter).run(mid)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert detail['mission']['room_mode'] == 'roundtable'
    assert [c[0] for c in adapter.calls] == participants * 2 + [participants[0]]
    assert detail['discussion']['turns_used'] == len(participants) * 2 + 1
    assert detail['discussion']['end_reason'] == 'budget_exhausted'
    assert detail['discussion']['phase'] == 'ended'
    assert len(detail['discussion']['summary_message_ids']) == 1
    assert '共识' in adapter.calls[-1][2] and '分歧' in adapter.calls[-1][2]


@pytest.mark.parametrize('updates', [
    {'discussion': {'participant_ids': ['chulan']}},
    {'discussion': {'participant_ids': ['chulan', 'chulan']}},
    {'discussion': {'participant_ids': ['chulan', 'unknown']}},
    {'discussion': {'participant_ids': PARTICIPANTS, 'max_turns': 3}},
    {'discussion': {'participant_ids': PARTICIPANTS, 'max_turns': 13}},
    {'discussion': {'participant_ids': PARTICIPANTS, 'max_turns': True}},
    {'target_agent_id': 'chulan'}, {'target_agent_ids': ['chulan']},
    {'room_mode': 'game'}, {'room_mode': 'task'}, {'discussion': None},
])
def test_invalid_roundtable_rejected(idle_client, updates):
    assert idle_client.post('/api/missions', json=request_body(**updates)).status_code == 422


def test_creation_idempotency_and_conversation_lock(idle_client, repository):
    mid = create(idle_client, request_id='create-1')
    assert create(idle_client, request_id='create-1') == mid
    assert len(repository.list_missions()) == 1
    cid = repository.get_mission(mid).conversation_id
    assert idle_client.post('/api/missions', json=request_body(conversation_id=cid)).status_code == 409
    assert idle_client.post(f'/api/conversations/{cid}/missions', json={'goal': '普通任务'}).status_code == 409
    assert idle_client.patch(f'/api/conversations/{cid}', json={'status': 'archived'}).status_code == 409
    assert idle_client.post('/api/missions', json=request_body(request_id='create-1', goal='不同内容')).status_code == 409


def test_controls_stop_wins_and_is_idempotent(idle_client, repository, temp_settings):
    mid = create(idle_client)
    def callback(adapter, mid, agent, turn, provider, tools):
        if len(adapter.calls) == 1:
            for action in ['finish', 'stop', 'stop']:
                r = idle_client.post(f'/api/missions/{mid}/discussion-controls', json={'action': action, 'request_id': action})
                assert r.status_code == 202, r.text
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert len(adapter.calls) == 1
    assert detail['discussion']['end_reason'] == 'user_stop'
    assert detail['discussion']['summary_message_ids'] == []
    assert idle_client.post(f'/api/missions/{mid}/discussion-controls', json={'action': 'stop', 'request_id': 'stop'}).status_code == 202
    assert idle_client.post(f'/api/missions/{mid}/interruptions', json={'content': '迟到', 'request_id': 'late'}).status_code == 409


def test_finish_selects_temporary_summary_speaker(idle_client, repository, temp_settings):
    mid = create(idle_client)
    def callback(adapter, mid, agent, turn, provider, tools):
        if len(adapter.calls) == 1:
            r = idle_client.post(f'/api/missions/{mid}/discussion-controls', json={'action': 'finish', 'summary_agent_id': 'wangye', 'request_id': 'f'})
            assert r.status_code == 202
        else:
            assert idle_client.get(f'/api/missions/{mid}').json()['discussion']['pending_control'] is None
            assert idle_client.post(f'/api/missions/{mid}/interruptions', json={'content': '太晚', 'request_id': 'late'}).status_code == 409
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    assert [c[0] for c in adapter.calls] == ['chulan', 'wangye']
    assert len([e for e in repository.list_events_after(mid, 0) if e.event_type == 'discussion.control_handled']) == 1
    assert idle_client.get(f'/api/missions/{mid}').json()['discussion']['end_reason'] == 'user_finish'


def tool_call(transport, tools, settings, mid, agent, turn, provider, name, arguments):
    if transport == 'in_process':
        if name == 'invoke_agent':
            return tools.invoke_agent(mid, agent, turn, arguments['target_agent_id'], arguments['purpose'], provider, mode=arguments.get('mode', 'fire_and_forget'))
        if name == 'publish_message':
            return tools.publish_message(mid, agent, turn, arguments['content'], provider)
        if name == 'write_artifact':
            return tools.write_artifact(mid, agent, turn, arguments['relative_path'], arguments['content'], provider_id=provider)
    from backend.tests.test_mcp_server import MCPServerHarness
    harness = MCPServerHarness(db_path=settings.database_path, artifact_root=settings.artifact_root,
                               mission_id=mid, agent_id=agent, turn_id=turn, provider_id=provider,
                               extra_env={'YIREN_MCP_VALID_AGENTS': ','.join(PARTICIPANTS)})
    try:
        harness.request('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'roundtable-test', 'version': '1'}})
        harness.notify('notifications/initialized')
        catalog = harness.request('tools/list', {}, request_id=3)['result']['tools']
        invitation = next(t for t in catalog if t['name'] == 'invoke_agent')
        expected = tools.repository.get_mission(mid).discussion.participant_ids
        assert invitation['inputSchema']['properties']['target_agent_id']['enum'] == expected
        response = harness.request('tools/call', {'name': name, 'arguments': arguments}, request_id=2)
        assert 'result' in response, response
        result = response['result']
        if result.get('isError'):
            raise MCPToolError(result['content'][0]['text'])
        return json.loads(result['content'][0]['text'])
    finally:
        harness.close()


@pytest.mark.parametrize('transport', ['in_process', 'stdio'])
def test_a2a_budget_and_scheduled_purpose_share_contract(idle_client, repository, temp_settings, transport):
    mid = create(idle_client, discussion={'participant_ids': PARTICIPANTS, 'max_turns': 4})
    def callback(adapter, mid, agent, turn, provider, tools):
        def invoke(target, mode='wait_reply', purpose='请回应离线条件'):
            return tool_call(transport, tools, temp_settings, mid, agent, turn, provider, 'invoke_agent',
                             {'target_agent_id': target, 'purpose': purpose, 'mode': mode})
        if len(adapter.calls) == 1:
            with pytest.raises(MCPToolError, match='budget'):
                invoke('baobao')
            with pytest.raises(MCPToolError, match='participant'):
                invoke('outside')
            result = invoke('baobao', mode='fire_and_forget', purpose='请专门比较两个离线方案')
            assert result['status'] == 'scheduled'
            assert invoke('baobao', mode='fire_and_forget', purpose='请专门比较两个离线方案') == result
        elif len(adapter.calls) == 2:
            assert '请专门比较两个离线方案' in adapter.calls[-1][2]
        elif len(adapter.calls) == 4:
            with pytest.raises(MCPToolError, match='closing'):
                invoke('baobao')
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert detail['mission']['status'] == 'completed'
    assert detail['discussion']['turns_used'] == 4
    assert [c[0] for c in adapter.calls] == PARTICIPANTS + ['chulan']
    assert len([e for e in repository.list_events_after(mid, 0) if e.event_type == 'discussion.invitation']) == 1


@pytest.mark.parametrize('transport', ['in_process', 'stdio'])
def test_nested_wait_reply_reserves_all_resumes_and_summary(idle_client, repository, temp_settings, transport):
    mid = create(idle_client)
    def callback(adapter, mid, agent, turn, provider, tools):
        def invite(target):
            return tool_call(transport, tools, temp_settings, mid, agent, turn, provider, 'invoke_agent',
                             {'target_agent_id': target, 'purpose': '请给出具体依据', 'mode': 'wait_reply'})
        n = len(adapter.calls)
        if n in (1, 2):
            assert invite('baobao' if n == 1 else 'wangye')['reserved_turns'] == 2
        if n == 3:
            with pytest.raises(MCPToolError, match='cycle'):
                invite('chulan')
        if n == 4:
            with pytest.raises(MCPToolError, match='budget'):
                invite('wangye')
            assert '观点3' in adapter.calls[-1][2]
        if n == 5:
            assert '观点2' in adapter.calls[-1][2]
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    assert [c[0] for c in adapter.calls[:5]] == ['chulan', 'baobao', 'wangye', 'baobao', 'chulan']
    assert len(adapter.calls) == 7
    assert idle_client.get(f'/api/missions/{mid}').json()['discussion']['end_reason'] == 'budget_exhausted'


@pytest.mark.parametrize('transport', ['in_process', 'stdio'])
def test_selected_participants_are_authoritative_for_both_transports(idle_client, repository, temp_settings, transport):
    mid = create(idle_client, discussion={'participant_ids': PARTICIPANTS[:2]})
    def callback(adapter, mid, agent, turn, provider, tools):
        if len(adapter.calls) == 1:
            with pytest.raises(MCPToolError, match='participant'):
                tool_call(transport, tools, temp_settings, mid, agent, turn, provider, 'invoke_agent',
                          {'target_agent_id': 'wangye', 'purpose': '不允许扩大团队'})
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    assert len(adapter.calls) == 5


@pytest.mark.parametrize('transport', ['in_process', 'stdio'])
@pytest.mark.parametrize('tool_name,arguments', [
    ('publish_message', {'content': '迟到消息'}),
    ('write_artifact', {'relative_path': 'late.md', 'content': '迟到产物'}),
    ('invoke_agent', {'target_agent_id': 'baobao', 'purpose': '迟到邀请'}),
])
def test_restart_rejects_late_mcp_mutations(idle_client, repository, temp_settings, transport, tool_name, arguments):
    from backend.app.orchestration.roundtable import Roundtable
    mid = create(idle_client)
    policy = Roundtable(repository)
    _, _, step, turn_id, _ = policy.claim(mid)
    policy.authorize_attempt(mid, turn_id, 'codex')
    # Actual app startup recovery, without launching providers.
    create_app(temp_settings)
    tools = MCPTools(repository, temp_settings.artifact_root)
    with pytest.raises(MCPToolError, match='ended'):
        tool_call(transport, tools, temp_settings, mid, step.agent_id, turn_id, 'codex', tool_name, arguments)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert detail['mission']['status'] == 'blocked'
    assert detail['discussion']['end_reason'] == 'service_restart'
    assert len(repository.list_messages(mid)) == 1
    assert not (temp_settings.artifact_root / mid / 'late.md').exists()
    assert any(e.event_type == 'tool.error' for e in repository.list_events_after(mid, 0))
    cid = repository.get_mission(mid).conversation_id
    assert idle_client.post(f'/api/conversations/{cid}/missions', json=request_body(request_id='continue')).status_code == 201


def test_pending_inputs_merge_and_target_cannot_starve_opening(idle_client, repository, temp_settings):
    mid = create(idle_client, discussion={'participant_ids': PARTICIPANTS, 'max_turns': 4})
    def callback(adapter, mid, agent, turn, provider, tools):
        n = len(adapter.calls)
        if n == 1:
            for index in range(3):
                payload = {'content': f'补充条件{index}', 'target_agent_id': 'chulan', 'request_id': f'i{index}'}
                assert idle_client.post(f'/api/missions/{mid}/interruptions', json=payload).status_code == 202
                assert idle_client.post(f'/api/missions/{mid}/interruptions', json=payload).status_code == 202
        elif n == 2 or n == 4:
            assert all(f'补充条件{i}' in adapter.calls[-1][2] for i in range(3))
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert [c[0] for c in adapter.calls] == PARTICIPANTS + ['chulan']
    assert len(detail['interrupts']) == 3
    assert all(i['status'] == 'handled' for i in detail['interrupts'])
    assert len([m for m in detail['messages'] if m['role'] == 'user']) == 4


def test_targeted_interrupt_moves_opening_forward(idle_client, repository, temp_settings):
    mid = create(idle_client)
    def callback(adapter, mid, agent, turn, provider, tools):
        if len(adapter.calls) == 1:
            r = idle_client.post(f'/api/missions/{mid}/interruptions', json={'content': '请王也先回答', 'target_agent_id': 'wangye', 'request_id': 'i'})
            assert r.status_code == 202
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    assert [c[0] for c in adapter.calls[:3]] == ['chulan', 'wangye', 'baobao']


def test_fallback_uses_one_turn_and_failure_preserves_partial_result(idle_client, repository, temp_settings):
    mid = create(idle_client)
    def callback(adapter, mid, agent, turn, provider, tools):
        if len(adapter.calls) == 2:
            with pytest.raises(MCPToolError, match='Provider attempt expired'):
                tools.publish_message(mid, agent, turn, '过期 provider 的迟到消息', 'claude')
    class BeforeOutputFailure(ScriptedAdapter):
        def run_turn(self, mid, agent, turn, provider, prompt, tools):
            if not self.calls:
                self.calls.append((agent, turn, prompt))
                return TurnOutcome(status=TurnStatus.BLOCKED, summary='try fallback')
            return super().run_turn(mid, agent, turn, provider, prompt, tools)
    adapter = BeforeOutputFailure(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    assert len(adapter.calls) == 8
    assert len({c[1] for c in adapter.calls}) == 7
    assert idle_client.get(f'/api/missions/{mid}').json()['discussion']['turns_used'] == 7
    mid2 = create(idle_client)
    failed = ScriptedAdapter(lambda *args: TurnOutcome(status=TurnStatus.BLOCKED, summary='providers unavailable'))
    MissionRunner(repository, temp_settings, failed).run(mid2)
    detail = idle_client.get(f'/api/missions/{mid2}').json()
    assert detail['mission']['status'] == 'blocked'
    assert detail['discussion']['end_reason'] == 'provider_failed'
    assert detail['discussion']['turns_used'] == 1
    assert detail['discussion']['summary_message_ids'] == []
    assert len(detail['messages']) > 1
    assert len(failed.calls) == 1  # Already published: never silently replay on another provider.


def test_summary_failure_is_distinct(idle_client, repository, temp_settings):
    mid = create(idle_client)
    def callback(adapter, *args):
        if '阶段：closing' in adapter.calls[-1][2]:
            return TurnOutcome(status=TurnStatus.BLOCKED, summary='summary failed')
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert detail['discussion']['turns_used'] == 7
    assert detail['discussion']['end_reason'] == 'summary_failed'


def test_concurrent_create_has_one_winner_across_endpoints(idle_client, repository):
    repository.create_conversation(Conversation(id='concurrent', title='c'))
    barrier = threading.Barrier(2)
    def post(index):
        barrier.wait(timeout=5)
        if index:
            return idle_client.post('/api/missions', json=request_body(conversation_id='concurrent')).status_code
        return idle_client.post('/api/conversations/concurrent/missions', json=request_body()).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(post, [0, 1]))
    assert sorted(results) == [201, 409]
    assert len(repository.list_missions_for_conversation('concurrent')) == 1


def test_concurrent_duplicate_creation_starts_one_mission(idle_client, repository):
    barrier = threading.Barrier(2)
    def post(_):
        barrier.wait(timeout=5)
        return create(idle_client, request_id='same')
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(post, [0, 1]))
    assert ids[0] == ids[1]
    assert len(repository.list_conversations()) == 1
    assert len(repository.list_messages(ids[0])) == 1


def test_accepted_stop_prevents_next_lease(idle_client, repository, temp_settings):
    entered, release = threading.Event(), threading.Event()
    mid = create(idle_client)
    def callback(adapter, mid, agent, turn, provider, tools):
        entered.set()
        assert release.wait(5)
        tools.publish_message(mid, agent, turn, '当前回合可以结束', provider)
    adapter = ScriptedAdapter(callback)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(MissionRunner(repository, temp_settings, adapter).run, mid)
        assert entered.wait(5)
        response = idle_client.post(f'/api/missions/{mid}/discussion-controls', json={'action': 'stop', 'request_id': 'stop'})
        assert response.status_code == 202
        assert response.json()['discussion']['pending_control'] == 'stop'
        assert response.json()['mission']['status'] == 'running'
        release.set()
        running.result(timeout=5)
    assert len(adapter.calls) == 1
    assert idle_client.get(f'/api/missions/{mid}').json()['discussion']['end_reason'] == 'user_stop'


def test_legacy_mission_defaults_and_server_limit(idle_client, temp_settings):
    assert Mission(id='old', title='old', goal='old').discussion is None
    temp_settings.roundtable_max_turns = 4
    assert idle_client.post('/api/missions', json=request_body()).status_code == 422
    mid = create(idle_client, discussion={'participant_ids': PARTICIPANTS, 'max_turns': 4})
    assert idle_client.get(f'/api/missions/{mid}').json()['discussion']['turns_limit'] == 4


def test_stop_before_start_cancels_accepted_input_without_calling_model(idle_client, repository, temp_settings):
    mid = create(idle_client)
    assert idle_client.post(f'/api/missions/{mid}/interruptions', json={'content': '待处理补充', 'request_id': 'i'}).status_code == 202
    assert idle_client.post(f'/api/missions/{mid}/discussion-controls', json={'action': 'stop', 'request_id': 's'}).status_code == 202
    adapter = ScriptedAdapter()
    MissionRunner(repository, temp_settings, adapter).run(mid)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert not adapter.calls
    assert detail['discussion']['turns_used'] == 0
    assert detail['interrupts'][0]['status'] == 'cancelled'
    assert len(detail['discussion']['unanswered_interrupt_ids']) == 1


def test_last_exchange_accepts_input_for_summary_then_rejects_late_input(idle_client, repository, temp_settings):
    mid = create(idle_client)
    def callback(adapter, mid, agent, turn, provider, tools):
        if len(adapter.calls) == 6:
            payload = {'content': '最后条件：只能用一天', 'target_agent_id': 'wangye', 'request_id': 'last'}
            assert idle_client.post(f'/api/missions/{mid}/interruptions', json=payload).status_code == 202
        if len(adapter.calls) == 7:
            assert '最后条件：只能用一天' in adapter.calls[-1][2]
            assert idle_client.post(f'/api/missions/{mid}/interruptions', json={'content': '晚到', 'request_id': 'late'}).status_code == 409
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert detail['interrupts'][0]['handled_in_summary'] is True
    assert len(adapter.calls) == 7


@pytest.mark.parametrize('transport', ['in_process', 'stdio'])
def test_previous_turn_cannot_write_during_next_speaker(idle_client, repository, temp_settings, transport):
    mid = create(idle_client)
    previous = []
    def callback(adapter, mid, agent, turn, provider, tools):
        if len(adapter.calls) == 1:
            previous.extend([agent, turn, provider])
        if len(adapter.calls) == 2:
            with pytest.raises(MCPToolError, match='lease'):
                tool_call(transport, tools, temp_settings, mid, *previous, 'publish_message', {'content': '上一回合迟到发言'})
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    assert all(m.content != '上一回合迟到发言' for m in repository.list_messages(mid))


def test_control_and_interrupt_request_ids_reject_conflicting_retries(idle_client):
    mid = create(idle_client)
    url = f'/api/missions/{mid}/interruptions'
    assert idle_client.post(url, json={'content': 'a', 'request_id': 'i'}).status_code == 202
    assert idle_client.post(url, json={'content': 'b', 'request_id': 'i'}).status_code == 409
    assert idle_client.post(url, json={'content': 'c'}).status_code == 422
    assert idle_client.post(url, json={'content': 'c', 'request_id': 'j', 'target_agent_id': 'outside'}).status_code == 422
    url = f'/api/missions/{mid}/discussion-controls'
    assert idle_client.post(url, json={'action': 'finish', 'request_id': 'r'}).status_code == 202
    assert idle_client.post(url, json={'action': 'stop', 'request_id': 'r'}).status_code == 409
    assert idle_client.post(url, json={'action': 'stop', 'request_id': 's', 'summary_agent_id': 'wangye'}).status_code == 422


def test_creation_transaction_rolls_back_all_rows(idle_client, repository, monkeypatch):
    original = type(repository).add_message
    def fail(self, message, db=None):
        original(self, message, db=db)
        raise RuntimeError('injected write failure')
    monkeypatch.setattr(type(repository), 'add_message', fail)
    with pytest.raises(RuntimeError, match='injected'):
        create(idle_client, request_id='atomic')
    assert repository.list_missions() == []
    assert repository.list_conversations() == []
    with repository._transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


def test_roundtable_fake_never_creates_artifacts_from_execution_keywords(idle_client, repository, temp_settings):
    from backend.app.adapters.fake import FakeCLIAdapter
    mid = create(idle_client, goal='讨论是否需要实现一个新系统')
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mid)
    assert idle_client.get(f'/api/missions/{mid}').json()['artifacts'] == []


def test_completed_discussion_continues_in_new_mission(idle_client, repository, temp_settings):
    mid = create(idle_client)
    MissionRunner(repository, temp_settings, ScriptedAdapter()).run(mid)
    cid = repository.get_mission(mid).conversation_id
    response = idle_client.post(f'/api/conversations/{cid}/missions', json=request_body(request_id='next', goal='继续，补充不同意见'))
    assert response.status_code == 201, response.text
    next_mid = response.json()['latest_mission_detail']['mission']['id']
    assert next_mid != mid
    assert response.json()['latest_mission_detail']['discussion']['turns_used'] == 0
    assert len(repository.list_missions_for_conversation(cid)) == 2
    assert len(response.json()['messages']) == 9


@pytest.mark.parametrize('count,limit', [(2, 3), (2, 5), (2, 12), (3, 4), (3, 7), (3, 12)])
def test_repeated_invites_never_exceed_budget_or_starve_participants(idle_client, repository, temp_settings, count, limit):
    participants = PARTICIPANTS[:count]
    mid = create(idle_client, discussion={'participant_ids': participants, 'max_turns': limit})
    def callback(adapter, mid, agent, turn, provider, tools):
        target = participants[(participants.index(agent) + 1) % count]
        try:
            tools.invoke_agent(mid, agent, turn, target, '继续接力', provider, mode='wait_reply')
        except MCPToolError:
            pass
    adapter = ScriptedAdapter(callback)
    MissionRunner(repository, temp_settings, adapter).run(mid)
    detail = idle_client.get(f'/api/missions/{mid}').json()
    assert len(adapter.calls) == limit
    assert set(c[0] for c in adapter.calls[:-1]) == set(participants)
    assert detail['mission']['status'] == 'completed'
    phases = [e.payload['phase'] for e in repository.list_events_after(mid, 0) if e.event_type == 'discussion.turn_claimed']
    ranks = [{'opening': 0, 'exchange': 1, 'closing': 2}[p] for p in phases]
    assert ranks == sorted(ranks)
