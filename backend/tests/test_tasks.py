import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.domain.models import Conversation, ConversationStatus, MissionStatus
from backend.app.main import create_app
from backend.app.storage.repository import Repository
from backend.app.work.models import WorkCommand
from backend.app.work.service import WorkError
from backend.app.work.tasks import Tasks


@pytest.fixture
def tasks(repository, tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(project)], check=True)
    subprocess.run(['git', '-C', str(project), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-qm', 'initial'], check=True)
    return Tasks(repository, project, check_timeout=1)


def request(**changes):
    return {'title': '独立完成活动页', 'owner_id': 'chulan', 'operation_id': uuid4().hex,
            'agreement': {'source': '用户从任务页要求完成活动页', 'goal': '交付活动页说明',
                          'scope': '本项目', 'criteria': [{'id': 'AC-1', 'text': '文件包含完整说明'}],
                          'delivery': 'document', 'risks': ['local'], 'validation_commands': []},
            'orchestration': {'mode': 'adaptive', 'steps': []}, **changes}


def flow():
    # Intentionally list the dependent node first: dependency ordering must win.
    return {'mode': 'workflow', 'steps': [
        {'id': 'write', 'title': '编写文档', 'owner_id': 'baobao', 'goal': '整合材料写出说明', 'depends_on': ['research']},
        {'id': 'research', 'title': '整理材料', 'owner_id': 'wangye', 'goal': '收集并整理已有材料', 'depends_on': []},
    ]}


def command(tasks, detail, action, data=None, operation_id=None):
    return tasks.command(detail['task']['id'], WorkCommand(action=action,
        expected_revision=detail['board']['revision'], data=data or {}, operation_id=operation_id or uuid4().hex))


def finish(tasks, detail, expected_step):
    cid = detail['execution_conversation_id']
    mission = tasks.work.claim(cid)
    assert mission is not None
    board = tasks.work.read(cid)
    node = next(t for t in board['tasks'] if t['active_mission_id'] == mission.id)
    assert node.get('workflow_step_id') == expected_step
    tasks.repo.update_mission(mission.model_copy(update={'status': MissionStatus.RUNNING}))
    tasks.work.authorize_provider(mission.id, 'test')
    root = next(t for t in board['tasks'] if not t['parent_id'])
    target = Path(root['environment']['workspace_dir']) / 'docs' / f'{expected_step or "integrated"}.md'
    target.parent.mkdir(exist_ok=True)
    target.write_text('实际交付并核对')
    tasks.work.command(cid, 'submit', {'task_id': node['id'], 'kind': 'document', 'summary': '交付完成',
        'artifact_paths': [str(target.relative_to(Path(root['environment']['workspace_dir'])))],
        'observations': [{'id': key, 'text': '文件存在且内容符合当前约定'} for key in node['criterion_ids']]},
        tasks.work.read(cid)['revision'], actor=node['owner_id'], mission_id=mission.id,
        turn_id='turn_test', operation_id=uuid4().hex)
    tasks.repo.update_mission(mission.model_copy(update={'status': MissionStatus.COMPLETED}))
    tasks.work.finish(cid, mission.id)
    return tasks.detail(root['id'])


def test_independent_creation_is_paused_hidden_and_concurrently_idempotent(tasks):
    payload = request(orchestration=flow())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: tasks.create(payload), range(2)))
    first = results[0]
    assert results[1]['task']['id'] == first['task']['id']
    assert first['task']['paused'] is True
    assert first['source_conversation_id'] is None
    assert tasks.repo.list_conversations() == []
    assert tasks.repo.list_conversations(include_task_contexts=True)[0].kind == 'task'
    assert len(tasks.list()) == 1
    assert len(first['board']['tasks']) == 3
    assert all(step['task_id'].startswith('wt_') for step in first['orchestration']['steps'])
    assert tasks.work.claim(first['execution_conversation_id']) is None
    assert not (tasks.work.environments.worktree_root / 'features').exists()
    with pytest.raises(WorkError, match='operation_id'):
        tasks.create({**payload, 'title': 'different'})


