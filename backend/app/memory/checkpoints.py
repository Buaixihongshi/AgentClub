"""Short-term, per-conversation rolling checkpoints in SQLite.

A checkpoint is agent-authored structured state ("what holds right now"), not a
model-generated summary. One rolling row per (conversation, agent); writes are
single transactions, so an interrupted process leaves no half checkpoint.
"""
import json

from backend.app.domain.models import utc_now

from backend.app.storage.repository import Repository

MAX_TEXT = 4000
MAX_REFS = 40


class CheckpointError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _validate(text: str, source_message_ids) -> tuple[str, list[str]]:
    text = str(text or '').strip()
    if not (1 <= len(text) <= MAX_TEXT):
        raise CheckpointError('检查点文本长度须在 1–{} 字符之间'.format(MAX_TEXT))
    if not isinstance(source_message_ids, (list, tuple)):
        source_message_ids = []
    refs = [str(r).strip() for r in source_message_ids if str(r).strip()]
    if len(refs) > MAX_REFS:
        raise CheckpointError('引用消息数最多 {} 条'.format(MAX_REFS))
    if any(',' in r or len(r) > 120 for r in refs):
        raise CheckpointError('引用消息 ID 无效')
    return text, refs


def save(repository: Repository, conversation_id: str, agent_id: str, text: str,
         source_message_ids=(), cursor_event_id: int = 0) -> dict:
    text, refs = _validate(text, source_message_ids)
    with repository._transaction(immediate=True) as db:
        db.execute(
            'INSERT INTO context_checkpoints (conversation_id, agent_id, text,'
            ' source_message_ids, cursor_event_id, updated_at) VALUES (?, ?, ?, ?, ?, ?)'
            ' ON CONFLICT(conversation_id, agent_id) DO UPDATE SET text=excluded.text,'
            ' source_message_ids=excluded.source_message_ids,'
            ' cursor_event_id=excluded.cursor_event_id, updated_at=excluded.updated_at',
            (conversation_id, agent_id, text, json.dumps(refs), int(cursor_event_id),
             utc_now().isoformat()))
    return read(repository, conversation_id, agent_id)


def read(repository: Repository, conversation_id: str, agent_id: str) -> dict | None:
    with repository._transaction() as db:
        row = db.execute(
            'SELECT * FROM context_checkpoints WHERE conversation_id=? AND agent_id=?',
            (conversation_id, agent_id)).fetchone()
    return _row(row) if row else None


def list_for_conversation(repository: Repository, conversation_id: str) -> list[dict]:
    with repository._transaction() as db:
        rows = db.execute(
            'SELECT * FROM context_checkpoints WHERE conversation_id=? ORDER BY updated_at DESC',
            (conversation_id,)).fetchall()
    return [_row(row) for row in rows]


def clear(repository: Repository, conversation_id: str, agent_id: str | None = None) -> int:
    with repository._transaction(immediate=True) as db:
        if agent_id is None:
            cursor = db.execute('DELETE FROM context_checkpoints WHERE conversation_id=?',
                                (conversation_id,))
        else:
            cursor = db.execute(
                'DELETE FROM context_checkpoints WHERE conversation_id=? AND agent_id=?',
                (conversation_id, agent_id))
        return cursor.rowcount


def _row(row) -> dict:
    try:
        refs = json.loads(row['source_message_ids'])
    except (TypeError, ValueError):
        refs = []
    return {'conversation_id': row['conversation_id'], 'agent_id': row['agent_id'],
            'text': row['text'], 'source_message_ids': refs,
            'cursor_event_id': row['cursor_event_id'], 'updated_at': row['updated_at']}
