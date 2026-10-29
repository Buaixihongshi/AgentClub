"""Per-root-task workspaces: one branch and one directory per independent work.

Bindings live on the root task itself; retries, role changes and restarts
resolve the same record instead of creating a second environment. Directory
preparation reuses plain git worktrees under a dedicated root, so a failed
prepare never falls back to developing inside the control plane's checkout.
"""
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from backend.app.work.service import WorkError, now, uid

DEFAULT_PREVIEW_COMMAND = None
PREVIEW_LOG_TAIL = 16000

# Detached previews outlive the request that started them; the watchdog pipe
# still ties their lifetime to this host process (host crash kills the group).
_WATCHDOG_FDS: dict[str, int] = {}

_WATCHDOG = Path(__file__).resolve().parents[1] / 'adapters' / 'process_watchdog.py'


def _git(cwd, args, timeout=120):
    try:
        result = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True,
                                text=True, timeout=timeout, check=True)
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        stderr = (getattr(exc, 'stderr', '') or '').strip()
        raise WorkError('git {} 失败：{}'.format(' '.join(args), (stderr or str(exc))[:500])) from exc


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _terminate(pid):
    """Stop a preview process group; code and records are left in place."""
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(.05)


def _attach_watchdog(instance_id, pid):
    read_fd, write_fd = os.pipe()
    try:
        subprocess.Popen([sys.executable, str(_WATCHDOG), str(read_fd), str(pid)],
                         pass_fds=(read_fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    _WATCHDOG_FDS[instance_id] = write_fd


def _release_watchdog(instance_id):
    fd = _WATCHDOG_FDS.pop(instance_id, None)
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


class TaskEnvironments:
    def __init__(self, repository, root, worktree_root=None, preview_command=None, preview_timeout=600):
        self.repo = repository
        self.root = Path(root).resolve()
        # Explicit roots win; isolated deployments redirect via the environment
        # so their bindings never mix with the daily checkout's siblings.
        self.worktree_root = Path(worktree_root or os.environ.get('YIREN_TASK_WORKTREE_ROOT')
                                  or self.root.parent / f'{self.root.name}-worktrees').resolve()
        self.preview_command = list(preview_command or DEFAULT_PREVIEW_COMMAND) if (preview_command or DEFAULT_PREVIEW_COMMAND) else None
        self.preview_timeout = preview_timeout

    def initial(self, task_id):
        short = task_id[3:13] if task_id.startswith('wt_') else task_id[:10]
        return {'short_id': short, 'status': 'pending', 'project_root': None, 'project_id': None,
                'worktree_root': str(self.worktree_root),
                'base_ref': None, 'base_sha': None, 'branch': None, 'workspace_dir': None,
                'failure_reason': None, 'created_at': now(), 'updated_at': now(),
                'preview': {'status': 'stopped', 'url': None, 'ports': None, 'pid': None,
                            'instance_id': None, 'candidate_id': None, 'failure_reason': None,
                            'log_id': None, 'started_at': None}}

    # -- workspace preparation ------------------------------------------------

    def _project(self):
        try:
            return Path(_git(self.root, ['rev-parse', '--show-toplevel']).strip()).resolve()
        except WorkError as exc:
            # The desktop app starts in a private folder until the user picks a repository.
            hint = '；请在菜单“Unity › 选择项目仓库…”中选择 Git 仓库后重试' \
                if os.environ.get('YIREN_ENVIRONMENT') == 'desktop' else ''
            raise WorkError('无法确定默认项目仓库，不能创建任务工作区' + hint + '：' + str(exc)) from exc

    def _project_id(self, project):
        return Path(_git(project, ['rev-parse', '--path-format=absolute', '--git-common-dir']).strip()).resolve()

    def _baseline(self, project):
        try:
            remotes = _git(project, ['remote']).split()
            if 'origin' in remotes:
                remotes.remove('origin')
                remotes.insert(0, 'origin')
        except WorkError:
            remotes = []
        if remotes:
            try:
                _git(project, ['fetch', '--quiet', remotes[0], 'main'], timeout=30)
            except WorkError:
                pass  # Offline hosts keep the last fetched mainline; the SHA is still recorded.
        for ref in [f'{remotes[0]}/main', f'{remotes[0]}/master', 'main', 'master'] if remotes \
                else ['main', 'master']:
            try:
                sha = _git(project, ['rev-parse', '--verify', '--quiet', ref + '^{commit}']).strip()
            except WorkError:
                continue
            if sha:
                return ref, sha
        raise WorkError('仓库中没有可用主线分支，无法确定任务基线')

    def verify(self, env):
        """True when the recorded directory still belongs to this task's repo and branch."""
        try:
            directory = Path(env['workspace_dir'])
            owned_root = Path(env.get('worktree_root') or self.worktree_root).resolve()
            if owned_root not in directory.resolve().parents:
                return False
            if _git(directory, ['rev-parse', '--is-inside-work-tree']).strip() != 'true':
                return False
            if _git(directory, ['rev-parse', '--abbrev-ref', 'HEAD']).strip() != env['branch']:
                return False
            return self._project_id(directory) == Path(env['project_id'])
        except (WorkError, OSError, KeyError, TypeError):
            return False

    def _verify_owned(self, project, target, branch, project_id, owned_root):
        if owned_root not in target.resolve().parents:
            raise WorkError(f'目录 {target} 不在任务工作区根之内')
        if _git(target, ['rev-parse', '--is-inside-work-tree']).strip() != 'true':
            raise WorkError(f'目录 {target} 已存在且不是本仓库的工作树')
        current = _git(target, ['rev-parse', '--abbrev-ref', 'HEAD']).strip()
        if current != branch:
            raise WorkError(f'目录 {target} 的分支是 {current}，不属于本任务')
        if self._project_id(target) != project_id:
            raise WorkError(f'目录 {target} 属于其他仓库')

    def prepare(self, env):
        """Idempotently create or re-find the bound worktree; never touches other checkouts."""
        env = dict(env)
        env.update(status='preparing', failure_reason=None, updated_at=now())
        try:
            project = self._project()
            project_id = self._project_id(project)
            if env.get('base_sha'):
                base_ref, base_sha = env['base_ref'], env['base_sha']
            else:
                base_ref, base_sha = self._baseline(project)
            owned_root = Path(env.get('worktree_root') or self.worktree_root).resolve()
            branch = env.get('branch') or f"codex/task-{env['short_id']}"
            target = (owned_root / 'features' / env['short_id']).resolve()
            if owned_root not in target.parents:
                raise WorkError('任务目录指向工作区根之外，未创建工作树')
            env.update(worktree_root=str(owned_root), base_ref=base_ref, base_sha=base_sha, branch=branch)
            if target.exists():
                self._verify_owned(project, target, branch, project_id, owned_root)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    existing = _git(project, ['rev-parse', '--verify', '--quiet', branch + '^{commit}']).strip()
                except WorkError:
                    existing = ''
                try:
                    if existing:
                        _git(project, ['worktree', 'add', str(target), branch], timeout=180)
                    else:
                        _git(project, ['worktree', 'add', '-b', branch, str(target), base_sha], timeout=180)
                except WorkError:
                    # A concurrent prepare may have won the race; ownership is what matters.
                    if not target.exists():
                        raise
                    self._verify_owned(project, target, branch, project_id, owned_root)
            env.update(status='ready', project_root=str(project), project_id=str(project_id),
                       base_ref=base_ref, base_sha=base_sha, branch=branch,
                       workspace_dir=str(target), updated_at=now())
        except (WorkError, OSError) as exc:
            env.update(status='failed', failure_reason=str(exc)[:2000], updated_at=now())
        return env

    def stop_preview_process(self, preview):
        if preview.get('managed'):
            from scripts import environment_supervisor as manager
            from scripts.environment_common import EnvironmentError
            root = Path(preview['checkout'])
            try:
                record = manager.read_record(root, 'development')
                if not record or record.get('instance_id') != preview.get('instance_id'):
                    raise WorkError('预览归属与当前运行实例不一致，未停止进程')
                if manager.alive(record):
                    manager.stop(root, 'development')
            except (EnvironmentError, OSError, ValueError, KeyError) as exc:
                if isinstance(exc, WorkError):
                    raise
                raise WorkError('预览未停止：' + str(exc)) from exc
            return
        if preview.get('pid'):
            from scripts.environment_supervisor import process_start
            if preview.get('pid_started_at') != process_start(preview['pid']):
                raise WorkError('预览进程身份已变化，未停止进程')
            _terminate(preview['pid'])
        if preview.get('instance_id'):
            _release_watchdog(preview['instance_id'])

    # -- preview --------------------------------------------------------------

    def _log_tail(self, workspace, instance):
        parts = []
        preview_dir = workspace / '.yiren' / 'preview'
        for name in (f'{instance}.log', '../backend.log', '../frontend.log'):
            path = (preview_dir / name).resolve()
            try:
                if path.is_relative_to(workspace) and path.is_file():
                    parts.append(f'--- {path.name} ---\n' + path.read_text(errors='replace')[-PREVIEW_LOG_TAIL // 2:])
            except OSError:
                continue
        return '\n'.join(parts)

    def start_preview(self, service, conversation_id, task_id, expected_revision):
        with service.repo._transaction(immediate=True) as db:
            board = service._load(db, conversation_id)
            if board['revision'] != expected_revision:
                raise WorkError('任务板已更新，请重新读取后操作')
            task = service._task(board, task_id)
            if task['parent_id']:
                raise WorkError('预览属于根工作卡', 422)
            env = task.get('environment') or {}
            if env.get('status') != 'ready':
                raise WorkError('任务工作区尚未就绪，不能启动预览', 409)
            if self.preview_command is None and (any(t.get('active_mission_id') for t in board['tasks'] if t['root_id'] == task_id)
                    or (task['status'] == 'ready' and not task['paused'])):
                raise WorkError('请先暂停任务或等待当前动作结束，再启动预览')
            if not self.verify(env):
                raise WorkError('工作区目录或分支与任务绑定不一致，请核对后重试')
            preview = env.get('preview') or {}
            if preview.get('status') in {'starting', 'ready'}:
                raise WorkError('预览已在运行，请先停止', 409)
            self.stop_preview_process(preview)
            instance = uid('pv_')
            env['preview'] = {'status': 'starting', 'instance_id': instance, 'candidate_id':
                              (task.get('candidate') or {}).get('id'), 'url': None, 'ports': None,
                              'pid': None, 'failure_reason': None, 'log_id': None, 'started_at': now()}
            task['environment'] = env
            workspace = Path(env['workspace_dir'])
            service._save(db, board, 'preview_starting', 'human', {'task_id': task_id, 'instance_id': instance})
        threading.Thread(target=self._run_preview, args=(service, conversation_id, task_id, workspace, instance),
                         daemon=True).start()
        return service.read(conversation_id)

    def _run_preview(self, service, conversation_id, task_id, workspace, instance):
        outcome = {'status': 'failed', 'failure_reason': None, 'log_id': None, 'url': None,
                   'pid': None, 'ports': None}
        if self.preview_command is None:
            return self._run_managed_preview(service, conversation_id, task_id, workspace, instance)
        proc = None
        pid_started_at = None
        log_dir = workspace / '.yiren' / 'preview'
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            ports = {'backend': _free_port(), 'frontend': _free_port()}
            env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(ports['backend']),
                   'FRONTEND_PORT': str(ports['frontend']), 'NO_COLOR': '1'}
            with open(log_dir / f'{instance}.log', 'ab') as output:
                proc = subprocess.Popen(self.preview_command, cwd=workspace, env=env,
                                        stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            _attach_watchdog(instance, proc.pid)
            from scripts.environment_supervisor import process_start
            pid_started_at = process_start(proc.pid)
            # Record the pid early so a concurrent stop can reach the process.
            with service.repo._transaction(immediate=True) as db:
                board = service._load(db, conversation_id)
                live = next((t for t in board['tasks'] if t['id'] == task_id), None)
                if live and (live.get('environment', {}).get('preview') or {}).get('instance_id') == instance:
                    live['environment']['preview']['pid'] = proc.pid
                    service._save(db, board, 'preview_process_started', 'host',
                                  {'task_id': task_id, 'instance_id': instance})
                else:
                    self.stop_preview_process({'pid': proc.pid, 'instance_id': instance, 'pid_started_at': pid_started_at})
                    return
            deadline = time.monotonic() + self.preview_timeout
            healthy = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise WorkError(f'预览进程已退出（退出码 {proc.returncode}）')
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.settimeout(.5)
                    healthy = probe.connect_ex(('127.0.0.1', ports['frontend'])) == 0
                if healthy:
                    break
                time.sleep(.5)
            if not healthy:
                raise WorkError(f'预览未在 {self.preview_timeout} 秒内就绪；日志已保留')
            outcome.update(status='ready', url=f"http://127.0.0.1:{ports['frontend']}",
                           pid=proc.pid, pid_started_at=pid_started_at, ports=ports)
        except (WorkError, OSError) as exc:
            outcome['failure_reason'] = str(exc)[:2000]
        # A stopped preview keeps the log entry point; code and records survive failures.
        if outcome['status'] != 'ready':
            if proc is not None and proc.poll() is None:
                self.stop_preview_process({'pid': proc.pid, 'instance_id': instance, 'pid_started_at': pid_started_at})
            tail = self._log_tail(workspace, instance)
            if tail:
                log_id = uid('wl_')
                with service.repo._transaction() as db:
                    db.execute('INSERT INTO work_check_logs VALUES (?, ?, ?)',
                               (log_id, conversation_id, tail.encode()))
                outcome['log_id'] = log_id
        with service.repo._transaction(immediate=True) as db:
            board = service._load(db, conversation_id)
            task = next((t for t in board['tasks'] if t['id'] == task_id), None)
            env = (task or {}).get('environment') or {}
            if task and (env.get('preview') or {}).get('instance_id') == instance \
                    and env['preview'].get('status') == 'starting':
                preview = dict(env['preview'])
                preview.update({k: v for k, v in outcome.items() if v is not None})
                if outcome['status'] != 'ready':
                    preview.update(url=None, pid=None)
                env['preview'] = preview
                task['environment'] = env
                service._save(db, board, 'preview_ready' if outcome['status'] == 'ready' else 'preview_failed',
                              'host', {'task_id': task_id, 'instance_id': instance})

    def _run_managed_preview(self, service, conversation_id, task_id, workspace, instance):
        from scripts import environment_supervisor as manager
        from scripts.environment_common import EnvironmentError
        outcome = {'status': 'failed', 'failure_reason': None, 'url': None}
        log_dir = workspace / '.yiren' / 'preview'
        setup_log = (log_dir / (instance + '.log')).resolve()
        try:
            if not setup_log.is_relative_to(workspace):
                raise EnvironmentError('候选的私有状态目录指向工作区之外，未准备或启动预览')
            log_dir.mkdir(parents=True, exist_ok=True)
            if manager.alive(manager.read_record(workspace, 'development')):
                raise EnvironmentError('工作区已有运行实例，请先核对其归属并停止')
            with setup_log.open('ab') as output:
                manager.setup(workspace, install=True, output=output)
            record = manager.start(workspace, 'development', detach=True, allocate=True,
                                   real=False, reload=False, install=False)
            outcome.update(status='ready', managed=True, checkout=str(workspace),
                           instance_id=record['instance_id'], pid=record['supervisor_pid'],
                           pid_started_at=record['supervisor_started_at'],
                           ports={'backend': record['backend_port'], 'frontend': record['frontend_port']},
                           url=f"http://127.0.0.1:{record['frontend_port']}")
        except (EnvironmentError, OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            outcome['failure_reason'] = (f'依赖准备或启动命令失败（退出码 {exc.returncode}），请查看预览日志后重试。'
                if isinstance(exc, subprocess.CalledProcessError) else '预览未就绪，请查看日志并核对候选的启动配置。')
            tails = [str(exc)]
            if setup_log.is_relative_to(workspace):
                try:
                    with setup_log.open('a') as output:
                        output.write('\n' + str(exc) + '\n')
                    tails.append(setup_log.read_text(errors='replace')[-PREVIEW_LOG_TAIL // 2:])
                except OSError:
                    pass
            for name in ('supervisor.log', 'backend.log', 'frontend.log'):
                path = (workspace / '.yiren/development/runtime' / name).resolve()
                if path.is_relative_to(workspace) and path.is_file():
                    tails.append(path.read_text(errors='replace')[-PREVIEW_LOG_TAIL // 3:])
            if tails:
                log_id = uid('wl_')
                with service.repo._transaction(immediate=True) as db:
                    db.execute('INSERT INTO work_check_logs VALUES (?, ?, ?)',
                               (log_id, conversation_id, '\n'.join(tails).encode()))
                outcome['log_id'] = log_id
        stale = False
        with service.repo._transaction(immediate=True) as db:
            board = service._load(db, conversation_id)
            task = service._task(board, task_id)
            preview = task['environment'].get('preview') or {}
            if preview.get('instance_id') != instance or preview.get('status') != 'starting':
                stale = True
            else:
                task['environment']['preview'] = {**preview, **outcome}
                service._save(db, board, 'preview_ready' if outcome['status'] == 'ready' else 'preview_failed',
                              'host', {'task_id': task_id, 'reason': outcome.get('failure_reason')})
        if stale and outcome['status'] == 'ready':
            self.stop_preview_process(outcome)

    def reconcile_preview(self, preview):
        if preview.get('managed'):
            from scripts import environment_supervisor as manager
            try:
                record = manager.read_record(Path(preview['checkout']), 'development')
                if (record and manager.alive(record) and record.get('instance_id') == preview.get('instance_id')
                        and record.get('checkout') == preview.get('checkout')):
                    return preview  # A separately managed preview can survive its controller restart.
            except (OSError, ValueError, KeyError):
                pass  # A broken candidate record must not prevent the controller from starting.
            return {**preview, 'status': 'failed', 'url': None, 'pid': None,
                    'failure_reason': '预览已停止或实例身份变化，请核对后重试'}
        self.stop_preview_process(preview)
        return {**preview, 'status': 'stopped', 'url': None, 'pid': None,
                'failure_reason': '服务重启，预览已停止；可重新启动'}

    def stop_preview(self, service, conversation_id, task_id, expected_revision):
        with service.repo._transaction() as db:
            board = service._load(db, conversation_id)
            if board['revision'] != expected_revision:
                raise WorkError('任务板已更新，请重新读取后操作')
            task = service._task(board, task_id)
            if task['parent_id']:
                raise WorkError('预览属于根工作卡', 422)
            preview = dict((task.get('environment') or {}).get('preview') or {})
            if preview.get('status') not in {'starting', 'ready'}:
                raise WorkError('预览未在运行', 409)
            if preview['status'] == 'starting':
                raise WorkError('预览正在准备，请等准备完成后停止')
        self.stop_preview_process(preview)
        with service.repo._transaction(immediate=True) as db:
            board = service._load(db, conversation_id)
            task = service._task(board, task_id)
            current = task['environment'].get('preview') or {}
            if current.get('instance_id') != preview.get('instance_id'):
                raise WorkError('预览实例已变化，请刷新任务')
            task['environment']['preview'] = {**current, 'status': 'stopped', 'url': None, 'pid': None}
            service._save(db, board, 'preview_stopped', 'human', {'task_id': task_id})
        return service.read(conversation_id)
