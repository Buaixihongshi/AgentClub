"""Project skill entry, frozen execution rules and message presentation."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.api.mission_requests import (
    CreateMissionRequest, DiscussionControlRequest, create_requested_mission,
)
from backend.app.domain import project_skills
from backend.app.domain.models import Mission, MissionStatus
from backend.app.main import create_app
from backend.app.orchestration.deliberation import Deliberation
from backend.app.orchestration.roundtable import DiscussionError
from backend.app.orchestration.runner import MissionRunner


@pytest.fixture()
def skill_file(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    target = project / 'skills' / 'discuss' / 'SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_text('---\nname: discuss\ndescription: 独立讨论\n---\n保留少数意见 SKILL_CANARY_V1。', encoding='utf-8')
    monkeypatch.setattr(project_skills, 'PROJECT_ROOT', project)
    return target


def create(repo, settings, **fields):
    return create_requested_mission(repo, settings, CreateMissionRequest(goal='核验方案', skill='discuss', **fields))[0]


def choose(repo, settings, mission):
    policy = Deliberation(repo)
    return policy.control(mission.id, DiscussionControlRequest(action='select_synthesizer',
        agent_id=mission.discussion.participant_ids[-1], request_id=uuid4().hex,
        expected_revision=policy.state(repo.get_mission(mission.id))['revision']), settings)


@pytest.mark.parametrize('payload', [
    {'goal': '/discuss 核验方案'},
    {'goal': ' \n/discuss\n核验方案'},
    {'goal': '核验方案', 'skill': 'discuss'},
])
def test_skill_entry_uses_registered_agents_and_freezes_file(payload, repository, temp_settings, skill_file):
    mission, _, _ = create_requested_mission(repository, temp_settings, CreateMissionRequest(**payload))
    assert mission.skill == 'discuss'
    assert mission.goal == '核验方案'
    assert mission.room_mode == 'roundtable'
    assert mission.discussion.protocol == 'deliberation_v2'
    assert mission.discussion.participant_ids == ['chulan', 'baobao', 'wangye']
    assert mission.discussion.skill_instructions == skill_file.read_text()
    assert mission.discussion.skill_hash == hashlib.sha256(skill_file.read_bytes()).hexdigest()


def test_default_participants_use_actual_config_and_skill_root_is_not_cwd(repository, temp_settings, skill_file, tmp_path, monkeypatch):
    profiles = json.loads(Path('config/agents.json').read_text())['agents'][:2]
    profiles[0]['id'], profiles[1]['id'] = 'architect', 'reviewer'
    config = tmp_path / 'custom-config'
    config.mkdir()
    (config / 'agents.json').write_text(json.dumps({'agents': profiles}))
    settings = temp_settings.model_copy(update={'config_dir': config})
    monkeypatch.chdir(tmp_path)
    mission = create(repository, settings)
    assert mission.discussion.participant_ids == ['architect', 'reviewer']
    assert 'SKILL_CANARY_V1' in mission.discussion.skill_instructions


@pytest.mark.parametrize('payload', [
    {'goal': '/discuss'}, {'goal': '/discuss  \n'},
    {'goal': '  ', 'skill': 'discuss'}, {'goal': 'test', 'skill': 'unknown'},
    {'goal': '/discuss test', 'skill': 'unknown'},
    {'goal': 'test', 'skill': 'discuss', 'target_agent_id': 'chulan'},
    {'goal': 'test', 'skill': 'discuss', 'target_agent_ids': []},
    {'goal': 'test', 'skill': 'discuss', 'discussion': {'protocol': 'roundtable_v1', 'participant_ids': ['chulan', 'baobao']}},
    {'goal': 'test', 'skill': 'discuss', 'discussion': {'max_turns': 7}},
    {'goal': 'test', 'room_mode': 'roundtable', 'discussion': {'protocol': 'deliberation_v2'}},
])
def test_invalid_skill_input_is_rejected(payload):
    with pytest.raises(ValidationError):
        CreateMissionRequest(**payload)


@pytest.mark.parametrize('goal', ['/discuss-more test', '/discuss中文', '请 /discuss test', '`/discuss test`'])
def test_command_requires_opening_complete_boundary(goal):
    request = CreateMissionRequest(goal=goal)
    assert request.skill is None
    assert request.room_mode == 'task'
    assert request.goal == goal


def test_skill_can_customize_participants_and_round_budget(repository, temp_settings, skill_file):
    mission = create(repository, temp_settings, discussion={'participant_ids': ['baobao', 'wangye'], 'max_exchange_rounds': 2})
    assert mission.discussion.participant_ids == ['baobao', 'wangye']
    assert mission.discussion.max_turns == 9


def test_skill_config_can_omit_participants(repository, temp_settings, skill_file):
    mission = create(repository, temp_settings, discussion={'max_exchange_rounds': 2})
    assert len(mission.discussion.participant_ids) == 3
    assert mission.discussion.max_turns == 13


def test_v2_requires_two_to_three_exchange_rounds():
    for rounds in (1, 4):
        with pytest.raises(ValidationError):
            CreateMissionRequest(goal='核验方案', skill='discuss',
                                 discussion={'participant_ids': ['chulan', 'baobao'],
                                             'max_exchange_rounds': rounds})


def test_missing_skill_fails_before_persisting_mission(repository, temp_settings, skill_file):
    skill_file.unlink()
    with pytest.raises(DiscussionError, match='skill discuss is unavailable'):
        create(repository, temp_settings)
    assert repository.list_missions() == []
    assert repository.list_conversations() == []


def test_old_tasks_and_v1_fingerprints_remain_compatible(repository, temp_settings):
    for fields in ({}, {'room_mode': 'roundtable', 'discussion': {'participant_ids': ['chulan', 'baobao']}}):
        request = CreateMissionRequest(goal='旧客户端', request_id=uuid4().hex, **fields)
        first, _, created = create_requested_mission(repository, temp_settings, request)
        second, _, repeated = create_requested_mission(repository, temp_settings, request)
        assert created and not repeated and first.id == second.id
        record = next(e for e in repository.list_events_after(first.id, 0) if e.event_type == 'mission.request_accepted')
        assert 'skill' not in record.payload['request']
        old_data = first.model_dump(mode='json', exclude={'skill'})
        assert Mission.model_validate(old_data).skill is None
        if first.discussion:
            assert first.discussion.protocol == 'roundtable_v1'


def seed_legacy_fingerprint(repo, settings, request, missing_fields):
    mission, _, _ = create_requested_mission(repo, settings, request.model_copy(update={'request_id': None}))
    fingerprint = request.model_dump(exclude={'conversation_id', 'skill'})
    for key in missing_fields:
        del fingerprint['discussion'][key]
    repo.add_event(mission.id, 'mission.request_accepted', {
        'scope': 'new_conversation', 'request_id': request.request_id, 'request': fingerprint,
    })
    return mission


@pytest.mark.parametrize('missing_fields', [('protocol',), ('max_exchange_rounds',), ('protocol', 'max_exchange_rounds')])
def test_handwritten_legacy_v1_fingerprints_replay_missing_defaults(repository, temp_settings, missing_fields):
    request = CreateMissionRequest(goal='旧V1问题', room_mode='roundtable',
        discussion={'participant_ids': ['chulan', 'baobao']}, request_id='legacy-request')
    original = seed_legacy_fingerprint(repository, temp_settings, request, missing_fields)
    replay, _, created = create_requested_mission(repository, temp_settings, request)
    assert not created and replay.id == original.id
    assert len(repository.list_missions()) == 1


@pytest.mark.parametrize('change', [
    {'goal': '另一个议题'}, {'title': '更改标题'},
    {'discussion': {'participant_ids': ['baobao', 'wangye']}},
    {'discussion': {'participant_ids': ['chulan', 'baobao'], 'max_exchange_rounds': 3}},
    {'discussion': {'participant_ids': ['chulan', 'baobao'], 'max_turns': 8}},
    {'discussion': {'participant_ids': ['chulan', 'baobao'], 'protocol': 'deliberation_v2'}},
    {'room_mode': 'task', 'discussion': None, 'target_agent_id': 'chulan'},
])
def test_legacy_v1_defaults_do_not_hide_meaningful_input_changes(repository, temp_settings, change):
    fields = {'goal': '旧V1问题', 'room_mode': 'roundtable',
        'discussion': {'participant_ids': ['chulan', 'baobao']}, 'request_id': 'legacy-request'}
    request = CreateMissionRequest(**fields)
    seed_legacy_fingerprint(repository, temp_settings, request, ('protocol', 'max_exchange_rounds'))
    with pytest.raises(DiscussionError, match='request_id was already used with different input'):
        create_requested_mission(repository, temp_settings, CreateMissionRequest(**{**fields, **change}))
    assert len(repository.list_missions()) == 1


def test_v2_fingerprint_with_missing_field_is_not_relaxed(repository, temp_settings):
    request = CreateMissionRequest(goal='V2问题', room_mode='roundtable', discussion={
        'protocol': 'deliberation_v2', 'participant_ids': ['chulan', 'baobao']}, request_id='v2-request')
    seed_legacy_fingerprint(repository, temp_settings, request, ('max_exchange_rounds',))
    with pytest.raises(DiscussionError, match='request_id was already used with different input'):
        create_requested_mission(repository, temp_settings, request)


@pytest.mark.parametrize('conversation_endpoint', [False, True])
def test_http_skill_entry_and_idempotent_schedule(conversation_endpoint, temp_settings, skill_file, monkeypatch):
    calls = []
    def run(*args):
        calls.append(args[2])
    monkeypatch.setattr('backend.app.api.missions._run_mission_in_background', run)
    monkeypatch.setattr('backend.app.api.conversations._run_mission_in_background', run)
    client = TestClient(create_app(temp_settings))
    endpoint = '/api/missions'
    if conversation_endpoint:
        cid = client.post('/api/conversations', json={}).json()['id']
        endpoint = f'/api/conversations/{cid}/missions'
    payload = {'goal': '/discuss HTTP核验', 'request_id': 'same-invocation'}
    first = client.post(endpoint, json=payload)
    second = client.post(endpoint, json=payload)
    assert first.status_code == second.status_code == 201
    detail = first.json()['latest_mission_detail'] if conversation_endpoint else first.json()
    assert detail['mission']['skill'] == 'discuss'
    assert detail['mission']['goal'] == 'HTTP核验'
    assert detail['mission']['discussion']['protocol'] == 'deliberation_v2'
    assert len(calls) == 1
    assert client.get('/api/health').json()['project_skills'] == ['discuss']


class CapturingFake(FakeCLIAdapter):
    def __init__(self):
        self.prompts = []

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        self.prompts.append(prompt)
        return super().run_turn(mission_id, agent_id, turn_id, provider_id, prompt, tools)


def test_frozen_skill_is_used_through_six_phases_and_restart_keeps_skill(repository, temp_settings, skill_file):
    mission = create(repository, temp_settings)
    skill_file.write_text('SKILL_CANARY_V2')
    adapter = CapturingFake()
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    policy = Deliberation(repository)
    assert policy.state(repository.get_mission(mission.id))['awaiting_reason'] == 'select_synthesizer'
    assert len(adapter.prompts) == 3
    assert all('SKILL_CANARY_V1' in prompt and 'SKILL_CANARY_V2' not in prompt for prompt in adapter.prompts)
    choose(repository, temp_settings, mission)
    MissionRunner(repository, temp_settings, adapter).run(mission.id)
    assert policy.state(repository.get_mission(mission.id))['phase'] == 'ended'
    assert len(adapter.prompts) == 6
    assert all('SKILL_CANARY_V1' in prompt and 'SKILL_CANARY_V2' not in prompt for prompt in adapter.prompts)
    manifests = [e.payload for e in repository.list_events_after(mission.id, 0) if e.event_type == 'discussion.prompt_manifest']
    assert all(m['skill_hash'] == mission.discussion.skill_hash for m in manifests)

    followup = create(repository, temp_settings)
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(followup.id)
    policy.interrupt(followup.id, '预算变化', None, 'condition')
    state = policy.state(repository.get_mission(followup.id))
    target, scheduled = policy.control(followup.id, DiscussionControlRequest(action='restart_with_input',
        request_id='restart', expected_revision=state['revision'], input_ids=state['pending_inputs'],
        goal='重新检查预算'), temp_settings)
    new = repository.get_mission(target)
    assert scheduled and new.skill == 'discuss'
    assert new.discussion.skill_instructions == 'SKILL_CANARY_V2'
    assert new.discussion.protocol == 'deliberation_v2'


def test_history_and_latest_message_projections_keep_final_only_metadata(repository, temp_settings, skill_file):
    mission = create(repository, temp_settings)
    runner = MissionRunner(repository, temp_settings, FakeCLIAdapter())
    runner.run(mission.id)
    client = TestClient(create_app(temp_settings))
    opening = client.get(f'/api/missions/{mission.id}').json()
    assert all(m['metadata']['discussion_visibility'] == 'internal' for m in opening['messages'] if m['role'] == 'agent')
    assert client.get('/api/conversations').json()[0]['latest_message_preview'] == mission.goal
    choose(repository, temp_settings, mission)
    runner.run(mission.id)
    policy = Deliberation(repository)
    state = policy.state(repository.get_mission(mission.id))
    final_message_id = state['summary_message_ids'][-1]
    completed = client.get(f'/api/missions/{mission.id}').json()
    for key in ('messages', 'events'):
        finals = [m for m in completed[key] if m.get('metadata', {}).get('discussion_visibility') == 'final']
        assert [m['id'] for m in finals] == [final_message_id]
        assert finals[0]['metadata']['discussion_revision_reviewed'] is True
        assert len(finals[0]['metadata']['discussion_audit']['sources']) == 3
        assert len(finals[0]['metadata']['discussion_audit']['reviews']) == 2
    next_mission, _, _ = create_requested_mission(repository, temp_settings,
        CreateMissionRequest(goal='普通后续问题'), mission.conversation_id)
    conversation = client.get(f'/api/conversations/{mission.conversation_id}').json()
    assert conversation['latest_mission_detail']['mission']['id'] == next_mission.id
    for message in conversation['messages']:
        metadata = message['metadata']
        if message['role'] == 'agent':
            assert metadata['discussion_visibility'] == ('final' if message['id'] == final_message_id else 'internal')
            if message['id'] == final_message_id:
                assert len(metadata['discussion_audit']['sources']) == 3
                assert len(metadata['discussion_audit']['reviews']) == 2
        else:
            assert 'discussion_visibility' not in metadata
    assert all('discussion_visibility' not in m.model_dump() for m in repository.list_messages(mission.id))


def test_stopped_discussion_preview_does_not_show_internal_opinions(repository, temp_settings, skill_file):
    mission = create(repository, temp_settings)
    MissionRunner(repository, temp_settings, FakeCLIAdapter()).run(mission.id)
    policy = Deliberation(repository)
    state = policy.state(repository.get_mission(mission.id))
    policy.control(mission.id, DiscussionControlRequest(action='stop', request_id='stop',
        expected_revision=state['revision']), temp_settings)
    client = TestClient(create_app(temp_settings))
    summary = client.get('/api/conversations').json()[0]
    assert summary['latest_message_preview'] == mission.goal


def test_reviewed_final_can_remain_visible_while_awaiting_user_choice(repository, temp_settings, skill_file):
    mission = create(repository, temp_settings)
    runner = MissionRunner(repository, temp_settings, FakeCLIAdapter())
    runner.run(mission.id)
    choose(repository, temp_settings, mission)
    runner.run(mission.id)
    policy = Deliberation(repository)
    current = repository.get_mission(mission.id)
    state = policy.state(current)
    state.update(phase='finalizing', awaiting_reason='resolve_value_choice')
    policy.save(current, state, status=MissionStatus.AWAITING_INPUT)
    client = TestClient(create_app(temp_settings))
    for detail in (client.get(f'/api/missions/{mission.id}').json(),
                   client.get(f'/api/conversations/{mission.conversation_id}').json()):
        final = next(m for m in detail['messages'] if m['metadata'].get('discussion_visibility') == 'final')
        assert final['metadata']['discussion_pending_decision'] is True