def test_source_archiving_does_not_own_or_pause_the_task(tasks):
    tasks.repo.create_conversation(Conversation(id='c_source', title='讨论'))
    detail = tasks.create(request(source_conversation_id='c_source'))
    source = tasks.repo.get_conversation('c_source')
    tasks.repo.update_conversation(source.model_copy(update={'status': ConversationStatus.ARCHIVED}))
    detail = command(tasks, detail, 'resume')
    assert tasks.work.claim(detail['execution_conversation_id']) is not None
    assert tasks.list()[0]['source_conversation_id'] == 'c_source'


@pytest.mark.parametrize('change', ['cycle', 'unknown', 'duplicate', 'owner', 'criterion', 'task_id'])
def test_invalid_plan_rolls_back_context_and_root(tasks, change):
    plan = flow()
    if change == 'cycle':
        plan['steps'][1]['depends_on'] = ['write']
    elif change == 'unknown':
        plan['steps'][0]['depends_on'] = ['missing']
    elif change == 'duplicate':
        plan['steps'][1]['id'] = 'write'
    elif change == 'owner':
        plan['steps'][0]['owner_id'] = 'unknown'
    elif change == 'criterion':
        plan['steps'][0]['criterion_ids'] = ['missing']
    else:
        plan['steps'][0]['task_id'] = 'wt_forged'
    with pytest.raises((ValueError, ValidationError, WorkError)):
        tasks.create(request(orchestration=plan))
    assert tasks.list() == []
    assert tasks.repo.list_conversations(include_task_contexts=True) == []


def test_workflow_claims_dependencies_then_requires_root_integration(tasks):
    detail = tasks.create(request(orchestration=flow()))
    detail = command(tasks, detail, 'resume')
    detail = finish(tasks, detail, 'research')
    assert detail['task']['status'] != 'done'
    detail = finish(tasks, detail, 'write')
    assert detail['task']['status'] != 'done'
    detail = finish(tasks, detail, None)
    assert detail['task']['status'] == 'done'
    assert detail['task']['attempts_used'] == 3
    assert len(detail['task']['deliveries']) == 1
    assert tasks.work.claim(detail['execution_conversation_id']) is None


def test_configure_is_locked_after_attempt_and_preserved_across_restart(tasks):
    detail = tasks.create(request())
    detail = command(tasks, detail, 'configure_workflow', {'orchestration': flow()})
    reloaded = Tasks(Repository(tasks.repo.database_path), tasks.work.root)
    assert reloaded.detail(detail['task']['id'])['orchestration'] == detail['orchestration']
    detail = command(tasks, detail, 'resume')
    with pytest.raises(WorkError, match='暂停'):
        command(tasks, detail, 'configure_workflow', {'orchestration': {'mode': 'adaptive', 'steps': []}})
    detail = finish(tasks, detail, 'research')
    detail = command(tasks, detail, 'pause')
    with pytest.raises(WorkError, match='尚未执行'):
        command(tasks, detail, 'configure_workflow', {'orchestration': {'mode': 'adaptive', 'steps': []}})
    reloaded.work.recover()
    assert reloaded.detail(detail['task']['id'])['task']['paused'] is True


def test_existing_chat_roots_project_independently_and_reject_cross_root_commands(tasks):
    tasks.repo.create_conversation(Conversation(id='c_old', title='已有会话'))
    for number in range(2):
        payload = request(title=f'已有工作 {number}')
        tasks.work.command('c_old', 'create_work', {k: payload[k] for k in ('title', 'owner_id', 'agreement')},
                           tasks.work.read('c_old')['revision'], operation_id=uuid4().hex)
    roots = tasks.list()
    first, other = roots[0], roots[1]
    detail = tasks.detail(first['id'])
    assert detail['source_conversation_id'] == 'c_old'
    assert len(detail['board']['tasks']) == len(detail['board']['contracts']) == 1
    assert {e['task_id'] for e in detail['board']['events']} == {first['id']}
    with pytest.raises(WorkError, match='不属于'):
        command(tasks, detail, 'cancel', {'task_id': other['id']})
    board = tasks.work.read('c_old')
    with tasks.repo._transaction(immediate=True) as db:
        board['decisions'].append({'id': 'wd_other', 'task_id': other['id'], 'status': 'pending'})
        tasks.work._save(db, board, 'test', 'host')
    detail = tasks.detail(first['id'])
    assert detail['board']['decisions'] == []
    with pytest.raises(WorkError, match='不属于'):
        command(tasks, detail, 'decide', {'decision_id': 'wd_other', 'option_id': 'approve'})
    with pytest.raises(WorkError, match='不属于'):
        tasks.evidence(first['id'], 'ws_other')


