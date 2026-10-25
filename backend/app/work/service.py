"""One atomic board per conversation; missions are attempts, never the work record."""
import hashlib
import json
import os
import signal
import subprocess
from pathlib import Path
from uuid import uuid4

from backend.app.domain.agent_registry import get_agent_registry
from backend.app.domain.models import Mission, MissionStatus, OrchestrationConfig, OrchestrationMode, utc_now
from backend.app.work.models import ContractInput, TaskInput


class WorkError(ValueError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def now():
    return utc_now().isoformat()


def uid(prefix):
    return prefix + uuid4().hex


class LegacySharedWork:
    def __init__(self, repository, workspace_root=None, check_timeout=300):
        self.repo = repository
        self.root = Path(workspace_root or Path.cwd()).resolve()
        self.check_timeout = check_timeout

    def _load(self, db, conversation_id):
        if not self.repo.get_conversation(conversation_id):
            raise WorkError('会话不存在', 404)
        row = db.execute('SELECT data FROM work_boards WHERE conversation_id=?', (conversation_id,)).fetchone()
        return json.loads(row['data']) if row else {
            'conversation_id': conversation_id, 'revision': 0, 'enabled': False,
            'active_contract_id': None, 'contracts': [], 'tasks': [], 'events': [],
            'workspace_root': str(self.root), 'stage_budget': 12,
        }

    def read(self, conversation_id):
        with self.repo._transaction() as db:
            board = self._load(db, conversation_id)
        try:
            head = self._git('rev-parse', 'HEAD') if Path(board['workspace_root']).resolve() == self.root else None
        except WorkError:
            head = None
        board['current_head'] = head
        return board

    def read_log(self, conversation_id, log_id):
        with self.repo._transaction() as db:
            self._load(db, conversation_id)
            row = db.execute('SELECT output FROM work_check_logs WHERE id=? AND conversation_id=?',
                             (log_id, conversation_id)).fetchone()
            if not row:
                raise WorkError('验证日志不存在', 404)
            return bytes(row['output'])

    def _save(self, db, board, action, actor, details=None):
        board.pop('current_head', None)
        board['revision'] += 1
        board['events'].append({'id': uid('we_'), 'action': action, 'actor': actor,
                                'created_at': now(), **(details or {})})
        db.execute('INSERT INTO work_boards VALUES (?, ?) ON CONFLICT(conversation_id) DO UPDATE SET data=excluded.data',
                   (board['conversation_id'], json.dumps(board, ensure_ascii=False)))

    def _git(self, *args):
        try:
            result = subprocess.run(['git', *args], cwd=self.root, capture_output=True,
                                    text=True, timeout=10, check=True)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkError('无法读取当前 Git 工作区，请检查仓库与提交记录') from exc

    def _clean_head(self):
        if self._git('status', '--porcelain', '--untracked-files=normal'):
            raise WorkError('工作区存在未提交改动，请提交后重新验证；审核必须绑定完整代码版本')
        return self._git('rev-parse', 'HEAD')

    @staticmethod
    def _task(board, task_id):
        task = next((t for t in board['tasks'] if t['id'] == task_id), None)
        if not task:
            raise WorkError('共享任务不存在', 404)
        return task

    @staticmethod
    def _contract(board, contract_id):
        contract = next((c for c in board['contracts'] if c['id'] == contract_id), None)
        if not contract:
            raise WorkError('开发约定不存在', 404)
        return contract

    def _agent_task(self, board, data, actor, mission_id, turn_id):
        task = self._task(board, data.get('task_id'))
        mission = self.repo.get_mission(mission_id) if mission_id else None
        if (not mission or not turn_id or mission.status != MissionStatus.RUNNING
                or mission.conversation_id != board['conversation_id']
                or task.get('active_mission_id') != mission_id
                or task['contract_id'] != board['active_contract_id']):
            raise WorkError('当前执行已过期或不属于这张共享任务')
        required = task['reviewer_id'] if task['status'] == 'review' else task['owner_id']
        if actor != required or task['status'] not in {'running', 'review'}:
            raise WorkError('当前角色不是本阶段责任人')
        return task

    def command(self, conversation_id, action, data, expected_revision, *, actor='human', mission_id=None, turn_id=None):
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            if board['revision'] != expected_revision:
                raise WorkError('任务板已更新，请重新读取后操作')
            conversation = self.repo.get_conversation(conversation_id)
            if conversation.status == 'archived':
                raise WorkError('请先恢复会话再推进共享任务')
            if actor != 'human' and action not in {'propose_contract', 'create_task', 'progress', 'submit', 'review', 'block'}:
                raise WorkError('只有用户可以启用约定、启动、暂停或重试任务', 403)
            if actor != 'human':
                source = self.repo.get_mission(mission_id) if mission_id else None
                if not source or source.status != MissionStatus.RUNNING or source.conversation_id != conversation_id:
                    raise WorkError('当前执行已过期，不能修改共享任务')
            if action == 'propose_contract':
                proposal = ContractInput.model_validate(data).model_dump()
                contract = {**proposal, 'id': uid('wc_'), 'version': len(board['contracts']) + 1,
                            'created_by': actor, 'created_at': now(), 'activated_at': None}
                contract['digest'] = hashlib.sha256(json.dumps(proposal, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                board['contracts'].append(contract)
            elif action == 'create_task':
                task = TaskInput.model_validate(data).model_dump()
                contract = self._contract(board, task['contract_id'])
                agents = set(get_agent_registry().ids())
                if task['owner_id'] not in agents or task['reviewer_id'] not in agents or task['owner_id'] == task['reviewer_id']:
                    raise WorkError('必须选择不同的有效执行人与审核人')
                if len(set(task['criterion_ids'])) != len(task['criterion_ids']) or not set(task['criterion_ids']) <= {c['id'] for c in contract['criteria']}:
                    raise WorkError('任务必须关联当前约定中的有效验收项')
                for dep_id in task['depends_on']:
                    if self._task(board, dep_id)['contract_id'] != contract['id']:
                        raise WorkError('依赖必须属于同一开发约定')
                board['tasks'].append({**task, 'id': uid('wt_'), 'status': 'ready', 'progress': '',
                    'blocker': None, 'active_mission_id': None, 'attempts': [], 'evidence': [], 'reviews': [],
                    'candidate_sha': None, 'created_at': now(), 'updated_at': now()})
            elif action == 'activate_contract':
                if any(t['active_mission_id'] for t in board['tasks']):
                    raise WorkError('请等待当前回合结束再启用新约定')
                contract = self._contract(board, data.get('contract_id'))
                contract['activated_at'] = contract['activated_at'] or now()
                board['active_contract_id'] = contract['id']
                board['enabled'] = False
            elif action == 'start':
                if Path(board['workspace_root']).resolve() != self.root:
                    raise WorkError('共享任务绑定的工作区已变化，请在原工作区启动服务')
                if any(m.status in {MissionStatus.PENDING, MissionStatus.RUNNING, MissionStatus.AWAITING_INPUT}
                       for m in self.repo.list_missions()):
                    raise WorkError('请等待当前任务结束后再启动自主推进')
                contract = self._contract(board, board['active_contract_id'])
                tasks = [t for t in board['tasks'] if t['contract_id'] == contract['id']]
                if not tasks or {c['id'] for c in contract['criteria']} - {c for t in tasks for c in t['criterion_ids']}:
                    raise WorkError('请先将全部验收项分配到共享任务')
                board['enabled'] = True
            elif action == 'pause':
                board['enabled'] = False
            elif action == 'retry':
                task = self._task(board, data.get('task_id'))
                if task['status'] != 'blocked' or task['active_mission_id'] or task['contract_id'] != board['active_contract_id']:
                    raise WorkError('只能重试当前约定下已停止的阻塞任务')
                task.update(status='ready', blocker=None, candidate_sha=None, updated_at=now())
            else:
                task = self._agent_task(board, data, actor, mission_id, turn_id)
                if action == 'progress':
                    text = data.get('text')
                    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                        raise WorkError('进度必须是 1–4000 字的说明')
                    task['progress'] = text
                elif action == 'block':
                    reason = data.get('reason')
                    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
                        raise WorkError('阻塞必须说明具体原因')
                    task.update(status='blocked', blocker=reason)
                elif action == 'submit':
                    if task['status'] != 'running':
                        raise WorkError('只有执行阶段可以提交验证')
                    # The execution ledger holds a write transaction around
                    # MCP calls. Request validation here; run commands only
                    # after the CLI exits and that transaction has committed.
                    task.update(status='validating', candidate_sha=self._clean_head(), blocker=None)
                    task['attempts'][-1]['submission_turn_id'] = turn_id
                elif action == 'review':
                    if task['status'] != 'review' or actor == task['owner_id']:
                        raise WorkError('只有独立审核人可以审核当前候选版本')
                    if data.get('commit_sha') != task['candidate_sha'] or self._clean_head() != task['candidate_sha']:
                        raise WorkError('代码版本已变化，请阻塞任务并重新执行验证')
                    if set(data.get('criterion_ids') or []) != set(task['criterion_ids']):
                        raise WorkError('审核必须逐项覆盖这张任务的验收项')
                    decision, note = data.get('decision'), data.get('note')
                    if decision not in {'approved', 'changes_requested'} or not isinstance(note, str) or not note.strip() or len(note) > 12000:
                        raise WorkError('审核必须包含有效结论和证据说明')
                    evidence = task['evidence'][-1]
                    if not evidence['passed'] or evidence['contract_id'] != task['contract_id'] or evidence['commit_sha'] != task['candidate_sha']:
                        raise WorkError('缺少当前约定与代码版本的验证证据')
                    task['reviews'].append({'id': uid('wr_'), 'contract_id': task['contract_id'],
                        'criterion_ids': task['criterion_ids'], 'commit_sha': task['candidate_sha'],
                        'evidence_id': evidence['id'], 'reviewer_id': actor, 'mission_id': mission_id,
                        'turn_id': turn_id, 'decision': decision, 'note': note, 'created_at': now()})
                    task.update(status='done' if decision == 'approved' else 'ready', candidate_sha=task['candidate_sha'] if decision == 'approved' else None)
                else:
                    raise WorkError('不支持的共享任务操作', 422)
                task['updated_at'] = now()
            self._save(db, board, action, actor, {'task_id': data.get('task_id'), 'mission_id': mission_id,
                'turn_id': turn_id, 'reason': str(data.get('reason') or '')[:4000]})
            return board

    def _checks(self, contract, task, mission_id, turn_id, actor):
        head = self._clean_head()
        if head != task['candidate_sha']:
            raise WorkError('提交后代码版本已变化，请重新提交验证')
        results = []
        for command in contract['validation_commands']:
            try:
                process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    raw, _ = process.communicate(timeout=self.check_timeout)
                    code = process.returncode
                except subprocess.TimeoutExpired:
                    # Test runners often launch children. Stop the whole group
                    # so descendants cannot keep writing after validation ends.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    raw, _ = process.communicate()
                    raw, code = raw + b'\nValidation timed out', -1
            except OSError as exc:
                raw, code = str(exc).encode(), -1
            log_id = uid('wl_')
            with self.repo._transaction(immediate=True) as db:
                db.execute('INSERT INTO work_check_logs VALUES (?, ?, ?)',
                    (log_id, self.repo.get_mission(mission_id).conversation_id, raw))
            results.append({'command': command, 'exit_code': code, 'output': raw[-24000:].decode(errors='replace'),
                            'log_id': log_id, 'output_sha256': hashlib.sha256(raw).hexdigest(), 'truncated': len(raw) > 24000})
        try:
            unchanged = self._clean_head() == head
        except WorkError:
            unchanged = False
        return {'id': uid('wv_'), 'contract_id': contract['id'], 'contract_digest': contract['digest'],
                'criterion_ids': task['criterion_ids'], 'commit_sha': head, 'mission_id': mission_id,
                'turn_id': turn_id, 'agent_id': actor, 'created_at': now(), 'checks': results,
                'workspace_unchanged': unchanged, 'passed': unchanged and all(r['exit_code'] == 0 for r in results)}

    def claim(self, conversation_id):
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            if not board['enabled'] or self.repo.get_conversation(conversation_id).status == 'archived':
                return None
            if Path(board['workspace_root']).resolve() != self.root:
                board['enabled'] = False
                self._save(db, board, 'workspace_changed', 'host', {'reason': '请在原工作区启动服务'})
                return None
            if any(t['active_mission_id'] for t in board['tasks']):
                return None
            # All boards share the runtime Git worktree. Claim globally, not
            # merely per conversation, so two starts cannot race file writes.
            if any(t['active_mission_id'] for row in db.execute('SELECT data FROM work_boards').fetchall()
                   for t in json.loads(row['data'])['tasks']):
                return None
            if any(m.status in {MissionStatus.PENDING, MissionStatus.RUNNING, MissionStatus.AWAITING_INPUT}
                   for m in self.repo.list_missions()):
                return None
            tasks = [t for t in board['tasks'] if t['contract_id'] == board['active_contract_id']]
            for exhausted in tasks:
                if exhausted['status'] in {'ready', 'review'} and len(exhausted['attempts']) >= board['stage_budget']:
                    exhausted.update(status='blocked', blocker='已达到自动执行预算，请拆分任务或调整约定')
                    self._save(db, board, 'budget_exhausted', 'host', {'task_id': exhausted['id']})
            completed = {t['id'] for t in tasks if t['status'] == 'done'}
            task = next((t for t in tasks if t['status'] == 'review'), None)
            task = task or next((t for t in tasks if t['status'] == 'ready' and set(t['depends_on']) <= completed), None)
            if not task:
                board['enabled'] = False
                self._save(db, board, 'settled', 'host', {'reason': '全部完成' if tasks and all(t['status'] == 'done' for t in tasks) else '等待阻塞处理'})
                return None
            reviewing = task['status'] == 'review'
            agent_id = task['reviewer_id'] if reviewing else task['owner_id']
            contract = self._contract(board, task['contract_id'])
            # Evidence remains in the durable board/log store. Do not copy
            # every previous test output into each subsequent CLI prompt.
            task_context = {key: value for key, value in task.items() if key not in {'evidence', 'reviews', 'attempts'}}
            task_context['latest_review'] = task['reviews'][-1] if task['reviews'] else None
            goal = ('共享开发任务审核' if reviewing else '共享开发任务执行') + '\n' + json.dumps({
                'task': task_context, 'contract': contract, 'workspace_root': board['workspace_root'],
            }, ensure_ascii=False)
            goal += ('\n先调用 shared_work(action="read") 获取最新 revision。'
                     '本轮只推进上述任务。使用 CLI 原生工具在指定工作区工作；不要自动合并或发布。'
                     '完成执行后必须调用 shared_work(action="submit", data={task_id})，宿主运行约定验证。'
                     '用户点击启动执行时已允许任务范围内的本地提交，请只提交本任务改动，保持工作区干净。'
                     '审核阶段只读代码和证据，不修改文件；调用 review，逐项关联 criterion_ids、commit_sha、decision 和 note。'
                     '无法推进时调用 block 并给 reason；中途使用 progress(text) 保存检查点。'
                     '所有写入携带最新 expected_revision；最后通过 publish_message 汇报。')
            mission = Mission(id=uid('m_'), title=task['title'], goal=goal, conversation_id=conversation_id,
                target_agent_id=agent_id, orchestration=OrchestrationConfig(mode=OrchestrationMode.SINGLE, participant_ids=[agent_id]))
            self.repo.create_mission(mission)
            conversation = self.repo.get_conversation(conversation_id)
            if conversation.title.strip() in {'', '新对话'}:
                self.repo.update_conversation(conversation.model_copy(update={'title': task['title'][:24], 'updated_at': utc_now()}))
            task['active_mission_id'] = mission.id
            if not reviewing:
                task['status'] = 'running'
            task['attempts'].append({'mission_id': mission.id, 'agent_id': agent_id,
                                     'stage': 'review' if reviewing else 'implement', 'started_at': now()})
            self._save(db, board, 'claimed', 'host', {'task_id': task['id'], 'mission_id': mission.id})
            self.repo.add_event(mission.id, 'work.claimed', {'task_id': task['id'], 'contract_id': contract['id'], 'stage': task['attempts'][-1]['stage']})
            return mission

    def finish(self, conversation_id, mission_id):
        validation = None
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            task = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
            if not task:
                return
            mission = self.repo.get_mission(mission_id)
            attempt = task['attempts'][-1]
            successful = mission and mission.status in {MissionStatus.COMPLETED, MissionStatus.COMPLETED_WITH_WARNINGS}
            if successful and attempt['stage'] == 'implement' and task['status'] == 'validating':
                if attempt.get('validation_started_at'):
                    return  # Another host already owns these command effects.
                attempt['validation_started_at'] = now()
                validation = (self._contract(board, task['contract_id']), task)
                self._save(db, board, 'validation_started', 'host', {'task_id': task['id'], 'mission_id': mission_id})
        checks, error = None, None
        if validation:
            contract, candidate = validation
            try:
                checks = self._checks(contract, candidate, mission_id,
                    candidate['attempts'][-1]['submission_turn_id'], candidate['owner_id'])
            except WorkError as exc:
                error = str(exc)
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            task = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
            if not task:
                return
            mission = self.repo.get_mission(mission_id)
            attempt = task['attempts'][-1]
            attempt.update(completed_at=now(), outcome=mission.status.value if mission else 'missing')
            if validation:
                if checks:
                    task['evidence'].append(checks)
                if checks and checks['passed']:
                    task.update(status='review', blocker=None)
                else:
                    task.update(status='blocked', blocker=error or '验证未通过，证据已保留')
            missing_result = (attempt['stage'] == 'implement' and task['status'] == 'running') or (attempt['stage'] == 'review' and task['status'] == 'review')
            if not mission or mission.status not in {MissionStatus.COMPLETED, MissionStatus.COMPLETED_WITH_WARNINGS} or missing_result:
                task.update(status='blocked', blocker=task['blocker'] or '执行未完成或缺少结构化交付，请查看回合记录后重试')
            task['active_mission_id'] = None
            task['updated_at'] = now()
            self._save(db, board, 'attempt_finished', 'host', {'task_id': task['id'], 'mission_id': mission_id})

    def recover(self):
        with self.repo._transaction(immediate=True) as db:
            for row in db.execute('SELECT conversation_id FROM work_boards').fetchall():
                board = self._load(db, row['conversation_id'])
                if board.get('protocol') == 'adaptive_v1':
                    continue
                if not board['enabled'] and not any(t['active_mission_id'] for t in board['tasks']):
                    continue
                board['enabled'] = False
                for task in board['tasks']:
                    if task['active_mission_id']:
                        mission = self.repo.get_mission(task['active_mission_id'])
                        if mission and mission.status in {MissionStatus.RUNNING, MissionStatus.PENDING}:
                            self.repo.update_mission(mission.model_copy(update={'status': MissionStatus.BLOCKED, 'updated_at': utc_now()}))
                        task['attempts'][-1].update(completed_at=now(), outcome='interrupted')
                        task.update(status='blocked', active_mission_id=None, blocker='服务重启中断；进度与证据已保留，请确认文件状态后重试')
                self._save(db, board, 'recovered', 'host')


class SharedWork:
    """Protocol facade: existing boards keep their original execution semantics."""
    def __init__(self, repository, workspace_root=None, check_timeout=300):
        self.repo = repository
        self.root = Path(workspace_root or Path.cwd()).resolve()
        self.check_timeout = check_timeout
        self._legacy = LegacySharedWork(repository, self.root, check_timeout)

    def __getattr__(self, name):
        return getattr(self._legacy, name)

    @property
    def _checks(self):
        return self._legacy._checks

    @_checks.setter
    def _checks(self, value):
        self._legacy._checks = value

    def _service(self, conversation_id, action=None):
        from backend.app.work.adaptive import AdaptiveWork
        with self.repo._transaction() as db:
            row = db.execute('SELECT data FROM work_boards WHERE conversation_id=?', (conversation_id,)).fetchone()
        board = json.loads(row['data']) if row else None
        adaptive = (board and board.get('protocol') == 'adaptive_v1') or (not board and action in {None, 'create_work', 'propose_work'})
        return AdaptiveWork(self.repo, self.root, self.check_timeout) if adaptive else self._legacy

    def read(self, conversation_id):
        return self._service(conversation_id).read(conversation_id)

    def read_log(self, conversation_id, log_id):
        return self._service(conversation_id).read_log(conversation_id, log_id)

    def command(self, conversation_id, action, data, expected_revision, **kwargs):
        from backend.app.work.adaptive import ENVIRONMENT_ACTIONS
        if action in ENVIRONMENT_ACTIONS:
            return self._service(conversation_id, action).command(
                conversation_id, action, data, expected_revision, **kwargs)
        with self.repo._transaction(immediate=True) as db:
            service = self._service(conversation_id, action)
            if action == 'create_work' and isinstance(service, LegacySharedWork) and type(service) is LegacySharedWork:
                old = service.read(conversation_id)
                if old['enabled'] or any(t['active_mission_id'] for t in old['tasks']):
                    raise WorkError('请先结束或暂停历史协议工作再创建新工作')
                from backend.app.work.adaptive import AdaptiveWork
                service = AdaptiveWork(self.repo, self.root, self.check_timeout)
                board = service._empty(conversation_id)
                board.update(revision=old['revision'], legacy_board=old)
                db.execute('UPDATE work_boards SET data=? WHERE conversation_id=?',
                           (json.dumps(board, ensure_ascii=False), conversation_id))
            if type(service) is LegacySharedWork:
                kwargs.pop('operation_id', None)
            return service.command(conversation_id, action, data, expected_revision, **kwargs)

    def claim(self, conversation_id):
        return self._service(conversation_id).claim(conversation_id)

    def finish(self, conversation_id, mission_id):
        return self._service(conversation_id).finish(conversation_id, mission_id)

    def execution_context(self, mission_id):
        mission = self.repo.get_mission(mission_id)
        if not mission or not mission.conversation_id:
            return None
        with self.repo._transaction() as db:
            row = db.execute('SELECT data FROM work_boards WHERE conversation_id=?', (mission.conversation_id,)).fetchone()
        if not row or json.loads(row['data']).get('protocol') != 'adaptive_v1':
            return None
        service = self._service(mission.conversation_id)
        return service.execution_context(mission_id) if hasattr(service, 'execution_context') else None

    def recover(self):
        from backend.app.work.adaptive import AdaptiveWork
        LegacySharedWork(self.repo, self.root, self.check_timeout).recover()
        AdaptiveWork(self.repo, self.root, self.check_timeout).recover()
