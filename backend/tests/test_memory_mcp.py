"""MCP memory tools: ownership, actions, events, and store-not-configured."""
import pytest

from backend.app.domain.models import (
    Conversation, Mission, MissionKind, MissionStatus,
)
from backend.app.mcp.tools import MCPTools, MCPToolError
from backend.app.memory import checkpoints as checkpoint_store


def seed(repository, *, mission_id='m_mem', conversation_id='c_mem', verification=False):
    if not verification:
        repository.create_conversation(Conversation(id=conversation_id, title='记忆'))
    mission = Mission(id=mission_id, title='任务', goal='验证记忆工具',
                      kind=MissionKind.PROVIDER_VERIFICATION if verification else MissionKind.TASK,
                      conversation_id=None if verification else conversation_id,
                      status=MissionStatus.RUNNING)
    repository.create_mission(mission)
    return mission


def tools(repository, temp_settings, configured=True):
    return MCPTools(repository, temp_settings.artifact_root,
                    memory_root=temp_settings.memory_root if configured else None)


def test_write_read_revise_forget_roundtrip(repository, temp_settings):
    mission = seed(repository)
    kit = tools(repository, temp_settings)
    result = kit.memory(mission.id, 'chulan', 't1', 'claude', 'write',
                        {'title': '约定', 'body': '发布前必须跑检查。', 'tags': ['流程'],
                         'source_refs': ['msg_9']})
    entry_id = result['id']
    listing = kit.memory(mission.id, 'chulan', 't2', 'claude', 'read')
    assert listing['entries'][0]['id'] == entry_id
    full = kit.memory(mission.id, 'chulan', 't3', 'claude', 'read', {'entry_id': entry_id})
    assert full['body'] == '发布前必须跑检查。'
    revised = kit.memory(mission.id, 'chulan', 't4', 'claude', 'revise',
                         {'entry_id': entry_id, 'body': '发布前必须跑检查并复核。'})
    assert revised['revision'] == 2
    forgotten = kit.memory(mission.id, 'chulan', 't5', 'claude', 'forget', {'entry_id': entry_id})
    assert forgotten['status'] == 'forgotten'
    assert kit.memory(mission.id, 'chulan', 't6', 'claude', 'read')['entries'] == []
    event_types = {e.event_type for e in repository.list_events_after(mission.id, 0)}
    assert {'memory.written', 'memory.revised', 'memory.forgotten'} <= event_types


def test_agent_cannot_touch_other_agents_memory(repository, temp_settings):
    mission = seed(repository)
    kit = tools(repository, temp_settings)
    entry_id = kit.memory(mission.id, 'chulan', 't1', 'claude', 'write',
                          {'title': '私人', 'body': '只属于张楚岚'})['id']
    for action, data in [('read', {'entry_id': entry_id}),
                         ('revise', {'entry_id': entry_id, 'body': '篡改'}),
                         ('forget', {'entry_id': entry_id})]:
        with pytest.raises(MCPToolError, match='只能'):
            kit.memory(mission.id, 'baobao', 't2', 'codex', action, data)
    # Listing stays scoped to the caller.
    assert kit.memory(mission.id, 'baobao', 't3', 'codex', 'read')['entries'] == []


def test_memory_rejects_invalid_input(repository, temp_settings):
    mission = seed(repository)
    kit = tools(repository, temp_settings)
    with pytest.raises(MCPToolError, match='标题'):
        kit.memory(mission.id, 'chulan', 't', 'claude', 'write', {'title': '', 'body': 'x'})
    with pytest.raises(MCPToolError, match='不支持'):
        kit.memory(mission.id, 'chulan', 't', 'claude', 'destroy', {})


def test_memory_tool_requires_configured_store(repository, temp_settings):
    mission = seed(repository)
    kit = tools(repository, temp_settings, configured=False)
    with pytest.raises(MCPToolError, match='记忆存储未配置'):
        kit.memory(mission.id, 'chulan', 't', 'claude', 'read')


def test_save_checkpoint_roundtrip_and_validation(repository, temp_settings):
    mission = seed(repository)
    kit = tools(repository, temp_settings)
    saved = kit.save_checkpoint(mission.id, 'chulan', 't1', 'claude',
                                '已确认用 SQLite 存检查点。', ['msg_a', 'msg_b'])
    assert saved['source_message_ids'] == ['msg_a', 'msg_b']
    again = checkpoint_store.read(repository, 'c_mem', 'chulan')
    assert again['text'] == '已确认用 SQLite 存检查点。'
    # Rolling: the second save replaces the first within one transaction.
    kit.save_checkpoint(mission.id, 'chulan', 't2', 'claude', '更新后的要点。')
    assert checkpoint_store.read(repository, 'c_mem', 'chulan')['text'] == '更新后的要点。'
    events = [e.event_type for e in repository.list_events_after(mission.id, 0)]
    assert events.count('checkpoint.saved') == 2
    with pytest.raises(MCPToolError, match='长度'):
        kit.save_checkpoint(mission.id, 'chulan', 't3', 'claude', 'x' * 4001)


def test_save_checkpoint_rejected_for_verification(repository, temp_settings):
    mission = seed(repository, mission_id='m_v', verification=True)
    kit = tools(repository, temp_settings)
    with pytest.raises(MCPToolError, match='普通会话'):
        kit.save_checkpoint(mission.id, 'chulan', 't', 'claude', '要点')
