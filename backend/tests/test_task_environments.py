"""多任务卡与独立开发工作区验收，见 docs/plans/2026-10-26-task-workspaces.md。"""
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.domain.models import Conversation, MissionStatus
from backend.app.work.adaptive import AdaptiveWork
from backend.app.work.service import WorkError

PREVIEW_SERVER = [sys.executable, '-c',
                  'import functools, http.server, os\n'
                  'port = int(os.environ["FRONTEND_PORT"])\n'
                  'handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=os.getcwd())\n'
                  'http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()\n']
FAILING_PREVIEW = [sys.executable, '-c', 'import sys; print("boom", file=sys.stderr); sys.exit(3)']


@pytest.fixture
def work(repository, tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'docs').mkdir()
    (root / 'docs' / 'readme.md').write_text('主线文档\n')
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'initial'], check=True)
    for cid in ('c_env_a', 'c_env_b'):
        repository.create_conversation(Conversation(id=cid, title=cid))
    return AdaptiveWork(repository, root, check_timeout=1,
                        preview_command=PREVIEW_SERVER, preview_timeout=20)


def create(service, cid, title='独立任务'):
    return service.command(cid, 'create_work', {'title': title, 'owner_id': 'chulan', 'agreement': {
        'source': '用户要求', 'goal': title, 'scope': '本项目', 'delivery': 'development',
        'criteria': [{'id': 'AC-1', 'text': '交付文件包含真实结果'}]}},
        service.read(cid)['revision'], operation_id=uuid4().hex)['tasks'][-1]['id']


def env_of(service, cid, task_id=None):
    board = service.read(cid)
    task = next(t for t in board['tasks'] if t['id'] == (task_id or next(x['id'] for x in board['tasks'] if not x['parent_id'])))
    return task['environment']


def run_mission(service, cid):
    mission = service.claim(cid)
    assert mission is not None
    service.repo.update_mission(mission.model_copy(update={'status': MissionStatus.RUNNING}))
    service.authorize_provider(mission.id, 'test')
    return mission


def settle(service, cid, mission):
    service.repo.update_mission(mission.model_copy(update={'status': MissionStatus.COMPLETED}))
    service.finish(cid, mission.id)


