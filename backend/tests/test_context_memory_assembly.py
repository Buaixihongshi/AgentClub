"""Unified assembly drives both the runner prompt and read_context."""
from backend.app.adapters.base import TurnOutcome
from backend.app.domain.models import (
    Conversation, Message, MessageRole, Mission, MissionKind, MissionStatus, TurnStatus,
)
from backend.app.memory import checkpoints as checkpoint_store
from backend.app.memory import handover as handover_store
from backend.app.memory.assembly import assemble, sections_into_prompt
from backend.app.memory.store import MemoryStore
from backend.app.mcp.tools import MCPTools
from backend.app.orchestration.runner import MissionRunner


def seed_conversation(repository, *, mission_id='m_asm', conversation_id='c_asm'):
    repository.create_conversation(Conversation(id=conversation_id, title='记忆会话'))
    mission = Mission(id=mission_id, title='任务', goal='跨会话记住偏好',
                      conversation_id=conversation_id, status=MissionStatus.PENDING)
    repository.create_mission(mission)
    repository.add_message(Message(id='msg_long', mission_id=mission_id,
                                   conversation_id=conversation_id, role=MessageRole.USER,
                                   content='关键条件' + '详细背景' * 300))
    repository.add_message(Message(id='msg_short', mission_id=mission_id,
                                   conversation_id=conversation_id, role=MessageRole.AGENT,
                                   agent_id='chulan', content='收到，先按离线方案推进。'))
    return mission


def test_assemble_combines_memory_checkpoint_and_handover(repository, temp_settings):
    mission = seed_conversation(repository)
    store = MemoryStore(repository, temp_settings.memory_root)
    store.write('chulan', title='用户偏好', body='用户偏好简洁中文回复。')
    checkpoint_store.save(repository, 'c_asm', 'chulan', '已确认方案 A；下一步验证导出。', ['msg_long'])
    agents = MissionRunner(repository, temp_settings, adapter=None).agents
    profile = next(a for a in agents if a.id == 'chulan')
    with repository._transaction() as db:
        db.execute('DELETE FROM model_state')
    handover_store.record_mission_start(repository, [profile.model_copy(
        update={'default_provider_id': 'codex'})], mission)
    handover_store.record_mission_start(repository, [profile.model_copy(
        update={'default_provider_id': 'claude'})], mission)

    sections = assemble(repository, temp_settings.memory_root, 'c_asm', 'chulan')
    assert '用户偏好' in sections.memory_text and '简洁中文回复' in sections.memory_text
    assert '已确认方案 A' in sections.checkpoint_text
    assert '原文可用 read_context' in sections.checkpoint_text
    assert '【模型交接】' in sections.handover_text and '设置变更' in sections.handover_text
    blocks = sections_into_prompt(sections)
    assert blocks == [sections.handover_text, sections.memory_text, sections.checkpoint_text]


def test_assemble_without_any_state_is_empty(repository, temp_settings):
    seed_conversation(repository)
    sections = assemble(repository, temp_settings.memory_root, 'c_asm', 'wangye')
    assert sections.memory_text == '' and sections.checkpoint_text == '' and sections.handover_text == ''


def test_checkpoint_text_is_bounded(repository, temp_settings):
    seed_conversation(repository)
    checkpoint_store.save(repository, 'c_asm', 'chulan', '长' * 3000, [])
    sections = assemble(repository, temp_settings.memory_root, 'c_asm', 'chulan',
                        checkpoint_chars=100)
    assert len(sections.checkpoint_text.splitlines()[-1]) <= 100


def test_runner_prompt_includes_sections_each_turn(repository, temp_settings):
    mission = seed_conversation(repository, mission_id='m_prompt')
    store = MemoryStore(repository, temp_settings.memory_root)
    store.write('chulan', title='项目事实', body='部署目标是本地单用户。')
    prompts = []

    class Adapter:
        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            prompts.append((agent_id, prompt))
            tools.publish_message(mission_id, agent_id, turn_id, '好的', provider_id)
            return TurnOutcome(status=TurnStatus.COMPLETED)

    MissionRunner(repository, temp_settings, adapter=Adapter()).run(mission.id)
    chulan_prompts = [p for agent, p in prompts if agent == 'chulan']
    assert chulan_prompts
    assert all('【长期记忆】' in p and '项目事实' in p for p in chulan_prompts)


def test_read_context_returns_memory_sections_and_full_originals(repository, temp_settings):
    mission = seed_conversation(repository, mission_id='m_read')
    store = MemoryStore(repository, temp_settings.memory_root)
    store.write('chulan', title='偏好', body='回复保持简洁。')
    checkpoint_store.save(repository, 'c_asm', 'chulan', '等待用户确认方案。', ['msg_long'])
    tools = MCPTools(repository, temp_settings.artifact_root, memory_root=temp_settings.memory_root)
    result = tools.read_context('m_read', agent_id='chulan', turn_id='t1')
    assert result['memory']['entries'][0]['title'] == '偏好'
    assert result['checkpoint']['text'] == '等待用户确认方案。'
    # 原文补读：按 ID 取未截断全文。
    full = tools.read_context('m_read', agent_id='chulan', turn_id='t2',
                              message_ids=['msg_long', 'msg_missing'])
    contents = {m['id']: m['content'] for m in full['messages']}
    assert contents['msg_long'] == '关键条件' + '详细背景' * 300
    assert full['missing_message_ids'] == ['msg_missing']


def test_verification_mission_gets_no_memory_sections(repository, temp_settings):
    mission = Mission(id='m_verify', title='验证 codex 接入', goal='接入验证 codex',
                      kind=MissionKind.PROVIDER_VERIFICATION, status=MissionStatus.PENDING)
    repository.create_mission(mission)
    store = MemoryStore(repository, temp_settings.memory_root)
    store.write('chulan', title='偏好', body='不应出现在验证任务。')
    tools = MCPTools(repository, temp_settings.artifact_root, memory_root=temp_settings.memory_root)
    result = tools.read_context('m_verify', agent_id='chulan', turn_id='t')
    assert 'memory' not in result and 'checkpoint' not in result
