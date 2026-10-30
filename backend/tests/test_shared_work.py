import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.domain.models import Conversation, MissionStatus
from backend.app.main import create_app
from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.storage.repository import Repository
from backend.app.extensions.tasks.service import SharedWork, WorkError


@pytest.fixture
def work(repository, tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-qm', 'initial'], check=True)
    repository.create_conversation(Conversation(id='c_work', title='开发'))
    return SharedWork(repository, root, check_timeout=1)


def command(work, action, **data):
    return work.command('c_work', action, data, work.read('c_work')['revision'])


def prepare(work, commands=None):
    board = command(work, 'propose_contract', source='用户要求恢复任务不能丢失', goal='可靠恢复', scope='共享任务',
                    non_goals='云端同步', criteria=[{'id': 'AC-1', 'text': '恢复后保留责任和进度'}],
                    validation_commands=commands or [[sys.executable, '-c', 'print("verified")']])
    contract_id = board['contracts'][0]['id']
    command(work, 'activate_contract', contract_id=contract_id)
    board = command(work, 'create_task', title='实现恢复', contract_id=contract_id, owner_id='chulan',
                    reviewer_id='baobao', criterion_ids=['AC-1'])
    command(work, 'start')
    return board['tasks'][0]['id']


def claim_running(work):
    mission = work.claim('c_work')
    assert mission is not None
    work.repo.update_mission(mission.model_copy(update={'status': MissionStatus.RUNNING}))
    return mission


def agent_command(work, mission, action, **data):
    return work.command('c_work', action, data, work.read('c_work')['revision'],
                        actor=mission.target_agent_id, mission_id=mission.id, turn_id='turn_host')


def finish(work, mission):
    work.repo.update_mission(mission.model_copy(update={'status': MissionStatus.COMPLETED}))
    work.finish('c_work', mission.id)
    return work.read('c_work')


def submit(work):
    task_id = prepare(work)
    mission = claim_running(work)
    agent_command(work, mission, 'submit', task_id=task_id)
    result = finish(work, mission)
    return task_id, result


def test_end_to_end_independent_review_and_persistence(work):
    task_id, result = submit(work)
    evidence = result['tasks'][0]['evidence'][0]
    assert evidence['checks'][0]['output'] == 'verified\n'
    assert evidence['checks'][0]['output_sha256'] == hashlib.sha256(b'verified\n').hexdigest()
    assert evidence['contract_digest'] == result['contracts'][0]['digest']
    reviewer = claim_running(work)
    assert reviewer.target_agent_id == 'baobao'
    board = agent_command(work, reviewer, 'review', task_id=task_id, commit_sha=evidence['commit_sha'],
                          criterion_ids=['AC-1'], decision='approved', note='逐项核对恢复逻辑与宿主测试输出，符合 AC-1。')
    finish(work, reviewer)
    restored = SharedWork(Repository(work.repo.database_path), work.root).read('c_work')
    assert restored['tasks'][0]['status'] == 'done'
    review = restored['tasks'][0]['reviews'][0]
    assert review['commit_sha'] == evidence['commit_sha']
    assert review['contract_id'] == evidence['contract_id']
    assert review['evidence_id'] == evidence['id']
    assert work.claim('c_work') is None


def test_cas_and_atomic_claim_across_workers(work):
    prepare(work)
    with pytest.raises(WorkError, match='已更新'):
        work.command('c_work', 'pause', {}, 0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: SharedWork(Repository(work.repo.database_path), work.root).claim('c_work'), range(4)))
    assert sum(m is not None for m in claims) == 1
    assert len(work.repo.list_missions()) == 1


