"""Model-switch handovers: durable notes that survive provider/model changes.

The host records a handover whenever an agent's effective (provider, model)
fingerprint changes (settings change detected at mission start, or an in-turn
fallback). The next prompt for that agent includes the handover once; the
acknowledgement is written only after the turn actually ran, so a crash before
that re-delivers it instead of losing it.
"""
import hashlib
import os

from backend.app.domain.models import utc_now

from backend.app.storage.repository import Repository


def _uid() -> str:
    return 'mh_' + hashlib.sha256(os.urandom(16)).hexdigest()[:12]


def fingerprint(agent) -> str:
    """Effective routing identity of a (possibly override-applied) profile."""
    provider = agent.default_provider_id or ''
    model = (agent.provider_models or {}).get(provider, '')
    return '{}:{}'.format(provider, model)


def describe(agent) -> str:
    provider = agent.default_provider_id or '?'
    model = (agent.provider_models or {}).get(provider) or '默认模型'
    return '{}/{}'.format(provider, model)


def record_mission_start(repository: Repository, agents, mission) -> list[dict]:
    """Compare each agent's routing fingerprint with the last recorded state.

    A mismatch means the user changed the role's provider/model (or connection
    routing) since its previous mission; that deserves an explicit handover so
    the new model instance knows it inherited ongoing context.
    """
    created = []
    with repository._transaction(immediate=True) as db:
        for agent in agents:
            current = fingerprint(agent)
            row = db.execute('SELECT provider_id, model FROM model_state WHERE agent_id=?',
                             (agent.id,)).fetchone()
            previous = '{}:{}'.format(row['provider_id'], row['model']) if row else None
            db.execute(
                'INSERT INTO model_state (agent_id, provider_id, model, updated_at)'
                ' VALUES (?, ?, ?, ?) ON CONFLICT(agent_id) DO UPDATE SET'
                ' provider_id=excluded.provider_id, model=excluded.model,'
                ' updated_at=excluded.updated_at',
                (agent.id, *current.split(':', 1), utc_now().isoformat()))
            if previous is None or previous == current:
                # First sight of the role records a baseline; an unchanged
                # fingerprint has nothing to hand over.
                continue
            handover_id = _uid()
            db.execute(
                'INSERT INTO model_handovers (id, agent_id, reason, previous, current,'
                ' mission_id, conversation_id, acknowledged, created_at)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)',
                (handover_id, agent.id, 'settings', previous, current,
                 mission.id, mission.conversation_id, utc_now().isoformat()))
            created.append({'id': handover_id, 'agent_id': agent.id, 'reason': 'settings',
                            'previous': previous, 'current': current})
    return created


def record_fallback(repository: Repository, mission, agent_id: str, previous: str,
                    current: str) -> dict | None:
    """A turn fell back to another provider mid-mission."""
    if previous == current:
        return None
    # Fallback does not rewrite model_state: that fingerprint tracks the
    # user-configured routing; a fallback is per-turn and the next mission
    # start must not duplicate it as a settings change.
    handover_id = _uid()
    with repository._transaction(immediate=True) as db:
        db.execute(
            'INSERT INTO model_handovers (id, agent_id, reason, previous, current,'
            ' mission_id, conversation_id, acknowledged, created_at)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)',
            (handover_id, agent_id, 'fallback', previous, current, mission.id,
             mission.conversation_id, utc_now().isoformat()))
    return {'id': handover_id, 'agent_id': agent_id, 'reason': 'fallback',
            'previous': previous, 'current': current}


def pending(repository: Repository, agent_id: str) -> list[dict]:
    with repository._transaction() as db:
        rows = db.execute(
            'SELECT * FROM model_handovers WHERE agent_id=? AND acknowledged=0'
            ' ORDER BY id', (agent_id,)).fetchall()
    return [_row(row) for row in rows]


def acknowledge(repository: Repository, agent_id: str) -> int:
    with repository._transaction(immediate=True) as db:
        cursor = db.execute(
            'UPDATE model_handovers SET acknowledged=1, acknowledged_at=?'
            ' WHERE agent_id=? AND acknowledged=0', (utc_now().isoformat(), agent_id))
        return cursor.rowcount


def list_recent(repository: Repository, agent_id: str | None = None, limit: int = 20) -> list[dict]:
    query = 'SELECT * FROM model_handovers'
    params: tuple = ()
    if agent_id:
        query += ' WHERE agent_id=?'
        params = (agent_id,)
    query += ' ORDER BY id DESC LIMIT ?'
    params = params + (int(limit),)
    with repository._transaction() as db:
        rows = db.execute(query, params).fetchall()
    return [_row(row) for row in rows]


def _row(row) -> dict:
    return {'id': row['id'], 'agent_id': row['agent_id'], 'reason': row['reason'],
            'previous': row['previous'], 'current': row['current'],
            'mission_id': row['mission_id'], 'conversation_id': row['conversation_id'],
            'acknowledged': bool(row['acknowledged']), 'created_at': row['created_at'],
            'acknowledged_at': row['acknowledged_at']}
