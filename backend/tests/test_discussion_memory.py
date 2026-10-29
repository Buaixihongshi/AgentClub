"""A finished discussion's conclusion can be kept in each participant's long-term memory."""
from uuid import uuid4

import pytest

from backend.app.api.mission_requests import DiscussionControlRequest
from backend.app.memory.assembly import assemble
from backend.app.memory.store import MemoryStore
from backend.app.orchestration.deliberation import Deliberation
from backend.app.orchestration.roundtable import DiscussionError
from backend.tests.test_quick_discussion import SplitFake, discuss, run


def control(repo, settings, mission, action, **fields):
    policy = Deliberation(repo)
    state = policy.state(repo.get_mission(mission.id))
    return policy.control(mission.id, DiscussionControlRequest(action=action, request_id=uuid4().hex,
                                                              expected_revision=state['revision'], **fields), settings)


def view(repo, mission):
    return Deliberation(repo).user_view(repo.get_mission(mission.id))


def test_synthesized_conclusion_is_saved_once_for_every_participant(repository, temp_settings):
    mission = discuss(repository, temp_settings)
    adapter = SplitFake()
    run(repository, temp_settings, mission, adapter)
    assert view(repository, mission)['memory_savable'] is False
    control(repository, temp_settings, mission, 'synthesize')
    run(repository, temp_settings, mission, adapter)
    assert view(repository, mission)['memory_savable'] is True

    control(repository, temp_settings, mission, 'save_memory')
    saved = view(repository, mission)
    assert set(saved['memory_entry_ids']) == {'chulan', 'baobao'} and saved['memory_savable'] is False
    store = MemoryStore(repository, temp_settings.memory_root)
    for agent_id, entry_id in saved['memory_entry_ids'].items():
        entry = store.read_entry(entry_id)
        assert entry['agent_id'] == agent_id and entry['tags'] == ['讨论结论']
        assert entry['title'].startswith('讨论结论：记忆要不要默认开启')
        assert '结论未经原参与者复核' in entry['body'] and '结论：综合' in entry['body']
        assert entry['source_refs'][0] == f'discussion:{mission.id}'
    events = [e.payload for e in repository.list_events_after(mission.id, 0) if e.event_type == 'memory.written']
    assert sorted(e['agent_id'] for e in events) == ['baobao', 'chulan']

    control(repository, temp_settings, mission, 'save_memory')
    assert len(store.read_entries('chulan')) == 1
    # The next conversation's prompt for this participant carries the conclusion.
    assert any('讨论结论' in item['title'] for item in assemble(
        repository, temp_settings.memory_root, None, 'chulan').memory_entries)


def test_user_decision_is_saved_with_each_voice(repository, temp_settings):
    mission = discuss(repository, temp_settings)
    run(repository, temp_settings, mission, SplitFake())
    control(repository, temp_settings, mission, 'user_decide', text='先不默认开启')
    control(repository, temp_settings, mission, 'save_memory')
    entry_id = view(repository, mission)['memory_entry_ids']['baobao']
    body = MemoryStore(repository, temp_settings.memory_root).read_entry(entry_id)['body']
    assert '用户决定：先不默认开启' in body
    assert 'chulan：默认开启' in body and 'baobao：暂缓开启' in body


def test_discussions_without_a_conclusion_cannot_be_saved(repository, temp_settings):
    running = discuss(repository, temp_settings)
    run(repository, temp_settings, running, SplitFake())
    with pytest.raises(DiscussionError, match='finished'):
        control(repository, temp_settings, running, 'save_memory')
    control(repository, temp_settings, running, 'finish')
    assert view(repository, running)['memory_savable'] is False
    with pytest.raises(DiscussionError, match='no conclusion'):
        control(repository, temp_settings, running, 'save_memory')
    assert MemoryStore(repository, temp_settings.memory_root).read_entries('chulan') == []