def test_task_api_and_old_conversation_endpoints_remain_compatible(temp_settings):
    client = TestClient(create_app(temp_settings))
    response = client.post('/api/tasks', json=request(orchestration=flow()))
    assert response.status_code == 201
    detail = response.json()
    assert client.get('/api/conversations').json() == []
    assert client.get('/api/tasks').json()['tasks'][0]['id'] == detail['task']['id']
    assert client.get('/api/tasks/' + detail['task']['id']).json() == detail
    assert client.post('/api/conversations', json={'title': '正常聊天'}).status_code == 201
    assert len(client.get('/api/conversations').json()) == 1
    result = client.post('/api/tasks/' + detail['task']['id'] + '/commands', json={
        'action': 'create_work', 'data': {}, 'expected_revision': detail['board']['revision'],
        'operation_id': 'denied'})
    assert result.status_code == 403


def test_legacy_boards_have_an_independent_read_and_control_entry_without_migration(tasks):
    from backend.app.work.service import SharedWork
    tasks.repo.create_conversation(Conversation(id='c_legacy', title='旧版需求'))
    work = SharedWork(tasks.repo, tasks.work.root)
    board = work.command('c_legacy', 'propose_contract', {
        'source': '旧需求', 'goal': '保留旧任务责任', 'scope': '本项目', 'non_goals': '不迁移',
        'criteria': [{'id': 'AC-1', 'text': '旧版仍可操作'}],
        'validation_commands': [['python3', '-c', 'print("ok")']]}, 0)
    contract = board['contracts'][0]['id']
    board = work.command('c_legacy', 'activate_contract', {'contract_id': contract}, board['revision'])
    board = work.command('c_legacy', 'create_task', {
        'title': '已有实现', 'contract_id': contract, 'owner_id': 'chulan', 'reviewer_id': 'baobao',
        'criterion_ids': ['AC-1']}, board['revision'])
    with tasks.repo._transaction() as db:
        before = db.execute('SELECT data FROM work_boards WHERE conversation_id=?', ('c_legacy',)).fetchone()['data']
    summary = tasks.list()[0]
    assert summary['protocol'] == 'legacy'
    assert summary['id'] == 'legacy:c_legacy'
    detail = tasks.detail(summary['id'])
    assert detail['board']['tasks'] == board['tasks']
    assert detail['archived'] is False
    with tasks.repo._transaction() as db:
        assert db.execute('SELECT data FROM work_boards WHERE conversation_id=?', ('c_legacy',)).fetchone()['data'] == before
    op = uuid4().hex
    created = command(tasks, detail, 'create_task', {'title': '第二项', 'contract_id': contract,
        'owner_id': 'wangye', 'reviewer_id': 'baobao', 'criterion_ids': ['AC-1']}, operation_id=op)
    repeated = command(tasks, detail, 'create_task', {'title': '第二项', 'contract_id': contract,
        'owner_id': 'wangye', 'reviewer_id': 'baobao', 'criterion_ids': ['AC-1']}, operation_id=op)
    assert len(repeated['board']['tasks']) == 2
    created = command(tasks, created, 'start')
    assert created['board']['enabled'] is True
    created = command(tasks, created, 'pause')
    assert created['board']['enabled'] is False
    with pytest.raises(WorkError, match='历史任务'):
        command(tasks, created, 'configure_workflow', {'orchestration': flow()})
    with pytest.raises(WorkError, match='不属于'):
        command(tasks, created, 'retry', {'task_id': 'wt_other'})
    source = tasks.repo.get_conversation('c_legacy')
    tasks.repo.update_conversation(source.model_copy(update={'status': ConversationStatus.ARCHIVED}))
    assert tasks.detail(summary['id'])['archived'] is True
    with pytest.raises(WorkError, match='恢复会话'):
        command(tasks, created, 'start')


