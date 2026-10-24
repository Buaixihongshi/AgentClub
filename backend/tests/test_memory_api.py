"""User management API: view, revise, forget/restore/purge, rebuild, checkpoints."""
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.memory import checkpoints as checkpoint_store
from backend.app.memory.store import MemoryStore


def client(repository, temp_settings):
    return TestClient(create_app(temp_settings))


def test_overview_lists_agents_and_consistency(repository, temp_settings):
    store = MemoryStore(repository, temp_settings.memory_root)
    store.write('chulan', title='事实', body='本地单用户部署。')
    response = client(repository, temp_settings).get('/api/memory/overview')
    assert response.status_code == 200
    body = response.json()
    assert body['consistency']['files'] == 1
    chulan = next(a for a in body['agents'] if a['id'] == 'chulan')
    assert chulan['active'] == 1 and chulan['forgotten'] == 0


def test_entry_lifecycle_through_api(repository, temp_settings):
    store = MemoryStore(repository, temp_settings.memory_root)
    entry = store.write('wangye', title='草稿', body='初稿内容')
    api = client(repository, temp_settings)
    listing = api.get('/api/memory/agents/wangye/entries').json()['entries']
    assert listing[0]['id'] == entry['id']

    revised = api.put('/api/memory/entries/{}'.format(entry['id']),
                      json={'body': '用户修正后的内容'})
    assert revised.status_code == 200 and revised.json()['revision'] == 2

    forgotten = api.post('/api/memory/entries/{}/forget'.format(entry['id']))
    assert forgotten.json()['status'] == 'forgotten'
    visible = api.get('/api/memory/agents/wangye/entries').json()['entries']
    assert visible == []
    with_forgotten = api.get('/api/memory/agents/wangye/entries',
                             params={'include_forgotten': True}).json()['entries']
    assert with_forgotten[0]['status'] == 'forgotten'

    restored = api.post('/api/memory/entries/{}/restore'.format(entry['id']))
    assert restored.json()['status'] == 'active'

    purged = api.delete('/api/memory/entries/{}'.format(entry['id']))
    assert purged.json()['purged'] is True
    missing = api.get('/api/memory/entries/{}'.format(entry['id']))
    assert missing.status_code == 404


def test_rebuild_recreates_index_from_files(repository, temp_settings):
    store = MemoryStore(repository, temp_settings.memory_root)
    store.write('baobao', title='审查要点', body='P1 问题必须优先修复。')
    with repository._transaction() as db:
        db.execute('DELETE FROM memory_entries')
    report = client(repository, temp_settings).post('/api/memory/rebuild').json()
    assert report['files'] == 1
    entries = client(repository, temp_settings).get('/api/memory/agents/baobao/entries').json()['entries']
    assert entries[0]['title'] == '审查要点'


def test_checkpoint_listing_and_clearing(repository, temp_settings):
    checkpoint_store.save(repository, 'c1', 'chulan', '要点 A', [])
    checkpoint_store.save(repository, 'c1', 'baobao', '要点 B', [])
    api = client(repository, temp_settings)
    rows = api.get('/api/memory/conversations/c1/checkpoints').json()['checkpoints']
    assert {row['agent_id'] for row in rows} == {'chulan', 'baobao'}
    removed = api.delete('/api/memory/conversations/c1/checkpoints',
                         params={'agent_id': 'chulan'}).json()['removed']
    assert removed == 1
    remaining = api.get('/api/memory/conversations/c1/checkpoints').json()['checkpoints']
    assert [row['agent_id'] for row in remaining] == ['baobao']


def test_handover_listing(repository, temp_settings):
    from backend.app.domain.models import Conversation, Mission, MissionStatus
    repository.create_conversation(Conversation(id='c2', title='x'))
    mission = Mission(id='m2', title='t', goal='g', conversation_id='c2',
                      status=MissionStatus.PENDING)
    repository.create_mission(mission)
    from backend.app.memory import handover as handover_store
    from backend.app.orchestration.runner import MissionRunner
    runner = MissionRunner(repository, temp_settings, adapter=None)
    handover_store.record_mission_start(repository, runner.agents, mission)
    profile = next(a for a in runner.agents if a.id == 'chulan')
    handover_store.record_mission_start(
        repository, [profile.model_copy(update={'default_provider_id': 'codex'})], mission)
    rows = client(repository, temp_settings).get('/api/memory/handovers',
                                                 params={'agent_id': 'chulan'}).json()['handovers']
    assert rows and rows[0]['agent_id'] == 'chulan'
