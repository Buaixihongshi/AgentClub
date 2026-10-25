"""Explicit, version-bound releases through a user-configured project adapter.

The model cannot supply deployment commands. Unknown results are queried, never
replayed. The verifier must attest the exact candidate and target environment.
"""
import hashlib
import json
import os
import subprocess
import time

from backend.app.adapters.subprocess_cli import kill_process_tree
from backend.app.work.service import WorkError, now, uid
from backend.app.work.runtime import managed_process

CONFIG_PATH = '.agenthub/work.local.json'


def configuration(root):
    path = root / CONFIG_PATH
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())['release']
        for key in ['command', 'verify_command']:
            if not isinstance(value[key], list) or not value[key] or not all(isinstance(x, str) and x for x in value[key]):
                raise ValueError('invalid command')
        if not isinstance(value['environment'], str) or not value['environment'].strip():
            raise ValueError('invalid environment')
        value['timeout'] = min(900, max(1, int(value.get('timeout', 300))))
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkError('项目发布配置无效，请检查 .agenthub/work.local.json') from exc


def _run(service, command, release, timeout, conversation_id, task_id, workspace, *, stop_on_cancel=False):
    env = {**os.environ, 'AGENTHUB_CANDIDATE_ID': release['candidate_id'],
           'AGENTHUB_TARGET_ENVIRONMENT': release['environment'], 'AGENTHUB_RELEASE_ID': release['id']}
    proc = subprocess.Popen(command, cwd=workspace, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    with managed_process(proc):
        with service.repo._transaction(immediate=True) as db:
            board = service._load(db, conversation_id)
            task = service._task(board, task_id)
            task['release']['cli_pid'] = proc.pid
            service._save(db, board, 'release_process_started', 'host', {'task_id': task_id})
        deadline = time.monotonic() + timeout
        while True:
            try:
                raw, _ = proc.communicate(timeout=min(.25, max(.01, deadline - time.monotonic())))
                return raw, proc.returncode
            except subprocess.TimeoutExpired:
                cancelled = stop_on_cancel and service._task(service.read(conversation_id), task_id)['status'] == 'cancelled'
                if cancelled or time.monotonic() >= deadline:
                    kill_process_tree(proc)
                    raw, _ = proc.communicate()
                    return raw + b'\nRelease interrupted; querying actual state is required', -1


def process(service, conversation_id):
    """Called only by the host dispatcher, including after a service restart."""
    with service.repo._transaction(immediate=True) as db:
        board = service._load(db, conversation_id)
        if any(t.get('active_mission_id') or t.get('release', {}).get('status') == 'running'
               for row in db.execute('SELECT data FROM work_boards') for t in json.loads(row['data'])['tasks']):
            return False
        preparing = next((t for t in board['tasks'] if t['status'] == 'waiting'
                          and t['waiting_reason'] == 'release_configuration' and not t.get('release')
                          and t.get('candidate') and not service._root_task(board, t)['paused']), None)
        if preparing:
            service._accept(board, preparing)
            service._save(db, board, 'release_configuration_restored', 'host', {'task_id': preparing['id']})
            return True
        task = next((t for t in board['tasks'] if t.get('release', {}).get('status') in {'awaiting_adapter', 'unknown'}
                     and t['status'] == 'waiting' and not service._root_task(board, t)['paused']), None)
        if not task:
            return False
        workspace = service._workspace(board, task)
        try:
            config = configuration(workspace)
        except WorkError:
            return False  # The work card keeps its actionable configuration error.
        if not config:
            return False
        release = task['release']
        if release['status'] == 'unknown' and release.get('cli_pid'):
            try:
                os.kill(release['cli_pid'], 0)
                return False
            except ProcessLookupError:
                pass
            except PermissionError:
                return False
        approval = next((d for d in board['decisions'] if d['id'] == release['approval_id']), None)
        if not approval or approval['status'] != 'answered' or approval.get('option_id') != 'approve':
            raise WorkError('缺少有效发布批准')
        if config['environment'] != release['environment'] or not service._current_matches(board, task, task['candidate']):
            service._invalidate(board, task, '候选版本或发布环境变化，需要重新验证与批准')
            service._save(db, board, 'release_approval_stale', 'host', {'task_id': task['id']})
            return True
        verify_only = release['status'] == 'unknown'
        release.setdefault('id', uid('release_'))
        release.update(status='running', host_pid=os.getpid(), started_at=now(),
                       adapter_digest=hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())
        service._save(db, board, 'release_started', 'host', {'task_id': task['id'], 'release_id': release['id'], 'verify_only': verify_only})
        task_id = task['id']
        release = dict(release)
    deployment, code = b'', None
    try:
        if not verify_only:
            deployment, code = _run(service, config['command'], release, config['timeout'], conversation_id, task_id, workspace, stop_on_cancel=True)
        verification, verify_code = _run(service, config['verify_command'], release, config['timeout'], conversation_id, task_id, workspace)
        try:
            report = json.loads(verification)
        except (ValueError, TypeError):
            report = {}
        published = verify_code == 0 and report == {'candidate_id': release['candidate_id'],
                         'environment': release['environment'], 'status': 'published'}
    except OSError as exc:
        verification, verify_code, published = str(exc).encode(), -1, False
    with service.repo._transaction(immediate=True) as db:
        board = service._load(db, conversation_id)
        task = service._task(board, task_id)
        record = task['release']
        log_id = uid('wl_')
        db.execute('INSERT INTO work_check_logs VALUES (?, ?, ?)',
                   (log_id, conversation_id, deployment + b'\n--- actual-state query ---\n' + verification))
        record.update(status='published' if published else 'unconfirmed', finished_at=now(), log_id=log_id,
                      command_exit_code=code, verify_exit_code=verify_code, verification=report if published else None)
        if published and task['status'] != 'cancelled':
            task.update(status='done', next_action='已批准版本已上线并核对实际状态', waiting_reason=None, blocker=None)
            task['deliveries'].append({'contract_id': task['contract_id'], 'candidate_id': release['candidate_id'],
                                      'summary': '已发布并确认线上版本', 'release_id': release['id'], 'created_at': now()})
        elif task['status'] != 'cancelled':
            service._waiting(task, 'release_unknown', '发布实际结果未确认；保留日志且不重放发布命令', '项目发布入口')
        service._save(db, board, 'release_finished', 'host', {'task_id': task_id, 'release_id': release['id']})
    return True