def test_same_baseline_and_output_in_two_independent_tasks_remain_readable(tasks):
    first = tasks.create(request(title='第一个独立任务'))
    second = tasks.create(request(title='第二个独立任务'))
    first = finish(tasks, command(tasks, first, 'resume'), None)
    second = finish(tasks, command(tasks, second, 'resume'), None)
    assert first['task']['status'] == second['task']['status'] == 'done'
    assert first['task']['candidate']['id'] == second['task']['candidate']['id']
    from backend.app.work.snapshots import content
    candidate = first['task']['candidate']['id']
    assert content(tasks.repo, first['execution_conversation_id'], candidate, 'docs/integrated.md')
    assert content(tasks.repo, second['execution_conversation_id'], candidate, 'docs/integrated.md')
    assert tasks.work.read_log(second['execution_conversation_id'], candidate)
    # An old snapshot may have no reference rows; its original scope remains
    # authoritative without moving or rewriting any blob.
    with tasks.repo._transaction(immediate=True) as db:
        db.execute('DELETE FROM work_snapshot_references WHERE conversation_id=?',
                   (first['execution_conversation_id'],))
    assert content(tasks.repo, first['execution_conversation_id'], candidate, 'docs/integrated.md')
    with pytest.raises(WorkError, match='快照不存在'):
        content(tasks.repo, 'unrelated_context', candidate, 'docs/integrated.md')


def test_legacy_log_api_dispatches_protocol_after_checking_task_ownership(tasks, temp_settings):
    from backend.app.work.service import SharedWork
    # Persist legacy evidence through its real board model. These are fixture
    # validation logs, not claims of real CLI execution or review approval.
    for suffix in ('a', 'b'):
        cid = 'c_legacy_' + suffix
        tasks.repo.create_conversation(Conversation(id=cid, title='旧版验证 ' + suffix))
        work = SharedWork(tasks.repo, tasks.work.root)
        board = work.command(cid, 'propose_contract', {
            'source': '既有需求', 'goal': '保留验证日志', 'scope': '本项目', 'non_goals': '不迁移',
            'criteria': [{'id': 'AC-1', 'text': '验证日志可按归属读取'}],
            'validation_commands': [['python3', '-c', 'print("verified")']]}, 0)
        board = work.command(cid, 'create_task', {
            'title': '旧版工作', 'contract_id': board['contracts'][0]['id'], 'owner_id': 'chulan',
            'reviewer_id': 'baobao', 'criterion_ids': ['AC-1']}, board['revision'])
        log_id = 'wl_legacy_' + suffix
        board['tasks'][0]['evidence'].append({'id': 'wv_' + suffix, 'checks': [{'log_id': log_id}]})
        with tasks.repo._transaction(immediate=True) as db:
            db.execute('INSERT INTO work_check_logs VALUES (?, ?, ?)', (log_id, cid, ('verified-' + suffix).encode()))
            work._save(db, board, 'verification_fixture', 'host')
    client = TestClient(create_app(temp_settings))
    response = client.get('/api/tasks/legacy:c_legacy_a/logs/wl_legacy_a')
    assert response.status_code == 200
    assert response.text == 'verified-a'
    assert response.headers['content-type'].startswith('text/plain')
    # A real foreign log exists, but its ID is not evidence of this task.
    denied = client.get('/api/tasks/legacy:c_legacy_a/logs/wl_legacy_b')
    assert denied.status_code == 404
    assert '不属于' in denied.json()['detail']
    assert 'verified-b' not in denied.text
    assert client.get('/api/tasks/legacy:c_legacy_b/logs/wl_legacy_b').text == 'verified-b'
