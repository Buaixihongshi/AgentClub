"""Discussion, tasks and maintenance are optional extensions, off by default."""
import pytest
from fastapi.testclient import TestClient

from backend.app.domain.agent_registry import AgentProfile
from backend.app.domain.models import Conversation, Mission
from backend.app.main import create_app
from backend.app.mcp import server
from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.orchestration.runner import _build_prompt
from backend.app.settings import EXTENSION_DISABLED, Settings, get_settings, snapshot_settings
from backend.app.storage import hub_settings


@pytest.fixture()
def core_settings(temp_settings):
    return temp_settings.model_copy(update={
        'discussion_enabled': False, 'tasks_enabled': False, 'maintenance_enabled': False,
    })


@pytest.fixture()
def core_client(core_settings):
    return TestClient(create_app(core_settings))


def _chat_mission(repository):
    repository.create_conversation(Conversation(id='c_core', title='core'))
    mission = Mission(id='m_core', title='聊聊', goal='聊聊', conversation_id='c_core')
    repository.create_mission(mission)
    return mission


def test_extensions_default_off(monkeypatch, tmp_path):
    for name in ('YIREN_DISCUSSION_ENABLED', 'YIREN_TASKS_ENABLED', 'YIREN_MAINTENANCE_ENABLED'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('YIREN_DATABASE_PATH', str(tmp_path / 'yirenhub.sqlite3'))
    settings = get_settings()
    assert (settings.discussion_enabled, settings.tasks_enabled, settings.maintenance_enabled) == (False, False, False)
    model = Settings()
    assert (model.discussion_enabled, model.tasks_enabled, model.maintenance_enabled) == (False, False, False)


def test_disabled_extensions_reject_creation(core_client):
    discussion = core_client.post('/api/missions', json={
        'goal': '要不要做？', 'room_mode': 'roundtable',
        'discussion': {'participant_ids': ['chulan', 'baobao', 'wangye']}})
    assert discussion.status_code == 409
    assert discussion.json()['detail'] == EXTENSION_DISABLED['discussion']

    discuss_skill = core_client.post('/api/missions', json={'goal': '要不要做？', 'skill': 'discuss'})
    assert discuss_skill.status_code == 409

    work = core_client.post('/api/missions', json={'goal': '写一份方案', 'work_mode': 'document'})
    assert work.status_code == 409
    assert work.json()['detail'] == EXTENSION_DISABLED['tasks']

    conversation = core_client.post('/api/conversations', json={}).json()
    command = core_client.post(f"/api/conversations/{conversation['id']}/work",
                               json={'action': 'pause', 'expected_revision': 0})
    assert command.status_code == 409
    task = core_client.post('/api/tasks', json={
        'title': '方案', 'owner_id': 'chulan', 'operation_id': 'op-1',
        'agreement': {'source': 's', 'goal': 'g', 'scope': 's', 'delivery': 'document',
                      'criteria': [{'id': 'AC-1', 'text': 't'}]}})
    assert task.status_code == 409
    # Reading stays available so existing records can still be inspected.
    assert core_client.get('/api/tasks').status_code == 200


def test_settings_toggle_applies_without_restart(core_client, core_settings):
    view = core_client.get('/api/hub/settings').json()['extensions']
    assert view == {name: {'value': False, 'source': 'default'} for name in ('discussion', 'tasks', 'maintenance')}

    response = core_client.put('/api/hub/settings', json={'extensions': {'tasks': True}})
    assert response.status_code == 200, response.text
    assert response.json()['extensions']['tasks'] == {'value': True, 'source': 'user'}
    saved = hub_settings.load(hub_settings.default_path(core_settings))
    assert saved['runtime']['tasks_enabled'] is True
    live = snapshot_settings(core_settings)
    assert live.tasks_enabled and not live.discussion_enabled


def test_core_mcp_tool_list_hides_shared_work(core_settings, repository):
    mission = _chat_mission(repository)

    def names(tasks_enabled):
        tools = MCPTools(repository, core_settings.artifact_root, tasks_enabled=tasks_enabled)
        ctx = server._Context(mission.id, 'chulan', 'turn_1', 'fake', tools)
        return {d['name'] for d in server._handle_tools_list(ctx, {})['tools']}

    assert names(False) == {'publish_message', 'memory', 'save_checkpoint', 'read_context', 'write_artifact',
                            'read_artifact', 'update_status', 'invoke_agent', 'log_thought'}
    assert 'shared_work' in names(True)


def test_core_mcp_tools_refuse_shared_work_and_omit_board(core_settings, repository):
    mission = _chat_mission(repository)
    tools = MCPTools(repository, core_settings.artifact_root, tasks_enabled=False)
    with pytest.raises(MCPToolError, match='任务扩展未开启'):
        tools.shared_work(mission.id, 'chulan', 'turn_1', 'fake')
    assert 'shared_work' not in tools.read_context(mission.id)
    assert 'shared_work' in MCPTools(repository, core_settings.artifact_root).read_context(mission.id)


def test_core_prompt_omits_task_proposal_hint():
    mission = Mission(id='m_1', title='聊聊', goal='聊聊', conversation_id='c_1')
    agent = AgentProfile(id='chulan', display_name='张楚岚', role='伙伴', persona='通透', default_provider_id='fake')
    assert 'propose_work' not in _build_prompt(mission, '回应', agent, tasks_enabled=False)
    assert 'propose_work' in _build_prompt(mission, '回应', agent, tasks_enabled=True)


def test_subprocess_env_carries_tasks_flag(core_settings):
    from backend.app.adapters.mcp_subprocess import MCPSubprocessAdapter
    adapter = MCPSubprocessAdapter('codex', db_path=core_settings.database_path,
                                   artifact_root=core_settings.artifact_root)
    assert adapter._mcp_env('m', 'a', 't', 'codex', tasks_enabled=False)['YIREN_MCP_TASKS_ENABLED'] == '0'
    assert adapter._mcp_env('m', 'a', 't', 'codex')['YIREN_MCP_TASKS_ENABLED'] == '1'


def test_dispatcher_skips_saved_work_when_tasks_disabled(core_settings, repository, monkeypatch):
    from backend.app.api import shared_work
    from backend.app.work import service

    def fail(*_args, **_kwargs):
        raise AssertionError('disabled tasks must not be claimed')

    monkeypatch.setattr(service.SharedWork, 'claim', fail)
    shared_work.drain(repository, core_settings, 'c_any')


def test_maintenance_disabled_rejects_runs(core_client):
    response = core_client.post('/api/maintenance/runs', json={'repository_id': 'rr_missing', 'goal': 'fix'})
    assert response.status_code == 409
    assert response.json()['detail'] == EXTENSION_DISABLED['maintenance']