def wait_preview(service, cid, status, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        env = env_of(service, cid)
        if env['preview']['status'] == status:
            return env['preview']
        time.sleep(.05)
    return env_of(service, cid)['preview']


def listening(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def test_two_root_works_bind_independent_workspaces(work):
    # M-01/M-02：接单自动建卡并绑定独立分支与目录；第二项任务得到另一份目录。
    create(work, 'c_env_a', '文件上传')
    create(work, 'c_env_b', '共享任务栏')
    mission = run_mission(work, 'c_env_a')
    # M-04：首版一次执行一个开发动作，第二项任务排队等待。
    assert work.claim('c_env_b') is None  # 排队前仍会准备好自己的工作区。
    env_a, env_b = env_of(work, 'c_env_a'), env_of(work, 'c_env_b')
    assert env_a['status'] == 'ready' and env_b['status'] == 'ready'
    assert env_a['workspace_dir'] != env_b['workspace_dir']
    assert env_a['branch'].startswith('codex/task-') and env_a['branch'] != env_b['branch']
    assert Path(env_a['workspace_dir']).is_relative_to(work.environments.worktree_root / 'features')
    assert (Path(env_a['workspace_dir']) / 'docs' / 'readme.md').read_text() == '主线文档\n'
    board_b = work.read('c_env_b')
    assert board_b['execution']['active'] == [{'conversation_id': 'c_env_a',
        'task_id': work.read('c_env_a')['tasks'][0]['id'], 'title': '文件上传'}]
    settle(work, 'c_env_a', mission)
    assert work.read('c_env_b')['execution']['active'] == []
    assert work.claim('c_env_b') is not None  # A 结束后 B 接续执行。


def test_execution_and_validation_use_bound_directory(work):
    # M-03：文件修改、验证和产物读取均发生在任务目录。
    task_id = create(work, 'c_env_a')
    mission = run_mission(work, 'c_env_a')
    context = work.execution_context(mission.id)
    bound = Path(env_of(work, 'c_env_a')['workspace_dir'])
    assert context['workspace_root'] == str(bound)
    assert bound != work.root
    target = bound / 'docs' / 'result.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('真实结果')
    work.command('c_env_a', 'submit', {'task_id': task_id, 'kind': 'code', 'summary': '已交付',
        'artifact_paths': ['docs/result.md'],
        'observations': [{'id': 'AC-1', 'text': '读取文件，内容为真实结果'}]},
        work.read('c_env_a')['revision'], actor='chulan', mission_id=mission.id,
        turn_id='turn_env', operation_id='submit-1')
    board = work.read('c_env_a')
    task = board['tasks'][0]
    settle(work, 'c_env_a', mission)
    task = work.read('c_env_a')['tasks'][0]
    assert task['status'] == 'done'
    assert task['evidence'][0]['passed']
    # 日常数据库不出现在候选工作区内。
    assert not (bound / 'data' / 'yirenhub.sqlite3').exists()


def test_repeat_requests_and_restart_reuse_binding(work):
    # M-07：刷新或重复请求不重复建目录；重启后仍找到原绑定。
    create(work, 'c_env_a')
    run_mission(work, 'c_env_a')
    first = env_of(work, 'c_env_a')
    work.ensure_environments('c_env_a', force=True)
    again = env_of(work, 'c_env_a')
    assert again['workspace_dir'] == first['workspace_dir'] and again['branch'] == first['branch']
    features = work.environments.worktree_root / 'features'
    assert sorted(p.name for p in features.iterdir()) == [first['short_id']]
    restarted = AdaptiveWork(work.repo, work.root, check_timeout=1, preview_command=PREVIEW_SERVER)
    restarted.recover()
    assert env_of(restarted, 'c_env_a')['workspace_dir'] == first['workspace_dir']
    assert env_of(restarted, 'c_env_a')['status'] == 'ready'


def test_prepare_failure_is_visible_and_retryable(work, tmp_path):
    create(work, 'c_env_a')
    create(work, 'c_env_b')
    mission_a = run_mission(work, 'c_env_a')  # 第一个任务占用正常目录
    (work.environments.worktree_root / 'features').chmod(0o500)
    try:
        assert work.claim('c_env_b') is None
        env = env_of(work, 'c_env_b')
        assert env['status'] == 'failed' and env['failure_reason']
        board = work.read('c_env_b')
        assert board['tasks'][0]['waiting_reason'] == 'environment'
        assert board['tasks'][0]['blocker'] == '工作区准备失败：' + env['failure_reason']
    finally:
        (work.environments.worktree_root / 'features').chmod(0o700)
    work.command('c_env_b', 'retry_environment', {'task_id': work.read('c_env_b')['tasks'][0]['id']},
                 work.read('c_env_b')['revision'], operation_id='retry-env')
    assert env_of(work, 'c_env_b')['status'] == 'ready'
    settle(work, 'c_env_a', mission_a)
    assert work.claim('c_env_b') is not None


def test_retry_keeps_original_baseline_and_recorded_root(work, tmp_path):
    create(work, 'c_env_a')
    work.ensure_environments('c_env_a')
    first = env_of(work, 'c_env_a')
    (work.root / 'docs/readme.md').write_text('更新后的主线')
    subprocess.run(['git', '-C', str(work.root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(work.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'advance main'], check=True)
    restarted = AdaptiveWork(work.repo, work.root, worktree_root=tmp_path / 'new-default')
    retried = restarted.environments.prepare({**first, 'status': 'failed'})
    assert retried['status'] == 'ready'
    assert retried['workspace_dir'] == first['workspace_dir']
    assert retried['base_sha'] == first['base_sha']
    assert not (tmp_path / 'new-default').exists()


def test_preview_starts_stops_and_keeps_code(work):
    # M-05：有 UI 的任务能从原任务卡打开候选。
    task_id = create(work, 'c_env_a')
    settle(work, 'c_env_a', run_mission(work, 'c_env_a'))
    board = work.command('c_env_a', 'start_preview', {'task_id': task_id},
                         work.read('c_env_a')['revision'], operation_id='preview-1')
    assert board['tasks'][0]['environment']['preview']['status'] == 'starting'
    preview = wait_preview(work, 'c_env_a', 'ready')
    assert preview['url'] == f"http://127.0.0.1:{preview['ports']['frontend']}"
    assert listening(preview['ports']['frontend'])
    assert preview['instance_id'].startswith('pv_')
    work.command('c_env_a', 'stop_preview', {'task_id': task_id},
                 work.read('c_env_a')['revision'], operation_id='preview-stop')
    stopped = env_of(work, 'c_env_a')['preview']
    assert stopped['status'] == 'stopped' and stopped['url'] is None
    deadline = time.monotonic() + 10
    while listening(preview['ports']['frontend']) and time.monotonic() < deadline:
        time.sleep(.05)
    assert not listening(preview['ports']['frontend'])
    assert Path(env_of(work, 'c_env_a')['workspace_dir']).is_dir()  # 代码保留


def test_preview_failure_shows_reason_and_keeps_workspace(work):
    # M-06：预览失败有原因且保留任务与代码。
    work.environments.preview_command = FAILING_PREVIEW
    task_id = create(work, 'c_env_a')
    settle(work, 'c_env_a', run_mission(work, 'c_env_a'))
    work.command('c_env_a', 'start_preview', {'task_id': task_id},
                 work.read('c_env_a')['revision'], operation_id='preview-fail')
    preview = wait_preview(work, 'c_env_a', 'failed')
    assert '退出码' in preview['failure_reason']
    assert preview['log_id']
    log = work.read_log('c_env_a', preview['log_id']).decode()
    assert 'boom' in log
    assert Path(env_of(work, 'c_env_a')['workspace_dir']).is_dir()


def test_restart_stops_orphan_preview_and_flags_unknown_execution(work):
    # M-07：服务重启后能找到原绑定；运行中的预览被停止，旧执行明确待恢复。
    task_id = create(work, 'c_env_a')
    mission = run_mission(work, 'c_env_a')
    work.command('c_env_a', 'start_preview', {'task_id': task_id},
                 work.read('c_env_a')['revision'], operation_id='preview-restart')
    preview = wait_preview(work, 'c_env_a', 'ready')
    port = preview['ports']['frontend']
    with work.repo._transaction(immediate=True) as db:
        board = work._load(db, 'c_env_a')
        board['tasks'][0]['attempts'][-1]['host_pid'] = 99999999  # 模拟另一宿主进程
        work._save(db, board, 'fixture', 'host')
    restarted = AdaptiveWork(work.repo, work.root, check_timeout=1, preview_command=PREVIEW_SERVER)
    restarted.recover()
    board = restarted.read('c_env_a')
    task = board['tasks'][0]
    assert task['environment']['preview']['status'] == 'stopped'
    # 旧执行不盲目重放：等待恢复或进入只读核对。
    assert task['waiting_reason'] == 'recovery' or task['next_kind'] == 'reconcile'
    assert task['environment']['workspace_dir'] == env_of(work, 'c_env_a')['workspace_dir']
    deadline = time.monotonic() + 10
    while listening(port) and time.monotonic() < deadline:
        time.sleep(.05)
    assert not listening(port)


def test_environment_actions_are_user_only_and_idempotent(work):
    task_id = create(work, 'c_env_a')
    mission = run_mission(work, 'c_env_a')
    with pytest.raises(WorkError, match='权限范围'):
        work.command('c_env_a', 'start_preview', {'task_id': task_id}, work.read('c_env_a')['revision'],
                     actor='chulan', mission_id=mission.id, turn_id='turn_env', operation_id='agent-preview')
    settle(work, 'c_env_a', mission)
    first = work.command('c_env_a', 'start_preview', {'task_id': task_id},
                         work.read('c_env_a')['revision'], operation_id='user-preview')
    replay = work.command('c_env_a', 'start_preview', {'task_id': task_id},
                          work.read('c_env_a')['revision'], operation_id='user-preview')
    assert replay == first
    with pytest.raises(WorkError, match='operation_id'):
        work.command('c_env_a', 'stop_preview', {'task_id': task_id},
                     work.read('c_env_a')['revision'], operation_id='user-preview')
    wait_preview(work, 'c_env_a', 'ready')


def test_api_actions_bind_and_preview_through_router(work, temp_settings, monkeypatch):
    # 环境与预览动作走 REST 门面：不占用立即写锁，operation_id 幂等。
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.adapters.base import CLIAdapter, TurnOutcome
    from backend.app.domain.models import TurnStatus
    from backend.app.work import environments

    class Noop(CLIAdapter):
        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            return TurnOutcome(status=TurnStatus.COMPLETED)

    monkeypatch.chdir(work.root)
    monkeypatch.setattr(environments, 'DEFAULT_PREVIEW_COMMAND', PREVIEW_SERVER)
    client = TestClient(create_app(temp_settings.model_copy(update={'config_dir': Path('config').resolve()}),
                                    mission_adapter=Noop()))
    revision = client.get('/api/conversations/c_env_a/work').json()['revision']
    response = client.post('/api/conversations/c_env_a/work', json={'action': 'create_work',
        'expected_revision': revision, 'operation_id': 'api-create',
        'data': {'title': '文件上传', 'owner_id': 'chulan', 'agreement': {'source': '增加文件上传',
            'goal': '增加文件上传', 'scope': '本项目', 'delivery': 'development',
            'criteria': [{'id': 'AC-1', 'text': '上传后可读取'}]}}})
    assert response.status_code == 200, response.text
    work.ensure_environments('c_env_a')  # 等价于后台 pump 的准备动作
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        board = client.get('/api/conversations/c_env_a/work').json()
        task = board['tasks'][0]
        if (task['environment']['status'] == 'ready' and task['status'] == 'waiting'
                and not task['active_mission_id']):
            break
        time.sleep(.05)
    task_id = board['tasks'][0]['id']
    assert board['tasks'][0]['environment']['branch'].startswith('codex/task-')
    started = client.post('/api/conversations/c_env_a/work', json={'action': 'start_preview',
        'expected_revision': board['revision'], 'operation_id': 'api-preview',
        'data': {'task_id': task_id}})
    assert started.status_code == 200, started.text
    preview = wait_preview(work, 'c_env_a', 'ready')
    assert listening(preview['ports']['frontend'])
    board = client.get('/api/conversations/c_env_a/work').json()
    stopped = client.post('/api/conversations/c_env_a/work', json={'action': 'stop_preview',
        'expected_revision': board['revision'], 'operation_id': 'api-preview-stop',
        'data': {'task_id': task_id}})
    assert stopped.status_code == 200
    assert env_of(work, 'c_env_a')['preview']['status'] == 'stopped'


def test_cancelled_unprepared_work_never_creates_workspace(work):
    task_id = create(work, 'c_env_a')
    work.command('c_env_a', 'cancel', {'task_id': task_id}, work.read('c_env_a')['revision'],
                 operation_id='cancel-before-prepare')
    assert work.claim('c_env_a') is None
    assert not (work.environments.worktree_root / 'features').exists()
    with pytest.raises(WorkError, match='已取消'):
        work.command('c_env_a', 'retry_environment', {'task_id': task_id},
                     work.read('c_env_a')['revision'], operation_id='retry-cancelled')


def test_stale_environment_revision_has_no_preparation_side_effect(work, monkeypatch):
    task_id = create(work, 'c_env_a')
    monkeypatch.setattr(work.environments, 'prepare', lambda *_: pytest.fail('stale request prepared a directory'))
    with pytest.raises(WorkError, match='任务板已更新'):
        work.command('c_env_a', 'retry_environment', {'task_id': task_id}, 0, operation_id='stale-retry')


def test_changed_bound_branch_cannot_be_used_for_execution(work):
    create(work, 'c_env_a')
    work.ensure_environments('c_env_a')
    directory = env_of(work, 'c_env_a')['workspace_dir']
    subprocess.run(['git', '-C', directory, 'checkout', '-qb', 'different-owner'], check=True)
    with pytest.raises(WorkError, match='身份变化'):
        work.claim('c_env_a')
    assert not work.read('c_env_a')['tasks'][0]['active_mission_id']


def test_binding_change_during_execution_finishes_as_recovery(work):
    create(work, 'c_env_a')
    mission = run_mission(work, 'c_env_a')
    directory = env_of(work, 'c_env_a')['workspace_dir']
    subprocess.run(['git', '-C', directory, 'checkout', '-qb', 'changed-during-run'], check=True)
    settle(work, 'c_env_a', mission)
    task = work.read('c_env_a')['tasks'][0]
    assert task['active_mission_id'] is None
    assert task['status'] == 'waiting' and task['waiting_reason'] == 'recovery'
    assert '身份变化' in task['blocker']


def test_mcp_service_resolves_binding_from_candidate_directory(work):
    create(work, 'c_env_a')
    mission = run_mission(work, 'c_env_a')
    directory = Path(env_of(work, 'c_env_a')['workspace_dir'])
    # MCP tools receive the task directory, while the controller uses its own checkout.
    delegated = AdaptiveWork(work.repo, directory)
    context = delegated.execution_context(mission.id)
    assert context['workspace_root'] == str(directory)
    assert delegated._workspace(delegated.read('c_env_a'), delegated.read('c_env_a')['tasks'][0]) == directory


def test_broken_preview_record_does_not_break_controller_recovery(work, monkeypatch):
    from scripts import environment_supervisor as manager
    def malformed(*a, **k):
        raise ValueError('broken preview record')
    monkeypatch.setattr(manager, 'read_record', malformed)
    preview = {'managed': True, 'checkout': str(work.root), 'status': 'ready',
               'instance_id': 'old-preview', 'pid': 12345, 'url': 'http://127.0.0.1:5999'}
    recovered = work.environments.reconcile_preview(preview)
    assert recovered['status'] == 'failed' and recovered['url'] is None
    with pytest.raises(WorkError, match='预览未停止'):
        work.environments.stop_preview_process(preview)


def test_dependency_failure_keeps_task_and_readable_log(work, monkeypatch):
    from scripts import environment_supervisor as manager
    task_id = create(work, 'c_env_a')
    settle(work, 'c_env_a', run_mission(work, 'c_env_a'))
    work.environments.preview_command = None
    def fail(*args, **kwargs):
        kwargs['output'].write(b'dependency fixture failed\n')
        raise subprocess.CalledProcessError(9, ['fixture-dependency'])
    monkeypatch.setattr(manager, 'setup', fail)
    monkeypatch.setattr(manager, 'start', lambda *a, **k: pytest.fail('started without ready dependencies'))
    work.command('c_env_a', 'start_preview', {'task_id': task_id}, work.read('c_env_a')['revision'],
                 operation_id='dependency-failure')
    preview = wait_preview(work, 'c_env_a', 'failed')
    assert '退出码 9' in preview['failure_reason']
    assert b'dependency fixture failed' in work.read_log('c_env_a', preview['log_id'])
    assert env_of(work, 'c_env_a')['status'] == 'ready'


def test_preview_private_state_cannot_escape_workspace(work, tmp_path):
    task_id = create(work, 'c_env_a')
    settle(work, 'c_env_a', run_mission(work, 'c_env_a'))
    directory = Path(env_of(work, 'c_env_a')['workspace_dir'])
    outside = tmp_path / 'unrelated-state'
    outside.mkdir()
    (directory / '.yiren').symlink_to(outside, target_is_directory=True)
    work.environments.preview_command = None
    work.command('c_env_a', 'start_preview', {'task_id': task_id}, work.read('c_env_a')['revision'],
                 operation_id='escaping-state')
    preview = wait_preview(work, 'c_env_a', 'failed')
    assert not list(outside.iterdir())
    assert '工作区之外' in work.read_log('c_env_a', preview['log_id']).decode()


def test_concurrent_preview_replay_starts_once(work):
    from concurrent.futures import ThreadPoolExecutor
    task_id = create(work, 'c_env_a')
    settle(work, 'c_env_a', run_mission(work, 'c_env_a'))
    revision = work.read('c_env_a')['revision']
    def request():
        return work.command('c_env_a', 'start_preview', {'task_id': task_id}, revision,
                            operation_id='same-concurrent-preview')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: request(), range(2)))
    assert first == second
    assert len([e for e in work.read('c_env_a')['events'] if e['action'] == 'preview_starting']) == 1
    wait_preview(work, 'c_env_a', 'ready')
    work.command('c_env_a', 'stop_preview', {'task_id': task_id},
                 work.read('c_env_a')['revision'], operation_id='concurrent-preview-stop')


def test_managed_preview_uses_lifecycle_and_refuses_foreign_instance(work, monkeypatch):
    from scripts import environment_supervisor as manager
    task_id = create(work, 'c_env_a')
    settle(work, 'c_env_a', run_mission(work, 'c_env_a'))
    work.environments.preview_command = None
    records = {}
    calls = []
    monkeypatch.setattr(manager, 'read_record', lambda *a, **k: records.get('record'))
    monkeypatch.setattr(manager, 'alive', lambda r: bool(r))
    monkeypatch.setattr(manager, 'setup', lambda *a, **k: sys.executable)
    def start(root, profile, **kwargs):
        calls.append((root, profile, kwargs))
        records['record'] = {'instance_id': 'owned-preview', 'checkout': str(root),
            'supervisor_pid': 12345, 'supervisor_started_at': 'birth', 'backend_port': 8765, 'frontend_port': 5765}
        return records['record']
    monkeypatch.setattr(manager, 'start', start)
    work.command('c_env_a', 'start_preview', {'task_id': task_id}, work.read('c_env_a')['revision'],
                 operation_id='managed-start')
    preview = wait_preview(work, 'c_env_a', 'ready')
    assert preview['managed'] and preview['instance_id'] == 'owned-preview'
    assert calls[0][1:] == ('development', {'detach': True, 'allocate': True, 'real': False,
                                          'reload': False, 'install': False})
    records['record']['instance_id'] = 'foreign-preview'
    monkeypatch.setattr(manager, 'stop', lambda *a, **k: pytest.fail('foreign instance was stopped'))
    with pytest.raises(WorkError, match='归属'):
        work.command('c_env_a', 'stop_preview', {'task_id': task_id}, work.read('c_env_a')['revision'],
                     operation_id='managed-stop-foreign')


def test_proposal_confirmation_creates_binding_only_after_human_accepts(work):
    from backend.app.domain.models import Mission
    mission = Mission(id='m_proposal_env', conversation_id='c_env_a', title='开发页面', goal='开发页面',
                      status=MissionStatus.RUNNING)
    work.repo.create_mission(mission)
    work.repo.add_event(mission.id, 'turn.started', {'turn_id': 'proposal-turn', 'agent_id': 'chulan'})
    board = work.command('c_env_a', 'propose_work', {'title': '开发页面', 'owner_id': 'chulan',
        'agreement': {'source': '模型来源', 'goal': '开发页面', 'scope': '当前项目', 'delivery': 'development',
                      'criteria': [{'id': 'AC-1', 'text': '页面可用'}]}}, 0,
        actor='chulan', mission_id=mission.id, turn_id='proposal-turn', operation_id='proposal-workspace')
    assert not board['tasks']
    assert not (work.environments.worktree_root / 'features').exists()
    board = work.command('c_env_a', 'accept_proposal', {'proposal_id': board['proposals'][0]['id']},
                         board['revision'], operation_id='accept-workspace-proposal')
    assert board['tasks'][0]['environment']['status'] == 'pending'
    work.ensure_environments('c_env_a')
    assert env_of(work, 'c_env_a')['status'] == 'ready'
    assert work.read('c_env_a')['proposals'][0]['status'] == 'accepted'


def test_real_managed_preview_has_isolated_state_and_matching_identity(work, monkeypatch):
    """Exercise the production lifecycle with actual HTTP services, no model calls."""
    import json
    import shutil
    from scripts import environment_supervisor as manager
    from scripts.environment_common import request
    source = Path(__file__).resolve().parents[2]
    if not (source / '.venv/bin/python').exists() or not (source / 'frontend/node_modules/.bin/vite').exists():
        pytest.skip('真实受管预览集成需要当前 checkout 已安装后端和前端依赖；其余生命周期回归独立执行')
    for name in ('backend', 'frontend', 'config', 'scripts'):
        shutil.copytree(source / name, work.root / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('node_modules', 'dist', '__pycache__', '.pytest_cache'))
    shutil.copy2(source / 'requirements.txt', work.root / 'requirements.txt')
    (work.root / '.gitignore').write_text('.yiren/\n.venv/\nnode_modules/\nfrontend/dist/\n__pycache__/\n')
    subprocess.run(['git', '-C', str(work.root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(work.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'managed preview fixture'], check=True)
    task_id = create(work, 'c_env_a')
    settle(work, 'c_env_a', run_mission(work, 'c_env_a'))
    directory = Path(env_of(work, 'c_env_a')['workspace_dir'])
    (directory / '.venv').symlink_to(source / '.venv', target_is_directory=True)
    (directory / 'frontend/node_modules').symlink_to(source / 'frontend/node_modules', target_is_directory=True)
    # Dependencies belong to the test checkout. The actual supervisor still
    # performs its normal identity, port, startup and authenticated-stop checks.
    monkeypatch.setattr(manager, 'setup', lambda *a, **k: str(source / '.venv/bin/python'))
    work.environments.preview_command = None
    work.command('c_env_a', 'start_preview', {'task_id': task_id}, work.read('c_env_a')['revision'],
                 operation_id='real-managed-start')
    preview = wait_preview(work, 'c_env_a', 'ready', timeout=55)
    try:
        assert preview['status'] == 'ready', preview.get('failure_reason')
        health = request(preview['ports']['backend'])
        frontend = request(preview['ports']['frontend'], '/__environment')
        started = health['runtime']['started']
        assert started['instance_id'] == preview['instance_id']
        assert started['checkout'] == str(directory)
        assert Path(started['database']).is_relative_to(directory / '.yiren/development')
        assert started['use_fake_cli'] is True
        assert frontend['checkout_id'] == started['checkout_id']
        assert frontend['source_digest'] == started['source_digest']
        old = dict(preview)
        # Only a known matching preview is retained across controller recovery.
        work.recover()
        assert env_of(work, 'c_env_a')['preview']['instance_id'] == old['instance_id']
    finally:
        if preview['status'] == 'ready':
            work.command('c_env_a', 'stop_preview', {'task_id': task_id}, work.read('c_env_a')['revision'],
                         operation_id='real-managed-stop')
    assert env_of(work, 'c_env_a')['preview']['status'] == 'stopped'
