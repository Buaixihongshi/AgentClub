"""Independent task resources over the existing durable execution kernel.

The user-facing identity is a root work ID, not a conversation. A private
execution conversation preserves MCP, checkpoint and evidence compatibility.
"""
import json
from copy import deepcopy

from pydantic import Field

from backend.app.domain.models import Conversation
from backend.app.work.adaptive import AdaptiveWork, digest
from backend.app.work.adaptive_models import NewWork
from backend.app.work.service import WorkError, SharedWork, now, uid
from backend.app.work.workflow import Workflow


class CreateTask(NewWork):
    orchestration: Workflow = Field(default_factory=Workflow)
    source_conversation_id: str | None = Field(default=None, min_length=1, max_length=120)
    operation_id: str = Field(min_length=1, max_length=120)
    paused: bool = True


TASK_ACTIONS = {'pause', 'resume', 'cancel', 'retry', 'adjust_budget', 'decide',
                'revise_work', 'grant_execution', 'accept_transfer',
                'retry_environment', 'start_preview', 'stop_preview', 'configure_workflow'}
LEGACY_ACTIONS = {'propose_contract', 'create_task', 'activate_contract', 'start', 'pause', 'retry'}
LEGACY_PREFIX = 'legacy:'


class Tasks:
    def __init__(self, repository, workspace_root=None, check_timeout=300):
        self.repo = repository
        self.work = AdaptiveWork(repository, workspace_root, check_timeout)

    def _boards(self, db):
        for row in db.execute('SELECT data FROM work_boards').fetchall():
            board = json.loads(row['data'])
            if board.get('protocol') == 'adaptive_v1':
                yield board

    def _locate(self, task_id):
        with self.repo._transaction() as db:
            for board in self._boards(db):
                root = next((t for t in board['tasks'] if t['id'] == task_id and not t['parent_id']), None)
                if root:
                    return board, root
        raise WorkError('任务不存在', 404)

    def _source(self, board, root):
        if 'source_conversation_id' in root:
            return root['source_conversation_id']
        context = self.repo.get_conversation(board['conversation_id'])
        return context.id if context and context.kind == 'chat' else None

    def _summary(self, board, root):
        contract = next(c for c in board['contracts'] if c['id'] == root['contract_id'])
        family = {t['id'] for t in board['tasks'] if t['root_id'] == root['id']}
        plan = deepcopy(root.get('orchestration') or {'mode': 'adaptive', 'steps': []})
        active = next((s['id'] for s in plan['steps'] if any(
            t['id'] == s.get('task_id') and t['active_mission_id'] for t in board['tasks'])), None)
        if active is None:
            active = next((s['id'] for s in plan['steps'] if any(
                t['id'] == s.get('task_id') and t['status'] not in {'done', 'cancelled'} for t in board['tasks'])), None)
        return {k: root[k] for k in ('id', 'title', 'owner_id', 'status', 'paused', 'created_at', 'updated_at')} | {
            'delivery': contract['delivery'], 'conversation_id': board['conversation_id'],
            'source_conversation_id': self._source(board, root), 'orchestration': plan,
            'current_step': active,
            'pending_decisions': sum(d['status'] == 'pending' and d['task_id'] in family for d in board['decisions']),
        }

    def list(self):
        with self.repo._transaction() as db:
            items = [self._summary(board, root) for board in self._boards(db)
                     for root in board['tasks'] if not root['parent_id']]
            for row in db.execute('SELECT data FROM work_boards').fetchall():
                board = json.loads(row['data'])
                if board.get('protocol') != 'adaptive_v1' and (board['tasks'] or board['contracts']):
                    items.append(self._legacy_summary(board))
        return sorted(items, key=lambda t: (t['updated_at'], t['id']), reverse=True)

    def detail(self, task_id):
        if task_id.startswith(LEGACY_PREFIX):
            board = self._legacy_board(task_id)
            summary = self._legacy_summary(board)
            context = self.repo.get_conversation(board['conversation_id'])
            return {'protocol': 'legacy', 'task': summary, 'board': board,
                    'archived': bool(context and context.status == 'archived'),
                    'source_conversation_id': board['conversation_id'],
                    'execution_conversation_id': board['conversation_id'],
                    'orchestration': summary['orchestration']}
        existing, _ = self._locate(task_id)
        board = self.work.read(existing['conversation_id'])
        root = next(t for t in board['tasks'] if t['id'] == task_id)
        ids = {t['id'] for t in board['tasks'] if t['root_id'] == task_id}
        contract_ids = {t['contract_id'] for t in board['tasks'] if t['id'] in ids}
        clipped = deepcopy(board)
        clipped['tasks'] = [t for t in clipped['tasks'] if t['id'] in ids]
        clipped['contracts'] = [c for c in clipped['contracts'] if c['id'] in contract_ids]
        clipped['decisions'] = [d for d in clipped['decisions'] if d['task_id'] in ids]
        clipped['events'] = [e for e in clipped['events'] if e.get('task_id') in ids]
        clipped['proposals'] = [p for p in clipped.get('proposals', []) if p.get('task_id') == task_id]
        clipped['active_contract_id'] = root['contract_id']
        clipped.pop('legacy_board', None)
        active = clipped.get('execution', {}).get('active', [])
        clipped['execution'] = {'active': [a for a in active if a['task_id'] in ids],
                                'queued': any(a['task_id'] not in ids for a in active)}
        return {'task': deepcopy(root), 'board': clipped, 'archived': False,
                'source_conversation_id': self._source(board, root),
                'execution_conversation_id': board['conversation_id'],
                'orchestration': deepcopy(root.get('orchestration') or {'mode': 'adaptive', 'steps': []})}

    def create(self, value):
        request = value if isinstance(value, CreateTask) else CreateTask.model_validate(value)
        fingerprint = digest(request.model_dump(exclude={'operation_id'}))
        with self.repo._transaction(immediate=True) as db:
            prior = db.execute('SELECT fingerprint, task_id FROM task_creation_operations WHERE operation_id=?',
                               (request.operation_id,)).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise WorkError('operation_id 已用于另一项任务')
                return self.detail(prior['task_id'])
            if request.source_conversation_id:
                source = self.repo.get_conversation(request.source_conversation_id)
                if not source or source.kind != 'chat':
                    raise WorkError('来源会话不存在', 404)
            context = Conversation(id=uid('c_task_'), title=request.title, kind='task')
            self.repo.create_conversation(context)
            board = self.work.command(context.id, 'create_work', request.model_dump(include={'title', 'owner_id', 'agreement'}),
                                      0, operation_id='task-create')
            root = board['tasks'][-1]
            # No dispatcher can observe this board before its plan and pause
            # authorization commit atomically with the creation receipt.
            root['paused'] = True
            root['source_conversation_id'] = request.source_conversation_id
            from backend.app.work.workflow import configure
            configure(self.work, board, root, request.orchestration.model_dump())
            root['paused'] = request.paused
            self.work._save(db, board, 'task_created', 'human', {'task_id': root['id']})
            db.execute('INSERT INTO task_creation_operations VALUES (?, ?, ?)',
                       (request.operation_id, fingerprint, root['id']))
            return self.detail(root['id'])

    def command(self, task_id, command):
        if task_id.startswith(LEGACY_PREFIX):
            return self._legacy_command(task_id, command)
        if command.action not in TASK_ACTIONS:
            raise WorkError('该操作不属于独立任务控制', 403)
        # Keep ownership checks and the mutation in one transaction; a board
        # can contain multiple roots migrated by projection from old chats.
        if command.action in {'retry_environment', 'start_preview', 'stop_preview'}:
            return self._command(task_id, command)
        with self.repo._transaction(immediate=True):
            return self._command(task_id, command)

    def _command(self, task_id, command):
        board, root = self._locate(task_id)
        family = {t['id'] for t in board['tasks'] if t['root_id'] == task_id}
        data = deepcopy(command.data)
        if data.get('task_id') is not None and data['task_id'] not in family:
            raise WorkError('工作卡不属于当前任务', 403)
        if data.get('decision_id'):
            decision = next((d for d in board['decisions'] if d['id'] == data['decision_id']), None)
            if not decision or decision['task_id'] not in family:
                raise WorkError('决定不属于当前任务', 403)
            data.setdefault('task_id', decision['task_id'])
        data.setdefault('task_id', task_id)
        if command.action == 'configure_workflow' and data['task_id'] != task_id:
            raise WorkError('编排只属于根任务', 422)
        if command.action == 'revise_work' and root.get('orchestration', {}).get('mode') == 'workflow':
            raise WorkError('固定工作流的范围变更请新建关联任务；开始前可修改编排')
        self.work.command(board['conversation_id'], command.action, data, command.expected_revision,
                          operation_id=command.operation_id)
        return self.detail(task_id)

    def evidence(self, task_id, evidence_id):
        detail = self.detail(task_id)
        refs = set()

        def collect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {'log_id', 'candidate_id', 'before_id'} and isinstance(child, str):
                        refs.add(child)
                    if key == 'id' and isinstance(child, str) and child.startswith('ws_'):
                        refs.add(child)
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(detail['board']['tasks'])
        collect(detail['board'].get('decisions', []))
        if evidence_id not in refs:
            raise WorkError('证据不属于当前任务', 404)
        return detail['execution_conversation_id']

    def read_log(self, task_id, log_id):
        context = self.evidence(task_id, log_id)
        # Task resources project both protocols; the execution scope must be
        # verified first, then logs dispatched by the board's actual protocol.
        return SharedWork(self.repo, self.work.root, self.work.check_timeout).read_log(context, log_id)

    def _legacy_board(self, task_id):
        context_id = task_id.removeprefix(LEGACY_PREFIX)
        with self.repo._transaction() as db:
            row = db.execute('SELECT data FROM work_boards WHERE conversation_id=?', (context_id,)).fetchone()
        if not row or json.loads(row['data']).get('protocol') == 'adaptive_v1':
            raise WorkError('历史共享任务不存在', 404)
        return SharedWork(self.repo, self.work.root, self.work.check_timeout).read(context_id)

    def _legacy_summary(self, board):
        context = self.repo.get_conversation(board['conversation_id'])
        items = board['tasks']
        active = next((t for t in items if t['active_mission_id']), None)
        finished = bool(items) and all(t['status'] == 'done' for t in items)
        paused = not board['enabled']
        state = 'running' if active else 'done' if finished else 'waiting' if paused or any(
            t['status'] == 'blocked' for t in items) else 'ready'
        stamp = context.updated_at.isoformat() if context else now()
        return {'id': LEGACY_PREFIX + board['conversation_id'], 'protocol': 'legacy',
                'title': (context.title if context else '历史共享任务') + ' · 旧版工作',
                'owner_id': items[0]['owner_id'] if items else '', 'status': state, 'paused': paused,
                'delivery': 'development', 'created_at': context.created_at.isoformat() if context else stamp,
                'updated_at': board['events'][-1]['created_at'] if board['events'] else stamp,
                'conversation_id': board['conversation_id'], 'source_conversation_id': board['conversation_id'],
                'current_step': active['id'] if active else None, 'pending_decisions': 0,
                'orchestration': {'mode': 'workflow', 'steps': [
                    {'id': t['id'], 'task_id': t['id'], 'title': t['title'], 'owner_id': t['owner_id'],
                     'goal': t['title'], 'depends_on': t['depends_on']} for t in items]}}

    def _legacy_command(self, task_id, command):
        if command.action not in LEGACY_ACTIONS:
            raise WorkError('历史任务保留原协议，不支持新编排或授权操作', 403)
        if not command.operation_id:
            raise WorkError('任务控制须携带稳定 operation_id', 422)
        with self.repo._transaction(immediate=True) as db:
            board = self._legacy_board(task_id)
            ids = {t['id'] for t in board['tasks']}
            contracts = {c['id'] for c in board['contracts']}
            data = command.data
            if data.get('task_id') is not None and data['task_id'] not in ids:
                raise WorkError('工作卡不属于当前任务', 403)
            if data.get('contract_id') is not None and data['contract_id'] not in contracts:
                raise WorkError('约定不属于当前任务', 403)
            if not set(data.get('depends_on', [])) <= ids:
                raise WorkError('依赖不属于当前任务', 403)
            fingerprint = digest({'action': command.action, 'data': data})
            operation_id = 'legacy:' + command.operation_id
            prior = db.execute('SELECT fingerprint FROM work_operations WHERE conversation_id=? AND operation_id=?',
                               (board['conversation_id'], operation_id)).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise WorkError('operation_id 已用于其他操作')
                return self.detail(task_id)
            result = SharedWork(self.repo, self.work.root, self.work.check_timeout).command(
                board['conversation_id'], command.action, data, command.expected_revision)
            db.execute('INSERT INTO work_operations VALUES (?, ?, ?, ?)',
                       (board['conversation_id'], operation_id, fingerprint, json.dumps(result, ensure_ascii=False)))
            return self.detail(task_id)
