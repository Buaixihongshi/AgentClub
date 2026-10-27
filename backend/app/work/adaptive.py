"""Durable work policy. Agents choose actions; the host owns acceptance and grants."""
import hashlib
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

from backend.app.domain.agent_registry import get_agent_registry
from backend.app.domain.models import Mission, MissionStatus, OrchestrationConfig, OrchestrationMode, utc_now
from backend.app.work.adaptive_models import (
    AGENT_ACTIONS, HUMAN_ACTIONS, POLICY, Agreement, Checkpoint, DecisionRequest, NewWork, Submission,
)
from backend.app.work.environments import TaskEnvironments
from backend.app.work.service import LegacySharedWork, WorkError, now, uid
from backend.app.work.snapshots import snapshot, store

PROTOCOL = 'adaptive_v1'
SUCCESS = {MissionStatus.COMPLETED, MissionStatus.COMPLETED_WITH_WARNINGS}
TERMINAL = {'done', 'cancelled'}
ENVIRONMENT_ACTIONS = {'retry_environment', 'start_preview', 'stop_preview'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class AdaptiveWork(LegacySharedWork):
    def __init__(self, repository, workspace_root=None, check_timeout=300,
                 worktree_root=None, preview_command=None, preview_timeout=600):
        super().__init__(repository, workspace_root, check_timeout)
        self.environments = TaskEnvironments(repository, self.root, worktree_root,
                                             preview_command=preview_command, preview_timeout=preview_timeout)

    def _save(self, db, board, action, actor, details=None):
        board.pop('execution', None)  # Derived per read, never persisted.
        super()._save(db, board, action, actor, details)

    def read(self, conversation_id):
        board = super().read(conversation_id)
        # One development action runs at a time across all boards; waiting
        # boards need to show the honest reason instead of silent idling.
        active = []
        with self.repo._transaction() as db:
            for row in db.execute('SELECT conversation_id, data FROM work_boards').fetchall():
                for task in json.loads(row['data'])['tasks']:
                    if task.get('active_mission_id'):
                        active.append({'conversation_id': row['conversation_id'],
                                       'task_id': task['id'], 'title': task['title']})
        board['execution'] = {'active': active}
        return board

    def _workspace(self, board, task):
        """Resolve the directory every action of this task must operate on."""
        root = task if (task.get('root_id') or task['id']) == task['id'] else self._root_task(board, task)
        env = root.get('environment')
        if not env:
            return self.root  # Boards created before binding keep single-workspace semantics.
        if env.get('status') != 'ready':
            raise WorkError('任务工作区尚未就绪：' + str(env.get('failure_reason') or env.get('status')))
        if not self.environments.verify(env):
            raise WorkError('任务工作区的目录或分支身份变化，请核对后重试')
        return Path(env['workspace_dir'])

    def ensure_environments(self, conversation_id, task_id=None, force=False):
        """Serialize preparation per task without holding a database write lock."""
        from scripts.environment_common import lock
        board = self.read(conversation_id)
        wanted = {'pending', 'preparing'} if not force else {'pending', 'preparing', 'failed'}
        pending = [t for t in board['tasks'] if not t['parent_id'] and t['status'] not in TERMINAL
                   and not t['paused'] and (t.get('environment') or {}).get('status') in wanted
                   and (task_id is None or t['id'] == task_id)]
        for stale in pending:
            key = 'prepare-' + digest({'conversation': conversation_id, 'task': stale['id']})
            with lock(self.environments.worktree_root / '.operations' / (key + '.lock'), wait=True):
                fresh = self.read(conversation_id)
                task = self._task(fresh, stale['id'])
                if (task['status'] in TERMINAL or task['paused']
                        or (task.get('environment') or {}).get('status') not in wanted):
                    continue
                prepared = self.environments.prepare(dict(task['environment']))
                with self.repo._transaction(immediate=True) as db:
                    current = self._load(db, conversation_id)
                    live = self._task(current, stale['id'])
                    if (live.get('environment') or {}).get('status') not in wanted:
                        continue
                    live['environment'] = prepared
                    if prepared['status'] == 'ready':
                        if live['waiting_reason'] == 'environment' and live['status'] not in TERMINAL and not live['paused']:
                            self._ready(live, '工作区已就绪；按约定推进')
                        self._save(db, current, 'environment_ready', 'host',
                                   {'task_id': live['id'], 'branch': prepared['branch']})
                    else:
                        if live['status'] not in TERMINAL:
                            self._waiting(live, 'environment', '工作区准备失败：' + str(prepared['failure_reason']), '用户')
                        self._save(db, current, 'environment_failed', 'host',
                                   {'task_id': live['id'], 'reason': prepared['failure_reason']})
        return bool(pending)

    def _recover_environment(self, board, task):
        """After a restart: re-verify the recorded binding, stop orphan previews,
        and leave ambiguous state explicitly waiting instead of replaying."""
        env = task['environment']
        changed = False
        preview = env.get('preview') or {}
        if preview.get('status') in {'starting', 'ready'}:
            env['preview'] = self.environments.reconcile_preview(preview)
            changed = True
        if env.get('status') == 'ready' and not self.environments.verify(env):
            env.update(status='failed', failure_reason='重启后核对失败：目录或分支与记录不匹配',
                       updated_at=now())
            if task['status'] not in TERMINAL:
                self._waiting(task, 'environment', env['failure_reason'], '用户')
            changed = True
        elif env.get('status') == 'preparing':
            env.update(status='pending', updated_at=now())  # Idempotent retry on next claim.
            changed = True
        return changed

    def _environment_command(self, conversation_id, action, data, expected_revision, **kwargs):
        from scripts.environment_common import lock
        key = digest({'conversation': conversation_id, 'task': data.get('task_id')})
        with lock(self.environments.worktree_root / '.operations' / (key + '.lock'), wait=True):
            return self._environment_command_locked(conversation_id, action, data, expected_revision, **kwargs)

    def _environment_command_locked(self, conversation_id, action, data, expected_revision, *,
                             actor, mission_id, turn_id, operation_id):
        if actor != 'human':
            raise WorkError('环境与预览操作仅用户可用', 403)
        if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 120:
            raise WorkError('写操作必须携带 1–120 字符的稳定 operation_id', 422)
        fingerprint = digest({'action': action, 'data': data, 'actor': actor,
                              'mission_id': mission_id, 'turn_id': turn_id})
        with self.repo._transaction() as db:
            prior = db.execute('SELECT fingerprint, result FROM work_operations WHERE conversation_id=? AND operation_id=?',
                               (conversation_id, operation_id)).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise WorkError('operation_id 已用于其他操作')
                return json.loads(prior['result'])
        task_id = str(data.get('task_id') or '')
        if not task_id:
            raise WorkError('请选择工作卡', 422)
        board = self.read(conversation_id)
        if expected_revision != board['revision']:
            raise WorkError('任务板已更新，请重新读取后操作')
        task = self._task(board, task_id)
        if task['parent_id']:
            raise WorkError('环境操作属于根工作卡', 422)
        if action != 'stop_preview' and task['status'] == 'cancelled':
            raise WorkError('任务已取消，不能准备工作区或启动预览')
        if action == 'retry_environment':
            self.ensure_environments(conversation_id, task_id, force=True)
        elif action == 'start_preview':
            self.environments.start_preview(self, conversation_id, task_id, expected_revision)
        else:
            self.environments.stop_preview(self, conversation_id, task_id, expected_revision)
        result = self.read(conversation_id)
        with self.repo._transaction() as db:
            db.execute('INSERT INTO work_operations VALUES (?, ?, ?, ?)',
                       (conversation_id, operation_id, fingerprint, json.dumps(result, ensure_ascii=False)))
        return result

    def _empty(self, conversation_id):
        return {'protocol': PROTOCOL, 'conversation_id': conversation_id, 'revision': 0,
                'enabled': True, 'workspace_root': str(self.root), 'contracts': [], 'tasks': [],
                'proposals': [], 'decisions': [], 'events': [], 'active_contract_id': None, 'policy': deepcopy(POLICY)}

    def _load(self, db, conversation_id):
        if not self.repo.get_conversation(conversation_id):
            raise WorkError('会话不存在', 404)
        row = db.execute('SELECT data FROM work_boards WHERE conversation_id=?', (conversation_id,)).fetchone()
        if not row:
            return self._empty(conversation_id)
        board = json.loads(row['data'])
        if board.get('protocol') != PROTOCOL:
            raise WorkError('历史协议任务须受控结束后，才能创建新的自主工作')
        source_ids = [m.id for m in self.repo.list_messages_for_conversation(conversation_id) if m.role == 'user']
        for proposal in board.get('proposals', []):
            if proposal['status'] == 'pending' and proposal.get('source_message_ids', []) != source_ids:
                proposal['status'] = 'superseded'
        return board

    def _agreement(self, board, task):
        return self._contract(board, task['contract_id'])

    def _root_task(self, board, task):
        return self._task(board, task['root_id'])

    def _waiting(self, task, reason, text, waiting_for='host'):
        task.update(status='waiting', waiting_reason=reason, blocker=text, waiting_for=waiting_for)

    def _ready(self, task, action=None):
        task.update(status='ready', waiting_reason=None, waiting_for=None, blocker=None)
        if action:
            task['next_action'] = action

    def _new_task(self, title, owner, contract, criteria, parent=None, dependencies=()):
        task_id = uid('wt_')
        return {'id': task_id, 'title': title, 'owner_id': owner, 'contract_id': contract['id'],
                'root_id': parent['root_id'] if parent else task_id,
                'parent_id': parent['id'] if parent else None, 'criterion_ids': list(criteria),
                'depends_on': list(dependencies), 'status': 'ready', 'next_action': '调查并选择下一项必要动作',
                'progress': '', 'waiting_reason': None, 'waiting_for': None, 'blocker': None,
                'active_mission_id': None, 'attempts': [], 'evidence': [], 'reviews': [],
                'candidate': None, 'candidate_sha': None, 'reviewer_id': None, 'review_required': False,
                'authors': [], 'risks': list(contract['risks']), 'deliveries': [],
                'paused': False, 'attempts_used': 0, 'no_progress': 0,
                'max_attempts': POLICY['max_attempts'], 'retries': 0,
                'created_at': now(), 'updated_at': now()}

    def _add_agreement(self, board, agreement, actor):
        value = {**agreement, 'id': uid('wc_'), 'version': len(board['contracts']) + 1,
                 'digest': digest(agreement), 'created_at': now(), 'created_by': actor}
        board['contracts'].append(value)
        board['active_contract_id'] = value['id']
        return value

    def _agent(self, actor):
        if actor not in get_agent_registry().ids():
            raise WorkError('未知角色', 422)

    def _actor_task(self, board, data, actor, mission_id, turn_id):
        task = self._task(board, data.get('task_id'))
        mission = self.repo.get_mission(mission_id) if mission_id else None
        if (not mission or mission.conversation_id != board['conversation_id'] or not turn_id
                or mission.status != MissionStatus.RUNNING or task['active_mission_id'] != mission_id
                or task['status'] in TERMINAL):
            raise WorkError('当前执行已过期或不属于这张工作卡')
        attempt = task['attempts'][-1]
        if attempt['agent_id'] != actor:
            raise WorkError('当前角色不是本动作责任人', 403)
        if attempt.get('contract_id') != task['contract_id']:
            raise WorkError('约定版本已变化，旧执行不能回写')
        return task

    def _require_owner(self, task, actor):
        if actor != task['owner_id'] or task['attempts'][-1]['stage'] == 'review':
            raise WorkError('仅当前负责人可以安排或提交工作', 403)

    def _risk_required(self, board, task):
        return bool(set(task['risks']) & set(board['policy']['review_risks'])) or task['review_required']

    def _snapshot(self, board, task, artifact_paths=()):
        return store(self.repo, board['conversation_id'], snapshot(self._workspace(board, task), artifact_paths))

    def _current_matches(self, board, task, candidate):
        root = self._workspace(board, task)
        return bool(candidate) and snapshot(root, candidate.get('artifact_paths', []))['id'] == candidate['id']

    @staticmethod
    def _attempt_alive(attempt):
        for pid in [attempt.get('host_pid'), attempt.get('cli_pid')]:
            if not pid:
                continue
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                continue
            except PermissionError:
                return True
        return False

    def _invalidate(self, board, task, reason):
        for decision in board['decisions']:
            if decision['task_id'] == task['id'] and decision['status'] != 'stale':
                decision['status'] = 'stale'
                decision['stale_reason'] = reason
        if task.get('release'):
            task.setdefault('release_history', []).append(task.pop('release'))
        task.update(candidate=None, submission=None, next_kind='work', review_required=self._risk_required(board, task))
        self._ready(task, reason + '；重新验证受影响结果')

    def _create_work(self, board, data, actor):
        request = NewWork.model_validate(data)
        self._agent(request.owner_id)
        agreement = self._add_agreement(board, request.agreement.model_dump(), actor)
        # Known project rules are host-owned floors, not agent exemptions.
        policy_words = {'security': ('权限', '鉴权', '安全漏洞', 'authentication', 'authorization'),
                        'migration': ('数据迁移', '数据库迁移', 'migration'),
                        'data': ('数据完整性', '持久化语义'), 'governance': ('调度规则', '授权规则', '审核规则')}
        text = (agreement['source'] + '\n' + agreement['goal']).lower()
        agreement['risks'] = sorted(set(agreement['risks']) | {
            risk for risk, words in policy_words.items() if any(word in text for word in words)})
        agreement['digest'] = digest({k: agreement[k] for k in Agreement.model_fields})
        task = self._new_task(request.title, request.owner_id, agreement,
                              [c['id'] for c in agreement['criteria']])
        # Authority is a host-owned fact originating in the human request.
        task['authorization'] = {'source': request.agreement.source, 'actor': actor, 'created_at': now(),
                                 'delivery': agreement['delivery'], 'publish': False,
                                 'capabilities': ['read', 'document'] + ([] if agreement['delivery'] == 'document' else ['develop', 'verify'])}
        task['policy'] = deepcopy(board['policy'])
        task['environment'] = self.environments.initial(task['id'])
        self._waiting(task, 'environment', '正在准备独立开发工作区', '系统')
        board['tasks'].append(task)
        return task

    def command(self, conversation_id, action, data, expected_revision, *, actor='human',
                mission_id=None, turn_id=None, operation_id=None):
        if action not in (HUMAN_ACTIONS if actor == 'human' else AGENT_ACTIONS):
            raise WorkError('该操作不在当前身份的权限范围内', 403)
        if action in ENVIRONMENT_ACTIONS:
            return self._environment_command(conversation_id, action, data, expected_revision, actor=actor,
                mission_id=mission_id, turn_id=turn_id, operation_id=operation_id)
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            fingerprint = digest({'action': action, 'data': data, 'actor': actor,
                                  'mission_id': mission_id, 'turn_id': turn_id})
            if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 120:
                raise WorkError('写操作必须携带 1–120 字符的稳定 operation_id', 422)
            if actor != 'human':
                operation_id = f'{mission_id}:{turn_id}:{operation_id}'
            prior = db.execute('SELECT fingerprint, result FROM work_operations WHERE conversation_id=? AND operation_id=?',
                               (conversation_id, operation_id)).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise WorkError('operation_id 已用于其他操作')
                return json.loads(prior['result'])
            if expected_revision != board['revision']:
                raise WorkError('任务板已更新，请重新读取后操作')
            task_id = data.get('task_id')
            if not task_id and data.get('decision_id'):
                decision = next((d for d in board.get('decisions', []) if d.get('id') == data['decision_id']), None)
                if decision:
                    task_id = decision.get('task_id')
            task = self._task(board, task_id) if task_id else None
            if Path(board['workspace_root']).resolve() != self.root and not (task and self._root_task(board, task).get('environment')):
                raise WorkError('工作区已变化，请在原工作区继续')
            if actor != 'human' and action != 'propose_work':
                task = self._actor_task(board, data, actor, mission_id, turn_id)
                if action not in {'review', 'accept_transfer'} and not (action == 'block' and task['attempts'][-1]['stage'] == 'handoff'):
                    self._require_owner(task, actor)
            if action in {'propose_work', 'accept_proposal', 'reject_proposal'}:
                if self.repo.get_conversation(conversation_id).status == 'archived':
                    raise WorkError('请先恢复会话再处理任务提议')
                board.setdefault('proposals', [])
            if action == 'propose_work':
                self._agent(actor)
                mission = self.repo.get_mission(mission_id) if mission_id else None
                events = self.repo.list_events_for_turn(mission_id, turn_id) if mission and turn_id else []
                started = any(e.event_type == 'turn.started' and e.payload.get('agent_id') == actor for e in events)
                ended = any(e.event_type in {'turn.completed', 'turn.failed', 'turn.blocked'} for e in events)
                if (not mission or mission.conversation_id != conversation_id or mission.status != MissionStatus.RUNNING
                        or not started or ended or self.execution_context(mission_id)):
                    raise WorkError('仅当前对话中正在回应的角色可以提出任务', 403)
                proposal_data = {**data, 'agreement': {**data.get('agreement', {}), 'delivery': data.get('agreement', {}).get('delivery', 'document')}}
                request = NewWork.model_validate(proposal_data)
                self._agent(request.owner_id)
                proposal = request.model_dump()
                # The model supplies the proposed scope; the host records the actual human source.
                proposal['agreement']['source'] = mission.goal[:12000]
                if len([p for p in board['proposals'] if p['status'] == 'pending']) >= 8:
                    raise WorkError('请先处理已有任务提议')
                for old in board['proposals']:
                    if old['status'] == 'pending' and old['created_by'] == actor:
                        old['status'] = 'superseded'
                board['proposals'].append({**proposal, 'id': uid('wp_'), 'status': 'pending',
                    'created_by': actor, 'mission_id': mission_id, 'turn_id': turn_id, 'created_at': now(),
                    'digest': digest(proposal), 'source_message_ids': [m.id for m in self.repo.list_messages_for_conversation(conversation_id) if m.role == 'user']})
            elif action in {'accept_proposal', 'reject_proposal'}:
                proposal = next((p for p in board['proposals'] if p['id'] == data.get('proposal_id')), None)
                if not proposal or proposal['status'] != 'pending':
                    raise WorkError('提议已处理或已被新版本替代，请重新核对')
                if action == 'accept_proposal':
                    task = self._create_work(board, {k: proposal[k] for k in ('title', 'owner_id', 'agreement')}, actor)
                    task['authorization']['proposal_id'] = proposal['id']
                    task['authorization']['proposal_digest'] = proposal['digest']
                    proposal.update(status='accepted', task_id=task['id'])
                else:
                    proposal['status'] = 'rejected'
                proposal.update(decided_by=actor, decided_at=now())
            elif action == 'create_work':
                task = self._create_work(board, data, actor)
            elif action == 'configure_workflow':
                from backend.app.work.workflow import configure
                configure(self, board, task, data.get('orchestration', {}))
            elif action == 'revise_work':
                self._require_root_quiet(board, task)
                agreement = Agreement.model_validate(data['agreement']).model_dump()
                previous = self._agreement(board, task)
                if agreement['delivery'] != previous['delivery']:
                    raise WorkError('交付终点变化请创建关联的新工作，以明确新的执行授权')
                new_contract = self._add_agreement(board, agreement, actor)
                for current in board['tasks']:
                    if current['root_id'] == task['id'] and current['status'] not in TERMINAL:
                        current.update(status='cancelled', blocker='约定修订，旧分工保留历史')
                    if current['root_id'] == task['id']:
                        for decision in board['decisions']:
                            if decision['task_id'] == current['id'] and decision['status'] == 'pending':
                                decision.update(status='stale', stale_reason='根需求已修订')
                task.update(contract_id=new_contract['id'], criterion_ids=[c['id'] for c in agreement['criteria']],
                            risks=agreement['risks'], depends_on=[], reviewer_id=None)
                self._invalidate(board, task, '用户修订了开发约定')
            elif action == 'refine_agreement':
                # Intake may make the original goal testable, never increase
                # delivery authority or rewrite the user's scope/source.
                old = self._agreement(board, task)
                if task['parent_id'] or task.get('submission') or task['evidence'] or task['depends_on']:
                    raise WorkError('已有产物或分工后，需求变更须提交人类决定')
                update = Agreement.model_validate({**{k: old[k] for k in Agreement.model_fields}, **data['agreement']}).model_dump()
                if any(update[k] != old[k] for k in ['source', 'goal', 'scope', 'delivery']):
                    raise WorkError('澄清验收不能改变用户目标、范围或授权')
                update['risks'] = sorted(set(update['risks']) | set(old['risks']))
                contract = self._add_agreement(board, update, actor)
                task.update(contract_id=contract['id'], criterion_ids=[c['id'] for c in update['criteria']], risks=update['risks'])
                task['attempts'][-1]['contract_id'] = contract['id']
            elif action in {'pause', 'resume', 'cancel', 'retry', 'adjust_budget', 'grant_execution'}:
                if not task:
                    raise WorkError('请选择工作卡', 422)
                root = self._root_task(board, task)
                if action == 'pause':
                    root['paused'] = True
                elif action == 'resume':
                    if root['status'] == 'cancelled':
                        raise WorkError('已取消的工作不能直接恢复')
                    root['paused'] = False
                    if root['waiting_reason'] == 'paused':
                        self._ready(root)
                elif action == 'cancel':
                    for current in board['tasks']:
                        if current['root_id'] == root['id'] and current['status'] not in TERMINAL:
                            current.update(status='cancelled', blocker='用户取消；已产生效果未自动撤销')
                elif action == 'adjust_budget':
                    limit = data.get('max_attempts')
                    if not isinstance(limit, int) or not root['attempts_used'] < limit <= 200:
                        raise WorkError('新预算必须高于已用次数且不超过 200', 422)
                    root['max_attempts'] = limit
                    root['no_progress'] = 0
                    if root['waiting_reason'] in {'budget', 'no_progress'}:
                        self._ready(root)
                elif action == 'grant_execution':
                    raise WorkError('文档工作不能升级执行权限；请带明确开发授权新建关联工作')
                elif action == 'retry':
                    if task['active_mission_id'] or task['status'] != 'waiting':
                        raise WorkError('只能重试已停止的等待工作')
                    if task['waiting_reason'] == 'release_unknown':
                        task['release']['status'] = 'unknown'
                    elif task['waiting_reason'] not in {'external', 'execution_failed', 'recovery'}:
                        raise WorkError('请先解决决定、依赖、预算或授权条件')
                    if task.get('old_process_alive') and self._attempt_alive(task['attempts'][-1]):
                        raise WorkError('旧进程仍存活，不能启动冲突写入')
                    task['old_process_alive'] = False
                    task['retries'] = 0
                    if task['waiting_reason'] != 'release_unknown':
                        task['next_kind'] = 'reconcile'
                        self._ready(task, '先只读核对已产生的效果，再继续；不得整轮重放')
            elif action in {'progress', 'continue_work'}:
                update = Checkpoint.model_validate(data)
                valid = {e['id'] for e in task['evidence']}
                if not set(update.evidence_ids) <= valid:
                    raise WorkError('进展引用的证据不存在', 422)
                task.update(progress=update.text, next_action=update.next_action)
                task['attempts'][-1]['structured'] = True
                task['attempts'][-1]['progress_refs'] = update.evidence_ids
            elif action == 'block':
                reason = str(data.get('reason', '')).strip()
                if not reason:
                    raise WorkError('等待必须说明具体原因', 422)
                self._waiting(task, 'external', reason, data.get('waiting_for', '负责人'))
                task['attempts'][-1]['structured'] = True
            elif action == 'raise_risk':
                risks = data.get('risks')
                if not isinstance(risks, list) or not set(risks) <= set(['local', *POLICY['review_risks']]):
                    raise WorkError('风险类型无效', 422)
                task['risks'] = sorted(set(task['risks']) | set(risks))
                root = self._root_task(board, task)
                root['risks'] = sorted(set(root['risks']) | set(risks))
                task['review_required'] = self._risk_required(board, task)
            elif action == 'request_decision':
                value = DecisionRequest.model_validate(data).model_dump()
                candidate = self._snapshot(board, task, value['artifact_paths'])
                decision = {**value, 'id': uid('wd_'), 'status': 'pending', 'created_at': now(),
                            'contract_id': task['contract_id'], 'candidate_id': candidate['id']}
                board['decisions'].append(decision)
                self._waiting(task, 'human', value['question'], '用户')
                task['attempts'][-1]['structured'] = True
            elif action == 'decide':
                self._decide(board, data, actor)
            elif action == 'submit':
                value = Submission.model_validate(data).model_dump()
                if value['reviewer_id']:
                    self._agent(value['reviewer_id'])
                if set(o['id'] for o in value['observations']) != set(task['criterion_ids']):
                    raise WorkError('提交必须覆盖本工作的全部验收项', 422)
                if len(value['observations']) != len(task['criterion_ids']):
                    raise WorkError('验收观察不能重复', 422)
                if any(self._task(board, dep)['status'] != 'done' for dep in task['depends_on']):
                    raise WorkError('必需依赖尚未完成')
                candidate = self._snapshot(board, task, value['artifact_paths'])
                task.update(candidate=candidate, candidate_sha=candidate['base_sha'], submission=value,
                            authors=sorted(set(task['authors']) | {actor}), progress=value['summary'])
                task['reviewer_id'] = value['reviewer_id'] or task.get('reviewer_id')
                task['review_required'] = self._risk_required(board, task) or bool(value['reviewer_id'])
                task['attempts'][-1].update(structured=True, submitted=True, submission_turn_id=turn_id)
                task['next_action'] = '核对产物与验收依据'
            elif action == 'review':
                self._review(board, task, data, actor, mission_id, turn_id)
            elif action == 'propose_revision':
                agreement = Agreement.model_validate(data['agreement']).model_dump()
                board['decisions'].append({'id': uid('wd_'), 'task_id': task['id'], 'kind': 'revision',
                    'question': '是否按新的约定调整目标和验收？', 'options': [{'id': 'accept', 'text': '接受修订'}, {'id': 'reject', 'text': '维持原约定'}],
                    'recommendation': data.get('reason', ''), 'proposal': agreement, 'artifact_paths': [],
                    'contract_id': task['contract_id'], 'candidate_id': None, 'status': 'pending', 'created_at': now()})
                self._waiting(task, 'human', '等待用户决定需求修订', '用户')
                task['attempts'][-1]['structured'] = True
            elif action == 'propose_transfer':
                self._agent(data.get('owner_id'))
                if not str(data.get('reason', '')).strip():
                    raise WorkError('移交必须说明原因', 422)
                task['transfer'] = {'from': actor, 'to': data['owner_id'], 'reason': data['reason'], 'status': 'pending'}
                task['transfer']['status'] = 'awaiting_acceptance'
                task['next_kind'] = 'handoff'
                task['attempts'][-1]['structured'] = True
                self._ready(task, '目标角色读取约定和检查点后明确接收责任；原负责人持续负责')
            elif action == 'accept_transfer':
                transfer = task.get('transfer')
                if not transfer or transfer['status'] not in {'pending', 'awaiting_acceptance'}:
                    raise WorkError('没有待接受的移交')
                if actor == 'human':
                    self._require_root_quiet(board, task, root_only=False)
                    transfer['status'] = 'awaiting_acceptance'
                    task['next_kind'] = 'handoff'
                    self._ready(task, '请目标角色读取约定与检查点，并明确接受责任；原负责人继续负责直到接收')
                else:
                    if actor != transfer['to'] or task['attempts'][-1]['stage'] != 'handoff':
                        raise WorkError('仅目标角色能确认接手', 403)
                    task['owner_id'] = actor
                    transfer.update(status='accepted', accepted_by=actor, accepted_at=now())
                    task['attempts'][-1]['structured'] = True
                    task['next_kind'] = 'work'
                    self._ready(task, '从已保存的约定、检查点与证据继续推进')
            task = task or (board['tasks'][-1] if board['tasks'] and action not in {'propose_work', 'reject_proposal'} else None)
            if task:
                task['updated_at'] = now()
            self._save(db, board, action, actor, {'task_id': task['id'] if task else None,
                'mission_id': mission_id, 'reason': str(data.get('reason') or '')[:4000]})
            db.execute('INSERT INTO work_operations VALUES (?, ?, ?, ?)',
                       (conversation_id, operation_id, fingerprint, json.dumps(board, ensure_ascii=False)))
            return board

    def _require_root_quiet(self, board, task, root_only=True):
        if not task or (root_only and task['parent_id']):
            raise WorkError('请选择根工作卡')
        if any(t['active_mission_id'] for t in board['tasks'] if t['root_id'] == task['root_id']):
            raise WorkError('请等待当前动作受控结束')

    def _decide(self, board, data, actor):
        decision = next((d for d in board['decisions'] if d['id'] == data.get('decision_id')), None)
        if not decision or decision['status'] != 'pending':
            raise WorkError('决定已处理或不存在')
        task = self._task(board, decision['task_id'])
        if task['status'] in TERMINAL or task['active_mission_id']:
            raise WorkError('请等待当前动作结束后再决定')
        if decision['contract_id'] != task['contract_id']:
            raise WorkError('决定对应的约定已失效')
        choice = data.get('option_id')
        if choice not in {x['id'] for x in decision['options']}:
            raise WorkError('请选择明确的有效选项；未回复不视为同意', 422)
        if decision.get('candidate_id') and snapshot(self._workspace(board, task))['id'] != decision['candidate_id']:
            decision.update(status='stale', stale_reason='产物内容已变化')
            self._ready(task, '决定对应的产物已变化，请重新准备具体稿与决定卡')
            return
        decision.update(status='answered', option_id=choice, actor=actor, answered_at=now(), note=str(data.get('note', ''))[:4000])
        self._root_task(board, task)['no_progress'] = 0
        if decision['kind'] == 'release':
            if choice == 'approve':
                # There is no implicit shell deployment. A configured release
                # adapter must consume this exact approval and reconcile effects.
                task['release'] = {'candidate_id': decision['candidate_id'], 'environment': decision['environment'],
                                   'approval_id': decision['id'], 'status': 'awaiting_adapter'}
                task['next_action'] = '配置受管发布入口后，只发布此获批版本并核对线上状态'
                self._waiting(task, 'release_configuration', '已批准此版本；尚未配置受管发布入口', '项目配置')
            else:
                decision.update(status='stale', stale_reason='用户要求修改候选')
                task.update(candidate=None, submission=None, next_kind='work')
                self._ready(task, '按用户的发布反馈修改候选')
        elif decision['kind'] == 'revision':
            if choice == 'accept':
                agreement = decision['proposal']
                if agreement['delivery'] != self._agreement(board, task)['delivery']:
                    raise WorkError('交付终点变化需要新的明确执行授权')
                contract = self._add_agreement(board, agreement, actor)
                task.update(contract_id=contract['id'], criterion_ids=[c['id'] for c in contract['criteria']], risks=contract['risks'])
                self._invalidate(board, task, '用户接受需求修订')
            else:
                self._ready(task, '保持原约定继续')
        elif decision['kind'] == 'authorization':
            self._waiting(task, 'authorization', '请用明确授权新建关联工作；本决定不自动提升执行权限', '用户')
        else:
            self._ready(task, '落实用户已记录的决定，再检查验收缺口')

    def delegate(self, conversation_id, mission_id, actor, turn_id, target, purpose, mode, operation_id):
        """Only invoke_agent calls this method: one A2A entrance, durable handoff."""
        if mode not in {'wait_reply', 'fire_and_forget'} or actor == target:
            raise WorkError('协作目标或模式无效', 422)
        self._agent(target)
        if not purpose.strip():
            raise WorkError('协作任务必须说明交付物', 422)
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            prior = next((e for e in board['events'] if e.get('operation_id') == operation_id), None)
            if prior:
                if prior.get('target') != target or prior.get('purpose') != purpose:
                    raise WorkError('交接操作 ID 冲突')
                return {'status': 'persisted', 'task_id': prior['child_id'], 'queued_position': 1,
                        'reply_pending': mode == 'wait_reply'}
            task = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
            if not task:
                raise WorkError('当前执行不属于自主工作')
            task = self._actor_task(board, {'task_id': task['id']}, actor, mission_id, turn_id)
            self._require_owner(task, actor)
            root = self._root_task(board, task)
            if root['attempts_used'] >= root['max_attempts'] or len(board['tasks']) >= 100:
                raise WorkError('累计协作预算已耗尽')
            # Re-entering an ancestor's responsibility would create a wait cycle.
            ancestors, current = [], task
            while current:
                ancestors.append(current['owner_id'])
                current = self._task(board, current['parent_id']) if current['parent_id'] else None
            if target in ancestors:
                raise WorkError('协作依赖会形成责任循环')
            parent_agreement = self._agreement(board, task)
            child_agreement = self._add_agreement(board, {
                **{k: parent_agreement[k] for k in Agreement.model_fields}, 'goal': purpose,
                'criteria': [{'id': 'OUT-1', 'text': '完成本协作任务所述产物，并说明实际核对结果'}],
                'validation_commands': [], 'risks': ['local'],
            }, actor)
            child = self._new_task(purpose[:200], target, child_agreement, ['OUT-1'], parent=task)
            child['next_action'] = purpose
            board['tasks'].append(child)
            task['depends_on'].append(child['id'])
            task['next_action'] = '集成协作结果并按原约定检查整体交付'
            task['attempts'][-1]['structured'] = True
            self._waiting(task, 'dependency', '等待协作结果后由负责人集成验收', target)
            self._save(db, board, 'delegated', actor, {'operation_id': operation_id, 'target': target,
                        'purpose': purpose, 'child_id': child['id'], 'task_id': task['id']})
            self.repo.add_event(mission_id, 'a2a.work_delegated', {'sender_agent_id': actor,
                'target_agent_id': target, 'task_id': child['id'], 'purpose': purpose, 'mode': mode})
            return {'status': 'persisted', 'task_id': child['id'], 'queued_position': 1, 'reply_pending': mode == 'wait_reply'}

    def _review(self, board, task, data, actor, mission_id, turn_id):
        if task['attempts'][-1]['stage'] != 'review' or actor in task['authors']:
            raise WorkError('只有非作者审核者能提交结论', 403)
        if data.get('candidate_id') != (task['candidate'] or {}).get('id') or not self._current_matches(board, task, task['candidate']):
            raise WorkError('候选内容已变化，请重新验证')
        if set(data.get('criterion_ids') or []) != set(task['criterion_ids']):
            raise WorkError('审核必须覆盖本次约定的验收项')
        if data.get('decision') not in {'approved', 'changes_requested'} or not str(data.get('note', '')).strip():
            raise WorkError('审核须有明确结论和依据', 422)
        evidence = task['evidence'][-1] if task['evidence'] else None
        if not evidence or not evidence['passed'] or evidence['candidate_id'] != task['candidate']['id'] or evidence['contract_id'] != task['contract_id']:
            raise WorkError('缺少当前约定与产物的验证依据')
        task['reviews'].append({'id': uid('wr_'), 'reviewer_id': actor, 'mission_id': mission_id,
            'turn_id': turn_id, 'contract_id': task['contract_id'], 'candidate_id': task['candidate']['id'],
            'criterion_ids': task['criterion_ids'], 'evidence_id': evidence['id'], 'decision': data['decision'],
            'note': data['note'], 'created_at': now()})
        task['attempts'][-1].update(structured=True, review_submitted=True)
        if data['decision'] == 'changes_requested':
            task['candidate'] = None
            task['submission'] = None
            self._ready(task, '修复审核指出的问题：' + data['note'])

    def _refresh(self, board):
        for task in board['tasks']:
            root = self._root_task(board, task)
            if task['status'] == 'waiting' and task['waiting_reason'] == 'recovery' and task.get('old_process_alive'):
                if not self._attempt_alive(task['attempts'][-1]):
                    task['old_process_alive'] = False
                    task['next_kind'] = 'reconcile'
                    self._ready(task, '旧进程已退出；先只读核对检查点和实际效果，再继续')
            if task['status'] == 'waiting' and task['waiting_reason'] == 'dependency' and not task['active_mission_id']:
                deps = [self._task(board, dep) for dep in task['depends_on']]
                if any(dep['status'] == 'cancelled' for dep in deps):
                    self._waiting(task, 'external', '依赖已取消，需要负责人调整', task['owner_id'])
                elif all(dep['status'] == 'done' for dep in deps):
                    self._ready(task, self._agreement(board, task)['goal'] if task.get('workflow_step_id')
                                else '集成协作结果并按原约定检查整体交付')
            if root['paused'] and task['status'] == 'ready':
                self._waiting(task, 'paused', '用户暂停，尚未启动后续动作', '用户')
            elif not root['paused'] and task['waiting_reason'] == 'paused':
                self._ready(task)

    def claim(self, conversation_id):
        # Workspace preparation runs outside the claim transaction; a slow or
        # failed prepare must not hold the write lock other boards depend on.
        self.ensure_environments(conversation_id)
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            self._refresh(board)
            # Shared worktrees are serialized across both legacy and adaptive work.
            if any(t.get('active_mission_id') or t.get('release', {}).get('status') == 'running' for row in db.execute('SELECT data FROM work_boards')
                   for t in json.loads(row['data'])['tasks']):
                return None
            if any(m.status in {MissionStatus.PENDING, MissionStatus.RUNNING, MissionStatus.AWAITING_INPUT}
                   for m in self.repo.list_missions()):
                return None
            selected = None
            for task in board['tasks']:
                root = self._root_task(board, task)
                if task['status'] != 'ready' or root['paused'] or root['status'] == 'cancelled':
                    continue
                env = root.get('environment')
                if env and env.get('status') != 'ready':
                    # Never fall back to the control plane checkout while the
                    # bound directory is missing or broken.
                    self._waiting(task, 'environment',
                                  '任务工作区未就绪：' + str(env.get('failure_reason') or env.get('status')),
                                  '系统' if env.get('status') in {'pending', 'preparing'} else '用户')
                    continue
                if not env and Path(board['workspace_root']).resolve() != self.root:
                    continue
                if root['attempts_used'] >= root['max_attempts']:
                    self._waiting(task, 'budget', '累计执行预算耗尽，请查看证据后决定', '用户')
                elif root['no_progress'] >= POLICY['max_no_progress']:
                    self._waiting(task, 'no_progress', '连续执行缺少可检查进展', '用户')
                elif all(self._task(board, dep)['status'] == 'done' for dep in task['depends_on']):
                    selected = task
                    break
                else:
                    self._waiting(task, 'dependency', '等待前置工作完成', '协作者')
            if not selected:
                # Persist derived waiting/resume state even if notifications were lost.
                row = db.execute('SELECT data FROM work_boards WHERE conversation_id=?', (conversation_id,)).fetchone()
                if row and json.loads(row['data']) != board:
                    self._save(db, board, 'conditions_checked', 'host')
                return None
            task = selected
            stage = task.get('next_kind') if task.get('next_kind') in {'review', 'reconcile', 'handoff'} else 'work'
            agent = task['reviewer_id'] if stage == 'review' else task['transfer']['to'] if stage == 'handoff' else task['owner_id']
            if not agent:
                self._waiting(task, 'external', '缺少具备能力的独立审核者', task['owner_id'])
                self._save(db, board, 'reviewer_unavailable', 'host')
                return None
            before = self._snapshot(board, task)
            mission = Mission(id=uid('m_'), title=task['title'], goal=task['next_action'],
                conversation_id=conversation_id, target_agent_id=agent,
                orchestration=OrchestrationConfig(mode=OrchestrationMode.SINGLE, participant_ids=[agent]))
            self.repo.create_mission(mission)
            task.update(status='running', active_mission_id=mission.id)
            task['attempts'].append({'mission_id': mission.id, 'agent_id': agent, 'stage': stage,
                'contract_id': task['contract_id'], 'started_at': now(), 'before_id': before['id'],
                'structured': False, 'host_pid': os.getpid(), 'provider_calls': 0})
            self._save(db, board, 'claimed', 'host', {'task_id': task['id'], 'mission_id': mission.id})
            self.repo.add_event(mission.id, 'adaptive.claimed', {'task_id': task['id'], 'root_id': task['root_id'], 'stage': stage})
            return mission

    def authorize_provider(self, mission_id, provider_id):
        mission = self.repo.get_mission(mission_id)
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, mission.conversation_id)
            task = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
            if not task or task['status'] == 'cancelled':
                raise WorkError('工作已停止')
            root = self._root_task(board, task)
            if root['attempts_used'] >= root['max_attempts']:
                raise WorkError('根任务累计预算耗尽')
            root['attempts_used'] += 1
            attempt = task['attempts'][-1]
            attempt['provider_calls'] += 1
            attempt.setdefault('providers', []).append(provider_id)
            self._save(db, board, 'provider_attempt', 'host', {'task_id': task['id'], 'provider_id': provider_id})

    def execution_context(self, mission_id):
        mission = self.repo.get_mission(mission_id)
        if not mission or not mission.conversation_id:
            return None
        board = self.read(mission.conversation_id)
        task = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
        if not task:
            return None
        root = self._root_task(board, task)
        agreement = self._agreement(board, task)
        context = {'task': task, 'agreement': agreement, 'authorization': root['authorization'],
                'policy': root.get('policy', board['policy']), 'workspace_root': str(self._workspace(board, task)),
                'children': [t for t in board['tasks'] if t['parent_id'] == task['id']],
                'decisions': [d for d in board['decisions'] if d['task_id'] == task['id']]}
        context['task'] = self._compact_task(task)
        context['children'] = [self._compact_task(t) for t in context['children']]
        return context

    @staticmethod
    def _compact_task(task):
        task = deepcopy(task)
        task['attempts'] = task['attempts'][-5:]
        task['reviews'] = task['reviews'][-3:]
        task['evidence'] = task['evidence'][-2:]
        for evidence in task['evidence']:
            for check in evidence['checks']:
                if len(check['output']) > 2000:
                    check['output'] = check['output'][-2000:]
                    check['truncated'] = True
        return task

    def agent_view(self, mission_id):
        mission = self.repo.get_mission(mission_id)
        board = self.read(mission.conversation_id)
        current = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
        if not current:
            raise WorkError('当前执行已过期')
        board['tasks'] = [self._compact_task(t) for t in board['tasks'] if t['root_id'] == current['root_id']]
        ids = {t['id'] for t in board['tasks']}
        contracts = {t['contract_id'] for t in board['tasks']}
        board['contracts'] = [c for c in board['contracts'] if c['id'] in contracts]
        board['decisions'] = [d for d in board['decisions'] if d['task_id'] in ids]
        board['events'] = board['events'][-40:]
        board.pop('legacy_board', None)
        return board

    def record_process(self, mission_id, pid):
        mission = self.repo.get_mission(mission_id)
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, mission.conversation_id)
            task = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
            if task:
                marker = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True,
                                        text=True, timeout=5).stdout.strip()
                task['attempts'][-1].update(cli_pid=pid, cli_started=marker)
                self._save(db, board, 'process_started', 'host', {'task_id': task['id']})

    def adopt_mission(self, conversation_id, task_id, mission):
        """The initial user request is already a mission; do not add an intake run."""
        self.ensure_environments(conversation_id, task_id)
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            task = self._task(board, task_id)
            environment = (task.get('environment') or {}).get('status')
            if environment != 'ready':
                self.repo.update_mission(mission.model_copy(update={'status': MissionStatus.COMPLETED}))
                self.repo.add_event(mission.id, 'adaptive.environment_blocked',
                                    {'task_id': task_id, 'environment_status': environment,
                                     'reason': (task.get('environment') or {}).get('failure_reason')})
                return False
            busy = any(t.get('active_mission_id') or t.get('release', {}).get('status') == 'running'
                       for row in db.execute('SELECT data FROM work_boards')
                       for t in json.loads(row['data'])['tasks']) or any(
                m.id != mission.id and m.status in {MissionStatus.PENDING, MissionStatus.RUNNING, MissionStatus.AWAITING_INPUT}
                for m in self.repo.list_missions())
            if busy:
                self.repo.update_mission(mission.model_copy(update={'status': MissionStatus.COMPLETED}))
                self.repo.add_event(mission.id, 'adaptive.queued', {'task_id': task_id})
                return False
            before = self._snapshot(board, task)
            task.update(status='running', active_mission_id=mission.id)
            task['attempts'].append({'mission_id': mission.id, 'agent_id': task['owner_id'], 'stage': 'work',
                'contract_id': task['contract_id'], 'started_at': now(), 'before_id': before['id'],
                'structured': False, 'host_pid': os.getpid(), 'provider_calls': 0})
            self._save(db, board, 'claimed', 'host', {'task_id': task_id, 'mission_id': mission.id})
            self.repo.add_event(mission.id, 'adaptive.claimed', {'task_id': task_id, 'root_id': task_id, 'stage': 'work'})
            return True

    def _accept(self, board, task):
        try:
            matches = self._current_matches(board, task, task['candidate'])
        except WorkError as exc:
            self._waiting(task, 'recovery', str(exc), task['owner_id'])
            return
        if not matches:
            self._invalidate(board, task, '验证后产物已变化')
            return
        if any(self._task(board, dep)['status'] != 'done' for dep in task['depends_on']):
            self._waiting(task, 'dependency', '整体交付依赖尚未满足')
            return
        evidence = task['evidence'][-1] if task['evidence'] else None
        if not evidence or not evidence['passed'] or evidence['contract_id'] != task['contract_id']:
            self._ready(task, '补齐本次约定的验证依据')
            return
        if self._risk_required(board, task):
            authors = {a for item in board['tasks'] if item['root_id'] == task['root_id']
                       and (item.get('submission') or {}).get('kind') == 'code' for a in item['authors']}
            task['authors'] = sorted(set(task['authors']) | authors)
            approved = any(r['decision'] == 'approved' and r['candidate_id'] == task['candidate']['id']
                           and r['contract_id'] == task['contract_id'] and r['reviewer_id'] not in task['authors']
                           for r in task['reviews'])
            if not approved:
                task['reviewer_id'] = task['reviewer_id'] or next((a for a in get_agent_registry().ids() if a not in task['authors']), None)
                if task['reviewer_id'] in task['authors']:
                    task['reviewer_id'] = next((a for a in get_agent_registry().ids() if a not in task['authors']), None)
                task['next_kind'] = 'review'
                self._ready(task, '独立核对原始约定、实际产物及证据，提交审核结论')
                return
        agreement = self._agreement(board, task)
        if not task['parent_id'] and agreement['delivery'] == 'online':
            from backend.app.work.releases import configuration
            try:
                config = configuration(self._workspace(board, task))
            except WorkError as exc:
                self._waiting(task, 'release_configuration', str(exc), '项目配置')
                return
            environment = config['environment'] if config else 'production'
            decision = next((d for d in board['decisions'] if d['task_id'] == task['id'] and d['kind'] == 'release'
                             and d['status'] != 'stale' and d['candidate_id'] == task['candidate']['id']), None)
            if not decision:
                board['decisions'].append({'id': uid('wd_'), 'task_id': task['id'], 'kind': 'release',
                    'question': f'是否批准这个候选版本发布到 {environment}？', 'options': [{'id': 'approve', 'text': '批准此版本上线'}, {'id': 'revise', 'text': '提出修改'}],
                    'recommendation': task['submission']['summary'], 'artifact_paths': task['submission']['artifact_paths'],
                    'contract_id': task['contract_id'], 'candidate_id': task['candidate']['id'],
                    'environment': environment, 'status': 'pending', 'created_at': now()})
            task['next_action'] = '等待用户批准候选版本及目标环境'
            self._waiting(task, 'human', '开发结果已验证，等待最终上线批准', '用户')
            return
        task.update(status='done', next_action='已按约定交付', waiting_reason=None, blocker=None)
        task['deliveries'].append({'contract_id': task['contract_id'], 'candidate_id': task['candidate']['id'],
                                  'summary': task['submission']['summary'], 'created_at': now()})

    def finish(self, conversation_id, mission_id):
        """Finalize only after the CLI exits; checks never hold a write txn."""
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            task = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
            if not task:
                return
            attempt = task['attempts'][-1]
            if attempt.get('finishing'):
                return
            attempt['finishing'] = True
            self._save(db, board, 'finishing', 'host', {'task_id': task['id'], 'mission_id': mission_id})
            candidate_task = deepcopy(task)
            agreement = self._agreement(board, task)
        mission = self.repo.get_mission(mission_id)
        checks = None
        error = None
        try:
            workspace = self._workspace(board, candidate_task)
            after = snapshot(workspace)
            if (mission and mission.status in SUCCESS and candidate_task.get('submission')
                    and candidate_task['attempts'][-1].get('submitted') and candidate_task['status'] != 'cancelled'):
                from backend.app.work.validation import validate
                checks = validate(self, conversation_id, candidate_task, agreement, mission_id, root=workspace)
        except (WorkError, OSError) as exc:
            after = None
            error = str(exc)
        with self.repo._transaction(immediate=True) as db:
            board = self._load(db, conversation_id)
            task = next((t for t in board['tasks'] if t['active_mission_id'] == mission_id), None)
            if not task:
                return
            attempt = task['attempts'][-1]
            root = self._root_task(board, task)
            attempt.update(completed_at=now(), outcome=mission.status.value if mission else 'missing')
            task['active_mission_id'] = None
            if task['status'] == 'cancelled':
                pass
            elif error:
                self._waiting(task, 'recovery', error, task['owner_id'])
            elif not mission or mission.status not in SUCCESS:
                self._waiting(task, 'execution_failed', '执行失败，须先核对实际效果再继续', task['owner_id'])
                if (after and after['id'] == attempt['before_id'] and task['retries'] < POLICY['max_retries']
                        and not any(e.event_type == 'adaptive.capability_unavailable' for e in self.repo.list_events_after(mission_id, 0))):
                    task['retries'] += 1
                    self._ready(task, '失败未改变项目快照；安全重试当前动作')
            elif checks:
                task['evidence'].append(checks)
                task['risks'] = sorted(set(task['risks']) | set(checks.get('detected_risks', [])))
                root['risks'] = sorted(set(root['risks']) | set(task['risks']))
                task['review_required'] = self._risk_required(board, task)
                if checks['passed']:
                    self._accept(board, task)
                else:
                    task.update(candidate=None, submission=None)
                    self._ready(task, '修复验证失败项，再提交可检查产物')
            elif attempt['stage'] == 'review' and attempt.get('review_submitted'):
                task['next_kind'] = 'work'
                if task['candidate']:
                    self._accept(board, task)
            elif task['status'] == 'waiting':
                pass
            elif attempt.get('structured'):
                if attempt['stage'] == 'reconcile':
                    task['next_kind'] = 'work'
                self._ready(task)
            else:
                self._waiting(task, 'execution_failed', '缺少结构化结果；聊天或 CLI 成功不能证明交付', task['owner_id'])
            progress = bool(checks) or bool(attempt.get('progress_refs')) or (after and after['id'] != attempt['before_id'])
            root['no_progress'] = 0 if progress else root['no_progress'] + 1
            self._refresh(board)
            self._save(db, board, 'attempt_finished', 'host', {'task_id': task['id'], 'mission_id': mission_id})

    def recover(self):
        with self.repo._transaction(immediate=True) as db:
            for row in db.execute('SELECT data FROM work_boards').fetchall():
                board = json.loads(row['data'])
                if board.get('protocol') != PROTOCOL:
                    continue
                changed = False
                for task in board['tasks']:
                    if not task['parent_id'] and task.get('environment'):
                        if self._recover_environment(board, task):
                            changed = True
                    if task.get('release', {}).get('status') == 'running':
                        task['release']['status'] = 'unknown'
                        self._waiting(task, 'release_unknown', '服务中断，须查询发布实际状态，不能重放', '项目发布入口')
                        changed = True
                    mission_id = task['active_mission_id']
                    if not mission_id:
                        continue
                    attempt = task['attempts'][-1]
                    if attempt.get('host_pid') == os.getpid():
                        # A second application instance in the same process must
                        # not fence a live dispatcher.
                        continue
                    mission = self.repo.get_mission(mission_id)
                    if mission:
                        self.repo.update_mission(mission.model_copy(update={'status': MissionStatus.BLOCKED}))
                    was_cancelled = task['status'] == 'cancelled'
                    task['active_mission_id'] = None
                    attempt.update(outcome='interrupted', completed_at=now())
                    if not was_cancelled:
                        self._waiting(task, 'recovery', '服务中断；先核对旧进程和已产生效果', task['owner_id'])
                    # A process still alive may retain filesystem write access.
                    old_pid = attempt.get('host_pid')
                    alive = False
                    if old_pid:
                        try:
                            os.kill(old_pid, 0)
                            alive = True
                        except ProcessLookupError:
                            pass
                        except PermissionError:
                            alive = True
                    cli_pid = attempt.get('cli_pid')
                    if cli_pid:
                        try:
                            os.kill(cli_pid, 0)
                            alive = True
                        except ProcessLookupError:
                            pass
                        except PermissionError:
                            alive = True
                    task['old_process_alive'] = alive
                    if not alive and not was_cancelled:
                        try:
                            current = snapshot(self._workspace(board, task))
                            if current['id'] == attempt['before_id']:
                                self._ready(task, '项目快照未变化；核对检查点后安全继续')
                            else:
                                task['next_kind'] = 'reconcile'
                                self._ready(task, '只读核对已改变文件与检查点，确定已产生效果后再安排后续动作；不得整轮重放')
                        except WorkError:
                            pass
                    changed = True
                self._refresh(board)
                if changed:
                    self._save(db, board, 'recovered', 'host')
