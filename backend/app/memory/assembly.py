"""Unified context assembly: one place composes the durable context sections
(long-term memory, short-term checkpoint, model handover) shared by the runner
prompt paths and the MCP ``read_context`` answer.

Rendering is bounded and deterministic; the conversation excerpt and artifact
index keep their existing builders. Verification missions get no memory
sections at all (their reads stay isolated to the mission).
"""
from dataclasses import dataclass, field
from typing import Optional

from backend.app import host_rules
from backend.app.domain.agent_registry import AgentRegistry
from backend.app.memory import checkpoints as checkpoint_store
from backend.app.memory import handover as handover_store
from backend.app.memory.store import MemoryStore
from backend.app.storage.repository import Repository


@dataclass
class MemorySections:
    memory_entries: list = field(default_factory=list)
    memory_text: str = ''
    checkpoint: Optional[dict] = None
    checkpoint_text: str = ''
    handovers: list = field(default_factory=list)
    handover_text: str = ''


def assemble(repository: Repository, memory_root, conversation_id: Optional[str],
             agent_id: str, *, agent_registry: Optional[AgentRegistry] = None,
             memory_limit: int = 6, memory_chars: int = 2400,
             checkpoint_chars: int = 1600) -> MemorySections:
    sections = MemorySections()
    if not agent_id:
        return sections
    store = MemoryStore(repository, memory_root)
    brief = store.brief(agent_id, limit=memory_limit, max_chars=memory_chars)
    if brief:
        sections.memory_entries = brief
        lines = ['【长期记忆】（你的跨会话记忆；需要完整原文时用 memory(action=read, entry_id) 补读）：']
        for item in brief:
            lines.append('- {}（r{}）{}{}'.format(
                item['title'], item['revision'], item['text'], '…[截断]' if item['truncated'] else ''))
        sections.memory_text = '\n'.join(lines)
    if conversation_id:
        checkpoint = checkpoint_store.read(repository, conversation_id, agent_id)
        if checkpoint:
            sections.checkpoint = checkpoint
            text = checkpoint['text']
            truncated = len(text) > checkpoint_chars
            if truncated:
                head = text[:checkpoint_chars - 1]
                text = head + '…'
            refs = ''
            if checkpoint['source_message_ids']:
                refs = '（原文可用 read_context(message_ids=[...]) 补读）'
            sections.checkpoint_text = (
                '【短期检查点】（你此前在本会话保存的进展要点{}）：\n{}'.format(refs, text))
    handovers = handover_store.pending(repository, agent_id)
    if handovers:
        sections.handovers = handovers
        lines = [host_rules.MODEL_HANDOVER.text]
        for item in handovers:
            lines.append('- {} → {}（原因：{}，{}）'.format(
                item['previous'] or '未知', item['current'], _reason_label(item['reason']),
                item['created_at']))
        sections.handover_text = '\n'.join(lines)
    return sections


def _reason_label(reason: str) -> str:
    return {'settings': '设置变更', 'fallback': '执行回退'}.get(reason, reason)


def sections_into_prompt(sections: MemorySections) -> list[str]:
    """Prompt blocks in stable order for _build_prompt callers."""
    blocks = []
    for text in (sections.handover_text, sections.memory_text, sections.checkpoint_text):
        if text:
            blocks.append(text)
    return blocks