def test_dependency_waits_for_review_not_implementation(work):
    first = prepare(work)
    contract_id = work.read('c_work')['active_contract_id']
    command(work, 'create_task', title='后续工作', contract_id=contract_id, owner_id='wangye',
            reviewer_id='chulan', criterion_ids=['AC-1'], depends_on=[first])
    implement = claim_running(work)
    agent_command(work, implement, 'submit', task_id=first)
    evidence = finish(work, implement)['tasks'][0]['evidence'][0]
    review = claim_running(work)
    assert review.target_agent_id == 'baobao'
    agent_command(work, review, 'review', task_id=first, commit_sha=evidence['commit_sha'],
                  criterion_ids=['AC-1'], decision='approved', note='AC-1 已核对。')
    finish(work, review)
    assert work.claim('c_work').target_agent_id == 'wangye'


def test_failed_check_preserves_evidence_and_blocks_review(work):
    task_id = prepare(work, [[sys.executable, '-c', 'print("failure proof"); raise SystemExit(7)']])
    mission = claim_running(work)
    agent_command(work, mission, 'submit', task_id=task_id)
    board = finish(work, mission)
    assert board['tasks'][0]['status'] == 'blocked'
    assert board['tasks'][0]['evidence'][0]['checks'][0]['exit_code'] == 7
    assert 'failure proof' in board['tasks'][0]['evidence'][0]['checks'][0]['output']
    finish(work, mission)
    assert work.claim('c_work') is None


