"""Durable, fenced execution grants for long-lived CLI workers.

The host issues capabilities; model arguments never select a mission or speaker.
All tool authorization, database effects and successful operation receipts share
one SQLite write transaction. Native CLI filesystem effects are not transactional.
"""
import hashlib
import json
import secrets
import time
from typing import Callable

from backend.app.domain.models import MissionStatus, utc_now
from backend.app.mcp.tools import MCPToolError


class ExecutionLedger:
    def __init__(self, repository):
        self.repo = repository
        with self.repo._transaction(immediate=True) as db:
            # execute individually: executescript would commit the caller's txn.
            db.execute("""CREATE TABLE IF NOT EXISTS execution_workers (
                worker_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
                state TEXT NOT NULL, active_hash TEXT, data TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS execution_grants (
                token_hash TEXT PRIMARY KEY, worker_id TEXT NOT NULL,
                generation INTEGER NOT NULL, mission_id TEXT NOT NULL,
                agent_id TEXT NOT NULL, turn_id TEXT NOT NULL, provider_id TEXT NOT NULL,
                receipt_id TEXT NOT NULL, state TEXT NOT NULL, expires_at REAL NOT NULL,
                data TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS execution_operations (
                mission_id TEXT NOT NULL, agent_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                tool TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                result TEXT NOT NULL,
                PRIMARY KEY (mission_id, agent_id, turn_id, tool, operation_id))""")

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def register(self, worker_id, **metadata):
        with self.repo._transaction(immediate=True) as db:
            db.execute("INSERT INTO execution_workers VALUES (?, 0, 'ready', NULL, ?)",
                       (worker_id, json.dumps(metadata)))

    def begin(self, worker_id, mission_id, agent_id, turn_id, provider_id, *, timeout, **metadata):
        token = secrets.token_urlsafe(32)
        token_hash = self.digest(token)
        receipt = 'mcp_' + secrets.token_hex(16)
        with self.repo._transaction(immediate=True) as db:
            worker = db.execute('SELECT * FROM execution_workers WHERE worker_id=?', (worker_id,)).fetchone()
            mission = self.repo.get_mission(mission_id)
            if not worker or worker['state'] != 'ready' or worker['active_hash']:
                raise MCPToolError('Worker is not ready for another turn')
            if not mission or mission.status not in {MissionStatus.PENDING, MissionStatus.RUNNING}:
                raise MCPToolError('Mission is no longer active')
            generation = worker['generation'] + 1
            db.execute('INSERT INTO execution_grants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (token_hash, worker_id, generation, mission_id, agent_id, turn_id, provider_id,
                        receipt, 'active', time.time() + timeout, json.dumps(metadata)))
            db.execute("UPDATE execution_workers SET generation=?, state='busy', active_hash=? WHERE worker_id=?",
                       (generation, token_hash, worker_id))
            self.repo.add_event(mission_id, 'execution.started', {
                'turn_id': turn_id, 'agent_id': agent_id, 'provider_id': provider_id,
                'worker_id': worker_id, 'generation': generation,
            })
        return token, receipt

    def _grant(self, db, worker_id, token, *, host_renewal=False):
        if not isinstance(token, str) or not token:
            raise MCPToolError('Missing context_token; use the host-issued token for this turn')
        grant = db.execute('SELECT g.*, w.state AS worker_state, w.active_hash, w.generation AS worker_generation '
                           'FROM execution_grants g JOIN execution_workers w USING(worker_id) '
                           'WHERE g.token_hash=? AND g.worker_id=?', (self.digest(token), worker_id)).fetchone()
        if (not grant or grant['state'] != 'active' or grant['worker_state'] != 'busy'
                or grant['active_hash'] != grant['token_hash']
                or grant['worker_generation'] != grant['generation']
                or (not host_renewal and grant['expires_at'] <= time.time())):
            raise MCPToolError('Execution grant expired; stale worker/turn is not authorized')
        mission = self.repo.get_mission(grant['mission_id'])
        if not mission or mission.status not in {MissionStatus.PENDING, MissionStatus.RUNNING}:
            raise MCPToolError('Mission is no longer active')
        return dict(grant)

    def renew(self, worker_id, token, *, timeout):
        """Host-only renewal after a progress check; never revives a fenced generation.

        A scheduled check may be polled just after the previous expiry. Model
        tool calls still enforce expiry; only this host API can extend it.
        """
        if timeout <= 0:
            raise MCPToolError('Cannot renew an expired turn deadline')
        with self.repo._transaction(immediate=True) as db:
            grant = self._grant(db, worker_id, token, host_renewal=True)
            db.execute('UPDATE execution_grants SET expires_at=? WHERE token_hash=?',
                       (time.time() + timeout, grant['token_hash']))

    def call(self, worker_id, token, tool, arguments, operation_id, handler: Callable):
        with self.repo._transaction(immediate=True) as db:
            grant = self._grant(db, worker_id, token)
            if operation_id is not None:
                if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 120:
                    raise MCPToolError('operation_id must be a non-empty string of at most 120 characters')
                # Exactly one canonical final publication per logical turn,
                # including a retry that supplies a different operation id.
                if tool == 'publish_message':
                    operation_id = 'final'
                fingerprint = hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                key = (grant['mission_id'], grant['agent_id'], grant['turn_id'], tool, operation_id)
                prior = db.execute('SELECT * FROM execution_operations WHERE mission_id=? AND agent_id=? '
                                   'AND turn_id=? AND tool=? AND operation_id=?', key).fetchone()
                if prior:
                    if prior['fingerprint'] != fingerprint:
                        raise MCPToolError('operation_id was already used with different arguments; reconcile saved results')
                    return json.loads(prior['result'])
            result = handler(grant)
            if time.time() >= grant['expires_at']:
                raise MCPToolError('Execution grant expired while the tool was running; reconcile native file effects')
            if operation_id is not None and not result.get('isError'):
                db.execute('INSERT INTO execution_operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (*key, fingerprint, json.dumps(result, ensure_ascii=False)))
            return result

    def checkpoint(self, worker_id, **updates):
        with self.repo._transaction(immediate=True) as db:
            row = db.execute('SELECT data FROM execution_workers WHERE worker_id=?', (worker_id,)).fetchone()
            if row:
                data = json.loads(row['data']); data.update(updates, updated_at=time.time())
                db.execute('UPDATE execution_workers SET data=? WHERE worker_id=?', (json.dumps(data), worker_id))

    def recovery_hint(self, conversation_id, agent_id):
        if not conversation_id:
            return ''
        with self.repo._transaction() as db:
            row = db.execute("SELECT e.event_type, e.payload, w.data FROM events e JOIN missions m ON m.id=e.mission_id "
                             "LEFT JOIN execution_workers w ON w.worker_id=json_extract(e.payload, '$.worker_id') "
                             "WHERE e.event_type IN ('execution.interrupted', 'execution.completed') "
                             "AND json_extract(m.data, '$.conversation_id')=? "
                             "AND json_extract(e.payload, '$.agent_id')=? ORDER BY e.id DESC LIMIT 1",
                             (conversation_id, agent_id)).fetchone()
        if not row or row['event_type'] == 'execution.completed':
            return ''
        metadata = json.loads(row['data'] or '{}')
        last_action = metadata.get('last_summary', '未记录到可核对的工具进度')
        return ('\n【中断恢复】本会话该角色有过中断记录，最近保存的动作摘要：' + last_action +
                '。先核对现有文件、Git 差异和已保存消息，再继续用户当前请求；不得假定此前操作未发生，也不得整轮重放。\n')

    def release(self, worker_id, token, *, outcome):
        with self.repo._transaction(immediate=True) as db:
            token_hash = self.digest(token)
            db.execute("UPDATE execution_grants SET state=? WHERE worker_id=? AND token_hash=? AND state='active'",
                       (outcome, worker_id, token_hash))
            db.execute("UPDATE execution_workers SET state='ready', active_hash=NULL WHERE worker_id=? AND active_hash=?",
                       (worker_id, token_hash))

    def revoke(self, worker_id, reason):
        """Fence first, then the caller terminates the process tree."""
        with self.repo._transaction(immediate=True) as db:
            grants = db.execute("SELECT * FROM execution_grants WHERE worker_id=? AND state='active'", (worker_id,)).fetchall()
            db.execute("UPDATE execution_grants SET state='interrupted' WHERE worker_id=? AND state='active'", (worker_id,))
            db.execute("UPDATE execution_workers SET state='closed', active_hash=NULL WHERE worker_id=?", (worker_id,))
            for grant in grants:
                self.repo.add_event(grant['mission_id'], 'execution.interrupted', {
                    'worker_id': worker_id, 'turn_id': grant['turn_id'], 'agent_id': grant['agent_id'],
                    'provider_id': grant['provider_id'], 'reason': reason, 'requires_reconciliation': True,
                })

    def recover(self):
        """Single-backend startup: revoke old grants, never replay native effects."""
        with self.repo._transaction(immediate=True) as db:
            workers = db.execute("SELECT worker_id FROM execution_workers WHERE state!='closed'").fetchall()
            for row in workers:
                worker_id = row['worker_id']
                grants = db.execute("SELECT * FROM execution_grants WHERE worker_id=? AND state='active'", (worker_id,)).fetchall()
                self.revoke(worker_id, 'service_restart')
                for grant in grants:
                    mission = self.repo.get_mission(grant['mission_id'])
                    if not mission or mission.room_mode == 'roundtable' or mission.status not in {MissionStatus.PENDING, MissionStatus.RUNNING}:
                        continue
                    error = '执行服务重启；已保留进度，请核对工作区后继续，未自动重放操作。'
                    self.repo.add_event(mission.id, 'turn.blocked', {
                        'turn_id': grant['turn_id'], 'agent_id': grant['agent_id'], 'provider_id': grant['provider_id'],
                        'attempt': 1, 'duration_ms': 0, 'error': error,
                    })
                    self.repo.update_mission(mission.model_copy(update={'status': MissionStatus.BLOCKED, 'updated_at': utc_now()}))
                    self.repo.add_event(mission.id, 'status', {'status': 'blocked', 'summary': error})