@pytest.mark.parametrize('dirty', [True, False])
def test_review_cannot_approve_changed_code(work, dirty):
    task_id, board = submit(work)
    sha = board['tasks'][0]['candidate_sha']
    review = claim_running(work)
    if dirty:
        (work.root / 'new.py').write_text('changed')
    else:
        subprocess.run(['git', '-C', str(work.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '--allow-empty', '-qm', 'new version'], check=True)
    with pytest.raises(WorkError, match='未提交|版本已变化'):
        agent_command(work, review, 'review', task_id=task_id, commit_sha=sha,
                      criterion_ids=['AC-1'], decision='approved', note='通过')
    assert work.read('c_work')['tasks'][0]['reviews'] == []


def test_owner_cannot_review_or_stale_mission_update(work):
    task_id = prepare(work)
    mission = claim_running(work)
    agent_command(work, mission, 'submit', task_id=task_id)
    with pytest.raises(WorkError, match='责任人'):
        agent_command(work, mission, 'review', task_id=task_id)
    finish(work, mission)
    with pytest.raises(WorkError, match='过期'):
        agent_command(work, mission, 'progress', task_id=task_id, text='过期更新')


def test_recovery_retains_checkpoint_and_requires_explicit_retry(work):
    task_id = prepare(work)
    mission = claim_running(work)
    agent_command(work, mission, 'progress', task_id=task_id, text='已定位恢复入口，尚未修改文件')
    work.recover()
    board = work.read('c_work')
    assert not board['enabled']
    assert board['tasks'][0]['progress'] == '已定位恢复入口，尚未修改文件'
    assert board['tasks'][0]['status'] == 'blocked'
    assert work.repo.get_mission(mission.id).status == MissionStatus.BLOCKED
    assert work.claim('c_work') is None
    with pytest.raises(WorkError):
        agent_command(work, mission, 'progress', task_id=task_id, text='旧进程回写')
    command(work, 'retry', task_id=task_id)
    command(work, 'start')
    assert work.claim('c_work').id != mission.id


def test_no_structured_delivery_never_marks_work_done(work):
    prepare(work)
    mission = claim_running(work)
    finish(work, mission)
    task = work.read('c_work')['tasks'][0]
    assert task['status'] == 'blocked'
    assert task['evidence'] == []
    assert '结构化交付' in task['blocker']


def test_contract_revisions_preserve_old_evidence(work):
    task_id, board = submit(work)
    proposal = {key: board['contracts'][0][key] for key in ['source', 'goal', 'scope', 'non_goals', 'criteria', 'validation_commands']}
    proposal['goal'] = '新的目标'
    new_board = command(work, 'propose_contract', **proposal)
    command(work, 'activate_contract', contract_id=new_board['contracts'][-1]['id'])
    restored = work.read('c_work')
    assert restored['contracts'][0]['goal'] == '可靠恢复'
    assert restored['tasks'][0]['evidence'] == board['tasks'][0]['evidence']
    assert work.claim('c_work') is None


def test_pause_allows_current_round_but_prevents_next(work):
    task_id = prepare(work)
    mission = claim_running(work)
    command(work, 'pause')
    assert agent_command(work, mission, 'submit', task_id=task_id)['tasks'][0]['status'] == 'validating'
    finish(work, mission)
    assert work.claim('c_work') is None


def test_rejection_automatically_returns_to_owner(work):
    task_id, board = submit(work)
    reviewer = claim_running(work)
    agent_command(work, reviewer, 'review', task_id=task_id, commit_sha=board['tasks'][0]['candidate_sha'],
                  criterion_ids=['AC-1'], decision='changes_requested', note='缺少恢复时的错误提示，请补齐。')
    finish(work, reviewer)
    assert work.claim('c_work').target_agent_id == 'chulan'


def test_api_exposes_board_and_conflicts_without_impersonation(temp_settings):
    client = TestClient(create_app(temp_settings))
    conversation_id = client.post('/api/conversations', json={}).json()['id']
    url = f'/api/conversations/{conversation_id}/work'
    assert client.get(url).json()['revision'] == 0
    assert client.post(url, json={'action': 'pause', 'expected_revision': 0}).status_code == 200
    assert client.post(url, json={'action': 'pause', 'expected_revision': 0}).status_code == 409
    assert client.post(url, json={'action': 'propose_contract', 'expected_revision': 1, 'data': {}}).status_code == 422
    assert client.get('/api/conversations/missing/work').status_code == 404


def test_mcp_identity_is_bound_to_host_context(work, tmp_path):
    task_id = prepare(work)
    mission = claim_running(work)
    tools = MCPTools(work.repo, tmp_path, workspace_root=work.root)
    board = tools.shared_work(mission.id, 'chulan', 'host-turn', 'codex', 'read')
    with pytest.raises(MCPToolError, match='责任人'):
        tools.shared_work(mission.id, 'wangye', 'host-turn', 'codex', 'progress',
                          {'task_id': task_id, 'text': '伪造责任'}, board['revision'])
    with pytest.raises(MCPToolError, match='只有用户'):
        tools.shared_work(mission.id, 'chulan', 'host-turn', 'codex', 'start', {}, board['revision'])


def test_dispatcher_advances_execution_review_and_dependencies(work, temp_settings, monkeypatch):
    from backend.app.adapters.base import CLIAdapter, TurnOutcome
    from backend.app.extensions.tasks.api.shared_work import drain
    from backend.app.domain.models import TurnStatus

    first = prepare(work)
    board = work.read('c_work')
    command(work, 'create_task', title='后续恢复验证', contract_id=board['active_contract_id'],
            owner_id='wangye', reviewer_id='baobao', criterion_ids=['AC-1'], depends_on=[first])
    settings = temp_settings.model_copy(update={'config_dir': Path('config').resolve()})
    monkeypatch.chdir(work.root)

    class Worker(CLIAdapter):
        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            view = tools.shared_work(mission_id, agent_id, turn_id, provider_id)
            task = next(t for t in view['tasks'] if t['active_mission_id'] == mission_id)
            data = {'task_id': task['id']}
            action = 'submit'
            if task['status'] == 'review':
                action = 'review'
                data.update(commit_sha=task['candidate_sha'], criterion_ids=task['criterion_ids'],
                            decision='approved', note='逐项核对代码、约定和宿主验证日志，符合验收。')
            tools.shared_work(mission_id, agent_id, turn_id, provider_id, action, data, view['revision'])
            tools.publish_message(mission_id, agent_id, turn_id, '本轮结果已写入共享任务。', provider_id)
            return TurnOutcome(status=TurnStatus.COMPLETED)

    drain(work.repo, settings, 'c_work', Worker())
    board = work.read('c_work')
    assert [t['status'] for t in board['tasks']] == ['done', 'done']
    assert not board['enabled']
    assert len(work.repo.list_missions()) == 4
    assert [m.target_agent_id for m in work.repo.list_missions_for_conversation('c_work')] == ['chulan', 'baobao', 'wangye', 'baobao']


def test_complete_logs_are_retained_and_scoped_to_conversation(work):
    task_id = prepare(work, [[sys.executable, '-c', 'print("x" * 30000)']])
    mission = claim_running(work)
    agent_command(work, mission, 'submit', task_id=task_id)
    board = finish(work, mission)
    check = board['tasks'][0]['evidence'][0]['checks'][0]
    assert check['truncated']
    assert len(check['output']) == 24000
    raw = work.read_log('c_work', check['log_id'])
    assert len(raw) == 30001
    assert hashlib.sha256(raw).hexdigest() == check['output_sha256']
    work.repo.create_conversation(Conversation(id='c_other', title='其他'))
    with pytest.raises(WorkError, match='日志不存在'):
        work.read_log('c_other', check['log_id'])


def test_timeout_is_evidence_not_a_passing_test(work):
    task_id = prepare(work, [[sys.executable, '-c', 'import time; time.sleep(3)']])
    mission = claim_running(work)
    agent_command(work, mission, 'submit', task_id=task_id)
    board = finish(work, mission)
    assert board['tasks'][0]['status'] == 'blocked'
    check = board['tasks'][0]['evidence'][0]['checks'][0]
    assert check['exit_code'] == -1
    assert 'timed out' in check['output']


def test_user_can_pause_during_host_validation(work, monkeypatch):
    task_id = prepare(work)
    mission = claim_running(work)
    agent_command(work, mission, 'submit', task_id=task_id)
    original = work._checks

    def checking(*args):
        # A separate connection must be able to write while commands run.
        other = SharedWork(Repository(work.repo.database_path), work.root)
        other.command('c_work', 'pause', {}, other.read('c_work')['revision'])
        return original(*args)

    monkeypatch.setattr(work, '_checks', checking)
    assert finish(work, mission)['tasks'][0]['status'] == 'review'
    assert not work.read('c_work')['enabled']


def test_demo_blocks_without_exposing_internal_prompt(work, temp_settings, monkeypatch):
    from backend.app.adapters.fake import FakeCLIAdapter
    from backend.app.extensions.tasks.api.shared_work import drain
    prepare(work)
    conversation = work.repo.get_conversation('c_work')
    work.repo.update_conversation(conversation.model_copy(update={'title': '新对话'}))
    settings = temp_settings.model_copy(update={'config_dir': Path('config').resolve()})
    monkeypatch.chdir(work.root)
    drain(work.repo, settings, 'c_work', FakeCLIAdapter())
    task = work.read('c_work')['tasks'][0]
    assert task['status'] == 'blocked'
    assert '演示模式' in task['blocker']
    assert task['evidence'] == []
    assert work.repo.get_conversation('c_work').title == '实现恢复'
    messages = work.repo.list_messages(task['attempts'][0]['mission_id'])
    assert len(messages) == 1
    assert messages[0].content == task['blocker']


def test_exhausted_task_does_not_strand_independent_work(work):
    first = prepare(work)
    board = work.read('c_work')
    command(work, 'create_task', title='独立任务', contract_id=board['active_contract_id'],
            owner_id='wangye', reviewer_id='baobao', criterion_ids=['AC-1'])
    with work.repo._transaction(immediate=True) as db:
        board = work._load(db, 'c_work')
        board['tasks'][0]['attempts'] = [{'mission_id': f'old-{i}'} for i in range(board['stage_budget'])]
        work._save(db, board, 'fixture', 'host')
    assert work.claim('c_work').target_agent_id == 'wangye'
    assert work.read('c_work')['tasks'][0]['status'] == 'blocked'
